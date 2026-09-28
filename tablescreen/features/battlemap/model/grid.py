"""
grid.py — Grid calibration, geometry and coordinate labels. No Tk.

Positions are always ``index * cell_px`` from the origin, never accumulated,
so fractional cells (~88.1 px) don't drift across the TV.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Optional

# ── Defaults (all overridable in config.toml [features.battlemap]) ───────────
DEFAULT_SCREEN_DIAGONAL_IN = 50.0     # the table TV
DEFAULT_CELL_SIZE_IN = 1.0            # 1" squares for 5e minis
DEFAULT_GRID_COLOR = "#ffffff"
DEFAULT_GRID_WIDTH = 1

# Runtime `map grid resize <percent>` bounds.
MIN_SCALE_PCT = 10.0
MAX_SCALE_PCT = 1000.0

# Below this a grid is visual noise and costs thousands of canvas items.
MIN_CELL_PX = 4.0


@dataclass(frozen=True)
class GridSettings:
    """Grid configuration read once from the feature's config section."""

    screen_diagonal_in: float = DEFAULT_SCREEN_DIAGONAL_IN
    cell_size_in: float = DEFAULT_CELL_SIZE_IN
    pixels_per_inch: Optional[float] = None   # override; None = derive
    color: str = DEFAULT_GRID_COLOR
    width: int = DEFAULT_GRID_WIDTH
    coords_style: str = "numbers"             # default; runtime value is state
    coords_location: str = "sides"            # default; runtime value is per view
    coords_color: Optional[str] = None        # None = same as the grid colour

    @property
    def label_color(self) -> str:
        return self.coords_color or self.color

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> tuple["GridSettings", list[str]]:
        """Build settings from config, returning them plus any warnings.

        Invalid values fall back to the default with a warning rather than
        stopping the feature from loading.
        """
        warnings: list[str] = []

        def positive(key: str, default: Optional[float]) -> Optional[float]:
            if key not in config:
                return default
            value = config[key]
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                return float(value)
            warnings.append(f"{key} must be a positive number, got {value!r}; "
                            f"using {default}.")
            return default

        def colour(key: str, default: Optional[str]) -> Optional[str]:
            if key not in config:
                return default
            value = config[key]
            if isinstance(value, str) and value.strip():
                return value.strip()
            warnings.append(f"{key} must be a colour string, got {value!r}; "
                            f"using {default or 'the grid colour'}.")
            return default

        def choice(key: str, options: tuple[str, ...]) -> str:
            default = options[0]
            value = config.get(key, default)
            if isinstance(value, str) and value.strip().lower() in options:
                return value.strip().lower()
            warnings.append(f"{key} must be one of {', '.join(options)}, "
                            f"got {value!r}; using {default}.")
            return default

        width = config.get("grid_width", DEFAULT_GRID_WIDTH)
        if isinstance(width, bool) or not isinstance(width, int) or width < 1:
            warnings.append(f"grid_width must be a whole number ≥ 1, got {width!r}; "
                            f"using {DEFAULT_GRID_WIDTH}.")
            width = DEFAULT_GRID_WIDTH

        settings = cls(
            screen_diagonal_in=positive("screen_diagonal_in", DEFAULT_SCREEN_DIAGONAL_IN),
            cell_size_in=positive("cell_size_in", DEFAULT_CELL_SIZE_IN),
            pixels_per_inch=positive("pixels_per_inch", None),
            color=colour("grid_color", DEFAULT_GRID_COLOR),
            width=width,
            coords_style=choice("coords_style", COORD_STYLES),
            coords_location=choice("coords_location", COORD_LOCATIONS),
            coords_color=colour("coords_color", None),
        )
        return settings, warnings


# ── Calibration ──────────────────────────────────────────────────────────────

def pixels_per_inch(width_px: int, height_px: int, diagonal_in: float) -> float:
    """Pixel density of a screen from its resolution and physical diagonal.

    Assumes square pixels, which holds for every TV and monitor this runs on.
    A 3840x2160 panel with a 50" diagonal gives ~88.1.
    """
    if width_px <= 0 or height_px <= 0 or diagonal_in <= 0:
        raise ValueError("resolution and diagonal must be positive")
    return math.hypot(width_px, height_px) / diagonal_in


def cell_px(ppi: float, cell_size_in: float, scale_pct: float = 100.0) -> float:
    """Size of one grid cell in pixels. Deliberately a float — see module doc."""
    return ppi * cell_size_in * scale_pct / 100.0


# ── Geometry ─────────────────────────────────────────────────────────────────

