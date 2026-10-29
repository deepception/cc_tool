#!/usr/bin/env python3
"""Lookback-window checks for cc-dash: the per-request `days` parameter, the lazy widening of
the store's holding, and the header's window picker (lib/cc_dash/store.py, server.py, graph.py,
plans.py, routing.py, web/app.js).

Builds one fake ${CLAUDE_CONFIG_DIR} (and a temp XDG_STATE_HOME) with a fixed clock and three
projects. Each session's records and its transcript's mtime sit a fixed age back, away from
every window edge (1, 3, 7, 14 and 30 days), and every message has a round token count, so the
expected numbers are summed by hand from the model prices, not read back from the code under test:

  alpha  L0  90 seconds Opus 5.5    $0.12  (live)   A1  2 hours  Opus 5.5    $3.00
         A2  5 days     Opus 5.5    $3.00           plans/alpha-widget.md, ticked, edited 10 days ago
  beta   B0  6 hours    Sonnet 5.5  $0.30           W0  3 hours  Sonnet 5.5  $0.50 now + $1.00 a call 4 days ago
         B2  25 days    Opus 5.5    $8.00  (one guard catch)
  gamma  G1  2 days     Sonnet 5.5  $6.00           G2  12 days  Opus 5.5    $6.00 + a Sonnet subagent "sub1" $1.00
         plans/gamma-gizmo.md, ticked, edited 12 days ago

so the 1, 3, 7, 14 and 30 day windows hold 4, 5, 6, 7 and 8 sessions that spent $3.92, $9.92,
$13.92, $20.92 and $28.92 inside the window (W0 is in every window but only counts the part of its
spend that falls inside it). The launch window is 7 days.

Then it checks, for every windowed endpoint:
  - per-request windows: counts and totals match the hand sums, and every payload equals what a
    store launched at that window answers (the invariant), for a store launched at 30 days and
    for one launched at 7 that widens on demand;
  - two windows asked at the same moment through the real server, each answered for its own;
  - widening happens once and is kept (also across the poll's forced rescans), and the live
    board stays on the launch window;
  - validation and clipping of `days` (never a 400), days_max and days_options in /api/meta;
  - 404 for a session outside the request's window, 200 inside it;
  - the pairing token on every windowed endpoint, GET only;
  - latency: a slow widening does not hold the launch window's readers, and a second caller for
    the same wider window waits for the first instead of parsing again;
  - in headless Chrome (when playwright-core and Chrome are present): the header's window picker
    shows the launch window, changes the Factory floor and Overview numbers, survives a reload,
    works with localStorage throwing and from the keyboard, and logs no console error.
Nothing outside the temp dir is read or written (never the real ~/.claude).

Run:  python3 tests/test_cc_dash_window.py
"""
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


# Isolation first: a temp config dir and a temp state dir before anything of cc_dash is imported.
BASE = tempfile.mkdtemp(prefix="cc-dash-window-test-")
CFG = os.path.join(BASE, "claude")
CFG2 = os.path.join(BASE, "claude-many")       # the latency suite's tree: many old transcripts
STATE = os.path.join(BASE, "state")
os.makedirs(os.path.join(CFG, "projects"))
os.makedirs(os.path.join(CFG2, "projects"))
os.makedirs(os.path.join(STATE, "cc_tool"), mode=0o700)
os.chmod(os.path.join(STATE, "cc_tool"), 0o700)
os.environ["CLAUDE_CONFIG_DIR"] = CFG
os.environ["XDG_STATE_HOME"] = STATE

sys.path.insert(0, os.path.join(REPO, "lib"))
sys.path.insert(0, os.path.join(REPO, "tests"))
try:
    from cc_dash import graph  # noqa: E402
    from cc_dash import parser as parser_mod  # noqa: E402
    from cc_dash import plans as plans_mod  # noqa: E402
    from cc_dash.server import make_server  # noqa: E402
    from cc_dash.store import Store  # noqa: E402
    from dash_pairing import auto_cookie  # noqa: E402  (tests/dash_pairing.py: requests to a paired server carry the token)
except BaseException:
    shutil.rmtree(BASE, ignore_errors=True)
    raise

PLAYWRIGHT = os.environ.get("CC_DASH_PLAYWRIGHT", "/home/lukasz/Files/hobby/color-analyzer/webapp/node_modules/playwright-core")
CHROME = os.environ.get("CC_DASH_CHROME", "/usr/bin/google-chrome")
SHOTS = os.environ.get("CC_DASH_SHOTS", "")     # optional: save the browser check's screenshots here

DAY = 86400.0
HOUR = 3600.0
T = (int(time.time()) // 60) * 60 + 30            # "now": the middle of a wall-clock minute
NOW = [float(T)]
WINDOWS = (1, 3, 7, 14, 30)
LAUNCH = 7

# USD per million tokens (input, output) for the models the fixture uses; the fixture has no cache traffic.
PRICE = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0)}

#   key  project  age        model              msgs  input      output   [subagent: msgs, input]  [guard]
SPECS = [
    {"key": "L0", "project": "alpha", "age": 90.0, "model": "claude-opus-5-5", "k": 2, "inp": 10_000, "out": 1_000},
    {"key": "A1", "project": "alpha", "age": 2 * HOUR, "model": "claude-opus-5-5", "k": 2, "inp": 250_000, "out": 25_000},
    {"key": "B0", "project": "beta", "age": 6 * HOUR, "model": "claude-sonnet-5-5", "k": 2, "inp": 50_000, "out": 5_000},
    # a long-running session: written 3 hours ago, in every window, but 4 days ago it also made a $1.00 call
    {"key": "W0", "project": "beta", "age": 3 * HOUR, "model": "claude-sonnet-5-5", "k": 2, "inp": 125_000, "out": 0,
     "early": {"age": 4 * DAY, "inp": 500_000, "out": 0}},
    {"key": "G1", "project": "gamma", "age": 2 * DAY, "model": "claude-sonnet-5-5", "k": 2, "inp": 1_000_000, "out": 100_000},
    {"key": "A2", "project": "alpha", "age": 5 * DAY, "model": "claude-opus-5-5", "k": 2, "inp": 250_000, "out": 25_000},
    {"key": "G2", "project": "gamma", "age": 12 * DAY, "model": "claude-opus-5-5", "k": 2, "inp": 500_000, "out": 50_000,
     "sub": {"model": "claude-sonnet-5-5", "k": 2, "inp": 250_000}},
    {"key": "B2", "project": "beta", "age": 25 * DAY, "model": "claude-opus-5-5", "k": 2, "inp": 750_000, "out": 50_000, "guard": True},
]
SP = {sp["key"]: sp for sp in SPECS}
SID = {sp["key"]: f"{i + 1:08d}-aaaa-4aaa-8aaa-{i + 1:012d}" for i, sp in enumerate(SPECS)}
SUB = "sub1"
PLANS = {"alpha": ("docs/superpowers/plans/2026-10-15-alpha-widget.md", 10 * DAY),
         "gamma": ("docs/superpowers/plans/2026-10-13-gamma-gizmo.md", 12 * DAY)}
PLAN_TEXT = "# %s Plan\n\n### Task 1: First\n\n- [x] **Step 1: Done**\n\n### Task 2: Second\n\n- [ ] **Step 1: Todo**\n"


def msg_cost(model, inp, out):
    pin, pout = PRICE[model]
    return (inp * pin + out * pout) / 1e6


def parts(sp):
    """[(age, cost, new tokens, tool calls)] of a session's bursts: the main one at sp["age"], and an earlier call (`early`) if any."""
    cost = sp["k"] * msg_cost(sp["model"], sp["inp"], sp["out"])
    tokens = sp["k"] * (sp["inp"] + sp["out"])
    calls = sp["k"] - 1
    if sp.get("sub"):
        cost += sp["sub"]["k"] * msg_cost(sp["sub"]["model"], sp["sub"]["inp"], 0)
        tokens += sp["sub"]["k"] * sp["sub"]["inp"]
        calls += sp["sub"]["k"]
    out = [(sp["age"], cost, tokens, calls)]
    if sp.get("early"):
        e = sp["early"]
        out.append((e["age"], msg_cost(sp["model"], e["inp"], e["out"]), e["inp"] + e["out"], 1))
    return out


def spec_cost(sp):
    """The whole session's cost, whatever the window."""
    return sum(p[1] for p in parts(sp))


def expected(days):
    """What the `days` window must hold, summed from SPECS: a session is in when its last write is inside; what it
    adds to the totals is only what was said inside (a long-running session counts its recent part)."""
    edge = days * DAY
    got = [sp for sp in SPECS if sp["age"] < edge]
    inside = {sp["key"]: [p for p in parts(sp) if p[0] < edge] for sp in got}
    projects = sorted({sp["project"] for sp in got})
    cost_of = lambda sps: round(sum(p[1] for sp in sps for p in inside[sp["key"]]), 4)  # noqa: E731
    return {"keys": [sp["key"] for sp in got], "sessions": len(got), "cost": cost_of(got),
            "cost_full": round(sum(spec_cost(sp) for sp in got), 4),
            "tokens": sum(p[2] for sp in got for p in inside[sp["key"]]), "projects": projects,
            "tool_calls": sum(p[3] for sp in got for p in inside[sp["key"]]),
            "by_project": {pr: cost_of([sp for sp in got if sp["project"] == pr]) for pr in projects},
            "sessions_by_project": {pr: sum(1 for sp in got if sp["project"] == pr) for pr in projects},
            "subagents": sum(1 for sp in got if sp.get("sub")), "guard": sum(1 for sp in got if sp.get("guard")),
            "ids": {SID[sp["key"]] for sp in got}}


