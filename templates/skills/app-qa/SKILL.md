---
name: app-qa
description: >-
  QA for an app: e2e test plan and run (docs/E2E-TEST-PLAN.md), live UI/UX walkthrough
  (docs/UI-UX-REVIEW.md), static frontend source review (docs/FRONTEND-REVIEW.md), or all
  three. Use when the user asks to "QA this app", to e2e-test an app, for a UI review or
  UX walkthrough, or for a frontend code review. Pass e2e, ux, frontend or all.
user-invocable: true
argument-hint: "[e2e|ux|frontend|all]"
---

# App QA

Runs one QA activity, or the full engagement, over a target app. The how of each activity lives in `references/`; this file picks what to run and shares discovery so the app is explored once.

| Argument | Activity | Output | Instructions |
|----------|----------|--------|--------------|
| `e2e` | Plan and run end-to-end scenarios against the running app | `docs/E2E-TEST-PLAN.md` | [references/e2e.md](references/e2e.md) |
| `ux` | Live severity-tagged UI/UX walkthrough | `docs/UI-UX-REVIEW.md` | [references/ux.md](references/ux.md) |
| `frontend` | Static source review of the interface layer, no live app needed | `docs/FRONTEND-REVIEW.md` | [references/frontend.md](references/frontend.md) |
| `all` or none | The full engagement below | all three | all of the above |

With a single argument, read that activity's file and follow it as written; skip the orchestration steps. Without an argument, or for `all`, run the steps below.

## Steps

1. **Pick deliverables.** One AskUserQuestion, multiselect: e2e testing / UI-UX review / frontend review. Default to all that apply (frontend review needs source access; UI/UX review needs a user-facing surface).

2. **Discover once.** Do the intake and recon every activity needs, in the main conversation: app type, launch method, fixtures/roles, feature map, driver availability (per [references/driving-tools.md](references/driving-tools.md)). The activities that follow inherit this from context.

3. **Dispatch the static review in parallel.** If frontend review is selected: launch a background subagent (general-purpose) now. Its prompt contains: the discovery summary; the instruction to read `references/frontend.md` and `references/review-dimensions.md` and follow them; the paths of sibling docs that exist so far; the output path `docs/FRONTEND-REVIEW.md`. It works while live testing proceeds.

4. **Run the live work.** If e2e testing is selected, follow `references/e2e.md`. If UI-UX review is selected, follow `references/ux.md` as well: when e2e ran agent-run, note UI/UX observations as they surface during e2e execution and fold the gap sweep into the same live session, so one session over the app feeds both docs; otherwise run it as its own walkthrough (it picks its mode via its own step 2).

5. **Reconcile and summarize**
   - Collect the subagent's doc. If live findings landed after it dispatched, reconcile: dedupe favoring the live docs; the static doc keeps only findings that add a new root cause.
   - Post a cross-doc chat summary: bugs by severity across all docs, one unified fix order. Per-doc fix orders stay in their docs.
   - Each activity posts its own wrap-up as it finishes; fold those into the cross-doc summary rather than repeating them, and state the loop-engineering pointer once (it applies when e2e ran).

## Boundaries

- This file orchestrates; the how of each activity lives in its reference file, so don't restate or override its steps.
- Each activity runs on its own and never requires the full engagement.
