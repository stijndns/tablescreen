"""
mirror.py — The DM's copy of the table view, in its own window.

A letterboxed copy: the table canvas scaled uniformly to fit the mirror
window, centred, with black bars around it. The mirror's pixels per inch is
the table's times that scale, so every layer lands on the same cells as on
the table, partial edge cells included. The canvas itself is sized (not
offset), so its origin is the map's top-left. Mainloop thread only.

Coordinates are rulers in a margin (column numbers above, row labels left)
at a fixed readable size, instead of the table's cell-sized labels, which
are too small to read at mirror sizes.
"""

from __future__ import annotations

import math
import tkinter as tk
import tkinter.font as tkfont
from typing import Optional

from ..model.grid import (MIN_CELL_PX, GridSettings, letterbox, row_label,
                          ruler_marks, ruler_step)
from ..styling import FONT_FAMILY
from .battlemap_view import BackdropCache, BattleMapView, PpiProvider

RULER_FONT_PX = 12      # at 96 DPI; follows the display scaling
RULER_PAD_PX = 3        # idem
RULER_BG = "black"      # same as the letterbox bars


class MirrorView:
    """A BattleMapView that follows the table view's canvas size."""

    def __init__(self, parent: tk.Frame, table: BattleMapView,
                 table_ppi: PpiProvider, grid_settings: GridSettings,
                 backdrops: BackdropCache, fallback_size: tuple[int, int]):
        self._parent = parent
        self._table = table
        self._table_ppi = table_ppi
        self._fallback_size = fallback_size     # table size until it's mapped
        self.scale: Optional[float] = None      # mirror px per table px
        self.view = BattleMapView(parent, grid_settings, self._ppi,
                                  backdrops=backdrops, mirror=True)
        self.view.set_show_grid(True)

        self.show_rulers = True
        dpi_scale = parent.winfo_fpixels("1i") / 96
        self._font = tkfont.Font(root=parent, family=FONT_FAMILY, weight="bold",
                                 size=-round(RULER_FONT_PX * dpi_scale))
        self._pad = max(2, round(RULER_PAD_PX * dpi_scale))
        self._top = tk.Canvas(parent, bg=RULER_BG, bd=0, highlightthickness=0)
        self._left = tk.Canvas(parent, bg=RULER_BG, bd=0, highlightthickness=0)
        self._map_size: Optional[tuple[int, int]] = None    # as last laid out
        self._margins = (0, 0)

        parent.bind("<Configure>", lambda e: self.layout(), add="+")

    def _ppi(self) -> Optional[float]:
        ppi = self._table_ppi()
        return ppi * self.scale if ppi and self.scale else None

    def table_size(self) -> tuple[int, int]:
        canvas = self._table.canvas
        width, height = canvas.winfo_width(), canvas.winfo_height()
        return (width, height) if width > 1 and height > 1 else self._fallback_size

    # ── Per-view settings ─────────────────────────────────────────────────

    @property
    def show_grid(self) -> bool:
        return self.view.show_grid

    def set_show_grid(self, on: bool) -> None:
        self.view.set_show_grid(on)

    def set_show_rulers(self, on: bool) -> None:
        self.show_rulers = on
        self.layout()

    # ── Layout ────────────────────────────────────────────────────────────

    def _ruler_margins(self) -> tuple[int, int]:
        """(left width, top height); room for the longest row label."""
        if not self.show_rulers:
            return 0, 0
        cell = self._table.current_cell_px()
        rows = math.ceil(self.table_size()[1] / cell) if cell else 2
        longest = len(row_label(max(rows, 1), self.view.coords_style))
        width = self._font.measure("W" * longest)      # W: widest letter
        return width + 2 * self._pad, self._font.metrics("linespace") + 2 * self._pad

    def layout(self) -> None:
        """Fit rulers and map to the window. Call after either window resizes
        and after anything that changes the table's cells (scale, style)."""
        box_w, box_h = self._parent.winfo_width(), self._parent.winfo_height()
        margin_x, margin_y = self._margins = self._ruler_margins()
        if box_w - margin_x <= 1 or box_h - margin_y <= 1:
            return
        scale, width, height = letterbox(self.table_size(),
                                         (box_w - margin_x, box_h - margin_y))
        # Rulers and map are centred together.
        x0 = (box_w - margin_x - width) // 2
        y0 = (box_h - margin_y - height) // 2
        changed = scale != self.scale
        self.scale = scale
        self.view.min_cell_px = MIN_CELL_PX * scale    # the table's threshold
        self.view.place(x=x0 + margin_x, y=y0 + margin_y, width=width, height=height)
        if self.show_rulers:
            self._top.place(x=x0 + margin_x, y=y0, width=width, height=margin_y)
            self._left.place(x=x0, y=y0 + margin_y, width=margin_x, height=height)
        else:
            self._top.place_forget()
            self._left.place_forget()
        self._map_size = (width, height)

        canvas = self.view.canvas
        if changed and (canvas.winfo_width(), canvas.winfo_height()) == (width, height):
            self.view.rescale()     # same size, new cells: no <Configure> follows
        self._draw_rulers()

    def _draw_rulers(self) -> None:
        self._top.delete("all")
        self._left.delete("all")
        cell = self.view.current_cell_px()
        if not self.show_rulers or self._map_size is None or not cell:
            return
        width, height = self._map_size
        margin_x, margin_y = self._margins
        font, pad = self._font, self._pad
        color, style = self.view.label_color, self.view.coords_style

        # A partial edge cell's label would stick out past the map: dropped.
        widest_col = str(math.ceil(width / cell))
        col_step = ruler_step(cell, font.measure(widest_col) + 2 * pad)
        for col, x in ruler_marks(width, cell, col_step) if col_step else ():
            if x + font.measure(str(col)) / 2 <= width:
                self._top.create_text(x, margin_y / 2, text=str(col), font=font,
                                      fill=color)
        line = font.metrics("linespace")
        row_step = ruler_step(cell, line)
        for row, y in ruler_marks(height, cell, row_step) if row_step else ():
            if y + line / 2 <= height:
                self._left.create_text(margin_x - pad, y, text=row_label(row, style),
                                       anchor="e", font=font, fill=color)

    def map_size(self) -> tuple[int, int]:
        """As laid out; Tk applies the placement only when idle."""
        if self._map_size is not None:
            return self._map_size
        return self.view.canvas.winfo_width(), self.view.canvas.winfo_height()

    # ── Rendering ─────────────────────────────────────────────────────────

    def render(self, snapshot: dict) -> None:
        self.view.render(snapshot)
        self.layout()       # scale or style may have changed the rulers

    def close(self) -> None:
        self.view.close()
