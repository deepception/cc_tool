# cc_tool

One-command setup for Claude Code projects: Ruflo MCP tools + Superpowers skills.

## What this configures

| Tool | What it does | Update strategy |
|------|-------------|-----------------|
| **Ruflo** (`@claude-flow/cli`) | 259 MCP tools for multi-agent work, memory, swarms | `npx -y` auto-fetches latest on every session start — nothing to do |
| **Superpowers** (`obra/superpowers`) | Methodology skills: TDD, brainstorming, debugging, verification | `cc-update` pulls from GitHub |
| **Hook: prompt-linter** | Warns when prompts are >50 words and ambiguous | Updated via `cc-setup` after `cc-update` |
| **Hook: websearch-year** | Appends current year to searches lacking temporal context | Updated via `cc-setup` after `cc-update` |

**Design principle:** Ruflo is a toolbox you call explicitly. Superpowers is the methodology layer that runs before you write code. No Ruflo CLAUDE.md, no behavioral autopilot, no cross-triggering.

---

## Setup (3 commands, once per machine)

```bash
# 1. Register commands with your shell
cd /home/lukasz/Files/Projects/cc_tool
./install.sh
source ~/.zshrc   # or ~/.bashrc

# 2. Install Superpowers globally (once, active in all projects)
cc-install-superpowers

# 3. Set up any project
cc-setup /path/to/your/project
```

---

## Per-project setup (1 command)

```bash
cc-setup /path/to/your/project
```

What it does:
- Removes Ruflo-generated scaffolding (`.claude-flow/`, `.claude/helpers/`, `.claude/skills/`, `.claude/commands/`, `.claude/agents/`) — with confirmation
- Creates/merges `.mcp.json` with the Ruflo MCP server config
- Installs hook scripts into `.claude/hooks/`
- Creates `.claude/settings.json` (new projects) or updates only the `hooks` section (existing projects, preserving your permissions)

Re-running `cc-setup` is safe and idempotent — permissions are never overwritten.

---

## Update (1 command)

```bash
cc-update
```

- Pulls latest `cc_tool` templates from GitHub
- Pulls latest Superpowers plugin from GitHub
- Ruflo: nothing to do — always latest via `npx -y`

After updating, refresh hook scripts in any project you care about:
```bash
cc-setup /path/to/your/project   # only updates hooks, never touches permissions
```

---

## How to work with the setup

The mental model: **Superpowers decides how to approach the work. Ruflo tools are heavy machinery you call deliberately.**

### Typical feature workflow

```
1. Design:     tell Claude → "use superpowers:brainstorming"
               (clarifying questions → spec doc → your approval required before any code)

2. Plan:       tell Claude → "use superpowers:writing-plans"
               (test-first tasks in docs/superpowers/plans/, each 2-5 min, exact file paths)

3. Execute:    tell Claude → "use superpowers:executing-plans"
               (fresh subagent per task, two review gates each)

   OR for heavy parallelism (4+ independent tasks):
               tell Claude → "use mcp__claude-flow__swarm_init to run tasks A/B/C in parallel"

4. Verify:     tell Claude → "use superpowers:verification-before-completion"
               (runs the actual tests, reads output — no hallucinated success)
```

### When to use which Ruflo tools

| Use case | Tool |
|----------|------|
| Parallel independent tasks (4+) | `mcp__claude-flow__swarm_init` + `mcp__claude-flow__agent_spawn` |
| Cross-session memory | `mcp__claude-flow__memory_store` / `memory_retrieve` |
| Track task progress | `mcp__claude-flow__task_create` / `task_complete` |
| Code review in isolation | `mcp__claude-flow__agent_spawn` (or use `superpowers:requesting-code-review`) |
| Coordinate multiple repos | `mcp__claude-flow__coordination_orchestrate` |

### Debugging

```
tell Claude → "use superpowers:systematic-debugging"
```
Hard rule the skill enforces: no fix without root-cause investigation first. If 3 fixes fail, stop patching and question the architecture.

### Branch completion

```
tell Claude → "use superpowers:finishing-a-development-branch"
```
Presents four options (merge locally, push+PR, keep, discard) and requires typing "discard" to confirm that path.

---

## Do Superpowers and Ruflo cross-trigger each other?

**No.** Here is why this is guaranteed when using manual invocation:

**Superpowers → Ruflo:** Superpowers SKILL.md files contain zero `mcp__claude-flow__*` calls. When a skill runs, it uses Claude Code's native `Task` tool for subagents and the `Skill` tool for nested skills. It has no knowledge of or path to Ruflo.

**Ruflo → Superpowers:** Ruflo spawns agents as headless Claude processes (`claude -p "..."`). Superpowers' `SessionStart` hook only fires on *interactive* Claude Code sessions — the `-p` flag bypasses it entirely. Ruflo's agents run in complete isolation from Superpowers.

**The condition that makes this safe:** This setup does NOT include Ruflo's `CLAUDE.md` (the 38KB behavioral file that ships with `npx ruflo init`). Without that file, Ruflo is inert until you explicitly call one of its MCP tools. Superpowers owns the workflow layer; Ruflo is a toolbox. Adding Ruflo's `CLAUDE.md` would break this — don't do it.

---

## Directory structure

```
cc_tool/
  install.sh                     one-time PATH setup
  bin/
    cc-setup                     configure a project
    cc-update                    update tools
    cc-install-superpowers       install Superpowers globally
  templates/
    mcp.json                     Ruflo MCP server config
    settings.json                full settings for new projects
    hooks-config.json            hooks-only (merged into existing settings)
    hooks/
      prompt-linter.sh           warns on long ambiguous prompts
      websearch-year.py          appends year to temporal searches
```
