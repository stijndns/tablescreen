"""
combat_view.py — Player-facing combat screen rendered on the Tkinter window.

Replaces the image during an active combat encounter.
The DM calls render(snapshot, page) whenever state changes; this redraws the canvas.
"""

import tkinter as tk
import math

from .styling import *
from ..model import Combatant, Type
from .combatant_view import CombatantView
from typing import Tuple

class CombatView(tk.Frame):
    """The combat tracker, drawn into a content slot's frame. The feature
    packs it and shows the slot; render(snapshot, page) redraws it."""

    def __init__(self, parent: tk.Widget):
        super().__init__(parent, bg=PALETTE["bg"])
        self._snapshot: dict | None = None
        self._page: int = 0          # 0-based current page index
        self._image_cache: dict = {}
        self.bind("<Configure>", lambda e: self._redraw((e.width, e.height)))
        # Grid layout: header in row 0, combatant i in row i + 1. Stretch the
        # column to the full width and keep the rows at the top (grid would
        # otherwise centre them vertically, unlike pack).
        self.columnconfigure(0, weight=1)
        self.grid_anchor("n")
        self.view_cache: list[CombatantView] = []
        self.header = None
        self.H = 0
        self.W = 0
        self.scale = 0

    # ── Public API ────────────────────────────────────────────────────────────

    def render(self, snapshot: dict, page: int | None = None):
        """Update snapshot and optionally force a specific page, then redraw."""
        self._snapshot = snapshot
        if page is not None:
            self._page = page
        self._clamp_page()
        self._redraw()

    def set_page(self, page: int):
        """Jump to a specific 0-based page and redraw."""
        self._page = page
        self._clamp_page()
        self._redraw()

    def page_next(self):
        pages = self._page_count()
        self._page = (self._page + 1) % pages
        self._redraw()

    def page_prev(self):
        pages = self._page_count()
        self._page = (self._page - 1) % pages
        self._redraw()

    def current_page(self) -> int:
        return self._page

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _page_count(self) -> int:
      return max(1, math.ceil(len(self._ordered_entries()) / MIN_PAGE_SIZE))

    def _clamp_page(self):
        if self._snapshot is None:
            self._page = 0
            return
        pages = self._page_count()
        self._page = max(0, min(self._page, pages - 1))

    def _ordered_entries(self) -> list:
        """All combatants in initiative order, with pending/left_combat/unacted monsters at bottom."""
        combatants = self._snapshot["combatants"] if self._snapshot is not None else []
        in_order = [c for c in combatants if c.is_in_combat() and not c.hidden_initiative()]
        bottom   = [c for c in combatants if not c.is_in_combat() or c.hidden_initiative()]
        return in_order + bottom

    def _page_entries(self) -> list:
        entries = self._ordered_entries()
        start   = self._page * MIN_PAGE_SIZE
        return entries[start : start + MIN_PAGE_SIZE]

    # ── Redraw ────────────────────────────────────────────────────────────────

    def _redraw(self, new_size: Tuple[int, int] | None = None):
        if self._snapshot is None:
            return
        if new_size is not None:
            self.W = new_size[0]
            self.H = new_size[1]
            self.resize()
        self._draw()

    def resize(self):
        self._image_cache.clear()   # row_h may have changed
        self.scale = min(self.W / 900, self.H / 600, 1.5)
        gap = int(6 * self.scale)
        for index, combatant_view in enumerate(self.view_cache, 0):
            if combatant_view.winfo_ismapped():
                combatant_view.grid_configure(pady=((gap * 2 if index == 0 else gap), 2), padx=combatant_view.padding)

    def _draw(self):
        assert self._snapshot is not None

        pages    = self._page_count()
        entries  = self._page_entries()

        pad      = int(PADDING * self.scale)
        header_h = int(60 * self.scale)

        self._draw_header(self._snapshot, self.W, header_h, pad, self._page + 1, pages)

        gap = int(6 * self.scale)
        combatant: Combatant
        for index, combatant in enumerate(entries, 0):
            if len(self.view_cache) <= index:
                combatant_view = CombatantView(self, combatant, self._image_cache, self.scale)
                self.view_cache.append(combatant_view)
            else:
                combatant_view = self.view_cache[index]
                combatant_view.update_config(self.scale)
            if not combatant_view.winfo_ismapped():
                combatant_view.grid(row=index + 1, column=0, sticky="ew",
                                    pady=((gap * 2 if index == 0 else gap), 2),
                                    padx=combatant_view.padding)

        # update all sizes of widgets so the canvas can correctly allign on right side
        self.update_idletasks()

        for index, combatant in enumerate(entries, 0):
            combatant_view = self.view_cache[index]
            combatant_view.delete('all')
            combatant_view.combatant = combatant
            if combatant_view.is_unrevealed():
                combatant_view.draw_unrevealed_row()
            else:
                combatant_view.draw_row(self._snapshot["current_index"] - self._page * MIN_PAGE_SIZE == index)

        for index in range(len(entries), len(self.view_cache)):
            self.view_cache[index].grid_remove()


    # ── Header ────────────────────────────────────────────────────────────────

    def _draw_header(self, snap, W, header_h, pad, page, pages):
        c: tk.Canvas
        if self.header is None:
            c = tk.Canvas(self, bg=PALETTE["surface"], bd=0, highlightthickness=0, height=header_h)
            self.header = c
            c.grid(row=0, column=0, sticky="ew")
        else:
            c = self.header
            c.delete('all')
            c.config(height=header_h)
        c.create_line(0, header_h, W, header_h, fill=PALETTE["border"], width=1)

        active    = snap["active"]
        round_num = snap["round"]
        title     = f"Round {round_num}" if active else "Combat — not started"
        c.create_text(pad, header_h // 2,
            text=title,
            fill=PALETTE["gold"],
            font=(FONT_FAMILY, scaled_font(ROUND_FONT_SIZE, self.scale), "bold"),
            anchor="w")

        page_text = f"Page {page} / {pages}"
        c.create_text(W - pad, header_h // 2,
            text=page_text,
            fill=PALETTE["text_muted"],
            font=(FONT_FAMILY, scaled_font(MUTED_FONT_SIZE, self.scale)),
            anchor="e")
