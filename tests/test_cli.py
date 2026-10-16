import contextlib
import io
import os
import shutil
import subprocess
import unittest

from agentcontrol import cli, projects, sessions, settings
from tests import ROOT

HAS_TMUX = shutil.which("tmux") is not None


def ac(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(list(args))
        except (RuntimeError, ValueError) as ex:  # what cli.run() turns into "ac: ..." and exit 1
            print(f"ac: {ex}", file=err)
            code = 1
    return code, out.getvalue(), err.getvalue()


class Cli(unittest.TestCase):
    def setUp(self):
        for p in (settings.PATH, projects.CONFIG):
            if os.path.exists(p):
                os.remove(p)

    def test_help(self):
        for args in ((), ("-h",), ("--help",), ("help",)):
            code, out, _ = ac(*args)
            self.assertEqual(code, 0)
            self.assertIn("ac new", out)

    def test_unknown_command(self):
        code, _, err = ac("frobnicate")
        self.assertEqual(code, 2)
        self.assertIn("unknown command", err)

    def test_project_add_ls_rm(self):
        code, out, _ = ac("project", "add", "Demo")
        self.assertEqual((code, out.strip()), (0, "project Demo: ~/projects/demo"))
        self.assertTrue(os.path.isdir(os.path.expanduser("~/projects/demo")))
        _, out, _ = ac("projects")
        self.assertIn("~/projects/demo", out)
        self.assertEqual(ac("project", "rm", "Demo")[0], 0)
        code, _, err = ac("project", "rm", "Demo")
        self.assertEqual(code, 1)
        self.assertIn("no project", err)

    def test_project_add_uses_settings_folder(self):
        settings.save(settings.load() | {"projects_dir": "~/elsewhere"})
        _, out, _ = ac("project", "add", "x")
        self.assertIn("~/elsewhere/x", out)

    def test_project_add_errors(self):
        ac("project", "add", "dup", "~/work/dup")
        code, _, err = ac("project", "add", "dup", "~/work/dup2")
        self.assertEqual(code, 1)
        self.assertIn("already", err)

    def test_wrong_project_arity_is_unknown(self):
        self.assertEqual(ac("project")[0], 2)
        self.assertEqual(ac("project", "add")[0], 2)
        self.assertEqual(ac("project", "add", "a", "b", "c")[0], 2)

    @unittest.skipUnless(HAS_TMUX, "tmux not installed")
    def test_new_ls_kill(self):
        sessions.ensure_server()
        try:
            code, out, _ = ac("new", "cli-agent", "-C", "~", "--", "--model", "opus")
            self.assertEqual(code, 0)
            self.assertIn("started cli-agent", out)
            _, out, _ = ac("ls")
            self.assertIn("cli-agent", out)
            code, _, err = ac("new", "cli-agent", "-C", "~")
            self.assertEqual(code, 1)
            self.assertIn("already exists", err)
            self.assertEqual(ac("kill", "cli-agent")[0], 0)
            _, out, _ = ac("ls")
            self.assertIn("no agents", out)
        finally:
            for s in sessions.list_sessions():
                sessions.kill(s.name)

    def test_bin_ac_runs(self):
        r = subprocess.run([os.path.join(ROOT, "bin", "ac"), "--help"], capture_output=True, text=True,
                           env=os.environ | {"AGENTCONTROL_PYTHON": shutil.which("python3")})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ac new", r.stdout)

    def test_bin_ac_error_exit_code(self):
        r = subprocess.run([os.path.join(ROOT, "bin", "ac"), "project", "rm", "nope"], capture_output=True,
                           text=True, env=os.environ | {"AGENTCONTROL_PYTHON": shutil.which("python3")})
        self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
