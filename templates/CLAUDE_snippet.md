<!-- cc_tool:snippet:start — managed by cc_tool; this block is replaced wholesale on cc-update-project. Put project-specific guidance ABOVE this marker. -->
## AI agent tools

This project has Superpowers skills (methodology layer) plus the project skills in `.claude/skills/` configured.

### Superpowers skills

Use these based on the situation. Invoke by reading the skill file, then following its instructions.

| Situation | Skill |
|-----------|-------|
| Designing a new feature, requirements unclear, multiple approaches possible | `superpowers:brainstorming` |
| About to write implementation code for a non-trivial feature | `superpowers:writing-plans` |
| Plan exists in docs/superpowers/plans/, ready to execute | `superpowers:executing-plans` |
| Writing a new module or function with testable behavior | `superpowers:test-driven-development` |
| A bug was not resolved after the first fix attempt | `superpowers:systematic-debugging` |
| Committing, opening a PR, or reporting a multi-step task done | `superpowers:verification-before-completion` |
| Independent tasks can run concurrently — consider fanning out | `superpowers:dispatching-parallel-agents` |
| Significant change is ready for review | `superpowers:requesting-code-review` |
| Feature work is done, needs merge, PR, or discard | `superpowers:finishing-a-development-branch` |

Recommended practice:
- Use `superpowers:brainstorming` before writing code for a non-trivial feature.
- Use `superpowers:verification-before-completion` before committing, opening a PR, or reporting a multi-step task done.
- Use `superpowers:systematic-debugging` when a first fix attempt fails, to find the root cause before patching further; if several fixes fail, reconsider the architecture.

### Orchestration: which fan-out mechanism

Pick by shape of the work. See the `dynamic-workflows` skill for the pattern catalog.

| Use | When |
|-----|------|
| `superpowers:dispatching-parallel-agents` | a handful of independent tasks (~2–5), you need the results back in your context, no codified repeat |
| native `Workflow` tool | dozens–hundreds of agents, OR you want loop-until-done / adversarial cross-checking / a rerunnable script — and intermediate results should stay OUT of main context |
| agent teams (experimental, gated by `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`) | peer Claudes that must message/debate each other |

Model/effort routing on fan-out: repetitive arms on `sonnet` (Sonnet 5) at lower effort; judgment stages — planning, synthesis, verification — on `opus` (Opus 5) at `/effort xhigh`. E.g. Planner and Reviewer on Opus 5, Coder and Tester on Sonnet 5. The Agent tool's model option also accepts `fable`, worth its ~2x cost only for a genuinely hardest stage — opt in stage-by-stage (pass the string `fable`, not `claude-fable-5[1m]`; needs Claude Code ≥2.1.170). Bake routing into the workflow script when you create it, not mid-run.

Disable native workflows with `disableWorkflows: true` in settings or `CLAUDE_CODE_DISABLE_WORKFLOWS=1`.

### Safe autonomous loops

Designing a loop rather than firing a one-off? The `loop-engineering` skill names the six-component anatomy (trigger, isolation, written-down context, tool integration, independent verification, disk-based state). Before any loop or unattended run:

- **Spec first** — a written spec with machine-checkable acceptance criteria BEFORE the loop starts. No spec → no loop. Pair with `/goal` to force a hard completion condition — a good `/goal` carries its own task statement, success criteria, constraints, checkpoint rule, self-verify step, and budget cap. When the user describes a loop-shaped task, offer to draft that `/goal` for them rather than making them write it.
- **Bound it** — explicit iteration / retry caps so a loop never runs forever, plus an early exit: each iteration judges whether it is still converging and abandons or escalates a doomed branch rather than spending the whole cap on it.
- **State on disk** — progress lives in a file/board/queue outside the conversation (e.g. an append-only `LOG.md` — see `loop-engineering`, Disk-based state), so a compaction or a new session doesn't lose track of what's done.
- **Cost guard** — tier models: Opus 5 to plan and judge, Sonnet 5 or lower effort for repetitive parallel arms.
- **No irreversible unattended actions** — draft and queue, don't send and pray. (The bash-guard hook already blocks pushes/commits to protected branches.)
- **Verification gate** — a loop reports done only when tests / acceptance criteria actually pass, and for unattended runs the judge must not be the worker itself (see Verification protocol).

Harness-native loop tools: `/loop` (recurring or self-paced re-invocation) and `/schedule` (cron cloud routines). Caveats: scope each run's tools tightly, define explicit failure handling, and review the post-run logs plus the `/usage` breakdown (spend by skill/subagent/MCP).

### Self-writing vault

