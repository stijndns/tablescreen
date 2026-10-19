"""
executor.py — The one thread that runs commands.

Every input source hands its commands to this executor instead of running
them itself: the keyboard (see console.py) and, through
``ShellServices.execute``, features such as the battlemap mirror. Commands
therefore run one at a time, in arrival order, on a single thread. That
thread is the only one that changes feature state, which is why the models
need no locks.

    keyboard (console thread) ──┐
                                ├─► queue ──► executor thread ──send()──► bus
    mirror clicks (main loop) ──┘   (line, source)

While a command runs, the executor remembers which source it came from, so
messages it sends carry that source and ``ask()`` knows whether someone is at
the keyboard to answer.
"""

from __future__ import annotations

import queue
import threading
import traceback
from dataclasses import dataclass
from typing import Callable, Optional

from .shell import Shell

SHELL_SOURCE = "shell"      # typed at the keyboard
APP_SOURCE = "app"          # anything not running on the executor thread


class NoAnswer(Exception):
    """A command asked a question nobody can answer (end of input, or the
    command came from a source without a keyboard). Ends the command."""


@dataclass
class _Work:
    line: str
    source: str
    on_done: Optional[Callable[[], None]] = None


class CommandExecutor:
    """Runs command lines from every source on one daemon thread."""

    def __init__(self, shell: Shell, on_stop: Callable[[], None]):
        self._shell = shell
        self._on_stop = on_stop         # called once when the loop ends
        self._queue: queue.Queue[_Work] = queue.Queue()
        self._local = threading.local()
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="executor")

    def start(self) -> None:
        self._thread.start()

    def submit(self, line: str, source: str,
               on_done: Optional[Callable[[], None]] = None) -> None:
        """Queue a command line. Safe from any thread; returns at once.
        ``on_done`` is called on the executor thread once it has run."""
        self._queue.put(_Work(line, source, on_done))

    @property
    def stopped(self) -> bool:
        """True once a command ended the loop (``exit``)."""
        return self._stopped.is_set()

    def current_source(self) -> str:
        """Source of the command running on the calling thread; APP_SOURCE
        when called from any other thread (e.g. the main loop)."""
        return getattr(self._local, "source", None) or APP_SOURCE

    # ── Executor thread ───────────────────────────────────────────────────

    def _run(self) -> None:
        try:
            while True:
                work = self._queue.get()
                stop = self._execute(work)
                if stop:
                    self._stopped.set()     # before on_done: no new prompt
                if work.on_done is not None:
                    work.on_done()
                if stop:
                    return
        finally:
            self._stopped.set()
            self._on_stop()

    def _execute(self, work: _Work) -> bool:
        """Run one line; True if it ends the loop. A failing command is
        reported and the loop carries on (it used to end the shell)."""
        self._local.source = work.source
        try:
            if work.source != SHELL_SOURCE:
                print(f"[{work.source}] {work.line}")   # what the click did
            return bool(self._shell.execute(work.line))
        except NoAnswer as exc:
            print(f"[i] Command cancelled: {exc}")
        except Exception:
            print(f"[!] Command failed: {work.line}")
            traceback.print_exc()
        finally:
            self._local.source = None
        return False
