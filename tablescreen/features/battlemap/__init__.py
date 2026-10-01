"""
battlemap — Backdrop (image or looping video) with a calibrated grid and
coordinate labels, for a TV lying flat under the minis.
"""

from __future__ import annotations

import math
import platform
from typing import Optional

from PIL import Image

from ...core.completion import get_arg_parts, tab_completion
from ...core.feature import FeatureBase, ShellServices
from ...core.paths import IMAGES_DIR
from .model.battlemap_state import BattleMapState, Sprite
from .model.grid import (COORD_LOCATIONS, COORD_STYLES, GridSettings,
                         MAX_SCALE_PCT, MIN_CELL_PX, MIN_LABEL_FONT_PX,
                         MIN_SCALE_PCT, CellArea, area_label, area_to_pixels,
                         cell_px, label_font_px, parse_area, parse_cell,
                         pixels_per_inch)
from .sprite_images import FRAME_MEMORY_WARN_BYTES
from .video import VIDEO_EXTENSIONS
from .views.battlemap_view import BattleMapView

CURRENT_OS = platform.system()

BATTLEMAP_HELP = """\
Battlemap commands:
    map show <file>                 — show an image or a looping .webm video
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
    map sprite add <file> <cell|range> [as <name>]
                                    — image fitted in a cell (E8) or a range of
                                      cells (E8:F9); default names sprite1, ...
    map sprite move <name> <cell>   — move the top-left there, keeping the size
    map sprite resize <name> <range>
                                    — set the footprint, e.g. B3:C4 or B3 (1x1)
    map sprite rotate <name> <degrees>
                                    — rotate clockwise by degrees (negative =
                                      counter-clockwise), adds to the current
    map sprite remove <name>        — remove a sprite
    map sprite list                 — all sprites with their cells
    map sprite clear                — remove all sprites
"""


