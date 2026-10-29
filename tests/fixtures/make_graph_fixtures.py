#!/usr/bin/env python3
"""Writes tests/fixtures/graph-poll1.json and graph-poll2.json: the Factory floor's GET /api/graph payload
(design doc §5.7), shaped by graph.py's rules (finish() below), for tests/test_cc_dash_factory_ui.py.

poll 1: 3 projects (cc_tool needs you, reports-app works its factory orders, valhalla-strategy is your
turn), 5 sessions, 20 agents nested up to three deep, one factory-line workflow running FO-003 (plan and
build done, test running), orders FO-002 (review), FO-003 (building) and FO-004 (spec gate waiting), one
permission alert, the activity feed. Every KPI is computed from the nodes, so the figures add up.
poll 2, three seconds later: states change in place, two agents arrive (Lead B's cohesion pass,
FO-003:verify), the long-finished Haiku explorer drops off, the permission was granted and a question
opened, FO-003 moves to verifying.
Both polls carry `series` (graph.py's last-hour buckets, Lead U): 20 buckets of 180 s, clock-aligned to NOW, a
floor that ramped up over the hour (Opus / Sonnet / Haiku tokens, tool calls with a few errors, 1..17 working
agents, 24 distinct in all), the last bucket still filling. Poll 2 has the same start, its last bucket a little
higher and one more agent.

Run:  python3 tests/fixtures/make_graph_fixtures.py [out dir]   (default: this file's directory)
"""
import copy
import hashlib
import json
import os
import sys

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
NOW = 1761400000.0

S1 = "7d1c0a52-1111-4a8e-9c3b-2f6a0e9b1001"   # cc_tool: the factory build
S6 = "7d1c0a52-6666-4f21-8d0e-91c3b7a26006"   # cc_tool: release notes
S3 = "3b9e47f0-3333-4c2d-b6a1-5e08d2f43003"   # reports-app: factory line for FO-003
S4 = "3b9e47f0-4444-4a7b-a3c9-0d1f6e2b4004"   # reports-app: FO-002 review lane
S5 = "c04f19d2-5555-4e6a-9b8c-7a3d5f10e005"   # valhalla-strategy
RUN = "wf_7f3a2c91"

FAM = {"opus": "Opus 5.5", "sonnet": "Sonnet 5.5", "haiku": "Haiku 5.5"}
RUNNING = ("tool", "generating", "waiting")


def spark(*v):
    v = list(v)
    return [0] * (15 - len(v)) + v


def burn(cost_min, tok_min):
    """graph.py's five burn keys; new tokens are about a twentieth of tok_min (the rest is cache reads)."""
    new = int(tok_min * 0.05)
    return {"cost_min": cost_min, "tok_min": tok_min, "new_min": new, "out_min": int(new * 0.6), "unpriced_min": 0}


def session(sid, project, label, fam, effort, state, cost, tokens, b, tool="", target="", last_text="", since=0.0,
            start=0.0, age=1.0, sp=None, errors=0, detail=None, needs=None):
    d = {"session": sid, "project": project, "worktree": "", "branch": "master", "cwd": "~/Files/hobby/" + project,
         "prompts": 0, "tool_calls": 0, "tool_errors": 0, "api_errors": 0,
         "usage": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
         "guard": {"deny": 0, "ask": 0, "warn": 0}, "subagents": 0, "running": 0, "plan": None}
    d.update(detail or {})
    return {"id": "s:" + sid, "kind": "session", "label": label, "parent": "p:" + project, "state": state,
            "needs": needs, "model": FAM[fam], "family": fam, "effort": effort, "effort_source": "recorded",
            "cost": cost, "tokens": tokens, "burn": b, "errors": errors, "tool": tool, "target": target,
            "last_text": last_text, "since": NOW - since, "start": NOW - start, "age": age, "spark": sp or spark(),
            "detail": d}


def agent(sid, aid, parent, label, fam, effort, state, cost, tokens, b, tool="", target="", last_text="", since=0.0,
          start=0.0, age=1.0, sp=None, errors=0, typ="general-purpose", kind="sub", phase="", run_id="",
          background=False, waiting_on=0, stalled_for=None, tool_calls=0, order="", needs=None, src="requested"):
    return {"id": "a:%s/%s" % (sid, aid), "kind": "agent", "parent": parent, "label": label, "state": state,
            "needs": needs, "model": FAM[fam], "family": fam, "effort": effort, "effort_source": src if effort else "",
            "cost": cost, "tokens": tokens, "burn": b, "errors": errors, "tool": tool, "target": target,
            "last_text": last_text, "since": NOW - since, "start": NOW - start, "age": age, "spark": sp or spark(),
            "detail": {"session": sid, "agent_id": aid, "type": typ, "kind": kind, "phase": phase, "run_id": run_id,
                       "background": background, "waiting_on": waiting_on, "stalled_for": stalled_for,
                       "tool_calls": tool_calls, "order": order}}


def stations(statuses, times):
    names = [("plan", "Plan"), ("build", "Build"), ("test", "Test"), ("verify", "Verify"), ("review", "Review")]
    return [{"name": n, "title": t, "status": s, "t": (NOW - times[i]) if times[i] is not None else None}
            for i, ((n, t), s) in enumerate(zip(names, statuses))]


def order(project, oid, slug, title, prio, status, station, sts, times, gate=None, lane=None, run_id="", rework=0,
          events=0, created=0.0, updated=0.0):
    return {"id": "o:%s/%s" % (project, oid), "kind": "order", "parent": "p:" + project, "label": title,
            "state": status,
            "detail": {"id": oid, "title": title, "priority": prio, "status": status,
                       "path": "factory/orders/%s-%s.md" % (oid, slug), "created": NOW - created,
                       "updated": NOW - updated, "station": station, "stations": stations(sts, times),
                       "gate": gate, "lane": lane, "run_id": run_id, "rework": rework, "events": events}}


