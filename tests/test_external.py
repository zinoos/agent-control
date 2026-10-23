import json
import os
import shutil
import subprocess
import unittest
from unittest import mock

import tests  # noqa: F401  first: this run's own HOME and config, before agentcontrol reads its paths
from agentcontrol import external, history, projects, sessions
from tests.test_cli import ac

HAS_TMUX = shutil.which("tmux") is not None


def proc_start(pid):
    with open(f"/proc/{pid}/stat") as f:
        stat = f.read()
    return stat[stat.rindex(")") + 2:].split()[19]


def register(pid, sid="s1", cwd="/w", **kw):
    """A Claude Code registry entry, like ~/.claude/sessions/<pid>.json."""
    os.makedirs(external.REGISTRY, exist_ok=True)
    d = {"pid": pid, "sessionId": sid, "cwd": cwd, "procStart": proc_start(pid), "kind": "interactive",
         "status": "idle", "name": "derived-name", "nameSource": "derived"} | kw
    with open(os.path.join(external.REGISTRY, f"{pid}.json"), "w") as f:
        json.dump(d, f)


def transcript(sid, cwd, records):
    d = os.path.join(history.CLAUDE_DIR, "projects", cwd.replace("/", "-"))
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{sid}.jsonl")
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return path


def user(text, **kw):
    return {"type": "user", "message": {"content": text}} | kw


def assistant(*blocks, **kw):
    return {"type": "assistant", "message": {"content": list(blocks)}} | kw


class Render(unittest.TestCase):
    def test_kinds(self):
        recs = [
            user("fix the bug"),
            assistant({"type": "thinking", "thinking": "hmm"}, {"type": "text", "text": "  Looking.  "}),
            assistant({"type": "tool_use", "name": "Bash", "input": {"command": "git status\n--short",
                                                                      "description": "Show status"}}),
            user([{"type": "tool_result", "content": "\n\n M a.py\n M b.py"}]),
            assistant({"type": "tool_use", "name": "mcp__hypr-computer__click", "input": {"x": 1, "text": "Save"}}),
            user([{"type": "tool_result", "is_error": True, "content": [{"type": "text", "text": "no window"}]}]),
            assistant({"type": "tool_use", "name": "TodoWrite", "input": {"todos": []}}),
            assistant({"type": "text", "text": "Done."}),
        ]
        self.assertEqual(external.render(recs), [
            ("prompt", "fix the bug"), ("text", "Looking."), ("tool", "Bash(git status --short)"),
            ("result", "M a.py"), ("tool", "hypr-computer · click(Save)"), ("result", "error: no window"),
            ("tool", "TodoWrite"), ("text", "Done.")])

    def test_skips_what_claude_code_hides(self):
        recs = [user("meta", isMeta=True), user("<command-name>/model</command-name>"),
                user("This session is being continued from a previous conversation…", isCompactSummary=True),
                {"type": "system", "subtype": "turn_duration"},
                user("sub", isSidechain=True), assistant({"type": "text", "text": "sub"}, isSidechain=True),
                {"type": "ai-title", "aiTitle": "t"}, {"type": "attachment"}, {"type": "user"},
                {"type": "assistant", "message": None}, assistant("junk", {"type": "text", "text": " "}),
                {"type": "assistant", "message": {"content": "plain string answer"}}]
        self.assertEqual(external.render(recs), [("text", "plain string answer")])

    def test_compaction(self):
        recs = [{"type": "system", "subtype": "compact_boundary", "content": "Conversation compacted"},
                user("summary of everything so far", isCompactSummary=True), user("go on")]
        self.assertEqual(external.render(recs), [("note", "conversation compacted"), ("prompt", "go on")])

    def test_long_lines_are_cut(self):
        tool, = external.render([assistant({"type": "tool_use", "name": "Bash", "input": {"command": "x" * 500}})])
        self.assertLess(len(tool[1]), 120)
        self.assertTrue(tool[1].endswith("…)"))

    def test_as_text(self):
        self.assertEqual(external.as_text([("prompt", "q"), ("tool", "Bash(ls)"), ("result", "a"), ("text", "ok"),
                                           ("note", "conversation compacted")]),
                         "❯ q\n● Bash(ls)\n  ⎿ a\nok\n── conversation compacted")


