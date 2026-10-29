#!/usr/bin/env python3
"""Graph API checks for cc-dash (lib/cc_dash/graph.py, GET /api/graph).

Builds one temp world with its own CLAUDE_CONFIG_DIR and XDG_STATE_HOME (nothing
outside the temp dir is read for state or written) and a fixed clock:

  alpha    S1 a live lead waiting on a plain subagent (5000-character description,
           clipped), that agent's nested subagent, a finished background agent, and
           the Workflow run wf_1 (agents FO-001:plan done, FO-001:build running,
           FO-001:test failed; one labelled only in the journal). S2 holds wf_2
           (FO-002:build running, labelled only in meta) and ends on a pending
           AskUserQuestion. S3 lives in alpha's .claude/worktrees/fo-003 on branch
           factory/FO-003-x. A hook PermissionRequest sits on S1's running subagent.
  beta     one idle live session.
  gamma    a session from 3 h ago (in the window, not live) and one queued order.
  delta    a session from 3 h ago and no orders: a hub only with all_projects.
  epsilon  a session from 2 h ago, off the board, with an open hook alert.

lib/cc_dash/factory.py is replaced by a stub (project_orders per project root,
a mode that raises, a "module missing" mode), so the order half of the graph is
tested against the §5.5 shape without the real factory.

Then it checks payload shape and types, ids, the sig (stable across polls, blind
to ticking ages, moved by any append, order change or burn-rate change), edge
integrity, workflow agents under their workflow, alerts attaching to the right
node (a session with no transcript yet: its project's hub), the order links
(run id, agent label, branch), the live station overlay, hidden terminal orders,
dormant and order-only hubs, scope by project, the board and Overview numbers the
graph must agree with, GET /api/graph (401 before pairing, same ids as
graph.payload after), the static asset entries, and the latency with ~50 live
transcripts.

`series` (the last hour in 20 buckets of 180 s) has its own config root and clock,
so nothing else sees it: project sx holds a lead running a tool now, a finished
subagent whose one call ran across four buckets, a running subagent, and a session
that finished ~40 minutes ago (off the board) with its subagent; project sy one
session with a guard-blocked call. Checked: shape, bucket edges (on start, on an
edge, at start - 1, at now), a usage rewrite counted once, synthetic replies, the
off-board session, the running fill, project scope (all_projects and days change
nothing), the empty floor, the sig (still in one bucket, moved by a bucket-only
write and by the window sliding), and the minute buckets' memory bound.

End to end, last: the REAL lib/cc_dash/factory.py and bin/cc-factory in a temp
git repo (temp HOME and XDG_STATE_HOME, GIT_* stripped, as tests/factory_cli_cases.py
runs it): init, two orders, approve and lane the first, mark it building with a
run id; then a session in the lane worktree on the lane branch and a main-checkout
session whose Workflow run (that run id) has FO-001:plan done and FO-001:build
running. graph.payload must show both orders, runs edges to the workflow and the
lane session, build active by the live overlay, and the kpis.orders counts.

Run:  python3 tests/test_cc_dash_graph.py
"""
import copy
import http.client
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import types
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


# Isolation first: a temp config dir and a temp state dir before anything of cc_dash is imported.
BASE = tempfile.mkdtemp(prefix="cc-dash-graph-test-")
CFG = os.path.join(BASE, "claude")
STATE = os.path.join(BASE, "state")
LOGDIR = os.path.join(STATE, "cc_tool")
LOG = os.path.join(LOGDIR, "attention.jsonl")
os.makedirs(os.path.join(CFG, "projects"))
os.makedirs(os.path.join(CFG, "sessions"))
os.chmod(os.path.join(CFG, "sessions"), 0o700)      # as Claude Code creates ~/.claude/sessions
os.makedirs(LOGDIR, mode=0o700)
os.chmod(LOGDIR, 0o700)
os.environ["CLAUDE_CONFIG_DIR"] = CFG
os.environ["XDG_STATE_HOME"] = STATE

sys.path.insert(0, os.path.join(REPO, "lib"))
sys.path.insert(0, os.path.join(REPO, "tests"))
try:
    import cc_dash as cc_dash_pkg  # noqa: E402
    from cc_dash import server as server_mod  # noqa: E402
    from cc_dash import store as store_mod  # noqa: E402
    from cc_dash.parser import tilde  # noqa: E402
    from cc_dash.server import make_server  # noqa: E402
    from cc_dash.store import Store  # noqa: E402
    from dash_pairing import auto_cookie  # noqa: E402  (tests/dash_pairing.py: every request carries the pairing token)
    from cc_dash import graph  # noqa: E402
except BaseException:
    shutil.rmtree(BASE, ignore_errors=True)
    raise

DEF_PLANS_TTL, DEF_ORDERS_TTL = graph.PLANS_TTL, graph.ORDERS_TTL
T = (int(time.time()) // 60) * 60 + 30            # "now": the middle of a wall-clock minute
NOW = [float(T)]

ID1 = "11111111-0000-0000-0000-000000000001"      # alpha: lead, plain subagents, wf_1
ID2 = "22222222-0000-0000-0000-000000000002"      # alpha: wf_2, pending AskUserQuestion
ID3 = "33333333-0000-0000-0000-000000000003"      # alpha worktree fo-003, branch factory/FO-003-x
IDB = "44444444-0000-0000-0000-000000000004"      # beta: idle
IDG = "55555555-0000-0000-0000-000000000005"      # gamma: 3 h ago
IDD = "66666666-0000-0000-0000-000000000006"      # delta: 3 h ago
IDO = "77777777-0000-0000-0000-000000000007"      # epsilon: 2 h ago, open hook alert
BRANCH = "factory/FO-003-x"
LONG = "Write the tests " + "x" * 4984             # 5000 characters

ALPHA = os.path.join(BASE, "work", "alpha")
GAMMA = os.path.join(BASE, "work", "gamma")
WT3 = os.path.join(ALPHA, ".claude", "worktrees", "fo-003")


def sn(sid):
    return "s:" + sid


def an(sid, aid):
    return f"a:{sid}/{aid}"


def wn(sid, run):
    return f"w:{sid}/{run}"


def on(label, oid):
    return f"o:{label}/{oid}"


class World:
    """Writes one session's transcripts; every file's mtime is its last record's time."""

    def __init__(self, name, sid, cwd=None, branch="main", cfg=None):
        self.cwd = cwd or os.path.join(BASE, "work", name)
        os.makedirs(self.cwd, exist_ok=True)
        self.pdir = os.path.join(cfg or CFG, "projects", self.cwd.replace("/", "-"))
        os.makedirs(self.pdir, exist_ok=True)
        self.sid = sid
        self.branch = branch
        self.main = os.path.join(self.pdir, sid + ".jsonl")
        self.subdir = os.path.join(self.pdir, sid, "subagents")
        self.n = 0

    def rec(self, kind, t, agent=None, **kw):
        self.n += 1
        r = {"type": kind, "timestamp": iso(t), "sessionId": self.sid, "cwd": self.cwd, "gitBranch": self.branch,
             "version": "2.1.300", "isSidechain": agent is not None, "uuid": f"{self.sid[:4]}-{self.n}"}
        if agent:
            r["agentId"] = agent
        r.update(kw)
        return r

    def prompt(self, t, text, agent=None):
        return self.rec("user", t, agent, message={"role": "user", "content": text})

    def tool(self, t, tid, name, inp, agent=None, model="claude-opus-5-5", stop="tool_use"):
        mid = f"m-{tid}"
        return self.rec("assistant", t, agent, message={"id": mid, "model": model, "role": "assistant", "stop_reason": stop,
                                                        "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}],
                                                        "usage": {"input_tokens": 10, "output_tokens": 100, "cache_read_input_tokens": 1000}})

    def text(self, t, mid, text, agent=None, model="claude-opus-5-5", stop="end_turn"):
        return self.rec("assistant", t, agent, message={"id": mid, "model": model, "role": "assistant", "stop_reason": stop,
                                                        "content": [{"type": "text", "text": text}],
                                                        "usage": {"input_tokens": 10, "output_tokens": 50}})

    def result(self, t, tid, text="ok", agent=None, err=False, tur=None):
        r = self.rec("user", t, agent, message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": err}]})
        if tur is not None:
            r["toolUseResult"] = tur
        return r

    @staticmethod
    def write(path, records, append=False):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a" if append else "w") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")
        last = max(datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")).timestamp() for r in records if "timestamp" in r)
        os.utime(path, (last, last))

    def write_main(self, records, append=False):
        self.write(self.main, records, append)

    def write_sub(self, aid, records, meta=None, run=None, append=False):
        d = os.path.join(self.subdir, "workflows", run) if run else self.subdir
        self.write(os.path.join(d, f"agent-{aid}.jsonl"), records, append)
        if meta is not None:
            with open(os.path.join(d, f"agent-{aid}.meta.json"), "w") as fh:
                json.dump(meta, fh)

    def sub_path(self, aid, run=None):
        d = os.path.join(self.subdir, "workflows", run) if run else self.subdir
        return os.path.join(d, f"agent-{aid}.jsonl")

    def journal(self, run, records):
        d = os.path.join(self.subdir, "workflows", run)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "journal.jsonl"), "w") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")

    def summary(self, run, data):
        d = os.path.join(self.pdir, self.sid, "workflows")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, run + ".json"), "w") as fh:
            json.dump(data, fh)


