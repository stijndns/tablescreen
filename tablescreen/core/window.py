"""
window.py — Named windows and content slots.

Features do not own windows. Core owns them, and features rent space:

    slot = services.windows.content_slot("player", "combat")
    my_view = CombatView(slot.frame)
    slot.show()

A *window* is a named Toplevel (``"player"``, ``"table"``, ``"dm"``). A
*slot* is one feature's content inside a window. Several slots may live on
one window; at most one is visible at a time, and ``show()`` on a slot hides
its siblings — "last shown wins". That single rule reproduces the old
behaviour where the combat tracker replaced the image on the players'
screen, without either feature knowing about the other.

Windows are created on demand, so a window named at runtime (a mirror on a
DM screen, say) works exactly like one named in config.

The root itself is never used as a feature window. It stays hidden and owns
only the mainloop, so no feature is accidentally special.
"""

from __future__ import annotations

import platform
import tkinter as tk
from typing import Callable, Optional

try:
    from screeninfo import get_monitors
except Exception:      # screeninfo missing or no display
    get_monitors = None

CURRENT_OS = platform.system()

DEFAULT_GEOMETRY = "800x600"
DEFAULT_BG = "black"


def monitor_at(monitors, x: int, y: int):
    """The monitor containing point (x, y), else the one nearest to it.

    ``monitors`` are screeninfo Monitor objects (anything with x, y, width,
    height). Returns None only when the list is empty, so a point in the gap
    of an uneven multi-monitor layout still resolves to a real monitor.
    """
    def distance_sq(m) -> int:
        # 0 when the point is inside the monitor's rectangle.
        dx = max(m.x - x, 0, x - (m.x + m.width - 1))
        dy = max(m.y - y, 0, y - (m.y + m.height - 1))
        return dx * dx + dy * dy

    return min(monitors, key=distance_sq, default=None)


class ContentSlot:
    """One feature's content inside a window.

    The feature builds its widgets into ``slot.frame`` and calls ``show()``
    when it wants the screen. The slot never destroys its frame, so hiding
    and re-showing preserves whatever the feature drew and whatever state
    its view holds.
    """

    def __init__(self, window: "Window", slot_id: str):
        self._window = window
        self.slot_id = slot_id
        # The frame the feature draws into. Created once, packed/unpacked as
        # the slot is shown and hidden.
        self.frame = tk.Frame(window.toplevel, bg=DEFAULT_BG)
        self._visible = False

    @property
    def visible(self) -> bool:
        return self._visible

    def show(self) -> None:
        """Make this slot the visible content of its window."""
        self._window.show_slot(self.slot_id)

    def hide(self) -> None:
        """Hide this slot, leaving the window showing nothing."""
        if self._visible:
            self.frame.pack_forget()
            self._visible = False

    # Called by Window; features use show()/hide() instead.
    def _pack(self) -> None:
        if not self._visible:
            self.frame.pack(fill="both", expand=True)
            self._visible = True


