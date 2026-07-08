<!-- cc_tool:snippet:start — managed by cc_tool; this block is replaced wholesale on cc-update-project. Put project-specific guidance ABOVE this marker. -->
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
| Independent tasks can run concurrently — consider fanning out | `superpowers:dispatching-parallel-agents` |
| Significant change is ready for review | `superpowers:requesting-code-review` |
| Feature work is done, needs merge, PR, or discard | `superpowers:finishing-a-development-branch` |

Recommended practice:
- Use `superpowers:brainstorming` before writing code for a non-trivial feature.
- Use `superpowers:verification-before-completion` before reporting a task done.
- Use `superpowers:systematic-debugging` when a first fix attempt fails, to find the root cause before patching further; if several fixes fail, reconsider the architecture.

### Ruflo MCP tools

Call these explicitly when the situation matches.

| Situation | Tool |
|-----------|------|
| Fan-out needing persistent swarm state or cross-repo coordination (see Orchestration table below) | `mcp__claude-flow__swarm_init` + `mcp__claude-flow__agent_spawn` |
| Pass state between parallel agents within a swarm session | `mcp__claude-flow__memory_store` / `mcp__claude-flow__memory_retrieve` |
| Tracking progress on a multi-step operation across agents | `mcp__claude-flow__task_create` / `mcp__claude-flow__task_complete` |
| Changes span multiple repositories simultaneously | `mcp__claude-flow__coordination_orchestrate` |

### Orchestration: which fan-out mechanism

Pick by shape of the work. See the `dynamic-workflows` skill for the pattern catalog.

| Use | When |
|-----|------|
| `superpowers:dispatching-parallel-agents` | a handful of independent tasks (~2–5), you need the results back in your context, no codified repeat |
| native `Workflow` tool | dozens–hundreds of agents, OR you want loop-until-done / adversarial cross-checking / a rerunnable script — and intermediate results should stay OUT of main context |
| Ruflo swarm | cross-repo work, or persistent swarm state across a session |
| agent teams (experimental, gated by `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`) | peer Claudes that must message/debate each other |

Model/effort routing on fan-out: run repetitive arms on a cheap model / lower effort; reserve the strongest tier for synthesis and verification — Opus 4.8 at `/effort xhigh`, or Fable 5 at `high` (`xhigh` only for the most capability-sensitive stages) (e.g. Planner and Reviewer on Opus, Coder and Tester on Sonnet). For the hardest / most ambiguous features, route Planner/Reviewer to `fable` via the Agent tool's model option — ~2x Opus cost, so opt in stage-by-stage, not as a default (pass the string `fable`, not `claude-fable-5[1m]`; needs Claude Code ≥2.1.170, ≥2.1.173 for `[1m]` normalization). Bake routing into the workflow script when you create it, not mid-run.

Disable native workflows with `disableWorkflows: true` in settings or `CLAUDE_CODE_DISABLE_WORKFLOWS=1`.

### Safe autonomous loops

Designing a loop rather than firing a one-off? The `loop-engineering` skill names the six-component anatomy (trigger, isolation, written-down context, tool integration, independent verification, disk-based state). Before any loop or unattended run:

- **Spec first** — a written spec with machine-checkable acceptance criteria BEFORE the loop starts. No spec → no loop. Pair with `/goal` to force a hard completion condition — a good `/goal` carries its own task statement, success criteria, constraints, checkpoint rule, self-verify step, and budget cap. When the user describes a loop-shaped task, offer to draft that `/goal` for them rather than making them write it.
- **Bound it** — explicit iteration / retry caps so a loop never runs forever, plus an early exit: each iteration judges whether it is still converging and abandons or escalates a doomed branch rather than spending the whole cap on it.
- **State on disk** — progress lives in a file/board/queue outside the conversation (e.g. an append-only `LOG.md` — see `loop-engineering`, Disk-based state), so a compaction or a new session doesn't lose track of what's done.
- **Cost guard** — tier models: strong model (Opus 4.8, or `fable` for the hardest plan/judge stages) to plan and judge, cheap model (Sonnet/Haiku) or lower effort for repetitive parallel arms.
- **No irreversible unattended actions** — draft and queue, don't send and pray. (The bash-guard hook already blocks pushes/commits to protected branches.)
- **Verification gate** — a loop reports done only when tests / acceptance criteria actually pass, and for unattended runs the judge must not be the worker itself (see Verification protocol).

