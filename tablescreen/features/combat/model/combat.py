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
        # `combat start` with initiatives missing: no combat until they're in.
        self.rolling: bool = False

    # ── Building the encounter ───────────────────────────────────────────────

    def add_combatant(
        self,
        name: str,
        combatant_type: str,
        initiative: Optional[int],
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
        """Return combatants sorted by initiative descending, tiebreaker
        ascending; those without an initiative yet come last."""
        return sorted(self.combatants, key=lambda c: (c.initiative is None,
                                                      -(c.initiative or 0), c.tiebreaker))

    def missing_initiative(self) -> list[Combatant]:
        """Combatants still taking part whose initiative isn't entered yet."""
        return [c for c in self.combatants if c.initiative is None and not c.left_combat]

    def set_initiative(self, name: str, value: int) -> str:
        """Enter or change one initiative. Before combat: any time. During
        combat: a missing one any time (that combatant isn't in the order yet
        and joins next round), a change only while the round's first
        combatant is active, so the order never changes mid-round. Raises
        ValueError with the reason otherwise."""
        c = self.get(name)
        if c is None:
            raise ValueError(f"No combatant named '{name}'.")
        if self.active and c.initiative is not None and self.turn_index != 0:
            raise ValueError("During combat, initiative can only change at the start "
                             "of a round (while its first combatant is active).")
        first_before = self.current_combatant() if self.active and self._in_combat_order() else None
        c.initiative = value
        c.tiebreaker = 0
        if first_before is not None and c.is_in_combat():
            first = self._in_combat_order()[0]
            if first is not first_before:
                self._begin_turn(first)      # the new first combatant's turn
                return f"{c.name}: initiative {value}; {first.name} now goes first."
        return f"{c.name}: initiative {value}."

    def start(self) -> str:
        if not self.combatants:
            return "[!] No combatants added yet."
        if not self._in_combat_order():
            return "[!] All combatants are pending."
        if self.missing_initiative():
            raise ValueError("initiatives are missing")    # callers check first
        self.rolling = False
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
            # Pending combatants enter rotation this round, once their
            # initiative is in: no initiative, no turn.
            for c in self.combatants:
                c.pending = c.initiative is None
            new_round_msg = f"\n  *** Round {self.round} begins! ***"

        # Re-fetch order after potential pending changes
        order = self._in_combat_order()

        # Mark the incoming combatant as having acted (reveals monsters on player screen)
        # Also reset reaction and legendary actions at the start of their turn (D&D convention)
        next_c = order[self.turn_index]
        self._begin_turn(next_c)
        return f"Next turn: {next_c.name} (Initiative {next_c.initiative}){new_round_msg}"

    @staticmethod
    def _begin_turn(c: Combatant) -> None:
        """Their turn starts: revealed on the player screen, and their Reaction
        and legendary actions refill (D&D convention)."""
        c.has_acted = True
        if "reaction" in c.resources:
            c.resources["reaction"].reset()
        if "legendary_actions" in c.resources:
            c.resources["legendary_actions"].reset()

    def tied_initiatives(self) -> dict[int, list[Combatant]]:
        """Return a dict of initiative value → combatants for all values with 2+ combatants."""
        from collections import Counter
        counts = Counter(c.initiative for c in self.combatants if c.initiative is not None)
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
        self.rolling = False
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
            f"══ Round {self.round} "
            f"{'(active)' if self.active else '(rolling initiative)' if self.rolling else '(not started)'} ══",
        ]
        current = self.current_combatant() if self.active else None
        for i, c in enumerate(order):
            arrow = "▶ " if c is current else "  "
            lines.append(f"{arrow}{c.summary()}")
        return lines

    def display_order(self) -> list[Combatant]:
        """The player screen's order, the single source for the view, paging
        and the highlight: revealed combatants by initiative, then the rest
        (pending, left, monsters before their first turn), also by initiative."""
        order = self._order()
        return ([c for c in order if c.is_revealed()]
                + [c for c in order if not c.is_revealed()])

    def snapshot(self) -> dict:
        """Return a dict for the player screen renderer."""
        # Not current_combatant(): after a removal the turn index can point past
        # the end (review item 3), and the screen must keep drawing anyway.
        order = self._in_combat_order()
        current = (order[self.turn_index]
                   if self.active and self.turn_index < len(order) else None)
        return {
            "active": self.active,
            "rolling": self.rolling,
            "round": self.round,
            "combatants": deepcopy(self.display_order()),
            "current": current.name if current else None,   # names are unique
        }