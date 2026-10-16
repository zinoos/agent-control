"""Past Claude Code sessions, from the transcripts in ~/.claude/projects/<dir>/<session id>.jsonl, to resume
with `claude --resume <id>`. Transcripts can be tens of MB, so only each file's head (working directory, first
prompt) and tail (latest title) are read."""

from __future__ import annotations

import glob
import json
import os
import time
from dataclasses import dataclass

CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
CHUNK = 128 * 1024
# Title records Claude Code appends as a session goes on, most specific first.
TITLE_KEYS = (("custom-title", "customTitle"), ("agent-name", "agentName"), ("ai-title", "aiTitle"))


@dataclass
class PastSession:
    id: str
    cwd: str
    title: str
    first_prompt: str
    mtime: float
    pid: int = 0  # Claude Code process running it right now, if any

    @property
    def short_cwd(self) -> str:
        home = os.path.expanduser("~")
        return "~" + self.cwd[len(home):] if self.cwd == home or self.cwd.startswith(home + "/") else self.cwd

    @property
    def when(self) -> str:
        d = time.time() - self.mtime
        if d < 90:
            return "just now"
        for unit, secs in (("d", 86400), ("h", 3600), ("min", 60)):
            if d >= secs:
                return f"{int(d // secs)} {unit} ago"
        return ""


def _lines(path: str, tail: bool) -> list[dict]:
    with open(path, "rb") as f:
        if tail:
            f.seek(max(0, os.fstat(f.fileno()).st_size - CHUNK))
        data = f.read(CHUNK)
    raw = data.split(b"\n")
    raw = raw[1:] if tail else raw[:-1]  # drop the partial line at the cut
    out = []
    for line in raw:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _prompt_text(rec: dict) -> str:
    """The user's typed text in a user record, or "" for tool results, commands and injected context."""
    if rec.get("type") != "user" or rec.get("isMeta") or rec.get("isSidechain"):
        return ""
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, list):
        content = " ".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    text = " ".join(str(content or "").split())
    return "" if text.startswith("<") else text


def _read(path: str) -> PastSession | None:
    try:
        head = _lines(path, tail=False)
        tail = _lines(path, tail=True) if os.path.getsize(path) > CHUNK else head
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    cwd = next((r["cwd"] for r in head if r.get("cwd")), "")
    first = next((t for t in map(_prompt_text, head) if t), "")
    if not cwd or not first:
        return None  # nothing was ever asked: not worth resuming
    title = ""
    for kind, key in TITLE_KEYS:
        title = next((r.get(key, "") for r in reversed(tail + head) if r.get("type") == kind and r.get(key)), "")
        if title:
            break
    if not title:
        title = next((r.get("lastPrompt", "") for r in reversed(tail) if r.get("type") == "last-prompt"), "")
    sid = os.path.basename(path)[:-len(".jsonl")]
    return PastSession(sid, cwd, title or first, first, mtime)


def running() -> dict[str, int]:
    """Session id -> pid of the Claude Code processes running now (Claude Code's own ~/.claude/sessions)."""
    out = {}
    for fn in glob.glob(os.path.join(CLAUDE_DIR, "sessions", "*.json")):
        try:
            with open(fn) as f:
                d = json.load(f)
            os.kill(int(d["pid"]), 0)
            out[d["sessionId"]] = int(d["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return out


def list_past(limit: int = 150) -> list[PastSession]:
    """The most recently used sessions, newest first."""
    files = glob.glob(os.path.join(CLAUDE_DIR, "projects", "*", "*.jsonl"))
    files.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0, reverse=True)
    live = running()
    out = []
    for path in files:
        s = _read(path)
        if s:
            s.pid = live.get(s.id, 0)
            out.append(s)
            if len(out) >= limit:
                break
    return out
