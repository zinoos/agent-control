"""Claude Code sessions in a dedicated tmux server, and which agent desktop (hypr-computer) each one uses."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field

from . import projects, settings

SOCKET = os.environ.get("AGENTCONTROL_SOCKET", "agentcontrol")  # override only for testing
CONF = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tmux.conf")
RUN = os.path.join(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"), "hypr-computer")
ACTIVE_SECONDS = 45  # a desktop counts as "in use" this long after the agent's last action there

# Claude Code puts its state in the terminal title: a spinner while working, ✳ when idle.
SPINNER = set("◐◓◑◒⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏·✢✶✻✽")


def tmux(*args: str, input: str | None = None, check: bool = False) -> str:
    r = subprocess.run(["tmux", "-L", SOCKET, "-f", CONF, *args], capture_output=True, text=True,
                       input=input, timeout=10)
    if check and r.returncode:
        raise RuntimeError(r.stderr.strip() or f"tmux {args[0]} failed")
    return r.stdout


@dataclass
class Desk:
    name: str
    wl_socket: str
    width: int
    height: int
    place: int
    accent: str
    owner_pid: int
    last_action: float = 0.0
    last_text: str = ""

    @property
    def active(self) -> bool:
        return time.time() - self.last_action < ACTIVE_SECONDS


@dataclass
class Session:
    name: str
    pane_pid: int
    title: str
    cwd: str
    created: int
    activity: int
    path: str = ""  # directory the session started in
    project: str = ""  # project it was started for (tmux option @ac_project)
    desk: Desk | None = None
    extra: dict = field(default_factory=dict)

    @property
    def state(self) -> str:
        t = self.title.strip()
        if not t:
            return "starting"
        if t[0] in SPINNER:
            return "working"
        if t[0] == "✳":
            return "idle"
        return "idle"

    @property
    def label(self) -> str:
        t = self.title.strip()
        return t[1:].strip() if t and (t[0] in SPINNER or t[0] == "✳") else (t or self.name)


def list_sessions() -> list[Session]:
    out = tmux("list-sessions", "-F",
               "#{session_name}\t#{pane_pid}\t#{pane_title}\t#{pane_current_path}\t#{session_created}\t#{session_activity}"
               "\t#{session_path}\t#{@ac_project}")
    sessions = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 6:
            continue
        name, pid, title, cwd, created, activity = parts[:6]
        path, project = (parts[6:8] + ["", ""])[:2]
        sessions.append(Session(name, int(pid or 0), title, cwd, int(created or 0), int(activity or 0),
                                path, project))
    desks = list_desks()
    for s in sessions:
        s.desk = _desk_for(s.pane_pid, desks)
    return sessions


def list_desks() -> list[Desk]:
    out = []
    if not os.path.isdir(RUN):
        return out
    for fn in os.listdir(RUN):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(RUN, fn)) as f:
                d = json.load(f)
            if not _alive(d["pid"]):
                continue
            desk = Desk(d["name"], d["wl_socket"], d["width"], d["height"], d.get("place", 0),
                        d.get("accent", "ff8c1a"), d.get("owner_pid", 0))
            act = os.path.join(RUN, f"{d['name']}.activity")
            if os.path.exists(act):
                with open(act) as f:
                    a = json.load(f)
                desk.last_action, desk.last_text = a.get("t", 0.0), a.get("text", "")
            out.append(desk)
        except Exception:
            continue
    return out


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _ancestors(pid: int, limit: int = 12) -> list[int]:
    chain = []
    for _ in range(limit):
        try:
            with open(f"/proc/{pid}/stat") as f:
                stat = f.read()
            pid = int(stat[stat.rindex(")") + 2:].split()[1])
        except (OSError, ValueError, IndexError):
            break
        if pid <= 1:
            break
        chain.append(pid)
    return chain


def _desk_for(pane_pid: int, desks: list[Desk]) -> Desk | None:
    """The agent desktop owned by an MCP server running under this session's process tree."""
    for d in desks:
        if d.owner_pid and (d.owner_pid == pane_pid or pane_pid in _ancestors(d.owner_pid)):
            return d
    return None


