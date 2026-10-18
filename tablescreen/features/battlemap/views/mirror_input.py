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
from collections import OrderedDict
from typing import Callable, Optional

from PIL import Image, ImageColor, ImageTk

from ....core.paths import IMAGES_DIR
from ..model.aoe import AoE, aoe_cells
from ..model.grid import CellArea, area_label, area_to_pixels, drag_area, pixels_to_cell
from ..sprite_images import ghost_image
from .battlemap_view import OVERLAY_TAG, PREVIEW_AOE_TAG, PREVIEW_SPRITES_TAG
from .mirror import MirrorView
from .mirror_tools import MirrorTool, Pos

PREVIEW_TAG = "mirror_preview"      # with OVERLAY_TAG, which the view keeps on top
TABLE_PREVIEW_TAG = "table_preview" # with the table view's PREVIEW_*_TAG layers
PREVIEW_COLOR = "#ffd000"
PREVIEW_SHADOW = "black"
GHOST_OPACITY = 0.4                 # sprite previews: see-through
SHADE_ALPHA = 170                   # of 255: dims what a preview replaces
AOE_FILL_ALPHA = 90                 # of 255: an AoE preview's cells
CLICK_SLOP_PX = 4       # a press and release closer than this is a click
FOCUS_CHECK_MS = 50     # focus moves as FocusOut then FocusIn: look after both
WHEEL_NOTCH = 120       # Windows' delta per mouse notch; touchpads send less
# Preview images kept per surface for reuse. Unbounded, one wild drag over
# many range sizes cached 247 table-sized ghosts (678 MB) and Tk on Windows
# died with "Fail to allocate bitmap"; a drag changes the size with almost
# every cell anyway, so a few recent ones cover going back and forth.
PREVIEW_CACHE_SIZE = 8


