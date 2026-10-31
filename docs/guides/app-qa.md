[← README](../../README.md)

# App QA & e2e testing (one skill, `/app-qa`)

Point Claude at any app — web, API, CLI, TUI, mobile — and get the QA engagement as documents in the target project's `docs/`: an executable e2e test plan with walkthrough results, a severity-tagged UI/UX review, and a static frontend review that duplicates neither. It is one skill, `app-qa`, and you pick the activity with an argument.

```
# full engagement — in a Claude Code session inside the target project:
/app-qa                      # or /app-qa all; point it at one app: /app-qa apps/web

# what happens, in order:
#  1. multiselect: which deliverables (e2e / UI-UX review / frontend review)
#  2. discovery once: app type, launch method, roles/fixtures, driver availability
#  3. frontend review runs as a background subagent while live testing proceeds
#  4. the e2e activity writes docs/E2E-TEST-PLAN.md and stops for your approval
#  5. mode question: agent-run (Claude drives the app) or paired
#     (Claude gives you one scenario at a time, you act and report)
#  6. results recorded in the plan doc in place: ✅ pass / ❌ bug (with root
#     cause + suggested fix inline) / unmarked = not yet exercised
#  7. wrap-up: cross-doc summary, unified fix order, offer to scaffold the
#     automated Playwright/pytest suite as a follow-up

# single activities (an argument skips the orchestration steps):
/app-qa e2e                  # just the test plan + execution (same approval + mode gates)
/app-qa ux                   # 🔴🟡🔵 walkthrough of the live app; "what works (keep)" included
/app-qa frontend             # source-level interface review; no running app needed
```

**Upgrading.** `e2e-testing`, `ui-ux-review` and `frontend-review` were separate skills and are now merged into `app-qa`; their instructions are `app-qa/references/e2e.md`, `ux.md` and `frontend.md`. `/e2e-testing`, `/ui-ux-review` and `/frontend-review` no longer exist. `cc-setup` and `cc-update-project` remove the old project copies, but only when a copy is byte-identical to a version cc_tool once shipped; one you edited, added a file to, or replaced with your own skill of the same name stays, with a `Left untouched` line in the output. The pruning reads cc_tool's git history, so an install without `.git` leaves the copies in place. The skill's description still carries the old trigger phrases ("QA this app", "UI review", "frontend code review"), so asking in words works as before.

**Execution modes are offered honestly.** Agent-run needs a way to drive the app: a browser MCP for web (`claude mcp add playwright -- npx @playwright/mcp@latest`), vision-agent for mobile — plain Bash already covers API/CLI/TUI. With no driver for a GUI app, only paired mode is offered and Claude names what to install for next time; it never fakes test results from source reading, and never marks ✅ without an observed result.

**Re-running is the point.** The finished plan doc is a verifiable goal: the wrap-up points at `loop-engineering` for turning re-runs into a recurring regression sweep (`/loop`, `/schedule`, or a `/goal` on "all P0 scenarios pass"). Doc formats live in `app-qa/references/` (test-plan format, severity rubric, review dimensions, app-type driving-tools table); a complete worked example of the output — mid-walkthrough, with a real inline bug entry — ships at [templates/skills/app-qa/references/example-e2e-test-plan.md](../../templates/skills/app-qa/references/example-e2e-test-plan.md).
