// npm can't install the system pieces agentControl needs (GTK 4 bindings, tmux, the Claude Code CLI), so this
// only reports what's missing, in the same words as install.sh. It never fails the install.
"use strict";

const { spawnSync } = require("node:child_process");

const has = (cmd) => spawnSync("sh", ["-c", `command -v ${cmd}`], { stdio: "ignore" }).status === 0;
const python = process.env.AGENTCONTROL_PYTHON || "/usr/bin/python3";
const gtkOk = spawnSync(python, ["-c",
  'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); gi.require_version("Vte", "3.91")\n' +
  "from gi.repository import Adw, Gtk, Vte"], { stdio: "ignore" }).status === 0;

const missing = [];
if (!has("claude")) missing.push("claude (Claude Code CLI)");
if (!has("tmux")) missing.push("tmux");
if (!gtkOk) missing.push("PyGObject with GTK 4, libadwaita and VTE 3.91");

if (missing.length > 0) {
  console.log("agentcontrol installed. Still missing (see README → Requirements):");
  for (const m of missing) console.log(`  - ${m}`);
  console.log("Then run: agentcontrol   (or: ac new my-agent -p \"hello\")");
} else {
  console.log("agentcontrol installed. Run: agentcontrol   (or: ac new my-agent -p \"hello\")");
}
