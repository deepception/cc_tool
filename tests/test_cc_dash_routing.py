#!/usr/bin/env python3
"""cc-dash routing: who picked each agent's model and effort (lib/cc_dash/routing.py, /api/routing,
web/routing.js, the Overview's "Routing" panel).

Builds a fake ${CLAUDE_CONFIG_DIR}/projects tree in a temp dir (project "demo", plus one agent in "other"):
  main (Opus 5.5, effort recorded xhigh): a 2-hour idle gap then a 60K-token cache write (a main
       rewrite), and a 30-minute gap then a 70K write (a big write that is not an expiry)
    ax  Agent call named model sonnet and effort medium; "Implement Task 3", edits + pytest
    ad  no model; "Implement Task 4", edits + pytest, Opus; an 85K write after 400 s idle (an
        expiry rewrite at the 5-minute subagent TTL), another after 100 s (not one)
    ae  Explore, no model, read-only, Opus (mechanical)
    aj  no model, "Review the plan", Opus, call named effort low (judgment)
    at  agent type `typed` whose definition sets model: sonnet; call named none (source: type)
    ab  no model, a reply with a 400K context (context over 300K)
    wf1/aw1  workflow agent with meta.model sonnet; aw2 without one
    aold  replies five days ago (outside the window)
  other project: ao, no model, Opus
then checks the classifier rules, model-source detection, effort, rewrites, flags and savings,
the scope filter, the payload shape, the endpoint's auth and (with Chrome and playwright-core)
the panel in a browser: content, a row opening the agent, and no DOM churn across polls.
Nothing outside the temp dir is touched.

Run:  python3 tests/test_cc_dash_routing.py        (SHOTS=<dir> also writes screenshots)
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
sys.path.insert(0, os.path.join(REPO, "lib"))
sys.path.insert(0, os.path.join(REPO, "tests"))
from cc_dash import routing  # noqa: E402
from cc_dash.server import make_server  # noqa: E402
from cc_dash.store import Store  # noqa: E402
from dash_pairing import auto_token  # noqa: E402

PLAYWRIGHT = os.environ.get("CC_DASH_PLAYWRIGHT", "/home/lukasz/Files/hobby/color-analyzer/webapp/node_modules/playwright-core")
CHROME = os.environ.get("CC_DASH_CHROME", "/usr/bin/google-chrome")
SHOTS = os.environ.get("SHOTS", "")
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print("%s %s" % ("PASS" if cond else "FAIL", name) + ("" if cond else "  " + str(detail)[:300]))


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


BASE = tempfile.mkdtemp(prefix="cc-dash-routing-")
CFG = os.path.join(BASE, "claude")
WORK = os.path.join(BASE, "work", "demo")
OTHER = os.path.join(BASE, "work", "other")
for d in (WORK, OTHER):
    os.makedirs(d)
PDIR = os.path.join(CFG, "projects", WORK.replace("/", "-"))
ODIR = os.path.join(CFG, "projects", OTHER.replace("/", "-"))
os.makedirs(PDIR)
os.makedirs(ODIR)
os.makedirs(os.path.join(WORK, ".claude", "agents"))
with open(os.path.join(WORK, ".claude", "agents", "typed.md"), "w") as fh:
    fh.write("---\nname: typed\ndescription: x\nmodel: sonnet\n---\nbody\n")
NOW = time.time()
T0 = NOW - 6 * 3600
OPUS, SONNET = "claude-opus-5-5", "claude-sonnet-5-5"


def dump(path, recs):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        for r in recs:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")


class T:
    """One transcript being written."""

    def __init__(self, sid, cwd=WORK, agent=None):
        self.sid, self.cwd, self.agent, self.recs, self.n = sid, cwd, agent, [], 0

    def base(self, kind, t, **kw):
        r = {"type": kind, "timestamp": iso(t), "sessionId": self.sid, "cwd": self.cwd, "version": "2.1.304",
             "isSidechain": bool(self.agent), "uuid": "u%d" % len(self.recs)}
        if self.agent:
            r["agentId"] = self.agent
        r.update(kw)
        return r

    def prompt(self, t, text):
        self.recs.append(self.base("user", t, message={"role": "user", "content": text}))

    def reply(self, t, model, effort="medium", inp=1000, out=200, cr=0, cw=0, tools=()):
        self.n += 1
        mid = "m-%s-%d" % (self.agent or self.sid[:4], self.n)
        u = {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw,
             "cache_creation": {"ephemeral_5m_input_tokens": cw, "ephemeral_1h_input_tokens": 0}}
        blocks = [{"type": "text", "text": "ok"}] + list(tools)
        for b in blocks:
            r = self.base("assistant", t, message={"id": mid, "model": model, "role": "assistant", "content": [b],
                                                    "stop_reason": "tool_use" if tools else "end_turn", "usage": u})
            if effort:
                r["effort"] = effort
            self.recs.append(r)

    def spawn(self, t, model, tid, desc, sub_model=None, effort=None, agent_id=None, subagent_type="general-purpose"):
        ti = {"description": desc, "prompt": "do " + desc, "subagent_type": subagent_type}
        if sub_model:
            ti["model"] = sub_model
        if effort:
            ti["effort"] = effort
        self.reply(t, model, tools=[{"type": "tool_use", "id": tid, "name": "Agent", "input": ti}])
        r = self.base("user", t + 1, message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": "done"}]})
        r["toolUseResult"] = {"status": "completed", "agentId": agent_id}
        self.recs.append(r)

    def tool(self, t, model, name, ti, effort="medium", **kw):
        self.reply(t, model, effort=effort, tools=[{"type": "tool_use", "id": "tu%d" % self.n, "name": name, "input": ti}], **kw)


def sub_dir(proj, sid, run=None):
    return os.path.join(proj, sid, "subagents") if not run else os.path.join(proj, sid, "subagents", "workflows", run)


def finish(proj, sid, t, meta, run=None):
    d = sub_dir(proj, sid, run)
    dump(os.path.join(d, "agent-%s.jsonl" % t.agent), t.recs)
    with open(os.path.join(d, "agent-%s.meta.json" % t.agent), "w") as fh:
        json.dump(meta, fh)


SID = "bbbbbbbb-0000-0000-0000-000000000001"
SO = "bbbbbbbb-0000-0000-0000-000000000002"

# ── main session ──
m = T(SID)
m.prompt(T0, "lead the work")
m.reply(T0 + 1, OPUS, "xhigh", inp=1000, cw=20000)
m.spawn(T0 + 10, OPUS, "tx", "Implement Task 3: parser", sub_model="sonnet", effort="medium", agent_id="ax")
m.spawn(T0 + 20, OPUS, "td", "Implement Task 4: api", agent_id="ad")
m.spawn(T0 + 30, OPUS, "te", "Find where Config is defined", agent_id="ae", subagent_type="Explore")
m.spawn(T0 + 40, OPUS, "tj", "Review the plan", effort="low", agent_id="aj")
m.spawn(T0 + 50, OPUS, "tt", "Refactor the module", agent_id="at", subagent_type="typed")
m.spawn(T0 + 60, OPUS, "tb", "Write the long plan", agent_id="ab")
m.reply(T0 + 7300, OPUS, "xhigh", inp=500, cr=0, cw=60000)          # 2 h idle: a main rewrite
m.reply(T0 + 7300 + 1800, OPUS, "xhigh", inp=500, cr=0, cw=70000)   # 30 min idle: a big write, not an expiry
m.reply(NOW - 100, OPUS, "xhigh", inp=500, cr=100000, cw=100)
dump(os.path.join(PDIR, SID + ".jsonl"), m.recs)


def meta_of(desc, tid, **kw):
    d = {"agentType": kw.pop("agentType", "general-purpose"), "description": desc, "toolUseId": tid, "spawnDepth": 1}
    d.update(kw)
    return d


a = T(SID, agent="ax")
a.prompt(T0 + 12, "Implement the parser per the spec.")
a.tool(T0 + 13, SONNET, "Edit", {"file_path": "/x/a.py", "old_string": "a", "new_string": "b"}, inp=3000, cw=10000)
a.tool(T0 + 20, SONNET, "Bash", {"command": "pytest tests/test_a.py"}, inp=300, cr=10000)
a.reply(T0 + 30, SONNET, "medium", inp=300, cr=11000)
finish(PDIR, SID, a, meta_of("Implement Task 3: parser", "tx", model="sonnet", effort="medium"))

a = T(SID, agent="ad")
a.prompt(T0 + 22, "Implement the API per the plan, Task 4.")
a.tool(T0 + 23, OPUS, "Edit", {"file_path": "/x/api.py", "old_string": "a", "new_string": "b"}, "xhigh", inp=5000, cw=40000)
a.tool(T0 + 24, OPUS, "Bash", {"command": "pytest -q tests/"}, "xhigh", inp=100, cr=40000)
a.reply(T0 + 24 + 400, OPUS, "xhigh", inp=100, cr=5000, cw=85000)    # 400 s idle: an expiry rewrite (85K of a 90K context)
a.reply(T0 + 24 + 500, OPUS, "xhigh", inp=100, cr=30000, cw=55000)   # 100 s idle: a big write, not an expiry
finish(PDIR, SID, a, meta_of("Implement Task 4: api", "td"))

a = T(SID, agent="ae")
a.prompt(T0 + 32, "Find where Config is defined and report file:line.")
a.tool(T0 + 33, OPUS, "Grep", {"pattern": "class Config"}, "high", inp=2000, cw=3000)
a.tool(T0 + 34, OPUS, "Read", {"file_path": "/x/config.py"}, "high", inp=500, cr=5000)
a.reply(T0 + 35, OPUS, "high", inp=300, cr=8000)
finish(PDIR, SID, a, meta_of("Find where Config is defined", "te", agentType="Explore"))

a = T(SID, agent="aj")
a.prompt(T0 + 42, "Review the plan and say what is wrong with it.")
a.tool(T0 + 43, OPUS, "Read", {"file_path": "/x/plan.md"}, "low", inp=2000, cw=5000)
a.reply(T0 + 50, OPUS, "low", inp=300, cr=7000)
finish(PDIR, SID, a, meta_of("Review the plan", "tj", effort="low"))

a = T(SID, agent="at")
a.prompt(T0 + 52, "Refactor the module.")
a.tool(T0 + 53, SONNET, "Edit", {"file_path": "/x/m.py", "old_string": "a", "new_string": "b"}, inp=2000, cw=5000)
a.reply(T0 + 60, SONNET, "medium", inp=300, cr=7000)
finish(PDIR, SID, a, meta_of("Refactor the module", "tt", agentType="typed"))

a = T(SID, agent="ab")
a.prompt(T0 + 62, "Write the long plan.")
a.tool(T0 + 63, OPUS, "Read", {"file_path": "/x/big.md"}, "xhigh", inp=10, cr=400000, cw=500)
a.reply(T0 + 70, OPUS, "xhigh", inp=10, cr=400500, cw=100)
finish(PDIR, SID, a, meta_of("Write the long plan", "tb"))

# workflow agents
for aid, with_model in (("aw1", True), ("aw2", False)):
    a = T(SID, agent=aid)
    a.prompt(T0 + 80, "Record the run.")
    a.tool(T0 + 81, SONNET if with_model else OPUS, "Edit", {"file_path": "/x/r.md", "old_string": "a", "new_string": "b"}, inp=1000, cw=2000)
    a.reply(T0 + 90, SONNET if with_model else OPUS, "medium", inp=300, cr=3000)
    md = {"agentType": "workflow-subagent", "description": "record:" + aid, "workflowPhase": "Record", "spawnDepth": 1}
    if with_model:
        md["model"] = "sonnet"
    finish(PDIR, SID, a, md, run="wf1")

# an agent whose replies are five days old: outside the 2-day window
a = T(SID, agent="aold")
a.prompt(NOW - 5 * 86400, "Review the old thing.")
a.tool(NOW - 5 * 86400 + 5, OPUS, "Read", {"file_path": "/x/o.md"}, "xhigh", inp=2000, cw=5000)
m2 = T(SID)   # (the spawn call is not needed: an unlinked agent still counts by its own meta)
finish(PDIR, SID, a, {"agentType": "general-purpose", "description": "Review the old thing", "spawnDepth": 1})

# second project
mo = T(SO, cwd=OTHER)
mo.prompt(T0, "other work")
mo.reply(T0 + 1, OPUS, "high", cw=5000)
mo.spawn(T0 + 10, OPUS, "to", "Check the thing", agent_id="ao")
dump(os.path.join(ODIR, SO + ".jsonl"), mo.recs)
a = T(SO, cwd=OTHER, agent="ao")
a.prompt(T0 + 12, "Check the thing.")
a.tool(T0 + 13, OPUS, "Read", {"file_path": "/y/a"}, "high", inp=1000, cw=2000)
a.reply(T0 + 20, OPUS, "high", inp=100, cr=3000)
finish(ODIR, SO, a, meta_of("Check the thing", "to"))


def store():
    return Store(root=CFG, days=2, clock=lambda: NOW)


# ── the classifier ──────────────────────────────────────────────────────
def test_classifier():
    C = routing.classify
    ro = ("Read", "Grep")
    cases = [
        ("rule 0: spawned agents itself", dict(desc="Write the thing", at="general-purpose", brief="", tools=("Agent", "Read"), ed=0, mu=0, te=0, sp=False), ("judgment", 0)),
        ("rule 0: description says Lead", dict(desc="Lead: build the dashboard", at="general-purpose", brief="", tools=("Edit",), ed=3, mu=0, te=1, sp=False), ("judgment", 0)),
        ("rule 0: the transcript has spawn calls", dict(desc="Implement Task 3", at="general-purpose", brief="", tools=("Edit",), ed=1, mu=0, te=0, sp=True), ("judgment", 0)),
        ("rule 1: Explore that edits nothing", dict(desc="Look around", at="Explore", brief="", tools=ro, ed=0, mu=0, te=0, sp=False), ("mechanical", 1)),
        ("Explore that edited is not rule 1", dict(desc="Update the notes", at="Explore", brief="", tools=("Edit",), ed=1, mu=0, te=0, sp=False), ("execution", 2)),
        ("rule 2: edits and a spec verb opens the description", dict(desc="Implement Task 4", at="general-purpose", brief="", tools=("Edit",), ed=1, mu=0, te=0, sp=False), ("execution", 2)),
        ("rule 2: an ID like F2: opens the description", dict(desc="F2: fast link reads", at="general-purpose", brief="", tools=("Edit",), ed=2, mu=0, te=1, sp=False), ("execution", 2)),
        ("rule 3: a judgment word in the description beats a later execution verb", dict(desc="Review and fix the parser", at="general-purpose", brief="", tools=("Edit",), ed=1, mu=0, te=0, sp=False), ("judgment", 3)),
        ("rule 4: edits and an execution word in the brief", dict(desc="The parser", at="general-purpose", brief="Please implement it.", tools=("Edit",), ed=1, mu=0, te=0, sp=False), ("execution", 4)),
        ("rule 5: edits alone", dict(desc="The parser", at="general-purpose", brief="Tidy it.", tools=("Edit",), ed=1, mu=0, te=0, sp=False), ("execution", 5)),
        ("a mutating Bash command counts as editing", dict(desc="The parser", at="general-purpose", brief="Tidy it.", tools=("Bash",), ed=0, mu=2, te=0, sp=False), ("execution", 5)),
        ("rule 6: read-only with a judgment brief", dict(desc="The parser", at="general-purpose", brief="Audit this for bugs.", tools=ro, ed=0, mu=0, te=0, sp=False), ("judgment", 6)),
        ("rule 7: read-only sweep", dict(desc="Locate the callers", at="general-purpose", brief="List the files.", tools=ro, ed=0, mu=0, te=0, sp=False), ("mechanical", 7)),
        ("rule 8: ran checks only", dict(desc="The parser", at="general-purpose", brief="Run it.", tools=("Bash",), ed=0, mu=0, te=2, sp=False), ("execution", 8)),
        ("rule 9: nothing matches, so judgment", dict(desc="Thing", at="general-purpose", brief="Hello.", tools=ro, ed=0, mu=0, te=0, sp=False), ("judgment", 9)),
        ("a sweep that also runs checks is not mechanical", dict(desc="Locate the callers", at="general-purpose", brief="", tools=("Bash",), ed=0, mu=0, te=1, sp=False), ("execution", 8)),
    ]
    for name, k, want in cases:
        got = C(k["desc"], k["at"], k["brief"], k["tools"], k["ed"], k["mu"], k["te"], k["sp"])
        check("classifier: " + name, got == want, (got, want))
    check("classifier: empty inputs do not raise", C("", "", "", {}, 0, 0, 0, False) == ("judgment", 9))
    check("classifier: None inputs do not raise", C(None, None, None, {}, set(), 0, 0, False)[0] == "judgment")


# ── the payload ─────────────────────────────────────────────────────────
def test_payload():
    st = store()
    routing.TOP = 50
    try:
        d = st.routing_payload("demo")
    finally:
        routing.TOP = 5
    top = {r["label"]: r for r in d["top"]}
    check("payload: eight agents in the window (the five-day-old one is out)", d["agents"] == 8, d["agents"])
    check("payload: the scope is the project", d["scope"] == {"project": "demo"} and d["families"] == ["opus", "sonnet"], (d["scope"], d["families"]))
    src = {s["source"]: s["agents"] for s in d["sources"]}
    check("sources: one explicit, one workflow, one type, five default", src == {"explicit": 1, "workflow": 1, "type": 1, "default": 5}, src)
    check("default: five agents, share of agents", d["default"]["agents"] == 5 and abs(d["default"]["share_agents"] - 5 / 8) < 1e-3, d["default"])
    check("default: all five ran on Opus", d["default"]["opus_agents"] == 5 and d["default"]["opus_cost"] > 0, d["default"])
    # flags
    ad = top.get("Implement Task 4: api")
    check("flags: execution on Opus with no model gets both reasons", ad and ad["flags"] == ["no_model", "execution_opus"], ad)
    check("flags: its saving is positive and below its cost", ad and 0 < ad["save"] < ad["cost"], ad)
    ae = top.get("Find where Config is defined")
    check("flags: Explore on Opus with no model: no_model and mechanical_opus", ae and ae["flags"] == ["no_model", "mechanical_opus"], ae)
    check("flags: mechanical saving re-priced on Haiku (context under 100K)", ae and ae["save"] > 0.5 * ae["cost"], ae)
    aj = top.get("Review the plan")
    check("flags: judgment on Opus with no model: only no_model, no saving", aj and aj["flags"] == ["no_model"] and aj["save"] == 0, aj)
    check("flags: explicit Sonnet execution is not flagged", "Implement Task 3: parser" not in top, sorted(top))
    check("flags: a typed agent with a definition model is not 'no model named'", "Refactor the module" not in top, sorted(top))
    check("flags: a workflow agent with meta.model is not flagged", "record:aw1" not in top, sorted(top))
    aw2 = top.get("record:aw2")
    check("flags: a workflow agent with no model is", aw2 and "no_model" in aw2["flags"], aw2)
    check("flags: the list is ranked by saving, then cost", [r["save"] for r in d["top"]] == sorted((r["save"] for r in d["top"]), reverse=True), [r["save"] for r in d["top"]])
    # effort
    check("effort: an explicit call effort is named, an inherited one is not", top["Review the plan"]["effort_named"] is True and ad["effort_named"] is False, (top["Review the plan"], ad))
    check("effort: the recorded level comes from the replies", ad["effort"] == "xhigh" and ae["effort"] == "high" and aj["effort"] == "low", (ad["effort"], ae["effort"], aj["effort"]))
    ef = {l["effort"]: l for l in d["effort"]["levels"]}
    check("effort: Opus cost split by level", set(ef) == {"low", "medium", "high", "xhigh"} and abs(sum(l["share"] for l in ef.values()) - 1) < 1e-2, ef)
    check("effort: Opus agents and the one that named an effort", d["effort"]["opus_agents"] == 5 and d["effort"]["named_agents"] == 1, d["effort"])
    # rewrites
    sub = d["rewrites"]["sub"]
    check("rewrites: one expiry rewrite at the 5-minute TTL, costed at its write", sub["count"] == 1 and abs(sub["cost"] - 85000 * 5 / 1e6) < 1e-3, sub)
    check("rewrites: the 100-second big write is kept apart", sub["other_count"] == 1 and abs(sub["other_cost"] - 55000 * 5 / 1e6) < 1e-3, sub)
    main = d["rewrites"]["main"]
    check("rewrites: main uses a 1-hour TTL: the 2-hour gap counts, the 30-minute one does not",
          main["count"] == 1 and abs(main["cost"] - 60000 * 5 / 1e6) < 1e-3 and main["other_count"] == 1 and main["ttl"] == 3600, main)
    check("rewrites: the share is of that role's spend", 0 < main["share"] < 1 and abs(main["share"] - main["cost"] / main["spend"]) < 1e-3, main)
    # context
    c3 = d["ctx300"]
    check("context: one agent passed 300K and its replies at that size are priced", c3["agents"] == 1 and c3["msg_cost"] > 0 and c3["cost"] >= c3["msg_cost"], c3)
    # matrix
    cls = {r["cls"]: r for r in d["classes"]}
    cell = lambda c, f: next(x for x in cls[c]["cells"] if x["family"] == f)  # noqa: E731
    check("matrix: execution on Opus is flagged, on Sonnet is not",
          cell("execution", "opus")["flagged_agents"] == 2 and cell("execution", "sonnet")["flagged_agents"] == 0 and cell("execution", "sonnet")["agents"] == 3, [cls["execution"]["cells"]])
    check("matrix: mechanical on Opus is flagged", cell("mechanical", "opus")["flagged_agents"] == 1 and cell("mechanical", "opus")["flagged_cost"] > 0, cls["mechanical"])
    check("matrix: judgment on Opus is never flagged by class", cell("judgment", "opus")["flagged_agents"] == 0 and cell("judgment", "opus")["agents"] == 2, cls["judgment"])
    check("matrix: totals add up", abs(sum(r["cost"] for r in d["classes"]) - d["cost"]) < 1e-3 and sum(r["agents"] for r in d["classes"]) == d["agents"], (d["cost"], [r["cost"] for r in d["classes"]]))
    check("flagged: totals cover agents with any reason", d["flagged"]["agents"] == 5 and d["flagged"]["save"] > 0, d["flagged"])
    check("cost: new tokens and cache reads are kept apart", d["new_tokens"] > 0 and d["cache_read"] > 400000 and d["cache_read_cost"] > 0, (d["new_tokens"], d["cache_read"]))
    # scopes
    allp = st.routing_payload("")
    check("scope: all projects adds the other project's agent", allp["agents"] == 9, allp["agents"])
    one = st.routing_payload("other")
    check("scope: another project sees only its own", one["agents"] == 1 and one["default"]["agents"] == 1, one["agents"])
    none = st.routing_payload("nope")
    check("scope: an unknown project is empty, not an error", none["agents"] == 0 and none["top"] == [] and none["cost"] == 0, none)
    check("scope: a hostile project string is only compared", st.routing_payload("../../etc/passwd" + "x" * 500)["agents"] == 0)
    # caching
    t0 = time.time()
    st.routing_payload("demo")
    check("cache: a repeat call is served from the cache", time.time() - t0 < 0.05, time.time() - t0)
    check("payload: JSON-serialisable and small", len(json.dumps(d)) < 200000, len(json.dumps(d)))


def test_source_detection():
    st = store()
    st.refresh(force=True)
    s = st.sessions[SID]
    by = {x.agent_id: x for x in s.subs}
    got = {aid: routing.model_source(x, CFG, WORK)[0] for aid, x in by.items()}
    want = {"ax": "explicit", "ad": "default", "ae": "default", "aj": "default", "at": "type", "ab": "default", "aw1": "workflow", "aw2": "default", "aold": "default"}
    check("model source per agent", got == want, got)
    check("effort named: call effort and meta effort count", routing.model_source(by["ax"], CFG, WORK)[1] and routing.model_source(by["aj"], CFG, WORK)[1] and not routing.model_source(by["ad"], CFG, WORK)[1])
    check("type model: a definition's model is found, inherit and unknown names are not",
          routing.type_model(CFG, WORK, "typed") == "sonnet" and routing.type_model(CFG, WORK, "Explore") == "" and routing.type_model(CFG, WORK, "../x") == "")
    # the explicit spawn input wins over a type definition
    by["at"].spawn = dict(by["at"].spawn or {}, model_requested="opus")
    check("model source: a named model beats the agent type's definition", routing.model_source(by["at"], CFG, WORK)[0] == "explicit")


def test_parser_fields():
    st = store()
    st.refresh(force=True)
    s = st.sessions[SID]
    by = {x.agent_id: x.acc for x in s.subs}
    check("parser: edited files, edits by Edit are counted", len(by["ax"].edited) == 1 and not by["ae"].edited)
    check("parser: pytest counts as a test run", by["ax"].bash_tests == 1 and by["ad"].bash_tests == 1 and by["ae"].bash_tests == 0)
    check("parser: per-reply effort comes from the assistant record", set(by["ad"].msg_eff.values()) == {"xhigh"} and set(by["ae"].msg_eff.values()) == {"high"})
    check("parser: a reply records its effort once per message id", len(by["ad"].msg_eff) == 4, by["ad"].msg_eff)


# ── the endpoint ────────────────────────────────────────────────────────
def _req(port, path, token=True):
    import dash_pairing
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.putrequest("GET", path, skip_host=True)
    c.putheader("Host", "127.0.0.1:%d" % port)
    if not token:
        c._cc_sent = True
        dash_pairing._orig_endheaders(c)
    else:
        c.endheaders()
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, body


def test_endpoint():
    srv = make_server(0, store())
    th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    th.start()
    try:
        s, _ = _req(srv.port, "/api/routing", token=False)
        check("endpoint: /api/routing without the token -> 401", s == 401, s)
        s, _ = _req(srv.port, "/api/routing?project=demo", token=False)
        check("endpoint: a project filter does not bypass the token", s == 401, s)
        auto_token(srv)
        s, body = _req(srv.port, "/api/routing?project=demo")
        d = json.loads(body) if s == 200 else {}
        check("endpoint: with the token -> the payload", s == 200 and d.get("agents") == 8 and d["scope"]["project"] == "demo", (s, body[:200]))
        s, body = _req(srv.port, "/api/routing")
        check("endpoint: no project -> every project", s == 200 and json.loads(body)["agents"] == 9, s)
        s, body = _req(srv.port, "/api/routing?project=nope")
        check("endpoint: unknown project -> 200 and empty", s == 200 and json.loads(body)["agents"] == 0, s)
        s, _ = _req(srv.port, "/routing.js")
        check("endpoint: routing.js served without a token (it carries no data)", s == 200, s)
        s, _ = _req(srv.port, "/routing.css")
        check("endpoint: routing.css served", s == 200, s)
        s, _ = _req(srv.port, "/api/routing/../session", token=True)
        check("endpoint: only the exact path is routed", s in (404, 400), s)
    finally:
        srv.shutdown()
        srv.server_close()


# ── the panel in a browser ──────────────────────────────────────────────
BROWSER_JS = r"""
const { chromium } = require(process.env.PW);
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME, headless: true });
  const out = {};
  const ctx0 = await browser.newContext();
  const p0 = await ctx0.newPage();
  await p0.goto(`http://127.0.0.1:${process.env.PORT}/pair#code=${process.env.PAIR}`, { waitUntil: 'load' });
  await p0.waitForTimeout(1000);
  const state = await ctx0.storageState();
  await ctx0.close();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, colorScheme: 'light', storageState: state });
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', (e) => errs.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()); });
  let routingCalls = 0;
  page.on('request', (rq) => { if (rq.url().includes('/api/routing')) routingCalls += 1; });
  await page.goto(`http://127.0.0.1:${process.env.PORT}/#/overview?p=demo`, { waitUntil: 'load' });
  await page.waitForSelector('section[data-key="routing"]', { timeout: 20000 });
  await page.waitForTimeout(500);
  out.stats = await page.$$eval('section[data-key="routing"] .route-stat .label', (r) => r.map((x) => x.textContent));
  out.values = await page.$$eval('section[data-key="routing"] .route-stat .value', (r) => r.map((x) => x.textContent));
  out.matrixRows = await page.$$eval('section[data-key="routing"] .route-matrix tbody tr', (r) => r.map((x) => x.dataset.key));
  out.flaggedCells = await page.$$eval('section[data-key="routing"] .route-matrix td .pill.warn', (r) => r.length);
  out.effortBars = await page.$$eval('section[data-key="routing"] .route-bars .hbar', (r) => r.map((x) => x.dataset.key));
  out.topRows = await page.$$eval('section[data-key="routing"] .route-top tbody tr', (r) => r.map((x) => x.dataset.key));
  out.why = await page.$$eval('section[data-key="routing"] .route-top .pill.warn', (r) => r.map((x) => x.textContent));
  out.foot = await page.$eval('section[data-key="routing"] > .panel-b > .insight', (x) => x.textContent);
  out.cspStyleAttrs = await page.$$eval('section[data-key="routing"] [style]', (r) => r.map((x) => x.getAttribute('style') || '').length);
  out.overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  if (process.env.SHOTS) {
    const el = await page.$('section[data-key="routing"]');
    await el.screenshot({ path: process.env.SHOTS + '/synthetic-light.png' });
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.waitForTimeout(300);
    await el.screenshot({ path: process.env.SHOTS + '/synthetic-dark.png' });
    await page.emulateMedia({ colorScheme: 'light' });
  }
  // No churn: observe the panel while the page re-polls with unchanged data.
  await page.evaluate(() => {
    window.__m = { childList: 0, attributes: 0, characterData: 0 };
    window.__what = [];
    new MutationObserver((ms) => { for (const m of ms) { window.__m[m.type] += 1; if (window.__what.length < 8) window.__what.push(m.type + ' ' + (m.attributeName || '') + ' ' + (m.target.className && m.target.className.baseVal != null ? m.target.className.baseVal : m.target.className)); } })
      .observe(document.querySelector('section[data-key="routing"]'), { subtree: true, childList: true, attributes: true, characterData: true });
  });
  const c0 = routingCalls;
  for (let i = 0; i < 4; i++) { await page.evaluate(() => window.ccApp.tick(true)); await page.waitForTimeout(900); }
  out.polls = routingCalls - c0;
  out.flicker = await page.evaluate(() => window.__m);
  out.flickerWhat = await page.evaluate(() => window.__what);
  // A flagged row opens that agent's timeline.
  await page.click('section[data-key="routing"] .route-top tbody tr');
  await page.waitForTimeout(500);
  out.hash = await page.evaluate(() => location.hash);
  // All projects: the project name shows beside each agent.
  await page.goto(`http://127.0.0.1:${process.env.PORT}/#/overview`, { waitUntil: 'load' });
  await page.waitForFunction(() => { const s = document.querySelector('section[data-key="routing"] .route-stat .note'); return s && /of 9\b/.test(s.textContent); }, null, { timeout: 20000 });
  out.allNote = await page.$eval('section[data-key="routing"] .route-stat .note', (x) => x.textContent);
  out.allProject = await page.$$eval('section[data-key="routing"] .route-name .faint', (r) => r.length);
  // Phone width: no horizontal page scroll.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(400);
  out.phoneOverflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  out.phoneTopCols = await page.$$eval('section[data-key="routing"] .route-top th', (r) => r.filter((x) => getComputedStyle(x).display !== 'none').length);
  // An empty scope hides the panel.
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`http://127.0.0.1:${process.env.PORT}/#/overview?p=nope`, { waitUntil: 'load' });
  await page.waitForTimeout(2500);
  out.emptyPanel = await page.$$eval('section[data-key="routing"]', (r) => r.length);
  out.errors = errs;
  console.log(JSON.stringify(out));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
