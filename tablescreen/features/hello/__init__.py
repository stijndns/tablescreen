import tkinter as tk
from tablescreen.core.feature import FeatureBase

class _View:
    def __init__(self, parent):
        self.label = tk.Label(parent, text="(nothing yet)",
                              bg="black", fg="white", font=("Consolas", 32))
        self.label.pack(fill="both", expand=True)
    def render(self, snap):
        self.label.config(text=snap["text"])

class HelloFeature(FeatureBase):
    name = "hello"

    def build(self, services):
        self.services = services
        self.state = {"text": "hello"}
        self.slot = services.slot(services.window_name())
        self.views.append(_View(self.slot.frame))
        services.register_command("say", self._do_say,
                                  help_text="say <text> — show text on screen.")
        services.register_command("big", self._do_big,
                                  help_text="big — fullscreen the window.")
        services.register_command("mirror", self._do_mirror,
                                  help_text="mirror <window> — open a mirror of the text on another window.")
        services.subscribe(self._on_message)

    def snapshot(self):
        return dict(self.state)

    # shell thread: only touch state + send
    def _do_say(self, arg):
        self.state["text"] = arg.strip() or "(empty)"
        self.services.send(self.name, "update")

    def _do_big(self, arg):
        self.services.send(self.name, "fullscreen")

    def _do_mirror(self, arg):
        window_name = arg.strip() or "dm"
        self.services.send(self.name, "mirror", window_name)

    # mainloop thread: safe to touch widgets
    def _on_message(self, msg):
        if msg.command == "fullscreen":
            win = self.services.windows.get_window(self.services.window_name())
            m = win.fullscreen()
            print(f"[+] Fullscreen on monitor {m}")
            return

        if msg.command == "mirror":
            window_name = msg.arg
            slot = self.services.slot(window_name, f"{self.name}_mirror")
            self.views.append(_View(slot.frame))
            slot.show()
            self.refresh()
            print(f"[+] Mirror opened on window '{window_name}'.")
            return
                
        self.slot.show()
        self.refresh()

FEATURE = HelloFeature()