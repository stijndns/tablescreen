# The player webapp: setup

The player webapp lets players follow the game on their own phone, tablet or laptop and act as their own character. In this version it has one tab, combat: the turn order and everyone's state as the combat screen shows them, live, plus initiative, damage, healing, conditions and ending their turn. Guests can watch without logging in.

It has two parts:

```
phones, tablets, laptops ──HTTPS──► gateway (Docker container) ──bridge──► Tablescreen (on the PC)
                                    accounts, sessions, the webapp          checks every action again
```

- The **gateway** is the web server. It holds the accounts and sessions and serves the webapp. It runs in a Docker container on the table PC, and only during a session.
- **Tablescreen** itself runs on the PC as always. It shares the public state of the features listed in its configuration, and runs the players' actions as ordinary commands, echoed in the console as `[api:<user>] …`.

The two only talk over the bridge, a local connection with a shared secret. Passwords never reach Tablescreen.

## What is needed

- The table PC with Tablescreen installed (see the [README](../README.md#installation)).
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) on the PC, with its default WSL 2 back end.
- The gateway's Python packages on the PC, for the two setup tools. Install them in Tablescreen's virtual environment:

  ```
  python -m pip install -r gateway/requirements.txt
  ```

- The players' devices on the same network as the PC.

## Setting up, once

All commands run from the repository root, with the virtual environment active.

### 1. Turn on the API

In `config.toml`:

```toml
[api]
enabled = true
share = ["combat"]          # one webapp tab per feature listed here
bridge_host = "127.0.0.1"
bridge_port = 8765
secret_file = "api/bridge.secret"

[api.views]
combat = "auto"
```

Start Tablescreen once. It creates the bridge secret in `api/bridge.secret` and prints `[+] api: waiting for the gateway on 127.0.0.1:8765; sharing combat.`

The `api/` folder holds everything private: the secret, the certificate's key and the accounts. It is excluded from version control and never goes into the container image.

### 2. Make the certificate

```
python tools/make_cert.py new
```

The webapp only runs over HTTPS, so nobody on the network can read logins or sessions. The certificate is self-signed for this PC: it lists the PC's host name and its LAN addresses, and is valid for 825 days. Check that the address the players will use is in the list. A missing one can be added: `python tools/make_cert.py new 192.168.1.77`.

Every device accepts the certificate once (see [On a device, the first time](#on-a-device-the-first-time)). A new certificate means accepting it again on every device, so keep it. Run `python tools/make_cert.py` without arguments to see the current one; it warns when the PC has an address the certificate doesn't cover, which is the time to make a new one. A fixed address for the PC (a DHCP reservation in the router) avoids that.

### 3. Prepare the accounts

```
python tools/prepare_accounts.py alice:player bob:player dm:dm
```

Each account is `name:role`, with the role `player` or `dm`. A name is 1 to 32 letters, digits, `-` or `_`; capitals don't matter when logging in. The tool asks for each account's initial password, without showing it, twice. Give each player theirs: at their first login, the webapp makes them choose their own (8 to 128 characters).

- **A new player later:** run the same command with only the new account.
- **A forgotten password:** `python tools/prepare_accounts.py --reset bob` sets a new initial password and logs that account out everywhere. There is no recovery by e-mail.
- **Removing an account:** open `api/accounts.db` with any SQLite tool and delete its row in `accounts`.

The database holds no passwords, only their argon2 hashes, and no session tokens, only their hashes.

### 4. Open the port in Windows

The PC's network profile must be **Private**: Settings → Network & Internet → Ethernet (or Wi-Fi) → Network profile type. On a Public profile, Windows blocks the players' devices.

The webapp's port is opened by Docker Desktop, so it needs its own firewall rule. In PowerShell, **as administrator**:

```
New-NetFirewallRule -DisplayName "Tablescreen webapp" -Direction Inbound -Protocol TCP -LocalPort 8443 -Action Allow -Profile Private
```

It can be removed again with `Remove-NetFirewallRule -DisplayName "Tablescreen webapp"`.

### 5. Check the container

With Docker Desktop running:

```
python tools/container_smoke.py
```

It builds the gateway's image and starts it with a throwaway certificate, account and stand-in for Tablescreen, then checks login, the database, the bridge, the live connection and an action. Every line should say PASS; on a failure it says what to look at and shows the gateway's log. Your `api/` folder and Tablescreen are not touched.

## Every session

1. Start Tablescreen: `python -m tablescreen`.
2. Start the gateway, from the repository folder:

   ```
   docker compose up -d --build
   ```

   `--build` picks up an update of the gateway; when nothing changed it takes a second. The console shows `[+] api: gateway connected`.
3. Link each player's account to their character, in Tablescreen's console:

   ```
   api user list
   api user link alice Aria
   ```

   Links are kept in the accounts database, so this is only needed when a character changes. `api user unlink alice` removes one.
4. The players open `https://<the PC's address>:8443`.
5. After the session:

   ```
   docker compose down
   ```

`api` in Tablescreen's console shows the bridge and the gateway's connection. `docker compose logs -f gateway` shows the gateway's log: logins, failed logins and every action with its result.

The gateway may be started before or after Tablescreen: it connects as soon as both run, and again whenever Tablescreen restarts. Meanwhile the webapp shows "Table offline" and keeps the last state on screen, greyed.

## On a device, the first time

1. Open `https://<the PC's address>:8443`. The browser warns that the connection is not private, because it doesn't know who made the certificate. Accept it:
   - Chrome: Advanced → Proceed to … (unsafe)
   - Firefox: Advanced → Accept the Risk and Continue
   - Safari: Show Details → visit this website

   To be sure it is this PC's certificate, compare the SHA-256 fingerprint in the browser's certificate details with the one `python tools/make_cert.py` prints.
2. Log in with the initial password, then choose your own. A guest taps "Watch as a guest" instead.
3. The combat tab follows the table. What players can do there is described in [the combat documentation](../tablescreen/features/combat/README.md#playing-from-the-webapp).

A session lasts until 30 days without use, so phones stay logged in from one game to the next. It also ends at logout, and when the password is changed on another device.

## Character intros

A player whose linked character has an intro sees it while the webapp loads. The character's loop plays, silently, for at least 10 seconds and until the table is connected; while the table is offline it says "Waiting for the table…" and keeps looping. Then a button (for example "Enter the realm") turns the sound on, the loop finishes its current clip, any closing scenes play, and the finale ends on white or black, which fades into the webapp.

- It plays once per browser session: the first time the webapp is opened after the browser was closed. "Replay intro" on the main page plays it again.
- "Skip" (or Esc) goes straight to the webapp, any time after it has loaded.
- On a phone held upright, the whole picture is shown across the middle; held sideways, it fills the screen.
- Players who turned on "reduce motion" on their device don't get it.

Intros live in the `intros/` folder in the repository root, one folder per intro. The folder isn't in version control: it is the players' own art, and large. A new intro is a new folder with a lowercase name, its clips and an `intro.json`:

```json
{
  "characters": ["Xyra", "Xyra El Finnis Feror", "Xyra_El_Finnis_Feror"],
  "loop": ["xyra_left.mp4", "xyra_right.mp4", "xyra_turn.mp4"],
  "finale": "xyra_finale.mp4",
  "loading": "Loading your campaign…",
  "button": "Enter the library",
  "after_click": "Xyra weaves her circle…",
  "color": "#5fd6a0"
}
```

| Field | Meaning |
|---|---|
| `characters` | The character names it is for, as linked with `api user link`. Capitals and extra spaces don't matter; list every other spelling. |
| `loop` | The clips played while loading, in random order. One clip is fine. Each should start and end on the same picture, so they join seamlessly. |
| `closing` | Optional scenes played once, in this order, between the button and the finale. |
| `finale` | The last clip. It should end on pure white, or on pure black with `ends_on`. |
| `ends_on` | Optional: `white` (the default) or `black`, the colour that fades into the webapp. |
| `loading`, `button`, `after_click` | Optional texts: while loading, on the button, and after the tap. |
| `color` | Optional colour of the progress bar and the button's glow. |

Clips are MP4 (H.264 video with AAC sound) or WebM. The loop's sound starts at the button's tap; the closing scenes and the finale play with their own sound. The gateway reads the intros when it starts, so restart it after adding one; its log lists them (`Intros: …`), or says why one was left out.

## Troubleshooting

**A device can't reach the webapp** ("took too long to respond"), but the PC itself can:

- The network profile is Public, or the firewall rule is missing (step 4).
- The device is on a guest Wi-Fi network, or has a VPN on.
- The address was typed without `https://` or without `:8443`.
- `netstat -ano | findstr 8443` on the PC should show `0.0.0.0:8443 … LISTENING`.

**"Table offline"** while Tablescreen runs: check `enabled = true` under `[api]`, and the gateway's log. "The app isn't reachable" means Tablescreen's bridge isn't listening (see its start-up message); "the bridge secret doesn't match" means the two read different secret files.

**The certificate warning comes back:** the certificate was replaced, or the device uses an address the certificate doesn't list.

**Port 8443 is in use:** another gateway runs already (`docker compose down`, or a `python -m gateway` in another window).

**"Too many wrong passwords":** after 5 wrong passwords for a name, it is locked for a minute.

**"Slow down":** a player can do at most 10 actions in 10 seconds.

**A tab shows plain, unstyled text:** the browser is too old; the webapp needs Chrome 73, Firefox 101, Safari 16.4 or later.

## Without the container

`python -m gateway` runs the same gateway as an ordinary process on the PC, with the same `api/` folder (`python -m gateway --help` lists its options). It is meant for development; sessions run the gateway in its container. `python tools/bridge_test_client.py` goes one step further and stands in for the gateway, to try Tablescreen's side of the bridge by hand.

## How it is kept safe

- **Encrypted:** everything between the devices and the gateway goes over HTTPS, logins included.
- **Accounts:** passwords are stored as argon2 hashes, in the gateway only. Tablescreen hears just the account name, its role and its character.
- **Sessions:** a session is a random token in a cookie that scripts can't read and other sites can't send; only the token's hash is stored.
- **Abuse limits:** wrong passwords lock a name for a minute, and actions are rate-limited.
- **What players see:** the webapp never shows more than the table screens do. Unrevealed monsters, their hit points and the console's notes for the game master are filtered out by Tablescreen before anything leaves it.
- **Every action checked twice:** first by the gateway (logged in, allowed for the role), then by Tablescreen with the game's rules (your own character, your turn) before it runs as a command.
- **The container:** it holds only the gateway, runs locked down (read-only, no privileges) and only during a session. Even if it were taken over, it could only act as a player or the game master within the actions the webapp offers; it has no way into the rest of the PC.