def poll1():
    nodes = []
    # ── projects ─────────────────────────────────────────────────────────
    nodes.append({"id": "p:cc_tool", "kind": "project", "label": "cc_tool", "parent": None, "state": "needs",
                  "needs": None, "cost": 41.70, "tokens": 9000000,
                  "detail": {"root": "~/Files/hobby/cc_tool", "sessions_live": 2, "sessions_window": 31,
                             "cost_window": 41.70, "tokens_window": 9000000,
                             "model_mix": [{"family": "opus", "model": "Opus 5.5", "tokens": 7000000, "cost": 35.10},
                                           {"family": "sonnet", "model": "Sonnet 5.5", "tokens": 1600000, "cost": 5.40},
                                           {"family": "haiku", "model": "Haiku 5.5", "tokens": 400000, "cost": 1.20}],
                             "guard": {"deny": 2, "ask": 1, "warn": 4}, "errors": 3,
                             "plans": [{"key": "docs/superpowers/plans/2026-10-25-software-factory.md",
                                        "title": "Software factory experiment", "status": "live", "done": 4, "total": 11},
                                       {"key": "docs/superpowers/plans/2026-10-18-cc-dash-alerts.md",
                                        "title": "cc-dash alerts drawer", "status": "done", "done": 7, "total": 7}],
                             "orders": {}}})
    nodes.append({"id": "p:reports-app", "kind": "project", "label": "reports-app", "parent": None, "state": "working",
                  "needs": None, "cost": 9.85, "tokens": 2300000,
                  "detail": {"root": "~/Files/work/reports-app", "sessions_live": 2, "sessions_window": 9,
                             "cost_window": 9.85, "tokens_window": 2300000,
                             "model_mix": [{"family": "opus", "model": "Opus 5.5", "tokens": 1400000, "cost": 6.90},
                                           {"family": "sonnet", "model": "Sonnet 5.5", "tokens": 900000, "cost": 2.95}],
                             "guard": {"deny": 0, "ask": 1, "warn": 1}, "errors": 1,
                             "plans": [{"key": "docs/superpowers/plans/2026-10-24-csv-export.md",
                                        "title": "CSV export for reports", "status": "live", "done": 2, "total": 5}],
                             "orders": {"spec": 1, "building": 1, "review": 1}}})
    nodes.append({"id": "p:valhalla-strategy", "kind": "project", "label": "valhalla-strategy", "parent": None,
                  "state": "idle", "needs": None, "cost": 6.20, "tokens": 1700000,
                  "detail": {"root": "~/Files/hobby/valhalla/valhalla-strategy", "sessions_live": 1,
                             "sessions_window": 6, "cost_window": 6.20, "tokens_window": 1700000,
                             "model_mix": [{"family": "sonnet", "model": "Sonnet 5.5", "tokens": 1250000, "cost": 4.10},
                                           {"family": "opus", "model": "Opus 5.5", "tokens": 330000, "cost": 1.80},
                                           {"family": "haiku", "model": "Haiku 5.5", "tokens": 120000, "cost": 0.30}],
                             "guard": {"deny": 1, "ask": 0, "warn": 0}, "errors": 4, "plans": [], "orders": {}}})

    # ── cc_tool: S1, the factory build (3 leads, 10 workers, one nested 3 deep) ──
    nodes.append(session(S1, "cc_tool", "Build the software factory experiment", "opus", "high", "waiting", 8.42, 2310000,
                         burn(0.21, 70000), tool="Agent", target="Lead B: factory floor UI",
                         last_text="Three leads are running in their own worktrees; I will merge C, then A, then B.",
                         since=840, start=5400, age=2.0, sp=spark(4, 6, 3, 2, 5, 8, 2, 1, 0, 3, 1, 0, 2, 1, 1),
                         detail={"worktree": "", "branch": "experiment/software-factory", "cwd": "~/Files/hobby/cc_tool",
                                 "prompts": 6, "tool_calls": 318, "tool_errors": 2, "api_errors": 0,
                                 "usage": {"input": 41200, "output": 168400, "cache_read": 18450000, "cache_write": 2100400},
                                 "guard": {"deny": 1, "ask": 1, "warn": 2}, "subagents": 12, "running": 9,
                                 "plan": {"title": "Software factory experiment", "done": 4, "total": 11, "status": "live"}},
                         errors=2))
    p = "s:" + S1
    A = lambda aid: "a:%s/%s" % (S1, aid)  # noqa: E731
    nodes += [
        agent(S1, "a4e1c07b9d2f3a10", p, "Lead A: graph API backend", "opus", "high", "waiting", 1.20, 310000,
              burn(0.01, 2500), tool="Agent", target="Graph API tests", since=420, start=4700, age=3.0,
              last_text="Payload shape is frozen; waiting on the test worker before I commit.",
              sp=spark(2, 4, 6, 3, 1, 0, 0, 1, 0, 0, 2, 0, 0, 1, 0), waiting_on=2, tool_calls=64),
        agent(S1, "a91f02d6c3b8e4a7", A("a4e1c07b9d2f3a10"), "Write the graph.py payload builder", "sonnet", "medium",
              "tool", 0.42, 160000, burn(0.04, 12000), tool="Edit", target="lib/cc_dash/graph.py", since=6, start=3900,
              age=1.0, last_text="Joining orders to workflow runs by ledger run_id, then by agent label.",
              sp=spark(3, 5, 7, 6, 8, 9, 7, 6, 8, 10, 9, 7, 8, 9, 4), tool_calls=142),
        agent(S1, "a2c7d8e90f1b3c45", A("a4e1c07b9d2f3a10"), "Graph API tests", "sonnet", "", "tool", 0.18, 72000,
              burn(0.01, 4000), tool="Bash", target="python3 tests/test_cc_dash_graph.py", since=11, start=2600,
              age=2.0, last_text="Ids stay stable across two polls; checking latency with 50 transcripts.",
              sp=spark(0, 0, 1, 2, 4, 3, 5, 4, 2, 3, 4, 5, 3, 4, 2), tool_calls=58),
        agent(S1, "a0b3f5c6d7e81922", A("a4e1c07b9d2f3a10"), "Look up the store.py board helpers", "haiku", "", "done",
              0.04, 41000, burn(0, 0), since=95, start=4500, age=95.0, typ="Explore",
              last_text="_board() and _agent_row() hold every field the graph needs; attention.alerts_payload gives alerts.",
              sp=spark(6, 9, 4, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0), tool_calls=19),
        agent(S1, "b7d3e1f0a9c25b68", p, "Lead B: factory floor UI", "opus", "high", "waiting", 1.35, 350000,
              burn(0.01, 1500), tool="Agent", target="Force layout engine (graph.js)", since=600, start=4650, age=4.0,
              last_text="Engine and view are building against the frozen API; I integrate when both report.",
              sp=spark(1, 3, 5, 2, 1, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0), waiting_on=3, tool_calls=71),
        agent(S1, "b1e8c2a7f6d40391", A("b7d3e1f0a9c25b68"), "Force layout engine (graph.js)", "opus", "high", "tool",
              0.88, 240000, burn(0.06, 22000), tool="Write", target="lib/cc_dash/web/graph.js", since=4, start=3800,
              age=1.0, last_text="Warm restart keeps settled nodes anchored; new nodes seed beside their parent.",
              sp=spark(2, 4, 6, 8, 7, 9, 10, 8, 9, 11, 10, 12, 9, 10, 5), tool_calls=96),
        agent(S1, "b5a9d0c3e2f17c84", A("b1e8c2a7f6d40391"), "Bench a 300-node layout", "sonnet", "", "tool", 0.12,
              52000, burn(0.01, 3000), tool="Bash", target="node bench/layout.mjs --nodes 300 --frames 600", since=19,
              start=900, age=3.0, last_text="Median frame 3.1 ms at 300 nodes; settle 1.1 s.",
              sp=spark(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 2, 3, 2, 1), tool_calls=14),
        agent(S1, "b2f4e6a8c0d13e57", A("b7d3e1f0a9c25b68"), "Fixtures and headless harness", "sonnet", "", "done",
              0.31, 120000, burn(0, 0), since=70, start=3700, age=70.0,
              last_text="Both polls load; harness patches server.ASSETS at runtime and stubs /api/graph.",
              sp=spark(4, 6, 5, 7, 8, 6, 5, 3, 0, 0, 0, 0, 0, 0, 0), tool_calls=77),
        agent(S1, "b3c5a7e9f1d24f60", A("b7d3e1f0a9c25b68"), "Factory stage visual pass", "opus", "high", "generating",
              0.57, 150000, burn(0.03, 8000), since=14, start=2100, age=14.0, needs="permission",
              last_text="Light stage needs a warmer porcelain ground; checking contrast of the HUD numbers.",
              sp=spark(0, 0, 1, 3, 2, 4, 3, 5, 4, 3, 2, 3, 1, 0, 0), tool_calls=48),
        agent(S1, "c6e0b4d2a8f35d71", p, "Lead C: factory core", "opus", "high", "tool", 0.96, 270000,
              burn(0.02, 7000), tool="Bash", target="python3 tests/test_factory.py", since=8, start=4600, age=1.0,
              last_text="Ledger, orders and the line config pass; checking cc-setup --factory.",
              sp=spark(1, 2, 2, 3, 4, 3, 2, 4, 5, 3, 4, 3, 5, 4, 2), tool_calls=88),
        agent(S1, "c9f3a1e5b7d04e82", A("c6e0b4d2a8f35d71"), "cc-factory CLI and ledger", "sonnet", "medium",
              "generating", 0.29, 110000, burn(0.02, 9000), since=3, start=3000, age=3.0,
              last_text="Writing the advance subcommand: station pass moves the order to the next station.",
              sp=spark(0, 1, 3, 5, 4, 6, 5, 7, 6, 5, 6, 7, 5, 6, 3), tool_calls=61),
    ]
    # cc_tool: S6, release notes (a Sonnet main thread and two Haiku helpers)
    nodes.append(session(S6, "cc_tool", "Draft the release notes for v0.2.6", "sonnet", "medium", "tool", 0.41, 190000,
                         burn(0.02, 6000), tool="Bash", target="git log --oneline v0.2.5..HEAD", since=5, start=1500,
                         age=1.0, last_text="Grouping the commits by area before drafting.",
                         sp=spark(0, 0, 0, 0, 0, 2, 3, 1, 2, 4, 3, 2, 3, 2, 1),
                         detail={"branch": "master", "cwd": "~/Files/hobby/cc_tool", "prompts": 2, "tool_calls": 27,
                                 "usage": {"input": 6100, "output": 14800, "cache_read": 980000, "cache_write": 96000},
                                 "subagents": 2, "running": 2}))
    p6 = "s:" + S6
    nodes += [
        agent(S6, "d1a2b3c4d5e6f708", p6, "Summarize the CHANGELOG entries", "haiku", "", "generating", 0.03, 21000,
              burn(0.0, 2500), since=2, start=300, age=2.0, typ="general-purpose",
              last_text="v0.2.1 to v0.2.5: control mode, plans, routing panel.", sp=spark(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 2, 1, 2, 1),
              tool_calls=9),
        agent(S6, "d9e8f7a6b5c4d302", p6, "Check the README links", "haiku", "", "tool", 0.02, 15000,
              burn(0.0, 1500), tool="WebFetch", target="https://code.claude.com/docs/en/hooks.md", since=3, start=280,
              age=1.0, typ="Explore", sp=spark(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 3, 2, 3, 2), tool_calls=12),
    ]
    # ── reports-app: S3 runs the factory line for FO-003 ───────────────────
    nodes.append(session(S3, "reports-app", "Run the factory line for FO-003", "opus", "medium", "waiting", 2.10, 610000,
                         burn(0.04, 11400), tool="Workflow", target="factory-line", since=780, start=2900, age=2.0,
                         last_text="factory-line launched for FO-003; I will report when review passes.",
                         sp=spark(2, 3, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0),
                         detail={"worktree": "", "branch": "main", "cwd": "~/Files/work/reports-app", "prompts": 1,
                                 "tool_calls": 22, "usage": {"input": 9800, "output": 31000, "cache_read": 2900000,
                                                             "cache_write": 410000},
                                 "guard": {"deny": 0, "ask": 1, "warn": 0}, "subagents": 4, "running": 2}))
    p3 = "s:" + S3
    w = "w:%s/%s" % (S3, RUN)
    nodes.append({"id": w, "kind": "workflow", "parent": p3, "label": "factory-line", "state": "running",
                  "detail": {"run_id": RUN, "name": "factory-line", "status": "running",
                             "phases": ["Plan", "Build", "Test", "Verify", "Review"], "total": 5, "finished": 2,
                             "running": 1, "failed": 0, "order": "FO-003"}})
    nodes += [
        agent(S3, "e1f2a3b4c5d6e7f8", w, "FO-003:plan", "opus", "high", "done", 0.66, 180000, burn(0, 0), since=520,
              start=2850, age=520.0, typ="workflow-subagent", kind="workflow", phase="Plan", run_id=RUN, order="FO-003",
              last_text="Plan: one CsvExport service, a button on the reports toolbar, streamed download.",
              sp=spark(5, 3, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0), tool_calls=31),
        agent(S3, "e2a3b4c5d6e7f809", w, "FO-003:build", "sonnet", "medium", "done", 0.48, 170000, burn(0, 0),
              since=140, start=2200, age=140.0, typ="workflow-subagent", kind="workflow", phase="Build", run_id=RUN,
              order="FO-003", last_text="CsvExport and the toolbar button are in; 14 files changed.",
              sp=spark(0, 4, 8, 9, 7, 8, 6, 5, 3, 0, 0, 0, 0, 0, 0), tool_calls=83),
        agent(S3, "e3b4c5d6e7f8091a", w, "FO-003:test", "sonnet", "", "tool", 0.21, 64000, burn(0.02, 11000),
              tool="Bash", target="npm test -- --run reports/csv", since=9, start=130, age=1.0, typ="workflow-subagent",
              kind="workflow", phase="Test", run_id=RUN, order="FO-003",
              last_text="Two snapshot tests needed the new column order; rerunning the csv suite.",
              sp=spark(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 4, 5, 3, 2), tool_calls=17),
        agent(S3, "e9c8b7a6f5e4d3c2", p3, "Watch CI for the FO-003 lane", "haiku", "", "tool", 0.01, 8000,
              burn(0.0, 400), tool="Bash", target="gh run watch 9921 --exit-status", since=210, start=600, age=30.0,
              typ="general-purpose", background=True, sp=spark(0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0), tool_calls=3),
    ]
    # reports-app: S4 reviews the FO-002 lane (its branch is the order's lane branch: a `runs` edge)
    nodes.append(session(S4, "reports-app", "Review the FO-002 lane", "opus", "high", "generating", 0.75, 230000,
                         burn(0.03, 12000), since=7, start=1100, age=7.0,
                         last_text="Checking the DST edge cases the verify station flagged.",
                         sp=spark(0, 0, 0, 0, 0, 0, 1, 2, 1, 2, 3, 2, 1, 2, 1),
                         detail={"worktree": "FO-002-digest-tz", "branch": "factory/FO-002-digest-tz",
                                 "cwd": "~/Files/work/reports-app/.claude/worktrees/FO-002-digest-tz", "prompts": 1,
                                 "tool_calls": 19, "usage": {"input": 3900, "output": 12100, "cache_read": 870000,
                                                             "cache_write": 120000},
                                 "subagents": 1, "running": 1}))
    p4 = "s:" + S4
    nodes.append(agent(S4, "f4e3d2c1b0a99887", p4, "FO-002:review", "opus", "high", "tool", 0.53, 150000,
                       burn(0.03, 9000), tool="Read", target="src/digest/timezone.ts", since=2, start=1000, age=1.0,
                       typ="general-purpose", order="FO-002",
                       last_text="The fix converts at the edge; the digest job still formats in server time.",
                       sp=spark(0, 0, 0, 0, 0, 1, 2, 3, 2, 3, 4, 3, 2, 3, 2), tool_calls=36))
    # reports-app: orders
    nodes += [
        order("reports-app", "FO-002", "digest-tz", "Fix the timezone in the daily digest", "p1", "review", "review",
              ["done", "done", "done", "done", "active"], [7200, 5400, 4300, 1500, 1000],
              lane={"branch": "factory/FO-002-digest-tz", "worktree": "~/Files/work/reports-app/.claude/worktrees/FO-002-digest-tz"},
              run_id="wf_51c0de77", rework=1, events=14, created=86400, updated=1000),
        order("reports-app", "FO-003", "csv-export", "Add CSV export to the reports page", "p2", "building", "test",
              ["done", "done", "active", "pending", "pending"], [2850, 2200, 130, None, None],
              lane={"branch": "factory/FO-003-csv-export", "worktree": "~/Files/work/reports-app/.claude/worktrees/FO-003-csv-export"},
              run_id=RUN, rework=0, events=9, created=7000, updated=130),
        order("reports-app", "FO-004", "audit-pagination", "Paginate the audit log", "p3", "spec", "",
              ["pending", "pending", "pending", "pending", "pending"], [None] * 5,
              gate={"name": "spec", "waiting": True}, events=2, created=3600, updated=3000),
    ]
    # ── valhalla-strategy: S5 is your turn; one failed and one stalled agent ──
    nodes.append(session(S5, "valhalla-strategy", "Fix the dry_run order gating", "sonnet", "medium", "idle", 0.96,
                         330000, burn(0, 0), since=260, start=3300, age=260.0,
                         last_text="The gating fix is in, but the trace agent failed on the broker mock. Retry it, or should I patch the mock first?",
                         sp=spark(3, 4, 2, 5, 3, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0),
                         detail={"worktree": "", "branch": "fix/dry-run-gating", "cwd": "~/Files/hobby/valhalla/valhalla-strategy",
                                 "prompts": 4, "tool_calls": 96, "tool_errors": 3, "api_errors": 0,
                                 "usage": {"input": 12000, "output": 40100, "cache_read": 3100000, "cache_write": 260000},
                                 "guard": {"deny": 1, "ask": 0, "warn": 0}, "subagents": 2, "running": 0,
                                 "plan": None}, errors=3))
    p5 = "s:" + S5
    nodes += [
        agent(S5, "a7b6c5d4e3f20011", p5, "Trace order placement under dry_run", "sonnet", "medium", "failed", 0.27,
              90000, burn(0, 0), since=300, start=1900, age=300.0, errors=3,
              last_text="ConnectionRefusedError: broker mock on :8711 is not running.",
              sp=spark(2, 3, 4, 3, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0), tool_calls=41),
        agent(S5, "a8c7d6e5f4a30022", p5, "Profile the ensemble scorer", "haiku", "", "stalled", 0.05, 30000,
              burn(0, 0), tool="Bash", target="python -m cProfile -s cumtime scripts/score.py", since=640, start=1400,
              age=640.0, stalled_for=640, sp=spark(1, 2, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0), tool_calls=7),
    ]

    # ── edges: every parent→child link, plus runs ────────────────────────
    by = {n["id"]: n for n in nodes}
    edges = []
    for n in nodes:
        if not n["parent"]:
            continue
        par = by[n["parent"]]
        kind = "member" if par["kind"] == "project" else "step" if par["kind"] == "workflow" else "spawn"
        active = n["state"] in RUNNING or n["state"] == "running" or n["state"] in ("spec", "approved", "building", "verifying", "review")
        if n["kind"] == "order":
            active = False   # an order hangs off its project; the work flows along its runs edge
        e = {"id": n["parent"] + ">" + n["id"], "source": n["parent"], "target": n["id"], "kind": kind, "active": active}
        if n["kind"] in ("agent", "session") and n.get("burn"):
            e["rate"] = n["burn"]["tok_min"] if active else 0
        edges.append(e)
    edges.append({"id": "o:reports-app/FO-003>" + w, "source": "o:reports-app/FO-003", "target": w, "kind": "runs", "active": True})
    edges.append({"id": "o:reports-app/FO-002>s:" + S4, "source": "o:reports-app/FO-002", "target": "s:" + S4, "kind": "runs",
                  "active": True, "rate": 12000})

    alerts = [{"id": "h-7c21e0b4", "node": A("b3c5a7e9f1d24f60"), "kind": "permission",
               "title": "Claude needs your permission to use Bash",
               "message": "google-chrome --headless=new --screenshot=/tmp/stage.png --window-size=1440,900 http://127.0.0.1:7878/#/factory",
               "tool": "Bash", "age": 14.0}]
    feed = [
        feedrow(4, A("b1e8c2a7f6d40391"), "Force layout engine (graph.js)", "sub", "cc_tool", "opus", "Write", "lib/cc_dash/web/graph.js", "pending"),
        feedrow(5, "s:" + S6, "Draft the release notes for v0.2.6", "main", "cc_tool", "sonnet", "Bash", "git log --oneline v0.2.5..HEAD", "pending"),
        feedrow(6, A("a91f02d6c3b8e4a7"), "Write the graph.py payload builder", "sub", "cc_tool", "sonnet", "Edit", "lib/cc_dash/graph.py", "pending"),
        feedrow(8, A("c6e0b4d2a8f35d71"), "Lead C: factory core", "sub", "cc_tool", "opus", "Bash", "python3 tests/test_factory.py", "pending"),
        feedrow(9, "a:%s/e3b4c5d6e7f8091a" % S3, "FO-003:test", "workflow", "reports-app", "sonnet", "Bash", "npm test -- --run reports/csv", "pending"),
        feedrow(11, A("a2c7d8e90f1b3c45"), "Graph API tests", "sub", "cc_tool", "sonnet", "Bash", "python3 tests/test_cc_dash_graph.py", "pending"),
        feedrow(12, "a:%s/f4e3d2c1b0a99887" % S4, "FO-002:review", "sub", "reports-app", "opus", "Read", "src/digest/timezone.ts", "ok"),
        feedrow(15, A("b3c5a7e9f1d24f60"), "Factory stage visual pass", "sub", "cc_tool", "opus", "Bash", "google-chrome --headless=new --screenshot=/tmp/stage.png", "blocked"),
        feedrow(18, A("a91f02d6c3b8e4a7"), "Write the graph.py payload builder", "sub", "cc_tool", "sonnet", "Read", "lib/cc_dash/store.py", "ok"),
        feedrow(19, A("b5a9d0c3e2f17c84"), "Bench a 300-node layout", "sub", "cc_tool", "sonnet", "Bash", "node bench/layout.mjs --nodes 300 --frames 600", "pending"),
        feedrow(23, "a:%s/d9e8f7a6b5c4d302" % S6, "Check the README links", "sub", "cc_tool", "haiku", "WebFetch", "https://code.claude.com/docs/en/hooks.md", "pending"),
        feedrow(27, A("a2c7d8e90f1b3c45"), "Graph API tests", "sub", "cc_tool", "sonnet", "Bash", "python3 tests/test_cc_dash_graph.py -k latency", "error"),
        feedrow(31, A("c9f3a1e5b7d04e82"), "cc-factory CLI and ledger", "sub", "cc_tool", "sonnet", "Edit", "bin/cc-factory", "ok"),
        feedrow(36, "s:" + S4, "Review the FO-002 lane", "main", "reports-app", "opus", "Grep", "toZonedTime", "ok"),
        feedrow(44, A("b1e8c2a7f6d40391"), "Force layout engine (graph.js)", "sub", "cc_tool", "opus", "Read", "lib/cc_dash/web/dom.js", "ok"),
        feedrow(52, "a:%s/e3b4c5d6e7f8091a" % S3, "FO-003:test", "workflow", "reports-app", "sonnet", "Edit", "src/reports/__snapshots__/csv.test.ts.snap", "ok"),
        feedrow(61, A("c6e0b4d2a8f35d71"), "Lead C: factory core", "sub", "cc_tool", "opus", "Write", "templates/factory/line.json", "ok"),
        feedrow(75, A("a91f02d6c3b8e4a7"), "Write the graph.py payload builder", "sub", "cc_tool", "sonnet", "Bash", "python3 -c 'import cc_dash.graph'", "ok"),
    ]
    agents = [n for n in nodes if n["kind"] == "agent"]
    sess = [n for n in nodes if n["kind"] == "session"]
    rows = agents + sess          # the board counts main threads as agents
    k = {"sessions_live": len(sess), "agents": len(rows),
         "working": sum(1 for n in rows if n["state"] in RUNNING and not n["needs"]),
         "needs": sum(1 for n in rows if n["needs"]),
         "stalled": sum(1 for n in rows if n["state"] == "stalled"),
         "failed": sum(1 for n in rows if n["state"] == "failed"),
         "done": sum(1 for n in rows if n["state"] == "done"),
         "burn_cost_min": round(sum(n["burn"]["cost_min"] for n in sess), 2),
         "burn_tok_min": sum(n["burn"]["tok_min"] for n in sess),
         "cost_live": round(sum(n["cost"] for n in sess), 2),
         "cost_window": round(sum(n["detail"]["cost_window"] for n in nodes if n["kind"] == "project"), 2),
         "guard": {g: sum(n["detail"]["guard"][g] for n in nodes if n["kind"] == "project") for g in ("deny", "ask", "warn")},
         "errors": sum(n["detail"]["errors"] for n in nodes if n["kind"] == "project"),
         "orders": {}}
    for n in nodes:
        if n["kind"] == "order":
            k["orders"][n["state"]] = k["orders"].get(n["state"], 0) + 1
    return {"now": NOW, "window_days": 7, "scope": {"project": ""}, "sig": "", "kpis": k, "nodes": nodes,
            "edges": edges, "alerts": alerts, "feed": feed, "limits": {"live_secs": 600, "linger": 120}}


