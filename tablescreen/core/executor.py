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

Besides command lines, the executor runs plain functions (``call``): the API
uses them to read a consistent snapshot of the state, or to check an action
and run its command in one go. ``call(..., capture=True)`` also collects
what the function printed on this thread, so the API can reply with it.
"""

from __future__ import annotations

import queue
import sys
import threading
import traceback
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, Callable, Optional

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
    fn: Optional[Callable[[], Any]] = None      # a call() instead of a line
    future: Optional[Future] = None
    capture: bool = False


@dataclass
class CallResult:
    value: Any
    output: str = ""        # what the call printed, with capture=True


class _OutputTee:
    """Stands in for sys.stdout: everything still goes to the console, and
    a thread that is capturing also keeps a copy. The rest (fileno, isatty)
    is the real stream's, so input() still uses readline."""

    def __init__(self, stream):
        self._stream = stream
        self._local = threading.local()

    def write(self, text: str) -> int:
        buffer = getattr(self._local, "buffer", None)
        if buffer is not None:
            buffer.append(text)
        return self._stream.write(text)

    def __getattr__(self, name):
        return getattr(self._stream, name)

    def start(self) -> None:
        self._local.buffer = []

    def stop(self) -> str:
        text = "".join(self._local.buffer or [])
        self._local.buffer = None
        return text


def _stdout_tee() -> _OutputTee:
    if not isinstance(sys.stdout, _OutputTee):
        sys.stdout = _OutputTee(sys.stdout)
    return sys.stdout


class CommandExecutor:
    """Runs command lines from every source on one daemon thread."""

    def __init__(self, shell: Shell, on_stop: Callable[[], None]):
        self._shell = shell
        self._on_stop = on_stop         # called once when the loop ends
        self._queue: queue.Queue[_Work] = queue.Queue()
        self._after_each: list[Callable[[], None]] = []
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

    def call(self, fn: Callable[[], Any], source: str = APP_SOURCE,
             capture: bool = False) -> Future:
        """Queue a function to run on the executor thread, like a command
        from ``source``. Returns a Future with a CallResult, or the function's
        exception. Inside, ``run_line()`` runs command lines."""
        future: Future = Future()
        self._queue.put(_Work("", source, fn=fn, future=future, capture=capture))
        return future

    def run_line(self, line: str) -> None:
        """Run a command line now, inside a call() on the executor thread:
        echoed, errors reported, as if it had been submitted. Never use it for
        `exit` (a call can't end the loop)."""
        self._run_line(line, self.current_source())

    def after_each(self, callback: Callable[[], None]) -> None:
        """Run ``callback`` on the executor thread after every command and
        call, e.g. to publish what changed. Register before start()."""
        self._after_each.append(callback)

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
                stop = self._call(work) if work.fn else self._execute(work)
                self._run_after_each()
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
        """Run one line; True if it ends the loop."""
        self._local.source = work.source
        try:
            return self._run_line(work.line, work.source)
        finally:
            self._local.source = None

    def _run_line(self, line: str, source: str) -> bool:
        """A failing command is reported and the loop carries on (it used
        to end the shell)."""
        try:
            if source != SHELL_SOURCE:
                print(f"[{source}] {line}")     # what the click did
            return bool(self._shell.execute(line))
        except NoAnswer as exc:
            print(f"[i] Command cancelled: {exc}")
        except Exception:
            print(f"[!] Command failed: {line}")
            traceback.print_exc()
        return False

    def _call(self, work: _Work) -> bool:
        tee = _stdout_tee() if work.capture else None
        self._local.source = work.source
        if tee:
            tee.start()
        try:
            value = work.fn()
        except BaseException as exc:
            output = tee.stop() if tee else ""
            exc.output = output     # what it printed before failing
            work.future.set_exception(exc)
        else:
            output = tee.stop() if tee else ""
            work.future.set_result(CallResult(value, output))
        finally:
            self._local.source = None
        return False

    def _run_after_each(self) -> None:
        for callback in self._after_each:
            try:
                callback()
            except Exception:
                print("[!] After-command hook failed:")
                traceback.print_exc()
