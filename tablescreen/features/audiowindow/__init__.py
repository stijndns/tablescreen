"""
audiowindow — Display images and play music.

The original AudioWindowCMD functionality, as a feature. Owns its own image
state, its views, and an audio player; knows nothing about combat, the
battlemap, or any other feature.

Threading, which is the thing to preserve when reading this:

  * ``_do_*`` methods run on the SHELL thread. They may touch this feature's
    plain state and send bus messages. They must never touch a widget.
  * ``_on_message`` runs on the MAINLOOP thread. Widget work — showing the
    slot, rendering, fullscreen — happens there.

Audio is the exception: pygame does not involve Tk, so the audio commands
act directly rather than going through the bus.
"""

from __future__ import annotations

import platform

from PIL import Image

from ...core.completion import tab_completion
from ...core.feature import FeatureBase, ShellServices
from .audio import AudioPlayer, AUDIO_EXTENSIONS
from .view import ImageView

CURRENT_OS = platform.system()


class AudiowindowFeature(FeatureBase):
    """Images on a shared window, plus music playback."""

    name = "audiowindow"

    # __init__ must only set attributes: FEATURE below is constructed at
    # import time, before the Tk root exists.
    def __init__(self) -> None:
        super().__init__()
        self.state: dict = {"image": None}
        self.audio = AudioPlayer()
        self.slot = None

    # ── Build ─────────────────────────────────────────────────────────────

    def build(self, services: ShellServices) -> None:
        self.services = services

        window_name = services.window_name()
        self.slot = services.slot(window_name)
        view = ImageView(self.slot.frame)
        self.views.append(view)

        # Re-letterbox the image when the window is resized or fullscreened.
        services.windows.get_window(window_name).on_geometry_change(view.rescale)

        services.register_command(
            "show", self._do_show, self._complete_show,
            help_text="show <file> — display an image from assets/images.")
        services.register_command(
            "fullscreen", self._do_fullscreen,
            help_text="fullscreen — fill the monitor the window sits on.")
        services.register_command(
            "restore", self._do_restore,
            help_text="restore — default size; also reopens the window if it was closed.")
        services.register_command(
            "minimize", self._do_minimize,
            help_text="minimize — iconify the window.")
        services.register_command(
            "play", self._do_play, self._complete_play,
            help_text="play <file> — play music from assets/audio.")
        services.register_command(
            "volume", self._do_volume,
            help_text="volume | volume <0-100> — get or set playback volume.")
        services.register_command(
            "stop", self._do_stop,
            help_text="stop — stop playback.")

        services.subscribe(self._on_message)

    def snapshot(self) -> dict:
        return dict(self.state)

    def shutdown(self) -> None:
        self.audio.shutdown()

    # ── Commands (shell thread — state and messages only) ─────────────────

    def _do_show(self, arg: str) -> None:
        filename = arg.strip()
        if not filename:
            print("Usage: show <file>")
            return
        self.state["image"] = filename
        self.services.send(self.name, "show")

    def _do_fullscreen(self, arg: str) -> None:
        self.services.send(self.name, "fullscreen")

    def _do_restore(self, arg: str) -> None:
        self.services.send(self.name, "restore")

    def _do_minimize(self, arg: str) -> None:
        self.services.send(self.name, "minimize")

    # Audio does not touch Tk, so these act directly.
    def _do_play(self, arg: str) -> None:
        filename = arg.strip()
        if not filename:
            print("Usage: play <file>")
            return
        self.audio.play(filename)

    def _do_volume(self, arg: str) -> None:
        text = arg.strip()
        if not text:
            print(self.audio.report_volume())
            return
        try:
            value = float(text)
        except ValueError:
            print("[!] Volume must be a number between 0 and 100.")
            return
        print(self.audio.set_volume(value))

    def _do_stop(self, arg: str) -> None:
        self.audio.stop()

    # ── Bus consumer (mainloop thread — safe to touch widgets) ────────────

    def _on_message(self, message) -> None:
        window = self.services.windows.get_window(self.services.window_name())

        if message.command == "show":
            self.slot.show()      # takes the screen back from any other slot
            self.refresh()
        elif message.command == "fullscreen":
            monitor = window.fullscreen()
            print(f"[+] Fullscreen on monitor {monitor}")
        elif message.command == "restore":
            reopened = window.restore()
            print("[+] Reopened the window." if reopened else "[+] Restored window.")
        elif message.command == "minimize":
            window.minimize()
            print("[+] Minimized window.")

    # ── Tab completion ────────────────────────────────────────────────────

    @staticmethod
    def _clean(text: str, line: str) -> str:
        """On Linux, readline hands back only the last token; recover the
        full partial path so directory navigation completes correctly."""
        if CURRENT_OS == "Linux" and len(line.split()) > 1:
            return line.split()[1]
        return text

    def _complete_show(self, text, line, begidx, endidx) -> list[str]:
        return tab_completion(self._clean(text, line),
                              list(Image.registered_extensions()),
                              CURRENT_OS, "image")

    def _complete_play(self, text, line, begidx, endidx) -> list[str]:
        return tab_completion(self._clean(text, line),
                              AUDIO_EXTENSIONS, CURRENT_OS, "audio")


FEATURE = AudiowindowFeature()
