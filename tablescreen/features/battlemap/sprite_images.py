"""
sprite_images.py — Decoding sprite images, static or animated. No Tk.

Animated sprites are small, so all frames are decoded up front (per cell
size); only the backdrop video needs streaming.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image, ImageSequence

DEFAULT_FRAME_S = 0.1


@dataclass(frozen=True)
class SpriteSource:
    size: tuple[int, int]
    durations: tuple[float, ...]    # seconds, one per frame

    @property
    def animated(self) -> bool:
        return len(self.durations) > 1


def frame_duration(ms: Optional[float]) -> float:
    """Seconds per frame. Missing or ≤10 ms means 100 ms, as browsers do."""
    return ms / 1000 if ms and ms > 10 else DEFAULT_FRAME_S


def read_source(path: Path) -> SpriteSource:
    """Size and per-frame durations (a static image has one frame)."""
    with Image.open(path) as img:
        durations = tuple(frame_duration(frame.info.get("duration"))
                          for frame in ImageSequence.Iterator(img))
        return SpriteSource(img.size, durations)


def fit_size(size: tuple[int, int], cell: float) -> tuple[int, int]:
    """Largest size with the same proportions that fits in the cell."""
    width, height = size
    scale = min(cell / width, cell / height)
    return max(1, round(width * scale)), max(1, round(height * scale))


def scaled_frames(path: Path, size: tuple[int, int]) -> list[Image.Image]:
    """Every frame as RGBA at ``size``, one source frame decoded at a time."""
    with Image.open(path) as img:
        return [frame.convert("RGBA").resize(size, Image.LANCZOS)
                for frame in ImageSequence.Iterator(img)]
