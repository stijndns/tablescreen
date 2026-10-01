"""
battlemapstate.py — Battlemap state model for Tablescreen.

Mutated on the shell thread, handed to views as a snapshot. Only what every
view must agree on lives here (cell scale, coordinate notation); what a view
shows, and where, lives on the view.
"""

import re
from dataclasses import dataclass, replace
from typing import Optional

from .grid import CellArea

SPRITE_NAME = re.compile(r"[A-Za-z0-9_-]+")


@dataclass(frozen=True)
class Sprite:
    name: str
    file: str               # relative to assets/images
    area: CellArea          # footprint; the image is fitted into it
    rotation: float = 0     # degrees clockwise, 0 <= rotation < 360


class BattleMapState:
    """Manages the full battlemap."""

    def __init__(self):
        self.bgimage: Optional[str] = None
        # Percentage of the calibrated cell size; 100 = cell_size_in exactly.
        self.grid_scale_pct: float = 100.0
        # "numbers" (5,8) or "letters" (E8). Row first either way. The
        # feature sets the configured default during build().
        self.coords_style: str = "numbers"
        # Add order = draw order. Replaced, never mutated, so a snapshot taken
        # on the mainloop can't see a half-applied change.
        self.sprites: tuple[Sprite, ...] = ()
        self._sprite_counter = 0     # default names never reuse a number

    def snapshot(self) -> dict:
        """Return a dict for the battlemap state."""
        return {
            "bgimage": self.bgimage,
            "grid_scale_pct": self.grid_scale_pct,
            "coords_style": self.coords_style,
            "sprites": self.sprites,
        }

    def load_map_image(self, filename: str) -> None:
        self.bgimage = filename

    def clear_map_image(self) -> None:
        self.bgimage = None

    def set_grid_scale(self, pct: float) -> None:
        self.grid_scale_pct = pct

    def set_coords_style(self, style: str) -> None:
        self.coords_style = style

    # ── Sprites ───────────────────────────────────────────────────────────

    def find_sprite(self, name: str) -> Optional[Sprite]:
        """Case-insensitive lookup by name."""
        key = name.lower()
        return next((s for s in self.sprites if s.name.lower() == key), None)

    def add_sprite(self, file: str, area: CellArea,
                   name: Optional[str] = None) -> Sprite:
        """Add a sprite; raises ValueError for an invalid or taken name."""
        if name is None:
            name = self._next_default_name()
        elif not SPRITE_NAME.fullmatch(name):
            raise ValueError(f"Invalid sprite name '{name}': use letters, "
                             f"digits, '_' and '-' only.")
        elif self.find_sprite(name):
            raise ValueError(f"A sprite named '{name}' already exists.")
        sprite = Sprite(name, file, area)
        self.sprites = self.sprites + (sprite,)
        return sprite

    def remove_sprite(self, name: str) -> Optional[Sprite]:
        sprite = self.find_sprite(name)
        if sprite is not None:
            self.sprites = tuple(s for s in self.sprites if s is not sprite)
        return sprite

    def rotate_sprite(self, name: str, degrees: float) -> Optional[Sprite]:
        """Rotate by ``degrees`` clockwise (negative = counter-clockwise),
        relative to the current rotation. Keeps the sprite's draw order."""
        old = self.find_sprite(name)
        return old and self._replace(old, rotation=(old.rotation + degrees) % 360)

    def move_sprite(self, name: str, row: int, col: int) -> Optional[Sprite]:
        """Move the footprint's top-left to (row, col), keeping its size."""
        old = self.find_sprite(name)
        return old and self._replace(old, area=old.area.moved_to(row, col))

    def resize_sprite(self, name: str, area: CellArea) -> Optional[Sprite]:
        """Set the footprint exactly (may also shift the sprite)."""
        old = self.find_sprite(name)
        return old and self._replace(old, area=area)

    def _replace(self, old: Sprite, **changes) -> Sprite:
        """Swap in an updated record at the same position (draw order holds)."""
        new = replace(old, **changes)
        self.sprites = tuple(new if s is old else s for s in self.sprites)
        return new

    def clear_sprites(self) -> int:
        """Remove all sprites in one change; returns how many there were.
        The name counter keeps counting, so old names still aren't reused."""
        count = len(self.sprites)
        self.sprites = ()
        return count

    def _next_default_name(self) -> str:
        while True:
            self._sprite_counter += 1
            name = f"sprite{self._sprite_counter}"
            if not self.find_sprite(name):      # skip names taken via 'as'
                return name
