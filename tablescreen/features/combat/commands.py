"""
commands.py — The combat tracker's shell commands.

All of these run on the SHELL thread. They mutate the combat model, write to
the log, and then ask the feature to refresh — which sends a bus message so
the redraw happens on the mainloop thread.

Commands live in a mixin rather than on the feature class itself only to
keep this file to one concern; ``CombatFeature`` inherits it and supplies
``self.combat``, ``self.log`` and ``self.refresh_views()``.

Some commands call into ``prompts`` and ``persistence``, both of which block
on ``input()``. That is safe here (shell thread) and would not be inside a
bus consumer.
"""

from __future__ import annotations

import glob
import os
import platform

from PIL import Image

from ...core.completion import get_arg_parts, tab_completion
from ...core.paths import COMBATANT_IMAGES_DIR, COMBATANTS_DIR
from . import persistence, prompts
from .model import Combatant, Type
from .views.styling import MIN_PAGE_SIZE

CURRENT_OS = platform.system()

ACTION_TYPES = ["damage", "heal", "condition", "remove_condition"]

COMBAT_HELP = """\
Combat tracker commands:
  combat new                                  — clear all combatants, start fresh
  combat add <name> <init> <type> <hp>        — add combatant
                                                type: pc | npc | monster
                                                e.g.  combat add Aria 18 pc 120
  combat add <name> <init> <type> <hp> <cur>  — add with current HP ≠ max
  combat start                                — begin combat (sort by initiative)
  combat status                               — print full DM status table
  combat end                                  — end combat, clear roster
  combat noreaction                           — add next combatant WITHOUT a reaction slot
                                                (use before the next 'combat add')
  combat remove <name>                        — remove a combatant (grey [LEFT COMBAT] if active)
  combat legendary <name> <max>               — add legendary actions to a monster or NPC
  combat reset resources                      — reset all resources for all combatants
  combat action <actor> damage <target> <amount> [type]  — deal damage
  combat action <actor> heal <target> <amount>           — heal
  combat action <actor> condition <target> <condition>   — apply condition
  combat action <actor> remove_condition <target> <cond> — remove condition
  combat image <name> <filename>              — assign an image to a combatant
  combat export <filename>                    — export roster to combatants/<filename>.json
  combat import <filename>                    — import roster from combatants/<filename>.json
  combat log                                  — print combat log to shell
  combat log save [filename]                  — save combat log to logs/<filename>.txt
  combat show                                 — restore combat view after showing an image

Shorthand commands (usable outside 'combat ...'):
  next                        — advance to next turn (resets current combatant's reaction)
  hp <name> <±amount>         — adjust HP:  hp Aria -15   hp Goblin-A +5
  hp <name> = <amount>        — set HP to exact value:  hp Aria = 80

  resource add <name> <res> <max>    — add/replace a resource slot
                                       e.g.  resource add Aria "Spell Slots 9th" 1
  resource <name> <res> <±amount>    — adjust a resource
                                       e.g.  resource Aria reaction -1
  resource reset <name>              — reset all resources for one combatant
  resource list <name>               — list a combatant's resources
"""