class MirrorInput:
    """Runs one tool at a time on a mirror and draws its previews."""

    def __init__(self, mirror: MirrorView, submit: Callable[[str], None]):
        self._mirror = mirror
        self._submit = submit
        self._canvas = mirror.view.canvas
        self._top = self._canvas.winfo_toplevel()
        self._title = self._top.title()
        self.mode: Optional[MirrorTool] = None
        self.table_shown = False            # T: public previews on the table too
        self._press: Optional[Pos] = None       # where the button went down
        self._pointer: Optional[Pos] = None     # last position on the map
        self._armed = False     # the mirror had focus since the mode started
        self._wheel = 0         # delta not yet worth a whole notch
        self._wheel_events = False              # <MouseWheel> seen (Tk 9 on X11)
        self._mirror_surface = _Surface(self._canvas, lambda: self.cell,
                                        self.map_size, PREVIEW_TAG)
        table = mirror.table
        self._table = _Surface(
            table.canvas, table.current_cell_px,
            lambda: (table.canvas.winfo_width(), table.canvas.winfo_height()),
            TABLE_PREVIEW_TAG)
        self._table_dirty = False           # drew on the table: restack it

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
        for key in ("<KeyPress-t>", "<KeyPress-T>"):
            self._top.bind(key, lambda e: self._toggle_table(), add="+")
        self._top.bind("<FocusIn>", self._on_focus_in, add="+")
        self._top.bind("<FocusOut>", self._on_focus_out, add="+")

    # ── Modes ─────────────────────────────────────────────────────────────

    def start(self, tool: MirrorTool) -> None:
        if self.mode is not None:
            self.stop(f"now {tool.label}")
        tool.ctx = self
        self.mode = tool
        self.table_shown = False
        self._press, self._wheel = None, 0
        self._armed = self._has_focus()
        self._canvas.configure(cursor="crosshair")
        self._set_title()
        table = " T shows the preview on the table too." if tool.public_preview else ""
        print(f"[+] Mirror: {tool.hint} Esc, a right-click or leaving the mirror "
              f"stops.{table}")
        self.focus()

    def stop(self, reason: str) -> None:
        if self.mode is None:
            return
        label, self.mode = self.mode.label, None
        self._press = None
        self.table_shown = False
        self.clear()
        self._mirror_surface.cache.clear()
        self._table.cache.clear()
        self._canvas.configure(cursor="")
        self._top.title(self._title)
        print(f"[i] Mirror: stopped {label} ({reason}).")

    def submit(self, line: str) -> None:
        """For tools: queue a command; a one-off mode then ends."""
        self._submit(line)
        if self.mode is not None and self.mode.once:
            self.stop("done")

    def _set_title(self) -> None:
        shown = ", shown on the table" if self.table_shown else ""
        self._top.title(f"{self._title} — {self.mode.label}{shown} (Esc to stop)")

    def _toggle_table(self) -> None:
        """T: show this mode's result previews on the table too, or stop."""
        tool = self.mode
        if tool is None or not tool.public_preview:
            return                      # e.g. fog reveal: the DM's decision
        self.table_shown = not self.table_shown
        self._set_title()
        print("[i] Mirror: the preview is shown on the table too."
              if self.table_shown else "[i] Mirror: the preview is on the mirror only.")
        self.clear()
        if self._pointer is not None:
            tool.redraw(self._pointer, pressing=self._press is not None)
        self._restack_table()

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
            self._pointer = self._clamp(event.x, event.y)
            self.mode.hover(self._pointer)
            self._restack_table()

    def _on_leave(self, event) -> None:
        if self.mode is not None and self._press is None:
            self._pointer = None
            self.mode.leave()

    def _on_press(self, event) -> None:
        if self.mode is None:
            return
        self._armed = True      # in use, whether or not Windows gave focus
        self._press = self._pointer = self._clamp(event.x, event.y)
        self.mode.press(self._press)
        self._restack_table()

    def _on_drag(self, event) -> None:
        if self.mode is not None and self._press is not None:
            self._pointer = self._clamp(event.x, event.y)
            self.mode.drag(self._pointer)
            self._restack_table()

    def _on_release(self, event) -> None:
        if self.mode is None or self._press is None:
            return
        start, self._press = self._press, None
        end = self._clamp(event.x, event.y)
        self.mode.release(end, math.dist(start, end) < CLICK_SLOP_PX)
        self._restack_table()

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
            self._restack_table()

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
        self._mirror_surface.clear()
        self._table.clear()

    def _restack_table(self) -> None:
        """Put new table previews on their layers (sprites under the fog)."""
        if self._table_dirty:
            self._table_dirty = False
            self._mirror.table.restack()

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

    def shade(self, area: CellArea, public: bool = False) -> None:
        """Dim an area (e.g. the sprite a rotation preview replaces), so a
        ghost drawn over it stands out. Canvas fills can't be transparent;
        images can."""
        for surface, tags in self._surfaces(public, PREVIEW_SPRITES_TAG):
            cell = surface.cell()
            if not cell:
                continue
            x, y, w, h = area_to_pixels(area, cell)
            size = (max(1, round(w)), max(1, round(h)))
            photo = surface.cached(("shade", size), lambda: ImageTk.PhotoImage(
                Image.new("RGBA", size, (0, 0, 0, SHADE_ALPHA))))
            surface.show_image(photo, x, y, "nw", tags)

    def aoe_preview(self, aoe: AoE, color: str = PREVIEW_COLOR, label: str = "",
                    fill: bool = True, public: bool = False) -> None:
        """An area of effect as it would be drawn: its cells filled
        see-through (unless ``fill`` is off, for an outline-only highlight),
        the outline around them and a dot on the origin, with ``label`` beside
        the dot. On the table (``public``): outline and dot only, since a
        table-sized see-through fill is re-blended on every video frame."""
        for surface, tags in self._surfaces(public, PREVIEW_AOE_TAG):
            cell = surface.cell()
            if not cell:
                continue
            on_table = surface is self._table
            map_w, map_h = surface.size()
            cells = {(r, c) for r, c in aoe_cells(aoe)
                     if (c - 1) * cell < map_w and (r - 1) * cell < map_h}
            if cells and fill and not on_table:
                image, x0, y0 = cells_overlay(cells, cell, color, AOE_FILL_ALPHA)
                surface.show_image(ImageTk.PhotoImage(image), x0, y0, "nw", tags)
            canvas, edge = surface.canvas, max(2, round(cell / 22))
            for row, col in cells:
                x, y = (col - 1) * cell, (row - 1) * cell
                for (dr, dc), line in (((-1, 0), (x, y, x + cell, y)),
                                       ((1, 0), (x, y + cell, x + cell, y + cell)),
                                       ((0, -1), (x, y, x, y + cell)),
                                       ((0, 1), (x + cell, y, x + cell, y + cell))):
                    if (row + dr, col + dc) not in cells:
                        canvas.create_line(*line, fill=color, width=edge,
                                           capstyle="projecting", tags=tags)
            x, y = (aoe.col - 1) * cell, (aoe.row - 1) * cell
            if not aoe.corner:
                x, y = x + cell / 2, y + cell / 2
            r = max(3.0, cell * 0.1)
            canvas.create_oval(x - r, y - r, x + r, y + r, fill=color,
                               outline="black", width=2, tags=tags)
            if label and not on_table:
                self.text(x + r + 4, y - self._mirror.font.metrics("linespace") / 2,
                          label, color)

    def ghost(self, file: str, rotation: float, area: CellArea,
              public: bool = False) -> None:
        """A see-through sprite fitted in the area, as the battlemap would draw
        it. Images are cached per size until the mode ends."""
        for surface, tags in self._surfaces(public, PREVIEW_SPRITES_TAG):
            cell = surface.cell()
            if not cell:
                continue
            x, y, w, h = area_to_pixels(area, cell)

            def make() -> Optional[ImageTk.PhotoImage]:
                try:
                    return ImageTk.PhotoImage(ghost_image(
                        IMAGES_DIR / file, rotation, (w, h), GHOST_OPACITY))
                except Exception:
                    return None         # the command reports the problem
            photo = surface.cached((file, rotation % 360, round(w), round(h)), make)
            if photo is not None:
                surface.show_image(photo, x + w / 2, y + h / 2, "center", tags)

    def _surfaces(self, public: bool, table_layer: str) -> list[tuple["_Surface", tuple]]:
        """Where to draw: the mirror (above everything), plus the table when
        the drawing is public and T is on (on ``table_layer``)."""
        targets = [(self._mirror_surface, self._tags())]
        if public and self.table_shown:
            targets.append((self._table, (TABLE_PREVIEW_TAG, table_layer)))
            self._table_dirty = True
        return targets

