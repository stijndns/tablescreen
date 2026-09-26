"""
battlemap_view.py — The battlemap display.
"""

from __future__ import annotations

import tkinter as tk
from typing import Optional

from PIL import Image, ImageTk

from ..styling import *

from typing import Tuple

from ....core.paths import IMAGES_DIR

class BattleMapView(tk.Frame):
    """Displays one image, scaled to fit its frame while preserving aspect."""

    def __init__(self, parent: tk.Widget):
        super().__init__(parent, bg=PALETTE["bg"])
        self.canvas = tk.Canvas(self, bg=PALETTE["surface"], bd=0, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self._original: Optional[Image.Image] = None
        self._photo: Optional[ImageTk.PhotoImage] = None
        self._filename: Optional[str] = None

        self.grid = False

        self.canvas.bind("<Configure>", lambda e: self.rescale((e.width, e.height)))

    # ── Loading ───────────────────────────────────────────────────────────

    def load(self, filename: str) -> bool:
        """Load an image by name, relative to assets/images."""
        if not filename:
            self._original = None
            self._photo = None
            self._filename = None
            return True

        path = IMAGES_DIR / filename
        if not path.exists():
            print(f"[!] File not found: {path}")
            return False
        try:
            self._original = Image.open(path)
            self._filename = filename
            print(f"[+] Loaded image: {filename}")
            return True
        except Exception as exc:
            print(f"[!] Error loading image: {exc}")
            return False

    # ── Rendering ─────────────────────────────────────────────────────────

    def render(self, snapshot: dict) -> None:
        """Render from a feature snapshot. Loads the image if it changed."""
        filename = snapshot.get("bgimage")
        if filename != self._filename:
            if not self.load(filename):
                return
        self.rescale()

    def rescale(self, new_size: Tuple[int, int] | None = None) -> None:
        """Redraw the current image letterboxed into the frame."""
        if self._original is None:
            self.canvas.delete("backdrop")
            return

        if new_size is not None:
            width, height = new_size
        else:
            width = self.canvas.winfo_width()
            height = self.canvas.winfo_height()
            # During construction the widget reports 1x1; nothing useful to draw.
        if width <= 1 or height <= 1:
            return

        img = self._original.copy()
        img = img.resize((width, height), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(img)
        self.canvas.delete("backdrop")
        self.canvas.create_image(0, 0, image=self._photo, anchor="nw", tags="backdrop")

        if self.grid:
            self.draw_grid()

#    def draw_grid(self):
        

    # def clear(self) -> None:
    #     self._original = None
    #     self._photo = None
    #     self._filename = None
    #     self.label.config(image="")