class CombatCommandsMixin:
    """Command handlers, mixed into CombatFeature."""

    # ── combat dispatcher ─────────────────────────────────────────────────

    def do_combat(self, arg: str) -> None:
        parts = get_arg_parts(arg)
        if not parts or parts[0] in ("help", "?"):
            print(COMBAT_HELP)
            return

        sub = parts[0].lower()
        rest = parts[1:]

        if sub == "new":
            if not self.log.check_unsaved_log():
                return
            self.combat.end()
            self.log.reset()
            print("[+] Combat roster cleared. Ready for new encounter.")
            self.refresh_views()

        elif sub == "add":
            self._cmd_add(rest)
        elif sub == "start":
            self._cmd_start()
        elif sub == "status":
            print("\n".join(self.combat.status()))
        elif sub == "end":
            self._cmd_end()
        elif sub == "remove":
            self._cmd_remove(rest)
        elif sub == "legendary":
            self._cmd_legendary(rest)
        elif sub == "log":
            self._cmd_log(rest)
        elif sub == "action":
            self._cmd_action(rest)
        elif sub == "export":
            if not rest:
                print("Usage: combat export <filename>")
            else:
                persistence.export_combatants(self.combat, rest[0])
        elif sub == "import":
            if not rest:
                print("Usage: combat import <filename>")
            elif persistence.import_combatants(self.combat, rest[0]):
                self.refresh_views()
        elif sub == "image":
            self._cmd_image(rest)
        elif sub in ("show", "screen"):
            if not self.combat.combatants:
                print("[!] No combatants added yet.")
            else:
                self.show_combat_view()
                print("[+] Combat view restored.")
        elif sub == "noreaction":
            self._next_no_reaction = True
            print("[i] Next 'combat add' will not get a Reaction slot.")
        elif sub == "reset" and rest and rest[0].lower() == "resources":
            message = self.combat.reset_all_resources()
            print(f"[+] {message}")
            self.log.log_entry("[combat reset resources] All resources reset.")
            self.refresh_views()
        else:
            print(f"[!] Unknown combat sub-command '{sub}'. Type 'combat help'.")

    # ── combat sub-commands ───────────────────────────────────────────────

    def _cmd_start(self) -> None:
        prompts.resolve_ties(self.combat)
        message = self.combat.start()
        print(f"[+] {message}")
        self.show_combat_view()
        if self.combat.active:
            # Seeds the log with the opening roster. Note this discards any
            # entries logged before the encounter started.
            self.log.reset(self.combat.status())
            first = self.combat.current_combatant()
            self.log.log_turn_marker(first.name, 1, new_round=False)

    def _cmd_end(self) -> None:
        if not self.log.check_unsaved_log():
            return
        message = self.combat.end()
        self.log.reset()
        print(f"[+] {message}")
        self.hide_combat_view()

    def _cmd_add(self, parts: list[str]) -> None:
        if len(parts) < 4:
            print("Usage: combat add <name> <initiative> <type> <hp_max> [hp_current]")
            return
        name = parts[0]
        try:
            initiative = int(parts[1])
            ctype = parts[2].lower()
            hp_max = int(parts[3])
            hp_current = int(parts[4]) if len(parts) >= 5 else None
        except ValueError:
            print("[!] initiative, hp_max, and hp_current must be integers.")
            return

        if ctype not in Type:
            print("[!] type must be one of: pc  npc  monster")
            return

        add_reaction = not self._next_no_reaction
        self._next_no_reaction = False

        combatant = self.combat.add_combatant(
            name=name, combatant_type=ctype, initiative=initiative,
            hp_max=hp_max, hp_current=hp_current, add_reaction=add_reaction,
        )
        if combatant is None:
            print(f"[!] A combatant named '{name}' already exists. "
                  f"Use a unique name, e.g. '{name} (Red)'.")
            return
        print(f"[+] Added: {combatant.summary()}")

        if self.combat.active:
            ties = self.combat.tied_initiatives()
            if initiative in ties:
                prompts.resolve_ties_for(self.combat, initiative, ties[initiative])
            self.log.log_entry(f"[combat add] {combatant.summary()}")
            self.refresh_views()

    def _cmd_remove(self, parts: list[str]) -> None:
        if not parts:
            print("Usage: combat remove <name>")
            return
        name = parts[0]
        if not self.combat.active:
            # Before combat starts a removal is a real deletion.
            if self.combat.remove_combatant(name):
                print(f"[+] {name} removed from roster.")
                self.refresh_views()
            else:
                print(f"[!] Combatant '{name}' not found.")
        else:
            # Mid-combat they are greyed out instead, so the log and the
            # turn order stay coherent.
            if self.combat.remove_combatant_from_active(name):
                print(f"[+] {name} has left combat.")
                self.log.log_entry(f"[combat remove] {name} left combat.")
                self.refresh_views()
            else:
                print(f"[!] Combatant '{name}' not found.")

    def _cmd_legendary(self, parts: list[str]) -> None:
        if len(parts) < 2:
            print("Usage: combat legendary <name> <max>")
            return
        name = parts[0]
        try:
            maximum = int(parts[1])
        except ValueError:
            print("[!] max must be an integer.")
            return
        combatant = self.combat.get(name)
        if combatant is None:
            print(f"[!] Combatant '{name}' not found.")
            return
        if combatant.type not in (Type.NPC, Type.MONSTER):
            print("[!] Legendary actions can only be assigned to NPCs and monsters.")
            return
        message = combatant.add_resource("legendary_actions", maximum)
        print(f"[+] {message}")
        self.log.log_entry(
            f"[combat legendary] {combatant.name}: Legendary Actions set to {maximum}")
        self.refresh_views()

    def _cmd_image(self, parts: list[str]) -> None:
        if len(parts) < 2:
            print("Usage: combat image <name> <filename>")
            return
        name, filename = parts[0], parts[1]
        combatant = self.combat.get(name)
        if combatant is None:
            print(f"[!] Combatant '{name}' not found.")
            return
        path = COMBATANT_IMAGES_DIR / filename
        if not path.exists():
            print(f"[!] Image not found: {path}")
            return
        combatant.image = filename
        print(f"[+] Image '{filename}' assigned to {name}.")
        self.refresh_views()

    def _cmd_log(self, parts: list[str]) -> None:
        if not parts or parts[0].lower() != "save":
            self.log.print()
        else:
            self.log.save(parts)

    def _cmd_action(self, parts: list[str]) -> None:
        if len(parts) < 3:
            print("Usage: combat action <actor> damage|heal|condition|"
                  "remove_condition <target> [args]")
            return

        actor_name, action_type, target_name = parts[0], parts[1].lower(), parts[2]
        rest = parts[3:]

        if action_type not in ACTION_TYPES:
            print(f"[!] Unknown action type '{action_type}'. "
                  f"Choose: {', '.join(ACTION_TYPES)}")
            return

        actor = self.combat.get(actor_name)
        if actor is None:
            print(f"[!] Actor '{actor_name}' not found.")
            return
        target = self.combat.get(target_name)
        if target is None:
            print(f"[!] Target '{target_name}' not found.")
            return

        # Ask about the resource BEFORE acting, so a cancelled/invalid action
        # does not leave a resource spent.
        resource_key = prompts.prompt_resource_spend(self.combat, actor)
        amount: int | str = ""

        if action_type in ("damage", "heal"):
            if not rest:
                print(f"[!] Usage: combat action <actor> {action_type} <target> <amount>")
                return
            try:
                amount = int(rest[0])
            except ValueError:
                print("[!] Amount must be an integer.")
                return
            if action_type == "damage":
                damage_type = rest[1] if len(rest) > 1 else None
                message = target.adjust_hp(-amount)
                suffix = f" {damage_type}" if damage_type else ""
                print(f"[+] {actor_name} → damage → {target_name}: "
                      f"{amount}{suffix}  ({message})")
            else:
                message = target.adjust_hp(amount)
                print(f"[+] {actor_name} → heal → {target_name}: {amount}  ({message})")
            prompts.apply_zero_hp_status(target, self.log)

        else:
            if not rest:
                print(f"[!] Usage: combat action <actor> {action_type} <target> <condition>")
                return
            condition = rest[0]
            if action_type == "condition":
                message = target.add_condition(condition)
                if message.startswith("[!]"):
                    print(f"    [!] Warning: {message}")
                else:
                    print(f"[+] {actor_name} → condition → {target_name}: {condition}")
            else:
                message = target.remove_condition(condition)
                if message.startswith("[!]"):
                    print(f"    {message}")
                else:
                    print(f"[+] {actor_name} → remove_condition → "
                          f"{target_name}: {condition}")

        prompts.apply_resource_spend(actor, resource_key)
        self._log_action(actor_name, action_type, target_name,
                         amount, rest, resource_key)
        self.refresh_views()

    def _log_action(self, actor_name, action_type, target_name,
                    amount, rest, resource_key) -> None:
        current = self.combat.current_combatant() if self.combat.active else None
        turn_ctx = (f"Round {self.combat.round}: {current.name}'s turn"
                    if current else "out of turn")
        if action_type in ("damage", "heal"):
            detail = f": {amount}" + (f" {rest[1]}" if len(rest) > 1 else "")
        else:
            detail = f": {rest[0]}"
        self.log.log_entry(
            f"[action | {turn_ctx}] {actor_name} → {action_type} → {target_name}{detail}")

        if resource_key not in (prompts.NO_RESOURCE, prompts.SPECIAL_CASE):
            display = resource_key.replace("_", " ").title()
            self.log.log_entry(f"           {actor_name} spent a {display}.")
        elif resource_key == prompts.SPECIAL_CASE:
            self.log.log_entry(
                f"           {actor_name}: special case (no resource spent).")

    # ── next ──────────────────────────────────────────────────────────────

    def do_next(self, arg: str) -> None:
        message = self.combat.next_turn()
        print(f"[+] {message}")
        if self.combat.active:
            new_round = "begins!" in message
            current = self.combat.current_combatant()
            if current:
                self.log.log_turn_marker(current.name, self.combat.round,
                                         new_round=new_round)
            self.refresh_views(page=self._page_of_current())

    def _page_of_current(self) -> int | None:
        """Page index holding the current combatant, so the view can follow
        the turn across page boundaries."""
        current = self.combat.current_combatant()
        if current is None:
            return None
        combatants: list[Combatant] = self.combat.snapshot()["combatants"]
        revealed = [c for c in combatants
                    if not c.pending and (c.type is Type.MONSTER or c.has_acted)]
        unrevealed = [c for c in combatants
                      if c.pending or (c.type is Type.MONSTER and not c.has_acted)]
        for index, entry in enumerate(revealed + unrevealed):
            if entry.name == current.name:
                return index // MIN_PAGE_SIZE
        return None

    # ── hp ────────────────────────────────────────────────────────────────

    def do_hp(self, arg: str) -> None:
        parts = get_arg_parts(arg)
        if len(parts) < 2:
            print("Usage: hp <name> <±amount>  |  hp <name> = <amount>")
            return

        combatant = self.combat.get(parts[0])
        if combatant is None:
            print(f"[!] Combatant '{parts[0]}' not found.")
            return

        try:
            if parts[1] == "=" and len(parts) >= 3:
                message = combatant.set_hp(int(parts[2]))
            else:
                message = combatant.adjust_hp(int(parts[1]))
        except ValueError:
            print("[!] Amount must be an integer (e.g. -15, +8, 42).")
            return

        print(f"[+] {message}")
        prompts.apply_zero_hp_status(combatant, self.log)
        self.log.log_entry(f"[hp] {message}")
        self.refresh_views()

    def do_maxhp(self, arg: str) -> None:
        parts = get_arg_parts(arg)
        if len(parts) < 2:
            print("Usage: maxhp <name> <new_max>")
            return
        combatant = self.combat.get(parts[0])
        if combatant is None:
            print(f"[!] Combatant '{parts[0]}' not found.")
            return
        try:
            new_max = int(parts[1])
        except ValueError:
            print("[!] new_max must be an integer.")
            return
        if new_max <= 0:
            print("[!] Maximum HP must be greater than 0.")
            return

        old_max = combatant.hp_max
        combatant.hp_max = new_max
        print(f"[+] {combatant.name} max HP: {old_max} → {new_max}")
        self.log.log_entry(f"[maxhp] {combatant.name} max HP: {old_max} → {new_max}")
        self.refresh_views()

    # ── resource ──────────────────────────────────────────────────────────

    def do_resource(self, arg: str) -> None:
        parts = get_arg_parts(arg)
        if not parts:
            print("Usage: resource add|reset|list|<name> …  Type 'help resource'.")
            return

        sub = parts[0].lower()

        if sub == "add":
            if len(parts) < 4:
                print("Usage: resource add <combatant_name> <resource_name> <max>")
                return
            try:
                maximum = int(parts[3])
            except ValueError:
                print("[!] max must be an integer.")
                return
            combatant = self.combat.get(parts[1])
            if combatant is None:
                print(f"[!] Combatant '{parts[1]}' not found.")
                return
            message = combatant.add_resource(parts[2], maximum)
            print(f"[+] {message}")
            self.log.log_entry(f"[resource add] {message}")
            self.refresh_views()

        elif sub == "reset":
            if len(parts) < 2:
                print("Usage: resource reset <combatant_name>")
                return
            combatant = self.combat.get(parts[1])
            if combatant is None:
                print(f"[!] Combatant '{parts[1]}' not found.")
                return
            combatant.reset_resources()
            print(f"[+] Resources reset for {combatant.name}.")
            self.log.log_entry(
                f"[resource reset] All resources reset for {combatant.name}.")
            self.refresh_views()

        elif sub == "list":
            if len(parts) < 2:
                print("Usage: resource list <combatant_name>")
                return
            combatant = self.combat.get(parts[1])
            if combatant is None:
                print(f"[!] Combatant '{parts[1]}' not found.")
                return
            if not combatant.resources:
                print(f"  {combatant.name} has no tracked resources.")
                return
            for resource in combatant.resources.values():
                display = resource.name.replace("_", " ").title()
                bar = "█" * resource.current + "░" * (resource.maximum - resource.current)
                print(f"  {display:<30s} {resource.current}/{resource.maximum}  [{bar}]")

        else:
            # resource <name> <resource_name> <±amount>
            if len(parts) < 3:
                print("Usage: resource <combatant_name> <resource_name> <±amount>")
                return
            try:
                delta = int(parts[2])
            except ValueError:
                print("[!] amount must be an integer.")
                return
            combatant = self.combat.get(parts[0])
            if combatant is None:
                print(f"[!] Combatant '{parts[0]}' not found.")
                return
            message = combatant.adjust_resource(parts[1], delta)
            print(f"[+] {message}")
            self.log.log_entry(f"[resource] {message}")
            self.refresh_views()

    # ── condition ─────────────────────────────────────────────────────────

    def do_condition(self, arg: str) -> None:
        parts = get_arg_parts(arg)
        if len(parts) < 2:
            print("Usage: condition add|remove|list <name> [condition]")
            return

        sub = parts[0].lower()
        combatant = self.combat.get(parts[1])
        if combatant is None:
            print(f"[!] Combatant '{parts[1]}' not found.")
            return

        if sub == "list":
            if not combatant.conditions:
                print(f"  {combatant.name} has no conditions.")
            else:
                for i, condition in enumerate(combatant.conditions, 1):
                    print(f"  {i}. {condition}")
            return

        if len(parts) < 3:
            print(f"Usage: condition {sub} <name> <condition>")
            return
        condition = parts[2]

        if sub == "add":
            message = combatant.add_condition(condition)
            print(f"[+] {message}")
            self.log.log_entry(f"[condition add] {message}")
            self.refresh_views()
        elif sub == "remove":
            message = combatant.remove_condition(condition)
            print(f"[+] {message}")
            self.log.log_entry(f"[condition remove] {message}")
            self.refresh_views()
        else:
            print(f"[!] Unknown sub-command '{sub}'. Use add, remove, or list.")

    # ── page ──────────────────────────────────────────────────────────────

    def do_page(self, arg: str) -> None:
        text = arg.strip().lower()
        if text == "next":
            self.services.send(self.name, "page_next")
        elif text == "prev":
            self.services.send(self.name, "page_prev")
        else:
            try:
                number = int(text)
            except ValueError:
                print("Usage: page next | page prev | page <number>")
                return
            self.services.send(self.name, "page_set", number - 1)  # to 0-based

    # ── Completion ────────────────────────────────────────────────────────

    def _names(self, text: str) -> list[str]:
        return [c.name for c in self.combat.combatants
                if c.name.lower().startswith(text.lower())]

    def complete_combat(self, text, line, begidx, endidx) -> list[str]:
        parts = get_arg_parts(line[:begidx])
        top_subs = ["new", "add", "start", "status", "end", "show", "screen",
                    "noreaction", "reset", "legendary", "action", "log",
                    "export", "import", "image", "remove"]

        if len(parts) == 1:
            return [s for s in top_subs if s.startswith(text)]

        sub = parts[1].lower()

        if sub in ("import", "export"):
            if len(parts) == 2:
                pattern = os.path.join(str(COMBATANTS_DIR), text + "*.json")
                return [os.path.splitext(os.path.basename(m))[0]
                        for m in glob.glob(pattern)]
            return []

        if sub == "remove":
            return self._names(text) if len(parts) == 2 else []

        if sub == "image":
            if len(parts) == 2:
                return self._names(text)
            if len(parts) == 3:
                clean = line.split()[-1] if not line.endswith(" ") else ""
                return tab_completion(clean, list(Image.registered_extensions()),
                                      CURRENT_OS, "combatant_image")
            return []

        if sub != "action":
            return [s for s in top_subs if s.startswith(text)]

        if len(parts) == 2:
            return self._names(text)
        if len(parts) == 3:
            return [a for a in ACTION_TYPES if a.startswith(text)]
        if len(parts) == 4:
            return self._names(text)
        if len(parts) == 5 and parts[3].lower() == "remove_condition":
            target = self.combat.get(parts[4])
            if target:
                return [c for c in target.conditions
                        if c.lower().startswith(text.lower())]
        return []

    def complete_hp(self, text, line, begidx, endidx) -> list[str]:
        return self._names(text)

    def complete_maxhp(self, text, line, begidx, endidx) -> list[str]:
        return self._names(text)

    def complete_next(self, text, line, begidx, endidx) -> list[str]:
        return []

    def complete_page(self, text, line, begidx, endidx) -> list[str]:
        return [o for o in ("next", "prev") if o.startswith(text)]

    def complete_condition(self, text, line, begidx, endidx) -> list[str]:
        parts = get_arg_parts(line[:begidx])
        if len(parts) == 1:
            return [s for s in ("add", "remove", "list") if s.startswith(text)]
        if len(parts) == 2:
            return self._names(text)
        if len(parts) == 3 and parts[1].lower() == "remove":
            combatant = self.combat.get(parts[2])
            if combatant:
                return [c for c in combatant.conditions
                        if c.lower().startswith(text.lower())]
        return []

    def complete_resource(self, text, line, begidx, endidx) -> list[str]:
        parts = get_arg_parts(line[:begidx])

        if len(parts) == 1:
            subs = ["add", "reset", "list"] + [c.name for c in self.combat.combatants]
            return [s for s in subs if s.lower().startswith(text.lower())]

        if len(parts) == 2:
            if parts[1].lower() in ("add", "reset", "list"):
                return self._names(text)
            combatant = self.combat.get(parts[1])
            if combatant:
                return [k for k in combatant.resources if k.startswith(text.lower())]

        if len(parts) == 3 and parts[1].lower() != "add":
            combatant = self.combat.get(parts[2])
            if combatant:
                return [k for k in combatant.resources if k.startswith(text.lower())]

        return []
