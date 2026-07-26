# cc_tool

One-command setup for Claude Code projects: Superpowers skills + project skills + hooks + a calibrated `settings.json` — plus optional per-project extras: a self-writing vault (`cc-vault`) and a sandboxed devcontainer (`cc-devcontainer`).

## What this configures

| Tool | What it does | Update strategy |
|------|-------------|-----------------|
| **Superpowers** (`obra/superpowers`) | Methodology skills: TDD, brainstorming, debugging, verification | `cc-update` pulls from GitHub |
| **taste-skill** (`Leonxlnx/taste-skill`) | Anti-slop for the **visual** surface (global): flagship + minimalist/brutalist/soft/redesign/output frontend-design variants, routed by the `design-director` project skill | `cc-update` runs `npx skills update -g` |
| **no-ai-slop** (forked, `petergyang/no-ai-slop`, MIT) | Anti-slop for the **prose** surface (project skill, on demand): edits a draft *you* wrote into sharper writing, or names the AI-writing patterns in it without rewriting. Distinct from `## Output discipline` in CLAUDE.md, which governs what Claude writes | Vendored in `templates/skills/`; `cc-setup` installs it and refreshes an untouched copy, but never overwrites one you edited — delete the project's `.claude/skills/no-ai-slop/` to force-adopt the current version |
| **Vault** (`cc-vault`, optional per project) | Self-writing vault: you dump raw thoughts into `vault/inbox/`; scheduled Claude runs file, cross-link, digest, and synthesize them **without you** | Local scaffold — `cc-vault --force` refreshes the seed files |
| **Hooks** (5 scripts) | Session context (incl. vault state), Bash guard (branch/push/`--no-verify`/secret reads), big-file read warning, context-usage warning (80% → `/compact`), post-edit typecheck | Local scripts — edit templates in `cc_tool/`, re-run `cc-setup` |

**Design principle:** Superpowers is the methodology layer — most skills trigger automatically via CLAUDE.md rules; the project skills in `.claude/skills/` are the explicit toolbox you invoke by name. Orchestration is harness-native (the `Workflow` tool, `superpowers:dispatching-parallel-agents`). cc_tool installs no MCP server — no background daemon, no behavioral autopilot.

**Prerequisites:** `python3` (hooks + the JSON merges in `cc-setup`), `node`/`npx` (for the global skills installed via `npx skills`).

---

## Choosing a model

**Claude Opus 5** (`claude-opus-5`) is the default — everything routes here unless there is a stated reason not to ($5/$25 per MTok, 1M context, 128K output). **Claude Sonnet 5** (`claude-sonnet-5`) is the cheap tier: repetitive parallel arms, high-volume or headless work, scheduled runs — $3/$15, same 1M context. **Claude Fable 5** (`claude-fable-5`) is available but rarely justified: 2x the price ($10/$50) for an edge Opus 5 mostly closes, so escalate only for the genuinely hardest long-horizon or vision-heavy work. Fable also requires 30-day retention, so ZDR orgs cannot use it at all.

Nothing older is a routing option — Opus 4.8/4.7/4.6 are the same price class as Opus 5 with less capability, and Haiku is not used. If latency is the constraint, `/fast` runs Opus with faster output at $10/$50 — the same model, not a downgrade to a smaller one.

> **The one exception.** Opus 5 runs safety classifiers and can decline offensive-security-adjacent work (HTTP 200, `stop_reason: refusal`); Opus 4.8 is the documented landing spot for cyber-category refusals, one `/model` away. That is the only reason an older Opus appears anywhere in cc_tool. cc_tool ships no `fallbackModel` — that setting covers availability gaps rather than refusals; add one to `.claude/settings.json` yourself if you want it.

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

`install.sh` handles PATH registration, adds the `obra/superpowers` marketplace, and installs the Superpowers plugin, the security-guidance plugin, and the taste-skill design skills (global) in one run.

---

## Per-project setup (1 command)

```bash
cc-setup /path/to/your/project
```

