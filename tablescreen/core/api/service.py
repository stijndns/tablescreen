"""
service.py — The API as an input source in core.

Features describe what players may see and do (``publish_state``,
``register_action`` on ShellServices); this service decides what leaves the
app and what comes in:

  * After every command, each shared feature's public state is taken on the
    executor thread and sent to the gateway when it changed. Features don't
    have to report changes: only commands change state, so nothing is missed.
  * An action from the gateway is checked (known, shared, allowed for the
    role, fields valid), then the feature's handler turns it into a command
    line on the executor, run with source ``api:<user>`` and echoed in the
    console like mouse input. The reply carries what the command printed.
  * ``api user …`` lists the gateway's accounts and links characters to
    them. Creating accounts and passwords is the gateway's business only:
    no password ever reaches the app.
"""

from __future__ import annotations

import json
import secrets
import shlex
import threading
from concurrent.futures import Future, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from ..completion import get_arg_parts
from ..executor import CommandExecutor
from ..paths import PROJECT_ROOT
from . import protocol
from .actions import PERMISSIONS, Action, Caller, Field
from .bridge import Bridge, Connection

DEFAULT_PORT = 8765
DEFAULT_SECRET_FILE = "api/bridge.secret"
ACCOUNTS_TIMEOUT = 5.0         # seconds to wait for the gateway's answer

API_HELP = """\
API commands (the player webapp's link to the app):
  api                                       — status: bridge, gateway, shared tabs
  api user list                             — the gateway's accounts
  api user link <name> <character>          — the character a player acts as
  api user unlink <name>                    — no character
Accounts are created and managed on the gateway's side, not here.
"""


@dataclass
class ApiConfig:
    enabled: bool = False
    share: list[str] = field(default_factory=list)
    bridge_host: str = "127.0.0.1"
    bridge_port: int = DEFAULT_PORT
    secret_file: Path = PROJECT_ROOT / DEFAULT_SECRET_FILE

    @classmethod
    def from_config(cls, config: dict) -> "ApiConfig":
        """The [api] table; invalid values are warned about and replaced
        by the default, like the rest of the config."""
        section = config.get("api", {})
        result = cls()
        if not isinstance(section, dict):
            print("[!] [api] must be a table; the API is off.")
            return result

        def setting(key, check, default, expected):
            value = section.get(key, default)
            if check(value):
                return value
            print(f"[!] [api] {key} = {value!r} is invalid ({expected}); "
                  f"using {default!r}.")
            return default

        result.enabled = setting("enabled", lambda v: isinstance(v, bool), False,
                                 "true or false")
        result.share = list(setting(
            "share", lambda v: isinstance(v, list) and all(isinstance(s, str) for s in v),
            [], "a list of feature names"))
        result.bridge_host = setting("bridge_host", lambda v: isinstance(v, str) and v,
                                     "127.0.0.1", "an address")
        result.bridge_port = setting(
            "bridge_port", lambda v: isinstance(v, int) and not isinstance(v, bool)
            and 0 <= v <= 65535, DEFAULT_PORT, "a port number")
        secret_file = setting("secret_file", lambda v: isinstance(v, str) and v,
                              DEFAULT_SECRET_FILE, "a file path")
        result.secret_file = PROJECT_ROOT / secret_file     # absolute paths stay absolute
        return result


def read_or_create_secret(path: Path) -> str:
    """The shared secret; created (random) on first use. The gateway reads
    the same file."""
    if path.exists():
        secret = path.read_text(encoding="utf-8").strip()
        if secret:
            return secret
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = protocol.new_secret()
    path.write_text(secret + "\n", encoding="utf-8")
    print(f"[+] api: created the bridge secret in {path}.")
    return secret


@dataclass
class _Published:
    snapshot: Callable[[], Any]
    schema: str
    version: int = 0
    last: Optional[str] = None      # JSON of the last state sent


