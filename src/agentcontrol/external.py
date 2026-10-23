"""Claude Code sessions that run outside agentControl: in a terminal, an editor or Claude Desktop. Claude Code
registers every running session in ~/.claude/sessions/<pid>.json (session id, cwd, name, busy / idle). The ones that
aren't in our tmux server show up read-only: their state, title and transcript, and a button that raises the window
they run in."""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass

from . import history, projects, sessions

REGISTRY = os.path.join(history.CLAUDE_DIR, "sessions")
CHUNK = 512 * 1024  # how much of a transcript's end a tile starts with
SHELLS = {"bash", "zsh", "fish", "sh", "dash", "ksh", "nu", "xonsh", "env", "sudo", "doas", "direnv", "nix", "script",
          "timeout", "node", "npx", "npm", "pnpm", "bun", "uv", "python", "python3", "login"}

_places: dict[tuple[int, str], tuple[str, str]] = {}  # (pid, procStart) -> (where, window address)
_titles: dict[str, tuple[float, str]] = {}  # transcript path -> (mtime, title)


@dataclass
class External:
    name: str  # <where>-<pid>, e.g. kitty-12345
    pid: int
    session_id: str
    cwd: str
    where: str  # what it runs in: kitty, Zed, Claude Desktop, tmux, …
    status: str  # Claude Code's own: busy, idle, …
    title: str = ""
    window: str = ""  # Hyprland address of its window, if known
    transcript: str = ""  # path of its .jsonl
    project: str = ""
    desk: sessions.Desk | None = None

    @property
    def path(self) -> str:
        return self.cwd

    @property
    def state(self) -> str:
        return "working" if self.status == "busy" else "idle"

    @property
    def label(self) -> str:
        return self.title or self.name