class Window:
    """A named Toplevel hosting one or more content slots."""

    def __init__(self, root: tk.Tk, name: str, geometry: str = DEFAULT_GEOMETRY):
        self.name = name
        self.toplevel = tk.Toplevel(root)
        self.toplevel.title(name)
        self.toplevel.geometry(geometry)
        self.toplevel.configure(bg=DEFAULT_BG)

        self._slots: dict[str, ContentSlot] = {}
        self._is_fullscreen = False
        self._default_geometry = geometry
        # "WxH+X+Y" where the window manager first put it; None until mapped.
        self._initial_geometry: Optional[str] = None
        self.toplevel.bind("<Map>", self._on_first_map, add="+")

        # Callbacks fired after the window geometry changes (fullscreen,
        # restore, resize). Content re-renders itself here — core does not
        # know what the content is, so it just notifies.
        self._on_geometry_change: list[Callable[[], None]] = []

        # Closing a feature window hides it rather than destroying it, so the
        # feature's content and state survive and can be re-shown.
        self.toplevel.protocol("WM_DELETE_WINDOW", self.hide_window)

    # ── Geometry on record ────────────────────────────────────────────────

    @property
    def default_geometry(self) -> str:
        """The geometry the window was created with; `restore()` returns to it."""
        return self._default_geometry

    @property
    def initial_geometry(self) -> Optional[str]:
        """Size and position ("WxH+X+Y") when the window was first mapped,
        e.g. to open another window in the same place. None until then."""
        return self._initial_geometry

    def _on_first_map(self, event) -> None:
        # Child widgets' <Map> events reach this binding too (bindtags).
        if event.widget is not self.toplevel or self._initial_geometry:
            return
        # The window manager may still be settling the position; read it
        # once the event loop is idle.
        self.toplevel.after_idle(self._record_initial_geometry)

    def _record_initial_geometry(self) -> None:
        if self._initial_geometry is None:
            try:
                self._initial_geometry = self.toplevel.geometry()
            except tk.TclError:
                pass        # destroyed in the meantime

    def on_close(self, callback: Callable[[], None]) -> None:
        """Replace what the X button does (default: hide the window)."""
        self.toplevel.protocol("WM_DELETE_WINDOW", callback)

    # ── Slots ─────────────────────────────────────────────────────────────

    def slot(self, slot_id: str) -> ContentSlot:
        """Get (creating if needed) the slot with this id."""
        if slot_id not in self._slots:
            self._slots[slot_id] = ContentSlot(self, slot_id)
        return self._slots[slot_id]

    def show_slot(self, slot_id: str) -> None:
        """Show one slot and hide every other slot on this window.

        This is the "last shown wins" rule. It is what makes the combat
        tracker replace the image when both features share a window, with
        neither feature needing to know the other exists.
        """
        target = self._slots.get(slot_id)
        if target is None:
            return
        for sid, slot in self._slots.items():
            if sid != slot_id:
                slot.hide()
        target._pack()
        self.toplevel.deiconify()

    def visible_slot(self) -> Optional[str]:
        for sid, slot in self._slots.items():
            if slot.visible:
                return sid
        return None

    # ── Geometry-change notification ──────────────────────────────────────

    def on_geometry_change(self, callback: Callable[[], None]) -> None:
        """Register a callback fired after fullscreen/restore/resize.

        Content uses this to re-render at the new size. Replaces the
        ``render_image()`` calls that used to be hard-coded into the window
        controls.
        """
        self._on_geometry_change.append(callback)

    def _notify_geometry_change(self) -> None:
        for cb in self._on_geometry_change:
            try:
                cb()
            except Exception as exc:
                print(f"[!] Error in geometry callback for '{self.name}': {exc}")

    # ── Window controls ───────────────────────────────────────────────────

    def fullscreen(self):
        """Borderless fullscreen on whichever monitor the window sits on.

        Restores first so the position measured below is the real windowed
        position, not a stale fullscreen geometry.
        """
        self.restore()

        m = self.monitor()
        if m is not None:
            self._apply_fullscreen(m)
            return m

        # No monitor matched (or screeninfo unavailable): fall back to Tk's
        # own fullscreen on the current screen.
        try:
            self.toplevel.attributes("-fullscreen", True)
        except tk.TclError:
            pass
        self._is_fullscreen = True
        self._notify_geometry_change()
        return None

    def monitor(self):
        """The screeninfo monitor the window is on, judged by its centre.

        Falls back to the monitor nearest the centre if none contains it.
        None only when screeninfo is unavailable, fails, or finds nothing.
        screeninfo and Tk both measure in physical pixels because the app
        is made DPI aware at startup (see app.enable_dpi_awareness).
        """
        if get_monitors is None:
            return None
        top = self.toplevel
        # Centre, not top-left: a maximised Windows window sits at about
        # x=-8 (invisible borders), which is on no monitor, and a window
        # dragged mostly onto the TV still has its corner on the laptop.
        cx = top.winfo_x() + top.winfo_width() // 2
        cy = top.winfo_y() + top.winfo_height() // 2
        try:
            monitors = get_monitors()
        except Exception:
            return None
        return monitor_at(monitors, cx, cy)

    def _apply_fullscreen(self, monitor) -> None:
        if CURRENT_OS == "Windows":
            # overrideredirect strips the title bar; geometry positions it
            # over exactly one monitor.
            self.toplevel.geometry(
                f"{monitor.width}x{monitor.height}+{monitor.x}+{monitor.y}")
            self.toplevel.overrideredirect(True)
        else:
            self.toplevel.attributes("-fullscreen", True)
        self._is_fullscreen = True
        self.toplevel.update_idletasks()
        self._notify_geometry_change()

    def restore(self) -> bool:
        """Return to the default windowed size, bringing the window back if it
        was closed (X) or minimised. Returns True if it had been closed."""
        was_closed = self.is_closed
        self.toplevel.overrideredirect(False)
        try:
            self.toplevel.attributes("-fullscreen", False)
        except tk.TclError:
            pass
        self.toplevel.state("normal")       # un-withdraws and un-iconifies
        self.toplevel.geometry(self._default_geometry)
        self.toplevel.lift()                # back in front, not behind others
        self._is_fullscreen = False
        self._notify_geometry_change()
        return was_closed

    @property
    def is_closed(self) -> bool:
        """Hidden via the X button (withdrawn), as opposed to minimised."""
        return self.toplevel.state() == "withdrawn"

    def minimize(self) -> None:
        """Iconify. A borderless window must be restored first, or it cannot
        be iconified properly."""
        if self.toplevel.overrideredirect():
            self.restore()
        self.toplevel.iconify()

    def hide_window(self) -> None:
        """Withdraw the whole window; slots and their content survive."""
        self.toplevel.withdraw()

    def show_window(self) -> None:
        """Un-hide or un-minimise at the current size, in front."""
        self.toplevel.deiconify()
        self.toplevel.lift()

    @property
    def is_fullscreen(self) -> bool:
        return self._is_fullscreen


