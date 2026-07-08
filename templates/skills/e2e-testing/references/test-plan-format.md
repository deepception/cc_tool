# E2E Test Plan — Document Format

Template for `docs/E2E-TEST-PLAN.md`. Sections in order; name scenario sections after the app's own feature areas. A complete worked example (mid-walkthrough state, one inline bug entry): [example-e2e-test-plan.md](example-e2e-test-plan.md).

## Header

State what the plan runs against, the marker legend, and the walkthrough date:

    # <App> — End-to-End Test Plan

    Covers typical user interactions against <stack description>
    (<launch commands>, <seeded users/fixtures source>).
    Scenarios are written to be directly implementable in <Playwright/pytest/…>;
    selectors prefer `data-testid` / roles where the project has conventions.

    Walkthrough of <date>: executed scenarios marked ✅ (pass) / ❌ (bug found).
    Unmarked scenarios are specified but not yet exercised.

## §0 Conventions & fixtures

Credentials per role, seeded entities with names and counts, the reset mechanism between specs (e.g. clear `localStorage` + reload), and a health probe run before everything (e.g. `GET /health`) so a dead backend fails fast instead of cascading.

## Scenario sections

One numbered section per feature area — auth first, role-based access second, then each major screen/command group, cross-cutting last. Each section is a table with hierarchical ids:

    | # | Scenario | Steps | Expected |
    |---|----------|-------|----------|
    | 1.1 ✅ | Login happy path | Open `/login`, fill valid credentials, submit | Redirect to `/boards`; header shows role chip; token persisted |
    | 1.2 | Login wrong password | Submit bad credentials | Inline error; stays on `/login`; no token stored |
    | 7.5 ❌ | Raw config tab | Click 4th tab | **BUG (<date>): user is logged out.** `GET /api/v1/config` → 307 → re-request drops the Bearer header → 401 → global handler force-logs-out. Fix: call the trailing-slash path in `services/api.ts`; the 401 handler should distinguish "endpoint failed" from "session invalid". Regression test: visiting the tab keeps the session |

Rules:
- Steps are executable by someone who has never seen the app: name the route, the control, the input value.
- Expected states observable outcomes, not intentions ("token cleared; back-button does not restore session").
- A ❌ carries its bug entry inline: symptom → evidence → root cause when diagnosable from source → suggested fix → regression-test note. Root cause is best-effort; an undiagnosed ❌ with good evidence is still a valid entry.

## Cross-cutting section

Pick what applies:
- GUI: dark/light theme, viewport ≤1280px, slow network (skeletons present?), backend down (error states, recovery), i18n completeness, multi-tenant isolation.
- API: authn/authz per endpoint, malformed payloads, pagination limits, concurrent writes, idempotency.
- CLI/TUI: bad flags, empty stdin, no-TTY invocation, exit codes, `--help` accuracy, interrupted runs.

## Priorities

Close the plan with tiers: **P0** — the flows that make the app worth shipping (login, the core object's happy path, regressions for data-destroying bugs); **P1** — the rest of the main features; **P2** — live-event edge cases and cross-cutting sweeps.

## Suggested automation structure

Sketch the suite the plan maps to — a file per section plus a fixtures module:

    e2e/
    ├── fixtures.ts          # login per role, seed helpers, health probe
    ├── auth.spec.ts         # §1, §2
    └── <area>.spec.ts       # §N

This is a sketch for a future suite; generating it is a separate follow-up.

## Suggested fix order

Appended at wrap-up, not authored with the plan: a short numbered list ordering the walkthrough's ❌ findings — user-blocking bugs first, then cheap-and-visible fixes.
