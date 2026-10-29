#!/usr/bin/env python3
"""Headless-Chrome checks for the cc-dash Factory floor (web/factory.js, graph.js, factory.css).

The page is served by the real cc_dash.server.Handler (subclassed) over an empty temp
CLAUDE_CONFIG_DIR; /graph.js, /factory.js and /factory.css are added to server.ASSETS at runtime
when server.py does not list them yet (setdefault, never an overwrite). GET /api/graph is stubbed
from tests/fixtures/graph-poll1.json and graph-poll2.json (the design doc's section 5.7 payloads),
auth-checked like every /api route, with timestamps shifted so the fixture's "now" is the wall
clock, ?project= and all=1 honoured. A test-only GET /__test/poll?n=... switches what the stub
returns without reloading the page:

  1       poll 1 (32 nodes)
  2       poll 2 (states changed in place, two agents added, one removed, the alert replaced)
  states  poll 1 with only states changed (same ids): a2 done, b3 no longer needs you, s6 generating
  big     a generated ~300-node payload
  empty   no nodes
  long    poll 1 plus two project hubs with 40-character names (one unbroken, one in underscored words)
  cycles  poll 1 plus two workflows that parent each other and one that parents itself
  robust  poll 1 plus an order with detail null, a duplicate node id and string costs
  garbage a 200 whose body is not JSON          objnodes  a payload whose nodes is an object
  rates   poll 1 with every session unpriced (burn in new tokens a minute only)
  noseries poll 1 without `series` (an older server)
Every variant but noseries carries `series` (the last-hour buckets); `empty` a zero one, `robust` one with junk in it.
  visuals poll 1 with the same ids: A91 failed, FO-002 and FO-003 sent back, S1 and E9 newer in their states

Control mode is stubbed the same way (GET /__test/ctl?mode=off|on|read&alerts=0|1): /api/meta,
/api/control/state (a flagged Bash permission for B3, a question for S1's main thread), a recording
POST /api/control/decide and /api/alerts (B3's toast); off by default, so every other step runs as before.

Static checks (no browser): index.html loads the three assets in the right order, Factory is the
first tab and the brand target, the harness serves the assets with the right content types.

Browser checks (needs node, playwright-core and Chrome, else "browser: skipped" and exit 0) walk the
view as a user would: the render and the HUD, the inspector for each node kind, polls that must
change the page in place (no element replaced, nothing moves, the camera stays), alerts that focus
their node by mouse and keyboard, keyboard navigation, both themes, reduced motion, a phone-width
viewport, the list equivalent, project and dormant scope, a 300-node payload and an empty one;
then the inspector re-framing an edge node with its parent and children clear of it (desktop,
phone, 300 nodes), no node under the zoom buttons after a fit, hub names inside their discs at
every level of detail (40-character names too), the activity panel and alerts holding still under
the pointer and focus (WCAG 2.2.2), and the contrast of every text run on glass measured against
a screenshot with the text hidden; then the review fixes: parent cycles, the focus outline's
screen width when zoomed out, a scope change refitting, List mode pausing the engine, answers
that are not a graph, refocus after the opened node left, the phone list's accessible names,
odd payloads (detail null, duplicate ids, string costs), and short viewports; then the activity column (recent actions and the three last-hour charts: rows from the feed, a row click handing the slot to the inspector, the toggle, the phone's bottom sheet, tooltips by pointer and keyboard, each header giving the hour and each svg's sentence the current bucket too, the split of a short slot (whole rows give way before the plots, which keep 40 px at 1366x768, and the column scrolls only as a last resort), glass in both themes, reduced motion, empty and odd payloads, no node under the column); then control mode:
Review beside B3's alert and in its inspector (never nested, never deciding), the review dialog
with the whole card (the full command, the bash-guard note, the typed "approve" that keeps Run it
disabled and POSTs nothing until typed), Escape closing a sub-form first and focus going back,
the page's shortcuts kept out of the dialog while control.js's y and n work, no Review without
control mode and the code field before the control code; toasts clear of the zoom buttons and an
open inspector at 1440x900 and 390x844 with nothing on the floor moving; burn rates that never
count cache reads; the new IA (Factory and Projects, #/live landing on the Factory); and the floor's
extra visuals: at far zoom a failed node's red ring, a stalled node's dashed amber one and the wider
needs-you halo (none of them up close), each node's time in its state and each order's rework count,
read from their fields, changed in place by a poll without a relayout, the time counting on between
polls without a repaint, and nothing of it moving under reduced motion. Every
browser step runs in its own context and is wrapped in try/catch, so one failing step never hides
the others.

Run:  python3 tests/test_cc_dash_factory_ui.py
Env:  CC_DASH_PLAYWRIGHT  playwright-core directory     CC_DASH_CHROME  Chrome binary
      CC_DASH_SHOTS       directory for screenshots: 1440x900 and 390x844, light and dark, each also with
                          the edge node opened, and in control mode with Review in the strip (and its
                          toast), the inspector with Review, and the review dialog's typed confirm; the
                          300-node floor and long hub names up close in both themes; the empty floor
      CC_DASH_FX_ONLY     comma list of browser steps to run (development)
"""
import copy
import http.client
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = tempfile.mkdtemp(prefix="cc-dash-factory-ui-test-")
os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(BASE, "claude")     # before cc_dash is imported: an empty config dir
os.makedirs(os.environ["CLAUDE_CONFIG_DIR"], exist_ok=True)
sys.path.insert(0, os.path.join(REPO, "lib"))
from cc_dash import server as server_mod  # noqa: E402
from cc_dash.auth import Auth  # noqa: E402
from cc_dash.store import Store  # noqa: E402

RESULTS = []
PLAYWRIGHT = os.environ.get("CC_DASH_PLAYWRIGHT", "/home/lukasz/Files/hobby/color-analyzer/webapp/node_modules/playwright-core")
CHROME = os.environ.get("CC_DASH_CHROME", "/usr/bin/google-chrome")
SHOTS = os.environ.get("CC_DASH_SHOTS", "")
ONLY = [x for x in os.environ.get("CC_DASH_FX_ONLY", "").split(",") if x]
WEB = server_mod.WEB
FIX = os.path.join(REPO, "tests", "fixtures")


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def note(msg):
    print(msg, flush=True)


# ── the stubbed /api/graph ───────────────────────────────────────────────────
NEW_ASSETS = {"/graph.js": ("graph.js", "text/javascript; charset=utf-8"),
              "/factory.js": ("factory.js", "text/javascript; charset=utf-8"),
              "/factory.css": ("factory.css", "text/css; charset=utf-8")}
for _path, _entry in NEW_ASSETS.items():
    server_mod.ASSETS.setdefault(_path, _entry)

TIME_KEYS = {"now", "since", "start", "t", "created", "updated"}
VARIANTS = ("1", "2", "states", "big", "empty", "long", "cycles", "robust", "garbage", "objnodes", "rates", "noseries", "visuals")
STATE = {"n": "1"}
# Control mode, stubbed (GET /__test/ctl?mode=off|on|read&alerts=0|1): /api/meta says control is enabled
# (scope 'control' for on, 'read' for read: the control code not typed yet), /api/control/state serves
# CTL_REQS, POST /api/control/decide is recorded in CTL["posts"], and /api/alerts serves the toast for
# B3's request while alerts=1. Off (the default for every step) leaves the real handler in charge.
CTL = {"mode": "off", "alerts": True, "posts": [], "status": {}}


def _load(n):
    with open(os.path.join(FIX, "graph-poll%s.json" % n)) as fh:
        return json.load(fh)


POLL1, POLL2 = _load(1), _load(2)
SID_A = "7d1c0a52-1111-4a8e-9c3b-2f6a0e9b1001"
A91 = "a:%s/a91f02d6c3b8e4a7" % SID_A      # "Write the graph.py payload builder": Edit lib/cc_dash/graph.py, $0.42
A2 = "a:%s/a2c7d8e90f1b3c45" % SID_A       # tool -> done in `states` and poll 2
B3 = "a:%s/b3c5a7e9f1d24f60" % SID_A       # holds the permission alert in poll 1; no longer needs you in `states`
S6 = "s:7d1c0a52-6666-4f21-8d0e-91c3b7a26006"   # tool -> generating
S1 = "s:" + SID_A
ORDER3 = "o:reports-app/FO-003"
PROJ = "p:cc_tool"
FAR = "p:valhalla-strategy"
EDGE = "s:c04f19d2-5555-4e6a-9b8c-7a3d5f10e005"   # valhalla-strategy's session: the floor's right edge, a parent and two children
LONG_NAMES = {"p:long-unbroken": "averyveryverylongprojectnamewithoutbreak", "p:long-words": "my_extremely_long_project_name_for_tests"}
CYCLES = {"w:cyc/a": "w:cyc/b", "w:cyc/b": "w:cyc/a", "w:cyc/self": "w:cyc/self"}   # id -> parent
ROBUST = {"order": "o:reports-app/FO-099", "junk": "a:%s/junk0cost" % SID_A, "str": "a:%s/str0cost" % SID_A}


def states_variant():
    """Poll 1 with only states changed: same node ids, same edges."""
    d = copy.deepcopy(POLL1)
    p2 = {n["id"]: n for n in POLL2["nodes"]}
    for i, n in enumerate(d["nodes"]):
        if n["id"] in (A2, B3, S6):
            d["nodes"][i] = copy.deepcopy(p2[n["id"]])
        elif n["id"] == PROJ:
            n["state"] = "working"
    d["alerts"] = []
    d["kpis"]["needs"] = 0
    d["sig"] = "states"
    return d


def big_variant():
    """~300 nodes: 6 projects x 5 sessions x 9 agents nested a little (306 nodes)."""
    base = copy.deepcopy(POLL1)
    proto_a = next(n for n in base["nodes"] if n["kind"] == "agent" and n["state"] == "tool")
    proto_s = next(n for n in base["nodes"] if n["kind"] == "session")
    proto_p = next(n for n in base["nodes"] if n["kind"] == "project")
    nodes, edges = [], []
    fams = ["opus", "sonnet", "haiku"]
    models = {"opus": "Opus 5.5", "sonnet": "Sonnet 5.5", "haiku": "Haiku 5.5"}
    states = ["tool", "generating", "waiting", "done", "tool", "generating", "idle", "stalled", "failed"]
    for pi in range(6):
        pid = "p:proj-%d" % pi
        nodes.append(dict(proto_p, id=pid, label="project-%d" % pi))
        for si in range(5):
            sid = "s:sess-%d-%d" % (pi, si)
            nodes.append(dict(proto_s, id=sid, parent=pid, label="Session %d.%d doing things" % (pi, si),
                              family=fams[si % 3], state=states[si % len(states)]))
            edges.append({"id": pid + ">" + sid, "source": pid, "target": sid, "kind": "member", "active": True, "rate": 9000})
            prev = sid
            for ai in range(9):
                aid = "a:sess-%d-%d/ag%d" % (pi, si, ai)
                par = sid if ai % 3 == 0 else prev
                st = states[(pi + si + ai) % len(states)]
                nodes.append(dict(proto_a, id=aid, parent=par, label="Agent %d.%d.%d task" % (pi, si, ai),
                                  family=fams[ai % 3], model=models[fams[ai % 3]], state=st,
                                  needs="permission" if (pi, si, ai) == (1, 2, 4) else None))
                edges.append({"id": par + ">" + aid, "source": par, "target": aid, "kind": "spawn",
                              "active": st in ("tool", "generating", "waiting"), "rate": 1000 * (ai + 1) * 3})
                if ai % 3 == 0:
                    prev = aid
    base.update(nodes=nodes, edges=edges, alerts=[], feed=[], sig="big")
    return base


def empty_variant():
    d = copy.deepcopy(POLL1)
    d["kpis"] = {k: ({} if isinstance(v, dict) else 0) for k, v in d["kpis"].items()}
    d.update(nodes=[], edges=[], alerts=[], feed=[], sig="empty")
    d["series"] = dict(d["series"], tokens=[0] * 20, tokens_by_family={}, tool_calls=[0] * 20, tool_errors=[0] * 20,
                       agents=[0] * 20, agents_total=0)
    return d


def noseries_variant():
    """Poll 1 as an older server sends it: no `series` at all."""
    d = copy.deepcopy(POLL1)
    del d["series"]
    d["sig"] = "noseries"
    return d


def long_variant():
    """Poll 1 plus two dormant-looking hubs whose names are far wider than a disc."""
    d = copy.deepcopy(POLL1)
    proto = next(n for n in d["nodes"] if n["id"] == FAR)
    for pid, label in LONG_NAMES.items():
        d["nodes"].append(dict(copy.deepcopy(proto), id=pid, label=label))
    d["sig"] = "long"
    return d


def cycles_variant():
    """Poll 1 plus a parent cycle of two workflows and a workflow that is its own parent (none has a session)."""
    d = copy.deepcopy(POLL1)
    proto = next(n for n in d["nodes"] if n["kind"] == "workflow")
    for wid, par in CYCLES.items():
        w = copy.deepcopy(proto)
        w.update(id=wid, parent=par, label="cycle " + wid.split("/")[-1])
        w["detail"] = {k: v for k, v in w["detail"].items() if k != "session"}
        d["nodes"].append(w)
    d["sig"] = "cycles"
    return d


def robust_variant():
    """Poll 1 plus an order whose detail is null, a second node with S6's id, and costs as strings."""
    d = copy.deepcopy(POLL1)
    order = copy.deepcopy(next(n for n in d["nodes"] if n["id"] == ORDER3))
    order.update(id=ROBUST["order"], label="An order with no detail", detail=None)
    dup = copy.deepcopy(next(n for n in d["nodes"] if n["id"] == S6))
    dup["label"] = "DUPLICATE of the release notes session"
    proto = next(n for n in d["nodes"] if n["id"] == A91)
    junk = dict(copy.deepcopy(proto), id=ROBUST["junk"], label="Cost is junk", cost="abc", tokens="many")
    num = dict(copy.deepcopy(proto), id=ROBUST["str"], label="Cost is a string", cost="12.5")
    d["nodes"] += [order, dup, junk, num]
    sr = d["series"]       # junk inside series (strings, a short array, a negative, null): nothing usable in any measure
    sr["tokens"] = ["abc", None, -5] + [0] * 17
    sr["tokens_by_family"] = {"opus": [0, 0, 0], "sonnet": None, "haiku": "x"}
    sr["tool_calls"] = [0] * 7
    sr["tool_errors"] = [None] * 20
    sr["agents"] = [-1, "a"] + [0] * 18
    sr["agents_total"] = "many"
    d["sig"] = "robust"
    return d


def objnodes_variant():
    d = copy.deepcopy(POLL1)
    d.update(nodes={}, sig="objnodes")
    return d


def rates_variant():
    """Poll 1 with every session unpriced (no dollars a minute, only new tokens a minute, and the HUD's
    burn_cost_min 0); agents keep their prices."""
    d = copy.deepcopy(POLL1)
    for n in d["nodes"]:
        if n["kind"] == "session":
            n["burn"] = dict(n["burn"], cost_min=0.0, unpriced_min=n["burn"]["new_min"])
    d["kpis"]["burn_cost_min"] = 0.0
    d["sig"] = "rates"
    return d


FAM_NAME = {"opus": "Opus", "sonnet": "Sonnet", "haiku": "Haiku", "fable": "Fable", "other": "Other"}
TIP_KS = (0, 3, 12, 18, 19)       # buckets whose tooltip is checked: the first, ordinary ones, the one before the last, the last


def app_compact(n):
    """app.js compact(): 1.2K, 36K, 1.3M."""
    a = abs(n)
    if a < 1000:
        return str(round(n))
    div, unit, nd = (1e3, "K", 1 if a < 1e4 else 0) if a < 1e6 else (1e6, "M", 1 if a < 1e7 else 0)
    t = "%.*f" % (nd, n / div)
    return (t[:-2] if t.endswith(".0") else t) + unit


def forms(n):
    """The ways the column may print a count: C.int (thousands separators) or C.compact (+ K / M)."""
    c = app_compact(n)
    return [format(n, ","), c]


def tip_rows(sr, kind, i):
    """The rows the contract gives bucket i's tooltip: [name, value] pairs, a Total last when more than one family."""
    if kind == "tokens":
        rows = [[FAM_NAME[f], format(v[i], ",")] for f, v in sr["tokens_by_family"].items() if v[i] > 0]
        if len(rows) > 1:
            rows.append(["Total", format(sr["tokens"][i], ",")])
        return rows or [["Nothing recorded", ""]]
    if kind == "tools":
        rows = [["Tool calls", format(sr["tool_calls"][i], ",")]]
        if sr["tool_errors"][i] > 0:
            rows.append(["Failed", format(sr["tool_errors"][i], ",")])
        return rows
    return [["Working agents", format(sr["agents"][i], ",")]]


def series_expect():
    sr = POLL1["series"]
    return {"ks": list(TIP_KS),
            "tips": {kind + str(i): tip_rows(sr, kind, i) for kind in ("tokens", "tools", "agents") for i in TIP_KS},
            "now": {"tokens": forms(sr["tokens"][-1]), "tools": forms(sr["tool_calls"][-1]), "agents": forms(sr["agents"][-1])},
            "sum": {"tokens": forms(sum(sr["tokens"])), "tools": forms(sum(sr["tool_calls"])), "agentsPeak": str(max(sr["agents"])), "agentsTotal": str(sr["agents_total"])}}


FAILED = "a:c04f19d2-5555-4e6a-9b8c-7a3d5f10e005/a7b6c5d4e3f20011"    # failed in poll 1
STALLED = "a:c04f19d2-5555-4e6a-9b8c-7a3d5f10e005/a8c7d6e5f4a30022"   # stalled in poll 1
E9 = "a:3b9e47f0-3333-4c2d-b6a1-5e08d2f43003/e9c8b7a6f5e4d3c2"        # running Bash for 3½ minutes in poll 1
VIS_SINCE = {S1: 125, E9: 30}                                         # `visuals`: seconds in the current state


def visuals_variant():
    """Poll 1, same ids and edges, with what the floor's extra visuals read moved on: A91 failed (the far
    ring), FO-002 sent back twice and FO-003 once (rework), S1 two minutes and E9 half a minute into
    their states (time in state)."""
    d = copy.deepcopy(POLL1)
    for n in d["nodes"]:
        if n["id"] == A91:
            n.update(state="failed", tool=None, target="", burn=dict(n["burn"], cost_min=0, tok_min=0, new_min=0, out_min=0))
        elif n["id"] in VIS_SINCE:
            n["since"] = d["now"] - VIS_SINCE[n["id"]]
        elif n["id"] == "o:reports-app/FO-002":
            n["detail"]["rework"] = 2
        elif n["id"] == ORDER3:
            n["detail"]["rework"] = 1
    for e in d["edges"]:
        if e["target"] == A91:
            e.update(active=False, rate=0)
    d["sig"] = "visuals"
    return d


def compact(n):
    """app.js compact() below a million."""
    a = abs(n)
    if a < 1000:
        return str(round(n))
    t = "%.*f" % (1 if a < 1e4 else 0, n / 1e3)
    return (t[:-2] if t.endswith(".0") else t) + "K"


# ── control mode: the requests control.js shows (B3's flagged Bash permission, S1's question) ──
B3_AGENT = B3.split("/")[-1]
CTL_CMD = ("rm -rf build/stage && mkdir -p build/stage && " + " && ".join(
    "google-chrome --headless=new --screenshot=build/stage/shot-%02d.png --window-size=1440,900 http://127.0.0.1:7878/#/factory" % i for i in range(1, 9))
    + " && echo CTL-TAIL-MARKER")
CTL_GUARD = "NEEDS APPROVAL: rm -rf deletes a directory tree (build/stage)"
REQ_B3 = "req-b3-perm-0001"
REQ_Q = "req-s1-question-0002"


def ctl_requests(now):
    reqs = [
        {"id": REQ_B3, "ts": now - 14, "session": SID_A, "agent_id": B3_AGENT, "agent_type": "general-purpose", "cwd": "~/Files/hobby/cc_tool",
         "tool": "Bash", "kind": "permission", "sig": "Bash rm -rf build/stage", "hash": "hash-b3", "mode": "default",
         "input": {"command": CTL_CMD, "description": "Take the stage screenshots"}, "guard": CTL_GUARD, "status": "open"},
        {"id": REQ_Q, "ts": now - 30, "session": SID_A, "agent_id": None, "agent_type": None, "cwd": "~/Files/hobby/cc_tool",
         "tool": "AskUserQuestion", "kind": "question", "sig": "AskUserQuestion", "hash": "hash-q", "mode": "default",
         "input": {"questions": [{"question": "Ship the Factory floor as the landing view?", "header": "Landing", "multiSelect": False,
                                  "options": [{"label": "Yes"}, {"label": "Not yet"}]}]}, "status": "open"},
    ]
    for r in reqs:
        st = CTL["status"].get(r["id"])
        if st:
            r.update(st)
    return reqs


def ctl_alerts(now):
    """/api/alerts: the toast for B3's request (its text is attention.jsonl's, unlike the card's)."""
    if not CTL["alerts"]:
        return []
    return [{"id": "h-ctl-b3", "session": SID_A, "agent_id": B3_AGENT, "project": "cc_tool", "cwd": "~/Files/hobby/cc_tool",
             "kind": "permission", "title": "Claude needs your permission to use Bash", "message": "npm test", "tool": "Bash",
             "since": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now - 14)) + "Z", "source": "hook", "since_ts": now - 14, "age": 14.0,
             "agent_type": "general-purpose", "agent_label": "Factory stage visual pass", "session_title": "Build the software factory experiment",
             "worktree": "", "notified": False, "request": REQ_B3}]


def variant(n):
    return {"1": lambda: copy.deepcopy(POLL1), "2": lambda: copy.deepcopy(POLL2), "states": states_variant,
            "big": big_variant, "empty": empty_variant, "long": long_variant, "cycles": cycles_variant,
            "robust": robust_variant, "objnodes": objnodes_variant, "rates": rates_variant, "noseries": noseries_variant, "visuals": visuals_variant}[n]()


def shift(o, delta):
    if isinstance(o, dict):
        return {k: (v + delta if k in TIME_KEYS and isinstance(v, (int, float)) and v > 1e9 else shift(v, delta)) for k, v in o.items()}
    if isinstance(o, list):
        return [shift(x, delta) for x in o]
    return o


def scoped(d, project="", all_=False):
    """What the real route does with ?project= (that project's subtree) and all=1 (dormant hubs)."""
    if project:
        byid = {n["id"]: n for n in d["nodes"]}
        keep = set()
        for n in d["nodes"]:
            root = n
            while root.get("parent"):
                root = byid[root["parent"]]
            if root["label"] == project:
                keep.add(n["id"])
        d["nodes"] = [n for n in d["nodes"] if n["id"] in keep]
        d["edges"] = [e for e in d["edges"] if e["source"] in keep and e["target"] in keep]
        d["alerts"] = [a for a in d["alerts"] if a["node"] in keep]
        d["feed"] = [f for f in d["feed"] if f.get("node") in keep]
        d["scope"] = {"project": project}
    elif all_:
        d["nodes"].append({"id": "p:dotfiles", "kind": "project", "label": "dotfiles", "parent": None, "state": "idle",
                           "needs": None, "cost": 0.84, "tokens": 210000,
                           "detail": {"root": "~/dotfiles", "sessions_live": 0, "sessions_window": 2, "cost_window": 0.84,
                                      "tokens_window": 210000,
                                      "model_mix": [{"family": "sonnet", "model": "Sonnet 5.5", "tokens": 210000, "cost": 0.84}],
                                      "guard": {"deny": 0, "ask": 0, "warn": 0}, "errors": 0, "plans": [], "orders": {}}})
    return d


class FxHandler(server_mod.Handler):
    def do_GET(self):  # noqa: N802
        parts = urlsplit(self.path)
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        if (parts.path.startswith("/__test/") or parts.path == "/api/graph") and not self._host_ok():
            return self._deny(421, "no")
        if parts.path == "/__test/code":                  # a fresh one-time pairing code for the next browser context
            return self._json(200, {"code": self.server.auth.new_code()})
        if parts.path == "/__test/poll":
            n = q.get("n", "1")
            if n not in VARIANTS:
                return self._json(400, {"error": "unknown variant " + n})
            STATE["n"] = n
            return self._json(200, {"n": n})
        if parts.path == "/__test/ctl":                   # control mode on / off / read-only, and whether the toast's alert is open
            if q.get("mode") in ("off", "on", "read"):
                CTL.update(mode=q["mode"], posts=[], status={})
            if q.get("alerts") in ("0", "1"):
                CTL["alerts"] = q["alerts"] == "1"
            return self._json(200, {"mode": CTL["mode"], "alerts": CTL["alerts"], "posts": CTL["posts"]})
        if CTL["mode"] != "off" and parts.path in ("/api/meta", "/api/control/state", "/api/alerts"):
            if self.server.auth.session(self.headers) is None:
                return self._json(401, {"error": "Not paired.", "repair": True})
            now = time.time()
            if parts.path == "/api/meta":
                meta = self.server.store.meta()
                meta["control"] = {"enabled": True, "paired": True, "scope": "control" if CTL["mode"] == "on" else "read", "locked": False}
                return self._json(200, meta)
            if parts.path == "/api/control/state":
                return self._json(200, {"now": now, "requests": ctl_requests(now), "messages": [], "audit": [], "stops": []})
            return self._json(200, {"now": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + "Z", "alerts": ctl_alerts(now)})
        if parts.path == "/api/graph":
            if self.server.auth.session(self.headers) is None:
                return self._json(401, {"error": "Not paired.", "repair": True})
            if STATE["n"] == "garbage":                  # a 200 that is not JSON (a proxy's error page, say)
                body = b"<html><body>Bad gateway, but with a 200</body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return None
            d = variant(STATE["n"])
            d = shift(d, time.time() - d["now"])
            d = scoped(d, q.get("project", ""), q.get("all") == "1")
            return self._json(200, d)
        return super().do_GET()

    def do_POST(self):  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/api/control/decide" and CTL["mode"] != "off":
            if not self._host_ok():
                return self._deny(421, "no")
            if self.server.auth.session(self.headers) is None:
                return self._json(401, {"error": "Not paired.", "repair": True})
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._json(400, {"error": "not JSON"})
            CTL["posts"].append(body)
            if CTL["mode"] != "on":
                return self._json(403, {"error": "Enter the control code from your terminal to act.", "unlock": True})
            st = {"status": "sent", "decision": {"action": body.get("action")}, "resolved_at": time.time()}
            CTL["status"][body.get("id")] = st
            return self._json(200, {"request": dict(st, id=body.get("id"))})
        return super().do_POST()


