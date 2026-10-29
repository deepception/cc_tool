#!/usr/bin/env python3
"""The recursive agent tree, effort and thinking (lib/cc_dash/tree.py, parser.py, /api/session/tree).

Builds fake ${CLAUDE_CONFIG_DIR}/projects sessions in a temp dir:
  A  main (Opus 5.5, effort recorded high, thinking blocks and counts)
     -> s1 (asked for low, records low, switches model) -> s2 (no record) -> s3
                                                        -> a Workflow run with one agent
     -> sm (no matching spawn call, linked by meta.parentAgentId to s1)
     -> sr (resumed two hours later: one node, wall > active)
     -> sq (asked for max, no record)
     so  (an orphan: its spawn call and parent are gone)
  B  main with no effort record (default medium), a Sonnet 5.5 child (default)
     and a Haiku 4.5 child (no known default)
  C  two subagents that spawned each other (a cycle), nothing from the main thread
  D  a chain of 30 nested subagents (past the depth bound)
then checks the tree, the totals, the effort sources, thinking, the session
payload, the effort on the board's rows (/api/live) and on the Factory floor's
nodes (/api/graph), Overview, the endpoint's auth and (with Chrome and
playwright-core) the Agents tab in a browser, the effort chip in the floor's
inspector head (where the Live board's chip used to show), and the control row
on an agent's page. Nothing outside the temp dir is touched.

Run:  python3 tests/test_cc_dash_tree.py
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
from cc_dash import graph as graph_mod  # noqa: E402
from cc_dash import tree as agent_tree  # noqa: E402
from cc_dash.parser import Acc, FileCache  # noqa: E402
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


BASE = tempfile.mkdtemp(prefix="cc-dash-tree-")
CFG = os.path.join(BASE, "claude")
os.environ["XDG_STATE_HOME"] = os.path.join(BASE, "state")      # attention.py reads the hooks' log from here: no real one is ever read
WORK = os.path.join(BASE, "work", "demo")
os.makedirs(WORK)
PDIR = os.path.join(CFG, "projects", WORK.replace("/", "-"))
os.makedirs(PDIR)
NOW = time.time()
T0 = NOW - 3 * 3600          # sessions started three hours ago


def dump(path, recs):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        for r in recs:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")   # compact, as Claude Code writes


class T:
    """One transcript being written."""

    def __init__(self, sid, agent=None):
        self.sid, self.agent, self.recs, self.n = sid, agent, [], 0

    def base(self, kind, t, **kw):
        r = {"type": kind, "timestamp": iso(t), "sessionId": self.sid, "cwd": WORK, "version": "2.1.304",
             "isSidechain": bool(self.agent), "uuid": "u%d" % len(self.recs)}
        if self.agent:
            r["agentId"] = self.agent
        r.update(kw)
        return r

    def request(self, t, model, effort=None):
        p = {"model": model, "thinking": {"type": "adaptive", "display": "updates"}}
        if effort:
            p["output_config"] = {"effort": effort}
        self.recs.append({"type": "api-request", "sessionId": self.sid, "timestamp": iso(t), "params": p})
        self.recs.append({"type": "api-request-shape", "sessionId": self.sid, "timestamp": iso(t), "shape": {"system": [{"type": "text", "text": "x" * 500}]}})

    def prompt(self, t, text):
        self.recs.append(self.base("user", t, message={"role": "user", "content": text}))

    def reply(self, t, model, out=100, inp=1000, think=None, think_text="", tools=(), stop="end_turn", details=True):
        self.n += 1
        mid = "m-%s-%d" % (self.agent or self.sid[:4], self.n)
        u = {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        if details and think is not None:
            u["output_tokens_details"] = {"thinking_tokens": think}
        blocks = []
        if think is not None:
            blocks.append({"type": "thinking", "thinking": think_text, "signature": "sig"})
        blocks.append({"type": "text", "text": "ok"})
        blocks.extend(tools)
        for b in blocks:   # one record per content block, same id and usage
            self.recs.append(self.base("assistant", t, message={"id": mid, "model": model, "role": "assistant", "content": [b],
                                                                 "stop_reason": "tool_use" if tools else stop, "usage": u}))

    def spawn(self, t, model, tid, desc, sub_model=None, effort=None, agent_id=None):
        ti = {"description": desc, "prompt": "do " + desc, "subagent_type": "general-purpose"}
        if sub_model:
            ti["model"] = sub_model
        if effort:
            ti["effort"] = effort
        self.reply(t, model, tools=[{"type": "tool_use", "id": tid, "name": "Agent", "input": ti}])
        tur = {"status": "completed", "agentId": agent_id} if agent_id else None
        r = self.base("user", t + 30, message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": "done"}]})
        if tur:
            r["toolUseResult"] = tur
        self.recs.append(r)

    def workflow(self, t, model, tid, run):
        self.reply(t, model, tools=[{"type": "tool_use", "id": tid, "name": "Workflow", "input": {"script": "export default { name: 'sweep' }"}}])
        r = self.base("user", t + 5, message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": "launched"}]})
        r["toolUseResult"] = {"status": "async_launched", "runId": run}
        self.recs.append(r)

    def handback(self, t, model, message="report"):
        self.reply(t, model, tools=[{"type": "tool_use", "id": "hb-" + (self.agent or ""), "name": "SubagentHandback", "input": {"message": message}}])


def sub_path(sid, aid, run=None):
    d = os.path.join(PDIR, sid, "subagents") if not run else os.path.join(PDIR, sid, "subagents", "workflows", run)
    return os.path.join(d, "agent-%s.jsonl" % aid)


def meta(sid, aid, data, run=None):
    p = sub_path(sid, aid, run)[:-len(".jsonl")] + ".meta.json"
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as fh:
        json.dump(data, fh)


OPUS, SONNET, HAIKU45 = "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5-20251001"

# ── Session A ────────────────────────────────────────────────────────────
SA = "aaaaaaaa-0000-0000-0000-000000000001"
m = T(SA)
m.prompt(T0, "lead the work")
m.request(T0 + 1, OPUS, "high")
m.reply(T0 + 2, OPUS, out=400, think=150, think_text="let me think about it")
m.spawn(T0 + 10, OPUS, "t1", "Lead one", sub_model="sonnet", effort="low", agent_id="s1")
m.spawn(T0 + 20, OPUS, "t4", "Resumed helper", agent_id="sr")
m.spawn(T0 + 30, OPUS, "t5", "Max effort helper", effort="max", agent_id="sq")
m.reply(T0 + 40, OPUS, out=300, think=0)
m.reply(NOW - 60, OPUS, out=100, think=20, stop="end_turn")
dump(os.path.join(PDIR, SA + ".jsonl"), m.recs)

s1 = T(SA, "s1")
s1.prompt(T0 + 11, "do Lead one")
s1.request(T0 + 11, SONNET, "low")
s1.reply(T0 + 12, SONNET, out=50, think=10)
s1.spawn(T0 + 13, SONNET, "t2", "Second level", agent_id="s2")
s1.reply(T0 + 50, OPUS, out=60)            # model switch inside s1
s1.handback(T0 + 60, OPUS)
dump(sub_path(SA, "s1"), s1.recs)
meta(SA, "s1", {"agentType": "general-purpose", "description": "Lead one", "toolUseId": "t1", "spawnDepth": 1, "model": "sonnet"})

s2 = T(SA, "s2")
s2.prompt(T0 + 14, "do Second level")
s2.reply(T0 + 15, SONNET, out=40, think=5, details=False)   # stream-start usage: no thinking count
s2.spawn(T0 + 16, SONNET, "t3", "Third level", agent_id="s3")
s2.workflow(T0 + 20, SONNET, "tw", "wf_x")
s2.handback(T0 + 70, SONNET)
dump(sub_path(SA, "s2"), s2.recs)
meta(SA, "s2", {"agentType": "general-purpose", "description": "Second level", "toolUseId": "t2", "parentAgentId": "s1", "spawnDepth": 2})

s3 = T(SA, "s3")
s3.prompt(T0 + 17, "do Third level")
s3.reply(T0 + 18, SONNET, out=30)
REPORT = ("## Result\n\n**Branch / commit:** `worktree-x` at `abc1234`\n\n- first point\n- second point\n\n"
          "| suite | result |\n|---|---|\n| tree | 64 pass |\n\n<script>window.__pwned = 1</script> stays text\n\n[docs](https://example.com/x) and [bad](javascript:alert(1))")
s3.handback(T0 + 19, SONNET, REPORT)
dump(sub_path(SA, "s3"), s3.recs)
meta(SA, "s3", {"agentType": "Explore", "description": "Third level", "toolUseId": "t3", "parentAgentId": "s2", "spawnDepth": 3})

wa = T(SA, "wa")
wa.prompt(T0 + 21, "workflow step")
wa.reply(T0 + 22, SONNET, out=20)
dump(sub_path(SA, "wa", "wf_x"), wa.recs)
meta(SA, "wa", {"agentType": "workflow-subagent", "description": "review:one", "workflowPhase": "Review", "spawnDepth": 1, "model": "sonnet"}, "wf_x")
with open(os.path.join(PDIR, SA, "subagents", "workflows", "wf_x", "journal.jsonl"), "w") as fh:
    fh.write(json.dumps({"type": "started", "agentId": "wa", "label": "review:one", "phase": "Review"}) + "\n")
    fh.write(json.dumps({"type": "result", "agentId": "wa"}) + "\n")

sm = T(SA, "sm")
sm.prompt(T0 + 25, "meta linked")
sm.reply(T0 + 26, SONNET, out=10)
dump(sub_path(SA, "sm"), sm.recs)
meta(SA, "sm", {"agentType": "general-purpose", "description": "Meta linked", "toolUseId": "gone", "parentAgentId": "s1", "spawnDepth": 2})

sr = T(SA, "sr")
sr.prompt(T0 + 21, "do Resumed helper")
sr.reply(T0 + 25, OPUS, out=10)
sr.reply(T0 + 85, OPUS, out=10)
sr.prompt(T0 + 2 * 3600, "The coordinator sent a message while you were working: continue")   # SendMessage resume, same file
sr.reply(T0 + 2 * 3600 + 60, OPUS, out=10)
dump(sub_path(SA, "sr"), sr.recs)
meta(SA, "sr", {"agentType": "general-purpose", "description": "Resumed helper", "toolUseId": "t4", "spawnDepth": 1})

sq = T(SA, "sq")
sq.prompt(T0 + 31, "do Max effort helper")
sq.reply(T0 + 32, OPUS, out=10)
dump(sub_path(SA, "sq"), sq.recs)
meta(SA, "sq", {"agentType": "general-purpose", "description": "Max effort helper", "toolUseId": "t5", "spawnDepth": 1, "effort": "max"})

so = T(SA, "so")
so.prompt(T0 + 33, "orphan")
so.reply(T0 + 34, OPUS, out=10)
dump(sub_path(SA, "so"), so.recs)
meta(SA, "so", {"agentType": "general-purpose", "description": "Orphan", "toolUseId": "nope", "parentAgentId": "ghost", "spawnDepth": 2})

# ── Session B: nothing recorded ──────────────────────────────────────────
SB = "bbbbbbbb-0000-0000-0000-000000000002"
b = T(SB)
b.prompt(T0, "work")
b.reply(T0 + 1, OPUS, out=100)
b.spawn(T0 + 2, OPUS, "bt1", "Sonnet child", sub_model="sonnet", agent_id="b1")
b.spawn(T0 + 3, OPUS, "bt2", "Old haiku child", agent_id="b2")
dump(os.path.join(PDIR, SB + ".jsonl"), b.recs)
for aid, model, desc, tid in (("b1", SONNET, "Sonnet child", "bt1"), ("b2", HAIKU45, "Old haiku child", "bt2")):
    x = T(SB, aid)
    x.prompt(T0 + 4, "do " + desc)
    x.reply(T0 + 5, model, out=10)
    dump(sub_path(SB, aid), x.recs)
    meta(SB, aid, {"agentType": "general-purpose", "description": desc, "toolUseId": tid, "spawnDepth": 1})

# ── Session C: a cycle ───────────────────────────────────────────────────
SC = "cccccccc-0000-0000-0000-000000000003"
c = T(SC)
c.prompt(T0, "cycle")
c.reply(T0 + 1, OPUS, out=10)
dump(os.path.join(PDIR, SC + ".jsonl"), c.recs)
for aid, other_tid, my_tid in (("ca", "tb", "ta"), ("cb", "ta", "tb")):
    x = T(SC, aid)
    x.prompt(T0 + 2, "loop")
    x.spawn(T0 + 3, OPUS, other_tid, "spawns the other")
    dump(sub_path(SC, aid), x.recs)
    meta(SC, aid, {"agentType": "general-purpose", "description": "cycle " + aid, "toolUseId": my_tid, "spawnDepth": 1})

# ── Session E: a subagent running a tool right now (control row) ─────────
SE = "eeeeeeee-0000-0000-0000-000000000005"
e_ = T(SE)
e_.prompt(NOW - 120, "go")
e_.spawn(NOW - 110, OPUS, "et1", "Running helper", agent_id="ew")
e_.recs.pop()          # no tool result yet: the Agent call is still pending
dump(os.path.join(PDIR, SE + ".jsonl"), e_.recs)
ew = T(SE, "ew")
ew.prompt(NOW - 100, "do Running helper")
ew.reply(NOW - 30, OPUS, tools=[{"type": "tool_use", "id": "eb1", "name": "Bash", "input": {"command": "sleep 100"}}])
dump(sub_path(SE, "ew"), ew.recs)
meta(SE, "ew", {"agentType": "general-purpose", "description": "Running helper", "toolUseId": "et1", "spawnDepth": 1})

# ── Session D: 30 nested ─────────────────────────────────────────────────
SD = "dddddddd-0000-0000-0000-000000000004"
DEEP = 30
d = T(SD)
d.prompt(T0, "deep")
d.spawn(T0 + 1, OPUS, "dt0", "level 1", agent_id="d0")
dump(os.path.join(PDIR, SD + ".jsonl"), d.recs)
for i in range(DEEP):
    x = T(SD, "d%d" % i)
    x.prompt(T0 + 2 + i, "level %d" % (i + 1))
    if i + 1 < DEEP:
        x.spawn(T0 + 2 + i, OPUS, "dt%d" % (i + 1), "level %d" % (i + 2), agent_id="d%d" % (i + 1))
    else:
        x.reply(T0 + 2 + i, OPUS, out=10)
    dump(sub_path(SD, "d%d" % i), x.recs)
    meta(SD, "d%d" % i, {"agentType": "general-purpose", "description": "level %d" % (i + 1), "toolUseId": "dt%d" % i, "spawnDepth": i + 1})


def nodes_by_id(t):
    return {n["id"]: n for n in t["nodes"]}


def totals_consistent(t):
    """Every node: subtree = own + the subtrees of its children (tokens, cost, tool calls)."""
    by = nodes_by_id(t)
    bad = []
    for n in t["nodes"]:
        for k in ("tokens", "input", "output", "cache_read", "cache_write", "tool_calls", "errors"):
            want = n["own"][k] + sum(by[c]["sub"][k] for c in n["children"])
            if want != n["sub"][k]:
                bad.append((n["id"], k, n["sub"][k], want))
        want = n["own"]["cost"] + sum(by[c]["sub"]["cost"] for c in n["children"])
        if abs(want - n["sub"]["cost"]) > 1e-5:
            bad.append((n["id"], "cost", n["sub"]["cost"], want))
    return bad


def test_parser():
    a = Acc("x", "main")
    FileCache._lines(a, b"\n".join(json.dumps(r, separators=(",", ":")).encode() for r in m.recs))
    check("parser: api-request effort read", a.effort == "high" and a.effort_log and a.effort_log[0]["effort"] == "high", a.effort_log)
    check("parser: thinking mode read", a.thinking_mode == "adaptive/updates", a.thinking_mode)
    check("parser: messages credited with their effort", a.effort_msgs.get("high") == a.api_msgs, (a.effort_msgs, a.api_msgs))
    check("parser: effort marker in the timeline (once, no change after)", [e["k"] for e in a.events].count("effort") == 1)
    check("parser: thinking blocks and text counted", a.think_blocks == 3 and a.think_text_blocks == 1 and a.think_chars == len("let me think about it"),
          (a.think_blocks, a.think_text_blocks, a.think_chars))
    check("parser: thinking tokens once per message", a.think_model.get(OPUS, [0, 0, 0])[2] == 170, a.think_model)
    check("parser: api-request-shape lines skipped, nothing unreadable", a.bad_lines == 0)
    s = Acc("y", "sub")
    FileCache._lines(s, b"\n".join(json.dumps(r, separators=(",", ":")).encode() for r in s1.recs))
    check("parser: model switch logged and shown", len(s.model_log) == 1 and any(e["k"] == "model" for e in s.events), s.model_log)
    check("parser: Agent call keeps the requested effort", m.recs and Acc is not None)
    a2 = Acc("z", "main")
    FileCache._lines(a2, b"\n".join(json.dumps(r, separators=(",", ":")).encode() for r in m.recs))
    sp = next(x for x in a2.spawns if x["id"] == "t1")
    check("parser: spawn record carries effort_requested", sp["effort_requested"] == "low", sp)
    # An effort change mid-transcript: one log entry and one marker per change.
    x = T("e")
    x.request(T0, OPUS, "medium")
    x.reply(T0 + 1, OPUS)
    x.request(T0 + 2, OPUS, "xhigh")
    x.reply(T0 + 3, OPUS)
    a3 = Acc("e", "main")
    FileCache._lines(a3, b"\n".join(json.dumps(r, separators=(",", ":")).encode() for r in x.recs))
    check("parser: effort change logged", [e["effort"] for e in a3.effort_log] == ["medium", "xhigh"] and a3.effort_msgs == {"medium": 1, "xhigh": 1}, (a3.effort_log, a3.effort_msgs))
    # 2.1.30x: no api-request record, but every assistant record carries its own `effort`.
    y = T("r")
    y.reply(T0, OPUS)
    y.reply(T0 + 1, OPUS)
    for r in y.recs:
        if r.get("type") == "assistant":
            r["effort"] = "xhigh"
    a4 = Acc("r", "sub")
    FileCache._lines(a4, _lines(y.recs))
    check("parser: assistant-record effort counts as recorded", a4.effort == "xhigh" and a4.effort_msgs == {"xhigh": a4.api_msgs}
          and [e["effort"] for e in a4.effort_log] == ["xhigh"], (a4.effort, a4.effort_msgs, a4.effort_log))


def _lines(recs):
    return b"\n".join(json.dumps(r, separators=(",", ":")).encode() for r in recs)


def test_tokens():
    """New tokens, cache reads apart, context, and the output estimate for stream-start usage."""
    x = T("tok", "sx")
    u0 = {"input_tokens": 3, "output_tokens": 8, "cache_read_input_tokens": 50000, "cache_creation_input_tokens": 1200}
    long_text = "word " * 2000                                  # 10,000 characters -> about 2,500 tokens
    tool_in = {"command": "x" * 3996}                           # ~4,000 characters of tool input
    for b in ({"type": "text", "text": long_text}, {"type": "tool_use", "id": "tu1", "name": "Bash", "input": tool_in}):
        x.recs.append(x.base("assistant", T0, message={"id": "mstart", "model": OPUS, "role": "assistant", "content": [b], "stop_reason": None, "usage": u0}))
    a = Acc("tok", "sub")
    FileCache._lines(a, _lines(x.recs))
    t = a.totals()
    chars = len(long_text) + len(json.dumps(tool_in, separators=(",", ":"))) + len("Bash")
    check("estimate: a reply with only its stream-start usage gets output from its content", t["output"] == (chars + 3) // 4 and t["output_estimated"] == (chars + 3) // 4 - 8
          and t["est_msgs"] == 1, (t["output"], t["output_estimated"], chars))
    from cc_dash import pricing
    want = pricing.message_cost(OPUS, 3, (chars + 3) // 4, 50000, 1200, 0)
    check("estimate: the estimate is priced", abs(t["cost"] - want) < 1e-9, (t["cost"], want))
    check("tokens: new tokens = input + cache writes + output; cache reads apart", t["tokens"] == 3 + 1200 + t["output"] and t["cache_read"] == 50000, t)
    check("tokens: context is the last call's input + cache read + cache write", t["context"] == 3 + 50000 + 1200, t["context"])
    # The final record arrives later (an incremental read): it replaces the estimate, no double count.
    fin = dict(u0, output_tokens=777)
    x.recs.append(x.base("assistant", T0 + 1, message={"id": "mstart", "model": OPUS, "role": "assistant", "content": [{"type": "text", "text": "!"}], "stop_reason": "end_turn", "usage": fin}))
    FileCache._lines(a, _lines(x.recs[-1:]))
    t = a.totals()
    check("estimate: a final record replaces it, counted once", t["output"] == 777 and t["output_estimated"] == 0 and t["est_msgs"] == 0 and t["msgs"] == 1, t)
    # A message whose records all carry stop_reason keeps its recorded output even with long content.
    y = T("tok2", "sy")
    for b in ({"type": "text", "text": long_text}, {"type": "text", "text": "tail"}):
        y.recs.append(y.base("assistant", T0, message={"id": "mfin", "model": OPUS, "role": "assistant", "content": [b], "stop_reason": "end_turn", "usage": dict(u0, output_tokens=3000)}))
    b2 = Acc("tok2", "sub")
    FileCache._lines(b2, _lines(y.recs))
    check("estimate: a reply with a final record is not estimated", b2.totals()["output"] == 3000 and b2.totals()["output_estimated"] == 0, b2.totals())
    # The main transcript of session A: every record is final, so nothing changes there.
    mm = Acc("main", "main")
    FileCache._lines(mm, _lines(m.recs))
    check("estimate: main transcript with final records unchanged", mm.totals()["output_estimated"] == 0 and mm.totals()["output"] == 400 + 300 + 100 + 3 * 100, mm.totals()["output"])


def test_tree():
    st = Store(root=CFG, days=1, clock=lambda: NOW)
    st.refresh(force=True)
    t = st.tree_payload(SA)
    by = nodes_by_id(t)
    sm_ = t["summary"]
    check("tree: every transcript is a node (main, 8 subagents, 1 workflow run)",
          set(by) >= {"main", "a:s1", "a:s2", "a:s3", "a:wa", "a:sm", "a:sr", "a:sq", "a:so", "w:wf_x"}, sorted(by))
    check("tree: three levels of nesting", by["a:s1"]["level"] == 1 and by["a:s2"]["level"] == 2 and by["a:s3"]["level"] == 3,
          [(k, by[k]["level"]) for k in ("a:s1", "a:s2", "a:s3")])
    check("tree: each child under its spawner", by["a:s2"]["parent"] == "a:s1" and by["a:s3"]["parent"] == "a:s2" and by["a:s1"]["parent"] == "main")
    check("tree: workflow run inside a subagent hangs under that subagent", by["w:wf_x"]["parent"] == "a:s2" and by["a:wa"]["parent"] == "w:wf_x",
          (by["w:wf_x"]["parent"], by["a:wa"]["parent"]))
    check("tree: workflow agent sits one level below its caller", by["a:wa"]["level"] == 3 and by["w:wf_x"]["status"] == "done", (by["a:wa"]["level"], by["w:wf_x"]["status"]))
    check("tree: max depth 3, 8 subagents, 1 workflow", sm_["max_depth"] == 3 and sm_["agents"] == 8 and sm_["workflows"] == 1, (sm_["max_depth"], sm_["agents"], sm_["workflows"]))
    check("tree: meta.parentAgentId links a child whose spawn call is gone", by["a:sm"]["parent"] == "a:s1" and by["a:sm"]["link"] == "meta", by["a:sm"]["link"])
    check("tree: an orphan hangs under the main thread, flagged", by["a:so"]["parent"] == "main" and by["a:so"]["link"] == "orphan" and sm_["orphans"] == 1,
          (by["a:so"]["parent"], by["a:so"]["link"]))
    r = by["a:sr"]
    check("tree: a resumed agent is one node; wall time spans the gap, active time does not",
          r["own"]["wall"] > 7000 and r["own"]["active"] < 200 and r["own"]["msgs"] == 3, (r["own"]["wall"], r["own"]["active"], r["own"]["msgs"]))
    bad = totals_consistent(t)
    check("tree: subtree totals = own + children, at every node", not bad, bad[:3])
    row = next(x for x in st.sessions_payload()["sessions"] if x["id"] == SA)
    s = st.sessions[SA]
    every = [s.acc] + [x.acc for x in s.subs]
    check("tree: session total tokens include every level", t["nodes"][0]["sub"]["tokens"] == row["tokens"] == sum(a.totals()["tokens"] for a in every),
          (t["nodes"][0]["sub"]["tokens"], row["tokens"]))
    check("tree: session total cost includes every level", abs(t["nodes"][0]["sub"]["cost"] - row["cost"]) < 1e-3, (t["nodes"][0]["sub"]["cost"], row["cost"]))
    check("tree: share of cost in subagents", abs(sm_["sub_share"] - sm_["sub_cost"] / sm_["cost"]) < 1e-3 and 0 < sm_["sub_share"] < 1, sm_["sub_share"])
    lv = {x["level"]: x["agents"] for x in sm_["levels"]}
    check("tree: agents per level", lv == {0: 1, 1: 4, 2: 2, 3: 2}, lv)
    # effort
    e = {k: (by[k]["effort"]["value"], by[k]["effort"]["source"], by[k]["effort"]["explicit"]) for k in ("main", "a:s1", "a:s2", "a:s3", "a:wa", "a:sr", "a:sq")}
    check("effort: main recorded", e["main"] == ("high", "recorded", True), e["main"])
    check("effort: s1 recorded (and asked for) low", e["a:s1"] == ("low", "recorded", True), e["a:s1"])
    check("effort: s2 and s3 inherit low, marked inferred", e["a:s2"] == ("low", "inherited", False) and e["a:s3"] == ("low", "inherited", False), (e["a:s2"], e["a:s3"]))
    check("effort: a workflow agent inherits from the workflow's caller", e["a:wa"] == ("low", "inherited", False), e["a:wa"])
    check("effort: an unrecorded child of the main thread inherits high", e["a:sr"] == ("high", "inherited", False), e["a:sr"])
    check("effort: asked for max, nothing recorded -> requested, explicit", e["a:sq"] == ("max", "requested", True), e["a:sq"])
    cell = next((c for c in sm_["matrix"] if c["model"] == "Opus 5.5" and c["effort"] == "high"), None)
    check("effort: matrix splits explicit and inferred", cell and cell["explicit"]["agents"] == 1 and cell["inferred"]["agents"] >= 1, cell)
    # thinking
    th = by["main"]["thinking"]
    check("thinking: per node blocks, text and tokens", th["blocks"] == 3 and th["text_blocks"] == 1 and th["tokens"] == 170 and th["measured_msgs"] == 3,
          th)
    check("thinking: a message saved at stream start has no count", by["a:s2"]["thinking"]["measured_msgs"] == 0 and by["a:s2"]["thinking"]["blocks"] == 1,
          by["a:s2"]["thinking"])
    tr = next((x for x in sm_["thinking"] if x["model"] == "Opus 5.5" and x["effort"] == "high"), None)
    check("thinking: share per model and effort", tr and tr["thinking"] == 170 and tr["output"] > 170, tr)
    check("model switch: counted on the node", by["a:s1"]["model_switches"] == 1 and len(by["a:s1"]["models"]) == 2, by["a:s1"]["models"])
    # session payload, agent payload, live, overview
    sp = st.session_payload(SA)
    check("session payload: agents summary and effort", sp["agents"]["agents"] == 8 and sp["agents"]["max_depth"] == 3
          and sp["effort"]["value"] == "high" and sp["effort"]["explicit"] and sp["effort"]["thinking_mode"] == "adaptive/updates", (sp["agents"], sp["effort"]))
    ap = st.agent_payload(SA, "s2")
    check("agent payload: effort for the header", ap["node"]["effort"]["source"] == "inherited", ap["node"])
    live = st.live_payload()
    lv_rows = {a["key"]: a for a in live["agents"]}
    main_row = lv_rows.get(SA)
    check("live: main thread shows its recorded effort", main_row and main_row["effort"] == "high" and main_row["effort_source"] == "recorded", main_row and main_row["effort"])
    # the Factory floor takes the same field (its inspector head draws the chip from it)
    floor_node = {n["id"]: n for n in graph_mod.payload(st)["nodes"]}.get("s:" + SA, {})
    check("floor: the session node carries the board row's effort and where it came from",
          floor_node.get("effort") == "high" and floor_node.get("effort_source") == "recorded", (floor_node.get("effort"), floor_node.get("effort_source")))
    ov = st.overview_payload()
    om = next((x for x in ov["models"] if x["model"] == OPUS), {})
    check("overview: recorded effort per model", om.get("effort", {}).get("high", 0) >= 4, om.get("effort"))
    # cached: same object back while nothing changed
    check("cache: a second build with nothing changed is reused", st.agent_tree(st.sessions[SA], NOW) is st.agent_tree(st.sessions[SA], NOW))

    tb = nodes_by_id(st.tree_payload(SB))
    check("effort: no record -> model default medium, inferred", (tb["main"]["effort"]["value"], tb["main"]["effort"]["source"], tb["main"]["effort"]["explicit"]) == ("medium", "default", False),
          tb["main"]["effort"])
    check("effort: a child of a default parent is at its own model's default", (tb["a:b1"]["effort"]["value"], tb["a:b1"]["effort"]["source"]) == ("medium", "default"), tb["a:b1"]["effort"])
    check("effort: a model with no known default is unknown, never guessed", tb["a:b2"]["effort"]["source"] == "unknown" and not tb["a:b2"]["effort"]["value"], tb["a:b2"]["effort"])

    tc = st.tree_payload(SC)
    bc = nodes_by_id(tc)
    check("cycle: both agents kept, flagged, under the main thread",
          {bc["a:ca"]["link"], bc["a:cb"]["link"]} >= {"cycle"} and "a:ca" in bc and "a:cb" in bc
          and (bc["a:ca"]["parent"] == "main" or bc["a:cb"]["parent"] == "main"), [(k, bc[k]["link"], bc[k]["parent"]) for k in ("a:ca", "a:cb")])
    check("cycle: totals still add up", not totals_consistent(tc) and tc["nodes"][0]["sub"]["agents"] == 2, totals_consistent(tc)[:2])

    td = st.tree_payload(SD)
    check("depth bound: all %d agents kept" % DEEP, td["summary"]["agents"] == DEEP, td["summary"]["agents"])
    check("depth bound: no node deeper than MAX_DEPTH", max(n["depth"] for n in td["nodes"]) <= agent_tree.MAX_DEPTH, max(n["depth"] for n in td["nodes"]))
    check("depth bound: flattened nodes flagged, totals add up", any(n.get("deep") for n in td["nodes"]) and not totals_consistent(td))
    check("depth bound: root total = every transcript", td["nodes"][0]["sub"]["tokens"] == sum(a.totals()["tokens"] for a in [st.sessions[SD].acc] + [x.acc for x in st.sessions[SD].subs]))


def _req(port, path, token=True):
    import dash_pairing
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.putrequest("GET", path, skip_host=True)
    c.putheader("Host", "127.0.0.1:%d" % port)
    if not token:
        c._cc_sent = True          # dash_pairing adds the token unless told it was sent
        dash_pairing._orig_endheaders(c)
    else:
        c.endheaders()
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, body


def test_endpoint():
    st = Store(root=CFG, days=1, clock=lambda: NOW)
    srv = make_server(0, st)
    th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    th.start()
    try:
        s, _ = _req(srv.port, "/api/session/tree?id=" + SA, token=False)
        check("endpoint: /api/session/tree without the token -> 401", s == 401, s)
        auto_token(srv)
        s, body = _req(srv.port, "/api/session/tree?id=" + SA)
        data = json.loads(body) if s == 200 else {}
        check("endpoint: /api/session/tree with the token -> nodes", s == 200 and data.get("nodes") and data["nodes"][0]["id"] == "main", s)
        s, _ = _req(srv.port, "/api/session/tree?id=missing")
        check("endpoint: unknown session -> 404", s == 404, s)
        s, _ = _req(srv.port, "/agents.js")
        check("endpoint: agents.js served", s == 200, s)
    finally:
        srv.shutdown()
        srv.server_close()


BROWSER_JS = r"""
const { chromium } = require(process.env.PW);
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME, headless: true });
  const out = {};
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 1000 }, colorScheme: 'light' })).newPage();
  const errs = [];
  page.on('pageerror', (e) => errs.push(String(e)));
  let polls = 0;
  page.on('request', (rq) => { if (rq.url().includes('/api/session/tree')) polls += 1; });
  await page.goto(`http://127.0.0.1:${process.env.PORT}/pair#code=${process.env.PAIR}`, { waitUntil: 'load' });
  await page.waitForTimeout(800);
  await page.goto(`http://127.0.0.1:${process.env.PORT}/#/s/${process.env.SID}`, { waitUntil: 'load' });
  await page.waitForSelector('.dtabs [data-key="agents"]', { timeout: 15000 });
  await page.click('.dtabs [data-key="agents"]');
  await page.waitForSelector('.p-table tbody tr', { timeout: 15000 });
  await page.waitForTimeout(3500);
  out.rows = await page.$$eval('.p-table tbody tr', (r) => r.map((x) => x.dataset.key));
  out.inferred = await page.$$eval('.p-table .tag.inferred', (r) => r.length);
  out.charts = await page.$$eval('.chart svg', (r) => r.length);
  out.gantt = await page.$$eval('.gantt-row', (r) => r.length);
  out.kpis = await page.$$eval('.kpi .label', (r) => r.map((x) => x.textContent));
  if (process.env.SHOTS) {
    await page.screenshot({ path: process.env.SHOTS + '/synthetic-agents-light.png', fullPage: true });
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.waitForTimeout(300);
    await page.screenshot({ path: process.env.SHOTS + '/synthetic-agents-dark.png', fullPage: true });
    await page.emulateMedia({ colorScheme: 'light' });
  }
  await page.evaluate(() => {
    window.__m = { childList: 0, attributes: 0, characterData: 0 };
    window.__what = [];
    new MutationObserver((ms) => { for (const m of ms) { window.__m[m.type] += 1; if (window.__what.length < 8) window.__what.push(m.type + ' ' + (m.attributeName || '') + ' ' + (m.target.className && m.target.className.baseVal != null ? m.target.className.baseVal : m.target.className)); } })
      .observe(document.querySelector('#main'), { subtree: true, childList: true, attributes: true, characterData: true });
  });
  const p0 = polls;
  await page.waitForTimeout(12000);
  out.flicker = await page.evaluate(() => window.__m);
  out.flickerWhat = await page.evaluate(() => window.__what);
  out.polls = polls - p0;
  // collapse s1: its subtree leaves the table and the timeline
  await page.click('.p-table tr[data-key="a:s1"] .p-exp');
  await page.waitForTimeout(400);
  out.afterCollapse = await page.$$eval('.p-table tbody tr', (r) => r.map((x) => x.dataset.key));
  out.ganttAfter = await page.$$eval('.gantt-row', (r) => r.length);
  await page.click('.p-table tr[data-key="a:sq"]');
  await page.waitForTimeout(400);
  out.hash = await page.evaluate(() => location.hash);
  // The report handed back by s3, rendered as Markdown with the Docs renderer.
  await page.goto(`http://127.0.0.1:${process.env.PORT}/#/s/${process.env.SID}/a/s3`, { waitUntil: 'load' });
  await page.waitForSelector('.md-tl', { timeout: 15000 });
  await page.waitForTimeout(1500);
  out.md = await page.evaluate(() => {
    const r = [...document.querySelectorAll('.md-tl')].find((x) => x.textContent.includes('Branch'));
    return r ? { strong: r.querySelector('strong') ? r.querySelector('strong').textContent : '', code: r.querySelectorAll('code').length, li: r.querySelectorAll('li').length,
      table: !!r.querySelector('table'), scriptEl: !!document.querySelector('#main script'), scriptText: r.textContent.includes('<script>'), pwned: !!window.__pwned,
      links: [...r.querySelectorAll('a')].map((a) => a.getAttribute('href')), heading: getComputedStyle(r.querySelector('h2')).fontSize } : null;
  });
  // Markdown entries on a live session's timeline must morph in place on polls.
  await page.goto(`http://127.0.0.1:${process.env.PORT}/#/s/${process.env.SID}`, { waitUntil: 'load' });
  await page.waitForSelector('.tl .md-tl', { timeout: 15000 });
  await page.waitForTimeout(3500);
  let agentPolls = 0;
  page.on('request', (rq) => { if (rq.url().includes('/api/session?')) agentPolls += 1; });
  await page.evaluate(() => {
    window.__m = { childList: 0, attributes: 0, characterData: 0 };
    new MutationObserver((ms) => { for (const m of ms) window.__m[m.type] += 1; })
      .observe(document.querySelector('#main'), { subtree: true, childList: true, attributes: true, characterData: true });
  });
  await page.waitForTimeout(9000);
  out.agentFlicker = await page.evaluate(() => window.__m);
  out.agentPolls = agentPolls;
  // Control row on a running subagent, then the same agent once it is done.
  let doneMode = false;
  await page.route('**/api/live', async (route) => {
    const resp = await route.fetch();
    const j = await resp.json();
    if (doneMode) for (const a of j.agents) if (a.agent_id === 'ew') { a.state = 'done'; a.tool = null; }
    await route.fulfill({ response: resp, json: j });
  });
  await page.goto(`http://127.0.0.1:${process.env.PORT}/#/s/${process.env.SID_E}/a/ew`, { waitUntil: 'load' });
  await page.waitForSelector('.ctl-panel', { state: 'attached', timeout: 15000 });
  await page.waitForTimeout(6500);
  const ctl = () => page.evaluate(() => { const p = document.querySelector('.ctl-panel'); return p ? { panel: !p.hidden, stop: !p.querySelector('.ctl-stop-row').hidden,
    history: !p.querySelector('.ctl-history').hidden, text: p.textContent } : null; });
  out.ctlRunning = await ctl();
  doneMode = true;
  await page.waitForTimeout(7500);
  out.ctlDone = await ctl();
  // The effort chip on the Factory floor: the session's inspector head names its recorded effort (a solid chip, not an inferred one).
  await page.goto(`http://127.0.0.1:${process.env.PORT}/#/factory`, { waitUntil: 'load' });
  const nid = 's:' + process.env.SID;
  await page.waitForSelector('.gx-node[data-id=' + JSON.stringify(nid) + ']', { timeout: 15000 });
  await page.waitForTimeout(800);
  await page.evaluate((id) => { try { window.ccFactory.graph().center(id, { animate: false }); } catch (e) { /* no engine */ } }, nid);
  await page.waitForTimeout(250);
  const pt = await page.evaluate((id) => { const r = document.querySelector('.gx-node[data-id=' + JSON.stringify(id) + ']').getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; }, nid);
  await page.mouse.click(pt.x, pt.y);
  await page.waitForFunction(() => { const a = document.querySelector('aside.fx-inspector'); return a && !a.hidden; }, null, { timeout: 4000 });
  out.floorChip = await page.evaluate(() => {
    const head = document.querySelector('aside.fx-inspector .fx-insp-kind');
    return { text: head.textContent.replace(/\s+/g, ' ').trim(), tags: [...head.querySelectorAll('.tag')].map((t) => ({ text: t.textContent.trim(), inferred: t.classList.contains('inferred'), title: t.title })) };
  });
  out.errors = errs;
  await browser.close();
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def test_browser():
    if not (os.path.isdir(PLAYWRIGHT) and os.path.exists(CHROME) and shutil.which("node")):
        print("browser: skipped (no playwright-core, Chrome or node)")
        return
    st = Store(root=CFG, days=1, clock=lambda: NOW)   # frozen: polls bring nothing new
    srv = make_server(0, st)
    from cc_dash.control import Control
    ctl = Control(st, srv.port, ctl_dir=os.path.join(BASE, "ctl"), agents_cmd=None, sockets=False, hook_check=False)
    ctl.start(thread=False)
    srv.control = ctl
    th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    th.start()
    js = os.path.join(BASE, "tree_check.js")
    with open(js, "w") as fh:
        fh.write(BROWSER_JS)
    env = dict(os.environ, PW=PLAYWRIGHT, CHROME=CHROME, PORT=str(srv.port), PAIR=srv.auth.new_code(), SID=SA, SID_E=SE, SHOTS=SHOTS)
    try:
        r = subprocess.run(["node", js], capture_output=True, text=True, timeout=180, env=env)
    finally:
        srv.shutdown()
        srv.server_close()
    if r.returncode != 0:
        check("browser: page check ran", False, r.stderr[-800:])
        return
    o = json.loads(r.stdout.strip().splitlines()[-1])
    check("browser: no page errors", not o["errors"], o["errors"])
    check("browser: one table row per node, in tree order", o["rows"][:6] == ["main", "a:s1", "a:s2", "a:s3", "w:wf_x", "a:wa"] and len(o["rows"]) == 10, o["rows"])
    check("browser: inferred efforts drawn as the dashed tag", o["inferred"] >= 3, o["inferred"])
    check("browser: four charts (depth, matrix, icicle, plus columns host)", o["charts"] >= 3, o["charts"])
    check("browser: nested timeline has a row per visible non-main node", o["gantt"] == len(o["rows"]) - 1, (o["gantt"], len(o["rows"])))
    check("browser: KPI strip names depth and subagent cost", "Cost in subagents" in o["kpis"] and "Subagents" in o["kpis"], o["kpis"])
    print("flicker: %d tree polls, mutations %s" % (o["polls"], o["flicker"]))
    check("browser: 3 or more polls watched", o["polls"] >= 3, o["polls"])
    check("browser: no node added, removed or changed across polls with unchanged data", o["flicker"] == {"childList": 0, "attributes": 0, "characterData": 0}, (o["flicker"], o.get("flickerWhat")))
    check("browser: collapsing a row hides its subtree in the table and the timeline",
          "a:s2" not in o["afterCollapse"] and "a:s1" in o["afterCollapse"] and o["ganttAfter"] == len(o["afterCollapse"]) - 1, (o["afterCollapse"], o["ganttAfter"]))
    check("browser: a row opens that agent's timeline", o["hash"] == "#/s/%s/a/sq" % SA, o["hash"])
    md = o.get("md") or {}
    check("markdown: a handed-back report renders bold, inline code, a list and a table", md.get("strong") == "Branch / commit:" and md.get("code", 0) >= 2
          and md.get("li") == 2 and md.get("table"), md)
    check("markdown: raw <script> stays text and never runs", md.get("scriptText") and not md.get("scriptEl") and not md.get("pwned"), md)
    check("markdown: https link kept, javascript: link dropped", "https://example.com/x" in (md.get("links") or []) and not any((h or "").startswith("javascript") for h in md.get("links") or []), md.get("links"))
    check("markdown: headings scaled to the timeline (14px at most)", md.get("heading") in ("13px", "14px"), md.get("heading"))
    check("markdown: a live timeline with Markdown entries morphs in place across polls", o["agentPolls"] >= 2 and o["agentFlicker"] == {"childList": 0, "attributes": 0, "characterData": 0}, (o["agentPolls"], o["agentFlicker"]))
    cr, cd = o.get("ctlRunning") or {}, o.get("ctlDone") or {}
    chip = [t for t in (o.get("floorChip") or {}).get("tags", []) if t["text"].startswith("effort")]
    check("floor: the inspector head carries the recorded effort chip (solid, with where it came from)",
          len(chip) == 1 and chip[0]["text"] == "effort high" and not chip[0]["inferred"] and "recorded" in chip[0]["title"], o.get("floorChip"))
    check("control: a running subagent shows Stop and its (empty) history", cr.get("panel") and cr.get("stop") and cr.get("history"), cr)
    check("control: once done, no Stop row, no empty history, no panel", cd and not cd.get("stop") and not cd.get("history") and not cd.get("panel"), cd)


def main():
    try:
        for fn in (test_parser, test_tokens, test_tree, test_endpoint, test_browser):
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
