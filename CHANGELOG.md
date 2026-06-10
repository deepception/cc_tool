# Changelog

All notable changes to `cc_tool` are documented here. See the [README](README.md) for usage.

## v0.0.6

Loops & multi-agent orchestration release — distilled from nine community X articles on Claude Code loops, dynamic workflows, and recursive agent calls, then adversarially verified (6-domain review + skeptic-per-finding) and design-debated (3 positions + judge per open question).

- **New skill [`dynamic-workflows`](templates/skills/dynamic-workflows/SKILL.md)** — teaches the native `Workflow` tool: the harness mental model (per-agent isolation/model choice; fixes agentic laziness, self-preferential bias, goal drift), the core API (`agent`/`parallel`/`pipeline`), the 6 orchestration patterns (classify-and-act, fan-out-and-synthesize, adversarial-verification, generate-and-filter, tournament, loop-until-done), composition, and operational controls (`/goal`, `/loop`, explicit token budgets, quarantine for untrusted input).
- **New skill [`knowledge-wiki`](templates/skills/knowledge-wiki/SKILL.md)** — Karpathy's compile-once "LLM Wiki" pattern: digest an external source corpus into a project-local `.wiki/` (raw snapshots + synthesized pages + index + log) and answer queries from the digest instead of re-reading raw files (~84-90% token reduction per the llm-wiki-compiler reference). Complements basic-memory (emitted decisions) — does not replace it. In-repo sources are cited by path, never duplicated into `raw/`.
- **Two worked-example workflows** (repo-local, templates to adapt — deliberately NOT installed into projects): [`ship-pipeline.js`](.claude/workflows/ship-pipeline.js) — Planner (Opus) → Coder (Sonnet) → Tester (Sonnet) → Reviewer (Opus) with schema'd handoffs, the coder report threaded into the read-only review gate, and a fail-fast guard on missing args; [`loop-until-clean.js`](.claude/workflows/loop-until-clean.js) — loop-until-done sweep (stop after two consecutive dry rounds, canonical dedup slugs normalized in plain code) + adversarial verification of survivors.
- **`CLAUDE_snippet.md` — orchestration & loop guidance** (managed block): a which-fan-out-mechanism decision table (`dispatching-parallel-agents` ~2–5 tasks / native `Workflow` for scale, loops, adversarial checks / Ruflo swarm for cross-repo or persistent swarm state / experimental agent teams for peer debate); model/effort routing on fan-out (cheap arms, Opus + `xhigh` for synthesis/verification); a **Safe autonomous loops** checklist (spec-first + `/goal`, bounded iterations, cost guard, no irreversible unattended actions, verification gate where the judge is never the worker); first-class notes on `/loop` and `/schedule`; basic-memory vs knowledge-wiki boundary; compile-once context principle.
- **Anti-fabrication layer** (from the "4-layer honesty setup"): new Critical rule **No fabrication** — never invent functions/methods/imports/flags/config keys/paths, confirm symbols before referencing, "I don't know" is rewarded; Verification protocol step 4 now requires the actual command + tail of its real output (a bare "tests pass" claim is a skipped step); `CLAUDE_template.md` mirrors the paste-output rule at the implementation checkpoint and points to the single-sourced No-fabrication rule (asymmetric dedupe per debate ruling).
- **New hook [`post-edit-typecheck.py`](templates/hooks/post-edit-typecheck.py)** (PostToolUse Edit|Write|MultiEdit) — fast project check after source edits (`tsc --noEmit` / `cargo check`; `ruff check` file-scoped), informational only, silent on success, never blocks; resolves project-local `tsc` first (no `npx` fallback — squatting risk), strips ANSI, handles `ruff.toml`-only projects; tsc timeouts back off for 30 min via a self-describing marker in `.git/` (cargo self-heals via incremental `target/`, so no marker). Registered in both `settings.json` and `hooks-config.json`.
- **`bash-guard.py` — secret-read guard**: denies Bash reads of secret material (`.env*`, key files, credential stores) via readers the file-Read deny rules can't see — `grep`/`awk`/`sort`/`cut`/`source`/`.`/`xargs`/inline `python -c`/`node -e` — with command-position gating for `.`/`source` and grep-family pattern-argument awareness (searching the codebase *for* ".env" is not reading `.env`). Verified against a 24-case allow/deny matrix; existing branch/push/`--no-verify` guards unchanged.
- **`settings.json` deny hardening**: bare env dumps (`env`, `printenv`, `set`, `export -p`; the old `printenv*AWS*`/`*GOOGLE*` pair is subsumed and removed); Azure / DigitalOcean / git-credentials / npmrc / pypirc / gnupg / shell-history credential stores; terraform state files + `Read(**/*secret*.tfvars*)` (narrowed from blanket `*.tfvars` per debate ruling — plain IaC config stays readable); Read-deny on `*.pem`/`*.key`/`id_rsa*` for symmetry.
- **README** — workflow pattern catalog + operational controls; **Unattended / autonomous runs** section (the `ask` list auto-denies in `claude -p` and stalls interactive loops — pre-resolve + pre-allowlist paired with `/sandbox`, or run in the devcontainer); native **`/sandbox`** section (literal-path deny rules gain OS-level teeth; Linux bubblewrap is literal-paths-only, macOS Seatbelt supports globs); accurate hook table (7 scripts).
- **`design-an-interface`** — sub-agents now ground designs in the project's interface vocabulary while explicitly preserving radical structural divergence per their assigned constraint.

