[← README](../../README.md)

# Native dynamic workflows (Claude Code v2.1.154+)

The harness can now write and run its own multi-agent orchestration (the `Workflow` tool — triggered by the word "workflow", `/deep-research`, a saved workflow, or `ultracode` mode), spawning tens-to-hundreds of subagents whose intermediate results stay out of the main context. It is the mechanism for large independent fan-out. Per-stage model routing: routine arms on Sonnet 5; plan/review stages on `opus` or `fable` at the effort you have measured for that model (see `### Orchestration: which fan-out mechanism` in CLAUDE_snippet.md). Two things to know:

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

cc_tool ships three saved workflows under [`.claude/workflows/`](../../.claude/workflows/):

- [`model-recalibration-audit.js`](../../.claude/workflows/model-recalibration-audit.js) — re-audits this setup against a new Claude model (see the config-staleness note in [commands.md](commands.md)), writing its report under `docs/` (git-ignored). Re-run per model release with `Workflow({name:"model-recalibration-audit", args:{newModel, oldModel}})`.
- [`ship-pipeline.js`](../../.claude/workflows/ship-pipeline.js) — Planner → Coder → Tester → Reviewer pipeline for shipping a change end-to-end.
- [`loop-until-clean.js`](../../.claude/workflows/loop-until-clean.js) — loop-until-done sweep that fans out finder agents over a target until two consecutive rounds surface nothing new, then adversarially verifies the survivors and returns only the confirmed findings.

## Unattended / autonomous runs

An unattended run has **no human to answer a permission prompt** — so this project's `ask` list (`npm install*`, `npx *`, `pip install*`, `uv add*`, `cargo install*`, …) derails the run the first time the agent reaches for an install: in `claude -p` the tool call is **auto-denied** (headless mode can't show a prompt; the agent gets a permission error and routes around it or gives up), while an unwatched interactive session (`/loop`, a long-running saved workflow) **stalls indefinitely** on the prompt. Recall that workflow subagents run `acceptEdits` and inherit the `allow` list but still honor `ask`/`deny`, so a mid-run `ask` fails or stalls the whole thing silently, depending on the run mode.

For unattended runs, pick one:

- **Pre-resolve + pre-allowlist (host):** install dependencies *before* the run so no `ask` rule ever triggers, and add the specific build/test/dev-server commands the loop needs (plus Playwright MCP, if the loop drives a browser) to `allow` in `settings.json`. Pair this with native `/sandbox` (see [sandboxing](sandboxing.md)) so the widened allowlist keeps an OS-level boundary on Bash.
- **Run inside `cc-devcontainer`:** the firewall + `managed-settings.json` make `--dangerously-skip-permissions` safe-by-sandbox — no prompts to stall on, because the container itself is the trust boundary.

**Where the run lives** — an unattended loop needs a host that stays up when you walk away; a laptop that sleeps on lid-close will kill it. The `cc-devcontainer` above is one durable home; for long or recurring loops, a persistent box you SSH into — the container on an always-on machine, or a cheap Linux VPS with the session under `tmux`/`screen` so it survives disconnects — is the other. `/schedule` (cron cloud routines) sidesteps this entirely by running the agent in Anthropic's cloud rather than on your machine.
