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
| **Managed `CLAUDE.md` block** | Model routing, reasoning approach, when to keep going and when to stop, output discipline, verification protocol, context management, critical rules; tells Claude when each Superpowers skill applies | [templates/CLAUDE_snippet.md](templates/CLAUDE_snippet.md), replaced in place on update |
| **Superpowers** (`obra/superpowers`) | Methodology skills: brainstorming, planning, TDD, systematic debugging, verification before completion, code review | Global plugin, updated by `cc-update` |
| **Project skills** (21) | The explicit toolbox: QA and e2e testing, design routing, product-UI motion, dynamic workflows, loop engineering, knowledge wiki, vault, issue triage, skill engineering, prose de-slopping and more | [templates/skills/](templates/skills/), copied and refreshed by `cc-setup` |
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

cc_tool splits work across three models: **Opus 5.5 decides, Sonnet 5.5 carries out, Haiku 5.5 reads.**

**Claude Opus 5.5** (`claude-opus-5-5`, what the `opus` alias resolves to on the Claude API, Bedrock and Vertex) is the session model ($4/$20 per MTok, cache reads $0.20, 1M context, 128K output). It plans, root-causes bugs, makes architecture calls, reviews and verifies, and handles anything ambiguous or long-horizon. Its default effort is `medium`, which matches or beats Opus 5 at `high`; leave it there and raise it with `/effort` where you measure a gain. It is also the default subagent model: `.claude/settings.json` sets `CLAUDE_CODE_SUBAGENT_MODEL=claude-opus-5-5`, so an Agent call or workflow `agent()` that names no model runs on Opus. The exception is an agent type whose definition sets `model:`, such as one in `~/.claude/agents/`, which runs on that model.

**Claude Sonnet 5.5** (`claude-sonnet-5-5`, the `sonnet` alias from Claude Code 2.1.284 on the Claude API; on Bedrock, Vertex and Foundry the alias still maps to Sonnet 4.5, so keep arms on Opus there) runs execution arms at half the price ($2/$10 per MTok, same $0.20 cache reads, 1M context, `medium` effort by default in Claude Code, no fast mode). An execution arm has a written spec and a check that proves it done: one step of a plan, a per-file migration, tests against given acceptance criteria, a well-defined search sweep. The managed block's `## Model routing` tells Claude when to set `model: "sonnet"`. Its rules: Opus makes the pass/fail call on Sonnet's work, Sonnet arms that change code stay at `medium` or above, and a Sonnet arm that fails twice goes to Opus rather than to higher effort. It also covers agent types that set their own model, and how `superpowers:subagent-driven-development`'s model tiers map onto the split. `ship-pipeline.js` follows the same split: Opus plans and reviews, Sonnet codes and tests.

**Claude Haiku 5.5** (`claude-haiku-5-5`, the `haiku` alias from Claude Code 2.1.293 on the Claude API; on Bedrock, Vertex and Foundry the alias is still Haiku 4.5) costs $0.10/$0.50 per MTok for prompts up to 100K tokens and $0.50/$2.50 above that. That is a twentieth of Sonnet's price, with 1M context and `medium` effort by default. Anthropic positions it for high-volume summarization, extraction and classification, and as a subagent next to Opus and Sonnet. It is not for agentic coding: on Terminal-Bench 4.0 it scores 39% against Sonnet 5.5's 71%. The managed block gives it read-only mechanical arms: locating code (`Explore` with `model: "haiku"`, since `Explore` otherwise inherits Opus), and extracting or summarizing from logs, transcripts, docs and fetched pages. It also classifies items against given categories. Its output always feeds an Opus or Sonnet step, and it never edits or judges code. Briefs state what a complete answer looks like, because Haiku 5.5 tends to stop early. Each Haiku arm stays under 100K tokens of context, and thin output moves up to Sonnet. Claude Code's own background calls that use the default Haiku model moved to 5.5 with CLI 2.1.293, with no change from cc_tool. To keep every subagent on Opus in one project, add `"CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "true"` to its settings `env`.

For the main session, the default stays Opus 5.5. Two opt-ins, when you already know the work is well-scoped:

- `/model sonnet` for a session of well-scoped work: a list of small fixes, quick iteration on a feature, doc or slide edits.
- `/model opusplan` runs Opus in plan mode and Sonnet once you leave it. That's the same split applied to one session, and it fits when you plan in plan mode and then execute. Debugging outside plan mode then runs on Sonnet, so switch back to `opus` when a bug resists the first fix. Past 200K tokens of context, plan mode stays on Sonnet too.

For back-and-forth work where you read each reply before sending the next, `/fast` runs Opus 5.5 with faster output at $8/$40; it needs extra usage turned on. Sonnet 5.5 has no fast mode.

> **Flagged messages.** Opus 5.5 runs safety classifiers (cybersecurity, biology, frontier-LLM development), and Sonnet 5.5 has cybersecurity safeguards at the same level; either can decline a request (`stop_reason: refusal`). Finding vulnerabilities in source is permitted; most false positives come from compile-check phrasing. When a message is flagged, Claude Code shows a notice and moves the session to an older model, where the work continues. From Opus 5.5 that's Opus 4.8 for cybersecurity and Opus 5 for biology and frontier-LLM work. From Sonnet 5.5 (`/model sonnet`, or `opusplan` outside plan mode) it's Sonnet 5 for cybersecurity and frontier-LLM work. `/model` switches back, and pressing Esc twice lets you edit the flagged message and retry. The check covers the whole conversation, files and tool output included, so a flag can come from earlier content, and switching back can flag again until you start fresh. To be asked before each switch, change "Switch models when a message is flagged" in `/config`; if a flag was wrong, `/feedback`. Prompts that ask the model to write out its internal reasoning are declined as `reasoning_extraction`, which no fallback retries. cc_tool ships no `fallbackModel`; that setting covers overload and availability, not refusals.

Per-stage routing inside workflows and the on-fan-out effort guidance live in the managed block (`## Model routing` and `### Orchestration`).

**Long runs.** Opus 5.5 works longer on its own and sometimes stops to report instead of continuing. The managed block's `## When to keep going` tells it to keep going unless it needs you, and to stop before anything destructive. cc_tool's permission `ask`/`deny` lists and the bash and write guards stay on as the check behind that. They are tuned to ask you only before an install or a deletion; everything else either runs or is refused outright. If you'd rather pair-program, with a one-line plan and a go-ahead before each task, say so in your project's part of `CLAUDE.md`, above the managed marker. Give a long task in one message with its finish line ("done means: every endpoint uses the new client and the suite passes"). You can type follow-ups while it works rather than restarting.

## Day-to-day use

Once the block is in `CLAUDE.md`, these fire on their own: `superpowers:brainstorming` when starting a non-trivial feature, `superpowers:systematic-debugging` when a first fix fails, and `superpowers:verification-before-completion` before anything is reported done.

Everything else you ask for by name:

```
"use superpowers:writing-plans"                  — test-first task breakdown
"use superpowers:subagent-driven-development"    — run a written plan, fresh subagent per task + review
"use superpowers:executing-plans"                — run a written plan inline yourself
"use superpowers:dispatching-parallel-agents"    — a handful (~2–5) of independent tasks in parallel
"use superpowers:requesting-code-review"         — isolated code review subagent
"use superpowers:finishing-a-development-branch" — merge/PR/keep with confirmation

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
