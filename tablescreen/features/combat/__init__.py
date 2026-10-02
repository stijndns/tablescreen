"""
combat — D&D 5e initiative tracker and combat screen.

Owns the combat model, the combat log, and one or more CombatViews. Draws
into a content slot on whichever window config assigns it; sharing that
window with audiowindow reproduces the old behaviour where the tracker
replaced the image, without either feature knowing about the other.

Threading, as everywhere:

  * ``do_*`` handlers (in commands.py) run on the SHELL thread. They mutate
    the model and log, then call ``refresh_views()``, which only *sends* a
    message.
  * ``_on_message`` runs on the MAINLOOP thread and does the drawing.

Paging currently applies to the primary view (``views[0]``). If a mirror is
added later, paging needs an explicit target — see the note in _on_message.
"""

from __future__ import annotations

from ...core.feature import FeatureBase, ShellServices
from .commands import CombatCommandsMixin
from .log import CombatLog
from .model import Combat
from .views.combat_view import CombatView


class CombatFeature(CombatCommandsMixin, FeatureBase):
    """The combat tracker as a pluggable feature."""

    name = "combat"

    # Only attribute assignment here: this is constructed at import time,
    # before the Tk root exists.
    def __init__(self) -> None:
        super().__init__()
        self.combat = Combat()
        self.log = CombatLog()
        self.slot = None
        self._next_no_reaction = False

    # ── Build ─────────────────────────────────────────────────────────────

    def build(self, services: ShellServices) -> None:
        self.services = services

        window_name = services.window_name()
        self.slot = services.slot(window_name)
        view = CombatView(self.slot.frame)
        view.pack(fill="both", expand=True)
        self.views.append(view)

        # Re-layout the tracker when the window is resized or fullscreened.
        services.windows.get_window(window_name).on_geometry_change(
            lambda: self._resize_views())

        services.register_command(
            "combat", self.do_combat, self.complete_combat,
            help_text="combat <sub-command> — tracker control. Type 'combat help'.")
        services.register_command(
            "next", self.do_next, self.complete_next,
            help_text="next — advance to the next combatant's turn.")
        services.register_command(
            "hp", self.do_hp, self.complete_hp,
            help_text="hp <name> <±amount> | hp <name> = <amount> — adjust HP.")
        services.register_command(
            "maxhp", self.do_maxhp, self.complete_maxhp,
            help_text="maxhp <name> <new_max> — change maximum HP.")
        services.register_command(
            "resource", self.do_resource, self.complete_resource,
            help_text="resource add|reset|list|<name> … — manage resources.")
        services.register_command(
            "condition", self.do_condition, self.complete_condition,
            help_text="condition add|remove|list <name> [condition].")
        services.register_command(
            "page", self.do_page, self.complete_page,
            help_text="page next | page prev | page <number> — navigate pages.")

        services.subscribe(self._on_message)

    def snapshot(self) -> dict:
        return self.combat.snapshot()

    # ── Called from commands (shell thread) ───────────────────────────────

    def refresh_views(self, page: int | None = None) -> None:
        """Ask the mainloop to redraw. Safe from the shell thread."""
        self.services.send(self.name, "update", page)

    def show_combat_view(self) -> None:
        self.services.send(self.name, "show")

    def hide_combat_view(self) -> None:
        self.services.send(self.name, "hide")

    # ── Bus consumer (mainloop thread) ────────────────────────────────────

    def _on_message(self, message) -> None:
        command = message.command

        if command == "show":
            self.slot.show()       # takes the screen from any sibling slot
            self._render(page=0)

        elif command == "hide":
            self.slot.hide()

        elif command == "update":
            self._render(page=message.arg)

        # Paging targets the primary view. With a mirror this would need an
        # explicit target so the DM could page independently.
        elif command == "page_next":
            if self.views:
                self.views[0].page_next()
        elif command == "page_prev":
            if self.views:
                self.views[0].page_prev()
        elif command == "page_set":
            if self.views:
                self.views[0].set_page(message.arg)

    def _render(self, page: int | None = None) -> None:
        snapshot = self.snapshot()
        for view in self.views:
            try:
                view.render(snapshot, page)
            except Exception as exc:
                print(f"[!] combat: view render failed: {exc}")

    def _resize_views(self) -> None:
        for view in self.views:
            try:
                view._redraw((view.winfo_width(), view.winfo_height()))
            except Exception as exc:
                print(f"[!] combat: view resize failed: {exc}")


FEATURE = CombatFeature()
