"""Tells the app when the checkout it runs from is behind origin/master, and fast-forwards it on request.

Only a git checkout can update itself (the npm package has no .git, so it never reports an update). An update is
only offered when the checkout's HEAD is an ancestor of origin/master: a fast-forward never rewrites history and
never touches local commits, so a branch with its own commits gets no button."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BRANCH = "master"
TIMEOUT = 60  # seconds for one git call; a stalled network must not hang the app


@dataclass(frozen=True)
class Update:
    behind: int  # commits on origin/master that HEAD doesn't have
    local: str   # short sha of HEAD now
    remote: str  # short sha of origin/master


def _git(*args: str, root: str = ROOT) -> str:
    r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=TIMEOUT)
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or f"git {args[0]} failed")
    return r.stdout.strip()


def is_checkout(root: str = ROOT) -> bool:
    return os.path.exists(os.path.join(root, ".git"))


def check(root: str = ROOT) -> Update | None:
    """Fetch origin/master and say how far behind HEAD is. None when up to date, not a git checkout, not
    fast-forwardable, or disabled with AGENTCONTROL_NO_UPDATE_CHECK=1. Raises RuntimeError when git fails
    (offline, no origin, ...)."""
    if os.environ.get("AGENTCONTROL_NO_UPDATE_CHECK") or not is_checkout(root):
        return None
    _git("fetch", "--quiet", "origin", BRANCH, root=root)
    if _git("rev-list", "--count", f"HEAD..origin/{BRANCH}", root=root) == "0":
        return None
    try:
        _git("merge-base", "--is-ancestor", "HEAD", f"origin/{BRANCH}", root=root)
    except RuntimeError:  # diverged: local commits on top of or instead of master
        return None
    return Update(behind=int(_git("rev-list", "--count", f"HEAD..origin/{BRANCH}", root=root)),
                  local=_git("rev-parse", "--short", "HEAD", root=root),
                  remote=_git("rev-parse", "--short", f"origin/{BRANCH}", root=root))


def apply(root: str = ROOT) -> str:
    """Fetch and fast-forward the checkout to origin/master. Returns the new short sha. Raises RuntimeError if
    git refuses (local changes in the way, diverged history, network). The running app keeps the old code until
    it restarts."""
    _git("fetch", "--quiet", "origin", BRANCH, root=root)
    _git("merge", "--ff-only", f"origin/{BRANCH}", root=root)
    return _git("rev-parse", "--short", "HEAD", root=root)