def feedrow(ago, node, label, kind, project, family, name, target, status):
    sid = node.split(":", 1)[1].split("/")[0]
    key = node.split(":", 1)[1] if node.startswith("a:") else sid
    return {"t": NOW - ago, "key": key, "id": "toolu_" + hashlib.sha1(("%s|%s|%s|%s" % (ago, node, name, target)).encode()).hexdigest()[:12], "label": label,
            "kind": kind, "project": project, "family": family, "name": name, "target": target, "status": status,
            "dur": None if status == "pending" else 1.4, "node": node}


def recount(d):
    nodes = d["nodes"]
    agents = [n for n in nodes if n["kind"] == "agent"]
    sess = [n for n in nodes if n["kind"] == "session"]
    rows = agents + sess
    k = d["kpis"]
    k.update({"sessions_live": len(sess), "agents": len(rows),
              "working": sum(1 for n in rows if n["state"] in RUNNING and not n["needs"]),
              "needs": sum(1 for n in rows if n["needs"]),
              "stalled": sum(1 for n in rows if n["state"] == "stalled"),
              "failed": sum(1 for n in rows if n["state"] == "failed"),
              "done": sum(1 for n in rows if n["state"] == "done"),
              "burn_cost_min": round(sum(n["burn"]["cost_min"] for n in sess), 2),
              "burn_tok_min": sum(n["burn"]["tok_min"] for n in sess),
              "cost_live": round(sum(n["cost"] for n in sess), 2)})
    k["orders"] = {}
    for n in nodes:
        if n["kind"] == "order":
            k["orders"][n["state"]] = k["orders"].get(n["state"], 0) + 1


