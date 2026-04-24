## AI agent tools

This project has Superpowers skills (methodology layer), Ruflo MCP tools (explicit toolbox), and basic-memory (persistent knowledge graph) configured.
Never auto-invoke Ruflo. Never add Ruflo's CLAUDE.md or run `npx ruflo init`.

### Superpowers skills

Use these based on the situation. Invoke by reading the skill file, then following its instructions.

| Situation | Skill |
|-----------|-------|
| Designing a new feature, requirements unclear, multiple approaches possible | `superpowers:brainstorming` |
| About to write implementation code for a non-trivial feature | `superpowers:writing-plans` |
| Plan exists in docs/superpowers/plans/, ready to execute | `superpowers:executing-plans` |
| Writing a new module or function with testable behavior | `superpowers:tdd` |
| A bug was not resolved after the first fix attempt | `superpowers:systematic-debugging` |
| Marking any task as complete | `superpowers:verification-before-completion` |
| 3 or more independent tasks can run concurrently | `superpowers:dispatching-parallel-agents` |
| Significant change is ready for review | `superpowers:requesting-code-review` |
| Feature work is done, needs merge, PR, or discard | `superpowers:finishing-a-development-branch` |

Hard rules:
- Run `superpowers:brainstorming` before writing code for any non-trivial feature. Do not skip to implementation.
- Run `superpowers:verification-before-completion` before every "done" report. Never claim success without running the actual tests and reading their output.
- Run `superpowers:systematic-debugging` after any first fix attempt fails. Do not keep patching without finding the root cause first. If 3 fixes fail, stop and question the architecture.

### Ruflo MCP tools

Call these explicitly when the situation matches. Do not invoke them speculatively.

| Situation | Tool |
|-----------|------|
| 4+ truly independent tasks need to run in parallel | `mcp__claude-flow__swarm_init` + `mcp__claude-flow__agent_spawn` |
| Pass state between parallel agents within a swarm session | `mcp__claude-flow__memory_store` / `mcp__claude-flow__memory_retrieve` |
| Tracking progress on a multi-step operation across agents | `mcp__claude-flow__task_create` / `mcp__claude-flow__task_complete` |
| Changes span multiple repositories simultaneously | `mcp__claude-flow__coordination_orchestrate` |

For parallelism under 4 tasks, prefer `superpowers:dispatching-parallel-agents` over swarm.

### basic-memory (persistent knowledge graph)

Use for knowledge that must survive across sessions: project decisions, architecture notes, user preferences, recurring patterns.
Do NOT use for ephemeral swarm state — that is Ruflo's job.

| Situation | Tool |
|-----------|------|
| Record a project decision, architecture choice, or user preference | `mcp__basic-memory__write_note` |
| Load context about a topic before starting work | `mcp__basic-memory__build_context` |
| Search for notes by topic or concept | `mcp__basic-memory__search_notes` |
| Append to or update an existing note | `mcp__basic-memory__edit_note` |

---

## Reasoning protocol

Before implementing any non-trivial change, run this internal reasoning protocol silently. Surface only conclusions and the chosen approach to the user.

**Trigger**: debugging, architecture decisions, complex logic, changes touching multiple files, ambiguous requirements. Skip for trivial changes (typos, single-line fixes, renaming).

**Phase 1 — Clarify the problem**
- "What exactly is being asked? What are the acceptance criteria?"
- "What do I NOT know that I need to find out from the codebase?"

**Phase 2 — Challenge assumptions**
- "What am I assuming about existing code, data flow, or user intent?"
- "Could this request be interpreted differently?"

**Phase 3 — Explore alternatives**
- "What are at least 2 different approaches and their trade-offs?"
- "What would the ideal solution look like ignoring all constraints? Now work backward through real constraints."
- "If forced to cut 90% of this, what is the essential core?"

**Phase 4 — Anticipate consequences**
- "If this failed completely, what would be the most likely root cause?" (pre-mortem)
- "What edge cases exist? Does my solution address the root cause or just the symptom?"

**Phase 5 — Meta-check**
- "Am I solving the right problem at the right level of abstraction?"
- "What hidden constraints am I accepting without questioning?"
- "Would a senior engineer call this overcomplicated?"

When uncertain at any phase: decompose into sub-questions, answer each with evidence from the codebase. Do NOT guess and push forward.

---

## Verification protocol

Never mark a task complete without proving it works.

1. Run the formatter and linter
2. Run affected tests — read the actual output, do not assume it passed
3. If changing critical logic, verify against known test scenarios
4. State what was verified: "Tests X, Y, Z passed. Linter clean."

---

## Context management

Context is your most important resource. Use subagents (Task tool) to keep exploration, research, and verbose operations out of the main conversation.

**Spawn agents for:** codebase exploration (reading 3+ files to answer a question), research tasks (web searches, doc lookups), code review or analysis (produces verbose output), any investigation where only the summary matters.

**Stay in main context for:** direct file edits the user requested, short targeted reads (1-2 files), conversations requiring back-and-forth, tasks where the user needs intermediate steps.

If a task will read more than ~3 files or produce output the user doesn't need verbatim, delegate it to a subagent and return a summary.

---

## Critical rules

1. **Read before writing** — understand existing code before modifying it. Never speculate about code you have not opened — if a file is referenced, read it first.
2. **Plan first** — use plan mode for any task with 3+ steps or architectural decisions.
3. **Think before acting** — run the reasoning protocol for any non-trivial task.
4. **Minimal impact** — touch only what is necessary; avoid cascading changes. Every changed line should trace directly to the user's request. Remove imports and variables orphaned by YOUR changes; do not delete pre-existing dead code unless asked — mention it instead.
5. **Verify before done** — prove every change works with tests or demonstration.
6. **Never skip tests** — run at minimum the tests related to your changes.
7. **No hardcoded secrets** — use environment variables and .env files.
8. **Never hand-edit lockfiles** — `uv.lock`, `package-lock.json`, `pnpm-lock.yaml` are managed by their tools.
9. **Run quality checks before every commit** — format, lint, type check.
