"""
app.py — Application startup and lifecycle.

This is the only place that knows how the pieces fit together:

    config.toml  ──►  which features to load
    root (hidden) ─►  owns the mainloop; every feature window is a Toplevel
    MessageBus    ─►  the one queue; input threads produce, mainloop consumes
    WindowService ─►  vends named windows and content slots
    Shell         ─►  neutral cmd.Cmd host, daemon thread, feeds the bus

Features are loaded by name with importlib, so core never imports a
feature module directly.

Threading: the Tk mainloop runs on the main thread and is the application's
lifecycle — when it ends, the app ends. The shell runs as a daemon thread
beside it. Potential future input sources join as further daemons feeding the same bus;
nothing about a feature changes.
"""

from __future__ import annotations

import importlib
import re
import threading
import tkinter as tk
import tomllib
from pathlib import Path
from typing import Any, Optional

from .bus import MessageBus, Message, APP_TARGET
from .feature import Feature, ShellServices
from .shell import Shell
from .window import WindowService

# Repo root: .../tablescreen/tablescreen/core/app.py
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.toml"

FEATURE_PACKAGE = "tablescreen.features"

# ── Config ──────────────────────────────────────────────────────────────

def load_config(path: Optional[Path] = None) -> dict[str, Any]:
    path = path or DEFAULT_CONFIG_PATH
    if not path.exists():
        print(f"[i] No config at {path}; using defaults.")
        return {"features": {"enabled": ["audiowindow"]}}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception as exc:
        print(f"[!] Could not read {path}: {exc}; using defaults.")
        return {"features": {"enabled": ["audiowindow"]}}

def enabled_features(config: dict[str, Any]) -> list[str]:
    return list(config.get("features", {}).get("enabled", []))

def feature_config(config: dict[str, Any], name: str) -> dict[str, Any]:
    section = config.get("features", {}).get(name, {})
    return dict(section) if isinstance(section, dict) else {}

_GEOMETRY_RE = re.compile(r"\d+x\d+(?:[+-]\d+[+-]\d+)?")

def window_geometries(config: dict[str, Any]) -> dict[str, str]:
    """The [windows] table as {window name: Tk geometry string}.

    Invalid entries are skipped with a warning, so a typo falls back to the
    default size rather than stopping a feature from building its window.
    """
    section = config.get("windows", {})
    if not isinstance(section, dict):
        return {}
    geometries: dict[str, str] = {}
    for name, value in section.items():
        if isinstance(value, str) and _GEOMETRY_RE.fullmatch(value.strip()):
            geometries[name] = value.strip()
        else:
            print(f"[!] Invalid size for window '{name}': {value!r} "
                  f"(expected e.g. \"1200x900\"); using the default.")
    return geometries


# ── Feature loading ─────────────────────────────────────────────────────

def load_feature(name: str) -> Optional[Feature]:
    """Import a feature package by name and obtain its Feature object."""
    module_name = f"{FEATURE_PACKAGE}.{name}"
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        print(f"[!] Feature '{name}' could not be imported ({exc}). Skipping.")
        return None

    feature = getattr(module, "FEATURE", None)
    if feature is None:
        print(f"[!] Feature '{name}' exposes no FEATURE object. Skipping.")
        return None

    if not hasattr(feature, "build"):
        print(f"[!] Feature '{name}' has no build() method. Skipping.")
        return None
    return feature

# ── The application ─────────────────────────────────────────────────────

class Application:
    """Owns the root, the bus, the windows, the shell and the features."""

    def __init__(self, config: Optional[dict[str, Any]] = None):
        self.config = config if config is not None else load_config()

        self.root = tk.Tk()
        self.root.withdraw()

        self.bus = MessageBus(self.root)
        self.windows = WindowService(self.root, window_geometries(self.config))
        self.shell = Shell(self.bus, intro_lines=self._intro_lines())
        self.features: dict[str, Feature] = {}
        self._shutting_down = False

        # Application-level messages
        self.bus.register_consumer(APP_TARGET, self._handle_app_message)

    # ── Startup ───────────────────────────────────────────────────────────

    def _intro_lines(self) -> list[str]:
        names = ", ".join(enabled_features(self.config)) or "none"
        return [
            "Tablescreen. Type help or ? to list commands.",
            f"Features enabled: {names}",
        ]

    def build_features(self) -> None:
        for name in enabled_features(self.config):
            feature = load_feature(name)
            if feature is None:
                continue
            services = self._services_for(name)
            try:
                feature.build(services)
            except Exception as exc:
                # One broken feature must not stop the others from loading.
                print(f"[!] Feature '{name}' failed to build: {exc}")
                continue
            self.features[name] = feature
            print(f"[+] Loaded feature '{name}'.")

    def _services_for(self, name: str) -> ShellServices:
        """Build the narrow capability set for one feature.

        The closures below are the enforcement mechanism: they capture
        `name`, so a feature cannot misattribute its commands or subscribe to
        another feature's bus target.
        """
        def register_command(cmd_name, handler, completer=None, help_text=""):
            self.shell.register_command(cmd_name, handler, completer,
                                        help_text, feature=name)

        def subscribe(consumer):
            self.bus.register_consumer(name, consumer)

        return ShellServices(
            feature_name=name,
            register_command=register_command,
            windows=self.windows,
            send=self.bus.send,
            subscribe=subscribe,
            config=feature_config(self.config, name),
        )

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def _handle_app_message(self, message: Message) -> None:
        """Consumer for APP_TARGET. Runs on the mainloop thread."""
        if message.command == "exit":
            self.shutdown()
        else:
            print(f"[i] Unknown app command '{message.command}'.")

    def shutdown(self) -> None:
        """Tear down features and end the mainloop. Mainloop thread only."""
        if self._shutting_down:
            return
        self._shutting_down = True

        for name, feature in self.features.items():
            shutdown = getattr(feature, "shutdown", None)
            if callable(shutdown):
                try:
                    shutdown()
                except Exception as exc:
                    print(f"[!] Feature '{name}' shutdown error: {exc}")

        self.windows.destroy_all()
        try:
            self.root.quit()        # ends mainloop; run() then destroys root
        except tk.TclError:
            pass

    def _shell_thread(self) -> None:
        try:
            self.shell.cmdloop()
        except Exception as exc:
            print(f"[!] Shell terminated: {exc}")

    def run(self) -> None:
        """Build everything, start the shell daemon, run the mainloop."""
        self.build_features()

        if not self.features:
            print("[!] No features loaded — check 'enabled' in config.toml.")

        threading.Thread(target=self._shell_thread, daemon=True).start()

        try:
            self.root.mainloop()
        finally:
            try:
                self.root.destroy()
            except tk.TclError:
                pass


def main() -> None:
    Application().run()