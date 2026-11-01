"""
bridge_test_client.py — Stands in for the gateway, to try the bridge by hand.

Connects to the app's bridge, prints everything the app sends, and reads
commands from the keyboard:

    as <user> <role> [character]        who the next actions come from
                                        (role: guest | player | dm)
    do <feature> <action> [key=value …] send an action, e.g.
                                        do combat action kind=damage target=Goblin amount=5
    raw <json>                          send any message (to try bad input)
    quit

It also answers the app's account requests (`api user list|link|unlink`
in the app's console) from a pretend account list kept in memory.

    python tools/bridge_test_client.py [host] [port]

Run from the repository root; it reads the secret from api/bridge.secret.
"""

from __future__ import annotations

import json
import shlex
import socket
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bridge_protocol as protocol  # noqa: E402

accounts: dict[str, dict] = {
    "alice": {"user": "alice", "role": "player", "character": None},
    "bob": {"user": "bob", "role": "player", "character": None},
    "dm": {"user": "dm", "role": "dm", "character": None},
}


def accounts_answer(request: dict) -> dict:
    op, args = request.get("op"), request.get("args", {})
    user = args.get("user")
    answer = {"type": "accounts_result", "id": request["id"], "ok": True}
    if op == "list":
        answer["message"] = f"{len(accounts)} accounts"
        answer["users"] = list(accounts.values())
    elif user not in accounts:
        answer.update(ok=False, message=f"No account named {user}.")
    elif op == "link":
        accounts[user]["character"] = args.get("character")
        answer["message"] = f"{user} now plays {args.get('character') or 'no character'}."
    else:
        answer.update(ok=False, message=f"Unknown request {op!r}.")
    return answer


def show(message: dict) -> None:
    kind = message.get("type")
    if kind == "state":
        print(f"\n<< state {message['feature']} v{message['version']} ({message['schema']}):")
        print(json.dumps(message["data"], indent=2))
    elif kind == "result":
        status = "ok" if message["ok"] else "REFUSED"
        print(f"\n<< result {message['id']}: {status}")
        for line in (message.get("error") or message.get("output") or "").splitlines():
            print(f"   {line}")
    else:
        print(f"\n<< {json.dumps(message)}")


def read(sock: socket.socket) -> None:
    reader = protocol.LineReader()
    while True:
        data = sock.recv(65536)
        if not data:
            print("\n[connection closed by the app]")
            return
        for message in reader.feed(data):
            show(message)
            if message.get("type") == "accounts":
                sock.sendall(protocol.encode(accounts_answer(message)))
        print("> ", end="", flush=True)


def main() -> None:
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8765
    secret = (ROOT / "api" / "bridge.secret").read_text(encoding="utf-8").strip()

    sock = socket.create_connection((host, port))
    reader = protocol.LineReader()
    hello = []
    while not hello:
        hello = reader.feed(sock.recv(4096))
    print(f"<< {json.dumps(hello[0])}")
    sock.sendall(protocol.encode({"type": "auth", "mac": protocol.mac(secret, hello[0]["nonce"])}))
    threading.Thread(target=read, args=(sock,), daemon=True).start()

    caller = {"user": "test", "role": "dm", "character": None}
    count = 0
    print("Acting as test (dm). Type 'as', 'do', 'raw' or 'quit'.")
    while True:
        try:
            line = input("> ").strip()
        except EOFError:
            break
        if not line:
            continue
        words = shlex.split(line)
        if words[0] == "quit":
            break
        if words[0] == "as" and len(words) in (3, 4):
            caller = {"user": words[1], "role": words[2],
                      "character": words[3] if len(words) == 4 else None}
            print(f"Acting as {caller}")
        elif words[0] == "do" and len(words) >= 3:
            args = {}
            for word in words[3:]:
                key, _, value = word.partition("=")
                args[key] = int(value) if value.lstrip("-").isdigit() else value
            count += 1
            sock.sendall(protocol.encode({"type": "action", "id": f"t{count}",
                                          "feature": words[1], "action": words[2],
                                          "caller": caller, "args": args}))
        elif words[0] == "raw":
            sock.sendall(line[4:].encode() + b"\n")
        else:
            print("as <user> <role> [character] | do <feature> <action> [key=value …] | raw <json> | quit")
    sock.close()


if __name__ == "__main__":
    main()
