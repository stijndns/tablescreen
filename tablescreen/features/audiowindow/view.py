"""
view.py — The image display, with the puzzle timer on top.

Draws an image, letterboxed, into whatever frame it was given, and the timer
(if one is running) as large digits in the middle. A canvas rather than a
label: a label can't be transparent, so the digits would sit in a black box.

It knows nothing about windows, fullscreen, queues or other features. Core
calls ``render()`` when the slot content should update, and again via a
geometry callback when the window is resized or fullscreened. The timer
redraws itself from the clock while one is shown (see timer.py).
"""

from __future__ import annotations

import time
import tkinter as tk
from typing import Optional

from PIL import Image, ImageTk

from ...core.paths import IMAGES_DIR
from .timer import Timer

TIMER_TAG = "timer"
TICK_MS = 100                   # redraws: the digits change on time, give or take this
BLINK_S = 0.5                   # on and off when it ran out
FONT_FAMILY = "Consolas"        # same width for every digit, so nothing jumps
TEXT_COLOR = "#ffffff"
RAN_OUT_COLOR = "#ff3b30"
OUTLINE_COLOR = "#000000"
# Black copies around the white digits: an outline, readable on any image.
OUTLINE = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy]


class ImageView:
    """Displays one image, scaled to fit its frame while preserving aspect,
    and the timer over it."""

    def __init__(self, parent: tk.Widget):
        self.canvas = tk.Canvas(parent, bg="black", highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        self._original: Optional[Image.Image] = None
        self._photo: Optional[ImageTk.PhotoImage] = None
        self._filename: Optional[str] = None
        self._image_item: Optional[int] = None

        self._timer: Optional[Timer] = None
        self._tick_id: Optional[str] = None
        self._shown: Optional[tuple] = None     # (text, colour, visible) last drawn
        self._announced = False                 # "ran out" printed for this run

        # Re-render when the frame itself changes size (the window was
        # resized), not only when a new image is shown.
        self.canvas.bind("<Configure>", lambda e: self.rescale())

    # ── Loading ───────────────────────────────────────────────────────────

    def load(self, filename: str) -> bool:
        """Load an image by name, relative to assets/images."""
        path = IMAGES_DIR / filename
        if not path.exists():
            print(f"[!] File not found: {path}")
            return False
        try:
            self._original = Image.open(path)
            self._filename = filename
            print(f"[+] Loaded image: {filename}")
            return True
        except Exception as exc:
            print(f"[!] Error loading image: {exc}")
            return False

    # ── Rendering ─────────────────────────────────────────────────────────

    def render(self, snapshot: dict) -> None:
        """Render from a feature snapshot. Loads the image if it changed."""
        filename = snapshot.get("image")
        if filename and filename != self._filename:
            if self.load(filename):
                self.rescale()
        else:
            self.rescale()
        self.set_timer(snapshot.get("timer"))

    def _size(self) -> Optional[tuple[int, int]]:
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        # During construction the widget reports 1x1; nothing useful to draw.
        return (width, height) if width > 1 and height > 1 else None

    def rescale(self) -> None:
        """Redraw the current image letterboxed into the frame, and put the
        timer back in the middle at its new size."""
        size = self._size()
        if size is None:
            return
        width, height = size
        if self._original is not None:
            img = self._original.copy()
            img_w, img_h = img.size
            scale = min(width / img_w, height / img_h)
            new_w = max(1, int(img_w * scale))
            new_h = max(1, int(img_h * scale))
            img = img.resize((new_w, new_h), Image.LANCZOS)  # type: ignore[attr-defined]
            # Keep a reference: Tk does not own PhotoImage data, so dropping
            # the last Python reference would blank the image.
            self._photo = ImageTk.PhotoImage(img)
            if self._image_item is None:
                self._image_item = self.canvas.create_image(width // 2, height // 2,
                                                            image=self._photo)
            else:
                self.canvas.itemconfig(self._image_item, image=self._photo)
                self.canvas.coords(self._image_item, width // 2, height // 2)
            self.canvas.tag_lower(self._image_item)
        if self._timer is not None:
            self._create_timer_items()

    def clear(self) -> None:
        if self._image_item is not None:
            self.canvas.delete(self._image_item)
        self._image_item = None
        self._original = None
        self._photo = None
        self._filename = None

    # ── The timer ─────────────────────────────────────────────────────────

    def set_timer(self, timer: Optional[Timer]) -> None:
        """Show this timer (None: none). Redraws itself until it is gone."""
        self._timer = timer
        if timer is None:
            self.canvas.delete(TIMER_TAG)
            self._shown = None
            self._announced = False
            if self._tick_id is not None:
                self.canvas.after_cancel(self._tick_id)
                self._tick_id = None
            return
        if not self.canvas.find_withtag(TIMER_TAG):
            self._create_timer_items()
        self._draw_timer()
        if self._tick_id is None:
            self._tick_id = self.canvas.after(TICK_MS, self._tick)

    def _tick(self) -> None:
        self._tick_id = None
        if self._timer is None:
            return
        self._draw_timer()
        self._tick_id = self.canvas.after(TICK_MS, self._tick)

    def _create_timer_items(self) -> None:
        """The digits' items at the window's size: outline copies, then the
        digits themselves on top."""
        self.canvas.delete(TIMER_TAG)
        self._shown = None
        size = self._size()
        if size is None:
            return
        width, height = size
        # As large as fits: up to 0:00:00 in about 90 % of the width.
        font_px = max(12, int(min(height * 0.24, width * 0.2)))
        font = (FONT_FAMILY, -font_px, "bold")
        offset = max(2, font_px // 28)
        x, y = width // 2, height // 2
        for dx, dy in OUTLINE:
            self.canvas.create_text(x + dx * offset, y + dy * offset, text="", font=font,
                                    fill=OUTLINE_COLOR, tags=(TIMER_TAG, "timer-outline"))
        self.canvas.create_text(x, y, text="", font=font, fill=TEXT_COLOR,
                                tags=(TIMER_TAG, "timer-digits"))
        self.canvas.tag_raise(TIMER_TAG)
        self._draw_timer()

    def _draw_timer(self) -> None:
        timer = self._timer
        if timer is None:
            return
        now = time.monotonic()
        ran_out = timer.ran_out(now)
        if ran_out and not self._announced:
            print("[!] The timer ran out.")
        self._announced = ran_out
        # Hidden: still drawn from the clock (the ran-out line), just not shown.
        visible = not timer.hidden and (not ran_out or int(now / BLINK_S) % 2 == 0)
        shown = (timer.shown(now), RAN_OUT_COLOR if ran_out else TEXT_COLOR, visible)
        if shown == self._shown:
            return
        text, color, visible = shown
        self.canvas.itemconfig(TIMER_TAG, text=text, state="normal" if visible else "hidden")
        self.canvas.itemconfig("timer-digits", fill=color)
        self._shown = shown