## v0.0.5

- **Recalibrated the config for Claude Opus 4.8** (released 2026-05-28; a drop-in replacement for 4.7 with no breaking API changes), driven by the new `model-recalibration-audit` workflow. No P0 changes — tuning only. **Every security/safety guardrail is unchanged** (permission `deny`/`ask` lists, `bash-guard` protections, devcontainer firewall + managed settings, `websearch-year.py`, `big-file-guard.py` threshold, `session-context.py` caps, and the Superpowers-vs-Ruflo separation).
- **New reusable workflow** [.claude/workflows/model-recalibration-audit.js](.claude/workflows/model-recalibration-audit.js) — re-audits hooks/permissions/CLAUDE.md/skills against a newer Claude model (research → capability profile → per-component analysis → adversarial verification → report). Re-runnable per model release with `args { newModel, oldModel }`, matching the v0.0.4 config-staleness note.
- **`CLAUDE_snippet.md` — reasoning and phrasing tuned for 4.8** (4.8 steers reasoning via the `effort` lever / adaptive thinking, follows instructions more literally, and over-triggers on emphatic guardrail phrasing):
  - Replaced the always-resident 5-phase Reasoning protocol with a compact `## Reasoning approach` directive that defers depth control to `effort` (e.g. `/effort xhigh`). Kept the clarify/alternatives/altitude judgment, the contradiction-handling rule, and the no-guessing rule.
  - Downgraded anti-laziness "Never/CRITICAL/MUST" phrasing to plain imperatives across the practice list, Verification protocol, and Critical rule #3; de-duplicated the triply-stated verify-before-done rule (now one authoritative Verification protocol, referenced elsewhere). Secret-handling rules stay emphatic.
  - Reframed Ruflo/superpowers fan-out from ceilings ("3+/under 4 tasks") to positive "consider fanning out" guidance (4.8 fans out conservatively by default); **kept "Never auto-invoke Ruflo"**.
  - Stated the Verification protocol's scope explicitly; added a note on harness-native dynamic workflows + the `disableWorkflows` / `CLAUDE_CODE_DISABLE_WORKFLOWS=1` switch.
- **`context-usage.py` — 1M-context-aware (Opus 4.8 default window):** derives the budget from the session model (`message.model` in the transcript) — 1,000,000 for Opus 4.8, 200,000 fallback for 4.7-and-earlier / Foundry / unknown — instead of a hardcoded 200K. `CONTEXT_USAGE_LIMIT` still overrides. Fixes false "80% full" warnings firing at ~16% of a real 1M window. (The model-detection assumption was verified against a live 4.8 transcript: `message.model` is `claude-opus-4-8`.)
- **`bash-guard.py` — hardened push detection:** now denies `git push --all` / `--mirror` and parses every refspec positional (checking each destination, normalizing `refs/heads/…` and `+`/`src:dst` forms) instead of only the second token. Parsing is scoped to the `git push` invocation via a punctuation-aware tokenizer, so chained commands, comments, and quoted text no longer cause false negatives or false positives; falls back to the legacy regex if tokenizing fails.
- **`prompt-linter.sh`** — dropped the per-prompt "ask one clarifying question" directive (4.8 self-initiates clarification) and raised the length threshold from 50 → 150 words; now a neutral length note.
- **`CLAUDE_template.md`** — softened non-security "do NOT / BEFORE" conventions to plain imperatives (secret-handling rules unchanged); reframed the verification and progress-tracking workflow lines.
- **Skills** — `skills-audit` now separates a broad coverage pass from a prioritization step (4.8 obeys narrow review rubrics literally, which can drop recall); `skill-engineer` now teaches plain-imperative phrasing and explicit instruction scope for authored skills.
- **README** — documents the native dynamic-workflow / `ultracode` capability of the Opus 4.8 harness, that workflow subagents run in `acceptEdits` and inherit the `settings.json` allowlist, and how to disable them.
- **Keep Claude out of commit co-authors** — `templates/settings.json` sets `attribution: { "commit": "" }`, suppressing the `Co-Authored-By: Claude` trailer in cc_tool-managed projects (native Claude Code setting, not a hook). `cc-setup` applies it to existing projects too, only where unset (additive, idempotent).

