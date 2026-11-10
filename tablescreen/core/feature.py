"""
feature.py — The contract between core and a feature.

A feature is a self-contained package (audiowindow, combat, battlemap) that
plugs into core at startup. Core discovers features by name from config,
builds each one, and hands it a ``ShellServices`` — a deliberately narrow
set of capabilities.

Two rules define the architecture, and this module is where they are
enforced:

  1. Features depend on core; core never imports a feature. Core knows only
     the *names* it read from config and the protocol below.
  2. Features never reach each other. A feature receives ``ShellServices``,
     not the shell, not the root, and not a registry of other features.
     Anything a feature needs that is not on ShellServices is a design
     conversation, not a silent reach-through.

The narrowness is the point. If a feature could obtain the Tk root it could
destroy it; if it could obtain the shell it could enumerate and call another
feature's commands. ShellServices exposes capabilities instead of objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from .bus import Message
from .window import ContentSlot, WindowService

# Signatures mirroring cmd.Cmd's, re-stated here so features do not need to
# import the shell module.
Handler = Callable[[str], Optional[bool]]
Completer = Callable[[str, str, int, int], list[str]]
Consumer = Callable[[Message], None]


@dataclass
class ShellServices:
    """Everything a feature is allowed to touch.

    Deliberately small. Note what is absent: the Tk root, the Shell object,
    the MessageBus itself, and any way to look up another feature.
    """

    # ── Identity ──────────────────────────────────────────────────────────
    feature_name: str
    """The name this feature was loaded under. Used as its bus target and as
    the owner label on its registered commands."""

    # ── Capabilities ──────────────────────────────────────────────────────
    register_command: Callable[..., None]
    """Add a command to the shell. Signature:
    ``register_command(name, handler, completer=None, help_text="", feature="")``
    Core pre-binds `feature` to this feature's name."""

    windows: WindowService
    """Obtain named windows and content slots to draw into."""

    send: Callable[..., None]
    """Put a message on the bus: ``send(target, command, arg=None)``.
    Normally a feature sends to itself (its own window consumer); sending to
    another feature's target is possible but is the coupling smell the
    architecture exists to discourage."""

    subscribe: Callable[[Consumer], None]
    """Register this feature's bus consumer. Core binds it to the feature's
    own target, so a feature cannot accidentally consume another's
    messages."""

    execute: Callable[[str, str], None]
    """Run one of this feature's own commands as if typed:
    ``execute("map fow reveal B3:D7", source="mirror")``. Queued on the
    executor (one command at a time, after anything already queued) and
    echoed as ``[mirror] map fow reveal B3:D7``; returns at once, safe from
    any thread. For input that doesn't come from the keyboard, such as mouse
    clicks. Raises ValueError for another feature's command or the source
    "shell"."""

    publish_state: Callable[..., None]
    """Offer a public state to the player webapp:
    ``publish_state(snapshot, "combat/1", view=path)``. ``snapshot()``
    returns plain JSON data and runs on the executor thread after every
    command; it must never show more than the table screens do (it's sent to
    every player). Sent only when the feature is in ``[api] share`` and the
    state changed. ``view`` (optional) is the feature's default web view, a
    JS module with ``mount(element, ctx)`` → ``{update(state), unmount()}``;
    without one the webapp shows a generic view. A no-op in effect when the
    API is off."""

    register_action: Callable[..., None]
    """Offer an action to the webapp:
    ``register_action(name, fields, permission, handler)`` with ``fields`` a
    dict of ``core.api.Field``, ``permission`` "player" or "dm" (the dm may
    do everything), and ``handler(args, caller) -> command line``. Core checks
    the fields first; the handler runs on the executor, checks what only the
    feature knows (own character, whose turn) and raises ValueError with a
    message for the player to refuse. The line runs as ``api:<user>``.
    Optional ``reply=fn(output) -> text``: what a player is told instead of
    the command's console output (which may say more than the table shows,
    e.g. a monster's hit points); the dm gets the full output."""

    config: dict[str, Any]
    """This feature's own config section (e.g. which window it draws on).
    Only its own — not the whole config file."""

    # ── Convenience ───────────────────────────────────────────────────────
    def slot(self, window_name: str, slot_id: Optional[str] = None) -> ContentSlot:
        """Get a content slot on a named window.

        Defaults the slot id to the feature name, which is what a feature
        with a single view wants.
        """
        return self.windows.content_slot(window_name, slot_id or self.feature_name)

    def window_name(self, default: str = "player") -> str:
        """The window this feature should draw on, from its config section."""
        return self.config.get("window", default)


@runtime_checkable
class Feature(Protocol):
    """What a feature package must provide.

    A feature package's ``__init__.py`` exposes a ``build(services)``
    function (or a class with that method). Core calls it once at startup,
    after which the feature owns its own model, views and commands.

    ``Protocol`` rather than a base class: a feature does not have to import
    and subclass anything from core, it just has to have the right shape.
    That keeps the dependency as light as possible.
    """

    name: str

    def build(self, services: ShellServices) -> None:
        """Create state, register commands, subscribe to the bus.

        Called once, on the mainloop thread, during startup. After this
        returns the feature is live.
        """
        ...

    def shutdown(self) -> None:
        """Release anything that needs explicit cleanup (optional).

        Called on app exit. Windows and slots are torn down by core, so most
        features need nothing here; it exists for things like stopping audio
        playback or flushing a log.
        """
        ...


class FeatureBase:
    """Optional convenience base for features.

    Implementing the Protocol directly is fine; this just saves the small
    amount of boilerplate every feature would otherwise repeat, and gives a
    no-op ``shutdown`` so features that need no cleanup can omit it.

    The ``views`` list is deliberately plural from the start: a feature may
    render the same snapshot into several windows (a DM mirror of the combat
    screen, say). Keeping it a list now means that becomes an additive
    change rather than a refactor.
    """

    name: str = "unnamed"

    def __init__(self) -> None:
        self.services: Optional[ShellServices] = None
        self.views: list[Any] = []

    def build(self, services: ShellServices) -> None:      # pragma: no cover
        raise NotImplementedError

    def shutdown(self) -> None:
        return None

    # ── Helpers most features want ────────────────────────────────────────

    def refresh(self, *args, **kwargs) -> None:
        """Re-render every view from the current state.

        Features override ``snapshot()`` and let this fan it out, so adding a
        mirror view needs no change here.
        """
        snapshot = self.snapshot()
        if snapshot is None:
            return
        for view in self.views:
            try:
                view.render(snapshot, *args, **kwargs)
            except Exception as exc:
                print(f"[!] {self.name}: view render failed: {exc}")

    def snapshot(self) -> Any:
        """State to hand the views. Override in features that render."""
        return None