"""
battlemap_view.py — The battlemap display.

One canvas in tagged layers: backdrop → sprites → grid → coords (bottom to top).
The backdrop is a static image or a streamed video (see animation.py); it is
expensive, so other layers never force it to redraw. Mainloop thread only.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable, Optional, Tuple

from PIL import Image, ImageTk

from ..styling import *
from ..model.grid import (GridSettings, DEFAULT_GRID_COLOR, MIN_CELL_PX, cell_px,
                          cell_to_pixels, coord_labels, grid_line_positions,
                          label_font_px)
from ..video import VideoStream, is_video, oversize_warning, probe
from .animation import AnimationClock, VideoBackdrop

from ....core.paths import IMAGES_DIR

BACKDROP_TAG = "backdrop"
SPRITES_TAG = "sprites"
GRID_TAG = "grid"
COORDS_TAG = "coords"

LABEL_SHADOW_COLOR = "black"    # no canvas transparency, so labels get a shadow

# Returns the current pixels per inch, or None if it cannot be determined.
PpiProvider = Callable[[], Optional[float]]


class BattleMapView(tk.Frame):
    """Displays the battlemap backdrop with optional grid and coordinates."""

    def __init__(self, parent: tk.Widget, grid_settings: GridSettings,
                 ppi_provider: PpiProvider):
        super().__init__(parent, bg=PALETTE["bg"])
        self.canvas = tk.Canvas(self, bg=PALETTE["surface"], bd=0, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self._original: Optional[Image.Image] = None
        self._photo: Optional[ImageTk.PhotoImage] = None
        self._filename: Optional[str] = None
        # (filename, width, height) of the backdrop currently on the canvas.
        # Rescaling a 4K image costs ~100 MB transiently, so skip it when
        # only another layer (e.g. the grid) changed.
        self._backdrop_key: Optional[tuple] = None

        self._grid_settings = grid_settings
        self._grid_color = self._valid_color(
            grid_settings.color, "grid_color", DEFAULT_GRID_COLOR)
        self._label_color = self._valid_color(
            grid_settings.label_color, "coords_color", self._grid_color)
        self._ppi_provider = ppi_provider
        # Per-view settings; scale and style are shared and come in the snapshot.
        self._show_grid = False
        self._show_coords = False
        self._coords_location = grid_settings.coords_location
        self._grid_scale_pct = 100.0
        self._coords_style = grid_settings.coords_style

        self._sprites: tuple = ()           # from the snapshot
        self._sprite_sources: dict[str, Optional[Image.Image]] = {}  # None = failed
        self._sprite_photos: dict[tuple, ImageTk.PhotoImage] = {}    # (file, w, h)

        self._clock = AnimationClock(self.canvas)
        self._video_path = None             # set when the backdrop is a video
        self._video: Optional[VideoBackdrop] = None

        self.canvas.bind("<Configure>", lambda e: self.rescale((e.width, e.height)))

    def _valid_color(self, color: str, key: str, fallback: str) -> str:
        """Tk is the authority on colour names; fall back if it rejects one."""
        try:
            self.canvas.winfo_rgb(color)
            return color
        except tk.TclError:
            print(f"[!] battlemap: unknown {key} {color!r}; using {fallback}.")
            return fallback

    # ── Loading ───────────────────────────────────────────────────────────

    def load(self, filename: str) -> bool:
        """Load an image or video by name, relative to assets/images."""
        if not filename:
            self._stop_video()
            self._original = None
            self._photo = None
            self._filename = None
            return True

        path = IMAGES_DIR / filename
        if not path.exists():
            print(f"[!] File not found: {path}")
            return False
        if is_video(filename):
            return self._load_video(path, filename)
        try:
            original = Image.open(path)
        except Exception as exc:
            print(f"[!] Error loading image: {exc}")
            return False
        self._stop_video()
        self._original = original
        self._filename = filename
        print(f"[+] Loaded image: {filename}")
        return True

    def _load_video(self, path, filename: str) -> bool:
        try:
            info = probe(path)
        except Exception as exc:
            print(f"[!] Error loading video: {exc}")
            return False
        self._stop_video()
        self._original = None
        self._video_path = path
        self._filename = filename
        length = f", {info.duration:.0f} s loop" if info.duration else ""
        print(f"[+] Loaded video: {filename} ({info.width}x{info.height} "
              f"@ {info.fps:g} fps{length})")
        warning = oversize_warning(info, filename)
        if warning:
            print(f"[!] {warning}")
        return True

    def _stop_video(self) -> None:
        if self._video is not None:
            self._clock.remove(self._video)
            self._video.close()
            self._video = None
        self._video_path = None

    def close(self) -> None:
        """Stop background work (the video decoder). Called on shutdown."""
        self._stop_video()

    # ── Rendering ─────────────────────────────────────────────────────────

    def render(self, snapshot: dict) -> None:
        """Render from a feature snapshot. Loads the image if it changed."""
        self._grid_scale_pct = snapshot.get("grid_scale_pct", 100.0)
        self._coords_style = snapshot.get("coords_style", self._coords_style)
        self._sprites = snapshot.get("sprites", ())

        filename = snapshot.get("bgimage")
        if filename != self._filename:
            self.load(filename)     # on failure keep drawing the grid anyway
        self.rescale()

    def rescale(self, new_size: Tuple[int, int] | None = None) -> None:
        """Redraw every layer at the canvas's current (or given) size."""
        if new_size is not None:
            width, height = new_size
        else:
            width = self.canvas.winfo_width()
            height = self.canvas.winfo_height()
        # During construction the widget reports 1x1; nothing useful to draw.
        if width <= 1 or height <= 1:
            return

        self._draw_backdrop(width, height)
        self._draw_sprites(width, height)
        self._draw_grid(width, height)
        self._draw_coords(width, height)

    def _restack(self) -> None:
        """Enforce backdrop → sprites → grid → coords; new items land on top."""
        self.canvas.tag_lower(BACKDROP_TAG)
        self.canvas.tag_raise(SPRITES_TAG)
        self.canvas.tag_raise(GRID_TAG)
        self.canvas.tag_raise(COORDS_TAG)

    def _draw_backdrop(self, width: int, height: int) -> None:
        key = (self._filename, width, height)
        if key == self._backdrop_key:
            return      # same image at the same size is already drawn
        self._backdrop_key = key

        if self._video_path is not None:
            if self._video is None:
                stream = VideoStream(self._video_path, (width, height))
                self._video = VideoBackdrop(self.canvas, stream, (width, height),
                                            BACKDROP_TAG)
                self._clock.add(self._video)
            else:
                self._video.resize((width, height))
            self._restack()
            return

        # Free the old image first (item before photo: Tk holds the data while
        # an item uses it), so two full-size copies never coexist.
        self.canvas.delete(BACKDROP_TAG)
        self._photo = None
        if self._original is None:
            return

        img = self._original.resize((width, height), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(img)
        del img     # the PhotoImage holds its own copy
        self.canvas.create_image(0, 0, image=self._photo, anchor="nw", tags=BACKDROP_TAG)
        self._restack()

    # ── Sprites ───────────────────────────────────────────────────────────

    def _draw_sprites(self, width: int, height: int) -> None:
        self.canvas.delete(SPRITES_TAG)
        cell = self.current_cell_px()
        if cell is None or cell < MIN_CELL_PX:
            return

        photos = {}         # only keep photos still in use at this cell size
        for sprite in self._sprites:
            x, y = cell_to_pixels(row=sprite.row, col=sprite.col, cell=cell)
            if x >= width or y >= height:
                continue                    # off the visible grid
            source = self._sprite_source(sprite.file)
            if source is None:
                continue
            scale = min(cell / source.width, cell / source.height)
            size = (max(1, round(source.width * scale)),
                    max(1, round(source.height * scale)))
            key = (sprite.file, *size)
            photo = photos.get(key) or self._sprite_photos.get(key)
            if photo is None:
                photo = ImageTk.PhotoImage(source.resize(size, Image.LANCZOS))
            photos[key] = photo
            self.canvas.create_image(x + cell / 2, y + cell / 2, image=photo,
                                     anchor="center", tags=SPRITES_TAG)
        self._sprite_photos = photos
        self._restack()

    def _sprite_source(self, file: str) -> Optional[Image.Image]:
        """Decoded sprite image, loaded once per file (first frame for GIFs)."""
        if file not in self._sprite_sources:
            try:
                with Image.open(IMAGES_DIR / file) as img:
                    self._sprite_sources[file] = img.convert("RGBA")
            except Exception as exc:
                print(f"[!] Could not load sprite image {file}: {exc}")
                self._sprite_sources[file] = None       # don't retry every draw
        return self._sprite_sources[file]

    # ── Grid ──────────────────────────────────────────────────────────────

    @property
    def show_grid(self) -> bool:
        return self._show_grid

    def set_show_grid(self, on: bool) -> None:
        """Show or hide this view's grid. Redraws only the grid layer."""
        self._show_grid = on
        self._draw_grid(self.canvas.winfo_width(), self.canvas.winfo_height())

    def current_cell_px(self) -> Optional[float]:
        """Cell size in pixels at the current scale, or None if unknown."""
        ppi = self._ppi_provider()
        if ppi is None:
            return None
        return cell_px(ppi, self._grid_settings.cell_size_in, self._grid_scale_pct)

    def _draw_grid(self, width: int, height: int) -> None:
        self.canvas.delete(GRID_TAG)
        if not self._show_grid or width <= 1 or height <= 1:
            return
        cell = self.current_cell_px()
        if cell is None or cell < MIN_CELL_PX:
            return

        line = {"fill": self._grid_color, "width": self._grid_settings.width,
                "tags": GRID_TAG}
        for x in grid_line_positions(width, cell):
            self.canvas.create_line(x, 0, x, height, **line)
        for y in grid_line_positions(height, cell):
            self.canvas.create_line(0, y, width, y, **line)
        self._restack()

    # ── Coordinates ───────────────────────────────────────────────────────

    @property
    def show_coords(self) -> bool:
        return self._show_coords

    @property
    def coords_location(self) -> str:
        return self._coords_location

    def set_show_coords(self, on: bool) -> None:
        """Show or hide this view's coordinates. Redraws only that layer."""
        self._show_coords = on
        self._draw_coords(self.canvas.winfo_width(), self.canvas.winfo_height())

    def set_coords_location(self, location: str) -> None:
        """"sides" or "cells". Redraws only the coordinates layer."""
        self._coords_location = location
        self._draw_coords(self.canvas.winfo_width(), self.canvas.winfo_height())

    def coords_font_px(self) -> Optional[int]:
        """Label font size at the current cell size, or None if too small."""
        cell = self.current_cell_px()
        if cell is None or cell < MIN_CELL_PX:
            return None
        return label_font_px(cell, self._coords_location)

    def _draw_coords(self, width: int, height: int) -> None:
        self.canvas.delete(COORDS_TAG)
        if not self._show_coords or width <= 1 or height <= 1:
            return
        size = self.coords_font_px()
        if size is None:
            return
        cell = self.current_cell_px()

        # Negative size = pixels, not points: exact on the DPI-aware TV and
        # proportional to the cell whatever the display scaling.
        font = (FONT_FAMILY, -size, "bold")
        offset = max(1, size // 12)
        for label in coord_labels(width, height, cell,
                                  self._coords_location, self._coords_style):
            shadow = self.canvas.create_text(
                label.x + offset, label.y + offset, text=label.text,
                anchor=label.anchor, font=font, fill=LABEL_SHADOW_COLOR,
                tags=COORDS_TAG)
            text = self.canvas.create_text(
                label.x, label.y, text=label.text, anchor=label.anchor,
                font=font, fill=self._label_color, tags=COORDS_TAG)
            # Partial edge cells: drop a label Tk says would be clipped.
            x1, y1, x2, y2 = self.canvas.bbox(text)
            if x2 > width or y2 > height:
                self.canvas.delete(shadow, text)
        self._restack()
