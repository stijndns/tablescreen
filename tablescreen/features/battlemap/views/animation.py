"""
animation.py — The mainloop side of animation. Mainloop thread only.

AnimationClock is the single after() loop that drives everything animated
on a canvas (the video backdrop now, sprites later), so animations do not
run competing timers.
"""

from __future__ import annotations

import math
import time
import tkinter as tk
from typing import Optional, Protocol

from PIL import ImageTk

from ..video import MAX_FPS, Frame, VideoStream

HIDDEN_POLL_S = 0.25    # tick rate while nothing is visible
DUE_TOLERANCE_S = 0.002 # a frame due this soon counts as due (after() jitter)


class Animation(Protocol):
    def tick(self, now: float) -> float:
        """Advance to monotonic time ``now``; return when to be ticked next."""
        ...


class AnimationClock:
    """One after() loop for every animation on a widget."""

    def __init__(self, widget: tk.Misc):
        self._widget = widget
        self._animations: list[Animation] = []
        self._job: Optional[str] = None

    def add(self, animation: Animation) -> None:
        self._animations.append(animation)
        self._schedule(0)

    def remove(self, animation: Animation) -> None:
        if animation in self._animations:
            self._animations.remove(animation)
        if not self._animations:
            self._cancel()

    def _cancel(self) -> None:
        if self._job is not None:
            self._widget.after_cancel(self._job)
            self._job = None

    def _schedule(self, delay_s: float) -> None:
        self._cancel()
        # Round up: waking a hair early just costs an extra empty tick.
        self._job = self._widget.after(max(1, math.ceil(delay_s * 1000)), self._tick)

    def _tick(self) -> None:
        self._job = None
        now = time.monotonic()
        wake = []
        for animation in list(self._animations):
            try:
                wake.append(animation.tick(now))
            except Exception as exc:
                print(f"[!] Animation stopped: {exc}")
                self._animations.remove(animation)
        if self._animations:
            self._schedule(min(wake) - time.monotonic())


class VideoBackdrop:
    """Streams a video into one reused PhotoImage on the canvas.

    Shows the newest frame that is due and drops older ones, at most MAX_FPS.
    While the canvas is not viewable nothing is pasted; the full queue then
    blocks the decoder, so a hidden video costs almost nothing.
    """

    def __init__(self, canvas: tk.Canvas, stream: VideoStream,
                 size: tuple[int, int], tag: str):
        self._canvas = canvas
        self._stream = stream
        self._tag = tag
        self._photo: Optional[ImageTk.PhotoImage] = None
        self._pending: Optional[Frame] = None
        self._anchor: Optional[float] = None    # monotonic time of video t=0
        self._last_shown = 0.0
        self._create_photo(size)
        stream.start()

    @property
    def size(self) -> tuple[int, int]:
        return self._photo.width(), self._photo.height()

    def resize(self, size: tuple[int, int]) -> None:
        self._stream.set_size(size)
        self._create_photo(size)

    def close(self) -> None:
        self._stream.stop()
        self._canvas.delete(self._tag)
        self._photo = None

    def _create_photo(self, size: tuple[int, int]) -> None:
        self._canvas.delete(self._tag)      # free the old image first
        self._photo = None
        self._photo = ImageTk.PhotoImage("RGB", size)
        self._canvas.create_image(0, 0, image=self._photo, anchor="nw", tags=self._tag)

    def tick(self, now: float) -> float:
        if not self._canvas.winfo_viewable():
            self._anchor = None             # re-sync on return, don't fast-forward
            return now + HIDDEN_POLL_S

        frame = self._next_due(now)
        if frame is not None:
            image = frame[1]
            if image.size == self.size:     # skip frames from before a resize
                self._photo.paste(image)
            self._last_shown = now

        wake = now + 1 / MAX_FPS
        if self._pending is not None and self._anchor is not None:
            wake = max(self._anchor + self._pending[0], self._last_shown + 1 / MAX_FPS)
        return wake

    def _next_due(self, now: float) -> Optional[Frame]:
        """Newest frame whose time has come; older due frames are dropped."""
        due = None
        while True:
            if self._pending is None:
                self._pending = self._stream.get()
                if self._pending is None:
                    return due
            if self._anchor is None:
                self._anchor = now - self._pending[0]
            if self._anchor + self._pending[0] > now + DUE_TOLERANCE_S:
                return due
            due, self._pending = self._pending, None
