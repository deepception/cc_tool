---
name: frontend-review
description: >-
  Static source review of an app's interface layer — API client and auth plumbing,
  state management, component quality, i18n, type safety, accessibility, test
  coverage, build hygiene — written to docs/FRONTEND-REVIEW.md. Use when the user
  asks for a frontend code review or a source-level complement to live testing.
  Runs without the app running.
user-invocable: true
argument-hint: [src-path]
---

# Frontend Review

Your task is to review the interface layer's source and write `docs/FRONTEND-REVIEW.md` in the target project. No live app needed — this review reads code, and is safe to run as a background subagent alongside live testing.

## Contract

Two rules shape every finding:

1. **No duplication.** Read sibling docs first (`docs/E2E-TEST-PLAN.md`, `docs/UI-UX-REVIEW.md`) if they exist. A finding already catalogued there appears here only when this review adds a new, distinct root cause — and the doc header states this contract, naming the docs it complements.
2. **Honesty about limits.** Findings that can't be verified from source alone (contrast ratios, focus behavior, whether a bug reproduces live) go in a closing "Needs live confirmation" section, not asserted as fact.

## Steps

1. **Scope** — The interface layer is whatever the user touches: a React/Vue `src/`, a CLI's arg-parsing and output modules, an API's route handlers and serializers. Name the scope in the doc header.

2. **Review by dimension** — Work through [references/review-dimensions.md](references/review-dimensions.md). Skip dimensions that don't apply to the app type and say which were skipped.

3. **Verify each finding at its citation** — Every finding carries `file:line`. Re-read the cited lines before writing the finding; a claim that doesn't reproduce at its citation is dropped.

4. **Map test coverage** — The three-column table from the dimensions reference: covered directly / indirectly / not at all. Tie coverage gaps to bugs found in other dimensions where the gap explains them.

5. **Close** — Severity-tagged findings throughout (same 🔴/🟡/🔵 rubric as `ui-ux-review`), a coverage-gaps summary, and the "Needs live confirmation" list.
