"""
web.py — The gateway's web side: the webapp's pages and its JSON API.

    GET  /, /login, /password       the webapp's pages (static files)
    GET  /api/me                    who this browser is (a guest without login)
    POST /api/login                 {user, password} → a session cookie
    POST /api/logout
    POST /api/password              {current, new}

A session is an HttpOnly, Secure, SameSite=Strict cookie, so scripts can't
read it and other sites can't send it. POSTs to /api/ must be JSON, which a
plain HTML form on another site can't send either. A player still on their
initial password can only change it (``must_change``).
"""

from __future__ import annotations

import asyncio
import logging
import math
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .accounts import SESSION_IDLE, Account, AccountStore, password_problem
from .throttle import Throttle

COOKIE = "tablescreen_session"
FAIL_DELAY = 0.5                # seconds added to every failed password check
WEBAPP = Path(__file__).resolve().parent / "webapp"

HEADERS = {
    # Only our own files; no inline scripts, no framing by other sites.
    "Content-Security-Policy": "default-src 'self'; base-uri 'none'; form-action 'self';"
                               " frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}
# No HSTS: with a self-signed certificate it would stop browsers from
# letting players accept the certificate warning.

log = logging.getLogger("gateway")


def create_app(accounts: AccountStore, bridge: Any, *, throttle: Optional[Throttle] = None,
               fail_delay: float = FAIL_DELAY,
               background: tuple[Callable[[], Awaitable[None]], ...] = ()) -> FastAPI:
    """``bridge`` tells whether the app is connected (``.connected``);
    ``background`` coroutines (the bridge client) run while the server does."""
    throttle = throttle or Throttle()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        tasks = [asyncio.create_task(job()) for job in background]
        yield
        for task in tasks:
            task.cancel()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if (request.method == "POST" and path.startswith("/api/")
                and request.headers.get("content-type", "").split(";")[0].strip()
                != "application/json"):
            response: Response = JSONResponse({"error": "Send JSON."}, status_code=415)
        else:
            response = await call_next(request)
        response.headers.update(HEADERS)
        response.headers["Cache-Control"] = "no-store" if path.startswith("/api/") else "no-cache"
        return response

    # ── Pages ─────────────────────────────────────────────────────────────

    def page(name: str):
        return lambda: FileResponse(WEBAPP / name, media_type="text/html")

    app.get("/", include_in_schema=False)(page("index.html"))
    app.get("/login", include_in_schema=False)(page("login.html"))
    app.get("/password", include_in_schema=False)(page("password.html"))
    app.mount("/static", StaticFiles(directory=WEBAPP / "static"), name="static")

    # ── Helpers ───────────────────────────────────────────────────────────

    def current(request: Request) -> tuple[Optional[Account], str]:
        token = request.cookies.get(COOKIE, "")
        return (accounts.session(token) if token else None), token

    def with_cookie(response: Response, token: str) -> Response:
        response.set_cookie(COOKIE, token, max_age=SESSION_IDLE, path="/",
                            httponly=True, secure=True, samesite="strict")
        return response

    def error(status: int, message: str) -> JSONResponse:
        return JSONResponse({"error": message}, status_code=status)

    async def json_body(request: Request, *keys: str) -> Optional[dict]:
        """The named text fields of a JSON object, or None."""
        try:
            data = await request.json()
        except ValueError:
            return None
        if not isinstance(data, dict) or not all(isinstance(data.get(k), str) for k in keys):
            return None
        return {k: data[k] for k in keys}

    async def wrong_password(name: str) -> JSONResponse:
        throttle.failed(name)
        await asyncio.sleep(fail_delay)
        return error(401, "Wrong name or password.")

    def locked(name: str) -> Optional[JSONResponse]:
        wait = throttle.wait(name)
        if wait:
            return error(429, f"Too many wrong passwords: try again in {math.ceil(wait)} s.")
        return None

    # ── API ───────────────────────────────────────────────────────────────

    @app.get("/api/me")
    async def me(request: Request) -> Response:
        account, token = current(request)
        if account is None:
            response = JSONResponse({"user": None, "role": "guest", "character": None,
                                     "must_change": False, "app_connected": bridge.connected})
            if token:
                response.delete_cookie(COOKIE, path="/")    # ended or unknown
            return response
        # The cookie's lifetime starts again, like the session's.
        return with_cookie(JSONResponse({**account.public(),
                                         "app_connected": bridge.connected}), token)

    @app.post("/api/login")
    async def login(request: Request) -> Response:
        data = await json_body(request, "user", "password")
        if data is None:
            return error(400, "Send a name and a password.")
        name = data["user"].strip()[:64]
        refused = locked(name)
        if refused:
            return refused
        account = await asyncio.to_thread(accounts.check_password, name, data["password"])
        if account is None:
            log.info("Failed login for %r.", name)
            return await wrong_password(name)
        throttle.succeeded(name)
        token = accounts.new_session(account.user)
        log.info("%s logged in.", account.user)
        return with_cookie(JSONResponse(account.public()), token)

    @app.post("/api/logout")
    async def logout(request: Request) -> Response:
        account, token = current(request)
        if token:
            accounts.end_session(token)
        if account:
            log.info("%s logged out.", account.user)
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/")
        return response

    @app.post("/api/password")
    async def change_password(request: Request) -> Response:
        account, token = current(request)
        if account is None:
            return error(401, "Log in first.")
        data = await json_body(request, "current", "new")
        if data is None:
            return error(400, "Send the current and the new password.")
        problem = password_problem(data["new"])
        if problem:
            return error(400, problem)
        if data["new"] == data["current"]:
            return error(400, "The new password must differ from the current one.")
        refused = locked(account.user)
        if refused:
            return refused
        if await asyncio.to_thread(accounts.check_password, account.user,
                                   data["current"]) is None:
            await wrong_password(account.user)
            return error(403, "Your current password is wrong.")
        throttle.succeeded(account.user)
        await asyncio.to_thread(accounts.set_password, account.user, data["new"], token)
        log.info("%s changed their password.", account.user)
        return JSONResponse({**accounts.get(account.user).public(),
                             "app_connected": bridge.connected})

    return app
