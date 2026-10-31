# UI/UX Review

Your task is to review the running app's user experience surface by surface and write `docs/UI-UX-REVIEW.md` in the target project.

## Steps

1. **Baseline.** Establish and record the vantage point: profile/role, viewport, theme, locale, date. The review is a snapshot from a stated baseline, not a universal claim.

2. **Access the app.** Reuse the driver detection in the [driving-tools reference](driving-tools.md); otherwise probe ToolSearch for a browser/device driver. No driver → paired mode: the user walks, shares screenshots and observations, you record.

3. **Walk every surface.** Follow [references/review-rubric.md](review-rubric.md): global findings first, then one section per screen, each finding tagged 🔴/🟡/🔵. For CLI/API apps the rubric's adaptation table redefines "screen".

4. **Sweep beyond the happy path.** The rubric's checklist: both themes, narrow viewport, i18n leaks and diacritics, empty states, loading states, error states.

5. **Record what works.** A "What already works well (keep)" section is part of the doc: it protects good patterns from churn and keeps the review actionable.

6. **Close with fix order.** A short numbered list: severity first, cheap-and-visible next.

## Boundaries

- A functional bug found on the way (crash, data loss, logout trap) belongs in the e2e test plan. Cross-reference it if one exists; otherwise record it here and suggest running `/app-qa e2e`.
- Root causes in source belong to the frontend review (`/app-qa frontend`); here record the observed symptom.
