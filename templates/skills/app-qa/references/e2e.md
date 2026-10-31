# E2E Testing

Your task is to plan and execute an end-to-end test pass against a target app, producing `docs/E2E-TEST-PLAN.md` in the target project with results recorded in place.

## Steps

1. **Intake**: Identify the target app; ask which one if the workspace holds several. Determine:
   - App type: web SPA / server-rendered / API / CLI / TUI / mobile.
   - How to launch it locally: README, a project `run` skill, docker-compose, Makefile, package scripts.
   - Fixtures: seeded users and roles, test data, how to reset state between scenarios.
   These become §0 of the plan doc.

2. **Detect driving tools**: Follow [references/driving-tools.md](driving-tools.md): probe with ToolSearch for a driver matching the app type. The result decides which execution modes exist. With no driver for a GUI app, only paired mode can be offered. Say so plainly and name what to install for agent-run next time.

3. **Recon and plan**: Explore the code: routes/pages/commands, roles and permissions, feature areas, auth model, persistence, i18n, external integrations. Write `docs/E2E-TEST-PLAN.md` following [references/test-plan-format.md](test-plan-format.md). Stop and get the user's approval of the plan before executing anything.

4. **Ask mode and scope**: One AskUserQuestion call, two questions:
   - Mode: **agent-run** (you drive the app yourself) or **paired** (you instruct step by step, the user acts and reports). Offer agent-run only if step 2 found an execution path: an MCP driver for GUI/mobile apps, or plain Bash (`curl`, CLI invocation, tmux) for API/CLI/TUI targets.
   - Scope: all scenarios / P0 only / named sections.

5. **Execute**: Both modes record into the plan doc in place: ✅ on observed pass, ❌ with an inline bug entry on failure, unmarked = not exercised. Mark ✅ only after observing the expected result. An assumed pass stays unmarked.
   - Bug entries follow the inline bug-entry rules in [references/test-plan-format.md](test-plan-format.md) (symptom → evidence → root cause → fix → regression note).
   - Agent-run: launch and health-check the stack first; run scenarios in priority order; on selector/timing flakiness retry once, then mark the scenario blocked with a note.
   - Paired: one scenario per exchange. Give the steps and expected result, ask pass / fail (describe) / skip / stop (AskUserQuestion), record, adapt. A reported anomaly may spawn follow-up scenarios; add them to the doc.

6. **Wrap up**
   - Chat summary: counts (pass/fail/unrun), bugs by severity, what remains.
   - Append a suggested fix order to the doc.
   - Offer as a follow-up: scaffolding the automated suite from the plan (per the doc's suggested-structure section).
   - Point at recurrence: the plan is now a verifiable goal, and the `loop-engineering` skill covers turning re-runs into a recurring regression sweep (`/loop`, `/schedule`, or a `/goal` on "all P0 scenarios pass").

## Boundaries

- UI/UX findings met on the way (styling, copy, layout) belong in a UX review doc (`/app-qa ux`). Note them briefly and suggest it; don't grow this plan with them.
- A single bug entry's root cause stays here; broader source-level review belongs to the frontend review (`/app-qa frontend`), whose scope is the interface layer of any app type.
- Generating the automated suite is a follow-up on request, not part of this task.
