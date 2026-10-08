---
name: dynamic-workflows
description: Catalog of the native Workflow tool's 6 orchestration patterns (classify-and-act, fan-out-and-synthesize, adversarial-verification, generate-and-filter, tournament, loop-until-done) plus operational controls. Consult when designing multi-agent orchestration — how to fan out, verify at scale, loop until done, pick parallel vs pipeline, or compose patterns into a workflow.
user-invocable: true
---

# Dynamic Workflows

A workflow is a harness Claude writes for THIS task: a JS script that coordinates subagents. Each agent gets its own context (intermediate results stay out of the main conversation), its own model and effort (Opus 5.5 by default for judgment stages, `model: 'sonnet'` for spec'd execution arms, `effort` to tune within a model; see Model routing in CLAUDE.md), and its own isolation level (worktree or none). The structure — not better prompting — is what fixes three failure modes of long single-context work:

- **Agentic laziness** — declares done after partial progress. A loop with a stop condition keeps going.
- **Self-preferential bias** — can't fairly judge its own work. A separate verifier agent can.
- **Goal drift** — constraints quietly vanish after compaction. Constraints baked into each agent's prompt don't.

## Core API

```js
agent(prompt, {schema, label, phase, model, isolation})  // one subagent, structured output via schema
parallel([() => agent(...), ...])  // barrier: waits for ALL thunks before continuing
pipeline(items, stage1, stage2)    // streaming: each item flows stage-to-stage, no barrier
phase('Name'); log('progress')     // progress reporting
export const meta = {name, description, whenToUse, phases}  // required header block
// Forbidden inside workflow code: current-time APIs, random APIs, Node/filesystem access
```

Pick `parallel` vs `pipeline` by one question: do I need ALL results before the next step? Yes (synthesis, dedup, ranking) → `parallel` barrier. No (each item is independent end-to-end) → `pipeline` streams.

## The 6 patterns

**classify-and-act** — a cheap classifier routes work before anything does it: triage the input, then send each item down the right branch with the right model. Use when inputs vary in complexity and you want to spend the expensive model only where complexity demands it.

**fan-out-and-synthesize** — one agent per enumerable independent item (file, module, research angle), then a synthesize barrier merges the results. The dominant pattern; reach for it whenever the work splits into independent pieces and the answer needs all of them.

**adversarial-verification** — a separate skeptic agent tries to refute each finding against an explicit rubric. The verifier sees only the rubric and the artifact — never the producer or its reasoning — so it can't be charmed into agreement. Its default is to reject: it confirms a finding only when it can reproduce it at the cited location, so its job is killing false positives, not rubber-stamping. Use whenever the producer's output feeds a decision; this is the structural fix for self-preferential bias — a model grading its own work passes almost everything.

**generate-and-filter** — generate wide and commit late: many candidates first, then filter and dedup by an explicit rubric. Use for brainstorming, naming, and solution design, where the first idea is rarely the best and judging is cheaper than generating.

**tournament** — N attempts compete; a judge compares them PAIRWISE, because comparative judgment beats absolute scoring for taste and ranking. The bracket lives in deterministic loop code; agents only do the comparisons. Use when "which is best" matters more than "is this good".

**loop-until-done** — for work of unknown quantity (find all X, sweep until clean). The stop condition is a state of the world — no new findings two rounds running, logs clean — never a fixed pass count. Dedup each round against ALL findings seen so far, or the loop never converges.

## Composition

Real workflows compose 2-4 patterns. Map the failure mode you fear to the pattern that prevents it:

| Failure mode | Pattern |
|--------------|---------|
| Drift / partial coverage over a big surface | fan-out-and-synthesize |
| Self-preference (worker grades its own output) | adversarial-verification |
| Open-ended work, unknown size | loop-until-done |
| Hard-to-score outputs (taste, ranking) | tournament |

Worked examples ship in the cc_tool repo under `.claude/workflows/`:

- `model-recalibration-audit.js` — fan-out research + per-component analysis with adversarial verification, wired as a pipeline.
- `ship-pipeline.js` — plan → code → test → review with a read-only review gate, structured hand-offs between stages, per-stage models (Opus plans and reviews, Sonnet codes and tests), and optional per-stage effort.
- `loop-until-clean.js` — loop-until-done sweep (stop after two dry rounds) + adversarial verification of survivors.

Treat them as templates to adapt, not scripts to run verbatim — copy one into your project's `.claude/workflows/` to adapt it.

## Operational controls

- **Pair loops with `/goal`** for a hard completion target. Without it, workflows stop at soft completion — "looks done" instead of "is done". A complete `/goal` states the task in one line and carries measurable success criteria, invariant constraints, a checkpoint rule (pause vs run-through), a self-verify step, and a budget cap. Let the model draft it — it prompts a loop better than a hand-written spec.
- **`/loop`** runs the whole workflow on a recurring schedule.
- **Set an explicit token budget in the prompt.** Ambitious workflows balloon 5-10x past the naive estimate; the budget is the only brake.
- **Quarantine untrusted input** (support tickets, scraped pages, third-party API output): the reader agents that touch it get NO privileged actions — no edits, no shell side effects — and separate agents act on their sanitized summaries. A prompt injection in the data then has nothing to grab.
- **Workflow subagents run with acceptEdits and inherit the session's tool allowlist** — they apply file edits without prompting, so the deny-list / bash-guard hook is the load-bearing safety boundary, not an interactive confirmation. Give each agent an explicit scope (which files/commands are in-bounds), keep untrusted-input readers tool-restricted per the quarantine rule above, and don't enable workflows in a project that lacks the bash-guard PreToolUse hook.
- **Model per stage, then effort.** Pick the model from the stage's shape. Execution arms with a written spec and a check (implement a step, migrate a file, run tests against given criteria) get `model: 'sonnet'`. Read-only mechanical stages get `model: 'haiku'`: locate files or call sites, extract or summarize from logs, docs or fetched pages, classify items into given buckets (not severity or true-positive triage, which is judgment). They change nothing, and their output feeds a later stage. Never give Haiku a stage that writes, tests or judges code. Planning, root-causing, verification, adversarial checks and synthesis omit `model` and run on Opus 5.5. A classify-and-act router can make this decision per item. A Sonnet stage can run tests; the pass/fail judgment on its work is always an Opus stage. Workflow agents take a model alias. `sonnet` is Sonnet 5.5 only on the Claude API, and `haiku` is Haiku 5.5 only there and only from Claude Code 2.1.293. On Bedrock, Vertex or Foundry, leave these stages on Opus.
- **Effort is model-conditional.** Omit `effort` on an `agent()` call and it inherits the session level: `medium` by default on Opus 5.5, which matches or beats Opus 5 at `high`. Set `low` only for read-only mechanical stages that stay on Opus or Sonnet. A Haiku stage keeps its `medium` default: it already costs a twentieth of Sonnet. If its output comes back thin, move the stage to Sonnet rather than taking Haiku past `high`. Give each Haiku stage a countable finish line, such as "one row per file", because Haiku 5.5 tends to stop early. Shard its input so no single agent's context passes 100K tokens, since above that its price is 5x. Sonnet 5.5 at `low` can report a code change done without exercising it, so Sonnet stages that edit code stay at `medium` or above. Raise only the hardest verify and judge stages, going to `xhigh`/`max` only where a measured quality gain justifies it. A Sonnet stage that seems to need `xhigh` moves to Opus; Anthropic suggests considering Opus 5.5 before that point. Re-run the sweep when a stage changes model; effort names do not carry the same meaning across models.
- **Reflect-or-kill on retry loops.** When a stage can retry, don't just spend the whole cap — after each attempt have the agent judge against the goal and emit one of `continue` / `abandon` / `escalate`: `abandon` a branch that is no longer converging, `escalate` to a stronger model (a Sonnet arm goes to Opus with what was tried) or a human instead of grinding identical failed attempts. The iteration cap is the backstop; reflect-or-kill is what usually ends the loop first.

## When NOT to use

A task a regular session finishes in minutes does not need a workflow, and most traditional coding tasks do not need a panel of five reviewers. The overhead only pays off when scale, verification, or open-endedness demand structure.

Common token-wasting mistakes:

- No token budget — the single biggest cost multiplier.
- The same agent works AND verifies — reintroduces self-preferential bias.
- Treating `parallel` and `pipeline` as interchangeable — barriers where none is needed, or missing barriers before synthesis.
- Skipping `/goal` on loop patterns — soft completion ends the loop early.
- Absolute scoring where a tournament's pairwise comparison was needed.
- Letting untrusted content reach an agent that can act.
- Never saving working workflows — save them, ship them as a skill, and adapt the template per task instead of rebuilding from scratch.
