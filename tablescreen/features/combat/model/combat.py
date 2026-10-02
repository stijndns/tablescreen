"""
combat.py — Combat state model for AudioWindowCMD.

Tracks combatants, turn order, HP, and arbitrary named resources.
No rule-checking; purely a bookkeeping layer.
"""

from typing import Optional
from copy import deepcopy
from .combatant import Combatant, Type

class Combat:
    """Manages the full combat encounter."""

    def __init__(self):
        self.combatants: list[Combatant] = []
        self.round: int = 0
        self.turn_index: int = 0        # index into sorted active list
        self.active: bool = False

    # ── Building the encounter ───────────────────────────────────────────────

    def add_combatant(
        self,
        name: str,
        combatant_type: str,
        initiative: int,
        hp_max: int,
        hp_current: Optional[int] = None,
        add_reaction: bool = True,
    ) -> Combatant | None:
        existing = self.get(name)
        if existing is not None:
            if existing.left_combat:
                # Allow rejoining — fully remove the old entry
                self.combatants.remove(existing)
            else:
                return None
        c = Combatant(
            name=name,
            type=Type(combatant_type.lower()),
            initiative=initiative,
            hp_current=hp_current if hp_current is not None else hp_max,
            hp_max=hp_max,
        )
        if combatant_type.lower() in ("pc", "npc"):
            c.has_acted = True
        if self.active:
            c.pending = True
        if add_reaction:
            c.add_resource("Reaction", 1)
        self.combatants.append(c)
        return c

    def remove_combatant(self, name: str) -> bool:
        for i, c in enumerate(self.combatants):
            if c.name.lower() == name.lower():
                self.combatants.pop(i)
                # Clamp turn index
                self.turn_index = min(self.turn_index, max(0, len(self._order()) - 1))
                return True
        return False

    def remove_combatant_from_active(self, name: str) -> bool:
        """Mark a combatant as having left combat (greyed out, out of rotation)."""
        c = self.get(name)
        if c is None:
            return False
        c.left_combat = True
        return True

    def get(self, name: str) -> Optional[Combatant]:
        for c in self.combatants:
            if c.name.lower() == name.lower():
                return c
        return None

    # ── Turn flow ────────────────────────────────────────────────────────────

    def _order(self) -> list[Combatant]:
        """Return combatants sorted by initiative descending, tiebreaker ascending."""
        return sorted(self.combatants, key=lambda c: (-c.initiative, c.tiebreaker))

    def start(self) -> str:
        if not self.combatants:
            return "[!] No combatants added yet."
        if not self._in_combat_order():
            return "[!] All combatants are pending."
        self.active = True
        self.round = 1
        self.turn_index = 0
        first = self._order()[0]
        first.has_acted = True
        return f"Combat started! Round 1. First up: {first.name}"

    def current_combatant(self) -> Combatant:
        order = self._in_combat_order()
        return order[self.turn_index]

    def _in_combat_order(self) -> list[Combatant]:
        """Return only active (non-pending, non-left) combatants in initiative order."""
        return [c for c in self._order() if c.is_in_combat()]

    def next_turn(self) -> str:
        if not self.active:
            return "[!] Combat is not active. Use 'combat start'."
        order = self._in_combat_order()
        if not order:
            return "[!] No combatants."

        self.turn_index += 1
        new_round_msg = ""
        if self.turn_index >= len(order):
            self.turn_index = 0
            self.round += 1
            # Clear pending flags — all pending combatants enter rotation this round
            for c in self.combatants:
                c.pending = False
            new_round_msg = f"\n  *** Round {self.round} begins! ***"

        # Re-fetch order after potential pending changes
        order = self._in_combat_order()

        # Mark the incoming combatant as having acted (reveals monsters on player screen)
        # Also reset reaction and legendary actions at the start of their turn (D&D convention)
        next_c = order[self.turn_index]
        next_c.has_acted = True
        if "reaction" in next_c.resources:
            next_c.resources["reaction"].reset()
        if "legendary_actions" in next_c.resources:
            next_c.resources["legendary_actions"].reset()
        return f"Next turn: {next_c.name} (Initiative {next_c.initiative}){new_round_msg}"

    def tied_initiatives(self) -> dict[int, list[Combatant]]:
        """Return a dict of initiative value → combatants for all values with 2+ combatants."""
        from collections import Counter
        counts = Counter(c.initiative for c in self.combatants)
        return {
            val: [c for c in self.combatants if c.initiative == val]
            for val, count in counts.items() if count > 1
        }

    def apply_tiebreaker_order(self, initiative: int, names: list[str], ranks: list[int]) -> str:
        """Assign tiebreaker integers based on rank input.
        ranks is an ordered sequence of 1-based indices into names,
        where position in ranks = desired turn order.
        e.g. names=[A,B,C], ranks=[3,1,2] → C goes first, A second, B third."""
        combatants_at_init = [c for c in self.combatants if c.initiative == initiative]
        if set(n.lower() for n in names) != set(c.name.lower() for c in combatants_at_init):
            return "[!] Name list does not match combatants at that initiative value."
        for tiebreaker, name_idx in enumerate(ranks):
            c = self.get(names[name_idx - 1])
            if c:
                c.tiebreaker = tiebreaker
        return f"Tiebreaker order set for initiative {initiative}."

    def end(self) -> str:
        self.active = False
        self.combatants = []
        self.round = 0
        self.turn_index = 0
        return "Combat ended. All combatants cleared."

    def reset_all_resources(self) -> str:
        for c in self.combatants:
            c.reset_resources()
        return "All resources reset."

    # ── Display ──────────────────────────────────────────────────────────────

    def status(self) -> list[str]:
        """Full DM status printout."""
        if not self.combatants:
            return ["[i] No active combat."]
        order = self._order()
        lines = [
            f"══ Round {self.round} {'(active)' if self.active else '(not started)'} ══",
        ]
        current = self.current_combatant() if self.active else None
        for i, c in enumerate(order):
            arrow = "▶ " if c is current else "  "
            lines.append(f"{arrow}{c.summary()}")
        return lines

    def snapshot(self) -> dict:
        """Return a dict for the player screen renderer."""
        order = self._order()
        return {
            "active": self.active,
            "round": self.round,
            "combatants": deepcopy(order),
            "current_index": self.turn_index if self.active else -1
        }