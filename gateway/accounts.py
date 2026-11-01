"""
accounts.py — Accounts and sessions in SQLite; passwords as argon2 hashes.

Accounts are made before a session (``tools/prepare_accounts.py``), each
with an initial password that the player replaces at their first login. The
app sees accounts only through the bridge (it lists them and links
characters); passwords and sessions never leave the gateway.

A session is a random token in the browser's cookie. Only its SHA-256 is
stored, so a copy of the database logs no one in. A session ends after
SESSION_IDLE seconds without use, at logout, or when the password is
changed on another device.

Every call opens its own connection, so any thread may use the store.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

import bridge_protocol as protocol

ROLES = ("player", "dm")
MIN_PASSWORD, MAX_PASSWORD = 8, 128     # the maximum keeps argon2's input small
SESSION_IDLE = 30 * 24 * 3600           # seconds without use before a session ends
TOUCH_EVERY = 3600                      # seconds between "last used" writes

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    user          TEXT PRIMARY KEY COLLATE NOCASE,
    role          TEXT NOT NULL CHECK (role IN ('player', 'dm')),
    character     TEXT,
    password_hash TEXT NOT NULL,
    must_change   INTEGER NOT NULL DEFAULT 1,
    created       REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash    TEXT PRIMARY KEY,
    user          TEXT NOT NULL REFERENCES accounts (user) ON DELETE CASCADE,
    created       REAL NOT NULL,
    last_used     REAL NOT NULL
);
"""


@dataclass(frozen=True)
class Account:
    user: str
    role: str
    character: Optional[str]
    must_change: bool               # still the initial password

    def public(self) -> dict:
        return {"user": self.user, "role": self.role, "character": self.character,
                "must_change": self.must_change}


