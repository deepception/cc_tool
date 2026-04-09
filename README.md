# cc_tool

One-command setup for Claude Code projects: Ruflo MCP tools + basic-memory + Superpowers skills.

## What this configures

| Tool | What it does | Update strategy |
|------|-------------|-----------------|
| **Superpowers** (`obra/superpowers`) | Methodology skills: TDD, brainstorming, debugging, verification | `cc-update` pulls from GitHub |
| **basic-memory** (`basicmachines-co/basic-memory`) | Persistent knowledge graph: project decisions, cross-session memory | `uvx` auto-downloads on first use — nothing to do |
| **Ruflo** (`@claude-flow/cli`) | MCP tools for swarm coordination, multi-repo orchestration | `npx -y` auto-fetches latest on every session start — nothing to do |
| **Hooks** (prompt-linter, websearch-year) | Prompt quality warning + temporal context for searches | Local scripts — edit templates in `cc_tool/`, re-run `cc-setup` |

**Design principle:** Superpowers is the methodology layer — most skills trigger automatically via CLAUDE.md rules. Ruflo MCP tools are available in every session and Claude may use them when needed (swarm parallelism, multi-repo). basic-memory is the persistent knowledge layer that survives across sessions. No Ruflo CLAUDE.md, no behavioral autopilot.

**Prerequisites:** `node`/`npx` (for Ruflo), `uv`/`uvx` (for basic-memory). Install uv: `curl -LsSf https://astral.sh/uv/install.sh | sh`

---

## Setup (2 commands, once per machine)

```bash
# 1. Install everything (PATH + Superpowers + tries claude plugin add)
cd /path/to/cc_tool
./install.sh
source ~/.zshrc   # or ~/.bashrc

# 2. Set up any project
cc-setup /path/to/your/project
```

`install.sh` handles PATH registration, adds the `obra/superpowers` marketplace, and installs the Superpowers plugin in one run.

---

## Per-project setup (1 command)

```bash
cc-setup /path/to/your/project
```

What it does:
- Removes Ruflo-generated scaffolding (`.claude-flow/`, `.claude/helpers/`, `.claude/skills/`, `.claude/commands/`, `.claude/agents/`) — with confirmation
- Creates/merges `.mcp.json` with Ruflo + basic-memory MCP server configs
- Installs hook scripts into `.claude/hooks/`
- Creates `.claude/settings.json` (new projects) or updates only the `hooks` section (existing projects, preserving your permissions)
- Copies skills from `templates/skills/` into `.claude/skills/` (skips existing ones)
- Creates `CLAUDE.md` from `CLAUDE_template.md` if none exists (full template with placeholders), or appends `CLAUDE_snippet.md` to an existing one (AI tools + reasoning protocol + verification + critical rules)

Re-running `cc-setup` is safe and idempotent — permissions are never overwritten.

---

## Update (1 command)

```bash
cc-update
```

- Updates Superpowers plugin from GitHub (`obra/superpowers`)
- Ruflo: nothing to do — always latest via `npx -y`
- `cc_tool` itself is local-only — edit templates in place, then re-run `cc-setup`

After editing templates, apply changes to any project:
```bash
cc-setup /path/to/your/project   # re-copies hooks + skills, preserves permissions
```

---

## How to work with the setup

### What happens automatically (via CLAUDE.md)

If you injected `CLAUDE_snippet.md` into your project's `CLAUDE.md` during `cc-setup`, these Superpowers skills trigger automatically based on context — you don't need to invoke them:

| Situation | What fires |
|-----------|------------|
| Starting a non-trivial feature | `superpowers:brainstorming` — clarifying questions → spec → your approval before any code |
| First fix attempt for a bug failed | `superpowers:systematic-debugging` — root-cause investigation, no more patching |
| About to report a task as done | `superpowers:verification-before-completion` — runs actual tests, reads output |

### What you invoke manually

These skills are available but require you to ask for them:

```
"use superpowers:writing-plans"           — test-first task breakdown in docs/superpowers/plans/
"use superpowers:executing-plans"         — fresh subagent per task, review gates
"use superpowers:dispatching-parallel-agents" — 3+ independent tasks in parallel
"use superpowers:requesting-code-review"  — isolated code review subagent
"use superpowers:finishing-a-development-branch" — merge/PR/keep/discard with confirmation
```

### basic-memory (persistent knowledge)

Cross-session knowledge: project decisions, architecture notes, user preferences. Stored as Obsidian-compatible markdown in `~/basic-memory/` — human-readable, git-trackable, navigable as a graph.

Claude uses it automatically when CLAUDE.md is present. You can also ask explicitly: "save this decision to basic-memory" or "what do we know about X?"

### Ruflo

Ruflo's MCP tools (swarm parallelism, multi-repo coordination) are loaded via `.mcp.json` and visible to Claude in every session. Claude may use them on its own when it judges they fit the task — you don't need to ask for them explicitly. For most parallel work (3-5 tasks), Claude will prefer `superpowers:dispatching-parallel-agents` since it's simpler.

---

## Do Superpowers and Ruflo cross-trigger each other?

**No.** Superpowers skills contain zero Ruflo calls. Ruflo agents run headless (`claude -p`) so Superpowers hooks don't fire. This setup does NOT include Ruflo's `CLAUDE.md` (the 38KB behavioral file from `npx ruflo init`) — without it, Ruflo is inert until you explicitly call an MCP tool. Don't add Ruflo's `CLAUDE.md` or this separation breaks.

---

## Directory structure

```
cc_tool/
  install.sh                     one-time machine setup (PATH + Superpowers)
  bin/
    cc-setup                     configure a project
    cc-update                    update tools
    cc-install-superpowers       install Superpowers globally (called by install.sh)
  templates/
    mcp.json                     Ruflo + basic-memory MCP server configs
    settings.json                full settings for new projects
    hooks-config.json            hooks-only (merged into existing settings)
    CLAUDE_template.md           full CLAUDE.md for new projects (placeholders to fill in)
    CLAUDE_snippet.md            appended to existing CLAUDE.md (AI tools + reasoning + critical rules)
    skills/                      project skills copied to .claude/skills/ on cc-setup
      reflect/SKILL.md           session reflection and learning extraction
      skills-audit/SKILL.md      audit installed skills for quality and overlap
      skill-engineer/SKILL.md    create and update skills from workflow descriptions
    hooks/
      prompt-linter.sh           warns on long ambiguous prompts
      websearch-year.py          appends year to temporal searches
```
