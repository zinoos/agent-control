#!/usr/bin/env bash
# Install agentControl for the current user: `ac` and `agentcontrol` on PATH, an app-launcher entry, and
# (with --skill) the agent-control skill for Claude Code. Everything is a symlink back into this checkout,
# so `git pull` updates it. Undo: ./install.sh --uninstall
set -euo pipefail
here="$(cd -- "$(dirname -- "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
bin="${AGENTCONTROL_BIN_DIR:-$HOME/.local/bin}"
apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
skills="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills"

link() {  # link SRC DEST: never replaces a real file
  if [ -e "$2" ] && [ ! -L "$2" ]; then echo "skip: $2 exists and isn't a symlink" >&2; return; fi
  mkdir -p "$(dirname "$2")"
  ln -sfn "$1" "$2"
  echo "linked $2 -> $1"
}
unlink_ours() {
  if [ -L "$1" ] && [[ "$(readlink -f "$1")" == "$here"* ]]; then rm "$1" && echo "removed $1"; fi
}

case "${1:-}" in
  --uninstall)
    for f in "$bin/ac" "$bin/agentcontrol" "$apps/agentcontrol.desktop" "$skills/agent-control"; do unlink_ours "$f"; done
    exit 0 ;;
  ""|--skill) ;;
  *) echo "usage: $0 [--skill | --uninstall]" >&2; exit 2 ;;
esac

missing=()
command -v tmux >/dev/null || missing+=("tmux")
command -v claude >/dev/null || missing+=("claude (Claude Code CLI)")
"${AGENTCONTROL_PYTHON:-/usr/bin/python3}" - <<'EOF' 2>/dev/null || missing+=("PyGObject with GTK 4, libadwaita and VTE 3.91")
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); gi.require_version("Vte", "3.91")
from gi.repository import Adw, Gtk, Vte  # noqa
EOF

optional=()
"${AGENTCONTROL_PYTHON:-/usr/bin/python3}" -c 'import gi; gi.require_version("WebKit", "6.0")' 2>/dev/null \
  || optional+=("WebKitGTK 6.0 bindings (token panel)")
command -v tokentracker >/dev/null || command -v npx >/dev/null || optional+=("tokentracker or Node's npx (token panel)")

link "$here/bin/ac" "$bin/ac"
link "$here/bin/agentcontrol" "$bin/agentcontrol"
link "$here/agentcontrol.desktop" "$apps/agentcontrol.desktop"
[ "${1:-}" = --skill ] && link "$here/skill" "$skills/agent-control"
command -v update-desktop-database >/dev/null && update-desktop-database "$apps" 2>/dev/null || true

case ":$PATH:" in *":$bin:"*) ;; *) echo "note: $bin is not on your PATH" >&2 ;; esac
if [ ${#missing[@]} -gt 0 ]; then
  echo >&2
  echo "Still missing (see README → Requirements):" >&2
  printf '  - %s\n' "${missing[@]}" >&2
fi
if [ ${#optional[@]} -gt 0 ]; then
  echo "Optional, not found:" >&2
  printf '  - %s\n' "${optional[@]}" >&2
fi
echo "Done. Run: agentcontrol   (or: ac new my-agent -p \"hello\")"
