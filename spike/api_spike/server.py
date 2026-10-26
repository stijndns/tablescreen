"""
server.py — Spike: HTTPS + cookie login + secure WebSocket, for testing on
real devices. Throwaway; not part of Tablescreen.

    python make_cert.py          # once
    python server.py [port]      # default 8443

Test accounts: player / spike and dm / spike.
"""

import asyncio
import json
import secrets
import socket
import sys
import time
from pathlib import Path

import uvicorn
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

HERE = Path(__file__).resolve().parent
COOKIE = "spike_session"

hasher = PasswordHasher()
USERS = {name: hasher.hash("spike") for name in ("player", "dm")}
sessions: dict[str, str] = {}           # token → user
clients: set[WebSocket] = set()

app = FastAPI()


@app.get("/")
def page() -> HTMLResponse:
    return HTMLResponse((HERE / "page.html").read_text(encoding="utf-8"))


@app.post("/login")
async def login(request: Request) -> Response:
    data = await request.json()
    user, password = str(data.get("user", "")), str(data.get("password", ""))
    try:
        hasher.verify(USERS[user], password)
    except (KeyError, VerifyMismatchError):
        await asyncio.sleep(0.5)        # a little friction for guessing
        return JSONResponse({"ok": False, "error": "wrong user or password"}, status_code=401)
    token = secrets.token_urlsafe(32)
    sessions[token] = user
    response = JSONResponse({"ok": True, "user": user})
    response.set_cookie(COOKIE, token, httponly=True, secure=True, samesite="strict")
    return response


@app.post("/logout")
def logout(request: Request) -> Response:
    sessions.pop(request.cookies.get(COOKIE, ""), None)
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE)
    return response


@app.get("/me")
def me(request: Request) -> Response:
    user = sessions.get(request.cookies.get(COOKIE, ""))
    return JSONResponse({"user": user}, status_code=200 if user else 401)


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await websocket.accept()
    # The cookie came with the handshake: the key assumption under test.
    user = sessions.get(websocket.cookies.get(COOKIE, ""))
    if user is None:
        await websocket.send_text(json.dumps({"type": "error", "error": "not logged in"}))
        await websocket.close(code=4401)
        return
    clients.add(websocket)
    await websocket.send_text(json.dumps({"type": "hello", "user": user,
                                          "clients": len(clients)}))

    async def push() -> None:
        n = 0
        while True:
            n += 1
            await websocket.send_text(json.dumps({
                "type": "tick", "n": n, "server_time": time.time(), "clients": len(clients)}))
            await asyncio.sleep(1)

    pusher = asyncio.create_task(push())
    try:
        while True:
            message = json.loads(await websocket.receive_text())
            if message.get("type") == "echo":
                await websocket.send_text(json.dumps({**message, "user": user}))
    except WebSocketDisconnect:
        pass
    finally:
        pusher.cancel()
        clients.discard(websocket)


def lan_urls(port: int) -> list[str]:
    addresses = set()
    try:
        addresses.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.0.2.1", 9))
            addresses.add(probe.getsockname()[0])
        except OSError:
            pass
    return [f"https://{a}:{port}" for a in sorted(addresses) if not a.startswith("127.")]


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8443
    if not (HERE / "cert.pem").exists():
        sys.exit("No certificate yet: run 'python make_cert.py' first.")
    print("Open on the devices:", *lan_urls(port) or [f"https://localhost:{port}"], sep="\n  ")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning",
                ssl_certfile=str(HERE / "cert.pem"), ssl_keyfile=str(HERE / "key.pem"))
