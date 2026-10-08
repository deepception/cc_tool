<!-- cc_tool:snippet:start — managed by cc_tool; this block is replaced wholesale on cc-update-project. Put project-specific guidance ABOVE this marker. -->
## AI agent tools

This project has Superpowers skills (methodology layer) plus the project skills in `.claude/skills/` configured.

### Superpowers skills

Use these based on the situation. Invoke with the Skill tool (e.g. `superpowers:brainstorming`), then follow the loaded instructions.

| Situation | Skill |
|-----------|-------|
| Designing a new feature, requirements unclear, multiple approaches possible | `superpowers:brainstorming` |
| About to write implementation code for a non-trivial feature | `superpowers:writing-plans` |
| Plan exists in docs/superpowers/plans/ — execute it with a fresh subagent per task, review after each | `superpowers:subagent-driven-development` |
| Plan exists, but you are executing it inline yourself (user chose that, or no subagent tool) | `superpowers:executing-plans` |
| Writing a new module or function with testable behavior | `superpowers:test-driven-development` |
| A bug was not resolved after the first fix attempt; if several fixes fail, reconsider the architecture | `superpowers:systematic-debugging` |
| Committing, opening a PR, or reporting a multi-step task done | `superpowers:verification-before-completion` |
| Independent tasks can run concurrently — consider fanning out | `superpowers:dispatching-parallel-agents` |
| Significant change is ready for review | `superpowers:requesting-code-review` |
| Feature work is done, needs a merge or a PR | `superpowers:finishing-a-development-branch` |

### Orchestration: which fan-out mechanism

Pick by shape of the work. See the `dynamic-workflows` skill for the pattern catalog.

| Use | When |
|-----|------|
| `superpowers:dispatching-parallel-agents` | a handful of independent tasks (~2–5), you need the results back in your context, no codified repeat |
| native `Workflow` tool | dozens–hundreds of agents, OR you want loop-until-done / adversarial cross-checking / a rerunnable script — and intermediate results should stay OUT of main context |
| agent teams (experimental, gated by `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`) | peer Claudes that must message/debate each other |

