"""
mirror_tools.py — What the mouse does on the DM mirror while a mode is on.

A tool receives the mouse events (canvas pixels on the mirror's map) from
MirrorInput and answers with command lines through ``ctx.submit``. It may
draw previews with ``ctx``'s helpers; it never changes state. Mainloop
thread only.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Callable, Optional

from ..model.battlemap_state import Sprite, sprite_at
from ..model.grid import CellArea, cell_label

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


REMOVE_COLOR = "#ff4040"


class SpriteRemoveTool(MirrorTool):
    """Click a cell to remove the topmost sprite in it; one click, one sprite.
    The sprite a click would remove is outlined under the pointer."""

    label = "removing sprites"
    hint = "click a sprite to remove it (the topmost one in that cell)."

    def __init__(self) -> None:
        super().__init__()
        self._hovered: Optional[tuple[int, int]] = None

    def hover(self, pos: Pos) -> None:
        cell = self.ctx.cell_at(pos)
        if cell == self._hovered:
            return
        self._hovered = cell
        self.ctx.clear()
        sprite = sprite_at(self.ctx.sprites, *cell) if cell else None
        if sprite is not None:
            self.ctx.outline(sprite.area, color=REMOVE_COLOR)
            self.ctx.label_at(sprite.area, sprite.name, color=REMOVE_COLOR)

    def leave(self) -> None:
        self._hovered = None
        super().leave()

    def release(self, pos: Pos, click: bool) -> None:
        cell = self.ctx.cell_at(pos)
        sprite = sprite_at(self.ctx.sprites, *cell) if cell else None
        self._hovered = None            # redraw on the next move: it's gone
        self.ctx.clear()
        if sprite is None:
            print(f"[i] Mirror: no sprite in {cell_label(*cell, self.ctx.style)}."
                  if cell else "[i] Mirror: no sprite there.")
            return
        self.ctx.submit(f"map sprite remove {sprite.name}")


class SpriteMoveTool(MirrorTool):
    """Pick a sprite, then put it somewhere else: click it and click where it
    goes, or drag it there. It hangs from the pointer by the cell it was
    picked at, and its footprint stays on the map. Esc drops it."""

    label = "moving sprites"
    hint = "click a sprite, then click where it goes (or drag it there)."

    def __init__(self) -> None:
        super().__init__()
        self._held: Optional[Sprite] = None
        self._grab = (0, 0)             # picked cell − footprint top-left
        self._carrying = False          # picked with a click: follows the pointer
        self._shown: Optional[tuple] = None     # what the preview shows now

    # Picking (nothing held) and carrying (held) share the pointer hooks.

    def hover(self, pos: Pos) -> None:
        if self._held is not None:
            self._show_target(pos)
        else:
            self._show_pickable(pos)

    def leave(self) -> None:
        if self._held is None:
            self._shown = None
            super().leave()

    def press(self, pos: Pos) -> None:
        if self._held is not None:
            return                      # carrying: it lands on release
        cell = self.ctx.cell_at(pos)
        sprite = sprite_at(self.ctx.sprites, *cell) if cell else None
        if sprite is None:
            return
        self._held = sprite
        self._grab = (cell[0] - sprite.area.top, cell[1] - sprite.area.left)
        self._show_target(pos)

    def drag(self, pos: Pos) -> None:
        if self._held is not None:
            self._show_target(pos)

    def release(self, pos: Pos, click: bool) -> None:
        if self._held is None:
            cell = self.ctx.cell_at(pos)
            if cell:
                print(f"[i] Mirror: no sprite in {cell_label(*cell, self.ctx.style)}.")
            return
        if click and not self._carrying:
            self._carrying = True       # picked up: click again to put it down
            return
        sprite, target = self._held, self._target(pos)
        self._drop()
        if target is None or (target.top, target.left) == (sprite.area.top,
                                                            sprite.area.left):
            print(f"[i] Mirror: {sprite.name} stays where it is.")
            return
        self.ctx.submit(f"map sprite move {sprite.name} "
                        f"{cell_label(target.top, target.left, self.ctx.style)}")

    def cancel(self) -> bool:
        if self._held is None:
            return False
        name = self._held.name
        self._drop()
        print(f"[i] Mirror: put {name} back.")
        return True

    def _drop(self) -> None:
        self._held, self._carrying, self._shown = None, False, None
        self.ctx.clear()

    def _target(self, pos: Pos) -> Optional[CellArea]:
        """Where the held footprint would land: the pointer's cell minus the
        grab offset, kept on the fully visible cells (a partial edge cell is
        half off the screen)."""
        cell = self.ctx.cell_at(pos)
        size = self.ctx.cell
        if cell is None or not size:
            return None
        width, height = self.ctx.map_size()
        rows, cols = math.floor(height / size), math.floor(width / size)
        area = self._held.area
        top = min(max(cell[0] - self._grab[0], 1), max(rows - area.rows + 1, 1))
        left = min(max(cell[1] - self._grab[1], 1), max(cols - area.cols + 1, 1))
        return area.moved_to(top, left)

    def _show_target(self, pos: Pos) -> None:
        target = self._target(pos)
        if target is None or ("target", target) == self._shown:
            return
        self._shown = ("target", target)
        ctx, sprite = self.ctx, self._held
        ctx.clear()
        ctx.ghost(sprite.file, sprite.rotation, target)
        ctx.outline(target)
        ctx.label_at(target, f"{sprite.name} → "
                             f"{cell_label(target.top, target.left, ctx.style)}")

    def _show_pickable(self, pos: Pos) -> None:
        cell = self.ctx.cell_at(pos)
        sprite = sprite_at(self.ctx.sprites, *cell) if cell else None
        key = ("pick", sprite.name if sprite else None, cell if sprite is None else None)
        if key == self._shown:
            return
        self._shown = key
        self.ctx.clear()
        if sprite is not None:
            self.ctx.outline(sprite.area, width=2)
            self.ctx.label_at(sprite.area, sprite.name)
