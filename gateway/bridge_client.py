"""
bridge_client.py — The gateway's end of the bridge: it connects to the app.

The app listens; this client connects, answers the app's challenge with an
HMAC of its nonce (the secret never crosses the wire) and keeps the
connection, trying again every RETRY seconds while the app isn't running.

It keeps what the app sends for the webapp (the shared features and the
latest state of each) and answers the app's account requests (``api user
…`` in its console) from the accounts database.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

import bridge_protocol as protocol

from .accounts import AccountStore

RETRY = 2.0                 # seconds between connection attempts
HANDSHAKE_TIMEOUT = 5.0

log = logging.getLogger("gateway")


class BridgeClient:
    def __init__(self, host: str, port: int, secret_file: Path, accounts: AccountStore):
        self.host, self.port = host, port
        self.secret_file = Path(secret_file)
        self.accounts = accounts
        self.features: list[dict] = []          # from the app's "ready"
        self.states: dict[str, dict] = {}       # feature → {schema, version, data}
        self._writer: Optional[asyncio.StreamWriter] = None
        self._ready = False

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    @property
    def connected(self) -> bool:
        return self._ready and self._writer is not None

    async def run(self) -> None:
        """Connect, and connect again whenever the connection is lost.
        Runs until cancelled."""
        problem = None                  # logged once until it changes
        while True:
            try:
                secret = self.secret_file.read_text(encoding="utf-8").strip()
                if not secret:
                    raise FileNotFoundError
            except OSError:
                secret = None
                new_problem = (f"No bridge secret in {self.secret_file} yet: start Tablescreen"
                               f" with the API enabled once.")
            if secret:
                try:
                    reader, writer = await asyncio.open_connection(self.host, self.port)
                except OSError as exc:
                    new_problem = f"The app isn't reachable at {self.address} ({exc.strerror or exc})."
                else:
                    problem = None
                    await self._session(reader, writer, secret)
                    new_problem = None
            if new_problem and new_problem != problem:
                log.info("%s Trying again every %g s.", new_problem, RETRY)
            problem = new_problem
            await asyncio.sleep(RETRY)

    async def _session(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                       secret: str) -> None:
        lines = protocol.LineReader()
        pending: list[dict] = []
        try:
            hello = await asyncio.wait_for(self._first(reader, lines, pending),
                                           HANDSHAKE_TIMEOUT)
            if hello.get("type") != "hello" or not isinstance(hello.get("nonce"), str):
                raise protocol.ProtocolError("the app didn't say hello")
            if hello.get("protocol") != protocol.PROTOCOL:
                raise protocol.ProtocolError(
                    f"the app speaks bridge protocol {hello.get('protocol')!r},"
                    f" this gateway {protocol.PROTOCOL}: update one of them")
            self._writer = writer
            await self.send({"type": "auth", "mac": protocol.mac(secret, hello["nonce"])})
            for message in pending:
                await self._handle(message)
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                for message in lines.feed(data):
                    await self._handle(message)
                for error in lines.errors:
                    log.warning("Ignored a message from the app: %s", error)
                lines.errors.clear()
        except (OSError, asyncio.TimeoutError, protocol.ProtocolError) as exc:
            log.warning("Bridge: %s", exc or type(exc).__name__)
        finally:
            if self._ready:
                log.info("Lost the connection to the app.")
            elif self._writer is not None:
                log.warning("The app closed the connection before it was ready:"
                            " the bridge secret doesn't match the app's?")
            self._writer, self._ready = None, False
            self.features, self.states = [], {}
            writer.close()

    @staticmethod
    async def _first(reader: asyncio.StreamReader, lines: protocol.LineReader,
                     pending: list[dict]) -> dict:
        """The first message; any that came with it go to ``pending``."""
        while not pending:
            data = await reader.read(65536)
            if not data:
                raise ConnectionError("the app closed the connection")
            pending += lines.feed(data)
            if lines.errors:
                raise protocol.ProtocolError(lines.errors[0])
        return pending.pop(0)

    async def send(self, message: dict) -> bool:
        writer = self._writer
        if writer is None:
            return False
        writer.write(protocol.encode(message))
        await writer.drain()
        return True

    # ── What the app sends ────────────────────────────────────────────────

    async def _handle(self, message: dict) -> None:
        kind = message["type"]
        if kind == "ready":
            features = message.get("features")
            self.features = features if isinstance(features, list) else []
            self.states = {}
            self._ready = True
            names = [f.get("name") for f in self.features if isinstance(f, dict)]
            log.info("Connected to the app at %s; it shares %s.", self.address,
                     ", ".join(map(str, names)) or "nothing")
        elif kind == "state":
            feature = message.get("feature")
            if isinstance(feature, str):
                self.states[feature] = {"schema": message.get("schema"),
                                        "version": message.get("version"),
                                        "data": message.get("data")}
        elif kind == "accounts":
            answer = await asyncio.to_thread(self.answer_accounts, message)
            if answer:
                await self.send(answer)
        elif kind == "result":
            pass                        # answers to actions: step 5
        else:
            log.warning("Ignored a %r message from the app.", kind)

    def answer_accounts(self, request: dict) -> Optional[dict]:
        """``api user …`` from the app's console: list the accounts, or
        link a character to one (None unlinks)."""
        request_id = request.get("id")
        if not isinstance(request_id, str) or not protocol.ID_RE.fullmatch(request_id):
            return None
        answer = {"type": "accounts_result", "id": request_id, "ok": False}
        op, args = request.get("op"), request.get("args")
        args = args if isinstance(args, dict) else {}
        if op == "list":
            users = self.accounts.list()
            answer.update(ok=True, message=f"{len(users)} accounts",
                          users=[{"user": a.user, "role": a.role, "character": a.character}
                                 for a in users])
        elif op == "link":
            user, character = args.get("user"), args.get("character")
            try:
                if not isinstance(user, str):
                    raise protocol.ProtocolError("no account name")
                if character is not None:
                    protocol.clean_text(character, "character")
            except protocol.ProtocolError as exc:
                answer["message"] = f"Can't link: {exc}."
                return answer
            account = self.accounts.get(user)
            if account is None or not self.accounts.link(account.user, character):
                answer["message"] = f"There is no account named {user}."
            elif character is None:
                answer.update(ok=True, message=f"{account.user} plays no character now.")
            else:
                answer.update(ok=True, message=f"{account.user} now plays {character}.")
        else:
            answer["message"] = f"The gateway doesn't know the request {op!r}."
        return answer
