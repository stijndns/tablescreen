# Core

The core is the framework the features plug into. It owns the shell, the windows and the message bus, reads the configuration and loads the enabled features. It also owns the player webapp's way in, the API. It contains no game logic of its own.

## Two rules

The architecture rests on two rules, and most design decisions follow from them.

1. **Features depend on core; core never imports a feature.** Features are loaded by name with `importlib`, using the names listed under `enabled` in `config.toml`. A disabled feature is never imported, so its commands do not exist and its windows are never created.
2. **Features never reach into each other.** A feature receives a small `ShellServices` object and nothing else: no Tk root, no shell, no message bus, no list of other features. Anything a feature needs that is not on `ShellServices` should be treated as a design question rather than worked around.

## Files

| File | Contents |
|---|---|
| `app.py` | Start-up and shutdown. Reads `config.toml`, creates the hidden Tk root, the bus, the window service, the shell, the executor and the console, builds each enabled feature, then runs the Tk main loop. Also makes the process DPI-aware on Windows. |
| `bus.py` | `MessageBus`, the single queue between the executor and the Tk main loop |
| `shell.py` | `Shell`, a `cmd.Cmd` that starts with no commands and lets features register theirs; the command registry, help, tab completion and dispatch |
| `executor.py` | `CommandExecutor`, the one thread that runs commands from every input source |
| `console.py` | `Console`, which reads the keyboard, and `ask()` for questions asked in the middle of a command |
| `window.py` | Named windows, content slots, fullscreen, restore and monitor detection |
| `feature.py` | The `Feature` protocol, `ShellServices` and the optional `FeatureBase` |
| `paths.py` | Locations of `assets/`, `combatants/` and `logs/`, independent of the working directory |
| `completion.py` | Tab completion for file names and argument splitting that respects quotes |
| `api/service.py` | `ApiService`: the player webapp's input source. Sends the shared features' public states, runs the players' actions, and the `api` command |
| `api/bridge.py` | `Bridge`, the socket the gateway connects to |
| `api/actions.py` | `Field`, `Caller` and `Action`: how a feature describes an action and who calls it |

## Threading

Tkinter is not thread-safe. Everything that touches a widget has to run on the thread that runs the Tk main loop, which is the main thread. Commands run on a separate daemon thread, the executor, and the two meet in the message bus:

```
keyboard (console thread) ───┐
mouse on a window (main loop)├─► executor queue ─► executor thread ─send()─► bus ─► feature's consumer
webapp (bridge thread) ──────┘   (line, source)    runs the command                 (main loop thread)
```

Every input source hands its commands to the executor instead of running them itself. The console thread reads a line from the keyboard, submits it and waits until it has finished before showing the next prompt. A feature can submit one of its own commands with `ShellServices.execute`, for example when the game master clicks on a window. The executor runs the commands one at a time, in the order they arrive, so it is the only thread that ever changes feature state.

The rules that follow from this:

