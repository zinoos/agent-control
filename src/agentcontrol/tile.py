"""One agent session: its Claude Code terminal (tmux attach in VTE), which you type into directly, and its
live desktop stream while it is using one."""

from __future__ import annotations

import os
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango, Vte  # noqa: E402

from . import sessions  # noqa: E402
from . import stream  # noqa: E402
from .stream import StreamView  # noqa: E402

PALETTE = ["#1b1d22", "#ff6b6b", "#69db7c", "#ffd43b", "#74c0fc", "#da77f2", "#66d9e8", "#ced4da",
           "#495057", "#ff8787", "#8ce99a", "#ffe066", "#a5d8ff", "#e599f7", "#99e9f2", "#f8f9fa"]


def _rgba(hexcolor: str) -> Gdk.RGBA:
    c = Gdk.RGBA()
    c.parse(hexcolor)
    return c


class SessionTile(Gtk.Box):
    def __init__(self, app, name: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.app = app
        self.name = name
        self.session: sessions.Session | None = None
        self.mode = "auto"  # auto | stream | terminal
        self.add_css_class("tile")

        # ---- header
        head = Gtk.Box(spacing=8)
        head.add_css_class("tile-head")
        self.dot = Gtk.Label(label="●")
        self.dot.add_css_class("dot")
        self.title = Gtk.Label(label=name, xalign=0)
        self.title.add_css_class("tile-name")
        self.sub = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.sub.add_css_class("tile-sub")
        self.live = Gtk.Label(label="LIVE")
        self.live.add_css_class("live-pill")
        self.live.set_visible(False)
        head.append(self.dot)
        head.append(self.title)
        head.append(self.sub)
        head.append(self.live)
        self.view_btn = Gtk.Button(icon_name="video-display-symbolic", tooltip_text="Stream / terminal / auto")
        self.view_btn.connect("clicked", self._cycle_mode)
        self.view_btn.set_visible(stream.AVAILABLE)  # nothing to stream without hypr-computer
        stop = Gtk.Button(icon_name="media-playback-stop-symbolic", tooltip_text="Interrupt (Esc)")
        stop.connect("clicked", lambda *_: self._bg(sessions.interrupt, name))
        focus = Gtk.Button(icon_name="view-fullscreen-symbolic", tooltip_text="Focus (double-click header)")
        focus.connect("clicked", lambda *_: app.toggle_focus(name))
        hide = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Hide from the grid (session keeps running)")
        hide.connect("clicked", lambda *_: app.set_shown(name, False))
        for b in (self.view_btn, stop, focus, hide):
            b.add_css_class("flat")
            head.append(b)
        dbl = Gtk.GestureClick()
        dbl.connect("pressed", lambda g, n, x, y: n == 2 and app.toggle_focus(name))
        head.add_controller(dbl)
        self.append(head)

        # ---- body: stream | terminal
        self.stream = StreamView()
        self.term = Vte.Terminal(hexpand=True, vexpand=True)
        self.term.set_font(Pango.FontDescription.from_string(os.environ.get("AGENTCONTROL_FONT", "monospace 10")))
        self.term.set_colors(_rgba("#e9ecef"), _rgba("#14161a"), [_rgba(c) for c in PALETTE])
        self.term.set_scrollback_lines(0)  # tmux keeps the history
        self.term.set_mouse_autohide(True)
        self.term.connect("child-exited", self._on_exit)
        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, wide_handle=True, vexpand=True)
        self.paned.set_start_child(self.stream)
        self.paned.set_end_child(self.term)
        self.paned.set_resize_start_child(True)
        self.paned.set_resize_end_child(False)
        self.paned.set_shrink_end_child(False)
        self.append(self.paned)
        self.stream.set_visible(False)
        self._spawn()

    # ------------------------------------------------------------ terminal

    def _spawn(self):
        argv = sessions.attach_argv(self.name)
        env = [f"{k}={v}" for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")] + ["TERM=xterm-256color"]
        self.term.spawn_async(Vte.PtyFlags.DEFAULT, os.path.expanduser("~"), argv, env,
                              GLib.SpawnFlags.SEARCH_PATH, None, None, -1, None, None)

    def _on_exit(self, *_):
        if any(s.name == self.name for s in sessions.list_sessions()):
            GLib.timeout_add(300, lambda: (self._spawn(), False)[1])  # detached by accident: reattach
        else:
            self.sub.set_label("session ended")

    # ------------------------------------------------------------ background actions

    def _bg(self, fn, *a):
        threading.Thread(target=lambda: self._safe(fn, *a), daemon=True).start()

    def _safe(self, fn, *a):
        try:
            fn(*a)
        except Exception as ex:
            GLib.idle_add(self.app.toast, f"{self.name}: {ex}")

    # ------------------------------------------------------------ state

    def _cycle_mode(self, *_):
        self.mode = {"auto": "stream", "stream": "terminal", "terminal": "auto"}[self.mode]
        self.app.toast(f"{self.name}: view = {self.mode}")
        self.update(self.session)

    def update(self, s: sessions.Session | None):
        self.session = s
        if s is None:
            return
        self.sub.set_label(s.label if s.label != s.name else s.cwd.replace(os.path.expanduser("~"), "~"))
        for st in ("working", "idle", "starting"):
            (self.dot.add_css_class if s.state == st else self.dot.remove_css_class)(st)
        desk = s.desk
        live = bool(desk and desk.active)
        show_stream = stream.AVAILABLE and bool(desk) and (self.mode == "stream" or (self.mode == "auto" and live))
        self.live.set_visible(live)
        if desk:
            where = f"{desk.name} · Super+{'0' if desk.place == 10 else desk.place}" if desk.place else desk.name
            self.stream.set_source(desk.wl_socket, where)
            self.stream.set_activity(live, desk.last_text)
        if show_stream != self.stream.get_visible():
            self.stream.set_visible(show_stream)
            if show_stream:
                GLib.idle_add(self._split)

    def _split(self):
        w = self.paned.get_width()
        if w > 0:
            self.paned.set_position(int(w * 0.66))  # stream left, the session's terminal on the right
        return False

    def set_fps(self, fps: int):
        self.stream.fps = fps
