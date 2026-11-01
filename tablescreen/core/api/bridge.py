"""
bridge.py — The app's end of the bridge: a socket the gateway connects to.

One listener thread accepts connections; each connection gets a reader
thread (handshake, then messages) and a writer thread (an outgoing queue, so
the executor never blocks on a slow gateway). Only one authenticated
connection is used at a time: a new one replaces the old (a restarted
gateway).

A connection is dropped only when it fails the handshake (wrong secret, no
answer in time) or stops reading what the app sends. After the handshake,
a bad message is reported in the console and ignored: dropping wouldn't
protect anything (a gateway that has the secret just reconnects) but would
cut every player off, and a gateway that keeps sending the same bad message
would reconnect in a loop.
"""

from __future__ import annotations

import queue
import socket
import threading
import traceback
from typing import Callable, Optional

import bridge_protocol as protocol

AUTH_TIMEOUT = 5.0          # seconds for the gateway to answer the challenge
OUTGOING_LIMIT = 1000       # queued messages before a stuck gateway is dropped


class Connection:
    def __init__(self, sock: socket.socket, address: str):
        self.sock = sock
        self.address = address
        self._outgoing: queue.Queue[Optional[bytes]] = queue.Queue(OUTGOING_LIMIT)
        self._closed = threading.Event()
        threading.Thread(target=self._write, daemon=True, name="bridge-writer").start()

    def send(self, message: dict) -> None:
        if self._closed.is_set():
            return
        try:
            self._outgoing.put_nowait(protocol.encode(message))
        except queue.Full:
            print(f"[!] api: the gateway isn't reading; dropping it ({self.address}).")
            self.close()
        except protocol.ProtocolError as exc:
            print(f"[!] api: not sent: {exc}")

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()
        try:
            self._outgoing.put_nowait(None)     # wakes the writer
        except queue.Full:
            pass

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def _write(self) -> None:
        while not self._closed.is_set():
            data = self._outgoing.get()
            if data is None:
                return
            try:
                self.sock.sendall(data)
            except OSError:
                self.close()


class Bridge:
    """Listens for the gateway. Callbacks run on the connection's reader
    thread: ``on_ready(connection)`` right after a successful handshake
    (before it becomes the current connection), ``on_message(message)`` for
    every validated message, ``on_lost()`` when the current one closes."""

    def __init__(self, host: str, port: int, secret: str,
                 on_ready: Callable[[Connection], None],
                 on_message: Callable[[dict], None],
                 on_lost: Callable[[], None]):
        self._host, self._port, self._secret = host, port, secret
        self._on_ready, self._on_message, self._on_lost = on_ready, on_message, on_lost
        self._server: Optional[socket.socket] = None
        self._current: Optional[Connection] = None
        self._lock = threading.Lock()

    @property
    def address(self) -> str:
        return f"{self._host}:{self.port}"

    @property
    def port(self) -> int:
        """The bound port (port 0 in config picks a free one, for tests)."""
        return self._server.getsockname()[1] if self._server else self._port

    @property
    def connected(self) -> bool:
        current = self._current
        return current is not None and not current.closed

    @property
    def gateway_address(self) -> Optional[str]:
        current = self._current
        return current.address if current and not current.closed else None

    def start(self) -> None:
        """Bind and listen; raises OSError (e.g. the port is taken)."""
        server = socket.create_server((self._host, self._port))
        self._server = server
        threading.Thread(target=self._accept, daemon=True, name="bridge").start()

    def stop(self) -> None:
        server, self._server = self._server, None
        if server:
            try:
                server.shutdown(socket.SHUT_RDWR)  # wakes accept() (Linux)
            except OSError:
                pass
            server.close()
        with self._lock:
            current, self._current = self._current, None
        if current:
            current.close()

    def send(self, message: dict) -> bool:
        """To the current gateway; False when none is connected."""
        current = self._current
        if current is None or current.closed:
            return False
        current.send(message)
        return True

    # ── Threads ───────────────────────────────────────────────────────────

    def _accept(self) -> None:
        while self._server is not None:
            try:
                sock, (host, port, *_) = self._server.accept()
            except OSError:
                return                  # stopped
            connection = Connection(sock, f"{host}:{port}")
            threading.Thread(target=self._serve, args=(connection,), daemon=True,
                             name="bridge-reader").start()

    def _serve(self, connection: Connection) -> None:
        reader = protocol.LineReader()
        pending: list[dict] = []
        try:
            pending = self._handshake(connection, reader)
        except (OSError, protocol.ProtocolError) as exc:
            print(f"[!] api: refused a bridge connection from {connection.address}: {exc}")
            connection.close()
            return

        self._on_ready(connection)
        with self._lock:
            old, self._current = self._current, connection
        if old:
            old.close()
        print(f"[+] api: gateway connected ({connection.address}).")

        try:
            for message in pending:
                self._handle(message)
            while not connection.closed:
                data = connection.sock.recv(65536)
                if not data:
                    break
                for message in reader.feed(data):
                    self._handle(message)
                for error in reader.errors:
                    print(f"[!] api: ignored a message from the gateway: {error}")
                reader.errors.clear()
        except OSError:
            pass
        connection.close()
        with self._lock:
            lost = self._current is connection
            if lost:
                self._current = None
        if lost:
            print("[i] api: gateway disconnected.")
            self._on_lost()

    def _handle(self, message: dict) -> None:
        try:
            checked = protocol.check(message)
        except protocol.ProtocolError as exc:
            print(f"[!] api: ignored a message from the gateway: {exc}")
            return
        try:
            self._on_message(checked)
        except Exception:
            print("[!] api: handling a message from the gateway failed:")
            traceback.print_exc()

    def _handshake(self, connection: Connection, reader: protocol.LineReader) -> list[dict]:
        """Challenge and response; returns messages that arrived with it."""
        nonce = protocol.new_nonce()
        connection.sock.settimeout(AUTH_TIMEOUT)
        connection.send({"type": "hello", "protocol": protocol.PROTOCOL, "nonce": nonce})
        messages: list[dict] = []
        while not messages:
            data = connection.sock.recv(4096)
            if not data:
                raise protocol.ProtocolError("closed during the handshake")
            messages = reader.feed(data)
            if reader.errors:
                raise protocol.ProtocolError(reader.errors[0])
        auth = messages.pop(0)
        if auth.get("type") != "auth" or not protocol.mac_matches(
                self._secret, nonce, auth.get("mac")):
            raise protocol.ProtocolError("wrong secret")
        connection.sock.settimeout(None)
        return messages
