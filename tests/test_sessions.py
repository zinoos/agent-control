import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

import tests  # noqa: F401  first: this run's own HOME and config, before agentcontrol reads its paths
from agentcontrol import projects, sessions, settings
from tests import ARGS

HAS_TMUX = shutil.which("tmux") is not None


def S(title="", **kw):
    return sessions.Session(kw.pop("name", "n"), 1, title, "/tmp", 0, 0, **kw)


class SessionState(unittest.TestCase):
    def test_state(self):
        self.assertEqual(S("").state, "starting")
        self.assertEqual(S("   ").state, "starting")
        self.assertEqual(S("⠋ Fixing tests").state, "working")
        self.assertEqual(S("✻ Thinking").state, "working")
        self.assertEqual(S("✳ Claude Code").state, "idle")
        self.assertEqual(S("bash").state, "idle")

    def test_label(self):
        self.assertEqual(S("⠋ Fixing tests").label, "Fixing tests")
        self.assertEqual(S("✳  Done ").label, "Done")
        self.assertEqual(S("vim").label, "vim")
        self.assertEqual(S("", name="agent-1").label, "agent-1")
        self.assertEqual(S("✳").label, "")


class Desks(unittest.TestCase):
    def setUp(self):
        self.run_dir = tempfile.mkdtemp()
        patcher = mock.patch.object(sessions, "RUN", self.run_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def desk(self, name, pid, **extra):
        with open(os.path.join(self.run_dir, f"{name}.json"), "w") as f:
            json.dump({"name": name, "wl_socket": f"wl-{name}", "width": 800, "height": 600, "pid": pid} | extra, f)

    def test_no_run_dir(self):
        with mock.patch.object(sessions, "RUN", "/nonexistent/dir"):
            self.assertEqual(sessions.list_desks(), [])

    def test_live_dead_and_broken(self):
        self.desk("alive", os.getpid(), place=5, owner_pid=1234)
        self.desk("dead", 2 ** 22 + 12345)
        with open(os.path.join(self.run_dir, "broken.json"), "w") as f:
            f.write("{")
        with open(os.path.join(self.run_dir, "partial.json"), "w") as f:
            json.dump({"name": "partial", "pid": os.getpid()}, f)
        open(os.path.join(self.run_dir, "notes.txt"), "w").close()
        desks = sessions.list_desks()
        self.assertEqual([d.name for d in desks], ["alive"])
        self.assertEqual((desks[0].place, desks[0].owner_pid, desks[0].accent), (5, 1234, "ff8c1a"))

    def test_activity(self):
        self.desk("d", os.getpid())
        with open(os.path.join(self.run_dir, "d.activity"), "w") as f:
            json.dump({"t": time.time(), "text": "click OK"}, f)
        d = sessions.list_desks()[0]
        self.assertTrue(d.active)
        self.assertEqual(d.last_text, "click OK")
        d.last_action = time.time() - sessions.ACTIVE_SECONDS - 1
        self.assertFalse(d.active)

    def test_desk_for(self):
        me = os.getpid()
        parent = os.getppid()
        d_child = sessions.Desk("c", "", 0, 0, 0, "", owner_pid=me)
        d_none = sessions.Desk("x", "", 0, 0, 0, "", owner_pid=0)
        self.assertIs(sessions._desk_for(me, [d_none, d_child]), d_child)  # owner is the pane itself
        self.assertIs(sessions._desk_for(parent, [d_child]), d_child)  # owner runs under the pane
        self.assertIsNone(sessions._desk_for(999999999, [d_child, d_none]))

    def test_ancestors_of_missing_pid(self):
        self.assertEqual(sessions._ancestors(2 ** 22 + 54321), [])
        self.assertIn(os.getppid(), sessions._ancestors(os.getpid()))


@unittest.skipUnless(HAS_TMUX, "tmux not installed")
class Tmux(unittest.TestCase):
    """Real tmux (own server, see tests/__init__.py) with a fake claude."""

    def setUp(self):
        for p in (settings.PATH, projects.CONFIG):
            if os.path.exists(p):
                os.remove(p)
        sessions.ensure_server()

    def tearDown(self):
        for s in sessions.list_sessions():
            sessions.kill(s.name)

    def args(self, name):
        for _ in range(100):
            path = os.path.join(ARGS, name)
            if os.path.exists(path):
                with open(path) as f:
                    return f.read().splitlines()
            time.sleep(0.05)
        self.fail(f"fake claude never started for {name}")

    def wait_screen(self, name, text):
        for _ in range(100):
            if text in sessions.peek(name, 50):
                return
            time.sleep(0.05)
        self.fail(f"{text!r} not on {name}'s screen:\n{sessions.peek(name, 50)}")

    def test_new_list_kill(self):
        n = sessions.new_session("t1", "~", None)
        self.assertEqual(n, "t1")
        ss = {s.name: s for s in sessions.list_sessions()}
        self.assertIn("t1", ss)
        self.assertEqual(ss["t1"].path, os.path.expanduser("~"))
        sessions.kill("t1")
        self.assertNotIn("t1", {s.name for s in sessions.list_sessions()})

    def test_name_sanitized_and_auto(self):
        self.assertEqual(sessions.new_session("my agent!/x", "~"), "my-agent-x")
        self.assertEqual(sessions.new_session("!!!", "~"), "agent")
        self.assertEqual(sessions.new_session(None, "~"), "agent-1")
        self.assertEqual(sessions.new_session(None, "~"), "agent-2")

    def test_duplicate_name(self):
        sessions.new_session("dup", "~")
        with self.assertRaises(RuntimeError):
            sessions.new_session("dup", "~")

    def test_unknown_project(self):
        with self.assertRaises(RuntimeError):
            sessions.new_session(None, None, project="nope")

    def test_project_name_dir_and_option(self):
        projects.upsert("My Proj", "~/work/my-proj")
        n = sessions.new_session(None, None, project="My Proj")
        self.assertEqual(n, "my-proj-1")
        s = next(s for s in sessions.list_sessions() if s.name == n)
        self.assertEqual(s.project, "My Proj")
        self.assertEqual(s.path, os.path.expanduser("~/work/my-proj"))
        sessions.set_project(n, None)
        self.assertEqual(next(s for s in sessions.list_sessions() if s.name == n).project, "")

    def test_prompt_and_args(self):
        sessions.new_session("a1", "~", "fix it; rm -rf / $(whoami) 'quoted'", ["--resume", "abc"])
        self.assertEqual(self.args("a1"), ["--resume", "abc", "fix it; rm -rf / $(whoami) 'quoted'"])

    def test_default_claude_args_from_settings(self):
        settings.save(settings.load() | {"claude_args": "--model opus --append-system-prompt 'be brief'"})
        sessions.new_session("a2", "~", "hi", ["--resume", "x"])
        self.assertEqual(self.args("a2"), ["--model", "opus", "--append-system-prompt", "be brief",
                                           "--resume", "x", "hi"])

    def test_bad_claude_args_start_nothing(self):
        settings.save(settings.load() | {"claude_args": "--model 'unclosed"})
        with self.assertRaises(ValueError):
            sessions.new_session("bad", "~")
        self.assertNotIn("bad", {s.name for s in sessions.list_sessions()})

    def test_missing_cwd(self):
        with self.assertRaises(RuntimeError):
            sessions.new_session("nowhere", "/nonexistent/dir/xyz")

    def test_title_gives_idle_state(self):
        sessions.new_session("idle1", "~")
        self.wait_screen("idle1", "READY")
        s = next(s for s in sessions.list_sessions() if s.name == "idle1")
        self.assertEqual(s.state, "idle")
        self.assertEqual(sessions.wait_idle("idle1", timeout=10, settle=0.2), "idle")

    def test_send_and_peek(self):
        sessions.new_session("talk", "~")
        self.wait_screen("talk", "READY")
        sessions.send("talk", "hello from the test")
        self.wait_screen("talk", "hello from the test")
        self.assertEqual(sessions.peek("talk", 1).strip() != "", True)

    def test_wait_on_ended_and_timeout(self):
        self.assertEqual(sessions.wait_idle("never-existed", timeout=2), "ended")
        self.assertEqual(sessions.wait_idle("x", timeout=0), "timeout")

    def test_actions_on_missing_session_raise(self):
        for fn in (sessions.kill, sessions.interrupt, lambda n: sessions.send(n, "x"), sessions.peek):
            with self.assertRaises(RuntimeError):
                fn("missing-session")


if __name__ == "__main__":
    unittest.main()
