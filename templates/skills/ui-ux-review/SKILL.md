---
name: ui-ux-review
description: >-
  Reviews a running app's user experience by walking through every screen (or
  command/endpoint surface) and writing severity-tagged findings to
  docs/UI-UX-REVIEW.md. Use when the user asks for a UI review, UX walkthrough,
  design QA, or a "does the app feel right" pass on a live app.
user-invocable: true
argument-hint: [app-path-or-description]
---

# UI/UX Review

Your task is to review the running app's user experience surface by surface and write `docs/UI-UX-REVIEW.md` in the target project.

## Steps

1. **Baseline** — Establish and record the vantage point: profile/role, viewport, theme, locale, date. The review is a snapshot from a stated baseline, not a universal claim.

2. **Access the app** — Reuse the driver detection in `e2e-testing`'s [driving-tools reference](../e2e-testing/references/driving-tools.md) if installed alongside; otherwise probe ToolSearch for a browser/device driver. No driver → paired mode: the user walks, shares screenshots and observations, you record.

3. **Walk every surface** — Follow [references/review-rubric.md](references/review-rubric.md): global findings first, then one section per screen, each finding tagged 🔴/🟡/🔵. For CLI/API apps the rubric's adaptation table redefines "screen".

4. **Sweep beyond the happy path** — The rubric's checklist: both themes, narrow viewport, i18n leaks and diacritics, empty states, loading states, error states.

5. **Record what works** — A "What already works well (keep)" section is part of the doc: it protects good patterns from churn and keeps the review actionable.

6. **Close with fix order** — A short numbered list: severity first, cheap-and-visible next.

## Boundaries

- A functional bug found on the way (crash, data loss, logout trap) belongs in the e2e test plan — cross-reference it if one exists; otherwise record it here and suggest running `e2e-testing`.
- Root causes in source belong to `frontend-review`; here record the observed symptom.
