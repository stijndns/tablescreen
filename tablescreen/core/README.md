# Core

The core is the framework the features plug into. It owns the shell, the windows and the message bus, reads the configuration and loads the enabled features. It contains no game logic of its own.

## Two rules

The architecture rests on two rules, and most design decisions follow from them.

1. **Features depend on core; core never imports a feature.** Features are loaded by name with `importlib`, using the names listed under `enabled` in `config.toml`. A disabled feature is never imported, so its commands do not exist and its windows are never created.
2. **Features never reach into each other.** A feature receives a small `ShellServices` object and nothing else: no Tk root, no shell, no message bus, no list of other features. Anything a feature needs that is not on `ShellServices` should be treated as a design question rather than worked around.

## Files

| File | Contents |
|---|---|
| `app.py` | Start-up and shutdown. Reads `config.toml`, creates the hidden Tk root, the bus, the window service and the shell, builds each enabled feature, then runs the Tk main loop. Also makes the process DPI-aware on Windows. |
| `bus.py` | `MessageBus`, the single queue between input threads and the Tk main loop |
| `shell.py` | `Shell`, a `cmd.Cmd` that starts with no commands and lets features register theirs |
| `window.py` | Named windows, content slots, fullscreen, restore and monitor detection |
| `feature.py` | The `Feature` protocol, `ShellServices` and the optional `FeatureBase` |
| `paths.py` | Locations of `assets/`, `combatants/` and `logs/`, independent of the working directory |
| `completion.py` | Tab completion for file names and argument splitting that respects quotes |

## Threading

Tkinter is not thread-safe. Everything that touches a widget has to run on the thread that runs the Tk main loop, which is the main thread. The shell, however, spends nearly all its time waiting for input, so it runs on a separate daemon thread. The two meet in the message bus:

```
shell thread  ──send()──►  queue  ──drain()──►  feature's consumer  (main loop thread)
```

The rules that follow from this:

- Command handlers run on the shell thread. They may change the feature's own state and send messages, and must never touch a widget.
- A feature's bus consumer (`_on_message` by convention) and all view code run on the main loop thread. That is where drawing happens.
- Anything that blocks on `input()`, such as the combat tracker's prompts, belongs on the shell thread. Blocking the main loop would freeze every window.
- Work that does not involve Tk at all, such as audio playback through pygame, may be done directly from a command handler without a bus round trip.
- Long-running background work (the battlemap's video decoder) runs on its own daemon thread and hands results to the main loop through a queue. It never touches Tk either.

Because state is changed on the shell thread and read on the main loop thread, features keep shared state in a form that is safe to read at any moment. The battlemap, for instance, stores sprites and areas of effect as tuples of frozen records that are replaced as a whole on every change, never modified in place.

When commands arrive faster than the main loop handles them (pasted or piped input), the main loop may only see the latest state. A message that reports on its own command should therefore carry the values it reports, instead of reading them from the state when it is handled.

### Why the bus has two wake-up paths

`send()` puts a message on the queue and asks Tk to fire a `<<QueueMsg>>` event, which makes the main loop drain the queue almost immediately. Tk can silently drop generated events, notably for windows that are not mapped yet or are withdrawn, so the bus also polls the queue every 100 ms. Without the poll, a dropped event would leave a message waiting forever. Both paths are needed; this has gone wrong in practice.

Draining always empties the whole queue, because Tk merges events and there is no one-to-one relation between events and messages. Messages for a feature that is not loaded are dropped with a single warning, which is what makes running any subset of features safe.

## Windows, slots and views

- A **window** is a named Tk `Toplevel`, created on first request and owned by core. Its default size comes from `[windows]` in the configuration. The Tk root itself stays hidden and only runs the main loop.
- A **content slot** is one feature's `tk.Frame` inside a window. A window can hold several slots but shows only one: calling `show()` on a slot hides its siblings. This is how the combat screen and the image viewer share the main screen without knowing about each other.
- A **view** is owned by a feature and draws the feature's state into a slot. A feature may have several views of the same state; `FeatureBase.views` is a list for that reason.

Closing a window with its close button only hides it; slots and their contents survive. `Window.restore()` brings it back.

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

    def _do_say(self, arg):         # shell thread: state and messages only
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

## Shutting down

`exit` in the shell sends a message to the application, which then calls every feature's `shutdown()`, destroys the windows and ends the main loop. The shell thread is a daemon on purpose: it is almost always blocked in `input()`, and a non-daemon thread there would keep the process alive after the main loop ended for any other reason.
