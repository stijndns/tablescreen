"""
mirror_tools.py — What the mouse does on the DM mirror while a mode is on.

A tool receives the mouse events (canvas pixels on the mirror's map) from
MirrorInput and answers with command lines through ``ctx.submit``. It may
draw previews with ``ctx``'s helpers; it never changes state. Mainloop
thread only.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import TYPE_CHECKING, Callable, Optional

from ..model.aoe import AoE, aoe_at
from ..model.battlemap_state import Sprite, sprite_at
from ..model.grid import CellArea, area_label, cell_label, pick_origin

if TYPE_CHECKING:
    from .mirror_input import MirrorInput

Pos = tuple[float, float]


class MirrorTool:
    """Base: every hook is optional. ``ctx`` is set when the mode starts."""

    label = ""          # "revealing fog": title and messages
    hint = ""           # printed when the mode starts
    once = False        # the mode ends after the first command
    public_preview = False  # T can show its result preview on the table

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

    def table_note(self) -> Optional[str]:
        """Why the preview stays on the mirror right now, if it does for a
        reason worth saying (a hidden sprite); printed when T is pressed."""
        return None

    def redraw(self, pos: Pos, pressing: bool) -> None:
        """Draw the preview again (after T): tools skip redrawing what they
        already show, so forget that first."""
        if hasattr(self, "_shown"):
            self._shown = None
        (self.drag if pressing else self.hover)(pos)


class AreaTool(MirrorTool):
    """Click a cell or drag over cells; the cells become a command. Shows the
    cell under the pointer and, while dragging, the cells that will count.
    With ``ghost`` (a sprite file) it previews that sprite fitted in them."""

    def __init__(self, label: str, hint: str, command: Callable[[CellArea], str],
                 once: bool = False, ghost: Optional[str] = None,
                 private_note: Optional[str] = None):
        super().__init__()
        self.label, self.hint, self.once = label, hint, once
        self._command = command
        self._ghost = ghost
        self._private_note = private_note           # e.g. placing a hidden sprite
        # Fog selections stay private; so does anything with a private note.
        self.public_preview = ghost is not None and private_note is None
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

    def table_note(self) -> Optional[str]:
        return self._private_note

    def redraw(self, pos: Pos, pressing: bool) -> None:
        self._hovered = None
        super().redraw(pos, pressing)

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
            print("[i] Mirror: the cell size isn't known yet; nothing changed.")
            return
        self.ctx.submit(self._command(area))

    def _show(self, area: CellArea, label: bool) -> None:
        if self._ghost:
            self.ctx.ghost(self._ghost, 0, area, public=self.public_preview)
        self.ctx.outline(area, width=3 if label else 2)
        if label:
            self.ctx.area_label(area)


REMOVE_COLOR = "#ff4040"
HIDE_COLOR = "#a020f0"      # as the mirror marks hidden sprites
PICK_AOE_COLOR = "white"    # AoEs come in yellow too: the usual highlight would vanish


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


class SpriteVisibilityTool(MirrorTool):
    """Click a hidden sprite to reveal it on the table (``reveal``), or a
    visible one to hide it. Picks the topmost sprite of that kind in the cell,
    so a visible chair on a hidden trap doesn't get in the way."""

    def __init__(self, reveal: bool):
        super().__init__()
        self._reveal = reveal
        self.label = "revealing hidden sprites" if reveal else "hiding sprites"
        self.hint = ("click a hidden sprite to show it on the battlemap."
                     if reveal else "click a sprite to hide it from the battlemap.")
        self._color = {} if reveal else {"color": HIDE_COLOR}   # reveal: the usual
        self._hovered: Optional[tuple[int, int]] = None

    def _target(self, cell: Optional[tuple[int, int]]) -> Optional[Sprite]:
        if cell is None:
            return None
        wanted = tuple(s for s in self.ctx.sprites if s.hidden == self._reveal)
        return sprite_at(wanted, *cell)

    def hover(self, pos: Pos) -> None:
        cell = self.ctx.cell_at(pos)
        if cell == self._hovered:
            return
        self._hovered = cell
        self.ctx.clear()
        sprite = self._target(cell)
        if sprite is not None:
            self.ctx.outline(sprite.area, **self._color)
            self.ctx.label_at(sprite.area, sprite.name, **self._color)

    def leave(self) -> None:
        self._hovered = None
        super().leave()

    def release(self, pos: Pos, click: bool) -> None:
        cell = self.ctx.cell_at(pos)
        sprite = self._target(cell)
        self._hovered = None            # redraw on the next move: it changed
        self.ctx.clear()
        if sprite is None:
            if cell:
                kind = "hidden sprite" if self._reveal else "visible sprite"
                print(f"[i] Mirror: no {kind} in {cell_label(*cell, self.ctx.style)}.")
            return
        verb = "reveal" if self._reveal else "hide"
        self.ctx.submit(f"map sprite {verb} {sprite.name}")


