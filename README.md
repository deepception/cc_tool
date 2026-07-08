# cc_tool

One-command setup for Claude Code projects: Ruflo MCP tools + basic-memory + Superpowers skills.

## What this configures

| Tool | What it does | Update strategy |
|------|-------------|-----------------|
| **Superpowers** (`obra/superpowers`) | Methodology skills: TDD, brainstorming, debugging, verification | `cc-update` pulls from GitHub |
| **basic-memory** (`basicmachines-co/basic-memory`) | Persistent knowledge graph: project decisions, cross-session memory | `uvx` auto-downloads on first use — nothing to do |
| **Ruflo** (`@claude-flow/cli`) | MCP tools for swarm coordination, multi-repo orchestration | `npx -y` auto-fetches latest on every session start — nothing to do |
| **Hooks** (7 scripts) | Prompt linting, search-year injection, session context, Bash guard (branch/push/`--no-verify`/secret reads), big-file read warning, context-usage warning (80% → `/compact`), post-edit typecheck | Local scripts — edit templates in `cc_tool/`, re-run `cc-setup` |

**Design principle:** Superpowers is the methodology layer — most skills trigger automatically via CLAUDE.md rules. Ruflo MCP tools are available in every session and Claude may use them when needed (swarm parallelism, multi-repo). basic-memory is the persistent knowledge layer that survives across sessions. No Ruflo CLAUDE.md, no behavioral autopilot.

**Prerequisites:** `node`/`npx` (for Ruflo), `uv`/`uvx` (for basic-memory). Install uv: `curl -LsSf https://astral.sh/uv/install.sh | sh`

---

## Choosing a model: Opus 4.8 (default) vs Fable 5 (escalation)

Claude Opus 4.8 stays the rational everyday default. Claude Fable 5 (`claude-fable-5`, GA 2026-06-09) is the capability tier above it, at exactly 2x the price ($10/$50 vs $5/$25 per MTok, as of 2026-06). Same 1M context, same 128K output, same API — a drop-in escalation, not a replacement.

**Route a session/stage to Fable 5 when at least one holds:** the task spans >5 files or >30 min; it involves images/screenshots/dense technical vision; a prior Opus 4.8 attempt stalled or needed >2 retries; it runs autonomously >1h without check-in; or it is hard, well-specified, long-horizon, or deep code review/debugging (Fable has higher bug-finding recall). Otherwise stay on Opus 4.8.

**Two caveats that send work back to Opus 4.8:**

- *Refusals:* Fable 5 runs safety classifiers; offensive-security-adjacent or deep security-audit work can trip the `cyber`/`reasoning_extraction` categories (HTTP 200, `stop_reason: refusal`). Official mitigation is to fall back to Opus 4.8 — set `"fallbackModel": "claude-opus-4-8"` in `.claude/settings.json` so availability gaps fall back automatically; on a refusal, Opus 4.8 is one `/model` away.
- *ZDR:* Fable 5 requires 30-day retention and is not available under zero-data-retention agreements. ZDR orgs stay on Opus 4.8.

**Speed vs capability at equal price:** Fable 5 has no fast mode (fast is Opus 4.8/4.7/4.6 only). Opus 4.8 in fast mode costs $10/$50 — the *same* price as Fable 5 standard — so the real choice at that price point is Opus-fast (lower latency) vs Fable-standard (higher capability).

Per-stage routing inside workflows/skills and the on-fan-out effort guidance live in [templates/CLAUDE_snippet.md](templates/CLAUDE_snippet.md) (`## Model routing` and `### Orchestration: which fan-out mechanism`).

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
- Creates `.claude/settings.json` (new projects) or updates the `hooks` section + commit-attribution policy (existing projects, preserving your permissions). The policy (`attribution.commit: ""`) keeps Claude out of commit co-authors.
- Copies skills from `templates/skills/` into `.claude/skills/` (skips existing ones)
- Creates `CLAUDE.md` from `CLAUDE_template.md` if none exists (full template with placeholders), or appends `CLAUDE_snippet.md` to an existing one (AI tools + reasoning protocol + verification + critical rules)