def start_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FxHandler)
    srv.daemon_threads = True
    srv.store, srv.verbose = Store(days=1, clock=time.time), False
    srv.port = srv.server_address[1]
    srv.allowed_hosts = {"127.0.0.1:%d" % srv.port, "localhost:%d" % srv.port}
    srv.auth, srv.control = Auth(srv.port), None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def http_get(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    c.request("GET", path)
    r = c.getresponse()
    body = r.read()
    out = (r.status, r.getheader("Content-Type") or "", body)
    c.close()
    return out


# ── static checks ────────────────────────────────────────────────────────────
def test_static(srv):
    with open(os.path.join(WEB, "index.html"), encoding="utf-8") as fh:
        html = fh.read()
    check("static: index.html loads /factory.css", re.search(r'<link[^>]+rel="stylesheet"[^>]+href="/factory\.css"', html))
    pos = {n: html.find('<script src="/%s"' % n) for n in ("graph.js", "factory.js", "app.js")}
    check("static: index.html loads /graph.js and /factory.js", pos["graph.js"] >= 0 and pos["factory.js"] >= 0, pos)
    check("static: graph.js, then factory.js, load before app.js (app.js calls ccFactory.init)",
          0 <= pos["graph.js"] < pos["factory.js"] < pos["app.js"], pos)
    nav = re.search(r'<nav class="tabs"[^>]*>(.*?)</nav>', html, re.S)
    first = re.search(r"<a\b[^>]*>.*?</a>", nav.group(1), re.S) if nav else None
    check("static: Factory is the first nav tab (href #/factory)",
          first and 'href="#/factory"' in first.group(0) and "Factory" in re.sub(r"<[^>]+>", "", first.group(0)), first.group(0) if first else None)
    brand = re.search(r'<a\b[^>]*class="brand"[^>]*>', html)
    check("static: the brand link points at #/factory", brand and 'href="#/factory"' in brand.group(0), brand.group(0) if brand else None)
    for path, want in (("/graph.js", ("text/javascript", "application/javascript")), ("/factory.js", ("text/javascript", "application/javascript")),
                       ("/factory.css", ("text/css",))):
        status, ctype, body = http_get(srv.port, path)
        check("static: %s is served 200 with %s" % (path, want[0]), status == 200 and ctype.split(";")[0].strip() in want and len(body) > 200,
              (status, ctype, len(body)))


# ── the browser part ─────────────────────────────────────────────────────────
BROWSER_JS = r"""
'use strict';
const { chromium } = require(process.env.PW);
const fs = require('fs');
const BASE = process.env.BASE;
const X = JSON.parse(fs.readFileSync(process.env.EXPECT, 'utf8'));
const SHOTS = process.env.SHOTS || '';
const ONLY = process.env.ONLY ? process.env.ONLY.split(',') : null;
const out = {};
let browser;

// Helpers injected into every page (window.__t): small, read-only probes.
const HELPERS = `(() => {
  const q = (id) => document.querySelector('.gx-node[data-id=' + JSON.stringify(id) + ']');
  const vis = (el) => {
    if (!el || el.hidden) return false;
    const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.display !== 'none' && cs.visibility !== 'hidden';
  };
  const g = () => (window.ccFactory && window.ccFactory.graph && window.ccFactory.graph()) || null;
  const ids = () => [...document.querySelectorAll('.gx-node[data-id]')].map((e) => e.dataset.id);
  const clone = (o) => JSON.parse(JSON.stringify(o));
  const insp = () => {
    const a = document.querySelector('aside.fx-inspector');
    if (!a) return { exists: false, visible: false };
    const t = a.querySelector('.fx-insp-title'), b = a.querySelector('.fx-insp-body') || a, r = a.getBoundingClientRect();
    return { exists: true, visible: vis(a), hiddenAttr: a.hidden, title: t ? t.textContent.trim() : '', label: a.getAttribute('aria-label') || '',
      text: b.textContent.replace(/\\s+/g, ' ').trim(), hrefs: [...a.querySelectorAll('a[href]')].map((x) => x.getAttribute('href')),
      secs: [...a.querySelectorAll('.fx-sec[data-sec]')].map((x) => x.dataset.sec),
      rect: { l: r.left, t: r.top, r: r.right, b: r.bottom, w: r.width, h: r.height } };
  };
  const centres = () => {
    const o = {};
    for (const el of document.querySelectorAll('.gx-node[data-id]')) { const r = el.getBoundingClientRect(); o[el.dataset.id] = { x: r.left + r.width / 2, y: r.top + r.height / 2 }; }
    return o;
  };
  const face = (id) => {
    const n = q(id), f = n && n.querySelector('.fx-node');
    return f ? { cls: String(f.className), text: f.textContent.replace(/\\s+/g, ' ').trim().slice(0, 160), halo: !!f.querySelector('.fx-halo'), needs: f.classList.contains('needs') } : null;
  };
  const hud = () => {
    const o = {};
    document.querySelectorAll('.fx-hud .fx-kpi[data-k]').forEach((k) => {
      const v = k.querySelector('.fx-kpi-v'), l = k.querySelector('.fx-kpi-l');
      o[k.dataset.k] = { v: v ? v.textContent.trim() : null, l: l ? l.textContent.trim() : null, bs: v ? [...v.querySelectorAll('b')].map((b) => b.textContent.trim()) : [] };
    });
    return o;
  };
  const wait = (ms) => new Promise((ok) => setTimeout(ok, ms));
  // Resolves to the elapsed ms when fn() turns truthy, or -1 after ms.
  const until = async (fn, ms) => {
    const t0 = performance.now();
    while (performance.now() - t0 < ms) { try { if (fn()) return performance.now() - t0; } catch (e) { /* not yet */ } await wait(30); }
    return -1;
  };
  // Control mode: the strip item of a node (its alert button and the Review beside it) and the review dialog.
  const txt = (n) => (n ? n.textContent.split(/[ \\n\\t]+/).filter(Boolean).join(' ') : '');
  const strip = (node) => {
    const b = [...document.querySelectorAll('.fx-alert')].find((x) => x.dataset.node === node), w = b && b.closest('.fx-alert-w');
    const rv = w ? w.querySelector(':scope > .ctl-inline [data-ctl-act]') : null;
    return { has: !!b, aria: b ? b.getAttribute('aria-label') : null, title: b ? b.title : null, nested: b ? !!b.querySelector('button') : null,
      rv: rv ? { label: txt(rv), req: rv.dataset.ctlReq, href: rv.dataset.ctlHref, act: rv.dataset.ctlAct, visible: vis(rv), inAlert: !!rv.closest('.fx-alert') } : null };
  };
  const rect = (el) => { const b = el.getBoundingClientRect(); return { l: b.left, t: b.top, r: b.right, b: b.bottom }; };
  const dlg = () => {
    const d = document.querySelector('dialog.ctl-review');
    if (!d) return { exists: false, open: false };
    const card = d.querySelector('.ctl-card[data-req]'), conf = d.querySelector('.ctl-confirm'), typed = conf && conf.querySelector('input');
    const run = conf ? [...conf.querySelectorAll('button')].find((b) => txt(b) === 'Run it') : null;
    const a = document.activeElement, link = d.querySelector('.ctl-rv-open'), arm = d.querySelector('.ctl-arm'), lock = d.querySelector('.ctl-lock');
    return { exists: true, open: d.open, visible: vis(d), rect: d.open ? rect(d) : null, what: txt(d.querySelector('.ctl-rv-what')), who: txt(d.querySelector('.ctl-rv-who')),
      link: link && !link.hidden ? link.getAttribute('href') : null, linkText: txt(link), req: card ? card.dataset.req : null,
      compact: card ? card.classList.contains('compact') : null, box: (d.querySelector('.ctl-box') || {}).textContent || '',
      guard: [...d.querySelectorAll('.ctl-guard')].map(txt), buttons: card ? [...card.querySelectorAll('button')].filter(vis).map(txt) : [],
      confirm: conf ? vis(conf) : null, placeholder: typed ? typed.placeholder : null, typedFocused: !!typed && a === typed, typedValue: typed ? typed.value : null,
      run: run ? { disabled: run.disabled, visible: vis(run) } : null, cardFocused: !!card && a === card,
      status: txt(d.querySelector('.ctl-status')), arm: arm && !arm.hidden ? txt(arm) : '', lock: lock ? vis(lock) : null,
      active: a ? { tag: a.tagName, cls: String(a.className).slice(0, 60), id: a.id || '', req: (a.dataset && a.dataset.ctlReq) || '', inStrip: !!(a.closest && a.closest('.fx-alert-w')),
        inInsp: !!(a.closest && a.closest('aside.fx-inspector')) } : null };
  };
  // The toasts a user can see, the zoom buttons and the open inspector, as screen rects.
  const glass = () => {
    const z = document.querySelector('.fx-zoom'), i = document.querySelector('aside.fx-inspector'), root = document.documentElement;
    return { toasts: [...document.querySelectorAll('.al-stack .al-toast')].filter(vis).map(rect), allToasts: document.querySelectorAll('.al-stack .al-toast').length,
      zoom: vis(z) ? rect(z) : null, insp: insp().visible ? rect(i) : null, fx: root.classList.contains('al-fx'), away: root.classList.contains('al-fx-away'),
      dx: root.style.getPropertyValue('--al-dx'), dy: root.style.getPropertyValue('--al-dy'), cam: g() ? clone(g().camera()) : null,
      vw: root.clientWidth, vh: root.clientHeight };
  };
  // The activity column (right-hand slot), its toggle, the stage's right inset, the feed rows and the open tooltip.
  const fxRight = () => { const s = document.querySelector('.fx-stage'); return s ? (parseFloat(s.style.getPropertyValue('--fx-right')) || 0) : null; };
  const side = () => {
    const a = document.getElementById('fx-side'), b = document.querySelector('.fx-side-btn'), st = document.querySelector('.fx-stage');
    return { exists: !!a, visible: vis(a), hiddenAttr: a ? a.hidden : null, rect: vis(a) ? rect(a) : null, stage: st ? rect(st) : null,
      pressed: b ? b.getAttribute('aria-pressed') : null, btnVisible: vis(b), controls: b ? b.getAttribute('aria-controls') : null, fxRight: fxRight() };
  };
  const rows = () => [...document.querySelectorAll('.fx-ev-w')].map((w) => {
    const b = w.querySelector('.fx-ev'), st = w.querySelector('.fx-ev-st');
    return { key: w.dataset.key, node: b ? b.dataset.node : null, status: b ? b.dataset.status : null, st: txt(st), who: txt(w.querySelector('.fx-ev-who')), text: txt(b) };
  });
  const tipInfo = () => {
    const t = document.getElementById('tip');
    if (!t || t.hidden) return null;
    const tt = t.querySelector('.tt');
    return { title: tt ? tt.textContent : null, text: txt(t), rows: [...t.querySelectorAll('.tr')].map((r) => ({ name: r.children[1] ? r.children[1].textContent : '', value: r.querySelector('b') ? r.querySelector('b').textContent : '', total: r.classList.contains('total') })) };
  };
  // Local clock of a bucket's edges, as the tooltip title shows them (C.clock(ts, false), joined by an EN DASH).
  const pad2 = (n) => String(n).padStart(2, '0');
  const clk = (ts) => { const d = new Date(ts * 1000); return pad2(d.getHours()) + ':' + pad2(d.getMinutes()); };
  const titles = (i) => (window.__starts || []).map((st) => clk(st + 180 * i) + '\u2013' + clk(st + 180 * (i + 1)) + (i === 19 ? ' (so far)' : ''));
  // Every series.start the page was served (it shifts with the wall clock on each poll).
  window.__starts = [];
  const f0 = window.fetch;
  window.fetch = function (u) {
    const p = f0.apply(this, arguments);
    try { if (String(u && u.url ? u.url : u).indexOf('/api/graph') >= 0) p.then((r) => r.clone().json()).then((j) => { if (j && j.series && j.series.start) window.__starts.push(j.series.start); }).catch(() => {}); } catch (e) { /* ignore */ }
    return p;
  };
  const charts = () => [...document.querySelectorAll('.fx-chart')].map((c) => {
    const svg = c.querySelector('svg'), now = c.querySelector('.fx-chart-now'), sum = c.querySelector('.fx-chart-sum');
    const marks = svg ? [...svg.querySelectorAll('g.fx-col *')].filter((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0.5 && getComputedStyle(e).fill !== 'none'; }).length : -1;
    return { k: c.dataset.k, label: txt(c.querySelector('.fx-chart-l')), now: txt(now), hasNow: !!now, head: txt(c.querySelector('.fx-chart-h')), sum: txt(sum), svg: !!svg, cols: svg ? svg.querySelectorAll('g.fx-col').length : -1, hits: svg ? svg.querySelectorAll('rect.hit').length : -1,
      colIdx: svg ? [...svg.querySelectorAll('g.fx-col')].map((e) => e.dataset.i).join() : '', hitIdx: svg ? [...svg.querySelectorAll('rect.hit')].map((e) => e.dataset.i).join() : '',
      role: svg ? svg.getAttribute('role') : null, tab: svg ? svg.getAttribute('tabindex') : null, aria: svg ? svg.getAttribute('aria-label') : null, marks };
  });
  window.__t = { q, vis, g, ids, clone, insp, centres, face, hud, wait, until, strip, dlg, glass, txt, rect, side, rows, tipInfo, titles, charts, fxRight };
})();`;

async function getJSON(p) { return (await fetch(BASE + p)).json(); }
const setPoll = (n) => getJSON('/__test/poll?n=' + n);
const setCtl = (mode, alerts) => getJSON('/__test/ctl?' + (mode ? 'mode=' + mode + '&' : '') + (alerts == null ? '' : 'alerts=' + (alerts ? '1' : '0')));
const ctlPosts = async () => (await getJSON('/__test/ctl')).posts;
const first = (e) => String((e && e.message) || e).split('\n')[0].slice(0, 300);

// One step = one try/catch; whatever it collected before failing is kept.
async function step(name, fn) {
  if (ONLY && !ONLY.includes(name)) return;
  const r = {};
  const t0 = Date.now();
  try { await fn(r); } catch (e) { r.error = first(e); }
  r.ms = Date.now() - t0;
  out[name] = r;
  process.stderr.write('step ' + name + ': ' + (r.error ? 'ERROR ' + r.error : 'ok') + ' (' + r.ms + ' ms)\n');
}

// A fresh paired browser context + page. Page errors and console errors are collected into r.
async function withPage(opts, r, fn) {
  const o = Object.assign({ w: 1440, h: 900, scheme: 'light', reduced: false, poll: '1', hash: '#/factory', url: null, ctl: 'off', alerts: true }, opts);
  await setPoll(o.poll);
  await setCtl(o.ctl, o.alerts);                 // control mode off unless the step asks (a fresh decide log either way)
  const ctx = await browser.newContext({ viewport: { width: o.w, height: o.h }, colorScheme: o.scheme,
    reducedMotion: o.reduced ? 'reduce' : 'no-preference', deviceScaleFactor: 1, locale: 'en-US' });
  await ctx.addInitScript(HELPERS);
  const page = await ctx.newPage();
  page.setDefaultTimeout(8000);
  const rec = { errs: [], cons: [], graphReqs: [], bad: [] };
  page.on('pageerror', (e) => rec.errs.push(first(e)));
  page.on('console', (m) => {
    if (m.type() !== 'error') return;
    const loc = m.location() || {};
    if (/\/api\/pair-local$/.test(loc.url || '')) return;      // the pairing shell, not the Factory
    // plans.js starts polling at load, before auth.js has traded the pairing code: one 401 on /api/plans (races the pairing; not the Factory)
    if (/status of 401/.test(m.text()) && /\/api\/plans$/.test(loc.url || '')) return;
    rec.cons.push(m.text().slice(0, 300) + (loc.url ? ' @ ' + loc.url : ''));
  });
  page.on('request', (rq) => { try { const u = new URL(rq.url()); if (u.pathname === '/api/graph') rec.graphReqs.push(u.search); } catch (e) { /* ignore */ } });
  const t0 = Date.now();
  page.on('response', (rs) => {
    if (rs.status() < 400) return;
    const rq = rs.request();
    rec.bad.push(rs.status() + ' ' + new URL(rs.url()).pathname + ' token=' + (rq.headers()['x-cc-dash-token'] ? 'yes' : 'no') + ' at ' + (Date.now() - t0) + ' ms');
  });
  try {
    const { code } = await getJSON('/__test/code');
    await page.goto(BASE + '/pair#code=' + code, { waitUntil: 'load' });
    await page.waitForFunction(() => window.ccAuth && window.ccAuth.token(), null, { timeout: 8000 });
    if (o.url) await page.goto(BASE + o.url, { waitUntil: 'load' });
    else await page.evaluate((h) => { location.hash = h; }, o.hash);
    await fn(page, rec);
  } finally {
    r.errors = (r.errors || []).concat(rec.errs);
    r.consoleErrors = (r.consoleErrors || []).concat(rec.cons);
    r.badResponses = (r.badResponses || []).concat(rec.bad);
    await ctx.close().catch(() => {});
  }
}

async function waitFx(page, n, ms) {
  ms = ms || 9000;
  try { await page.waitForSelector('section.fx .fx-stage', { state: 'attached', timeout: ms }); }
  catch (e) { throw new Error('section.fx .fx-stage never appeared (hash ' + (await page.evaluate(() => location.hash)) + ')'); }
  if (n) {
    try { await page.waitForFunction((n) => document.querySelectorAll('.gx-node[data-id]').length >= n, n, { timeout: ms }); }
    catch (e) { throw new Error('expected >= ' + n + ' .gx-node[data-id], have ' + (await page.evaluate(() => document.querySelectorAll('.gx-node[data-id]').length))); }
  }
}
const settle = (page, ms) => page.evaluate((ms) => __t.until(() => __t.g() && __t.g().stats().settled, ms), ms || 4000);
const waitInsp = (page, ms) => page.evaluate((ms) => __t.until(() => __t.insp().visible, ms), ms || 3000);

// Centre a node in the free area, then click its centre with the real mouse.
async function clickNode(page, id) {
  const has = await page.evaluate((id) => { const el = __t.q(id); if (!el) return false; try { __t.g().center(id, { animate: false }); } catch (e) { /* no engine */ } return true; }, id);
  if (!has) throw new Error('no .gx-node for ' + id);
  await page.waitForTimeout(150);
  const p = await page.evaluate((id) => {
    const el = __t.q(id), r = el.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2, hit = document.elementFromPoint(x, y);
    return { x, y, covered: !(hit && hit.closest('.gx-node') === el), top: hit ? hit.tagName + '.' + String(hit.className).slice(0, 40) : null };
  }, id);
  await page.mouse.click(p.x, p.y);
  return p;
}

// Resolves when the camera has held still for a few samples (ms waited, or -1).
const settleCam = (page, ms) => page.evaluate(async (ms) => {
  let last = null, same = 0;
  const t0 = performance.now();
  while (performance.now() - t0 < ms) {
    const c = JSON.stringify(__t.g().camera());
    if (c === last) { if (++same >= 4) return performance.now() - t0; } else { same = 0; last = c; }
    await __t.wait(40);
  }
  return -1;
}, ms || 2500);
// Screen boxes of the glass and of the given nodes (all by default), plus the camera.
const boxes = (page, ids) => page.evaluate((ids) => {
  const R = (el) => { if (!el) return null; const b = el.getBoundingClientRect(); return { l: b.left, t: b.top, r: b.right, b: b.bottom }; };
  const vz = __t.vis(document.querySelector('.fx-zoom'));
  return { insp: __t.insp().visible ? R(document.querySelector('aside.fx-inspector')) : null, graph: R(document.querySelector('.fx-graph')),
    top: R(document.querySelector('.fx-top')), zoom: vz ? R(document.querySelector('.fx-zoom')) : null, cam: __t.g().camera(),
    nodes: Object.fromEntries((ids || __t.ids()).map((id) => [id, R(__t.q(id))])) };
}, ids || null);
// The node a user would open at the floor's edge, among those whose centre is really clickable: on a
// desktop the rightmost one with a parent and children (poll 1: X.ids.edge), so the inspector would
// cover it and its neighbours; on a phone the lowest one, so the bottom sheet would.
const edgeNode = (page, phone, poll) => page.evaluate(([phone, tree]) => {
  const z = document.querySelector('.fx-zoom').getBoundingClientRect();
  let best = null;
  for (const id of __t.ids()) {
    if (!phone && !(tree[id] && tree[id].parent && tree[id].kids.length)) continue;
    const b = __t.q(id).getBoundingClientRect(), x = b.left + b.width / 2, y = b.top + b.height / 2, hit = document.elementFromPoint(x, y);
    if (!hit || hit.closest('.gx-node') !== __t.q(id) || (x > z.left - 4 && y > z.top - 4)) continue;
    const v = phone ? b.bottom : b.right;
    if (!best || v > best.v) best = { id, v };
  }
  return best && best.id;
}, [phone, X.tree[poll || '1']]);
// Click a node where it stands (no centring first), as a user would.
async function clickInPlace(page, id) {
  const p = await page.evaluate((id) => {
    const el = __t.q(id), b = el.getBoundingClientRect(), x = b.left + b.width / 2, y = b.top + b.height / 2, hit = document.elementFromPoint(x, y);
    return { x, y, covered: !(hit && hit.closest('.gx-node') === el) };
  }, id);
  await page.mouse.click(p.x, p.y);
  return p;
}

(async () => {
  browser = await chromium.launch({ executablePath: process.env.CHROME, headless: true });

  // 1. Render, HUD, particles under normal motion ---------------------------------------------------------------
  await step('render', async (r) => {
    await withPage({}, r, async (page) => {
      await waitFx(page, 1);
      await page.waitForFunction((n) => document.querySelectorAll('.gx-node[data-id]').length >= n, X.poll1.ids.length, { timeout: 6000 }).catch(() => {});
      r.settleMs = await settle(page, 4000);
      await page.waitForTimeout(1500);
      Object.assign(r, await page.evaluate(() => {
        const st = document.querySelector('.fx-stage');
        const txt = (s) => { const e = document.querySelector(s); return e ? e.textContent.replace(/\s+/g, ' ').trim() : null; };
        return { hasFx: !!document.querySelector('section.fx'), hasStage: !!st, stageMode: st ? st.dataset.mode : null, ids: __t.ids(), hud: __t.hud(),
          h1: txt('section.fx h1'), sub: txt('.fx-sub'), stats: __t.g() ? __t.g().stats() : null, hash: location.hash,
          hostIsGx: !!document.querySelector('.fx-graph.gx'), canvas: !!document.querySelector('.gx-canvas'),
          alerts: document.querySelectorAll('.fx-alert').length, evs: document.querySelectorAll('.fx-ev-w').length, ticker: !!document.querySelector('.fx-ticker, .fx-tick'),
          side: __t.side(),
          zoomBtns: [...document.querySelectorAll('.fx-zoom button[data-zoom]')].map((b) => b.dataset.zoom),
          worldLabel: (document.querySelector('.gx-world') || { getAttribute: () => null }).getAttribute('aria-label') };
      }));
    });
  });

  // 2. Inspector content per node kind ---------------------------------------------------------------------------
  await step('inspector', async (r) => {
    r.cases = {};
    await withPage({}, r, async (page) => {
      await waitFx(page, 32); await settle(page, 4000);
      for (const [key, id] of Object.entries(X.inspectIds)) {
        const c = r.cases[key] = {};
        try {
          c.click = await clickNode(page, id);
          c.openMs = await waitInsp(page, 3000);
          c.insp = await page.evaluate(() => __t.insp());
          await page.waitForTimeout(400);                                 // the engine's own selection must follow a user pick at once
          c.selected = await page.evaluate((id) => ({ sel: __t.g().selected(), cls: !!(__t.q(id) && __t.q(id).classList.contains('is-selected')) }), id);
          await page.keyboard.press('Escape');
          c.closedMs = await page.evaluate(() => __t.until(() => !__t.insp().visible, 2000));
          c.afterEsc = await page.evaluate(() => ({ visible: __t.insp().visible, selected: __t.g().selected() }));
        } catch (e) { c.error = first(e); }
      }
    });
  });

  // 3. State-only polls change the page in place; structural polls keep the survivors anchored --------------------
  await step('inplace', async (r) => {
    await withPage({}, r, async (page) => {
      await waitFx(page, 32); r.settle0 = await settle(page, 4000);
      await page.waitForTimeout(3500);                                   // one poll of the unchanged payload goes by
      r.base = await page.evaluate((I) => {
        const g = __t.g();
        window.__fxTags = {};
        let i = 0;
        for (const el of document.querySelectorAll('.gx-node[data-id]')) { el.__fxTag = 'T' + i; window.__fxTags[el.dataset.id] = 'T' + i; i++; }
        window.__fxBase = { pos: __t.clone(g.positions()), scr: __t.centres(), cam: __t.clone(g.camera()) };
        const m = window.__fxM = { world: 0, screen: 0, cam: 0, camK: 0, frames: 0 };
        const loop = () => {
          if (window.__fxM !== m) return;
          const gg = __t.g();
          if (gg) {
            const p = gg.positions(), c = __t.centres(), cam = gg.camera(), b = window.__fxBase;
            for (const id in b.pos) {
              if (p[id]) m.world = Math.max(m.world, Math.hypot(p[id].x - b.pos[id].x, p[id].y - b.pos[id].y));
              if (c[id] && b.scr[id]) m.screen = Math.max(m.screen, Math.hypot(c[id].x - b.scr[id].x, c[id].y - b.scr[id].y));
            }
            m.cam = Math.max(m.cam, Math.abs(cam.x - b.cam.x), Math.abs(cam.y - b.cam.y));
            m.camK = Math.max(m.camK, Math.abs(cam.k - b.cam.k));
            m.frames++;
          }
          requestAnimationFrame(loop);
        };
        requestAnimationFrame(loop);
        const labels = Object.fromEntries(__t.ids().map((id) => [id, __t.q(id).getAttribute('aria-label')]));
        return { n: i, cam: window.__fxBase.cam, labels, faces: { a2: __t.face(I.a2), b3: __t.face(I.b3), s6: __t.face(I.s6) } };
      }, X.ids);
      await setPoll('states');
      r.stateAppearMs = await page.evaluate((id) => __t.until(() => { const f = __t.face(id); return f && /(^| )st-done( |$)/.test(f.cls); }, 9000), X.ids.a2);
      await page.waitForTimeout(800);
      r.afterStates = await page.evaluate((I) => {
        const m = window.__fxM; window.__fxM = null;
        const g = __t.g(), els = [...document.querySelectorAll('.gx-node[data-id]')];
        const p = g.positions(), c = __t.centres(), b = window.__fxBase, cam = g.camera();
        let fw = 0, fs = 0;
        for (const id in b.pos) {
          if (p[id]) fw = Math.max(fw, Math.hypot(p[id].x - b.pos[id].x, p[id].y - b.pos[id].y));
          if (c[id] && b.scr[id]) fs = Math.max(fs, Math.hypot(c[id].x - b.scr[id].x, c[id].y - b.scr[id].y));
        }
        return { m, count: els.length, replaced: els.filter((e) => !e.__fxTag || e.__fxTag !== window.__fxTags[e.dataset.id]).map((e) => e.dataset.id),
          finalWorld: fw, finalScreen: fs, camBefore: b.cam, camAfter: cam,
          faces: { a2: __t.face(I.a2), b3: __t.face(I.b3), s6: __t.face(I.s6) }, alerts: document.querySelectorAll('.fx-alert').length,
          needsHud: (__t.hud().needs || {}).v || null, labels: Object.fromEntries(__t.ids().map((id) => [id, __t.q(id).getAttribute('aria-label')])) };
      }, X.ids);
      // poll 2: structure changes
      r.pre2 = await page.evaluate(() => { window.__fxPre2 = __t.clone(__t.g().positions()); return { n: Object.keys(window.__fxPre2).length, cam: __t.clone(__t.g().camera()) }; });
      await setPoll('2');
      r.addedAppearMs = await page.evaluate((added) => __t.until(() => added.every((id) => __t.q(id)), 9000), X.poll2.added);
      r.removedGoneMs = await page.evaluate((removed) => __t.until(() => removed.every((id) => !__t.q(id)), 4000), X.poll2.removed);
      r.settle2Ms = await page.evaluate(() => __t.until(() => __t.g().stats().settled, 3500));
      await page.waitForTimeout(400);
      r.after2 = await page.evaluate((I) => {
        const g = __t.g(), p = g.positions(), pre = window.__fxPre2, d = [];
        for (const id in pre) if (p[id]) d.push(Math.hypot(p[id].x - pre[id].x, p[id].y - pre[id].y));
        d.sort((a, b) => a - b);
        const near = {};
        for (const id of I.added) { const n = p[id], par = I.parentOf[id]; near[id] = n && p[par] ? Math.hypot(n.x - p[par].x, n.y - p[par].y) : null; }
        return { ids: __t.ids(), common: d.length, max: d.length ? d[d.length - 1] : null, median: d.length ? d[Math.floor(d.length / 2)] : null,
          stats: g.stats(), camera: g.camera(), nearParent: near, hud: __t.hud(),
          alerts: [...document.querySelectorAll('.fx-alert')].map((b) => ({ node: b.dataset.node, text: b.textContent.replace(/\s+/g, ' ').trim().slice(0, 120) })) };
      }, { added: X.poll2.added, parentOf: X.poll2.parentOf });
    });
  });

  // 4. Alerts focus their node (mouse and keyboard) -------------------------------------------------------------
  await step('alerts', async (r) => {
    r.runs = {};
    await withPage({}, r, async (page) => {
      await waitFx(page, 32); await settle(page, 4000); await page.waitForTimeout(500);
      r.alerts = await page.evaluate(() => [...document.querySelectorAll('.fx-alert')].map((b) => ({ node: b.dataset.node, key: b.dataset.key, tag: b.tagName, text: b.textContent.replace(/\s+/g, ' ').trim().slice(0, 140) })));
      if (!r.alerts.length) throw new Error('no .fx-alert on poll 1');
      const nodeId = r.alerts[0].node;
      for (const how of ['click', 'keyboard']) {
        const c = r.runs[how] = {};
        try {
          await page.keyboard.press('Escape');
          await page.evaluate((far) => { const g = __t.g(); g.select(null); g.center(far, { animate: false, zoom: 1.25 }); }, X.ids.far);   // pan away first
          await page.waitForTimeout(250);
          c.before = await page.evaluate((id) => ({ sel: __t.g().selected(), pos: __t.g().screenPos(id) }), nodeId);
          if (how === 'click') await page.click('.fx-alert', { timeout: 4000 });
          else { await page.focus('.fx-alert'); await page.keyboard.press('Enter'); }
          await page.waitForTimeout(1500);                                // an animated centre may run
          c.after = await page.evaluate((id) => {
            const g = __t.g(), h = document.querySelector('.fx-graph').getBoundingClientRect();
            return { sel: g.selected(), pos: g.screenPos(id), host: { w: h.width, h: h.height }, insp: __t.insp(), cam: g.camera() };
          }, nodeId);
        } catch (e) { c.error = first(e); }
      }
    });
  });

  // 5. Keyboard -------------------------------------------------------------------------------------------------
  await step('keyboard', async (r) => {
    await withPage({}, r, async (page) => {
      await waitFx(page, 32); await settle(page, 4000);
      r.tabStops = await page.evaluate(() => [...document.querySelectorAll('.gx-node[data-id]')].filter((n) => n.tabIndex >= 0).length);
      await page.evaluate(() => { const a = document.activeElement; if (a && a !== document.body) a.blur(); });
      r.tabsToNode = -1;
      for (let i = 1; i <= 60 && r.tabsToNode < 0; i++) {
        await page.keyboard.press('Tab');
        if (await page.evaluate(() => !!(document.activeElement && document.activeElement.classList.contains('gx-node')))) r.tabsToNode = i;
      }
      const pick = await page.evaluate(() => {
        const g = __t.g(), pts = __t.ids().map((id) => [id, g.screenPos(id)]).filter((x) => x[1]);
        return { left: pts.reduce((a, b) => (b[1].x < a[1].x ? b : a))[0], top: pts.reduce((a, b) => (b[1].y < a[1].y ? b : a))[0], first: __t.ids()[0], last: __t.ids().slice(-1)[0] };
      });
      r.pick = pick;
      const active = () => page.evaluate(() => { const a = document.activeElement; return { id: a && a.classList.contains('gx-node') ? a.dataset.id : null, what: a ? a.tagName + '.' + String(a.className).slice(0, 30) : null }; });
      const press = async (from, key) => {
        await page.evaluate((id) => __t.q(id).focus(), from);
        await page.keyboard.press(key);
        await page.waitForTimeout(250);
        const a = await active();
        return { from, to: a.id, what: a.what };
      };
      r.right = await press(pick.left, 'ArrowRight');
      r.down = await press(pick.top, 'ArrowDown');
      r.home = await press(pick.last, 'Home');
      r.end = await press(pick.first, 'End');
      // Enter opens, Escape closes
      await page.evaluate((id) => __t.q(id).focus(), X.ids.agent);
      await page.keyboard.press('Enter');
      r.enterOpenMs = await waitInsp(page, 3000);
      await page.waitForTimeout(400);
      r.enter = await page.evaluate((id) => ({ insp: __t.insp(), selected: __t.g().selected(), cls: !!__t.q(id).classList.contains('is-selected') }), X.ids.agent);
      await page.keyboard.press('Escape');
      r.escClosedMs = await page.evaluate(() => __t.until(() => !__t.insp().visible, 2000));
      r.esc = await page.evaluate(() => ({ visible: __t.insp().visible, selected: __t.g().selected() }));
      // Space opens too
      await page.evaluate((id) => __t.q(id).focus(), X.ids.session);
      await page.keyboard.press('Space');
      r.spaceOpenMs = await waitInsp(page, 3000);
      await page.waitForTimeout(400);
      r.space = await page.evaluate((id) => ({ insp: __t.insp(), selected: __t.g().selected(), cls: !!__t.q(id).classList.contains('is-selected') }), X.ids.session);
      await page.keyboard.press('Escape');
      await page.waitForTimeout(300);
      // view keys: j / k move focus through the nodes, + / - zoom
      await page.evaluate(() => { const a = document.activeElement; if (a && a !== document.body) a.blur(); });
      const key = async (k) => { await page.keyboard.press(k); await page.waitForTimeout(250); return (await active()).id; };
      r.j1 = await key('j'); r.j2 = await key('j'); r.k1 = await key('k');
      await page.evaluate(() => { const a = document.activeElement; if (a && a !== document.body) a.blur(); });
      const k = () => page.evaluate(() => __t.g().camera().k);
      r.k0 = await k();
      await page.keyboard.press('+'); await page.waitForTimeout(800); r.kIn = await k();
      await page.keyboard.press('-'); await page.keyboard.press('-'); await page.waitForTimeout(800); r.kOut = await k();
      // `f` from Projects (the other tab) lands on the Factory
      await page.evaluate(() => { const a = document.activeElement; if (a && a !== document.body) a.blur(); location.hash = '#/overview'; });
      await page.waitForFunction(() => !document.querySelector('section.fx'), null, { timeout: 5000 }).catch(() => {});
      r.fromHash = await page.evaluate(() => location.hash);
      r.fromHadFx = await page.evaluate(() => !!document.querySelector('section.fx'));
      await page.keyboard.press('f');
      r.fKeyMs = await page.evaluate(() => __t.until(() => location.hash.indexOf('#/factory') === 0 && document.querySelector('section.fx'), 4000));
      r.fKeyHash = await page.evaluate(() => location.hash);
    });
  });

  // 6. Both themes ------------------------------------------------------------------------------------------------
  await step('themes', async (r) => {
    r.v = {};
    const measure = (page) => page.evaluate(() => {
      const st = document.querySelector('.fx-stage'), gr = document.querySelector('.fx-graph'), kv = document.querySelector('.fx-hud .fx-kpi-v');
      const cs = st && getComputedStyle(st), gs = gr && getComputedStyle(gr), ks = kv && getComputedStyle(kv);
      return { dataTheme: document.documentElement.dataset.theme || null, systemDark: matchMedia('(prefers-color-scheme: dark)').matches,
        stageImg: cs ? cs.backgroundImage : null, stageColor: cs ? cs.backgroundColor : null, graphImg: gs ? gs.backgroundImage : null,
        graphColor: gs ? gs.backgroundColor : null, hudColor: ks ? ks.color : null, bodyBg: getComputedStyle(document.body).backgroundColor, nodes: __t.ids().length };
    });
    const variants = [['light', { scheme: 'light' }], ['dark', { scheme: 'dark' }],
      ['darkOverride', { scheme: 'light', url: '/?theme=dark#/factory' }], ['lightOverride', { scheme: 'dark', url: '/?theme=light#/factory' }]];
    for (const [name, o] of variants) {
      const v = r.v[name] = {};
      const rr = {};
      try {
        await withPage(o, rr, async (page) => {
          await waitFx(page, 32); await settle(page, 4000); await page.waitForTimeout(700);
          Object.assign(v, await measure(page));
          if (name === 'dark') {                                          // the system flips to light while the page is open
            await page.emulateMedia({ colorScheme: 'light' });
            await page.waitForTimeout(900);
            v.flip = await measure(page);
          }
        });
      } catch (e) { v.error = first(e); }
      v.errors = rr.errors; v.consoleErrors = rr.consoleErrors;
    }
  });

  // 7. Reduced motion ---------------------------------------------------------------------------------------------
  await step('reduced', async (r) => {
    await withPage({ reduced: true }, r, async (page) => {
      await page.waitForFunction(() => document.querySelector('.gx-node[data-id]'), null, { timeout: 10000, polling: 'raf' });
      r.first = await page.evaluate(async () => {
        const g = __t.g(), t0 = performance.now();
        const pos0 = __t.clone(g.positions());
        while (!g.stats().settled && performance.now() - t0 < 2000) await new Promise((ok) => requestAnimationFrame(ok));
        window.__fxPos0 = pos0;
        return { settledAfterMs: performance.now() - t0, settled: g.stats().settled, stats: g.stats(), reduce: matchMedia('(prefers-reduced-motion: reduce)').matches, nodes: __t.ids().length };
      });
      r.particleMax = await page.evaluate(async () => {
        let mx = 0;
        for (let i = 0; i < 25; i++) { mx = Math.max(mx, __t.g().stats().particles); await __t.wait(80); }
        return mx;
      });
      r.later = await page.evaluate(() => {
        const g = __t.g(), p = g.positions(), b = window.__fxPos0;
        let mv = 0;
        for (const id in b) if (p[id]) mv = Math.max(mv, Math.hypot(p[id].x - b[id].x, p[id].y - b[id].y));
        return { moved: mv, stats: g.stats() };
      });
    });
  });

  // 8. Phone width ------------------------------------------------------------------------------------------------
  await step('mobile', async (r) => {
    const measure = (page) => page.evaluate(() => {
      const iw = innerWidth;
      const offenders = [...document.querySelectorAll('body *')].filter((e) => !e.closest('.gx-world') && !e.closest('[hidden]') && e.getBoundingClientRect().right > iw + 1 && e.getBoundingClientRect().width > 0)
        .slice(0, 6).map((e) => e.tagName.toLowerCase() + '.' + (e.getAttribute('class') || '').split(' ').slice(0, 2).join('.') + ' r=' + Math.round(e.getBoundingClientRect().right));
      return { scrollWidth: document.documentElement.scrollWidth, bodyScrollWidth: document.body.scrollWidth, innerWidth: iw, offenders };
    });
    await withPage({ w: 390, h: 844 }, r, async (page) => {
      await waitFx(page, 32); await settle(page, 4000); await page.waitForTimeout(1000);
      r.noInspector = await measure(page);
      try { await page.click('.fx-alert', { timeout: 3000 }); r.opened = 'alert'; }
      catch (e) { r.opened = 'alert click failed: ' + first(e); await page.evaluate(() => { const b = document.querySelector('.fx-alert'); if (b) b.click(); }); }
      r.inspMs = await waitInsp(page, 3000);
      await page.waitForTimeout(600);
      r.withInspector = await measure(page);
      r.insp = await page.evaluate(() => __t.insp());
      await page.keyboard.press('Escape');
      await page.waitForTimeout(300);
      const lb = await page.$('.fx-mode button[data-mode="list"]');
      if (lb) { await lb.click(); await page.waitForTimeout(500); r.listMode = await measure(page); }
    });
  });

  // 9. List equivalent --------------------------------------------------------------------------------------------
  await step('list', async (r) => {
    await withPage({}, r, async (page) => {
      await waitFx(page, 32); await settle(page, 4000);
      await page.click('.fx-mode button[data-mode="list"]');
      await page.waitForTimeout(600);
      Object.assign(r, await page.evaluate(() => {
        const st = document.querySelector('.fx-stage'), ls = document.querySelector('.fx-list');
        const items = [...document.querySelectorAll('button.fx-li[data-node]')].map((b) => b.dataset.node);
        return { mode: st ? st.dataset.mode : null, listVisible: __t.vis(ls), graphVisible: __t.vis(document.querySelector('.fx-graph')), items, uniq: new Set(items).size,
          pressed: [...document.querySelectorAll('.fx-mode button[data-mode]')].map((b) => b.dataset.mode + '=' + b.getAttribute('aria-pressed')),
          sample: items.length ? document.querySelector('button.fx-li[data-node]').textContent.replace(/\s+/g, ' ').trim().slice(0, 100) : null };
      }));
      const target = X.ids.agent;
      await page.click('button.fx-li[data-node=' + JSON.stringify(target) + ']');
      r.openMs = await waitInsp(page, 3000);
      r.insp = await page.evaluate(() => __t.insp());
      await page.keyboard.press('Escape');
      await page.waitForTimeout(300);
      await page.click('.fx-mode button[data-mode="graph"]');
      r.backMs = await page.evaluate((n) => __t.until(() => __t.ids().length >= n && __t.vis(document.querySelector('.fx-graph')), 5000), X.poll1.ids.length);
    });
  });

  // 10. Scope: project filter, then the dormant toggle -------------------------------------------------------------
  await step('scope', async (r) => {
    await withPage({ hash: '#/factory?p=reports-app' }, r, async (page, rec) => {
      await waitFx(page, 1);
      await page.waitForTimeout(3700);                                   // at least one more poll
      r.reqs = rec.graphReqs.slice();
      r.ids = await page.evaluate(() => __t.ids());
      r.hash = await page.evaluate(() => location.hash);
      r.tools = await page.evaluate(() => { const t = document.querySelector('.fx-tools'); return t ? t.textContent.replace(/\s+/g, ' ').trim() : null; });
      // clearing the scope in the address goes back to every project
      const n0 = rec.graphReqs.length;
      await page.evaluate(() => { location.hash = '#/factory'; });
      r.clearedMs = await page.evaluate((n) => __t.until(() => __t.ids().length >= n, 9000), X.poll1.ids.length);
      r.clearedReqs = rec.graphReqs.slice(n0);
      r.clearedIds = await page.evaluate(() => __t.ids());
    });
  });
  await step('dormant', async (r) => {
    await withPage({}, r, async (page, rec) => {
      await waitFx(page, 32); await settle(page, 4000);
      r.before = await page.evaluate(() => { const b = document.querySelector('button.fx-dormant'); return { exists: !!b, pressed: b ? b.getAttribute('aria-pressed') : null, hub: !!__t.q('p:dotfiles') }; });
      const n0 = rec.graphReqs.length;
      await page.click('button.fx-dormant');
      r.hubMs = await page.evaluate(() => __t.until(() => __t.q('p:dotfiles'), 9000));
      r.on = await page.evaluate(() => ({ pressed: document.querySelector('button.fx-dormant').getAttribute('aria-pressed'), nodes: __t.ids().length, hash: location.hash }));
      r.onReqs = rec.graphReqs.slice(n0);
      const n1 = rec.graphReqs.length;
      await page.click('button.fx-dormant');
      r.hubGoneMs = await page.evaluate(() => __t.until(() => !__t.q('p:dotfiles'), 9000));
      r.off = await page.evaluate(() => ({ pressed: document.querySelector('button.fx-dormant').getAttribute('aria-pressed'), nodes: __t.ids().length, hash: location.hash }));
      r.offReqs = rec.graphReqs.slice(n1);
    });
  });

  // 11. Performance: ~300 nodes ----------------------------------------------------------------------------------
  await step('perf', async (r) => {
    await withPage({ poll: 'big' }, r, async (page) => {
      await waitFx(page, X.bigN, 20000);
      r.settleWaitMs = await settle(page, 6000);
      await page.waitForTimeout(2500);
      r.samples = [];
      for (let i = 0; i < 4; i++) { r.samples.push(await page.evaluate(() => __t.g().stats())); await page.waitForTimeout(500); }
      r.count = await page.evaluate(() => __t.ids().length);
      r.fps = await page.evaluate(() => new Promise((ok) => { let n = 0; const t0 = performance.now(); const f = () => { n++; if (performance.now() - t0 < 1000) requestAnimationFrame(f); else ok(n); }; requestAnimationFrame(f); }));
    });
  });

  // 12. Empty payload, and recovery --------------------------------------------------------------------------------
  await step('empty', async (r) => {
    await withPage({ poll: 'empty' }, r, async (page) => {
      await page.waitForSelector('section.fx', { timeout: 9000 });
      r.emptyMs = await page.evaluate(() => __t.until(() => __t.vis(document.querySelector('.fx-stage .empty')), 9000));
      r.empty = await page.evaluate(() => { const e = document.querySelector('.fx-stage .empty'); return { text: e ? e.textContent.replace(/\s+/g, ' ').trim().slice(0, 160) : null, nodes: __t.ids().length, hasStage: !!document.querySelector('.fx-stage') }; });
      await setPoll('1');
      r.recoverMs = await page.evaluate((n) => __t.until(() => __t.ids().length >= n, 9000), X.poll1.ids.length);
      r.recovered = await page.evaluate(() => ({ nodes: __t.ids().length, emptyVisible: __t.vis(document.querySelector('.fx-stage .empty')) }));
    });
  });

  // 12b. The inspector re-frames: the opened node, its parent and its children end up clear of it; closing fits again
  await step('reframe', async (r) => {
    r.cases = {};
    // poll 1 on a desktop still fits readably beside the inspector (re-fit); the 300-node floor does not (pan)
    for (const [name, o] of [['desktop', { w: 1440, h: 900 }], ['phone', { w: 390, h: 844 }], ['desktop big', { w: 1440, h: 900, poll: 'big' }]]) {
      const c = r.cases[name] = {};
      const rr = {};
      try {
        await withPage(o, rr, async (page) => {
          await waitFx(page, o.poll === 'big' ? X.bigN : 32, 20000); await settle(page, 6000); await settleCam(page);
          c.cam0 = await page.evaluate(() => __t.g().camera());
          c.id = await edgeNode(page, name === 'phone', o.poll);
          if (!c.id) throw new Error('no clickable node at the edge');
          const t = X.tree[o.poll || '1'][c.id];
          c.hood = [c.id, t.parent, ...t.kids].filter(Boolean);
          c.before = await boxes(page, c.hood);
          c.click = await clickInPlace(page, c.id);
          c.openMs = await waitInsp(page, 3000);
          await page.waitForTimeout(320);
          c.camMs = await settleCam(page);
          c.open = await boxes(page, c.hood);
          c.sel = await page.evaluate(() => __t.g().selected());
          await page.keyboard.press('Escape');
          c.closedMs = await page.evaluate(() => __t.until(() => !__t.insp().visible, 2000));
          await page.waitForTimeout(120);
          c.closeCamMs = await settleCam(page);
          c.closed = await boxes(page, c.hood);
        });
      } catch (e) { c.error = first(e); }
      c.errors = rr.errors; c.consoleErrors = rr.consoleErrors;
    }
  });

  // 12c. Fit never puts a node under the zoom buttons (poll 1 and the 300-node floor, both widths)
  await step('zoomclear', async (r) => {
    r.cases = {};
    for (const [poll, w, h] of [['1', 1440, 900], ['1', 390, 844], ['big', 1440, 900], ['big', 390, 844]]) {
      const c = r.cases[poll + '@' + w] = {};
      const rr = {};
      try {
        await withPage({ poll, w, h }, rr, async (page) => {
          await waitFx(page, poll === 'big' ? X.bigN : 32, 20000); await settle(page, 6000); await settleCam(page);
          const hits = (b) => Object.entries(b.nodes).filter(([, n]) => n && b.zoom && n.r > b.zoom.l && n.l < b.zoom.r && n.b > b.zoom.t && n.t < b.zoom.b).map(([id]) => id);
          let b = await boxes(page);
          c.load = { hits: hits(b), zoom: b.zoom, k: b.cam.k, n: Object.keys(b.nodes).length };
          await page.keyboard.press('+'); await page.keyboard.press('+'); await settleCam(page);
          await page.keyboard.press('0'); await page.waitForTimeout(100); await settleCam(page);
          b = await boxes(page);
          c.fit = { hits: hits(b), zoom: b.zoom, k: b.cam.k, n: Object.keys(b.nodes).length };
        });
      } catch (e) { c.error = first(e); }
      c.errors = rr.errors; c.consoleErrors = rr.consoleErrors;
    }
  });

  // 12d. Every project hub's name stays inside its disc at every level of detail, long names included
  await step('hubs', async (r) => {
    r.levels = {};
    await withPage({ poll: 'long' }, r, async (page) => {
      await waitFx(page, 34); await settle(page, 4000);
      for (const [lod, k] of [['near', 1.0], ['mid', 0.7], ['far', 0.35]]) {
        await page.evaluate((k) => __t.g().center('p:long-unbroken', { zoom: k, animate: false }), k);
        await page.waitForTimeout(160);
        r.levels[lod] = await page.evaluate(() => {
          const out = { lod: document.querySelector('.fx-graph').dataset.lod, hubs: {} };
          for (const n of document.querySelectorAll('.gx-node[data-kind="project"]')) {
            const d = n.getBoundingClientRect(), cx = d.left + d.width / 2, cy = d.top + d.height / 2, R = d.width / 2, rim = R * (64 - 7.5) / 64;
            const shown = [...n.querySelectorAll('.fx-hn')].filter((v) => getComputedStyle(v).display !== 'none');
            const lines = shown.flatMap((v) => [...v.children]).map((s) => ({ text: s.textContent, b: s.getBoundingClientRect() }));
            const extra = [...n.querySelectorAll('.fx-hub-meta, .fx-hub-cost')].filter((e) => __t.vis(e)).map((e) => ({ text: e.textContent, b: e.getBoundingClientRect() }));
            const out1 = (x) => Math.max(...[[x.b.left, x.b.top], [x.b.right, x.b.top], [x.b.left, x.b.bottom], [x.b.right, x.b.bottom]].map(([px, py]) => Math.hypot(px - cx, py - cy))) / R;
            const nm = n.querySelector('.fx-hub-name');
            out.hubs[n.dataset.id] = { variants: shown.map((v) => v.dataset.lod), lines: lines.map((x) => x.text), worst: lines.length ? Math.max(...lines.map(out1)) : null,
              rimRatio: rim / R, extraWorst: extra.length ? Math.max(...extra.map(out1)) : 0, title: nm ? nm.getAttribute('title') : null,
              aria: n.getAttribute('aria-label') || '', fs: shown.length ? getComputedStyle(shown[0]).fontSize : null };
          }
          return out;
        });
      }
    });
  });

  // 12e. WCAG 2.2.2: the activity panel (.fx-acts) holds still under the pointer and while focused, and takes the
  // newest feed on release; a focused alert stays put
  await step('hold', async (r) => {
    await withPage({}, r, async (page) => {
      await waitFx(page, 32); await settle(page, 4000); await page.waitForTimeout(600);
      const snap = () => page.evaluate(() => {
        const t = document.querySelector('.fx-acts');
        return { keys: [...t.querySelectorAll('.fx-ev-w')].map((x) => x.dataset.key), text: t.textContent, tagged: [...t.querySelectorAll('.fx-ev-w')].filter((x) => x.__tag).length };
      });
      const tag = () => page.evaluate(() => { for (const x of document.querySelectorAll('.fx-acts .fx-ev-w')) x.__tag = 1; });
      const poll = async (n, done) => { await setPoll(n); return page.evaluate((done) => __t.until(() => (done.has ? !!__t.q(done.id) : !__t.q(done.id)), 9000), done); };
      const added = X.poll2.added[0];
      // pointer over the panel
      const tb = await page.evaluate(() => { const b = document.querySelector('.fx-acts').getBoundingClientRect(); return { x: b.left + b.width / 2, y: b.top + Math.min(b.height / 2, 120) }; });
      await page.mouse.move(tb.x, tb.y);
      await page.waitForTimeout(100);
      await tag();
      r.hover0 = await snap();
      r.hoverPollMs = await poll('2', { id: added, has: true });
      await page.waitForTimeout(3300);                                    // and one more poll while held
      r.hover1 = await snap();
      await page.mouse.move(4, 4);
      await page.waitForTimeout(300);
      r.hover2 = await snap();
      // focus in the panel
      await page.focus('.fx-acts .fx-ev');
      await tag();
      r.focus0 = await snap();
      r.focusPollMs = await poll('1', { id: added, has: false });
      await page.waitForTimeout(600);
      r.focus1 = await snap();
      r.focusStill = await page.evaluate(() => !!(document.activeElement && document.activeElement.closest('.fx-acts')));
      await page.evaluate(() => document.activeElement.blur());
      await page.waitForTimeout(300);
      r.focus2 = await snap();
      // a focused alert is not replaced under the keyboard
      await page.focus('.fx-alert');
      r.alert0 = await page.evaluate(() => { const a = document.activeElement; a.__tag = 1; return a.dataset.node; });
      r.alertPollMs = await poll('2', { id: added, has: true });
      await page.waitForTimeout(600);
      r.alert1 = await page.evaluate(() => { const a = document.activeElement; return { node: a && a.dataset.node, same: !!(a && a.__tag), connected: !!(a && a.isConnected), n: document.querySelectorAll('.fx-alert').length }; });
      await page.evaluate(() => document.activeElement.blur());
      await page.waitForTimeout(300);
      r.alert2 = await page.evaluate(() => [...document.querySelectorAll('.fx-alert')].map((b) => b.dataset.node));
    });
  });

  // 12f. Text on glass clears WCAG AA against what is really behind it: every text run on a node, in the
  // instrument panel and in the inspector is measured against a screenshot of the page with the text made
  // transparent (so gradients, the lattice, edges and the frosted blur all count), in both themes, at the
  // fitted zoom and up close. Reduced motion, so no particle passes under a face mid-shot.
  await step('contrast', async (r) => {
    r.views = {};
    for (const scheme of ['light', 'dark']) {
      for (const view of ['fit', 'near']) {
        const v = r.views[scheme + ' ' + view] = {};
        const rr = {};
        try {
          await withPage({ scheme, reduced: true }, rr, async (page) => {
            await waitFx(page, 32); await settle(page, 4000); await page.waitForTimeout(400);
            if (view === 'near') {
              await page.evaluate((id) => { __t.g().center(id, { zoom: 1, animate: false }); }, X.ids.session);
              await clickInPlace(page, X.ids.agent);
              await waitInsp(page, 3000);
              await page.waitForTimeout(500);
            }
            const items = await page.evaluate(() => {
              const R = (el) => el.getBoundingClientRect();
              const top = R(document.querySelector('.fx-top')), gr = R(document.querySelector('.fx-graph'));
              const ia = document.querySelector('aside.fx-inspector'), ir = ia && __t.vis(ia) ? R(ia) : null;
              const sa = document.getElementById('fx-side'), sr = sa && __t.vis(sa) ? R(sa) : null;
              const meets = (a, b) => b && a.right > b.left && a.left < b.right && a.bottom > b.top && a.top < b.bottom;
              const out = [];
              window.__fxText = [];
              const roots = [...document.querySelectorAll('.gx-node:not(.is-dim)'), document.querySelector('.fx-top'), ir ? ia : null, sr ? sa : null].filter(Boolean);
              for (const root of roots) {
                const onNode = root.classList.contains('gx-node');
                const tw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
                for (let t = tw.nextNode(); t; t = tw.nextNode()) {
                  const el = t.parentElement;
                  if (!t.nodeValue.trim() || !el || el.closest('[hidden], .vh')) continue;      // .vh: the screen-reader table, not painted
                  const cs = getComputedStyle(el), box = R(el), svgText = el instanceof SVGElement;
                  // what of it can show: inside its own box and every clipping ancestor (ellipsis, a row's clip)
                  const clip = { left: box.left, top: box.top, right: box.right, bottom: box.bottom };
                  for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
                    const ac = getComputedStyle(a);
                    if (ac.overflowX === 'visible' && ac.overflowY === 'visible') continue;
                    const ab = R(a);
                    clip.left = Math.max(clip.left, ab.left); clip.top = Math.max(clip.top, ab.top); clip.right = Math.min(clip.right, ab.right); clip.bottom = Math.min(clip.bottom, ab.bottom);
                  }
                  clip.right = Math.min(clip.right, innerWidth); clip.bottom = Math.min(clip.bottom, innerHeight);
                  const rg = document.createRange();
                  rg.selectNodeContents(t);
                  for (const q of rg.getClientRects()) {
                    const l = Math.max(q.left, clip.left), tp = Math.max(q.top, clip.top), rt = Math.min(q.right, clip.right), bt = Math.min(q.bottom, clip.bottom);
                    if (rt - l < 3 || bt - tp < 4) continue;
                    const rect = { left: l, top: tp, right: rt, bottom: bt };
                    if (onNode && (meets(rect, top) || meets(rect, ir) || meets(rect, sr) || l < gr.left || rt > gr.right || tp < gr.top || bt > gr.bottom)) continue;   // under other glass or off the floor
                    out.push({ l, t: tp, r: rt, b: bt, color: svgText ? cs.fill : cs.color, fs: parseFloat(cs.fontSize), text: t.nodeValue.trim().slice(0, 28), where: onNode ? 'node' : root === ia ? 'inspector' : root === sa ? 'column' : 'panel' });
                  }
                }
              }
              // every element, not just the text's parents: an ellipsis is painted in its block's colour
              for (const root of roots) {
                for (const el of [root, ...root.querySelectorAll('*')]) {
                  if (el.__fxText || !el.style) continue;
                  el.__fxText = 1; window.__fxText.push(el);
                  el.style.setProperty('color', 'transparent', 'important'); el.style.setProperty('-webkit-text-fill-color', 'transparent', 'important');
                  if (el.tagName.toLowerCase() === 'text') el.style.setProperty('fill', 'transparent', 'important');   // svg text is painted with fill
                }
              }
              return out;
            });
            await page.waitForTimeout(60);
            const png = (await page.screenshot()).toString('base64');
            await page.evaluate(() => { for (const el of window.__fxText) { el.style.removeProperty('color'); el.style.removeProperty('-webkit-text-fill-color'); el.style.removeProperty('fill'); } });
            const res = await page.evaluate(async ([b64, items]) => {
              const bin = atob(b64), u8 = new Uint8Array(bin.length);
              for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
              const bmp = await createImageBitmap(new Blob([u8], { type: 'image/png' }));
              const cv = document.createElement('canvas');
              cv.width = bmp.width; cv.height = bmp.height;
              const cx = cv.getContext('2d');
              cx.drawImage(bmp, 0, 0);
              const parse = (c) => {
                let m = /^rgba?\(([^)]+)\)/.exec(c);
                if (m) { const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; }
                m = /^color\(srgb ([^)]+)\)/.exec(c);
                if (m) { const p = m[1].split(/[ /]+/).filter(Boolean).map(Number); return [p[0] * 255, p[1] * 255, p[2] * 255, p.length > 3 ? p[3] : 1]; }
                return null;
              };
              const lin = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
              const lum = (r, g, b) => 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
              const out = [];
              for (const it of items) {
                const fg = parse(it.color);
                if (!fg) { out.push({ ...it, ratio: null }); continue; }
                const x0 = Math.ceil(it.l), y0 = Math.ceil(it.t), w = Math.max(1, Math.floor(it.r) - x0), h = Math.max(1, Math.floor(it.b) - y0);
                const px = cx.getImageData(x0, y0, w, h).data, rs = [];
                for (let i = 0; i < px.length; i += 4) {
                  const a = fg[3], tr = fg[0] * a + px[i] * (1 - a), tg = fg[1] * a + px[i + 1] * (1 - a), tb = fg[2] * a + px[i + 2] * (1 - a);
                  const L1 = lum(tr, tg, tb), L2 = lum(px[i], px[i + 1], px[i + 2]);
                  rs.push([(Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05), i]);
                }
                rs.sort((a, b) => a[0] - b[0]);
                const [ratio, at] = rs[Math.floor(rs.length * 0.02)];
                out.push({ text: it.text, where: it.where, fs: it.fs, color: it.color, ratio, bg: 'rgb(' + px[at] + ', ' + px[at + 1] + ', ' + px[at + 2] + ')',
                  at: [x0 + (at / 4) % w, y0 + Math.floor(at / 4 / w)] });
              }
              return out;
            }, [png, items]);
            v.n = res.length;
            v.low = res.filter((x) => x.ratio == null || x.ratio < 4.5).slice(0, 12);
            v.min = res.reduce((m, x) => (x.ratio != null && x.ratio < m ? x.ratio : m), 99);
            for (const where of ['node', 'panel', 'inspector', 'column']) {
              const xs = res.filter((x) => x.where === where && x.ratio != null);
              v[where] = xs.length ? { n: xs.length, min: Math.min(...xs.map((x) => x.ratio)), worst: xs.reduce((a, b) => (b.ratio < a.ratio ? b : a)).text } : null;
            }
          });
        } catch (e) { v.error = first(e); }
        v.errors = rr.errors; v.consoleErrors = rr.consoleErrors;
      }
    }
  });

  // 12g. Parent cycles: every walk ends, every node is listed and opens ---------------------------------------------
  await step('cycles', async (r) => {
    r.graph = {}; r.list = {};
    await withPage({ poll: 'cycles' }, r, async (page) => {
      await waitFx(page, 35); await settle(page, 4000);
      for (const id of X.cycles) {
        const c = r.graph[id] = {};
        try {
          await clickNode(page, id);
          c.openMs = await waitInsp(page, 3000);
          c.title = (await page.evaluate(() => __t.insp())).title;
          await page.keyboard.press('Escape');
          await page.waitForTimeout(250);
        } catch (e) { c.error = first(e); }
      }
      await page.click('.fx-mode button[data-mode="list"]');
      await page.waitForTimeout(400);
      r.items = await page.evaluate(() => [...document.querySelectorAll('button.fx-li[data-node]')].map((b) => b.dataset.node));
      for (const id of X.cycles) {
        const c = r.list[id] = {};
        try {
          await page.click('button.fx-li[data-node=' + JSON.stringify(id) + ']', { timeout: 4000 });
          c.openMs = await waitInsp(page, 3000);
          c.title = (await page.evaluate(() => __t.insp())).title;
          await page.keyboard.press('Escape');
          await page.waitForTimeout(250);
        } catch (e) { c.error = first(e); }
      }
    });
  });

  // 12h. Focus outline and selected ring keep their screen width on the fitted 306-node floor -------------------------
  await step('focusring', async (r) => {
    await withPage({ poll: 'big' }, r, async (page) => {
      await waitFx(page, X.bigN, 20000); await settle(page, 6000); await settleCam(page);
      await page.evaluate(() => { const a = document.activeElement; if (a && a !== document.body) a.blur(); });
      await page.keyboard.press('j');                                   // keyboard focus: :focus-visible
      await page.waitForTimeout(150); await settleCam(page);
      r.focus = await page.evaluate(() => {
        const a = document.activeElement, cs = getComputedStyle(a);
        return { id: a.dataset.id || null, k: __t.g().camera().k, width: parseFloat(cs.outlineWidth), style: cs.outlineStyle, offset: parseFloat(cs.outlineOffset), visible: a.matches(':focus-visible') };
      });
      await page.keyboard.press('Enter');
      await waitInsp(page, 3000); await page.waitForTimeout(350); await settleCam(page);
      r.selected = await page.evaluate(() => {
        const id = __t.g().selected(), f = __t.q(id).querySelector('.fx-node'), sh = getComputedStyle(f).boxShadow;
        const m = /(?:^|, )(?:rgba?\([^)]*\)|color\([^)]*\)) 0px 0px 0px ([\d.]+)px$/.exec(sh);
        return { id, k: __t.g().camera().k, spread: m ? parseFloat(m[1]) : null, shadow: sh.slice(-80) };
      });
      // What it costs: 60 zoom steps with a forced style and layout pass each, the property on the
      // focused and selected nodes only (as shipped), then also rewriting one on the host (the cheap-looking way)
      r.cost = await page.evaluate(() => {
        const g = __t.g(), host = document.querySelector('.fx-graph');
        const run = (hostToo) => {
          const t0 = performance.now();
          for (let i = 0; i < 60; i++) {
            g.zoomBy(i % 2 ? 1 / 1.01 : 1.01, { animate: false });
            if (hostToo) host.style.setProperty('--gx-probe', String(1 / g.camera().k));
            void document.documentElement.offsetHeight;
          }
          return (performance.now() - t0) / 60;
        };
        run(false);
        const own = run(false), hostWide = run(true);
        host.style.removeProperty('--gx-probe');
        return { own, hostWide };
      });
    });
  });

  // 12i. A new scope is fitted, like pressing 0 ------------------------------------------------------------------------
  await step('scopefit', async (r) => {
    await withPage({}, r, async (page) => {
      await waitFx(page, 32); await settle(page, 4000); await settleCam(page);
      r.cam0 = await page.evaluate(() => __t.g().camera());
      await page.evaluate(() => { location.hash = '#/factory?p=valhalla-strategy'; });
      r.scopedMs = await page.evaluate(() => __t.until(() => __t.ids().length === 4, 9000));
      await page.waitForTimeout(500); await settleCam(page);
      r.cam1 = await page.evaluate(() => __t.g().camera());
      await page.evaluate(() => { const a = document.activeElement; if (a && a !== document.body) a.blur(); });
      await page.keyboard.press('0'); await page.waitForTimeout(100); await settleCam(page);
      r.cam2 = await page.evaluate(() => __t.g().camera());
    });
  });

  // 12j. List mode stops the engine's frame loop ---------------------------------------------------------------------
  await step('listpause', async (r) => {
    await withPage({}, r, async (page) => {
      await waitFx(page, 32); await settle(page, 4000);
      const frames = () => page.evaluate(() => __t.g().stats().frames);
      const f0 = await frames(); await page.waitForTimeout(1000); r.graphFrames = (await frames()) - f0;
      await page.click('.fx-mode button[data-mode="list"]');
      await page.waitForTimeout(300);
      const f1 = await frames(); await page.waitForTimeout(1000); r.listFrames = (await frames()) - f1;
      r.paused = await page.evaluate(() => __t.g().stats().paused);
      await page.click('.fx-mode button[data-mode="graph"]');
      await page.waitForTimeout(300);
      const f2 = await frames(); await page.waitForTimeout(1000); r.backFrames = (await frames()) - f2;
    });
  });

  // 12k. A 200 that is not a graph is an error, not a forever skeleton -------------------------------------------------
  await step('badpoll', async (r) => {
    const state = (page) => page.evaluate(() => {
      const m = document.querySelector('.fx-msg');
      return { nodes: __t.ids().length, syncErr: !!document.querySelector('#sync.err'), msg: m && !m.hidden ? m.dataset.kind || 'shown' : null,
        msgText: m && !m.hidden ? m.textContent.replace(/\s+/g, ' ').trim().slice(0, 120) : null, skeleton: !!document.querySelector('.fx-hud .sk'),
        shimmer: [...document.querySelectorAll('.fx-hud .sk')].some((x) => getComputedStyle(x).animationName !== 'none') };
    });
    r.kept = {};
    await withPage({}, r, async (page) => {
      await waitFx(page, 32); await settle(page, 4000);
      for (const bad of ['garbage', 'objnodes']) {
        await setPoll(bad);
        r.kept[bad + 'Ms'] = await page.evaluate(() => __t.until(() => document.querySelector('#sync.err'), 9000));
        r.kept[bad] = await state(page);
      }
      await setPoll('1');
      r.kept.recoverMs = await page.evaluate(() => __t.until(() => !document.querySelector('#sync.err'), 9000));
      r.kept.recovered = await state(page);
    });
    r.cold = {};
    const rr = {};
    try {
      await withPage({ poll: 'garbage' }, rr, async (page) => {
        await page.waitForSelector('section.fx', { timeout: 9000 });
        r.cold.errMs = await page.evaluate(() => __t.until(() => { const m = document.querySelector('.fx-msg'); return m && !m.hidden && m.dataset.kind === 'error'; }, 9000));
        r.cold.err = await state(page);
        await setPoll('1');
        r.cold.recoverMs = await page.evaluate(() => __t.until(() => __t.ids().length >= 32, 9000));
        r.cold.recovered = await state(page);
      });
    } catch (e) { r.cold.error = first(e); }
    r.errors = (r.errors || []).concat(rr.errors || []);
    r.consoleErrors = (r.consoleErrors || []).concat(rr.consoleErrors || []);
  });

  // 12l. Closing the inspector of a node that left the floor puts focus on its nearest surviving ancestor -------------
  await step('refocus', async (r) => {
    await withPage({}, r, async (page) => {
      await waitFx(page, 32); await settle(page, 4000);
      await clickNode(page, X.gone.id);
      r.openMs = await waitInsp(page, 3000);
      await setPoll('2');
      r.goneMs = await page.evaluate((id) => __t.until(() => !__t.q(id), 9000), X.gone.id);
      r.inspGone = await page.evaluate(() => __t.insp().text.slice(0, 60));
      await page.focus('aside.fx-inspector .fx-insp-close');
      r.before = await page.evaluate(() => document.activeElement.className);
      await page.keyboard.press('Escape');
      await page.waitForTimeout(300);
      r.after = await page.evaluate(() => { const a = document.activeElement; return { tag: a.tagName, id: (a.dataset && a.dataset.id) || null, cls: String(a.className).slice(0, 40) }; });
    });
  });

  // 12m. A phone's list keeps the state word in each row's accessible name ---------------------------------------------
  await step('phonelist', async (r) => {
    await withPage({ w: 390, h: 844 }, r, async (page) => {
      await waitFx(page, 32); await settle(page, 4000);
      await page.click('.fx-mode button[data-mode="list"]');
      await page.waitForTimeout(400);
      r.named = await page.getByRole('button', { name: /cc_tool.*needs you/ }).count();
      r.word = await page.evaluate(() => { const w = document.querySelector('button.fx-li .fx-li-word'), b = w.getBoundingClientRect(); return { text: w.textContent, w: b.width, h: b.height, display: getComputedStyle(w).display }; });
      r.scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    });
  });

  // 12n. Robustness: an order with detail null, a duplicate id, string costs --------------------------------------------
  await step('robust', async (r) => {
    await withPage({ poll: 'robust' }, r, async (page) => {
      await waitFx(page, X.robust.n); await settle(page, 4000);
      r.faces = await page.evaluate((R) => ({ junk: __t.face(R.junk), str: __t.face(R.str), dup: __t.face(R.dup), order: __t.face(R.order), n: __t.ids().length,
        dupCount: document.querySelectorAll('.gx-node[data-id=' + JSON.stringify(R.dup) + ']').length }), X.robust);
      await clickNode(page, X.robust.reports);
      r.openMs = await waitInsp(page, 3000);
      r.insp = await page.evaluate(() => __t.insp().text.slice(0, 600));
      await page.keyboard.press('Escape');
      await page.click('.fx-mode button[data-mode="list"]');
      await page.waitForTimeout(400);
      r.items = await page.evaluate(() => [...document.querySelectorAll('button.fx-li[data-node]')].map((b) => b.dataset.node));
      r.dupRow = await page.evaluate((id) => { const b = document.querySelector('button.fx-li[data-node=' + JSON.stringify(id) + ']'); return b ? b.textContent : null; }, X.robust.dup);
    });
  });

  // 12o. Short viewports: the stage and its zoom buttons fit above the fold --------------------------------------------
  await step('shortview', async (r) => {
    r.cases = {};
    for (const [w, h] of [[1366, 650], [375, 667], [1440, 900]]) {
      const c = r.cases[w + 'x' + h] = {};
      const rr = {};
      try {
        await withPage({ w, h }, rr, async (page) => {
          await waitFx(page, 32); await settle(page, 4000);
          Object.assign(c, await page.evaluate(() => {
            const st = document.querySelector('.fx-stage').getBoundingClientRect(), z = document.querySelector('.fx-zoom').getBoundingClientRect();
            return { inner: innerHeight, scroll: document.documentElement.scrollHeight, stageTop: st.top, stageBottom: st.bottom, stageH: st.height, zoomBottom: z.bottom };
          }));
        });
      } catch (e) { c.error = first(e); }
      c.errors = rr.errors; c.consoleErrors = rr.consoleErrors;
    }
  });

  // 12c. Control mode on the floor: Review in the strip and the inspector, and the review dialog -----------------
  const K = X.ctl;
  const stripRv = (page) => page.evaluate((n) => __t.until(() => __t.strip(n).rv, 6000), K.b3Node);
  const openReview = async (page, sel) => {
    await page.click(sel);
    const ms = await page.evaluate(() => __t.until(() => __t.dlg().open && __t.dlg().req, 3000));
    await page.waitForTimeout(800);                                        // the card's buttons arm after 700 ms
    return ms;
  };
  const STRIP_RV = '.fx-alert-w:has(> .fx-alert[data-node="' + K.b3Node + '"]) [data-ctl-act="open"]';
  await step('ctlreview', async (r) => {
    await withPage({ ctl: 'on', alerts: false }, r, async (page) => {
      await waitFx(page, 32); await settle(page, 4000);
      // (a) the strip item of B3 (the permission alert) shows Review beside its alert button
      r.stripMs = await stripRv(page);
      r.strip = await page.evaluate((n) => __t.strip(n), K.b3Node);
      r.ctlOn = await page.evaluate(() => document.documentElement.classList.contains('ctl-on'));
      // the other request (S1's question) is S1's own: its inspector says Answer; an agent with none offers nothing
      await clickNode(page, K.s1Node); await waitInsp(page, 3000);
      await page.evaluate(() => __t.until(() => document.querySelector('aside.fx-inspector [data-ctl-act]'), 3000));
      r.s1 = await page.evaluate(() => { const b = document.querySelector('aside.fx-inspector [data-ctl-act]'); return { title: __t.insp().title, rv: b ? { label: __t.txt(b), req: b.dataset.ctlReq } : null }; });
      await clickNode(page, K.a91); await waitInsp(page, 3000); await page.waitForTimeout(300);
      r.a91 = await page.evaluate(() => ({ title: __t.insp().title, n: document.querySelectorAll('aside.fx-inspector [data-ctl-act]').length }));
      // (b) B3's inspector, opened from its alert: Review in the "needs" box, with the request's own summary
      await page.click('.fx-alert[data-node="' + K.b3Node + '"]');
      await waitInsp(page, 3000);
      await page.evaluate(() => __t.until(() => document.querySelector('aside.fx-inspector .fx-now [data-ctl-act]'), 3000));
      r.insp = await page.evaluate(() => { const b = document.querySelector('aside.fx-inspector .fx-now [data-ctl-act]'), box = document.querySelector('aside.fx-inspector .fx-now');
        return { title: __t.insp().title, rv: b ? { label: __t.txt(b), req: b.dataset.ctlReq, href: b.dataset.ctlHref } : null, box: __t.txt(box) }; });
      // (c) Review opens the review dialog with the whole card
      r.openMs = await openReview(page, STRIP_RV);
      r.d1 = await page.evaluate(() => __t.dlg());
      r.posts0 = (await ctlPosts()).length;
      await page.locator('dialog.ctl-review .ctl-card > .ctl-acts > button').filter({ hasText: /^Approve$/ }).click({ timeout: 3000 });
      await page.waitForTimeout(150);
      r.d2 = await page.evaluate(() => __t.dlg());                            // the typed confirm, Run it dead
      await page.keyboard.type('appr');
      r.d3 = await page.evaluate(() => __t.dlg());
      r.posts1 = (await ctlPosts()).length;
      await page.keyboard.press('Escape');                                   // the sub-form closes first
      await page.waitForTimeout(150);
      r.d4 = await page.evaluate(() => __t.dlg());
      await page.locator('dialog.ctl-review .ctl-card > .ctl-acts > button').filter({ hasText: /^Approve$/ }).click({ timeout: 3000 });
      await page.waitForTimeout(100);
      r.d4b = await page.evaluate(() => __t.dlg());                           // opened again: the field asks for the whole word again
      await page.keyboard.type('approve');
      await page.waitForTimeout(100);
      r.d5 = await page.evaluate(() => __t.dlg());                            // Run it live, still nothing sent
      r.posts2 = (await ctlPosts()).length;
      // (d) Escape (the sub-form), Escape (the dialog): focus back on the strip's Review, the inspector still open
      await page.keyboard.press('Escape'); await page.waitForTimeout(100);
      await page.keyboard.press('Escape');
      r.closeMs = await page.evaluate(() => __t.until(() => !__t.dlg().open, 2000));
      await page.waitForTimeout(250);
      r.d6 = await page.evaluate(() => Object.assign(__t.dlg(), { inspOpen: __t.insp().visible }));
      // the keyboard path: Enter on the inspector's Review; the page's own keys do nothing inside; y y works
      await page.focus('aside.fx-inspector .fx-now [data-ctl-act]');
      await page.keyboard.press('Enter');
      r.enterMs = await page.evaluate(() => __t.until(() => __t.dlg().open, 2000));
      await page.waitForTimeout(800);
      r.d7 = await page.evaluate(() => __t.dlg());
      const before = await page.evaluate(() => ({ theme: document.documentElement.dataset.theme || '', hash: location.hash, cam: __t.clone(__t.g().camera()) }));
      for (const k of ['a', 't', '?', 'j', '0', '-']) { await page.keyboard.press(k); await page.waitForTimeout(80); }
      await page.waitForTimeout(400);
      r.keys = await page.evaluate((b) => { const c = __t.g().camera();
        return { themeSame: (document.documentElement.dataset.theme || '') === b.theme, hashSame: location.hash === b.hash,
          camSame: Math.abs(c.k - b.cam.k) < 1e-6 && Math.abs(c.x - b.cam.x) < 0.01 && Math.abs(c.y - b.cam.y) < 0.01,
          drawer: !(document.getElementById('alert-drawer') || { hidden: true }).hidden, help: !!(document.getElementById('help') || {}).open, dlg: __t.dlg() }; }, before);
      await page.evaluate(() => { const c = document.querySelector('dialog.ctl-review .ctl-card'); if (c) c.focus(); });
      await page.keyboard.press('y'); await page.waitForTimeout(100);
      r.y1 = await page.evaluate(() => __t.dlg());
      await page.keyboard.press('y'); await page.waitForTimeout(150);
      r.y2 = await page.evaluate(() => __t.dlg());                            // the flagged command: y y opens the typed confirm, never runs it
      r.posts3 = (await ctlPosts()).length;
      await page.keyboard.type('approve');
      await page.locator('dialog.ctl-review .ctl-confirm button').filter({ hasText: /^Run it$/ }).click({ timeout: 3000 });
      r.postMs = await page.evaluate(async () => { const t0 = performance.now(); while (performance.now() - t0 < 3000) { const p = await (await fetch('/__test/ctl')).json(); if (p.posts.length) return performance.now() - t0; await __t.wait(50); } return -1; });
      r.posts = await ctlPosts();
      r.sentMs = await page.evaluate(() => __t.until(() => /sent/.test(__t.dlg().status), 3000));
      r.d8 = await page.evaluate(() => __t.dlg());
      r.goneMs = await page.evaluate((n) => __t.until(() => !__t.strip(n).rv && !document.querySelector('aside.fx-inspector [data-ctl-act]'), 4000), K.b3Node);
      await page.keyboard.press('Escape');
      await page.evaluate(() => __t.until(() => !__t.dlg().open, 2000));
      await page.waitForTimeout(250);
      r.d9 = await page.evaluate(() => __t.dlg());                            // its button went away: focus falls back, never to <body>
    });
  });

  // (e) Without control mode nothing on the floor can act; with it but before the control code, Review shows the code field
  await step('ctloff', async (r) => {
    await withPage({}, r, async (page) => {
      await waitFx(page, 32); await settle(page, 4000); await page.waitForTimeout(1500);
      await page.click('.fx-alert[data-node="' + K.b3Node + '"]'); await waitInsp(page, 3000); await page.waitForTimeout(400);
      r.off = await page.evaluate(() => ({ acts: document.querySelectorAll('[data-ctl-act]').length, ctlOn: document.documentElement.classList.contains('ctl-on'),
        box: __t.txt(document.querySelector('aside.fx-inspector .fx-now')), dialog: !!document.querySelector('dialog.ctl-review') }));
    });
  });
  await step('ctllocked', async (r) => {
    await withPage({ ctl: 'read', alerts: false }, r, async (page) => {
      await waitFx(page, 32); await settle(page, 4000);
      r.stripMs = await stripRv(page);
      r.strip = await page.evaluate((n) => __t.strip(n), K.b3Node);
      r.openMs = await openReview(page, STRIP_RV);
      r.d = await page.evaluate(() => Object.assign(__t.dlg(), { locked: document.documentElement.classList.contains('ctl-locked') }));
      await page.keyboard.press('y'); await page.keyboard.press('y'); await page.waitForTimeout(150);
      r.afterY = await page.evaluate(() => ({ active: document.activeElement && document.activeElement.classList.contains('ctl-code') }));
      r.posts = (await ctlPosts()).length;
    });
  });

  // (f) Toasts on the floor: never over the zoom buttons or an open inspector; nothing on the floor moves for one
  await step('toasts', async (r) => {
    r.cases = {};
    for (const [w, h] of [[1440, 900], [390, 844]]) {
      const c = r.cases[w + 'x' + h] = {};
      const rr = {};
      try {
        await withPage({ ctl: 'on', alerts: false, w, h }, rr, async (page) => {
          await waitFx(page, 32); await settle(page, 4000); await settleCam(page);
          await page.waitForTimeout(600);
          c.before = await page.evaluate(() => __t.glass());
          await setCtl(null, true);                                        // the alert opens: alerts.js shows its toast
          c.toastMs = await page.evaluate(() => __t.until(() => __t.glass().toasts.length > 0, 6000));
          await page.waitForTimeout(700);                                    // the stack glides into place
          c.shown = await page.evaluate(() => __t.glass());
          await page.click('.fx-alert[data-node="' + K.b3Node + '"]');        // the inspector opens (a side panel, or the phone's sheet)
          await waitInsp(page, 3000); await page.waitForTimeout(900);
          c.open = await page.evaluate(() => __t.glass());
          await page.keyboard.press('Escape');
          await page.evaluate(() => __t.until(() => !__t.insp().visible, 2000)); await page.waitForTimeout(700);
          c.closed = await page.evaluate(() => __t.glass());
          await page.evaluate(() => { location.hash = '#/overview'; });
          await page.waitForFunction(() => !document.querySelector('section.fx'), null, { timeout: 5000 }).catch(() => {});
          await page.waitForTimeout(300);
          c.left = await page.evaluate(() => __t.glass());
        });
      } catch (e) { c.error = first(e); }
      c.errors = rr.errors; c.consoleErrors = rr.consoleErrors;
    }
  });

  // Burn rates: dollars a minute, else new tokens a minute, never the total that counts cache reads
  await step('rates', async (r) => {
    await withPage({ poll: 'rates' }, r, async (page) => {
      await waitFx(page, 32); await settle(page, 4000);
      r.faces = await page.evaluate(() => [...document.querySelectorAll('.fx-rate')].map((x) => __t.txt(x)));
      r.s6 = await page.evaluate((id) => { const f = __t.q(id); const x = f && f.querySelector('.fx-rate'); return x ? __t.txt(x) : null; }, X.ids.s6);
      r.hud = await page.evaluate(() => { const k = document.querySelector('.fx-kpi[data-k="burn"]'); return k ? { text: __t.txt(k.querySelector('.fx-kpi-v')), title: k.title } : null; });
      r.sub = await page.evaluate(() => __t.txt(document.querySelector('.fx-sub')));
      for (const [k, id] of [['s6i', X.ids.s6], ['a91i', K.a91]]) {
        await clickNode(page, id); await waitInsp(page, 3000); await page.waitForTimeout(300);
        r[k] = await page.evaluate(() => { const row = [...document.querySelectorAll('aside.fx-inspector .fx-row')].find((x) => x.dataset.key === 'Burn');
          const v = row && row.querySelector('.fx-row-v > span'); return row ? { text: __t.txt(row.querySelector('.fx-row-v')), title: v ? v.title : '' } : null; });
      }
    });
  });

  // 13. The landing route -------------------------------------------------------------------------------------------
  await step('landing', async (r) => {
    await withPage({ hash: '#/overview' }, r, async (page) => {
      await page.waitForSelector('#main > *', { timeout: 8000 });
      r.fromNoFx = await page.evaluate(() => !document.querySelector('section.fx'));
      r.nav = await page.evaluate(() => [...document.querySelectorAll('.tabs a')].filter((a) => __t.vis(a)).map((a) => __t.txt(a)));
      await page.click('a.brand');
      await page.waitForSelector('section.fx', { timeout: 8000 });
      r.brandHash = await page.evaluate(() => location.hash);
      r.brandCurrent = await page.evaluate(() => [...document.querySelectorAll('.tabs a[aria-current="page"]')].map((a) => a.getAttribute('href')));
      await page.goto(BASE + '/', { waitUntil: 'load' });                   // no hash at all
      await page.waitForSelector('section.fx', { timeout: 8000 });
      r.rootHash = await page.evaluate(() => location.hash);
      r.firstTab = await page.evaluate(() => { const a = document.querySelector('.tabs a'); return a ? { href: a.getAttribute('href'), current: a.getAttribute('aria-current') } : null; });
      // The old Live route goes to the Factory now
      await page.evaluate(() => { location.hash = '#/overview'; });
      await page.waitForFunction(() => !document.querySelector('section.fx'), null, { timeout: 5000 }).catch(() => {});
      await page.evaluate(() => { location.hash = '#/live'; });
      r.liveMs = await page.evaluate(() => __t.until(() => location.hash.indexOf('#/factory') === 0 && document.querySelector('section.fx'), 5000));
      r.liveHash = await page.evaluate(() => location.hash);
    });
  });

  // 13. The activity column (recent actions + three last-hour charts), Lead U ------------------------------------------------
  const nodesUnder = (page, sel) => page.evaluate((sel) => {
    const s = document.querySelector(sel);
    if (!s || !__t.vis(s)) return null;
    const a = s.getBoundingClientRect();
    return [...document.querySelectorAll('.gx-node[data-id]')].filter((n) => { const b = n.getBoundingClientRect(); return b.width > 0 && b.right > a.left + 0.5 && b.left < a.right - 0.5 && b.bottom > a.top + 0.5 && b.top < a.bottom - 0.5; }).map((n) => n.dataset.id);
  }, sel);
  const sideShown = (page, ms) => page.evaluate((ms) => __t.until(() => __t.vis(document.getElementById('fx-side')), ms), ms || 2000);
  const sideGone = (page, ms) => page.evaluate((ms) => __t.until(() => !__t.vis(document.getElementById('fx-side')), ms), ms || 2000);

  // 13a. Desktop: the column by default, the feed rows, a row click, the inspector in the same slot, nothing under either
  await step('side', async (r) => {
    r.vp = {};
    for (const [w, h] of [[1440, 900], [1920, 1080]]) {
      const v = r.vp[w + 'x' + h] = {};
      const rr = {};
      try {
        await withPage({ w, h }, rr, async (page) => {
          await waitFx(page, 32); await settle(page, 4000); await page.waitForTimeout(800);
          v.s0 = await page.evaluate(() => __t.side());
          v.rows = await page.evaluate(() => __t.rows());
          v.noTicker = await page.evaluate(() => !document.querySelector('.fx-ticker, .fx-tick'));
          v.order = await page.evaluate(() => [...document.querySelectorAll('#fx-side > section')].map((x) => x.className.split(' ')[0]));
          v.labelled = await page.evaluate(() => { const a = document.getElementById('fx-side'); return { tag: a && a.tagName, label: a && a.getAttribute('aria-label'), list: (document.querySelector('.fx-evs') || { getAttribute: () => null }).getAttribute('role') }; });
          await page.click('.fx-zbtn[data-zoom="fit"]'); await settleCam(page);
          v.under0 = await nodesUnder(page, '#fx-side');
          // a row click focuses its node: the inspector takes the slot, the column leaves
          v.want = (v.rows[0] || {}).node;
          await page.click('.fx-ev-w:first-child .fx-ev');
          v.openMs = await waitInsp(page, 3000);
          await page.waitForTimeout(350);
          v.afterClick = await page.evaluate(() => ({ sel: __t.g().selected(), insp: __t.insp(), side: __t.side() }));
          await page.click('.fx-zbtn[data-zoom="fit"]'); await settleCam(page);
          v.under1 = await nodesUnder(page, 'aside.fx-inspector');
          await page.keyboard.press('Escape');
          v.backMs = await sideShown(page, 2000);
          await page.waitForTimeout(400);
          v.afterEsc = await page.evaluate(() => ({ insp: __t.insp().visible, side: __t.side(), sel: __t.g().selected() }));
        });
      } catch (e) { v.error = first(e); }
      v.errors = rr.errors; v.consoleErrors = rr.consoleErrors;
    }
  });

  // 13b. The charts: structure, hover and keyboard tooltips, the current value
  await step('charts', async (r) => {
    await withPage({}, r, async (page) => {
      await waitFx(page, 32); await settle(page, 4000); await page.waitForTimeout(1500);
      r.charts = await page.evaluate(() => __t.charts());
      r.vh = await page.evaluate(() => [...document.querySelectorAll('.fx-chart')].map((c) => ({ k: c.dataset.k, hasTable: !!c.querySelector('table.vh') })).concat([{ k: 'hour', hasTable: !!document.querySelector('.fx-hour table.vh'), rows: document.querySelectorAll('.fx-hour table.vh tr').length }]));
      r.starts = await page.evaluate(() => window.__starts.length);
      r.tips = {};
      for (const kind of ['tokens', 'tools', 'agents']) {
        for (const i of X.series.ks) {
          const t = r.tips[kind + i] = {};
          try {
            await page.hover('.fx-chart[data-k=' + kind + '] rect.hit[data-i="' + i + '"]');
            await page.waitForTimeout(150);
            t.tip = await page.evaluate((i) => ({ info: __t.tipInfo(), titles: __t.titles(i) }), i);
            await page.mouse.move(4, 4);
            await page.waitForTimeout(80);
            t.hidden = await page.evaluate(() => !__t.tipInfo());
          } catch (e) { t.error = first(e); }
        }
      }
      // keyboard: End -> the last bucket, ArrowLeft -> the one before, Home -> the first, ArrowRight -> the second; blur hides
      r.kb = {};
      for (const kind of ['tokens', 'tools', 'agents']) {
        const k = r.kb[kind] = {};
        try {
          await page.focus('.fx-chart[data-k=' + kind + '] svg');
          const read = async (i) => { await page.waitForTimeout(100); return page.evaluate((i) => ({ info: __t.tipInfo(), titles: __t.titles(i) }), i); };
          await page.keyboard.press('End'); k.end = await read(19);
          await page.keyboard.press('ArrowLeft'); k.left = await read(18);
          await page.keyboard.press('Home'); k.home = await read(0);
          await page.keyboard.press('ArrowRight'); k.right = await read(1);
          await page.evaluate(() => document.activeElement.blur());
          await page.waitForTimeout(150);
          k.hiddenAfterBlur = await page.evaluate(() => !__t.tipInfo());
        } catch (e) { k.error = first(e); }
      }
    });
  });

  // 13b'. The column's split of a short slot: the list gives up whole rows (the next one peeking)
  // before the plots get small; on a tall one it fills the slot; the column scrolls only as a last resort.
  await step('colfit', async (r) => {
    r.vp = {};
    for (const [w, h] of [[1366, 768], [1440, 900], [1920, 1080], [1280, 720], [1366, 650]]) {
      const v = r.vp[w + 'x' + h] = {};
      try {
        await withPage({ w, h }, v, async (page) => {
          await waitFx(page, 32); await settle(page, 4000); await page.waitForTimeout(1200);
          Object.assign(v, await page.evaluate(() => {
            const side = document.getElementById('fx-side');
            if (!__t.vis(side)) return { visible: false };
            const evs = side.querySelector('.fx-evs'), hour = side.querySelector('.fx-hour'), eb = evs.getBoundingClientRect();
            const rows = [...evs.querySelectorAll('.fx-ev-w')];
            return { visible: true, over: side.classList.contains('over'), scroll: [side.scrollHeight, side.clientHeight],
              sideBottom: side.getBoundingClientRect().bottom, hourBottom: hour.getBoundingClientRect().bottom,
              plots: [...side.querySelectorAll('.fx-chart')].map((c) => c.__ph), svgs: [...side.querySelectorAll('.fx-chart svg')].map((x) => x.getBoundingClientRect().height),
              n: rows.length, whole: rows.filter((x) => { const b = x.getBoundingClientRect(); return b.top >= eb.top - 0.5 && b.bottom <= eb.bottom + 0.5; }).length,
              rowH: rows.length ? rows[0].getBoundingClientRect().height : 0, listH: evs.clientHeight, more: evs.hasAttribute('data-more') };
          }));
        });
      } catch (e) { v.error = first(e); }
    }
  });

  // 13c. The toggle at 1440, with a node selected
  await step('sidebtn', async (r) => {
    await withPage({}, r, async (page) => {
      await waitFx(page, 32); await settle(page, 4000); await page.waitForTimeout(800);
      r.s0 = await page.evaluate(() => __t.side());
      await page.click('.fx-side-btn');
      r.hideMs = await sideGone(page, 2000);
      await page.waitForTimeout(500);
      r.s1 = await page.evaluate(() => __t.side());
      await page.click('.fx-side-btn');
      r.showMs = await sideShown(page, 2000);
      await page.waitForTimeout(500);
      r.s2 = await page.evaluate(() => __t.side());
      await clickNode(page, X.ids.agent);
      r.inspMs = await waitInsp(page, 3000);
      await page.waitForTimeout(300);
      r.s3 = await page.evaluate(() => ({ side: __t.side(), insp: __t.insp().visible }));
      await page.click('.fx-side-btn');
      r.backMs = await sideShown(page, 2000);
      await page.waitForTimeout(500);
      r.s4 = await page.evaluate(() => ({ side: __t.side(), insp: __t.insp().visible, sel: __t.g().selected() }));
    });
  });

  // 13d. Phone: hidden by default, a bottom sheet when pressed, the totals step aside, a selected node replaces it
  await step('sidephone', async (r) => {
    await withPage({ w: 390, h: 844 }, r, async (page) => {
      await waitFx(page, 32); await settle(page, 4000); await page.waitForTimeout(900);
      const scroll = () => page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth, bsw: document.body.scrollWidth }));
      r.s0 = await page.evaluate(() => ({ side: __t.side(), hud: __t.vis(document.querySelector('.fx-hud')) }));
      r.sc0 = await scroll();
      await page.click('.fx-side-btn');
      r.showMs = await sideShown(page, 2000);
      await page.waitForTimeout(600);
      r.s1 = await page.evaluate(() => ({ side: __t.side(), hud: __t.vis(document.querySelector('.fx-hud')), rows: __t.rows().length, charts: __t.charts().length }));
      r.sc1 = await scroll();
      try { await page.click('.fx-alert', { timeout: 3000 }); } catch (e) { await page.evaluate(() => { const b = document.querySelector('.fx-alert'); if (b) b.click(); }); }
      r.inspMs = await waitInsp(page, 3000);
      await page.waitForTimeout(500);
      r.s2 = await page.evaluate(() => ({ side: __t.side(), insp: __t.insp(), hud: __t.vis(document.querySelector('.fx-hud')) }));
      r.sc2 = await scroll();
    });
  });

  // 13e. Glass in both themes
  await step('sidetheme', async (r) => {
    r.v = {};
    for (const scheme of ['light', 'dark']) {
      const v = r.v[scheme] = {};
      const rr = {};
      try {
        await withPage({ scheme }, rr, async (page) => {
          await waitFx(page, 32); await settle(page, 4000); await page.waitForTimeout(800);
          Object.assign(v, await page.evaluate(() => {
            const alpha = (c) => {
              let m = /^rgba?\(([^)]+)\)/.exec(c);
              if (m) { const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return p.length > 3 ? p[3] : 1; }
              m = /^color\(.*?\/\s*([\d.]+)\s*\)/.exec(c);
              if (m) return Number(m[1]);
              return /^color\(/.test(c) || /^rgb/.test(c) ? 1 : null;
            };
            const o = {};
            for (const sel of ['.fx-acts', '.fx-hour']) {
              const e = document.querySelector(sel), cs = e && getComputedStyle(e);
              o[sel] = e ? { filter: cs.backdropFilter || cs.webkitBackdropFilter || 'none', bg: cs.backgroundColor, alpha: alpha(cs.backgroundColor), vis: __t.vis(e) } : null;
            }
            return o;
          }));
        });
      } catch (e) { v.error = first(e); }
      v.errors = rr.errors; v.consoleErrors = rr.consoleErrors;
    }
  });

  // 13f. Reduced motion: poll 2 swaps the feed with no entrance, and the column neither transitions nor animates
  await step('sidereduced', async (r) => {
    await withPage({ reduced: true }, r, async (page) => {
      await waitFx(page, 32); await settle(page, 4000); await page.waitForTimeout(800);
      await page.evaluate(() => {
        window.__enter = 0;
        const side = document.getElementById('fx-side');
        new MutationObserver((ms) => {
          for (const m of ms) {
            if (m.type === 'childList') m.addedNodes.forEach((n) => { if (n.nodeType === 1 && (n.matches('.enter') || n.querySelector('.enter'))) window.__enter++; });
            else if (m.target.classList && m.target.classList.contains('enter')) window.__enter++;
          }
        }).observe(side, { subtree: true, childList: true, attributes: true, attributeFilter: ['class'] });
      });
      r.first0 = await page.evaluate(() => __t.rows().map((x) => x.key));
      await setPoll('2');
      r.swapMs = await page.evaluate((k) => __t.until(() => __t.rows()[0] && __t.rows()[0].key === k, 9000), X.poll2.feed8[0].key);
      await page.waitForTimeout(900);
      r.after = await page.evaluate(() => {
        const side = document.getElementById('fx-side'), cs = getComputedStyle(side);
        const props = cs.transitionProperty.split(',').map((x) => x.trim()), durs = cs.transitionDuration.split(',').map((x) => parseFloat(x));
        const anims = document.getAnimations().filter((a) => a.effect && a.effect.target && side.contains(a.effect.target));
        return { enterNow: document.querySelectorAll('#fx-side .enter').length, enterSeen: window.__enter, rows: __t.rows().length, reduce: matchMedia('(prefers-reduced-motion: reduce)').matches,
          transform: props.some((p, i) => (p === 'transform' || p === 'all') && durs[i % durs.length] > 0), transition: cs.transitionProperty + ' ' + cs.transitionDuration,
          animName: cs.animationName, running: anims.filter((a) => a.playState === 'running').map((a) => a.constructor.name + ':' + (a.animationName || a.transitionProperty || '')) };
      });
    });
  });

  // 13g. Empty and odd payloads: the empty feed, a zero series, junk in the series, no series at all
  await step('sidestates', async (r) => {
    r.v = {};
    for (const [poll, n] of [['empty', 0], ['robust', X.robust.n], ['noseries', X.poll1.ids.length]]) {
      const v = r.v[poll] = {};
      const rr = {};
      try {
        await withPage({ poll }, rr, async (page) => {
          if (n) await waitFx(page, n); else await page.waitForSelector('section.fx', { timeout: 9000 });
          await page.waitForTimeout(1800);
          v.side = await page.evaluate(() => __t.side());
          v.charts = await page.evaluate(() => __t.charts());
          v.rows = await page.evaluate(() => __t.rows().length);
          v.none = await page.evaluate(() => { const e = document.querySelector('.fx-evs-none'); return { exists: !!e, visible: __t.vis(e), text: __t.txt(e) }; });
          try {
            await page.hover('.fx-chart[data-k=tokens] rect.hit[data-i="5"]');
            await page.waitForTimeout(150);
            v.tip = await page.evaluate(() => __t.tipInfo());
          } catch (e) { v.tipError = first(e); }
        });
      } catch (e) { v.error = first(e); }
      v.errors = rr.errors; v.consoleErrors = rr.consoleErrors;
    }
  });

  // 13c. The floor's extra visuals: far-zoom status weight, time in state, rework ----------------------------------------
  // Read up close (near) and zoomed out (far), then a same-ids poll (`visuals`) that moves every field they read;
  // then the time counting on between polls; then reduced motion.
  const visRead = (page) => page.evaluate((I) => {
    const lod = document.querySelector('.gx').dataset.lod, now = Date.now() / 1000;
    const since = {};
    for (const el of document.querySelectorAll('.gx-node[data-id] .fx-since')) {
      const t = Number(el.dataset.t), cs = getComputedStyle(el);
      since[el.closest('.gx-node').dataset.id] = { hidden: el.hidden, shown: __t.vis(el), drop: el.dataset.drop || '', text: el.lastChild.textContent, age: now - t,
        aria: el.getAttribute('aria-hidden'), anims: el.getAnimations().length, trans: cs.transitionDuration };
    }
    const rework = {};
    for (const el of document.querySelectorAll('.gx-node[data-id] .fx-rework')) {
      rework[el.closest('.gx-node').dataset.id] = { text: el.textContent, shown: __t.vis(el), aria: el.getAttribute('aria-hidden'), trans: getComputedStyle(el).transitionDuration };
    }
    const rgb = (c) => { const m = String(c).match(/[\d.]+/g) || []; const k = /^color\(/.test(c) ? 255 : 1; return m.slice(0, 3).map((x) => +x * k); };
    const ring = (id) => {
      const f = __t.q(id) && __t.q(id).querySelector('.fx-node');
      if (!f) return null;
      const cs = getComputedStyle(f), [r, g, b] = rgb(cs.outlineColor);
      return { style: cs.outlineStyle, width: parseFloat(cs.outlineWidth), red: r > g + 60 && r > b + 60, amber: r > 180 && g > 120 && b < 90, trans: cs.transitionProperty };
    };
    const halo = (id) => { const h = __t.q(id) && __t.q(id).querySelector('.fx-halo'); if (!h) return null; const cs = getComputedStyle(h); return { top: parseFloat(cs.top), anims: h.getAnimations().length }; };
    const moving = document.getAnimations().filter((a) => a.playState === 'running' && a.effect && a.effect.target && a.effect.target.closest && a.effect.target.closest('.gx-world')).length;
    return { lod, since, rework, failed: ring(I.failed), stalled: ring(I.stalled), working: ring(I.session), a91: ring(I.agent), halo: halo(I.b3), moving,
      pos: __t.clone(__t.g().positions()), cam: __t.clone(__t.g().camera()) };
  }, X.ids);
  const visZoom = (page, k) => page.evaluate(([id, k]) => { __t.g().center(id, { zoom: k, animate: false }); }, [X.ids.session, k]);
  await step('visuals', async (r) => {
    await withPage({}, r, async (page) => {
      await waitFx(page, 32); await settle(page, 4000);
      await visZoom(page, 1); await page.waitForTimeout(250);
      r.near = await visRead(page);
      await visZoom(page, 0.3); await page.waitForTimeout(250);
      r.far = await visRead(page);
      await page.evaluate(() => { for (const el of document.querySelectorAll('.gx-node[data-id]')) { el.__visTag = el.dataset.id; const f = el.querySelector('.fx-node'); if (f) f.__visTag = el.dataset.id; } });
      await setPoll('visuals');
      r.pollMs = await page.evaluate((id) => __t.until(() => /(^| )c-failed( |$)/.test((__t.face(id) || {}).cls || ''), 9000), X.ids.agent);
      await page.waitForTimeout(500);
      r.farAfter = await visRead(page);
      r.replaced = await page.evaluate(() => [...document.querySelectorAll('.gx-node[data-id]')].filter((e) => e.__visTag !== e.dataset.id).map((e) => e.dataset.id));
      await visZoom(page, 1); await page.waitForTimeout(250);
      r.nearAfter = await visRead(page);
      // Between polls: a node that crossed its first minute shows it within the 5 s tick, and its face is not rebuilt.
      r.tick = await page.evaluate(async (id) => {
        const el = __t.q(id), sp = el && el.querySelector('.fx-since'), f = el && el.querySelector('.fx-node');
        if (!sp) return { missing: true };
        const before = { hidden: sp.hidden, text: sp.lastChild.textContent };
        sp.dataset.t = String(Date.now() / 1000 - 61);
        const ms = await __t.until(() => !sp.hidden && sp.lastChild.textContent === '1m', 6500);
        return { before, ms, after: { hidden: sp.hidden, text: sp.lastChild.textContent }, sameFace: el.querySelector('.fx-node') === f && f.__visTag === id, sameSpan: el.querySelector('.fx-since') === sp };
      }, X.ids.e9);
    });
    await withPage({ reduced: true }, r, async (page) => {
      await waitFx(page, 32); await settle(page, 4000);
      await visZoom(page, 0.3); await page.waitForTimeout(400);
      r.reducedFar = await visRead(page);
      await visZoom(page, 1); await page.waitForTimeout(400);
      r.reducedNear = await visRead(page);
    });
  });

  // 14. Screenshots (CC_DASH_SHOTS) -----------------------------------------------------------------------------------
  // The selected shots open the floor's edge node in place (the inspector re-frames it); then the 300-node
  // floor in both themes, long hub names up close, and the empty floor.
  await step('shots', async (r) => {
    if (!SHOTS) { r.skipped = true; return; }
    r.files = [];
    const shot = async (page, name) => { const f = SHOTS + '/' + name + '.png'; await page.screenshot({ path: f }); r.files.push(f); };
    const run = async (o, fn) => { const rr = {}; try { await withPage(o, rr, fn); } catch (e) { r.files.push(JSON.stringify(o) + ': ' + first(e)); } };
    for (const scheme of ['light', 'dark']) {
      for (const [w, h] of [[1440, 900], [390, 844]]) {
        await run({ scheme, w, h }, async (page) => {
          await waitFx(page, 32); await settle(page, 4000); await page.waitForTimeout(1800);
          await shot(page, 'factory-' + w + 'x' + h + '-' + scheme);
          try { await clickInPlace(page, await edgeNode(page, w < 600)); await waitInsp(page, 3000); await page.waitForTimeout(400); await settleCam(page); await page.waitForTimeout(500); }
          catch (e) { r.files.push('select failed: ' + first(e)); }
          await shot(page, 'factory-' + w + 'x' + h + '-' + scheme + '-selected');
        });
      }
      await run({ scheme, w: 1920, h: 1080 }, async (page) => {
        await waitFx(page, 32); await settle(page, 4000); await page.waitForTimeout(1800);
        await shot(page, 'factory-1920x1080-' + scheme);
      });
      await run({ scheme, w: 390, h: 844 }, async (page) => {            // the activity sheet open on a phone
        await waitFx(page, 32); await settle(page, 4000); await page.waitForTimeout(1200);
        await page.click('.fx-side-btn'); await sideShown(page, 2000); await page.waitForTimeout(700);
        await shot(page, 'factory-390x844-' + scheme + '-column');
      });
      // Control mode: Review in the strip (with the toast clear of the zoom buttons), the inspector with
      // Review, and the review dialog with the typed confirm open
      for (const [w, h] of [[1440, 900], [390, 844]]) {
        await run({ scheme, w, h, ctl: 'on' }, async (page) => {
          const tag = 'factory-ctl-' + w + 'x' + h + '-' + scheme;
          await waitFx(page, 32); await settle(page, 4000);
          await stripRv(page);
          await page.evaluate(() => __t.until(() => __t.glass().toasts.length > 0, 6000)); await page.waitForTimeout(1200);
          await shot(page, tag + '-strip');
          await page.click('.fx-alert[data-node="' + K.b3Node + '"]'); await waitInsp(page, 3000); await page.waitForTimeout(400); await settleCam(page); await page.waitForTimeout(500);
          await shot(page, tag + '-inspector');
          await openReview(page, 'aside.fx-inspector .fx-now [data-ctl-act="open"]');
          await page.locator('dialog.ctl-review .ctl-card > .ctl-acts > button').filter({ hasText: /^Approve$/ }).click({ timeout: 3000 });
          await page.keyboard.type('appr'); await page.waitForTimeout(400);
          await shot(page, tag + '-dialog');
        });
      }
      await run({ scheme, poll: 'big' }, async (page) => {
        await waitFx(page, X.bigN, 20000); await settle(page, 6000); await page.waitForTimeout(1800);
        await shot(page, 'factory-big-' + scheme);
      });
      await run({ scheme, poll: 'long' }, async (page) => {
        await waitFx(page, 34); await settle(page, 4000);
        await page.evaluate(() => __t.g().center('p:valhalla-strategy', { zoom: 1, animate: false }));
        await page.waitForTimeout(1200);
        await shot(page, 'factory-hubs-near-' + scheme);
      });
    }
    await run({ poll: 'empty' }, async (page) => {
      await page.waitForSelector('section.fx', { timeout: 9000 });
      await page.evaluate(() => __t.until(() => __t.vis(document.querySelector('.fx-stage .empty')), 9000));
      await page.waitForTimeout(600);
      await shot(page, 'factory-empty-light');
    });
  });

  await browser.close();
  console.log(JSON.stringify({ steps: out }));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def expectations():
    p1 = {n["id"]: n for n in POLL1["nodes"]}
    p2 = {n["id"]: n for n in POLL2["nodes"]}
    added = sorted(set(p2) - set(p1))
    removed = sorted(set(p1) - set(p2))
    reports = scoped(copy.deepcopy(POLL1), "reports-app")
    big = big_variant()

    def tree(d):
        out = {n["id"]: {"parent": n.get("parent"), "kids": []} for n in d["nodes"]}
        for n in d["nodes"]:
            if n.get("parent") in out:
                out[n["parent"]]["kids"].append(n["id"])
        return out

    feed = lambda d: [f["key"] + "|" + f["id"] for f in d["feed"][:4]]   # noqa: E731

    def feed8(d):
        """The column's rows: the feed newest first (stable), the first 8; pending shows as running, blocked as error."""
        fs = sorted(d["feed"], key=lambda f: -f["t"])[:8]
        return [{"key": f["key"] + "|" + f["id"], "node": f["node"], "status": {"pending": "running", "blocked": "error"}.get(f["status"], f["status"]),
                 "word": {"pending": "running", "ok": "ok", "error": "fail", "blocked": "blocked"}[f["status"]], "label": f["label"]} for f in fs]

    def tree_order(d):
        t, seen, out = tree(d), set(), []

        def walk(i):
            out.append(i)
            for c in t[i]["kids"]:
                walk(c)
        for n in d["nodes"]:
            if n["id"] not in seen and not (n.get("parent") in t):
                walk(n["id"])
        return out
    gone = removed[0]
    return {
        "ids": {"a2": A2, "b3": B3, "s6": S6, "agent": A91, "session": S1, "project": PROJ, "order": ORDER3, "far": FAR, "edge": EDGE,
                "failed": FAILED, "stalled": STALLED, "e9": E9, "fo2": "o:reports-app/FO-002"},
        "inspectIds": {"agent": A91, "session": S1, "project": PROJ, "order": ORDER3},
        "poll1": {"ids": [n["id"] for n in POLL1["nodes"]], "labels": {n["id"]: n["label"] for n in POLL1["nodes"]}, "feed": feed(POLL1),
                  "alert": POLL1["alerts"][0]["node"], "treeOrder": tree_order(POLL1),
                  "feed8": feed8(POLL1), "feedKeys": [x["key"] for x in feed8(POLL1)],
                  "ticks": {n["id"]: n["target"] for n in POLL1["nodes"] if len(n.get("target") or "") > 3}},
        "gone": {"id": gone, "parent": p1[gone]["parent"], "parentSurvives": p1[gone]["parent"] in p2},
        "cycles": list(CYCLES), "robust": dict(ROBUST, dup=S6, reports="p:reports-app", n=len(POLL1["nodes"]) + 3),
        "poll2": {"ids": [n["id"] for n in POLL2["nodes"]], "added": added, "removed": removed,
                  "parentOf": {i: p2[i]["parent"] for i in added}, "feed": feed(POLL2),
                  "feed8": feed8(POLL2), "feedKeys": [x["key"] for x in feed8(POLL2)], "alert": POLL2["alerts"][0]["node"]},
        "tree": {"1": tree(POLL1), "big": tree(big)},
        "reportsIds": [n["id"] for n in reports["nodes"]],
        "bigN": len(big["nodes"]),
        "series": series_expect(),
        "long": {"labels": dict(LONG_NAMES, **{n["id"]: n["label"] for n in POLL1["nodes"] if n["kind"] == "project"})},
        "rates": {"newMin": sum(n["burn"]["new_min"] for n in POLL1["nodes"] if n["kind"] == "session"), "tokMin": POLL1["kpis"]["burn_tok_min"]},
        "ctl": {"b3Node": B3, "s1Node": S1, "a91": A91, "b3": REQ_B3, "q": REQ_Q, "cmd": CTL_CMD, "cmdStart": CTL_CMD[:60],
                "guardText": CTL_GUARD.replace("NEEDS APPROVAL: ", ""), "b3Href": "#/s/%s/a/%s" % (SID_A, B3_AGENT),
                "fixtureMsg": POLL1["alerts"][0]["message"]},
    }


def run_browser(srv, exp):
    js = os.path.join(BASE, "factory_ui_check.js")
    with open(js, "w") as fh:
        fh.write(BROWSER_JS)
    ex = os.path.join(BASE, "expect.json")
    with open(ex, "w") as fh:
        json.dump(exp, fh)
    env = dict(os.environ, PW=PLAYWRIGHT, CHROME=CHROME, BASE="http://127.0.0.1:%d" % srv.port, EXPECT=ex, SHOTS=SHOTS, ONLY=",".join(ONLY))
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
    r = subprocess.run(["node", js], capture_output=True, text=True, timeout=900, env=env)
    for line in r.stderr.splitlines():
        if line.startswith("step "):
            note("  " + line)
    if r.returncode != 0:
        check("browser: the page check ran", False, r.stderr[-1200:])
        return None
    return json.loads(r.stdout.strip().splitlines()[-1])["steps"]


def fnum(x, nd=2):
    return "n/a" if x is None else ("%.*f" % (nd, x))


def evaluate(steps, exp):
    def ran(name):
        return not ONLY or name in ONLY

    def S(name):
        return steps.get(name) or {"error": "step did not run"}

    def why(r):
        return r.get("error", "")

    def no_errors(name, r):
        check("%s: no page errors" % name, r.get("errors") == [], r.get("errors") if "errors" in r else why(r))
        check("%s: no console errors" % name, r.get("consoleErrors") == [],
              ((r.get("consoleErrors"), r.get("badResponses")) if r.get("consoleErrors") else None) if "consoleErrors" in r else why(r))

    p1ids = exp["poll1"]["ids"]
    labels = exp["poll1"]["labels"]

    # ── 2. renders ──
    if ran("render"):
        r = S("render")
        check("render: section.fx and .fx-stage exist", r.get("hasFx") and r.get("hasStage"), why(r) or (r.get("hasFx"), r.get("hasStage")))
        no_errors("render", r)
        ids = r.get("ids") or []
        check("render: exactly one .gx-node[data-id] per payload node (%d)" % len(p1ids),
              len(ids) == len(p1ids) and sorted(ids) == sorted(p1ids),
              why(r) or {"have": len(ids), "missing": sorted(set(p1ids) - set(ids))[:4], "extra": sorted(set(ids) - set(p1ids))[:4]})
        check("render: the engine host is .fx-graph.gx with a canvas", r.get("hostIsGx") and r.get("canvas"), why(r) or (r.get("hostIsGx"), r.get("canvas")))
        hud = r.get("hud") or {}
        hv = lambda k: ((hud.get(k) or {}).get("v") or "")   # noqa: E731
        note("render: %d nodes, settle %s ms, HUD %s" % (len(ids), fnum(r.get("settleMs"), 0), {k: v.get("v") for k, v in hud.items()}))
        note("render: h1=%r sub=%r alerts=%s feed rows=%s zoom=%s" % (r.get("h1"), r.get("sub"), r.get("alerts"), r.get("evs"), r.get("zoomBtns")))
        has = lambda k, n: re.search(r"(?<![\d.])%s(?![\d])" % re.escape(str(n)), hv(k)) is not None   # noqa: E731
        check("render: HUD shows working = 17", has("working", 17), why(r) or hud.get("working"))
        check("render: HUD shows needs = 1", has("needs", 1), why(r) or hud.get("needs"))
        check("render: HUD shows sessions live = 5", has("live", 5), why(r) or hud.get("live"))
        # the stalled figure sits beside the failed one (two <b> in one value), so read them apart
        check("render: HUD shows stalled = 1 and failed = 1", (hud.get("stalled") or {}).get("bs") == ["1", "1"], why(r) or hud.get("stalled"))
        check("render: HUD shows the live cost $12.64", "$12.64" in hv("cost"), why(r) or hud.get("cost"))
        check("render: the top ticker is gone and the column lists 8 recent actions", not r.get("ticker") and r.get("evs") == 8, why(r) or (r.get("ticker"), r.get("evs")))
        check("render: HUD has a live, burn and orders figure", all(k in hud for k in ("live", "burn", "orders")), sorted(hud))
        st = r.get("stats") or {}
        check("render: normal motion draws particles on active edges", (st.get("particles") or 0) > 0, why(r) or st)
        check("render: the graph group is labelled 'Factory floor'", r.get("worldLabel") == "Factory floor", why(r) or r.get("worldLabel"))
        check("render: node elements are in tree order (reading order follows the tree)", ids == exp["poll1"]["treeOrder"],
              why(r) or [i for i, (a, b) in enumerate(zip(ids, exp["poll1"]["treeOrder"])) if a != b][:3])

    # ── 3. inspector ──
    if ran("inspector"):
        r = S("inspector")
        cs = r.get("cases") or {}
        sid = SID_A

        def case(key):
            c = cs.get(key) or {}
            return c, (c.get("insp") or {}), (c.get("error") or why(r))

        def opened(c, i, want_id, label_part):
            return c.get("openMs", -1) >= 0 and i.get("visible") and label_part in (i.get("title") or "")

        c, i, e = case("agent")
        note("inspector: agent title=%r sections=%s links=%s" % (i.get("title"), i.get("secs"), i.get("hrefs")))
        check("inspector: agent node opens aside.fx-inspector titled with its label",
              opened(c, i, A91, "Write the graph.py payload builder"), e or (c.get("openMs"), i.get("title"), i.get("visible")))
        t = i.get("text") or ""
        check("inspector: agent body has the tool Edit, lib/cc_dash/graph.py and $0.42",
              "Edit" in t and "lib/cc_dash/graph.py" in t and "$0.42" in t, e or t[:300])
        check("inspector: agent links to #/s/<sid>/a/<aid>", "#/s/%s/a/a91f02d6c3b8e4a7" % sid in (i.get("hrefs") or []), e or i.get("hrefs"))
        c, i, e = case("session")
        check("inspector: session node opens with its title", opened(c, i, S1, "Build the software factory experiment"),
              e or (c.get("openMs"), i.get("title")))
        check("inspector: session body shows 318 tool calls", re.search(r"(?<!\d)318(?!\d)", i.get("text") or "") is not None, e or (i.get("text") or "")[:300])
        check("inspector: session links to #/s/<sid>", "#/s/%s" % sid in (i.get("hrefs") or []), e or i.get("hrefs"))
        c, i, e = case("project")
        check("inspector: project hub opens", opened(c, i, PROJ, "cc_tool"), e or (c.get("openMs"), i.get("title")))
        check("inspector: project hub links to #/overview?p=cc_tool and #/plans?p=cc_tool",
              "#/overview?p=cc_tool" in (i.get("hrefs") or []) and "#/plans?p=cc_tool" in (i.get("hrefs") or []), e or i.get("hrefs"))
        c, i, e = case("order")
        t = (i.get("text") or "").lower()
        check("inspector: order FO-003 opens", c.get("openMs", -1) >= 0 and i.get("visible") and
              ("csv export" in (i.get("title") or "").lower() or "fo-003" in (i.get("title") or "").lower()), e or (c.get("openMs"), i.get("title")))
        check("inspector: order body shows its stations (plan, build, test, verify, review)",
              all(s in t for s in ("plan", "build", "test", "verify", "review")), e or t[:300])
        want = {"agent": A91, "session": S1, "project": PROJ, "order": ORDER3}
        unsel = {k: (c.get("selected") or {}) for k, c in cs.items() if not ((c.get("selected") or {}).get("sel") == want[k] and (c.get("selected") or {}).get("cls"))}
        check("inspector: a user pick (click) selects the node in the engine at once (graph().selected() and .is-selected)", len(cs) == 4 and not unsel,
              why(r) or unsel)
        gone = [k for k, c in cs.items() if not (c.get("closedMs", -1) >= 0 and (c.get("afterEsc") or {}).get("visible") is False and (c.get("afterEsc") or {}).get("selected") is None)]
        check("inspector: Esc closes the inspector and deselects (all four kinds)", len(cs) == 4 and not gone, why(r) or {"not closed": gone, "n": len(cs)})
        no_errors("inspector", r)
        covered = [k for k, c in cs.items() if (c.get("click") or {}).get("covered")]
        if covered:
            note("inspector: note, the click point was covered for %s: %s" % (covered, {k: cs[k]["click"].get("top") for k in covered}))

    # ── 4. in-place updates ──
    if ran("inplace"):
        r = S("inplace")
        a, m = r.get("afterStates") or {}, (r.get("afterStates") or {}).get("m") or {}
        base = r.get("base") or {}
        note("in-place (states poll): visible after %s ms; max world move %s px (final %s), max screen move %s px (final %s), camera x/y %s, k %s, %s frames sampled; replaced=%s"
             % (fnum(r.get("stateAppearMs"), 0), fnum(m.get("world")), fnum(a.get("finalWorld")), fnum(m.get("screen")), fnum(a.get("finalScreen")),
                fnum(m.get("cam")), fnum(m.get("camK"), 4), m.get("frames"), a.get("replaced")))
        check("in-place: the changed state reaches the page within a few polls (a2 -> st-done)", r.get("stateAppearMs", -1) >= 0 and r.get("stateAppearMs", -1) <= 7500,
              why(r) or r.get("stateAppearMs"))
        check("in-place: state-only poll replaces no .gx-node (same elements, same count)",
              a.get("count") == base.get("n") == len(p1ids) and a.get("replaced") == [], why(r) or {"count": a.get("count"), "baseline": base.get("n"), "replaced": a.get("replaced")})
        check("in-place: state-only poll moves no node (< 1 px in world coordinates, any frame)", m.get("world") is not None and m.get("world") < 1 and a.get("finalWorld", 9) < 1,
              why(r) or (m.get("world"), a.get("finalWorld")))
        check("in-place: state-only poll moves no node on screen (< 1 px, any frame)", m.get("screen") is not None and m.get("screen") < 1 and a.get("finalScreen", 9) < 1,
              why(r) or (m.get("screen"), a.get("finalScreen")))
        cb, ca = a.get("camBefore") or {}, a.get("camAfter") or {}
        check("in-place: state-only poll leaves the camera alone", bool(cb) and bool(ca) and all(abs(cb[k] - ca[k]) < 1e-6 for k in ("x", "y", "k")) and (m.get("cam") or 0) < 0.01,
              why(r) or (cb, ca))
        f = a.get("faces") or {}
        fa2, fb3, fs6 = f.get("a2") or {}, f.get("b3") or {}, f.get("s6") or {}
        fa2b, fb3b, fs6b = (base.get("faces") or {}).get("a2") or {}, (base.get("faces") or {}).get("b3") or {}, (base.get("faces") or {}).get("s6") or {}
        check("in-place: a2 now shows done on its face (class st-done, text changed)", "st-done" in (fa2.get("cls") or "").split() and fa2.get("text") != fa2b.get("text"),
              why(r) or (fa2b.get("cls"), fa2.get("cls"), fa2.get("text")))
        check("in-place: b3 no longer shows the needs-you halo", fb3b.get("needs") and fb3b.get("halo") and not fb3.get("needs") and not fb3.get("halo"),
              why(r) or {"before": fb3b, "after": fb3})
        check("in-place: s6 now shows generating (class st-generating)", "st-generating" in (fs6.get("cls") or "").split() and "st-generating" not in (fs6b.get("cls") or "").split(),
              why(r) or (fs6b.get("cls"), fs6.get("cls")))
        check("in-place: the alert for b3 is gone and the HUD needs figure is 0", a.get("alerts") == 0 and (a.get("needsHud") or "").strip() in ("0", ""),
              why(r) or (a.get("alerts"), a.get("needsHud")))
        lb0, lb1 = base.get("labels") or {}, a.get("labels") or {}
        ticks = exp["poll1"]["ticks"]
        ticking = {k: v for k, v in lb1.items() if "$" in (v or "") or "Running " in (v or "") or (ticks.get(k) and ticks[k] in (v or ""))}
        check("in-place: node aria-labels carry no cost, tool or target (nothing that ticks)", bool(lb1) and not ticking, why(r) or dict(list(ticking.items())[:3]))
        same = [X for X in (A91, S6) if lb0.get(X) and lb0.get(X) == lb1.get(X)]
        check("in-place: a node whose status holds keeps its aria-label across a poll (a91 working, s6 tool -> generating)", len(same) == 2,
              why(r) or {X[-8:]: (lb0.get(X), lb1.get(X)) for X in (A91, S6)})
        check("in-place: a node whose status changes gets a new aria-label (a2 -> finished)", lb0.get(A2) and lb1.get(A2) and lb0[A2] != lb1[A2] and "finished" in lb1[A2],
              why(r) or (lb0.get(A2), lb1.get(A2)))
        # poll 2
        a2 = r.get("after2") or {}
        ids2 = a2.get("ids") or []
        p2 = exp["poll2"]
        note("in-place (poll 2): added visible after %s ms, removed gone after %s ms, settled after %s ms; %d common nodes moved max %s px, median %s px; new nodes sit %s px from their parents"
             % (fnum(r.get("addedAppearMs"), 0), fnum(r.get("removedGoneMs"), 0), fnum(r.get("settle2Ms"), 0), a2.get("common") or 0, fnum(a2.get("max")), fnum(a2.get("median")),
                {k.split("/")[-1][:6]: fnum(v, 0) for k, v in (a2.get("nearParent") or {}).items()}))
        check("in-place: poll 2 shows %d nodes with exactly poll 2's ids" % len(p2["ids"]), sorted(ids2) == sorted(p2["ids"]),
              why(r) or {"have": len(ids2), "missing": sorted(set(p2["ids"]) - set(ids2)), "extra": sorted(set(ids2) - set(p2["ids"]))})
        check("in-place: the two added agents appear", r.get("addedAppearMs", -1) >= 0, why(r) or r.get("addedAppearMs"))
        check("in-place: the removed agent is gone after its exit", r.get("removedGoneMs", -1) >= 0 and not set(p2["removed"]) & set(ids2), why(r) or r.get("removedGoneMs"))
        check("in-place: the layout settles within 3 s of the structural poll", r.get("settle2Ms", -1) >= 0 and (a2.get("stats") or {}).get("settled"), why(r) or r.get("settle2Ms"))
        check("in-place: survivors stay anchored (max world displacement < 6 px)", a2.get("max") is not None and a2.get("common", 0) >= 28 and a2["max"] < 6,
              why(r) or (a2.get("max"), a2.get("median"), a2.get("common")))
        al = a2.get("alerts") or []
        check("in-place: poll 2's question alert replaces the permission alert",
              len(al) == 1 and al[0].get("node") == "a:%s/c9f3a1e5b7d04e82" % SID_A, why(r) or al)
        no_errors("in-place", r)

    # ── 5. alerts focus their node ──
    if ran("alerts"):
        r = S("alerts")
        al = r.get("alerts") or []
        node = al[0]["node"] if al else None
        check("alerts: poll 1 lists the permission alert as a button.fx-alert for b3", len(al) == 1 and al[0].get("node") == B3 and al[0].get("tag") == "BUTTON", why(r) or al)
        for how, label in (("click", "clicking the alert"), ("keyboard", "Enter on the focused alert")):
            c = (r.get("runs") or {}).get(how) or {}
            af = c.get("after") or {}
            i = af.get("insp") or {}
            pos, host = af.get("pos"), af.get("host") or {}
            lab = labels.get(node, "\0")
            note("alerts (%s): before screenPos=%s, after screenPos=%s in host %sx%s, camera %s, inspector %r" %
                 (how, c.get("before", {}).get("pos") and {k: round(v) for k, v in c["before"]["pos"].items()}, pos and {k: round(v) for k, v in pos.items()},
                  host.get("w") and round(host["w"]), host.get("h") and round(host["h"]), af.get("cam") and {k: round(v, 2) for k, v in af["cam"].items()}, i.get("title")))
            check("alerts: %s selects the alert's node" % label, node is not None and af.get("sel") == node, c.get("error") or why(r) or (af.get("sel"), node))
            check("alerts: %s opens the inspector for that node's label" % label, i.get("visible") and (lab in (i.get("title") or "") or lab in (i.get("label") or "")),
                  c.get("error") or why(r) or (i.get("visible"), i.get("title"), i.get("label")))
            check("alerts: %s brings the node inside the .fx-graph box" % label,
                  pos is not None and host.get("w") and 0 <= pos["x"] <= host["w"] and 0 <= pos["y"] <= host["h"], c.get("error") or why(r) or (pos, host))
        no_errors("alerts", r)

    # ── 6. keyboard ──
    if ran("keyboard"):
        r = S("keyboard")
        note("keyboard: Tab reached a node after %s presses; right %s, down %s, home %s, end %s; j,j,k -> %s,%s,%s; camera k %s -> + %s -> - - %s; f -> %s"
             % (r.get("tabsToNode"), (r.get("right") or {}).get("to") and "moved", (r.get("down") or {}).get("to") and "moved", (r.get("home") or {}).get("to") and "ok",
                (r.get("end") or {}).get("to") and "ok", (r.get("j1") or "-")[-6:], (r.get("j2") or "-")[-6:], (r.get("k1") or "-")[-6:], fnum(r.get("k0"), 3), fnum(r.get("kIn"), 3),
                fnum(r.get("kOut"), 3), r.get("fKeyHash")))
        check("keyboard: exactly one node is a tab stop (roving tabindex)", r.get("tabStops") == 1, why(r) or r.get("tabStops"))
        check("keyboard: Tab reaches a .gx-node", (r.get("tabsToNode") or -1) > 0, why(r) or r.get("tabsToNode"))
        rt, dn = r.get("right") or {}, r.get("down") or {}
        check("keyboard: ArrowRight moves focus to another .gx-node", rt.get("to") and rt["to"] != rt.get("from"), why(r) or rt)
        check("keyboard: ArrowDown moves focus to another .gx-node", dn.get("to") and dn["to"] != dn.get("from"), why(r) or dn)
        hm, en = r.get("home") or {}, r.get("end") or {}
        check("keyboard: Home and End jump to different nodes", hm.get("to") and en.get("to") and hm["to"] != en["to"], why(r) or (hm, en))
        e = r.get("enter") or {}
        ei = e.get("insp") or {}
        check("keyboard: Enter on a focused node opens the inspector for it", r.get("enterOpenMs", -1) >= 0 and ei.get("visible") and
              "Write the graph.py payload builder" in (ei.get("title") or ""), why(r) or (r.get("enterOpenMs"), ei.get("title"), ei.get("visible")))
        check("keyboard: Enter selects the node in the engine at once (graph().selected() and .is-selected)", e.get("selected") == A91 and e.get("cls") is True,
              why(r) or (e.get("selected"), e.get("cls")))
        check("keyboard: Escape closes the inspector and deselects", r.get("escClosedMs", -1) >= 0 and (r.get("esc") or {}).get("selected") is None, why(r) or r.get("esc"))
        sp = r.get("space") or {}
        check("keyboard: Space on a focused node opens the inspector too", r.get("spaceOpenMs", -1) >= 0 and (sp.get("insp") or {}).get("visible") and
              "Build the software factory experiment" in ((sp.get("insp") or {}).get("title") or ""), why(r) or (r.get("spaceOpenMs"), (sp.get("insp") or {}).get("title")))
        check("keyboard: Space selects the node in the engine at once", sp.get("selected") == S1 and sp.get("cls") is True, why(r) or (sp.get("selected"), sp.get("cls")))
        check("keyboard: j and k move focus between nodes", bool(r.get("j1")) and bool(r.get("j2")) and r["j1"] != r["j2"] and r.get("k1") == r.get("j1"), why(r) or (r.get("j1"), r.get("j2"), r.get("k1")))
        check("keyboard: + zooms in and - zooms out", r.get("kIn") and r.get("k0") and r["kIn"] > r["k0"] and r.get("kOut") and r["kOut"] < r["kIn"], why(r) or (r.get("k0"), r.get("kIn"), r.get("kOut")))
        check("keyboard: f from Projects (#/overview) lands on #/factory",
              (r.get("fromHash") or "").startswith("#/overview") and not r.get("fromHadFx") and r.get("fKeyMs", -1) >= 0 and (r.get("fKeyHash") or "").startswith("#/factory"),
              why(r) or (r.get("fromHash"), r.get("fromHadFx"), r.get("fKeyHash"), r.get("fKeyMs")))
        no_errors("keyboard", r)

    # ── 7. themes ──
    if ran("themes"):
        r = S("themes")
        v = r.get("v") or {}
        li, da = v.get("light") or {}, v.get("dark") or {}
        stage = lambda x: (x.get("stageImg"), x.get("stageColor"))   # noqa: E731
        note("themes: stage light=%s dark=%s; HUD value colour light=%s dark=%s" % (stage(li), stage(da), li.get("hudColor"), da.get("hudColor")))
        errs = {k: (x.get("errors") or []) + (x.get("consoleErrors") or []) + ([x["error"]] if x.get("error") else []) for k, x in v.items()}
        check("themes: light, dark and both ?theme= overrides render %d nodes with no errors" % len(p1ids),
              len(v) == 4 and all(x.get("nodes") == len(p1ids) for x in v.values()) and not any(errs.values()), why(r) or {k: e for k, e in errs.items() if e} or {k: x.get("nodes") for k, x in v.items()})
        check("themes: the stage background differs between light and dark", bool(li) and bool(da) and stage(li) != stage(da) and li.get("stageImg") is not None, why(r) or (stage(li), stage(da)))
        check("themes: the HUD value colour differs between light and dark", bool(li.get("hudColor")) and li.get("hudColor") != da.get("hudColor"), why(r) or (li.get("hudColor"), da.get("hudColor")))
        do, lo = v.get("darkOverride") or {}, v.get("lightOverride") or {}
        check("themes: ?theme=dark under a light system sets data-theme=dark and the dark stage", do.get("dataTheme") == "dark" and stage(do) == stage(da),
              why(r) or (do.get("dataTheme"), stage(do), stage(da)))
        check("themes: ?theme=light under a dark system sets data-theme=light and the light stage", lo.get("dataTheme") == "light" and stage(lo) == stage(li),
              why(r) or (lo.get("dataTheme"), stage(lo), stage(li)))
        fl = da.get("flip") or {}
        check("themes: a live prefers-color-scheme flip restyles the stage (dark -> light)", bool(fl) and stage(fl) == stage(li) and stage(fl) != stage(da), why(r) or (stage(fl), stage(li)))

    # ── 8. reduced motion ──
    if ran("reduced"):
        r = S("reduced")
        f = r.get("first") or {}
        note("reduced motion: matchMedia=%s, settled %s ms after the first node, particles max %s, later movement %s px"
             % (f.get("reduce"), fnum(f.get("settledAfterMs"), 0), r.get("particleMax"), fnum((r.get("later") or {}).get("moved"))))
        check("reduced motion: the context really prefers reduced motion", f.get("reduce") is True, why(r) or f)
        no_errors("reduced motion", r)
        check("reduced motion: no particles are ever emitted (stats().particles === 0)", r.get("particleMax") == 0 and (f.get("stats") or {}).get("particles") == 0, why(r) or (r.get("particleMax"), f.get("stats")))
        check("reduced motion: layout is settled within ~300 ms of the first render", f.get("settled") is True and f.get("settledAfterMs", 9999) <= 300, why(r) or (f.get("settled"), f.get("settledAfterMs")))
        check("reduced motion: nodes do not move after the first render (< 1 px)", (r.get("later") or {}).get("moved") is not None and r["later"]["moved"] < 1, why(r) or r.get("later"))

    # ── 9. phone width ──
    if ran("mobile"):
        r = S("mobile")
        n, w = r.get("noInspector") or {}, r.get("withInspector") or {}
        note("mobile 390x844: scrollWidth %s (body %s) without the inspector, %s with it (opened via %s); inspector rect %s; list mode %s"
             % (n.get("scrollWidth"), n.get("bodyScrollWidth"), w.get("scrollWidth"), r.get("opened"), {k: round(x) for k, x in ((r.get("insp") or {}).get("rect") or {}).items()},
                (r.get("listMode") or {}).get("scrollWidth")))
        check("mobile: no horizontal page scroll at 390 px (Factory, nothing open)", n.get("scrollWidth") is not None and n["scrollWidth"] <= n["innerWidth"], why(r) or n)
        check("mobile: no horizontal page scroll with the inspector open", r.get("inspMs", -1) >= 0 and w.get("scrollWidth") is not None and w["scrollWidth"] <= w["innerWidth"], why(r) or (r.get("inspMs"), w))
        rect = (r.get("insp") or {}).get("rect") or {}
        check("mobile: the open inspector fits inside the viewport", bool(rect) and rect["l"] >= -1 and rect["r"] <= 391 and rect["b"] <= 845, why(r) or rect)
        if r.get("listMode"):
            check("mobile: no horizontal page scroll in list mode", r["listMode"]["scrollWidth"] <= r["listMode"]["innerWidth"], r["listMode"])
        no_errors("mobile", r)

    # ── 10. list equivalent ──
    if ran("list"):
        r = S("list")
        items = r.get("items") or []
        note("list: %d items, mode=%s, pressed=%s, sample=%r" % (len(items), r.get("mode"), r.get("pressed"), r.get("sample")))
        check("list: the mode switch shows .fx-list and hides the graph", r.get("mode") == "list" and r.get("listVisible") and not r.get("graphVisible"), why(r) or (r.get("mode"), r.get("listVisible"), r.get("graphVisible")))
        check("list: one button.fx-li[data-node] per payload node", r.get("uniq") == len(p1ids) == len(items) and sorted(items) == sorted(p1ids),
              why(r) or {"items": len(items), "unique": r.get("uniq"), "missing": sorted(set(p1ids) - set(items))[:4]})
        i = r.get("insp") or {}
        check("list: clicking an item opens the inspector for that node", r.get("openMs", -1) >= 0 and "Write the graph.py payload builder" in (i.get("title") or ""), why(r) or (r.get("openMs"), i.get("title")))
        check("list: switching back to the graph shows every node again", r.get("backMs", -1) >= 0, why(r) or r.get("backMs"))
        no_errors("list", r)

    # ── 11. scope filters ──
    if ran("scope"):
        r = S("scope")
        reqs = r.get("reqs") or []
        note("scope: scoped requests %s -> %d nodes %s; after clearing: %s" % (sorted(set(reqs)), len(r.get("ids") or []), r.get("hash"), sorted(set(r.get("clearedReqs") or []))))
        # pairing lands on #/factory before the test sets ?p=, so one unscoped poll may come first
        since = next((i for i, q in enumerate(reqs) if "project=reports-app" in q), len(reqs))
        check("scope: #/factory?p=reports-app requests /api/graph?project=reports-app (every poll once scoped)",
              since <= 1 and len(reqs) - since >= 2 and all("project=reports-app" in q for q in reqs[since:]), why(r) or reqs)
        check("scope: only that project's nodes are rendered", sorted(r.get("ids") or []) == sorted(exp["reportsIds"]),
              why(r) or {"have": len(r.get("ids") or []), "want": len(exp["reportsIds"]), "extra": sorted(set(r.get("ids") or []) - set(exp["reportsIds"]))[:4]})
        check("scope: the page head shows the picked project", "reports-app" in (r.get("tools") or ""), why(r) or r.get("tools"))
        cr = r.get("clearedReqs") or []
        check("scope: clearing ?p= requests /api/graph without a project and shows every node",
              r.get("clearedMs", -1) >= 0 and cr and all("project=" not in q for q in cr) and sorted(r.get("clearedIds") or []) == sorted(p1ids), why(r) or (r.get("clearedMs"), cr))
        no_errors("scope", r)
    if ran("dormant"):
        r = S("dormant")
        note("dormant: on -> %s requests %s, hub after %s ms; off -> requests %s" % (r.get("on"), sorted(set(r.get("onReqs") or [])), fnum(r.get("hubMs"), 0), sorted(set(r.get("offReqs") or []))))
        b = r.get("before") or {}
        check("dormant: button.fx-dormant exists, unpressed, and the hub p:dotfiles is absent", b.get("exists") and b.get("pressed") == "false" and not b.get("hub"), why(r) or b)
        on = r.get("on") or {}
        check("dormant: pressing it requests all=1, sets aria-pressed and adds the dormant hub p:dotfiles",
              r.get("hubMs", -1) >= 0 and on.get("pressed") == "true" and any("all=1" in q for q in r.get("onReqs") or []), why(r) or (r.get("hubMs"), on, r.get("onReqs")))
        off = r.get("off") or {}
        check("dormant: pressing it again drops all=1 and the hub", r.get("hubGoneMs", -1) >= 0 and off.get("pressed") == "false" and (r.get("offReqs") or []) and
              not all("all=1" in q for q in r.get("offReqs") or []), why(r) or (r.get("hubGoneMs"), off, r.get("offReqs")))
        no_errors("dormant", r)

    # ── 12. performance ──
    if ran("perf"):
        r = S("perf")
        sm = r.get("samples") or []
        last = sm[-1] if sm else {}
        fm = max((x.get("frameMs") or 0) for x in sm) if sm else None
        note("perf: %s nodes, stats %s, max frameMs %s, rAF frames in 1 s: %s" % (r.get("count"), last, fnum(fm, 3), r.get("fps")))
        check("perf: ~300 nodes (%d) are all rendered" % exp["bigN"], r.get("count") == exp["bigN"], why(r) or r.get("count"))
        check("perf: layout settles in under 1.5 s (stats().settleMs)", bool(last) and last.get("settled") and 0 < last.get("settleMs", 0) < 1500, why(r) or (last.get("settled"), last.get("settleMs")))
        check("perf: engine JS under 12 ms per frame in headless Chrome (stats().frameMs)", fm is not None and fm < 12, why(r) or fm)
        check("perf: the 300-node stage keeps drawing (more than 10 rAF frames per second)", (r.get("fps") or 0) > 10, why(r) or r.get("fps"))
        no_errors("perf", r)

    # ── 13. empty payload ──
    if ran("empty"):
        r = S("empty")
        em = r.get("empty") or {}
        note("empty: .empty shown after %s ms with %r; recovered %s nodes after %s ms" % (fnum(r.get("emptyMs"), 0), em.get("text"), (r.get("recovered") or {}).get("nodes"), fnum(r.get("recoverMs"), 0)))
        check("empty: .fx-stage .empty is shown, with no nodes and the stage still there", r.get("emptyMs", -1) >= 0 and em.get("nodes") == 0 and em.get("hasStage") and bool(em.get("text")), why(r) or em)
        rc = r.get("recovered") or {}
        check("empty: the next non-empty poll fills the stage and hides .empty", r.get("recoverMs", -1) >= 0 and rc.get("nodes") == len(p1ids) and not rc.get("emptyVisible"), why(r) or (r.get("recoverMs"), rc))
        no_errors("empty", r)

    # ── landing ──
    if ran("landing"):
        r = S("landing")
        check("landing: the brand link opens #/factory (from Projects)", (r.get("brandHash") or "").startswith("#/factory") and r.get("fromNoFx"),
              why(r) or (r.get("fromNoFx"), r.get("brandHash")))
        check("landing: the nav is Factory and Projects", r.get("nav") == ["Factory", "Projects"], why(r) or r.get("nav"))
        check("landing: the old #/live route lands on the Factory", r.get("liveMs", -1) >= 0 and (r.get("liveHash") or "").startswith("#/factory"),
              why(r) or (r.get("liveMs"), r.get("liveHash")))
        check("landing: Factory is the current tab", "#/factory" in (r.get("brandCurrent") or []) or [x for x in r.get("brandCurrent") or [] if x.startswith("#/factory")], why(r) or r.get("brandCurrent"))
        ft = r.get("firstTab") or {}
        check("landing: an empty hash lands on the Factory (first tab current)", r.get("rootHash") is not None and ft.get("current") == "page" and (ft.get("href") or "").startswith("#/factory"), why(r) or (r.get("rootHash"), ft))

    # ── 12b. the inspector re-frames ──
    def meets(a, b, pad=0.5):
        return bool(a and b) and a["r"] > b["l"] + pad and a["l"] < b["r"] - pad and a["b"] > b["t"] + pad and a["t"] < b["b"] - pad

    if ran("reframe"):
        r = S("reframe")
        for name, c in sorted((r.get("cases") or {}).items()):
            e = c.get("error") or why(r)
            op, cl, bf = c.get("open") or {}, c.get("closed") or {}, c.get("before") or {}
            ins, hood = op.get("insp"), c.get("hood") or []
            covered0 = [i for i in hood if meets((bf.get("nodes") or {}).get(i), ins)]
            under = [i for i in hood if meets((op.get("nodes") or {}).get(i), ins)]
            g, top, z = op.get("graph") or {}, op.get("top") or {}, op.get("zoom")
            outside = [i for i in hood if not ((op.get("nodes") or {}).get(i) and g and top and op["nodes"][i]["l"] >= g["l"] - 0.5 and op["nodes"][i]["r"] <= g["r"] + 0.5
                                               and op["nodes"][i]["t"] >= top["b"] - 0.5 and op["nodes"][i]["b"] <= g["b"] + 0.5)]
            onzoom = [i for i in hood if meets((op.get("nodes") or {}).get(i), z)]
            c0, c1 = c.get("cam0") or {}, cl.get("cam") or {}
            note("reframe (%s): opened %s (hood of %d: %d under the inspector before, %d after), camera k %s -> %s -> %s, settled in %s ms, after close %s ms"
                 % (name, (c.get("id") or "")[-18:], len(hood), len(covered0), len(under), fnum(c0.get("k"), 3), fnum((op.get("cam") or {}).get("k"), 3), fnum(c1.get("k"), 3),
                    fnum(c.get("camMs"), 0), fnum(c.get("closeCamMs"), 0)))
            check("reframe (%s): clicking the edge node in place opens the inspector and selects it" % name,
                  c.get("openMs", -1) >= 0 and c.get("sel") == c.get("id") and bool(ins), e or (c.get("openMs"), c.get("sel"), c.get("id")))
            if "phone" in name:       # no column on a phone: the sheet is the first thing to cover the floor
                check("reframe (%s): the case is real (before re-framing, part of that neighbourhood sat where the inspector opened)" % name, len(covered0) >= 1, e or (covered0, ins))
            else:                     # the column's inset is the inspector's: the floor is already fit clear of the slot
                check("reframe (%s): the floor was already fit clear of the right-hand slot, so no node of that neighbourhood sat where the inspector opened" % name, not covered0 and bool(ins), e or (covered0, ins))
            check("reframe (%s): the node, its parent and its children end up clear of the inspector" % name, bool(ins) and len(hood) >= 2 and not under,
                  e or {"under": under, "insp": ins, "boxes": {i: (op.get("nodes") or {}).get(i) for i in under}})
            check("reframe (%s): ... inside the floor below the instrument panel, and off the zoom buttons" % name, bool(ins) and not outside and not onzoom,
                  e or {"outside": outside, "on zoom": onzoom, "top": top, "graph": g})
            check("reframe (%s): closing the inspector fits the floor again (the camera is back where it was)" % name,
                  c.get("closedMs", -1) >= 0 and bool(c0) and bool(c1) and abs(c0["x"] - c1["x"]) < 1 and abs(c0["y"] - c1["y"]) < 1 and abs(c0["k"] - c1["k"]) < 1e-3, e or (c0, c1))
            errs = (c.get("errors") or []) + (c.get("consoleErrors") or [])
            check("reframe (%s): no page or console errors" % name, "errors" in c and not errs, errs or e)

    # ── 12c. fit keeps the zoom buttons clear ──
    if ran("zoomclear"):
        r = S("zoomclear")
        cs = r.get("cases") or {}
        note("zoom clear: " + ", ".join("%s k %s/%s hits %d/%d" % (k, fnum((c.get("load") or {}).get("k"), 3), fnum((c.get("fit") or {}).get("k"), 3),
                                                                     len((c.get("load") or {}).get("hits") or []), len((c.get("fit") or {}).get("hits") or [])) for k, c in sorted(cs.items())))
        for key in ("1@1440", "1@390", "big@1440", "big@390"):
            c = cs.get(key) or {}
            want = exp["bigN"] if key.startswith("big") else len(p1ids)
            ld, ft = c.get("load") or {}, c.get("fit") or {}
            check("zoom clear (%s): no node box under the zoom buttons after the first fit and after pressing 0" % key,
                  ld.get("n") == want and ft.get("n") == want and bool(ld.get("zoom")) and ld.get("hits") == [] and ft.get("hits") == [],
                  c.get("error") or why(r) or {"load": (ld.get("hits") or [])[:5], "fit": (ft.get("hits") or [])[:5], "n": (ld.get("n"), ft.get("n")), "zoom": ld.get("zoom")})
            errs = (c.get("errors") or []) + (c.get("consoleErrors") or [])
            check("zoom clear (%s): no page or console errors" % key, "errors" in c and not errs, errs or c.get("error"))

    # ── 12d. hub names fit their discs ──
    if ran("hubs"):
        r = S("hubs")
        lv = r.get("levels") or {}
        labels = exp["long"]["labels"]
        for lod in ("near", "mid", "far"):
            L = lv.get(lod) or {}
            hubs = L.get("hubs") or {}
            note("hubs (%s, host lod %s): %s" % (lod, L.get("lod"), {k[2:16]: ("/".join(v.get("lines") or []), v.get("fs"), fnum(v.get("worst"), 3)) for k, v in sorted(hubs.items())}))
            check("hubs (%s): the engine is at that level of detail and every hub shows that one variant" % lod,
                  L.get("lod") == lod and len(hubs) == len(labels) and all(v.get("variants") == [lod] for v in hubs.values()),
                  why(r) or (L.get("lod"), {k: v.get("variants") for k, v in hubs.items()}))
            bad = {k: (v.get("lines"), fnum(v.get("worst"), 3), fnum(v.get("rimRatio"), 3)) for k, v in hubs.items() if v.get("worst") is None or v["worst"] > v.get("rimRatio", 0) + 0.004}
            check("hubs (%s): every hub name's line boxes stay inside the disc's rim (40-character names included)" % lod, bool(hubs) and not bad, why(r) or bad)
            bad = {k: fnum(v.get("extraWorst"), 3) for k, v in hubs.items() if (v.get("extraWorst") or 0) > v.get("rimRatio", 0) + 0.004}
            check("hubs (%s): the live and cost lines stay inside the rim too" % lod, bool(hubs) and not bad, why(r) or bad)
            bad = {k: (v.get("title"), v.get("aria")[:60]) for k, v in hubs.items() if v.get("title") != labels.get(k) or labels.get(k, "\0") not in (v.get("aria") or "")}
            check("hubs (%s): the full name stays in the title and the aria-label" % lod, bool(hubs) and not bad, why(r) or bad)
            if lod != "far":
                v = hubs.get(FAR) or {}
                check("hubs (%s): valhalla-strategy wraps at its hyphen, whole (no ellipsis)" % lod, "".join(v.get("lines") or []) == "valhalla-strategy" and len(v.get("lines") or []) == 2,
                      why(r) or v.get("lines"))
        no_errors("hubs", r)

    # ── 12e. the activity row and the alerts hold still ──
    if ran("hold"):
        r = S("hold")
        h0, h1, h2 = r.get("hover0") or {}, r.get("hover1") or {}, r.get("hover2") or {}
        f0, f1, f2 = r.get("focus0") or {}, r.get("focus1") or {}, r.get("focus2") or {}
        note("hold: hover keys %s -> %s -> %s; focus keys %s -> %s -> %s; alert %s -> %s -> %s" % (
            [k[-6:] for k in h0.get("keys") or []], [k[-6:] for k in h1.get("keys") or []], [k[-6:] for k in h2.get("keys") or []],
            [k[-6:] for k in f0.get("keys") or []], [k[-6:] for k in f1.get("keys") or []], [k[-6:] for k in f2.get("keys") or []],
            (r.get("alert0") or "")[-6:], r.get("alert1"), [x[-6:] for x in r.get("alert2") or []]))
        check("hold: with the pointer over the activity panel, two polls (new feed) leave its DOM unchanged (same elements, same text)",
              r.get("hoverPollMs", -1) >= 0 and bool(h0.get("keys")) and h1.get("keys") == h0.get("keys") and h1.get("text") == h0.get("text") and h1.get("tagged") == len(h0.get("keys") or []),
              why(r) or {"before": h0, "after": h1, "pollMs": r.get("hoverPollMs")})
        check("hold: the panel starts as poll 1's first 8 actions", h0.get("keys") == exp["poll1"]["feedKeys"], why(r) or (h0.get("keys"), exp["poll1"]["feedKeys"]))
        check("hold: when the pointer leaves, the newest feed goes in", h2.get("keys") == exp["poll2"]["feedKeys"], why(r) or (h2.get("keys"), exp["poll2"]["feedKeys"]))
        check("hold: with focus in the activity panel, a poll leaves its DOM unchanged and focus stays",
              r.get("focusPollMs", -1) >= 0 and bool(f0.get("keys")) and f1.get("keys") == f0.get("keys") and f1.get("text") == f0.get("text") and f1.get("tagged") == len(f0.get("keys") or [])
              and r.get("focusStill") is True, why(r) or {"before": f0, "after": f1, "still": r.get("focusStill")})
        check("hold: when focus leaves, the newest feed goes in", f2.get("keys") == exp["poll1"]["feedKeys"], why(r) or (f2.get("keys"), exp["poll1"]["feedKeys"]))
        a1 = r.get("alert1") or {}
        check("hold: a focused alert is not replaced or moved by a poll that resolves it", r.get("alert0") == exp["poll1"]["alert"] and r.get("alertPollMs", -1) >= 0 and a1.get("same") and a1.get("connected")
              and a1.get("node") == exp["poll1"]["alert"], why(r) or (r.get("alert0"), a1))
        check("hold: when focus leaves the alerts, the newest alerts go in", r.get("alert2") == [exp["poll2"]["alert"]], why(r) or r.get("alert2"))
        no_errors("hold", r)

    # ── 12f. contrast of text on glass ──
    if ran("contrast"):
        r = S("contrast")
        for name, v in sorted((r.get("views") or {}).items()):
            note("contrast (%s): %s runs, min %s; nodes %s, panel %s, inspector %s, column %s" % (name, v.get("n"), fnum(v.get("min")),
                 v.get("node") and "%s (%s)" % (fnum(v["node"]["min"]), v["node"]["worst"]), v.get("panel") and "%s (%s)" % (fnum(v["panel"]["min"]), v["panel"]["worst"]),
                 v.get("inspector") and "%s (%s)" % (fnum(v["inspector"]["min"]), v["inspector"]["worst"]),
                 v.get("column") and "%s (%s)" % (fnum(v["column"]["min"]), v["column"]["worst"])))
            errs = (v.get("errors") or []) + (v.get("consoleErrors") or [])
            check("contrast (%s): every text run on glass clears 4.5:1 against its composited background" % name,
                  (v.get("n") or 0) > 40 and not v.get("low") and not errs and "error" not in v, v.get("error") or errs or v.get("low"))
            if name.endswith("fit"):      # the inspector is open in the 'near' views, so the column is only measured on the fitted floor
                check("contrast (%s): the activity column's text was measured (feed rows, charts)" % name, (v.get("column") or {}).get("n", 0) > 30, v.get("error") or v.get("column"))

    # ── 12g. parent cycles ──
    if ran("cycles"):
        r = S("cycles")
        g, li = r.get("graph") or {}, r.get("list") or {}
        note("cycles: graph %s; list %s; %d list items" % ({k[-5:]: (v.get("openMs"), v.get("title")) for k, v in g.items()},
                                                          {k[-5:]: (v.get("openMs"), v.get("title")) for k, v in li.items()}, len(r.get("items") or [])))
        ok = lambda c: c.get("openMs", -1) >= 0 and (c.get("title") or "").startswith("cycle ") and not c.get("error")   # noqa: E731
        check("cycles: clicking each node of a parent 2-cycle and a self-parent on the floor opens its inspector (no hang)",
              len(g) == 3 and all(ok(c) for c in g.values()), why(r) or g)
        check("cycles: List mode shows every node, the cycle ones included (35)", sorted(r.get("items") or []) == sorted(exp["poll1"]["ids"] + exp["cycles"]),
              why(r) or {"have": len(r.get("items") or []), "missing": sorted(set(exp["cycles"]) - set(r.get("items") or []))})
        check("cycles: clicking each cycle node in List mode opens its inspector", len(li) == 3 and all(ok(c) for c in li.values()), why(r) or li)
        no_errors("cycles", r)

    # ── 12h. focus ring width ──
    if ran("focusring"):
        r = S("focusring")
        f, sl, co = r.get("focus") or {}, r.get("selected") or {}, r.get("cost") or {}
        note("focus ring: k %s, outline %s px (x k = %s, %s), selected ring %s px (x k = %s); per zoom step with a forced style+layout pass: %s ms with the property on the focused and selected nodes, %s ms when also rewriting one on the host"
             % (fnum(f.get("k"), 3), fnum(f.get("width")), fnum((f.get("width") or 0) * (f.get("k") or 0)), f.get("style"), fnum(sl.get("spread")),
                fnum((sl.get("spread") or 0) * (sl.get("k") or 0)), fnum(co.get("own"), 3), fnum(co.get("hostWide"), 3)))
        check("focus ring: on the fitted 306-node floor the keyboard focus outline is at least 1.9 px on screen", f.get("visible") and f.get("style") == "solid"
              and (f.get("k") or 1) < 0.5 and (f.get("width") or 0) * (f.get("k") or 0) >= 1.9, why(r) or f)
        check("focus ring: the selected node's ring is at least 0.95 px on screen there too", sl.get("spread") is not None and sl["spread"] * (sl.get("k") or 0) >= 0.95, why(r) or sl)
        no_errors("focus ring", r)

    # ── 12i. scope change fits ──
    if ran("scopefit"):
        r = S("scopefit")
        c0, c1, c2 = r.get("cam0") or {}, r.get("cam1") or {}, r.get("cam2") or {}
        note("scope fit: all %s -> ?p=valhalla-strategy %s (after %s ms) vs pressing 0 %s" % ({k: round(v, 3) for k, v in c0.items()}, {k: round(v, 3) for k, v in c1.items()},
             fnum(r.get("scopedMs"), 0), {k: round(v, 3) for k, v in c2.items()}))
        check("scope fit: after all -> ?p=valhalla-strategy the camera is what pressing 0 gives", r.get("scopedMs", -1) >= 0 and bool(c1) and bool(c2)
              and abs(c1["x"] - c2["x"]) < 2 and abs(c1["y"] - c2["y"]) < 2 and abs(c1["k"] - c2["k"]) < 0.01 and abs(c1["k"] - c0.get("k", 0)) > 0.05, why(r) or (c0, c1, c2))
        no_errors("scope fit", r)

    # ── 12j. List mode pauses the engine ──
    if ran("listpause"):
        r = S("listpause")
        note("list pause: engine frames in 1 s: graph %s, list %s (paused %s), graph again %s" % (r.get("graphFrames"), r.get("listFrames"), r.get("paused"), r.get("backFrames")))
        check("list pause: the engine draws about 0 frames a second in List mode", (r.get("graphFrames") or 0) > 20 and r.get("listFrames") is not None and r["listFrames"] <= 2 and r.get("paused") is True,
              why(r) or (r.get("graphFrames"), r.get("listFrames"), r.get("paused")))
        check("list pause: back in Graph mode it draws again", (r.get("backFrames") or 0) > 20, why(r) or r.get("backFrames"))
        no_errors("list pause", r)

    # ── 12k. bad bodies ──
    if ran("badpoll"):
        r = S("badpoll")
        k, c = r.get("kept") or {}, r.get("cold") or {}
        note("bad poll: kept %s; cold %s" % ({x: k.get(x) for x in ("garbage", "objnodes", "recovered")}, {x: c.get(x) for x in ("err", "recovered")}))
        for bad in ("garbage", "objnodes"):
            st_ = k.get(bad) or {}
            check("bad poll (%s after a good one): the last floor stays and the app shows the failure" % bad,
                  k.get(bad + "Ms", -1) >= 0 and st_.get("nodes") == len(p1ids) and st_.get("syncErr") and not st_.get("skeleton"), why(r) or st_)
        rc = k.get("recovered") or {}
        check("bad poll: the next good poll clears the failure", k.get("recoverMs", -1) >= 0 and rc.get("nodes") == len(p1ids) and not rc.get("syncErr"), why(r) or rc)
        ce, cr = c.get("err") or {}, c.get("recovered") or {}
        check("bad poll (first answer not JSON): the stage shows the error state, nothing shimmers as if loading", c.get("errMs", -1) >= 0 and ce.get("msg") == "error" and ce.get("shimmer") is False,
              c.get("error") or why(r) or ce)
        check("bad poll (first answer not JSON): the next good poll fills the floor and hides the error", c.get("recoverMs", -1) >= 0 and cr.get("nodes") == len(p1ids) and not cr.get("msg"),
              c.get("error") or why(r) or cr)
        no_errors("bad poll", r)

    # ── 12l. refocus after the node left ──
    if ran("refocus"):
        r = S("refocus")
        af, gone = r.get("after") or {}, exp["gone"]
        note("refocus: %s left the floor (%s ms); Escape from %r -> focus on %s" % (gone["id"][-8:], fnum(r.get("goneMs"), 0), r.get("before"), af))
        check("refocus: closing the inspector of a node that left the floor focuses its nearest surviving ancestor, not <body>",
              gone["parentSurvives"] and r.get("goneMs", -1) >= 0 and "Left the floor" in (r.get("inspGone") or "") and af.get("id") == gone["parent"], why(r) or (af, r.get("inspGone")))
        no_errors("refocus", r)

    # ── 12m. phone list rows keep their state word ──
    if ran("phonelist"):
        r = S("phonelist")
        w = r.get("word") or {}
        note("phone list: %s buttons named like 'cc_tool ... needs you'; state word box %sx%s (%s)" % (r.get("named"), w.get("w"), w.get("h"), w.get("display")))
        check("phone list: at 390 px a row's accessible name still includes its state word (visually hidden, not display:none)",
              (r.get("named") or 0) >= 1 and w.get("display") != "none" and (w.get("w") or 9) <= 1.5 and (r.get("scrollWidth") or 999) <= 390, why(r) or (r.get("named"), w, r.get("scrollWidth")))
        no_errors("phone list", r)

    # ── 12n. robustness ──
    if ran("robust"):
        r = S("robust")
        f = r.get("faces") or {}
        R = exp["robust"]
        note("robust: faces junk=%r str=%r dup=%r order=%r; %d list items" % ((f.get("junk") or {}).get("text"), (f.get("str") or {}).get("text"), ((f.get("dup") or {}).get("text") or "")[:40],
             ((f.get("order") or {}).get("text") or "")[:40], len(r.get("items") or [])))
        check("robust: string and junk costs render (n/a and $12.50) instead of throwing", "n/a" in ((f.get("junk") or {}).get("text") or "") and "$12.50" in ((f.get("str") or {}).get("text") or ""),
              why(r) or (f.get("junk"), f.get("str")))
        check("robust: a duplicate node id keeps the first node, on the floor and in the list", f.get("dupCount") == 1 and "DUPLICATE" not in ((f.get("dup") or {}).get("text") or "")
              and "DUPLICATE" not in (r.get("dupRow") or "") and r.get("dupRow"), why(r) or (f.get("dup"), r.get("dupRow")))
        check("robust: the project inspector opens with an order whose detail is null", r.get("openMs", -1) >= 0 and "An order with no detail" in (r.get("insp") or ""), why(r) or (r.get("openMs"), (r.get("insp") or "")[:200]))
        check("robust: every unique node renders and is listed (%d)" % R["n"], f.get("n") == R["n"] and len(set(r.get("items") or [])) == R["n"] == len(r.get("items") or []),
              why(r) or (f.get("n"), len(r.get("items") or [])))
        no_errors("robust", r)

    # ── 12o. short viewports ──
    if ran("shortview"):
        r = S("shortview")
        for key, c in sorted((r.get("cases") or {}).items()):
            note("short view %s: page %s of %s px, stage %s..%s (%s px), zoom bottom %s" % (key, c.get("scroll"), c.get("inner"), fnum(c.get("stageTop"), 0), fnum(c.get("stageBottom"), 0),
                 fnum(c.get("stageH"), 0), fnum(c.get("zoomBottom"), 0)))
            check("short view %s: no vertical page scroll, the stage and its zoom buttons inside the viewport" % key,
                  c.get("scroll") is not None and c["scroll"] <= c["inner"] and c["stageBottom"] <= c["inner"] and c["zoomBottom"] <= c["inner"] and c["stageH"] >= 250,
                  c.get("error") or c)
            errs = (c.get("errors") or []) + (c.get("consoleErrors") or [])
            check("short view %s: no page or console errors" % key, "errors" in c and not errs, errs or c.get("error"))

    # ── 12c. control mode on the floor ──
    K = exp["ctl"]

    def same_cam(a, b):
        return bool(a and b) and abs(a["k"] - b["k"]) < 1e-6 and abs(a["x"] - b["x"]) < 0.01 and abs(a["y"] - b["y"]) < 0.01

    def same_rect(a, b):
        return bool(a and b) and all(abs(a[k] - b[k]) < 0.5 for k in "ltrb")

    if ran("ctlreview"):
        r = S("ctlreview")
        s, d = r.get("strip") or {}, lambda k: r.get(k) or {}
        rv = s.get("rv") or {}
        note("control: strip %s; inspector box %r; dialog %r / %r, buttons %s; posts %s" % (rv, (d("insp").get("box") or "")[:90], d("d1").get("what"), d("d1").get("who"),
             d("d1").get("buttons"), r.get("posts")))
        check("control (a): B3's strip item shows Review beside its alert", r.get("stripMs", -1) >= 0 and rv.get("label") == "Review" and rv.get("req") == K["b3"]
              and rv.get("act") == "open" and rv.get("visible"), why(r) or s)
        check("control (a): Review is the alert button's sibling (no nested buttons), linked to B3's agent page",
              bool(rv) and rv.get("inAlert") is False and s.get("nested") is False and rv.get("href") == K["b3Href"], why(r) or s)
        check("control (a): beside Review the alert button leaves out the alert's own text (attention.jsonl)",
              bool(s.get("aria")) and K["fixtureMsg"] not in (s.get("aria") or "") and K["fixtureMsg"] not in (s.get("title") or ""), why(r) or (s.get("aria"), s.get("title")))
        s1 = d("s1").get("rv") or {}
        check("control: the other node's request is its own (S1's inspector: Answer, for its question)", s1.get("label") == "Answer" and s1.get("req") == K["q"], why(r) or d("s1"))
        check("control: a node with no open request offers no Review", d("a91").get("n") == 0, why(r) or d("a91"))
        ins = d("insp")
        check("control (b): B3's inspector shows Review in its needs box", (ins.get("rv") or {}).get("label") == "Review" and (ins.get("rv") or {}).get("req") == K["b3"],
              why(r) or ins)
        check("control (b): the needs box shows the request's own command, not the alert's text",
              K["cmdStart"] in (ins.get("box") or "") and K["fixtureMsg"] not in (ins.get("box") or ""), why(r) or ins.get("box"))
        d1 = d("d1")
        check("control (c): Review opens dialog.ctl-review with B3's full (not compact) card", r.get("openMs", -1) >= 0 and d1.get("open") and d1.get("visible")
              and d1.get("req") == K["b3"] and d1.get("compact") is False, why(r) or d1)
        check("control (c): the dialog head names what and who from the request record", d1.get("what") == "Wants to run a command" and d1.get("who") == "general-purpose · Bash",
              why(r) or (d1.get("what"), d1.get("who")))
        check("control (c): the card shows the whole command", (d1.get("box") or "").startswith(K["cmd"]) and "characters not shown" not in (d1.get("box") or ""),
              why(r) or (d1.get("box") or "")[-160:])
        check("control (c): the card shows the bash-guard note", any("bash-guard asked about this command" in g and K["guardText"] in g for g in d1.get("guard") or []),
              why(r) or d1.get("guard"))
        check("control (c): the dialog's Open link is B3's agent page", d1.get("link") == K["b3Href"] and d1.get("linkText") == "Open agent", why(r) or (d1.get("link"), d1.get("linkText")))
        check("control (c): focus goes to the card on open", d1.get("cardFocused") is True, why(r) or d1.get("active"))
        check("control (c): the card's own buttons are the only deciders (Approve, Deny, Deny with reason…)",
              all(b in (d1.get("buttons") or []) for b in ("Approve", "Deny", "Deny with reason…")), why(r) or d1.get("buttons"))
        d2, d3, d5 = d("d2"), d("d3"), d("d5")
        check("control (c): Approve opens the typed confirm (placeholder 'type approve', focused) with Run it disabled",
              d2.get("confirm") is True and d2.get("placeholder") == "type approve" and d2.get("typedFocused") and (d2.get("run") or {}).get("disabled") is True, why(r) or d2)
        check("control (c): Run it stays disabled until 'approve' is typed", (d3.get("run") or {}).get("disabled") is True and (d5.get("run") or {}).get("disabled") is False,
              why(r) or (d3.get("run"), d5.get("run")))
        d4b = d("d4b")
        check("control (c): a closed typed confirm forgets what was typed (opened again: empty, Run it disabled)",
              d4b.get("confirm") is True and d4b.get("typedValue") == "" and (d4b.get("run") or {}).get("disabled") is True, why(r) or d4b)
        check("control (c): nothing was POSTed before Run it", (r.get("posts0"), r.get("posts1"), r.get("posts2"), r.get("posts3")) == (0, 0, 0, 0),
              why(r) or (r.get("posts0"), r.get("posts1"), r.get("posts2"), r.get("posts3")))
        d4 = d("d4")
        check("control (d): Escape closes the open sub-form first; the dialog stays, focus on the card", d4.get("open") and d4.get("confirm") is False and d4.get("cardFocused"),
              why(r) or d4)
        d6 = d("d6")
        act = d6.get("active") or {}
        check("control (d): the next Escape closes the dialog and focus returns to the strip's Review", r.get("closeMs", -1) >= 0 and d6.get("open") is False
              and act.get("inStrip") and act.get("req") == K["b3"], why(r) or (r.get("closeMs"), act))
        check("control (d): that Escape stays in the dialog (the inspector is still open)", d6.get("inspOpen") is True, why(r) or d6)
        d7 = d("d7")
        check("control: Enter on the inspector's Review opens the dialog, focus on the card", r.get("enterMs", -1) >= 0 and d7.get("open") and d7.get("cardFocused"),
              why(r) or (r.get("enterMs"), d7.get("active")))
        k = d("keys")
        check("control: the page's shortcuts do not fire inside the dialog (a, t, ?, j, 0, -)", k.get("themeSame") and k.get("hashSame") and k.get("camSame")
              and k.get("drawer") is False and k.get("help") is False and (k.get("dlg") or {}).get("open"), why(r) or {x: k.get(x) for x in ("themeSame", "hashSame", "camSame", "drawer", "help")})
        check("control: y on the focused card arms (control.js's own y and n work inside the dialog)", "Press y again" in (d("y1").get("arm") or ""), why(r) or d("y1").get("arm"))
        y2 = d("y2")
        check("control: y y on a flagged command opens the typed confirm and runs nothing", y2.get("confirm") is True and y2.get("typedFocused") and r.get("posts3") == 0,
              why(r) or (y2.get("confirm"), y2.get("active"), r.get("posts3")))
        posts = r.get("posts") or []
        check("control: Run it after typing approve POSTs one decide (allow, confirm approve, the card's hash)",
              len(posts) == 1 and posts[0].get("id") == K["b3"] and posts[0].get("action") == "allow" and posts[0].get("confirm") == "approve" and posts[0].get("hash") == "hash-b3",
              why(r) or posts)
        check("control: the card shows the result, and the floor drops Review once the request is no longer open", r.get("sentMs", -1) >= 0 and r.get("goneMs", -1) >= 0,
              why(r) or (d("d8").get("status"), r.get("goneMs")))
        act9 = d("d9").get("active") or {}
        check("control: with its button gone, closing the dialog puts focus on the page, not <body>", d("d9").get("open") is False and act9.get("tag") not in (None, "BODY"),
              why(r) or act9)
        no_errors("ctlreview", r)

    if ran("ctloff"):
        r = S("ctloff")
        o = r.get("off") or {}
        check("control (e): without control mode nothing on the floor can act (no [data-ctl-act], no dialog)",
              o.get("acts") == 0 and o.get("ctlOn") is False and o.get("dialog") is False, why(r) or o)
        check("control (e): without control mode the needs box keeps the alert's text", K["fixtureMsg"][:40] in (o.get("box") or ""), why(r) or o.get("box"))
        no_errors("ctloff", r)

    if ran("ctllocked"):
        r = S("ctllocked")
        dd = r.get("d") or {}
        check("control: before the control code is typed the strip still offers Review", r.get("stripMs", -1) >= 0 and ((r.get("strip") or {}).get("rv") or {}).get("label") == "Review",
              why(r) or r.get("strip"))
        check("control: before the control code, the dialog's card offers the code field and no Approve", dd.get("open") and dd.get("locked") and dd.get("lock") is True
              and "Approve" not in (dd.get("buttons") or []), why(r) or dd)
        check("control: before the control code, y y only focuses the code field and sends nothing", (r.get("afterY") or {}).get("active") and r.get("posts") == 0,
              why(r) or (r.get("afterY"), r.get("posts")))
        no_errors("ctllocked", r)

    if ran("toasts"):
        r = S("toasts")
        for key, c in sorted((r.get("cases") or {}).items()):
            e = c.get("error") or why(r)
            b0, sh, op, cl, lf = (c.get(x) or {} for x in ("before", "shown", "open", "closed", "left"))
            phone = key.startswith("390")
            note("toasts %s: shown after %s ms, stack dx=%s dy=%s away=%s; inspector open dx=%s away=%s, %d toast(s) visible" % (key, fnum(c.get("toastMs"), 0),
                 sh.get("dx"), sh.get("dy"), sh.get("away"), op.get("dx"), op.get("away"), len(op.get("toasts") or [])))
            check("toasts %s: the toast shows on the floor" % key, c.get("toastMs", -1) >= 0 and bool(sh.get("toasts")), e or sh)
            check("toasts %s: the zoom buttons sit under no toast (closed and after the inspector closed)" % key, bool(sh.get("zoom")) and bool(cl.get("toasts"))
                  and not any(meets(sh["zoom"], t) for t in sh["toasts"]) and not any(meets(cl.get("zoom"), t) for t in cl["toasts"]), e or (sh.get("zoom"), sh.get("toasts"), cl.get("toasts")))
            check("toasts %s: nothing on the floor moves for a toast (camera and zoom buttons as before it)" % key,
                  same_cam(b0.get("cam"), sh.get("cam")) and same_rect(b0.get("zoom"), sh.get("zoom")), e or (b0.get("cam"), sh.get("cam"), b0.get("zoom"), sh.get("zoom")))
            check("toasts %s: no toast covers the open inspector or the zoom buttons" % key, bool(op.get("insp"))
                  and not any(meets(op["insp"], t) for t in op.get("toasts") or []) and not any(meets(op.get("zoom"), t) for t in op.get("toasts") or []),
                  e or (op.get("insp"), op.get("toasts"), op.get("zoom")))
            if phone:
                check("toasts %s: while the bottom sheet is up the stack steps aside, and it is back once the sheet closes" % key,
                      op.get("away") is True and not op.get("toasts") and op.get("allToasts", 0) > 0 and cl.get("away") is False and bool(cl.get("toasts")), e or (op, cl))
            else:
                check("toasts %s: with the inspector open the stack sits left of it and stays shown" % key, bool(op.get("toasts"))
                      and all(t["r"] <= op["insp"]["l"] for t in op["toasts"]), e or (op.get("toasts"), op.get("insp")))
            check("toasts %s: leaving the floor puts the stack back where it rests" % key, lf.get("fx") is False and lf.get("away") is False and not lf.get("dx") and not lf.get("dy"),
                  e or lf)
            errs = (c.get("errors") or []) + (c.get("consoleErrors") or [])
            check("toasts %s: no page or console errors" % key, "errors" in c and not errs, errs or c.get("error"))

    if ran("rates"):
        r = S("rates")
        faces = r.get("faces") or []
        s6i, a91i, hud = r.get("s6i") or {}, r.get("a91i") or {}, r.get("hud") or {}
        note("rates: faces %s; S6 burn %s; A91 burn %s; HUD %s; sub %r" % (faces, s6i, a91i, hud, r.get("sub")))
        check("rates: an unpriced running session's face shows new tokens a minute", r.get("s6") == "300 new tok/min", why(r) or r.get("s6"))
        check("rates: no face shows a token rate that counts cache reads", bool(faces) and all(f.startswith("$") or f.endswith(" new tok/min") for f in faces), why(r) or faces)
        check("rates: the inspector's Burn row follows the same rule; cache reads only in its title, said apart",
              s6i.get("text") == "300 new tok/min" and "cache reads, counted apart, 5.7K" in (s6i.get("title") or ""), why(r) or s6i)
        check("rates: a priced node's Burn row is dollars a minute with new tokens beside, never the total",
              a91i.get("text") == "$0.04/min · 600 new tok/min" and "12K" not in (a91i.get("text") or ""), why(r) or a91i)
        want = compact(exp["rates"]["newMin"]) + " new tok/min"
        check("rates: an unpriced floor's HUD Burn is its sessions' new tokens a minute, never kpis.burn_tok_min",
              hud.get("text") == want and "cache reads are not counted" in (hud.get("title") or ""), why(r) or (want, hud))
        check("rates: the finding line uses the same figure", want in (r.get("sub") or "") and compact(exp["rates"]["tokMin"]) + " tok/min" not in (r.get("sub") or ""),
              why(r) or (want, r.get("sub")))
        no_errors("rates", r)

    # ── 13. the activity column ──
    def close(a, b, tol=1.0):
        return a is not None and b is not None and abs(a - b) <= tol

    feed1, feed2 = exp["poll1"]["feed8"], exp["poll2"]["feed8"]
    srx = exp["series"]
    if ran("side"):
        r = S("side")
        for vp, v in sorted((r.get("vp") or {}).items()):
            s0, rows = v.get("s0") or {}, v.get("rows") or []
            note("side %s: visible=%s pressed=%s fxRight=%s rect=%s; %d rows; row 0 -> %s; inspector title %r" % (
                vp, s0.get("visible"), s0.get("pressed"), s0.get("fxRight"), s0.get("rect"), len(rows), (v.get("want") or "")[-8:], ((v.get("afterClick") or {}).get("insp") or {}).get("title")))
            e = (v.get("errors") or []) + (v.get("consoleErrors") or [])
            check("side %s: the activity column is showing by default, its toggle pressed and wired to it" % vp,
                  s0.get("visible") and s0.get("pressed") == "true" and s0.get("controls") == "fx-side" and s0.get("btnVisible") and (v.get("labelled") or {}).get("tag") == "ASIDE"
                  and (v.get("labelled") or {}).get("label") == "Floor activity", v.get("error") or (s0, v.get("labelled")))
            check("side %s: the top ticker is gone and the column holds Recent actions, then Last hour" % vp,
                  v.get("noTicker") and v.get("order") == ["fx-acts", "fx-hour"] and (v.get("labelled") or {}).get("list") == "list", v.get("error") or (v.get("noTicker"), v.get("order"), v.get("labelled")))
            check("side %s: 8 rows, data-keys equal the first 8 of the feed newest first" % vp,
                  [x.get("key") for x in rows] == [x["key"] for x in feed1] and len(rows) == 8, v.get("error") or ([x.get("key", "")[-14:] for x in rows], [x["key"][-14:] for x in feed1]))
            check("side %s: each row's data-node and data-status match the feed (pending -> running, blocked -> error)" % vp,
                  [(x.get("node"), x.get("status")) for x in rows] == [(x["node"], x["status"]) for x in feed1], v.get("error") or [(x.get("node", "")[-6:], x.get("status")) for x in rows])
            check("side %s: a failed row says failed and a blocked row says blocked" % vp,
                  all((x.get("st") or "").lower().find(w["word"]) >= 0 for x, w in zip(rows, feed1) if w["word"] in ("blocked", "ok", "running")) and any("blocked" in (x.get("st") or "").lower() for x in rows)
                  or not any(w["word"] == "blocked" for w in feed1), v.get("error") or [(x.get("st"), w["word"]) for x, w in zip(rows, feed1)])
            ac = v.get("afterClick") or {}
            ins = ac.get("insp") or {}
            lbl = exp["poll1"]["labels"].get(v.get("want"), "\0")
            check("side %s: a row click focuses its node (engine selection, inspector shows its label) and the column leaves" % vp,
                  v.get("openMs", -1) >= 0 and ac.get("sel") == v.get("want") and lbl in (ins.get("title") or "") and not (ac.get("side") or {}).get("visible"), v.get("error") or (ac.get("sel"), v.get("want"), ins.get("title"), (ac.get("side") or {}).get("visible")))
            check("side %s: the stage's --fx-right is the same with the column and with the inspector (within 1px)" % vp,
                  s0.get("fxRight", 0) > 100 and close(s0.get("fxRight"), (ac.get("side") or {}).get("fxRight")), v.get("error") or (s0.get("fxRight"), (ac.get("side") or {}).get("fxRight")))
            ae = v.get("afterEsc") or {}
            check("side %s: Escape closes the inspector and the column is back" % vp, v.get("backMs", -1) >= 0 and not ae.get("insp") and (ae.get("side") or {}).get("visible") and (ae.get("side") or {}).get("pressed") == "true",
                  v.get("error") or ae)
            check("side %s: after Fit no node is under the column" % vp, v.get("under0") == [], v.get("error") or v.get("under0"))
            check("side %s: after Fit no node is under the open inspector" % vp, v.get("under1") == [], v.get("error") or v.get("under1"))
            check("side %s: no page or console errors" % vp, not e and "error" not in v, v.get("error") or e)
        check("side: both viewports ran", len(r.get("vp") or {}) == 2, why(r))

    if ran("charts"):
        r = S("charts")
        cs = r.get("charts") or []
        note("charts: %s; %s series.start samples" % ([(c.get("k"), c.get("cols"), c.get("hits"), c.get("now"), c.get("sum")) for c in cs], r.get("starts")))
        check("charts: three .fx-chart in order tokens, tools, agents", [c.get("k") for c in cs] == ["tokens", "tools", "agents"], why(r) or [c.get("k") for c in cs])
        check("charts: headings Tokens, Tool calls, Working agents", [c.get("label") for c in cs] == ["Tokens", "Tool calls", "Working agents"], why(r) or [c.get("label") for c in cs])
        idx = ",".join(str(i) for i in range(20))
        check("charts: each svg has 20 g.fx-col and 20 rect.hit, indexed 0..19 oldest first",
              len(cs) == 3 and all(c.get("cols") == 20 and c.get("hits") == 20 and c.get("colIdx") == idx and c.get("hitIdx") == idx for c in cs), why(r) or [(c.get("cols"), c.get("hits")) for c in cs])
        check("charts: each svg is role=img, focusable, and described in a sentence",
              len(cs) == 3 and all(c.get("role") == "img" and c.get("tab") == "0" and len((c.get("aria") or "").split()) >= 4 for c in cs), why(r) or [(c.get("role"), c.get("tab"), c.get("aria")) for c in cs])
        check("charts: with data, every chart draws marks", len(cs) == 3 and all(c.get("marks", 0) > 0 for c in cs), why(r) or [c.get("marks") for c in cs])
        vh = r.get("vh") or []
        check("charts: the 20 buckets x 3 measures are also in a screen-reader table", any(x.get("k") == "hour" and x.get("hasTable") and x.get("rows", 0) >= 20 for x in vh) or sum(1 for x in vh if x.get("hasTable")) == 3, why(r) or vh)
        now = {c["k"]: c for c in cs}
        for kind in ("tokens", "tools", "agents"):
            c = now.get(kind) or {}
            # "Now" is the instrument panel's: a header gives the hour (its total, or its peak), and the
            # current bucket's figure is in the svg's sentence (and its tooltip), never a second "now".
            check("charts: the %s header gives the hour only (no .fx-chart-now, no \"now\")" % kind,
                  bool(c) and not c.get("hasNow") and not re.search(r"\bnow\b", c.get("head") or "") and "in all" in (c.get("head") or ""), why(r) or c.get("head"))
            check("charts: the %s svg's sentence has the hour and the current bucket's value (%s)" % (kind, srx["now"][kind][0]),
                  "in all" in (c.get("aria") or "") and re.search(r"; %s (so far )?in the current 3 minutes\.$" % re.escape(srx["now"][kind][0]), c.get("aria") or ""), why(r) or c.get("aria"))
        sm = lambda k: (now.get(k) or {}).get("sum") or ""   # noqa: E731
        check("charts: .fx-chart-sum of tokens and tools hold the hour total", any(f in sm("tokens") for f in srx["sum"]["tokens"]) and any(f in sm("tools") for f in srx["sum"]["tools"]), why(r) or (sm("tokens"), sm("tools"), srx["sum"]))
        check("charts: .fx-chart-sum of agents holds the peak and the distinct total", srx["sum"]["agentsPeak"] in sm("agents") and srx["sum"]["agentsTotal"] in sm("agents"), why(r) or (sm("agents"), srx["sum"]))
        for kind in ("tokens", "tools", "agents"):
            for i in srx["ks"]:
                t = (r.get("tips") or {}).get(kind + str(i)) or {}
                tip = (t.get("tip") or {})
                info = tip.get("info") or {}
                want = srx["tips"][kind + str(i)]
                rows = [[x.get("name"), x.get("value")] for x in info.get("rows") or []]
                title = info.get("title")
                ok_title = bool(title) and title in (tip.get("titles") or [])
                ok_rows = (sorted(rows) == sorted(want)) if want != [["Nothing recorded", ""]] else "Nothing recorded" in (info.get("text") or "")
                if len(want) > 1 and kind == "tokens":
                    ok_rows = ok_rows and bool(info.get("rows")) and info["rows"][-1].get("name") == "Total"
                check("charts: hovering %s bucket %d shows the title HH:MM–HH:MM%s and the exact values %s" % (kind, i, " (so far)" if i == 19 else "", [x[1] for x in want]),
                      ok_title and ok_rows and t.get("hidden") is True, t.get("error") or why(r) or {"title": title, "want_titles": tip.get("titles"), "rows": rows, "want": want, "hiddenAfter": t.get("hidden")})
        for kind in ("tokens", "tools", "agents"):
            k = (r.get("kb") or {}).get(kind) or {}
            steps_ = [("end", 19), ("left", 18), ("home", 0), ("right", 1)]
            ok = bool(k) and "error" not in k
            bad = None
            for name, i in steps_:
                x = k.get(name) or {}
                info = x.get("info") or {}
                want = srx["tips"].get(kind + str(i)) or tip_rows(POLL1["series"], kind, i)
                rows = [[y.get("name"), y.get("value")] for y in info.get("rows") or []]
                if not (info.get("title") in (x.get("titles") or []) and sorted(rows) == sorted(want)):
                    ok = False
                    bad = bad or (name, i, info.get("title"), rows, want)
            check("charts: keyboard on the %s svg (End, ArrowLeft, Home, ArrowRight) moves the tooltip bucket by bucket; blur hides it" % kind, ok and k.get("hiddenAfterBlur") is True, k.get("error") or why(r) or bad or k.get("hiddenAfterBlur"))
        no_errors("charts", r)

    if ran("colfit"):
        r = S("colfit")
        # (size, the fewest whole rows, the smallest plot) a window of that size must give, without the column scrolling
        for vp, rows_min, plot_min in (("1366x768", 3, 40), ("1440x900", 7, 40), ("1920x1080", 8, 80), ("1280x720", 1, 30)):
            v = (r.get("vp") or {}).get(vp) or {}
            note("colfit %s: %s of %s rows whole (list %s px, row %s), plots %s, hour bottom %s / slot %s, over %s" % (
                vp, v.get("whole"), v.get("n"), v.get("listH"), fnum(v.get("rowH"), 1), v.get("plots"), fnum(v.get("hourBottom"), 1), fnum(v.get("sideBottom"), 1), v.get("over")))
            plots = v.get("plots") or []
            check("colfit %s: three plots of at least %d px (svgs as tall)" % (vp, plot_min),
                  len(plots) == 3 and all(isinstance(x, (int, float)) and x >= plot_min for x in plots) and all(s >= p + 7 for s, p in zip(v.get("svgs") or [], plots)), v.get("error") or why(r) or (plots, v.get("svgs")))
            check("colfit %s: at least %d whole rows of Recent actions, and the Last hour panel ends inside the slot without the column scrolling" % (vp, rows_min),
                  v.get("visible") and (v.get("whole") or 0) >= min(rows_min, v.get("n") or 0) and v.get("n") == 8 and not v.get("over")
                  and v.get("hourBottom", 9e9) <= v.get("sideBottom", 0) + 0.5, v.get("error") or why(r) or v)
            cut = (v.get("whole") or 0) < (v.get("n") or 0)
            check("colfit %s: %s" % (vp, "the list is whole rows plus a 12 px peek of the next, faded (data-more)" if cut else "every row shows, unfaded"),
                  (abs((v.get("listH") or 0) - (v.get("whole") or 0) * (v.get("rowH") or 0) - 12) <= 1 and v.get("more")) if cut else (not v.get("more") and v.get("whole") == v.get("n")),
                  v.get("error") or why(r) or (v.get("listH"), v.get("whole"), v.get("rowH"), v.get("more")))
            check("colfit %s: no page or console errors" % vp, "errors" in v and not (v.get("errors") or []) and not (v.get("consoleErrors") or []), v.get("error") or v.get("errors") or v.get("consoleErrors"))
        v = (r.get("vp") or {}).get("1920x1080") or {}
        check("colfit 1920x1080: with every row showing, the plots take the rest and the column fills its slot (to within 4 px)",
              v.get("visible") and 0 <= v.get("sideBottom", 0) - v.get("hourBottom", 9e9) <= 4, v.get("error") or (v.get("hourBottom"), v.get("sideBottom")))
        v = (r.get("vp") or {}).get("1366x650") or {}
        sc = v.get("scroll") or [0, 0]
        check("colfit 1366x650: too short for 1 row and 30 px plots, the column scrolls as one (the last resort), its plots still 30 px",
              v.get("visible") and v.get("over") and sc[0] > sc[1] and min(v.get("plots") or [0]) >= 30 and (v.get("whole") or 0) >= 1, v.get("error") or why(r) or v)

    if ran("sidebtn"):
        r = S("sidebtn")
        s0, s1, s2 = r.get("s0") or {}, r.get("s1") or {}, r.get("s2") or {}
        s3, s4 = r.get("s3") or {}, r.get("s4") or {}
        note("sidebtn: --fx-right %s -> %s -> %s; with a node %s; after the press %s" % (s0.get("fxRight"), s1.get("fxRight"), s2.get("fxRight"), (s3.get("side") or {}).get("fxRight"), (s4.get("side") or {}).get("fxRight")))
        check("sidebtn: aria-pressed is true at 1440 by default", s0.get("pressed") == "true" and s0.get("visible"), why(r) or s0)
        check("sidebtn: pressing it hides the column, aria-pressed false and --fx-right drops to 0",
              r.get("hideMs", -1) >= 0 and not s1.get("visible") and s1.get("pressed") == "false" and (s1.get("fxRight") or 0) < 1, why(r) or s1)
        check("sidebtn: pressing it again restores the column and the same --fx-right",
              r.get("showMs", -1) >= 0 and s2.get("visible") and s2.get("pressed") == "true" and close(s2.get("fxRight"), s0.get("fxRight")), why(r) or (s2, s0.get("fxRight")))
        check("sidebtn: with a node selected the column is replaced by the inspector",
              r.get("inspMs", -1) >= 0 and s3.get("insp") and not (s3.get("side") or {}).get("visible"), why(r) or s3)
        check("sidebtn: pressing it with the inspector open closes the inspector and shows the column",
              r.get("backMs", -1) >= 0 and not s4.get("insp") and (s4.get("side") or {}).get("visible") and (s4.get("side") or {}).get("pressed") == "true", why(r) or s4)
        no_errors("sidebtn", r)

    if ran("sidephone"):
        r = S("sidephone")
        s0, s1, s2 = r.get("s0") or {}, r.get("s1") or {}, r.get("s2") or {}
        sd, st = s1.get("side") or {}, (s1.get("side") or {}).get("stage") or {}
        rc = sd.get("rect") or {}
        note("sidephone 390x844: default %s; sheet %s over stage %s; scroll %s" % (s0.get("side", {}).get("visible"), {k: round(x) for k, x in rc.items()}, {k: round(x) for k, x in st.items()}, r.get("sc1")))
        check("sidephone: the column is hidden by default and aria-pressed is false", not (s0.get("side") or {}).get("visible") and (s0.get("side") or {}).get("pressed") == "false" and (s0.get("side") or {}).get("btnVisible") and s0.get("hud"), why(r) or s0)
        check("sidephone: pressing the toggle shows the column as a bottom sheet (full stage width, anchored at the stage bottom, at most ~52%)",
              r.get("showMs", -1) >= 0 and sd.get("visible") and sd.get("pressed") == "true" and rc and st
              and rc["l"] - st["l"] <= 17 and st["r"] - rc["r"] <= 17 and (rc["r"] - rc["l"]) >= (st["r"] - st["l"]) - 34 and st["b"] - rc["b"] <= 17
              and (rc["b"] - rc["t"]) <= 0.56 * (st["b"] - st["t"]) and rc["t"] > (st["t"] + (st["b"] - st["t"]) * 0.3), why(r) or (rc, st))
        check("sidephone: the sheet holds 8 rows and three charts", s1.get("rows") == 8 and s1.get("charts") == 3, why(r) or (s1.get("rows"), s1.get("charts")))
        check("sidephone: no horizontal page scroll with the sheet closed or up", all(x and x["sw"] <= x["cw"] and x["bsw"] <= x["cw"] for x in (r.get("sc0"), r.get("sc1"))), why(r) or (r.get("sc0"), r.get("sc1")))
        check("sidephone: the totals step aside while the sheet is up", not s1.get("hud"), why(r) or s1.get("hud"))
        check("sidephone: selecting a node replaces the sheet with the inspector sheet (no horizontal scroll either)",
              r.get("inspMs", -1) >= 0 and (s2.get("insp") or {}).get("visible") and not (s2.get("side") or {}).get("visible") and (r.get("sc2") or {}).get("sw", 9e9) <= (r.get("sc2") or {}).get("cw", 0), why(r) or (s2.get("insp", {}).get("visible"), s2.get("side"), r.get("sc2")))
        no_errors("sidephone", r)

    if ran("sidetheme"):
        r = S("sidetheme")
        for scheme, v in sorted((r.get("v") or {}).items()):
            for sel in (".fx-acts", ".fx-hour"):
                x = v.get(sel) or {}
                check("sidetheme (%s): %s is frosted glass (backdrop-filter set, background alpha < 1)" % (scheme, sel),
                      x.get("vis") and x.get("filter") not in (None, "", "none") and x.get("alpha") is not None and x["alpha"] < 1, v.get("error") or why(r) or x)
            e = (v.get("errors") or []) + (v.get("consoleErrors") or [])
            check("sidetheme (%s): no page or console errors" % scheme, not e, e)

    if ran("sidereduced"):
        r = S("sidereduced")
        a = r.get("after") or {}
        note("sidereduced: swap after %s ms; enter seen %s, now %s; transition %r; animations %s" % (fnum(r.get("swapMs"), 0), a.get("enterSeen"), a.get("enterNow"), a.get("transition"), a.get("running")))
        check("sidereduced: poll 2's feed goes in (first row is poll 2's newest)", r.get("first0") == [x["key"] for x in feed1] and r.get("swapMs", -1) >= 0 and a.get("rows") == 8 and a.get("reduce") is True, why(r) or (r.get("swapMs"), a))
        check("sidereduced: the swap adds no .enter rows", a.get("enterSeen") == 0 and a.get("enterNow") == 0, why(r) or a)
        check("sidereduced: the column has no transform transition and no running animation", a.get("transform") is False and a.get("running") == [], why(r) or a)
        no_errors("sidereduced", r)

    if ran("sidestates"):
        r = S("sidestates")
        for name in ("empty", "robust", "noseries"):
            v = (r.get("v") or {}).get(name) or {}
            cs = v.get("charts") or []
            e = (v.get("errors") or []) + (v.get("consoleErrors") or [])
            check("sidestates (%s): no page or console errors" % name, not e and "error" not in v, v.get("error") or e or why(r))
            check("sidestates (%s): the column is showing with three charts of 20 g.fx-col and 20 rect.hit and no data marks" % name,
                  (v.get("side") or {}).get("visible") and [c.get("k") for c in cs] == ["tokens", "tools", "agents"] and all(c.get("cols") == 20 and c.get("hits") == 20 and c.get("marks") == 0 for c in cs),
                  v.get("error") or why(r) or [(c.get("k"), c.get("cols"), c.get("hits"), c.get("marks")) for c in cs])
        v = (r.get("v") or {}).get("empty") or {}
        check("sidestates (empty): .fx-evs-none is shown and there are no rows", (v.get("none") or {}).get("visible") and v.get("rows") == 0 and bool((v.get("none") or {}).get("text")), v.get("error") or why(r) or (v.get("none"), v.get("rows")))
        check("sidestates (empty): hovering an empty bucket says Nothing recorded", "Nothing recorded" in ((v.get("tip") or {}).get("text") or ""), v.get("error") or v.get("tipError") or v.get("tip"))
        for name, n in (("robust", len(POLL1["feed"])), ("noseries", len(POLL1["feed"]))):
            v = (r.get("v") or {}).get(name) or {}
            check("sidestates (%s): the feed still shows its 8 rows" % name, v.get("rows") == 8 and not (v.get("none") or {}).get("visible"), v.get("error") or why(r) or (v.get("rows"), v.get("none")))
    # ── 13c. the floor's extra visuals ──
    if ran("visuals"):
        r = S("visuals")
        I = exp["ids"]
        no_errors("visuals", r)
        near, far, fa, na, rf, rn = (r.get(k) or {} for k in ("near", "far", "farAfter", "nearAfter", "reducedFar", "reducedNear"))
        note("visuals: lod %s/%s, far rings failed %s stalled %s working %s, halo %s; poll %s ms, replaced %s; tick %s"
             % (near.get("lod"), far.get("lod"), far.get("failed"), far.get("stalled"), far.get("working"), far.get("halo"), fnum(r.get("pollMs"), 0),
                r.get("replaced"), r.get("tick")))
        check("visuals: the step zoomed to near, then far", near.get("lod") == "near" and far.get("lod") == "far" and na.get("lod") == "near", why(r) or (near.get("lod"), far.get("lod")))
        ring = lambda o, k: (o.get(k) or {})   # noqa: E731
        # status weight: from the node's state, at far only
        check("visuals: at far a failed node has a solid red ring inside its edge (>= 3px)",
              ring(far, "failed").get("style") == "solid" and ring(far, "failed").get("width", 0) >= 3 and ring(far, "failed").get("red"), why(r) or ring(far, "failed"))
        check("visuals: at far a stalled node has a dashed amber ring (>= 3px)",
              ring(far, "stalled").get("style") == "dashed" and ring(far, "stalled").get("width", 0) >= 3 and ring(far, "stalled").get("amber"), why(r) or ring(far, "stalled"))
        check("visuals: at far a working node has no ring", ring(far, "working").get("style") == "none", why(r) or ring(far, "working"))
        check("visuals: at far the needs-you halo is wider (12px out, not 6px)", (far.get("halo") or {}).get("top") == -12 and (near.get("halo") or {}).get("top") == -6,
              why(r) or (far.get("halo"), near.get("halo")))
        check("visuals: up close no node has a status ring (the border and the halo carry it there)",
              all(ring(near, k).get("style") == "none" for k in ("failed", "stalled", "working")), why(r) or [ring(near, k) for k in ("failed", "stalled", "working")])
        check("visuals: a poll that fails A91 gives it the red ring in place", ring(far, "a91").get("style") == "none" and ring(fa, "a91").get("style") == "solid" and ring(fa, "a91").get("red"),
              why(r) or (ring(far, "a91"), ring(fa, "a91")))
        # time in state: from `since`, for working and your-turn sessions and agents
        now1 = POLL1["now"]
        want = {n["id"]: now1 - n["since"] for n in POLL1["nodes"] if n["kind"] in ("session", "agent") and n.get("since")
                and (n.get("needs") in (None, "idle")) and n["state"] in ("tool", "generating", "waiting", "idle")}
        got = near.get("since") or {}
        check("visuals: exactly the working and your-turn sessions and agents carry a time in state", sorted(got) == sorted(want),
              why(r) or {"missing": sorted(set(want) - set(got))[:4], "extra": sorted(set(got) - set(want))[:4]})
        bad = {i: (g, want.get(i)) for i, g in got.items() if i in want and (
            g.get("hidden") != (want[i] < 60) or (not g.get("hidden") and g.get("text") not in ("%dm" % (want[i] // 60), "%dm" % (want[i] // 60 + 1))))}
        check("visuals: a node under a minute in its state shows no time; past it, whole minutes since `since`", got and not bad, why(r) or bad)
        drop = {i: g for i, g in got.items() if not g.get("hidden") and not g.get("shown") and "near" not in g.get("drop", "").split()}
        check("visuals: every time that is due shows up close, unless line 2 has no room for it there (data-drop)", got and not drop, why(r) or drop)
        check("visuals: the time is aria-hidden (the inspector says it; a focused node is not re-announced)", got and all(g.get("aria") == "true" for g in got.values()), why(r) or got)
        check("visuals: S1 shows its time up close", (got.get(I["session"]) or {}).get("shown") and (got.get(I["session"]) or {}).get("text") == "14m", why(r) or got.get(I["session"]))
        check("visuals: no time at far (line 2 is gone)", (far.get("since") or {}) and not any(g.get("shown") for g in far["since"].values()), why(r) or far.get("since"))
        ga = na.get("since") or {}
        check("visuals: a poll moves S1 to 2m and E9 under a minute, in place", (ga.get(I["session"]) or {}).get("text") == "2m" and (ga.get(I["e9"]) or {}).get("hidden") is True,
              why(r) or (ga.get(I["session"]), ga.get(I["e9"])))
        check("visuals: a node that failed carries no time", I["agent"] not in ga and I["agent"] in got, why(r) or (ga.get(I["agent"]), got.get(I["agent"])))
        tk = r.get("tick") or {}
        check("visuals: between polls the time crosses its first minute within the 5 s tick, without rebuilding the face",
              tk.get("ms", -1) >= 0 and tk.get("sameFace") and tk.get("sameSpan") and (tk.get("before") or {}).get("hidden") is True, why(r) or tk)
        # rework: from detail.rework, orders only, only when > 0
        rw, rwa = near.get("rework") or {}, na.get("rework") or {}
        check("visuals: rework shows from detail.rework (FO-002 sent back once; none on FO-003 and FO-004)",
              set(rw) == {I["fo2"]} and rw[I["fo2"]].get("text") == "rework 1" and rw[I["fo2"]].get("shown") and rw[I["fo2"]].get("aria") == "true", why(r) or rw)
        check("visuals: a poll updates rework in place (FO-002 2, FO-003 1)",
              (rwa.get(I["fo2"]) or {}).get("text") == "rework 2" and (rwa.get(I["order"]) or {}).get("text") == "rework 1", why(r) or rwa)
        check("visuals: no rework at far (line 2 is gone)", not any(g.get("shown") for g in (far.get("rework") or {}).values()), why(r) or far.get("rework"))
        # the poll: no relayout, no element replaced, the camera holds
        p0, p1 = far.get("pos") or {}, fa.get("pos") or {}
        mv = max([math.hypot(p1[i]["x"] - p0[i]["x"], p1[i]["y"] - p0[i]["y"]) for i in p0 if i in p1] or [None]) if p0 and p1 else None
        check("visuals: the poll moves no node (< 0.5 px) and keeps the camera", mv is not None and mv < 0.5 and far.get("cam") == fa.get("cam"), why(r) or (mv, far.get("cam"), fa.get("cam")))
        check("visuals: the poll replaces no node element", r.get("replaced") == [], why(r) or r.get("replaced"))
        # reduced motion: the marks stay, nothing of them moves
        check("visuals: reduced motion keeps the far rings (static paint)", ring(rf, "failed").get("style") == "solid" and ring(rf, "stalled").get("style") == "dashed",
              why(r) or (ring(rf, "failed"), ring(rf, "stalled")))
        check("visuals: reduced motion runs no animation on the floor (the halo does not breathe)", rf.get("moving") == 0 and rn.get("moving") == 0 and (rf.get("halo") or {}).get("anims") == 0,
              why(r) or (rf.get("moving"), rn.get("moving"), rf.get("halo")))
        stat = [g for g in list((rn.get("since") or {}).values()) + list((rn.get("rework") or {}).values()) if g.get("trans") not in ("0s", None)]
        check("visuals: the time and the rework never transition; the ring is not in the face's transitions",
              not stat and (rn.get("since") or rn.get("rework")) and "outline" not in (ring(rf, "failed").get("trans") or "outline"), why(r) or (stat, ring(rf, "failed")))

    if ran("shots") and SHOTS:
        r = S("shots")
        for f in r.get("files") or []:
            note("screenshot: " + f)
        check("screenshots: all twenty-nine were written", len([f for f in r.get("files") or [] if f.endswith(".png") and os.path.exists(f)]) == 29, why(r) or r.get("files"))


def test_browser(srv):
    if not (os.path.isdir(PLAYWRIGHT) and os.path.exists(CHROME) and shutil.which("node")):
        print("browser: skipped (no playwright-core, Chrome or node)")
        return
    exp = expectations()
    check("fixtures: poll 1 has 32 nodes, poll 2 33 (2 added, 1 removed)",
          len(exp["poll1"]["ids"]) == 32 and len(exp["poll2"]["ids"]) == 33 and len(exp["poll2"]["added"]) == 2 and len(exp["poll2"]["removed"]) == 1,
          (len(exp["poll1"]["ids"]), len(exp["poll2"]["ids"])))
    steps = run_browser(srv, exp)
    if steps is not None:
        evaluate(steps, exp)


def main():
    srv = start_server()
    try:
        test_static(srv)
        test_browser(srv)
    finally:
        srv.shutdown()
        srv.server_close()
        shutil.rmtree(BASE, ignore_errors=True)
    fails = [r for r in RESULTS if not r[1]]
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"FAIL  {name}  {detail}")
    print(f"total={len(RESULTS)}  pass={len(RESULTS) - len(fails)}  fail={len(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