class SpritePickTool(MirrorTool):
    """Base for tools that first pick a sprite (the topmost in a cell), then
    do something with it. Esc puts a picked sprite back."""

    def __init__(self) -> None:
        super().__init__()
        self._held: Optional[Sprite] = None
        self._shown: Optional[tuple] = None     # what the preview shows now

    def _sprite_under(self, pos: Pos) -> tuple[Optional[tuple[int, int]], Optional[Sprite]]:
        cell = self.ctx.cell_at(pos)
        return cell, (sprite_at(self.ctx.sprites, *cell) if cell else None)

    def _show_pickable(self, pos: Pos) -> None:
        """Outline the sprite a click would pick."""
        cell, sprite = self._sprite_under(pos)
        key = ("pick", sprite.name if sprite else None, cell if sprite is None else None)
        if key == self._shown:
            return
        self._shown = key
        self.ctx.clear()
        if sprite is not None:
            self.ctx.outline(sprite.area, width=2)
            self.ctx.label_at(sprite.area, sprite.name)

    def _no_sprite(self, cell: Optional[tuple[int, int]]) -> None:
        if cell:
            print(f"[i] Mirror: no sprite in {cell_label(*cell, self.ctx.style)}.")

    def _pick(self, sprite: Sprite) -> None:
        self._held = sprite
        if sprite.hidden and self.ctx.table_shown:
            print(f"[i] Mirror: {self.table_note()}")

    @property
    def _public(self) -> bool:
        """A hidden sprite's preview never goes to the table: it would give
        the trap away."""
        return self._held is not None and not self._held.hidden

    def table_note(self) -> Optional[str]:
        if self._held is not None and self._held.hidden:
            return f"{self._held.name} is hidden; its preview stays on the mirror."
        return None

    def leave(self) -> None:
        if self._held is None:
            self._shown = None
            super().leave()

    def cancel(self) -> bool:
        if self._held is None:
            return False
        name = self._held.name
        self._drop()
        print(f"[i] Mirror: put {name} back.")
        return True

    def _drop(self) -> None:
        self._held, self._shown = None, None
        self.ctx.clear()


class SpriteMoveTool(SpritePickTool):
    """Pick a sprite, then put it somewhere else: click it and click where it
    goes, or drag it there. It hangs from the pointer by the cell it was
    picked at, and its footprint stays on the map. Esc drops it."""

    label = "moving sprites"
    hint = "click a sprite, then click where it goes (or drag it there)."
    public_preview = True

    def __init__(self) -> None:
        super().__init__()
        self._grab = (0, 0)             # picked cell − footprint top-left
        self._carrying = False          # picked with a click: follows the pointer

    # Picking (nothing held) and carrying (held) share the pointer hooks.

    def hover(self, pos: Pos) -> None:
        if self._held is not None:
            self._show_target(pos)
        else:
            self._show_pickable(pos)

    def press(self, pos: Pos) -> None:
        if self._held is not None:
            return                      # carrying: it lands on release
        cell, sprite = self._sprite_under(pos)
        if sprite is None:
            return
        self._pick(sprite)
        self._grab = (cell[0] - sprite.area.top, cell[1] - sprite.area.left)
        self._show_target(pos)

    def drag(self, pos: Pos) -> None:
        if self._held is not None:
            self._show_target(pos)

    def release(self, pos: Pos, click: bool) -> None:
        if self._held is None:
            self._no_sprite(self.ctx.cell_at(pos))
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

    def _drop(self) -> None:
        self._carrying = False
        super()._drop()

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
        ctx.ghost(sprite.file, sprite.rotation, target, public=self._public)
        ctx.outline(target)
        ctx.label_at(target, f"{sprite.name} → "
                             f"{cell_label(target.top, target.left, ctx.style)}")