"""


def test_browser():
    if not (os.path.isdir(PLAYWRIGHT) and os.path.exists(CHROME) and shutil.which("node")):
        print("browser: skipped (no playwright-core, Chrome or node)")
        return
    srv = make_server(0, store())
    th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    th.start()
    js = os.path.join(BASE, "routing_check.js")
    with open(js, "w") as fh:
        fh.write(BROWSER_JS)
    env = dict(os.environ, PW=PLAYWRIGHT, CHROME=CHROME, PORT=str(srv.port), PAIR=srv.auth.new_code(), SHOTS=SHOTS)
    try:
        r = subprocess.run(["node", js], capture_output=True, text=True, timeout=240, env=env)
    finally:
        srv.shutdown()
        srv.server_close()
    if r.returncode != 0:
        check("browser: page check ran", False, r.stderr[-800:])
        return
    o = json.loads(r.stdout.strip().splitlines()[-1])
    check("browser: no page errors (CSP included)", not o["errors"], o["errors"])
    check("browser: four stats", o["stats"] == ["Picked by default", "Opus at xhigh or max", "Re-sending expired context", "Context over 300K"], o["stats"])
    check("browser: the stats carry numbers", all(v and v != "NaN" and "undefined" not in v for v in o["values"]), o["values"])
    check("browser: matrix has the three work types", o["matrixRows"] == ["judgment", "execution", "mechanical"], o["matrixRows"])
    check("browser: flagged cells drawn as amber pills (execution on Opus, mechanical on Opus)", o["flaggedCells"] == 2, o["flaggedCells"])
    check("browser: effort bars for the levels Opus agents ran at", o["effortBars"] == ["low", "medium", "high", "xhigh"], o["effortBars"])
    check("browser: the flagged list has a row per flagged agent (five), best saving first", len(o["topRows"]) == 5, o["topRows"])
    check("browser: reasons are worded", "No model named" in o["why"] and "Execution on Opus" in o["why"] and "Read-only on Opus" in o["why"], o["why"])
    check("browser: the footnote says the work type is an estimate", "estimated from the agent's description and tools" in o["foot"], o["foot"])
    check("browser: no inline style attributes needed beyond the bar widths", o["cspStyleAttrs"] <= 6, o["cspStyleAttrs"])
    check("browser: no horizontal page scroll at 1440 and at 390", not o["overflow"] and not o["phoneOverflow"], (o["overflow"], o["phoneOverflow"]))
    print("flicker: %d routing polls, mutations %s" % (o["polls"], o["flicker"]))
    check("browser: polls happened", o["polls"] >= 3, o["polls"])
    check("browser: no node added, removed or changed across polls with unchanged data", o["flicker"] == {"childList": 0, "attributes": 0, "characterData": 0}, (o["flicker"], o["flickerWhat"]))
    check("browser: a flagged row opens that agent's timeline", o["hash"].startswith("#/s/%s/a/" % SID), o["hash"])
    check("browser: all projects counts both projects and names each agent's project", "of 9" in o["allNote"] and o["allProject"] >= 1, (o["allNote"], o["allProject"]))
    check("browser: the flagged list drops model, effort and cost at phone width", o["phoneTopCols"] == 3, o["phoneTopCols"])
    check("browser: a scope with no agents shows no Routing panel", o["emptyPanel"] == 0, o["emptyPanel"])


def main():
    try:
        for fn in (test_classifier, test_parser_fields, test_source_detection, test_payload, test_endpoint, test_browser):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                check(fn.__name__ + " raised", False, "%s: %s" % (type(e).__name__, e))
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
    fails = [r for r in RESULTS if not r[1]]
    print("total=%d  pass=%d  fail=%d" % (len(RESULTS), len(RESULTS) - len(fails), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
