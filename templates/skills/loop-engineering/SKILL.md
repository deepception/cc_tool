---
name: loop-engineering
description: The structural model of an autonomous loop — the four loop types (turn-based, goal-based, time-based, proactive), the six components, and inner/outer layering — for designing a loop rather than one-off prompting. Consult when turning a repeated task into a loop, picking the right loop type, deciding what a loop must contain to run unattended, or when the user asks to "set up a loop / make this run on its own". For the native Workflow tool's orchestration patterns see dynamic-workflows; for the operational safety checklist see "Safe autonomous loops" in CLAUDE.md.
user-invocable: true
---

# Loop Engineering

A loop is a small program that prompts the agent, reads what it produced, decides whether it's done, and if not prompts it again with the error or the next step. The design work moves from writing the prompt to designing the loop that prompts for you.

When a task recurs, ask "what job should run on its own from now on?" Start with ONE loop, verify it end-to-end, then add more. The harness setup is half the result — a good goal plus a real verifier is what lets you point the agent at a large surface and trust the output.

## Pick the loop type

Four types, by trigger and stop condition (the Claude Code team's taxonomy). Pick the weakest that fits — each step up costs setup and tokens.

| Type | Trigger | Stops when | Best for | Usage lever |
|------|---------|------------|----------|-------------|
| Turn-based | your prompt | Claude judges it done or needs input | short tasks outside any regular process | specific prompts; verification skills cut turns |
| Goal-based (`/goal`) | your prompt | evaluator confirms the criteria, or turn cap | tasks with verifiable exit criteria | deterministic criteria + explicit cap ("stop after 5 tries") |
| Time-based (`/loop`, `/schedule`) | an interval | you cancel, or the work completes (PR merged, queue empty) | recurring work; polling an external system | match the interval to how fast the watched thing changes |
| Proactive | schedule/event, no human in real time | each task exits at its goal; the routine runs until turned off | recurring streams of well-defined work: triage, migrations, dependency bumps, inbox processing | cheap model for routine arms, strongest only for judgment |

`/goal` mechanics worth knowing: each time the agent tries to stop, a separate evaluator model checks the stated condition and sends it back until the goal is met or the turn cap hits — so deterministic criteria (N tests pass, score clears a threshold) beat judgment calls, which let the loop end at "good enough". (The six elements of a complete `/goal` are in "Safe autonomous loops" in CLAUDE.md.)

A proactive loop is a composition, not a primitive: `/schedule` (trigger) + `/goal` + verification skills (stop) + a workflow (orchestration) + auto mode (no permission stalls). The self-writing vault (`vault` skill) is the shipped example.

## Six components

A loop that runs unattended has all six. Missing one is usually why a loop stalls, drifts, or silently corrupts state.

| Component | What it is | Where cc_tool provides it |
|-----------|------------|---------------------------|
| Trigger | Starts the loop without you pressing go — a schedule or a recurring re-invocation | `/loop` (recurring/self-paced), `/schedule` (cron cloud routine) |
| Isolation | A private checkout per agent so concurrent agents don't overwrite each other | `superpowers:using-git-worktrees`; per-agent `isolation` in the Workflow tool |
| Written-down context | Conventions, build steps, project rules kept where the agent reads them every run | CLAUDE.md + `/goal` for a verifiable end state |
| Tool integration | Connectors to issue tracker, CI, chat so the loop can open PRs, link tickets, post results | project MCP servers + allowlisted `gh`/build commands |
| Independent verification | A separate grader — a model reviewing its own work passes almost everything | Verification protocol (judge ≠ worker); the `adversarial-verification` pattern |
| Disk-based state | A file/board/queue OUTSIDE the conversation recording what's done and what's next | see below — a loop must not keep its progress only in context |

## Disk-based state

Context is lost on compaction and between runs; a loop's memory of what's done must live on disk. Minimum: an append-only `LOG.md` the agent reads (latest few entries) before major work and appends a concise summary to after, with entries linking to the artifacts they produced. Scale up to a task board or queue when several loops share a domain. This is distinct from `knowledge-wiki` (a read-only compiled corpus) and Claude Code's native memory (cross-session decisions) — it is the loop's own work ledger.

## Inner vs outer loop

Two nested layers, each a different design job:

- **Inner loop** — the agent runtime: given this task, how do we help the agent complete it reliably? Context, tools, execution, verification. Most of cc_tool (CLAUDE.md, skills, hooks, the Verification protocol) hardens the inner loop.
- **Outer loop** — what should the agent work on next, how does state persist across sessions, how do results get monitored and fed back? Triggers, disk state, and the grader live here.

When a single loop underperforms, first ask which layer is failing: an inner-loop failure needs better context/tools/verification; an outer-loop failure needs a better trigger, clearer state, or a real feedback signal.

And when an individual result misses the bar, don't stop at fixing that result — encode the failure back into the system (a sharper verifier, a new CLAUDE.md convention, a skill update via `reflect`) so every future iteration inherits the fix. A loop's quality ceiling is the system around it, not the model.

## Keeping a loop cheap

- Match the trigger interval to how often the watched thing actually changes; react to events over polling where possible.
- Pilot on a small slice before a large run — fan-outs balloon well past the naive estimate (see the token-budget control in `dynamic-workflows`).
- Push deterministic steps into scripts the loop calls (scaffolding, parsing, form-filling) instead of re-reasoning them every run — the loop-shaped case of the "Right tool for the job" critical rule.
- Monitor: `/usage` breaks down spend by skill/subagent/MCP; `/goal` with no arguments shows turns and tokens so far; `/workflows` shows per-agent usage and lets you stop an agent.

## When NOT to build a loop

A one-off task does not need a loop — the setup cost only pays off for work that genuinely repeats. Build the loop after you've done the task by hand once and know its verifiable end state. The adoption test for a candidate task: are you the bottleneck; can you write the verification check; is the goal crisp; does the work arrive on a schedule? A "no" on the verification check is disqualifying — fix that before wiring any trigger.
