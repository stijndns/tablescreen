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
from .model import Combat


def _resolve(filename: str):
    """Add the .json suffix if missing and return the full path."""
    if not filename.endswith(".json"):
        filename += ".json"
    return COMBATANTS_DIR / filename


# ── Export ──────────────────────────────────────────────────────────────

def export_combatants(combat: Combat, filename: str) -> None:
    """Write the current roster to combatants/<filename>.json.

    Stores the reusable definition of each combatant — name, type, max HP,
    portrait and resource maxima — not their live state. Current HP,
    conditions and initiative are per-encounter and deliberately omitted.
    """
    if combat.active:
        print("[!] Cannot export during active combat.")
        return
    if not combat.combatants:
        print("[!] No combatants to export.")
        return

    path = _resolve(filename)
    ensure_dir(COMBATANTS_DIR)

    data = [
        {
            "name": c.name,
            "type": c.type,
            "hp_max": c.hp_max,
            "image": c.image,
            "resources": [
                {"name": r.name, "maximum": r.maximum}
                for r in c.resources.values()
            ],
        }
        for c in combat.combatants
    ]

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"[+] Exported {len(data)} combatant(s) to {path}")
    except Exception as exc:
        print(f"[!] Could not export: {exc}")


# ── Import ──────────────────────────────────────────────────────────────

def import_combatants(combat: Combat, filename: str) -> bool:
    """Load a roster, prompting for each combatant's initiative.

    Returns True if anything was added, so the caller knows whether to
    refresh the display.
    """
    if combat.active:
        print("[!] Cannot import during active combat.")
        return False

    path = _resolve(filename)
    if not path.exists():
        print(f"[!] File not found: {path}")
        return False

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        print(f"[!] Could not read file: {exc}")
        return False

    imported = 0
    overwritten = 0

    for entry in data:
        name = entry["name"]
        ctype = entry["type"]
        hp_max = entry["hp_max"]

        if combat.get(name) is not None:
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

        image = entry.get("image")
        if image:
            if (COMBATANT_IMAGES_DIR / image).exists():
                combatant.image = image
            else:
                print(f"  [!] Image not found for {name}: {image} (skipped)")

        print(f"[+] Imported: {combatant.summary()}")
        imported += 1

    print(f"\n[+] Import complete: {imported} added, {overwritten} overwritten.")
    return imported > 0


def _prompt_initiative(name: str, ctype: str, hp_max: int) -> int:
    while True:
        try:
            raw = input(f"  Initiative for {name} ({ctype.upper()}, {hp_max} HP): ")
            return int(raw.strip())
        except ValueError:
            print("  [!] Please enter an integer.")
