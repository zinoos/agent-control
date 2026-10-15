"""Projects: named working directories that agents start in. The sidebar groups agents by project."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

CONFIG = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "agentcontrol",
                      "projects.json")
DEFAULTS: list[dict] = []  # first run starts with no presets; add them in the app or with `ac project add`
# Where new projects go by default (`ac project add NAME` without DIR, and the new-project dialog).
ROOT = os.environ.get("AGENTCONTROL_PROJECTS_DIR", "~/projects").rstrip("/")
OTHER = "Other"  # group for agents outside every project


@dataclass
class Project:
    name: str
    path: str  # absolute

    @property
    def short_path(self) -> str:
        home = os.path.expanduser("~")
        return "~" + self.path[len(home):] if self.path == home or self.path.startswith(home + "/") else self.path


def _abs(path: str) -> str:
    return os.path.normpath(os.path.abspath(os.path.expanduser(path.strip() or "~")))


def slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-").lower() or "project"


def default_dir(name: str) -> str:
    """The directory a new project called name gets unless one is given: ROOT/<slug>."""
    return f"{ROOT}/{slug(name)}"


def load() -> list[Project]:
    try:
        with open(CONFIG) as f:
            raw = json.load(f).get("projects", [])
    except FileNotFoundError:
        raw = DEFAULTS
        _write(raw)
    except (OSError, ValueError):
        raw = []
    return [Project(p["name"], _abs(p["path"])) for p in raw if p.get("name") and p.get("path")]


def _write(raw: list[dict]):
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    tmp = CONFIG + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"projects": raw}, f, indent=2)
        f.write("\n")
    os.replace(tmp, CONFIG)


def save(projects: list[Project]):
    _write([{"name": p.name, "path": p.short_path} for p in projects])


def get(name: str, projects: list[Project] | None = None) -> Project | None:
    return next((p for p in (projects if projects is not None else load()) if p.name == name), None)


def upsert(name: str, path: str, old_name: str | None = None, create: bool = True) -> Project:
    """Add a project, or change the one called old_name. Creates the directory if it doesn't exist yet."""
    name = name.strip()
    if not name:
        raise ValueError("a project needs a name")
    if name == OTHER:
        raise ValueError(f"{OTHER!r} is reserved")
    projects = load()
    if any(p.name == name for p in projects if p.name != old_name):
        raise ValueError(f"there is already a project called {name!r}")
    path = _abs(path)
    if os.path.exists(path) and not os.path.isdir(path):
        raise ValueError(f"{path} is a file, not a directory")
    if create:
        os.makedirs(path, exist_ok=True)
    proj = Project(name, path)
    i = next((i for i, p in enumerate(projects) if p.name == old_name), None) if old_name else None
    if i is None:
        projects.append(proj)
    else:
        projects[i] = proj
    save(projects)
    return proj


def remove(name: str):
    """Forget a project (its directory and agents stay)."""
    save([p for p in load() if p.name != name])


def match(path: str, projects: list[Project]) -> Project | None:
    """The project whose directory contains path (the most specific one)."""
    if not path:
        return None
    path = os.path.normpath(path)
    best = None
    for p in projects:
        if (path == p.path or path.startswith(p.path.rstrip("/") + "/")) and (not best or len(p.path) > len(best.path)):
            best = p
    return best


def group_of(session, projects: list[Project]) -> str:
    """Project name for a session: the one it was started in, else the one its start directory is in."""
    if session.project and any(p.name == session.project for p in projects):
        return session.project
    p = match(session.path, projects) or match(session.cwd, projects)
    return p.name if p else OTHER
