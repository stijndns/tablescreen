"""
mirror_input.py — Mouse input on the DM mirror.

A mirror command (``map mirror fow reveal``) puts the mirror in a MODE; the
mouse then supplies the cells: a click selects one cell, a drag a rectangle
of cells (see grid.drag_area). The result is the same command text the DM
would type (``map fow reveal B3:D7``), handed to the feature's submit
callback, which queues it on the executor. Nothing here changes state.

A mode ends on Esc, a right-click, another mode, the mirror closing, or the
mirror losing focus (only after it had focus, since the mode is started
from the console). While one is on, the cursor is a crosshair and the title
says which mode it is. Mainloop thread only.
"""

from __future__ import annotations

import math
import tkinter as tk
from dataclasses import dataclass
from typing import Callable, Optional

from ..model.grid import CellArea, area_label, area_to_pixels, drag_area, pixels_to_cell
from .mirror import MirrorView

PREVIEW_TAG = "mirror_preview"
PREVIEW_COLOR = "#ffd000"
PREVIEW_SHADOW = "black"
CLICK_SLOP_PX = 4       # a press and release closer than this is a click
FOCUS_CHECK_MS = 50     # focus moves as FocusOut then FocusIn: look after both


@dataclass(frozen=True)
class MirrorMode:
    """What the mouse does on the mirror until the mode ends."""
    label: str                          # "revealing fog": title and messages
    hint: str                           # printed when the mode starts
    command: Callable[[CellArea], str]  # selected cells → command line
    once: bool = False                  # ends after one use


class MirrorInput:
    """Turns clicks and drags on a mirror into command lines."""

    def __init__(self, mirror: MirrorView, submit: Callable[[str], None]):
        self._mirror = mirror
        self._submit = submit
        self._canvas = mirror.view.canvas
        self._top = self._canvas.winfo_toplevel()
        self._title = self._top.title()
        self.mode: Optional[MirrorMode] = None
        self._press: Optional[tuple[float, float]] = None   # drag start
        self._armed = False     # the mirror had focus since the mode started

        canvas = self._canvas
        canvas.bind("<ButtonPress-1>", self._on_press, add="+")
        canvas.bind("<B1-Motion>", self._on_motion, add="+")
        canvas.bind("<ButtonRelease-1>", self._on_release, add="+")
        canvas.bind("<ButtonPress-3>", lambda e: self.stop("right-click"), add="+")
        # On the toplevel: its bindtag is on every widget inside it.
        self._top.bind("<Escape>", lambda e: self.stop("Esc"), add="+")
        self._top.bind("<FocusIn>", self._on_focus_in, add="+")
        self._top.bind("<FocusOut>", self._on_focus_out, add="+")

    # ── Modes ─────────────────────────────────────────────────────────────

    def start(self, mode: MirrorMode) -> None:
        if self.mode is not None:
            self.stop(f"now {mode.label}")
        self.mode = mode
        self._armed = self._has_focus()
        self._canvas.configure(cursor="crosshair")
        self._top.title(f"{self._title} — {mode.label} (Esc to stop)")
        print(f"[+] Mirror: {mode.hint} Esc, a right-click or leaving the mirror "
              f"stops.")
        self.focus()

    def stop(self, reason: str) -> None:
        if self.mode is None:
            return
        label, self.mode = self.mode.label, None
        self._cancel_drag()
        self._canvas.configure(cursor="")
        self._top.title(self._title)
        print(f"[i] Mirror: stopped {label} ({reason}).")

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

    def _on_press(self, event) -> None:
        if self.mode is None:
            return
        self._armed = True      # in use, whether or not Windows gave focus
        self._press = self._clamp(event.x, event.y)
        self._draw_preview(self._press)

    def _on_motion(self, event) -> None:
        if self._press is not None:
            self._draw_preview(self._clamp(event.x, event.y))

    def _on_release(self, event) -> None:
        if self._press is None or self.mode is None:
            return
        area = self._selection(self._press, self._clamp(event.x, event.y))
        self._cancel_drag()
        if area is None:
            print("[i] Mirror: that rectangle covers no cell; nothing changed.")
            return
        mode = self.mode
        self._submit(mode.command(area))
        if mode.once:
            self.stop("done")

    def _clamp(self, x: float, y: float) -> tuple[float, float]:
        """Keep a drag that leaves the map on its edge."""
        width, height = self._mirror.map_size()
        return min(max(x, 0), width - 1), min(max(y, 0), height - 1)

    def _selection(self, start: tuple[float, float],
                   end: tuple[float, float]) -> Optional[CellArea]:
        cell = self._mirror.view.current_cell_px()
        if not cell:
            return None
        if math.dist(start, end) < CLICK_SLOP_PX:
            row, col = pixels_to_cell(*start, cell)
            return CellArea(row, col, row, col)
        return drag_area(start, end, cell, self._mirror.map_size())

    # ── Preview (mirror only, never state) ────────────────────────────────

    def _draw_preview(self, end: tuple[float, float]) -> None:
        canvas = self._canvas
        canvas.delete(PREVIEW_TAG)
        (x0, y0), (x1, y1) = self._press, end
        canvas.create_rectangle(x0, y0, x1, y1, outline="white", dash=(4, 4),
                                tags=PREVIEW_TAG)
        area = self._selection(self._press, end)
        cell = self._mirror.view.current_cell_px()
        if area is None or not cell:
            return
        width, height = self._mirror.map_size()
        x, y, w, h = area_to_pixels(area, cell)
        canvas.create_rectangle(x, y, min(x + w, width), min(y + h, height),
                                outline=PREVIEW_COLOR, width=3, tags=PREVIEW_TAG)
        text = area_label(area, self._mirror.view.coords_style)
        for dx, color in ((1, PREVIEW_SHADOW), (0, PREVIEW_COLOR)):
            canvas.create_text(x + 5 + dx, y + 4 + dx, text=text, anchor="nw",
                               font=self._mirror.font, fill=color, tags=PREVIEW_TAG)

    def _cancel_drag(self) -> None:
        self._press = None
        self._canvas.delete(PREVIEW_TAG)
