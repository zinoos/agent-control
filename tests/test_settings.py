import json
import os
import re
import shutil
import tempfile
import unittest

import tests  # noqa: F401  first: this run's own HOME and config, before agentcontrol reads its paths
from agentcontrol import settings


class SettingsFile(unittest.TestCase):
    def setUp(self):
        if os.path.exists(settings.PATH):
            os.remove(settings.PATH)

    def write(self, content):
        os.makedirs(os.path.dirname(settings.PATH), exist_ok=True)
        with open(settings.PATH, "w") as f:
            f.write(content if isinstance(content, str) else json.dumps(content))

    def test_missing_file_gives_defaults(self):
        self.assertEqual(settings.load(), settings.DEFAULTS)

    def test_load_returns_a_copy(self):
        settings.load()["gap"] = 99
        self.assertEqual(settings.DEFAULTS["gap"], 8)

    def test_corrupt_json_gives_defaults(self):
        self.write("{not json")
        self.assertEqual(settings.load(), settings.DEFAULTS)

    def test_empty_file_gives_defaults(self):
        self.write("")
        self.assertEqual(settings.load(), settings.DEFAULTS)

    def test_non_object_json_gives_defaults(self):
        for content in ([1, 2], "just a string", 42, None):
            self.write(content)
            self.assertEqual(settings.load(), settings.DEFAULTS, content)

    def test_partial_file_keeps_other_defaults(self):
        self.write({"gap": 20})
        s = settings.load()
        self.assertEqual(s["gap"], 20)
        self.assertEqual(s["radius"], settings.DEFAULTS["radius"])

    def test_wrong_types_fall_back(self):
        self.write({"opacity": "abc", "gap": "8", "notify": "yes", "font": 12, "confirm_kill": 1,
                    "max_fps": None, "radius": [4], "claude_args": False})
        self.assertEqual(settings.load(), settings.DEFAULTS)

    def test_bool_is_not_a_number(self):
        self.write({"gap": True, "opacity": False})
        s = settings.load()
        self.assertEqual((s["gap"], s["opacity"]), (8, 1.0))

    def test_nan_falls_back(self):
        self.write('{"opacity": NaN}')
        self.assertEqual(settings.load()["opacity"], 1.0)

    def test_int_accepted_for_float(self):
        self.write({"opacity": 1})
        self.assertEqual(settings.load()["opacity"], 1)

    def test_numbers_are_clamped(self):
        self.write({"opacity": 0.01, "gap": -5, "radius": 1000, "max_fps": 0})
        s = settings.load()
        self.assertEqual((s["opacity"], s["gap"], s["radius"], s["max_fps"]), (0.3, 0, 24, 5))
        self.write({"opacity": 7, "max_fps": 500})
        s = settings.load()
        self.assertEqual((s["opacity"], s["max_fps"]), (1.0, 60))

    def test_unknown_choices_fall_back(self):
        self.write({"view": "xyz", "accent": "neon", "tokens_at_start": "maybe"})
        s = settings.load()
        self.assertEqual((s["view"], s["accent"], s["tokens_at_start"]), ("auto", "system", "last"))

    def test_valid_choices_kept(self):
        self.write({"view": "terminal", "accent": "teal", "tokens_at_start": "open"})
        s = settings.load()
        self.assertEqual((s["view"], s["accent"], s["tokens_at_start"]), ("terminal", "teal", "open"))

    def test_unknown_scheme_still_renders(self):
        self.write({"scheme": "does not exist"})
        self.assertEqual(settings.scheme(settings.load()["scheme"]), settings.SCHEMES["agentControl"])

    def test_save_roundtrip_and_drops_unknown_keys(self):
        s = settings.load() | {"gap": 4, "bogus": 1}
        settings.save(s)
        with open(settings.PATH) as f:
            raw = json.load(f)
        self.assertNotIn("bogus", raw)
        self.assertEqual(settings.load()["gap"], 4)
        self.assertFalse(os.path.exists(settings.PATH + ".tmp"))

    def test_save_creates_directory(self):
        shutil.rmtree(os.path.dirname(settings.PATH), ignore_errors=True)
        settings.save(settings.load())
        self.assertTrue(os.path.exists(settings.PATH))

    def test_unicode_survives(self):
        settings.save(settings.load() | {"claude_args": "--append-system-prompt 'Antworte auf Deutsch ü'"})
        self.assertIn("ü", settings.get("claude_args"))

    def test_get(self):
        self.write({"radius": 6})
        self.assertEqual(settings.get("radius"), 6)


