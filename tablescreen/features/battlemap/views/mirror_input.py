"""
mirror_input.py — Mouse input on the DM mirror.

A mirror command (``map mirror fow reveal``) puts the mirror in a MODE with
a tool (see mirror_tools.py); the tool turns clicks, drags and the wheel
into the same command text the DM would type (``map fow reveal B3:D7``),
handed to the feature's submit callback, which queues it on the executor.
Nothing here changes state.

This controller owns what every mode shares: the event bindings, focus,
the crosshair cursor and title, adding up wheel deltas, the preview drawing
helpers (the canvas's overlay layer, above everything) and the exits. A mode
ends on Esc or a right-click (unless the tool only drops something it holds),
another mode, the mirror closing, or the mirror losing focus (only after it
had focus, since the mode is started from the console). Mainloop thread only.
"""

from __future__ import annotations

import math
import tkinter as tk
from typing import Callable, Optional

from PIL import Image, ImageTk

from ....core.paths import IMAGES_DIR
from ..model.grid import CellArea, area_label, area_to_pixels, drag_area, pixels_to_cell
from ..sprite_images import ghost_image
from .battlemap_view import OVERLAY_TAG
from .mirror import MirrorView
from .mirror_tools import MirrorTool, Pos

PREVIEW_TAG = "mirror_preview"      # with OVERLAY_TAG, which the view keeps on top
PREVIEW_COLOR = "#ffd000"
PREVIEW_SHADOW = "black"
GHOST_OPACITY = 0.4                 # sprite previews: see-through
SHADE_ALPHA = 170                   # of 255: dims what a preview replaces
CLICK_SLOP_PX = 4       # a press and release closer than this is a click
FOCUS_CHECK_MS = 50     # focus moves as FocusOut then FocusIn: look after both
WHEEL_NOTCH = 120       # Windows' delta per mouse notch; touchpads send less


