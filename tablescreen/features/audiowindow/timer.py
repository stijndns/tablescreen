"""
timer.py — The puzzle timer's state: counting up, or down to zero. No Tk.

The time is never counted tick by tick: a ``Timer`` remembers when it was
started and how long it ran before, and every reading is computed from
``time.monotonic()``. So it can't drift, pausing and resuming are exact,
and the executor (which changes it) and the main loop (which draws it)
only ever share a frozen record.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from typing import Optional

MAX_SECONDS = 100 * 3600 - 1            # 99:59:59
_DURATION = re.compile(r"(\d+)(?::(\d{1,2}))?(?::(\d{1,2}))?")


@dataclass(frozen=True)
class Timer:
    mode: str                       # "up" | "down"
    seconds: float                  # down: the time to count down; up: an offset (timer add)
    ran: float = 0.0                # time it ran before `since`
    since: Optional[float] = None   # monotonic time it last (re)started; None while paused

    @classmethod
    def start(cls, mode: str, seconds: float, now: float) -> "Timer":
        return cls(mode, seconds, 0.0, now)

    def run_time(self, now: float) -> float:
        return self.ran + (now - self.since if self.since is not None else 0.0)

    def value(self, now: float) -> float:
        """What the screen shows, in seconds: elapsed, or what is left."""
        if self.mode == "up":
            return max(0.0, self.seconds + self.run_time(now))
        return max(0.0, self.seconds - self.run_time(now))

    def ran_out(self, now: float) -> bool:
        return self.mode == "down" and self.value(now) <= 0

    @property
    def paused(self) -> bool:
        return self.since is None

    def pause(self, now: float) -> "Timer":
        return replace(self, ran=self.run_time(now), since=None)

    def resume(self, now: float) -> "Timer":
        return replace(self, since=now)

    def add(self, seconds: float, now: float) -> "Timer":
        """More (or, negative, less) time on the clock; it never goes below 0:00."""
        ran = self.run_time(now)
        if self.mode == "up":
            return replace(self, seconds=max(self.seconds + seconds, -ran))
        # Past zero the run time keeps growing: count from 0:00, not below it.
        return replace(self, seconds=max(max(self.seconds, ran) + seconds, ran))

    def shown(self, now: float) -> str:
        """The digits: a countdown shows a second until it has fully passed."""
        value = self.value(now)
        whole = math.ceil(value - 1e-9) if self.mode == "down" else math.floor(value + 1e-9)
        return format_time(whole)


def format_time(seconds: int) -> str:
    """m:ss, or h:mm:ss from an hour."""
    hours, rest = divmod(max(0, int(seconds)), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def parse_duration(text: str, allow_negative: bool = False) -> int:
    """'90', '1:30' or '1:30:00' (also '-0:30' when allowed) in seconds;
    ValueError with a message for the console."""
    text = text.strip()
    sign = 1
    if allow_negative and text[:1] in "+-":
        sign = -1 if text[0] == "-" else 1
        text = text[1:]
    match = _DURATION.fullmatch(text)
    if not match:
        raise ValueError(f"'{text}' isn't a time: write 90, 1:30 or 1:30:00.")
    parts = [int(p) for p in match.groups() if p is not None]
    if any(p >= 60 for p in parts[1:]):
        raise ValueError(f"'{text}': minutes and seconds go up to 59.")
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + part
    if seconds > MAX_SECONDS:
        raise ValueError("A timer goes up to 99:59:59.")
    return sign * seconds
