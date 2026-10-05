"""
mirror.py — The DM's copy of the table view, in its own window.

A letterboxed copy: the table canvas scaled uniformly to fit the mirror
window, centred, with black bars around it. The mirror's pixels per inch is
the table's times that scale, so every layer lands on the same cells as on
the table, partial edge cells included. The canvas itself is sized (not
offset), so its origin is the map's top-left. Mainloop thread only.
"""

from __future__ import annotations

import tkinter as tk
from typing import Optional

from ..model.grid import GridSettings, letterbox
from .battlemap_view import BackdropCache, BattleMapView, PpiProvider


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
        parent.bind("<Configure>", lambda e: self.layout(), add="+")

    def _ppi(self) -> Optional[float]:
        ppi = self._table_ppi()
        return ppi * self.scale if ppi and self.scale else None

    def table_size(self) -> tuple[int, int]:
        canvas = self._table.canvas
        width, height = canvas.winfo_width(), canvas.winfo_height()
        return (width, height) if width > 1 and height > 1 else self._fallback_size

    def layout(self) -> None:
        """Fit the copy to the window. Call after either window resizes."""
        box = self._parent.winfo_width(), self._parent.winfo_height()
        if min(box) <= 1:
            return
        scale, width, height = letterbox(self.table_size(), box)
        changed = scale != self.scale
        self.scale = scale
        self.view.place(relx=0.5, rely=0.5, anchor="center",
                        width=width, height=height)
        canvas = self.view.canvas
        if changed and (canvas.winfo_width(), canvas.winfo_height()) == (width, height):
            self.view.rescale()     # same size, new cells: no <Configure> follows

    def map_size(self) -> tuple[int, int]:
        return self.view.canvas.winfo_width(), self.view.canvas.winfo_height()

    def render(self, snapshot: dict) -> None:
        self.view.render(snapshot)

    def close(self) -> None:
        self.view.close()
