"""
hub.py — The webapp's live connections: one WebSocket per open page.

Every page gets ``hello`` (who it is, whether the table is connected, the
tabs and where their views are) and each feature's ``state`` when it
connects, and again whenever something changes:

  * a new state from the app → every page;
  * the app connecting or dropping, a character linked or unlinked → a new
    ``hello``;
  * a session ending (logout, the password changed on another device) →
    that page's socket closes with CLOSE_LOGIN and the page goes to login.

Changes made outside the gateway (``prepare_accounts.py --reset``, a
hand-edited database) are found by checking every open session each
CHECK_EVERY seconds.

A slow page holds nothing up: each keeps only the latest unsent message of
each kind, and its own task sends them.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import WebSocket

from .accounts import Account, AccountStore

CHECK_EVERY = 30.0          # seconds between checks of the open sessions
CLOSE_LOGIN = 4401          # the session ended: go to the login page
CLOSE_PASSWORD = 4403       # still the initial password: go and change it
VIEW_MODES = ("auto", "webapp", "feature", "generic")

log = logging.getLogger("gateway")


class Page:
    """One open webapp page."""

    def __init__(self, websocket: WebSocket, token: str, account: Optional[Account]):
        self.websocket = websocket
        self.token = token                  # "" for a guest
        self.account = account
        self._pending: dict[str, dict] = {}     # the latest unsent message per kind
        self._wake = asyncio.Event()
        self._close_code: Optional[int] = None
        self.task: Optional[asyncio.Task] = None

    def push(self, key: str, message: dict) -> None:
        self._pending.pop(key, None)
        self._pending[key] = message
        self._wake.set()

    def close(self, code: int) -> None:
        self._close_code = code
        self._wake.set()

    async def send_loop(self) -> None:
        try:
            while True:
                await self._wake.wait()
                self._wake.clear()
                while self._pending and self._close_code is None:
                    key = next(iter(self._pending))
                    message = self._pending.pop(key)
                    await self.websocket.send_text(json.dumps(message, ensure_ascii=False))
                if self._close_code is not None:
                    await self.websocket.close(self._close_code)
                    return
        except Exception:
            return              # the page went away; the endpoint cleans up


class Hub:
    def __init__(self, accounts: AccountStore, bridge: Any, overrides: Path):
        """``bridge`` is the BridgeClient (features, views, states,
        connected); ``overrides`` the folder of the webapp's own views."""
        self.accounts = accounts
        self.bridge = bridge
        self.overrides = overrides
        self.pages: set[Page] = set()
        bridge.on_event = self.on_bridge_event

    # ── Pages ─────────────────────────────────────────────────────────────

    def join(self, websocket: WebSocket, token: str, account: Optional[Account]) -> Page:
        page = Page(websocket, token, account)
        self.pages.add(page)
        page.push("hello", self.hello(page))
        for feature in list(self.bridge.states):
            page.push(f"state:{feature}", self.state(feature))
        page.task = asyncio.create_task(page.send_loop())
        return page

    def leave(self, page: Page) -> None:
        self.pages.discard(page)
        if page.task:
            page.task.cancel()

    def hello(self, page: Page) -> dict:
        account = page.account
        return {"type": "hello",
                "user": account.user if account else None,
                "role": account.role if account else "guest",
                "character": account.character if account else None,
                "app_connected": self.bridge.connected,
                "tabs": self.tabs()}

    def tabs(self) -> list[dict]:
        """One per shared feature, with the views a page may load."""
        tabs = []
        for feature in self.bridge.features:
            name = feature["name"]
            webapp = self.overrides / f"{name}.js"
            mode = feature.get("view")
            tabs.append({
                "name": name,
                "schema": feature.get("schema"),
                "view": mode if mode in VIEW_MODES else "auto",
                "views": {
                    "webapp": f"/views/webapp/{name}.js" if webapp.is_file() else None,
                    "feature": (f"/views/feature/{name}.js?v={self.bridge.view_version(name)}"
                                if name in self.bridge.views else None),
                },
            })
        return tabs

    def state(self, feature: str) -> dict:
        return {"type": "state", "feature": feature, **self.bridge.states[feature]}

    def _hello_all(self, pages=None) -> None:
        for page in list(pages if pages is not None else self.pages):
            page.push("hello", self.hello(page))

    # ── What happens elsewhere ────────────────────────────────────────────

    def on_bridge_event(self, kind: str, detail: Optional[str]) -> None:
        if kind == "status":
            self._hello_all()
        elif kind == "state" and detail in self.bridge.states:
            message = self.state(detail)
            for page in list(self.pages):
                page.push(f"state:{detail}", message)
        elif kind == "account" and detail:
            self.account_changed(detail)

    def account_changed(self, user: str) -> None:
        """The account's pages hear its new details (a linked character)."""
        account = self.accounts.get(user)
        pages = [p for p in self.pages if p.account and p.account.user == user]
        for page in pages:
            if account is None:
                page.close(CLOSE_LOGIN)
            else:
                page.account = account
        self._hello_all([p for p in pages if account is not None])

    def session_ended(self, token: str) -> None:
        """Logout: that session's pages go to the login page."""
        for page in list(self.pages):
            if token and page.token == token:
                page.close(CLOSE_LOGIN)

    def password_changed(self, user: str, keep_token: str) -> None:
        """The user's other sessions ended; this one stays."""
        for page in list(self.pages):
            if page.account and page.account.user == user and page.token != keep_token:
                page.close(CLOSE_LOGIN)
        self.account_changed(user)

    async def watch(self) -> None:
        """Runs while the server does: notices sessions ended or changed
        outside the gateway."""
        while True:
            await asyncio.sleep(CHECK_EVERY)
            await self.check_sessions()

    async def check_sessions(self) -> None:
        for page in list(self.pages):
            if page.account is None:
                continue
            account = await asyncio.to_thread(self.accounts.session, page.token)
            if account is None:
                page.close(CLOSE_LOGIN)
            elif account.must_change:
                page.close(CLOSE_PASSWORD)
            elif account != page.account:
                page.account = account
                page.push("hello", self.hello(page))