def poll2(d1):
    """Three seconds later: states change in place, one agent appears under Lead B, the verify station starts,
    the Haiku explorer that finished long ago drops off, the permission was granted and a question opened."""
    d = copy.deepcopy(d1)
    by = {n["id"]: n for n in d["nodes"]}
    A = lambda aid: "a:%s/%s" % (S1, aid)  # noqa: E731
    # tests finished
    t = by[A("a2c7d8e90f1b3c45")]
    t.update(state="done", tool="", target="", since=NOW, age=1.0, burn={"cost_min": 0, "tok_min": 0},
             last_text="All 38 graph checks pass; latency 41 ms with 50 live transcripts.")
    # permission granted: the visual pass runs its screenshot
    v = by[A("b3c5a7e9f1d24f60")]
    v.update(state="tool", needs=None, tool="Bash",
             target="google-chrome --headless=new --screenshot=/tmp/stage.png --window-size=1440,900", since=NOW - 1)
    # the CLI worker asks a question
    q = by[A("c9f3a1e5b7d04e82")]
    q.update(state="waiting", needs="question", tool="AskUserQuestion", target="Should `cc-factory advance` refuse to skip a station?",
             since=NOW - 2)
    # release notes: from Bash to thinking
    s6 = by["s:" + S6]
    s6.update(state="generating", tool="", target="", since=NOW - 1, last_text="Drafting: Factory floor, ledger, the line.")
    # FO-003: test done, verify starts
    ft = by["a:%s/e3b4c5d6e7f8091a" % S3]
    ft.update(state="done", tool="", target="", since=NOW - 1, burn={"cost_min": 0, "tok_min": 0},
              last_text="reports/csv: 12 passed.")
    wf = by["w:%s/%s" % (S3, RUN)]
    wf["detail"].update(finished=3, running=1)
    o3 = by["o:reports-app/FO-003"]
    o3["state"] = "verifying"
    o3["detail"].update(status="verifying", station="verify", updated=NOW - 1, events=11)
    o3["detail"]["stations"][2].update(status="done", t=NOW - 1)
    o3["detail"]["stations"][3].update(status="active", t=NOW - 1)
    o3["detail"]["stations"][4]["status"] = "pending"
    # nodes that arrive
    new_b4 = agent(S1, "b4d6f8a0c2e35a19", A("b7d3e1f0a9c25b68"), "Cohesion pass on nav and help", "sonnet", "",
                   "generating", 0.01, 4000, burn(0.0, 3000), since=2, start=2, age=2.0,
                   last_text="Reading index.html and the help dialog.", sp=spark(1), tool_calls=1)
    new_f4 = agent(S3, "e4c5d6e7f8091a2b", "w:%s/%s" % (S3, RUN), "FO-003:verify", "opus", "high", "generating", 0.02,
                   6000, burn(0.01, 5000), since=1, start=1, age=1.0, typ="workflow-subagent", kind="workflow",
                   phase="Verify", run_id=RUN, order="FO-003", last_text="Reading the holdout scenarios.", sp=spark(1),
                   tool_calls=1)
    # node that leaves (finished more than the linger time ago)
    gone = A("a0b3f5c6d7e81922")
    d["nodes"] = [n for n in d["nodes"] if n["id"] != gone]
    i = next(i for i, n in enumerate(d["nodes"]) if n["id"] == A("b3c5a7e9f1d24f60")) + 1
    d["nodes"].insert(i, new_b4)
    j = next(i for i, n in enumerate(d["nodes"]) if n["id"] == "a:%s/e3b4c5d6e7f8091a" % S3) + 1
    d["nodes"].insert(j, new_f4)
    # session totals follow
    by = {n["id"]: n for n in d["nodes"]}
    by["s:" + S1].update(cost=8.47, detail=dict(by["s:" + S1]["detail"], subagents=13, running=9))
    by["s:" + S3].update(cost=2.13)
    # edges: rebuild from parents, then runs
    edges = []
    for n in d["nodes"]:
        if not n["parent"]:
            continue
        par = by[n["parent"]]
        kind = "member" if par["kind"] == "project" else "step" if par["kind"] == "workflow" else "spawn"
        active = (n["state"] in RUNNING or n["state"] == "running") and n["kind"] != "order"
        e = {"id": n["parent"] + ">" + n["id"], "source": n["parent"], "target": n["id"], "kind": kind, "active": active}
        if n["kind"] in ("agent", "session"):
            e["rate"] = n["burn"]["tok_min"] if active else 0
        edges.append(e)
    edges += [x for x in d1["edges"] if x["kind"] == "runs"]
    d["edges"] = edges
    d["alerts"] = [{"id": "h-91d4a2c3", "node": A("c9f3a1e5b7d04e82"), "kind": "question",
                    "title": "Claude has a question", "message": "Should `cc-factory advance` refuse to skip a station?",
                    "tool": "AskUserQuestion", "age": 2.0}]
    d["feed"] = [
        feedrow(1, A("b3c5a7e9f1d24f60"), "Factory stage visual pass", "sub", "cc_tool", "opus", "Bash",
                "google-chrome --headless=new --screenshot=/tmp/stage.png --window-size=1440,900", "pending"),
        feedrow(2, A("a2c7d8e90f1b3c45"), "Graph API tests", "sub", "cc_tool", "sonnet", "Bash",
                "python3 tests/test_cc_dash_graph.py", "ok"),
        feedrow(2, "a:%s/e3b4c5d6e7f8091a" % S3, "FO-003:test", "workflow", "reports-app", "sonnet", "Bash",
                "npm test -- --run reports/csv", "ok"),
    ] + d1["feed"][:-3]
    for f in d["feed"]:
        f["t"] -= 0      # same clock: poll 2 is "now" too; the harness shifts both to the wall clock
    recount(d)
    d["sig"] = "poll2"
    return d


