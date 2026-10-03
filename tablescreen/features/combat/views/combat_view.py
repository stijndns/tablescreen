"""
combat_view.py — Player-facing combat screen rendered on the Tkinter window.

Replaces the image during an active combat encounter.
The DM calls render(snapshot, page) whenever state changes; this redraws it.
Sizes come from layout.py: rows are sized by the text, and the window height
decides how many fit on a page.
"""

import tkinter as tk
import math

from .styling import *
from ..model import Combatant
from .combatant_view import CombatantView
from .layout import (DEFAULT_TEXT_SCALE, LAYOUTS, Layout, compute_layout, grid_cell,
                     page_of, text_unit)
from typing import Optional, Tuple

# render(snapshot, page=FOLLOW_CURRENT): show the page with the active combatant.
# Only the view knows its page size, so the shell asks for this instead of a number.
FOLLOW_CURRENT = "current"


class CombatView(tk.Frame):
    """The combat tracker, drawn into a content slot's frame. The feature
    packs it and shows the slot; render(snapshot, page) redraws it."""

    def __init__(self, parent: tk.Widget, text_scale: float = DEFAULT_TEXT_SCALE,
                 columns: int = 1):
        super().__init__(parent, bg=PALETTE["bg"])
        self._snapshot: dict | None = None
        self._page: int = 0          # 0-based current page index
        self._follow_pending = False
        self._text_scale = text_scale
        self._columns = columns
        self.layout: Optional[Layout] = None
        self._warned: set[tuple] = set()    # shortened texts already reported
        self._image_cache: dict = {}
        self.bind("<Configure>", lambda e: self._redraw((e.width, e.height)))
        # Grid layout: header in row 0 across all columns, combatants below,
        # down the first column and then the next (layout.grid_cell). Keep
        # everything at the top (grid would otherwise centre it vertically).
        self._configure_columns()
        self.grid_anchor("n")
        self.view_cache: list[CombatantView] = []
        self.header = None
        self.H = 0
        self.W = 0
        self.scale = 0

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def layout_name(self) -> str:
        return next(name for name, cols in LAYOUTS.items() if cols == self._columns)

    @property
    def text_scale(self) -> float:
        return self._text_scale

    def set_text_scale(self, text_scale: float):
        """Resize all text and rows; keeps the active combatant in view."""
        self._text_scale = text_scale
        self.resize()
        self._redraw()

    def set_columns(self, columns: int):
        """Switch between one and two columns; keeps the active combatant in view."""
        self._columns = columns
        self._configure_columns()
        self.resize()
        self._redraw()

    def render(self, snapshot: dict, page: int | str | None = None):
        """Update the snapshot, optionally go to a page (or FOLLOW_CURRENT), redraw."""
        self._snapshot = snapshot
        if page == FOLLOW_CURRENT:
            self._follow_pending = True     # resolved once the page size is known
        elif page is not None:
            self._page = page
        self._redraw()

    def set_page(self, page: int):
        """Jump to a specific 0-based page and redraw."""
        self._page = page
        self._redraw()

    def page_next(self):
        self._page = (self._page + 1) % self._page_count()
        self._redraw()

    def page_prev(self):
        self._page = (self._page - 1) % self._page_count()
        self._redraw()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _page_size(self) -> int:
        return self.layout.page_size if self.layout else 1

    def _page_count(self) -> int:
        return max(1, math.ceil(len(self._ordered_entries()) / self._page_size()))

    def _configure_columns(self):
        """Equal-width columns for the active layout; unused ones collapse."""
        for column in range(max(LAYOUTS.values())):
            used = column < self._columns
            self.columnconfigure(column, weight=1 if used else 0,
                                 uniform="combatants" if used else "")

    def _report_shortened(self, name: str, field: str, text: str):
        """Warn once per shortened text and layout; the user wants to avoid it."""
        key = (name, field, text, self.layout)
        if key in self._warned:
            return
        self._warned.add(key)
        print(f"[!] combat: shortened {name}'s {field} to fit the combat screen: {text!r}. "
              f"A wider window, a smaller text_scale or the single layout avoids this.")

    def _resolve_page(self):
        """Apply a pending FOLLOW_CURRENT, then keep the page in range."""
        if self._follow_pending:
            names = [c.name for c in self._ordered_entries()]
            page = page_of(names, self._snapshot["current"], self._page_size())
            if page is not None:
                self._page = page
            self._follow_pending = False
        self._page = max(0, min(self._page, self._page_count() - 1))

    def _ordered_entries(self) -> list:
        """All combatants, already in display order (Combat.display_order)."""
        return self._snapshot["combatants"] if self._snapshot is not None else []

    def _page_entries(self) -> list:
        size = self._page_size()
        start = self._page * size
        return self._ordered_entries()[start : start + size]

    # ── Redraw ────────────────────────────────────────────────────────────────

    def _redraw(self, new_size: Tuple[int, int] | None = None):
        if new_size is not None:
            self.W, self.H = new_size
            self.resize()
        if self._snapshot is None or self.layout is None:
            return
        self._resolve_page()
        self._draw()

    def resize(self):
        """Recompute the layout for the current window size."""
        if self.W <= 1 or self.H <= 1:
            return      # not mapped yet
        unit = text_unit(self.winfo_fpixels("1i"), self._text_scale)
        old = self.layout
        if old is None or old.unit != unit:
            self._image_cache.clear()       # portraits are sized by the row height
        first_visible = self._page * old.page_size if old else 0
        current_visible = (self._snapshot is not None and old is not None and
                           self._snapshot["current"] in [c.name for c in self._page_entries()])
        self.layout = compute_layout(self.W, self.H, unit, self._columns)
        self.scale = unit
        # Keep the active combatant on screen if it was; otherwise keep the
        # combatant that was at the top of the page.
        if current_visible:
            self._follow_pending = True
        else:
            self._page = first_visible // self.layout.page_size

    def _draw(self):
        assert self._snapshot is not None and self.layout is not None
        layout = self.layout

        pages    = self._page_count()
        entries  = self._page_entries()

        self._draw_header(self._snapshot, self.W, layout.header_h, layout.pad,
                          self._page + 1, pages)

        gap, pad = layout.gap, layout.pad
        combatant: Combatant
        for index, combatant in enumerate(entries, 0):
            if len(self.view_cache) <= index:
                combatant_view = CombatantView(self, combatant, self._image_cache,
                                               self.scale, layout.row_h,
                                               on_shortened=self._report_shortened)
                self.view_cache.append(combatant_view)
            else:
                combatant_view = self.view_cache[index]
                combatant_view.update_config(self.scale, layout.row_h)
            # (Re)placed on every draw, so a layout switch moves every row.
            row, column = grid_cell(index, layout.rows)
            if layout.columns == 1:
                padx = pad
            else:   # outer margin pad, gutter between the columns also pad
                padx = (pad, pad // 2) if column == 0 else (pad // 2, pad)
            combatant_view.grid(row=row + 1, column=column, sticky="ew",
                                pady=((gap * 2 if row == 0 else gap), 2), padx=padx)

        # update all sizes of widgets so the canvas can correctly allign on right side
        self.update_idletasks()

        for index, combatant in enumerate(entries, 0):
            combatant_view = self.view_cache[index]
            combatant_view.delete('all')
            combatant_view.combatant = combatant
            if combatant_view.is_unrevealed():
                combatant_view.draw_unrevealed_row()
            else:
                combatant_view.draw_row(combatant.name == self._snapshot["current"])

        for index in range(len(entries), len(self.view_cache)):
            self.view_cache[index].grid_remove()


    # ── Header ────────────────────────────────────────────────────────────────

    def _draw_header(self, snap, W, header_h, pad, page, pages):
        c: tk.Canvas
        if self.header is None:
            c = tk.Canvas(self, bg=PALETTE["surface"], bd=0, highlightthickness=0, height=header_h)
            self.header = c
            c.grid(row=0, column=0, columnspan=max(LAYOUTS.values()), sticky="ew")
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
