"""
protocol.py — The bridge's wire format, shared by the app and the gateway.

One JSON object per line (UTF-8, ``\\n``), at most MAX_LINE bytes. The app
listens; the gateway connects and proves it knows the shared secret:

    app:     {"type": "hello", "protocol": 1, "nonce": "<hex>"}
    gateway: {"type": "auth", "mac": hmac_sha256(secret, nonce)}
    app:     {"type": "ready", "features": [...]}   then every state

The secret itself never crosses the wire. After that the app sends
``state``, ``result`` and ``accounts`` (list them, link a character); the
gateway sends ``action`` and ``accounts_result``. No password ever crosses
the bridge: accounts are managed on the gateway's side only. Everything the gateway sends is untrusted: ``check()``
validates each message's shape before anything looks at it.

No imports from the rest of the app, so the gateway can use this file too.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from typing import Any

PROTOCOL = 1
MAX_LINE = 1 << 20              # bytes per message
NONCE_BYTES = 32

ROLES = ("guest", "player", "dm")
USER_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")
NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")       # feature, action, field names
ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
MAX_TEXT = 100                  # characters in a string field


class ProtocolError(ValueError):
    """A message that breaks the protocol; the connection is dropped."""


# ── Framing ─────────────────────────────────────────────────────────────

def encode(message: dict) -> bytes:
    data = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"
    if len(data) > MAX_LINE:
        raise ProtocolError(f"message too long ({len(data)} bytes)")
    return data


class LineReader:
    """Splits a byte stream into messages; refuses over-long lines."""

    def __init__(self) -> None:
        self._buffer = b""

    def feed(self, data: bytes) -> list[dict]:
        self._buffer += data
        messages = []
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            if len(line) > MAX_LINE:
                raise ProtocolError("message too long")
            messages.append(decode(line))
        if len(self._buffer) > MAX_LINE:
            raise ProtocolError("message too long")
        return messages


def decode(line: bytes) -> dict:
    try:
        message = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProtocolError(f"not JSON: {exc}") from None
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        raise ProtocolError("a message is an object with a 'type'")
    return message


# ── Authentication ──────────────────────────────────────────────────────

def new_nonce() -> str:
    return secrets.token_hex(NONCE_BYTES)


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def mac(secret: str, nonce: str) -> str:
    return hmac.new(secret.encode(), nonce.encode(), hashlib.sha256).hexdigest()


def mac_matches(secret: str, nonce: str, answer: Any) -> bool:
    return isinstance(answer, str) and hmac.compare_digest(mac(secret, nonce), answer)


# ── Validation of what the gateway sends ────────────────────────────────

def clean_text(value: Any, what: str, max_length: int = MAX_TEXT) -> str:
    """A short single-line string: no control characters (a newline would
    end a command line), at most ``max_length`` characters."""
    if not isinstance(value, str):
        raise ProtocolError(f"{what} must be text")
    if not 0 < len(value) <= max_length:
        raise ProtocolError(f"{what} must be 1 to {max_length} characters")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ProtocolError(f"{what} contains control characters")
    return value


def _match(pattern: re.Pattern, value: Any, what: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ProtocolError(f"invalid {what}")
    return value


def check_caller(message: dict) -> dict:
    """The user an action comes from, as the gateway vouches for it."""
    caller = message.get("caller")
    if not isinstance(caller, dict):
        raise ProtocolError("missing caller")
    role = caller.get("role")
    if role not in ROLES:
        raise ProtocolError("invalid role")
    character = caller.get("character")
    return {
        "user": _match(USER_RE, caller.get("user"), "user"),
        "role": role,
        "character": None if character is None else clean_text(character, "character"),
    }


def check(message: dict) -> dict:
    """Validate a message from the gateway (after authentication). Returns
    it with only the known fields; raises ProtocolError otherwise."""
    kind = message.get("type")
    if kind == "action":
        args = message.get("args", {})
        if not isinstance(args, dict) or len(args) > 16:
            raise ProtocolError("args must be an object (at most 16 fields)")
        for key in args:
            _match(NAME_RE, key, "field name")
        return {
            "type": kind,
            "id": _match(ID_RE, message.get("id"), "id"),
            "feature": _match(NAME_RE, message.get("feature"), "feature"),
            "action": _match(NAME_RE, message.get("action"), "action"),
            "caller": check_caller(message),
            "args": args,           # values are checked against the action's fields
        }
    if kind == "accounts_result":
        result = {
            "type": kind,
            "id": _match(ID_RE, message.get("id"), "id"),
            "ok": message.get("ok") is True,
            "message": clean_text(message.get("message", "-"), "message", 1000),
        }
        users = message.get("users")
        if users is not None:
            if not isinstance(users, list) or len(users) > 500:
                raise ProtocolError("users must be a list")
            result["users"] = [check_caller({"caller": u}) for u in users]
        return result
    raise ProtocolError(f"unknown message type {kind!r}")