What it does:
- Installs hook scripts into `.claude/hooks/`
- Creates `.claude/settings.json` (new projects) or updates the `hooks` section + commit-attribution policy (existing projects, preserving your permissions). The policy (`attribution.commit: ""`) keeps Claude out of commit co-authors.
- Copies skills from `templates/skills/` into `.claude/skills/`. An existing skill directory is **refreshed** when its contents still match a version cc_tool has shipped (i.e. untouched locally); a directory carrying local edits is left alone and named in the output. Local *additions* inside a refreshed skill are preserved.
- Creates `CLAUDE.md` from `CLAUDE_template.md` if none exists (full template with placeholders), or appends `CLAUDE_snippet.md` to an existing one (AI tools + model routing + reasoning approach + output discipline + verification + context management + critical rules)
- With `--vault`: chains to `cc-vault` to scaffold a self-writing vault (see the [Self-writing vault](#self-writing-vault-your-thinking-processed-without-you) section)

Re-running `cc-setup` is safe and idempotent — permissions are never overwritten.

---

## The commands, one job each

```bash
cc-setup /path/to/project           # first-time setup
cc-update-project /path/to/project  # update an existing project (hooks + skills + permissions)
cc-update                           # update external deps (Superpowers plugin)
cc-devcontainer /path/to/project    # sandbox Claude Code in a container (see below)
cc-vault /path/to/project           # scaffold a self-writing vault (see below)
```

- **`cc-setup`** — initialize a project the first time: `.claude/settings.json`, `.claude/hooks/`, `.claude/skills/`, `CLAUDE.md`. Safe to re-run; idempotent on the parts it manages. It does not manage `.mcp.json` — add an MCP server directly or with `claude mcp add` if a project needs one.
- **`cc-update-project`** — roll new cc_tool changes into an existing project: re-copies hooks, adds new skills *and refreshes unmodified existing ones*, merges new hooks into `settings.json`, additively merges new `deny`/`ask` entries, applies the commit-attribution policy, and replaces the marker-delimited methodology block in `CLAUDE.md` in place. Preserves existing permissions and all project-specific CLAUDE.md content; never clobbers local edits. Ends with a non-destructive template-drift check (lists template sections the project lacks). **Note:** project-specific CLAUDE.md *structure* (Overview, Codebase Map, Commands…) is seeded once from the template and is **not** auto-merged on update — only the managed methodology block is. Internally calls `cc-setup` + `cc-update-permissions`.
- **`cc-update`** — updates global plugins (Superpowers from `obra/superpowers`; checks/installs `security-guidance` from `anthropics/claude-plugins-official`; checks/installs the taste-skill design skills and runs `npx skills update -g`). Independent of any project.

`cc_tool` itself is local-only — edit templates in place, then run `cc-update-project` on any project to pick up changes.

> **Config staleness:** re-audit your hooks, permissions, and CLAUDE.md roughly once per Claude model release. Capabilities and failure modes shift between models — a guardrail that earned its keep on one model may be noise (or a gap) on the next — and a release may add a *new active tier* (a second model worth routing specific work to) or make instructions written for an older model too prescriptive for the newer one while remaining appropriate for the older one. Re-auditing covers all three: stale guardrails, model routing, and over-scaffolded guidance.

---

## Sandboxing Claude Code in a devcontainer

For projects where the agent runs untrusted code, touches cloud credentials, or you just want a stronger trust boundary than file-level permissions, `cc-devcontainer` drops a `.devcontainer/` into your project. Then start the container with whichever tool your IDE supports — Claude Code runs inside Docker with:

- **Filesystem** — project bind-mounted at `/workspace`; nothing outside it (host `~/.ssh`, `~/.config/gh`, host `~/.claude.json`) is visible. Optional read-only cloud creds via `--cloud`.
- **Network** — default-deny egress + ipset allowlist (Anthropic API, npm/PyPI, GitHub IP ranges, VS Code hosts, `astral.sh`, plus cloud hosts when `--cloud` is set). Disable with `--firewall off`.
- **Policy & tooling** — `managed-settings.json` at `/etc/claude-code/` blocks `--dangerously-skip-permissions` from inside; node, python3, and uv are in the image, so a project's own MCP servers run as-is; GitHub via `gh` CLI (host `GITHUB_TOKEN`/`GH_TOKEN` carried through, or `gh auth login` inside).

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

**Bringing host MCPs (Atlassian, GitHub, etc.) into the container** — by default the container only sees project-scope MCPs from the project's own `.mcp.json`, if it has one. To carry your host's user-scope MCPs in, pass `--share-mcp-auth` (bind-mounts host `~/.claude.json` read-only) plus `--mcp-domains` to allowlist the API hosts those MCPs talk to. Tradeoff: anything in the container can read the bind-mounted MCP tokens — the firewall blocks exfiltration to non-allowlisted destinations, but the tokens themselves are visible. Use this only when the agent runs code you trust.

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

/app-qa            — full QA engagement: e2e tests + UI/UX review + frontend review
/e2e-testing       — plan + execute e2e tests, agent-run or paired   (see App QA section)
/ui-ux-review      — severity-tagged walkthrough of the live app
/frontend-review   — static interface-layer source review
/no-ai-slop        — de-slop a draft you wrote, or detect AI patterns without rewriting
```

### Self-writing vault (your thinking, processed without you)

An optional per-project, single-inbox markdown vault where Claude maintains your own raw thinking. You only capture; scheduled runs do everything else — file, cross-link, digest, synthesize. Distilled from the "Self-Writing Vault" pattern, and the shipped worked example of a *proactive loop* (see the `loop-engineering` skill).

```bash
cc-vault /path/to/project      # scaffold vault/           (or: cc-setup /path --vault)
                               # --private → git-ignore it; --force → re-copy seed files

# capture — the inbox is the ONLY folder you write; no sorting at capture time
echo "half-idea about X" > vault/inbox/$(date +%F)-x.md   # or voice-transcription drops files here

# operations (the `vault` project skill), in a Claude Code session:
/vault process      # inbox → originals preserved untouched in dated raw/, distilled into
                    # notes/ with ≥3 [[backlinks]] each, index updated, daily digest written
/vault synthesize   # weekly: the one file worth rereading — recurring themes,
                    # contradictions in your own thinking, half-made promises
/vault health       # monthly graph pulse: link density, orphans, inbox backlog
```

**Run it without you** — wire ONE trigger from the generated `vault/AUTOMATION.md` (your project path pre-substituted): a system cron line running headless `claude -p --model claude-sonnet-5 --permission-mode acceptEdits "/vault process"` each weekday morning, a `/schedule` cloud routine (no laptop involved), or `/loop` in a long-lived session. Two properties make this cheap and safe unattended: processing needs only file edits inside `vault/` (nothing on the `ask` list, so headless runs never stall), and an empty inbox exits after one check — a daily trigger on a quiet vault costs ~nothing.

**The contract** (full version in the scaffolded `vault/README.md`): `inbox/` is the single inlet; `raw/` originals are immutable; every note gets ≥3 backlinks, at least one to an old note; digests daily, synthesis weekly; health means link density climbing, not file count growing. `session-context.py` surfaces vault state (inbox backlog + latest digest/synthesis) at every session start, so each session opens already knowing where your thinking stands.

**Boundaries**: your own raw thinking → vault; an external source corpus to digest once → `knowledge-wiki`; cross-session decisions/preferences → Claude Code's native memory.

### Design & frontend taste (taste-skill + design-director + product-ui-motion)

Frontend visual work (landing pages, heroes, portfolios, redesigns) is covered by two layers. `install.sh` (or `cc-install-tasteskill`) installs a curated subset of [Leonxlnx/taste-skill](https://github.com/Leonxlnx/taste-skill) (MIT) as **global** skills: `design-taste-frontend` (flagship — brief inference, VARIANCE/MOTION/DENSITY dials, anti-slop rules), `minimalist-ui`, `industrial-brutalist-ui`, `high-end-visual-design`, `redesign-existing-projects`, `full-output-enforcement`. The **`design-director` project skill** (copied by `cc-setup`) is the control layer: it reads the brief, routes to the right variant, and composes a section-by-section *master design prompt* from three archetype templates (signature-interaction hero, immersive scroll experience, dark minimal landing — distilled from motionsites.ai-style briefs) plus a prompt-anatomy guide. Say "design a landing page for X" and the routing happens automatically; the archetypes also work standalone if taste-skill isn't installed.

Those two layers cover the *marketing* surface and explicitly scope out dashboards and data-heavy product UI. The **`product-ui-motion` project skill** owns what they leave behind: how product components actually move. It asks whether a thing should animate at all before asking how — a frequency gate where 100+/day interactions get no animation at all — then supplies the exact durations, easing curves, `transform-origin` and interruptibility rules, with gesture physics (velocity handoff, Apple's momentum projection, rubber-banding) in a second reference. It fires on "add a drawer", "this dropdown feels sluggish", and as dimension 9 of `frontend-review`, so the same catalog governs the code that gets written and the review that reads it. The rule set is derived from [emilkowalski/skills](https://github.com/emilkowalski/skills) (MIT) — condensed from four overlapping skills into one, with seven technical claims corrected against current browser and library behaviour and two blanket bans narrowed to the frequency arguments underneath them. Where the global taste skills prescribe landing-page motion values (`transition: all`, transitions on *all* interactive elements, a flat `ease-in-out` ban), `product-ui-motion` supersedes them at product scale; those files are third-party and npx-managed, so cc_tool states precedence rather than patching them.

### App QA & e2e testing (app-qa + three workers)

Point Claude at any app — web, API, CLI, TUI, mobile — and get the QA engagement as documents in the target project's `docs/`: an executable e2e test plan with walkthrough results, a severity-tagged UI/UX review, and a static frontend review that duplicates neither.

```
# full engagement — in a Claude Code session inside the target project:
/app-qa                      # or point it at one app: /app-qa apps/web

# what happens, in order:
#  1. multiselect: which deliverables (e2e / UI-UX review / frontend review)
#  2. discovery once: app type, launch method, roles/fixtures, driver availability
#  3. frontend review runs as a background subagent while live testing proceeds
#  4. e2e-testing writes docs/E2E-TEST-PLAN.md and stops for your approval
#  5. mode question: agent-run (Claude drives the app) or paired
#     (Claude gives you one scenario at a time, you act and report)
#  6. results recorded in the plan doc in place: ✅ pass / ❌ bug (with root
#     cause + suggested fix inline) / unmarked = not yet exercised
#  7. wrap-up: cross-doc summary, unified fix order, offer to scaffold the
#     automated Playwright/pytest suite as a follow-up

# single activities (each works standalone, no orchestrator needed):
/e2e-testing                 # just the test plan + execution (same approval + mode gates)
/ui-ux-review                # 🔴🟡🔵 walkthrough of the live app; "what works (keep)" included
/frontend-review             # source-level interface review; no running app needed
```

**Execution modes are offered honestly.** Agent-run needs a way to drive the app: a browser MCP for web (`claude mcp add playwright -- npx @playwright/mcp@latest`), vision-agent for mobile — plain Bash already covers API/CLI/TUI. With no driver for a GUI app, only paired mode is offered and Claude names what to install for next time; it never fakes test results from source reading, and never marks ✅ without an observed result.

**Re-running is the point.** The finished plan doc is a verifiable goal: the wrap-up points at `loop-engineering` for turning re-runs into a recurring regression sweep (`/loop`, `/schedule`, or a `/goal` on "all P0 scenarios pass"). Doc formats live in the skills' `references/` files (test-plan format, severity rubric, review dimensions, app-type driving-tools table); a complete worked example of the output — mid-walkthrough, with a real inline bug entry — ships at [templates/skills/e2e-testing/references/example-e2e-test-plan.md](templates/skills/e2e-testing/references/example-e2e-test-plan.md).

### Native dynamic workflows (Claude Code v2.1.154+)

The harness can now write and run its own multi-agent orchestration (the `Workflow` tool — triggered by the word "workflow", `/deep-research`, a saved workflow, or `ultracode` mode), spawning tens-to-hundreds of subagents whose intermediate results stay out of the main context. It is the mechanism for large independent fan-out. Per-stage model routing: keep plan/review stages on Opus 5 and routine arms on Sonnet 5; the Agent tool's model enum also accepts `fable`, opt-in for the rare hardest stage (see `### Orchestration: which fan-out mechanism` in CLAUDE_snippet.md). Two things to know:

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

## Directory structure

```
cc_tool/
  install.sh                     one-time machine setup (PATH + Superpowers)
  bin/
    cc-setup                     first-time project setup (--vault / --devcontainer chain to cc-vault / cc-devcontainer)
    cc-vault                     scaffold a self-writing vault (single-inbox autonomous note processing)
    cc-devcontainer              drop .devcontainer/ to sandbox Claude Code in Docker
    cc-token                     generate/refresh CLAUDE_CODE_OAUTH_TOKEN on host (for sandboxed containers)
    cc-update-project            update an existing project (hooks + skills + permissions)
    cc-update                    update global plugins (Superpowers + security-guidance)
    cc-update-permissions        [internal] deny/ask merge helper, called by cc-update-project
    cc-install-superpowers       install Superpowers globally (called by install.sh)
    cc-install-security          install Anthropic security-guidance plugin (called by install.sh)
    cc-install-tasteskill        install taste-skill visual-design skills globally (called by install.sh)
  templates/
    settings.json                full settings for new projects; its hooks block is also
                                 what cc-setup merges into an existing settings.json
    CLAUDE_template.md           full CLAUDE.md for new projects (placeholders to fill in)
    CLAUDE_snippet.md            appended to existing CLAUDE.md (AI tools + model routing + reasoning + output discipline + verification + context mgmt + critical rules)
    vault/                       seed files dropped into project vault/ by cc-vault
      README.md                    the vault contract (5 rules + layout table)
      AUTOMATION.md                trigger wiring: cron / /schedule / /loop (path substituted)
      index.md, log.md             routing table + append-only run ledger seeds
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
      loop-engineering/SKILL.md               structural model of an autonomous loop: loop-type taxonomy, six-component anatomy, disk state, inner/outer layers
      knowledge-wiki/SKILL.md                 Karpathy compile-once wiki: distill a codebase/topic into a durable wiki
      vault/SKILL.md                          self-writing vault operations: process inbox → linked notes + digest, weekly synthesis, graph health
      no-ai-slop/SKILL.md                     edit a draft you wrote into sharper prose, or name its AI patterns without rewriting (MIT, petergyang/no-ai-slop)
        eval.md, LICENSE                        upstream self-check + preserved MIT licence (vendored verbatim)
      design-an-interface/SKILL.md            generate divergent interface designs via parallel sub-agents, then compare (MIT, mattpocock/skills)
      design-director/SKILL.md                route frontend design briefs to taste-skill variants + compose master design prompts
        references/                             prompt-anatomy guide + 3 archetype master-prompt templates
      product-ui-motion/SKILL.md              motion craft for product UI: frequency gate, easing/duration budgets, origin, interruptibility (derived from MIT, emilkowalski/skills)
        references/                             full motion catalog + review format; gesture physics (velocity handoff, momentum projection, rubber-banding)
      improve-codebase-architecture/SKILL.md  surface deep-module refactor opportunities as GitHub-issue RFCs (MIT, mattpocock/skills)
      app-qa/SKILL.md                         full QA engagement orchestrator: e2e + UI/UX review + frontend review over shared discovery, up to three docs
      e2e-testing/SKILL.md                    plan + execute e2e tests of any app type; agent-run or paired mode; ✅/❌ walkthrough plan doc
        references/                             test-plan doc format + app-type driving-tools table
      ui-ux-review/SKILL.md                   live severity-tagged UX walkthrough (🔴🟡🔵) with beyond-happy-path sweep
        references/                             severity rubric, doc structure, app-type adaptation table
      frontend-review/SKILL.md                static interface-layer source review; no-duplication contract vs sibling docs
        references/                             the 9 review dimensions + coverage-mapping format
    hooks/
      session-context.py         SessionStart: git state, sensitive files, vault state, detected quality commands
      bash-guard.py              PreToolUse Bash: block commits/pushes to main/master, block --no-verify, block secret-file reads (.env, keys, credential stores) via grep/awk/xargs/inline interpreters
      big-file-guard.py          PreToolUse Read: warn on files >200KB without offset/limit
      context-usage.py           Stop: warn when session context window passes 80% (suggest /compact)
      post-edit-typecheck.py     PostToolUse Edit|Write|MultiEdit: fast project check (tsc/cargo; ruff file-scoped for Python) after source edits, surface errors inline; tsc timeouts back off for 30 min via a marker in .git/
  tests/
    test_bash_guard.py           124-case allow/deny matrix for bash-guard.py (stdlib only, self-contained fixtures)
  .claude/
    workflows/                   saved Workflow definitions (run via the Workflow tool)
      model-recalibration-audit.js  re-audit this setup against a new Claude model
      ship-pipeline.js              Planner → Coder → Tester → Reviewer pipeline
      loop-until-clean.js           loop-until-done sweep: stop after two dry rounds, then verify survivors
```

### Verifying the Bash guard

`bash-guard.py` is the enforced boundary for protected-branch git writes during unattended and workflow runs, so it has a matrix rather than a promise:

```bash
python3 tests/test_bash_guard.py        # 124 cases, ~3s, exits non-zero on any deviation
```

Expectations encode *intended* behaviour, including the bypasses deliberately out of scope (shell expansion, `sh -c` wrappers, base64) — those assert ALLOW on purpose, so a change that appears to close one surfaces here as a diff to justify rather than a silent behavioural shift. Re-run after editing the guard, and whenever the Claude Code CLI changes its hook contract.

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for the full version history.
