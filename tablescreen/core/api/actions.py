"""
actions.py — What a feature registers for the API: typed actions.

A feature describes each action's fields; core checks every value against
them before the feature's handler sees it. The handler then decides whether
this caller may do this now (own character, combat running, …) and returns
the command line to run, or raises ValueError with a message for the player.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .protocol import ProtocolError, clean_text

PERMISSIONS = ("player", "dm")      # who may call an action at all


@dataclass(frozen=True)
class Field:
    """One argument of an action."""
    kind: type                      # int or str
    required: bool = True
    choices: tuple[str, ...] = ()   # str only: the allowed values
    minimum: int = -9999            # int only
    maximum: int = 9999
    max_length: int = 64            # str only

    def check(self, name: str, value) -> int | str:
        if self.kind is int:
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{name} must be a whole number.")
            if not self.minimum <= value <= self.maximum:
                raise ValueError(f"{name} must be {self.minimum} to {self.maximum}.")
            return value
        try:
            value = clean_text(value, name, self.max_length)
        except ProtocolError as exc:
            raise ValueError(f"{exc}.") from None
        if self.choices and value not in self.choices:
            raise ValueError(f"{name} must be one of: {', '.join(self.choices)}.")
        return value


@dataclass(frozen=True)
class Caller:
    """Who sent an action, as the gateway vouches for it."""
    user: str
    role: str                       # guest | player | dm
    character: Optional[str] = None

    @property
    def is_dm(self) -> bool:
        return self.role == "dm"


# handler(args, caller) -> command line; runs on the executor thread.
Handler = Callable[[dict, Caller], str]
# reply(output) -> what a player is told; pure text, any thread.
Reply = Callable[[str], str]


@dataclass(frozen=True)
class Action:
    feature: str
    name: str
    fields: dict[str, Field]
    permission: str
    handler: Handler
    reply: Optional[Reply] = None   # None: players get the command's output

    def check_args(self, args: dict) -> dict:
        """The args with only known fields, each checked; ValueError otherwise."""
        unknown = set(args) - set(self.fields)
        if unknown:
            raise ValueError(f"Unknown field: {', '.join(sorted(unknown))}.")
        checked = {}
        for name, spec in self.fields.items():
            if name in args and args[name] is not None:
                checked[name] = spec.check(name, args[name])
            elif spec.required:
                raise ValueError(f"{name} is missing.")
        return checked

    def allows(self, caller: Caller) -> bool:
        if caller.role == "dm":
            return True
        return self.permission == "player" and caller.role == "player"
