from datetime import datetime

from ...core.paths import LOGS_DIR, ensure_dir

class CombatLog:
    """ ── Combat log helpers ───────────────────────────────────────────────────"""
    def __init__(self):
        self.entries: list[str] = []
        self.saved: bool = False

    def log_entry(self, entry: str):
        """Append an entry to the combat log and mark log as unsaved."""
        self.entries.append(entry)
        self.saved = False

    def log_turn_marker(self, combatant_name: str, round_num: int, new_round: bool = False):
        """Append a turn marker, optionally with a round header."""
        if new_round:
            self.entries.append(f"{'─'*60}")
            self.entries.append(f"--- Round {round_num} begins ---")
        self.entries.append(f"--- Round {round_num}: {combatant_name}'s turn ---")
        self.saved = False

    def reset(self, status: list[str]|None=None):
        """Clear the log, optionally seeding with current combat status."""
        self.entries = []
        self.saved = False
        if status is not None:
            self.entries.append("═" * 60)
            self.entries.append("COMBAT START")
            self.entries.append("═" * 60)
            for line in status:
                self.entries.append(line)
            self.entries.append("═" * 60)

    def check_unsaved_log(self) -> bool:
        """Warn if log has unsaved entries. Returns True if safe to proceed."""
        if not self.entries or self.saved:
            return True
        print("[!] Combat log has unsaved entries.")
        while True:
            ans = input("    Discard log and continue? (Y/N): ").strip().upper()
            if ans == "Y":
                return True
            if ans == "N":
                return False

    def save(self, parts: list[str]):
        def entry_lines():
            for entry in self.entries:
                yield entry
                yield "\n"

        ensure_dir(LOGS_DIR)
        if len(parts) >= 2:
            filename = parts[1]
            if not filename.endswith(".txt"):
                filename += ".txt"
        else:
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            filename = f"combat_log_{timestamp}.txt"

        filepath = LOGS_DIR / filename
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.writelines(entry_lines())
            self.saved = True
            print(f"[+] Combat log saved to {filepath}")
        except Exception as e:
            print(f"[!] Could not save log: {e}")

    def print(self):
        if not self.entries:
            print("[i] Combat log is empty.")
        else:
            print()
            for line in self.entries:
                print(line)
            print()