If the project has a `vault/`: route any mid-session "note this down / remember this thought" capture to a new dated file in `vault/inbox/` (the single inlet) — never file it into `vault/notes/` directly, and never edit anything under `vault/raw/`. Filing, weekly synthesis, and graph health belong to the scheduled `/vault` runs (see the `vault` skill; contract in `vault/README.md`).

Which store: knowledge that must survive across sessions (decisions, architecture notes, user preferences, recurring patterns) → Claude Code's native memory; an external SOURCE corpus you will query repeatedly → `knowledge-wiki`, which compiles it once into a wiki; the USER'S own raw thinking → the project `vault/`, filed autonomously by the `vault` skill.

### Design & frontend taste

Any frontend *visual* design work — landing page, hero section, portfolio, marketing site, redesign, "make it look good / premium / not generic" — starts with the `design-director` skill: it reads the brief, routes to the right globally-installed taste-skill variant (`design-taste-frontend`, `minimalist-ui`, `industrial-brutalist-ui`, `high-end-visual-design`, `redesign-existing-projects`), and composes a section-by-section master design prompt from its archetype templates. Not for dashboards/data-heavy product UI; module/API shape questions go to `design-an-interface` instead.

Product-UI *motion* is a separate surface: building or tuning a dropdown, modal, drawer, sheet, toast, tooltip, popover, command palette, tab, accordion or drag/swipe interaction — or a complaint that motion feels sluggish or janky — goes to `product-ui-motion`, in new code and existing apps alike, dashboards included. It decides whether something animates at all before deciding how, and on duration, easing and `transition` shorthand it outranks the motion advice in the global taste skills, which is written at landing-page scale.

### App QA & e2e testing