class Tail(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tests.TMP, "tail.jsonl")
        if os.path.exists(self.path):
            os.remove(self.path)

    def write(self, text, mode="a"):
        with open(self.path, mode) as f:
            f.write(text)

    def test_follows_appends_and_waits_for_whole_lines(self):
        self.write('{"n": 1}\n{"n": 2}\n')
        t = external.Tail(self.path)
        self.assertEqual(t.read(), [{"n": 1}, {"n": 2}])
        self.assertEqual(t.read(), [])
        self.write('{"n": 3}\n{"n": ')
        self.assertEqual(t.read(), [{"n": 3}])
        self.write('4}\n')
        self.assertEqual(t.read(), [{"n": 4}])

    def test_starts_at_the_end_of_a_big_file(self):
        self.write("".join(json.dumps({"n": i, "pad": "x" * 50}) + "\n" for i in range(100)))
        got = external.Tail(self.path, chunk=1000).read()
        self.assertTrue(0 < len(got) < 20)
        self.assertEqual(got[-1]["n"], 99)
        self.assertEqual([r["n"] for r in got], list(range(100 - len(got), 100)))  # no half line at the cut

    def test_replaced_file_starts_over(self):
        self.write('{"n": 1}\n{"n": 2}\n')
        t = external.Tail(self.path)
        t.read()
        self.write('{"n": 9}\n', mode="w")
        self.assertEqual(t.read(), [{"n": 9}])

    def test_missing_file_and_garbage(self):
        t = external.Tail(self.path)
        self.assertEqual(t.read(), [])
        self.write('not json\n[1, 2]\n\n{"ok": true}\n')
        self.assertEqual(t.read(), [{"ok": True}])


class Place(unittest.TestCase):
    def test_window_of_the_nearest_ancestor(self):
        with mock.patch.object(external, "_comm", return_value="bash"):
            self.assertEqual(external._place({}, [10, 20, 30], {20: ("dev.zed.Zed", "0xa"), 30: ("kitty", "0xb")}),
                             ("Zed", "0xa"))
            self.assertEqual(external._place({"entrypoint": "claude-desktop"}, [10, 20], {20: ("electron", "0xc")}),
                             ("Claude Desktop", "0xc"))
            self.assertEqual(external._place({"entrypoint": "claude-desktop"}, [10], {}), ("Claude Desktop", ""))
            self.assertEqual(external._place({}, [10], {}), ("terminal", ""))

    def test_tmux_and_other_programs(self):
        comms = {10: "bash", 20: "tmux: server", 30: "sshd"}
        with mock.patch.object(external, "_comm", side_effect=lambda pid: comms.get(pid, "")):
            self.assertEqual(external._place({}, [10, 20, 30], {}), ("tmux", ""))
            self.assertEqual(external._place({}, [10, 30], {}), ("sshd", ""))

    def test_no_hyprland(self):
        with mock.patch.dict(os.environ, {"HYPRLAND_INSTANCE_SIGNATURE": ""}):
            self.assertEqual(external._clients(), {})


