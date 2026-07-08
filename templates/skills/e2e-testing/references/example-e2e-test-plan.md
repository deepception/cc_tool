# Example: a filled-in E2E test plan

What `docs/E2E-TEST-PLAN.md` looks like mid-engagement for a fictional app ("taskboard", a FastAPI + React kanban). Plan approved, paired mode, P0 scope; §1–§2 executed, one bug found. Everything below the line is the document itself.

---

# taskboard — End-to-End Test Plan

Covers typical user interactions against the local dev stack
(`docker compose up` → backend `:8000` with `GET /health`, frontend `:5173`;
seeded roles per §0). Scenarios are written to be directly implementable in
Playwright; selectors prefer `data-testid` / ARIA roles.

Walkthrough of 2026-07-08 (paired mode, scope P0): executed scenarios marked
✅ (pass) / ❌ (bug found). Unmarked scenarios are specified but not yet exercised.

## §0 Conventions & fixtures

- Users: `admin/admin123` (full access), `member/member123` (no Settings page).
- Seeded data: workspace "Acme" with 2 boards, 3 columns each, 12 cards.
- Reset between specs: clear `localStorage` (auth token) + reload.
- Health probe before everything: `curl -fsS http://localhost:8000/health` →
  200 `{"status":"ok"}`. A non-200 stops the run.

## §1 Authentication

| # | Scenario | Steps | Expected |
|---|----------|-------|----------|
| 1.1 ✅ | Login happy path | Open `/login`, enter `admin`/`admin123`, submit | Redirect to `/boards`; nav shows Settings entry; token in `localStorage`; no console errors |
| 1.2 ✅ | Login wrong password | Enter `admin`/`wrongpass`, submit | Inline error in the card; stays on `/login`; no token stored |
| 1.3 | Session survives reload | Log in, reload | Still on `/boards`, no bounce to `/login` |
| 1.4 | Deep-link guard | Logged out, visit `/boards` directly | Redirect to `/login` |

## §2 Role-based access

| # | Scenario | Steps | Expected |
|---|----------|-------|----------|
| 2.1 ✅ | Member cannot see Settings | Log in as `member` | No Settings nav entry; direct visit `/settings` redirects |
| 2.2 ❌ | Member privileged request replay | As `member`, POST `/api/workspace/rename` directly (copy the admin request, swap the token) | **BUG (2026-07-08): returns 200 and renames the workspace.** Symptom: RBAC enforced only in the UI (hidden nav), not server-side. Evidence: `POST /api/workspace/rename` with member Bearer token → `200`, name changed for all users. Root cause (from source): `routes/workspace.py` checks authentication but not role. Suggested fix: role guard on all `/api/workspace/*` mutations → `403`. Regression test: member token on each admin-only endpoint gets 403 and no state change |

## §3 Board view & drag-drop

| # | Scenario | Steps | Expected |
|---|----------|-------|----------|
| 3.1 | Drag card across columns | Drag a card from "Todo" to "Done" | Card moves; persists across reload (one 2xx persistence request) |
| 3.2 | Concurrent delete (known quirk) | Tab A: open a card's modal. Tab B: delete that card. Tab A: save | Graceful 404/409 + modal closes and board refetches — never a 500 |

## §4 Cross-cutting

| # | Scenario | Steps | Expected |
|---|----------|-------|----------|
| 4.1 | Dark mode | Toggle on each page | All pages readable; preference persists |
| 4.2 | i18n completeness | Sweep all pages in PL | No English leaks; `dd.mm.rrrr` date placeholders |
| 4.3 | Backend down | Stop backend, navigate | Visible error states, no infinite spinners; recovery after restart |

## Priorities

**P0** — 1.1, 1.2, 1.4, 2.1, 2.2, 3.1. **P1** — 1.3, 3.2, §4.1–4.2. **P2** — 4.3.

## Suggested automation structure

    e2e/
    ├── fixtures.ts          # login per role, seed helpers, health probe
    ├── auth.spec.ts         # §1
    ├── rbac.spec.ts         # §2 (incl. regression for 2.2)
    ├── board.spec.ts        # §3
    └── cross-cutting.spec.ts# §4

This is a sketch for a future suite; generating it is a separate follow-up.

## Suggested fix order

1. 2.2 — server-side RBAC on workspace mutations (data-integrity bug, all P0 flows depend on it).
2. 3.2 — turn the known concurrent-delete 500 into a 404/409 with UI recovery.