Re-running `cc-setup` is safe and idempotent — permissions are never overwritten.

---

## Three commands, one job each

```bash
cc-setup /path/to/project           # first-time setup
cc-update-project /path/to/project  # update an existing project (hooks + skills + permissions)
cc-update                           # update external deps (Superpowers plugin)
cc-devcontainer /path/to/project    # sandbox Claude Code in a container (see below)
```

- **`cc-setup`** — initialize a project the first time: `.mcp.json`, `.claude/settings.json`, `.claude/hooks/`, `.claude/skills/`, `CLAUDE.md`. Safe to re-run; idempotent on the parts it manages.
- **`cc-update-project`** — roll new cc_tool changes into an existing project: re-copies hooks, adds any new skills, merges new hooks into `settings.json`, additively merges new `deny`/`ask` entries, applies the commit-attribution policy, and replaces the marker-delimited methodology block in `CLAUDE.md` in place. Preserves existing permissions and all project-specific CLAUDE.md content; never clobbers local edits. Ends with a non-destructive template-drift check (lists template sections the project lacks). **Note:** project-specific CLAUDE.md *structure* (Overview, Codebase Map, Commands…) is seeded once from the template and is **not** auto-merged on update — only the managed methodology block is. Internally calls `cc-setup` + `cc-update-permissions`.
- **`cc-update`** — updates global plugins (Superpowers from `obra/superpowers`; checks/installs `security-guidance` from `anthropics/claude-plugins-official`). Independent of any project.

Ruflo needs no update — always latest via `npx -y`. `cc_tool` itself is local-only — edit templates in place, then run `cc-update-project` on any project to pick up changes.

> **Config staleness:** re-audit your hooks, permissions, and CLAUDE.md roughly once per Claude model release. Capabilities and failure modes shift between models — a guardrail that earned its keep on one model may be noise (or a gap) on the next — and a release may add a *new active tier* (a second model worth routing specific work to) or make instructions written for an older model too prescriptive for the newer one while remaining appropriate for the older one. Re-auditing covers all three: stale guardrails, model routing, and over-scaffolded guidance.

---

## Sandboxing Claude Code in a devcontainer

For projects where the agent runs untrusted code, touches cloud credentials, or you just want a stronger trust boundary than file-level permissions, `cc-devcontainer` drops a `.devcontainer/` into your project. Then start the container with whichever tool your IDE supports — Claude Code runs inside Docker with:

- **Filesystem** — project bind-mounted at `/workspace`; nothing outside it (host `~/.ssh`, `~/.config/gh`, host `~/.claude.json`) is visible. Optional read-only cloud creds via `--cloud`.
- **Network** — default-deny egress + ipset allowlist (Anthropic API, npm/PyPI, GitHub IP ranges, VS Code hosts, `astral.sh`, plus cloud hosts when `--cloud` is set). Disable with `--firewall off`.
- **Policy & tooling** — `managed-settings.json` at `/etc/claude-code/` blocks `--dangerously-skip-permissions` from inside; cc_tool's MCPs (`claude-flow` / `basic-memory`) run as-is via node + uv in the image; GitHub via `gh` CLI (host `GITHUB_TOKEN`/`GH_TOKEN` carried through, or `gh auth login` inside).

```bash
cc-devcontainer /path/to/project                # cloud=none, firewall on (safest)
cc-devcontainer /path/to/project --cloud aws    # adds awscli + bind-mounts ~/.aws read-only
cc-devcontainer /path/to/project --cloud gcp    # adds google-cloud-cli + bind-mounts ~/.config/gcloud read-only
cc-devcontainer /path/to/project --firewall off # disable firewall (host-network parity)
cc-devcontainer /path/to/project --share-mcp-auth \
    --mcp-domains api.atlassian.com,mycompany.atlassian.net   # carry host MCPs (Atlassian, etc.) into the container
cc-setup /path/to/project --devcontainer --cloud aws    # one-shot: project setup + container
```