- Command handlers run on the executor thread. They may change the feature's own state and send messages, and must never touch a widget.
- A feature's bus consumer (`_on_message` by convention) and all view code run on the main loop thread. That is where drawing happens.
- A question in the middle of a command, such as the combat tracker's prompts, uses `ask()` from `core/console.py`, never `input()`. It blocks the executor until the answer is typed in the console. Blocking the main loop instead would freeze every window.
- Work that does not involve Tk at all, such as audio playback through pygame, may be done directly from a command handler without a bus round trip.
- Long-running background work (the battlemap's video decoder) runs on its own daemon thread and hands results to the main loop through a queue. It never touches Tk either.

Because state is changed on the executor thread and read on the main loop thread, features keep shared state in a form that is safe to read at any moment. The battlemap, for instance, stores sprites and areas of effect as tuples of frozen records that are replaced as a whole on every change, never modified in place.

When commands arrive faster than the main loop handles them (pasted or piped input), the main loop may only see the latest state. A message that reports on its own command should therefore carry the values it reports, instead of reading them from the state when it is handled.

### Questions in the middle of a command

`ask(prompt)` shows the prompt in the console and returns the line typed in reply. While a typed command runs, the console thread is waiting for it anyway, so it reads the answer and passes it back; commands from other sources wait in the queue until the question is answered.

If no answer can come, `ask()` raises `NoAnswer` and the executor cancels the command with a message. That happens at the end of input, and when a command that did not come from the keyboard (one submitted with `execute`) asks a question, because nobody is at the keyboard for it. Commands meant for other sources should therefore not ask questions. Before the console is installed, as in tests, `ask()` falls back to `input()`.

### Why the bus has two wake-up paths

`send()` puts a message on the queue and asks Tk to fire a `<<QueueMsg>>` event, which makes the main loop drain the queue almost immediately. Tk can silently drop generated events, notably for windows that are not mapped yet or are withdrawn, so the bus also polls the queue every 100 ms. Without the poll, a dropped event would leave a message waiting forever. Both paths are needed; this has gone wrong in practice.

Draining always empties the whole queue, because Tk merges events and there is no one-to-one relation between events and messages. Messages for a feature that is not loaded are dropped with a single warning, which is what makes running any subset of features safe.

## Windows, slots and views

- A **window** is a named Tk `Toplevel`, created on first request and owned by core. Its default size comes from `[windows]` in the configuration. The Tk root itself stays hidden and only runs the main loop.
- A **content slot** is one feature's `tk.Frame` inside a window. A window can hold several slots but shows only one: calling `show()` on a slot hides its siblings. This is how the combat screen and the image viewer share the main screen without knowing about each other.
- A **view** is owned by a feature and draws the feature's state into a slot. A feature may have several views of the same state; `FeatureBase.views` is a list for that reason.

Closing a window with its close button only hides it; slots and their contents survive. `Window.restore()` brings it back. A feature can replace that behaviour for its own window with `Window.on_close(callback)`, and `WindowService.remove_window(name)` destroys a window for good; the owner releases what its content holds (threads, images) first.

`Window.default_geometry` is the size the window was created with (what `restore()` returns to). `Window.initial_geometry` is its size and position (`"WxH+X+Y"`) when it was first mapped, or `None` before that; it can be passed to `get_window(name, geometry)` to open another window in the same place.

`Window.monitor()` returns the monitor containing the centre of the window, or the nearest one. The centre is used rather than the top-left corner because maximised windows on Windows sit a few pixels off-screen.

## ShellServices

What a feature receives in `build()`:

| Member | Purpose |
|---|---|
| `feature_name` | The name the feature was loaded under; also its bus address |
| `register_command(name, handler, completer=None, help_text="")` | Adds a shell command. Registering a name that another feature already uses raises an error. |
| `windows` | The window service, for windows and slots |
| `send(target, command, arg=None)` | Puts a message on the bus. Normally the target is the feature itself. |
| `subscribe(consumer)` | Registers the feature's bus consumer, bound to its own name |
| `publish_state(snapshot, schema, view=None)` | Offers a public state to the player webapp; see [the player webapp](#the-player-webapp) |
| `register_action(name, fields, permission, handler, reply=None)` | Offers an action to the player webapp |
| `execute(line, source)` | Runs one of the feature's own commands as if it had been typed, e.g. `execute("map fow reveal B3:D7", source="mirror")`. The command is queued on the executor and echoed in the console as `[mirror] map fow reveal B3:D7`; the call returns at once and is safe from any thread. Messages the command sends carry the source. Another feature's command, or the source `shell`, is refused. |
| `config` | The feature's own section of `config.toml` |
| `slot(window_name)` | Shortcut for a content slot named after the feature |
| `window_name(default)` | The window configured for this feature |

## Writing a feature

A feature is a package in `tablescreen/features/` whose `__init__.py` exposes a `FEATURE` object with a `build(services)` method. Adding its name to `enabled` in the configuration loads it. The `hello` feature is a complete minimal example; it is not enabled by default.

```python
import tkinter as tk
from tablescreen.core.feature import FeatureBase


class MyView:
    def __init__(self, parent):
        self.label = tk.Label(parent, text="", bg="black", fg="white")
        self.label.pack(fill="both", expand=True)

    def render(self, snapshot):
        self.label.config(text=snapshot["text"])


class MyFeature(FeatureBase):
    name = "myfeature"

    def __init__(self):
        super().__init__()          # runs at import time: attributes only
        self.state = {"text": ""}

    def build(self, services):      # runs once, on the main loop thread
        self.services = services
        self.slot = services.slot(services.window_name())
        self.views.append(MyView(self.slot.frame))
        services.register_command("say", self._do_say, help_text="say <text>")
        services.subscribe(self._on_message)

    def snapshot(self):
        return dict(self.state)

    def _do_say(self, arg):         # executor thread: state and messages only
        self.state["text"] = arg
        self.services.send(self.name, "update")

    def _on_message(self, message): # main loop thread: widgets allowed
        self.slot.show()
        self.refresh()              # calls render(snapshot) on every view


FEATURE = MyFeature()
```

Things that have caused problems before:

- `FEATURE = MyFeature()` runs when the module is imported, before the Tk root exists. The constructor must not create widgets, open files or start audio.
- A view that subclasses `tk.Frame` must be packed into the slot frame in `build()`. If it is not, nothing is shown and no error is raised.
- `FeatureBase.refresh()` expects every view to have `render(snapshot)`. This contract is not yet formalised; the combat view takes an extra page argument and therefore bypasses `refresh()`.
- Never assign to `self.grid` on a widget: it replaces Tk's grid geometry method.
- Keep a reference to every `PhotoImage`. Tk does not keep the image data alive, and an image that is garbage collected turns blank without an error. Dropping the reference is also the only way to free its memory.
- `root.update()` processes pending drawing but does not run `after()` callbacks. Code or tests that depend on the bus or timers need a running main loop.
- `shutdown()` is called on exit and is the place to stop background threads or save state.

## The player webapp

The player webapp lets players follow the game on their own device and act as their character. Its server, the **gateway** (`gateway/` at the repository root), is a separate program that runs in a container and talks to the application only over the **bridge**, a local socket with a shared secret. Setting it up is described in [the setup guide](../../docs/webapp_setup.md), and the gateway itself in [its documentation](../../gateway/README.md).

On the application's side the webapp is an input source in core, like the console, configured in `[api]` and off by default. A feature takes part by publishing a public state and registering actions on `ShellServices`; it never sees the bridge or the gateway. The combat tracker is the example: `features/combat/api.py`.

### Publishing a state

```python
services.publish_state(lambda: public_state(self.combat), "combat/1",
                       view=Path(__file__).parent / "web" / "view.js")
```

- **`snapshot()`** returns plain JSON data. It runs on the executor thread after every command while a gateway is connected, and its result is sent whenever it changed, with a new version number. Only features listed in `[api] share` are sent.
- **What it may contain:** the state goes to every device, guests included, so it must never show more than the table screens do: no unrevealed monsters, no hidden sprites, nothing under the fog. The filtering belongs here, because anything sent can be read in a browser's developer tools.
- **The schema** names the state's shape and version, such as `combat/1`. Change the number when the shape changes, so views written for the old one are skipped.
- **The view** is optional: a JS module that draws the state in the webapp. It is sent to the gateway on every connect, so an edited view only needs a reconnect, and it may be at most 512 KiB. Without one, the webapp shows a generic view. How to write one is described in [gateway/webapp/views/README.md](../../gateway/webapp/views/README.md).

### Registering an action

```python
from tablescreen.core.api import Field, quote

services.register_action(
    "heal", {"target": Field(str), "amount": Field(int, minimum=1)},
    "player", self._heal_line, reply=first_line)

def _heal_line(self, args, caller):
    if caller.character is None:
        raise ValueError("Your account isn't linked to a character.")
    return f"hp {quote(args['target'])} +{args['amount']}"
```

- **Fields:** `Field(kind, required=True, choices=(), minimum=-9999, maximum=9999, max_length=64)`, with `kind` either `int` or `str`. Core checks every value against its field before the handler sees it. Unknown fields are refused, and text may not contain control characters.
- **Permission:** `"player"` (players and the dm) or `"dm"` (the dm only). The dm may do everything.
- **The handler** runs on the executor thread, as `handler(args, caller)`. It checks what only the feature knows (own character, whose turn it is) and returns one command line, which runs as if it had been typed, with the source `api:<user>`. The console echoes it as `[api:alice] hp Aria +4`. Build the line with `quote()` for every text value. Raising `ValueError` refuses the action; its message goes to the player.
- **`caller`** is a `Caller` with `user`, `role` (`player` or `dm`), `character` (the linked character, or `None`) and `is_dm`, as the gateway vouches for it.
- **The reply** is what the command printed. A first line starting with `[!]` or `Usage` marks the action as refused; later `[!]` lines are notices. `reply(output)` replaces the text for everyone but the dm, for output meant for the game master only (the combat tracker's hit point numbers).
- **No questions:** the command must not ask anything. `ask()` raises `NoAnswer` for commands that didn't come from the console, and `can_ask()` in `core/console.py` lets a command print a notice for the game master instead.

### The bridge

The application listens on `[api] bridge_host:bridge_port` (127.0.0.1:8765 by default); the gateway connects. Each message is one JSON object per line, at most 1 MiB. The gateway proves it knows the shared secret (`api/bridge.secret`, created on the first start) by answering a challenge with an HMAC within 5 seconds; the secret itself never crosses the wire. The wire format is `bridge_protocol.py` at the repository root, used by both sides.

| From | Message | Meaning |
|---|---|---|
| application | `hello` | The challenge, with the protocol version |
| gateway | `auth` | The answer |
| application | `ready` | The shared features, their actions and views |
| application | `state` | A feature's public state, with its schema and version |
| gateway | `action` | A player's action, with the caller |
| application | `result` | Its answer: done or refused, and the text |
| application | `accounts` | `api user list` or `link`, asked of the gateway |
| gateway | `accounts_result` | The answer |

Everything the gateway sends is untrusted: `bridge_protocol.check()` validates every message before anything else looks at it. An invalid action is refused; any other bad message is ignored, with a note in the console. The connection is dropped only for a failed handshake, or when the gateway stops reading what the application sends. A newly authenticated connection replaces the old one, as when the gateway restarts.

Accounts live in the gateway. The application never sees a password; it only lists the accounts and links characters to them:

```
api                         status: the bridge, the gateway, the shared features
api user list               the gateway's accounts and their characters
api user link alice Aria    the character an account plays
api user unlink alice
```

`tools/bridge_test_client.py` stands in for the gateway, to try the application's side by hand.

### Configuration

```toml
[api]
enabled = false             # true to run the bridge
share = ["combat"]          # one webapp tab per feature listed here
bridge_host = "127.0.0.1"
bridge_port = 8765
secret_file = "api/bridge.secret"

[api.views]
combat = "auto"             # auto | webapp | feature | generic: which view a tab uses
```

`auto` uses the webapp's own view for a tab if there is one, else the feature's, else the generic one; the other values force where the tab starts.

## Shutting down

`exit` in the shell sends a message to the application, which then calls every feature's `shutdown()`, destroys the windows and ends the main loop. The end of input (Ctrl-D on Linux, Ctrl-Z on Windows, or the end of piped input) counts as `exit`, and so does Ctrl-C: it is handled on the main thread and performs the same clean shutdown instead of ending the program with a traceback. A second Ctrl-C ends the program immediately, in case a shutdown hangs. Whenever the executor stops, it sends that message as well, so the windows never outlive the shell. The console and executor threads are daemons on purpose: the console is almost always blocked in `input()`, and a non-daemon thread there would keep the process alive after the main loop ended for any other reason.
