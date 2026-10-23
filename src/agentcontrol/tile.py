"""One agent session: its Claude Code terminal (tmux attach in VTE), which you type into directly, and its
live desktop stream while it is using one. Sessions running outside agentControl get a read-only tile that follows
their transcript instead."""

from __future__ import annotations

import os
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango, Vte  # noqa: E402

from . import external, sessions, settings  # noqa: E402
from . import stream  # noqa: E402
from .stream import StreamView  # noqa: E402

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
        self.mode = app.cfg["view"]  # auto | stream | terminal
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
        focus = Gtk.Button(icon_name="view-fullscreen-symbolic", tooltip_text="Focus (double-click header)")
        focus.connect("clicked", lambda *_: app.toggle_focus(name))
        hide = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Hide from the grid (session keeps running)")
        hide.connect("clicked", lambda *_: app.set_shown(name, False))
        for b in (self.view_btn, *self._actions(), focus, hide):
            b.add_css_class("flat")
            head.append(b)
        dbl = Gtk.GestureClick()
        dbl.connect("pressed", lambda g, n, x, y: n == 2 and app.toggle_focus(name))
        head.add_controller(dbl)
        self.append(head)

        # ---- body: stream | terminal
        self.stream = StreamView()
        body = self._body()
        hover = Gtk.EventControllerMotion()
        hover.connect("enter", self._focus_on_hover)
        self.term.add_controller(hover)
        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, wide_handle=True, vexpand=True)
        self.paned.set_start_child(self.stream)
        self.paned.set_end_child(body)
        self.paned.set_resize_start_child(True)
        self.paned.set_resize_end_child(False)
        self.paned.set_shrink_end_child(False)
        self.append(self.paned)
        self.stream.set_visible(False)
        self._start()

    def _actions(self) -> list[Gtk.Button]:
        stop = Gtk.Button(icon_name="media-playback-stop-symbolic", tooltip_text="Interrupt (Esc)")
        stop.connect("clicked", lambda *_: self._bg(sessions.interrupt, self.name))
        return [stop]

    # ------------------------------------------------------------ terminal

    def _body(self) -> Gtk.Widget:
        self.term = Vte.Terminal(hexpand=True, vexpand=True)
        self.apply_look()
        self.term.set_scrollback_lines(0)  # tmux keeps the history
        self.term.set_mouse_autohide(True)
        self.term.connect("child-exited", self._on_exit)
        return self.term

    def _start(self):
        self._spawn()

    def apply_look(self):
        cfg = self.app.cfg
        fg, bg, pal = settings.scheme(cfg["scheme"])
        self.term.set_font(Pango.FontDescription.from_string(cfg["font"]))
        bg = _rgba(bg)
        bg.alpha = cfg["opacity"]
        self.term.set_colors(_rgba(fg), bg, [_rgba(c) for c in pal])

    def _spawn(self):
        argv = sessions.attach_argv(self.name)
        env = [f"{k}={v}" for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")] + ["TERM=xterm-256color"]
        self.term.spawn_async(Vte.PtyFlags.DEFAULT, os.path.expanduser("~"), argv, env,
                              GLib.SpawnFlags.SEARCH_PATH, None, None, -1, None, None)

    def _focus_on_hover(self, *_):
        # don't steal focus from a text field the user is typing in (search, message box)
        if not isinstance(self.term.get_root().get_focus(), Gtk.Editable):
            self.term.grab_focus()

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


class ExternalTile(SessionTile):
    """A Claude Code session that runs somewhere else (a terminal, an editor, Claude Desktop). Its terminal can't be
    attached, so the tile shows its transcript as it grows, read-only, and can raise the window it runs in."""

    MAX_LINES = 3000
    MARKS = {"prompt": "❯ ", "tool": "● ", "result": "  ⎿ ", "text": "", "note": "── "}

    def _actions(self) -> list[Gtk.Button]:
        self.window_btn = Gtk.Button(icon_name="go-jump-symbolic", tooltip_text="Show its window")
        self.window_btn.connect("clicked", lambda *_: self._bg(self._raise))
        self.window_btn.set_visible(False)
        return [self.window_btn]

    def _body(self) -> Gtk.Widget:
        self.term = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                 left_margin=10, right_margin=10, top_margin=8, bottom_margin=8)
        self.term.add_css_class("transcript")
        buf = self.term.get_buffer()
        self.tags = {k: buf.create_tag(k) for k in ("base", "prompt", "text", "tool", "result", "note")}  # later win
        self.tags["prompt"].set_property("weight", Pango.Weight.BOLD)
        self.tags["note"].set_property("style", Pango.Style.ITALIC)
        self.end = buf.create_mark("end", buf.get_end_iter(), False)
        self.scroll = Gtk.ScrolledWindow(child=self.term, hexpand=True, vexpand=True,
                                         hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.apply_look()
        return self.scroll

    def _start(self):
        self.tail: external.Tail | None = None

    def apply_look(self):
        fg, _bg, pal = settings.scheme(self.app.cfg["scheme"])
        self.tags["base"].set_property("font-desc", Pango.FontDescription.from_string(self.app.cfg["font"]))
        for tag, color in (("base", fg), ("prompt", pal[12]), ("tool", pal[7]), ("result", pal[8]), ("note", pal[8])):
            self.tags[tag].set_property("foreground-rgba", _rgba(color))

    def _raise(self):
        if not (self.session and external.raise_window(self.session)):
            raise RuntimeError("its window is gone")

    def update(self, s: external.External | None):
        super().update(s)
        if s is None:
            return
        self.window_btn.set_visible(bool(s.window))
        self.window_btn.set_tooltip_text(f"Show its window ({s.where})")
        self.title.set_tooltip_text(f"Runs in {s.where}, outside agentControl: read-only here")
        if self.tail is None or self.tail.path != s.transcript:  # new, or /clear started another transcript
            self.tail = external.Tail(s.transcript)
            self.term.get_buffer().set_text("")
        if s.transcript:
            self._add_lines(external.render(self.tail.read()))

    def _add_lines(self, lines: list[tuple[str, str]]):
        if not lines:
            return
        adj = self.scroll.get_vadjustment()
        follow = adj.get_value() >= adj.get_upper() - adj.get_page_size() - 40  # at the bottom: stay there
        buf = self.term.get_buffer()
        for kind, text in lines:
            if kind == "prompt" and buf.get_char_count():
                buf.insert_with_tags(buf.get_end_iter(), "\n", self.tags["base"])
            buf.insert_with_tags(buf.get_end_iter(), self.MARKS[kind] + text + "\n", self.tags["base"],
                                 self.tags[kind])
        extra = buf.get_line_count() - self.MAX_LINES
        if extra > 0:
            buf.delete(buf.get_start_iter(), buf.get_iter_at_line(extra)[1])
        if follow:
            GLib.idle_add(lambda: self.term.scroll_to_mark(self.end, 0, False, 0, 1) and False)
