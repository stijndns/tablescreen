"""
container_smoke.py — Checks the gateway's container on this PC (Docker Desktop).

    python tools/container_smoke.py                  # the bridge on 127.0.0.1, as the app
    python tools/container_smoke.py --bind 0.0.0.0   # if the container can't reach that
    python tools/container_smoke.py --keep           # leave it running to look around

Builds the image and starts it through compose.yaml (as the project
"tablescreen-smoke" on port 18443) with a throwaway data folder: its own
certificate, a test account and a bridge secret. This script stands in for
the app: it listens on the bridge with a test feature and one action. Then,
over HTTPS with the certificate checked: log in, change the password, the
WebSocket's hello and state, the action, and the session the container
wrote into the database. Your api/ folder and the app are not touched.

Run from the repository root, with Docker Desktop running and the
gateway's packages on this PC (python -m pip install -r
gateway/requirements.txt).
"""

from __future__ import annotations

import argparse
import http.client
import importlib.util
import json
import os
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bridge_protocol as protocol  # noqa: E402
from gateway.accounts import AccountStore  # noqa: E402

PROJECT = "tablescreen-smoke"
PORT = 18443
BRIDGE_WAIT = 20.0         # seconds for the container to reach the bridge
USER, FIRST, OWN = "smoke", "smoke-initial", "smoke-own-password"
FEATURE = {"name": "smoke", "schema": "smoke/1", "view": "auto", "view_source": None,
           "actions": [{"name": "ping", "permission": "player", "fields": {}}]}


def load_make_cert():
    spec = importlib.util.spec_from_file_location("make_cert", ROOT / "tools" / "make_cert.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["make_cert"] = module           # dataclasses look it up
    spec.loader.exec_module(module)
    return module


class FakeApp:
    """The app's end of the bridge, just enough for the gateway: the
    handshake, one feature with a state, and an answer to its action."""

    def __init__(self, host: str, secret: str):
        self.secret = secret
        self.server = socket.create_server((host, 0))
        self.port = self.server.getsockname()[1]
        self.connected = threading.Event()
        self.arrived_on = None          # this PC's address the container reached
        self.actions = 0
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                sock, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(sock,), daemon=True).start()

    def _serve(self, sock: socket.socket):
        reader = protocol.LineReader()
        nonce = protocol.new_nonce()
        try:
            sock.sendall(protocol.encode({"type": "hello", "protocol": protocol.PROTOCOL,
                                          "nonce": nonce}))
            messages = []
            while True:
                if not messages:
                    data = sock.recv(65536)
                    if not data:
                        return
                    messages += reader.feed(data)
                    continue
                message = messages.pop(0)
                if not self.connected.is_set():
                    if message.get("type") != "auth" or not protocol.mac_matches(
                            self.secret, nonce, message.get("mac")):
                        return
                    self.arrived_on = sock.getsockname()[0]
                    sock.sendall(protocol.encode({"type": "ready", "protocol": protocol.PROTOCOL,
                                                  "features": [FEATURE]}))
                    sock.sendall(protocol.encode({"type": "state", "feature": "smoke",
                                                  "schema": "smoke/1", "version": 1,
                                                  "data": {"hello": "from the smoke test"}}))
                    self.connected.set()
                elif message.get("type") == "action":
                    self.actions += 1
                    sock.sendall(protocol.encode({"type": "result", "id": message["id"],
                                                  "ok": True, "output": "[+] pong", "error": ""}))
        except OSError:
            return
        finally:
            sock.close()

    def close(self):
        self.server.close()


class Https:
    """Requests to the container, checking its certificate; keeps the
    session cookie."""

    def __init__(self, cafile: Path):
        self.context = ssl.create_default_context(cafile=str(cafile))
        self.cookie = None

    def request(self, method: str, path: str, body=None) -> tuple[int, dict]:
        connection = http.client.HTTPSConnection("127.0.0.1", PORT, context=self.context,
                                                 timeout=10)
        headers = {"Content-Type": "application/json"} if body is not None else {}
        if self.cookie:
            headers["Cookie"] = self.cookie
        connection.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        for name, value in response.getheaders():
            if name.lower() == "set-cookie" and value.startswith("tablescreen_session="):
                self.cookie = value.split(";", 1)[0]
        data = response.read()
        connection.close()
        try:
            return response.status, json.loads(data)
        except ValueError:
            return response.status, {}


class Failed(Exception):
    pass


