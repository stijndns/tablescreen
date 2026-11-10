# Gateway

The gateway is the player webapp's server: HTTPS, accounts and sessions, the webapp's files, the live connection to every open page, and the bridge to Tablescreen. It is a separate program (`python -m gateway`, in a container for sessions) and never imports Tablescreen; Tablescreen never imports it either. The two share only the bridge's wire format, `bridge_protocol.py` at the repository root.

Setting it up and running it is described in [the setup guide](../docs/webapp_setup.md). This page is about how it works.

## Files

| File | Contents |
|---|---|
| `__main__.py` | Start-up: checks for the certificate and the accounts, then runs uvicorn over HTTPS |
| `settings.py` | Options, else `TABLESCREEN_GATEWAY_<OPTION>` environment variables, else defaults matching Tablescreen's |
| `accounts.py` | `AccountStore`: accounts and sessions in SQLite, argon2 password hashes |
| `throttle.py` | `Throttle` for wrong passwords, `RateLimit` for actions |
| `bridge_client.py` | `BridgeClient`: connects to Tablescreen, keeps the shared features, views and states, sends actions, answers `api user …` |
| `hub.py` | `Hub`: one entry per open page, pushing `hello` and `state` messages |
| `intros.py` | `Intros`: the character intros in `intros/`, read from each `intro.json` at start-up |
| `web.py` | The FastAPI app: pages, the JSON API, the WebSocket, security headers |
| `webapp/` | The webapp: the pages, `static/` (the shell, styles, the generic view), `static/theme/` (the look's images: parchment, cornice, column pieces) and `views/` (the webapp's own views) |
| `requirements.txt` | The gateway's packages; the container installs only these |

## The bridge, from this side

Tablescreen listens; the gateway connects (127.0.0.1:8765 by default, `host.docker.internal` from the container) and proves it knows the shared secret by answering a challenge with an HMAC. The secret itself never crosses the wire. It retries every 2 seconds while Tablescreen isn't running or hasn't opened the table (`api open`), and reconnects after a restart or `api close`.

After the handshake, Tablescreen sends `ready` (the shared features, their actions and their views' source) and then every feature's `state`, and a new `state` after every command that changed it. The gateway sends `action` messages and waits up to 5 seconds for each `result`. Tablescreen's `api user list|link|unlink` arrive as `accounts` requests, answered from the database.

When the connection drops, the last features and states stay: the webapp keeps showing them, greyed, until Tablescreen is back. The message format and Tablescreen's checks are described in [the core documentation](../tablescreen/core/README.md#the-player-webapp).

## The web side

| Request | Purpose |
|---|---|
| `GET /`, `/login`, `/password` | The webapp's pages |
| `GET /static/…` | The shell's scripts and styles, and the generic view |
| `GET /views/webapp/<feature>.js` | The webapp's own view for a tab, if there is one |
| `GET /views/feature/<feature>.js` | The feature's view, as Tablescreen sent it |
| `GET /intros/<intro>/<clip>` | A character intro's clip, for logged-in accounts; only the files its `intro.json` names, with range requests for video players |
| `GET /api/me` | Who this browser is; a guest without a session |
| `POST /api/login` | `{user, password}` → a session cookie |
| `POST /api/logout` | Ends this session |
| `POST /api/password` | `{current, new}` → the new password; ends the account's other sessions |
| `POST /api/actions/<feature>/<action>` | A player's action: the action's fields as a JSON object → `{ok, message}` |
| `WS /ws` | The live connection |

The WebSocket sends a `hello` (user, role, character, whether the table is connected, and the tabs with their actions and the views a page may load) and a `state` per feature: at connect, and again whenever something changes. Each page keeps only the latest unsent message of each kind, so a slow phone skips old states instead of falling behind. When its session ends, a page's socket closes with code 4401 and the page goes to the login page; an account still on its initial password gets 4403 and goes to the change-password page. Open sessions are checked every 30 seconds for changes made outside the gateway, such as `prepare_accounts.py --reset`.

An action is checked as far as the gateway knows: logged in past the initial password, at most 10 actions in 10 seconds per account, the table connected, an action Tablescreen offers, and a dm-only action only for the dm. The caller's character comes from the database, never from the browser. Tablescreen then checks the game's rules itself, and its answer is the reply, without the console's `[+]`/`[!]` markers.

## DM notes

The game master's notes to a player (`api user notes` in Tablescreen's console) are stored in the `notes` table and arrive as `notes_show`, `notes_add` and `notes_clear` requests over the bridge. Unlike the features' states, which go to every page, they are private: the hub adds a built-in "DM notes" tab to logged-in pages only, and sends a change only to the pages of that account. The view is `webapp/views/dm-notes.js`.

## Character intros

When the account's linked character has an intro (matched ignoring case and extra spaces), `hello` carries it: its clips' URLs, texts and colour. The page then plays it with `static/intro.js` before showing the webapp, once per browser session: the loop until the webapp is loaded (at least 10 seconds, the table connected, every tab showing its state), then any closing scenes and the finale after the player's tap. How to add one is described in [the setup guide](../docs/webapp_setup.md#character-intros). The container gets the `intros/` folder read-only, as `/intros`.

## Views

A tab shows the first view that loads: the webapp's own (`webapp/views/<feature>.js`), the feature's (sent by Tablescreen), or the generic one, which shows any state as a tree with a form per action. `[api.views]` in Tablescreen's configuration can force where a tab starts. A view written for other state versions is skipped with a notice. Writing a view is described in [webapp/views/README.md](webapp/views/README.md).

## Security decisions

- **HTTPS only**, with the self-signed certificate from `tools/make_cert.py`. There is deliberately no HSTS: with a self-signed certificate it would stop browsers from letting players accept the warning.
- **Sessions:** a random token in an HttpOnly, Secure, SameSite=Strict cookie. Only its SHA-256 is stored. A session ends after 30 days unused; the cookie's lifetime is renewed with the session's.
- **No cross-site requests:** POSTs to `/api/` must be JSON, which a form on another site can't send, and the cookie isn't sent cross-site anyway. A WebSocket from another site's page is refused.
- **Passwords:** argon2. An unknown name costs as much time as a wrong password, every failure adds half a second, and 5 failures lock the name for a minute.
- **Content security policy `default-src 'self'`:** no inline scripts or styles and no framing, so the pages run only the gateway's own files. Views add their styles as constructed stylesheets.
- **The container** gets only `gateway/` and `bridge_protocol.py` (see `.dockerignore`), runs as a normal user on a read-only file system with no capabilities, and shares only the `api/` folder.