class MirrorInput:
    """Runs one tool at a time on a mirror and draws its previews."""

    def __init__(self, mirror: MirrorView, submit: Callable[[str], None]):
        self._mirror = mirror
        self._submit = submit
        self._canvas = mirror.view.canvas
        self._top = self._canvas.winfo_toplevel()
        self._title = self._top.title()
        self.mode: Optional[MirrorTool] = None
        self._press: Optional[Pos] = None       # where the button went down
        self._armed = False     # the mirror had focus since the mode started
        self._wheel = 0         # delta not yet worth a whole notch
        self._wheel_events = False              # <MouseWheel> seen (Tk 9 on X11)
        self._images: list[ImageTk.PhotoImage] = []     # previews on screen
        self._ghosts: dict[tuple, Optional[ImageTk.PhotoImage]] = {}

        canvas = self._canvas
        canvas.bind("<Motion>", self._on_hover, add="+")
        canvas.bind("<Leave>", self._on_leave, add="+")
        canvas.bind("<ButtonPress-1>", self._on_press, add="+")
        canvas.bind("<B1-Motion>", self._on_drag, add="+")
        canvas.bind("<ButtonRelease-1>", self._on_release, add="+")
        canvas.bind("<ButtonPress-3>", lambda e: self._cancel("right-click"), add="+")
        canvas.bind("<MouseWheel>", self._on_wheel, add="+")
        canvas.bind("<Button-4>", lambda e: self._on_button_wheel(WHEEL_NOTCH), add="+")
        canvas.bind("<Button-5>", lambda e: self._on_button_wheel(-WHEEL_NOTCH), add="+")
        # On the toplevel: its bindtag is on every widget inside it.
        self._top.bind("<Escape>", lambda e: self._cancel("Esc"), add="+")
        self._top.bind("<FocusIn>", self._on_focus_in, add="+")
        self._top.bind("<FocusOut>", self._on_focus_out, add="+")

    # ── Modes ─────────────────────────────────────────────────────────────

    def start(self, tool: MirrorTool) -> None:
        if self.mode is not None:
            self.stop(f"now {tool.label}")
        tool.ctx = self
        self.mode = tool
        self._press, self._wheel = None, 0
        self._armed = self._has_focus()
        self._canvas.configure(cursor="crosshair")
        self._top.title(f"{self._title} — {tool.label} (Esc to stop)")
        print(f"[+] Mirror: {tool.hint} Esc, a right-click or leaving the mirror "
              f"stops.")
        self.focus()

    def stop(self, reason: str) -> None:
        if self.mode is None:
            return
        label, self.mode = self.mode.label, None
        self._press = None
        self.clear()
        self._ghosts.clear()
        self._canvas.configure(cursor="")
        self._top.title(self._title)
        print(f"[i] Mirror: stopped {label} ({reason}).")

    def submit(self, line: str) -> None:
        """For tools: queue a command; a one-off mode then ends."""
        self._submit(line)
        if self.mode is not None and self.mode.once:
            self.stop("done")

    def _cancel(self, reason: str) -> None:
        if self.mode is not None and not self.mode.cancel():
            self.stop(reason)

    # ── Focus ─────────────────────────────────────────────────────────────

    def focus(self) -> None:
        """Bring the mirror to the front with keyboard focus. Windows may
        refuse a program taking the foreground; then the first click on the
        mirror gives it focus instead."""
        top = self._top
        top.deiconify()
        top.lift()

        def force(tries: int = 10) -> None:
            if not top.winfo_exists():
                return
            if top.winfo_viewable():
                self._canvas.focus_force()
            elif tries:
                top.after(50, force, tries - 1)     # not mapped yet (just opened)
        top.after_idle(force)

    def _has_focus(self) -> bool:
        try:
            widget = self._top.focus_get()
        except (KeyError, tk.TclError):
            return False
        return widget is not None and widget.winfo_toplevel() is self._top

    def _on_focus_in(self, event) -> None:
        if self.mode is not None:
            self._armed = True

    def _on_focus_out(self, event) -> None:
        if self.mode is not None and self._armed:
            self._top.after(FOCUS_CHECK_MS, self._check_focus)

    def _check_focus(self) -> None:
        if self.mode is not None and self._armed and not self._has_focus():
            self.stop("the mirror lost focus")

    # ── Mouse ─────────────────────────────────────────────────────────────

    def _on_hover(self, event) -> None:
        if self.mode is not None and self._press is None:
            self.mode.hover(self._clamp(event.x, event.y))

    def _on_leave(self, event) -> None:
        if self.mode is not None and self._press is None:
            self.mode.leave()

    def _on_press(self, event) -> None:
        if self.mode is None:
            return
        self._armed = True      # in use, whether or not Windows gave focus
        self._press = self._clamp(event.x, event.y)
        self.mode.press(self._press)

    def _on_drag(self, event) -> None:
        if self.mode is not None and self._press is not None:
            self.mode.drag(self._clamp(event.x, event.y))

    def _on_release(self, event) -> None:
        if self.mode is None or self._press is None:
            return
        start, self._press = self._press, None
        end = self._clamp(event.x, event.y)
        self.mode.release(end, math.dist(start, end) < CLICK_SLOP_PX)

    def _on_wheel(self, event) -> None:
        self._wheel_events = True
        self._add_wheel(event.delta)

    def _on_button_wheel(self, delta: int) -> None:
        if not self._wheel_events:      # Tk 9 sends <MouseWheel> instead
            self._add_wheel(delta)

    def _add_wheel(self, delta: int) -> None:
        """Hand the tool whole notches; a touchpad's small deltas add up."""
        if self.mode is None:
            return
        self._wheel += delta
        steps = int(self._wheel / WHEEL_NOTCH)      # towards zero
        if steps:
            self._wheel -= steps * WHEEL_NOTCH
            self.mode.wheel(steps)

    def _clamp(self, x: float, y: float) -> Pos:
        """Keep a pointer that leaves the map on its edge."""
        width, height = self._mirror.map_size()
        return min(max(x, 0), width - 1), min(max(y, 0), height - 1)

    # ── For tools: the map ────────────────────────────────────────────────

    @property
    def cell(self) -> Optional[float]:
        return self._mirror.view.current_cell_px()

    @property
    def style(self) -> str:
        return self._mirror.view.coords_style

    @property
    def sprites(self) -> tuple:
        return self._mirror.view.sprites

    @property
    def aoes(self) -> tuple:
        return self._mirror.view.aoes

    def map_size(self) -> tuple[int, int]:
        return self._mirror.map_size()

    def cell_at(self, pos: Pos) -> Optional[tuple[int, int]]:
        """(row, col) under a map pixel, None while the cell size is unknown."""
        cell = self.cell
        return pixels_to_cell(*pos, cell) if cell else None

    def selection(self, start: Pos, end: Pos) -> Optional[CellArea]:
        """A click's cell or a drag's cells (see grid.drag_area)."""
        cell = self.cell
        if not cell:
            return None
        if math.dist(start, end) < CLICK_SLOP_PX:
            row, col = pixels_to_cell(*start, cell)
            return CellArea(row, col, row, col)
        return drag_area(start, end, cell, self.map_size())

    # ── For tools: previews (mirror only, never state) ────────────────────

    def clear(self) -> None:
        self._canvas.delete(PREVIEW_TAG)
        self._images.clear()

    def _tags(self) -> tuple[str, str]:
        return PREVIEW_TAG, OVERLAY_TAG

    def dashed_rect(self, start: Pos, end: Pos) -> None:
        self._canvas.create_rectangle(*start, *end, outline="white", dash=(4, 4),
                                      tags=self._tags())

    def outline(self, area: CellArea, color: str = PREVIEW_COLOR, width: int = 3) -> None:
        cell = self.cell
        if not cell:
            return
        map_w, map_h = self.map_size()
        x, y, w, h = area_to_pixels(area, cell)
        self._canvas.create_rectangle(x, y, min(x + w, map_w), min(y + h, map_h),
                                      outline=color, width=width, tags=self._tags())

    def text(self, x: float, y: float, text: str, color: str = PREVIEW_COLOR) -> None:
        """Readable on any map: the ruler font with a shadow."""
        for dx, fill in ((1, PREVIEW_SHADOW), (0, color)):
            self._canvas.create_text(x + dx, y + dx, text=text, anchor="nw",
                                     font=self._mirror.font, fill=fill,
                                     tags=self._tags())

    def area_label(self, area: CellArea, color: str = PREVIEW_COLOR) -> None:
        """The range (``B3:D7``) in the area's top-left corner."""
        self.label_at(area, area_label(area, self.style), color)

    def label_at(self, area: CellArea, text: str, color: str = PREVIEW_COLOR) -> None:
        """Text in the area's top-left corner."""
        cell = self.cell
        if cell:
            x, y, _, _ = area_to_pixels(area, cell)
            self.text(x + 5, y + 4, text, color)

    def shade(self, area: CellArea) -> None:
        """Dim an area (e.g. the sprite a rotation preview replaces), so a
        ghost drawn over it stands out. Canvas fills can't be transparent;
        images can."""
        cell = self.cell
        if not cell:
            return
        x, y, w, h = area_to_pixels(area, cell)
        size = (max(1, round(w)), max(1, round(h)))
        key = ("shade", size)
        if key not in self._ghosts:
            self._ghosts[key] = ImageTk.PhotoImage(
                Image.new("RGBA", size, (0, 0, 0, SHADE_ALPHA)))
        photo = self._ghosts[key]
        self._images.append(photo)
        self._canvas.create_image(x, y, image=photo, anchor="nw", tags=self._tags())

    def ghost(self, file: str, rotation: float, area: CellArea) -> None:
        """A see-through sprite fitted in the area, as the battlemap would draw
        it. Images are cached per size until the mode ends."""
        cell = self.cell
        if not cell:
            return
        x, y, w, h = area_to_pixels(area, cell)
        key = (file, rotation % 360, round(w), round(h))
        if key not in self._ghosts:
            try:
                image = ghost_image(IMAGES_DIR / file, rotation, (w, h), GHOST_OPACITY)
                self._ghosts[key] = ImageTk.PhotoImage(image)
            except Exception:
                self._ghosts[key] = None    # the command reports the problem
        photo = self._ghosts[key]
        if photo is not None:
            self._images.append(photo)
            self._canvas.create_image(x + w / 2, y + h / 2, image=photo,
                                      anchor="center", tags=self._tags())
