"""Token usage panel: the tokscale TUI (https://github.com/junhoyeo/tokscale) in a terminal on the right.
Tabs: Overview, Usage (subscription quotas), Models, Daily, Hourly, Monthly, Sessions, Projects, Stats, Agents.

The command is $AGENTCONTROL_TOKSCALE if set (e.g. "bunx tokscale@latest"), else `tokscale` on PATH, else
`npx -y tokscale@latest`. If a `token-monitor` launcher is on PATH (the Token Monitor desktop app), the panel
gets a button that opens it."""

from __future__ import annotations

import os
import shlex
import shutil

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")
gi.require_version("Pango", "1.0")
from gi.repository import GLib, Gtk, Pango, Vte  # noqa: E402

from .tile import PALETTE, _rgba  # noqa: E402

TOKEN_MONITOR = os.environ.get("AGENTCONTROL_TOKEN_MONITOR") or shutil.which("token-monitor")


def tokscale_argv() -> list[str] | None:
    if os.environ.get("AGENTCONTROL_TOKSCALE"):
        return shlex.split(os.environ["AGENTCONTROL_TOKSCALE"])
    if shutil.which("tokscale"):
        return [shutil.which("tokscale")]
    if shutil.which("npx"):
        return [shutil.which("npx"), "-y", "tokscale@latest"]
    return None


class TokenPanel(Gtk.Box):
    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.app = app
        self.add_css_class("tile")
        self.started = False

        head = Gtk.Box(spacing=8)
        head.add_css_class("tile-head")
        title = Gtk.Label(label="Token usage", xalign=0)
        title.add_css_class("tile-name")
        hint = Gtk.Label(label="tokscale · ←→/Tab switch view", xalign=0, hexpand=True,
                         ellipsize=Pango.EllipsizeMode.END)
        hint.add_css_class("tile-sub")
        head.append(title)
        head.append(hint)
        buttons = [("view-refresh-symbolic", "Rescan (r)", lambda *_: self._key("r"))]
        if TOKEN_MONITOR:
            buttons.append(("window-new-symbolic", "Open the Token Monitor app", lambda *_: self._open_app()))
        buttons.append(("window-close-symbolic", "Close panel (Ctrl+Shift+T)", lambda *_: app.toggle_tokens(False)))
        for icon, tip, cb in buttons:
            b = Gtk.Button(icon_name=icon, tooltip_text=tip)
            b.add_css_class("flat")
            b.connect("clicked", cb)
            head.append(b)
        self.append(head)

        self.term = Vte.Terminal(hexpand=True, vexpand=True)
        self.term.set_font(Pango.FontDescription.from_string(os.environ.get("AGENTCONTROL_FONT", "monospace 10")))
        self.term.set_colors(_rgba("#e9ecef"), _rgba("#14161a"), [_rgba(c) for c in PALETTE])
        self.term.set_scrollback_lines(0)
        self.term.connect("child-exited", self._on_exit)
        self.append(self.term)

    def start(self):
        """Spawn the TUI on first open; it keeps running (and its data loaded) while the panel is hidden."""
        if self.started:
            return
        argv = tokscale_argv()
        if not argv:
            self.term.feed(b"\x1b[1mtokscale not found.\x1b[0m\r\n\r\nInstall it (npm i -g tokscale, or bun / Node's npx),"
                           b" or point AGENTCONTROL_TOKSCALE at it, then reopen this panel.\r\n")
            return
        self.started = True
        env = [f"{k}={v}" for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")] + ["TERM=xterm-256color"]
        self.term.spawn_async(Vte.PtyFlags.DEFAULT, os.path.expanduser("~"), [*argv, "tui"], env,
                              GLib.SpawnFlags.DEFAULT, None, None, -1, None, None)

    def _on_exit(self, _term, status):
        # q in the TUI (or a crash) ends it; show why and start a fresh one on the next open.
        self.started = False
        self.term.feed(f"\r\n\x1b[2mtokscale exited ({status}). Close and reopen the panel to restart.\x1b[0m\r\n".encode())

    def _key(self, text):
        if self.started:
            self.term.feed_child(text.encode())
        self.term.grab_focus()

    def _open_app(self):
        try:
            GLib.spawn_async(shlex.split(TOKEN_MONITOR), flags=GLib.SpawnFlags.DEFAULT)
        except GLib.Error as ex:
            self.app.toast(f"Token Monitor: {ex.message}")
