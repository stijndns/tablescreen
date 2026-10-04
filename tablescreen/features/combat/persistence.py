"""
persistence.py — Saving and loading combatant rosters as JSON.

Self-contained serialisation: no bus, no views, no feature state. Takes a
Combat to read from or write into, and reports what it did.

Import prompts for each combatant's initiative, so like prompts.py this runs
on the shell thread and must not be called from a bus consumer.

Files live in the project's ``combatants/`` directory, resolved through
core.paths so it works regardless of the working directory.
"""

from __future__ import annotations

import json

from ...core.paths import COMBATANT_IMAGES_DIR, COMBATANTS_DIR, ensure_dir
from . import prompts
from .model import Combat, Combatant, Status


def _resolve(filename: str):
    """Add the .json suffix if missing and return the full path."""
    if not filename.endswith(".json"):
        filename += ".json"
    return COMBATANTS_DIR / filename


# ── Export ──────────────────────────────────────────────────────────────

def export_combatants(combat: Combat, filename: str) -> None:
    """Write the roster to combatants/<filename>.json, also during combat.

    Stores each combatant's definition (name, type, max HP, starting temp HP,
    portrait, resource maxima) plus their current HP and temp HP, so damaged
    survivors can be carried into the next encounter. Dead combatants are
    left out. Initiative, conditions and resource use are per-encounter and
    not stored.
    """
    if not combat.combatants:
        print("[!] No combatants to export.")
        return

    dead = [c.name for c in combat.combatants if c.status is Status.DEAD]
    survivors = [c for c in combat.combatants if c.status is not Status.DEAD]
    if not survivors:
        print("[!] No combatants to export: all of them are dead.")
        return

    path = _resolve(filename)
    ensure_dir(COMBATANTS_DIR)

    data = [
        {
            "name": c.name,
            "type": c.type,
            "hp_max": c.hp_max,
            "hp_current": c.hp_current,
            # Starting temp HP; "max" mirrors hp_max, it isn't a cap.
            "temp_hp_max": c.temp_hp_max,
            "temp_hp_current": c.temp_hp,
            "image": c.image,
            "resources": [
                {"name": r.name, "maximum": r.maximum}
                for r in c.resources.values()
            ],
        }
        for c in survivors
    ]

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"[+] Exported {len(data)} combatant(s) to {path}")
        if dead:
            print(f"    Left out (dead): {', '.join(dead)}")
    except Exception as exc:
        print(f"[!] Could not export: {exc}")


# ── Import ──────────────────────────────────────────────────────────────

def import_combatants(combat: Combat, filename: str) -> list[Combatant]:
    """Load a roster, prompting for each combatant's initiative.

    Returns the combatants that were added (empty if none), so the caller can
    log them and knows whether to refresh the display. During combat they
    join as pending (next round), and a name that already exists is skipped
    rather than overwritten, because removing someone from a running turn
    order is unsafe.
    """
    path = _resolve(filename)
    if not path.exists():
        print(f"[!] File not found: {path}")
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        print(f"[!] Could not read file: {exc}")
        return []

    added: list[Combatant] = []
    overwritten = skipped = 0

    for entry in data:
        name = entry["name"]
        ctype = entry["type"]
        hp_max = entry["hp_max"]

        if combat.get(name) is not None:
            if combat.active:
                print(f"[!] {name} is already in this combat — skipped. "
                      f"Rename or remove the existing one to import it.")
                skipped += 1
                continue
            print(f"[!] {name} already exists — overwriting.")
            combat.remove_combatant(name)
            overwritten += 1

        initiative = _prompt_initiative(name, ctype, hp_max)

        # add_reaction=False: resources are restored from the file below, so
        # injecting a default Reaction here would duplicate or override it.
        combatant = combat.add_combatant(
            name=name, combatant_type=ctype, initiative=initiative,
            hp_max=hp_max, add_reaction=False,
        )
        if combatant is None:
            print(f"[!] Failed to add {name} (unexpected duplicate).")
            continue

        for resource in entry.get("resources", []):
            combatant.add_resource(resource["name"], resource["maximum"])

        # All optional; older files lack them. Current values win over maxima.
        combatant.hp_current = _count(entry, "hp_current", hp_max, name)
        combatant.temp_hp_max = _count(entry, "temp_hp_max", 0, name)
        combatant.temp_hp = _count(entry, "temp_hp_current", combatant.temp_hp_max, name)

        image = entry.get("image")
        if image:
            if (COMBATANT_IMAGES_DIR / image).exists():
                combatant.image = image
            else:
                print(f"  [!] Image not found for {name}: {image} (skipped)")

        print(f"[+] Imported: {combatant.summary()}")
        added.append(combatant)

    if combat.active and added:
        _resolve_new_ties(combat, added)

    summary = f"{len(added)} added, {overwritten} overwritten"
    if skipped:
        summary += f", {skipped} skipped"
    if combat.active and added:
        summary += "; they join the turn order next round"
    print(f"\n[+] Import complete: {summary}.")
    return added


def _count(entry: dict, key: str, default: int, name: str) -> int:
    """A non-negative whole number from the file, or the default with a warning."""
    value = entry.get(key, default)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    print(f"  [!] Invalid {key} for {name}: {value!r} (using {default})")
    return default


def _resolve_new_ties(combat: Combat, added: list[Combatant]) -> None:
    """Ask the turn order for initiative ties the import created, as
    `combat add` does during combat."""
    ties = combat.tied_initiatives()
    for initiative in sorted({c.initiative for c in added} & ties.keys(), reverse=True):
        prompts.resolve_ties_for(combat, initiative, ties[initiative])


def _prompt_initiative(name: str, ctype: str, hp_max: int) -> int:
    while True:
        try:
            raw = input(f"  Initiative for {name} ({ctype.upper()}, {hp_max} HP): ")
            return int(raw.strip())
        except ValueError:
            print("  [!] Please enter an integer.")
