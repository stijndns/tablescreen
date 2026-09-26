"""
grid.py — Battlemap grid calibration and geometry. No Tk.

Everything that turns physical inches into screen pixels lives here, so the
maths can be tested without a display and there is exactly one place to
change when calibration changes.

    screen diagonal (config) + monitor resolution  ─►  pixels per inch
    pixels per inch × cell size (config) × scale %  ─►  cell size in pixels
    cell size in pixels  ─►  cell_to_pixels(col, row)  ─►  grid lines

Positions are always ``index * cell_px`` from the origin, never "previous
line + cell_px": at ~88.1 px per cell, rounding per step drifts by several
pixels across the width of the TV.
"""

from __future__ import annotations

import math
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

        color = config.get("grid_color", DEFAULT_GRID_COLOR)
        if not isinstance(color, str) or not color.strip():
            warnings.append(f"grid_color must be a colour string, got {color!r}; "
                            f"using {DEFAULT_GRID_COLOR}.")
            color = DEFAULT_GRID_COLOR

        width = config.get("grid_width", DEFAULT_GRID_WIDTH)
        if isinstance(width, bool) or not isinstance(width, int) or width < 1:
            warnings.append(f"grid_width must be a whole number ≥ 1, got {width!r}; "
                            f"using {DEFAULT_GRID_WIDTH}.")
            width = DEFAULT_GRID_WIDTH

        settings = cls(
            screen_diagonal_in=positive("screen_diagonal_in", DEFAULT_SCREEN_DIAGONAL_IN),
            cell_size_in=positive("cell_size_in", DEFAULT_CELL_SIZE_IN),
            pixels_per_inch=positive("pixels_per_inch", None),
            color=color.strip(),
            width=width,
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

def cell_to_pixels(col: int, row: int, cell: float) -> tuple[float, float]:
    """Top-left pixel of a cell. 1-based, origin top-left: (1, 1) → (0, 0).

    The single source of truth for where a cell is. Grid lines, coordinate
    labels and anything placed on a cell must all go through this so they
    cannot drift apart on the fractional edge cells.
    """
    return (col - 1) * cell, (row - 1) * cell


def grid_line_positions(extent_px: float, cell: float) -> list[float]:
    """Offsets of every grid line from 0 up to and including ``extent_px``.

    Line i sits at the top-left edge of cell i+1, so it goes through
    cell_to_pixels. The last cell is usually partial.
    """
    if cell <= 0:
        raise ValueError("cell size must be positive")
    count = int(extent_px // cell) + 1
    return [cell_to_pixels(i + 1, 1, cell)[0] for i in range(count)]
