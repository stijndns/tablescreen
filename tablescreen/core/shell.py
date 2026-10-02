"""
shell.py — The neutral command shell.

``cmd.Cmd`` discovers commands by looking for ``do_*`` methods on the
instance, which normally means the command set is fixed when the class is
written. That is incompatible with choosing features at runtime, so this
shell is a neutral *host*: it starts empty, and each enabled feature
registers its commands into it during startup.

Registration attaches the handler where ``cmd.Cmd`` will find it
(``do_<name>``) and records it in a registry. The registry — not the class
body — is the source of truth for what exists, which is what lets ``help``
and tab-completion show exactly the enabled features' commands and nothing
else.

The shell runs on its own daemon thread. It must never touch a widget
directly; it sends messages through the bus instead, which delivers them on
the mainloop thread.
"""

from __future__ import annotations

import cmd
from dataclasses import dataclass
from typing import Callable, Optional

from .bus import MessageBus, APP_TARGET

# A command handler takes the argument string the user typed after the
# command name. Returning True ends the shell loop (cmd.Cmd's convention).
Handler = Callable[[str], Optional[bool]]
# A completer matches cmd.Cmd's complete_* signature.
Completer = Callable[[str, str, int, int], list[str]]


@dataclass
class RegisteredCommand:
    """One command in the registry."""
    name: str
    handler: Handler
    completer: Optional[Completer] = None
    help_text: str = ""
    feature: str = ""          # which feature registered it, for `commands`


class Shell(cmd.Cmd):
    """A cmd.Cmd host that features plug commands into."""

    prompt = "> "

    def __init__(self, bus: MessageBus, intro_lines: Optional[list[str]] = None):
        super().__init__()
        self._bus = bus
        self._commands: dict[str, RegisteredCommand] = {}
        self._intro_lines = intro_lines or ["Tablescreen. Type help or ? to list commands."]

        # `exit` is application-level: it belongs to no feature, so the shell
        # provides it itself.
        self.register_command(
            "exit", self._do_exit,
            help_text="Close the application.", feature="core")

    # ── Registration ──────────────────────────────────────────────────────

    def register_command(self, name: str, handler: Handler,
                         completer: Optional[Completer] = None,
                         help_text: str = "", feature: str = "") -> None:
        """Add a command to the shell.

        Two things happen. The command is recorded in ``self._commands``,
        which drives help and tab-completion. And the handler is attached as
        ``do_<name>`` (and the completer as ``complete_<name>``) because that
        is where ``cmd.Cmd`` looks when the user types.

        A duplicate name is a programming error — two features claiming the
        same command — so it raises rather than silently shadowing.
        """
        if name in self._commands:
            existing = self._commands[name].feature or "?"
            raise ValueError(
                f"Command '{name}' is already registered by feature "
                f"'{existing}'; '{feature or '?'}' cannot also claim it.")

        entry = RegisteredCommand(name=name, handler=handler,
                                  completer=completer, help_text=help_text,
                                  feature=feature)
        self._commands[name] = entry

        # The glue: put the handler where cmd.Cmd's dispatch will find it.
        setattr(self, f"do_{name}", handler)
        if completer is not None:
            setattr(self, f"complete_{name}", completer)

    def registered_commands(self) -> list[str]:
        return sorted(self._commands)

    def command_owner(self, name: str) -> str:
        entry = self._commands.get(name)
        return entry.feature if entry else ""

    # ── cmd.Cmd overrides ─────────────────────────────────────────────────

    @property
    def intro(self) -> str:      # type: ignore[override]
        """Built at display time so it reflects whatever registered."""
        return "\n".join(self._intro_lines)

    def completenames(self, text, *ignored) -> list[str]:
        """Complete only registered commands.

        cmd.Cmd's default implementation scans for every ``do_*`` attribute,
        which would include inherited ones such as ``do_help``. Driving this
        from the registry keeps the top-level prompt clean and — importantly
        — means a disabled feature's commands do not appear.
        """
        return [name for name in sorted(self._commands) if name.startswith(text)]

    def do_help(self, arg: str):
        """List commands, or show help for one command."""
        if arg:
            entry = self._commands.get(arg.strip())
            if entry is None:
                print(f"[!] No such command '{arg.strip()}'.")
                return
            print(entry.help_text or f"No help available for '{entry.name}'.")
            return

        print("\nAvailable commands:")
        # Group by feature so it is obvious what each enabled feature adds.
        by_feature: dict[str, list[RegisteredCommand]] = {}
        for entry in self._commands.values():
            by_feature.setdefault(entry.feature or "other", []).append(entry)
        for feature in sorted(by_feature):
            names = sorted(e.name for e in by_feature[feature])
            print(f"  [{feature}] {'  '.join(names)}")
        print("\nType 'help <command>' for details.\n")

    def emptyline(self) -> None:
        """Do nothing on an empty line.

        cmd.Cmd's default is to repeat the previous command, which is a
        surprising way to accidentally advance a turn or re-fire an action.
        """
        return None

    def default(self, line: str) -> None:
        print(f"[!] Unknown command: {line.split()[0] if line.split() else line}. "
              f"Type 'help' to list commands.")

    # ── Application lifecycle ─────────────────────────────────────────────

    def _do_exit(self, arg: str) -> bool:
        """Ask the app to shut down, then end the shell loop."""
        self._bus.send(APP_TARGET, "exit", source="shell")
        print("Exiting shell.")
        return True     # cmd.Cmd stops the loop on a truthy return