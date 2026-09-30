"""
animation.py — The mainloop side of animation. Mainloop thread only.

AnimationClock is the single after() loop that drives everything animated
on a canvas (video backdrop, animated sprites), so animations do not run
competing timers.
"""

from __future__ import annotations

import bisect
import itertools
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


def frame_at(durations: list[float], elapsed: float) -> tuple[int, float]:
    """Frame index at ``elapsed`` seconds into a looping animation, and the
    seconds until the next frame starts. Derived from time, not counted per
    tick, so a late tick skips ahead instead of drifting."""
    total = sum(durations)
    t = elapsed % total
    ends = list(itertools.accumulate(durations))
    # Tolerance for float sums: at t=0.3, frames of 0.1+0.2 must be over.
    index = bisect.bisect_right(ends, t + 1e-6)
    if index == len(ends):                  # a hair before the loop restarts
        return 0, ends[0] + total - t
    return index, ends[index] - t


class SpriteAnimation:
    """Cycles an animated sprite's frames on its canvas items.

    One per (file, size): sprites sharing a file stay in sync and share the
    frames. The view reassigns ``items`` on every sprite redraw, and passes
    the old ``start`` on a resize, so neither restarts the animation.
    """

    def __init__(self, canvas: tk.Canvas, frames: list[ImageTk.PhotoImage],
                 durations: list[float], start: Optional[float] = None):
        self._canvas = canvas
        self.frames = frames
        self._durations = durations
        self.start = time.monotonic() if start is None else start
        self.items: list[int] = []
        self._shown: Optional[int] = None

    def tick(self, now: float) -> float:
        if not self._canvas.winfo_viewable():
            return now + HIDDEN_POLL_S
        index, until_next = frame_at(self._durations, now - self.start)
        if index != self._shown:
            for item in self.items:
                self._canvas.itemconfigure(item, image=self.frames[index])
            self._shown = index
        return now + until_next

    def current_frame(self) -> ImageTk.PhotoImage:
        """The frame to create new items with, so a redraw doesn't flash frame 0."""
        return self.frames[frame_at(self._durations, time.monotonic() - self.start)[0]]

    def set_items(self, items: list[int]) -> None:
        self.items = items
        self._shown = None      # re-apply on the next tick


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
