"""
battlemap — Display the grid-based battlemap.

The grid is calibrated in physical inches (see model/grid.py). Pixels per
inch come from config's `pixels_per_inch` override if set, otherwise from the
screen diagonal in config and the resolution of the monitor the window is on.
Resolving that needs Tk, so it happens on the mainloop thread, in
``_calibration()``, which the view calls every time it draws the grid.
"""

from __future__ import annotations

import platform
from typing import Optional

from PIL import Image

from ...core.completion import get_arg_parts, tab_completion
from ...core.feature import FeatureBase, ShellServices
from .model.battlemap_state import BattleMapState
from .model.grid import (COORD_LOCATIONS, COORD_STYLES, GridSettings,
                         MAX_SCALE_PCT, MIN_CELL_PX, MIN_LABEL_FONT_PX,
                         MIN_SCALE_PCT, cell_px, label_font_px,
                         pixels_per_inch)
from .views.battlemap_view import BattleMapView

CURRENT_OS = platform.system()

BATTLEMAP_HELP = """\
Battlemap commands:
    map show <file>                 — load and show the battlemap window
    map bgclear                     — clear the backdrop (window stays)
    map fullscreen                  — borderless fullscreen on its monitor
    map restore                     — back to default windowed size
    map grid                        — show grid status and calibration
    map grid on | off               — show or hide the grid
    map grid resize <percent>       — cell size as % of the calibrated size
                                      (100 = config's cell_size_in); affects
                                      grid lines and coords
    map coords                      — show coordinate label status
    map coords on | off             — show or hide coordinate labels
    map coords style numbers | letters
                                    — row first: 5,8 (numbers) or E8 (letters)
    map coords location sides | cells
                                    — along the top/left edges, or in every cell
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
        self.grid_settings = GridSettings()

    # ── Build ─────────────────────────────────────────────────────────────

    def build(self, services: ShellServices) -> None:
        self.services = services

        self.grid_settings, warnings = GridSettings.from_config(services.config)
        for warning in warnings:
            print(f"[!] battlemap: {warning}")
        self.state.set_coords_style(self.grid_settings.coords_style)

        window_name = services.window_name()
        self.slot = services.slot(window_name)
        view = BattleMapView(self.slot.frame, self.grid_settings,
                             lambda: self._calibration()[0])
        view.pack(fill="both", expand=True)
        self.views.append(view)

        # Redraw backdrop and grid when the window is resized or fullscreened.
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
            self._cmd_grid(rest)
        elif sub == "coords":
            self._cmd_coords(rest)
        else:
            print(f"[!] Unknown map sub-command '{sub}'. Type 'map help'.")

    def _cmd_grid(self, rest: list[str]) -> None:
        action = rest[0].lower() if rest else "status"

        if action == "status":
            self._send_grid("status")
        elif action in ("on", "off"):
            # Visibility is per-view presentation, not model state: nothing
            # to mutate here, the consumer applies it to the view.
            self._send_grid(action)
        elif action == "resize":
            pct = self._parse_percent(rest[1] if len(rest) > 1 else "")
            if pct is None:
                print(f"Usage: map grid resize <percent>  "
                      f"({MIN_SCALE_PCT:g}-{MAX_SCALE_PCT:g}, 100 = default)")
                return
            self.state.set_grid_scale(pct)
            self._send_grid("resize")
        else:
            print("Usage: map grid [on | off | resize <percent>]")

    def _send_grid(self, action: str) -> None:
        """Send a grid message carrying the scale as of this command, so the
        report describes this command even if later ones have already
        changed the state by the time the mainloop handles it."""
        self.services.send(self.name, "grid", (action, self.state.grid_scale_pct))

    def _cmd_coords(self, rest: list[str]) -> None:
        action = rest[0].lower() if rest else "status"
        value = rest[1].lower() if len(rest) > 1 else ""

        if action in ("status", "on", "off"):
            # On/off is per-view presentation, applied by the consumer.
            self._send_coords(action)
        elif action == "style":
            if value not in COORD_STYLES:
                print(f"Usage: map coords style {' | '.join(COORD_STYLES)}")
                return
            # Shared notation: model state, so every view (and later the
            # cell-reference parser) agrees.
            self.state.set_coords_style(value)
            self._send_coords(action)
        elif action == "location":
            if value not in COORD_LOCATIONS:
                print(f"Usage: map coords location {' | '.join(COORD_LOCATIONS)}")
                return
            self._send_coords(action, value)
        else:
            print("Usage: map coords [on | off | style <numbers | letters> | "
                  "location <sides | cells>]")

    def _send_coords(self, action: str, value: str = "") -> None:
        """Like _send_grid: carries the shared state as of this command."""
        self.services.send(self.name, "coords",
                           (action, value, self.state.coords_style,
                            self.state.grid_scale_pct))

    @staticmethod
    def _parse_percent(text: str) -> Optional[float]:
        """'110' or '110%' → 110.0; None if missing, invalid or out of range."""
        try:
            pct = float(text.strip().rstrip("%"))
        except ValueError:
            return None
        return pct if MIN_SCALE_PCT <= pct <= MAX_SCALE_PCT else None

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
        elif message.command == "grid":
            action, scale = message.arg
            # Grid visibility targets the primary view. A mirror would need an
            # explicit target (e.g. `map grid on dm`), as with combat paging.
            view = self.views[0]
            if action == "on":
                self.slot.show()
            self.refresh()
            if action in ("on", "off"):
                view.set_show_grid(action == "on")
            print(self._grid_report(action, view.show_grid, scale))
        elif message.command == "coords":
            action, value, style, scale = message.arg
            # Per-view settings target the primary view, as with the grid.
            view = self.views[0]
            if action == "on":
                self.slot.show()
            self.refresh()      # picks up a style change from the snapshot
            if action in ("on", "off"):
                view.set_show_coords(action == "on")
            elif action == "location":
                view.set_coords_location(value)
            print(self._coords_report(action, view, style, scale))

    # ── Grid calibration (mainloop thread — reads Tk geometry) ────────────

    def _calibration(self) -> tuple[Optional[float], str]:
        """Pixels per inch and a description of where the number came from.

        Order: config override; the monitor the window is on; Tk's screen
        size as a last resort. The window's own size is deliberately not
        used, even when fullscreen: the WM applies fullscreen asynchronously
        (or not at all), and a half-applied window gives a wildly wrong
        density. OS display scaling is handled by the override.
        """
        settings = self.grid_settings
        if settings.pixels_per_inch is not None:
            return settings.pixels_per_inch, "pixels_per_inch override in config"

        window = self.services.windows.get_window(self.services.window_name())
        monitor = window.monitor()
        if monitor is not None:
            width, height, source = monitor.width, monitor.height, "monitor"
        else:
            top = window.toplevel
            width, height, source = (top.winfo_screenwidth(),
                                     top.winfo_screenheight(), "Tk screen")
        if width <= 1 or height <= 1:
            return None, "window not mapped yet"

        ppi = pixels_per_inch(width, height, settings.screen_diagonal_in)
        return ppi, f'{width}x{height} {source} @ {settings.screen_diagonal_in:g}"'

    def _grid_report(self, action: str, grid_on: bool, scale: float) -> str:
        if action == "off":
            return "[+] Grid off."

        state = "on" if grid_on else "off"
        ppi, source = self._calibration()
        if ppi is None:
            return f"[i] Grid {state}; cell size unknown ({source})."

        settings = self.grid_settings
        cell = cell_px(ppi, settings.cell_size_in, scale)
        report = (f'[+] Grid {state}: {settings.cell_size_in:g}" cells at {scale:g}% '
                  f"= {cell:.1f} px ({ppi:.1f} px/in from {source}).")
        if cell < MIN_CELL_PX:
            report += f"\n[!] Cells under {MIN_CELL_PX:g} px are not drawn."
        # Coords follow the cell size, so a resize can hide them silently.
        view = self.views[0]
        if view.show_coords and self._label_font_px(scale, view.coords_location) is None:
            kind = "in-cell" if view.coords_location == "cells" else "side"
            report += (f"\n[!] Coords hidden: cells too small for {kind} "
                       f"labels (font under {MIN_LABEL_FONT_PX} px).")
        return report

    def _label_font_px(self, scale: float, location: str) -> Optional[int]:
        """Label font size at a given scale, for reports.

        Computed from the scale carried in the message, not read from the
        view: the view already shows the *latest* shared state, which may be
        several commands ahead when input arrives quickly (e.g. piped).
        Per-view settings (location, on/off) are safe to read from the view,
        because only messages change them, and those are handled in order.
        """
        ppi, _ = self._calibration()
        if ppi is None:
            return None
        cell = cell_px(ppi, self.grid_settings.cell_size_in, scale)
        return label_font_px(cell, location) if cell >= MIN_CELL_PX else None

    def _coords_report(self, action: str, view: BattleMapView,
                       style: str, scale: float) -> str:
        if action == "off":
            return "[+] Coords off."

        state = "on" if view.show_coords else "off"
        example = "E8" if style == "letters" else "5,8"
        report = (f"[+] Coords {state}: {style} (row first, e.g. {example}), "
                  f"{view.coords_location}")
        size = self._label_font_px(scale, view.coords_location)
        if size is None:
            report += (f".\n[!] Cells too small for labels (font under "
                       f"{MIN_LABEL_FONT_PX} px); none drawn.")
        else:
            report += f", {size} px font."
        return report


    # ── Tab completion ────────────────────────────────────────────────────

    def complete_battlemap(self, text, line, begidx, endidx) -> list[str]:
        parts = get_arg_parts(line[:begidx])
        top_subs = ["show", "bgclear", "fullscreen", "restore", "grid", "coords"]

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
        if sub == "grid" and len(parts) == 2:
            return [s for s in ("on", "off", "resize") if s.startswith(text)]
        if sub == "coords":
            if len(parts) == 2:
                options = ("on", "off", "style", "location")
            elif len(parts) == 3 and parts[2].lower() == "style":
                options = COORD_STYLES
            elif len(parts) == 3 and parts[2].lower() == "location":
                options = COORD_LOCATIONS
            else:
                options = ()
            return [s for s in options if s.startswith(text)]
        return []

FEATURE = BattleMapFeature()
