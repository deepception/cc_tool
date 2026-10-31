[← README](../../README.md)

# Native dynamic workflows (Claude Code v2.1.154+)

The harness can now write and run its own multi-agent orchestration (the `Workflow` tool — triggered by the word "workflow", `/deep-research`, a saved workflow, or `ultracode` mode), spawning tens-to-hundreds of subagents whose intermediate results stay out of the main context. It is the mechanism for large independent fan-out. Workflow agents default to Opus 5.5 (cc_tool's `CLAUDE_CODE_SUBAGENT_MODEL`). Execution arms with a written spec and a check opt into Sonnet 5.5 with `model: 'sonnet'`, and plan, review and verify stages stay on Opus; `effort` tunes each within its model (see `## Model routing` in CLAUDE_snippet.md). Two things to know:

- **Permissions:** workflow-spawned subagents run in `acceptEdits` (file edits auto-approved) and inherit this project's `settings.json` allowlist. The `deny`/`ask` lists and the devcontainer firewall still apply to what they cover — but they do not gate protected-branch git writes: `Bash(git commit *)` is allowlisted and `deny` only blocks `git push --force *`, so the deterministic `bash-guard.py` PreToolUse hook is the enforced boundary for commit/push to `main`/`master`/`production`/`release` during a workflow run. Keep that hook installed wherever workflows are enabled; pre-populating `allow` with build/test commands keeps long runs from stalling on mid-run prompts.
- **Disabling:** set `disableWorkflows: true` in `settings.json` (or `CLAUDE_CODE_DISABLE_WORKFLOWS=1`), which also removes `ultracode` and the bare-word trigger.

**Workflow pattern catalog** — six composable patterns the harness can apply:

- **classify-and-act** — route the input before doing anything (pick the right branch/tool first).
- **fan-out-and-synthesize** — run independent sub-steps in parallel, then a synthesize barrier merges them.
- **adversarial-verification** — a separate agent tries to refute each finding (counters the model's self-preferential bias toward its own output).
- **generate-and-filter** — generate wide, then filter down by an explicit rubric.
- **tournament** — pairwise comparison instead of absolute scoring; wins for taste/ranking judgments.
- **loop-until-done** — repeat until a stop condition (no new findings, logs clean), not a fixed iteration count.

The shipped `model-recalibration-audit.js` already composes two of these (fan-out-and-synthesize, adversarial-verification), wired together as a pipeline. The full catalog — with worked examples and when each fits — lives in the `loop-engineering` skill, in `references/workflow-patterns.md` (the former `dynamic-workflows` skill, folded in).

**Operational controls**:

- Pair a workflow with `/goal` for a hard completion target, or `/loop` for a recurring schedule.
- Set an explicit **token budget** so a fan-out or loop can't run away.
- **Quarantine pattern** for untrusted input: have read-only reader agents (no privileged actions, no edits, no shell side effects) ingest the untrusted data, so a prompt-injection in that data can't reach into edits or commands.

cc_tool ships four saved workflows under [`.claude/workflows/`](../../.claude/workflows/):

- [`model-recalibration-audit.js`](../../.claude/workflows/model-recalibration-audit.js) — re-audits this setup against a new Claude model (see the config-staleness note in [commands.md](commands.md)), writing its report under `docs/` (git-ignored). Re-run per model release with `Workflow({name:"model-recalibration-audit", args:{newModel, oldModel}})`.
- [`loop-until-clean.js`](../../.claude/workflows/loop-until-clean.js) — loop-until-done sweep that fans out finder agents over a target until two consecutive rounds surface nothing new, then adversarially verifies the survivors and returns only the confirmed findings.
- [`factory-line.js`](../../.claude/workflows/factory-line.js) — the software factory's line for one work order: plan → build → test → verify against holdout scenarios the builder is told not to read → review (which re-runs the tests; read-only by instruction), with a rework loop, in the order's own lane (the `quick` line leaves out verify); it never merges. Unlike the others it is copied into a project, by `cc-factory init` (which `cc-setup` and `cc-update-project` now run by default), and driven by the `/factory` skill. See [factory.md](factory.md).
- [`self-heal.js`](../../.claude/workflows/self-heal.js) — classify → root cause → propose → adversarial check → write over an evidence bundle of recent sessions' errors, returning harness patches as proposals only. Its readers have Read/Grep/Glob only (quarantine pattern) and one writer writes into the run directory. cc-dash's Self-heal button and `cc-selfheal` run it headless with a fixed argv; it is not meant to be run by hand. See [self-heal.md](self-heal.md).

## Unattended / autonomous runs

An unattended run has **no human to answer a permission prompt** — so this project's `ask` list (`npm install*`, `pip install*`, `uv add*`, `cargo install*`, …) and `bash-guard.py`'s asks (deletions, an `npx <pkg>` whose package is not installed in the project, a `gh api` call that may write to GitHub, and, on a protected branch, any git command that could move it, or anywhere one that names it) derail the run the first time the agent reaches for an install: in `claude -p` the tool call is **auto-denied** (headless mode can't show a prompt; the agent gets a permission error and routes around it or gives up), while an unwatched interactive session (`/loop`, a long-running saved workflow) **stalls indefinitely** on the prompt. Recall that workflow subagents run `acceptEdits` and inherit the `allow` list but still honor `ask`/`deny`, so a mid-run `ask` fails or stalls the whole thing silently, depending on the run mode.

**Headless sessions end background workflows.** Observed on Claude Code 2.1.309, not a documented limit: a `claude -p` session ends its turn, stays alive for background work, and the Workflow is then aborted. A real factory line run was killed at about 610 seconds (status `killed`, error `Workflow aborted`) and the session never received the completion notice, so anything that was to run after the workflow (for the factory, `cc-factory record`) did not run. An interactive session is not affected, and neither is a headless session held open with `--input-format stream-json` and stdin left open: a 673-second line ran to completion that way, and the session received the notice and recorded the result. A workflow that needs longer than about 10 minutes needs one of those two. For a killed factory line, see [factory.md](factory.md#not-done-yet): the order stays `building`; run `cc-factory mark ID failed`, then `/factory run ID`.

For unattended runs, pick one:

- **Pre-resolve + pre-allowlist (host):** install dependencies *before* the run so no `ask` rule ever triggers (`npx vitest`, `npx tsc` and the like pass without a prompt once the package is in `node_modules`), and add the specific build/test/dev-server commands the loop needs (plus Playwright MCP, if the loop drives a browser) to `allow` in `settings.json`. Pair this with native `/sandbox` (see [sandboxing](sandboxing.md)) so the widened allowlist keeps an OS-level boundary on Bash.
- **Run inside `cc-devcontainer`:** the firewall is the trust boundary, so widen `allow` in `settings.json` freely there and use `--permission-mode acceptEdits`. Note that `managed-settings.json` in the container *disables* `--dangerously-skip-permissions` (see [sandboxing](sandboxing.md)); a pre-resolved allowlist is the way to get a prompt-free run, not that flag.

**Where the run lives** — an unattended loop needs a host that stays up when you walk away; a laptop that sleeps on lid-close will kill it. The `cc-devcontainer` above is one durable home; for long or recurring loops, a persistent box you SSH into — the container on an always-on machine, or a cheap Linux VPS with the session under `tmux`/`screen` so it survives disconnects — is the other. `/schedule` (cron cloud routines) sidesteps this entirely by running the agent in Anthropic's cloud rather than on your machine.
