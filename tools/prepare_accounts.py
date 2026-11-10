"""
prepare_accounts.py — Prepare the gateway's accounts before a session.

    python tools/prepare_accounts.py alice:player bob:player dm:dm
    python tools/prepare_accounts.py --reset alice      # forgot their password

Asks for each account's initial password (not shown while typing, asked
twice) and stores only its argon2 hash. Players choose their own password
at their first login. ``--reset`` gives an existing account a new initial
password and logs it out everywhere.

Run from the repository root; the database is api/accounts.db unless
--db says otherwise. Characters are linked in the app (`api user link`).
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bridge_protocol import USER_RE  # noqa: E402
from gateway.accounts import ROLES, AccountStore, password_problem  # noqa: E402


def ask_password(user: str) -> str:
    while True:
        password = getpass.getpass(f"Initial password for {user}: ")
        problem = password_problem(password)
        if problem:
            print(problem)
        elif getpass.getpass("Again: ") != password:
            print("Those weren't the same.")
        else:
            return password


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    parser.add_argument("accounts", nargs="+", metavar="name:role | name",
                        help=f"new accounts (role: {' or '.join(ROLES)}); names with --reset")
    parser.add_argument("--reset", action="store_true",
                        help="give existing accounts a new initial password")
    parser.add_argument("--db", default=str(ROOT / "api" / "accounts.db"),
                        help="the accounts database (default: api/accounts.db)")
    args = parser.parse_args()

    store = AccountStore(Path(args.db))
    store.create()

    if args.reset:
        for user in args.accounts:
            if store.get(user) is None:
                sys.exit(f"There is no account named {user}.")
        for user in args.accounts:
            store.reset_password(store.get(user).user, ask_password(user))
            print(f"{user}: new initial password; logged out everywhere.")
        return

    wanted = []
    for spec in args.accounts:
        user, _, role = spec.partition(":")
        if role not in ROLES:
            sys.exit(f"{spec}: write name:role, with role {' or '.join(ROLES)}.")
        if not USER_RE.fullmatch(user):
            sys.exit(f"{user}: a name is 1 to 32 letters, digits, - or _.")
        if store.get(user) or user.lower() in (u.lower() for u, _ in wanted):
            sys.exit(f"There is already an account named {user} (--reset gives it a new"
                     f" initial password).")
        wanted.append((user, role))
    for user, role in wanted:
        try:
            store.add(user, role, ask_password(user))
        except ValueError as exc:
            sys.exit(f"{user}: {exc}")
        print(f"Added {user} ({role}).")
    print(f"Accounts in {store.path}: {', '.join(a.user for a in store.list())}."
          f" Each player changes their password at their first login.")


if __name__ == "__main__":
    main()