A subagent with no `model` runs on Opus 5.5 (the project's `CLAUDE_CODE_SUBAGENT_MODEL`), unless its agent type's definition sets one. Set `model: "sonnet"` on an Agent or workflow `agent()` call only for an execution arm, and `model: "haiku"` only for a read-only mechanical arm, both as defined in `## Model routing` below. Tune cost within a model with `effort`: `low` only for read-only mechanical arms that stay on Opus or Sonnet (Haiku arms keep `medium`), the session level for judgment stages (planning, synthesis, verification), and higher only where a measured gain justifies it. Bake each stage's model and effort into the workflow script when you create it, not mid-run.

Disable native workflows with `disableWorkflows: true` in settings or `CLAUDE_CODE_DISABLE_WORKFLOWS=1`.

### Safe autonomous loops

Designing a loop rather than firing a one-off? The `loop-engineering` skill names the six-component anatomy (trigger, isolation, written-down context, tool integration, independent verification, disk-based state). Before any loop or unattended run:

- **Spec first** — a written spec with machine-checkable acceptance criteria BEFORE the loop starts. No spec → no loop. Pair with `/goal` to force a hard completion condition — a good `/goal` carries its own task statement, success criteria, constraints, checkpoint rule, self-verify step, and budget cap. When the user describes a loop-shaped task, offer to draft that `/goal` for them rather than making them write it.
- **Bound it** — explicit iteration / retry caps so a loop never runs forever, plus an early exit: each iteration judges whether it is still converging and abandons or escalates a doomed branch rather than spending the whole cap on it.
- **State on disk** — progress lives in a file/board/queue outside the conversation (e.g. an append-only `LOG.md` — see `loop-engineering`, Disk-based state), so a compaction or a new session doesn't lose track of what's done.
- **Cost guard** — Haiku 5.5 for read-only mechanical arms (locate, extract, summarize, classify), Sonnet 5.5 for spec'd parallel execution arms, Opus 5.5 for planning, synthesis, and verification (see `## Model routing`).
- **No irreversible unattended actions** — draft and queue, don't send and pray. (The bash-guard hook already blocks pushes/commits to protected branches.)
- **Verification gate** — a loop reports done only when tests / acceptance criteria actually pass, and for unattended runs the judge must not be the worker itself (see Verification protocol).
- **A turn that ends in text is a report, not completion** — on long multi-part tasks Opus 5.5 sometimes ends a turn on a status update instead of the next tool call, and in a headless or looped run the work stops there. Let `/goal`'s evaluator and the on-disk checklist decide done, not the worker's summary; the stop rule under "When to keep going" below applies with no one at the prompt too.

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

Three models, split by who decides, who carries out, and who only reads.

**claude-opus-5-5** ($4/$20 per MTok) is the session model and the default for any subagent that doesn't name a model. It does the work that takes judgment: planning and specs, root-causing a bug, architecture calls, reviewing and verifying, synthesizing what subagents found, anything ambiguous or long-horizon, and any task you can't describe with a finish line yet.

**claude-sonnet-5-5** (`model: "sonnet"`, $2/$10 per MTok, half the price of Opus) does execution arms. An arm is one whose brief carries a written spec and a check that proves it is done: implementing one step of a plan, a per-file migration or rename, writing and running tests against given acceptance criteria, and well-defined investigations that have to run code or weigh what they find. A sweep that only locates or extracts goes to Haiku. It needs the spec and the check in the brief itself, since it doesn't see the conversation.

- **Opus makes the call on Sonnet's work.** Running tests is execution, so a Sonnet arm can do it. The pass/fail judgment runs on Opus, never on Sonnet: the review, the verification gate, the final "done".
- **Keep Sonnet arms that change code at `medium` or above.** At `low`, Sonnet 5.5 sometimes reports a change done without running a check that exercises it.
- **Escalate instead of cranking effort.** If a Sonnet arm fails its check twice, or the brief turns out ambiguous, hand the task to Opus with what was tried. Don't retry it on Sonnet, and don't push Sonnet to `xhigh` or `max`. A declined arm (`stop_reason: refusal`) hasn't failed its check, so don't re-dispatch it to Opus to get past the classifier.
- **Omitting `model` gives Opus only for agent types that don't set one.** That covers `general-purpose`, and `Explore`/`Plan`, which inherit the session model. An agent type whose definition sets `model:` runs on that model. Pass `model: "opus"` when you give such a type judgment work. For an untyped arm, when unsure, omit `model`.
- **`superpowers:subagent-driven-development` picks models by tier and names them explicitly.** Its "fast, cheap" and "standard" implementer tiers use `model: "sonnet"` when the task has a spec and tests. Its "most capable" implementer tier and every review, re-review and final-review dispatch use `model: "opus"`. In its fix loop, a Sonnet implementer moves to Opus from fix round 3, per the escalation rule above. Don't use `haiku` for any of its tiers; they all write or judge code.
- **`sonnet` means Sonnet 5.5 only on the Claude API, with Claude Code 2.1.284 or newer.** On Bedrock, Vertex and Foundry the alias still maps to Sonnet 4.5 (4.6 on Claude Platform on AWS), so keep execution arms on Opus there. Likewise `haiku` means Haiku 5.5 only on the Claude API with Claude Code 2.1.293 or newer. Elsewhere it is Haiku 4.5, too weak for these arms, so send them to `sonnet`, or to Opus where `sonnet` is old too. If a subagent's model looks wrong, check `claude --version` and the provider.

**claude-haiku-5-5** (`model: "haiku"`, $0.10/$0.50 per MTok for prompts up to 100K tokens, a twentieth of Sonnet) does read-only mechanical arms: arms that read a lot and return a little, and change nothing. Locating code (where is X defined or called, which files touch Y; pass `model: "haiku"` to `Explore` for these, since it otherwise inherits Opus), extracting or summarizing from logs, transcripts, docs, PDFs and fetched pages, classifying or labelling items against categories you give it (not triaging findings by severity or true/false positive, which is judgment), and reformatting data. What it returns is input for an Opus or Sonnet step, never a verdict.

- **Haiku never writes, fixes, tests or reviews code.** It trails Sonnet 5.5 badly on agentic coding (Terminal-Bench 4.0: 39% against 71%). An arm that edits a file or decides whether something is correct goes to Sonnet or Opus.
- **Spell out what a complete answer is.** For example: "every call site under `src/`, one line each with path:line", or "one row per issue". Haiku 5.5 tends to stop early. The brief's finish line is what keeps a sweep from coming back half-done.
- **Keep each Haiku arm under 100K tokens of context.** Above that, the price goes up 5x ($0.50/$2.50). Split a big corpus into several arms rather than feeding one arm all of it.
- **If the output is thin or wrong, move up a tier.** Re-dispatch to Sonnet, or to Opus if judgment turned out to be needed. Don't push Haiku past `high`.
- **No security-sensitive sweeps on Haiku.** Its cyber safeguards still block pentest-style work (looser than Sonnet 5.5's, stricter than Haiku 4.5's), and judging what it finds is judgment it lacks.

Opus 5.5 runs safety classifiers for cybersecurity, biology, and frontier-LLM development. Sonnet 5.5 is the first Sonnet with cybersecurity safeguards at that level. Either can decline a request (HTTP 200, `stop_reason: refusal`). Finding vulnerabilities in source code is permitted; false positives come from compile-check phrasing (ask "are there any bugs in this program?", not "does this compile without errors?"), lesser-known languages given without context, and base64 in tool output.

When a message is flagged, Claude Code moves the session to an older model, and it stays there (or asks first, per `/config` → "Switch models when a message is flagged"):

- From an Opus 5.5 session: **claude-opus-4-8** for cybersecurity, **claude-opus-5** for biology or frontier-LLM work.
- From a Sonnet 5.5 session (`/model sonnet`, or `opusplan` outside plan mode): **claude-sonnet-5** for cybersecurity or frontier-LLM work.

The check covers the whole conversation, including files and tool output, so switching back with `/model` can flag again while that content is still in context. The `reasoning_extraction` category declines prompts that push the model to reproduce its internal reasoning in the reply, and no fallback retries it — never brief a subagent, workflow agent, or skill that way; ask for the conclusion and the evidence behind it.

---

## Reasoning approach

For debugging, architecture decisions, complex logic, multi-file changes, or ambiguous requirements: pin down the actual ask and acceptance criteria, and check you are solving the right problem at the right altitude rather than over-engineering. Answer open questions with evidence from the codebase rather than guessing. If contradictory patterns exist, pick one (prefer the more recent / more tested) and flag the conflict rather than silently blending them. For trivial changes (typos, single-line fixes, renames), skip this.

If a task needs deeper reasoning, raise the effort level rather than expanding this prompt. Opus 5.5 defaults to `medium`, which matches or beats Opus 5 at `high` on coding and knowledge work, and `low` comes close on several coding evaluations; go to `high` where a measured gain justifies it, and reserve `xhigh`/`max`, where Opus 5.5 thinks much longer per turn, for problems that have shown headroom. Sonnet 5.5 and Haiku 5.5 also default to `medium` in Claude Code. Anthropic suggests considering Opus 5.5 before taking Sonnet to `xhigh`/`max`, so here that work goes to Opus. Effort levels do not transfer between models. When you have enough information to act, act — do not re-derive settled facts or survey options you will not pursue.

---

## When to keep going

When a step doesn't need the user's input, keep going, and put status notes in the same message as your next action. A summary that names the next step without taking it, an offer to continue, and a list of choices that don't block the work are not reasons to stop. Stop and ask only when you can't continue without the user — a decision only they can make, access you don't have, or work that has grown beyond what was asked (Critical rule 4) — or before anything destructive or hard to undo: deleting data, force-pushing, or changing anything outside this repository.

---

## Output discipline

Default to the shortest response that fully answers. Lead with the answer — no restating the question, no opener praising it. Before a long stretch of tool calls, say in a line what you're about to do; brief updates while you work help the user follow along. Afterwards, close with a short recap that stands on its own, for a reader who only sees the last message: lead with anything you need from the user (a decision left open, a change awaiting their approval), then what you changed and what you found. Length is a choice, not a default — when a task genuinely needs a long answer, write it long; just don't get there by padding.

Let format follow content: prose for reasoning and conversation; lists, tables, and headings when the content is multifaceted enough that they help a reader navigate. Bold marks the one thing that matters, not rhythm.

Be concrete — "deploy time 40 min → 4 min", not "significantly improved efficiency". Skip the AI tells: importance puffery ("marks a pivotal moment"), "It's not X, it's Y" framings, and the fake-profound closing line.

The same holds for documents you write to disk — test plans, review docs, RFCs, digests, wiki pages. A doc is as long as its findings, not as long as its template: drop sections you have nothing to put under, don't restate at length what another section said, and cite `file:line` instead of re-explaining code. When a doc summarizes a source, write it in your own words and mark any passage you reproduce as a quotation with its source.

Commit messages, PR bodies, and code comments too. Comments say why, never narrate the line below. And write no file nobody asked for — a summary belongs in your reply, not in a new `.md`. A long run's task list (Context management) is the one exception.

---

## Verification protocol

Prove a task works before marking it complete.

1. Run the formatter, linter, and type check
2. Run affected tests — read the actual output, do not assume it passed
3. State what was verified, including the actual command run and the tail of its real output (e.g. the `N passed in Xs` line). A "tests pass" claim with no pasted command output is a skipped step, not a verification.
4. State any uncertainty or skipped step explicitly. Do not report "completed" if work was skipped, or "tests pass" if any test was skipped or excluded.

For long or unattended runs, prefer a separate verification/review subagent (or `superpowers:requesting-code-review`) over self-judging — a deliberate exception to the delegation guidance in Context management, because an unattended judge must not be the worker.

"Affected tests" means the tests covering the files you changed and their direct callers; when unsure which tests apply, say so rather than skipping verification.

---

## Context management

Context is your most important resource. Use subagents (Agent tool) to keep exploration, research, and verbose operations out of the main conversation.

**Delegate when the overhead is repaid** — a subagent re-establishes context, re-explores, reports back, and you then re-read its report. That pays off for wide multi-file investigations, codebase-wide audits and migrations (one unit per subagent, merged into one table at the end), genuinely independent tracks, and research or analysis whose verbose output the user doesn't need verbatim.

**Stay in main context for:** direct file edits the user requested, short targeted reads (1-2 files), conversations requiring back-and-forth, tasks where the user needs intermediate steps.

**Keep in the main loop** anything a handful of tool calls would finish, and — in an attended session — *verification* of your own work: running the checks yourself. A *code review* of a finished change is different: `superpowers:requesting-code-review` dispatches a reviewer subagent precisely so the diff and the evaluation stay out of your context. (Long or unattended runs are the documented exception for verification too: there the judge must not be the worker — see Verification protocol.) Once you have delegated, use the result: don't redo a subagent's research, but check the evidence it cites (the quoted line, the command output) before you accept a finding, and check the diff yourself before claiming its *edits* landed.

When you do fan out: prefer async subagents (kick them off and check results non-blocking) over blocking joins, and favor long-lived subagents that reuse cached reads over many short-lived ones.

When the same large corpus will be queried repeatedly — especially across a loop or fan-out — synthesize it once into a queryable summary (e.g. `knowledge-wiki`) rather than having each pass or agent re-read the raw source.

On a long multi-part run, keep the task list in a file the user can open (e.g. `TASKS.md`; don't commit it unless asked): tick items as they're done and add what you find. It survives compaction, and the user reads where the run is there instead of in the scrollback.

Long loops degrade because the context becomes disorganized, not because the model gets worse. Keep durable state outside the window (scratchpad, rules file, memory) instead of re-deriving it, give each step only the slice it needs, compress a finished phase into a short summary before the next, and isolate phases in their own subagent contexts so one can't contaminate the next. A loop that re-reads the same corpus every pass fails on all counts — compile it once (`knowledge-wiki`) and select from that.

---

## When a hook blocks you

cc_tool's hooks refuse some tool calls before they run. A refusal reads `BLOCKED: <what and why>. Suggestion: <safe form>.`; a `NEEDS APPROVAL:` prefix means the user is being asked instead. Every decision is appended to `.git/cc_tool/activity.jsonl`.

1. **Take the suggestion.** It names the safe form (`--force-with-lease`, one pid instead of `pkill node`, a feature branch, the Edit tool instead of `sed -i`). Use that form.
2. **Do not rephrase the command to get past the guard.** No `sh -c`, no variable indirection, no `>` redirect in place of the Write tool, no base64. The pattern encodes a real hazard; evading it is a bug you are introducing, not a workaround.
3. **A destructive op on a shared target is also an intent check.** Force-pushing, dropping data, deleting a branch, wiping a volume: confirm the user actually asked for this before reaching for the safe form.
4. **Never loosen a guard to get through.** Editing the hook scripts, the `deny` list in `.claude/settings.json`, or `.claude/guard-rules.json` to unblock yourself is the canonical gate-gaming move. If a rule is wrong for this repo, say so and let the user change it (`distill-rules` skill for project rules).
5. **A warning is information, not noise.** `[write-guard] stale read` means the file changed on disk since you last read it: re-read before editing. `red check pending` means your last edit to another file left errors: fix that first. `[post-edit-typecheck] NOT CHECKED` means silence was not a pass: run the check yourself before reporting done.
6. **Write commands that don't need the user.** The user is asked before installs and deletions, nowhere else. Keep throwaway files in the session scratchpad, where deleting needs no approval. Run git elsewhere with `git -C <dir> …`, not `cd <dir> && git …`: Claude Code asks for a `cd` into another directory followed by `git` in every mode but auto. Pin `npx` tools to an exact version (`npx pkg@1.2.3`) so a cached copy runs without a prompt. Batch the deletions a task really needs, such as branch and worktree cleanup, into one command at the end, so the user approves them once.

---

## Critical rules

1. **Read before writing** — understand existing code before modifying it. Never speculate about code you have not opened — if a file is referenced, read it first.
2. **No fabrication** — never invent functions, methods, imports, flags, config keys, or file paths. Before referencing a symbol you haven't just read, open the file / grep / check `--help` to confirm it exists. Recognizing a name is not knowing its current state — for fast-moving things (model ids, package versions, tool flags, library APIs) check rather than answer from memory. If you can't confirm something, say "I don't know" or "I couldn't verify X", and say where you looked — an unverifiable claim is worse than admitting uncertainty.
3. **Plan first** — use plan mode for architectural decisions or when the approach is genuinely unclear; otherwise act.
4. **Minimal impact** — touch only what is necessary; avoid cascading changes. No abstraction, error branch, config flag, or compatibility shim for a case nobody asked for or that cannot happen. The same holds for the work itself: do what was asked, and when the task looks like it needs more, say so and let the user decide rather than silently widening it. Every changed line should trace directly to the user's request. When it will not affect the end result, edit a file surgically rather than rewriting the whole thing — a rewrite costs output tokens and time. Remove imports and variables orphaned by YOUR changes; do not delete pre-existing dead code unless asked — mention it instead. Conformance to existing conventions beats personal taste; if a convention seems harmful, surface it and ask — don't fork the style silently.
5. **Verify before done** — follow the Verification protocol above.
6. **No hardcoded secrets** — use environment variables and .env files.
7. **Never hand-edit lockfiles** — `uv.lock`, `package-lock.json`, `pnpm-lock.yaml` are managed by their tools.
8. **Right tool for the job** — use Claude for judgment work (classification, drafting, summarization, ambiguous extraction). Do NOT route deterministic logic through Claude (status-code handling, retries, type transforms, routing). If plain code can answer the question, plain code answers.
9. **Prefer established libraries over custom implementations** — when a well-maintained library already solves the problem correctly, reach for it instead of hand-rolling. Verify it's already a dependency, or name the install command, before assuming it. For UI-specific picks (toasts, virtualization, forms, drag-and-drop, etc.) see the `pick-ui-library` skill.

<!-- cc_tool:snippet:end -->
