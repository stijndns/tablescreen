# Spike: HTTPS and secure WebSockets on real devices

Throwaway code for step 0 of `api_webapp_plan_v0.md`. It checks, on the actual phones, tablet and laptop, that a self-signed certificate is enough for a login with a secure cookie and a live secure WebSocket. Not part of Tablescreen; not merged.

## Running it on the table PC

From this folder, in the Tablescreen virtual environment:

```
pip install fastapi uvicorn websockets argon2-cffi cryptography
python make_cert.py
python server.py
```

`make_cert.py` writes `cert.pem` and `key.pem` for this PC's host name and LAN addresses. Generate it once and keep it: a new certificate means accepting the browser warning again on every device. An address it missed can be added: `python make_cert.py 192.168.1.77`.

`server.py` prints the addresses to open, for example `https://192.168.1.50:8443`. The first time, Windows asks whether Python may accept connections: allow it for **private** networks.

Test accounts: `player` / `spike` and `dm` / `spike`.

## What to check on each device

1. Open the printed address. The browser warns that the connection is not private. Accept it (Chrome: Advanced → Proceed; Firefox: Advanced → Accept the risk).
2. "Secure context" says **yes**.
3. Log in as `player`. "Logged in as" shows the name, "WebSocket" says **connected**, "Server pushes received" counts up every second, and "Echo round trip" shows a few milliseconds.
4. Open the page on a second device: "Connected clients" goes up on both.
5. Reload the page: it reconnects without logging in again (the cookie works).
6. Turn Wi-Fi off and on again, or lock the phone for a minute: "Reconnects" goes up and the counter carries on.
7. Log out: the WebSocket closes and the login form returns.

Note per device (model, browser) anything that differs, especially a WebSocket that stays "connecting" or errors after the certificate was accepted.
