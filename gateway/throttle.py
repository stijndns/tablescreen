"""
throttle.py — Slows down password guessing.

Failures are counted per account name. After MAX_FAILURES within LOCKOUT
seconds the name is locked for LOCKOUT seconds, also for the right
password, so guessing gets at most MAX_FAILURES tries a minute. Kept in
memory: a restart of the gateway forgets it.
"""

from __future__ import annotations

import time
from typing import Callable

MAX_FAILURES = 5
LOCKOUT = 60.0          # seconds
MAX_NAMES = 10_000      # names tracked before old entries are dropped


class Throttle:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def wait(self, name: str) -> float:
        """Seconds until ``name`` may try again (0: now)."""
        until = self._locked_until.get(name.lower(), 0.0)
        return max(0.0, until - self._clock())

    def failed(self, name: str) -> None:
        key, now = name.lower(), self._clock()
        recent = [t for t in self._failures.get(key, []) if t > now - LOCKOUT]
        recent.append(now)
        if len(recent) >= MAX_FAILURES:
            self._locked_until[key] = now + LOCKOUT
            recent = []
        self._failures[key] = recent
        if len(self._failures) > MAX_NAMES:
            self._forget_old(now)

    def succeeded(self, name: str) -> None:
        self._failures.pop(name.lower(), None)

    def _forget_old(self, now: float) -> None:
        self._failures = {k: v for k, v in self._failures.items()
                          if v and v[-1] > now - LOCKOUT}
        self._locked_until = {k: t for k, t in self._locked_until.items() if t > now}
