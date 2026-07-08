---
name: app-qa
description: >-
  Runs a full QA engagement for an app: e2e test plan and execution, UI/UX walkthrough
  review, and static frontend review — up to three docs in the project's docs/.
  Use when the user asks to "QA this app", "test and review the app", or wants
  the full treatment; for a single activity invoke e2e-testing, ui-ux-review, or
  frontend-review directly.
user-invocable: true
argument-hint: [app-path-or-description]
---

# App QA

Your task is to run a full QA engagement over a target app by orchestrating three worker skills — `e2e-testing`, `ui-ux-review`, `frontend-review` — sharing discovery so the app is explored once.

## Steps

1. **Pick deliverables** — One AskUserQuestion, multiselect: e2e testing / UI-UX review / frontend review. Default to all that apply (frontend review needs source access; UI/UX review needs a user-facing surface).

2. **Discover once** — Do the intake and recon every worker needs, in the main conversation: app type, launch method, fixtures/roles, feature map, driver availability (per `e2e-testing`'s driving-tools reference). Workers invoked afterwards inherit this from context.

3. **Dispatch the static review in parallel** — If frontend review is selected: launch a background subagent (general-purpose) now. Its prompt contains: the discovery summary; the instruction to read `frontend-review`'s SKILL.md and references and follow them; the paths of sibling docs that exist so far; the output path `docs/FRONTEND-REVIEW.md`. It works while live testing proceeds.

4. **Run the live work** — If e2e testing is selected, invoke the `e2e-testing` skill (Skill tool). If UI-UX review is selected, invoke `ui-ux-review` as well: when e2e ran agent-run, note UI/UX observations as they surface during e2e execution and fold the gap sweep into the same live session — one session over the app feeds both docs; otherwise run it as its own walkthrough (it picks its mode via its own step 2).

5. **Reconcile and summarize**
   - Collect the subagent's doc. If live findings landed after it dispatched, reconcile: dedupe favoring the live docs; the static doc keeps only findings that add a new root cause.
   - Post a cross-doc chat summary: bugs by severity across all docs, one unified fix order. Per-doc fix orders stay in their docs.
   - Workers post their own wrap-ups as they finish; fold those into the cross-doc summary rather than repeating them, and state the loop-engineering pointer once (it applies when e2e ran).

## Boundaries

- This skill orchestrates; the how of each activity lives in the worker skills — don't restate or override their steps.
- Workers are independently invocable and never require this orchestrator.