def emit(event, session, ts, **kw):
    """One attention-hook line in the temp state dir (0700 dir, 0600 file, as the hook writes it)."""
    rec = {"v": 1, "ts": round(ts, 3), "event": event, "session": session, "agent": kw.pop("agent", None),
           "agent_type": kw.pop("agent_type", None), "cwd": kw.pop("cwd", ""), "transcript": ""}
    rec.update(kw)
    fd = os.open(LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    os.write(fd, (json.dumps(rec) + "\n").encode())
    os.close(fd)


FX = types.SimpleNamespace()       # the worlds, filled in by build_world()


def build_world():
    plan = os.path.join(ALPHA, "docs", "superpowers", "plans", "2026-10-24-widget.md")
    os.makedirs(os.path.dirname(plan), exist_ok=True)
    with open(plan, "w") as fh:
        fh.write("# Widget Plan\n\n### Task 1: Parser\n\n- [ ] **Step 1: Write the failing test**\n\n"
                 "### Task 2: Store\n\n- [ ] **Step 1: Write the failing test**\n")

    # ── S1: lead waiting on c1; c1 spawned c3; c2 handed back; wf_1 ──
    S1 = FX.S1 = World("alpha", ID1)
    S1.write_main([
        S1.prompt(T - 300, "Ship the feature"),
        S1.tool(T - 298, "tu_plan", "Read", {"file_path": plan}),
        S1.result(T - 297, "tu_plan"),
        S1.tool(T - 295, "tu_wf", "Workflow", {"script": "export const meta = { name: 'factory-line' }"}),
        S1.result(T - 294, "tu_wf", "launched", tur={"status": "async_launched", "runId": "wf_1"}),
        S1.tool(T - 290, "tu_bg", "Agent", {"description": "Background survey", "subagent_type": "Explore", "run_in_background": True, "prompt": "x"}),
        S1.result(T - 289, "tu_bg", "launched", tur={"status": "async_launched", "agentId": "c2"}),
        S1.tool(T - 280, "tu_c1", "Agent", {"description": LONG, "subagent_type": "general-purpose", "prompt": "x"}),
    ])
    S1.write_sub("c1", [
        S1.prompt(T - 279, "Write the tests", agent="c1"),
        S1.tool(T - 200, "c1_read", "Read", {"file_path": "/w/a.py"}, agent="c1", model="claude-sonnet-5-5"),
        S1.result(T - 199, "c1_read", agent="c1"),
        S1.tool(T - 130, "c1_spawn", "Agent", {"description": "Check fixtures", "subagent_type": "Explore", "run_in_background": True, "prompt": "y"}, agent="c1", model="claude-sonnet-5-5"),
        S1.result(T - 129, "c1_spawn", "launched", agent="c1", tur={"status": "async_launched", "agentId": "c3"}),
        S1.tool(T - 70, "c1_edit", "Edit", {"file_path": "/w/a.py"}, agent="c1", model="claude-sonnet-5-5"),
        S1.result(T - 69, "c1_edit", "boom", agent="c1", err=True),
        S1.tool(T - 42, "c1_bash", "Bash", {"command": "pytest -q tests/"}, agent="c1", model="claude-sonnet-5-5"),
    ], meta={"agentType": "general-purpose", "description": LONG, "toolUseId": "tu_c1"})
    S1.write_sub("c2", [
        S1.prompt(T - 288, "Survey", agent="c2"),
        S1.tool(T - 100, "c2_grep", "Grep", {"pattern": "TODO"}, agent="c2", model="claude-haiku-5-5"),
        S1.result(T - 99, "c2_grep", agent="c2"),
        S1.tool(T - 60, "c2_done", "SubagentHandback", {"message": "Found 3 TODOs."}, agent="c2", model="claude-haiku-5-5"),
    ], meta={"agentType": "Explore", "description": "Background survey", "toolUseId": "tu_bg", "requestShape": "background"})
    S1.write_sub("c3", [
        S1.prompt(T - 128, "Check fixtures", agent="c3"),
        S1.tool(T - 8, "c3_glob", "Glob", {"pattern": "**/fixtures/*"}, agent="c3", model="claude-haiku-5-5"),
        S1.result(T - 5, "c3_glob", agent="c3"),
    ], meta={"agentType": "Explore", "description": "Check fixtures", "toolUseId": "c1_spawn"})
    S1.write_sub("w1", [S1.prompt(T - 293, "Plan FO-001", agent="w1"), S1.text(T - 100, "w1m", "plan is ready", agent="w1", model="claude-haiku-5-5", stop=None)],
                 meta={"agentType": "workflow-subagent", "description": "FO-001:plan", "workflowPhase": "Plan"}, run="wf_1")
    S1.write_sub("w2", [S1.prompt(T - 292, "Build FO-001", agent="w2"), S1.tool(T - 10, "w2_bash", "Bash", {"command": "make build"}, agent="w2", model="claude-haiku-5-5")],
                 meta={"agentType": "workflow-subagent", "description": "FO-001:build", "workflowPhase": "Build"}, run="wf_1")
    S1.write_sub("w3", [S1.prompt(T - 291, "Test FO-001", agent="w3"), S1.text(T - 50, "w3m", "cannot", agent="w3", model="claude-haiku-5-5", stop=None)],
                 meta={"agentType": "workflow-subagent"}, run="wf_1")       # no meta description: the journal label names it
    S1.journal("wf_1", [{"type": "launched"},
                        {"type": "started", "agentId": "w1", "label": "FO-001:plan", "phase": "Plan"},
                        {"type": "started", "agentId": "w2", "label": "FO-001:build", "phase": "Build"},
                        {"type": "started", "agentId": "w3", "label": "FO-001:test", "phase": "Test"},
                        {"type": "result", "agentId": "w1", "result": "fine"},
                        {"type": "failed", "agentId": "w3"}])
    S1.summary("wf_1", {"runId": "wf_1", "workflowName": "factory-line", "status": "running", "agentCount": 3,
                        "phases": [{"title": "Plan"}, {"title": "Build"}, {"title": "Test"}]})

    # ── S2: wf_2 (labels in meta only), then a question nobody answered ──
    S2 = FX.S2 = World("alpha", ID2)
    S2.write_main([
        S2.prompt(T - 250, "Build FO-002"),
        S2.tool(T - 245, "tu_wf2", "Workflow", {"script": "export const meta = { name: 'factory-line' }"}),
        S2.result(T - 244, "tu_wf2", "launched", tur={"status": "async_launched", "runId": "wf_2"}),
        S2.tool(T - 30, "s2_ask", "AskUserQuestion", {"questions": [{"question": "Which branch?"}]}),
    ])
    S2.write_sub("v1", [S2.prompt(T - 243, "Build FO-002", agent="v1"), S2.tool(T - 8, "v1_edit", "Edit", {"file_path": "/w/b.py"}, agent="v1", model="claude-haiku-5-5")],
                 meta={"agentType": "workflow-subagent", "description": "FO-002:build", "workflowPhase": "Build"}, run="wf_2")
    S2.write_sub("v2", [S2.prompt(T - 243, "Plan FO-002", agent="v2"), S2.text(T - 90, "v2m", "planned", agent="v2", model="claude-haiku-5-5")],
                 meta={"agentType": "workflow-subagent", "description": "FO-002:plan", "workflowPhase": "Plan"}, run="wf_2")
    S2.summary("wf_2", {"runId": "wf_2", "workflowName": "factory-line", "status": "running", "agentCount": 2,
                        "phases": [{"title": "Plan"}, {"title": "Build"}]})

    # ── S3: a worktree of alpha on the order's lane branch ──
    S3 = FX.S3 = World("alpha-fo-003", ID3, cwd=WT3, branch=BRANCH)
    S3.write_main([S3.prompt(T - 200, "Review the lane"), S3.text(T - 150, "s3m", "Lane looks fine.")])

    # ── beta: idle; gamma, delta: three hours ago; epsilon: two hours ago with an open alert ──
    SB = FX.SB = World("beta", IDB)
    SB.write_main([SB.prompt(T - 160, "Explain the bug"), SB.tool(T - 150, "sb_read", "Read", {"file_path": "/w/cache.py"}),
                   SB.result(T - 149, "sb_read"), SB.text(T - 100, "sbm", "It was a stale cache key.")])
    SG = FX.SG = World("gamma", IDG)
    SG.write_main([SG.prompt(T - 10810, "Old gamma"), SG.text(T - 10800, "sgm", "Done.")])
    SD = FX.SD = World("delta", IDD)
    SD.write_main([SD.prompt(T - 10810, "Old delta"), SD.text(T - 10800, "sdm", "Done.")])
    SO = FX.SO = World("epsilon", IDO)
    SO.write_main([SO.prompt(T - 7210, "Old epsilon"), SO.text(T - 7200, "som", "Done.")])

    # ── hook alerts: a permission on S1's running subagent, one on the off-board session ──
    emit("PermissionRequest", ID1, T - 30, agent="c1", agent_type="general-purpose", tool="Bash", sig="pytest -q tests/",
         message="pytest -q tests/", cwd=S1.cwd)
    emit("PermissionRequest", IDO, T - 60, tool="Bash", sig="make deploy", message="make deploy", cwd=SO.cwd)


# ── the stub factory ─────────────────────────────────────────────────────────
STUB = {"mode": "ok", "orders": {}, "calls": []}


def _project_orders(root, now):
    STUB["calls"].append((root, now))
    if STUB["mode"] == "raise":
        raise RuntimeError("stub factory failure")
    return copy.deepcopy(STUB["orders"].get(root, []))


stub = types.ModuleType("cc_dash.factory")
stub.project_orders = _project_orders
stub.ledger_path = lambda root: None
stub.line_config = lambda root: {}


def use_stub():
    sys.modules["cc_dash.factory"] = stub
    setattr(cc_dash_pkg, "factory", stub)


def hide_factory():
    """What a tree without lib/cc_dash/factory.py looks like to graph.py."""
    if hasattr(cc_dash_pkg, "factory"):
        delattr(cc_dash_pkg, "factory")
    sys.modules["cc_dash.factory"] = None


def mk_stations(**st):
    names = [("plan", "Plan"), ("build", "Build"), ("test", "Test"), ("verify", "Verify"), ("review", "Review")]
    return [{"name": n, "title": t, "status": st.get(n, "pending"),
             "t": (T - 3600.0) if st.get(n) in ("done", "active", "failed") else None} for n, t in names]


def mk_order(oid, title, status, **kw):
    d = {"id": oid, "title": title, "priority": "p2", "status": status, "path": f"factory/orders/{oid}-x.md",
         "created": T - 7200.0, "updated": T - 120.0, "station": "", "stations": mk_stations(), "gate": None, "lane": None,
         "run_id": "", "rework": 0, "events": 4}
    d.update(kw)
    return d


def set_orders():
    STUB["orders"] = {
        ALPHA: [
            mk_order("FO-001", "Add CSV export", "building", run_id="wf_1", stations=mk_stations(plan="done")),
            mk_order("FO-002", "Rate limit the API", "building"),
            mk_order("FO-003", "Fix the login redirect", "review", station="review",
                     stations=mk_stations(plan="done", build="done", test="done", verify="done"),
                     gate={"name": "merge", "waiting": True}, lane={"branch": BRANCH, "worktree": WT3}),
            mk_order("FO-004", "Old finished work", "done", created=T - 3 * 86400.0, updated=T - 2 * 86400.0,
                     stations=mk_stations(plan="done", build="done", test="done", verify="done", review="done")),
        ],
        GAMMA: [mk_order("FO-001", "Gamma first order", "queued", updated=None)],
    }


# ── type helpers ─────────────────────────────────────────────────────────────
def is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def is_str(x):
    return isinstance(x, str)


def is_bool(x):
    return isinstance(x, bool)


def opt(f):
    return lambda x: x is None or f(x)


def lst(f):
    return lambda x: isinstance(x, list) and all(f(i) for i in x)


def fits(obj, schema):
    return isinstance(obj, dict) and all(k in obj and pred(obj[k]) for k, pred in schema.items())


def sub(schema):
    return lambda x: fits(x, schema)


def bad(obj, schema):
    if not isinstance(obj, dict):
        return ["<not a dict>"]
    return [k for k, pred in schema.items() if k not in obj or not pred(obj[k])]


GUARD = {"deny": is_int, "ask": is_int, "warn": is_int}
BURN = {"cost_min": is_num, "tok_min": is_int, "new_min": is_int, "out_min": is_int, "unpriced_min": is_int}
CORE = {"id": is_str, "kind": is_str, "label": is_str, "parent": opt(is_str), "state": is_str, "needs": opt(is_str)}
LIVE = {"model": is_str, "family": is_str, "effort": is_str, "effort_source": is_str, "cost": is_num, "tokens": is_int,
        "burn": sub(BURN), "errors": is_int, "tool": opt(is_str), "target": is_str, "last_text": is_str,
        "since": is_num, "start": is_num, "age": is_num, "spark": lambda x: isinstance(x, list) and len(x) == 15 and all(is_int(i) for i in x)}
PROJECT = {**CORE, "cost": is_num, "tokens": is_int, "detail": sub({
    "root": is_str, "sessions_live": is_int, "sessions_window": is_int, "cost_window": is_num, "tokens_window": is_int,
    "model_mix": lst(sub({"family": is_str, "model": is_str, "tokens": is_int, "cost": opt(is_num)})),
    "guard": sub(GUARD), "errors": is_int,
    "plans": lst(sub({"key": is_str, "title": is_str, "status": is_str, "done": is_int, "total": is_int})),
    "orders": lambda x: isinstance(x, dict) and all(is_str(k) and is_int(v) for k, v in x.items())})}
SESSION = {**CORE, **LIVE, "detail": sub({
    "session": is_str, "project": is_str, "worktree": is_str, "branch": is_str, "cwd": is_str, "prompts": is_int,
    "tool_calls": is_int, "tool_errors": is_int, "api_errors": is_int,
    "usage": sub({"input": is_int, "output": is_int, "cache_read": is_int, "cache_write": is_int}),
    "guard": sub(GUARD), "subagents": is_int, "running": is_int,
    "plan": opt(sub({"title": is_str, "done": is_int, "total": is_int, "status": is_str}))})}
AGENT = {**CORE, **LIVE, "detail": sub({
    "session": is_str, "agent_id": is_str, "type": is_str, "kind": is_str, "phase": is_str, "run_id": is_str,
    "background": is_bool, "waiting_on": is_int, "stalled_for": opt(is_int), "tool_calls": is_int, "order": is_str})}
WORKFLOW = {**CORE, "detail": sub({
    "run_id": is_str, "name": is_str, "status": is_str, "phases": lst(is_str), "total": is_int, "finished": is_int,
    "running": is_int, "failed": is_int, "order": is_str})}
ORDER = {**CORE, "detail": sub({
    "id": is_str, "title": is_str, "priority": is_str, "status": is_str, "path": is_str, "created": opt(is_num),
    "updated": opt(is_num), "station": is_str,
    "stations": lst(sub({"name": is_str, "title": is_str, "status": is_str, "t": opt(is_num)})),
    "gate": opt(sub({"name": is_str, "waiting": is_bool})), "lane": opt(sub({"branch": is_str, "worktree": is_str})),
    "run_id": is_str, "rework": is_int, "events": is_int})}
SCHEMAS = {"project": PROJECT, "session": SESSION, "agent": AGENT, "workflow": WORKFLOW, "order": ORDER}
BOARD = ("tool", "generating", "waiting", "idle", "done", "failed", "stalled")
RUNNING = ("tool", "generating", "waiting")
ORDER_STATES = ("queued", "spec", "approved", "building", "verifying", "review", "done", "blocked", "rejected", "failed")
STATES = {"project": ("working", "needs", "idle"), "session": BOARD, "agent": BOARD,
          "workflow": ("running", "done", "failed"), "order": ORDER_STATES}
PREFIX = {"project": "p:", "session": "s:", "agent": "a:", "workflow": "w:", "order": "o:"}
TOP = {"now", "window_days", "scope", "sig", "kpis", "nodes", "edges", "alerts", "feed", "series", "limits"}
KPIS = {"sessions_live": is_int, "agents": is_int, "working": is_int, "needs": is_int, "stalled": is_int, "failed": is_int,
        "done": is_int, "burn_cost_min": is_num, "burn_tok_min": is_int, "cost_live": is_num, "cost_window": is_num,
        "guard": sub(GUARD), "errors": is_int,
        "orders": lambda x: isinstance(x, dict) and all(is_str(k) and is_int(v) and v > 0 for k, v in x.items())}
EDGE = {"id": is_str, "source": is_str, "target": is_str, "kind": is_str, "active": is_bool, "rate": is_int}
ALERT = {"id": is_str, "node": is_str, "kind": is_str, "title": is_str, "message": is_str, "tool": opt(is_str), "age": is_num}
FEED = {"t": is_num, "key": is_str, "id": is_str, "label": is_str, "kind": is_str, "project": is_str, "family": is_str,
        "name": is_str, "target": is_str, "status": is_str, "dur": opt(is_num), "node": is_str}
PAIRS = {("p", "s"): "member", ("p", "o"): "member", ("s", "a"): "spawn", ("a", "a"): "spawn", ("s", "w"): "spawn",
         ("a", "w"): "spawn", ("w", "a"): "step", ("o", "w"): "runs", ("o", "s"): "runs"}


# ── helpers over a payload ───────────────────────────────────────────────────
def by_id(p):
    return {n["id"]: n for n in p["nodes"]}


def ids(p):
    return {n["id"] for n in p["nodes"]}


def kind_of(p, kind):
    return [n for n in p["nodes"] if n["kind"] == kind]


def under(nodes, nid, root):
    seen = set()
    while nid is not None and nid not in seen:
        if nid == root:
            return True
        seen.add(nid)
        n = nodes.get(nid)
        nid = n["parent"] if n else None
    return False


def node_of_row(key):
    return ("a:" + key) if "/" in key else ("s:" + key)


def fresh_store(days=1):
    st = Store(days=days, clock=lambda: NOW[0])
    st.refresh(force=True)
    return st


def poll(st, **kw):
    return graph.payload(st, **kw)


def set_clock(st, t):
    NOW[0] = float(t)
    st.refresh(force=True)


# ── 1. shape and types ───────────────────────────────────────────────────────
def test_shape(st):
    p = poll(st)
    check("shape: top-level keys are exactly the spec's", set(p) == TOP, sorted(set(p) ^ TOP))
    check("shape: now is the store clock, window_days the lookback", p["now"] == NOW[0] and is_num(p["now"]) and p["window_days"] == 1,
          (p["now"], p["window_days"]))
    check("shape: scope.project is empty by default", isinstance(p["scope"], dict) and p["scope"].get("project") == "", p["scope"])
    check("shape: sig is a hex string", is_str(p["sig"]) and re.fullmatch(r"[0-9a-f]{8,}", p["sig"]) is not None, p["sig"])
    try:
        json.dumps(p, allow_nan=False)
        ok = True
    except (TypeError, ValueError) as e:
        ok = False
        print("json:", e)
    check("shape: the payload is JSON without NaN or Infinity", ok)
    k = p["kpis"]
    check("shape: kpis keys are exactly the spec's", set(k) == set(KPIS), sorted(set(k) ^ set(KPIS)))
    check("shape: kpis types (counts are ints, rates numbers, orders only non-zero)", not bad(k, KPIS), bad(k, KPIS))
    check("shape: limits carry the board's live_secs and linger",
          isinstance(p["limits"], dict) and p["limits"].get("live_secs") == store_mod.LIVE_SECS == 600
          and p["limits"].get("linger") == store_mod.LINGER_SECS == 120, p["limits"])
    for kind, schema in SCHEMAS.items():
        nodes = kind_of(p, kind)
        probs = [(n["id"], bad(n, schema)) for n in nodes if bad(n, schema)]
        check(f"shape: every {kind} node has the right fields and types ({len(nodes)} nodes)", nodes and not probs, probs[:3])
        badstate = [(n["id"], n["state"]) for n in nodes if n["state"] not in STATES[kind]]
        check(f"shape: {kind} states stay in their vocabulary", not badstate, badstate[:3])
    unknown = [n.get("kind") for n in p["nodes"] if n.get("kind") not in SCHEMAS]
    check("shape: no node of an unknown kind", not unknown, unknown)
    probs = [(e.get("id"), bad(e, EDGE)) for e in p["edges"] if bad(e, EDGE)]
    check(f"shape: every edge has id/source/target/kind/active/rate with the right types ({len(p['edges'])} edges)",
          p["edges"] and not probs, probs[:3])
    probs = [(a.get("id"), bad(a, ALERT)) for a in p["alerts"] if bad(a, ALERT)]
    check(f"shape: every alert has id/node/kind/title/message/tool/age ({len(p['alerts'])} alerts)", p["alerts"] and not probs, probs[:3])
    probs = [(f.get("id"), bad(f, FEED)) for f in p["feed"] if bad(f, FEED)]
    check(f"shape: every feed row carries the board's fields plus node ({len(p['feed'])} rows)", p["feed"] and not probs, probs[:3])
    check("shape: the feed is the board's: newest first, at most 50",
          len(p["feed"]) <= store_mod.FEED_MAX and all(p["feed"][i]["t"] >= p["feed"][i + 1]["t"] for i in range(len(p["feed"]) - 1)))
    nodes = by_id(p)
    probs = [n["id"] for n in p["nodes"] if n["kind"] == "order" and n["state"] != n["detail"].get("status")]
    check("shape: an order node's state is its status", not probs, probs)
    probs = [n["id"] for n in p["nodes"] if n["kind"] == "order" and n["label"] != n["detail"].get("title")]
    check("shape: an order node's label is its title", not probs, probs)
    probs = [(n["id"], n["detail"].get("session")) for n in p["nodes"] if n["kind"] in ("session", "agent") and n["id"].split(":", 1)[1].split("/")[0] != n["detail"].get("session")]
    check("shape: session and agent detail.session is the id's session", not probs, probs[:3])
    probs = [n["id"] for n in p["nodes"] if n["kind"] in ("workflow", "order") and n["needs"] is not None]
    check("shape: workflows and orders never carry needs", not probs, probs)
    long_ = nodes.get(an(ID1, "c1"))
    check("shape: a 5000-character description is clipped to 200", long_ is not None and 0 < len(long_["label"]) <= 200
          and long_["label"].startswith("Write the tests"), long_ and len(long_["label"]))
    check("shape: no label anywhere is longer than 200", all(len(n["label"]) <= 200 for n in p["nodes"]),
          [(n["id"], len(n["label"])) for n in p["nodes"] if len(n["label"]) > 200][:3])
    s = p.get("series")
    check("shape: series follows the contract (keys, 20 non-negative ints per list, aligned start holding now, family sums, errors <= calls)",
          not series_probs(s, p["now"]), series_probs(s, p["now"]))
    check("shape: series counts the fixture's hour: alpha's and beta's 12 transcripts, 16 tool calls, c1's failed Edit (gamma, delta, epsilon are older)",
          not series_probs(s, p["now"]) and s["agents_total"] == 12 and sum(s["tokens"]) > 0 and sum(s["tool_calls"]) == 16
          and sum(s["tool_errors"]) == 1
          and sum(s["tokens"]) == sum(nodes[sn(x)]["tokens"] for x in (ID1, ID2, ID3, IDB))   # every reply of theirs is in the hour
          and set(s["tokens_by_family"]) == {"opus", "sonnet", "haiku"}, s)
    return p


# ── 2. ids ───────────────────────────────────────────────────────────────────
def test_ids(p):
    seen = [n["id"] for n in p["nodes"]]
    check("ids: unique", len(seen) == len(set(seen)), [i for i in seen if seen.count(i) > 1][:3])
    probs = [(n["id"], n["kind"]) for n in p["nodes"] if not n["id"].startswith(PREFIX.get(n["kind"], "?:"))]
    check("ids: the prefix matches the kind (p: s: a: w: o:)", not probs, probs[:3])
    probs = [n["id"] for n in p["nodes"] if n["kind"] in ("agent", "workflow", "order") and "/" not in n["id"]]
    check("ids: agents, workflows and orders are <scope>/<name>", not probs, probs)
    probs = [n["id"] for n in p["nodes"] if n["kind"] == "project" and n["id"] != "p:" + n["label"]]
    check("ids: a project's id is p:<its label>", not probs, probs)
    want = {sn(ID1), sn(ID2), sn(ID3), sn(IDB), sn(IDO), an(ID1, "c1"), an(ID1, "c2"), an(ID1, "c3"), an(ID1, "w1"),
            an(ID1, "w2"), an(ID1, "w3"), an(ID2, "v1"), an(ID2, "v2"), wn(ID1, "wf_1"), wn(ID2, "wf_2"),
            "p:alpha", "p:beta", "p:gamma", "p:epsilon", on("alpha", "FO-001"), on("alpha", "FO-002"), on("alpha", "FO-003"),
            on("gamma", "FO-001")}
    check("ids: the default graph holds exactly the fixture's nodes", ids(p) == want, (sorted(ids(p) - want), sorted(want - ids(p))))
    st = fresh_store()
    p2 = poll(st)
    check("ids: the same set on a second poll (fixed clock, nothing changed)", ids(p2) == ids(p), sorted(ids(p) ^ ids(p2)))


# ── 3. sig ───────────────────────────────────────────────────────────────────
def test_sig():
    st = fresh_store()
    p1 = poll(st)
    p2 = poll(st)
    check("sig: identical across two polls with nothing changed", p1["sig"] == p2["sig"], (p1["sig"], p2["sig"]))
    check("sig: the other fields agree too (nodes, edges, alerts, kpis)",
          all(p1[k] == p2[k] for k in ("nodes", "edges", "alerts", "kpis")),
          [k for k in ("nodes", "edges", "alerts", "kpis") if p1[k] != p2[k]])
    # A moving clock: ages tick (left out of the sig); burn rates and spark buckets are windows over
    # the clock, so the sig follows them exactly when their numbers change, and holds when they do not.
    set_clock(st, T + 1)
    p3 = poll(st)
    set_clock(st, T)

    def visible(p):
        out = []
        for n in p["nodes"]:
            n = {k: v for k, v in n.items() if k != "age"}
            if n["kind"] == "agent":
                n["detail"] = dict(n["detail"], stalled_for=n["detail"]["stalled_for"] is not None)
            out.append(n)
        return (out, p["edges"], p["kpis"], p["feed"], p["series"], [{k: v for k, v in a.items() if k != "age"} for a in p["alerts"]])
    same = visible(p1) == visible(p3)
    check("sig: a 1 s clock move changes the sig exactly when something besides the ages changed (here: burn rates)",
          (p3["sig"] == p1["sig"]) == same and not same and ids(p3) == ids(p1), (p1["sig"], p3["sig"], same))
    check("sig: but now moves with the clock", p3["now"] == T + 1 and p1["now"] == T, (p1["now"], p3["now"]))
    # A quiet floor: past every burn window (5 min after the last message) and inside one spark minute.
    set_clock(st, T + 335)
    q1 = poll(st)
    set_clock(st, T + 336)
    q2 = poll(st)
    set_clock(st, T)
    check("sig: on a quiet floor (nothing burning) a 1 s clock move keeps the sig while the ages tick",
          q1["sig"] == q2["sig"] and q1["kpis"]["burn_tok_min"] == 0 and visible(q1) == visible(q2)
          and any(n.get("age") != m.get("age") for n, m in zip(q1["nodes"], q2["nodes"])), (q1["sig"], q2["sig"], q1["kpis"]["burn_tok_min"]))
    # An order change reaches the sig (the stub is read fresh: ORDERS_TTL is 0) and reverting restores it.
    base = poll(st)["sig"]
    STUB["orders"][ALPHA][1]["status"] = "verifying"
    p4 = poll(st)
    STUB["orders"][ALPHA][1]["status"] = "building"
    p5 = poll(st)
    check("sig: changes when an order's status changes", p4["sig"] != base, p4["sig"])
    check("sig: returns to the same value when it changes back", p5["sig"] == base, (base, p5["sig"]))
    # An append to a live transcript changes it; the structure (the id set) does not.
    base_ids = ids(p1)
    World.write(FX.S1.sub_path("c3"), [FX.S1.tool(T - 2, "c3_g2", "Grep", {"pattern": "extra"}, agent="c3", model="claude-haiku-5-5")], append=True)
    st.refresh(force=True)
    p6 = poll(st)
    check("sig: changes after a record is appended to a live transcript", p6["sig"] != base, p6["sig"])
    check("ids: the same set after an append that does not change the structure", ids(p6) == base_ids, sorted(ids(p6) ^ base_ids))
    c3 = by_id(p6)[an(ID1, "c3")]
    check("sig: the append shows on the agent (now running a Grep)", c3["state"] == "tool" and c3["tool"] == "Grep", (c3["state"], c3["tool"]))
    p7 = poll(st)
    check("sig: stable again once the append has been read", p7["sig"] == p6["sig"], (p6["sig"], p7["sig"]))


# ── 4. integrity ─────────────────────────────────────────────────────────────
def rate_of(nodes, edges, nid, memo=None):
    memo = {} if memo is None else memo
    if nid in memo:
        return memo[nid]
    n = nodes[nid]
    if n["kind"] in ("session", "agent"):
        r = n["burn"]["tok_min"]
    elif n["kind"] == "workflow":
        r = sum(rate_of(nodes, edges, e["target"], memo) for e in edges if e["source"] == nid and e["kind"] == "step")
    elif n["kind"] == "order":
        r = sum(rate_of(nodes, edges, e["target"], memo) for e in edges if e["source"] == nid and e["kind"] == "runs")
    else:
        r = 0
    memo[nid] = r
    return r


def test_integrity(p, label=""):
    nodes, edges = by_id(p), p["edges"]
    tag = f"edges{label}"
    dangling = [e["id"] for e in edges if e["source"] not in nodes or e["target"] not in nodes]
    check(f"{tag}: every edge's source and target is a node id", not dangling, dangling[:3])
    eids = [e["id"] for e in edges]
    check(f"{tag}: edge ids are <source>><target> and unique",
          all(e["id"] == f"{e['source']}>{e['target']}" for e in edges) and len(eids) == len(set(eids)),
          [e["id"] for e in edges if e["id"] != f"{e['source']}>{e['target']}"][:3])
    probs = [(e["id"], e["kind"]) for e in edges if PAIRS.get((e["source"][0], e["target"][0])) != e["kind"]]
    check(f"{tag}: kinds follow the pair (p>s member, s>a spawn, w>a step, o>w runs ...)", not probs, probs[:4])
    probs = [n["id"] for n in p["nodes"] if (n["parent"] is None) != (n["kind"] == "project")]
    check(f"{tag}: only project nodes have no parent", not probs, probs[:3])
    probs = []
    for n in p["nodes"]:
        if n["parent"] is None:
            continue
        mine = [e for e in edges if e["target"] == n["id"] and e["kind"] in ("member", "spawn", "step")]
        if len(mine) != 1 or mine[0]["source"] != n["parent"]:
            probs.append((n["id"], n["parent"], [e["source"] for e in mine]))
    check(f"{tag}: every child has exactly one parent edge, from its parent", not probs, probs[:3])
    missing = [n["id"] for n in p["nodes"] if n["parent"] is not None and n["parent"] not in nodes]
    check(f"{tag}: every parent is a node", not missing, missing[:3])
    probs = [e["id"] for e in edges if e["active"] != _active(nodes[e["target"]])]
    check(f"{tag}: active means the target is running (state, or order in building/verifying/review)", not probs, probs[:4])
    memo = {}
    probs = [(e["id"], e["rate"], rate_of(nodes, edges, e["target"], memo)) for e in edges if e["rate"] != rate_of(nodes, edges, e["target"], memo)]
    check(f"{tag}: rate is the target's tok_min (workflow: its agents', order: its runs targets')", not probs, probs[:4])


def _active(n):
    if n["kind"] in ("session", "agent"):
        return n["state"] in RUNNING
    if n["kind"] == "workflow":
        return n["state"] == "running"
    if n["kind"] == "order":
        return n["state"] in ("building", "verifying", "review")
    return False


def test_tree(p):
    nodes = by_id(p)
    eids = {e["id"]: e for e in p["edges"]}
    want = [(sn(ID1), "p:alpha"), (sn(ID2), "p:alpha"), (sn(ID3), "p:alpha"), (sn(IDB), "p:beta"), (sn(IDO), "p:epsilon"),
            (an(ID1, "c1"), sn(ID1)), (an(ID1, "c2"), sn(ID1)), (an(ID1, "c3"), an(ID1, "c1"))]
    probs = [(c, nodes.get(c, {}).get("parent"), par) for c, par in want if nodes.get(c, {}).get("parent") != par]
    check("tree: sessions under their project (a worktree folds into its repo), subagents under who spawned them", not probs, probs)
    e = eids.get(f"{an(ID1, 'c1')}>{an(ID1, 'c3')}")
    check("tree: the nested subagent hangs off its parent agent by a spawn edge (a: > a:)", e is not None and e["kind"] == "spawn", e)
    e = eids.get(f"p:alpha>{sn(ID3)}")
    check("tree: member edge from the project to a worktree session", e is not None and e["kind"] == "member", e)
    s3 = nodes[sn(ID3)]
    check("tree: the worktree session carries its worktree and branch", s3["detail"]["worktree"] == "fo-003" and s3["detail"]["branch"] == BRANCH,
          (s3["detail"]["worktree"], s3["detail"]["branch"]))
    check("tree: project node labels are the store's project labels", all(nodes[f"p:{x}"]["label"] == x for x in ("alpha", "beta", "gamma", "epsilon")))
    check("tree: no stray orphans: every non-project node reaches a project", all(
        any(under(nodes, n["id"], pid) for pid in nodes if nodes[pid]["kind"] == "project") for n in p["nodes"]))


# ── 5. workflow agents ───────────────────────────────────────────────────────
def test_workflows(p):
    nodes = by_id(p)
    wagents = [n for n in kind_of(p, "agent") if n["detail"]["run_id"]]
    probs = []
    for a in wagents:
        sid, run = a["detail"]["session"], a["detail"]["run_id"]
        w = nodes.get(wn(sid, run))
        if a["parent"] != wn(sid, run) or w is None or w["parent"] != sn(sid):
            probs.append((a["id"], a["parent"], w and w["parent"]))
    check("wf: every workflow agent hangs under w:<sid>/<run>, whose parent is the main thread s:<sid>", wagents and not probs, probs[:3])
    check("wf: five workflow agents (wf_1: w1 w2 w3, wf_2: v1 v2)", {a["id"] for a in wagents} == {
        an(ID1, "w1"), an(ID1, "w2"), an(ID1, "w3"), an(ID2, "v1"), an(ID2, "v2")}, sorted(a["id"] for a in wagents))
    check("wf: a workflow agent's detail.kind is workflow, a plain one's is sub",
          all(a["detail"]["kind"] == "workflow" for a in wagents)
          and all(nodes[an(ID1, x)]["detail"]["kind"] == "sub" for x in ("c1", "c2", "c3")))
    for sid, run in ((ID1, "wf_1"), (ID2, "wf_2")):
        w = nodes[wn(sid, run)]
        d = w["detail"]
        check(f"wf: {run} is a running workflow node named factory-line",
              w["state"] == "running" and w["label"] == "factory-line" and d["name"] == "factory-line" and d["run_id"] == run
              and d["status"] == "running", (w["state"], w["label"], d))
    d = nodes[wn(ID1, "wf_1")]["detail"]
    check("wf: wf_1 progress ints: total 3, finished 2, running 1, failed 1",
          (d["total"], d["finished"], d["running"], d["failed"]) == (3, 2, 1, 1), d)
    check("wf: wf_1 phases come from the run summary", d["phases"] == ["Plan", "Build", "Test"], d["phases"])
    d = nodes[wn(ID2, "wf_2")]["detail"]
    check("wf: wf_2 progress counts are ints (total 2, one agent running)", d["total"] == 2 and d["running"] >= 1, d)
    n1 = {x: nodes[an(ID1, x)] for x in ("w1", "w2", "w3")}
    check("wf: agent states follow the journal and the transcript (done / tool / failed)",
          (n1["w1"]["state"], n1["w2"]["state"], n1["w3"]["state"]) == ("done", "tool", "failed"),
          [n1[x]["state"] for x in ("w1", "w2", "w3")])
    check("wf: labels: meta description for w1 and w2, the journal's label when meta has none (w3)",
          (n1["w1"]["label"], n1["w2"]["label"], n1["w3"]["label"]) == ("FO-001:plan", "FO-001:build", "FO-001:test"),
          [n1[x]["label"] for x in ("w1", "w2", "w3")])
    check("wf: phase on the agent, from meta or the journal",
          (n1["w1"]["detail"]["phase"], n1["w2"]["detail"]["phase"], n1["w3"]["detail"]["phase"]) == ("Plan", "Build", "Test"),
          [n1[x]["detail"]["phase"] for x in ("w1", "w2", "w3")])
    e = {e["id"]: e for e in p["edges"]}
    step = e.get(f"{wn(ID1, 'wf_1')}>{an(ID1, 'w2')}")
    check("wf: step edge from the workflow to its running agent is active", step is not None and step["kind"] == "step" and step["active"] is True, step)
    spawn = e.get(f"{sn(ID1)}>{wn(ID1, 'wf_1')}")
    check("wf: spawn edge from the main thread to the workflow", spawn is not None and spawn["kind"] == "spawn" and spawn["active"] is True, spawn)
    w1 = nodes[wn(ID1, "wf_1")]
    check("wf: the workflow's own parent edge rate sums its agents' tok_min",
          spawn is not None and spawn["rate"] == sum(n["burn"]["tok_min"] for n in n1.values()), (spawn and spawn["rate"], w1["id"]))


# ── 6. alerts ────────────────────────────────────────────────────────────────
def test_alerts(p):
    nodes = by_id(p)
    al = {a["node"]: a for a in p["alerts"]}
    check("alerts: exactly three open (hook on c1, question on S2, hook on the off-board session)",
          len(p["alerts"]) == 3 and set(al) == {an(ID1, "c1"), sn(ID2), sn(IDO)}, sorted(al))
    a = al.get(an(ID1, "c1"), {})
    check("alerts: the hook PermissionRequest attaches to a:<sid>/<aid> and is a Bash permission",
          a.get("kind") == "permission" and a.get("tool") == "Bash" and a.get("title") and a.get("id", "").startswith("h-"), a)
    check("alerts: that agent's needs is permission", nodes[an(ID1, "c1")]["needs"] == "permission", nodes[an(ID1, "c1")]["needs"])
    check("alerts: its age is the seconds since the event", abs(a.get("age", -1) - 30) < 1.0, a.get("age"))
    q = al.get(sn(ID2), {})
    check("alerts: the pending AskUserQuestion attaches to the session s:<sid> as a question",
          q.get("kind") == "question" and q.get("tool") == "AskUserQuestion", q)
    check("alerts: that session's needs is question", nodes[sn(ID2)]["needs"] == "question", nodes[sn(ID2)]["needs"])
    o = al.get(sn(IDO), {})
    check("alerts: the off-board session's alert attaches to s:<sid>", o.get("kind") == "permission", o)
    so = nodes.get(sn(IDO))
    check("alerts: so the off-board session is in the graph, idle, with needs permission",
          so is not None and so["needs"] == "permission" and so["state"] == "idle" and so["tool"] is None and so["parent"] == "p:epsilon",
          so and (so["needs"], so["state"], so["tool"], so["parent"]))
    check("alerts: and it is idle with no burn and a flat sparkline",
          so is not None and so["spark"] == [0] * 15 and all(v == 0 for v in so["burn"].values()), so and (so["spark"], so["burn"]))
    check("alerts: every alert's node exists in the graph", all(x["node"] in nodes for x in p["alerts"]))
    check("alerts: the owning projects are in state needs (alpha, epsilon)",
          nodes["p:alpha"]["state"] == "needs" and nodes["p:epsilon"]["state"] == "needs",
          (nodes["p:alpha"]["state"], nodes["p:epsilon"]["state"]))
    check("alerts: a project without alerts is not in needs", nodes["p:beta"]["state"] != "needs" and nodes["p:gamma"]["state"] != "needs")
    need = {n["id"] for n in p["nodes"] if n["kind"] in ("session", "agent") and n["needs"]}
    check("alerts: needs is set on exactly the three alerted session/agent nodes", need == {an(ID1, "c1"), sn(ID2), sn(IDO)}, sorted(need))


# ── board and Overview agreement ─────────────────────────────────────────────
def test_board(st):
    p = poll(st)
    lp = st.live_payload()
    nodes = by_id(p)
    rows = {r["key"]: r for r in lp["agents"]}
    alert_kind = {a["node"]: a["kind"] for a in p["alerts"]}
    probs = [k for k in rows if node_of_row(k) not in nodes]
    check(f"board: every board row ({len(rows)}) is a node", not probs, probs[:3])
    probs = []
    for key, r in rows.items():
        n = nodes.get(node_of_row(key))
        if n is None or not r["agent_id"]:
            continue
        want_needs = alert_kind.get(n["id"]) or (r["needs"] or {}).get("kind")
        got = (n["state"], n["needs"], n["tokens"], round(n["cost"], 3), n["label"], n["errors"], n["since"], n["spark"], n["tool"],
               {k: n["burn"][k] for k in BURN}, n["detail"]["run_id"], n["detail"]["phase"], n["detail"]["type"],
               n["detail"]["waiting_on"], n["detail"]["stalled_for"], n["detail"]["background"])
        exp = (r["state"], want_needs, r["tokens"], round(r["cost"], 3), r["label"], r["errors"], r["since"], [b["n"] for b in r["spark"]],
               (r["tool"] or {}).get("name"), {k: r["burn"][k] for k in BURN}, r["run_id"], r["phase"], r["type"],
               r["waiting_on"], r["stalled_for"], r["background"])
        if got != exp:
            probs.append((key[-12:], [(i, g, e) for i, (g, e) in enumerate(zip(got, exp)) if g != e][:2]))
    check("board: each agent node carries its board row's state, tokens, cost, burn, spark, tool, phase, run", not probs, probs[:3])
    probs = []
    for sid in (ID1, ID2, ID3, IDB):
        n, r = nodes.get(sn(sid)), rows.get(sid)
        if n is None or r is None:
            probs.append((sid[:4], "missing"))
            continue
        mine = [x for k, x in rows.items() if x["session"] == sid]
        burn = {k: sum(x["burn"][k] for x in mine) for k in BURN}
        spark = [sum(x["spark"][i]["n"] for x in mine) for i in range(15)]
        if (n["state"], n["since"], n["start"], n["last_text"], n["tool"]) != (r["state"], r["since"], r["start"], r["last_text"], (r["tool"] or {}).get("name")):
            probs.append((sid[:4], "main-thread live fields", n["state"], r["state"]))
        if any(abs(n["burn"][k] - burn[k]) > 1e-3 for k in BURN):
            probs.append((sid[:4], "burn", n["burn"], burn))
        if n["spark"] != spark:
            probs.append((sid[:4], "spark", n["spark"], spark))
    check("board: a session node takes the main thread's live fields and sums burn and spark over its rows", not probs, probs[:3])
    groups = {g["session"]: g for g in lp["groups"]}
    probs = [(sid[:4], nodes[sn(sid)]["cost"], g["cost"], nodes[sn(sid)]["tokens"], g["tokens"]) for sid, g in groups.items()
             if sn(sid) in nodes and (abs(nodes[sn(sid)]["cost"] - g["cost"]) > 1e-3 or nodes[sn(sid)]["tokens"] != g["tokens"])]
    check("board: a session's cost and tokens cover the whole session, subagents included", groups and not probs, probs[:3])
    check("board: the lead waits on its subagent, c1 runs Bash, c3 generates, c2 is done",
          (nodes[sn(ID1)]["state"], nodes[an(ID1, "c1")]["state"], nodes[an(ID1, "c3")]["state"], nodes[an(ID1, "c2")]["state"]) ==
          ("waiting", "tool", "generating", "done"),
          [nodes[x]["state"] for x in (sn(ID1), an(ID1, "c1"), an(ID1, "c3"), an(ID1, "c2"))])
    c1 = nodes[an(ID1, "c1")]
    check("board: c1 identity and counters (Bash target, type, one error, four calls)",
          c1["tool"] == "Bash" and "pytest" in c1["target"] and c1["detail"]["type"] == "general-purpose" and c1["errors"] == 1
          and c1["detail"]["tool_calls"] == 4 and c1["family"] == "sonnet", (c1["tool"], c1["target"], c1["errors"], c1["detail"]["tool_calls"]))
    feed_nodes = [f["node"] for f in p["feed"]]
    check("board: every feed row's node exists, s:<sid> for a main thread, a:<sid>/<aid> for a subagent",
          all(f["node"] in nodes and f["node"] == node_of_row(f["key"]) for f in p["feed"]), [f for f in p["feed"] if f["node"] not in nodes][:2])
    check("board: the feed has the board's rows", [f["id"] for f in p["feed"]] == [f["id"] for f in lp["feed"]] and feed_nodes,
          (len(p["feed"]), len(lp["feed"])))
    # kpis from the nodes
    k = p["kpis"]
    sa = [n for n in p["nodes"] if n["kind"] in ("session", "agent")]

    def bucket(n):
        return "needs" if n["needs"] else "working" if n["state"] in RUNNING else n["state"]
    cnt = {}
    for n in sa:
        cnt[bucket(n)] = cnt.get(bucket(n), 0) + 1
    got = {x: k[x] for x in ("agents", "working", "needs", "stalled", "failed", "done")}
    exp = {"agents": len(sa), "working": cnt.get("working", 0), "needs": cnt.get("needs", 0), "stalled": cnt.get("stalled", 0),
           "failed": cnt.get("failed", 0), "done": cnt.get("done", 0)}
    check("kpis: agents/working/needs/stalled/failed/done are the node buckets (needs first, idle not reported)", got == exp, (got, exp))
    check("kpis: fixture numbers (13 session/agent nodes: 3 need you, 4 work, w3 failed, c2 w1 v2 done, none stalled)",
          (k["agents"], k["needs"], k["working"], k["failed"], k["done"], k["stalled"]) == (13, 3, 4, 1, 3, 0), k)
    live = sum(1 for s in st.sessions.values() if NOW[0] - s.last_write < store_mod.LIVE_SECS)
    check("kpis: sessions_live counts sessions written under LIVE_SECS ago (S1 S2 S3 beta), not the 2 h-old alerted one",
          k["sessions_live"] == live == 4, (k["sessions_live"], live))
    boards = [x for x in rows.values()]
    check("kpis: burn_cost_min and burn_tok_min sum the board rows",
          abs(k["burn_cost_min"] - sum(r["burn"]["cost_min"] for r in boards)) < 1e-3 and k["burn_tok_min"] == sum(r["burn"]["tok_min"] for r in boards),
          (k["burn_cost_min"], sum(r["burn"]["cost_min"] for r in boards), k["burn_tok_min"], sum(r["burn"]["tok_min"] for r in boards)))
    check("kpis: cost_live sums the session nodes' cost",
          abs(k["cost_live"] - sum(n["cost"] for n in p["nodes"] if n["kind"] == "session")) < 1e-3,
          (k["cost_live"], sum(n["cost"] for n in p["nodes"] if n["kind"] == "session")))
    pall = poll(st, all_projects=True)
    pn = [n for n in pall["nodes"] if n["kind"] == "project"]
    check("kpis: cost_window is the whole window's (every project's cost_window, dormant hubs included), whatever all=1 says",
          abs(k["cost_window"] - sum(n["detail"]["cost_window"] for n in pn)) <= 1e-4 * len(pn) and pall["kpis"]["cost_window"] == k["cost_window"]
          and abs(k["cost_window"] - st.overview_payload("")["totals"]["cost"]) < 1e-3,
          (k["cost_window"], sum(n["detail"]["cost_window"] for n in pn), pall["kpis"]["cost_window"]))
    check("kpis: errors counts the window's tool errors (c1's failed Edit), guard is zero here", k["errors"] >= 1 and k["guard"] == {"deny": 0, "ask": 0, "warn": 0},
          (k["errors"], k["guard"]))
    # projects
    ov = st.overview_payload("alpha")
    a = nodes["p:alpha"]
    d = a["detail"]
    check("project: alpha's window numbers are the Overview's (cost, tokens)",
          abs(d["cost_window"] - ov["totals"]["cost"]) < 1e-3 and d["tokens_window"] == ov["totals"]["tokens"],
          (d["cost_window"], ov["totals"]["cost"], d["tokens_window"], ov["totals"]["tokens"]))
    check("project: cost and tokens repeat detail.cost_window and tokens_window", a["cost"] == d["cost_window"] and a["tokens"] == d["tokens_window"], (a["cost"], d["cost_window"]))
    check("project: model_mix lists the families the sessions used (opus, sonnet, haiku)",
          {m["family"] for m in d["model_mix"]} == {"opus", "sonnet", "haiku"} and sum(m["tokens"] for m in d["model_mix"]) == ov["totals"]["tokens"],
          d["model_mix"])
    check("project: sessions_window counts every session in the window, sessions_live the live ones",
          (d["sessions_live"], d["sessions_window"]) == (3, 3) and
          (nodes["p:beta"]["detail"]["sessions_live"], nodes["p:beta"]["detail"]["sessions_window"]) == (1, 1) and
          (nodes["p:gamma"]["detail"]["sessions_live"], nodes["p:gamma"]["detail"]["sessions_window"]) == (0, 1) and
          (nodes["p:epsilon"]["detail"]["sessions_live"], nodes["p:epsilon"]["detail"]["sessions_window"]) == (0, 1),
          [(x, nodes[f"p:{x}"]["detail"]["sessions_live"], nodes[f"p:{x}"]["detail"]["sessions_window"]) for x in ("alpha", "beta", "gamma", "epsilon")])
    check("project: root is the project root", a["detail"]["root"] == tilde(ALPHA) and nodes["p:gamma"]["detail"]["root"] == tilde(GAMMA),
          (a["detail"]["root"], tilde(ALPHA)))
    check("project: states (alpha needs, beta idle, gamma idle)", (a["state"], nodes["p:beta"]["state"], nodes["p:gamma"]["state"]) == ("needs", "idle", "idle"),
          (a["state"], nodes["p:beta"]["state"], nodes["p:gamma"]["state"]))
    plans = a["detail"]["plans"]
    check("project: the plan alpha's session read shows in detail.plans", len(plans) >= 1 and plans[0]["total"] >= 2 and plans[0]["title"], plans)
    s1 = nodes[sn(ID1)]["detail"]
    check("project: session detail.plan is None or the plan it works on", s1["plan"] is None or s1["plan"]["total"] >= 2, s1["plan"])
    return p


# ── 7. orders ────────────────────────────────────────────────────────────────
def test_orders(st):
    graph.ORDERS_TTL = 0
    STUB["calls"].clear()
    p = poll(st)
    nodes = by_id(p)
    edges = {e["id"]: e for e in p["edges"]}
    roots = [c[0] for c in STUB["calls"]]
    check("orders: factory.project_orders is called with each project's root (worktrees folded in)",
          ALPHA in roots and GAMMA in roots and WT3 not in roots and all(is_num(c[1]) for c in STUB["calls"]), roots)
    o1, o2, o3 = (nodes.get(on("alpha", x)) for x in ("FO-001", "FO-002", "FO-003"))
    check("orders: alpha's three open orders are nodes under the project with their status as state",
          all(o is not None for o in (o1, o2, o3)) and (o1["state"], o2["state"], o3["state"]) == ("building", "building", "review")
          and all(o["parent"] == "p:alpha" for o in (o1, o2, o3)), (o1 and o1["state"], o2 and o2["state"], o3 and o3["state"]))
    check("orders: member edges project > order, active for building/review",
          all(edges.get(f"p:alpha>{on('alpha', x)}", {}).get("kind") == "member" for x in ("FO-001", "FO-002", "FO-003"))
          and edges[f"p:alpha>{on('alpha', 'FO-001')}"]["active"] is True and edges[f"p:alpha>{on('alpha', 'FO-003')}"]["active"] is True)
    runs = {e["id"] for e in p["edges"] if e["kind"] == "runs"}
    want = {f"{on('alpha', 'FO-001')}>{wn(ID1, 'wf_1')}", f"{on('alpha', 'FO-002')}>{wn(ID2, 'wf_2')}", f"{on('alpha', 'FO-003')}>{sn(ID3)}"}
    check("orders: exactly three runs edges (by run id, by agent label, by branch)", runs == want, (sorted(runs - want), sorted(want - runs)))
    e = edges.get(f"{on('alpha', 'FO-001')}>{wn(ID1, 'wf_1')}")
    check("orders: FO-001 runs wf_1 through its ledger run_id (active, rate = the workflow's)",
          e is not None and e["kind"] == "runs" and e["active"] is True and e["rate"] == rate_of(nodes, p["edges"], wn(ID1, "wf_1")), e)
    e = edges.get(f"{on('alpha', 'FO-002')}>{wn(ID2, 'wf_2')}")
    check("orders: FO-002 (run_id empty) runs wf_2 through its agents' FO-002:... labels", e is not None and e["kind"] == "runs" and e["active"] is True, e)
    e = edges.get(f"{on('alpha', 'FO-003')}>{sn(ID3)}")
    check("orders: FO-003 runs the session on its lane branch", e is not None and e["kind"] == "runs", e)
    check("orders: workflow detail.order is the linked order (wf_1 FO-001, wf_2 FO-002)",
          nodes[wn(ID1, "wf_1")]["detail"]["order"] == "FO-001" and nodes[wn(ID2, "wf_2")]["detail"]["order"] == "FO-002",
          (nodes[wn(ID1, "wf_1")]["detail"]["order"], nodes[wn(ID2, "wf_2")]["detail"]["order"]))
    check("orders: so do its agents' detail.order, and a plain subagent has none",
          all(nodes[an(ID1, x)]["detail"]["order"] == "FO-001" for x in ("w1", "w2", "w3"))
          and all(nodes[an(ID2, x)]["detail"]["order"] == "FO-002" for x in ("v1", "v2"))
          and all(nodes[an(ID1, x)]["detail"]["order"] == "" for x in ("c1", "c2", "c3")),
          {x: nodes[an(ID1, x)]["detail"]["order"] for x in ("w1", "w2", "w3", "c1")})
    # live overlay
    d1, d2, d3 = o1["detail"], o2["detail"], o3["detail"]
    sta = lambda d: {s["name"]: s["status"] for s in d["stations"]}  # noqa: E731
    check("orders: FO-002 detail.station is build and its build station is active (a running FO-002:build agent)",
          d2["station"] == "build" and sta(d2)["build"] == "active", (d2["station"], sta(d2)))
    check("orders: the overlay only touches that station (the others keep their ledger status)",
          all(v == "pending" for k, v in sta(d2).items() if k != "build"), sta(d2))
    check("orders: FO-001 shows build active too, plan stays done", d1["station"] == "build" and sta(d1)["build"] == "active"
          and sta(d1)["plan"] == "done" and sta(d1)["test"] == "pending", (d1["station"], sta(d1)))
    check("orders: FO-003 has no running agent labelled FO-003: so its station is untouched",
          d3["station"] == "review" and "active" not in sta(d3).values(), (d3["station"], sta(d3)))
    check("orders: the detail is the §5.5 dict (id, title, priority, path, gate, lane, run_id, rework, events)",
          d1["id"] == "FO-001" and d1["title"] == "Add CSV export" and d1["priority"] == "p2" and d1["path"].startswith("factory/orders/")
          and d1["run_id"] == "wf_1" and d3["gate"] == {"name": "merge", "waiting": True} and d3["rework"] == 0 and d3["events"] == 4,
          (d1["id"], d3["gate"]))
    check("orders: lane.branch is kept and lane.worktree goes through tilde",
          d3["lane"] == {"branch": BRANCH, "worktree": tilde(WT3)}, d3["lane"])
    # visibility
    check("orders: a done order older than a day (FO-004) is hidden by default", on("alpha", "FO-004") not in nodes)
    check("orders: gamma has no live session but a queued order: its hub shows with the order and no session",
          "p:gamma" in nodes and on("gamma", "FO-001") in nodes and not [n for n in p["nodes"] if n["kind"] == "session" and n["parent"] == "p:gamma"]
          and edges.get(f"p:gamma>{on('gamma', 'FO-001')}", {}).get("kind") == "member"
          and nodes[on("gamma", "FO-001")]["state"] == "queued" and edges[f"p:gamma>{on('gamma', 'FO-001')}"]["active"] is False,
          [n["id"] for n in p["nodes"] if n["parent"] == "p:gamma"])
    check("orders: gamma's FO-001 is not linked to alpha's FO-001 workflow (orders link within their project)",
          not [e for e in p["edges"] if e["source"] == on("gamma", "FO-001") and e["kind"] == "runs"])
    check("orders: delta (a session 3 h ago, no orders) is not shown by default", "p:delta" not in nodes)
    ko = p["kpis"]["orders"]
    check("orders: kpis.orders counts the statuses in scope (building 2, review 1, queued 1), zero statuses left out",
          ko.get("building") == 2 and ko.get("review") == 1 and ko.get("queued") == 1 and all(v > 0 for v in ko.values()), ko)
    do = nodes["p:alpha"]["detail"]["orders"]
    check("orders: alpha's detail.orders counts building 2, review 1", do.get("building") == 2 and do.get("review") == 1 and "queued" not in do, do)
    check("orders: gamma's detail.orders counts its queued order", nodes["p:gamma"]["detail"]["orders"] == {"queued": 1}, nodes["p:gamma"]["detail"]["orders"])
    test_integrity(p, " (with orders)")
    # all_projects
    pa = poll(st, all_projects=True)
    na = by_id(pa)
    check("orders: all_projects shows the old done order too (FO-004, state done)", on("alpha", "FO-004") in na and na[on("alpha", "FO-004")]["state"] == "done"
          and na[on("alpha", "FO-004")]["parent"] == "p:alpha")
    check("orders: all_projects counts it in kpis.orders", pa["kpis"]["orders"].get("done") == 1 and pa["kpis"]["orders"].get("building") == 2, pa["kpis"]["orders"])
    check("orders: all_projects shows delta as a dormant hub with no children",
          "p:delta" in na and na["p:delta"]["state"] == "idle" and not [n for n in pa["nodes"] if n["parent"] == "p:delta"], na.get("p:delta"))
    check("orders: all_projects adds hubs only: every default node is still there, no session for the old ones",
          ids(p) <= ids(pa) and sn(IDG) not in na and sn(IDD) not in na, sorted(ids(p) - ids(pa)))
    check("orders: the dormant hub's window numbers are real", na["p:delta"]["detail"]["sessions_window"] == 1 and na["p:delta"]["detail"]["sessions_live"] == 0,
          na["p:delta"]["detail"])
    test_integrity(pa, " (all projects)")
    # a missing factory module
    hide_factory()
    pm = poll(st)
    nm = by_id(pm)
    check("orders: with the factory module missing there are no order nodes and no runs edges, and no exception",
          not [n for n in pm["nodes"] if n["kind"] == "order"] and not [e for e in pm["edges"] if e["kind"] == "runs"] and pm["kpis"]["orders"] == {},
          [n["id"] for n in pm["nodes"] if n["kind"] == "order"])
    check("orders: the sessions, agents and workflows are all still there", {i for i in ids(p) if i[0] in "psaw"} - {"p:gamma"} <= ids(pm),
          sorted({i for i in ids(p) if i[0] in "psaw"} - {"p:gamma"} - ids(pm)))
    check("orders: gamma (order-only) is gone with its order", "p:gamma" not in nm, "p:gamma" in nm)
    check("orders: nothing links a workflow to an order any more", all(nm[wn(ID1, "wf_1")]["detail"]["order"] == "" for _ in [0]),
          nm[wn(ID1, "wf_1")]["detail"]["order"])
    test_integrity(pm, " (no factory)")
    use_stub()
    # a stub that raises
    STUB["mode"] = "raise"
    pr = poll(st)
    check("orders: a factory whose project_orders raises gives no orders and no exception",
          not [n for n in pr["nodes"] if n["kind"] == "order"] and pr["kpis"]["orders"] == {} and "p:alpha" in by_id(pr) and "p:gamma" not in by_id(pr),
          [n["id"] for n in pr["nodes"] if n["kind"] == "order"])
    test_integrity(pr, " (factory raises)")
    STUB["mode"] = "ok"
    pb = poll(st)
    check("orders: and the orders come back when the factory does",
          on("alpha", "FO-001") in ids(pb) and on("gamma", "FO-001") in ids(pb), sorted(i for i in ids(pb) if i[0] == "o"))
    # the TTL cache: with the default TTL a changed answer is not re-read at once
    graph.ORDERS_TTL = 60
    poll(st)
    STUB["orders"][GAMMA][0]["status"] = "spec"
    cached = by_id(poll(st))[on("gamma", "FO-001")]["state"]
    graph.ORDERS_TTL = 0
    fresh = by_id(poll(st))[on("gamma", "FO-001")]["state"]
    STUB["orders"][GAMMA][0]["status"] = "queued"
    check("orders: ORDERS_TTL caches a project's orders (60 s keeps the old answer, 0 reads the new)", (cached, fresh) == ("queued", "spec"), (cached, fresh))


# ── 8. scope ─────────────────────────────────────────────────────────────────
def test_scope(st):
    p = poll(st, project="alpha")
    nodes = by_id(p)
    check("scope: project=alpha sets scope.project", p["scope"].get("project") == "alpha", p["scope"])
    stray = [n["id"] for n in p["nodes"] if not (n["id"] == "p:alpha" or under(nodes, n["id"], "p:alpha"))]
    check("scope: every node is p:alpha or descends from it", "p:alpha" in nodes and not stray, stray[:4])
    check("scope: nothing of beta, gamma or epsilon", not [i for i in nodes if i in ("p:beta", "p:gamma", "p:epsilon") or i in (sn(IDB), sn(IDO))])
    check("scope: alpha keeps its sessions, agents, workflows and orders",
          {sn(ID1), sn(ID2), sn(ID3), an(ID1, "c1"), wn(ID1, "wf_1"), on("alpha", "FO-001")} <= set(nodes))
    k = p["kpis"]
    check("scope: kpis.sessions_live counts alpha only (3)", k["sessions_live"] == 3, k["sessions_live"])
    check("scope: kpis agents and needs are alpha's (2 need you: c1 and S2)",
          k["agents"] == len([n for n in p["nodes"] if n["kind"] in ("session", "agent")]) and k["needs"] == 2, (k["agents"], k["needs"]))
    check("scope: kpis.orders are alpha's (building 2, review 1, no queued)", k["orders"].get("building") == 2 and k["orders"].get("review") == 1
          and "queued" not in k["orders"], k["orders"])
    check("scope: cost_window is alpha's project cost_window", abs(k["cost_window"] - nodes["p:alpha"]["detail"]["cost_window"]) < 1e-6,
          (k["cost_window"], nodes["p:alpha"]["detail"]["cost_window"]))
    check("scope: alerts are filtered (c1 and the question, not epsilon's)", {a["node"] for a in p["alerts"]} == {an(ID1, "c1"), sn(ID2)},
          [a["node"] for a in p["alerts"]])
    check("scope: feed rows are alpha's and still carry a node in the scope",
          p["feed"] and all(f["project"] == "alpha" and f["node"] in nodes for f in p["feed"]), [(f["project"], f["node"] in nodes) for f in p["feed"]][:3])
    check("scope: burn kpis are alpha's rows only", k["burn_tok_min"] == sum(r["burn"]["tok_min"] for r in st.live_payload()["agents"] if r["project"] == "alpha"),
          k["burn_tok_min"])
    dangling = [e["id"] for e in p["edges"] if e["source"] not in nodes or e["target"] not in nodes]
    check("scope: no edge leaves the scope", not dangling, dangling[:3])
    test_integrity(p, " (scope alpha)")
    pb = poll(st, project="beta")
    check("scope: project=beta is the hub and its one session", ids(pb) == {"p:beta", sn(IDB)} and pb["kpis"]["sessions_live"] == 1, sorted(ids(pb)))
    check("scope: beta's feed is its one Read; the unscoped feed has rows of both projects",
          [f["project"] for f in pb["feed"]] == ["beta"] and {f["project"] for f in poll(st)["feed"]} == {"alpha", "beta"},
          ([f["project"] for f in pb["feed"]], {f["project"] for f in poll(st)["feed"]}))
    pg = poll(st, project="gamma")
    check("scope: an order-only project can be scoped to (gamma: hub and order)", ids(pg) == {"p:gamma", on("gamma", "FO-001")}, sorted(ids(pg)))
    pd = poll(st, project="delta")
    check("scope: a dormant project asked for by name gives its hub or nothing, never other nodes (delta)", ids(pd) <= {"p:delta"}, sorted(ids(pd)))
    pu = poll(st, project="nope")
    check("scope: an unknown project gives nodes == [] and no exception", pu["nodes"] == [] and pu["scope"].get("project") == "nope", pu["nodes"][:2])
    check("scope: and empty edges, alerts and feed, zero kpis",
          pu["edges"] == [] and pu["alerts"] == [] and pu["feed"] == [] and pu["kpis"]["sessions_live"] == 0 and pu["kpis"]["agents"] == 0
          and pu["kpis"]["orders"] == {}, (pu["edges"], pu["alerts"], pu["kpis"]))
    check("scope: the empty graph still has a sig and the full key set", is_str(pu["sig"]) and set(pu) == TOP)
    zs = pu["series"]
    check("scope: and a zero series (20 zeros per list, no families, agents_total 0)",
          not series_probs(zs, pu["now"]) and zs["tokens_by_family"] == {} and zs["agents_total"] == 0
          and all(zs[k] == [0] * 20 for k in ("tokens", "tool_calls", "tool_errors", "agents")), zs)
    sa, sb, s_all = p["series"], pb["series"], poll(st)["series"]
    check("scope: series is the project's own: alpha's 11 transcripts + beta's 1 = the unscoped 12, bucket by bucket",
          (sa["agents_total"], sb["agents_total"], s_all["agents_total"]) == (11, 1, 12)
          and all(s_all[k] == [x + y for x, y in zip(sa[k], sb[k])] for k in ("tokens", "tool_calls", "tool_errors", "agents")),
          (sa["agents_total"], sb["agents_total"], s_all["agents_total"]))
    check("scope: all_projects leaves series unchanged", poll(st, project="alpha", all_projects=True)["series"] == sa
          and poll(st, all_projects=True)["series"] == s_all)
    check("scope: the match is exact (no prefix, no case folding)", poll(st, project="alph")["nodes"] == [] and poll(st, project="ALPHA")["nodes"] == [])
    odd = ["", "../../etc", "a" * 5000, "alpha\x00", "p:alpha"]
    try:
        out = [poll(st, project=x) for x in odd]
        ok = all(isinstance(o["nodes"], list) for o in out) and out[0]["nodes"] and all(o["nodes"] == [] for o in out[1:])
    except Exception as e:  # noqa: BLE001
        ok = False
        print("odd project:", repr(e))
    check("scope: odd project strings are labels, never paths (no exception, nothing matches)", ok)
    pall = poll(st, project="alpha", all_projects=True)
    check("scope: all_projects with a project is still that project only", all(n["id"] == "p:alpha" or under(by_id(pall), n["id"], "p:alpha") for n in pall["nodes"])
          and on("alpha", "FO-004") in ids(pall))


# ── 9. HTTP ──────────────────────────────────────────────────────────────────
def _get(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    c.request("GET", path, headers={"Host": f"127.0.0.1:{port}"})
    r = c.getresponse()
    body = r.read()
    ctype = r.getheader("Content-Type") or ""
    c.close()
    return r.status, body, ctype


def test_http(st):
    srv = make_server(0, st, fallback=False)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        code, _, _ = _get(srv.port, "/api/graph")
        check("http: GET /api/graph before pairing is 401", code == 401, code)
        code, _, _ = _get(srv.port, "/api/graph?project=alpha&all=1")
        check("http: with query parameters too", code == 401, code)
        auto_cookie(srv)
        for path, kw in (("/api/graph", {}), ("/api/graph?project=alpha", {"project": "alpha"}), ("/api/graph?all=1", {"all_projects": True}),
                         ("/api/graph?project=nope", {"project": "nope"})):
            code, body, ctype = _get(srv.port, path)
            try:
                d = json.loads(body)
            except ValueError:
                d = {}
            want = poll(st, **kw)
            check(f"http: {path} is 200 JSON with graph.payload's node ids", code == 200 and ctype.startswith("application/json") and d.get("nodes") is not None
                  and {n["id"] for n in d["nodes"]} == ids(want), (code, ctype, len(d.get("nodes") or []), len(want["nodes"])))
            check(f"http: {path} has the same sig and edges as graph.payload (nothing changed between the calls)",
                  d.get("sig") == want["sig"] and d.get("edges") == want["edges"], (d.get("sig"), want["sig"]))
        code, body, _ = _get(srv.port, "/api/graph?project=alpha")
        d = json.loads(body)
        check("http: ?project=alpha echoes scope.project", d["scope"].get("project") == "alpha", d["scope"])
        code, body, _ = _get(srv.port, "/api/graph?all=1")
        d = json.loads(body)
        check("http: ?all=1 adds the dormant hub (p:delta)", "p:delta" in {n["id"] for n in d["nodes"]})
        code, body, _ = _get(srv.port, "/api/graph")
        d = json.loads(body)
        check("http: no query is the default graph (no p:delta)", "p:delta" not in {n["id"] for n in d["nodes"]})
    finally:
        srv.shutdown()
        srv.server_close()
    A = server_mod.ASSETS
    check("assets: /graph.js and /factory.js are served as JavaScript",
          A.get("/graph.js", (None, None))[1] == "text/javascript; charset=utf-8" and A.get("/factory.js", (None, None))[1] == "text/javascript; charset=utf-8",
          (A.get("/graph.js"), A.get("/factory.js")))
    check("assets: /factory.css is served as CSS", A.get("/factory.css", (None, None))[1] == "text/css; charset=utf-8", A.get("/factory.css"))
    check("assets: each entry names its own file", A.get("/graph.js", (None,))[0] == "graph.js" and A.get("/factory.js", (None,))[0] == "factory.js"
          and A.get("/factory.css", (None,))[0] == "factory.css", [A.get(x) for x in ("/graph.js", "/factory.js", "/factory.css")])


# ── alert for a session with no transcript: its project's hub carries it ─────
def test_alert_hub():
    sid = "88888888-0000-0000-0000-000000000008"
    emit("PermissionRequest", sid, T - 20, tool="Bash", sig="make release", message="make release",
         cwd=os.path.join(BASE, "work", "zeta"))
    st = fresh_store()
    p = poll(st)
    nodes = by_id(p)
    al = [a for a in p["alerts"] if a["node"] == "p:zeta"]
    check("alerts: a session with no transcript yet attaches its alert to its project's hub, made for it",
          len(al) == 1 and al[0]["kind"] == "permission" and "p:zeta" in nodes and nodes["p:zeta"]["needs"] == "permission"
          and nodes["p:zeta"]["state"] == "needs" and sn(sid) not in nodes and not [n for n in p["nodes"] if n["parent"] == "p:zeta"],
          (al, nodes.get("p:zeta")))
    check("alerts: that hub is in the scope of its own label only",
          "p:zeta" in ids(poll(st, project="zeta")) and "p:zeta" not in ids(poll(st, project="alpha")))
    test_integrity(p, " (alert hub)")


def test_rerun_link():
    """A rerun: the ledger's run_id is the RECORDED run R1, still lingering on the floor, while R2 runs under
    the same FO-NNN:<station> labels. The order links to both, R1 first (floor order), each once."""
    sid = "99999999-0000-0000-0000-000000000009"
    eta = os.path.join(BASE, "work", "eta")
    w = World("eta", sid)
    w.write_main([w.prompt(T - 400, "/factory run FO-001"),
                  w.tool(T - 395, "tu_r1", "Workflow", {"script": "export const meta = { name: 'factory-line' }"}),
                  w.result(T - 394, "tu_r1", "launched", tur={"status": "async_launched", "runId": "wf_r1"}),
                  w.tool(T - 60, "tu_r2", "Workflow", {"script": "export const meta = { name: 'factory-line' }"}),
                  w.result(T - 59, "tu_r2", "launched", tur={"status": "async_launched", "runId": "wf_r2"})])
    w.write_sub("r1a", [w.prompt(T - 393, "Review FO-001", agent="r1a"), w.text(T - 70, "r1am", "pass", agent="r1a")],
                meta={"agentType": "workflow-subagent", "description": "FO-001:review", "workflowPhase": "Review"}, run="wf_r1")
    w.journal("wf_r1", [{"type": "started", "agentId": "r1a", "label": "FO-001:review", "phase": "Review"},
                        {"type": "result", "agentId": "r1a", "result": "ok"}])
    w.summary("wf_r1", {"runId": "wf_r1", "workflowName": "factory-line", "status": "completed", "agentCount": 1,
                        "phases": [{"title": "Review"}]})
    w.write_sub("r2b", [w.prompt(T - 58, "Build FO-001", agent="r2b"),
                        w.tool(T - 5, "r2b_bash", "Bash", {"command": "make test"}, agent="r2b", model="claude-sonnet-5-5")],
                meta={"agentType": "workflow-subagent", "description": "FO-001:build", "workflowPhase": "Build"}, run="wf_r2")
    STUB["orders"][eta] = [mk_order("FO-001", "Rerun me", "building", run_id="wf_r1", stations=mk_stations(plan="done"))]
    try:
        graph.ORDERS_TTL = 0
        st = fresh_store()
        p = poll(st, project="eta")
        nodes = by_id(p)
        r1, r2 = wn(sid, "wf_r1"), wn(sid, "wf_r2")
        runs = [e["target"] for e in p["edges"] if e["kind"] == "runs" and e["source"] == on("eta", "FO-001")]
        check("rerun: both workflows are on the floor (the recorded one lingers)", r1 in nodes and r2 in nodes, sorted(n for n in nodes if n.startswith("w:")))
        check("rerun: the order links to the recorded run (ledger run_id) and to the rerun (labels), each once",
              sorted(runs) == sorted([r1, r2]) and len(runs) == len(set(runs)), runs)
        check("rerun: both workflows carry the order", nodes.get(r1, {}).get("detail", {}).get("order") == "FO-001"
              and nodes.get(r2, {}).get("detail", {}).get("order") == "FO-001", (nodes.get(r1, {}).get("detail"), nodes.get(r2, {}).get("detail")))
        e2 = next((e for e in p["edges"] if e["id"] == "%s>%s" % (on("eta", "FO-001"), r2)), {})
        check("rerun: the edge to the running rerun is active", e2.get("active") is True, e2)
        test_integrity(p, " (rerun)")
    finally:
        STUB["orders"].pop(eta, None)


# ── 12. series: the last hour in 3-minute buckets ────────────────────────────
SCFG = os.path.join(BASE, "claude-series")      # its own config root: unscoped, the series world is all there is
S_NOW = [0.0]
SKEYS = {"bucket_secs", "start", "tokens", "tokens_by_family", "tool_calls", "tool_errors", "agents", "agents_total"}
FAMS = ("opus", "sonnet", "haiku", "fable", "other")
IDXA = "a0a0a0a0-0000-0000-0000-00000000000a"   # sx: lead running a tool now; subagents "long" (done) and "run" (running)
IDXB = "b0b0b0b0-0000-0000-0000-00000000000b"   # sx: finished ~40 minutes ago (off the board), subagent "old"
IDXC = "c0c0c0c0-0000-0000-0000-00000000000c"   # sy: another project, 25 minutes ago, one guard-blocked call


def at(cells):
    """20 buckets, zero but for {bucket: value}."""
    out = [0] * 20
    for i, v in cells.items():
        out[i] = v
    return out


# What the sx world holds, bucket by bucket (build_series_world says why).
SX_FAM = {"opus": {0: 60, 1: 220, 3: 550, 5: 110, 6: 60, 19: 110}, "sonnet": {5: 110, 8: 60}, "haiku": {6: 170, 17: 110}}
SX_TOOLS, SX_ERRS = {1: 2, 5: 2, 6: 1, 17: 1, 19: 1}, {1: 1}
SX_AGENTS = {0: 1, 1: 1, 2: 1, 3: 1, 4: 2, 5: 3, 6: 3, 7: 1, 8: 1, 13: 1, 17: 1, 18: 1, 19: 2}
SY_FAM = {"opus": {11: 110}, "fable": {11: 120}}


def series_probs(s, now):
    """What in one `series` breaks the contract (empty: nothing)."""
    if not isinstance(s, dict) or set(s) != SKEYS:
        return ["keys", sorted(set(s) ^ SKEYS) if isinstance(s, dict) else type(s).__name__]
    arr = lambda v: isinstance(v, list) and len(v) == 20 and all(is_int(x) and x >= 0 for x in v)  # noqa: E731
    probs = [k for k in ("tokens", "tool_calls", "tool_errors", "agents") if not arr(s[k])]
    fam = s["tokens_by_family"]
    if not (isinstance(fam, dict) and set(fam) <= set(FAMS) and all(arr(v) and any(v) for v in fam.values())):
        probs.append("tokens_by_family")
    elif "tokens" not in probs and any(sum(v[i] for v in fam.values()) != s["tokens"][i] for i in range(20)):
        probs.append("family sums != tokens")
    if not (s["bucket_secs"] == 180 and is_int(s["start"]) and s["start"] % 180 == 0 and s["start"] + 3420 <= now < s["start"] + 3600):
        probs.append(("start", s["start"], now))
    if not ({"tool_calls", "tool_errors"} & set(probs)) and any(e > c for e, c in zip(s["tool_errors"], s["tool_calls"])):
        probs.append("tool_errors > tool_calls")
    if not (is_int(s["agents_total"]) and "agents" not in probs and max(s["agents"]) <= s["agents_total"] <= sum(s["agents"])):
        probs.append(("agents_total", s["agents_total"]))
    return probs


def build_series_world():
    """Times are offsets from the hour's start B0 (now = B0 + 3520, bucket 19); bucket i is [B0 + 180 i, B0 + 180 (i + 1)).
    New tokens: World.text 60 (10 in + 50 out), World.tool 110 (10 + 100; its 1000 cache reads never count)."""
    for d in ("projects", "sessions"):
        os.makedirs(os.path.join(SCFG, d), exist_ok=True)
    os.chmod(os.path.join(SCFG, "sessions"), 0o700)
    b0 = FX.B0 = (T // 180) * 180 - 3420
    now = b0 + 3520
    S_NOW[0] = float(now)
    opus, sonnet, haiku = "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"

    def reply(w, t, mid, out, stop="end_turn", text="ok", model=opus, cr=0):
        return w.rec("assistant", t, message={"id": mid, "model": model, "role": "assistant", "stop_reason": stop,
                                              "content": [{"type": "text", "text": text}],
                                              "usage": {"input_tokens": 10, "output_tokens": out, "cache_creation_input_tokens": 40,
                                                        "cache_read_input_tokens": cr}})
    A = World("sx", IDXA, cfg=SCFG)
    A.write_main([
        A.prompt(b0 - 2, "Before the hour"),
        A.text(b0 - 1, "xa_pre", "Before the hour."),                         # start - 1: not in the hour
        A.text(b0, "xa_edge", "On the first edge."),                           # exactly on start: bucket 0
        A.tool(b0 + 180, "xa_read", "Read", {"file_path": "/w/x"}),            # exactly on an edge: bucket 1
        A.result(b0 + 181, "xa_read"),
        A.tool(b0 + 359, "xa_bash", "Bash", {"command": "make"}),              # bucket 1; its failed result lands in 2
        A.result(b0 + 365, "xa_bash", "boom", err=True),
        reply(A, b0 + 600, "xa_rw", 10, stop=None, text="partial"),           # first record: bucket 3 (60 so far)
        reply(A, b0 + 740, "xa_rw", 500, cr=9999),                             # same message, final usage: 550, still bucket 3
        A.rec("assistant", b0 + 2500, message={"id": "xa_syn", "model": "<synthetic>", "role": "assistant", "stop_reason": "end_turn",
                                               "content": [{"type": "text", "text": "No response requested."}],
                                               "usage": {"input_tokens": 1000, "output_tokens": 1000}}),   # bucket 13: no tokens
        A.tool(now, "xa_grep", "Grep", {"pattern": "x"}),                      # at now: bucket 19, still running
    ])
    A.write_sub("long", [A.prompt(b0 + 890, "Run the suite", agent="long"),                      # bucket 4
                         A.tool(b0 + 900, "lg_bash", "Bash", {"command": "make test"}, agent="long", model=sonnet),   # 5
                         A.result(b0 + 1500, "lg_bash", agent="long"),                           # 8: ran across 5..8
                         A.text(b0 + 1510, "lg_end", "All green.", agent="long", model=sonnet)],
                meta={"agentType": "general-purpose", "description": "Run the suite"})
    A.write_sub("run", [A.prompt(now - 400, "Build it", agent="run"),                            # bucket 17
                        A.tool(now - 390, "rn_bash", "Bash", {"command": "make build"}, agent="run", model=haiku)],
                meta={"agentType": "general-purpose", "description": "Build it"})              # on the board, running: 17..19
    B = World("sx", IDXB, cfg=SCFG)
    B.write_main([B.prompt(b0 + 1000, "Find it"),                                                 # bucket 5
                  B.tool(b0 + 1010, "xb_agent", "Agent", {"description": "Find it", "subagent_type": "Explore", "prompt": "find"}),
                  B.result(b0 + 1200, "xb_agent", "found", tur={"status": "completed", "agentId": "old"}),   # 6
                  B.text(b0 + 1210, "xb_end", "Found it.")])                                     # now - 2310: off the board
    B.write_sub("old", [B.prompt(b0 + 1015, "Find it", agent="old"),                              # 5
                        B.tool(b0 + 1100, "old_read", "Read", {"file_path": "/w/y"}, agent="old", model=haiku),   # 6
                        B.result(b0 + 1101, "old_read", agent="old"),
                        B.text(b0 + 1190, "old_end", "It is in y.", agent="old", model=haiku)],
                meta={"agentType": "Explore", "description": "Find it", "toolUseId": "xb_agent"})
    FX.XB = B
    C = World("sy", IDXC, cfg=SCFG)
    C.write_main([C.prompt(b0 + 2000, "Hello"),                                                   # all in bucket 11
                  C.text(b0 + 2010, "xc_1", "Hi.", model="claude-fable-5"),
                  C.tool(b0 + 2020, "xc_write", "Write", {"file_path": "/w/z"}),
                  C.result(b0 + 2021, "xc_write", "BLOCKED: the write guard refused /w/z"),       # a guard block is an error
                  C.text(b0 + 2030, "xc_2", "Blocked.", model="claude-fable-5")])


def series_store():
    st = Store(root=SCFG, days=1, clock=lambda: S_NOW[0])
    st.refresh(force=True)
    return st


def test_series():
    build_series_world()
    st = series_store()
    now, b0 = S_NOW[0], FX.B0
    p = poll(st, project="sx")
    s, nodes = p["series"], by_id(p)
    check("series: shape (keys, 20 non-negative ints per list, aligned start holding now, family sums, errors <= calls)",
          not series_probs(s, now), series_probs(s, now))
    check("series: start = (floor(now / 180) + 1) * 180 - 3600, and 180 s buckets",
          s["start"] == b0 == (int(now) // 180 + 1) * 180 - 3600 and s["bucket_secs"] == 180, (s["start"], b0))
    fam = s["tokens_by_family"]
    check("series: a reply exactly on start is in bucket 0; one at start - 1 is not in the hour",
          fam.get("opus", [0])[0] == 60 and sum(fam.get("opus", [])) == 1110, fam.get("opus"))
    check("series: a record exactly on an edge goes to the later bucket (tokens and tool calls)",
          fam.get("opus", [0, 0])[1] == 220 and s["tool_calls"][1] == 2, (fam.get("opus"), s["tool_calls"]))
    check("series: a record at now is in the last bucket", s["tool_calls"][19] == 1 and fam.get("opus", [0] * 20)[19] == 110,
          (s["tool_calls"][19], fam.get("opus")))
    check("series: a failed call counts in its tool_use's bucket, not its result's", s["tool_errors"] == at(SX_ERRS), s["tool_errors"])
    check("series: a reply rewritten by a later record counts once, at its latest usage, in its first record's bucket",
          fam.get("opus", [0] * 5)[3] == 550 and fam.get("opus", [0] * 5)[4] == 0, fam.get("opus"))
    check("series: a synthetic reply carries no tokens (its transcript still worked in that bucket)",
          s["tokens"][13] == 0 and s["agents"][13] == 1, (s["tokens"][13], s["agents"][13]))
    check("series: tokens_by_family is exactly the fixture's, only the families used", fam == {f: at(c) for f, c in SX_FAM.items()}, fam)
    check("series: tokens is the family sum, tool_calls the fixture's", s["tokens"] == [sum(v[i] for v in fam.values()) for i in range(20)]
          and s["tool_calls"] == at(SX_TOOLS), (s["tokens"], s["tool_calls"]))
    check("series: a subagent finished ~40 minutes ago, off the board, still counts (tokens, tools, agents)",
          an(IDXB, "old") not in nodes and sn(IDXB) not in nodes and fam.get("haiku", [0] * 7)[6] == 170 and s["tool_calls"][6] == 1
          and s["agents"][5] == s["agents"][6] == 3, (sorted(nodes), fam.get("haiku"), s["agents"][5:7]))
    check("series: a long tool call counts its agent in every bucket it ran across (bucket 7 has no record at all)",
          an(IDXA, "long") not in nodes and s["agents"][7] == 1 and s["agents"][8] == 1, s["agents"][4:9])
    run_ = nodes.get(an(IDXA, "run"))
    check("series: a running board row counts from its since through now (bucket 18 only by that)",
          run_ is not None and run_["state"] == "tool" and s["agents"][17:] == [1, 1, 2], (run_ and run_["state"], s["agents"][17:]))
    check("series: agents per bucket and agents_total (5 distinct transcripts, not the sum)",
          s["agents"] == at(SX_AGENTS) and s["agents_total"] == 5, (s["agents"], s["agents_total"]))

    # scope: project=X counts only X; all_projects and days change nothing
    sy = poll(st, project="sy")["series"]
    check("series scope: project=sy is sy's alone (fable and opus, a guard block counted as an error)",
          sy["tokens_by_family"] == {f: at(c) for f, c in SY_FAM.items()} and sy["tool_calls"] == at({11: 1})
          and sy["tool_errors"] == at({11: 1}) and sy["agents"] == at({11: 1}) and sy["agents_total"] == 1, sy)
    sa = poll(st)["series"]
    add = all(sa[k] == [x + y for x, y in zip(s[k], sy[k])] for k in ("tokens", "tool_calls", "tool_errors", "agents"))
    check("series scope: unscoped is sx + sy, bucket by bucket, with every family used",
          add and sa["agents_total"] == 6 and set(sa["tokens_by_family"]) == {"opus", "sonnet", "haiku", "fable"} and not series_probs(sa, now),
          (sa["agents_total"], sorted(sa["tokens_by_family"])))
    same = [poll(st, all_projects=True)["series"] == sa, poll(st, days=7)["series"] == sa,
            poll(st, project="sx", all_projects=True, days=7)["series"] == s]
    check("series scope: all_projects and days leave it unchanged (always the last hour)", all(same), same)
    pn = poll(st, project="nope")
    zs = pn["series"]
    check("series: an empty floor still has a zero series",
          pn["nodes"] == [] and not series_probs(zs, now) and zs["tokens_by_family"] == {} and zs["agents_total"] == 0
          and all(zs[k] == [0] * 20 for k in ("tokens", "tool_calls", "tool_errors", "agents")) and zs["start"] == b0, zs)
    junk = {"start": b0, "fam": {"opus": [-5, 2.7, "x", True, float("nan")] + [1] * 30, "gpt": [9] * 20, "haiku": [0] * 20},
            "tools": [1, 2], "errors": [5, 1, -1, 4], "agents": None, "total": -3}
    v = graph._series_view(junk, now)
    check("series view: junk is clipped (non-negative ints, 20 long, families whitelisted and non-zero, errors <= calls)",
          not series_probs(v, now) and v["tokens_by_family"] == {"opus": [0, 2, 0, 0, 0] + [1] * 15} and v["tokens"] == v["tokens_by_family"]["opus"]
          and v["tool_calls"] == at({0: 1, 1: 2}) and v["tool_errors"] == at({0: 1, 1: 1}) and v["agents"] == [0] * 20 and v["agents_total"] == 0, v)
    empty = [graph._series_view(x, now) for x in (None, [], {"start": b0 + 180, "tools": [3] * 20}, {"start": "soon"})]
    check("series view: no series, or one whose start does not hold now, is the empty hour ending after now",
          all(not series_probs(e, now) and e["start"] == b0 and e["tool_calls"] == [0] * 20 and e["tokens_by_family"] == {} for e in empty),
          [e["start"] for e in empty])

    # sig: holds in one bucket with nothing written; moves when a bucket changes, and when the window slides
    def rest(p):
        return (p["nodes"], p["edges"], p["kpis"], p["feed"], [{k: v for k, v in a.items() if k != "age"} for a in p["alerts"]])
    q1, q2 = poll(st, project="sx"), poll(st, project="sx")
    S_NOW[0] = now + 15                     # same bucket, same minute: no burn, spark or series change
    st.refresh(force=True)
    q3 = poll(st, project="sx")
    S_NOW[0] = now
    st.refresh(force=True)
    check("series sig: holds between polls in the same bucket while nothing is written",
          q1["sig"] == q2["sig"] == q3["sig"] and q1["series"] == q3["series"] and q3["now"] == now + 15, (q1["sig"], q2["sig"], q3["sig"]))
    World.write(FX.XB.main, [FX.XB.prompt(b0 + 1300, "One more thing")], append=True)   # bucket 7, off the board, no usage
    st.refresh(force=True)
    q4 = poll(st, project="sx")
    check("series sig: a write that changes only a bucket moves the sig (nothing else on the floor changed)",
          q4["series"]["agents"][7] == 2 and q4["sig"] != q1["sig"] and rest(q4) == rest(q1)
          and [k for k in q4["series"] if q4["series"][k] != q1["series"][k]] == ["agents"],
          (q4["series"]["agents"][7], q4["sig"], q1["sig"], rest(q4) == rest(q1)))
    S_NOW[0] = now + 100                    # the next bucket: the window slides by one
    st.refresh(force=True)
    q5 = poll(st, project="sx")
    S_NOW[0] = now
    st.refresh(force=True)
    a4, a5 = q4["series"], q5["series"]
    check("series sig: the window slides by one bucket at the next edge, and the sig moves",
          a5["start"] == b0 + 180 and q5["sig"] != q4["sig"]
          and all(a5[k] == a4[k][1:] + [0] for k in ("tokens", "tool_calls", "tool_errors"))
          and a5["agents"] == a4["agents"][1:] + [2] and a5["agents_total"] == 5 and not series_probs(a5, now + 100),
          (a5["start"], a5["agents"], a4["agents"]))


def test_series_memory():
    """The minute buckets stay bounded on a long transcript, refuse what they pruned, and never go negative."""
    from cc_dash import parser as pm
    keep, cap = pm.SERIES_KEEP_MINS, pm.SERIES_KEEP_MINS + pm.SERIES_SLACK_MINS
    w = World("mem", "d0d0d0d0-0000-0000-0000-00000000000d", cfg=SCFG)    # builds records; nothing is written
    a = pm.Acc("mem.jsonl", "sub")
    t0 = float((T // 60) * 60 - 3 * 3600)
    peak = 0
    a.feed(w.tool(t0 + 1, "mm_long", "Monitor", {"description": "wait"}, agent="m"))
    for k in range(540):                    # a call every 20 s for three hours; every 7th fails
        t = t0 + 20 * k + 2
        for r in (w.tool(t, f"mm{k}", "Read", {"file_path": "/w"}, agent="m"),
                  w.result(t + 5, f"mm{k}", "no" if k % 7 == 0 else "ok", agent="m", err=k % 7 == 0)):
            a.feed(r)
            peak = max(peak, len(a.mins))
    end = t0 + 20 * 540
    a.feed(w.result(end, "mm_long", agent="m"))           # a three-hour call: its fill is capped at the kept span
    peak = max(peak, len(a.mins))
    top = a.min_top
    check(f"series memory: three hours of records keep at most KEEP + slack minute rows (peak {peak}, cap {cap})",
          peak <= cap and len(a.mins) <= cap and all(k <= top for k in a.mins), (peak, len(a.mins)))
    check("series memory: the kept span is the last KEEP minutes, each one active",
          sum(1 for k, r in a.mins.items() if k > top - keep and r[pm.M_ACTIVE]) == keep, sorted(a.mins)[:3])
    check("series memory: no cell is negative and errors never exceed calls in a minute",
          all(x >= 0 for r in a.mins.values() for x in r) and all(r[pm.M_ERRORS] <= r[pm.M_TOOLS] for r in a.mins.values()))

    def toks():
        return sum(sum(r[:5]) for r in a.mins.values())

    def kept_usage():
        return sum(u[1][0] + u[1][1] + u[1][3] + u[1][4] for mid, u in a.msg_usage.items()
                   if a.msg_ts.get(mid) and int(a.msg_ts[mid] // 60) > top - keep)
    check("series memory: the kept minutes hold exactly the new tokens of the replies that started in them",
          sum(sum(r[:5]) for k, r in a.mins.items() if k > top - keep) == kept_usage(), kept_usage())
    before, rows = toks(), len(a.mins)
    a.feed(w.text(t0 + 30, "mm_late", "A late record for a pruned minute.", agent="m"))
    check("series memory: a record for a pruned minute is refused (never re-created)",
          int((t0 + 30) // 60) not in a.mins and toks() == before and len(a.mins) == rows, (toks(), before))
    a.feed(w.rec("assistant", end, "m", message={"id": "m-mm0", "model": "claude-opus-5-5", "role": "assistant", "stop_reason": "tool_use",
                                                   "content": [], "usage": {"input_tokens": 10, "output_tokens": 900}}))
    check("series memory: a rewrite of a reply whose minute was pruned takes nothing from any other minute",
          toks() == before and a.msg_usage["m-mm0"][1][1] == 900 and all(x >= 0 for r in a.mins.values() for x in r), (toks(), before))
    b = pm.Acc("long.jsonl", "sub")
    b.feed(w.tool(t0, "lg", "Monitor", {"description": "wait"}, agent="m"))
    b.feed(w.result(t0 + 10800, "lg", agent="m"))
    check("series memory: a lone three-hour call fills only the kept span",
          len(b.mins) <= cap and sum(1 for k, r in b.mins.items() if k > b.min_top - keep and r[pm.M_ACTIVE]) == keep, len(b.mins))


# ── 11. end to end: the real factory.py and bin/cc-factory ───────────────────
FACTORY_LIB = os.path.join(REPO, "lib", "cc_dash", "factory.py")
FACTORY_CLI = os.path.join(REPO, "bin", "cc-factory")
# The kernel's interpreter: with zsh's exported ARGV0, sys.executable can name an editor's AppImage.
PY = os.path.realpath("/proc/self/exe") if os.path.exists("/proc/self/exe") else sys.executable
CLI_HOME = os.path.join(BASE, "cli-home")
CLI_STATE = os.path.join(BASE, "cli-state")


def _cli_env():
    """The CLI's environment, as tests/factory_cli_cases.py builds it: temp HOME and state, no GIT_*."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("GIT_", "PYTHON", "CLAUDE_")) and k != "XDG_CONFIG_HOME"}
    env.update(HOME=CLI_HOME, XDG_STATE_HOME=CLI_STATE, XDG_CONFIG_HOME=os.path.join(CLI_HOME, ".config"),
               GIT_TERMINAL_PROMPT="0")
    return env


def _run(cmd, cwd):
    r = subprocess.run(cmd, cwd=cwd, env=_cli_env(), capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError("%s failed (%d): %s %s" % (" ".join(cmd[1:3]), r.returncode, r.stdout[-300:], r.stderr[-300:]))
    return r


def _git(repo, *args):
    return _run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"] + list(args), repo)


def _ccf(repo, *args):
    return _run([PY, FACTORY_CLI, "-C", repo] + list(args), BASE)     # cwd is no repo: -C picks the project


def test_real_factory():
    if not (os.path.isfile(FACTORY_LIB) and os.path.isfile(FACTORY_CLI) and shutil.which("git")):
        print("real factory: skipped (no lib/cc_dash/factory.py, bin/cc-factory or git)")
        return
    os.makedirs(CLI_HOME, exist_ok=True)
    os.makedirs(CLI_STATE, exist_ok=True)
    repo = os.path.realpath(os.path.join(BASE, "work", "shop"))
    os.makedirs(repo)
    _git(repo, "init", "-q")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "initial")
    _ccf(repo, "init")
    o1 = _ccf(repo, "new", "--", "Add CSV export").stdout.split()[0]
    o2 = _ccf(repo, "new", "--", "Rate limit the API").stdout.split()[0]
    # The spec gate's pass, written straight into the ledger: `cc-factory approve` refuses under Claude Code,
    # which runs this test (tests/factory_cli_cases.py covers approve itself); lane needs only the pass event.
    with open(os.path.join(repo, ".git", "cc_tool", "factory", "ledger.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"t": time.time(), "order": o1, "event": "gate", "gate": "spec", "state": "pass",
                             "status": "approved", "by": "cc-factory"}) + "\n")
    lane = json.loads(_ccf(repo, "lane", o1, "--json").stdout)
    run = "wf_fo001"
    _ccf(repo, "mark", o1, "building", "--run-id", run)
    check("real factory: the CLI made FO-001 and FO-002, a lane on factory/FO-001-... under .claude/worktrees",
          (o1, o2) == ("FO-001", "FO-002") and lane["branch"].startswith("factory/FO-001-") and os.path.isdir(lane["worktree"])
          and lane["worktree"].startswith(os.path.join(repo, ".claude", "worktrees") + "/"), (o1, o2, lane))

    # The main checkout ran the line: a Workflow with that run id, plan done, build running.
    sm = World("shop", "f0f0f0f0-0000-0000-0000-000000000001", cwd=repo, branch="main")
    sm.write_main([sm.prompt(T - 200, "/factory run FO-001"),
                   sm.tool(T - 195, "tu_line", "Workflow", {"script": "export const meta = { name: 'factory-line' }"}),
                   sm.result(T - 194, "tu_line", "launched", tur={"status": "async_launched", "runId": run})])
    sm.write_sub("p1", [sm.prompt(T - 193, "Plan FO-001", agent="p1"), sm.text(T - 120, "p1m", "Plan written.", agent="p1")],
                 meta={"agentType": "workflow-subagent", "description": "FO-001:plan", "workflowPhase": "Plan"}, run=run)
    sm.write_sub("b1", [sm.prompt(T - 118, "Build FO-001", agent="b1"),
                        sm.tool(T - 6, "b1_bash", "Bash", {"command": "make test"}, agent="b1", model="claude-sonnet-5-5")],
                 meta={"agentType": "workflow-subagent", "description": "FO-001:build", "workflowPhase": "Build"}, run=run)
    sm.journal(run, [{"type": "launched"},
                     {"type": "started", "agentId": "p1", "label": "FO-001:plan", "phase": "Plan"},
                     {"type": "started", "agentId": "b1", "label": "FO-001:build", "phase": "Build"},
                     {"type": "result", "agentId": "p1", "result": "ok"}])
    # ...and someone works in the lane worktree, on the lane branch.
    sl = World("shop-lane", "f0f0f0f0-0000-0000-0000-000000000002", cwd=lane["worktree"], branch=lane["branch"])
    sl.write_main([sl.prompt(T - 90, "Look over the lane"), sl.tool(T - 60, "sl_read", "Read", {"file_path": "README.md"}),
                   sl.result(T - 59, "sl_read"), sl.text(T - 30, "slm", "The lane is clean.")])

    hide_factory()                       # drop the stub, import the real module
    sys.modules.pop("cc_dash.factory", None)
    try:
        real = importlib.import_module("cc_dash.factory")
        check("real factory: graph.py now imports lib/cc_dash/factory.py itself",
              real is not stub and os.path.realpath(real.__file__) == os.path.realpath(FACTORY_LIB), getattr(real, "__file__", None))
        graph.ORDERS_TTL = 0
        st = fresh_store()
        p = poll(st, project="shop")
        nodes = by_id(p)
        n1, n2 = nodes.get(on("shop", "FO-001")), nodes.get(on("shop", "FO-002"))
        wid, lid = wn(sm.sid, run), sn(sl.sid)
        check("real factory: both orders are nodes under p:shop (FO-001 building, FO-002 queued), titled",
              n1 is not None and n2 is not None and (n1["state"], n2["state"]) == ("building", "queued")
              and (n1["label"], n2["label"]) == ("Add CSV export", "Rate limit the API") and n1["parent"] == n2["parent"] == "p:shop",
              (n1 and n1["state"], n2 and n2["state"]))
        d1 = (n1 or {}).get("detail") or {}
        check("real factory: FO-001's detail carries the ledger's run id and lane (worktree via tilde)",
              d1.get("run_id") == run and d1.get("lane") == {"branch": lane["branch"], "worktree": tilde(lane["worktree"])}, d1)
        runs = {e["id"] for e in p["edges"] if e["kind"] == "runs"}
        want = {"%s>%s" % (on("shop", "FO-001"), wid), "%s>%s" % (on("shop", "FO-001"), lid)}
        check("real factory: runs edges from FO-001 to its workflow and to the lane session, none from FO-002",
              runs == want, (sorted(runs - want), sorted(want - runs)))
        w = nodes.get(wid) or {}
        check("real factory: the workflow runs FO-001 and hangs under the main checkout's session",
              w.get("state") == "running" and (w.get("detail") or {}).get("order") == "FO-001" and w.get("parent") == sn(sm.sid), w)
        check("real factory: the lane session folds into the project, on the lane branch",
              (nodes.get(lid) or {}).get("parent") == "p:shop" and (nodes.get(lid) or {}).get("detail", {}).get("branch") == lane["branch"],
              nodes.get(lid))
        sta = {s["name"]: s["status"] for s in d1.get("stations") or []}
        check("real factory: the live overlay puts FO-001 at build (active) while FO-001:build runs; plan is not active",
              d1.get("station") == "build" and sta.get("build") == "active" and sta.get("plan") != "active", (d1.get("station"), sta))
        check("real factory: kpis.orders counts building 1 and queued 1", p["kpis"]["orders"] == {"building": 1, "queued": 1}, p["kpis"]["orders"])
        check("real factory: the hub's detail.orders agrees", nodes["p:shop"]["detail"]["orders"] == {"building": 1, "queued": 1},
              nodes["p:shop"]["detail"]["orders"])
        test_integrity(p, " (real factory)")
        pd = poll(st)
        check("real factory: the default floor has the same orders and links, and no other project has orders",
              {i for i in ids(pd) if i.startswith("o:")} == {on("shop", "FO-001"), on("shop", "FO-002")}
              and want <= {e["id"] for e in pd["edges"] if e["kind"] == "runs"}, sorted(i for i in ids(pd) if i.startswith("o:")))
    finally:
        use_stub()


# ── 10. latency ──────────────────────────────────────────────────────────────
def test_latency():
    """Many live transcripts: 12 extra sessions with 3 running subagents (200 tool calls each) next to the fixture."""
    graph.PLANS_TTL, graph.ORDERS_TTL = DEF_PLANS_TTL, DEF_ORDERS_TTL      # the cached path is what a poll runs
    for i in range(12):
        w = World(f"load{i}", f"aaaaaaaa-0000-0000-0000-{i:012d}")
        recs = [w.prompt(T - 400, "go")]
        for j in range(3):
            recs += [w.tool(T - 390 + j, f"sp{i}_{j}", "Agent", {"description": f"part {j}", "run_in_background": True, "prompt": "p"}),
                     w.result(T - 389 + j, f"sp{i}_{j}", "launched", tur={"status": "async_launched", "agentId": f"x{j}"})]
        recs.append(w.text(T - 380, f"lm{i}", "Waiting on three agents."))
        w.write_main(recs)
        for j in range(3):
            r = [w.prompt(T - 388, "part", agent=f"x{j}")]
            for k in range(200):
                r += [w.tool(T - 380 + k, f"x{i}{j}_{k}", "Read", {"file_path": f"/w/{k}"}, agent=f"x{j}"), w.result(T - 380 + k + .5, f"x{i}{j}_{k}", agent=f"x{j}")]
            r.append(w.tool(T - 3, f"x{i}{j}_run", "Bash", {"command": "make"}, agent=f"x{j}"))
            w.write_sub(f"x{j}", r, meta={"agentType": "general-purpose", "description": f"part {j}", "toolUseId": f"sp{i}_{j}"})
    st = Store(days=1, clock=lambda: NOW[0])
    st.refresh(force=True)
    p = graph.payload(st)                  # cold: parse and fill the caches
    times = []
    for _ in range(5):
        st._last = 0
        t0 = time.perf_counter()
        p = graph.payload(st)
        times.append((time.perf_counter() - t0) * 1000)
    med = sorted(times)[2]
    live = sum(1 for n in p["nodes"] if n["kind"] in ("session", "agent") and n["state"] in RUNNING)
    print(f"latency: {len(p['nodes'])} nodes, graph.payload warm median {med:.1f} ms ({live} running)")
    load = [n for n in p["nodes"] if n["kind"] == "agent" and n["id"].startswith("a:aaaaaaaa")]
    check("latency: the graph with ~50 live transcripts builds in well under a poll interval (target 150 ms)", med < 500, f"{med:.1f} ms")
    check("latency: all 36 extra subagents are nodes and running", len(load) == 36 and all(n["state"] == "tool" for n in load), len(load))
    check("latency: 12 more project hubs, each holding its session",
          sum(1 for n in p["nodes"] if n["kind"] == "project" and n["label"].startswith("load")) == 12)
    test_integrity(p, " (large)")
    with st.lock:                          # the series alone, as payload() builds it under the lock
        rows = st._board(NOW[0])["agents"]
        stimes = []
        for _ in range(21):
            t0 = time.perf_counter()
            graph._series(st.sessions, rows, "", NOW[0])
            stimes.append((time.perf_counter() - t0) * 1000)
    smed = sorted(stimes)[10]
    s = p["series"]
    print(f"latency: series alone median {smed:.3f} ms over {s['agents_total']} transcripts in the hour")
    check("latency: the series is a small part of the poll (median under 10 ms) and counts all 48 load transcripts working now",
          smed < 10 and s["agents"][-1] >= 48 and not series_probs(s, NOW[0]), (f"{smed:.3f} ms", s["agents"][-1]))


def run(fn, *a):
    try:
        return fn(*a)
    except Exception as e:  # noqa: BLE001
        tb = traceback.extract_tb(e.__traceback__)[-1]
        check(f"{fn.__name__}: raised {type(e).__name__}", False, f"{e} at {os.path.basename(tb.filename)}:{tb.lineno}")
        return None


def main():
    try:
        graph.PLANS_TTL = graph.ORDERS_TTL = 0
        build_world()
        set_orders()
        use_stub()
        st = fresh_store()
        p = run(test_shape, st)
        if p is not None:
            run(test_ids, p)
            run(test_integrity, p)
            run(test_tree, p)
            run(test_workflows, p)
            run(test_alerts, p)
        run(test_board, st)
        run(test_orders, st)
        run(test_scope, st)
        run(test_http, st)
        run(test_sig)
        run(test_alert_hub)
        run(test_rerun_link)
        run(test_series)
        run(test_series_memory)
        run(test_latency)
        run(test_real_factory)
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
    fails = [r for r in RESULTS if not r[1]]
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"FAIL  {name}  {detail}")
    print(f"total={len(RESULTS)}  pass={len(RESULTS) - len(fails)}  fail={len(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