class ApiService:
    """Owned by the Application; one per app."""

    def __init__(self, config: ApiConfig, executor: CommandExecutor):
        self.config = config
        self._executor = executor
        self._published: dict[str, _Published] = {}
        self._actions: dict[tuple[str, str], Action] = {}
        self._bridge: Optional[Bridge] = None
        self._pending: dict[str, Future] = {}       # account requests by id
        self._pending_lock = threading.Lock()

    # ── Registration (features, at build time) ────────────────────────────

    def publish_state(self, feature: str, snapshot: Callable[[], Any], schema: str) -> None:
        if not isinstance(schema, str) or "/" not in schema:
            raise ValueError(f"schema must look like 'combat/1', not {schema!r}")
        self._published[feature] = _Published(snapshot, schema)

    def register_action(self, feature: str, name: str, fields: dict[str, Field],
                        permission: str, handler: Callable,
                        reply: Optional[Callable[[str], str]] = None) -> None:
        if not protocol.NAME_RE.fullmatch(name):
            raise ValueError(f"invalid action name {name!r}")
        if permission not in PERMISSIONS:
            raise ValueError(f"permission must be one of {PERMISSIONS}")
        for field_name in fields:
            if not protocol.NAME_RE.fullmatch(field_name):
                raise ValueError(f"invalid field name {field_name!r}")
        self._actions[(feature, name)] = Action(feature, name, dict(fields),
                                                permission, handler, reply)

    def shared(self) -> list[str]:
        """Shared features that publish a state, in config order."""
        return [name for name in self.config.share if name in self._published]

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> None:
        if not self.config.enabled:
            return
        for name in self.config.share:
            if name not in self._published:
                print(f"[!] api: '{name}' is in [api] share but publishes nothing "
                      f"(not enabled, or no API support).")
        secret = read_or_create_secret(self.config.secret_file)
        self._bridge = Bridge(self.config.bridge_host, self.config.bridge_port, secret,
                              on_ready=self._on_ready, on_message=self._on_message,
                              on_lost=self._on_lost)
        try:
            self._bridge.start()
        except OSError as exc:
            print(f"[!] api: can't listen on {self._bridge.address}: {exc}")
            self._bridge = None
            return
        self._executor.after_each(self._publish_changes)
        print(f"[+] api: waiting for the gateway on {self._bridge.address}; "
              f"sharing {', '.join(self.shared()) or 'nothing'}.")

    def stop(self) -> None:
        if self._bridge:
            self._bridge.stop()

    @property
    def bridge(self) -> Optional[Bridge]:
        return self._bridge

    # ── States out (executor thread) ──────────────────────────────────────

    def _publish_changes(self, force: bool = False,
                         to: Optional[Connection] = None) -> None:
        """Send the states that changed (all with ``force``) to the current
        gateway, or to ``to``."""
        if to is None and not (self._bridge and self._bridge.connected):
            return
        send = to.send if to else self._bridge.send
        for name in self.shared():
            published = self._published[name]
            try:
                data = published.snapshot()
                text = json.dumps(data, sort_keys=True, ensure_ascii=False)
            except Exception as exc:
                print(f"[!] api: {name}'s public state failed: {exc}")
                continue
            if text == published.last and not force:
                continue
            published.last = text
            published.version += 1
            send({"type": "state", "feature": name,
                               "schema": published.schema,
                               "version": published.version, "data": data})

    # ── Bridge callbacks (bridge reader thread) ───────────────────────────

    def _on_ready(self, connection: Connection) -> None:
        connection.send({"type": "ready", "protocol": protocol.PROTOCOL,
                         "features": self._describe()})
        # Every state again, taken on the executor (consistent) and queued
        # behind "ready" on this connection.
        self._executor.call(lambda: self._publish_changes(force=True, to=connection))

    def _describe(self) -> list[dict]:
        features = []
        for name in self.shared():
            actions = [{
                "name": a.name,
                "permission": a.permission,
                "fields": {fname: {"kind": "int" if f.kind is int else "text",
                                   "required": f.required,
                                   **({"choices": list(f.choices)} if f.choices else {}),
                                   **({"minimum": f.minimum, "maximum": f.maximum}
                                      if f.kind is int else {"max_length": f.max_length})}
                           for fname, f in a.fields.items()},
            } for (feature, _), a in self._actions.items() if feature == name]
            features.append({"name": name, "schema": self._published[name].schema,
                             "actions": actions})
        return features

    def _on_lost(self) -> None:
        with self._pending_lock:
            pending, self._pending = self._pending, {}
        for future in pending.values():
            future.set_exception(ConnectionError("the gateway disconnected"))

    def _on_message(self, message: dict) -> None:
        if message["type"] == "action":
            self._on_action(message)
        elif message["type"] == "accounts_result":
            with self._pending_lock:
                future = self._pending.pop(message["id"], None)
            if future is not None:
                future.set_result(message)

    def _on_action(self, message: dict) -> None:
        caller = Caller(**message["caller"])
        source = f"api:{caller.user}"

        def reply(ok: bool, output: str = "", error: str = "") -> None:
            if self._bridge:
                self._bridge.send({"type": "result", "id": message["id"], "ok": ok,
                                   "output": output, "error": error})

        action = self._actions.get((message["feature"], message["action"]))
        if action is None or action.feature not in self.shared():
            reply(False, error="Unknown action.")
            return
        if not action.allows(caller):
            reply(False, error="Not allowed.")
            return
        try:
            args = action.check_args(message["args"])
        except ValueError as exc:
            reply(False, error=str(exc))
            return

        def run() -> str:
            line = action.handler(args, caller)     # ValueError: refused
            self._executor.run_line(line)
            return line

        def done(future: Future) -> None:
            try:
                result = future.result()
            except ValueError as exc:
                print(f"[{source}] {action.feature} {action.name} refused: {exc}")
                reply(False, error=str(exc))
                return
            except Exception as exc:
                print(f"[!] api: {action.feature} {action.name} failed: {exc!r}")
                reply(False, error="The app couldn't do that.")
                return
            # Without the console echo. A command that refuses says so on its
            # first line; later "[!]" lines are notices (e.g. for the DM).
            output = result.output.removeprefix(f"[{source}] {result.value}\n").strip()
            first = output.split("\n", 1)[0]
            if action.reply and not caller.is_dm:
                output = action.reply(output)       # the console's text is the DM's
            reply(not first.startswith(("[!]", "Usage")), output=output)

        self._executor.call(run, source=source, capture=True).add_done_callback(done)

    # ── Accounts (executor thread) ────────────────────────────────────────

    def _accounts(self, op: str, **args) -> Optional[dict]:
        """Ask the gateway and wait for its answer; None (and a message)
        when it can't be asked."""
        if not (self._bridge and self._bridge.connected):
            print("[!] The gateway isn't connected." if self._bridge
                  else "[!] The API is off (enable it in config.toml [api]).")
            return None
        request_id = secrets.token_hex(8)
        future: Future = Future()
        with self._pending_lock:
            self._pending[request_id] = future
        self._bridge.send({"type": "accounts", "id": request_id, "op": op, "args": args})
        try:
            return future.result(timeout=ACCOUNTS_TIMEOUT)
        except FutureTimeout:
            print("[!] The gateway didn't answer.")
        except ConnectionError as exc:
            print(f"[!] {exc}.")
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)
        return None

    def do_api(self, arg: str) -> None:
        try:
            parts = get_arg_parts(arg)
        except ValueError as exc:
            print(f"[!] {exc}")
            return
        if not parts or parts == ["status"]:
            self._print_status()
        elif parts[0] == "help":
            print(API_HELP)
        elif parts[0] == "user" and len(parts) >= 2:
            self._cmd_user(parts[1].lower(), parts[2:])
        else:
            print(API_HELP)

    def _print_status(self) -> None:
        if not self.config.enabled:
            print("[i] The API is off (enable it in config.toml [api]).")
            return
        if self._bridge is None:
            print("[!] The API is on but the bridge isn't listening (see the start-up message).")
            return
        gateway = self._bridge.gateway_address
        print(f"[i] Bridge on {self._bridge.address}; gateway "
              f"{'connected (' + gateway + ')' if gateway else 'not connected'}.")
        for name in self.shared():
            published = self._published[name]
            actions = [a for (f, a) in self._actions if f == name]
            print(f"    {name}: {published.schema}, version {published.version}, "
                  f"actions: {', '.join(actions) or 'none'}")

    def _cmd_user(self, op: str, parts: list[str]) -> None:
        def name_ok(name: str) -> bool:
            if protocol.USER_RE.fullmatch(name):
                return True
            print("[!] Account names are 1 to 32 letters, digits, - or _.")
            return False

        def character_ok(character: str) -> bool:
            try:
                protocol.clean_text(character, "character")
                return True
            except protocol.ProtocolError as exc:
                print(f"[!] {exc}.")
                return False

        if op == "list" and not parts:
            answer = self._accounts("list")
            if answer:
                users = answer.get("users") or []
                if not users:
                    print("[i] No accounts.")
                for u in users:
                    print(f"    {u['user']:<20} {u['role']:<7} {u['character'] or '-'}")
        elif op == "unlink" and len(parts) == 1:
            if name_ok(parts[0]):
                self._print_answer(self._accounts("link", user=parts[0], character=None))
        elif op == "link" and len(parts) == 2:
            if name_ok(parts[0]) and character_ok(parts[1]):
                self._print_answer(self._accounts("link", user=parts[0],
                                                  character=parts[1]))
        else:
            print(API_HELP)

    @staticmethod
    def _print_answer(answer: Optional[dict]) -> None:
        if answer:
            print(f"[{'+' if answer['ok'] else '!'}] {answer['message']}")

    def complete_api(self, text, line, begidx, endidx) -> list[str]:
        parts = get_arg_parts(line[:begidx])
        if len(parts) == 1:
            options = ["status", "user", "help"]
        elif len(parts) == 2 and parts[1] == "user":
            options = ["list", "link", "unlink"]
        else:
            options = []
        return [o for o in options if o.startswith(text)]


def quote(value: str) -> str:
    """For handlers building command lines: one shell word, whatever the
    text (spaces, quotes)."""
    return shlex.quote(value)
