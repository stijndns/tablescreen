"""
layout.py — Combat screen geometry. No Tk.

Every size on the combat screen is a design size (pixels at 96 DPI and
text_scale 1) times one unit. The unit follows the display scaling, so text
keeps its physical size on any screen, and text_scale enlarges everything for
reading from across the table. Rows are sized by that same unit, so they always
fit their text; the window height only decides how many rows fit on a page.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from .styling import COND_EXTRA, PADDING, ROW_HEIGHT_BASE

REFERENCE_DPI = 96
HEADER_H = 60
ROW_GAP = 6             # space between rows; doubled above the first row
ROW_EXTRA = 4           # 2 px canvas border + 2 px bottom padding per row
MIN_ROW_H = 30

DEFAULT_TEXT_SCALE = 1.0
TEXT_SCALE_RANGE = (0.25, 4.0)

LAYOUTS = {"single": 1, "double": 2}       # name → number of columns
DEFAULT_LAYOUT = "single"
ELLIPSIS = "…"


def text_unit(pixels_per_inch: float, text_scale: float) -> float:
    """The size unit: 1.0 at 96 DPI and text_scale 1."""
    return text_scale * pixels_per_inch / REFERENCE_DPI


@dataclass(frozen=True)
class Layout:
    unit: float
    header_h: int
    pad: int
    gap: int
    row_h: int
    rows: int           # rows per column that fit in the window
    columns: int

    @property
    def page_size(self) -> int:
        return self.rows * self.columns


def compute_layout(width: int, height: int, unit: float, columns: int = 1) -> Layout:
    """How the combat screen is laid out in a window of this size."""
    header_h = int(HEADER_H * unit)
    gap = int(ROW_GAP * unit)
    row_h = max(MIN_ROW_H, int((ROW_HEIGHT_BASE + COND_EXTRA) * unit))
    first = 2 * gap + row_h + ROW_EXTRA         # the first row has a double gap
    following = gap + row_h + ROW_EXTRA
    available = height - header_h
    rows = 1 if available < first else 1 + (available - first) // following
    return Layout(unit=unit, header_h=header_h, pad=int(PADDING * unit), gap=gap,
                  row_h=row_h, rows=rows, columns=columns)


def page_of(names: Sequence[str], name: Optional[str], page_size: int) -> Optional[int]:
    """0-based page on which ``name`` appears, or None if it isn't listed."""
    if name is None or name not in names:
        return None
    return list(names).index(name) // page_size


def grid_cell(index: int, rows: int) -> tuple[int, int]:
    """(row, column) of the index-th combatant on a page: down the first
    column, then down the next. Row 0 is the first combatant row."""
    return index % rows, index // rows


def fit_text(text: str, max_px: float, measure: Callable[[str], float]) -> str:
    """``text`` unchanged if it fits in ``max_px``, else the longest prefix
    that fits with an ellipsis appended (just the ellipsis if nothing does)."""
    if measure(text) <= max_px:
        return text
    low, high = 0, len(text)                # binary search on the prefix length
    while low < high:
        mid = (low + high + 1) // 2
        if measure(text[:mid].rstrip() + ELLIPSIS) <= max_px:
            low = mid
        else:
            high = mid - 1
    return text[:low].rstrip() + ELLIPSIS


def parse_layout(value) -> tuple[str, Optional[str]]:
    """layout from config: (name, warning or None)."""
    if isinstance(value, str) and value.strip().lower() in LAYOUTS:
        return value.strip().lower(), None
    return DEFAULT_LAYOUT, (f"layout must be one of {', '.join(LAYOUTS)}, "
                            f"got {value!r}; using {DEFAULT_LAYOUT}.")


def text_scale_from_text(text: str) -> Optional[float]:
    """A text_scale typed in the shell ("1.5"), or None if invalid or out of range."""
    try:
        value = float(text)
    except ValueError:
        return None
    low, high = TEXT_SCALE_RANGE
    return value if low <= value <= high else None


def parse_text_scale(value) -> tuple[float, Optional[str]]:
    """text_scale from config: (value, warning or None)."""
    low, high = TEXT_SCALE_RANGE
    if isinstance(value, (int, float)) and not isinstance(value, bool) and low <= value <= high:
        return float(value), None
    return DEFAULT_TEXT_SCALE, (f"text_scale must be a number from {low:g} to {high:g}, "
                                f"got {value!r}; using {DEFAULT_TEXT_SCALE:g}.")
