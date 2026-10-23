"""Test setup: every test run gets its own HOME, config and state dirs, Claude dir and tmux server, and a fake
`claude` on PATH, so the tests never touch the real agents or settings. This has to happen before agentcontrol
is imported: its modules read these paths at import time. So every test module starts with `import tests`: unittest
doesn't run this file by itself when the tests aren't imported as a package (`discover -s tests` without `-t .`),
and without it the tests would delete the real projects.json and settings.json."""

import atexit
import os
import shutil
import subprocess
import sys
import tempfile

if any(m == "agentcontrol" or m.startswith("agentcontrol.") for m in sys.modules):
    raise RuntimeError("agentcontrol was imported before the test sandbox, so its paths are the real config: "
                       "start every test module with `import tests`")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="agentcontrol-test-")
HOME = os.path.join(TMP, "home")
BIN = os.path.join(TMP, "bin")
ARGS = os.path.join(TMP, "args")  # the fake claude writes its argv here, one file per session
for d in (HOME, BIN, ARGS):
    os.makedirs(d)

for k in ("TMUX", "TMUX_PANE", "AGENTCONTROL_FONT", "AGENTCONTROL_PROJECTS_DIR", "AGENTCONTROL_TOKENTRACKER",
          "AGENTCONTROL_SESSION"):
    os.environ.pop(k, None)
os.environ.update({
    "HOME": HOME,
    "XDG_CONFIG_HOME": os.path.join(HOME, ".config"),
    "XDG_STATE_HOME": os.path.join(HOME, ".local", "state"),
    "CLAUDE_CONFIG_DIR": os.path.join(HOME, ".claude"),
    "TMUX_TMPDIR": TMP,
    "AGENTCONTROL_SOCKET": "agentcontrol-test",
    "AC_TEST_ARGS": ARGS,
    "PATH": BIN + os.pathsep + os.environ.get("PATH", ""),
})

# Fake Claude Code: records its arguments, sets the terminal title like Claude does when idle, echoes input.
with open(os.path.join(BIN, "claude"), "w") as f:
    f.write('#!/bin/sh\n'
            'for a in "$@"; do printf "%s\\n" "$a"; done > "$AC_TEST_ARGS/$AGENTCONTROL_SESSION"\n'
            'printf "\\033]2;\\342\\234\\263 fake claude\\007"\n'
            'echo READY\n'
            'exec cat\n')
os.chmod(os.path.join(BIN, "claude"), 0o755)

sys.path.insert(0, os.path.join(ROOT, "src"))


@atexit.register
def _cleanup():
    if shutil.which("tmux"):
        subprocess.run(["tmux", "-L", os.environ["AGENTCONTROL_SOCKET"], "kill-server"], capture_output=True)
    shutil.rmtree(TMP, ignore_errors=True)