"Test the app end to end / QA this app / review the UI" → the `app-qa` skill orchestrates the full engagement (e2e test plan + execution, UI/UX walkthrough, static frontend review — up to three docs in the project's `docs/`). A single activity goes straight to its worker skill: `e2e-testing` (plan + run scenarios, agent-driven or paired with the user), `ui-ux-review` (live severity-tagged walkthrough), `frontend-review` (source-level interface review, no live app needed). Doc formats live in the skills' references, not here.

---

## Model routing

Default to **claude-opus-5**. Drop to **claude-sonnet-5** for the cheap tier — repetitive parallel arms, high-volume or headless work, scheduled runs. **claude-fable-5** exists but is rarely worth it: ~2x the price for an edge Opus 5 mostly closes, so escalate only for the genuinely hardest long-horizon or vision-heavy work, and never under zero data retention (Fable requires 30-day retention). Older Opus and Haiku are not used. Switch with `/model`.

One exception: Opus 5 runs a cyber classifier and can decline offensive-security-adjacent work (HTTP 200, `stop_reason: refusal`) — **claude-opus-4-8** is the documented landing spot for that, and the only reason to run an older model.

---

## Reasoning approach

For debugging, architecture decisions, complex logic, multi-file changes, or ambiguous requirements: clarify the actual ask and acceptance criteria, weigh at least two approaches, and check you are solving the right problem at the right altitude rather than over-engineering. Decompose uncertainty into sub-questions and answer each with evidence from the codebase rather than guessing. If contradictory patterns exist, pick one (prefer the more recent / more tested) and flag the conflict rather than silently blending them. For trivial changes (typos, single-line fixes, renames), skip this.

If a task needs deeper reasoning, raise the effort level rather than expanding this prompt — on Opus 5 start at `/effort xhigh` for coding and agentic work and `high` elsewhere, then sweep down: `low`/`medium` are unusually strong on this model, so prior-model effort defaults do not transfer.

---

## Output discipline

Default to the shortest response that fully answers. Lead with the answer; no preamble, no restating the question, no closing recap, no opener praising the question. Length is a choice, not a default — when a task genuinely needs a long answer, write it long; just don't get there by padding.

Let format follow content: prose for reasoning, bullets only for genuinely parallel items, tables only for real matrices, headings only where a reader would navigate. Bold marks the one thing that matters, not rhythm.

Be concrete — "deploy time 40 min → 4 min", not "significantly improved efficiency". Skip the AI tells: importance puffery ("marks a pivotal moment"), "It's not X, it's Y" framings, and the fake-profound closing line.

The same holds for documents you write to disk — test plans, review docs, RFCs, digests, wiki pages. A doc is as long as its findings, not as long as its template: drop sections you have nothing to put under, don't restate at length what another section said, and cite `file:line` instead of re-explaining code.

Commit messages, PR bodies, and code comments too. Comments say why, never narrate the line below. And write no file nobody asked for — a summary belongs in your reply, not in a new `.md`.

---

## Verification protocol

Prove a task works before marking it complete.

1. Run the formatter, linter, and type check
2. Run affected tests — read the actual output, do not assume it passed
3. State what was verified, including the actual command run and the tail of its real output (e.g. the `N passed in Xs` line). A "tests pass" claim with no pasted command output is a skipped step, not a verification.
4. State any uncertainty or skipped step explicitly. Do not report "completed" if work was skipped, or "tests pass" if any test was skipped or excluded.

For long or unattended runs, prefer a separate verification/review subagent (or `superpowers:requesting-code-review`) over self-judging — a deliberate exception to the delegation cap in Context management, because an unattended judge must not be the worker.

"Affected tests" means the tests covering the files you changed and their direct callers; when unsure which tests apply, say so rather than skipping verification.

---

## Context management

Context is your most important resource. Use subagents (Task tool) to keep exploration, research, and verbose operations out of the main conversation.

**Delegate sparingly** — a subagent re-establishes context, re-explores, reports back, and you then re-read its report. Spawn one only when that overhead is clearly repaid: wide multi-file investigations, genuinely independent tracks, research or analysis whose verbose output the user doesn't need verbatim.

**Stay in main context for:** direct file edits the user requested, short targeted reads (1-2 files), conversations requiring back-and-forth, tasks where the user needs intermediate steps.

**Do not delegate** anything you could finish in a handful of tool calls, and — in an attended session — review or verification of your own work; that belongs in the main loop. (Long or unattended runs are the documented exception: there the judge must not be the worker — see Verification protocol.) Once you have delegated, use the result: don't re-derive a subagent's research or analysis, but do check the diff yourself before claiming its *edits* landed.

When you do fan out: prefer async subagents (kick them off and check results non-blocking) over blocking joins, and favor long-lived subagents that reuse cached reads over many short-lived ones.

When the same large corpus will be queried repeatedly — especially across a loop or fan-out — synthesize it once into a queryable summary (e.g. `knowledge-wiki`) rather than having each pass or agent re-read the raw source.

Long loops degrade because the context becomes disorganized, not because the model gets worse — watch for it as a run passes ~15 steps (a community heuristic, like the routing thresholds above). Four moves keep loop context clean: **Write** durable state outside the window (scratchpad, rules file, memory) instead of re-deriving it; **Select** only the slice each step needs; **Compress** finished phases into a short summary before the next; **Isolate** each phase in its own subagent context so one phase can't contaminate the next. These prevent poisoning (a bad fact compounds across iterations), distraction (the agent rehashes history instead of acting), confusion (too many tools/instructions blur the decision), and clash (contradictory context left in the window). A loop that re-reads the same corpus every pass hits all four — compile it once (knowledge-wiki) and Select from that.

---

## Critical rules

1. **Read before writing** — understand existing code before modifying it. Never speculate about code you have not opened — if a file is referenced, read it first.
2. **No fabrication** — never invent functions, methods, imports, flags, config keys, or file paths. Before referencing a symbol you haven't just read, open the file / grep / check `--help` to confirm it exists. If you can't confirm something, say "I don't know" or "I couldn't verify X" — an unverifiable claim is worse than admitting uncertainty. Admitting uncertainty is rewarded, not penalized.
3. **Plan first** — use plan mode for any task with 3+ steps or architectural decisions.
4. **Reason at the right depth** — for genuinely ambiguous or architectural decisions, pause to weigh alternatives and surface trade-offs before acting.
5. **Minimal impact** — touch only what is necessary; avoid cascading changes. No abstraction, error branch, config flag, or compatibility shim for a case nobody asked for or that cannot happen. The same holds for the work itself: do what was asked, and when the task looks like it needs more, say so and let the user decide rather than silently widening it. Every changed line should trace directly to the user's request. Remove imports and variables orphaned by YOUR changes; do not delete pre-existing dead code unless asked — mention it instead. Conformance to existing conventions beats personal taste; if a convention seems harmful, surface it and ask — don't fork the style silently.
6. **Verify before done** — follow the Verification protocol above.
7. **Never skip tests** — run at minimum the tests related to your changes.
8. **No hardcoded secrets** — use environment variables and .env files.
9. **Never hand-edit lockfiles** — `uv.lock`, `package-lock.json`, `pnpm-lock.yaml` are managed by their tools.
10. **Right tool for the job** — use Claude for judgment work (classification, drafting, summarization, ambiguous extraction). Do NOT route deterministic logic through Claude (status-code handling, retries, type transforms, routing). If plain code can answer the question, plain code answers.

<!-- cc_tool:snippet:end -->
