"""
aoe.py — Areas of effect: command parsing and affected cells. No Tk.

Distances use the 5-10-5 diagonal rule. Origins are a cell centre ("E8") or
the top-left corner of a cell ("E8c"); which one a shape accepts is fixed:
sphere both, cube corner only, cone cell only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .grid import parse_cell

FEET_PER_CELL = 5
SHAPES = ("sphere", "cube", "cone")

# Unit steps (row, col); n = towards row A (top of the screen).
DIRECTIONS = {
    "n": (-1, 0), "ne": (-1, 1), "e": (0, 1), "se": (1, 1),
    "s": (1, 0), "sw": (1, -1), "w": (0, -1), "nw": (-1, -1),
}
ORTHOGONAL = ("n", "e", "s", "w")

USAGE = {
    "sphere": "map aoe sphere <origin> <radius> [as <name>]   e.g. sphere E8 20, sphere E8c 10",
    "cube": "map aoe cube <corner> <size> [as <name>]   e.g. cube E8c 15",
    "cone": "map aoe cone <cell> <length> <direction> [mirror] [as <name>]   e.g. cone E8 15 e",
}


@dataclass(frozen=True)
class AoE:
    name: str
    shape: str              # sphere | cube | cone
    row: int                # origin cell, 1-based
    col: int
    corner: bool            # origin is the cell's top-left corner, not its centre
    size_ft: int            # radius / edge / length
    direction: Optional[str] = None     # cones only
    mirror: bool = False                # orthogonal cones only
    color: str = "#ff5050"


def distance_ft(d_rows: int, d_cols: int) -> int:
    """5-10-5: straight steps 5 ft, diagonals alternate 5 and 10 ft."""
    a, b = abs(d_rows), abs(d_cols)
    diag, straight = min(a, b), abs(a - b)
    return straight * 5 + (diag // 2) * 15 + (diag % 2) * 5


# ── Parsing ──────────────────────────────────────────────────────────────────

def parse_origin(text: str) -> Optional[tuple[int, int, bool]]:
    """"E8"/"5,8" → (5, 8, False); "E8c"/"5,8c" → (5, 8, True); else None."""
    corner = text[-1:].lower() == "c" and parse_cell(text[:-1]) is not None
    cell = parse_cell(text[:-1] if corner else text)
    return (*cell, corner) if cell else None


def parse_feet(text: str) -> int:
    """'15' or '15ft' → 15; raises ValueError unless a positive multiple of 5."""
    raw = text.lower().removesuffix("ft")
    if not raw.isdigit() or int(raw) <= 0 or int(raw) % FEET_PER_CELL:
        raise ValueError(f"Sizes are in feet, a positive multiple of 5 "
                         f"(e.g. 15); got '{text}'.")
    return int(raw)


def parse_aoe(shape: str, args: list[str]) -> tuple[dict, Optional[str]]:
    """Fields for an AoE (without name/colour) and the 'as' name, if any.
    Raises ValueError with a message that says exactly what is wrong."""
    if len(args) >= 2 and args[-2].lower() == "as":
        args, name = args[:-2], args[-1]
    else:
        name = None
    arity = {"sphere": (2,), "cube": (2,), "cone": (3, 4)}[shape]
    if len(args) not in arity:
        raise ValueError(f"Usage: {USAGE[shape]}")

    origin = parse_origin(args[0])
    if origin is None:
        raise ValueError(f"Invalid origin '{args[0]}'. Use a cell like E8 or 5,8, "
                         f"or a corner like E8c.")
    row, col, corner = origin
    if shape == "cube" and not corner:
        raise ValueError(f"A cube starts at a corner: use {args[0]}c (the top-left "
                         f"corner of that cell); the cube extends right and down.")
    if shape == "cone" and corner:
        raise ValueError(f"A cone starts from the caster's cell: use {args[0][:-1]} "
                         f"(no 'c'); the cone begins next to that cell.")
    fields = {"shape": shape, "row": row, "col": col, "corner": corner,
              "size_ft": parse_feet(args[1])}

    if shape == "cone":
        direction = args[2].lower()
        if direction not in DIRECTIONS:
            raise ValueError(f"Cone direction must be one of {' '.join(DIRECTIONS)} "
                             f"(n = top of the screen); got '{args[2]}'.")
        mirror = len(args) == 4
        if mirror and args[3].lower() != "mirror":
            raise ValueError(f"Unexpected '{args[3]}'. Usage: {USAGE['cone']}")
        if mirror and direction not in ORTHOGONAL:
            raise ValueError("'mirror' only applies to n, e, s and w cones; "
                             "diagonal cones are symmetric.")
        fields.update(direction=direction, mirror=mirror)
    return fields, name


# ── Affected cells ───────────────────────────────────────────────────────────

def aoe_cells(aoe: AoE) -> frozenset[tuple[int, int]]:
    """Every (row, col) the AoE covers; cells left of/above the grid dropped."""
    reach = aoe.size_ft // FEET_PER_CELL
    if aoe.shape == "sphere":
        cells = _corner_sphere(aoe, reach) if aoe.corner else _centre_sphere(aoe, reach)
    elif aoe.shape == "cube":
        cells = {(aoe.row + r, aoe.col + c) for r in range(reach) for c in range(reach)}
    else:
        cells = _cone(aoe, reach)
    return frozenset((r, c) for r, c in cells if r >= 1 and c >= 1)


def _centre_sphere(aoe: AoE, reach: int) -> set:
    """Cell to cell from the origin cell (which is included)."""
    return {(aoe.row + dr, aoe.col + dc)
            for dr in range(-reach, reach + 1) for dc in range(-reach, reach + 1)
            if distance_ft(dr, dc) <= aoe.size_ft}


def _corner_sphere(aoe: AoE, reach: int) -> set:
    """From the grid point at the top-left of the origin cell. The four cells
    touching that point are 5 ft away (you enter them), so a cell `a` rows and
    `b` columns further out (0-based, per quarter) is distance(a+1, b+1)."""
    cells = set()
    for a in range(reach):
        for b in range(reach):
            if distance_ft(a + 1, b + 1) > aoe.size_ft:
                continue
            for below in (True, False):
                for right in (True, False):
                    cells.add((aoe.row + a if below else aoe.row - 1 - a,
                               aoe.col + b if right else aoe.col - 1 - b))
    return cells


def _cone(aoe: AoE, reach: int) -> set:
    d_row, d_col = DIRECTIONS[aoe.direction]
    if aoe.direction in ORTHOGONAL:
        # Step k (1-based) away from the caster is k cells wide. Even widths
        # can't centre: they lean down (e/w) or right (n/s); mirror flips it.
        side = -1 if aoe.mirror else 1
        cells = set()
        for k in range(1, reach + 1):
            offsets = range(-((k - 1) // 2), k // 2 + 1)
            for o in offsets:
                across = o * side
                if d_row == 0:          # east/west: spread over rows
                    cells.add((aoe.row + across, aoe.col + d_col * k))
                else:                   # north/south: spread over columns
                    cells.add((aoe.row + d_row * k, aoe.col + across))
        return cells
    # Diagonal: every cell strictly in the forward quarter within range.
    return {(aoe.row + d_row * i, aoe.col + d_col * j)
            for i in range(1, reach + 1) for j in range(1, reach + 1)
            if distance_ft(i, j) <= aoe.size_ft}