ORDER_ACTIVE = ("building", "verifying", "review")
RANK = {"permission": 0, "question": 1, "plan": 2, "other": 3}


def finish(d):
    """Shape a payload the way lib/cc_dash/graph.py does (Lead A, 2026-10-25): tool None when nothing runs,
    needs None on workflows and orders, every edge an int rate (a workflow sums its step agents, an order
    what it runs), an order's member edge active while the order works, and a hub's needs = the most
    blocking needs under it."""
    nodes = d["nodes"]
    by = {n["id"]: n for n in nodes}
    for n in nodes:
        if n["kind"] in ("session", "agent") and not n.get("tool"):
            n["tool"], n["target"] = None, ""
        if n["kind"] in ("workflow", "order"):
            n["needs"] = None
    edges = []
    for n in nodes:
        if n["parent"]:
            par = by[n["parent"]]
            kind = "member" if par["kind"] == "project" else "step" if par["kind"] == "workflow" else "spawn"
            edges.append({"id": n["parent"] + ">" + n["id"], "source": n["parent"], "target": n["id"], "kind": kind})
    edges += [{"id": e["id"], "source": e["source"], "target": e["target"], "kind": "runs"} for e in d["edges"] if e["kind"] == "runs"]
    rate, active = {}, {}
    for n in nodes:
        if n["kind"] in ("session", "agent"):
            rate[n["id"]], active[n["id"]] = int(n["burn"]["tok_min"]), n["state"] in RUNNING
    for n in nodes:
        if n["kind"] == "workflow":
            rate[n["id"]] = sum(rate[e["target"]] for e in edges if e["source"] == n["id"] and e["kind"] == "step")
            active[n["id"]] = n["state"] == "running"
    for n in nodes:
        if n["kind"] == "order":
            rate[n["id"]] = sum(rate[e["target"]] for e in edges if e["source"] == n["id"] and e["kind"] == "runs")
            active[n["id"]] = n["state"] in ORDER_ACTIVE or any(x["status"] == "active" for x in n["detail"]["stations"])
    for n in nodes:
        if n["kind"] == "project":
            rate[n["id"]], active[n["id"]] = 0, False
    for e in edges:
        e["active"], e["rate"] = active[e["target"]], rate[e["target"]]
    d["edges"] = edges
    for hub in (n for n in nodes if n["kind"] == "project"):
        def root(x):
            while x["parent"]:
                x = by[x["parent"]]
            return x
        kinds = [n["needs"] for n in nodes if n is not hub and n.get("needs") and root(n) is hub]
        hub["needs"] = min(kinds, key=lambda k: RANK.get(k, 9)) if kinds else None
        hub["state"] = "needs" if hub["needs"] else hub["state"] if hub["state"] != "needs" else "working"
    return d