def format_sprites(sprites: tuple[Sprite, ...], style: str) -> str:
    """The `map sprite list` table, cells in the active coords style."""
    if not sprites:
        return "No sprites."
    rotated = any(s.rotation for s in sprites)
    rows = [(s.name, area_label(s.area, style),
             f"{s.rotation:g}°" if rotated else "", s.file) for s in sprites]
    widths = [max(len(r[i]) for r in rows) for i in range(3)]
    lines = [f"Sprites ({len(rows)}):"]
    for row in rows:
        cols = [f"{v:<{w}}" for v, w in zip(row, widths) if w] + [row[3]]
        lines.append("  " + "  ".join(cols))
    return "\n".join(lines)

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

    def shutdown(self) -> None:
        for view in self.views:
            view.close()        # stops the video decoder thread

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
        elif sub == "sprite":
            self._cmd_sprite(rest)
        else:
            print(f"[!] Unknown map sub-command '{sub}'. Type 'map help'.")

    def _cmd_grid(self, rest: list[str]) -> None:
        action = rest[0].lower() if rest else "status"

        if action == "status":
            self._send_grid("status")
        elif action in ("on", "off"):
            self._send_grid(action)     # per-view: applied by the consumer
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
        """Carries the scale as of this command, so the report stays accurate
        when later commands change state before the mainloop gets here."""
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

    def _cmd_sprite(self, rest: list[str]) -> None:
        action = rest[0].lower() if rest else ""
        args = rest[1:]

        if action == "add":
            self._sprite_add(args)
        elif action == "remove":
            if len(args) != 1:
                print("Usage: map sprite remove <name>")
                return
            sprite = self.state.remove_sprite(args[0])
            if sprite is None:
                print(f"[!] No sprite named '{args[0]}'. See 'map sprite list'.")
                return
            self.services.send(self.name, "render")
            where = area_label(sprite.area, self.state.coords_style)
            print(f"[+] Removed sprite '{sprite.name}' from {where}.")
        elif action == "rotate":
            self._sprite_rotate(args)
        elif action == "move":
            self._sprite_move(args)
        elif action == "resize":
            self._sprite_resize(args)
        elif action == "list":
            print(format_sprites(self.state.sprites, self.state.coords_style))
        elif action == "clear":
            count = self.state.clear_sprites()
            self.services.send(self.name, "render")
            print(f"[+] Removed {count} sprite{'s' if count != 1 else ''}.")
        else:
            print("Usage: map sprite add <file> <cell|range> [as <name>] | "
                  "move <name> <cell> | resize <name> <range> | "
                  "rotate <name> <degrees> | remove <name> | list | clear")

    def _sprite_rotate(self, args: list[str]) -> None:
        try:
            degrees = float(args[1]) if len(args) == 2 else math.nan
        except ValueError:
            degrees = math.nan
        if not math.isfinite(degrees):
            print("Usage: map sprite rotate <name> <degrees>  (clockwise; e.g. 90, -45)")
            return
        sprite = self.state.rotate_sprite(args[0], degrees)
        if sprite is None:
            print(f"[!] No sprite named '{args[0]}'. See 'map sprite list'.")
            return
        self.services.send(self.name, "render")
        direction = "clockwise" if degrees >= 0 else "counter-clockwise"
        print(f"[+] Rotated '{sprite.name}' {abs(degrees):g}° {direction} "
              f"(now {sprite.rotation:g}°).")

    def _sprite_add(self, args: list[str]) -> None:
        if len(args) == 4 and args[2].lower() == "as":
            file, where, name = args[0], args[1], args[3]
        elif len(args) == 2:
            (file, where), name = args, None
        else:
            print("Usage: map sprite add <file> <cell|range> [as <name>]")
            return

        area = parse_area(where)
        if area is None:
            print(f"[!] Invalid cell or range '{where}'. Use e.g. E8, 5,8 or E8:F9.")
            return
        if not (IMAGES_DIR / file).is_file():
            print(f"[!] File not found: {IMAGES_DIR / file}")
            return
        try:
            sprite = self.state.add_sprite(file, area, name)
        except ValueError as exc:
            print(f"[!] {exc}")
            return
        self._send_sprite_change("added", sprite, None)

    def _sprite_move(self, args: list[str]) -> None:
        if len(args) != 2:
            print("Usage: map sprite move <name> <cell>")
            return
        if ":" in args[1]:
            print("[!] move takes a single cell (the new top-left); "
                  "use 'map sprite resize' to change the footprint.")
            return
        cell = parse_cell(args[1])
        if cell is None:
            print(f"[!] Invalid cell '{args[1]}'. Use e.g. 5,8 or E8.")
            return
        self._change_sprite("moved", args[0], lambda: self.state.move_sprite(args[0], *cell))

    def _sprite_resize(self, args: list[str]) -> None:
        area = parse_area(args[1]) if len(args) == 2 else None
        if area is None:
            print("Usage: map sprite resize <name> <range>  (e.g. B3:C4, or B3 for 1x1)")
            return
        self._change_sprite("resized", args[0], lambda: self.state.resize_sprite(args[0], area))

    def _change_sprite(self, verb: str, name: str, change) -> None:
        old = self.state.find_sprite(name)
        if old is None:
            print(f"[!] No sprite named '{name}'. See 'map sprite list'.")
            return
        self._send_sprite_change(verb, change(), old.area)

    def _send_sprite_change(self, verb: str, sprite: Sprite,
                            old_area: Optional[CellArea]) -> None:
        """Reported by the consumer, which can check visibility and memory."""
        self.services.send(self.name, "sprite_changed",
                           (verb, sprite, old_area, self.state.coords_style,
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
        elif message.command == "sprite_changed":
            verb, sprite, old_area, style, scale = message.arg
            self.slot.show()
            self.refresh()
            print(self._sprite_report(verb, sprite, old_area, style, scale))

    # ── Grid calibration (mainloop thread — reads Tk geometry) ────────────

    def _calibration(self) -> tuple[Optional[float], str]:
        """Pixels per inch and where it came from: config override, else the
        window's monitor, else Tk's screen. Never the window's own size —
        fullscreen applies asynchronously and a half-applied size is wrong."""
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
        """Label font size for reports, from the scale carried in the message.
        The view may already show later state; per-view settings are safe to
        read from it because only in-order messages change them."""
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


    def _sprite_report(self, verb: str, sprite: Sprite, old_area: Optional[CellArea],
                       style: str, scale: float) -> str:
        where = area_label(sprite.area, style)
        if old_area is None:
            report = f"[+] Added sprite '{sprite.name}': {sprite.file} at {where}."
        else:
            report = (f"[+] {verb.capitalize()} '{sprite.name}': "
                      f"{area_label(old_area, style)} → {where}.")

        view = self.views[0]
        ppi, _ = self._calibration()
        width, height = view.canvas.winfo_width(), view.canvas.winfo_height()
        if ppi is not None and width > 1 and height > 1:    # 1x1 before mapping
            x, y, _, _ = area_to_pixels(sprite.area,
                                        cell_px(ppi, self.grid_settings.cell_size_in, scale))
            if x >= width or y >= height:
                report += f"\n[!] {where} is outside the visible grid; not drawn."
        memory = view.frame_memory(sprite.name)
        if memory > FRAME_MEMORY_WARN_BYTES:
            report += (f"\n[!] '{sprite.name}' uses {memory / 2**20:.0f} MB of image "
                       f"memory at this size (all animation frames).")
        return report

    # ── Tab completion ────────────────────────────────────────────────────

    def complete_battlemap(self, text, line, begidx, endidx) -> list[str]:
        parts = get_arg_parts(line[:begidx])
        top_subs = ["show", "bgclear", "fullscreen", "restore", "grid", "coords",
                    "sprite"]

        if len(parts) == 1:
            return [s for s in top_subs if s.startswith(text)]

        sub = parts[1].lower()

        if sub == "show":
            if len(parts) == 2:
                clean = line.split()[-1] if not line.endswith(" ") else ""
                #print(f"clean: {clean}")
                return tab_completion(clean, [*Image.registered_extensions(), *VIDEO_EXTENSIONS],
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
        if sub == "sprite":
            if len(parts) == 2:
                return [s for s in ("add", "move", "resize", "rotate", "remove",
                                    "list", "clear")
                        if s.startswith(text)]
            action = parts[2].lower()
            if action == "add" and len(parts) == 3:
                clean = line.split()[-1] if not line.endswith(" ") else ""
                return tab_completion(clean, list(Image.registered_extensions()),
                                      CURRENT_OS, "image")
            if action == "add" and len(parts) == 5:
                return ["as"] if "as".startswith(text.lower()) else []
            if action in ("remove", "rotate", "move", "resize") and len(parts) == 3:
                return [s.name for s in self.state.sprites
                        if s.name.lower().startswith(text.lower())]
        return []

FEATURE = BattleMapFeature()