def cell_to_pixels(*, row: int, col: int, cell: float) -> tuple[float, float]:
    """Top-left pixel (x, y) of a cell; 1-based. Everything placed on a cell
    goes through this. Keyword-only because cells are row first but pixels
    are x first."""
    return (col - 1) * cell, (row - 1) * cell


def grid_line_positions(extent_px: float, cell: float) -> list[float]:
    """Offsets of every grid line from 0 up to and including ``extent_px``.

    Line i sits at the top-left edge of cell i+1, so it goes through
    cell_to_pixels. The last cell is usually partial.
    """
    if cell <= 0:
        raise ValueError("cell size must be positive")
    count = int(extent_px // cell) + 1
    return [cell_to_pixels(row=1, col=i + 1, cell=cell)[0] for i in range(count)]


# ── Coordinate labels ────────────────────────────────────────────────────────
# Row first: "5,8" / "E8". Letters are rows so the 25-row TV stays A-Y.

COORD_STYLES = ("numbers", "letters")
COORD_LOCATIONS = ("sides", "cells")

# Font size as a fraction of the cell. In-cell labels share the cell with a
# mini, so they are smaller.
SIDE_FONT_FRACTION = 0.30
CELL_FONT_FRACTION = 0.18
MIN_LABEL_FONT_PX = 8          # below this, labels are not drawn


@dataclass(frozen=True)
class Label:
    """One coordinate label: text at a pixel position with a Tk anchor."""
    text: str
    x: float
    y: float
    anchor: str


def row_label(row: int, style: str) -> str:
    """Row name: "5" in numbers style; bijective base-26 letters otherwise
    (1 → A, 26 → Z, 27 → AA, 52 → AZ, 53 → BA)."""
    if row < 1:
        raise ValueError("rows are 1-based")
    if style == "numbers":
        return str(row)
    letters = ""
    while row > 0:
        row, rem = divmod(row - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


def col_label(col: int) -> str:
    """Columns are numbers in both styles."""
    if col < 1:
        raise ValueError("columns are 1-based")
    return str(col)


def cell_label(row: int, col: int, style: str) -> str:
    """Full coordinate, row first: "5,8" (numbers) or "E8" (letters)."""
    sep = "," if style == "numbers" else ""
    return f"{row_label(row, style)}{sep}{col_label(col)}"


_CELL_NUMBERS = re.compile(r"(\d+),(\d+)")
_CELL_LETTERS = re.compile(r"([A-Za-z]+)(\d+)")


def parse_cell(text: str) -> Optional[tuple[int, int]]:
    """(row, col) from "5,8" or "E8" (either style, letters any case), else None.
    Both notations are row first, so they never disagree about a cell."""
    text = text.strip()
    if m := _CELL_NUMBERS.fullmatch(text):
        row, col = int(m[1]), int(m[2])
    elif m := _CELL_LETTERS.fullmatch(text):
        row = 0
        for ch in m[1].upper():             # inverse of row_label
            row = row * 26 + ord(ch) - ord("A") + 1
        col = int(m[2])
    else:
        return None
    return (row, col) if row >= 1 and col >= 1 else None


def label_font_px(cell: float, location: str) -> Optional[int]:
    """Label font size in pixels for this cell size, or None if too small."""
    fraction = SIDE_FONT_FRACTION if location == "sides" else CELL_FONT_FRACTION
    size = int(cell * fraction)
    return size if size >= MIN_LABEL_FONT_PX else None


def coord_labels(width: float, height: float, cell: float,
                 location: str, style: str) -> list[Label]:
    """Every label for a canvas of this size.

    sides: column numbers along the top edge, row labels along the left.
    cells: the full coordinate in each cell's top-left corner.
    Partial edge cells are included if the anchor is on the canvas; the view
    drops labels whose text would be clipped.
    """
    if cell <= 0:
        raise ValueError("cell size must be positive")
    pad = max(2.0, cell * 0.06)
    cols = math.ceil(width / cell)      # includes a partial last column
    rows = math.ceil(height / cell)
    labels: list[Label] = []

    if location == "sides":
        for col in range(1, cols + 1):
            x, y = cell_to_pixels(row=1, col=col, cell=cell)
            if x + cell / 2 < width:
                labels.append(Label(col_label(col), x + cell / 2, y + pad, "n"))
        for row in range(1, rows + 1):
            x, y = cell_to_pixels(row=row, col=1, cell=cell)
            if y + cell / 2 < height:
                labels.append(Label(row_label(row, style), x + pad, y + cell / 2, "w"))
    else:
        for row in range(1, rows + 1):
            for col in range(1, cols + 1):
                x, y = cell_to_pixels(row=row, col=col, cell=cell)
                if x + pad < width and y + pad < height:
                    labels.append(Label(cell_label(row, col, style),
                                        x + pad, y + pad, "nw"))
    return labels