## v0.0.4

- **Sharpened CLAUDE_snippet.md** with four clauses distilled from a community extension of Karpathy's CLAUDE.md rules (skill not forked — the phrasings are sharper than the skill):
  - New Critical Rule #10 — *"Right tool for the job"*: use Claude for judgment work; do NOT route deterministic logic (status codes, retries, type transforms, routing) through it
  - Critical Rule #4 — added *"conformance to existing conventions beats personal taste; surface harmful conventions, don't fork the style silently"*
  - Reasoning Phase 5 — added the *"surface conflicts, don't average them into a weird hybrid"* self-check
  - Verification protocol — added *"surface uncertainty, never hide it; 'completed' is wrong if anything was skipped silently"*
- **New hook `context-usage.py`** (Stop) — warns to stderr when the session's context window passes 80% (suggests `/compact`/`/clear`). Stdlib-only, no daemon/browser/network; tunable via `CONTEXT_USAGE_LIMIT` / `CONTEXT_USAGE_WARN_PCT`. Parses the transcript JSONL and no-ops gracefully if the format changes.
- **Hierarchical CLAUDE.md guidance** in [CLAUDE_template.md](templates/CLAUDE_template.md) — renamed Modules → `### Codebase Map` (one line per top-level folder) and added a note to keep the root file lean and push local conventions into subdirectory CLAUDE.md files.
- **`security-guidance` plugin integration** — closes the one orthogonal gap in the v0.0.3 hardening: vulnerability patterns in code Claude *writes* (command injection, `eval`/`new Function`, XSS, Python pickle, `os.system`, unsafe exec), vs. v0.0.3's access-control focus on what Claude can *touch*.
  - New [bin/cc-install-security](bin/cc-install-security) — installs the Anthropic-verified `security-guidance@claude-plugins-official` plugin. Idempotent.
  - `cc-update` now also checks security-guidance: updates it if present, installs it if missing.
  - `install.sh` runs `cc-install-security` alongside Superpowers on first setup.
- **Config-staleness note** in the README — re-audit hooks/permissions/CLAUDE.md once per Claude model release.
- **Fixed CLAUDE.md snippet updates** — `cc-setup` (and therefore `cc-update-project`) now wraps the cc_tool-managed block in `<!-- cc_tool:snippet:start/end -->` markers and replaces it in place on every run. Previously a coarse "does the file mention basic-memory + Reasoning protocol?" check skipped the update entirely, so snippet changes (new rules, protocol tweaks) never reached existing projects. The new logic: replace between markers if present; else migrate a legacy appended block (from `## AI agent tools` to EOF) to markers; else append. Project-specific content above the block is never touched, and re-runs are idempotent (no duplication).
- **`cc-update-project` template-drift check (Step 3)** — informational, non-destructive. After updating the managed block, it compares `CLAUDE_template.md`'s section headings against the project's CLAUDE.md (project content only, above the markers). Template *structural* changes are not auto-applied — project-specific content diverges from the template and a silent merge would clobber it — so this surfaces drift instead: template-following projects get a precise list of missing sections (e.g. "Codebase Map"); heavily-customized projects (low heading overlap) get a "review the template manually" hint rather than a false-positive spew.

## v0.0.3

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

## v0.0.2

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

## v0.0.1

- Initial release: `cc-setup`, `cc-update`, `cc-install-superpowers`
- Ruflo + basic-memory MCP via `.mcp.json`
- Superpowers plugin install
- Two hooks: `prompt-linter.sh` (UserPromptSubmit), `websearch-year.py` (PreToolUse WebSearch)
- `CLAUDE_template.md` for new projects, `CLAUDE_snippet.md` for existing ones
- Three project skills: `reflect`, `skills-audit`, `skill-engineer`