class ListExternal(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(history.CLAUDE_DIR, ignore_errors=True)
        external._places.clear()
        external._titles.clear()
        self.proc = subprocess.Popen(["sleep", "60"])
        self.addCleanup(self.proc.wait)
        self.addCleanup(self.proc.kill)

    def test_listed_with_state_title_and_transcript(self):
        register(self.proc.pid, status="busy")
        path = transcript("s1", "/w", [{"cwd": "/w"}, user("first"), {"type": "ai-title", "aiTitle": "Fix tests"}])
        e, = external.list_external([])
        self.assertEqual((e.pid, e.session_id, e.cwd, e.path, e.status, e.state), (self.proc.pid, "s1", "/w", "/w",
                                                                                   "busy", "working"))
        self.assertEqual(e.title, "Fix tests")
        self.assertEqual(e.label, "Fix tests")
        self.assertEqual(e.transcript, path)
        self.assertEqual(e.name, f"{projects.slug(e.where)}-{self.proc.pid}")
        self.assertIsNone(e.desk)

    def test_title_falls_back_and_user_names_win(self):
        register(self.proc.pid)
        self.assertEqual(external.list_external([])[0].title, "derived-name")  # no transcript yet
        transcript("s1", "/w", [{"cwd": "/w"}, user("first")])
        self.assertEqual(external.list_external([])[0].title, "first")
        register(self.proc.pid, name="My session", nameSource="user")
        self.assertEqual(external.list_external([])[0].title, "My session")
        shutil.rmtree(os.path.join(history.CLAUDE_DIR, "projects"))
        register(self.proc.pid, name="", nameSource="")
        e, = external.list_external([])
        self.assertEqual((e.title, e.label), ("", e.name))

    def test_transcript_in_another_folder(self):
        register(self.proc.pid, cwd="/moved")
        path = transcript("s1", "/w", [{"cwd": "/w"}, user("first")])
        self.assertEqual(external.list_external([])[0].transcript, path)

    def test_stale_and_foreign_entries_are_skipped(self):
        register(self.proc.pid, kind="sdk")
        self.assertEqual(external.list_external([]), [])
        register(self.proc.pid, procStart="1")  # the pid was reused by another process
        self.assertEqual(external.list_external([]), [])
        register(self.proc.pid)
        self.proc.kill()
        self.proc.wait()
        self.assertEqual(external.list_external([]), [])
        with open(os.path.join(external.REGISTRY, "bad.json"), "w") as f:
            f.write("{broken")
        with open(os.path.join(external.REGISTRY, "nopid.json"), "w") as f:
            json.dump({"sessionId": "x"}, f)
        self.assertEqual(external.list_external([]), [])

    def test_no_registry(self):
        self.assertEqual(external.list_external([]), [])

    def test_sessions_in_our_tmux_are_not_external(self):
        register(self.proc.pid)
        ours = sessions.Session("agent-1", self.proc.pid, "", "/w", 0, 0)
        self.assertEqual(external.list_external([ours]), [])
        parent = sessions.Session("agent-1", os.getpid(), "", "/w", 0, 0)  # claude runs under the pane's shell
        self.assertEqual(external.list_external([parent]), [])

    def test_grouped_by_project_folder(self):
        register(self.proc.pid, cwd="/w/app/src")
        e, = external.list_external([])
        self.assertEqual(projects.group_of(e, [projects.Project("app", "/w/app")]), "app")
        self.assertEqual(projects.group_of(e, []), projects.OTHER)

    def test_get_and_peek(self):
        register(self.proc.pid)
        transcript("s1", "/w", [{"cwd": "/w"}, user("first"), assistant({"type": "text", "text": "a\nb\nc"})])
        e = external.get(external.list_external([])[0].name)
        self.assertEqual(external.peek(e, 2), "b\nc")
        self.assertEqual(external.peek(e), "❯ first\na\nb\nc")
        self.assertIsNone(external.get("nope"))
        e.transcript = ""
        self.assertEqual(external.peek(e), "")

    def test_raise_window(self):
        e = external.External("kitty-1", 1, "s", "/w", "kitty", "idle")
        self.assertFalse(external.raise_window(e))  # no window known
        e.window = "0xabc"
        with mock.patch.object(external.subprocess, "run",
                               return_value=subprocess.CompletedProcess([], 0, "ok\n", "")) as run:
            self.assertTrue(external.raise_window(e))
        self.assertEqual(run.call_args[0][0], ["hyprctl", "dispatch", "focuswindow", "address:0xabc"])


@unittest.skipUnless(HAS_TMUX, "needs tmux")
class Cli(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(history.CLAUDE_DIR, ignore_errors=True)
        external._places.clear()
        self.proc = subprocess.Popen(["sleep", "60"])
        self.addCleanup(self.proc.wait)
        self.addCleanup(self.proc.kill)
        register(self.proc.pid, status="busy")
        transcript("s1", "/w", [{"cwd": "/w"}, user("hello there"), {"type": "ai-title", "aiTitle": "Greeting"}])
        self.name = external.list_external([])[0].name

    def test_ls_and_peek(self):
        code, out, _ = ac("ls")
        self.assertEqual(code, 0)
        line = next(ln for ln in out.splitlines() if self.name in ln)
        self.assertIn("working", line)
        self.assertIn("Greeting", line)
        self.assertIn("outside agentControl", line)
        self.assertEqual(ac("peek", self.name), (0, "❯ hello there\n", ""))

    def test_cannot_be_driven(self):
        for cmd in (("send", self.name, "hi"), ("stop", self.name), ("kill", self.name), ("wait", self.name),
                    ("attach", self.name)):
            code, _, err = ac(*cmd)
            self.assertEqual(code, 1, cmd)
            self.assertIn("outside agentControl", err)


if __name__ == "__main__":
    unittest.main()