def usage(inp, out):
    return {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
            "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0}}


class Transcript:
    """One transcript file's records; the file's mtime is its last record's time."""

    def __init__(self, sid, cwd, agent=None):
        self.sid, self.cwd, self.agent = sid, cwd, agent
        self.lines = []
        self.n = 0

    def rec(self, kind, t, **kw):
        self.n += 1
        r = {"type": kind, "timestamp": iso(t), "sessionId": self.sid, "cwd": self.cwd, "gitBranch": "main", "version": "2.1.300",
             "entrypoint": "cli", "isSidechain": bool(self.agent), "uuid": "%s-%s-%d" % (self.sid[:8], self.agent or "m", self.n)}
        if self.agent:
            r["agentId"] = self.agent
        r.update(kw)
        self.lines.append(r)

    def prompt(self, t, text):
        self.rec("user", t, message={"role": "user", "content": [{"type": "text", "text": text}]})

    def call(self, t, tid, name, tool_input, model, inp, out, tur=None):
        """An assistant message that calls a tool, and the user record carrying the result."""
        self.rec("assistant", t, message={"id": "m_" + tid, "model": model, "role": "assistant", "stop_reason": "tool_use",
                                          "content": [{"type": "tool_use", "id": tid, "name": name, "input": tool_input}],
                                          "usage": usage(inp, out)})
        extra = {"toolUseResult": tur} if tur is not None else {}
        self.rec("user", t + 1, message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": "ok"}]}, **extra)

    def reply(self, t, mid, text, model, inp, out):
        self.rec("assistant", t, message={"id": mid, "model": model, "role": "assistant", "stop_reason": "end_turn",
                                          "content": [{"type": "text", "text": text}], "usage": usage(inp, out)})

    def write(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            for r in self.lines:
                fh.write(json.dumps(r) + "\n")
        last = max(datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")).timestamp() for r in self.lines)
        os.utime(path, (last, last))


def write_session(cfg, cwd, sid, t0, model, k, inp, out, sub=None, guard_log=None, early=None):
    """k assistant messages of inp/out tokens: k-1 tool calls (the first one an Agent spawn when `sub` is set), then a reply.
    Every record sits within a couple of minutes after t0. `early` ({age, inp, out}) is one more tool call made that long ago."""
    pdir = os.path.join(cfg, "projects", cwd.replace("/", "-"))
    m = Transcript(sid, cwd)
    if early:
        te = T - early["age"]
        m.prompt(te, "An earlier start")
        m.call(te + 5, "tu_%s_e" % sid[:8], "Bash", {"command": "echo early"}, model, early["inp"], early["out"])
    m.prompt(t0, "Work in " + os.path.basename(cwd))
    t = t0 + 5
    for i in range(k - 1):
        tid = "tu_%s_%d" % (sid[:8], i)
        if sub and i == 0:
            m.call(t, tid, "Agent", {"description": "Helper", "subagent_type": "general-purpose", "prompt": "help"}, model, inp, out,
                   tur={"status": "completed", "agentId": SUB})
            sub_tid = tid
        else:
            m.call(t, tid, "Bash", {"command": "echo %d" % i}, model, inp, out)
        t += 10
    m.reply(t, "m_%s_end" % sid[:8], "Done.", model, inp, out)
    m.write(os.path.join(pdir, sid + ".jsonl"))
    if sub:
        s = Transcript(sid, cwd, agent=SUB)
        s.prompt(t0 + 6, "help")
        st = t0 + 7
        for i in range(sub["k"]):
            name, ti = ("Read", {"file_path": "/x"}) if i < sub["k"] - 1 else ("SubagentHandback", {"message": "All done: the report."})
            s.call(st, "st_%d" % i, name, ti, sub["model"], sub["inp"], 0)
            st += 10
        sdir = os.path.join(pdir, sid, "subagents")
        s.write(os.path.join(sdir, "agent-%s.jsonl" % SUB))
        with open(os.path.join(sdir, "agent-%s.meta.json" % SUB), "w") as fh:
            json.dump({"agentType": "general-purpose", "description": "Helper", "toolUseId": sub_tid}, fh)
    if guard_log:
        os.makedirs(os.path.dirname(guard_log), exist_ok=True)
        with open(guard_log, "w") as fh:
            fh.write(json.dumps({"ts": t0 + 7, "kind": "deny", "tool": "Bash", "hook": "bash-guard", "command": "rm -rf /", "message": "no"}) + "\n")


def work(name):
    return os.path.join(BASE, "work", name)


def build_world():
    for proj in ("alpha", "beta", "gamma"):
        os.makedirs(os.path.join(work(proj), ".git"), exist_ok=True)
    for proj, (rel, age) in PLANS.items():
        path = os.path.join(work(proj), rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(PLAN_TEXT % proj.capitalize())
        os.utime(path, (T - age, T - age))
    for sp in SPECS:
        cwd = work(sp["project"])
        write_session(CFG, cwd, SID[sp["key"]], T - sp["age"], sp["model"], sp["k"], sp["inp"], sp["out"], sp.get("sub"),
                      guard_log=os.path.join(cwd, ".git", "cc_tool", "activity.jsonl") if sp.get("guard") else None, early=sp.get("early"))


def edges_clear():
    """No session may sit within 6 hours of a window edge, or the hand sums would depend on the minute the suite starts."""
    for sp in SPECS:
        for age in [p[0] for p in parts(sp)]:
            for w in WINDOWS:
                if abs(age - w * DAY) < 6 * HOUR:
                    return False
    return True


build_world()


def new_store(days=LAUNCH, root=CFG):
    return Store(root=root, days=days, clock=lambda: NOW[0])


# ── helpers ──────────────────────────────────────────────────────────────────
def serve(store, pair=True):
    srv = make_server(0, store, fallback=False)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    if pair:
        auto_cookie(srv)          # requests to this port now carry its pairing token
    return srv


def stop(srv):
    srv.shutdown()
    srv.server_close()


def request(port, path, method="GET"):
    """(status, parsed JSON body or None)."""
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    headers = {"Host": "127.0.0.1:%d" % port}
    if method != "GET":
        headers["Content-Type"] = "application/json"
    c.request(method, path, body=None if method == "GET" else b"{}", headers=headers)
    r = c.getresponse()
    raw = r.read()
    c.close()
    try:
        return r.status, json.loads(raw)
    except ValueError:
        return r.status, None


def num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def close(a, b, tol=1e-3):
    return num(a) and num(b) and abs(a - b) <= tol


def norm(x, sort):
    """A payload with numbers as rounded floats; with `sort`, every list in a canonical order."""
    if isinstance(x, bool) or x is None or isinstance(x, str):
        return x
    if num(x):
        return round(float(x), 6) + 0.0
    if isinstance(x, dict):
        return {k: norm(v, sort) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        out = [norm(v, sort) for v in x]
        return sorted(out, key=lambda v: json.dumps(v, sort_keys=True, default=str)) if sort else out
    return str(x)


def same(a, b):
    """Equal payloads: numbers to 6 places; lists in order, or (when only the tie order differs) as sets."""
    if a is None or b is None:
        return a is None and b is None
    return norm(a, False) == norm(b, False) or norm(a, True) == norm(b, True)


def strip(p):
    return {k: v for k, v in p.items() if k not in ("now", "scan", "sig")} if isinstance(p, dict) else p


def differing(a, b):
    """Which payloads of two collect() results differ, with the top-level keys that do."""
    out = []
    for k in sorted(set(a) | set(b)):
        x, y = strip(a.get(k)), strip(b.get(k))
        if same(x, y):
            continue
        if isinstance(x, dict) and isinstance(y, dict):
            keys = sorted(kk for kk in set(x) | set(y) if not same(x.get(kk), y.get(kk)))
            out.append("%s[%s]" % (k, ",".join(keys)))
        else:
            out.append("%s[%s vs %s]" % (k, "none" if x is None else "payload", "none" if y is None else "payload"))
    return out


def collect(st, days=None):
    """Every windowed payload of a store for one window (days=None: the store's own launch window)."""
    kw = {} if days is None else {"days": days}
    out = {"sessions": st.sessions_payload(**kw),
           "overview": st.overview_payload("", **kw), "overview:alpha": st.overview_payload("alpha", **kw),
           "routing": st.routing_payload("", **kw), "routing:gamma": st.routing_payload("gamma", **kw),
           "graph": graph.payload(st, "", True, **kw), "graph:beta": graph.payload(st, "beta", False, **kw)}
    eng = plans_mod.for_store(st)
    out["plans"] = eng.payload("", [], **kw)
    out["plans:all"] = eng.payload("", [], everything=True, **kw)
    for proj in ("alpha", "gamma"):
        out["plans:detail:" + proj] = eng.detail(proj, PLANS[proj][0], [], **kw)
    for sp in SPECS:
        out["session:" + sp["key"]] = st.session_payload(SID[sp["key"]], **kw)
        out["tree:" + sp["key"]] = st.tree_payload(SID[sp["key"]], **kw)
    out["agent:G2"] = st.agent_payload(SID["G2"], SUB, **kw)
    return out


def money(v):
    return "$%.2f" % v


def check_window(tag, col, d, e):
    """The hand sums against one window's payloads."""
    ov, sp, rt, g = col["overview"], col["sessions"], col["routing"], col["graph"]
    t = ov["totals"]
    got = (t["sessions"], t["cost"], t["tokens"], t["tool_calls"], t["subagents"], sum(ov["guard"].values()))
    want = (e["sessions"], e["cost"], e["tokens"], e["tool_calls"], e["subagents"], e["guard"])
    check(f"{tag} days={d}: Overview holds {e['sessions']} sessions, {money(e['cost'])}, {e['subagents']} subagents, {e['guard']} guard",
          close(got[1], want[1]) and (got[0], got[2], got[3], got[4], got[5]) == (want[0], want[2], want[3], want[4], want[5]) and ov["days"] == d,
          f"{got} vs {want}, days={ov.get('days')}")
    check(f"{tag} days={d}: Overview lists exactly the projects in the window and their costs",
          sorted(p["project"] for p in ov["projects"]) == e["projects"] and all(close(p["cost"], e["by_project"][p["project"]]) for p in ov["projects"])
          and {p["project"] for p in ov["project_list"]} == set(e["projects"]),
          f"{[(p['project'], p['cost']) for p in ov['projects']]} vs {e['by_project']}")
    hours_cost = sum(c for hh in ov["hours"] for c in hh["c"].values())
    check(f"{tag} days={d}: cost chart sums to the window's cost (hour buckets cut at the window)", close(hours_cost, e["cost"]), hours_cost)
    rows = sp["sessions"]
    check(f"{tag} days={d}: Sessions list has the {e['sessions']} sessions of the window",
          {r["id"] for r in rows} == e["ids"] and len(rows) == e["sessions"] and sp["days"] == d
          and {p["project"] for p in sp["projects"]} == set(e["projects"]) and close(sum(r["cost"] for r in rows), e["cost_full"]),
          f"{sorted(r['id'][:2] for r in rows)} days={sp.get('days')}")
    sub_cost = msg_cost("claude-sonnet-5-5", 250_000, 0) * 2
    check(f"{tag} days={d}: Routing sees {e['subagents']} subagent(s) in the window",
          rt["agents"] == e["subagents"] and close(rt["cost"], sub_cost * e["subagents"]) and rt["days"] == d, f"{rt['agents']} {rt['cost']} days={rt.get('days')}")
    hubs = {n["id"]: n["detail"] for n in g["nodes"] if n["kind"] == "project"}
    check(f"{tag} days={d}: Factory floor (all=1) keeps the live session, counts the window's cost and sessions per project",
          close(g["window_days"], d) and close(g["kpis"]["cost_window"], e["cost"]) and close(g["kpis"]["cost_live"], spec_cost(SP["L0"]))
          and set(hubs) == {"p:" + p for p in e["projects"]}
          and all(hubs["p:" + p]["sessions_window"] == n and close(hubs["p:" + p]["cost_window"], e["by_project"][p]) for p, n in e["sessions_by_project"].items()),
          f"window_days={g.get('window_days')} kpis={ {k: g['kpis'][k] for k in ('cost_window', 'cost_live')} } hubs={ {k: (v['sessions_window'], v['cost_window']) for k, v in hubs.items()} }")
    guard_n = sum(g["kpis"]["guard"].values())
    check(f"{tag} days={d}: Factory guard KPI counts the window's catches", guard_n == e["guard"], guard_n)
    total = 1 if d < 3 else 2          # a project's plans are listed only while the project has a session in the window
    shown = 2 if d >= 14 else 0        # edited 10 and 12 days ago: "recent" only inside a window that reaches them
    pl, pla = col["plans"], col["plans:all"]
    check(f"{tag} days={d}: Plans lists {total} plan(s), {shown} recent",
          pl["total"] == total and len(pl["plans"]) == shown and pl["older"] == total - shown and len(pla["plans"]) == total
          and pl["days"] == d and pla["days"] == d, f"total={pl['total']} shown={len(pl['plans'])} older={pl['older']} days={pl.get('days')}")
    dg = col["plans:detail:gamma"]
    check(f"{tag} days={d}: gamma's plan opens only once gamma has a session in the window",
          (dg is None) == (d < 3) and (dg is None or dg["days"] == d) and col["plans:detail:alpha"] is not None and col["plans:detail:alpha"]["days"] == d,
          f"gamma={'none' if dg is None else dg.get('days')}")
    ok = all((col["session:" + sp["key"]] is not None) == (sp["key"] in e["keys"]) and (col["tree:" + sp["key"]] is not None) == (sp["key"] in e["keys"])
             for sp in SPECS)
    ok = ok and all(col["session:" + k]["days"] == d and col["tree:" + k]["days"] == d for k in e["keys"])
    check(f"{tag} days={d}: session and agent-tree payloads exist exactly for the window's sessions, and echo the window", ok,
          [(sp["key"], col["session:" + sp["key"]] is not None) for sp in SPECS])
    ag = col["agent:G2"]
    check(f"{tag} days={d}: the subagent opens exactly when its session is in the window",
          (ag is not None) == ("G2" in e["keys"]) and (ag is None or ag["days"] == d), "none" if ag is None else ag.get("days"))


def test_windows():
    check("fixture: no session sits within 6 hours of a window edge", edges_clear())
    big = new_store(30)
    lazy = new_store(7)
    for d in WINDOWS:
        e = expected(d)
        fresh = collect(new_store(d))
        for tag, st in (("launch-30 store", big), ("launch-7 store, widened on demand", lazy)):
            col = collect(st, d)
            check_window(tag, col, d, e)
            diff = differing(col, fresh)
            check(f"invariant: {tag} answers every payload for days={d} like Store(days={d}) ({len(col)} payloads)", not diff, "; ".join(diff[:4]))
        check(f"fresh Store(days={d}) agrees with the hand sums too",
              close(fresh["overview"]["totals"]["cost"], e["cost"]) and fresh["overview"]["totals"]["sessions"] == e["sessions"], fresh["overview"]["totals"])
    teeth = differing(collect(new_store(7)), collect(new_store(30)))
    check("invariant check has teeth: Store(days=7) and Store(days=30) are told apart (sessions, overview, graph, routing, plans)",
          all(any(x.startswith(k) for x in teeth) for k in ("sessions[", "overview[", "graph[", "routing[", "plans[")), teeth[:6])
    # The default window (no days) is the launch value on a store that has been widened.
    diff = differing(collect(lazy), collect(new_store(7)))
    check("a widened store still answers a request without `days` for the launch window", not diff, "; ".join(diff[:4]))
    ids = lambda ss: {s.sid for s in ss}  # noqa: E731
    check("window_sessions(days) holds the window's sessions, window_sessions() the launch window",
          all(ids(big.window_sessions(d)) == expected(d)["ids"] for d in WINDOWS) and ids(big.window_sessions()) == expected(30)["ids"]
          and ids(lazy.window_sessions()) == expected(7)["ids"] and ids(lazy.window_sessions(3)) == expected(3)["ids"],
          [len(big.window_sessions(d)) for d in WINDOWS])
    check("scope_sessions(project, days) narrows by project inside the window",
          ids(big.scope_sessions("alpha", 3)) == {SID["L0"], SID["A1"]} and ids(big.scope_sessions("gamma", 3)) == {SID["G1"]}
          and ids(big.scope_sessions("gamma", 1)) == set() and ids(big.scope_sessions("", 14)) == expected(14)["ids"]
          and ids(lazy.scope_sessions("alpha")) == {SID["L0"], SID["A1"], SID["A2"]},
          [len(big.scope_sessions("alpha", 3)), len(big.scope_sessions("gamma", 3))])
    check("Store.sessions stays the launch window on a store widened to 30 days", set(lazy.sessions) == expected(7)["ids"] and lazy.span == 30.0,
          f"{len(lazy.sessions)} sessions, span {lazy.span}")


def transcript_bytes():
    """Bytes of every transcript under the fixture's config dir (main and subagent)."""
    n = 0
    for dp, _, fns in os.walk(os.path.join(CFG, "projects")):
        n += sum(os.path.getsize(os.path.join(dp, f)) for f in fns if f.endswith(".jsonl"))
    return n


def file_bytes(key, sub=False):
    sp = next(s for s in SPECS if s["key"] == key)
    pdir = os.path.join(CFG, "projects", work(sp["project"]).replace("/", "-"))
    return os.path.getsize(os.path.join(pdir, SID[key], "subagents", "agent-%s.jsonl" % SUB) if sub else os.path.join(pdir, SID[key] + ".jsonl"))


def test_two_windows_at_once():
    st = new_store(7)
    st.refresh(force=True)         # as bin/cc-dash does before it serves
    srv = serve(st)
    port = srv.port
    rounds = [(1, 30), (30, 1), (3, 14), (14, 3), (1, 30), (7, 30)]
    bad = []
    answers = 0
    try:
        for rnd, (d1, d2) in enumerate(rounds):
            jobs = []
            for d in (d1, d2, d1, d2):
                jobs += [("/api/overview", d), ("/api/graph?all=1", d)]
            results = [None] * len(jobs)
            gate = threading.Barrier(len(jobs))

            def run(i, path, d):
                gate.wait()
                results[i] = request(port, "%s%sdays=%s" % (path, "&" if "?" in path else "?", d))

            threads = [threading.Thread(target=run, args=(i, p, d)) for i, (p, d) in enumerate(jobs)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(90)
            for (path, d), res in zip(jobs, results):
                answers += 1
                e = expected(d)
                code, body = res if res else (0, None)
                if code != 200 or not isinstance(body, dict):
                    bad.append(f"round {rnd} {path} days={d}: HTTP {code}")
                    continue
                if path.startswith("/api/overview"):
                    ok = body["days"] == d and body["totals"]["sessions"] == e["sessions"] and close(body["totals"]["cost"], e["cost"])
                    got = (body.get("days"), body["totals"]["sessions"], body["totals"]["cost"])
                else:
                    hubs = {n["id"] for n in body["nodes"] if n["kind"] == "project"}
                    ok = close(body["window_days"], d) and close(body["kpis"]["cost_window"], e["cost"]) and hubs == {"p:" + p for p in e["projects"]}
                    got = (body.get("window_days"), body["kpis"]["cost_window"], sorted(hubs))
                if not ok:
                    bad.append(f"round {rnd} {path} days={d}: got {got}, want sessions={e['sessions']} cost={e['cost']}")
        check(f"two windows at once: {answers} concurrent overview/graph answers over {len(rounds)} rounds each carry their own window's totals",
              not bad and answers == len(rounds) * 8, "; ".join(bad[:3]))
        check("two windows at once: the store widened once, to the widest window asked", st.span == 30.0 and st.last_widen and st.last_widen["to"] == 30.0,
              (st.span, st.last_widen))
        check("two windows at once: every transcript was read exactly once across all those requests",
              st.cache.bytes_read == transcript_bytes(), f"{st.cache.bytes_read} read vs {transcript_bytes()} on disk")
    finally:
        stop(srv)


def test_widening():
    st = new_store(7)
    srv = serve(st)
    port = srv.port
    meta = lambda: request(port, "/api/meta")[1]  # noqa: E731
    try:
        m = meta()
        check("meta: launch window 7, days_max 30, options 1/3/7/14/30, span 7, nothing widened yet",
              m["days"] == 7 and m["days_max"] == 30 and m["days_options"] == [1, 3, 7, 14, 30] and m["days_span"] == 7 and m["widen"] is None
              and m["sessions"] == len(expected(7)["ids"]), {k: m.get(k) for k in ("days", "days_max", "days_options", "days_span", "widen", "sessions")})
        launch = set(st.sessions)
        for d in (3, 1, 7):
            request(port, "/api/overview?days=%s" % d)
        m = meta()
        check("a window inside the held one (3, 1, 7) does not widen", m["days_span"] == 7 and m["widen"] is None and st.last_widen is None
              and st.cache.bytes_read == transcript_bytes() - file_bytes("G2") - file_bytes("G2", True) - file_bytes("B2"),
              (m["days_span"], m["widen"], st.cache.bytes_read))
        live0 = strip(st.live_payload())
        request(port, "/api/overview?days=14")
        m = meta()
        w14 = m["widen"]
        keys = {"from", "to", "secs", "files", "parsed", "bytes", "lock_secs"}
        check("days=14 widens 7 -> 14 and reports it", m["days_span"] == 14 and isinstance(w14, dict) and keys <= set(w14) and w14["from"] == 7 and w14["to"] == 14, w14)
        check("the widening read the two older files (12-day session and its subagent), and only them",
              isinstance(w14, dict) and w14["parsed"] == 2 and w14["bytes"] == file_bytes("G2") + file_bytes("G2", True) and w14["lock_secs"] < 0.25 and w14["secs"] >= 0, w14)
        request(port, "/api/overview?days=30")
        m = meta()
        w30 = m["widen"]
        check("days=30 widens 14 -> 30 (only the 25-day session is new)",
              m["days_span"] == 30 and isinstance(w30, dict) and w30["from"] == 14 and w30["to"] == 30 and w30["parsed"] == 1 and w30["bytes"] == file_bytes("B2"), w30)
        b1 = st.cache.bytes_read
        for path in ("/api/overview?days=30", "/api/overview?days=14", "/api/graph?all=1&days=30", "/api/routing?days=14", "/api/sessions?days=30",
                     "/api/plans?days=30", "/api/overview?days=1", "/api/overview?days=0.5", "/api/overview"):
            request(port, path)
        m = meta()
        check("asking 30 again, then 14, 1, 0.5 and none does not widen again: last_widen is unchanged", m["widen"] == w30 and m["days_span"] == 30, m["widen"])
        check("...and reads no byte more while the transcripts are unchanged", st.cache.bytes_read == b1 == transcript_bytes(), (st.cache.bytes_read, b1, transcript_bytes()))
        check("Store.sessions still holds only the launch window; meta.sessions counts it", set(st.sessions) == launch and m["sessions"] == len(expected(7)["ids"]) and st.span == 30.0,
              (len(st.sessions), m["sessions"], st.span))
        live1 = strip(st.live_payload())
        check("the live board is the same after widening", same(live0, live1), differing({"live": live0}, {"live": live1}))
        # The server polls every few seconds: a forced rescan of the launch window must not drop what the widening parsed.
        NOW[0] = float(T) + 5
        st.refresh(force=True)
        m = meta()
        b2 = st.cache.bytes_read
        ov = request(port, "/api/overview?days=30")[1]
        check("a rescan of the launch window keeps the widened window: the next days=30 read nothing new, no new widening",
              st.cache.bytes_read == b2 == b1 and m["widen"] == w30 and ov["totals"]["sessions"] == expected(30)["sessions"] and st.span == 30.0 and set(st.sessions) == launch,
              (st.cache.bytes_read, b1, m["widen"], ov["totals"]["sessions"]))
        # An old session that is resumed: its transcript grows and its mtime is new. It joins the launch window, only the
        # new bytes are read, and every window still answers like a store launched at that window.
        old = os.path.join(CFG, "projects", work("gamma").replace("/", "-"), SID["G2"] + ".jsonl")
        size0, mt0 = os.path.getsize(old), os.stat(old).st_mtime
        extra = json.dumps({"type": "user", "timestamp": iso(T + 6), "sessionId": SID["G2"], "cwd": work("gamma"), "uuid": "late-1",
                            "message": {"role": "user", "content": [{"type": "text", "text": "one more"}]}}) + "\n"
        try:
            with open(old, "a") as fh:
                fh.write(extra)
            os.utime(old, (T + 6, T + 6))
            NOW[0] = float(T) + 10
            st.refresh(force=True)
            request(port, "/api/overview?days=30")
            check("a resumed old session: only its new bytes are read, and it moves into the launch window",
                  st.cache.bytes_read - b1 == len(extra.encode()) and SID["G2"] in st.sessions and st.span == 30.0,
                  (st.cache.bytes_read - b1, len(extra.encode()), SID["G2"] in st.sessions))
            diffs = []
            for d in (1, 7, 30):
                diffs += ["days=%s %s" % (d, x) for x in differing(collect(st, d), collect(new_store(d)))]
            check("a resumed old session: the widened store still answers days=1, 7 and 30 like a store launched at that window", not diffs, "; ".join(diffs[:4]))
        finally:
            os.truncate(old, size0)
            os.utime(old, (mt0, mt0))
    finally:
        NOW[0] = float(T)
        stop(srv)


CLAMP = [("", 7), (None, 7), ("abc", 7), ("nan", 7), ("NaN", 7), ("inf", 7), ("-inf", 7), ("Infinity", 7), ("-1", 7), ("0", 7), ("-0", 7), ("0.0", 7),
         ("1e400", 7), ("-1e400", 7), ("x" * 40, 7), ("12abc", 7), ("31", 30), ("999", 30), ("30.0001", 30), ("30", 30), ("0.5", 0.5), ("1", 1),
         ("3", 3), ("7", 7), ("14", 14), ("2.5", 2.5), ("9" * 30, 30)]
HTTP_RAW = ["abc", "nan", "inf", "-1", "0", "1e400", "x" * 40, "31", "999", "0.5", "9" * 30, "7", "3"]


def effective(raw, launch=7.0, top=30.0):
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return launch
    return launch if v != v or v in (float("inf"), float("-inf")) or v <= 0 else min(v, top)


def test_validation():
    from urllib.parse import quote
    st = new_store(7)
    bad = [(raw, st.clamp_days(raw), want) for raw, want in CLAMP if not (num(st.clamp_days(raw)) and st.clamp_days(raw) == want)]
    check(f"clamp_days: {len(CLAMP)} inputs (empty, None, junk, nan, inf, <=0, 1e400, over 30, fractions) land on the launch value, 30 or themselves",
          not bad and isinstance(st.clamp_days("3"), float) and isinstance(st.clamp_days("abc"), float), bad[:3])
    check("Store.days_max is max(30, launch)", st.days_max == 30.0 and new_store(45).days_max == 45.0 and new_store(10).days_max == 30.0, (st.days_max, new_store(45).days_max))
    s45 = new_store(45)
    s05 = new_store(0.5)
    check("clamp_days with launch 45: over 45 -> 45, 31 stays 31, junk -> 45",
          [s45.clamp_days(x) for x in ("46", "100", "31", "45", "-1", "", "inf")] == [45, 45, 31, 45, 45, 45, 45], [s45.clamp_days(x) for x in ("46", "100", "31", "45", "-1", "", "inf")])
    check("clamp_days with launch 0.5: junk -> 0.5, 3 stays 3", [s05.clamp_days(x) for x in ("junk", "-2", "3")] == [0.5, 0.5, 3], [s05.clamp_days(x) for x in ("junk", "-2", "3")])
    # Over HTTP: the echo field shows the effective window, and nothing is ever a 400.
    srv = serve(st)
    port = srv.port
    plan_q = "project=alpha&path=" + quote(PLANS["alpha"][0])
    ends = [("/api/overview?", "days"), ("/api/sessions?", "days"), ("/api/routing?", "days"), ("/api/plans?", "days"), ("/api/graph?all=1&", "window_days"),
            ("/api/plans/detail?" + plan_q + "&", "days"), ("/api/session?id=%s&" % SID["L0"], "days"), ("/api/session/tree?id=%s&" % SID["L0"], "days")]
    problems = []
    n = 0
    try:
        for q, key in ends:
            for raw in HTTP_RAW + [None]:
                n += 1
                code, body = request(port, q + ("days=" if raw is None else "days=" + quote(raw)))
                want = effective(raw if raw is not None else "")
                if code != 200 or not isinstance(body, dict) or not close(body.get(key), want, 1e-9):
                    problems.append(f"{q}{raw!r}: HTTP {code} {key}={body.get(key) if isinstance(body, dict) else body} want {want}")
        check(f"http: {n} windowed requests with empty, junk, nan, inf, negative, zero, 1e400, 40-char, 31, 999, 0.5 `days` are all 200 and echo the effective window",
              not problems, "; ".join(problems[:3]))
        agent_problems = []
        for raw in HTTP_RAW:
            code, body = request(port, "/api/agent?session=%s&id=%s&days=%s" % (SID["G2"], SUB, quote(raw)))
            eff = effective(raw)
            if code != (200 if eff > 12 else 404) or (code == 200 and not close(body.get("days"), eff, 1e-9)):
                agent_problems.append(f"{raw!r}: HTTP {code} (effective {eff})")
        check("http: /api/agent with junk `days` is 404 or 200 by the effective window, never 400", not agent_problems, "; ".join(agent_problems[:3]))
        code, body = request(port, "/api/overview?days=3&days=14")
        check("http: with `days` twice the first value wins", code == 200 and body["days"] == 3, (code, body.get("days")))
        code, body = request(port, "/api/overview?days=0.5")
        e = expected(0.5)
        check("http: days=0.5 is a twelve-hour window", code == 200 and body["days"] == 0.5 and body["totals"]["sessions"] == e["sessions"] and 0 < e["sessions"] < expected(30)["sessions"] and close(body["totals"]["cost"], e["cost"]),
              (code, body["totals"]["sessions"], e["sessions"]))
        code, body = request(port, "/api/live?days=1")
        live_default = request(port, "/api/live")[1]
        check("http: /api/live ignores `days`", code == 200 and same(strip(body), strip(live_default)), code)
        code, body = request(port, "/api/meta?days=1")
        check("http: /api/meta ignores `days` (days is still the launch value)", code == 200 and body["days"] == 7, (code, body.get("days")))
    finally:
        stop(srv)
    # Launch values other than 7: the options list and the ceiling.
    for launch, opts, mx in ((7, [1, 3, 7, 14, 30], 30), (10, [1, 3, 7, 10, 14, 30], 30), (30, [1, 3, 7, 14, 30], 30), (45, [1, 3, 7, 14, 30, 45], 45), (0.5, [0.5, 1, 3, 7, 14, 30], 30)):
        s = new_store(launch)
        srv = serve(s)
        try:
            code, m = request(srv.port, "/api/meta")
            check(f"meta: launch {launch} -> days_options {opts}, days_max {mx}, days_span {launch}",
                  code == 200 and m["days"] == launch and m["days_options"] == opts and m["days_max"] == mx and m["days_span"] == launch and m["widen"] is None,
                  {k: m.get(k) for k in ("days", "days_options", "days_max", "days_span", "widen")})
            if launch == 45:
                got = [request(srv.port, "/api/overview?days=%s" % x)[1]["days"] for x in ("46", "100", "31", "45", "abc")]
                check("http: launch 45 -> days=46 and 100 clip to 45, 31 is allowed, junk gives 45", got == [45, 45, 31, 45, 45], got)
        finally:
            stop(srv)


def test_outside_window():
    st = new_store(7)
    srv = serve(st)
    port = srv.port
    g2, a2, a1, l0, b0 = SID["G2"], SID["A2"], SID["A1"], SID["L0"], SID["B0"]
    try:
        def status(path):
            code, body = request(port, path)
            return code, body
        trio = (("session", "/api/session?id=%s" % g2), ("tree", "/api/session/tree?id=%s" % g2), ("agent", "/api/agent?session=%s&id=%s" % (g2, SUB)))
        for name, path in trio:
            outs = [status(path + "&days=7"), status(path), status(path + "&days=3")]
            check(f"404 outside the window: {name} of the 12-day session is 404 for days=7, 3 and with no days (launch 7)",
                  all(c == 404 and isinstance(b, dict) and isinstance(b.get("error"), str) for c, b in outs), [c for c, _ in outs])
        check("a 404 outside the held window does not widen the store", st.span == 7.0 and st.last_widen is None, (st.span, st.last_widen))
        outs = [status(path + "&days=11.9")[0] for _, path in trio]
        check("404 still holds for a window that reaches almost, but not quite, to the session (11.9 days vs 12)", outs == [404, 404, 404], outs)
        for name, path in (("session", "/api/session?id=%s" % g2), ("tree", "/api/session/tree?id=%s" % g2), ("agent", "/api/agent?session=%s&id=%s" % (g2, SUB))):
            c14, b14 = status(path + "&days=14")
            c30, b30 = status(path + "&days=30")
            check(f"200 inside the window: {name} of the 12-day session answers for days=14 and 30, echoing the window",
                  c14 == 200 and c30 == 200 and close(b14.get("days"), 14) and close(b30.get("days"), 30), (c14, c30))
        c, b = status("/api/session?id=%s&days=14" % g2)
        check("the widened session payload is the session's", c == 200 and b["session"]["id"] == g2 and b["session"]["project"] == "gamma" and b["session"]["subagents"] == 1
              and close(b["session"]["cost"], expected(14)["by_project"]["gamma"] - spec_cost(SP["G1"])),
              (c, b["session"]["cost"] if c == 200 else None))
        # Other ages against the same rule.
        checks = [("5-day session, days=3", "/api/session?id=%s&days=3" % a2, 404), ("5-day session, launch window", "/api/session?id=%s" % a2, 200),
                  ("5-day session tree, days=3", "/api/session/tree?id=%s&days=3" % a2, 404), ("5-day session tree, days=5.5", "/api/session/tree?id=%s&days=5.5" % a2, 200),
                  ("2-hour session, days=1", "/api/session?id=%s&days=1" % a1, 200), ("2-hour session, days=0.05 (72 min)", "/api/session?id=%s&days=0.05" % a1, 404),
                  ("90-second session, days=0.05", "/api/session?id=%s&days=0.05" % l0, 200), ("6-hour session, days=0.2 (4.8 h)", "/api/session?id=%s&days=0.2" % b0, 404),
                  ("unknown id, days=30", "/api/session?id=nope&days=30", 404), ("unknown id tree, days=30", "/api/session/tree?id=nope&days=30", 404),
                  ("unknown agent, days=30", "/api/agent?session=%s&id=nope&days=30" % g2, 404)]
        got = [(label, status(path)[0], want) for label, path, want in checks]
        check("404/200 by the request's own window for sessions of other ages", all(c == w for _, c, w in got), [x for x in got if x[1] != x[2]])
    finally:
        stop(srv)


def test_auth():
    st_u, st_p = new_store(7), new_store(7)
    srv_u, srv_p = serve(st_u, pair=False), serve(st_p)
    from urllib.parse import quote
    paths = ["/api/graph?all=1", "/api/sessions", "/api/overview", "/api/routing", "/api/plans", "/api/plans/detail?project=alpha&path=" + quote(PLANS["alpha"][0]),
             "/api/session?id=%s" % SID["L0"], "/api/session/tree?id=%s" % SID["L0"], "/api/agent?session=%s&id=%s" % (SID["G2"], SUB), "/api/meta"]
    try:
        got = []
        for p in paths:
            sep = "&" if "?" in p else "?"
            code, body = request(srv_u.port, p + sep + "days=30")
            got.append((p.split("?")[0], code, bool(body and body.get("repair"))))
        check("auth: every windowed endpoint (and meta) with days=30 and no token is 401 with repair", all(c == 401 and r for _, c, r in got), [g for g in got if g[1] != 401 or not g[2]])
        c = http.client.HTTPConnection("127.0.0.1", srv_u.port, timeout=10)
        c.request("GET", "/api/overview?days=30", headers={"Host": "127.0.0.1:%d" % srv_u.port, "X-CC-Dash-Token": "not-a-token"})
        r = c.getresponse()
        r.read()
        c.close()
        check("auth: a wrong token is 401 too", r.status == 401, r.status)
        check("auth: unauthenticated requests never widen the store", st_u.span == 7.0 and st_u.last_widen is None and st_u.cache.bytes_read == 0, (st_u.span, st_u.last_widen))
        ok = [request(srv_p.port, p + ("&" if "?" in p else "?") + "days=3")[0] for p in paths[:9] if "agent" not in p]
        check("auth: with the token the same requests answer (200)", all(c == 200 for c in ok), ok)
        posts = []
        for p in paths:
            sep = "&" if "?" in p else "?"
            posts.append((p.split("?")[0], request(srv_p.port, p + sep + "days=30", "POST")[0], request(srv_u.port, p + sep + "days=30", "POST")[0]))
        check("auth: POST to any windowed endpoint is 405, with or without the token", all(a == 405 and b == 405 for _, a, b in posts), [x for x in posts if x[1:] != (405, 405)])
        check("auth: those POSTs did not widen the store", st_p.span == 7.0 and st_p.last_widen is None, (st_p.span, st_p.last_widen))
        put = []
        for method in ("PUT", "DELETE", "PATCH"):
            c = http.client.HTTPConnection("127.0.0.1", srv_p.port, timeout=10)
            c.request(method, "/api/overview?days=30", body=b"{}", headers={"Host": "127.0.0.1:%d" % srv_p.port})
            r = c.getresponse()
            r.read()
            c.close()
            put.append(r.status)
        check("auth: PUT, DELETE and PATCH are 405 as well", put == [405, 405, 405], put)
    finally:
        stop(srv_u)
        stop(srv_p)


def build_many():
    """A second config dir: two recent sessions and thirty older ones, for the latency suite."""
    cwd = work("many")
    os.makedirs(os.path.join(cwd, ".git"), exist_ok=True)
    for i, age in enumerate((1 * HOUR, 3 * HOUR)):
        write_session(CFG2, cwd, "%08d-bbbb-4bbb-8bbb-%012d" % (i + 1, i + 1), T - age, "claude-sonnet-5-5", 2, 100_000, 0)
    for i in range(30):
        write_session(CFG2, cwd, "%08d-cccc-4ccc-8ccc-%012d" % (i + 1, i + 1), T - (8 + 0.7 * i) * DAY, "claude-sonnet-5-5", 2, 125_000, 0)


def test_latency():
    build_many()
    st = Store(root=CFG2, days=7, clock=lambda: NOW[0])
    st.refresh(force=True)
    recent = round(2 * 2 * msg_cost("claude-sonnet-5-5", 100_000, 0), 4)            # 0.80
    older = round(30 * 2 * msg_cost("claude-sonnet-5-5", 125_000, 0), 4)            # 15.00
    check("latency fixture: the launch window holds the two recent sessions", len(st.sessions) == 2, len(st.sessions))
    orig = parser_mod.FileCache._consume
    lock = threading.Lock()
    firsts = []
    started = threading.Event()

    def slow(self, path, e, size):
        if e["offset"] == 0 and size > 0:          # the first read of a transcript: 50 ms
            with lock:
                firsts.append(path)
            started.set()
            time.sleep(0.05)
        return orig(self, path, e, size)

    res = {}

    def wide(name):
        t0 = time.perf_counter()
        res[name] = (st.overview_payload("", days=30), time.perf_counter() - t0, time.perf_counter())

    parser_mod.FileCache._consume = slow
    try:
        w1 = threading.Thread(target=wide, args=("w1",))
        w1.start()
        started.wait(5)
        timings = {}
        got_lock = st.lock.acquire(timeout=0.5)       # Store.lock must not be held while the older transcripts are read
        if got_lock:
            st.lock.release()
        for name, fn in (("overview days=7", lambda: st.overview_payload("", days=7)), ("sessions days=7", lambda: st.sessions_payload(days=7)),
                         ("routing days=7", lambda: st.routing_payload("", days=7)), ("live", st.live_payload), ("meta", st.meta),
                         ("graph days=7", lambda: graph.payload(st, "", True, days=7)), ("overview (launch)", lambda: st.overview_payload(""))):
            t0 = time.perf_counter()
            timings[name] = (fn(), time.perf_counter() - t0)
        alive = w1.is_alive()
        n_during = len(firsts)
        w2 = threading.Thread(target=wide, args=("w2",))
        w2.start()
        w1.join(60)
        w2.join(60)
    finally:
        parser_mod.FileCache._consume = orig
    p1, d1, e1 = res["w1"]
    p2, d2, e2 = res["w2"]
    print(f"latency: widening 7 -> 30 days over {len(firsts)} slow transcripts took {d1:.2f} s; launch-window reads meanwhile took "
          + ", ".join(f"{k} {v[1] * 1000:.0f} ms" for k, v in timings.items()) + f"; the second days=30 caller took {d2:.2f} s")
    check("latency: the patched parser makes the widening slow (>= 1 s) and the launch window's readers started while it ran", d1 >= 1.0 and alive and n_during < 30,
          f"{d1:.2f} s, alive={alive}, {n_during} read when the readers finished")
    check("latency: Store.lock is free while the older transcripts are being read", got_lock, "could not take Store.lock for 0.5 s during the widening")
    worst = max(v[1] for v in timings.values())
    check("latency: launch-window reads (overview, sessions, routing, graph, live, meta) answer in < 0.5 s while another window widens", worst < 0.5,
          {k: round(v[1], 3) for k, v in timings.items()})
    ov7 = timings["overview days=7"][0]
    sp7 = timings["sessions days=7"][0]
    check("latency: and they answer correctly for the launch window (2 sessions)",
          ov7["totals"]["sessions"] == 2 and close(ov7["totals"]["cost"], recent) and ov7["days"] == 7 and len(sp7["sessions"]) == 2
          and len(timings["live"][0]["sessions"]) <= 2 and timings["overview (launch)"][0]["totals"]["sessions"] == 2 and len(st.sessions) == 2,
          (ov7["totals"]["sessions"], ov7["totals"]["cost"], len(sp7["sessions"])))
    check("latency: the days=30 caller gets the full window (32 sessions)", p1["totals"]["sessions"] == 32 and close(p1["totals"]["cost"], recent + older) and p1["days"] == 30,
          (p1["totals"]["sessions"], p1["totals"]["cost"]))
    check("latency: a second concurrent days=30 caller waits and gets the same correct answer", p2["totals"]["sessions"] == 32 and close(p2["totals"]["cost"], recent + older)
          and p2["days"] == 30 and e2 >= e1 - 0.05, (p2["totals"]["sessions"], p2["totals"]["cost"], round(e2 - e1, 3)))
    check("latency: ...without a second widening: each older transcript was parsed once, one last_widen, and it did not run again after the first finished",
          len(firsts) == 30 and st.last_widen and st.last_widen["from"] == 7 and st.last_widen["to"] == 30 and e2 - e1 < 0.5,
          (len(firsts), st.last_widen, round(e2 - e1, 3), round(d2, 2)))
    lw = st.last_widen or {}
    check("latency: the merge under Store.lock was brief (lock_secs < 0.25) and the widening reports its own time and files",
          lw.get("lock_secs", 9) < 0.25 and lw.get("secs", 0) >= 1.0 and lw.get("parsed") == 30 and lw.get("bytes", 0) > 0 and len(st.sessions) == 2, lw)


# ── browser: the header's window picker ──────────────────────────────────────
BROWSER_JS = r"""
const { chromium } = require(process.env.PW);
const BASE = process.env.BASE;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const out = { timeouts: [] };

// What the page shows right now: the picker, the Factory floor's cost KPI and hubs, Overview's KPIs.
const SNAP = () => {
  const t = (s) => { const e = document.querySelector(s); return e ? e.textContent.trim() : null; };
  const main = document.querySelector('#main');
  const btn = document.querySelector('#win-btn');
  const pop = document.querySelector('#win-pop');
  return {
    hash: location.hash,
    label: t('#win-btn .picker-label'),
    expanded: btn ? btn.getAttribute('aria-expanded') : null,
    popHidden: pop ? pop.hidden : null,
    inScope: !!document.querySelector('#scope #win.picker.win #win-btn'),
    scopeTxt: t('#scope-txt'),
    focusId: document.activeElement ? document.activeElement.id : '',
    main: { cls: main.className, busy: main.getAttribute('aria-busy') },
    cost: t('.fx-kpi[data-k="cost"]'),
    hubs: [...document.querySelectorAll('.fx-node.k-project')].map((n) => { const nm = n.querySelector('.fx-hub-name'); const c = n.querySelector('.fx-hub-cost'); return [nm ? nm.getAttribute('title') : '', c ? c.textContent.trim() : '']; }),
    kpis: Object.fromEntries([...document.querySelectorAll('.kpis .kpi')].map((k) => [k.querySelector('.label').textContent.trim(), k.querySelector('.value').textContent.trim()])),
    sub: t('.page-head .sub'),
    stored: (() => { try { return localStorage.getItem('cc-dash-days'); } catch (e) { return 'throws'; } })(),
  };
};
const OPTS = () => [...document.querySelectorAll('#win-pop li.picker-opt')].map((li) => ({ days: li.dataset.days, role: li.getAttribute('role'), selected: li.getAttribute('aria-selected'), text: li.textContent.trim(), active: li.classList.contains('active') }));

async function until(page, name, fn, arg, timeout) {
  try { await page.waitForFunction(fn, arg, { timeout: timeout || 15000 }); return true; } catch (e) { out.timeouts.push(name); return false; }
}
const factoryReady = async (page, phrase) => {
  const ok = await until(page, 'factory ' + phrase, (ph) => {
    const k = document.querySelector('.fx-kpi[data-k="cost"]');
    const m = document.querySelector('#main');
    return !!k && k.textContent.includes(ph) && document.querySelectorAll('.fx-node.k-project').length > 0 && !m.classList.contains('stale');
  }, phrase);
  await sleep(900);      // a hub that left the floor fades out for a moment; let the floor settle before it is read
  return ok;
};
const overviewReady = (page, phrase) => until(page, 'overview ' + phrase, (ph) => {
  const s = document.querySelector('.page-head .sub');
  const m = document.querySelector('#main');
  return !!s && s.textContent.includes(ph) && !m.classList.contains('stale') && !!document.querySelector('.kpis .kpi');
}, phrase);
async function openPage(browser, throwing) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, colorScheme: 'light' });
  if (throwing) await ctx.addInitScript(() => {
    try { Object.defineProperty(window, 'localStorage', { configurable: true, get() { throw new DOMException('denied', 'SecurityError'); } }); } catch (e) { /* not definable */ }
  });
  const page = await ctx.newPage();
  const rec = { errors: [], reqs: [], prePair: [] };
  // plans.js polls once at load, possibly before the pairing answer has landed: that request carries no token and
  // answers 401. It belongs to the pairing flow, not to the window, so it is listed apart (prePair) and not counted.
  const noTok = new Set();
  page.on('pageerror', (e) => rec.errors.push('pageerror: ' + e));
  page.on('request', (r) => {
    const u = new URL(r.url());
    if (!u.pathname.startsWith('/api/')) return;
    rec.reqs.push(u.pathname + u.search);
    if (!r.headers()['x-cc-dash-token'] && !u.pathname.startsWith('/api/pair')) noTok.add(r.url());
  });
  page.on('response', (r) => {
    if (r.status() < 400) return;
    if (r.status() === 401 && noTok.has(r.url())) { rec.prePair.push(new URL(r.url()).pathname); return; }
    rec.errors.push('HTTP ' + r.status() + ' ' + r.request().method() + ' ' + new URL(r.url()).pathname + new URL(r.url()).search);
  });
  page.on('console', (m) => {
    if (m.type() !== 'error') return;
    const loc = m.location().url || '';
    if (/Failed to load resource/.test(m.text()) && noTok.has(loc)) return;
    rec.errors.push('console: ' + m.text() + ' @ ' + loc);
  });
  return { ctx, page, rec };
}
async function pairUp(page, code) {
  await page.goto(`${BASE}/pair#code=${code}`, { waitUntil: 'load' });
  const landed = await page.evaluate(() => location.hash);
  await page.waitForSelector('#win-btn', { timeout: 15000 }).catch(() => out.timeouts.push('win-btn'));
  await page.evaluate(() => { location.hash = '#/factory?all=1'; });
  return landed;
}

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME, headless: true });

  // 1. Mouse: launch window, open, pick Last day, Overview, pick Last 30 days (slowed, to see the dimmed state), Factory, reload.
  {
    const s = out.mouse = {};
    const { page, rec } = await openPage(browser, false);
    s.landed = await pairUp(page, process.env.CODE1);
    await factoryReady(page, 'in 7 days');
    s.launch = await page.evaluate(SNAP);
    await page.click('#win-btn');
    await page.waitForSelector('#win-pop:not([hidden])', { timeout: 5000 }).catch(() => out.timeouts.push('pop open'));
    s.open = await page.evaluate(SNAP);
    s.opts = await page.evaluate(OPTS);
    if (process.env.SHOTS) await page.screenshot({ path: process.env.SHOTS + '/window-picker-open.png' });
    await page.evaluate(() => {
      window.__w = { seen: false };
      const m = document.querySelector('#main');
      new MutationObserver((rs) => {
        for (const r of rs) {
          const was = (r.attributeName === 'class' && /\bstale\b/.test(r.oldValue || '')) || (r.attributeName === 'aria-busy' && r.oldValue === 'true');
          if (was || m.classList.contains('stale') || m.getAttribute('aria-busy') === 'true') window.__w.seen = true;
        }
      }).observe(m, { attributes: true, attributeFilter: ['class', 'aria-busy'], attributeOldValue: true });
    });
    const mark = rec.reqs.length;
    await page.click('#win-pop li.picker-opt[data-days="1"]');
    await factoryReady(page, 'in 1 day');
    await sleep(300);
    s.day1 = await page.evaluate(SNAP);
    s.day1Stale = await page.evaluate(() => window.__w.seen);
    s.day1Reqs = rec.reqs.slice(mark);
    const markO = rec.reqs.length;
    await page.evaluate(() => { location.hash = '#/overview'; });
    await overviewReady(page, 'last day');
    await sleep(300);
    s.ov1 = await page.evaluate(SNAP);
    s.ov1Reqs = rec.reqs.slice(markO);
    await page.route(/\/api\/.*days=30/, async (route) => { await sleep(700); await route.continue(); });
    const mark30 = rec.reqs.length;
    await page.click('#win-btn');
    await page.waitForSelector('#win-pop:not([hidden])', { timeout: 5000 }).catch(() => out.timeouts.push('pop open 2'));
    await page.click('#win-pop li.picker-opt[data-days="30"]');
    await sleep(250);
    s.mid30 = await page.evaluate(SNAP);
    await overviewReady(page, 'last 30 days');
    s.ov30 = await page.evaluate(SNAP);
    s.ov30Reqs = rec.reqs.slice(mark30);
    await page.unroute(/\/api\/.*days=30/);
    await page.evaluate(() => { location.hash = '#/factory?all=1'; });
    await factoryReady(page, 'in 30 days');
    s.f30 = await page.evaluate(SNAP);
    if (process.env.SHOTS) await page.screenshot({ path: process.env.SHOTS + '/window-factory-30.png' });
    const markR = rec.reqs.length;
    await page.reload({ waitUntil: 'load' });
    await until(page, 'reload label', () => { const l = document.querySelector('#win-btn .picker-label'); return !!l && l.textContent.trim() === 'Last 30 days'; });
    await factoryReady(page, 'in 30 days');
    s.reload = await page.evaluate(SNAP);
    s.reloadReqs = rec.reqs.slice(markR);
    await page.evaluate(() => { location.hash = '#/overview'; });
    await overviewReady(page, 'last 30 days');
    s.reloadOv = await page.evaluate(SNAP);
    s.errors = rec.errors;
    s.prePair = rec.prePair;
  }

  // 2. Keyboard, on a fresh profile (so the launch window is the remembered one).
  {
    const s = out.keys = {};
    const { page, rec } = await openPage(browser, false);
    await pairUp(page, process.env.CODE2);
    await factoryReady(page, 'in 7 days');
    s.start = await page.evaluate(SNAP);
    await page.focus('#win-btn');
    await page.keyboard.press('Enter');
    await sleep(250);
    s.opened = await page.evaluate(SNAP);
    s.openedOpts = await page.evaluate(OPTS);
    await page.keyboard.press('ArrowDown');
    await sleep(100);
    s.down = await page.evaluate(OPTS);
    await page.keyboard.press('Enter');
    await factoryReady(page, 'in 14 days');
    await sleep(200);
    s.picked = await page.evaluate(SNAP);
    await page.keyboard.press('Enter');
    await sleep(250);
    s.reopened = await page.evaluate(SNAP);
    await page.keyboard.press('Escape');
    await sleep(250);
    s.escaped = await page.evaluate(SNAP);
    s.errors = rec.errors;
    s.prePair = rec.prePair;
  }

  // 3. localStorage throws on every access.
  {
    const s = out.nostore = {};
    const { page, rec } = await openPage(browser, true);
    await pairUp(page, process.env.CODE3);
    await factoryReady(page, 'in 7 days');
    s.launch = await page.evaluate(SNAP);
    await page.click('#win-btn');
    await page.waitForSelector('#win-pop:not([hidden])', { timeout: 5000 }).catch(() => out.timeouts.push('pop open 3'));
    await page.click('#win-pop li.picker-opt[data-days="1"]');
    await factoryReady(page, 'in 1 day');
    s.day1 = await page.evaluate(SNAP);
    s.errors = rec.errors;
    s.prePair = rec.prePair;
  }
  await browser.close();
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def days_word(d):
    return "1 day" if d == 1 else "%g days" % d


def test_browser():
    if not (os.path.isdir(PLAYWRIGHT) and os.path.exists(CHROME) and shutil.which("node")):
        print("browser: skipped (no playwright-core, Chrome or node)")
        return
    st = Store(root=CFG, days=LAUNCH, clock=time.time)        # the page computes its timers from its own clock
    srv = serve(st)
    js = os.path.join(BASE, "browser_check.js")
    with open(js, "w") as fh:
        fh.write(BROWSER_JS)
    env = dict(os.environ, PW=PLAYWRIGHT, CHROME=CHROME, BASE="http://127.0.0.1:%d" % srv.port, SHOTS=SHOTS,
               CODE1=srv.auth.new_code(), CODE2=srv.auth.new_code(), CODE3=srv.auth.new_code())
    try:
        r = subprocess.run(["node", js], capture_output=True, text=True, timeout=240, env=env)
    finally:
        stop(srv)
    if r.returncode != 0:
        check("browser: page check ran", False, r.stderr[-800:])
        return
    out = json.loads(r.stdout.strip().splitlines()[-1])
    m, k, ns = out["mouse"], out["keys"], out["nostore"]
    check("browser: nothing waited for timed out", not out["timeouts"], out["timeouts"])
    pre = m["prePair"] + k["prePair"] + ns["prePair"]
    if pre:
        print(f"note: {len(pre)} request(s) made before the pairing token landed answered 401 ({', '.join(sorted(set(pre)))}); counted apart, not as errors")

    def floor(tag, snap, d):
        """The Factory floor shows the window d: the cost KPI, and each project hub's caption (its cost and the window)."""
        e = expected(d)
        hubs = {name: text for name, text in snap["hubs"]}
        check(f"{tag}: the Factory cost KPI reads {money(e['cost'])} in {days_word(d)}", ("%s in %s" % (money(e["cost"]), days_word(d))) in (snap["cost"] or ""),
              (snap["cost"], e["cost"]))
        want = {p: "%s · %s" % (money(e["by_project"][p]), days_word(d)) for p in e["projects"]}
        check(f"{tag}: every Factory hub caption reads its project's cost and '{days_word(d)}'", hubs == want, {"got": hubs, "want": want})

    def ov_ok(snap, d):
        e = expected(d)
        return (snap["kpis"].get("Sessions") == str(e["sessions"]) and snap["kpis"].get("Estimated cost") == money(e["cost"])
                and ("last %s" % ("day" if d == 1 else "%g days" % d)) in (snap["sub"] or ""))

    def asked(urls, path, d):
        return any(u.split("?")[0] == path and ("days=%g" % d) in u.split("?", 1)[-1].split("&") for u in urls)

    check("browser: pairing lands on #/factory", m["landed"] == "#/factory", m["landed"])
    ln = m["launch"]
    check("browser: the picker sits in the header scope and shows the launch window ('Last 7 days')",
          ln["inScope"] and ln["label"] == "Last 7 days" and ln["expanded"] == "false" and ln["popHidden"] is True and ln["scopeTxt"],
          {k_: ln[k_] for k_ in ("inScope", "label", "expanded", "popHidden", "scopeTxt")})
    floor("browser: launch window", ln, 7)
    op, opts = m["open"], m["opts"]
    check("browser: clicking the button opens the list (aria-expanded true) with options 1, 3, 7, 14, 30 as role=option, the launch window selected",
          op["expanded"] == "true" and op["popHidden"] is False and [o["days"] for o in opts] == ["1", "3", "7", "14", "30"]
          and all(o["role"] == "option" for o in opts) and [o["days"] for o in opts if o["selected"] == "true"] == ["7"]
          and [o["text"].split("default")[0].strip() for o in opts] == ["Last day", "Last 3 days", "Last 7 days", "Last 14 days", "Last 30 days"],
          (op["expanded"], op["popHidden"], opts))
    d1 = m["day1"]
    check("browser: picking 'Last day' changes the label, closes the list and remembers it in localStorage",
          d1["label"] == "Last day" and d1["expanded"] == "false" and d1["popHidden"] is True and d1["stored"] == "1", {k_: d1[k_] for k_ in ("label", "expanded", "popHidden", "stored")})
    floor("browser: after picking 'Last day'", d1, 1)
    check("browser: #main was dimmed (stale, aria-busy) while the new window loaded and is clear once it landed",
          m["day1Stale"] is True and "stale" not in d1["main"]["cls"].split() and d1["main"]["busy"] is None, (m["day1Stale"], d1["main"]))
    check("browser: the floor's requests carry days=1 after the pick", asked(m["day1Reqs"], "/api/graph", 1), m["day1Reqs"][:6])
    o1 = m["ov1"]
    check("browser: Overview shows the 1-day totals (sessions, cost, subtitle)", ov_ok(o1, 1), (o1["kpis"].get("Sessions"), o1["kpis"].get("Estimated cost"), o1["sub"]))
    check("browser: Overview asks for its overview and routing with days=1", asked(m["ov1Reqs"], "/api/overview", 1) and asked(m["ov1Reqs"], "/api/routing", 1), m["ov1Reqs"][:8])
    mid = m["mid30"]
    check("browser: while the 30-day answer is still on its way the label already says 'Last 30 days' and #main is stale + aria-busy",
          mid["label"] == "Last 30 days" and "stale" in mid["main"]["cls"].split() and mid["main"]["busy"] == "true", (mid["label"], mid["main"]))
    o30 = m["ov30"]
    check("browser: Overview shows the 30-day totals once they land, and #main is clear",
          ov_ok(o30, 30) and "stale" not in o30["main"]["cls"].split() and o30["main"]["busy"] is None,
          (o30["kpis"].get("Sessions"), o30["kpis"].get("Estimated cost"), o30["sub"], o30["main"]))
    check("browser: Overview's requests carry days=30", asked(m["ov30Reqs"], "/api/overview", 30), m["ov30Reqs"][:6])
    f30 = m["f30"]
    floor("browser: after picking 'Last 30 days' and returning to the floor", f30, 30)
    check("browser: 'Last 30 days' is remembered in localStorage (cc-dash-days)", f30["stored"] == "30", f30["stored"])
    rl = m["reload"]
    check("browser: after a reload the picker still says 'Last 30 days'", rl["label"] == "Last 30 days", rl["label"])
    floor("browser: after the reload", rl, 30)
    check("browser: after the reload the data requests ask for days=30", asked(m["reloadReqs"], "/api/graph", 30), m["reloadReqs"][:8])
    check("browser: Overview after the reload is the 30-day one too", ov_ok(m["reloadOv"], 30), (m["reloadOv"]["kpis"].get("Sessions"), m["reloadOv"]["sub"]))
    check("browser: no console error or page error across the mouse run", not m["errors"], m["errors"][:3])
    # keyboard
    check("browser: a fresh profile starts on the launch window", k["start"]["label"] == "Last 7 days" and k["start"]["stored"] is None, (k["start"]["label"], k["start"]["stored"]))
    check("browser: Enter on the focused button opens the list, with focus on the list and the current window active",
          k["opened"]["expanded"] == "true" and k["opened"]["popHidden"] is False and k["opened"]["focusId"] == "win-list"
          and [o["days"] for o in k["openedOpts"] if o["active"]] == ["7"],
          (k["opened"]["expanded"], k["opened"]["focusId"], [(o["days"], o["active"]) for o in k["openedOpts"]]))
    check("browser: ArrowDown moves to the next window (14)", [o["days"] for o in k["down"] if o["active"]] == ["14"], [(o["days"], o["active"]) for o in k["down"]])
    pk = k["picked"]
    check("browser: Enter picks it: label 'Last 14 days', list closed, focus back on the button, remembered",
          pk["label"] == "Last 14 days" and pk["expanded"] == "false" and pk["popHidden"] is True and pk["focusId"] == "win-btn" and pk["stored"] == "14",
          (pk["label"], pk["expanded"], pk["focusId"], pk["stored"]))
    floor("browser: after Enter picks 'Last 14 days'", pk, 14)
    ro, es = k["reopened"], k["escaped"]
    check("browser: Esc closes the list without changing the window or leaving the view",
          ro["expanded"] == "true" and es["expanded"] == "false" and es["popHidden"] is True and es["label"] == "Last 14 days" and es["focusId"] == "win-btn" and es["hash"] == pk["hash"],
          (ro["expanded"], es["expanded"], es["label"], es["focusId"], es["hash"], pk["hash"]))
    check("browser: no console error or page error across the keyboard run", not k["errors"], k["errors"][:3])
    # localStorage throws
    check("browser: with localStorage throwing the page loads on the launch window",
          ns["launch"]["label"] == "Last 7 days" and ns["launch"]["stored"] == "throws", (ns["launch"]["label"], ns["launch"]["stored"]))
    floor("browser: localStorage throwing, launch window", ns["launch"], 7)
    check("browser: ...and picking a window still works (it just is not remembered)", ns["day1"]["label"] == "Last day", ns["day1"]["label"])
    floor("browser: localStorage throwing, after picking 'Last day'", ns["day1"], 1)
    check("browser: ...with no console error or page error", not ns["errors"], ns["errors"][:3])


def main():
    try:
        for fn in (test_windows, test_two_windows_at_once, test_widening, test_validation, test_outside_window, test_auth, test_latency, test_browser):
            try:
                fn()
            except Exception:  # noqa: BLE001 - one broken part must not hide the others' results
                import traceback
                check(f"{fn.__name__} ran to the end", False, traceback.format_exc()[-700:])
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