def new_session(name: str | None = None, cwd: str | None = None, prompt: str | None = None,
                claude_args: list[str] | None = None, project: str | None = None) -> str:
    """Start Claude Code in a new tmux session. With a project and no cwd it starts in the project's directory."""
    existing = {s.name for s in list_sessions()}
    if project:
        proj = projects.get(project)
        if not proj:
            raise RuntimeError(f"no project called {project!r} (ac projects)")
        cwd = cwd or proj.path
    if not name:
        base = projects.slug(project) if project else "agent"
        name = next(f"{base}-{i}" for i in range(1, 1000) if f"{base}-{i}" not in existing)
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-") or "agent"
    if name in existing:
        raise RuntimeError(f"a session called {name!r} already exists")
    cwd = os.path.expanduser(cwd or "~")
    if not os.path.isdir(cwd):
        raise RuntimeError(f"no such directory: {cwd}")  # tmux would silently start it in ~ instead
    cmd = ["claude", *shlex.split(settings.get("claude_args")), *(claude_args or [])]
    if prompt:
        cmd.append(prompt)
    tmux("new-session", "-d", "-s", name, "-c", cwd, "-x", "160", "-y", "48",
         # drop to a shell when Claude exits (double Ctrl+C) instead of closing the session
         "-e", f"AGENTCONTROL_SESSION={name}", shlex.join(cmd) + "; exec bash", check=True)
    if project:
        set_project(name, project)
    return name


def set_project(name: str, project: str | None):
    if project:
        tmux("set-option", "-t", f"{name}:", "@ac_project", project, check=True)
    else:
        tmux("set-option", "-u", "-t", f"{name}:", "@ac_project")


def send(name: str, text: str, submit: bool = True):
    """Type a message into the session's Claude Code prompt (bracketed paste keeps newlines intact).
    While Claude is working, Claude Code queues it and reads it mid-turn."""
    buf = f"ac-{name}-{int(time.time() * 1000)}"
    tmux("set-buffer", "-b", buf, "--", text, check=True)
    tmux("paste-buffer", "-p", "-d", "-b", buf, "-t", f"{name}:", check=True)
    if submit:
        time.sleep(0.05)
        tmux("send-keys", "-t", f"{name}:", "Enter", check=True)


def peek(name: str, lines: int = 40) -> str:
    """The last `lines` lines of the agent's screen (its Claude Code transcript as shown), plain text."""
    out = tmux("capture-pane", "-p", "-J", "-S", f"-{max(lines, 1) + 200}", "-t", f"{name}:", check=True)
    rows = out.rstrip("\n").splitlines()
    while rows and not rows[-1].strip():
        rows.pop()
    return "\n".join(rows[-lines:])


def wait_idle(name: str, timeout: float = 600, settle: float = 3.0) -> str:
    """Block until the agent has been idle (not working) for `settle` seconds. Returns the final state."""
    t0 = time.time()
    idle_since = None
    while time.time() - t0 < timeout:
        s = next((x for x in list_sessions() if x.name == name), None)
        if s is None:
            return "ended"
        if s.state == "working":
            idle_since = None
        else:
            idle_since = idle_since or time.time()
            if time.time() - idle_since >= settle:
                return s.state
        time.sleep(1)
    return "timeout"


def interrupt(name: str):
    tmux("send-keys", "-t", f"{name}:", "Escape", check=True)


def kill(name: str):
    tmux("kill-session", "-t", f"{name}:", check=True)


def attach_argv(name: str) -> list[str]:
    return ["tmux", "-L", SOCKET, "-f", CONF, "attach-session", "-t", f"{name}:"]


def ensure_server():
    """Start the tmux server with our config (no status bar, mouse scrolling, big history)."""
    tmux("start-server")
    for opt in (("status", "off"), ("mouse", "on"), ("history-limit", "50000"), ("window-size", "latest"),
                ("set-titles", "on"), ("allow-passthrough", "on"), ("escape-time", "0")):
        tmux("set-option", "-g", *opt)