class SpriteRotateTool(SpritePickTool):
    """Click a sprite, turn the wheel to rotate a see-through copy of it (down =
    clockwise, ``step`` degrees a notch), click to apply. Esc puts it back."""

    label = "rotating sprites"
    public_preview = True

    def __init__(self, step: float = 15) -> None:
        super().__init__()
        self.step = step
        self.hint = (f"click a sprite, turn the wheel to rotate it ({step:g}° a "
                     f"notch, down = clockwise), click to apply.")
        self._delta = 0.0               # degrees clockwise, relative

    def hover(self, pos: Pos) -> None:
        if self._held is None:
            self._show_pickable(pos)

    def release(self, pos: Pos, click: bool) -> None:
        if self._held is None:
            cell, sprite = self._sprite_under(pos)
            if sprite is None:
                self._no_sprite(cell)
                return
            self._pick(sprite)
            self._delta = 0.0
            self._show()
            return
        sprite, delta = self._held, self._delta
        self._drop()
        if delta == 0:
            print(f"[i] Mirror: {sprite.name} keeps its rotation.")
            return
        self.ctx.submit(f"map sprite rotate {sprite.name} {delta:g}")

    def redraw(self, pos: Pos, pressing: bool) -> None:
        if self._held is not None:
            self._show()
        else:
            super().redraw(pos, pressing)

    def wheel(self, steps: int) -> None:
        if self._held is None:
            return
        # Kept within -180..180 so the command takes the short way round.
        self._delta = (self._delta - steps * self.step + 180) % 360 - 180
        self._show()

    def _show(self) -> None:
        ctx, sprite = self.ctx, self._held
        ctx.clear()
        ctx.shade(sprite.area, public=self._public)     # the original is below
        ctx.ghost(sprite.file, sprite.rotation + self._delta, sprite.area,
                  public=self._public)
        ctx.outline(sprite.area)
        ctx.label_at(sprite.area, f"{sprite.name} {self._delta:+g}°")


class SpriteResizeTool(SpritePickTool):
    """Click a sprite, then drag its new footprint (a click makes it 1x1 in
    that cell); a see-through copy is fitted in it while dragging. Release
    applies. Esc puts it back."""

    label = "resizing sprites"
    hint = "click a sprite, then drag its new footprint (a click: one cell)."
    public_preview = True

    def __init__(self) -> None:
        super().__init__()
        self._start: Optional[Pos] = None       # drag start, once held

    def hover(self, pos: Pos) -> None:
        if self._held is None:
            self._show_pickable(pos)
            return
        cell = self.ctx.cell_at(pos)
        if ("hover", cell) == self._shown:
            return
        self._shown = ("hover", cell)
        self._show_held()
        if cell is not None:
            self.ctx.outline(CellArea(*cell, *cell), width=2)

    def press(self, pos: Pos) -> None:
        if self._held is not None:
            self._start = pos
            self.drag(pos)

    def drag(self, pos: Pos) -> None:
        if self._held is None or self._start is None:
            return
        ctx = self.ctx
        self._shown = None
        self._show_held()
        ctx.dashed_rect(self._start, pos)
        area = ctx.selection(self._start, pos)
        if area is not None:
            ctx.ghost(self._held.file, self._held.rotation, area, public=self._public)
            ctx.outline(area)
            ctx.area_label(area)

    def release(self, pos: Pos, click: bool) -> None:
        if self._held is None:
            cell, sprite = self._sprite_under(pos)
            if sprite is None:
                self._no_sprite(cell)
                return
            self._pick(sprite)
            self._shown = None
            self._show_held()
            return
        if self._start is None:
            return
        area = self.ctx.selection(self._start, pos)
        sprite = self._held
        self._drop()
        if area is None:
            print("[i] Mirror: the cell size isn't known yet; nothing changed.")
        elif area == sprite.area:
            print(f"[i] Mirror: {sprite.name} keeps its size.")
        else:
            self.ctx.submit(f"map sprite resize {sprite.name} "
                            f"{area_label(area, self.ctx.style)}")

    def _drop(self) -> None:
        self._start = None
        super()._drop()

    def _show_held(self) -> None:
        """The picked sprite's current footprint, for reference."""
        self.ctx.clear()
        self.ctx.outline(self._held.area, width=1)
        self.ctx.label_at(self._held.area, self._held.name)


def origin_text(row: int, col: int, corner: bool, style: str) -> str:
    """``E8`` or, for a corner, ``E8c`` (the form `map aoe` takes)."""
    return cell_label(row, col, style) + ("c" if corner else "")


# Which origins each shape takes (see grid.pick_origin).
ALLOW = {"sphere": "both", "cube": "corner", "cone": "cell"}

# The 12 cone variants, clockwise from north; a wheel notch down steps on.
CONE_VARIANTS = (("n", False), ("n", True), ("ne", False), ("e", False),
                 ("e", True), ("se", False), ("s", False), ("s", True),
                 ("sw", False), ("w", False), ("w", True), ("nw", False))


