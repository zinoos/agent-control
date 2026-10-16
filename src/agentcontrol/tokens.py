"""Token usage panel: the TokenTracker dashboard (https://github.com/xiufengsun/TokenTracker) in a web view on
the right. Usage trends, cost, models, projects, sessions and subscription limits across Claude Code, Codex,
Cursor, OpenCode and many other clients, all read from local logs.

A TokenTracker server already running on 127.0.0.1:7680 (its desktop app, or `tokentracker serve`) is reused.
Otherwise the panel starts `<cmd> serve --no-open` on first open and stops it when agentControl quits. The
command is $AGENTCONTROL_TOKENTRACKER if set, else `tokentracker` on PATH, else `npx -y tokentracker-cli@latest`.
Needs WebKitGTK 6.0 (GTK 4)."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import threading
import urllib.request

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import settings  # noqa: E402

try:
    gi.require_version("WebKit", "6.0")
    from gi.repository import WebKit  # noqa: E402
except (ValueError, ImportError):
    WebKit = None

DEFAULT_URL = "http://127.0.0.1:7680"
# serve prints "dashboard running at:" and then the URL; init's banner before it names a URL too.
URL_RE = re.compile(r"https?://(?:127\.0\.0\.1|localhost):\d+")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def tracker_argv() -> list[str] | None:
    if settings.get("tokentracker").strip():
        return shlex.split(settings.get("tokentracker"))
    if shutil.which("tokentracker"):
        return [shutil.which("tokentracker")]
    if shutil.which("npx"):
        return [shutil.which("npx"), "-y", "tokentracker-cli@latest"]
    return None


def running_server(url: str = DEFAULT_URL) -> bool:
    """True if a TokenTracker server answers at url (its health endpoint reports a serverVersion)."""
    try:
        with urllib.request.urlopen(f"{url}/api/local-auth", timeout=1.5) as r:
            return bool(json.loads(r.read(4096)).get("serverVersion"))
    except (OSError, ValueError, AttributeError):
        return False


class TokenPanel(Gtk.Box):
    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.app = app
        self.add_css_class("tile")
        self.started = False
        self.url: str | None = None  # the server's address, once known
        self.loaded = False
        self.proc: Gio.Subprocess | None = None
        self.log: list[str] = []
        self.listening = False

        head = Gtk.Box(spacing=8)
        head.add_css_class("tile-head")
        title = Gtk.Label(label="Token usage", xalign=0)
        title.add_css_class("tile-name")
        self.hint = Gtk.Label(label="TokenTracker", xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.hint.add_css_class("tile-sub")
        head.append(title)
        head.append(self.hint)
        for icon, tip, cb in (("view-refresh-symbolic", "Reload", lambda *_: self.reload()),
                              ("web-browser-symbolic", "Open in the browser", lambda *_: self._open_browser()),
                              ("window-close-symbolic", "Close panel (Ctrl+Shift+T)",
                               lambda *_: app.toggle_tokens(False))):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip)
            b.add_css_class("flat")
            b.connect("clicked", cb)
            head.append(b)
        self.append(head)

        self.stack = Gtk.Stack(hexpand=True, vexpand=True)
        status = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER,
                         margin_start=24, margin_end=24)
        self.spinner = Gtk.Spinner(spinning=False, width_request=32, height_request=32)
        self.message = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, selectable=True)
        self.output = Gtk.Label(wrap=True, xalign=0, selectable=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.output.add_css_class("tile-sub")
        self.output.add_css_class("monospace")
        self.retry = Gtk.Button(label="Retry", halign=Gtk.Align.CENTER, visible=False)
        self.retry.connect("clicked", lambda *_: self.reload())
        for w in (self.spinner, self.message, self.output, self.retry):
            status.append(w)
        self.stack.add_named(status, "status")

        self.web = None
        if WebKit:
            self.web = WebKit.WebView(hexpand=True, vexpand=True)
            self.web.set_background_color(Gdk.RGBA(red=0.078, green=0.086, blue=0.102, alpha=1))
            self.web.connect("decide-policy", self._on_policy)
            self.web.connect("load-failed", self._on_load_failed)
            self.stack.add_named(self.web, "web")
        self.append(self.stack)

    # ------------------------------------------------------------ server

    def start(self):
        """Find or start the server on first open; it keeps running (and the page loaded) while hidden."""
        if self.started:
            return
        if not self.web:
            return self._fail("WebKitGTK 6.0 not found.\n\nInstall its GObject bindings (Fedora: webkitgtk6.0,"
                              " Debian/Ubuntu: gir1.2-webkit-6.0, Arch: webkitgtk-6.0), then restart agentControl.")
        self.started = True
        self._status("Looking for TokenTracker…", busy=True)
        threading.Thread(target=self._probe, daemon=True).start()

    def _probe(self):
        found = running_server()
        GLib.idle_add(self._load if found else self._spawn, *([DEFAULT_URL] if found else []))

    def _spawn(self):
        argv = tracker_argv()
        if not argv:
            return self._fail("TokenTracker not found.\n\nInstall it (npm i -g tokentracker-cli, or Node's npx),"
                              " or point AGENTCONTROL_TOKENTRACKER at it, then press Retry.")
        launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE
                                              | Gio.SubprocessFlags.STDIN_PIPE)
        for k in ("TMUX", "TMUX_PANE"):
            launcher.unsetenv(k)
        launcher.setenv("NO_COLOR", "1", True)
        launcher.setenv("FORCE_COLOR", "0", True)
        launcher.set_cwd(os.path.expanduser("~"))
        try:
            self.proc = launcher.spawnv([*argv, "serve", "--no-open"])
        except GLib.Error as ex:
            return self._fail(f"Couldn't start {argv[0]}: {ex.message}")
        self.log = []
        self.listening = False
        self._status("Starting TokenTracker (the first run scans all local logs, which can take a minute)…",
                     busy=True)
        self.proc.wait_async(None, self._on_exit, self.proc)
        self._read(Gio.DataInputStream.new(self.proc.get_stdout_pipe()), self.proc)

    def _read(self, stream, proc):
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self._on_line, proc)

    def _on_line(self, stream, result, proc):
        try:
            line, _ = stream.read_line_finish_utf8(result)
        except GLib.Error:
            line = None
        if line is None or proc is not self.proc:
            return
        line = ANSI_RE.sub("", line).strip()
        if line:
            self.log = (self.log + [line])[-8:]
            if not self.url:
                self.listening = self.listening or "running at" in line
                m = URL_RE.search(line) if self.listening else None
                if m:
                    self._load(m.group(0))
                else:
                    self.output.set_label("\n".join(self.log))
        self._read(stream, proc)  # keep draining so the server never blocks on a full pipe

    def _on_exit(self, proc, result, _data):
        if proc is not self.proc:
            return
        self.proc = None
        self.started = False
        self.url = None
        self.loaded = False
        self._fail(f"TokenTracker exited ({proc.get_exit_status() if proc.get_if_exited() else 'killed'}).")
        self.output.set_label("\n".join(self.log))

    def stop(self):
        """Stop the server this panel started (not one it found already running)."""
        if self.proc:
            proc, self.proc = self.proc, None
            proc.send_signal(signal.SIGTERM)

    # ------------------------------------------------------------ page

    def _load(self, url):
        self.url = url
        self.hint.set_label(f"TokenTracker · {url.split('//', 1)[1]}")
        self.spinner.set_spinning(False)
        self.loaded = True
        self.web.load_uri(url)
        self.stack.set_visible_child_name("web")

    def reload(self):
        if self.loaded:
            self.web.reload()
        elif self.url:
            self._load(self.url)
        elif not self.proc:  # nothing found or started yet: look again
            self.started = False
            self.start()

    def focus(self):
        (self.web if self.loaded else self.retry).grab_focus()

    def _on_policy(self, _web, decision, kind):
        # Keep the dashboard in the panel; anything off the local server (docs, GitHub, sign-in) opens outside.
        if kind not in (WebKit.PolicyDecisionType.NAVIGATION_ACTION, WebKit.PolicyDecisionType.NEW_WINDOW_ACTION):
            return False
        action = decision.get_navigation_action()
        uri = action.get_request().get_uri()
        if kind == WebKit.PolicyDecisionType.NAVIGATION_ACTION and (
                not action.is_user_gesture() or (self.url and uri.startswith(self.url))):
            return False  # the dashboard itself, iframes, redirects
        if uri.startswith(("http://", "https://", "mailto:")):
            Gtk.UriLauncher.new(uri).launch(self.app.win, None, None)
        decision.ignore()
        return True

    def _on_load_failed(self, _web, _event, uri, error):
        if self.url and uri.startswith(self.url):
            self.loaded = False
            self._fail(f"Couldn't load the dashboard: {error.message}")
        return False

    def _open_browser(self):
        Gtk.UriLauncher.new(self.url or DEFAULT_URL).launch(self.app.win, None, None)

    # ------------------------------------------------------------ status page

    def _status(self, text, busy=False):
        self.message.set_label(text)
        self.output.set_label("")
        self.spinner.set_spinning(busy)
        self.retry.set_visible(False)
        self.stack.set_visible_child_name("status")

    def _fail(self, text):
        self._status(text)
        self.retry.set_visible(True)
