"""
audio.py — Music playback for the audiowindow feature.

Wraps pygame's mixer. Extracted from the command bodies so the commands stay
thin and the playback state (the user-facing volume) lives in one place.

Audio does not touch Tk, so unlike the display side it needs no bus round
trip — the shell thread can call these directly.
"""

from __future__ import annotations

import threading
from typing import Optional

import pygame

from ...core.paths import AUDIO_DIR

AUDIO_EXTENSIONS = [".mp3", ".wav", ".ogg"]


class AudioPlayer:
    """Loads and plays music files from the audio assets directory."""

    def __init__(self) -> None:
        # The volume the user asked for (0-100). pygame stores 0.0-1.0, so we
        # keep the human-facing number to report it back accurately.
        self.user_volume: Optional[float] = None

    # ── Playback ──────────────────────────────────────────────────────────

    def play(self, filename: str) -> None:
        """Start playing a file. Loading happens on a worker thread because
        decoding a large file can block for a noticeable moment."""
        path = AUDIO_DIR / filename

        def _worker() -> None:
            try:
                pygame.mixer.init()
                pygame.mixer.music.load(str(path))
                pygame.mixer.music.play()
                # Re-apply the user's volume: mixer.init() resets it, so
                # without this a new track would jump back to full volume.
                if self.user_volume is not None:
                    pygame.mixer.music.set_volume(self.user_volume / 100.0)
                print(f"[+] Playing {filename}")
            except Exception as exc:
                print(f"[!] Could not play {path}: {exc}")

        threading.Thread(target=_worker, daemon=True).start()

    def stop(self) -> None:
        try:
            pygame.mixer.music.stop()
            print("[+] Playback stopped.")
        except Exception as exc:
            print(f"[!] Could not stop playback: {exc}")

    # ── Volume ────────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        return bool(pygame.mixer.get_init())

    def report_volume(self) -> str:
        if not self.is_ready():
            return "[!] Mixer not initialised. Play something first."
        internal = pygame.mixer.music.get_volume()
        if self.user_volume is not None:
            return f"Volume set to {self.user_volume}% (internal: {internal:.3f})"
        return f"Volume not manually set. Internal value: {internal:.3f}"

    def set_volume(self, value: float) -> str:
        if not self.is_ready():
            return "[!] Mixer not initialised. Play something first."
        if not 0 <= value <= 100:
            return "[!] Volume must be between 0 and 100."
        try:
            pygame.mixer.music.set_volume(value / 100.0)
            self.user_volume = value
            return f"Volume set to {value}%"
        except Exception as exc:
            return f"[!] Could not adjust volume: {exc}"

    def shutdown(self) -> None:
        """Stop playback and release the mixer on app exit."""
        try:
            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
                pygame.mixer.quit()
        except Exception:
            pass