def aoe_args(aoe: AoE, style: str) -> str:
    """What follows `map aoe`: ``sphere E8c 20`` or ``cone E8 15 e mirror``."""
    text = f"{aoe.shape} {origin_text(aoe.row, aoe.col, aoe.corner, style)} {aoe.size_ft}"
    if aoe.shape == "cone":
        text += f" {aoe.direction}" + (" mirror" if aoe.mirror else "")
    return text


def aoe_caption(aoe: AoE, style: str) -> str:
    """``20 ft sphere H15c`` / ``15 ft cone E8 e, mirrored``: preview labels."""
    text = (f"{aoe.size_ft} ft {aoe.shape} "
            f"{origin_text(aoe.row, aoe.col, aoe.corner, style)}")
    if aoe.shape == "cone":
        text += f" {aoe.direction}" + (", mirrored" if aoe.mirror else "")
    return text


class AoePlaceTool(MirrorTool):
    """A preview of the area follows the pointer; a click places it. Spheres
    take a grid corner when the pointer is near one, else the cell centre;
    cubes always the nearest corner (their top-left); cones the hovered cell
    (the caster), aimed with the wheel through all 12 variants."""

    def __init__(self, shape: str, size_ft: int, name: Optional[str] = None):
        super().__init__()
        self.shape, self.size_ft, self.name = shape, size_ft, name
        self.once = name is not None
        self.public_preview = True
        what = name or f"a {size_ft} ft {shape}"
        self.label = f"placing {what}"
        how = {"sphere": "; near a grid corner the corner is the centre.",
               "cube": "; it starts at the nearest grid corner (its top-left).",
               "cone": " from the caster's cell; turn the wheel to aim it "
                       "(down = clockwise)."}[shape]
        self.hint = (f"move the preview of {what} and click to place it{how}"
                     + ("" if self.once else " Each click adds one."))
        self._variant = 0               # cones: index into CONE_VARIANTS
        self._origin_now: Optional[tuple[int, int, bool]] = None
        self._shown: Optional[tuple] = None

    def _origin(self, pos: Pos) -> Optional[tuple[int, int, bool]]:
        cell = self.ctx.cell
        return pick_origin(*pos, cell, ALLOW[self.shape]) if cell else None

    def _aoe(self, origin: tuple[int, int, bool]) -> AoE:
        direction, mirror = (CONE_VARIANTS[self._variant] if self.shape == "cone"
                             else (None, False))
        return AoE("preview", self.shape, *origin, self.size_ft, direction, mirror)

    def hover(self, pos: Pos) -> None:
        self._origin_now = self._origin(pos)
        self._draw()

    def drag(self, pos: Pos) -> None:
        self.hover(pos)                 # the button held down: still follows

    def wheel(self, steps: int) -> None:
        if self.shape == "cone":
            self._variant = (self._variant - steps) % len(CONE_VARIANTS)
            self._draw()

    def _draw(self) -> None:
        origin = self._origin_now
        key = (origin, self._variant)
        if origin is None or key == self._shown:
            return
        self._shown = key
        aoe = self._aoe(origin)
        self.ctx.clear()
        self.ctx.aoe_preview(aoe, label=aoe_caption(aoe, self.ctx.style), public=True)

    def leave(self) -> None:
        self._shown = self._origin_now = None
        super().leave()

    def release(self, pos: Pos, click: bool) -> None:
        origin = self._origin(pos)
        if origin is None:
            print("[i] Mirror: the cell size isn't known yet; nothing changed.")
            return
        line = f"map aoe {aoe_args(self._aoe(origin), self.ctx.style)}"
        self.ctx.submit(f"{line} as {self.name}" if self.name else line)


