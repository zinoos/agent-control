"""Settings from the Settings dialog (~/.config/agentcontrol/settings.json), shared by the app and `ac`.
Environment variables only give the defaults."""

from __future__ import annotations

import json
import os
import re

PATH = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "agentcontrol",
                    "settings.json")

DEFAULTS = {
    "opacity": 1.0,
    "font": os.environ.get("AGENTCONTROL_FONT", "monospace 10"),
    "scheme": "agentControl",
    "accent": "system",
    "gap": 8,
    "radius": 12,
    "projects_dir": os.environ.get("AGENTCONTROL_PROJECTS_DIR", "~/projects"),
    "claude_args": "",
    "notify": True,
    "confirm_kill": True,
    "view": "auto",
    "max_fps": 30,
    "tokens_at_start": "last",  # last | open | closed
    "tokentracker": os.environ.get("AGENTCONTROL_TOKENTRACKER", ""),
}

# Allowed values; anything else in settings.json falls back to the default.
RANGES = {"opacity": (0.3, 1.0), "gap": (0, 32), "radius": (0, 24), "max_fps": (5, 60)}
ACCENTS = ["system", "blue", "teal", "green", "yellow", "orange", "red", "pink", "purple", "slate"]
CHOICES = {"accent": ACCENTS, "view": ["auto", "stream", "terminal"], "tokens_at_start": ["last", "open", "closed"]}

# name -> (foreground, background, 16 palette colours)
SCHEMES = {
    "agentControl": ("#e9ecef", "#14161a",
                     ["#1b1d22", "#ff6b6b", "#69db7c", "#ffd43b", "#74c0fc", "#da77f2", "#66d9e8", "#ced4da",
                      "#495057", "#ff8787", "#8ce99a", "#ffe066", "#a5d8ff", "#e599f7", "#99e9f2", "#f8f9fa"]),
    "Catppuccin Mocha": ("#cdd6f4", "#1e1e2e",
                         ["#45475a", "#f38ba8", "#a6e3a1", "#f9e2af", "#89b4fa", "#f5c2e7", "#94e2d5", "#bac2de",
                          "#585b70", "#f38ba8", "#a6e3a1", "#f9e2af", "#89b4fa", "#f5c2e7", "#94e2d5", "#a6adc8"]),
    "Tokyo Night": ("#c0caf5", "#1a1b26",
                    ["#15161e", "#f7768e", "#9ece6a", "#e0af68", "#7aa2f7", "#bb9af7", "#7dcfff", "#a9b1d6",
                     "#414868", "#f7768e", "#9ece6a", "#e0af68", "#7aa2f7", "#bb9af7", "#7dcfff", "#c0caf5"]),
    "Gruvbox Dark": ("#ebdbb2", "#282828",
                     ["#282828", "#cc241d", "#98971a", "#d79921", "#458588", "#b16286", "#689d6a", "#a89984",
                      "#928374", "#fb4934", "#b8bb26", "#fabd2f", "#83a598", "#d3869b", "#8ec07c", "#ebdbb2"]),
    "kitty": None,  # read from ~/.config/kitty/kitty.conf when used
}


def load() -> dict:
    try:
        with open(PATH) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = {}
    out = dict(DEFAULTS)
    for k, v in (raw.items() if isinstance(raw, dict) else ()):
        if k in out and _valid(k, v):
            out[k] = min(max(v, RANGES[k][0]), RANGES[k][1]) if k in RANGES else v
    return out


def _valid(key, v) -> bool:
    d = DEFAULTS[key]
    if isinstance(d, bool) or isinstance(v, bool):
        return isinstance(d, bool) and isinstance(v, bool)
    if isinstance(d, (int, float)):
        return isinstance(v, (int, float)) and v == v  # v == v rules out NaN
    return isinstance(v, str) and (key not in CHOICES or v in CHOICES[key])


def save(s: dict):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump({k: v for k, v in s.items() if k in DEFAULTS}, f, indent=2)
        f.write("\n")
    os.replace(tmp, PATH)


def get(key: str):
    return load()[key]


def scheme(name: str):
    if name == "kitty":
        return kitty_scheme() or SCHEMES["agentControl"]
    return SCHEMES.get(name) or SCHEMES["agentControl"]


def kitty_scheme(path: str = "~/.config/kitty/kitty.conf"):
    """foreground, background and color0-15 from kitty's config, following `include` like kitty does."""
    fg, bg, pal = SCHEMES["agentControl"]
    vals = {"foreground": fg, "background": bg} | {f"color{i}": c for i, c in enumerate(pal)}
    found = False

    def read(p, depth=0):
        nonlocal found
        try:
            with open(os.path.expanduser(p)) as f:
                lines = f.readlines()
        except OSError:
            return
        for line in lines:
            parts = line.split()
            if len(parts) >= 2 and parts[0] == "include" and depth < 5:
                inc = os.path.expanduser(parts[1])
                read(inc if os.path.isabs(inc) else os.path.join(os.path.dirname(os.path.expanduser(p)), inc), depth + 1)
            elif len(parts) >= 2 and parts[0] in vals and re.fullmatch(r"#[0-9a-fA-F]{6}", parts[1]):
                vals[parts[0]] = parts[1]
                found = True

    read(path)
    return (vals["foreground"], vals["background"], [vals[f"color{i}"] for i in range(16)]) if found else None

