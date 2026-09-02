[← README](../../README.md)

# App QA & e2e testing (app-qa + three workers)

Point Claude at any app — web, API, CLI, TUI, mobile — and get the QA engagement as documents in the target project's `docs/`: an executable e2e test plan with walkthrough results, a severity-tagged UI/UX review, and a static frontend review that duplicates neither.

```
# full engagement — in a Claude Code session inside the target project:
/app-qa                      # or point it at one app: /app-qa apps/web

# what happens, in order:
#  1. multiselect: which deliverables (e2e / UI-UX review / frontend review)
#  2. discovery once: app type, launch method, roles/fixtures, driver availability
#  3. frontend review runs as a background subagent while live testing proceeds
#  4. e2e-testing writes docs/E2E-TEST-PLAN.md and stops for your approval
#  5. mode question: agent-run (Claude drives the app) or paired
#     (Claude gives you one scenario at a time, you act and report)
#  6. results recorded in the plan doc in place: ✅ pass / ❌ bug (with root
#     cause + suggested fix inline) / unmarked = not yet exercised
#  7. wrap-up: cross-doc summary, unified fix order, offer to scaffold the
#     automated Playwright/pytest suite as a follow-up

# single activities (each works standalone, no orchestrator needed):
/e2e-testing                 # just the test plan + execution (same approval + mode gates)
/ui-ux-review                # 🔴🟡🔵 walkthrough of the live app; "what works (keep)" included
/frontend-review             # source-level interface review; no running app needed
```

**Execution modes are offered honestly.** Agent-run needs a way to drive the app: a browser MCP for web (`claude mcp add playwright -- npx @playwright/mcp@latest`), vision-agent for mobile — plain Bash already covers API/CLI/TUI. With no driver for a GUI app, only paired mode is offered and Claude names what to install for next time; it never fakes test results from source reading, and never marks ✅ without an observed result.

**Re-running is the point.** The finished plan doc is a verifiable goal: the wrap-up points at `loop-engineering` for turning re-runs into a recurring regression sweep (`/loop`, `/schedule`, or a `/goal` on "all P0 scenarios pass"). Doc formats live in the skills' `references/` files (test-plan format, severity rubric, review dimensions, app-type driving-tools table); a complete worked example of the output — mid-walkthrough, with a real inline bug entry — ships at [templates/skills/e2e-testing/references/example-e2e-test-plan.md](../../templates/skills/e2e-testing/references/example-e2e-test-plan.md).