**`--cloud` choices**

| `--cloud` | CLI installed | Mount (read-only) | Env vars exposed | Firewall additions |
|-----------|---------------|-------------------|------------------|---------------------|
| `none` (default) | — | — | — | — |
| `aws` | `awscli` | `~/.aws` → `/home/node/.aws` | `AWS_SHARED_CREDENTIALS_FILE`, `AWS_CONFIG_FILE`, `AWS_PROFILE`, `AWS_REGION` | `*.amazonaws.com` (sts, s3, ec2, iam, ssm, sso) |
| `gcp` | `google-cloud-cli` | `~/.config/gcloud` → `/home/node/.config/gcloud` | `CLOUDSDK_CONFIG`, `GOOGLE_APPLICATION_CREDENTIALS`, `CLOUDSDK_CORE_PROJECT` | `*.googleapis.com`, `accounts.google.com`, `oauth2.googleapis.com` |

Re-running `cc-devcontainer` is idempotent — pass `--force` to overwrite existing `.devcontainer/` files.

**Bringing host MCPs (Atlassian, GitHub, etc.) into the container** — by default the container only sees project-scope MCPs from `.mcp.json` (claude-flow, basic-memory). To carry your host's user-scope MCPs in, pass `--share-mcp-auth` (bind-mounts host `~/.claude.json` read-only) plus `--mcp-domains` to allowlist the API hosts those MCPs talk to. Tradeoff: anything in the container can read the bind-mounted MCP tokens — the firewall blocks exfiltration to non-allowlisted destinations, but the tokens themselves are visible. Use this only when the agent runs code you trust.

**`--mcp-domains` per MCP** — `--share-mcp-auth` carries over *all* MCPs from `~/.claude.json`; the `--mcp-domains` list just controls which extra hosts the firewall lets through. Add the rows that apply to you:

| MCP | `--mcp-domains` to add |
|-----|------------------------|
| GitHub (`mcp__github`) | *nothing* — GitHub IPs already allowlisted |
| Atlassian (`mcp__atlassian`) | `api.atlassian.com,<company>.atlassian.net` |
| Linear (`mcp__linear`) | `api.linear.app,linear.app` |
| Notion (`mcp__notion`) | `api.notion.com` |
| Slack (`mcp__slack`) | `slack.com,api.slack.com,slack-edge.com` |
| DataHub | `<your-datahub-host>` (e.g. `datahub.mycompany.com`) |
| Sentry MCP | `sentry.io` — *already in base allowlist* |

Examples:

```bash
# GitHub-only user — no --mcp-domains needed
cc-devcontainer /path --cloud aws --share-mcp-auth

# Linear + Notion
cc-devcontainer /path --cloud aws --share-mcp-auth \
    --mcp-domains api.linear.app,api.notion.com

# Atlassian
cc-devcontainer /path --cloud aws --share-mcp-auth \
    --mcp-domains api.atlassian.com,mycompany.atlassian.net
```

If an MCP times out, find what host it's hitting (check that MCP's README or the `env`/`url` fields under its entry in `~/.claude.json`), then re-run with the host added to `--mcp-domains` and rebuild.

