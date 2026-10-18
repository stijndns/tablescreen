"""
mirror_tools.py — What the mouse does on the DM mirror while a mode is on.

A tool receives the mouse events (canvas pixels on the mirror's map) from
MirrorInput and answers with command lines through ``ctx.submit``. It may
draw previews with ``ctx``'s helpers; it never changes state. Mainloop
thread only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

from ..model.grid import CellArea

if TYPE_CHECKING:
    from .mirror_input import MirrorInput

Pos = tuple[float, float]


class MirrorTool:
    """Base: every hook is optional. ``ctx`` is set when the mode starts."""

    label = ""          # "revealing fog": title and messages
    hint = ""           # printed when the mode starts
    once = False        # the mode ends after the first command

    def __init__(self) -> None:
        self.ctx: Optional["MirrorInput"] = None

    def hover(self, pos: Pos) -> None:
        """The pointer moved with no button pressed."""

    def leave(self) -> None:
        """The pointer left the map."""
        self.ctx.clear()

    def press(self, pos: Pos) -> None: ...

    def drag(self, pos: Pos) -> None: ...

    def release(self, pos: Pos, click: bool) -> None:
        """``click``: released where it was pressed (not a drag)."""

    def wheel(self, steps: int) -> None:
        """Whole wheel notches: positive = up (away), negative = down."""

    def cancel(self) -> bool:
        """Esc or a right-click. True if that only dropped something in hand
        (the mode goes on); False ends the mode."""
        return False


class AreaTool(MirrorTool):
    """Click a cell or drag over cells; the cells become a command. Shows the
    cell under the pointer and, while dragging, the cells that will count.
    With ``ghost`` (a sprite file) it previews that sprite fitted in them."""

    def __init__(self, label: str, hint: str, command: Callable[[CellArea], str],
                 once: bool = False, ghost: Optional[str] = None):
        super().__init__()
        self.label, self.hint, self.once = label, hint, once
        self._command = command
        self._ghost = ghost
        self._start: Optional[Pos] = None       # drag start
        self._hovered: Optional[tuple[int, int]] = None

    def hover(self, pos: Pos) -> None:
        cell = self.ctx.cell_at(pos)
        if cell == self._hovered:
            return                  # same cell: nothing to redraw
        self._hovered = cell
        self.ctx.clear()
        if cell is not None:
            self._show(CellArea(*cell, *cell), label=False)

    def leave(self) -> None:
        self._hovered = None
        super().leave()

    def press(self, pos: Pos) -> None:
        self._start = pos
        self.drag(pos)

    def drag(self, pos: Pos) -> None:
        if self._start is None:
            return
        ctx = self.ctx
        ctx.clear()
        ctx.dashed_rect(self._start, pos)
        area = ctx.selection(self._start, pos)
        if area is not None:
            self._show(area, label=True)

    def release(self, pos: Pos, click: bool) -> None:
        if self._start is None:
            return
        area = self.ctx.selection(self._start, pos)
        self._start = self._hovered = None
        self.ctx.clear()
        if area is None:
            print("[i] Mirror: that rectangle covers no cell; nothing changed.")
            return
        self.ctx.submit(self._command(area))

    def _show(self, area: CellArea, label: bool) -> None:
        if self._ghost:
            self.ctx.ghost(self._ghost, 0, area)
        self.ctx.outline(area, width=3 if label else 2)
        if label:
            self.ctx.area_label(area)
