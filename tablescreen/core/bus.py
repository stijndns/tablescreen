"""
bus.py — The single message bus.

Input sources produce messages from any thread. Consumers (feature windows)
handle them on the Tk mainloop thread only, because Tkinter is not thread-safe.

The bus is the boundary between those two worlds:

    shell thread  ──send()──►  Queue  ──drain()──►  consumer  (mainloop thread)

Two independent wake paths get messages out of the queue:

  1. A ``<<QueueMsg>>`` virtual event fired by send(). Fast (sub-millisecond),
     the normal case.
  2. A periodic poll. A safety net: Tk can silently drop generated events
     (notably before a widget is mapped, or when a window is withdrawn), and
     a dropped event with no poll means a message waits forever.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from dataclasses import dataclass, field
from typing import Any, Callable

POLL_INTERVAL_MS = 100

QUEUE_EVENT = "<<QueueMsg>>"

# Reserved target for application-level messages (exit, etc.).
APP_TARGET = "app"

@dataclass(frozen=True)
class Message:
    """One routed instruction travelling from a producer to a consumer.

    Attributes
    ----------
    target:
        Name of the consumer this is for — a feature name such as
        ``"audiowindow"`` or ``"combat"``, or ``APP_TARGET`` for
        application-level messages.
    command:
        What to do, e.g. ``"show"``, ``"combat_update"``, ``"exit"``.
    arg:
        Payload for the command. May be anything: a filename, a snapshot
        dict, a page number, or None.
    source:
        Which input produced this — ``"shell"`` today; later ``"rest"`` or
        ``"mobile"``. Not used for routing; carried for logging and for
        future per-source policy.
    """

    target: str
    command: str
    arg: Any = None
    source: str = "shell"


Consumer = Callable[[Message], None]


class MessageBus:
    """Owns the single queue and routes messages to registered consumers.

    Threading contract — this is the important part:

    * ``send()`` may be called from ANY thread. It only touches the
      thread-safe queue and asks Tk to fire an event.
    * ``drain()`` and every consumer handler run on the MAINLOOP thread
      only. That is what makes it safe for handlers to touch widgets.
    """

    def __init__(self, root: tk.Tk, poll_interval_ms: int = POLL_INTERVAL_MS):
        self._root = root
        self._queue: queue.Queue[Message] = queue.Queue()
        self._consumers: dict[str, Consumer] = {}
        self._poll_interval_ms = poll_interval_ms

        # Guards against re-entrant draining (see drain()).
        self._draining = False

        # Remembers which unknown targets we have already warned about, so a
        # disabled feature does not spam the console on every message.
        self._warned_targets: set[str] = set()

        # Wake path 1: the virtual event, bound on the ROOT.
        self._root.bind(QUEUE_EVENT, self._on_queue_event)

        # Wake path 2: the safety-net poll.
        self._root.after(self._poll_interval_ms, self._poll)

    # ── Registration ──────────────────────────────────────────────────────

    def register_consumer(self, target: str, handler: Consumer) -> None:
        """Register the handler that receives messages for the target.
        Called once per feature at startup, from the mainloop thread.
        """
        if target in self._consumers:
            raise ValueError(
                f"Target '{target}' already has a consumer registered."
            )
        self._consumers[target] = handler

    def unregister_consumer(self, target: str) -> None:
        self._consumers.pop(target, None)

    def known_targets(self) -> list[str]:
        return sorted(self._consumers)

    # ── Producing (any thread) ────────────────────────────────────────────

    def send(self, target: str, command: str, arg: Any = None,
             source: str = "shell") -> None:
        """Enqueue a message and wake the mainloop. Safe from any thread."""
        self._queue.put(Message(target=target, command=command,
                                arg=arg, source=source))
        self._wake()

    def post(self, message: Message) -> None:
        """Enqueue an already-built Message. Safe from any thread."""
        self._queue.put(message)
        self._wake()

    def _wake(self) -> None:
        """Ask the mainloop to drain the queue.

        ``when="tail"`` is required: a bare event_generate() is unreliable
        and can be dropped without ever reaching the binding. Even with
        "tail" the event can be lost in some widget states, which is exactly
        why the poll exists as a backstop — so a failure here is not fatal
        and must not raise.
        """
        try:
            self._root.event_generate(QUEUE_EVENT, when="tail")
        except tk.TclError:
            # The root is gone (app shutting down) or Tk refused the event.
            # The poll will pick the message up if the app is still alive.
            pass

    # ── Consuming (mainloop thread only) ──────────────────────────────────

    def _on_queue_event(self, _event) -> None:
        self.drain()

    def _poll(self) -> None:
        """Safety net: drain anything the event path missed, then reschedule."""
        try:
            if not self._queue.empty():
                self.drain()
        finally:
            # Always reschedule, even if a handler raised, or the poll dies
            # and the safety net is gone for the rest of the session.
            try:
                self._root.after(self._poll_interval_ms, self._poll)
            except tk.TclError:
                pass   # root destroyed; stop polling

    def drain(self) -> None:
        """Handle EVERY pending message. Mainloop thread only."""
        if self._draining:
            # A handler caused a nested drain (e.g. by calling update()).
            # The outer loop will pick up whatever is still queued.
            return
        self._draining = True
        try:
            while True:
                try:
                    message = self._queue.get_nowait()
                except queue.Empty:
                    break
                self._route(message)
        finally:
            self._draining = False

    def _route(self, message: Message) -> None:
        """Deliver one message to its consumer.

        An unknown target is NOT an error: it usually means the feature is
        simply disabled in config, and a disabled feature must fail quietly
        rather than crash the app. We warn once per target so a genuine typo
        is still discoverable.
        """
        handler = self._consumers.get(message.target)
        if handler is None:
            if message.target not in self._warned_targets:
                self._warned_targets.add(message.target)
                print(f"[i] No consumer for target '{message.target}' "
                      f"(feature not enabled?); ignoring its messages.")
            return
        try:
            handler(message)
        except Exception as exc:
            # One misbehaving consumer must not kill the drain loop or the
            # mainloop — the rest of the app keeps running.
            print(f"[!] Error handling {message.target}/{message.command}: {exc}")