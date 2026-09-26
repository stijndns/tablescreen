"""
battlemap — Display the grid-based battlemap.
"""

from __future__ import annotations

import platform

from PIL import Image

from ...core.completion import get_arg_parts, tab_completion
from ...core.feature import FeatureBase, ShellServices
from .model.battlemap_state import BattleMapState
from .views.battlemap_view import BattleMapView

CURRENT_OS = platform.system()

BATTLEMAP_HELP = """\
Battlemap commands:
    map show <file>                 — load and show the battlemap window
    map bgclear                     — clear the backdrop (window stays)
    map fullscreen                — borderless fullscreen on its monitor
    map restore                   — back to default windowed size
"""

class BattleMapFeature(FeatureBase):
    """Show battlemap with (optionally visible) grid on a window."""

    name = "battlemap"

    # __init__ must only set attributes: FEATURE below is constructed at
    # import time, before the Tk root exists.
    def __init__(self) -> None:
        super().__init__()
        self.state = BattleMapState()
        self.slot = None

    # ── Build ─────────────────────────────────────────────────────────────

    def build(self, services: ShellServices) -> None:
        self.services = services

        window_name = services.window_name()
        self.slot = services.slot(window_name)
        view = BattleMapView(self.slot.frame)
        view.pack(fill="both", expand=True)
        self.views.append(view)

        # Re-letterbox the image when the window is resized or fullscreened.
        services.windows.get_window(window_name).on_geometry_change(view.rescale)

        services.register_command(
            "map", self.do_battlemap, self.complete_battlemap,
            help_text="map <sub-command> — battlemap control. Type 'map help'.")

        services.subscribe(self._on_message)

    def snapshot(self) -> dict:
        return self.state.snapshot()

    # ── Commands (shell thread — state and messages only) ─────────────────

    def do_battlemap(self, arg: str) -> None:
        parts = get_arg_parts(arg)
        if not parts or parts[0] in ("help", "?"):
            print(BATTLEMAP_HELP)
            return

        sub = parts[0].lower()
        rest = parts[1:]

        if sub == "show":
            filename = rest[0] if rest else ""
            if not filename:
                print("Usage: map show <file>")
                return
            self.state.load_map_image(filename)
            self.services.send(self.name, "show")
        elif sub == "bgclear":
            self.state.clear_map_image()
            self.services.send(self.name, "show")
        elif sub == "fullscreen":
            self.services.send(self.name, "fullscreen")
        elif sub == "restore":
            self.services.send(self.name, "restore")
        elif sub == "grid":
            if rest[0].lower() == 'on':
                self.services.send(self.name, "")



    # ── Bus consumer  ─────────────────────────────────────────────────────

    def _on_message(self, message) -> None:
        window = self.services.windows.get_window(self.services.window_name())

        if message.command == "show":
            self.slot.show()
            self.refresh()
        elif message.command == "render":
            self.refresh()
        elif message.command == "fullscreen":
            monitor = window.fullscreen()
            print(f"[+] Battlemap fullscreen on monitor {monitor}")
        elif message.command == "restore":
            window.restore()
            print("[+] Restored battlemap window.")


    # ── Tab completion ────────────────────────────────────────────────────

    def complete_battlemap(self, text, line, begidx, endidx) -> list[str]:
        parts = get_arg_parts(line[:begidx])
        top_subs = ["show", "bgclear", "fullscreen", "restore"]

        if len(parts) == 1:
            return [s for s in top_subs if s.startswith(text)]

        sub = parts[1].lower()

        if sub == "show":
            if len(parts) == 2:
                clean = line.split()[-1] if not line.endswith(" ") else ""
                #print(f"clean: {clean}")
                return tab_completion(clean, list(Image.registered_extensions()),
                                        CURRENT_OS, "image")
            return []
        return []

FEATURE = BattleMapFeature()