class WindowService:
    """Vends named windows and their content slots.

    Features receive this (never the root) so they can obtain somewhere to
    draw without being able to touch global Tk state or another feature's
    window internals.
    """

    def __init__(self, root: tk.Tk, geometries: Optional[dict[str, str]] = None):
        self._root = root
        self._windows: dict[str, Window] = {}
        # Per-window default sizes from config's [windows] table.
        self._geometries: dict[str, str] = dict(geometries or {})

    def get_window(self, name: str, geometry: Optional[str] = None) -> Window:
        """Get the named window, creating it on first request.

        On-demand creation is what lets a window named at runtime (e.g. a
        mirror on a DM screen) work exactly like one declared in config.

        Size on creation: an explicit ``geometry`` wins, then the window's
        entry in config, then DEFAULT_GEOMETRY. An existing window is
        returned unchanged.
        """
        if name not in self._windows:
            size = geometry or self._geometries.get(name, DEFAULT_GEOMETRY)
            self._windows[name] = Window(self._root, name, size)
        return self._windows[name]

    def content_slot(self, window_name: str, slot_id: str) -> ContentSlot:
        """Convenience: get a slot on a (possibly new) named window."""
        return self.get_window(window_name).slot(slot_id)

    def window_names(self) -> list[str]:
        return sorted(self._windows)

    def remove_window(self, name: str) -> bool:
        """Destroy the named window and everything in it. A later
        `get_window(name)` creates a fresh one. Returns False if there was no
        such window. Content that needs cleanup (threads, images) must be
        released by its owner first."""
        window = self._windows.pop(name, None)
        if window is None:
            return False
        try:
            window.toplevel.destroy()
        except tk.TclError:
            pass
        return True

    def destroy_all(self) -> None:
        for window in self._windows.values():
            try:
                window.toplevel.destroy()
            except tk.TclError:
                pass
        self._windows.clear()