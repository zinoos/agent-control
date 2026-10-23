"""ac: manage agentControl's Claude Code sessions from any terminal.

  ac new [NAME] [-P PROJECT] [-C DIR] [-p PROMPT] [-- CLAUDE ARGS...]
                                                         start an agent (Claude Code in tmux); with -P it
                                                         starts in the project's directory
  ac ls                                                  list agents by project (state, desktop, live), and
                                                         the Claude Code sessions running elsewhere
  ac projects                                            list projects
  ac project add NAME [DIR]                              add a project (DIR default ~/projects/NAME, or the
                                                         folder from Settings; created if missing)
  ac project rm NAME                                     forget a project (files and agents stay)
  ac attach NAME                                         attach this terminal to an agent
  ac send NAME TEXT...                                   send a message (even mid-task)
  ac peek NAME [-n LINES]                                print the end of the agent's screen (default 40), or
                                                         of the transcript of a session running elsewhere
  ac wait NAME [-t SECONDS]                              block until the agent is idle (default 600 s);
                                                         prints idle / ended / timeout
  ac stop NAME                                           interrupt (Esc)
  ac kill NAME                                           end the session
  ac app                                                 open agentControl

Sessions running elsewhere (a terminal, an editor, Claude Desktop) are read-only here: ac ls and ac peek.
"""

import argparse
import os
import subprocess
import sys

from . import external, projects, sessions


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd in ("attach", "send", "wait", "stop", "kill") and rest and (e := _outside(rest[0])):
        raise RuntimeError(f"{e.name} runs in {e.where}, outside agentControl: ac ls and ac peek see it, but "
                           "it can't be driven from here")
    if cmd == "new":
        claude_args = []
        if "--" in rest:
            i = rest.index("--")
            rest, claude_args = rest[:i], rest[i + 1:]
        p = argparse.ArgumentParser(prog="ac new")
        p.add_argument("name", nargs="?")
        p.add_argument("-P", "--project")
        p.add_argument("-C", "--cwd")
        p.add_argument("-p", "--prompt")
        a = p.parse_args(rest)
        cwd = a.cwd or (None if a.project else os.getcwd())
        name = sessions.new_session(a.name, cwd, a.prompt, claude_args, project=a.project)
        print(f"started {name}  (watch: agentControl, or: ac attach {name})")
    elif cmd == "ls":
        ss = sessions.list_sessions()
        ss += external.list_external(ss)
        if not ss:
            print("no agents")
        projs = projects.load()
        groups = {p.name: [] for p in projs} | {projects.OTHER: []}
        for s in ss:
            groups[projects.group_of(s, projs)].append(s)
        for g, members in groups.items():
            if not members:
                continue
            p = projects.get(g, projs)
            print(f"{g}" + (f"  ({p.short_path})" if p else ""))
            for s in members:
                desk = ""
                if s.desk:
                    desk = f"  desktop {s.desk.name}" + (" LIVE" if s.desk.active else "")
                if isinstance(s, external.External):
                    desk += f"  in {s.where}, outside agentControl"
                print(f"  {s.name:16} {s.state:9} {s.label[:50]:50}{desk}")
    elif cmd == "projects":
        for p in projects.load():
            print(f"{p.name:20} {p.short_path}")
    elif cmd == "project" and rest[:1] == ["add"] and len(rest) in (2, 3):
        p = projects.upsert(rest[1], rest[2] if len(rest) == 3 else projects.default_dir(rest[1]))
        print(f"project {p.name}: {p.short_path}")
    elif cmd == "project" and rest[:1] == ["rm"] and len(rest) == 2:
        if not projects.get(rest[1]):
            print(f"no project called {rest[1]!r}", file=sys.stderr)
            return 1
        projects.remove(rest[1])
    elif cmd == "attach":
        os.execvp("tmux", sessions.attach_argv(rest[0]))
    elif cmd == "send":
        sessions.send(rest[0], " ".join(rest[1:]))
    elif cmd == "peek":
        p = argparse.ArgumentParser(prog="ac peek")
        p.add_argument("name")
        p.add_argument("-n", "--lines", type=int, default=40)
        a = p.parse_args(rest)
        e = _outside(a.name)
        print(external.peek(e, a.lines) if e else sessions.peek(a.name, a.lines))
    elif cmd == "wait":
        p = argparse.ArgumentParser(prog="ac wait")
        p.add_argument("name")
        p.add_argument("-t", "--timeout", type=float, default=600)
        a = p.parse_args(rest)
        state = sessions.wait_idle(a.name, a.timeout)
        print(state)
        return 0 if state in ("idle", "ended") else 1
    elif cmd == "stop":
        sessions.interrupt(rest[0])
    elif cmd == "kill":
        sessions.kill(rest[0])
    elif cmd == "app":
        subprocess.Popen([sys.executable, "-m", "agentcontrol"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        print(f"unknown command {cmd!r}\n\n{__doc__.strip()}", file=sys.stderr)
        return 2
    return 0


def _outside(name: str) -> external.External | None:
    """The session called name if it runs outside agentControl, not as one of our agents."""
    if any(s.name == name for s in sessions.list_sessions()):
        return None
    return external.get(name)


def run():
    try:
        return main()
    except (RuntimeError, ValueError) as ex:
        print(f"ac: {ex}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(run())