def series(later=False):
    """graph.py's `series` for NOW: 20 clock-aligned 180 s buckets, every invariant of the contract true."""
    start = (int(NOW // 180) + 1) * 180 - 3600
    opus = [0, 0, 4200, 9800, 15400, 22100, 18700, 31200, 27500, 38400, 44100, 36800, 52300, 47900, 61200, 58700,
            66400, 71800, 64300, 21500]
    sonnet = [1800, 3100, 5600, 8200, 12400, 9700, 15800, 21300, 18900, 24600, 29800, 33100, 27400, 38900, 35200,
              42700, 47100, 40300, 45600, 12400]
    haiku = [0, 900, 0, 1500, 2300, 0, 3100, 2700, 0, 4200, 3600, 5100, 0, 4800, 6300, 5400, 7200, 0, 6800, 1900]
    calls = [3, 7, 12, 18, 22, 19, 28, 35, 31, 42, 47, 44, 51, 49, 58, 55, 60, 57, 54, 17]
    errs = [0, 0, 1, 0, 2, 0, 1, 3, 0, 2, 1, 0, 4, 1, 2, 0, 3, 1, 2, 1]
    agents = [1, 2, 3, 4, 6, 6, 8, 9, 9, 11, 12, 12, 14, 13, 15, 16, 17, 16, 17, 12]
    if later:
        opus[-1] += 3400
        sonnet[-1] += 2100
        haiku[-1] += 600
        calls[-1] += 3
        errs[-1] += 1
        agents[-1] += 1
    tokens = [a + b + c for a, b, c in zip(opus, sonnet, haiku)]
    assert all(e <= c for e, c in zip(errs, calls)) and all(len(x) == 20 for x in (tokens, calls, errs, agents))
    return {"bucket_secs": 180, "start": start, "tokens": tokens,
            "tokens_by_family": {"opus": opus, "sonnet": sonnet, "haiku": haiku},
            "tool_calls": calls, "tool_errors": errs, "agents": agents, "agents_total": 24}


def main():
    d1 = finish(poll1())
    d1["series"] = series()
    d1["sig"] = "poll1"
    d2 = finish(poll2(d1))
    d2["series"] = series(True)
    # sanity: every edge end exists, every parent exists, ids unique, KPIs add up
    for d in (d1, d2):
        ids = [n["id"] for n in d["nodes"]]
        assert len(ids) == len(set(ids)), "duplicate ids"
        s = set(ids)
        for n in d["nodes"]:
            assert n["parent"] is None or n["parent"] in s, n["id"]
        for e in d["edges"]:
            assert e["source"] in s and e["target"] in s, e["id"]
        for a in d["alerts"]:
            assert a["node"] in s
        for f in d["feed"]:
            assert f["node"] in s, f["node"]
        k = d["kpis"]
        assert k["working"] + k["needs"] + k["stalled"] + k["failed"] + k["done"] + sum(
            1 for n in d["nodes"] if n["kind"] in ("agent", "session") and n["state"] == "idle" and not n["needs"]) == k["agents"], k
        for n in d["nodes"]:
            if n["kind"] == "project":
                assert abs(sum(m["cost"] for m in n["detail"]["model_mix"]) - n["detail"]["cost_window"]) < 0.005, n["id"]
    print("poll1: %d nodes (%d agents), %d edges; poll2: %d nodes, %d edges" % (
        len(d1["nodes"]), sum(1 for n in d1["nodes"] if n["kind"] == "agent"), len(d1["edges"]), len(d2["nodes"]), len(d2["edges"])))
    print("kpis1", json.dumps(d1["kpis"]))
    print("kpis2", json.dumps(d2["kpis"]))
    os.makedirs(OUT, exist_ok=True)
    for name, d in (("graph-poll1.json", d1), ("graph-poll2.json", d2)):
        with open(os.path.join(OUT, name), "w") as fh:
            json.dump(d, fh, indent=1)
            fh.write("\n")


if __name__ == "__main__":
    main()
