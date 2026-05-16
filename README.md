# cc_tool

One-command setup for Claude Code projects: Ruflo MCP tools + basic-memory + Superpowers skills.

## What this configures

| Tool | What it does | Update strategy |
|------|-------------|-----------------|
| **Superpowers** (`obra/superpowers`) | Methodology skills: TDD, brainstorming, debugging, verification | `cc-update` pulls from GitHub |
| **basic-memory** (`basicmachines-co/basic-memory`) | Persistent knowledge graph: project decisions, cross-session memory | `uvx` auto-downloads on first use — nothing to do |
| **Ruflo** (`@claude-flow/cli`) | MCP tools for swarm coordination, multi-repo orchestration | `npx -y` auto-fetches latest on every session start — nothing to do |
| **Hooks** (5 scripts) | Prompt linting, search-year injection, session context, Bash guard (branch/push/`--no-verify`), big-file read warning | Local scripts — edit templates in `cc_tool/`, re-run `cc-setup` |

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

## Three commands, one job each

```bash
cc-setup /path/to/project           # first-time setup
cc-update-project /path/to/project  # update an existing project (hooks + skills + permissions)
cc-update                           # update external deps (Superpowers plugin)
cc-devcontainer /path/to/project    # sandbox Claude Code in a container (see below)
```

- **`cc-setup`** — initialize a project the first time: `.mcp.json`, `.claude/settings.json`, `.claude/hooks/`, `.claude/skills/`, `CLAUDE.md`. Safe to re-run; idempotent on the parts it manages.
- **`cc-update-project`** — roll new cc_tool template changes into an existing project: re-copies hooks, adds any new skills, merges new hooks into `settings.json`, additively merges new `deny` entries. Preserves existing permissions, never clobbers local edits. Internally calls `cc-setup` + `cc-update-permissions`.
- **`cc-update`** — updates the Superpowers plugin globally from GitHub (`obra/superpowers`). Independent of any project.

Ruflo needs no update — always latest via `npx -y`. `cc_tool` itself is local-only — edit templates in place, then run `cc-update-project` on any project to pick up changes.

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
    cc-setup                     first-time project setup (--devcontainer chains to cc-devcontainer)
    cc-devcontainer              drop .devcontainer/ to sandbox Claude Code in Docker
    cc-token                     generate/refresh CLAUDE_CODE_OAUTH_TOKEN on host (for sandboxed containers)
    cc-update-project            update an existing project (hooks + skills + permissions)
    cc-update                    update Superpowers plugin globally
    cc-update-permissions        [internal] deny-list merge helper, called by cc-update-project
    cc-install-superpowers       install Superpowers globally (called by install.sh)
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
      design-an-interface/SKILL.md            generate 3+ divergent interface designs via parallel sub-agents (MIT, mattpocock/skills)
      improve-codebase-architecture/SKILL.md  surface deep-module refactor opportunities as GitHub-issue RFCs (MIT, mattpocock/skills)
    hooks/
      prompt-linter.sh           warns on long ambiguous prompts
      websearch-year.py          appends year to temporal searches
      session-context.py         SessionStart: git state, sensitive files, detected quality commands
      bash-guard.py              PreToolUse Bash: block commits/pushes to main/master, block --no-verify
      big-file-guard.py          PreToolUse Read: warn on files >200KB without offset/limit