**Authenticating Claude Code inside the container** — the firewall allowlist intentionally does NOT include `claude.ai` / `console.anthropic.com`, so OAuth login from inside the container will time out. Authenticate on the host once and pass a token in via env-file (matches Anthropic's official devcontainer pattern). One command does it all:

```bash
cc-token                                 # runs claude setup-token, writes
                                         # CLAUDE_CODE_OAUTH_TOKEN to ~/.zshrc
                                         # (or ~/.bashrc), prints next steps
source ~/.zshrc                          # reload current shell
# Then in Cursor: Dev Containers: Rebuild Container
```

The token is forwarded automatically through `containerEnv` (already wired in [templates/devcontainer/devcontainer.json](templates/devcontainer/devcontainer.json)). Re-run `cc-token` whenever the token expires (typically months apart). API-key users can `export ANTHROPIC_API_KEY=...` instead — the env var is also forwarded.

**Starting the container** — `.devcontainer/` follows the open [Dev Container Specification](https://containers.dev/), so any compatible tool works:

| Environment | How to launch |
|-------------|---------------|
| **VS Code / Cursor** | Install the `Dev Containers` extension → open the project folder → Command Palette → `Dev Containers: Reopen in Container` |
| **JetBrains** (IntelliJ, PyCharm, etc.) | File → Remote Development → Dev Containers → `New Dev Container From Local Project` |
| **CLI (any IDE, no extension)** | `npm install -g @devcontainers/cli`, then `devcontainer up --workspace-folder .` and `devcontainer exec --workspace-folder . claude` |

**Note on `Reopen in Container`** — that command only appears in the Command Palette when the currently-opened folder contains a `.devcontainer/`. Run `cc-devcontainer .` first, then open the project; the extension also shows a notification offering to reopen. If you want to start the container without first opening the folder, use `Dev Containers: Open Folder in Container...`.

### `/sandbox` for non-container projects

If you don't run the devcontainer, Claude Code's native `/sandbox` is the lighter-weight alternative: it enforces Bash filesystem and network access at the OS level (Seatbelt on macOS, bubblewrap on Linux) without Docker. Key fact for this setup: path-based Read/Edit deny rules merge into the sandbox filesystem boundary, so the literal-path entries of cc_tool's deny list (`~/.kube/**`, `~/.docker/config.json`, `~/.netrc`, `~/.config/gcloud/**`, `~/.aws/**`, …) gain real OS-level teeth once `/sandbox` is on — a Bash command can no longer reach those paths, not just the Read/Edit tools. Glob-pattern entries (`**/*service-account*.json`, `**/*.pem`, `**/id_rsa*`, …) are OS-enforced only on macOS (Seatbelt supports glob rules); Linux bubblewrap supports literal paths only, so on Linux those entries still bind only the Read/Edit tools. This is especially worth turning on for unattended/loop runs, where Bash would otherwise have no human gate.

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

### Native dynamic workflows (Claude Code v2.1.154+)

The harness can now write and run its own multi-agent orchestration (the `Workflow` tool — triggered by the word "workflow", `/deep-research`, a saved workflow, or `ultracode` mode), spawning tens-to-hundreds of subagents whose intermediate results stay out of the main context. This overlaps Ruflo's swarm role for large independent fan-out. Per-stage model routing: the harness Agent tool's model enum includes `fable` (Claude Fable 5) alongside the Opus/Sonnet tiers — route the hardest plan/review stages of a workflow to Fable 5 and keep routine arms on Opus 4.8/Sonnet (see `### Orchestration: which fan-out mechanism` in CLAUDE_snippet.md). Two things to know:

- **Permissions:** workflow-spawned subagents run in `acceptEdits` (file edits auto-approved) and inherit this project's `settings.json` allowlist. The `deny`/`ask` lists and the devcontainer firewall still apply to what they cover — but they do not gate protected-branch git writes: `Bash(git commit *)` is allowlisted and `deny` only blocks `git push --force *`, so the deterministic `bash-guard.py` PreToolUse hook is the enforced boundary for commit/push to `main`/`master`/`production`/`release` during a workflow run. Keep that hook installed wherever workflows are enabled; pre-populating `allow` with build/test commands keeps long runs from stalling on mid-run prompts.
- **Disabling:** set `disableWorkflows: true` in `settings.json` (or `CLAUDE_CODE_DISABLE_WORKFLOWS=1`), which also removes `ultracode` and the bare-word trigger.

**Workflow pattern catalog** — six composable patterns the harness can apply:

- **classify-and-act** — route the input before doing anything (pick the right branch/tool first).
- **fan-out-and-synthesize** — run independent sub-steps in parallel, then a synthesize barrier merges them.
- **adversarial-verification** — a separate agent tries to refute each finding (counters the model's self-preferential bias toward its own output).
- **generate-and-filter** — generate wide, then filter down by an explicit rubric.
- **tournament** — pairwise comparison instead of absolute scoring; wins for taste/ranking judgments.
- **loop-until-done** — repeat until a stop condition (no new findings, logs clean), not a fixed iteration count.

The shipped `model-recalibration-audit.js` already composes two of these (fan-out-and-synthesize, adversarial-verification), wired together as a pipeline. The full catalog — with worked examples and when each fits — lives in the `dynamic-workflows` skill.

**Operational controls**:

- Pair a workflow with `/goal` for a hard completion target, or `/loop` for a recurring schedule.
- Set an explicit **token budget** so a fan-out or loop can't run away.
- **Quarantine pattern** for untrusted input: have read-only reader agents (no privileged actions, no edits, no shell side effects) ingest the untrusted data, so a prompt-injection in that data can't reach into edits or commands.

cc_tool ships three saved workflows under [`.claude/workflows/`](.claude/workflows/):

- [`model-recalibration-audit.js`](.claude/workflows/model-recalibration-audit.js) — re-audits this setup against a new Claude model (see the config-staleness note above), writing its report under `docs/` (git-ignored). Re-run per model release with `Workflow({name:"model-recalibration-audit", args:{newModel, oldModel}})`.
- [`ship-pipeline.js`](.claude/workflows/ship-pipeline.js) — Planner → Coder → Tester → Reviewer pipeline for shipping a change end-to-end.
- [`loop-until-clean.js`](.claude/workflows/loop-until-clean.js) — loop-until-done sweep that fans out finder agents over a target until two consecutive rounds surface nothing new, then adversarially verifies the survivors and returns only the confirmed findings.

### Unattended / autonomous runs

An unattended run has **no human to answer a permission prompt** — so this project's `ask` list (`npm install*`, `npx *`, `pip install*`, `uv add*`, `cargo install*`, …) derails the run the first time the agent reaches for an install: in `claude -p` the tool call is **auto-denied** (headless mode can't show a prompt; the agent gets a permission error and routes around it or gives up), while an unwatched interactive session (`/loop`, a long-running saved workflow) **stalls indefinitely** on the prompt. Recall that workflow subagents run `acceptEdits` and inherit the `allow` list but still honor `ask`/`deny`, so a mid-run `ask` fails or stalls the whole thing silently, depending on the run mode.

For unattended runs, pick one:

- **Pre-resolve + pre-allowlist (host):** install dependencies *before* the run so no `ask` rule ever triggers, and add the specific build/test/dev-server commands the loop needs (plus Playwright MCP, if the loop drives a browser) to `allow` in `settings.json`. Pair this with native `/sandbox` (see above) so the widened allowlist keeps an OS-level boundary on Bash.
- **Run inside `cc-devcontainer`:** the firewall + `managed-settings.json` make `--dangerously-skip-permissions` safe-by-sandbox — no prompts to stall on, because the container itself is the trust boundary.

**Where the run lives** — an unattended loop needs a host that stays up when you walk away; a laptop that sleeps on lid-close will kill it. The `cc-devcontainer` above is one durable home; for long or recurring loops, a persistent box you SSH into — the container on an always-on machine, or a cheap Linux VPS with the session under `tmux`/`screen` so it survives disconnects — is the other. `/schedule` (cron cloud routines) sidesteps this entirely by running the agent in Anthropic's cloud rather than on your machine.

---

## Do Superpowers and Ruflo cross-trigger each other?

**No.** Superpowers skills contain zero Ruflo calls. Ruflo agents run headless (`claude -p`) so Superpowers hooks don't fire. This setup does NOT include Ruflo's `CLAUDE.md` (the 38KB behavioral file from `npx ruflo init`) — without it, Ruflo is inert until you explicitly call an MCP tool. Don't add Ruflo's `CLAUDE.md` or this separation breaks.

---

## Directory structure

```
cc_tool/
  install.sh                     one-time machine setup (PATH + Superpowers)
  bin/
    cc-setup                     first-time project setup (--devcontainer chains to cc-devcontainer)
    cc-devcontainer              drop .devcontainer/ to sandbox Claude Code in Docker
    cc-token                     generate/refresh CLAUDE_CODE_OAUTH_TOKEN on host (for sandboxed containers)
    cc-update-project            update an existing project (hooks + skills + permissions)
    cc-update                    update global plugins (Superpowers + security-guidance)
    cc-update-permissions        [internal] deny/ask merge helper, called by cc-update-project
    cc-install-superpowers       install Superpowers globally (called by install.sh)
    cc-install-security          install Anthropic security-guidance plugin (called by install.sh)
  templates/
    mcp.json                     Ruflo + basic-memory MCP server configs
    settings.json                full settings for new projects
    hooks-config.json            hooks-only (merged into existing settings)
    CLAUDE_template.md           full CLAUDE.md for new projects (placeholders to fill in)
    CLAUDE_snippet.md            appended to existing CLAUDE.md (AI tools + reasoning + critical rules)
    devcontainer/                files dropped into project .devcontainer/ by cc-devcontainer
      devcontainer.json            base config (cloud-specific mounts/env added at setup)
      Dockerfile                   node:20 + iptables/ipset + uv + optional cloud CLI
      init-firewall.sh             default-deny egress + ipset allowlist
      managed-settings.json        org-policy settings (highest precedence inside container)
    skills/                      project skills copied to .claude/skills/ on cc-setup
      reflect/SKILL.md                        session reflection and learning extraction
      skills-audit/SKILL.md                   audit installed skills for quality and overlap
      skill-engineer/SKILL.md                 create and update skills from workflow descriptions
      dynamic-workflows/SKILL.md              the 6 Workflow patterns + operational controls (full catalog)
      loop-engineering/SKILL.md               structural model of an autonomous loop: six-component anatomy, disk state, inner/outer layers
      knowledge-wiki/SKILL.md                 Karpathy compile-once wiki: distill a codebase/topic into a durable wiki
      design-an-interface/SKILL.md            generate 3+ divergent interface designs via parallel sub-agents (MIT, mattpocock/skills)
      improve-codebase-architecture/SKILL.md  surface deep-module refactor opportunities as GitHub-issue RFCs (MIT, mattpocock/skills)
    hooks/
      prompt-linter.sh           warns on long ambiguous prompts
      websearch-year.py          appends year to temporal searches
      session-context.py         SessionStart: git state, sensitive files, detected quality commands
      bash-guard.py              PreToolUse Bash: block commits/pushes to main/master, block --no-verify, block secret-file reads (.env, keys, credential stores) via grep/awk/xargs/inline interpreters
      big-file-guard.py          PreToolUse Read: warn on files >200KB without offset/limit
      context-usage.py           Stop: warn when session context window passes 80% (suggest /compact)
      post-edit-typecheck.py     PostToolUse Edit|Write|MultiEdit: fast project check (tsc/cargo; ruff file-scoped for Python) after source edits, surface errors inline; tsc timeouts back off for 30 min via a marker in .git/
  .claude/
    workflows/                   saved Workflow definitions (run via the Workflow tool)
      model-recalibration-audit.js  re-audit this setup against a new Claude model
      ship-pipeline.js              Planner → Coder → Tester → Reviewer pipeline
      loop-until-clean.js           loop-until-done sweep: stop after two dry rounds, then verify survivors
```

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for the full version history.