Harness-native loop tools: `/loop` (recurring or self-paced re-invocation) and `/schedule` (cron cloud routines). Caveats: scope each run's tools tightly, define explicit failure handling, and review the post-run logs.

### basic-memory (persistent knowledge graph)

Use for knowledge that must survive across sessions: project decisions, architecture notes, user preferences, recurring patterns.
Do NOT use for ephemeral swarm state — that is Ruflo's job.

basic-memory vs `knowledge-wiki`: basic-memory is a graph of decisions/preferences/architecture notes you emit as you work; `knowledge-wiki` compiles an external SOURCE corpus once into a queryable wiki you read repeatedly.

| Situation | Tool |
|-----------|------|
| Record a project decision, architecture choice, or user preference | `mcp__basic-memory__write_note` |
| Load context about a topic before starting work | `mcp__basic-memory__build_context` |
| Search for notes by topic or concept | `mcp__basic-memory__search_notes` |
| Append to or update an existing note | `mcp__basic-memory__edit_note` |

### Design & frontend taste

Any frontend *visual* design work — landing page, hero section, portfolio, marketing site, redesign, "make it look good / premium / not generic" — starts with the `design-director` skill: it reads the brief, routes to the right globally-installed taste-skill variant (`design-taste-frontend`, `minimalist-ui`, `industrial-brutalist-ui`, `high-end-visual-design`, `redesign-existing-projects`), and composes a section-by-section master design prompt from its archetype templates. Not for dashboards/data-heavy product UI; module/API shape questions go to `design-an-interface` instead.

---

## Model routing

Default to **claude-opus-4-8** — it remains the rational everyday model. Escalate a session or stage to **claude-fable-5** (the tier above Opus 4.8) when at least one holds:

