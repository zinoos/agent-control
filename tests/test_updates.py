"""The update check and fast-forward, against a real git checkout and a bare origin in a temp dir (no network)."""

import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from agentcontrol import updates


def git(cwd, *args):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                       cwd=cwd, capture_output=True, text=True, check=True)
    return r.stdout.strip()


class Repo(unittest.TestCase):
    """origin (bare) with one commit on master, a clone to update, and a second clone that pushes new commits."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentcontrol-updates-")
        self.origin = os.path.join(self.tmp, "origin.git")
        self.checkout = os.path.join(self.tmp, "checkout")
        self.pusher = os.path.join(self.tmp, "pusher")
        git(self.tmp, "init", "-q", "--bare", "-b", "master", self.origin)
        git(self.tmp, "clone", "-q", self.origin, self.pusher)
        self.commit("first")
        git(self.pusher, "push", "-q", "origin", "master")
        git(self.tmp, "clone", "-q", self.origin, self.checkout)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def commit(self, msg, path=None):
        path = path or os.path.join(self.pusher, "f.txt")
        with open(path, "a") as f:
            f.write(msg + "\n")
        git(self.pusher, "add", "-A")
        git(self.pusher, "commit", "-q", "-m", msg)

    def publish(self, *msgs):
        for m in msgs:
            self.commit(m)
        git(self.pusher, "push", "-q", "origin", "master")

    def local_commit(self, name):  # a commit made in the checkout and never pushed
        with open(os.path.join(self.checkout, name), "w") as f:
            f.write(name + "\n")
        git(self.checkout, "add", "-A")
        git(self.checkout, "commit", "-q", "-m", name)


class Check(Repo):
    def test_up_to_date_returns_none(self):
        self.assertIsNone(updates.check(self.checkout))

    def test_behind_reports_commit_count_and_shas(self):
        self.publish("second", "third")
        u = updates.check(self.checkout)
        self.assertEqual(u.behind, 2)
        self.assertEqual(u.local, git(self.checkout, "rev-parse", "--short", "HEAD"))
        self.assertEqual(u.remote, git(self.origin, "rev-parse", "--short", "master"))

    def test_diverged_checkout_is_not_offered(self):
        self.publish("second")
        self.local_commit("local.txt")
        self.assertIsNone(updates.check(self.checkout))

    def test_not_a_git_checkout_returns_none(self):
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        self.assertIsNone(updates.check(plain))

    def test_disabled_by_env(self):
        self.publish("second")
        with mock.patch.dict(os.environ, {"AGENTCONTROL_NO_UPDATE_CHECK": "1"}):
            self.assertIsNone(updates.check(self.checkout))

    def test_offline_raises(self):
        self.publish("second")
        shutil.rmtree(self.origin)
        with self.assertRaises(RuntimeError):
            updates.check(self.checkout)

    def test_other_branch_checked_out_still_compares_master(self):
        git(self.checkout, "checkout", "-q", "-b", "feature")
        self.publish("second")
        self.assertEqual(updates.check(self.checkout).behind, 1)


class Apply(Repo):
    def test_fast_forwards_to_origin_master(self):
        self.publish("second", "third")
        sha = updates.apply(self.checkout)
        self.assertEqual(sha, git(self.origin, "rev-parse", "--short", "master"))
        self.assertIsNone(updates.check(self.checkout))

    def test_refuses_when_local_changes_block_the_merge(self):
        self.publish("second")
        with open(os.path.join(self.checkout, "f.txt"), "a") as f:  # uncommitted edit to a file the update changes
            f.write("local edit\n")
        before = git(self.checkout, "rev-parse", "HEAD")
        with self.assertRaises(RuntimeError):
            updates.apply(self.checkout)
        self.assertEqual(git(self.checkout, "rev-parse", "HEAD"), before)

    def test_refuses_diverged_history(self):
        self.publish("second")
        self.local_commit("local.txt")
        before = git(self.checkout, "rev-parse", "HEAD")
        with self.assertRaises(RuntimeError):
            updates.apply(self.checkout)
        self.assertEqual(git(self.checkout, "rev-parse", "HEAD"), before)


class NotAGitCheckout(unittest.TestCase):
    def test_is_checkout(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(updates.is_checkout(d))
            os.makedirs(os.path.join(d, ".git"))
            self.assertTrue(updates.is_checkout(d))