class Schemes(unittest.TestCase):
    def test_all_schemes_are_complete(self):
        for name, sch in settings.SCHEMES.items():
            if sch is None:
                continue
            fg, bg, pal = sch
            self.assertEqual(len(pal), 16, name)
            for c in (fg, bg, *pal):
                self.assertRegex(c, r"^#[0-9a-fA-F]{6}$", name)

    def test_defaults_are_valid(self):
        for k, v in settings.DEFAULTS.items():
            self.assertTrue(settings._valid(k, v), k)
            if k in settings.RANGES:
                lo, hi = settings.RANGES[k]
                self.assertTrue(lo <= v <= hi, k)


class KittyScheme(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def write(self, name, text):
        path = os.path.join(self.dir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        return path

    def test_include_and_override_order(self):
        self.write("theme.conf", "foreground #111111\nbackground #222222\ncolor1 #333333\n")
        conf = self.write("kitty.conf", "include theme.conf\ncolor1 #444444\n")
        fg, bg, pal = settings.kitty_scheme(conf)
        self.assertEqual((fg, bg, pal[1]), ("#111111", "#222222", "#444444"))

    def test_later_include_wins(self):
        self.write("theme.conf", "color1 #555555\n")
        conf = self.write("kitty.conf", "color1 #444444\ninclude theme.conf\n")
        self.assertEqual(settings.kitty_scheme(conf)[2][1], "#555555")

    def test_absolute_and_nested_include(self):
        deep = self.write("a/b/deep.conf", "color2 #abcdef\n")
        self.write("a/mid.conf", f"include {deep}\n")
        conf = self.write("kitty.conf", "include a/mid.conf\n")
        self.assertEqual(settings.kitty_scheme(conf)[2][2], "#abcdef")

    def test_home_include(self):
        os.makedirs(os.path.expanduser("~/kittytest"), exist_ok=True)
        with open(os.path.expanduser("~/kittytest/t.conf"), "w") as f:
            f.write("color3 #123456\n")
        conf = self.write("kitty.conf", "include ~/kittytest/t.conf\n")
        self.assertEqual(settings.kitty_scheme(conf)[2][3], "#123456")

    def test_include_loop_terminates(self):
        conf = self.write("kitty.conf", "include kitty.conf\ncolor4 #0000ff\n")
        self.assertEqual(settings.kitty_scheme(conf)[2][4], "#0000ff")

    def test_missing_include_is_ignored(self):
        conf = self.write("kitty.conf", "include nope.conf\ncolor5 #00ff00\n")
        self.assertEqual(settings.kitty_scheme(conf)[2][5], "#00ff00")

    def test_invalid_values_and_comments_ignored(self):
        conf = self.write("kitty.conf", "# color1 #ffffff\ncolor1 red\ncolor2 #12345\ncolor3\n"
                                        "background_opacity 0.7\ncolor20 #ffffff\n")
        self.assertIsNone(settings.kitty_scheme(conf))

    def test_missing_file(self):
        self.assertIsNone(settings.kitty_scheme(os.path.join(self.dir, "missing.conf")))
        self.assertEqual(settings.scheme("kitty"), settings.SCHEMES["agentControl"])  # no kitty.conf in test HOME

    def test_partial_kitty_keeps_rest_of_default(self):
        conf = self.write("kitty.conf", "color1 #010101\n")
        fg, bg, pal = settings.kitty_scheme(conf)
        default = settings.SCHEMES["agentControl"]
        self.assertEqual((fg, bg, pal[0], pal[1]), (default[0], default[1], default[2][0], "#010101"))

    def test_scheme_kitty_reads_home_config(self):
        path = os.path.expanduser("~/.config/kitty/kitty.conf")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("background #0c141c\n")
        try:
            self.assertEqual(settings.scheme("kitty")[1], "#0c141c")
        finally:
            os.remove(path)

    def test_result_is_valid_hex(self):
        conf = self.write("kitty.conf", "color0 #AbCdEf\n")
        for c in settings.kitty_scheme(conf)[2]:
            self.assertTrue(re.fullmatch(r"#[0-9a-fA-F]{6}", c))


if __name__ == "__main__":
    unittest.main()