def password_problem(password: str) -> Optional[str]:
    """Why a new password can't be used, or None."""
    if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        return f"A password has {MIN_PASSWORD} to {MAX_PASSWORD} characters."
    return None


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AccountStore:
    def __init__(self, path: Path, hasher: Optional[PasswordHasher] = None):
        self.path = Path(path)
        self._hasher = hasher or PasswordHasher()
        self._dummy_hash: Optional[str] = None

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _run(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with closing(self._connect()) as connection, connection:
            return connection.execute(sql, args).fetchall()

    def exists(self) -> bool:
        return self.path.exists()

    def create(self) -> None:
        """The tables (and folder), if they don't exist yet."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.executescript(SCHEMA)

    # ── Accounts ──────────────────────────────────────────────────────────

    def add(self, user: str, role: str, password: str) -> Account:
        """A new account with an initial password (to be changed at the
        first login). ValueError for a bad name, role or password, or a
        name that's taken."""
        if not protocol.USER_RE.fullmatch(user):
            raise ValueError("A name is 1 to 32 letters, digits, - or _.")
        if role not in ROLES:
            raise ValueError(f"A role is one of: {', '.join(ROLES)}.")
        problem = password_problem(password)
        if problem:
            raise ValueError(problem)
        if self.get(user):
            raise ValueError(f"There is already an account named {user}.")
        self._run("INSERT INTO accounts (user, role, character, password_hash, must_change,"
                  " created) VALUES (?, ?, NULL, ?, 1, ?)",
                  (user, role, self._hasher.hash(password), time.time()))
        return Account(user, role, None, True)

    def get(self, user: str) -> Optional[Account]:
        rows = self._run("SELECT user, role, character, must_change FROM accounts"
                         " WHERE user = ?", (user,))
        return self._account(rows[0]) if rows else None

    def list(self) -> list[Account]:
        rows = self._run("SELECT user, role, character, must_change FROM accounts"
                         " ORDER BY user")
        return [self._account(row) for row in rows]

    def link(self, user: str, character: Optional[str]) -> bool:
        """The character an account plays (None: none); False if there's
        no such account."""
        with closing(self._connect()) as connection, connection:
            changed = connection.execute("UPDATE accounts SET character = ? WHERE user = ?",
                                         (character, user)).rowcount
        return changed == 1

    @staticmethod
    def _account(row: sqlite3.Row) -> Account:
        return Account(row["user"], row["role"], row["character"], bool(row["must_change"]))

    # ── Passwords ─────────────────────────────────────────────────────────

    def check_password(self, user: str, password: str) -> Optional[Account]:
        """The account if the password is right. An unknown name costs as
        much time as a wrong password, so names can't be guessed by timing."""
        rows = self._run("SELECT user, role, character, must_change, password_hash"
                         " FROM accounts WHERE user = ?", (user,))
        if not rows:
            if self._dummy_hash is None:
                self._dummy_hash = self._hasher.hash(secrets.token_hex(8))
            stored = self._dummy_hash
        else:
            stored = rows[0]["password_hash"]
        if len(password) > MAX_PASSWORD:
            return None
        try:
            self._hasher.verify(stored, password)
        except (VerificationError, InvalidHashError):
            return None
        if not rows:
            return None
        if self._hasher.check_needs_rehash(stored):
            self._run("UPDATE accounts SET password_hash = ? WHERE user = ?",
                      (self._hasher.hash(password), rows[0]["user"]))
        return self._account(rows[0])

    def set_password(self, user: str, password: str, keep_token: Optional[str] = None) -> None:
        """The player's own new password. Ends the account's other
        sessions (``keep_token`` is the one in use)."""
        problem = password_problem(password)
        if problem:
            raise ValueError(problem)
        keep = _token_hash(keep_token) if keep_token else ""
        with closing(self._connect()) as connection, connection:
            connection.execute("UPDATE accounts SET password_hash = ?, must_change = 0"
                               " WHERE user = ?", (self._hasher.hash(password), user))
            connection.execute("DELETE FROM sessions WHERE user = ? AND token_hash != ?",
                               (user, keep))

    def reset_password(self, user: str, password: str) -> None:
        """A new initial password (for a player who forgot theirs): to be
        changed at the next login; every session ends."""
        problem = password_problem(password)
        if problem:
            raise ValueError(problem)
        with closing(self._connect()) as connection, connection:
            changed = connection.execute(
                "UPDATE accounts SET password_hash = ?, must_change = 1 WHERE user = ?",
                (self._hasher.hash(password), user)).rowcount
            connection.execute("DELETE FROM sessions WHERE user = ?", (user,))
        if not changed:
            raise ValueError(f"There is no account named {user}.")

    # ── Sessions ──────────────────────────────────────────────────────────

    def new_session(self, user: str) -> str:
        """A new session's token (for the cookie only; never stored)."""
        token = secrets.token_urlsafe(32)
        now = time.time()
        self._run("INSERT INTO sessions (token_hash, user, created, last_used)"
                  " VALUES (?, ?, ?, ?)", (_token_hash(token), user, now, now))
        return token

    def session(self, token: str) -> Optional[Account]:
        """The account a session belongs to, or None (unknown or ended)."""
        if not token:
            return None
        now = time.time()
        rows = self._run(
            "SELECT a.user, a.role, a.character, a.must_change, s.last_used"
            " FROM sessions s JOIN accounts a ON a.user = s.user WHERE s.token_hash = ?",
            (_token_hash(token),))
        if not rows:
            return None
        if rows[0]["last_used"] < now - SESSION_IDLE:
            self.end_session(token)
            return None
        if rows[0]["last_used"] < now - TOUCH_EVERY:
            self._run("UPDATE sessions SET last_used = ? WHERE token_hash = ?",
                      (now, _token_hash(token)))
        return self._account(rows[0])

    def end_session(self, token: str) -> None:
        self._run("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))

    def purge_sessions(self) -> int:
        """Forget the sessions that ended by not being used."""
        with closing(self._connect()) as connection, connection:
            return connection.execute("DELETE FROM sessions WHERE last_used < ?",
                                      (time.time() - SESSION_IDLE,)).rowcount
