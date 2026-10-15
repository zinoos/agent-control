---
name: agent-control
description: Start, message and check on other Claude Code agents that the user watches in agentControl (CLI `ac`). Use when the user asks to start/spawn/launch an agent, run tasks in parallel with separate agents, hand a task to "another agent", check what an agent is doing or whether it finished, send an agent a message or new instruction, stop or end an agent, or mentions agentControl, `ac`, agent tiles, or an agent's live desktop stream. Not for your own built-in subagents doing quick internal research.
user-invocable: true
argument-hint: "[start <task> | status | check <name> | tell <name> <message>]"
---

# agentControl: agents the user can watch and talk to

agentControl (`agentcontrol`) shows every agent as a tile: its Claude Code terminal, a live stream of its
own desktop while it uses one (hypr-computer agent desktops, if installed), and a message box. Its agents are Claude Code sessions in a dedicated tmux server (`tmux -L agentcontrol`),
managed with the `ac` CLI, which works from your shell too.

| Command | Does |
|---|---|
| `ac ls` | agents grouped by project: name, state (working/idle/starting), label, desktop, LIVE |
| `ac projects` | project presets (name → directory), e.g. `my-app ~/projects/my-app` |
| `ac new NAME -P PROJECT -p "instruction"` | start an agent in the project's directory |
| `ac new NAME -C DIR -p "instruction"` | start one in any directory (grouped by the project containing DIR) |
| `ac new … -- --model X` | anything after `--` goes to `claude` |
| `ac send NAME "text"` | type a message into its prompt and submit (queued by Claude Code even mid-task) |
| `ac peek NAME -n 80` | the end of its screen, as plain text |
| `ac wait NAME -t 900` | block until idle; prints `idle` / `ended` / `timeout` (exit 1 on timeout) |
| `ac stop NAME` | Esc (interrupt the current turn) |
| `ac kill NAME` | end the session |
| `ac project add NAME [DIR]` | new project preset (DIR defaults to ~/projects/NAME, created if missing) |
| `ac app` | open the agentControl window |

## Starting an agent

Only when the user asked for one: every agent is a full Claude Code session on their plan.

1. `ac projects` and `ac ls`: pick the matching project and a name that doesn't exist yet. Use a short name
   after the task (`fix-login`, `api-tests`), not `agent-3`.
2. Write a **self-contained** first instruction. The new agent shares none of your context, so include the
   goal, the relevant paths/files/branch, constraints the user gave you, what "done" looks like, and how to
   report back (e.g. "finish with a short summary of what changed and how you verified it").
   Quote carefully: pass it as one shell argument; for long text use `-p "$(cat <<'EOF' … EOF)"`.
3. `ac new NAME -P PROJECT -p "…"`, then tell the user the agent's name. They can watch it in agentControl,
   or with `ac attach NAME` in a terminal. If it will use a desktop, it gets its own on the next free place.

## Following up

- Progress: `ac ls` (state) and `ac peek NAME -n 60`. Don't poll in a tight loop: use `ac wait NAME -t SECONDS`
  (run it in the background for long tasks) and then `ac peek` to read the result.
- New instructions or corrections: `ac send NAME "…"`. Mid-task is fine. Claude Code may stop to ask
  permission or a question: `ac peek` shows it, and answering is just another `ac send`. Ask the user if the
  answer is theirs to give.
- Report back in your own words: what the agent did, where, and what's left. Don't paste its whole screen.
- Leave finished agents running unless the user says otherwise: they may want to read the transcript in
  agentControl. `ac kill` only on request or for an agent you started by mistake.

## Notes

- `$AGENTCONTROL_SESSION` is set when you yourself run inside agentControl: the user may be watching your
  tile and can message you through it.
- Sessions started in a plain terminal (`claude` in any terminal) are invisible to agentControl and `ac`.
- Agent desktops (hypr-computer MCP) are per agent: one agent's clicks never touch another's or the user's
  cursor. The user watches a desktop in the agent's tile, where it shows as LIVE
  for 45 s after each action, with the action in a ticker.
