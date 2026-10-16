import json
import os
import shutil
import unittest
from types import SimpleNamespace

from agentcontrol import projects, settings

HOME = os.path.expanduser("~")


def reset():
    for p in (projects.CONFIG, settings.PATH):
        if os.path.exists(p):
            os.remove(p)


class Slug(unittest.TestCase):
    def test_cases(self):
        for raw, want in (("My App", "my-app"), ("  a//b  ", "a-b"), ("Über café", "ber-caf"), ("", "project"),
                          ("---", "project"), ("x_y-z", "x_y-z"), ("ÄÖÜ", "project"), ("a  b", "a-b")):
            self.assertEqual(projects.slug(raw), want, raw)


class Root(unittest.TestCase):
    def setUp(self):
        reset()

    def test_default(self):
        self.assertEqual(projects.root(), "~/projects")
        self.assertEqual(projects.default_dir("My App"), "~/projects/my-app")

    def test_from_settings_and_trailing_slash(self):
        settings.save(settings.load() | {"projects_dir": "~/code/"})
        self.assertEqual(projects.root(), "~/code")
        self.assertEqual(projects.default_dir("x"), "~/code/x")

    def test_blank_setting_falls_back(self):
        settings.save(settings.load() | {"projects_dir": "   "})
        self.assertEqual(projects.root(), "~/projects")

    def test_root_slash_only(self):
        settings.save(settings.load() | {"projects_dir": "/"})
        self.assertEqual(projects.default_dir("x"), "/x")


class Presets(unittest.TestCase):
    def setUp(self):
        reset()
        shutil.rmtree(os.path.join(HOME, "work"), ignore_errors=True)

    def test_first_load_creates_empty_file(self):
        self.assertEqual(projects.load(), [])
        with open(projects.CONFIG) as f:
            self.assertEqual(json.load(f), {"projects": []})

    def test_corrupt_file(self):
        os.makedirs(os.path.dirname(projects.CONFIG), exist_ok=True)
        with open(projects.CONFIG, "w") as f:
            f.write("{oops")
        self.assertEqual(projects.load(), [])

    def test_incomplete_entries_skipped(self):
        os.makedirs(os.path.dirname(projects.CONFIG), exist_ok=True)
        with open(projects.CONFIG, "w") as f:
            json.dump({"projects": [{"name": "a"}, {"path": "~/x"}, {"name": "", "path": "~/y"},
                                    {"name": "ok", "path": "~/ok"}]}, f)
        self.assertEqual([p.name for p in projects.load()], ["ok"])

    def test_upsert_creates_dir_and_stores_short_path(self):
        p = projects.upsert("web", "~/work/web")
        self.assertTrue(os.path.isdir(os.path.join(HOME, "work", "web")))
        self.assertEqual(p.short_path, "~/work/web")
        with open(projects.CONFIG) as f:
            self.assertEqual(json.load(f)["projects"], [{"name": "web", "path": "~/work/web"}])

    def test_upsert_without_create(self):
        projects.upsert("ghost", "~/work/ghost", create=False)
        self.assertFalse(os.path.exists(os.path.join(HOME, "work", "ghost")))

    def test_upsert_normalizes_path(self):
        p = projects.upsert("n", "~/work/./a/../n/")
        self.assertEqual(p.path, os.path.join(HOME, "work", "n"))

    def test_upsert_rejects(self):
        projects.upsert("one", "~/work/one")
        with self.assertRaises(ValueError):
            projects.upsert("one", "~/work/other")
        with self.assertRaises(ValueError):
            projects.upsert("   ", "~/work/x")
        with self.assertRaises(ValueError):
            projects.upsert(projects.OTHER, "~/work/x")
        open(os.path.join(HOME, "work", "file"), "w").close()
        with self.assertRaises(ValueError):
            projects.upsert("f", "~/work/file")
        self.assertEqual([p.name for p in projects.load()], ["one"])

    def test_rename_keeps_position(self):
        for n in ("a", "b", "c"):
            projects.upsert(n, f"~/work/{n}")
        projects.upsert("B2", "~/work/b", old_name="b")
        self.assertEqual([p.name for p in projects.load()], ["a", "B2", "c"])

    def test_rename_to_own_name_is_fine(self):
        projects.upsert("a", "~/work/a")
        projects.upsert("a", "~/work/a2", old_name="a")
        self.assertEqual(projects.get("a").short_path, "~/work/a2")

    def test_rename_onto_other_name_rejected(self):
        projects.upsert("a", "~/work/a")
        projects.upsert("b", "~/work/b")
        with self.assertRaises(ValueError):
            projects.upsert("b", "~/work/a", old_name="a")

    def test_remove_and_get(self):
        projects.upsert("a", "~/work/a")
        projects.remove("a")
        projects.remove("never-existed")
        self.assertIsNone(projects.get("a"))
        self.assertTrue(os.path.isdir(os.path.join(HOME, "work", "a")))  # files stay

    def test_short_path(self):
        self.assertEqual(projects.Project("x", HOME).short_path, "~")
        self.assertEqual(projects.Project("x", HOME + "2/a").short_path, HOME + "2/a")  # not under HOME
        self.assertEqual(projects.Project("x", "/opt/a").short_path, "/opt/a")


class Matching(unittest.TestCase):
    P = [projects.Project("a", "/w/a"), projects.Project("ab", "/w/a/b"), projects.Project("foo", "/w/foo")]

    def test_most_specific(self):
        self.assertEqual(projects.match("/w/a/b/c", self.P).name, "ab")
        self.assertEqual(projects.match("/w/a/x", self.P).name, "a")
        self.assertEqual(projects.match("/w/a", self.P).name, "a")

    def test_prefix_is_not_a_parent(self):
        self.assertIsNone(projects.match("/w/foobar", self.P))

    def test_empty_and_unnormalized(self):
        self.assertIsNone(projects.match("", self.P))
        self.assertEqual(projects.match("/w/a/b/../x/", self.P).name, "a")

    def test_root_project(self):
        self.assertEqual(projects.match("/anything", [projects.Project("r", "/")]).name, "r")

    def test_group_of(self):
        s = SimpleNamespace(project="", path="/w/foo/sub", cwd="/elsewhere")
        self.assertEqual(projects.group_of(s, self.P), "foo")
        s = SimpleNamespace(project="a", path="/w/foo", cwd="/w/foo")  # explicit project wins
        self.assertEqual(projects.group_of(s, self.P), "a")
        s = SimpleNamespace(project="deleted", path="", cwd="/w/a/b")  # unknown project: by directory
        self.assertEqual(projects.group_of(s, self.P), "ab")
        s = SimpleNamespace(project="", path="", cwd="/tmp")
        self.assertEqual(projects.group_of(s, self.P), projects.OTHER)


if __name__ == "__main__":
    unittest.main()
