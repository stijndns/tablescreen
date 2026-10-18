"""
sprite_images.py — Decoding sprite images, static or animated. No Tk.

Animated sprites are small, so all frames are decoded up front (per cell
size); only the backdrop video needs streaming.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from PIL import Image, ImageSequence

DEFAULT_FRAME_S = 0.1
FRAME_MEMORY_WARN_BYTES = 400 * 2**20   # warn (but still show) above this


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


def fit_size(size: tuple[int, int], box: tuple[float, float]) -> tuple[int, int]:
    """Largest size with the same proportions that fits in the box."""
    width, height = size
    scale = min(box[0] / width, box[1] / height)
    return max(1, round(width * scale)), max(1, round(height * scale))


def rotated_size(size: tuple[int, int], degrees: float) -> tuple[int, int]:
    """Bounding box of an image rotated by ``degrees`` (what gets fitted)."""
    width, height = size
    if degrees % 90 == 0:
        return (height, width) if degrees % 180 else (width, height)
    rad = math.radians(degrees)
    cos, sin = abs(math.cos(rad)), abs(math.sin(rad))
    return (math.ceil(width * cos + height * sin), math.ceil(width * sin + height * cos))


def scaled_frames(path: Path, size: tuple[int, int],
                  rotation: float = 0) -> Iterator[Image.Image]:
    """Every frame as RGBA, rotated clockwise then scaled to ``size``.
    Rotating first, at source resolution, keeps it sharp. A generator, so the
    caller can convert and drop each frame before the next is made."""
    with Image.open(path) as img:
        for frame in ImageSequence.Iterator(img):
            frame = frame.convert("RGBA")
            if rotation:
                # Pillow rotates counter-clockwise; multiples of 90 are exact.
                frame = frame.rotate(-rotation, resample=Image.BICUBIC, expand=True)
            yield frame.resize(size, Image.LANCZOS)


def ghost_image(path: Path, rotation: float, box: tuple[float, float],
                opacity: float) -> Image.Image:
    """A see-through preview of a sprite: its first frame, rotated and fitted
    into ``box`` like the battlemap draws it, at ``opacity`` (0-1). Reads only
    the first frame (read_source walks all of a GIF's frames: ~30 ms for 24 of
    them, per ghost size) and resizes with BILINEAR: fast enough to keep up
    with a drag, sharp enough for a preview."""
    with Image.open(path) as img:
        frame = img.convert("RGBA")
    if rotation:
        # Pillow rotates counter-clockwise; multiples of 90 are exact.
        frame = frame.rotate(-rotation, resample=Image.BICUBIC, expand=True)
    frame = frame.resize(fit_size(frame.size, box), Image.BILINEAR)
    alpha = frame.getchannel("A").point(lambda a: round(a * opacity))
    frame.putalpha(alpha)
    return frame