```

---

## Changelog

### v0.0.3

- **Sandboxed Claude Code via devcontainer** — new [bin/cc-devcontainer](bin/cc-devcontainer) drops a spec-compliant `.devcontainer/` (VS Code / Cursor / JetBrains / Codespaces / `@devcontainers/cli` all launch it) so Claude Code runs in Docker with the project bind-mounted at `/workspace`.
- **Default-deny egress firewall** — iptables + ipset allowlist (Anthropic API, npm/PyPI, GitHub IP ranges, VS Code hosts, `astral.sh`); `--firewall off` to disable.
- **Cloud opt-in** — `--cloud aws|gcp|none` (default `none`). `aws` installs `awscli`, bind-mounts `~/.aws` read-only, exposes `AWS_*` env vars, allowlists `*.amazonaws.com`. `gcp` does the same for `google-cloud-cli` + `~/.config/gcloud` + `*.googleapis.com`.
- **Org-policy + tooling parity inside container** — [managed-settings.json](templates/devcontainer/managed-settings.json) at `/etc/claude-code/` disables `--dangerously-skip-permissions`; cc_tool's MCPs (`claude-flow`, `basic-memory`) run as-is via node + uv in the image; `gh` CLI with `GITHUB_TOKEN`/`GH_TOKEN` passthrough. One-shot: `cc-setup /path --devcontainer --cloud aws`.
- **One-command auth** — new [bin/cc-token](bin/cc-token) runs `claude setup-token` and writes `CLAUDE_CODE_OAUTH_TOKEN` to your shell profile; the env var is auto-forwarded through `containerEnv`. The firewall stays tight (no OAuth domains allowlisted) and re-auth is a single command when the token eventually expires.
- **Extended `permissions.deny`** in [templates/settings.json](templates/settings.json) from 36 → 54 entries — defense-in-depth below the devcontainer firewall:
  - GCP credentials: `~/.config/gcloud/**`, `~/.boto`, `/etc/boto.cfg`, `**/*service-account*.json`, `**/*sa-key*.json`, `**/*application_default_credentials*`
  - Other secret-bearing paths: `~/.kube/**`, `~/.docker/config.json`, `~/.netrc`, `~/.pgpass`
  - Env-var exfiltration patterns: `Bash(printenv*AWS*)`, `Bash(printenv*GOOGLE*)`
  - Pipe-to-shell installer pattern: `Bash(curl * | sh)`, `Bash(curl * | bash)`, `Bash(wget * | sh)`, `Bash(wget * | bash)`
- **New `permissions.ask` block** (20 entries) — Claude must explicitly confirm every package install, defending against supply-chain attacks (postinstall scripts, typosquats, freshly-compromised maintainers):
  - Install commands: `npm install*`, `npm add*`, `pnpm install*`, `pnpm add*`, `yarn add*`, `bun add*`, `pip install*`, `uv add*`, `uv pip install*`, `poetry add*`, `cargo install*`, `go install*`, `pipx install*`, `uv tool install*`
  - Ad-hoc runners (no lockfile, fresh-pull): `npx *`, `uvx *`, `pnpm dlx*`, `pipx run *`
- **`cc-update-permissions`** extended to merge the `ask` array additively too (was deny-only). Same UX: shows diff grouped by section, asks confirmation, only adds. Existing `cc-update-project` picks this up automatically.

### v0.0.2

- **New hooks:**
  - `session-context.py` (SessionStart) — injects git branch/dirty status, recent commits, sensitive files in root, and auto-detected quality commands from `package.json` / `pyproject.toml` / `Makefile`
  - `bash-guard.py` (PreToolUse Bash) — blocks `git commit` / `git push` to `main` / `master` / `production` / `release` (allows `--amend`), blocks any `--no-verify` bypass
  - `big-file-guard.py` (PreToolUse Read) — non-blocking warning on files >200KB read without `offset` / `limit`
- **Extended `permissions.deny`** in [templates/settings.json](templates/settings.json) from 7 → 36 entries: user-level secrets (`~/.ssh/**`, `~/.aws/**`, `~/.config/gh/**`), key files (`**/*.pem`, `**/*.key`, `**/id_rsa*`), nine lockfile globs, and seven additional dangerous-git commands (`git clean -fd*`, `git checkout .`, `git checkout -- *`, `git branch -D *`, `git reflog expire *`, `git filter-branch *`, `git filter-repo *`)
- **New commands for a cleaner three-command model:**
  - `cc-update-project` — update an existing project's cc_tool-managed files (hooks + skills + additive deny-list merge). Main user-facing update command.
  - `cc-update-permissions` — internal helper called by `cc-update-project`; shows diff, asks confirmation, only adds (never removes or modifies). Can be invoked directly for deny-list-only updates.
- **Forked two external skills** into `templates/skills/` (MIT, [mattpocock/skills](https://github.com/mattpocock/skills); LICENSE preserved per attribution):
  - `design-an-interface` — parallel sub-agents generate 3+ divergent interface designs for a module, then compare. Based on Ousterhout's "Design It Twice."
  - `improve-codebase-architecture` — exploratory refactor-hunting that surfaces shallow-module opportunities, designs deepened interfaces via parallel agents, and files the result as a GitHub issue RFC.
- **Sharpened CLAUDE_snippet.md** with three clauses distilled from Karpathy's LLM-coding guidelines (no skill forked — the rules are sharper than the skill):
  - Critical rule #4 now includes *"Every changed line should trace directly to the user's request"* and *"Remove imports/variables orphaned by YOUR changes; do not delete pre-existing dead code unless asked — mention it instead"* (covers drive-by dead-code-deletion failure mode)
  - Reasoning protocol Phase 5 adds *"Would a senior engineer call this overcomplicated?"*
- **Infra:** `cc-setup` now glob-copies all `*.sh` / `*.py` in `templates/hooks/` — future hooks auto-install without editing the script

### v0.0.1

- Initial release: `cc-setup`, `cc-update`, `cc-install-superpowers`
- Ruflo + basic-memory MCP via `.mcp.json`
- Superpowers plugin install
- Two hooks: `prompt-linter.sh` (UserPromptSubmit), `websearch-year.py` (PreToolUse WebSearch)
- `CLAUDE_template.md` for new projects, `CLAUDE_snippet.md` for existing ones
- Three project skills: `reflect`, `skills-audit`, `skill-engineer`