def check(what: str, ok: bool, hint: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {what}")
    if not ok:
        raise Failed(hint)


def compose(*args: str, env: dict, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", "-p", PROJECT, *args], cwd=ROOT, env=env,
                          text=True, capture_output=capture)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    parser.add_argument("--bind", default="127.0.0.1",
                        help="where the stand-in app's bridge listens (default 127.0.0.1)")
    parser.add_argument("--keep", action="store_true",
                        help="leave the container and its data folder for a look")
    args = parser.parse_args()
    if shutil.which("docker") is None:
        sys.exit("No 'docker' command: is Docker Desktop installed and running?")

    data = Path(tempfile.mkdtemp(prefix="tablescreen-smoke-"))
    print(f"Data folder: {data}")
    load_make_cert().generate(data / "cert.pem", data / "key.pem")
    store = AccountStore(data / "accounts.db")
    store.create()
    store.add(USER, "player", FIRST)
    secret = protocol.new_secret()
    (data / "bridge.secret").write_text(secret + "\n", encoding="utf-8")
    app = FakeApp(args.bind, secret)
    print(f"Stand-in app: bridge on {args.bind}:{app.port}")

    env = {**os.environ, "TABLESCREEN_PORT": str(PORT), "TABLESCREEN_DATA": str(data),
           "TABLESCREEN_BRIDGE_PORT": str(app.port)}
    started = False
    try:
        print("Building and starting the container (the first build takes a while) …")
        result = compose("up", "-d", "--build", env=env)
        started = True
        check("the image builds and the container starts", result.returncode == 0,
              "See the docker output above.")

        web = Https(data / "cert.pem")
        deadline = time.monotonic() + 60
        status = None
        while time.monotonic() < deadline:
            try:
                status, _ = web.request("GET", "/api/me")
                break
            except (OSError, ssl.SSLError):
                time.sleep(1)
        check(f"HTTPS on port {PORT}, certificate checked", status == 200,
              "The gateway didn't answer; see its log below.")

        status, me = web.request("POST", "/api/login", {"user": USER, "password": FIRST})
        check("log in with the initial password", status == 200 and me.get("must_change"))
        status, me = web.request("POST", "/api/password", {"current": FIRST, "new": OWN})
        check("change the password", status == 200 and not me.get("must_change"),
              "If the log says the database is read-only or permission is denied, the"
              " container's user can't write to the shared folder (Docker Desktop's file"
              " sharing).")
        status, me = web.request("GET", "/api/me")
        check("the session works", status == 200 and me.get("user") == USER)
        with sqlite3.connect(data / "accounts.db") as db:
            sessions = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        check("the container wrote its session into the shared database", sessions >= 1)

        reached = app.connected.wait(BRIDGE_WAIT)
        hint = ""
        if not reached and args.bind == "127.0.0.1":
            hint = ("The container can't reach 127.0.0.1 on this PC. Run again with"
                    " --bind 0.0.0.0: it shows the address the container does reach, for"
                    " [api] bridge_host in config.toml.")
        check("the container connects to the bridge (host.docker.internal)", reached, hint)
        if args.bind != "127.0.0.1":
            print(f"        It arrived on this PC's address {app.arrived_on}: use"
                  f" bridge_host = \"{app.arrived_on}\" in [api].")

        import websockets.sync.client as ws_client
        with ws_client.connect(f"wss://127.0.0.1:{PORT}/ws", ssl=web.context,
                               additional_headers={"Cookie": web.cookie},
                               open_timeout=10) as ws:
            messages = {}
            deadline = time.monotonic() + 10
            while len(messages) < 2 and time.monotonic() < deadline:
                message = json.loads(ws.recv(timeout=10))
                if message["type"] == "hello" and message.get("app_connected"):
                    messages["hello"] = message
                elif message["type"] == "state":
                    messages["state"] = message
        hello = messages.get("hello", {})
        check("the WebSocket's hello: logged in, table connected",
              hello.get("user") == USER and [t["name"] for t in hello.get("tabs", [])] == ["smoke"])
        check("the WebSocket's state from the app",
              messages.get("state", {}).get("data") == {"hello": "from the smoke test"})

        status, answer = web.request("POST", "/api/actions/smoke/ping", {})
        check("an action goes to the app and back",
              status == 200 and answer == {"ok": True, "message": "pong"} and app.actions == 1)
        print("\nAll checks passed.")
    except Failed as failure:
        if str(failure):
            print(f"\n  → {failure}")
        if started:
            print("\nThe gateway's log:")
            compose("logs", "--no-color", "gateway", env=env)
        sys.exit(1)
    finally:
        app.close()
        if args.keep:
            print(f"\nLeft running: https://127.0.0.1:{PORT} (user {USER}, password {OWN})."
                  f" Stop it with: docker compose -p {PROJECT} down")
        else:
            if started:
                compose("down", env=env, capture=True)
            shutil.rmtree(data, ignore_errors=True)


if __name__ == "__main__":
    main()