def list_external(tmux_sessions: list[sessions.Session] | None = None) -> list[External]:
    """Running Claude Code sessions that aren't agentControl agents, oldest first."""
    if tmux_sessions is None:
        tmux_sessions = sessions.list_sessions()
    panes = {s.pane_pid for s in tmux_sessions}
    out, clients, desks = [], None, None
    for fn in glob.glob(os.path.join(REGISTRY, "*.json")):
        try:
            with open(fn) as f:
                d = json.load(f)
            pid, sid = int(d["pid"]), str(d["sessionId"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if d.get("kind", "interactive") != "interactive" or not _alive(pid, str(d.get("procStart", ""))):
            continue
        chain = sessions._ancestors(pid)
        if pid in panes or panes.intersection(chain):
            continue  # one of ours
        key = (pid, str(d.get("procStart", "")))
        if key not in _places:
            if clients is None:
                clients = _clients()
            _places[key] = _place(d, chain, clients)
        where, window = _places[key]
        cwd = str(d.get("cwd") or "")
        transcript = _transcript(sid, cwd)
        if desks is None:
            desks = sessions.list_desks()
        name = d.get("name") if d.get("nameSource") == "user" else ""
        out.append(External(f"{projects.slug(where)}-{pid}", pid, sid, cwd, where, str(d.get("status", "")),
                            name or _title(transcript) or str(d.get("name") or ""), window, transcript,
                            desk=sessions._desk_for(pid, desks)))
    out.sort(key=lambda e: e.pid)
    for k in [k for k in _places if not any(e.pid == k[0] for e in out)]:
        del _places[k]
    return out


def get(name: str) -> External | None:
    return next((e for e in list_external() if e.name == name), None)


def _alive(pid: int, proc_start: str) -> bool:
    """The pid runs, and is still the process that registered (not a new one that got the same pid)."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            stat = f.read()
    except OSError:
        return False
    if not proc_start:
        return True
    try:
        return stat[stat.rindex(")") + 2:].split()[19] == proc_start
    except (ValueError, IndexError):
        return False


def _comm(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/comm") as f:
            return f.read().strip()
    except OSError:
        return ""


def _clients() -> dict[int, tuple[str, str]]:
    """Hyprland windows: pid -> (class, address). Empty outside Hyprland."""
    if not (os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl")):
        return {}
    try:
        r = subprocess.run(["hyprctl", "clients", "-j"], capture_output=True, text=True, timeout=3)
        return {int(c["pid"]): (str(c.get("class") or ""), str(c.get("address") or ""))
                for c in json.loads(r.stdout) if c.get("pid", -1) > 0}
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return {}


def _place(d: dict, chain: list[int], clients: dict[int, tuple[str, str]]) -> tuple[str, str]:
    """What the session runs in, and its window: the nearest ancestor that owns a window, else a tmux server,
    else the nearest ancestor that isn't a shell."""
    where = "Claude Desktop" if d.get("entrypoint") == "claude-desktop" else ""
    for pid in chain:
        if pid in clients:
            cls, address = clients[pid]
            return where or cls.rsplit(".", 1)[-1] or "window", address  # dev.zed.Zed -> Zed
        if _comm(pid).startswith("tmux"):
            return where or "tmux", ""
    if where:
        return where, ""
    return next((c for c in map(_comm, chain) if c and c not in SHELLS), "terminal"), ""


def _transcript(sid: str, cwd: str) -> str:
    path = os.path.join(history.CLAUDE_DIR, "projects", re.sub(r"[^A-Za-z0-9]", "-", cwd), f"{sid}.jsonl")
    if os.path.exists(path):
        return path
    return next(iter(glob.glob(os.path.join(history.CLAUDE_DIR, "projects", "*", f"{sid}.jsonl"))), "")


def _title(path: str) -> str:
    if not path:
        return ""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return ""
    if _titles.get(path, (None,))[0] != mtime:
        s = history._read(path)
        _titles[path] = (mtime, s.title if s else "")
    return _titles[path][1]


def raise_window(e: External) -> bool:
    if not e.window:
        return False
    r = subprocess.run(["hyprctl", "dispatch", "focuswindow", f"address:{e.window}"], capture_output=True,
                       text=True, timeout=3)
    return r.returncode == 0 and "ok" in r.stdout


# ---------------------------------------------------------------- transcript

class Tail:
    """Follows a transcript as it grows: the records of its last CHUNK bytes first, then only what's appended."""

    def __init__(self, path: str, chunk: int = CHUNK):
        self.path, self.chunk = path, chunk
        self.pos = -1
        self.rest = b""

    def read(self) -> list[dict]:
        try:
            with open(self.path, "rb") as f:
                size = os.fstat(f.fileno()).st_size
                if self.pos < 0 or size < self.pos:  # first read, or the file was replaced
                    self.pos = max(0, size - self.chunk)
                    self.rest = b""
                    skip = self.pos > 0  # starts mid-line
                else:
                    skip = False
                f.seek(self.pos)
                data = f.read(size - self.pos)
        except OSError:
            return []
        self.pos += len(data)
        lines = (self.rest + data).split(b"\n")
        self.rest = lines.pop()
        if skip and lines:
            lines.pop(0)
        out = []
        for line in lines:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
        return out


def _oneline(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _tool(block: dict) -> str:
    name = str(block.get("name") or "tool")
    if name.startswith("mcp__"):
        name = name[5:].replace("__", " · ")
    args = block.get("input") if isinstance(block.get("input"), dict) else {}
    arg = next((args[k] for k in ("command", "file_path", "path", "pattern", "url", "query", "description", "prompt")
                if isinstance(args.get(k), str) and args[k]), None)
    if arg is None:
        arg = next((v for v in args.values() if isinstance(v, str) and v), "")
    return f"{name}({_oneline(arg, 100)})" if arg else name


def _result(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, list):
        content = "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    first = next((ln for ln in str(content or "").splitlines() if ln.strip()), "")
    return ("error: " if block.get("is_error") else "") + _oneline(first, 140)


def render(records: list[dict]) -> list[tuple[str, str]]:
    """Transcript records as (kind, text) lines the way Claude Code shows them: kind is prompt (what the user
    typed), text (Claude's answer), tool (a tool call), result (the first line of its result) or note."""
    out = []
    for rec in records:
        if rec.get("type") == "system" and rec.get("subtype") == "compact_boundary":
            out.append(("note", "conversation compacted"))
            continue
        if rec.get("isSidechain") or rec.get("isCompactSummary") or rec.get("type") not in ("user", "assistant"):
            continue
        content = (rec.get("message") or {}).get("content")
        if rec["type"] == "user":
            prompt = history._prompt_text(rec)
            if prompt:
                out.append(("prompt", prompt))
            elif isinstance(content, list):
                out += [("result", _result(c)) for c in content
                        if isinstance(c, dict) and c.get("type") == "tool_result"]
            continue
        for c in content if isinstance(content, list) else [{"type": "text", "text": content or ""}]:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "text" and str(c.get("text") or "").strip():
                out.append(("text", str(c["text"]).strip()))
            elif c.get("type") == "tool_use":
                out.append(("tool", _tool(c)))
    return out


def as_text(lines: list[tuple[str, str]]) -> str:
    marks = {"prompt": "❯ ", "tool": "● ", "result": "  ⎿ ", "text": "", "note": "── "}
    return "\n".join(marks[k] + t for k, t in lines)


def peek(e: External, lines: int = 40) -> str:
    """The end of its transcript as text, like `ac peek` shows an agent's screen."""
    text = as_text(render(Tail(e.transcript).read())) if e.transcript else ""
    return "\n".join(text.splitlines()[-max(lines, 1):])
