# cc_tool

One-command setup for Claude Code projects: Superpowers skills, a toolbox of project skills, seven guard hooks, a calibrated `settings.json`, and a managed methodology block in `CLAUDE.md`. Optional per-project extras: a self-writing vault (`cc-vault`) and a sandboxed devcontainer (`cc-devcontainer`).

**Prerequisites:** `python3` (hooks and the JSON merges), `node`/`npx` (global skills installed via `npx skills`).

## Quick start

```bash
# once per machine: PATH, Superpowers plugin, security-guidance plugin, taste-skill design skills
cd /path/to/cc_tool && ./install.sh
source ~/.zshrc   # or ~/.bashrc

# once per project
cc-setup /path/to/your/project
```

`cc-setup` installs hooks into `.claude/hooks/`, copies skills into `.claude/skills/`, creates or updates `.claude/settings.json` (your permissions are never overwritten), and creates `CLAUDE.md` from the template or appends the managed block to an existing one. Re-running is safe and idempotent. Add `--vault` for a self-writing vault or `--devcontainer` for a sandbox.

## What you get

| Layer | What it does | Where it comes from |
|-------|-------------|---------------------|
| **Managed `CLAUDE.md` block** | Model routing, reasoning approach, output discipline, verification protocol, context management, critical rules; tells Claude when each Superpowers skill applies | [templates/CLAUDE_snippet.md](templates/CLAUDE_snippet.md), replaced in place on update |
| **Superpowers** (`obra/superpowers`) | Methodology skills: brainstorming, planning, TDD, systematic debugging, verification before completion, code review | Global plugin, updated by `cc-update` |
| **Project skills** (20) | The explicit toolbox: QA and e2e testing, design routing, product-UI motion, dynamic workflows, loop engineering, knowledge wiki, vault, issue triage, skill engineering, prose de-slopping and more | [templates/skills/](templates/skills/), copied and refreshed by `cc-setup` |
| **Hooks** (7 scripts) | Session context, Bash guard (protected branches, `--no-verify`, secret reads, destructive commands, shell-write bypass warning), write guard (system paths, secrets in content, stale reads, red-check nudge), big-file read warning, context-usage warning at 80%, post-edit typecheck with explicit `NOT CHECKED`, activity log in `.git/cc_tool/`. Every refusal reads `BLOCKED: … Suggestion: …` and the managed block teaches Claude to take the suggestion rather than dodge the guard. Per-project rules in `.claude/guard-rules.json` (`distill-rules` skill) | [templates/hooks/](templates/hooks/) |
| **taste-skill** (`Leonxlnx/taste-skill`) | Anti-slop for the visual surface, routed by the `design-director` project skill | Global skills, updated by `cc-update` |

**Design principle:** Superpowers is the methodology layer and triggers automatically via the `CLAUDE.md` block; project skills are the toolbox you invoke by name. Orchestration is harness-native (the `Workflow` tool, `superpowers:dispatching-parallel-agents`). cc_tool installs no MCP server, no background daemon, no behavioral autopilot.

## Commands

```bash
cc-setup /path/to/project           # first-time setup (also re-runnable)
cc-update-project /path/to/project  # roll new cc_tool changes into an existing project
cc-update                           # update global plugins and skills (Superpowers, security-guidance, taste-skill)
cc-devcontainer /path/to/project    # sandbox Claude Code in a Docker devcontainer
cc-vault /path/to/project           # scaffold a self-writing vault
cc-token                            # mint CLAUDE_CODE_OAUTH_TOKEN for use inside the container
```

`cc_tool` is local-only: edit the templates in place, then run `cc-update-project` on any project to pick up the changes. What each command touches, what it preserves, and how often to re-audit the setup: [docs/guides/commands.md](docs/guides/commands.md).

## Choosing a model

**Claude Opus 5** (`claude-opus-5`) is the default ($5/$25 per MTok, 1M context, 128K output). **Claude Sonnet 5** (`claude-sonnet-5`) is the cheap tier for repetitive parallel arms, high-volume or headless work, and scheduled runs ($3/$15). **Claude Fable 5.1** (`claude-fable-5-1`) costs 2x per token ($10/$50), but its cache reads are half Opus 5's and at `low`/`medium` effort it is often competitive on cost per task while scoring higher. Use it for demanding reasoning and long-horizon agentic work, or when Opus 5 at higher effort still falls short. Fable requires 30-day retention, so ZDR orgs cannot use it.

Nothing older is a routing option. If latency is the constraint, `/fast` runs Opus with faster output at $10/$50.

> **The one exception.** Opus 5 and Fable run safety classifiers and can decline offensive-security-adjacent work (`stop_reason: refusal`). Finding vulnerabilities in source is permitted; most false positives come from compile-check phrasing. A Fable refusal lands on Opus 5; an Opus 5 cyber refusal lands on Opus 4.8, one `/model` away. cc_tool ships no `fallbackModel`; add one to `.claude/settings.json` if you want it.

Per-stage routing inside workflows and the on-fan-out effort guidance live in the managed block (`## Model routing` and `### Orchestration`).

## Day-to-day use

Once the block is in `CLAUDE.md`, these fire on their own: `superpowers:brainstorming` when starting a non-trivial feature, `superpowers:systematic-debugging` when a first fix fails, and `superpowers:verification-before-completion` before anything is reported done.

Everything else you ask for by name:

```
"use superpowers:writing-plans"                  — test-first task breakdown
"use superpowers:executing-plans"                — fresh subagent per task, review gates
"use superpowers:dispatching-parallel-agents"    — a handful (~2–5) of independent tasks in parallel
"use superpowers:requesting-code-review"         — isolated code review subagent
"use superpowers:finishing-a-development-branch" — merge/PR/keep/discard with confirmation

/app-qa            — full QA engagement: e2e tests + UI/UX review + frontend review
/e2e-testing       — plan + execute e2e tests, agent-run or paired
/ui-ux-review      — severity-tagged walkthrough of the live app
/frontend-review   — static interface-layer source review
/vault process     — file the vault inbox (also: synthesize, health)
/no-ai-slop        — de-slop a draft you wrote, or name its AI patterns
```

## Guides

| Guide | Read it when |
|-------|--------------|
| [Sandboxing Claude Code](docs/guides/sandboxing.md) | The agent runs untrusted code or touches cloud credentials: `cc-devcontainer`, `--cloud`, host MCPs, auth inside the container, and native `/sandbox` |
| [Self-writing vault](docs/guides/vault.md) | You want your raw project thinking filed, cross-linked and digested on a schedule without you |
| [Design & frontend taste](docs/guides/design.md) | Landing pages and marketing surfaces (`design-director` + taste-skill) or product-UI motion (`product-ui-motion`) |
| [App QA & e2e testing](docs/guides/app-qa.md) | You want a test plan, a UI/UX walkthrough, or a static frontend review as documents in `docs/` |
| [Dynamic workflows & unattended runs](docs/guides/workflows.md) | Multi-agent fan-out with the `Workflow` tool, the six patterns, the three shipped workflows, and running anything without a human at the prompt |
| [The commands in detail](docs/guides/commands.md) | What `cc-setup`, `cc-update-project`, and `cc-update` preserve and change, and how to re-audit the setup per model release |
| [Directory structure](docs/guides/layout.md) | Every file in this repo with a one-line purpose, plus how to verify the Bash guard |

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for the full version history.
