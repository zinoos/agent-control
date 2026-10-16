"""agentControl: every Claude Code agent at a glance. Their terminals, live streams of agent
desktops, and a tmux-like grid to put several on one screen."""

from __future__ import annotations

import json
import math
import os
import signal
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from . import history, projects, sessions  # noqa: E402
from .tile import SessionTile  # noqa: E402
from .tokens import TokenPanel  # noqa: E402

STATE = os.path.join(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")), "agentcontrol.json")

CSS = """
window, .main { background: #0e0f12; }
.sidebar-list { background: transparent; }
.sidebar-list { padding: 4px 6px; }
.sidebar-list > row { border-radius: 10px; margin: 2px 0; }
.sidebar-list list { background: transparent; }
.sidebar-list .agent-row { border-radius: 8px; margin: 1px 0 1px 10px; }
.sidebar-list .agent-row:selected { background: alpha(@accent_bg_color, 0.25); }
.project-row { font-weight: 700; }
.side-name { font-weight: 700; }
.side-sub { font-size: 11px; opacity: 0.65; }
.tile { background: #14161a; border-radius: 12px; border: 1px solid alpha(white, 0.06); }
.tile.focused-tile { border-color: alpha(@accent_bg_color, 0.7); }
.tile-head { padding: 4px 6px 4px 12px; border-bottom: 1px solid alpha(white, 0.05); }
.tile-name { font-weight: 800; }
.tile-sub { opacity: 0.6; font-size: 12px; }
.dot { color: #6c757d; font-size: 10px; }
.dot.working { color: #ffa94d; }
.dot.idle { color: #51cf66; }
.dot.starting { color: #74c0fc; }
.live-pill, .live-badge { background: #e03131; color: white; font-weight: 800; font-size: 10px;
                          border-radius: 4px; padding: 1px 6px; }
.live-badge { font-size: 12px; padding: 2px 8px; }
.stream { background: black; }
.stream-chip { background: alpha(black, 0.6); color: white; border-radius: 4px; padding: 2px 8px; font-size: 12px; }
.stream-ticker { background: linear-gradient(to top, alpha(black, 0.75), transparent); color: white;
                 padding: 14px 12px 8px 12px; font-size: 13px; }
.empty-title { font-size: 22px; font-weight: 800; }
.running-pill { background: alpha(#51cf66, 0.2); color: #51cf66; font-weight: 700; font-size: 10px;
                border-radius: 4px; padding: 1px 6px; }
"""


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id="dev.agentcontrol.App", flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.tiles: dict[str, SessionTile] = {}
        self.rows: dict[str, Gtk.ListBoxRow] = {}
        self.focus: str | None = None
        self.cols = 0  # 0 = auto
        self.hidden: set[str] = set()
        self.order: list[str] = []
        self.collapsed: set[str] = set()
        self.projects: list[projects.Project] = projects.load()
        self.groups: dict[str, Adw.ExpanderRow] = {}  # project name -> sidebar expander
        self.row_group: dict[str, str] = {}  # session name -> project name its row is in
        self.sessions: dict[str, sessions.Session] = {}
        self.tokens_open = False
        self.tokens_width = 1000
        self.sidebar_shown = True
        self.opacity = 1.0
        self.font = os.environ.get("AGENTCONTROL_FONT", "monospace 10")
        self._load_state()
        # Quit cleanly on signals too, so "shutdown" runs and stops the token panel's server.
        self.connect("shutdown", lambda *_: self.tokens.stop() if hasattr(self, "tokens") else None)
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda *_: self.quit() or GLib.SOURCE_REMOVE)

    # ------------------------------------------------------------ state file

    def _load_state(self):
        try:
            with open(STATE) as f:
                st = json.load(f)
            self.hidden = set(st.get("hidden", []))
            self.cols = int(st.get("cols", 0))
            self.order = st.get("order", [])
            self.collapsed = set(st.get("collapsed", []))
            self.tokens_open = bool(st.get("tokens_open", False))
            self.tokens_width = int(st.get("tokens_width", 1000))
            self.sidebar_shown = bool(st.get("sidebar", True))
            self.opacity = min(1.0, max(0.3, float(st.get("opacity", 1.0))))
            self.font = st.get("font") or self.font
        except (OSError, ValueError):
            pass

    def _save_state(self):
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        with open(STATE, "w") as f:
            json.dump({"hidden": sorted(self.hidden), "cols": self.cols, "order": self.order,
                       "collapsed": sorted(self.collapsed), "tokens_open": self.tokens_open,
                       "tokens_width": self.tokens_width, "sidebar": self.sidebar_shown,
                       "opacity": self.opacity, "font": self.font}, f)

    # ------------------------------------------------------------ UI

    def do_activate(self):
        if self.get_active_window():
            self.get_active_window().present()
            return
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        prov = Gtk.CssProvider()
        prov.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), prov,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.look = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.look,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self.apply_look()
        sessions.ensure_server()

        self.win = Adw.ApplicationWindow(application=self, title="agentControl")
        self.win.set_default_size(1600, 950)

        # sidebar
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class("sidebar-list")
        side_scroll = Gtk.ScrolledWindow(vexpand=True, child=self.list)
        side_tb = Adw.ToolbarView()
        side_head = Adw.HeaderBar(show_end_title_buttons=False)
        side_head.set_title_widget(Adw.WindowTitle(title="agentControl", subtitle="agents"))
        new_btn = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="New agent (Ctrl+N)")
        new_btn.connect("clicked", lambda *_: self.new_dialog())
        side_head.pack_start(new_btn)
        proj_btn = Gtk.Button(icon_name="folder-new-symbolic", tooltip_text="New project (Ctrl+Shift+N)")
        proj_btn.connect("clicked", lambda *_: self.project_dialog())
        side_head.pack_start(proj_btn)
        resume_btn = Gtk.Button(icon_name="document-open-recent-symbolic",
                                tooltip_text="Resume a past session (Ctrl+Shift+R)")
        resume_btn.connect("clicked", lambda *_: self.resume_dialog())
        side_head.pack_start(resume_btn)
        settings_btn = Gtk.Button(icon_name="emblem-system-symbolic", tooltip_text="Settings (Ctrl+,)")
        settings_btn.connect("clicked", lambda *_: self.settings_dialog())
        side_head.pack_end(settings_btn)
        side_tb.add_top_bar(side_head)
        side_tb.set_content(side_scroll)

        # content
        self.grid = Gtk.Grid(row_spacing=8, column_spacing=8, row_homogeneous=True, column_homogeneous=True,
                             margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
        self.empty = Adw.StatusPage(icon_name="utilities-terminal-symbolic", title="No agents yet",
                                    description="Start one with + (Ctrl+N), or from any terminal: ac new")
        self.stack = Gtk.Stack()
        self.stack.add_named(self.grid, "grid")
        self.stack.add_named(self.empty, "empty")
        content_tb = Adw.ToolbarView()
        content_head = Adw.HeaderBar()
        self.layout_label = Gtk.Label()
        layout_btn = Gtk.MenuButton(icon_name="view-grid-symbolic", tooltip_text="Grid layout")
        menu = Gio.Menu()
        for label, n in (("Auto grid", 0), ("1 column", 1), ("2 columns", 2), ("3 columns", 3), ("4 columns", 4)):
            menu.append(label, f"app.cols({n})")
        layout_btn.set_menu_model(menu)
        sidebar_btn = Gtk.ToggleButton(icon_name="sidebar-show-symbolic", tooltip_text="Show / hide the agent list (Ctrl+B)")
        content_head.pack_start(sidebar_btn)
        content_head.pack_start(layout_btn)
        content_head.set_title_widget(self.layout_label)
        content_tb.add_top_bar(content_head)
        self.tokens = TokenPanel(self)
        self.tokens.set_margin_top(8)
        self.tokens.set_margin_bottom(8)
        self.tokens.set_margin_end(8)
        self.tokens.set_visible(False)
        self.tokens_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, wide_handle=True,
                                      start_child=self.stack, end_child=self.tokens,
                                      resize_start_child=True, resize_end_child=False, shrink_end_child=False)
        self.tokens_paned.connect("notify::position", self._tokens_moved)
        self.toasts = Adw.ToastOverlay(child=self.tokens_paned)
        content_tb.set_content(self.toasts)

        split = Adw.OverlaySplitView(sidebar=side_tb, content=content_tb, min_sidebar_width=240, max_sidebar_width=300,
                                     show_sidebar=self.sidebar_shown)
        split.bind_property("show-sidebar", sidebar_btn, "active",
                            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
        split.connect("notify::show-sidebar", self._sidebar_toggled)
        self.win.set_content(split)

        act = Gio.SimpleAction.new_stateful("cols", GLib.VariantType.new("i"), GLib.Variant("i", self.cols))
        act.connect("activate", lambda a, v: self.set_cols(v.get_int32()))
        self.add_action(act)
        for name, keys, cb in (("new", ["<Control>n"], lambda *_: self.new_dialog()),
                               ("new-project", ["<Control><Shift>n"], lambda *_: self.project_dialog()),
                               ("resume", ["<Control><Shift>r"], lambda *_: self.resume_dialog()),
                               ("settings", ["<Control>comma"], lambda *_: self.settings_dialog()),
                               ("grid", ["<Control>g"], lambda *_: self.toggle_focus(None)),
                               ("tokens", ["<Control><Shift>t"], lambda *_: self.toggle_tokens(not self.tokens_open)),
                               ("sidebar", ["<Control>b"], lambda *_: split.set_show_sidebar(not split.get_show_sidebar()))):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", cb)
            self.add_action(a)
            self.set_accels_for_action(f"app.{name}", keys)
        for i in range(1, 10):
            a = Gio.SimpleAction.new(f"focus{i}", None)
            a.connect("activate", lambda _a, _p, i=i: self._focus_index(i))
            self.add_action(a)
            self.set_accels_for_action(f"app.focus{i}", [f"<Alt>{i}"])

        self.win.present()
        self._build_groups()
        self.refresh()
        if self.tokens_open:
            self.toggle_tokens(True)
        GLib.timeout_add(1000, self._tick)

    # ------------------------------------------------------------ polling

    def _tick(self):
        threading.Thread(target=self._poll, daemon=True).start()
        return True

    def _poll(self):
        try:
            ss = sessions.list_sessions()
        except Exception:
            return
        GLib.idle_add(self._apply, ss)

    def refresh(self):
        self._apply(sessions.list_sessions())

    def _apply(self, ss: list[sessions.Session]):
        names = [s.name for s in ss]
        for n in names:
            if n not in self.order:
                self.order.append(n)
        by = {s.name: s for s in ss}
        self.sessions = by
        # sidebar: one row per agent, inside its project's expander
        for n in list(self.rows):
            if n not in by:
                self.groups[self.row_group.pop(n)].remove(self.rows.pop(n))
        for n in self.order:
            if n not in by:
                continue
            g = projects.group_of(by[n], self.projects)
            if self.row_group.get(n) != g:
                if n in self.rows:
                    self.groups[self.row_group[n]].remove(self.rows[n])
                self.rows[n] = self._make_row(n)
                self.groups[g].add_row(self.rows[n])
                self.row_group[n] = g
        for n, row in self.rows.items():
            self._update_row(row, by[n])
        self._update_groups()
        # tiles
        changed = False
        for n in list(self.tiles):
            if n not in by:
                t = self.tiles.pop(n)
                if t.get_parent():
                    self.grid.remove(t)
                changed = True
                if self.focus == n:
                    self.focus = None
        for n in names:
            if n not in self.tiles:
                self.tiles[n] = SessionTile(self, n)
                changed = True
            self.tiles[n].update(by[n])
        if changed:
            self.layout()
        live = sum(1 for s in ss if s.desk and s.desk.active)
        working = sum(1 for s in ss if s.state == "working")
        self.layout_label.set_markup(f"<b>{len(ss)}</b> agent{'s' if len(ss) != 1 else ''} · {working} working · "
                                     f"<span foreground='#ff6b6b'>{live} live</span>")
        return False

    # ------------------------------------------------------------ sidebar

    # Projects are expanders in the sidebar; their agents are rows inside them.

    def _build_groups(self):
        """(Re)create the project expanders, e.g. after a project was added, edited or removed."""
        for g in self.groups.values():
            self.list.remove(g)
        self.groups.clear()
        self.rows.clear()
        self.row_group.clear()
        for p in self.projects:
            self.groups[p.name] = self._make_group(p)
        self.groups[projects.OTHER] = self._make_group(None)
        for g in self.groups.values():
            self.list.append(g)
        if self.sessions:
            self._apply(list(self.sessions.values()))

    def _make_group(self, proj):
        name = proj.name if proj else projects.OTHER
        g = Adw.ExpanderRow(title=GLib.markup_escape_text(name), expanded=name not in self.collapsed, subtitle_lines=1)
        g.add_css_class("project-row")
        g.project = proj
        g.connect("notify::expanded", lambda r, _p: self._set_collapsed(name, not r.get_expanded()))
        if proj:
            add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text=f"New agent in {name}",
                             valign=Gtk.Align.CENTER)
            add.add_css_class("flat")
            add.connect("clicked", lambda *_: self.new_dialog(name))
            g.add_suffix(add)
            menu = Gtk.GestureClick(button=3)
            menu.connect("pressed", lambda *_: self._group_menu(proj, g))
            g.add_controller(menu)
        return g

    def _update_groups(self):
        counts: dict[str, list[sessions.Session]] = {}
        for n, g in self.row_group.items():
            counts.setdefault(g, []).append(self.sessions[n])
        for name, g in self.groups.items():
            ss = counts.get(name, [])
            working = sum(1 for s in ss if s.state == "working")
            parts = [g.project.short_path] if g.project else []
            parts.append(f"{len(ss)} agent{'s' if len(ss) != 1 else ''}" + (f", {working} working" if working else ""))
            g.set_subtitle(GLib.markup_escape_text(" · ".join(parts)))
            if g.get_enable_expansion() != bool(ss):
                g.set_enable_expansion(bool(ss))
                if ss:
                    g.set_expanded(name not in self.collapsed)
            if name == projects.OTHER:
                g.set_visible(bool(ss))

    def _set_collapsed(self, name, collapsed):
        if not self.groups.get(name) or not self.groups[name].get_enable_expansion():
            return  # empty groups get disabled, which also collapses them: not the user's choice
        (self.collapsed.add if collapsed else self.collapsed.discard)(name)
        self._save_state()

    def _group_menu(self, proj, g):
        self._popover(g, (("New agent here", lambda: self.new_dialog(proj.name)),
                          ("Open folder", lambda: Gio.AppInfo.launch_default_for_uri(
                              Gio.File.new_for_path(proj.path).get_uri(), None)),
                          ("Copy path", lambda: self._copy(proj.path)),
                          ("Edit project…", lambda: self.project_dialog(proj)),
                          ("Remove project…", lambda: self._confirm_remove(proj))))

    def _popover(self, parent, items):
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for label, cb in items:
            b = Gtk.Button(label=label)
            b.add_css_class("flat")
            b.get_child().set_xalign(0)
            b.connect("clicked", lambda _b, cb=cb: (pop.popdown(), cb()))
            box.append(b)
        pop.set_child(box)
        pop.set_parent(parent)
        pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
        pop.popup()

    def _confirm_remove(self, proj):
        d = Adw.AlertDialog(heading=f"Remove project {proj.name}?",
                            body=f"Only the preset goes. {proj.short_path} and its agents stay; "
                                 "running agents move to Other.")
        d.add_response("cancel", "Cancel")
        d.add_response("remove", "Remove")
        d.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)

        def done(_d, r):
            if r == "remove":
                projects.remove(proj.name)
                self._reload_projects()
        d.connect("response", done)
        d.present(self.win)

    def _reload_projects(self):
        self.projects = projects.load()
        self._build_groups()
        self.layout()

    def _make_row(self, name):
        row = Adw.ActionRow(title=GLib.markup_escape_text(name), activatable=True, subtitle_lines=1)
        row.add_css_class("agent-row")
        row.name = name
        dot = Gtk.Label(label="●")
        dot.add_css_class("dot")
        live = Gtk.Label(label="LIVE", valign=Gtk.Align.CENTER)
        live.add_css_class("live-pill")
        eye = Gtk.Button(icon_name="view-reveal-symbolic", tooltip_text="Show in grid / hide", valign=Gtk.Align.CENTER)
        eye.add_css_class("flat")
        eye.connect("clicked", lambda *_: self.set_shown(name, name in self.hidden))
        row.add_prefix(dot)
        row.add_suffix(live)
        row.add_suffix(eye)
        row.parts = (dot, live, eye)
        row.connect("activated", lambda r: self._row_activated(None, r))
        menu = Gtk.GestureClick(button=3)
        menu.connect("pressed", lambda *_: self._row_menu(name, row))
        row.add_controller(menu)
        return row

    def _update_row(self, row, s):
        dot, live, eye = row.parts
        for st in ("working", "idle", "starting"):
            (dot.add_css_class if s.state == st else dot.remove_css_class)(st)
        sub = s.label if s.label != s.name else s.cwd.replace(os.path.expanduser("~"), "~")
        if row.get_subtitle() != GLib.markup_escape_text(sub):
            row.set_subtitle(GLib.markup_escape_text(sub))
        live.set_visible(bool(s.desk and s.desk.active))
        eye.set_icon_name("view-conceal-symbolic" if s.name in self.hidden else "view-reveal-symbolic")

    def _row_activated(self, _lb, row):
        if row.name in self.hidden:
            self.set_shown(row.name, True)
        self.toggle_focus(row.name)

    def _row_menu(self, name, row):
        self._popover(row, (("Focus", lambda: self.toggle_focus(name)),
                            ("Interrupt (Esc)", lambda: sessions.interrupt(name)),
                            ("Copy attach command", lambda: self._copy(f"ac attach {name}")),
                            ("End session…", lambda: self._confirm_kill(name))))

    def _copy(self, text):
        Gdk.Display.get_default().get_clipboard().set(text)
        self.toast(f"copied: {text}")

    def _confirm_kill(self, name):
        d = Adw.AlertDialog(heading=f"End {name}?", body="The Claude Code session and its tmux session close. "
                            "Its agent desktop (if any) stops when the session's MCP server exits.")
        d.add_response("cancel", "Cancel")
        d.add_response("kill", "End session")
        d.set_response_appearance("kill", Adw.ResponseAppearance.DESTRUCTIVE)
        d.connect("response", lambda _d, r: r == "kill" and sessions.kill(name))
        d.present(self.win)

    # ------------------------------------------------------------ layout

    def set_cols(self, n):
        self.cols = n
        self.lookup_action("cols").set_state(GLib.Variant("i", n))
        self._save_state()
        self.layout()

    def set_shown(self, name, shown):
        (self.hidden.discard if shown else self.hidden.add)(name)
        if not shown and self.focus == name:
            self.focus = None
        self._save_state()
        self.layout()
        if name in self.rows and name in self.tiles and self.tiles[name].session:
            self._update_row(self.rows[name], self.tiles[name].session)

    def toggle_focus(self, name):
        self.focus = None if (name is None or self.focus == name) else name
        self.layout()
        if self.focus and self.focus in self.tiles:
            self.tiles[self.focus].term.grab_focus()

    def _focus_index(self, i):
        shown = self._shown()
        if 0 < i <= len(shown):
            self.toggle_focus(shown[i - 1])

    def _shown(self):
        """Tiles in sidebar order: grouped by project."""
        rank = {g: i for i, g in enumerate(self.groups)}
        ns = [n for n in self.order if n in self.tiles and n not in self.hidden]
        return sorted(ns, key=lambda n: rank.get(self.row_group.get(n), len(rank)))

    def layout(self):
        for t in self.tiles.values():
            if t.get_parent() is self.grid:
                self.grid.remove(t)
            t.remove_css_class("focused-tile")
        shown = [self.focus] if self.focus in self.tiles else self._shown()
        if not self.tiles:
            self.stack.set_visible_child_name("empty")
            return
        self.stack.set_visible_child_name("grid")
        n = max(1, len(shown))
        cols = self.cols or math.ceil(math.sqrt(n))
        cols = min(cols, n)
        for i, name in enumerate(shown):
            t = self.tiles[name]
            self.grid.attach(t, i % cols, i // cols, 1, 1)
            # Streams in a crowded grid don't need 30 fps.
            t.set_fps(30 if n <= 2 else 15 if n <= 4 else 8)
        if self.focus:
            self.tiles[self.focus].add_css_class("focused-tile")

    # ------------------------------------------------------------ token panel

    def toggle_tokens(self, show: bool):
        self.tokens_open = show
        self._save_state()
        self._placing = show  # showing the panel moves the divider to a default spot: not a user drag
        self.tokens.set_visible(show)
        if show:
            self.tokens.start()
            GLib.idle_add(self._place_tokens)
            self.tokens.focus()

    def _place_tokens(self, last=-1, tries=40):
        # Repeat until the paned's width settles: the window can still grow after the first allocation (e.g.
        # when the tiler sizes it), and the extra space would all go to the grid.
        self._placing = True
        w = self.tokens_paned.get_width()
        if w > 0:
            self.tokens_paned.set_position(max(200, w - min(self.tokens_width, int(w * 0.6))))  # leave the grid room
        if (w <= 0 or w != last) and tries > 0:
            GLib.timeout_add(50, self._place_tokens, w, tries - 1)
        else:
            self._placing = False
        return False

    def _tokens_moved(self, paned, _p):
        w = paned.get_width()
        if self.tokens_open and w > 0 and not getattr(self, "_placing", False):
            width = w - paned.get_position()
            if width > 100 and abs(width - self.tokens_width) > 4:
                self.tokens_width = width
                GLib.timeout_add(500, self._save_tokens_width, width)

    def _save_tokens_width(self, width):
        if width == self.tokens_width:  # settled after the drag
            self._save_state()
        return False

    # ------------------------------------------------------------ new agent

    def new_dialog(self, project: str | None = None):
        d = Adw.AlertDialog(heading="New agent", body="Starts Claude Code in its own tmux session. If it uses an "
                            "agent desktop (hypr-computer), its tile streams it live.")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        choices = [p.name for p in self.projects] + ["No project"]
        proj = Gtk.DropDown.new_from_strings(choices)
        name = Gtk.Entry(placeholder_text="name (optional)", activates_default=True)
        cwd = Gtk.Entry(text="~", activates_default=True)
        prompt = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, height_request=90, top_margin=6, left_margin=6)

        def picked(*_):
            i = proj.get_selected()
            if i < len(self.projects):
                cwd.set_text(self.projects[i].short_path)
                name.set_placeholder_text(f"name (default: {projects.slug(self.projects[i].name)}-N)")
            else:
                name.set_placeholder_text("name (optional)")
        proj.connect("notify::selected", picked)
        names = [p.name for p in self.projects]
        proj.set_selected(names.index(project) if project in names else len(names))
        picked()
        for label, w in (("Project", proj), ("Name", name), ("Working directory", cwd)):
            box.append(Gtk.Label(label=label, xalign=0))
            box.append(w)
        box.append(Gtk.Label(label="First instruction (optional)", xalign=0))
        box.append(Gtk.Frame(child=prompt))
        d.set_extra_child(box)
        d.add_response("cancel", "Cancel")
        d.add_response("start", "Start agent")
        d.set_response_appearance("start", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("start")

        def done(_d, r):
            if r != "start":
                return
            buf = prompt.get_buffer()
            text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()
            i = proj.get_selected()
            try:
                n = sessions.new_session(name.get_text().strip() or None, cwd.get_text().strip() or "~", text or None,
                                         project=self.projects[i].name if i < len(self.projects) else None)
                self.refresh()
                self.toast(f"started {n}")
            except Exception as ex:
                self.toast(str(ex))
        d.connect("response", done)
        d.present(self.win)

    def _sidebar_toggled(self, split, _p):
        # A narrow window collapses the sidebar into an overlay by itself; only remember the user's choice.
        if not split.get_collapsed() and split.get_show_sidebar() != self.sidebar_shown:
            self.sidebar_shown = split.get_show_sidebar()
            self._save_state()

    def resume_dialog(self):
        """Pick a past Claude Code session and continue it in a new agent (claude --resume)."""
        d = Adw.Dialog(title="Resume a session", content_width=640, content_height=620)
        tb = Adw.ToolbarView()
        tb.add_top_bar(Adw.HeaderBar())
        search = Gtk.SearchEntry(placeholder_text="Search titles, prompts and folders", margin_start=12,
                                 margin_end=12, margin_bottom=6)
        tb.add_top_bar(search)
        lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, margin_start=12, margin_end=12, margin_bottom=12,
                         valign=Gtk.Align.START)
        lb.add_css_class("boxed-list")
        stack = Gtk.Stack()
        stack.add_named(Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                                    width_request=32, height_request=32), "loading")
        stack.add_named(Gtk.ScrolledWindow(child=lb, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER), "list")
        stack.add_named(Adw.StatusPage(icon_name="document-open-recent-symbolic", title="No past sessions",
                                       description="Claude Code sessions show up here once they have a prompt."),
                        "empty")
        tb.set_content(stack)
        d.set_child(tb)

        def matches(row):
            q = search.get_text().strip().lower()
            return not q or all(w in row.haystack for w in q.split())
        lb.set_filter_func(matches)
        search.connect("search-changed", lambda *_: lb.invalidate_filter())

        def first_visible():
            row = lb.get_first_child()
            while row and not (row.get_child_visible() and matches(row)):
                row = row.get_next_sibling()
            return row
        search.connect("activate", lambda *_: (r := first_visible()) and r.activate())

        def chosen(row):
            d.close()
            self._resume(row.past)

        def fill(past):
            for p in past:
                sub = f"{p.short_cwd} · {p.when}"
                if p.first_prompt != p.title:
                    sub += f" · {p.first_prompt}"
                row = Adw.ActionRow(title=GLib.markup_escape_text(p.title[:200]),
                                    subtitle=GLib.markup_escape_text(sub[:300]), activatable=True,
                                    title_lines=1, subtitle_lines=1, tooltip_text=p.first_prompt[:600])
                if p.pid:
                    pill = Gtk.Label(label="RUNNING", valign=Gtk.Align.CENTER)
                    pill.add_css_class("running-pill")
                    row.add_suffix(pill)
                row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
                row.past = p
                row.haystack = f"{p.title} {p.first_prompt} {p.short_cwd}".lower()
                row.connect("activated", chosen)
                lb.append(row)
            stack.set_visible_child_name("list" if past else "empty")
            return False

        threading.Thread(target=lambda: GLib.idle_add(fill, history.list_past()), daemon=True).start()
        d.present(self.win)
        search.grab_focus()

    def _resume(self, p: history.PastSession):
        if p.pid:
            # Already open: show its tile if it runs in agentControl, rather than a second copy of the session.
            mine = next((s for s in self.sessions.values()
                         if s.pane_pid == p.pid or s.pane_pid in sessions._ancestors(p.pid)), None)
            if mine:
                if mine.name in self.hidden:
                    self.set_shown(mine.name, True)
                if self.focus != mine.name:
                    self.toggle_focus(mine.name)
            else:
                self.toast(f"that session is already running in another terminal (pid {p.pid})")
            return
        if not os.path.isdir(p.cwd):
            self.toast(f"its folder is gone: {p.short_cwd}")
            return
        proj = projects.match(p.cwd, self.projects)
        base = projects.slug(" ".join(p.title.split()[:4]))[:28].strip("-") or "resumed"
        taken = set(self.sessions)
        name = base if base not in taken else next(f"{base}-{i}" for i in range(2, 1000) if f"{base}-{i}" not in taken)
        try:
            n = sessions.new_session(name, p.cwd, None, ["--resume", p.id], project=proj.name if proj else None)
            self.refresh()
            self.toast(f"resumed as {n}")
        except Exception as ex:
            self.toast(str(ex))

    def project_dialog(self, proj: projects.Project | None = None):
        """New project, or edit one: a name plus the directory its agents start in (created if missing)."""
        d = Adw.AlertDialog(heading=f"Edit {proj.name}" if proj else "New project",
                            body="Agents started in a project open in its directory and are grouped under it "
                                 "in the sidebar. A directory that doesn't exist yet is created.")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        name = Gtk.Entry(text=proj.name if proj else "", placeholder_text="e.g. my-app", activates_default=True)
        path = Gtk.Entry(text=proj.short_path if proj else projects.ROOT + "/", hexpand=True, activates_default=True)
        browse = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text="Choose a folder")
        row = Gtk.Box(spacing=6)
        row.append(path)
        row.append(browse)
        note = Gtk.Label(xalign=0, wrap=True)
        note.add_css_class("side-sub")
        auto = {"on": proj is None}  # follow the name until the directory is edited by hand

        def name_changed(*_):
            if auto["on"]:
                auto["setting"] = True
                path.set_text(projects.default_dir(name.get_text()) if name.get_text().strip() else projects.ROOT + "/")
                auto["setting"] = False
            check()

        def path_changed(*_):
            if not auto.get("setting"):
                auto["on"] = False
            check()

        def check():
            p = os.path.expanduser(path.get_text().strip() or "~")
            if os.path.isdir(p):
                note.set_label("Existing folder.")
            elif os.path.exists(p):
                note.set_label("That's a file, not a folder.")
            else:
                note.set_label("New folder: it will be created.")
            d.set_response_enabled("save", bool(name.get_text().strip()) and not (os.path.exists(p) and not os.path.isdir(p)))

        def chosen(dlg, res):
            try:
                f = dlg.select_folder_finish(res)
            except GLib.Error:
                return
            if f:
                auto["on"] = False
                path.set_text(projects.Project("", f.get_path()).short_path)

        def pick(*_):
            fd = Gtk.FileDialog(title="Project folder")
            start = os.path.expanduser(path.get_text().strip() or "~")
            while start and not os.path.isdir(start):
                start = os.path.dirname(start)
            fd.set_initial_folder(Gio.File.new_for_path(start or os.path.expanduser("~")))
            fd.select_folder(self.win, None, chosen)

        name.connect("changed", name_changed)
        path.connect("changed", path_changed)
        browse.connect("clicked", pick)
        box.append(Gtk.Label(label="Name", xalign=0))
        box.append(name)
        box.append(Gtk.Label(label="Directory", xalign=0))
        box.append(row)
        box.append(note)
        d.set_extra_child(box)
        d.add_response("cancel", "Cancel")
        d.add_response("save", "Save" if proj else "Create project")
        d.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("save")
        check()

        def done(_d, r):
            if r != "save":
                return
            new = name.get_text().strip()
            try:
                projects.upsert(new, path.get_text(), old_name=proj.name if proj else None)
            except Exception as ex:
                self.toast(str(ex))
                return
            if proj and new != proj.name:
                for s in self.sessions.values():
                    if s.project == proj.name:
                        sessions.set_project(s.name, new)
                if proj.name in self.collapsed:
                    self.collapsed.discard(proj.name)
                    self.collapsed.add(new)
            self.sessions = {s.name: s for s in sessions.list_sessions()}  # with the renamed @ac_project
            self._reload_projects()
            self.toast(f"project {new} saved" if proj else f"project {new} created")
        d.connect("response", done)
        d.present(self.win)

    def apply_look(self):
        a = self.opacity
        self.look.load_from_string(f"""
window, .main {{ background: alpha(#0e0f12, {a}); }}
.sidebar-pane {{ background: alpha(var(--sidebar-bg-color), {a}); }}
headerbar {{ background: alpha(var(--headerbar-bg-color), {a}); }}
.tile {{ background: alpha(#14161a, {a}); }}
""")
        for t in self.tiles.values():
            t.apply_look()

    def settings_dialog(self):
        d = Adw.PreferencesDialog(title="Settings")
        page = Adw.PreferencesPage(title="Appearance", icon_name="preferences-desktop-appearance-symbolic")
        win = Adw.PreferencesGroup(title="Window")
        op = Adw.SpinRow.new_with_range(30, 100, 5)
        op.set_title("Opacity")
        op.set_subtitle("In percent. The blur behind it comes from the compositor (Hyprland: decoration:blur)")
        op.set_value(round(self.opacity * 100))
        win.add(op)
        term = Adw.PreferencesGroup(title="Terminal")
        font_row = Adw.ActionRow(title="Font")
        font_btn = Gtk.FontDialogButton(dialog=Gtk.FontDialog(), valign=Gtk.Align.CENTER,
                                        font_desc=Pango.FontDescription.from_string(self.font))
        font_row.add_suffix(font_btn)
        term.add(font_row)
        page.add(win)
        page.add(term)
        d.add(page)

        def changed(*_):
            self.opacity = op.get_value() / 100
            self.font = font_btn.get_font_desc().to_string()
            self.apply_look()
            self._save_state()
        op.connect("notify::value", changed)
        font_btn.connect("notify::font-desc", changed)
        d.present(self.win)

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=3))
        return False


def main():
    return App().run(sys.argv[:1])
