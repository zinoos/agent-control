"""The tests' own HOME: nothing they write may land in the real config, however the suite is started."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import tests  # noqa: F401  first: this run's own HOME and config, before agentcontrol reads its paths
from agentcontrol import external, history, projects, sessions, settings
from tests import ROOT, TMP


class Sandbox(unittest.TestCase):
    def test_paths_are_in_the_test_dir(self):
        for path in (projects.CONFIG, settings.PATH, history.CLAUDE_DIR, external.REGISTRY):
            self.assertTrue(path.startswith(TMP + os.sep), path)
        self.assertEqual(sessions.SOCKET, "agentcontrol-test")

    def test_other_ways_of_starting_the_suite_keep_the_real_config(self):
        # The ways that skip tests/__init__.py unless every test module imports it: they used to wipe the user's
        # projects. Runs the project tests in a child with a stand-in for the real HOME.
        home = tempfile.mkdtemp(prefix="agentcontrol-realhome-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        conf = os.path.join(home, ".config", "agentcontrol")
        mine = {"projects": [{"name": "mine", "path": "~/mine"}]}
        env = {k: v for k, v in os.environ.items()
               if k not in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "CLAUDE_CONFIG_DIR", "TMUX_TMPDIR", "AC_TEST_ARGS",
                            "AGENTCONTROL_SOCKET")}
        env |= {"HOME": home, "PYTHONPATH": os.path.join(ROOT, "src")}
        for argv in (["discover", "-s", "tests"], ["discover", "tests"], ["tests.test_projects"]):
            with self.subTest(argv=argv):
                shutil.rmtree(conf, ignore_errors=True)
                os.makedirs(conf)
                with open(os.path.join(conf, "projects.json"), "w") as f:
                    json.dump(mine, f)
                r = subprocess.run([sys.executable, "-m", "unittest", *argv, "-k", "test_projects"], cwd=ROOT,
                                   env=env, capture_output=True, text=True, timeout=120)
                self.assertIn("OK", r.stderr.splitlines()[-1] if r.stderr else "", r.stderr[-2000:])
                with open(os.path.join(conf, "projects.json")) as f:
                    self.assertEqual(json.load(f), mine)
                self.assertFalse(os.path.exists(os.path.join(conf, "settings.json")))
