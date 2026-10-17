"""
console.py — The keyboard: reads command lines and answers to questions.

One daemon thread owns ``input()``. It reads a command line, hands it to the
executor and waits until the command has finished before showing the next
prompt, so typing feels exactly as it did when the shell ran commands
itself.

While a typed command runs, it may ask a question (``ask()``, e.g. the
combat tracker's initiative ties). The console thread is waiting for that
command anyway, so it reads the answer and passes it back:

    console thread                    executor thread
    input("> ")  → "combat start"
    submit, then wait  ─────────────► runs combat start
                       ◄── ask ────── ask("    > ")    (blocks)
    input("    > ") → "2 1"  ── answer ──►  continues
                       ◄── done ───── finished
    input("> ") ...

Features call the module-level ``ask()``; before a console is installed
(tests, scripts) it falls back to plain ``input()``.
"""

from __future__ import annotations

import queue
import threading
from typing import Optional

from .executor import SHELL_SOURCE, CommandExecutor, NoAnswer
from .shell import Shell

_EOF = object()     # end of input while a question was open


class Console:
    """Reads the keyboard on its own daemon thread."""

    def __init__(self, shell: Shell, executor: CommandExecutor):
        self._shell = shell
        self._executor = executor
        # Executor → console: ("ask", prompt) or ("done",).
        self._events: queue.Queue[tuple] = queue.Queue()
        self._answers: queue.Queue = queue.Queue()      # console → executor
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="console")

    def start(self) -> None:
        self._thread.start()

    def ask(self, prompt: str) -> str:
        """Ask the person at the keyboard; executor thread only. Raises
        NoAnswer at end of input, or when the running command did not come
        from the keyboard (nobody is there to answer, and the console thread
        is busy reading the next command)."""
        if self._executor.current_source() != SHELL_SOURCE:
            raise NoAnswer("it needs an answer, so type the command in the console")
        self._events.put(("ask", prompt))
        answer = self._answers.get()
        if answer is _EOF:
            raise NoAnswer("end of input")
        return answer

    # ── Console thread ────────────────────────────────────────────────────

    def _run(self) -> None:
        self._enable_tab_completion()
        print(self._shell.intro)
        eof = False
        while not self._executor.stopped:
            if eof:
                line = "exit"       # end of input (Ctrl-D/Ctrl-Z, end of a pipe)
            else:
                try:
                    line = input(self._shell.prompt)
                except EOFError:
                    eof, line = True, "exit"
                    print()
            self._executor.submit(line, SHELL_SOURCE,
                                  on_done=lambda: self._events.put(("done",)))
            eof = self._serve_until_done(eof)

    def _serve_until_done(self, eof: bool) -> bool:
        """Answer the running command's questions until it finishes.
        Returns whether input has ended."""
        while True:
            event = self._events.get()
            if event[0] == "done":
                return eof
            if eof:
                self._answers.put(_EOF)
                continue
            try:
                self._answers.put(input(event[1]))
            except EOFError:
                eof = True
                self._answers.put(_EOF)

    def _enable_tab_completion(self) -> None:
        """What cmd.Cmd.cmdloop did before reading; that loop isn't used."""
        try:
            import readline
        except ImportError:
            return
        readline.set_completer(self._shell.complete)
        if getattr(readline, "backend", "") == "editline":
            readline.parse_and_bind("bind ^I rl_complete")
        else:
            readline.parse_and_bind("tab: complete")


_console: Optional[Console] = None


def install(console: Console) -> None:
    global _console
    _console = console


def ask(prompt: str) -> str:
    """Ask the person at the keyboard a question mid-command and return the
    typed line. Raises NoAnswer if no answer can come; the command then ends
    with a message."""
    if _console is not None:
        return _console.ask(prompt)
    try:
        return input(prompt)
    except EOFError:
        raise NoAnswer("end of input") from None
