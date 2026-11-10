"""
prompts.py — Interactive questions asked of the DM mid-command.

Everything here calls core's ``ask()``, which blocks the executor thread
until the answer is typed in the console. That has a hard consequence: none
of this can move into a view or a bus consumer, because those run on the
mainloop thread and blocking there would freeze the display. At end of input
``ask()`` raises NoAnswer, which cancels the command.

Kept in its own module so that constraint is visible rather than buried
among the command handlers.

These functions take the combatant (and, where needed, the combat) they act
on, so they carry no feature state of their own.
"""

from __future__ import annotations

from ...core.console import ask, can_ask
from ...core.executor import NoAnswer
from .model import Combat, Combatant, Status

# Resource keys these prompts may return.
NO_RESOURCE = "none"
SPECIAL_CASE = "special"

# `combat action … uses <word>`: the out-of-turn answer given up front.
USES = {"reaction": "reaction", "legendary": "legendary_actions", "none": SPECIAL_CASE}


def _need_console() -> None:
    """Give up before printing a menu nobody can answer (e.g. a player's
    action from the webapp); callers turn this into a notice."""
    if not can_ask():
        raise NoAnswer("it needs an answer, so type the command in the console")


# ── Zero-HP status ──────────────────────────────────────────────────────

def prompt_zero_hp_status(combatant: Combatant) -> Status:
    """Ask what happens to a combatant who dropped to 0 or below.

    A combatant already dying gets a shorter menu — they can only die or
    stay dying.
    """
    _need_console()
    if combatant.status is Status.DYING:
        print(f"[?] {combatant.name} is already DYING and took more damage.")
        print("    1. Dead")
        print("    2. Remain Dying")
        options = [Status.DEAD, Status.DYING]
    else:
        print(f"[?] {combatant.name} dropped to {combatant.hp_current} HP. Status?")
        print("    1. Dead")
        print("    2. Dying")
        print("    3. Incapacitated")
        options = [Status.DEAD, Status.DYING, Status.INCAPACITATED]

    while True:
        try:
            index = int(ask("    > ").strip()) - 1
            if not 0 <= index < len(options):
                raise ValueError
            return options[index]
        except (ValueError, IndexError):
            print(f"    [!] Enter a number between 1 and {len(options)}.")


def apply_zero_hp_status(combatant: Combatant, log) -> None:
    """Update status after an HP change, prompting if they hit 0.

    Also handles the reverse: a healed combatant returns to active.
    """
    if combatant.hp_current > 0:
        if combatant.status in (Status.DYING, Status.INCAPACITATED):
            combatant.status = Status.ACTIVE
            print(f"    [+] {combatant.name} recovered and is now active.")
            log.log_entry(f"[status] {combatant.name} recovered to active.")
        return

    if combatant.status is Status.DEAD:
        return

    try:
        new_status = prompt_zero_hp_status(combatant)
    except NoAnswer:
        # Not typed in the console (e.g. a player's action): the DM decides.
        print(f"[!] {combatant.name} dropped to 0 HP: set the status with "
              f"'combat status {combatant.name} dead|dying|incapacitated'.")
        log.log_entry(f"[status] {combatant.name} at 0 HP; status left to the DM.")
        return
    combatant.status = new_status
    print(f"    [+] {combatant.name} is now [{new_status.value.upper()}].")
    log.log_entry(f"[status] {combatant.name} → [{new_status.value.upper()}].")


# ── Out-of-turn resource spend ──────────────────────────────────────────

def prompt_resource_spend(combat: Combat, actor: Combatant,
                          uses: str | None = None) -> str:
    """Ask which resource an out-of-turn action costs, unless ``uses``
    (reaction | legendary | none) already says.

    Returns a resource key, ``SPECIAL_CASE`` (acted with no cost), or
    ``NO_RESOURCE`` when it is the actor's own turn and nothing is spent.
    Raises ValueError when ``uses`` names a resource the actor doesn't have.
    """
    if combat.current_combatant().name == actor.name:
        return NO_RESOURCE      # their own turn — no prompt needed

    if uses is not None:
        key = USES[uses]
        if key != SPECIAL_CASE and key not in actor.resources:
            label = "a Reaction" if uses == "reaction" else "legendary actions"
            raise ValueError(f"{actor.name} has no {label}.")
        return key

    _need_console()
    options: list[tuple[str, str]] = []
    for key, label in (("reaction", "Reaction"),
                       ("legendary_actions", "Legendary Actions")):
        resource = actor.resources.get(key)
        if resource is not None:
            warn = " [EMPTY]" if resource.current == 0 else ""
            options.append(
                (key, f"{label} ({resource.current}/{resource.maximum}){warn}"))
    options.append((SPECIAL_CASE, "Special case (no resource spent)"))

    print(f"\n[?] {actor.name} is acting outside their turn. Resource spent?")
    for i, (_, label) in enumerate(options, 1):
        print(f"    {i}. {label}")

    while True:
        try:
            index = int(ask("    > ").strip()) - 1
            if not 0 <= index < len(options):
                raise ValueError
            return options[index][0]
        except (ValueError, IndexError):
            print(f"    [!] Enter a number between 1 and {len(options)}.")


def apply_resource_spend(actor: Combatant, resource_key: str) -> None:
    """Deduct the chosen resource and report it."""
    if resource_key in (NO_RESOURCE, SPECIAL_CASE):
        if resource_key == SPECIAL_CASE:
            print(f"    [i] Special case — no resource spent for {actor.name}.")
        return
    message = actor.adjust_resource(resource_key, -1)
    display = resource_key.replace("_", " ").title()
    print(f"    [+] {actor.name} spent a {display}. ({message})")


# ── Initiative ties ─────────────────────────────────────────────────────

def resolve_ties_for(combat: Combat, initiative: int, tied: list) -> None:
    """Ask the DM to order combatants sharing an initiative value."""
    _need_console()
    names = [c.name for c in tied]
    print(f"\n[?] Initiative tie at {initiative} between:")
    for i, name in enumerate(names, 1):
        print(f"    {i}. {name}")
    print(f"    Enter desired turn order as space-separated numbers (1-{len(names)}),")
    print("    e.g. '2 1 3' means combatant 2 goes first, 1 second, 3 third.")

    while True:
        try:
            raw = ask("    > ").strip().split()
            if len(raw) != len(names):
                raise ValueError
            positions = [int(x) for x in raw]
            if sorted(positions) != list(range(1, len(names) + 1)):
                raise ValueError
            message = combat.apply_tiebreaker_order(initiative, names, positions)
            print(f"    [+] {message}")
            return
        except (ValueError, IndexError):
            print(f"    [!] Invalid input. Enter {len(names)} unique numbers "
                  f"between 1 and {len(names)}.")


def resolve_ties(combat: Combat) -> None:
    """Resolve every tied initiative group — used when combat starts."""
    for initiative, tied in combat.tied_initiatives().items():
        resolve_ties_for(combat, initiative, tied)
