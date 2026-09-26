"""
battlemapstate.py — Battlemap state model for Tablescreen.

Plain data, mutated on the shell thread and handed to the views as a
snapshot. Nothing here knows about pixels; the view turns the grid scale into
a cell size using the calibration in grid.py.

Only what every view must agree on belongs here. The grid scale defines what
a cell *is* (coordinates and AoE will depend on it), so it is shared. Whether
a view draws the grid is presentation, so it lives on the view.
"""

from typing import Optional


class BattleMapState:
    """Manages the full battlemap."""

    def __init__(self):
        self.bgimage: Optional[str] = None
        # Percentage of the calibrated cell size; 100 = cell_size_in exactly.
        self.grid_scale_pct: float = 100.0

    def snapshot(self) -> dict:
        """Return a dict for the battlemap state."""
        return {
            "bgimage": self.bgimage,
            "grid_scale_pct": self.grid_scale_pct,
        }

    def load_map_image(self, filename: str) -> None:
        self.bgimage = filename

    def clear_map_image(self) -> None:
        self.bgimage = None

    def set_grid_scale(self, pct: float) -> None:
        self.grid_scale_pct = pct