- the hardest / most ambiguous problems, or first-shot correctness on a complex well-specified spec matters
- the task spans >5 files or >~30 min, or runs autonomously >1h
- it is vision-heavy (dense technical images / screenshots)
- 2+ prior Opus 4.8 attempts stalled or needed repeated retries
- code review / debugging where bug-finding recall matters (Fable's recall is officially higher than Opus 4.8, outside security domains)

Stay on (or fall back to) Opus 4.8 for: routine/short work (escalation is not worth ~2x cost); offensive-security-adjacent or deep security-audit work (Fable may refuse via the cyber classifier — fall back fable->opus-4-8); and ZDR/zero-retention orgs (Fable 5 is a Covered Model requiring 30-day retention). Switch with `/model`; `fallbackModel` accepts an ordered chain.

The >5-files / >30-min / vision / 2-failed-attempts / >1h thresholds are a community heuristic (consistent with Anthropic's positioning); the capability shapes and the security/ZDR caveats are from Anthropic docs.

---

## Reasoning approach

For debugging, architecture decisions, complex logic, multi-file changes, or ambiguous requirements: clarify the actual ask and acceptance criteria, weigh at least two approaches, and check you are solving the right problem at the right altitude rather than over-engineering. Decompose uncertainty into sub-questions and answer each with evidence from the codebase rather than guessing. If contradictory patterns exist, pick one (prefer the more recent / more tested) and flag the conflict rather than silently blending them. For trivial changes (typos, single-line fixes, renames), skip this. Surface only conclusions and the chosen approach to the user.

If a task needs deeper reasoning, raise the effort level rather than expanding this prompt — on Opus 4.8 use `/effort xhigh`; on Fable 5 `high` is already the default, so reserve `xhigh` for the most capability-sensitive work (lower effort on Fable often exceeds xhigh on prior models).

---

## Verification protocol

Prove a task works before marking it complete.

1. Run the formatter and linter
2. Run affected tests — read the actual output, do not assume it passed
3. If changing critical logic, verify against known test scenarios
4. State what was verified, including the actual command run and the tail of its real output (e.g. the `N passed in Xs` line). A "tests pass" claim with no pasted command output is a skipped step, not a verification.
5. State any uncertainty or skipped step explicitly. Do not report "completed" if work was skipped, or "tests pass" if any test was skipped or excluded.

For long or unattended runs, prefer a separate verification/review subagent (or `superpowers:requesting-code-review`) over self-judging.

Scope: apply this protocol to every code-changing task, not only large ones. "Affected tests" means the tests covering the files you changed and their direct callers; when unsure which tests apply, say so rather than skipping verification.

---

## Context management

Context is your most important resource. Use subagents (Task tool) to keep exploration, research, and verbose operations out of the main conversation.

**Spawn agents for:** codebase exploration (reading 3+ files to answer a question), research tasks (web searches, doc lookups), code review or analysis (produces verbose output), any investigation where only the summary matters.

**Stay in main context for:** direct file edits the user requested, short targeted reads (1-2 files), conversations requiring back-and-forth, tasks where the user needs intermediate steps.

If a task will read more than ~3 files or produce output the user doesn't need verbatim, delegate it to a subagent and return a summary.

On Fable 5, when you do fan out: prefer async subagents (kick them off and check results non-blocking) over blocking joins; favor long-lived subagents that reuse cached reads over many short-lived ones; and verification belongs to a fresh-context subagent rather than self-critique (the judge-not-the-worker rule in the Verification protocol).

When the same large corpus will be queried repeatedly — especially across a loop or fan-out — synthesize it once into a queryable summary (e.g. `knowledge-wiki`) rather than having each pass or agent re-read the raw source.

Long loops degrade because the context becomes disorganized, not because the model gets worse — watch for it as a run passes ~15 steps (a community heuristic, like the routing thresholds above). Four moves keep loop context clean: **Write** durable state outside the window (scratchpad, rules file, basic-memory) instead of re-deriving it; **Select** only the slice each step needs; **Compress** finished phases into a short summary before the next; **Isolate** each phase in its own subagent context so one phase can't contaminate the next. These prevent poisoning (a bad fact compounds across iterations), distraction (the agent rehashes history instead of acting), confusion (too many tools/instructions blur the decision), and clash (contradictory context left in the window). A loop that re-reads the same corpus every pass hits all four — compile it once (knowledge-wiki) and Select from that.

---

## Critical rules

1. **Read before writing** — understand existing code before modifying it. Never speculate about code you have not opened — if a file is referenced, read it first.
2. **No fabrication** — never invent functions, methods, imports, flags, config keys, or file paths. Before referencing a symbol you haven't just read, open the file / grep / check `--help` to confirm it exists. If you can't confirm something, say "I don't know" or "I couldn't verify X" — an unverifiable claim is worse than admitting uncertainty. Admitting uncertainty is rewarded, not penalized.
3. **Plan first** — use plan mode for any task with 3+ steps or architectural decisions.
4. **Reason at the right depth** — for genuinely ambiguous or architectural decisions, pause to weigh alternatives and surface trade-offs before acting.
5. **Minimal impact** — touch only what is necessary; avoid cascading changes. Every changed line should trace directly to the user's request. Remove imports and variables orphaned by YOUR changes; do not delete pre-existing dead code unless asked — mention it instead. Conformance to existing conventions beats personal taste; if a convention seems harmful, surface it and ask — don't fork the style silently.
6. **Verify before done** — follow the Verification protocol above.
7. **Never skip tests** — run at minimum the tests related to your changes.
8. **No hardcoded secrets** — use environment variables and .env files.
9. **Never hand-edit lockfiles** — `uv.lock`, `package-lock.json`, `pnpm-lock.yaml` are managed by their tools.
10. **Run quality checks before every commit** — format, lint, type check.
11. **Right tool for the job** — use Claude for judgment work (classification, drafting, summarization, ambiguous extraction). Do NOT route deterministic logic through Claude (status-code handling, retries, type transforms, routing). If plain code can answer the question, plain code answers.

<!-- cc_tool:snippet:end -->
