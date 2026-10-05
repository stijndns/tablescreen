"""
video.py — Streaming decode of animated backdrops. No Tk.

One daemon thread decodes frames, scales them to the canvas size in FFmpeg
and hands them over through a small bounded queue, so memory stays at a few
frames regardless of video length. The Tk side (views/animation.py) takes
frames off the queue on the mainloop.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import av
from PIL import Image

VIDEO_EXTENSIONS = (".webm",)

# Measured budget at 4K: ~35 ms of mainloop per frame, so 15 fps is the cap.
MAX_FPS = 15
MAX_SIZE = (3840, 2160)

DECODER_THREADS = 2     # FFmpeg workers: 1 is too slow at 4K, 4 gains nothing
QUEUE_FRAMES = 2

CONVERT_HINT = ('ffmpeg -i "{src}" -vf "fps=15,scale=3840:2160:flags=lanczos" -an '
                '-c:v libvpx-vp9 -crf 32 -b:v 0 -row-mt 1 -deadline good '
                '-cpu-used 4 "{dst}"')

Frame = tuple[float, Image.Image]      # (seconds since start, RGB image)


def is_video(filename: str) -> bool:
    return Path(filename).suffix.lower() in VIDEO_EXTENSIONS


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    fps: float
    duration: Optional[float]


def probe(path: Path) -> VideoInfo:
    """Read size, frame rate and duration from the header."""
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        rate = stream.average_rate or stream.guessed_rate
        duration = container.duration / 1e6 if container.duration else None
        return VideoInfo(stream.codec_context.width, stream.codec_context.height,
                         float(rate) if rate else 0.0, duration)


def first_frame(path: Path) -> Image.Image:
    """The first decodable frame at full size, as a still."""
    with av.open(str(path)) as container:
        for frame in container.decode(video=0):
            return frame.to_image()
    raise RuntimeError("no decodable frames")


def oversize_warning(info: VideoInfo, filename: str) -> Optional[str]:
    """A warning with the conversion command if playback would be too heavy."""
    too_big = info.width > MAX_SIZE[0] or info.height > MAX_SIZE[1]
    too_fast = info.fps > MAX_FPS + 0.5
    if not (too_big or too_fast):
        return None
    stem = Path(filename).stem
    return (f"{filename} is {info.width}x{info.height} @ {info.fps:g} fps; above "
            f"{MAX_SIZE[0]}x{MAX_SIZE[1]} @ {MAX_FPS} fps it costs far more CPU "
            f"and memory and may stutter. Convert it once with:\n    "
            + CONVERT_HINT.format(src=filename, dst=f"{stem}_4k15.webm"))


class VideoStream:
    """Decodes a video in a loop on a daemon thread.

    ``set_size`` and ``get`` are safe from any thread; frames arrive in order
    with timestamps that keep increasing across loops.
    """

    def __init__(self, path: Path, size: tuple[int, int]):
        self._path = path
        self._size = size
        self._queue: queue.Queue[Frame] = queue.Queue(maxsize=QUEUE_FRAMES)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"video:{path.name}")

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def set_size(self, size: tuple[int, int]) -> None:
        self._size = size       # picked up from the next decoded frame

    def get(self) -> Optional[Frame]:
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    # ── Decoder thread ────────────────────────────────────────────────────

    def _run(self) -> None:
        try:
            with av.open(str(self._path)) as container:
                self._decode_loop(container)
        except Exception as exc:
            if not self._stop.is_set():
                print(f"[!] Video {self._path.name} stopped: {exc}")

    def _decode_loop(self, container) -> None:
        stream = container.streams.video[0]
        stream.thread_type = "FRAME"
        stream.codec_context.thread_count = DECODER_THREADS
        rate = stream.average_rate or stream.guessed_rate
        interval = 1 / float(rate) if rate else 1 / MAX_FPS

        first: Optional[float] = None
        offset = last = 0.0
        while not self._stop.is_set():
            decoded = 0
            for frame in container.decode(stream):
                if self._stop.is_set():
                    return
                if frame.time is None:
                    continue
                if first is None:
                    first = frame.time
                last = offset + frame.time - first
                self._put((last, self._to_image(frame)))
                decoded += 1
            if decoded == 0:
                raise RuntimeError("no decodable frames")
            offset = last + interval        # the loop continues the timeline
            container.seek(0)

    def _to_image(self, frame) -> Image.Image:
        width, height = self._size
        if (frame.width, frame.height) == (width, height):
            return frame.to_image()
        return frame.to_image(width=width, height=height)     # scaled in FFmpeg

    def _put(self, item: Frame) -> None:
        # Blocking put is the streaming: the decoder waits for the display.
        while not self._stop.is_set():
            try:
                self._queue.put(item, timeout=0.1)
                return
            except queue.Full:
                pass
