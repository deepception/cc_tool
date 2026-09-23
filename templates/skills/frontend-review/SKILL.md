---
name: frontend-review
description: >-
  Static source review of an app's interface layer — API client and auth plumbing,
  state management, component quality, i18n, type safety, accessibility, test
  coverage, build hygiene, motion and interaction feel, untrusted content and
  client-side security — written to docs/FRONTEND-REVIEW.md. Use when the user asks
  for a frontend code review or a source-level complement to live testing. Runs
  without the app running.
user-invocable: true
argument-hint: [src-path]
---

<!-- The coverage line, the contradiction-only drop rule, root-cause grouping, the
     verbatim anchor in the finding format, and several checks in dimensions 4, 5, 6,
     8 and 10 are adapted from alibaba/open-code-review (Apache-2.0, (c) 2026 Alibaba):
     chiefly its review-filter and scan prompts and its per-file-type rule docs.
     Rewritten for this skill; no text reproduced. -->

# Frontend Review

Your task is to review the interface layer's source and write `docs/FRONTEND-REVIEW.md` in the target project. No live app needed — this review reads code, and is safe to run as a background subagent alongside live testing.

## Contract

Two rules shape every finding:

1. **No duplication.** Read sibling docs first (`docs/E2E-TEST-PLAN.md`, `docs/UI-UX-REVIEW.md`) if they exist. A finding already catalogued there appears here only when this review adds a new, distinct root cause — and the doc header states this contract, naming the docs it complements.
2. **Honesty about limits.** Findings that can't be verified from source alone (contrast ratios, focus behavior, whether a bug reproduces live) go in a closing "Needs live confirmation" section, not asserted as fact. The same goes for coverage: the header says how much of the scope was read in full, sampled, or skipped and why, because a review that quietly sampled a large `src/` reads as a clean bill of health for the files it never opened.

## Steps

1. **Scope** — The interface layer is whatever the user touches: a React/Vue `src/`, a CLI's arg-parsing and output modules, an API's route handlers and serializers. Name the scope in the doc header. Leave generated and vendored code out of findings (codegen'd API clients, `*.generated.*`, snapshots, build output) and name what was excluded; the fix for those belongs in the generator or upstream. Read what the app is meant to do before judging it — the project's spec or plan docs, or the discovery summary `app-qa` passes — so a stub the plan already schedules is reported as planned rather than rated as a defect.

2. **Review by dimension** — Work through [references/review-dimensions.md](references/review-dimensions.md). Skip dimensions that don't apply to the app type and say which were skipped. The bullets mark where defects usually hide, not the edge of the review: a defect that fits no bullet goes under the closest dimension. Conventions the project documents (CLAUDE.md, CONTRIBUTING, lint config) outrank a generic bullet, since a deliberate, documented choice is not a defect. Read across files wherever one contract spans several — client types against the server's serializers, a shared hook against its callers, locale files as a set.

3. **Verify each finding at its citation** — Every finding carries `file:line`. Re-read the cited lines before writing the finding. Drop it only when the code contradicts it: what it describes isn't there, or a line there shows the opposite. A finding that source alone can't confirm moves to "Needs live confirmation" instead, and a real problem cited at the wrong line gets its citation corrected. The asymmetry is deliberate: a doubtful finding costs the reader a minute, while a wrongly dropped one is a defect nobody hears about.

4. **Map test coverage** — The three-column table from the dimensions reference: covered directly / indirectly / not at all. Tie coverage gaps to bugs found in other dimensions where the gap explains them.

5. **Close** — Findings in the reference's finding format, severity-tagged throughout (same 🔴/🟡/🔵 rubric as `ui-ux-review`), a coverage-gaps summary, a suggested fix order (the same close `ui-ux-review` uses, which `app-qa` merges into its cross-doc order), and the "Needs live confirmation" list.