class AoePickTool(MirrorTool):
    """Base for tools that first pick an area of effect (the topmost one
    covering a cell). Esc puts a picked area back."""

    def __init__(self) -> None:
        super().__init__()
        self._held: Optional[AoE] = None
        self._shown: Optional[tuple] = None

    def _aoe_under(self, pos: Pos) -> tuple[Optional[tuple[int, int]], Optional[AoE]]:
        cell = self.ctx.cell_at(pos)
        return cell, (aoe_at(self.ctx.aoes, *cell) if cell else None)

    def _show_pickable(self, pos: Pos, color: str = PICK_AOE_COLOR) -> None:
        """Outline the area a click would pick, with its name."""
        cell, aoe = self._aoe_under(pos)
        key = ("pick", aoe.name if aoe else None, cell if aoe is None else None)
        if key == self._shown:
            return
        self._shown = key
        self.ctx.clear()
        if aoe is not None:
            self.ctx.aoe_preview(aoe, color=color, label=aoe.name, fill=False)

    def _no_aoe(self, cell: Optional[tuple[int, int]]) -> None:
        if cell:
            print(f"[i] Mirror: no area of effect in "
                  f"{cell_label(*cell, self.ctx.style)}.")

    def leave(self) -> None:
        if self._held is None:
            self._shown = None
            super().leave()

    def cancel(self) -> bool:
        if self._held is None:
            return False
        name = self._held.name
        self._drop()
        print(f"[i] Mirror: put {name} back.")
        return True

    def _drop(self) -> None:
        self._held, self._shown = None, None
        self.ctx.clear()


class AoeRemoveTool(AoePickTool):
    """Click an area of effect to remove it (the topmost covering that cell)."""

    label = "removing areas of effect"
    hint = "click an area of effect to remove it (the topmost one in that cell)."

    def hover(self, pos: Pos) -> None:
        self._show_pickable(pos, color=REMOVE_COLOR)

    def release(self, pos: Pos, click: bool) -> None:
        cell, aoe = self._aoe_under(pos)
        self._shown = None              # redraw on the next move: it's gone
        self.ctx.clear()
        if aoe is None:
            self._no_aoe(cell)
            return
        self.ctx.submit(f"map aoe remove {aoe.name}")


class AoeMoveTool(AoePickTool):
    """Pick an area of effect and put it somewhere else: click it and click
    where it goes, or drag it there. It keeps its shape, size, direction and
    colour; the new origin snaps as its shape requires, and the area hangs
    from the pointer by the point it was picked at. Esc puts it back."""

    label = "moving areas of effect"
    hint = "click an area of effect, then click where it goes (or drag it there)."
    public_preview = True

    def __init__(self) -> None:
        super().__init__()
        self._grab = (0.0, 0.0)         # pointer − origin, in pixels
        self._carrying = False

    def hover(self, pos: Pos) -> None:
        if self._held is not None:
            self._show_target(pos)
        else:
            self._show_pickable(pos)

    def press(self, pos: Pos) -> None:
        if self._held is not None:
            return                      # carrying: it lands on release
        _, aoe = self._aoe_under(pos)
        if aoe is None:
            return
        self._held = aoe
        ox, oy = self._origin_px(aoe)
        self._grab = (pos[0] - ox, pos[1] - oy)
        self._show_target(pos)

    def drag(self, pos: Pos) -> None:
        if self._held is not None:
            self._show_target(pos)

    def release(self, pos: Pos, click: bool) -> None:
        if self._held is None:
            self._no_aoe(self.ctx.cell_at(pos))
            return
        if click and not self._carrying:
            self._carrying = True       # picked up: click again to put it down
            return
        aoe, target = self._held, self._target(pos)
        self._drop()
        if target is None or (target.row, target.col, target.corner) == (
                aoe.row, aoe.col, aoe.corner):
            print(f"[i] Mirror: {aoe.name} stays where it is.")
            return
        self.ctx.submit(f"map aoe move {aoe.name} "
                        f"{origin_text(target.row, target.col, target.corner, self.ctx.style)}")

    def _drop(self) -> None:
        self._carrying = False
        super()._drop()

    def _origin_px(self, aoe: AoE) -> Pos:
        cell = self.ctx.cell
        x, y = (aoe.col - 1) * cell, (aoe.row - 1) * cell
        return (x, y) if aoe.corner else (x + cell / 2, y + cell / 2)

    def _target(self, pos: Pos) -> Optional[AoE]:
        cell = self.ctx.cell
        if not cell:
            return None
        width, height = self.ctx.map_size()
        x = min(max(pos[0] - self._grab[0], 0), width - 1)
        y = min(max(pos[1] - self._grab[1], 0), height - 1)
        row, col, corner = pick_origin(x, y, cell, ALLOW[self._held.shape])
        return replace(self._held, row=row, col=col, corner=corner)

    def _show_target(self, pos: Pos) -> None:
        target = self._target(pos)
        if target is None:
            return
        key = ("target", target.row, target.col, target.corner)
        if key == self._shown:
            return
        self._shown = key
        self.ctx.clear()
        self.ctx.aoe_preview(target, color=self._held.color, public=True,
                             label=f"{target.name} → "
                                   f"{origin_text(target.row, target.col, target.corner, self.ctx.style)}")
