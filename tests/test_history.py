import json
import os
import shutil
import time
import unittest
from unittest import mock

from agentcontrol import history

PROJ = os.path.join(history.CLAUDE_DIR, "projects", "-home-test")
SESS = os.path.join(history.CLAUDE_DIR, "sessions")


def user(text, **kw):
    return {"type": "user", "message": {"content": text}} | kw


def transcript(sid, records, mtime=None):
    os.makedirs(PROJ, exist_ok=True)
    path = os.path.join(PROJ, f"{sid}.jsonl")
    with open(path, "w") as f:
        for r in records:
            f.write((r if isinstance(r, str) else json.dumps(r)) + "\n")
    if mtime:
        os.utime(path, (mtime, mtime))
    return path


class PromptText(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(history._prompt_text(user("  fix   the\nbug ")), "fix the bug")
        self.assertEqual(history._prompt_text(user([{"type": "text", "text": "a"}, {"type": "image"},
                                                    {"type": "text", "text": "b"}, "junk"])), "a b")
        self.assertEqual(history._prompt_text(user("<command-name>/model</command-name>")), "")
        self.assertEqual(history._prompt_text(user("hi", isMeta=True)), "")
        self.assertEqual(history._prompt_text(user("hi", isSidechain=True)), "")
        self.assertEqual(history._prompt_text({"type": "assistant", "message": {"content": "x"}}), "")
        self.assertEqual(history._prompt_text({"type": "user"}), "")
        self.assertEqual(history._prompt_text({"type": "user", "message": None}), "")


class Past(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(history.CLAUDE_DIR, ignore_errors=True)

    def test_nothing(self):
        self.assertEqual(history.list_past(), [])

    def test_basic_and_title_priority(self):
        transcript("s1", [{"cwd": "/w"}, user("first question"),
                          {"type": "ai-title", "aiTitle": "AI title"},
                          {"type": "custom-title", "customTitle": "My title"}])
        transcript("s2", [{"cwd": "/w"}, user("only prompt")])
        transcript("s3", [{"cwd": "/w"}, user("q"), {"type": "last-prompt", "lastPrompt": "latest prompt"}])
        by = {p.id: p for p in history.list_past()}
        self.assertEqual(by["s1"].title, "My title")
        self.assertEqual(by["s1"].first_prompt, "first question")
        self.assertEqual(by["s2"].title, "only prompt")
        self.assertEqual(by["s3"].title, "latest prompt")

    def test_skips_empty_sessions(self):
        transcript("no-cwd", [user("hi")])
        transcript("no-prompt", [{"cwd": "/w"}, user("<local-command-stdout>x</local-command-stdout>")])
        transcript("blank", [])
        self.assertEqual(history.list_past(), [])

    def test_garbage_lines_ignored(self):
        transcript("g", ["not json", "{", {"cwd": "/w"}, "", user("real")])
        self.assertEqual([p.first_prompt for p in history.list_past()], ["real"])

    def test_newest_first_and_limit(self):
        now = time.time()
        for i in range(5):
            transcript(f"s{i}", [{"cwd": "/w"}, user(f"p{i}")], mtime=now - 100 * i)
        self.assertEqual([p.id for p in history.list_past()], ["s0", "s1", "s2", "s3", "s4"])
        self.assertEqual(len(history.list_past(limit=2)), 2)

    def test_big_transcript_reads_title_from_tail(self):
        filler = {"type": "assistant", "message": {"content": "x" * 1000}}
        transcript("big", [{"cwd": "/w"}, user("start")] + [filler] * 400
                   + [{"type": "ai-title", "aiTitle": "From the tail"}])
        self.assertGreater(os.path.getsize(os.path.join(PROJ, "big.jsonl")), history.CHUNK)
        p = history.list_past()[0]
        self.assertEqual((p.title, p.first_prompt), ("From the tail", "start"))

    def test_running(self):
        os.makedirs(SESS)
        for name, d in (("me", {"pid": os.getpid(), "sessionId": "s1"}),
                        ("dead", {"pid": 2 ** 22 + 999, "sessionId": "s2"}),
                        ("bad", {"pid": "x", "sessionId": "s3"}),
                        ("nokey", {"pid": os.getpid()})):
            with open(os.path.join(SESS, f"{name}.json"), "w") as f:
                json.dump(d, f)
        with open(os.path.join(SESS, "broken.json"), "w") as f:
            f.write("{")
        self.assertEqual(history.running(), {"s1": os.getpid()})
        transcript("s1", [{"cwd": "/w"}, user("q")])
        self.assertEqual(history.list_past()[0].pid, os.getpid())

    def test_file_vanishing_mid_scan(self):
        transcript("gone", [{"cwd": "/w"}, user("q")])
        with mock.patch.object(history, "_lines", side_effect=OSError):
            self.assertEqual(history.list_past(), [])


class Formatting(unittest.TestCase):
    def p(self, **kw):
        return history.PastSession("id", kw.get("cwd", "/w"), "t", "f", kw.get("mtime", time.time()))

    def test_when(self):
        now = time.time()
        self.assertEqual(self.p(mtime=now).when, "just now")
        self.assertEqual(self.p(mtime=now - 300).when, "5 min ago")
        self.assertEqual(self.p(mtime=now - 7200).when, "2 h ago")
        self.assertEqual(self.p(mtime=now - 3 * 86400).when, "3 d ago")
        self.assertEqual(self.p(mtime=now + 1000).when, "just now")  # clock skew

    def test_short_cwd(self):
        home = os.path.expanduser("~")
        self.assertEqual(self.p(cwd=home + "/x").short_cwd, "~/x")
        self.assertEqual(self.p(cwd=home + "x").short_cwd, home + "x")


if __name__ == "__main__":
    unittest.main()
