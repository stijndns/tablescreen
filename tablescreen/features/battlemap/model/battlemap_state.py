"""
battlemapstate.py — Battlemap state model for Tablescreen.

Mutated on the shell thread, handed to views as a snapshot. Only what every
view must agree on lives here (cell scale, coordinate notation); what a view
shows, and where, lives on the view.
"""

from typing import Optional


class BattleMapState:
    """Manages the full battlemap."""

    def __init__(self):
        self.bgimage: Optional[str] = None
        # Percentage of the calibrated cell size; 100 = cell_size_in exactly.
        self.grid_scale_pct: float = 100.0
        # "numbers" (5,8) or "letters" (E8). Row first either way. The
        # feature sets the configured default during build().
        self.coords_style: str = "numbers"

    def snapshot(self) -> dict:
        """Return a dict for the battlemap state."""
        return {
            "bgimage": self.bgimage,
            "grid_scale_pct": self.grid_scale_pct,
            "coords_style": self.coords_style,
        }

    def load_map_image(self, filename: str) -> None:
        self.bgimage = filename

    def clear_map_image(self) -> None:
        self.bgimage = None

    def set_grid_scale(self, pct: float) -> None:
        self.grid_scale_pct = pct

    def set_coords_style(self, style: str) -> None:
        self.coords_style = style
