"""The GTK app: settings dialog, live look changes, tiles, notifications. Needs a display (Wayland or X11, Xvfb in
CI), tmux and a D-Bus session bus (run the suite under dbus-run-session)."""

import dataclasses
import json
import os
import shutil
import time
import unittest
from unittest import mock

HAS_DISPLAY = bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))
HAS_TMUX = shutil.which("tmux") is not None

if HAS_DISPLAY:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

    from agentcontrol import app as appmod  # noqa: E402
    from agentcontrol import sessions, settings, updates  # noqa: E402
    from agentcontrol.tokens import TokenPanel  # noqa: E402


def pump(seconds=0.2):
    ctx = GLib.MainContext.default()
    end = time.time() + seconds
    while time.time() < end:
        while ctx.pending():
            ctx.iteration(False)
        time.sleep(0.01)


def find(widget, cls, title=None):
    if isinstance(widget, cls) and (title is None or widget.get_title() == title):
        return widget
    child = widget.get_first_child()
    while child:
        hit = find(child, cls, title)
        if hit:
            return hit
        child = child.get_next_sibling()
    return None


@unittest.skipUnless(HAS_DISPLAY and HAS_TMUX, "needs a display and tmux")
class AppTest(unittest.TestCase):
    """One app window for the whole class, with two fake agents in the test tmux server."""

    @classmethod
    def setUpClass(cls):
        for p in (settings.PATH, appmod.STATE):
            if os.path.exists(p):
                os.remove(p)
        cls.token_start = mock.patch.object(TokenPanel, "start", lambda self: None)  # never spawn tokentracker
        cls.token_start.start()
        cls.update_check = mock.patch.object(updates, "check", return_value=None)  # never fetch during the tests
        cls.update_check.start()
        sessions.ensure_server()
        for n in ("alpha", "beta"):
            sessions.new_session(n, "~")
        cls.app = appmod.App()
        cls.app.set_flags(cls.app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)  # never reach a running copy
        cls.app.register(None)
        cls.app.activate()
        pump(0.8)

    @classmethod
    def tearDownClass(cls):
        cls.app.win.destroy()
        pump(0.1)
        for s in sessions.list_sessions():
            sessions.kill(s.name)
        cls.token_start.stop()
        cls.update_check.stop()

    def setUp(self):
        a = self.app
        a.cfg = dict(settings.DEFAULTS)
        settings.save(a.cfg)
        a.apply_look()
        while (d := a.win.get_visible_dialog()):
            d.force_close()
        pump(0.05)

    def dialog(self):
        self.app.settings_dialog()
        pump(0.1)
        d = self.app.win.get_visible_dialog()
        self.assertIsInstance(d, Adw.PreferencesDialog)
        return d

    def css(self):
        return self.app.look.to_string()

    # ---------------------------------------------------------------- window

    def test_window_and_tiles(self):
        self.assertTrue(self.app.win.get_visible())
        self.assertEqual(set(self.app.tiles), {"alpha", "beta"})

    def test_update_button_shows_only_when_behind(self):
        a = self.app
        a._show_update(None)
        pump(0.05)
        self.assertFalse(a.update_btn.get_visible())
        a._show_update(updates.Update(behind=2, local="aaa1111", remote="bbb2222"))
        pump(0.05)
        self.assertTrue(a.update_btn.get_visible())
        self.assertIn("2 new commits", a.update_btn.get_tooltip_text())
        a._show_update(None)
        pump(0.05)
        self.assertFalse(a.update_btn.get_visible())

    def test_update_button_applies_and_hides(self):
        a = self.app
        a._show_update(updates.Update(behind=1, local="aaa1111", remote="bbb2222"))
        pump(0.05)
        with mock.patch.object(updates, "apply", return_value="bbb2222") as apply:
            a.update_btn.emit("clicked")
            pump(0.5)
        apply.assert_called_once()
        self.assertFalse(a.update_btn.get_visible())
        self.assertTrue(a.update_btn.get_sensitive())
        self.assertIsNone(a.update)

    def test_update_button_recovers_from_failure(self):
        a = self.app
        a._show_update(updates.Update(behind=1, local="aaa1111", remote="bbb2222"))
        pump(0.05)
        with mock.patch.object(updates, "apply", side_effect=RuntimeError("Not possible to fast-forward")):
            a.update_btn.emit("clicked")
            pump(0.5)
        self.assertTrue(a.update_btn.get_visible())
        self.assertTrue(a.update_btn.get_sensitive())
        self.assertEqual(a.update_btn.get_label(), "Update")
        a._show_update(None)
        pump(0.05)

    def test_settings_action_and_shortcut(self):
        self.assertIn("<Control>comma", self.app.get_accels_for_action("app.settings"))
        self.app.activate_action("settings", None)
        pump(0.1)
        self.assertIsInstance(self.app.win.get_visible_dialog(), Adw.PreferencesDialog)

    def test_dialog_opens_twice(self):
        self.dialog().force_close()
        pump(0.05)
        self.dialog()

    def test_pages(self):
        d = self.dialog()
        for name in ("appearance", "agents", "panels"):
            d.set_visible_page_name(name)
            self.assertEqual(d.get_visible_page_name(), name)

    # ---------------------------------------------------------------- appearance

    def test_opacity(self):
        self.app._set("opacity", 0.6)
        self.assertIn("0.6", self.css())
        for t in self.app.tiles.values():
            self.assertAlmostEqual(t.term.get_color_background_for_draw().alpha, 0.6, places=2)
        self.assertEqual(settings.get("opacity"), 0.6)

    def test_opacity_row_writes_fraction(self):
        d = self.dialog()
        row = find(d, Adw.SpinRow, "Opacity")
        self.assertEqual(row.get_value(), 100)
        row.set_value(75)
        self.assertEqual(self.app.cfg["opacity"], 0.75)
        row.set_value(5)  # below the row's range: clamped by the row
        self.assertEqual(self.app.cfg["opacity"], 0.3)

    def test_gap_and_radius(self):
        self.app._set("gap", 0)
        self.assertEqual((self.app.grid.get_row_spacing(), self.app.grid.get_margin_top()), (0, 0))
        self.app._set("gap", 20)
        self.assertEqual((self.app.grid.get_column_spacing(), self.app.grid.get_margin_end()), (20, 20))
        self.app._set("radius", 3)
        self.assertIn("border-top-left-radius: 3px", self.css())

    def test_gap_row_saves_int(self):
        find(self.dialog(), Adw.SpinRow, "Gap").set_value(14)
        self.assertEqual(settings.load()["gap"], 14)
        self.assertIsInstance(settings.load()["gap"], int)

    @unittest.skipUnless(HAS_DISPLAY and hasattr(Adw, "AccentColor"), "libadwaita < 1.6")
    def test_accent(self):
        self.app._set("accent", "teal")
        self.assertIn("--accent-bg-color", self.css())
        self.app._set("accent", "system")
        self.assertNotIn("--accent-bg-color", self.css())
        combo = find(self.dialog(), Adw.ComboRow, "Accent color")
        combo.set_selected(settings.ACCENTS.index("red"))
        self.assertEqual(settings.get("accent"), "red")

    def test_schemes_apply_to_tiles(self):
        for name in settings.SCHEMES:
            self.app._set("scheme", name)
            bg = settings.scheme(name)[1]
            self.assertIn("rgb(%d,%d,%d)" % tuple(int(bg[i:i + 2], 16) for i in (1, 3, 5)), self.css())
            got = self.app.tiles["alpha"].term.get_color_background_for_draw()
            self.assertEqual("#%02x%02x%02x" % tuple(round(c * 255) for c in (got.red, got.green, got.blue)),
                             bg.lower(), name)

    def test_scheme_row(self):
        combo = find(self.dialog(), Adw.ComboRow, "Color scheme")
        combo.set_selected(list(settings.SCHEMES).index("Tokyo Night"))
        self.assertEqual(settings.get("scheme"), "Tokyo Night")

    def test_font_rows(self):
        d = self.dialog()
        font, size = find(d, Adw.ComboRow, "Font"), find(d, Adw.SpinRow, "Font size")
        names = [font.get_model().get_string(i) for i in range(font.get_model().get_n_items())]
        self.assertEqual(len({n.lower() for n in names}), len(names), "duplicate font names")
        self.assertEqual(names[font.get_selected()].lower(), "monospace")
        self.assertEqual(size.get_value(), 10)
        size.set_value(14)
        self.assertEqual(settings.get("font"), f"{names[font.get_selected()]} 14")
        self.assertEqual(self.app.tiles["alpha"].term.get_font().get_size() // 1024, 14)

    def test_font_not_installed_is_kept(self):
        self.app.cfg["font"] = "Nonexistent Font 13"
        d = self.dialog()
        font, size = find(d, Adw.ComboRow, "Font"), find(d, Adw.SpinRow, "Font size")
        self.assertEqual(font.get_model().get_string(font.get_selected()), "Nonexistent Font")
        self.assertEqual(size.get_value(), 13)
        size.set_value(15)
        self.assertEqual(self.app.cfg["font"], "Nonexistent Font 15")

    def test_font_without_size(self):
        self.app.cfg["font"] = "Monospace"
        size = find(self.dialog(), Adw.SpinRow, "Font size")
        self.assertEqual(size.get_value(), 10)

    def test_no_modal_font_window(self):
        self.assertIsNone(find(self.dialog(), Gtk.FontDialogButton))

    # ---------------------------------------------------------------- agents

    def test_entry_rows_apply_on_enter_only(self):
        row = find(self.dialog(), Adw.EntryRow, "Extra claude arguments, e.g. --model opus")
        row.set_text("  --model opus  ")
        self.assertEqual(settings.get("claude_args"), "")  # typing alone doesn't save
        row.emit("apply")
        self.assertEqual(settings.get("claude_args"), "--model opus")

    def test_switch_rows(self):
        d = self.dialog()
        find(d, Adw.SwitchRow, "Ask before ending a session").set_active(False)
        find(d, Adw.SwitchRow, "Notify when an agent is done").set_active(False)
        s = settings.load()
        self.assertEqual((s["confirm_kill"], s["notify"]), (False, False))

    def test_confirm_kill(self):
        with mock.patch.object(sessions, "kill") as kill:
            self.app._confirm_kill("alpha")
            pump(0.05)
            kill.assert_not_called()
            self.assertIsInstance(self.app.win.get_visible_dialog(), Adw.AlertDialog)
            self.app.win.get_visible_dialog().force_close()
            self.app.cfg["confirm_kill"] = False
            self.app._confirm_kill("alpha")
            kill.assert_called_once_with("alpha")

    def notifications(self, steps, active=False):
        sent = []
        base = list(self.app.sessions.values())
        with mock.patch.object(self.app, "send_notification", lambda i, n: sent.append(i)), \
                mock.patch.object(self.app.win, "is_active", lambda: active):
            for titles in steps:
                self.app._apply([dataclasses.replace(s, title=titles.get(s.name, "✳ x")) for s in base])
        return sent

    def test_notify_on_working_to_idle(self):
        sent = self.notifications([{"alpha": "⠋ busy"}, {"alpha": "✳ done"}, {"alpha": "✳ done"}])
        self.assertEqual(sent, ["done-alpha"])

    def test_no_notify_when_focused_disabled_or_never_working(self):
        self.assertEqual(self.notifications([{"alpha": "⠋"}, {"alpha": "✳"}], active=True), [])
        self.assertEqual(self.notifications([{"alpha": ""}, {"alpha": "✳"}]), [])  # starting -> idle
        self.app.cfg["notify"] = False
        self.assertEqual(self.notifications([{"alpha": "⠋"}, {"alpha": "✳"}]), [])

    def test_no_notify_for_new_or_ended_sessions(self):
        base = list(self.app.sessions.values())
        sent = []
        with mock.patch.object(self.app, "send_notification", lambda i, n: sent.append(i)), \
                mock.patch.object(self.app.win, "is_active", lambda: False):
            self.app._apply([dataclasses.replace(base[0], title="⠋")])
            self.app._apply([dataclasses.replace(base[0], name="newcomer", title="✳")])
            self.app._apply([dataclasses.replace(s, title="✳") for s in base])
        self.assertEqual(sent, [])

    # ---------------------------------------------------------------- panels

    def test_fps_cap(self):
        self.app._set("max_fps", 10)
        self.app.layout()
        self.assertTrue(all(t.stream.fps <= 10 for t in self.app.tiles.values()))
        self.app._set("max_fps", 60)
        self.app.layout()
        self.assertTrue(all(t.stream.fps <= 30 for t in self.app.tiles.values()))

    def test_default_view_for_new_tiles(self):
        self.app.cfg["view"] = "terminal"
        sessions.new_session("gamma", "~")
        try:
            self.app.refresh()
            pump(0.2)
            self.assertEqual(self.app.tiles["gamma"].mode, "terminal")
            self.app.tiles["gamma"]._cycle_mode()
            self.assertEqual(self.app.tiles["gamma"].mode, "auto")
        finally:
            sessions.kill("gamma")
            self.app.refresh()

    def test_panels_page_without_streams(self):
        d = self.dialog()
        d.set_visible_page_name("panels")
        has_stream_rows = find(d, Adw.SpinRow, "Max frames per second") is not None
        self.assertEqual(has_stream_rows, appmod.stream.AVAILABLE)
        self.assertIsNotNone(find(d, Adw.ComboRow, "At start"))

    def test_css_has_no_invalid_values(self):
        self.app._set("opacity", 0.5)
        css = self.css()
        self.assertNotIn("background-clip: alpha", css)  # shorthand + var() smears the color into every part
        self.assertIn("background-color: alpha(var(--headerbar-bg-color), 0.5)", css)

    def test_corrupt_settings_file_does_not_break_dialog(self):
        with open(settings.PATH, "w") as f:
            json.dump({"opacity": "abc", "view": "xyz", "gap": 10 ** 9}, f)
        self.app.cfg = settings.load()
        self.app.apply_look()
        self.dialog()


@unittest.skipUnless(HAS_DISPLAY, "needs a display")
class TokensAtStart(unittest.TestCase):
    """Only the constructor: whether the token panel opens at start."""

    def start(self, setting, last_open):
        os.makedirs(os.path.dirname(appmod.STATE), exist_ok=True)
        with open(appmod.STATE, "w") as f:
            json.dump({"tokens_open": last_open}, f)
        settings.save(settings.load() | {"tokens_at_start": setting})
        try:
            return appmod.App().tokens_open
        finally:
            os.remove(appmod.STATE)
            os.remove(settings.PATH)

    def test_modes(self):
        self.assertTrue(self.start("last", True))
        self.assertFalse(self.start("last", False))
        self.assertTrue(self.start("open", False))
        self.assertFalse(self.start("closed", True))

    def test_corrupt_state_file(self):
        os.makedirs(os.path.dirname(appmod.STATE), exist_ok=True)
        with open(appmod.STATE, "w") as f:
            f.write("{broken")
        try:
            a = appmod.App()
            self.assertEqual((a.cols, a.hidden, a.tokens_open), (0, set(), False))
        finally:
            os.remove(appmod.STATE)


if __name__ == "__main__":
    unittest.main()