def cells_overlay(cells: set[tuple[int, int]], cell: float, color: str,
                  alpha: int) -> tuple[Image.Image, float, float]:
    """A see-through image covering just these cells (``color`` at ``alpha``)
    and where its top-left goes on the canvas. Sized to the cells' bounding
    box, so a preview never needs a canvas-sized image."""
    top = min(r for r, _ in cells)
    left = min(c for _, c in cells)
    rows = max(r for r, _ in cells) - top + 1
    cols = max(c for _, c in cells) - left + 1
    x0, y0 = (left - 1) * cell, (top - 1) * cell
    image = Image.new("RGBA", (max(1, math.ceil(cols * cell)),
                               max(1, math.ceil(rows * cell))), (0, 0, 0, 0))
    fill = Image.new("RGBA", (1, 1), (*ImageColor.getrgb(color)[:3], alpha))
    for row, col in cells:
        # Rounded edges, as the fog: neighbouring cells meet without a gap.
        x = round((col - 1) * cell - x0)
        y = round((row - 1) * cell - y0)
        size = (round(col * cell - x0) - x, round(row * cell - y0) - y)
        image.paste(fill.resize(size), (x, y))
    return image, x0, y0


class _Surface:
    """A canvas previews are drawn on, with its cell size, map size, the
    images on screen (Tk needs them referenced) and a per-size image cache."""

    def __init__(self, canvas: tk.Canvas, cell: Callable[[], Optional[float]],
                 size: Callable[[], tuple[int, int]], tag: str):
        self.canvas, self.cell, self.size, self.tag = canvas, cell, size, tag
        self.images: list[ImageTk.PhotoImage] = []
        self.cache: OrderedDict[tuple, Optional[ImageTk.PhotoImage]] = OrderedDict()

    def cached(self, key: tuple, make: Callable[[], Optional[ImageTk.PhotoImage]]):
        """The image for ``key``, made if needed; only the most recently used
        PREVIEW_CACHE_SIZE are kept (one on screen stays alive via ``images``)."""
        if key in self.cache:
            self.cache.move_to_end(key)
        else:
            self.cache[key] = make()
            while len(self.cache) > PREVIEW_CACHE_SIZE:
                self.cache.popitem(last=False)
        return self.cache[key]

    def show_image(self, photo, x: float, y: float, anchor: str, tags: tuple) -> None:
        self.images.append(photo)
        self.canvas.create_image(x, y, image=photo, anchor=anchor, tags=tags)

    def clear(self) -> None:
        self.canvas.delete(self.tag)
        self.images.clear()
