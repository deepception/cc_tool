---
name: loop-engineering
description: The structural model of an autonomous loop — its definition, six components, and inner/outer layering — for designing a loop rather than one-off prompting. Consult when turning a repeated task into a loop, deciding what a loop must contain to run unattended, or when the user asks to "set up a loop / make this run on its own". For the native Workflow tool's orchestration patterns see dynamic-workflows; for the operational safety checklist see "Safe autonomous loops" in CLAUDE.md.
user-invocable: true
---

# Loop Engineering

A loop is a small program that prompts the agent, reads what it produced, decides whether it's done, and if not prompts it again with the error or the next step. The design work moves from writing the prompt to designing the loop that prompts for you.

When a task recurs, ask "what job should run on its own from now on?" Start with ONE loop, verify it end-to-end, then add more. The harness setup is half the result — a good goal plus a real verifier is what lets you point the agent at a large surface and trust the output.

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

Context is lost on compaction and between runs; a loop's memory of what's done must live on disk. Minimum: an append-only `LOG.md` the agent reads (latest few entries) before major work and appends a concise summary to after, with entries linking to the artifacts they produced. Scale up to a task board or queue when several loops share a domain. This is distinct from `knowledge-wiki` (a read-only compiled corpus) and `basic-memory` (a cross-session decision graph) — it is the loop's own work ledger.

## Inner vs outer loop

Two nested layers, each a different design job:

- **Inner loop** — the agent runtime: given this task, how do we help the agent complete it reliably? Context, tools, execution, verification. Most of cc_tool (CLAUDE.md, skills, hooks, the Verification protocol) hardens the inner loop.
- **Outer loop** — what should the agent work on next, how does state persist across sessions, how do results get monitored and fed back? Triggers, disk state, and the grader live here.

When a single loop underperforms, first ask which layer is failing: an inner-loop failure needs better context/tools/verification; an outer-loop failure needs a better trigger, clearer state, or a real feedback signal.

## When NOT to build a loop

A one-off task does not need a loop — the setup cost only pays off for work that genuinely repeats. Build the loop after you've done the task by hand once and know its verifiable end state.
