from dataclasses import dataclass, field
from enum import StrEnum
from typing import Optional

# ── HP bar thresholds (fraction of max HP) ──────────────────────────────────
BAR_GREEN  = 0.75   # > 75 %  → green
BAR_YELLOW = 0.50   # 50–75 % → yellow
BAR_ORANGE = 0.25   # 25–50 % → orange
BAR_RED    = 0.01   # 1–24 %  → red
# ≤ 0 %             → dead / empty

@dataclass
class Resource:
    """A named, finite resource (spell slots, reactions, legendary actions, …)."""
    name: str
    current: int
    maximum: int

    def adjust(self, delta: int) -> str:
        """Apply delta, clamped to [0, maximum]. Returns a description string."""
        before = self.current
        self.current = max(0, min(self.maximum, self.current + delta))
        return f"{self.name}: {before} → {self.current}/{self.maximum}"

    def reset(self):
        self.current = self.maximum

    def __str__(self):
        return f"{self.name} {self.current}/{self.maximum}"

class Status(StrEnum):
    ACTIVE = "active"
    DYING = "dying"
    DEAD = "dead"
    INCAPACITATED = "incapacitated"

class Type(StrEnum):
    NPC = "npc"
    PC = "pc"
    MONSTER = "monster"
    OTHER: str

@dataclass
class Combatant:
    """One participant in combat (PC, NPC, or monster)."""

    name: str
    type: Type
    initiative: int
    hp_current: int
    hp_max: int
    resources: dict[str, Resource] = field(default_factory=dict)   # name → Resource
    conditions: list = field(default_factory=list)  # free-form condition strings
    status: Status = Status.ACTIVE
    has_acted: bool = False      # False until the combatant has taken their first turn
    tiebreaker: int = 0          # Used to resolve initiative ties; lower = earlier in order
    pending: bool = False        # True when added mid-combat; enters rotation next round
    legendary_actions_revealed: bool = False  # True after first legendary action is used
    image: str | None = None     # filename relative to assets/images/combatants/
    left_combat: bool = False    # True when removed during active combat

    # Default resources injected at creation time (reaction, etc.) are done
    # externally so the shell can control them.

    @property
    def is_active(self) -> bool:
        return self.status is Status.ACTIVE

    # ── HP helpers ──────────────────────────────────────────────────────────

    def adjust_hp(self, delta: int) -> str:
        before = self.hp_current
        self.hp_current = max(0, self.hp_current + delta)
        return f"{self.name} HP: {before} → {self.hp_current}/{self.hp_max}"

    def set_hp(self, value: int) -> str:
        return self.adjust_hp(value - self.hp_current)

    @property
    def hp_bar_state(self) -> str:
        """Return one of: 'green', 'yellow', 'orange', 'red', 'dead'."""
        if self.hp_max <= 0:
            return "dead"
        ratio = self.hp_current / self.hp_max
        if ratio > BAR_GREEN:
            return "green"
        if ratio > BAR_YELLOW:
            return "yellow"
        if ratio > BAR_ORANGE:
            return "orange"
        if ratio > 0:
            return "red"
        return "dead"

    @property
    def hp_fraction(self) -> float:
        if self.hp_max <= 0:
            return 0.0
        return max(0.0, min(1.0, self.hp_current / self.hp_max))

    # ── Resource helpers ─────────────────────────────────────────────────────

    def add_resource(self, name: str, maximum: int, current: Optional[int] = None) -> str:
        key = name.lower()
        c = maximum if current is None else current
        self.resources[key] = Resource(name=name, current=c, maximum=maximum)
        return f"Added resource '{name}' ({c}/{maximum}) to {self.name}"

    def adjust_resource(self, name: str, delta: int) -> str:
        key = name.lower()
        if key not in self.resources:
            return f"[!] {self.name} has no resource '{name}'"
        result = self.resources[key].adjust(delta)
        # Reveal legendary actions on first use (delta < 0 means spending)
        if key == "legendary_actions" and delta < 0:
            self.legendary_actions_revealed = True
        return result

    def reset_resources(self):
        for r in self.resources.values():
            r.reset()

    # ── Condition helpers ────────────────────────────────────────────────────

    def add_condition(self, condition: str) -> str:
        if condition.lower() in (c.lower() for c in self.conditions):
            return f"[!] {self.name} already has condition '{condition}'"
        self.conditions.append(condition)
        return f"Added condition '{condition}' to {self.name}"

    def remove_condition(self, condition: str) -> str:
        for i, c in enumerate(self.conditions):
            if c.lower() == condition.lower():
                self.conditions.pop(i)
                return f"Removed condition '{condition}' from {self.name}"
        return f"[!] {self.name} does not have condition '{condition}'"

    # ── Combat helpers ───────────────────────────────────────────────────────

    def is_in_combat(self) -> bool:
        return not self.pending and not self.left_combat #and self.status not in (Status.DEAD, Status.INCAPACITATED)

    def hidden_initiative(self) -> bool:
        return self.type is Type.MONSTER and not self.has_acted

    # ── Display helpers ──────────────────────────────────────────────────────

    def summary(self) -> str:
        """Single-line DM summary."""
        res_str = "  ".join(str(r) for r in self.resources.values())
        cond_str = ", ".join(self.conditions)
        if self.status is Status.DEAD:
            status = " [DEAD]"
        elif self.status is Status.DYING:
            status = " [DYING]"
        elif self.status is Status.INCAPACITATED:
            status = " [INCAPACITATED]"
        elif self.left_combat:
            status = " [LEFT COMBAT]"
        elif self.pending:
            status = " [PENDING]"
        else:
            status = ""
        return (
            f"[{self.type.value.upper():7s}] "
            f"{self.name:<20s} "
            f"Init:{self.initiative:>3}  "
            f"HP:{self.hp_current:>4}/{self.hp_max:<4}"
            + (f"  {res_str}" if res_str else "")
            + (f"  [{cond_str}]" if cond_str else "")
            + status
        )
