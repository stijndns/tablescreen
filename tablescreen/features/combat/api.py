"""
api.py — What the player webapp gets from the combat tracker.

``public_state()`` is the combat screen as data (schema ``combat/1``): it
follows the screen's rules exactly, so it never shows more than the table
does. The actions let a player act as their own character; the dm account
may act for anyone. Each handler only builds an ordinary command line; the
command's own checks and messages do the rest. Players get only the first
line of those messages, without hit point details (``player_reply``).

Runs on the executor thread, like every command.
"""

from __future__ import annotations

import re
from typing import Optional

from ...core.api import Caller, Field, quote
from .model import Combat, Combatant, Type

SCHEMA = "combat/1"
ACTION_KINDS = ("damage", "heal", "condition", "remove_condition")


# ── Public state ─────────────────────────────────────────────────────────

def public_state(combat: Combat) -> dict:
    """The combat screen as data. Before combat only while rolling
    initiative, and then only who the screen would show in full (no
    monsters yet)."""
    snapshot = combat.snapshot()
    shown = snapshot["combatants"]
    if not combat.active:
        shown = [c for c in shown if combat.rolling and c.is_revealed()]
    return {
        "active": combat.active,
        "rolling": combat.rolling,
        "round": combat.round,
        "current": snapshot["current"],
        "combatants": [_public_combatant(c) for c in shown],
        "waiting_for": [c.name for c in shown if c.initiative is None],
    }


def _public_combatant(c: Combatant) -> dict:
    """One row of the screen: greyed rows (pending, left, monsters before
    their first turn) show only name, type, initiative and status."""
    row = {
        "name": c.name,
        "type": c.type.value,
        "revealed": c.is_revealed(),
        "initiative": None if c.hidden_initiative() else c.initiative,
        "status": c.status.value,
        "pending": c.pending,
        "left": c.left_combat,
    }
    if not row["revealed"]:
        return row
    if c.type is Type.PC:
        row["hp"] = {"current": c.hp_current, "max": c.hp_max, "temp": c.temp_hp}
    else:
        row["health"] = c.hp_bar_state         # green | yellow | orange | red | dead
    for key, label in (("reaction", "reaction"), ("legendary_actions", "legendary")):
        resource = c.resources.get(key)
        if resource is not None:
            row[label] = {"current": resource.current, "max": resource.maximum}
    row["conditions"] = list(c.conditions)
    return row


# ── Actions ──────────────────────────────────────────────────────────────

ACTOR = Field(str, required=False)      # players: their own character (the default)

ACTION_FIELDS = {
    "actor": ACTOR,
    "kind": Field(str, choices=ACTION_KINDS),
    "target": Field(str),
    "amount": Field(int, required=False, minimum=1),
    "damage_type": Field(str, required=False, max_length=32),
    "condition": Field(str, required=False, max_length=32),
    "uses": Field(str, required=False, choices=("reaction", "legendary", "none")),
}
END_TURN_FIELDS = {"actor": ACTOR}
INITIATIVE_FIELDS = {"actor": ACTOR, "value": Field(int, minimum=-10, maximum=50)}


def register(services, combat: Combat) -> None:
    services.publish_state(lambda: public_state(combat), SCHEMA)
    services.register_action("action", ACTION_FIELDS, "player",
                             lambda args, caller: action_line(combat, args, caller),
                             reply=player_reply)
    services.register_action("end_turn", END_TURN_FIELDS, "player",
                             lambda args, caller: end_turn_line(combat, args, caller),
                             reply=player_reply)
    services.register_action("initiative", INITIATIVE_FIELDS, "player",
                             lambda args, caller: initiative_line(combat, args, caller),
                             reply=player_reply)


def player_reply(output: str) -> str:
    """The first line, without its details in brackets: the console's
    text is for the DM (``Goblin HP: 7 → 4/7`` is more than the screen's
    "Bloodied"; zero-HP notices are instructions for the DM). The live state
    shows the rest."""
    first = output.split("\n", 1)[0]
    return re.sub(r"\s+\(.*\)$", "", first)


def _actor(combat: Combat, args: dict, caller: Caller, required: bool = True
           ) -> Optional[Combatant]:
    """Who acts: a player's own character (``actor`` may only name it), or
    for the dm whoever ``actor`` names."""
    name = args.get("actor")
    if not caller.is_dm:
        if not caller.character:
            raise ValueError("Your account isn't linked to a character.")
        if name is not None and name.lower() != caller.character.lower():
            raise ValueError(f"You can only act as {caller.character}.")
        name = caller.character
    elif name is None:
        if required:
            raise ValueError("Say who acts (actor).")
        return None
    combatant = combat.get(name)
    if combatant is None:
        raise ValueError(f"{name} isn't in this combat.")
    return combatant


def action_line(combat: Combat, args: dict, caller: Caller) -> str:
    if not combat.active:
        raise ValueError("Combat hasn't started.")
    actor = _actor(combat, args, caller)
    # Anyone on the screen; a monster before its first turn is listed too.
    target = combat.get(args["target"])
    if target is None:
        raise ValueError(f"No combatant named {args['target']}.")
    kind = args["kind"]
    words = ["combat", "action", quote(actor.name), kind, quote(target.name)]
    if kind in ("damage", "heal"):
        if "amount" not in args:
            raise ValueError("How much? (amount)")
        words.append(str(args["amount"]))
        if kind == "damage" and "damage_type" in args:
            words.append(quote(args["damage_type"]))
    else:
        if "condition" not in args:
            raise ValueError("Which condition? (condition)")
        words.append(quote(args["condition"]))
    if "uses" in args:
        words += ["uses", args["uses"]]
    return " ".join(words)


def end_turn_line(combat: Combat, args: dict, caller: Caller) -> str:
    if not combat.active:
        raise ValueError("Combat hasn't started.")
    actor = _actor(combat, args, caller, required=False)
    current = combat.current_combatant()
    if actor is not None and actor is not current:
        raise ValueError(f"It isn't {actor.name}'s turn.")
    return "next"


def initiative_line(combat: Combat, args: dict, caller: Caller) -> str:
    actor = _actor(combat, args, caller)
    if not caller.is_dm and actor.initiative is not None:
        raise ValueError(f"{actor.name}'s initiative is already in; ask the DM to change it.")
    return f"combat initiative {quote(actor.name)} {args['value']}"
