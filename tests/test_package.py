"""The npm package: every file it ships exists, and its bin entries are executable scripts from the checkout."""

import json
import os
import unittest

from tests import ROOT


class Package(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "package.json")) as f:
            self.pkg = json.load(f)

    def test_bin_entries_are_executable(self):
        self.assertEqual(set(self.pkg["bin"]), {"agentcontrol", "ac"})
        for target in self.pkg["bin"].values():
            path = os.path.join(ROOT, target)
            self.assertTrue(os.path.isfile(path), target)
            self.assertTrue(os.access(path, os.X_OK), target)

    def test_every_listed_file_exists(self):
        for entry in self.pkg["files"]:
            self.assertTrue(os.path.exists(os.path.join(ROOT, entry)), entry)

    def test_package_ships_what_the_app_reads_at_runtime(self):
        for needed in ("bin", "src", "data", "tmux.conf", "scripts"):
            self.assertIn(needed, self.pkg["files"])
