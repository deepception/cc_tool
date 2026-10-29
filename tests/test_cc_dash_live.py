#!/usr/bin/env python3
"""Live board checks for cc-dash (lib/cc_dash/store.py live_payload, GET /api/live) and the
Factory floor drawn from the same Store (lib/cc_dash/graph.py, GET /api/graph).

Builds a fake ${CLAUDE_CONFIG_DIR}/projects tree in a temp dir with a fixed
clock, one session per situation the board must tell apart: a lead waiting on
a subagent that runs a tool, a nested subagent, a finished background agent
that lingers then drops, a workflow with done, running and failed agents, an
idle session, a crashed one (pending tool, no writes: "stalled?"), a stuck
model turn, an AskUserQuestion, an SDK run that ended on StructuredOutput, a
background agent still running under an old main thread, and a busy session
for the feed cap. Then it checks state derivation, tree linkage, sparkline
buckets, the feed, incremental re-reads after appends, the /api/live shape and
latency with many live transcripts, and that /api/graph puts every agent of
that board on the floor.

The board has no page of its own any more (the Factory floor is the landing view and
the one place that shows it), so the browser part (when headless Chrome and
playwright-core are present) opens the floor over the same real store, with hook
events in a temp XDG_STATE_HOME standing in for the alerts: every agent has a node,
the alerted subagent and the transcript AskUserQuestion need you (node, alert strip,
totals), the workflow reads "2 of 3", opening an agent from the floor (the inspector's
Open agent link, a double-click) lands on its page, an alert for a session with no
transcript lands on its project's hub, the floor works when /api/alerts answers 404,
#/live is replaced by #/factory and the nav has two links, and polls with unchanged
data add, remove or replace no node on the floor, move nothing and leave the sync bar
alone; the recent actions hold still under the pointer while a call arrives. Nothing outside
the temp dir is touched.

Run:  python3 tests/test_cc_dash_live.py
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
from http.server import ThreadingHTTPServer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "lib"))
from cc_dash import graph as graph_mod  # noqa: E402
from cc_dash import store as store_mod  # noqa: E402
from cc_dash.attention import Attention  # noqa: E402
from cc_dash.server import Handler, make_server  # noqa: E402
from cc_dash.auth import Auth  # noqa: E402
from dash_pairing import auto_cookie  # noqa: E402  (tests/dash_pairing.py: every request carries the pairing cookie)
from cc_dash.store import Store  # noqa: E402

RESULTS = []
PLAYWRIGHT = os.environ.get("CC_DASH_PLAYWRIGHT", "/home/lukasz/Files/hobby/color-analyzer/webapp/node_modules/playwright-core")
CHROME = os.environ.get("CC_DASH_CHROME", "/usr/bin/google-chrome")
SHOTS = os.environ.get("CC_DASH_SHOTS", "")     # optional: save the browser check's screenshots here


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


BASE = tempfile.mkdtemp(prefix="cc-dash-live-test-")
CFG = os.path.join(BASE, "claude")
STATE = os.path.join(BASE, "state")                # attention.py reads the hooks' log from ${XDG_STATE_HOME}/cc_tool
LOGDIR = os.path.join(STATE, "cc_tool")
LOG = os.path.join(LOGDIR, "attention.jsonl")
os.makedirs(LOGDIR, mode=0o700)
os.chmod(LOGDIR, 0o700)
os.environ["XDG_STATE_HOME"] = STATE               # before any Attention exists: no real log is ever read
T = (int(time.time()) // 60) * 60 + 30            # "now": the middle of a wall-clock minute
NOW = [float(T)]


class World:
    """Writes one session's transcripts; every file's mtime is its last record's time."""

    def __init__(self, name, sid):
        self.cwd = os.path.join(BASE, "work", name)
        os.makedirs(self.cwd, exist_ok=True)
        self.pdir = os.path.join(CFG, "projects", self.cwd.replace("/", "-"))
        os.makedirs(self.pdir, exist_ok=True)
        self.sid = sid
        self.main = os.path.join(self.pdir, sid + ".jsonl")
        self.subdir = os.path.join(self.pdir, sid, "subagents")
        self.n = 0

    def rec(self, kind, t, agent=None, **kw):
        self.n += 1
        r = {"type": kind, "timestamp": iso(t), "sessionId": self.sid, "cwd": self.cwd, "gitBranch": "main",
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


def emit(event, session, ts, **kw):
    """One attention-hook line in the temp state dir (0700 dir, 0600 file, as the hook writes it)."""
    rec = {"v": 1, "ts": round(ts, 3), "event": event, "session": session, "agent": kw.pop("agent", None),
           "agent_type": kw.pop("agent_type", None), "cwd": kw.pop("cwd", ""), "transcript": ""}
    rec.update(kw)
    fd = os.open(LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    os.write(fd, (json.dumps(rec) + "\n").encode())
    os.close(fd)


# ── S1: lead waiting on a subagent; nested agent; background agent done; workflow ──
S1 = World("alpha", "11111111-0000-0000-0000-000000000001")
S1.write_main([
    S1.prompt(T - 300, "Ship the feature"),
    S1.tool(T - 295, "tu_wf", "Workflow", {"script": "export const meta = { name: 'review-sweep' }"}),
    S1.result(T - 294, "tu_wf", "launched", tur={"status": "async_launched", "runId": "wf_1"}),
    S1.tool(T - 290, "tu_bg", "Agent", {"description": "Background survey", "subagent_type": "Explore", "run_in_background": True, "prompt": "x"}),
    S1.result(T - 289, "tu_bg", "launched", tur={"status": "async_launched", "agentId": "c2"}),
    S1.tool(T - 280, "tu_c1", "Agent", {"description": "Write the tests", "subagent_type": "general-purpose", "prompt": "x"}),
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
], meta={"agentType": "general-purpose", "description": "Write the tests", "toolUseId": "tu_c1"})
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
S1.write_sub("w1", [S1.prompt(T - 293, "Review a", agent="w1"), S1.text(T - 100, "w1m", "a is fine", agent="w1", model="claude-haiku-5-5", stop=None)],
             meta={"agentType": "workflow-subagent", "description": "review:a", "workflowPhase": "Review"}, run="wf_1")
S1.write_sub("w2", [S1.prompt(T - 293, "Review b", agent="w2"), S1.tool(T - 10, "w2_read", "Read", {"file_path": "/w/b.py"}, agent="w2", model="claude-haiku-5-5")],
             meta={"agentType": "workflow-subagent", "description": "review:b", "workflowPhase": "Review"}, run="wf_1")
S1.write_sub("w3", [S1.prompt(T - 292, "Review c", agent="w3"), S1.text(T - 50, "w3m", "cannot", agent="w3", model="claude-haiku-5-5", stop=None)],
             meta={"agentType": "workflow-subagent", "description": "review:c"}, run="wf_1")
with open(os.path.join(S1.subdir, "workflows", "wf_1", "journal.jsonl"), "w") as fh:
    for r in [{"type": "launched"},
              {"type": "started", "agentId": "w1", "label": "review:a", "phase": "Review"},
              {"type": "started", "agentId": "w2", "label": "review:b", "phase": "Review"},
              {"type": "started", "agentId": "w3", "label": "review:c", "phase": "Verify"},
              {"type": "result", "agentId": "w1", "result": "fine"},
              {"type": "failed", "agentId": "w3"}]:
        fh.write(json.dumps(r) + "\n")
os.makedirs(os.path.join(S1.pdir, S1.sid, "workflows"), exist_ok=True)
with open(os.path.join(S1.pdir, S1.sid, "workflows", "wf_1.json"), "w") as fh:
    json.dump({"runId": "wf_1", "workflowName": "review-sweep", "status": "running", "agentCount": 3,
               "phases": [{"title": "Review"}, {"title": "Verify"}]}, fh)

# ── S2: idle at the prompt ──
S2 = World("beta", "22222222-0000-0000-0000-000000000002")
S2.write_main([S2.prompt(T - 160, "Explain the bug"), S2.text(T - 100, "s2m", "It was a stale cache key.")])

# ── S3: crashed with a pending Bash: stalled, still shown; S4: same but past the show window ──
S3 = World("gamma", "33333333-0000-0000-0000-000000000003")
S3.write_main([S3.prompt(T - 1010, "Run the suite"), S3.tool(T - 1000, "s3_bash", "Bash", {"command": "make test"})])
S4 = World("delta", "44444444-0000-0000-0000-000000000004")
S4.write_main([S4.prompt(T - 2510, "Old"), S4.tool(T - 2500, "s4_bash", "Bash", {"command": "make"})])

# ── S5: a model turn that never came back ──
S5 = World("epsilon", "55555555-0000-0000-0000-000000000005")
S5.write_main([S5.prompt(T - 500, "Think hard")])

# ── S6: waiting on AskUserQuestion ──
S6 = World("zeta", "66666666-0000-0000-0000-000000000006")
S6.write_main([S6.prompt(T - 40, "Pick a name"), S6.tool(T - 30, "s6_ask", "AskUserQuestion", {"questions": [{"question": "Which name?"}]})])

# ── S7: an SDK run that ended on StructuredOutput ──
S7 = World("eta", "77777777-0000-0000-0000-000000000007")
S7.write_main([S7.prompt(T - 260, "Review"), S7.tool(T - 210, "s7_out", "StructuredOutput", {"verdict": "ok"}), S7.result(T - 200, "s7_out", "done")])

# ── S8: main thread quiet for 15 minutes, its background agent still running ──
S8 = World("theta", "88888888-0000-0000-0000-000000000008")
S8.write_main([S8.prompt(T - 1000, "Kick off"), S8.tool(T - 995, "s8_bg", "Agent", {"description": "Long job", "run_in_background": True, "prompt": "z"}),
               S8.result(T - 994, "s8_bg", "launched", tur={"status": "async_launched", "agentId": "b1"}), S8.text(T - 990, "s8m", "Started the long job.")])
S8.write_sub("b1", [S8.prompt(T - 993, "Long job", agent="b1"), S8.tool(T - 20, "b1_bash", "Bash", {"command": "sleep 30"}, agent="b1")],
             meta={"agentType": "general-purpose", "description": "Long job", "toolUseId": "s8_bg", "requestShape": "background"})

# ── S9: 70 tool calls for the feed cap ──
S9 = World("iota", "99999999-0000-0000-0000-000000000009")
recs = [S9.prompt(T - 610, "Lots of reads")]
for i in range(70):
    recs += [S9.tool(T - 600 + i * 5, f"s9_{i}", "Read", {"file_path": f"/w/f{i}.py"}), S9.result(T - 599 + i * 5, f"s9_{i}")]
recs.append(S9.text(T - 240, "s9m", "Read them all."))
S9.write_main(recs)

# ── hook events: what the floor's alert strip shows ──
# A permission on S1's running subagent c1, and "your turn" for a session that has no transcript
# (its project's hub carries the alert). S6's AskUserQuestion needs no hook: it is read from the transcript.
ELSEWHERE = "deadbeef-0000-0000-0000-00000000dead"
emit("PermissionRequest", S1.sid, T - 30, agent="c1", agent_type="general-purpose", tool="Bash", sig="pytest -q tests/",
     message="pytest -q tests/", cwd=S1.cwd)
emit("Notification", ELSEWHERE, T - 90, ntype="idle_prompt", message="Claude is waiting for your input", cwd="/w/elsewhere")

os.environ["CLAUDE_CONFIG_DIR"] = CFG


def board(st):
    p = st.live_payload()
    return p, {a["key"]: a for a in p["agents"]}


def test_states():
    st = Store(days=1, clock=lambda: NOW[0])
    p, A = board(st)
    k1, kc1, kc2, kc3 = S1.sid, S1.sid + "/c1", S1.sid + "/c2", S1.sid + "/c3"
    check("state: lead with a pending Agent call is 'waiting'", A[k1]["state"] == "waiting", A[k1]["state"])
    check("state: lead waits on the subagents still running", A[k1]["waiting_on"] >= 1, A[k1]["waiting_on"])
    c1 = A[kc1]
    check("state: subagent with a pending Bash is 'tool'", c1["state"] == "tool", c1["state"])
    check("state: running tool name and target", c1["tool"]["name"] == "Bash" and "pytest" in c1["tool"]["target"], c1["tool"])
    check("state: tool elapsed counts from the tool_use time", abs((NOW[0] - c1["tool"]["t"]) - 42) < 0.01, NOW[0] - c1["tool"]["t"])
    check("state: model is the one running now", c1["model"] == "Sonnet 5.5" and c1["family"] == "sonnet", c1["model"])
    check("pulse: tool calls and errors counted", c1["tool_calls"] == 4 and c1["errors"] == 1, (c1["tool_calls"], c1["errors"]))
    check("pulse: tokens so far and a burn rate", c1["tokens"] > 0 and c1["burn"]["tok_min"] > 0 and c1["burn"]["out_min"] > 0, c1["burn"])
    check("state: model turn after a tool result is 'generating'", A[kc3]["state"] == "generating", A[kc3]["state"])
    check("state: handed-back background agent is 'done' and lingers", A.get(kc2, {}).get("state") == "done", A.get(kc2))
    check("state: idle at the prompt after end_turn", A[S2.sid]["state"] == "idle", A[S2.sid]["state"])
    check("state: crashed session with a pending tool is 'stalled', not running",
          A.get(S3.sid, {}).get("state") == "stalled" and A[S3.sid]["stalled_for"] >= 1000, A.get(S3.sid))
    check("state: stalled keeps what it was doing", (A[S3.sid]["tool"] or {}).get("name") == "Bash")
    check("state: stalled agents leave the board after the show window", S4.sid not in A)
    check("state: a model turn with no write past the cap is 'stalled'", A.get(S5.sid, {}).get("state") == "stalled", A.get(S5.sid))
    n6 = A[S6.sid]
    check("state: pending AskUserQuestion needs you (from the transcript)",
          n6["needs"] and n6["needs"]["kind"] == "question" and n6["needs"]["source"] == "transcript", n6["needs"])
    check("state: SDK run ended on StructuredOutput is idle, not stalled", A[S7.sid]["state"] == "idle", A[S7.sid]["state"])
    check("state: old main thread with a running background agent is 'waiting'",
          A[S8.sid]["state"] == "waiting" and A[S8.sid]["waiting_on"] == 1, (A[S8.sid]["state"], A[S8.sid]["waiting_on"]))
    check("state: that background agent is visible and running", A[S8.sid + "/b1"]["state"] == "tool")
    w = {k.split("/")[1]: a for k, a in A.items() if a["run_id"] == "wf_1"}
    check("state: workflow agent done per journal", w.get("w1", {}).get("state") == "done", w.get("w1"))
    check("state: workflow agent failed per journal", w.get("w3", {}).get("state") == "failed", w.get("w3"))
    check("state: workflow agent with a pending Read is running", w.get("w2", {}).get("state") == "tool", w.get("w2"))
    c = p["counts"]
    check("counts: needs, working, stalled, idle, done, failed tallied",
          c["needs"] == 1 and c["stalled"] == 2 and c["failed"] == 1 and c["done"] == 2 and c["working"] >= 6 and c["idle"] >= 3, c)
    g = [x["session"] for x in p["groups"]]
    check("groups: the session that needs you sorts first", g[0] == S6.sid, g[:3])
    return st


def test_tree(st):
    p, A = board(st)
    k1 = S1.sid
    order = [a["key"] for a in p["agents"] if a["session"] == k1]
    check("tree: main thread first, at depth 0", order[0] == k1 and A[k1]["depth"] == 0 and A[k1]["parent"] is None, order[:2])
    check("tree: subagent parent is the main thread", A[k1 + "/c1"]["parent"] == k1 and A[k1 + "/c1"]["depth"] == 1)
    check("tree: nested subagent parent is the subagent that spawned it",
          A[k1 + "/c3"]["parent"] == k1 + "/c1" and A[k1 + "/c3"]["depth"] == 2, (A[k1 + "/c3"]["parent"], A[k1 + "/c3"]["depth"]))
    check("tree: depth-first order puts the nested agent right after its parent",
          order.index(k1 + "/c3") == order.index(k1 + "/c1") + 1, order)
    wf_keys = [k for k in order if A[k]["run_id"] == "wf_1"]
    check("tree: workflow agents linked to the agent that called Workflow",
          len(wf_keys) == 3 and all(A[k]["parent"] == k1 and A[k]["kind"] == "workflow" and A[k]["depth"] == 2 for k in wf_keys), wf_keys)
    idx = [order.index(k) for k in wf_keys]
    check("tree: a workflow's agents stay together", max(idx) - min(idx) == 2, idx)
    check("tree: workflow phase from meta or journal", A[k1 + "/w1"]["phase"] == "Review" and A[k1 + "/w3"]["phase"] == "Verify",
          (A[k1 + "/w1"]["phase"], A[k1 + "/w3"]["phase"]))
    wf = [w for w in p["workflows"] if w["run_id"] == "wf_1"]
    check("tree: one workflow header with name, depth and progress",
          len(wf) == 1 and wf[0]["name"] == "review-sweep" and wf[0]["depth"] == 1 and wf[0]["parent"] == k1
          and wf[0]["total"] == 3 and wf[0]["finished"] == 2 and wf[0]["failed"] == 1 and wf[0]["running"] == 1, wf)
    check("tree: identity fields", A[k1 + "/c1"]["label"] == "Write the tests" and A[k1 + "/c1"]["type"] == "general-purpose"
          and A[k1 + "/c2"]["background"] is True and A[k1]["kind"] == "main" and A[k1]["agent_id"] is None)
    check("tree: keys address alerts (session uuid, optional agent id)",
          all(a["key"] == (a["session"] + "/" + a["agent_id"] if a["agent_id"] else a["session"]) for a in p["agents"]))


def test_spark_feed(st):
    p, A = board(st)
    c1 = A[S1.sid + "/c1"]
    check("spark: 15 one-minute buckets ending after the current minute", len(c1["spark"]) == 15 and c1["spark_end"] == T + 30, c1["spark_end"] - T)
    check("spark: calls land in their minutes", [b["n"] for b in c1["spark"]] == [0] * 11 + [1, 1, 2, 0], c1["spark"])
    s9 = [b["n"] for b in A[S9.sid]["spark"]]
    check("spark: older calls fall off the 15-minute window", sum(s9) == sum(1 for i in range(70) if T - 600 + i * 5 >= T + 30 - 900), s9)
    feed = p["feed"]
    check("feed: capped at 50", len(feed) == store_mod.FEED_MAX == 50, len(feed))
    check("feed: newest first", all(feed[i]["t"] >= feed[i + 1]["t"] for i in range(len(feed) - 1)))
    check("feed: entry carries agent label, tool and target",
          any(f["key"] == S1.sid + "/c1" and f["name"] == "Bash" and f["status"] == "pending" and "pytest" in f["target"] and f["label"] == "Write the tests" for f in feed))
    check("feed: SubagentHandback is not a feed entry", not any(f["name"] == "SubagentHandback" for f in feed))
    check("feed: only the last 15 minutes", all(f["t"] >= NOW[0] - 900 for f in feed))


def test_linger_and_incremental():
    st = Store(days=1, clock=lambda: NOW[0])
    _, A = board(st)
    kc1, kc2 = S1.sid + "/c1", S1.sid + "/c2"
    # Append the Bash result to c1: only the new bytes are read and the state moves on.
    path = S1.sub_path("c1")
    before = os.path.getsize(path)
    b0 = st.cache.bytes_read
    NOW[0] = T + 2
    World.write(path, [S1.result(T + 1, "c1_bash", "12 passed", agent="c1")], append=True)
    st.refresh(force=True)
    _, A = board(st)
    added = os.path.getsize(path) - before
    check("incremental: an append re-reads only the appended bytes", st.cache.bytes_read - b0 == added, (st.cache.bytes_read - b0, added))
    check("incremental: tool result moves the agent to 'generating'", A[kc1]["state"] == "generating" and A[kc1]["tool"] is None, A[kc1]["state"])
    check("incremental: the tool's feed entry is no longer pending",
          any(f["id"] == "c1_bash" and f["status"] == "ok" for f in st.live_payload()["feed"]))
    # Linger: c2 finished at T-60; at T+90 it is past LINGER_SECS (120 s) and drops.
    NOW[0] = T + 50
    st.refresh(force=True)
    _, A = board(st)
    check("linger: finished agent still shown inside the linger window", kc2 in A and A[kc2]["state"] == "done")
    NOW[0] = T + 90
    st.refresh(force=True)
    _, A = board(st)
    check("linger: finished agent dropped after the linger window", kc2 not in A)
    check("linger: the running agents are still there", kc1 in A and S1.sid in A)
    NOW[0] = float(T)


def _get(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path, headers={"Host": f"127.0.0.1:{port}"})
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, body


def test_endpoint():
    st = Store(days=1, clock=lambda: NOW[0])
    srv = make_server(0, st, fallback=False)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    auto_cookie(srv)
    try:
        code, body = _get(srv.port, "/api/live")
        d = json.loads(body)
        check("endpoint: /api/live 200", code == 200, code)
        check("endpoint: board keys present", all(k in d for k in ("sessions", "agents", "groups", "workflows", "feed", "counts", "limits")), list(d))
        need = {"key", "session", "agent_id", "parent", "kind", "depth", "run_id", "phase", "label", "type", "background", "project",
                "model", "family", "state", "since", "tool", "needs", "waiting_on", "stalled_for", "start", "last_write", "age",
                "tokens", "cost", "burn", "tool_calls", "errors", "spark", "spark_end", "rate", "peak", "last_tool", "last_text"}
        check("endpoint: every agent row has the full shape", all(need <= set(a) for a in d["agents"]), need - set(d["agents"][0]))
        check("endpoint: groups carry project and title", all({"session", "project", "title", "cost", "agents", "running", "needs"} <= set(g) for g in d["groups"]))
        check("endpoint: the payload still carries the sessions list (control.js's composer reads /api/live)", isinstance(d["sessions"], list) and all("running_subs" in s for s in d["sessions"]))
    finally:
        srv.shutdown()
        srv.server_close()


def test_latency():
    """Many live transcripts: 12 extra sessions with 4 running subagents each."""
    for i in range(12):
        w = World(f"load{i}", f"aaaaaaaa-0000-0000-0000-{i:012d}")
        recs = [w.prompt(T - 400, "go")]
        for j in range(4):
            recs += [w.tool(T - 390 + j, f"sp{i}_{j}", "Agent", {"description": f"part {j}", "run_in_background": True, "prompt": "p"}),
                     w.result(T - 389 + j, f"sp{i}_{j}", "launched", tur={"status": "async_launched", "agentId": f"x{j}"})]
        recs.append(w.text(T - 380, f"lm{i}", "Waiting on four agents."))
        w.write_main(recs)
        for j in range(4):
            r = [w.prompt(T - 388, "part", agent=f"x{j}")]
            for k in range(200):
                r += [w.tool(T - 380 + k, f"x{i}{j}_{k}", "Read", {"file_path": f"/w/{k}"}, agent=f"x{j}"), w.result(T - 380 + k + .5, f"x{i}{j}_{k}", agent=f"x{j}")]
            r.append(w.tool(T - 3, f"x{i}{j}_run", "Bash", {"command": "make"}, agent=f"x{j}"))
            w.write_sub(f"x{j}", r, meta={"agentType": "general-purpose", "description": f"part {j}", "toolUseId": f"sp{i}_{j}"})
    st = Store(days=1, clock=lambda: NOW[0])
    st.refresh(force=True)
    times = []
    for _ in range(5):
        st._last = 0
        t0 = time.perf_counter()
        p = st.live_payload()
        times.append((time.perf_counter() - t0) * 1000)
    live = sum(1 for a in p["agents"] if a["state"] in store_mod.RUNNING)
    med = sorted(times)[2]
    print(f"latency: {len(p['agents'])} agents on the board ({live} running), warm live_payload median {med:.1f} ms")
    check("latency: board with ~70 agents builds in well under a poll interval", med < 300, f"{med:.1f} ms")
    check("latency: all extra subagents are running", sum(1 for a in p["agents"] if a["key"].startswith("aaaaaaaa") and a["state"] == "tool") == 48)



# ── the floor over the same store ────────────────────────────────────────────
def test_floor_payload():
    """GET /api/graph is built from this same Store: every agent of the board is a node, and the three
    kinds of "needs you" (a hook permission on an agent, a transcript question, a "your turn" for a session
    that has no transcript yet) land where the floor draws them."""
    st = Store(days=1, clock=lambda: NOW[0])
    g = graph_mod.payload(st)
    nodes = {n["id"]: n for n in g["nodes"]}
    _, A = board(st)
    want = {("a:" + k) if "/" in k else ("s:" + k) for k in A}
    check("floor: every agent and session of the board has a node on /api/graph", len(A) >= 12 and want <= set(nodes), sorted(want - set(nodes))[:3])
    c1, s6 = nodes.get("a:%s/c1" % S1.sid, {}), nodes.get("s:" + S6.sid, {})
    al = {a["node"]: a for a in g["alerts"]}
    check("floor: the hook permission is on the subagent it names", c1.get("needs") == "permission" and al.get("a:%s/c1" % S1.sid, {}).get("tool") == "Bash", (c1.get("needs"), al))
    check("floor: the transcript AskUserQuestion is on its session", s6.get("needs") == "question" and "s:" + S6.sid in al, (s6.get("needs"), list(al)))
    hub = nodes.get("p:elsewhere", {})
    check("floor: an alert for a session with no transcript sits on its project's hub",
          hub.get("kind") == "project" and al.get("p:elsewhere", {}).get("kind") == "idle", (hub.get("kind"), al.get("p:elsewhere")))
    check("floor: the totals count the two agents that need a decision (a 'your turn' is not one)", g["kpis"]["needs"] == 2, g["kpis"]["needs"])
    wf = nodes.get("w:%s/wf_1" % S1.sid, {}).get("detail", {})
    check("floor: the workflow run is a node with its progress (2 of 3 finished, 1 failed)",
          (wf.get("finished"), wf.get("total"), wf.get("failed")) == (2, 3, 1), wf)


STAMP = {"alerts": 0}


class StubHandler(Handler):
    def do_GET(self):  # noqa: N802
        if self.path == "/__test/code":     # test hook: a fresh one-time pairing code per browser context
            return self._json(200, {"code": self.server.auth.new_code()})
        if self.path.startswith("/__test/append"):
            # Test hook: one more tool call in the nested subagent's transcript.
            t = max(self.server.store.clock(), T) + 1   # newer than every synthetic record
            n = getattr(self.server, "appended", 0) + 1
            self.server.appended = n
            World.write(S1.sub_path("c3"), [S1.tool(t, f"c3_extra{n}", "Grep", {"pattern": f"extra{n}"}, agent="c3", model="claude-haiku-5-5")], append=True)
            self.server.store._last = 0
            return self._json(200, {"ok": n})
        if self.path.startswith("/api/alerts") and self.server.alerts_missing:
            # A build without the alerts endpoint: paired and on the right host, then 404.
            if not self._host_ok():
                return self._deny(421, "no")
            if self.server.auth.session(self.headers) is None:
                return self._json(401, {"error": "Not paired."})
            return self._deny(404, "Not found.")
        return super().do_GET()


def _stub_server(st, alerts_missing=False):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
    srv.daemon_threads = True
    srv.store, srv.verbose, srv.alerts_missing = st, False, alerts_missing
    srv.port = srv.server_address[1]
    srv.allowed_hosts = {f"127.0.0.1:{srv.port}", f"localhost:{srv.port}"}
    srv.auth, srv.control = Auth(srv.port), None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


BROWSER_JS = r"""
const { chromium } = require(process.env.PW);
const first = (e) => String((e && e.message) || e).split('\n')[0].slice(0, 300);
const { S1, S2, S6, PORT_A, PORT_B, PORT_C } = process.env;
const out = {};
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME, headless: true });
  const base = (port) => 'http://127.0.0.1:' + port;
  // Small readers for the floor, installed before the page's own scripts.
  const HELPERS = () => {
    const txt = (el) => (el ? el.textContent.replace(/\s+/g, ' ').trim() : null);
    const q = (id) => document.querySelector('.gx-node[data-id=' + JSON.stringify(id) + ']');
    const ids = () => [...document.querySelectorAll('.gx-node[data-id]')].map((e) => e.dataset.id);
    const face = (id) => { const n = q(id), f = n && n.querySelector('.fx-node'); return n ? { aria: n.getAttribute('aria-label'), cls: f ? String(f.className) : '', text: txt(f) } : null; };
    const insp = () => {
      const a = document.querySelector('aside.fx-inspector');
      if (!a || a.hidden) return { visible: false };
      return { visible: true, title: txt(a.querySelector('.fx-insp-title')), head: txt(a.querySelector('.fx-insp-kind')), body: txt(a.querySelector('.fx-insp-body')),
        links: [...a.querySelectorAll('a.fx-link')].map((x) => ({ key: x.dataset.key, href: x.getAttribute('href'), text: x.textContent.trim() })) };
    };
    const alerts = () => [...document.querySelectorAll('.fx-alerts .fx-alert')].map((b) => ({ node: b.dataset.node, text: txt(b), aria: b.getAttribute('aria-label') }));
    const hud = () => { const o = {}; document.querySelectorAll('.fx-hud .fx-kpi[data-k]').forEach((k) => { o[k.dataset.k] = txt(k.querySelector('.fx-kpi-v')); }); return o; };
    window.__t = { txt, q, ids, face, insp, alerts, hud };
  };
  // A fresh paired page (a new one-time code per context). Returns where pairing landed.
  const open = async (port, opts) => {
    const ctx = await browser.newContext(Object.assign({ viewport: { width: 1440, height: 1000 }, colorScheme: 'light' }, opts || {}));
    await ctx.addInitScript(HELPERS);
    const page = await ctx.newPage();
    page.errs = []; page.alertsStatus = [];
    page.on('pageerror', (e) => page.errs.push(first(e)));
    page.on('response', (rs) => { if (new URL(rs.url()).pathname === '/api/alerts') page.alertsStatus.push(rs.status()); });
    const { code } = await (await fetch(base(port) + '/__test/code')).json();
    await page.goto(base(port) + '/pair#code=' + code, { waitUntil: 'load' });   // auth.js trades the code for the token
    const landed = await page.evaluate(() => ({ hash: location.hash, path: location.pathname }));
    return { ctx, page, landed };
  };
  const floor = (page) => page.waitForSelector('.gx-node[data-id]', { timeout: 15000 });
  // Centre a node in the free area; returns where its middle is on screen.
  const nodeAt = async (page, id) => {
    const has = await page.evaluate((id) => { if (!__t.q(id)) return false; try { window.ccFactory.graph().center(id, { animate: false }); } catch (e) { /* no engine */ } return true; }, id);
    if (!has) throw new Error('no node ' + id);
    await page.waitForTimeout(250);
    return page.evaluate((id) => {
      const el = __t.q(id), r = el.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2, hit = document.elementFromPoint(x, y);
      return { x, y, covered: !(hit && hit.closest('.gx-node') === el) };
    }, id);
  };
  const step = async (name, fn) => { const r = {}; try { await fn(r); } catch (e) { r.error = first(e); } out[name] = r; };
  const shot = async (page, name) => { if (process.env.SHOTS) await page.screenshot({ path: process.env.SHOTS + '/' + name + '.png' }); };

  // 1. The floor over the real store: nodes, needs-you, the workflow, opening an agent ------------------------
  await step('floor', async (r) => {
    const { ctx, page, landed } = await open(PORT_A);
    try {
      r.landed = landed;
      await floor(page);
      await page.waitForTimeout(3500);
      // The fixture is time-relative, so an agent can age out between one API read and the poll that
      // follows it: compare the DOM with a fresh read until both agree (or ~10 s).
      Object.assign(r, await page.evaluate(async () => {
        const get = async (p) => (await window.ccAuth.fetch(p)).json();
        let live, graph, same = false;
        for (let i = 0; i < 4 && !same; i++) {
          if (i) await new Promise((k) => setTimeout(k, 3500));
          graph = await get('/api/graph'); live = await get('/api/live');
          same = __t.ids().slice().sort().join('|') === graph.nodes.map((n) => n.id).sort().join('|');
        }
        return { dom: __t.ids(), graphIds: graph.nodes.map((n) => n.id), same,
          liveIds: live.agents.map((a) => (a.key.includes('/') ? 'a:' : 's:') + a.key),
          feedFirst: graph.feed[0] ? graph.feed[0].key + '|' + graph.feed[0].id : null };
      }));
      Object.assign(r, await page.evaluate(([s1, s6]) => ({
        c1: __t.face('a:' + s1 + '/c1'), s6: __t.face('s:' + s6), hub: __t.face('p:elsewhere'), wf: __t.face('w:' + s1 + '/wf_1'),
        alerts: __t.alerts(), hud: __t.hud(), sub: __t.txt(document.querySelector('.fx-sub')),
        ticks: [...document.querySelectorAll('.fx-side .fx-evs .fx-ev-w')].map((b) => b.dataset.key),
      }), [S1, S6]));
      await shot(page, 'floor-needs-light');
      await page.emulateMedia({ colorScheme: 'dark' });
      await page.waitForTimeout(300);
      await shot(page, 'floor-needs-dark');
      await page.emulateMedia({ colorScheme: 'light' });
      // The workflow reads "2 of 3" in its inspector.
      const pw = await nodeAt(page, 'w:' + S1 + '/wf_1');
      await page.mouse.click(pw.x, pw.y);
      await page.waitForFunction(() => __t.insp().visible, null, { timeout: 4000 });
      r.wfInsp = await page.evaluate(() => __t.insp());
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => !__t.insp().visible, null, { timeout: 4000 });
      // A double-click opens the subagent's page; its crumb leads back to the floor.
      const c1id = 'a:' + S1 + '/c1';
      const pt = await nodeAt(page, c1id);
      await page.mouse.dblclick(pt.x, pt.y);
      await page.waitForFunction((s1) => location.hash === '#/s/' + s1 + '/a/c1', S1, { timeout: 4000 }).catch(() => {});
      r.dblHash = await page.evaluate(() => location.hash);
      await page.waitForFunction(() => { const c = document.querySelector('a.crumb'); return c && c.textContent.trim(); }, null, { timeout: 8000 });
      const crumb = () => page.evaluate(() => ({ text: document.querySelector('a.crumb').textContent.trim(), href: document.querySelector('a.crumb').getAttribute('href') }));
      r.agentCrumb = await crumb();               // an agent's page leads up to its session
      await page.click('a.crumb');
      await page.waitForFunction((s1) => location.hash === '#/s/' + s1, S1, { timeout: 4000 });
      await page.waitForFunction(() => { const c = document.querySelector('a.crumb'); return c && /Factory/.test(c.textContent); }, null, { timeout: 4000 }).catch(() => {});
      r.crumb = await crumb();                    // the session page, opened from the floor, leads back to it
      await page.click('a.crumb');
      await floor(page);
      r.backHash = await page.evaluate(() => location.hash);
      await page.waitForTimeout(800);
      // The alert names the subagent; its inspector's Open agent link goes to the same page.
      await page.click('.fx-alerts .fx-alert[data-node=' + JSON.stringify(c1id) + ']');
      await page.waitForFunction(() => __t.insp().visible, null, { timeout: 4000 });
      r.alertInsp = await page.evaluate(() => __t.insp());
      await page.click('aside.fx-inspector a.fx-link[data-key="ag"]');
      await page.waitForFunction((s1) => location.hash === '#/s/' + s1 + '/a/c1', S1, { timeout: 4000 }).catch(() => {});
      r.linkHash = await page.evaluate(() => location.hash);
      r.errors = page.errs;
    } finally { await ctx.close(); }
  });

  // 2. No hook alerts and /api/alerts answering 404: the floor still works, the transcript question still needs you ---
  await step('noalerts', async (r) => {
    const { ctx, page, landed } = await open(PORT_B);
    try {
      r.landed = landed;
      await floor(page);
      await page.waitForTimeout(4000);
      Object.assign(r, await page.evaluate(([s1, s6]) => ({
        c1: __t.face('a:' + s1 + '/c1'), s6: __t.face('s:' + s6), hub: __t.face('p:elsewhere'), alerts: __t.alerts(), n: __t.ids().length, hud: __t.hud(),
      }), [S1, S6]));
      r.alertsStatus = page.alertsStatus;
      r.errors = page.errs;
    } finally { await ctx.close(); }
  });

  // 3. Old links, two nav links, keys -------------------------------------------------------------------------
  await step('redirect', async (r) => {
    const { ctx, page } = await open(PORT_B);
    try {
      await floor(page);
      r.nav = await page.$$eval('.tabs a', (a) => a.map((x) => ({ view: x.dataset.view || null, text: x.textContent.trim(), href: x.getAttribute('href'), cur: x.getAttribute('aria-current') })));
      r.leftovers = await page.evaluate(() => ({ liveCount: document.querySelectorAll('#live-count').length,
        gone: document.querySelectorAll('.tabs a[data-view="live"], .tabs a[data-view="sessions"]').length }));
      const reqs = [];
      page.on('request', (rq) => { const u = new URL(rq.url()); if (u.pathname === '/api/graph') reqs.push(u.search); });
      await page.evaluate(() => { location.hash = '#/live?p=alpha'; });
      await page.waitForFunction(() => location.hash === '#/factory?p=alpha', null, { timeout: 5000 }).catch(() => {});
      r.hash1 = await page.evaluate(() => location.hash);
      await page.waitForFunction(() => { const ids = __t.ids(); return ids.includes('p:alpha') && !ids.some((i) => i.startsWith('p:') && i !== 'p:alpha'); }, null, { timeout: 9000 }).catch(() => {});
      r.scoped = await page.evaluate(() => __t.ids().filter((i) => i.startsWith('p:')));
      r.scopedReq = reqs.filter((s) => s.includes('project=alpha')).length > 0;
      // No extra history entry: back goes to where the floor was, not to a #/live that bounces forward again.
      await page.evaluate(() => history.back());
      await page.waitForTimeout(700);
      r.backHash = await page.evaluate(() => location.hash);
      // A bookmark: a cold load of #/live.
      await page.evaluate(() => { history.replaceState(null, '', '#/live'); });
      await page.reload({ waitUntil: 'load' });
      await page.waitForFunction(() => location.hash === '#/factory', null, { timeout: 5000 }).catch(() => {});
      r.cold = await page.evaluate(() => location.hash);
      await floor(page);
      // l and v are gone; f is still Factory.
      await page.keyboard.press('l');
      await page.waitForTimeout(300);
      r.afterL = await page.evaluate(() => location.hash);
      await page.keyboard.press('v');
      await page.waitForTimeout(300);
      r.afterV = await page.evaluate(() => location.hash);
      await page.evaluate(() => { location.hash = '#/overview'; });
      await page.waitForSelector('.area-tabs', { timeout: 8000 });
      await page.keyboard.press('f');
      await page.waitForFunction(() => location.hash.startsWith('#/factory'), null, { timeout: 4000 }).catch(() => {});
      r.afterF = await page.evaluate(() => location.hash);
      await page.keyboard.press('?');
      await page.waitForSelector('#help[open]', { timeout: 3000 });
      r.helpKeys = await page.$$eval('#help dt kbd', (k) => k.map((x) => x.textContent.trim()));
      await page.keyboard.press('Escape');
      r.errors = page.errs;
    } finally { await ctx.close(); }
  });

  // 4. No flicker: data frozen on the server, so routine polls must not add, remove or replace a node, nothing
  //    moves, no entrance animation runs, and the sync bar restarts once per applied poll ---------------------------
  await step('flicker', async (r) => {
    const { ctx, page } = await open(PORT_C, { viewport: { width: 1440, height: 900 } });
    try {
      let polls = 0;
      page.on('request', (rq) => { if (new URL(rq.url()).pathname === '/api/graph') polls += 1; });
      await floor(page);
      await page.waitForTimeout(4500);          // first render and its follow-up poll settle
      // Open a session's inspector, select its title, and park the pointer on another node.
      const pt = await nodeAt(page, 's:' + S2);
      await page.mouse.click(pt.x, pt.y);
      await page.waitForFunction(() => __t.insp().visible, null, { timeout: 4000 });
      await page.waitForFunction(() => window.ccFactory.graph().stats().settled, null, { timeout: 8000 }).catch(() => {});
      await page.waitForTimeout(1500);          // the floor re-frames around the inspector
      const hv = await page.evaluate(() => {
        const sel = document.querySelector('.fx-insp-title'), rg = document.createRange();
        rg.selectNodeContents(sel);
        const s = getSelection(); s.removeAllRanges(); s.addRange(rg);
        window.__selText = s.toString();
        for (const el of document.querySelectorAll('.gx-node[data-id]')) {
          const b = el.getBoundingClientRect(), x = b.left + b.width / 2, y = b.top + b.height / 2, hit = document.elementFromPoint(x, y);
          if (b.width > 0 && hit && hit.closest('.gx-node') === el) { window.__hovered = el; return { id: el.dataset.id, x, y }; }
        }
        return null;
      });
      if (!hv) throw new Error('no hoverable node');
      await page.mouse.move(hv.x, hv.y);
      await page.waitForTimeout(300);
      r.before = await page.evaluate(() => {
        const root = document.querySelector('#main'), g = window.ccFactory.graph();
        window.__muts = { childList: [], attributes: [], same: 0, characterData: 0 };
        window.__anims = [];
        // The header sync bar's late pulse (sync-wait) is a state animation, not poll churn: it runs only when a
        // poll answer is 700 ms past due, which a loaded machine can cause. Its fill is a Web Animation (no
        // animationstart, no DOM writes) and the header is outside #main, so nothing else of it is counted.
        document.addEventListener('animationstart', (e) => e.animationName !== 'sync-wait' && window.__anims.push(e.animationName + ' on ' + (e.target.className.baseVal != null ? e.target.className.baseVal : e.target.className)), true);
        const desc = (n) => n.nodeType === 1 ? n.tagName.toLowerCase() + '.' + String(n.className.baseVal != null ? n.className.baseVal : n.className).split(' ').join('.') : '#' + n.nodeType + ':' + (n.nodeValue || '').slice(0, 20);
        // An attribute written with the value it already had changes nothing on screen: count those apart (same)
        // and list only the writes that changed a value.
        const attrs = (list) => {
          const last = new Map();
          const fin = (m, now) => { if (m.oldValue === now) window.__muts.same += 1; else window.__muts.attributes.push(m.attributeName + ' on ' + desc(m.target) + ': ' + m.oldValue + ' -> ' + now); };
          for (const m of list) {
            let per = last.get(m.target);
            if (!per) last.set(m.target, per = new Map());
            const prev = per.get(m.attributeName);
            if (prev) fin(prev, m.oldValue);
            per.set(m.attributeName, m);
          }
          for (const [el, per] of last) for (const [name, m] of per) fin(m, el.getAttribute(name));
        };
        // A face's time in state (.fx-since, factory.js sinceTick) is a clock, like the ages: the tick that
        // takes it past its first minute shows it (hidden goes) and writes its first text. Counted as text ticks.
        const since = (m) => { const e = m.target.nodeType === 1 ? m.target : m.target.parentElement; return !!(e && e.closest('.fx-since')); };
        new MutationObserver((ms) => {
          attrs(ms.filter((m) => m.type === 'attributes' && !(since(m) && m.attributeName === 'hidden')));
          for (const m of ms) {
            if (since(m) && (m.type === 'childList' || m.attributeName === 'hidden')) window.__muts.characterData += 1;
            else if (m.type === 'childList') window.__muts.childList.push({ target: desc(m.target), added: [...m.addedNodes].map(desc), removed: [...m.removedNodes].map(desc) });
            else if (m.type === 'characterData') window.__muts.characterData += 1;
          }
        }).observe(root, { subtree: true, childList: true, attributes: true, attributeOldValue: true, characterData: true });
        window.__tag = new Map(); [...document.querySelectorAll('.gx-node[data-id]')].forEach((e) => window.__tag.set(e.dataset.id, e));
        window.__pos0 = JSON.parse(JSON.stringify(g.positions())); window.__cam0 = JSON.parse(JSON.stringify(g.camera()));
        // Sync bar: count fill restarts (one per applied poll), frame by frame.
        const fill = document.querySelector('#sync .sync-fill');
        window.__sync = { resets: 0, late: 0 };
        let prevAnim = null;
        const watch = () => {
          const a = fill.getAnimations()[0];
          if (a && a !== prevAnim) { if (prevAnim) window.__sync.resets += 1; prevAnim = a; }
          if (document.querySelector('#sync').classList.contains('late')) window.__sync.late += 1;
          requestAnimationFrame(watch);
        };
        requestAnimationFrame(watch);
        return { nodes: window.__tag.size, settled: g.stats().settled };
      });
      const p0 = polls;
      await page.waitForTimeout(12500);         // three or more /api/graph polls
      r.after = await page.evaluate(() => {
        const g = window.ccFactory.graph(), pos = g.positions(), cam = g.camera();
        let move = 0;
        for (const id in window.__pos0) if (pos[id]) move = Math.max(move, Math.hypot(pos[id].x - window.__pos0[id].x, pos[id].y - window.__pos0[id].y));
        const els = [...document.querySelectorAll('.gx-node[data-id]')];
        return { muts: window.__muts, anims: window.__anims, move, cam0: window.__cam0, cam,
          replaced: els.filter((e) => window.__tag.get(e.dataset.id) !== e).map((e) => e.dataset.id), count: els.length,
          sel: getSelection().toString() === window.__selText && window.__selText.length > 0,
          hoverKept: window.__hovered.isConnected && window.__hovered.matches(':hover'),
          sync: Object.assign({}, window.__sync, {
            barHidden: document.querySelector('#sync .sync-bar').getAttribute('aria-hidden'),
            role: document.querySelector('#sync').getAttribute('role'),
            liveText: document.querySelector('#sync-live').textContent,
            text: document.querySelector('#sync').textContent }) };
      });
      r.polls = polls - p0;
      r.hovered = hv.id;
      // The recent actions hold still under the pointer when a call arrives, and show it once the pointer
      // leaves. The inspector holds the right-hand slot: closing it brings the column back.
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => { const s = document.querySelector('.fx-side'); return s && !s.hidden && !s.classList.contains('leaving'); }, null, { timeout: 4000 });
      await page.hover('.fx-acts .fx-ev');
      const snap = () => page.evaluate(() => { const t = document.querySelector('.fx-evs .fx-ev-w'); return { first: t.dataset.key, top: t.getBoundingClientRect().top, n: document.querySelectorAll('.fx-evs .fx-ev-w').length }; });
      r.t0 = await snap();
      await page.evaluate(() => fetch('/__test/append'));
      await page.waitForTimeout(4000);
      r.t1 = await snap();
      await page.mouse.move(5, 650);
      await page.waitForTimeout(3500);
      r.t2 = await snap();
      r.errors = page.errs;
    } finally { await ctx.close(); }
  });

  await browser.close();
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def test_browser():
    if not (os.path.isdir(PLAYWRIGHT) and os.path.exists(CHROME) and shutil.which("node")):
        print("browser: skipped (no playwright-core, Chrome or node)")
        return
    st = Store(days=1, clock=time.time)  # the page computes timers from its own clock
    a = _stub_server(st)
    # B: no hook log (an empty private state dir) and an /api/alerts that answers 404.
    quiet = tempfile.mkdtemp(prefix="no-hooks-", dir=BASE)
    stb = Store(days=1, clock=time.time)
    stb._attention = Attention(stb, log_dir=quiet)
    b = _stub_server(stb, alerts_missing=True)
    frozen = [time.time()]
    c = _stub_server(Store(days=1, clock=lambda: frozen[0]))   # data stays put unless /__test/append runs
    js = os.path.join(BASE, "browser_check.js")
    with open(js, "w") as fh:
        fh.write(BROWSER_JS)
    env = dict(os.environ, PW=PLAYWRIGHT, CHROME=CHROME, PORT_A=str(a.port), PORT_B=str(b.port), PORT_C=str(c.port), SHOTS=SHOTS,
               S1=S1.sid, S2=S2.sid, S6=S6.sid)
    try:
        r = subprocess.run(["node", js], capture_output=True, text=True, timeout=300, env=env)
    finally:
        for srv in (a, b, c):
            srv.shutdown()
            srv.server_close()
    if r.returncode != 0:
        check("browser: page check ran", False, r.stderr[-800:])
        return
    out = json.loads(r.stdout.strip().splitlines()[-1])
    for name, step in out.items():
        if step.get("error"):
            check("browser: step %s ran to the end" % name, False, step["error"])
    if os.environ.get("CC_DASH_DEBUG"):
        print(json.dumps(out, indent=1)[:12000])
    fl, na, rd, fk = out["floor"], out["noalerts"], out["redirect"], out["flicker"]
    c1id, s6id = "a:%s/c1" % S1.sid, "s:" + S6.sid
    LANDED = {"hash": "#/factory", "path": "/"}

    # every agent has a node
    if "linkHash" in fl:
        check("floor: no page errors", not fl.get("errors") and not na.get("errors"), (fl.get("errors"), na.get("errors")))
        check("floor: pairing lands on #/factory (the landing view) with the code gone from the address", fl["landed"] == LANDED and na["landed"] == LANDED, (fl["landed"], na["landed"]))
        check("floor: a node for every agent and session on the board, and the page draws exactly the graph's nodes",
              len(fl["liveIds"]) >= 12 and set(fl["liveIds"]) <= set(fl["dom"]) and fl["same"] and len(fl["dom"]) == len(set(fl["dom"])),
              (len(fl["liveIds"]), sorted(set(fl["liveIds"]) - set(fl["dom"]))[:3], fl["same"]))
        c1 = fl["c1"] or {}
        check("floor: the alerted subagent's node needs you (halo class, name and 'Approve Bash?')",
              " needs" in (c1.get("cls") or "") + " " and "needs you: approve Bash" in (c1.get("aria") or "") and "Approve Bash?" in (c1.get("text") or ""), c1)
        al = {x["node"]: x for x in fl["alerts"]}
        check("floor: the alert strip names it: Permission, Bash, Write the tests",
              c1id in al and all(w in al[c1id]["text"] for w in ("Permission", "Bash", "Write the tests")), al.get(c1id))
        s6 = fl["s6"] or {}
        check("floor: a transcript AskUserQuestion needs you too (node and strip)",
              "needs you: answer a question" in (s6.get("aria") or "") and s6id in al and "Question" in al[s6id]["text"] and "Pick a name" in al[s6id]["text"], (s6, al.get(s6id)))
        hub = fl["hub"] or {}
        check("floor: an alert for a session with no transcript lands on its project's hub and sorts after the decisions",
              "p:elsewhere" in al and "Your turn" in al["p:elsewhere"]["text"] and hub and fl["alerts"][-1]["node"] == "p:elsewhere" and len(fl["alerts"]) == 3,
              (hub, [x["node"] for x in fl["alerts"]]))
        check("floor: the totals count the two agents that need a decision (a 'your turn' is not one)",
              fl["hud"].get("needs") == "2" and fl["sub"].startswith("2 need you"), (fl["hud"].get("needs"), fl["sub"]))
        wf = fl["wf"] or {}
        check("floor: the workflow's node shows the run and its progress",
              "review-sweep" in (wf.get("text") or "") and "2/3" in (wf.get("text") or ""), wf)
        wi = fl.get("wfInsp") or {}
        check("floor: its inspector reads '2 of 3 finished'", "2 of 3 finished" in (wi.get("body") or ""), wi)
        check("floor: the column's recent actions show the newest calls (at most 8), newest first",
              0 < len(fl["ticks"]) <= 8 and fl["ticks"][0] == fl["feedFirst"], (fl["ticks"], fl["feedFirst"]))
        check("floor: double-clicking a subagent opens its page", fl.get("dblHash") == "#/s/%s/a/c1" % S1.sid, fl.get("dblHash"))
        cr, ac = fl.get("crumb") or {}, fl.get("agentCrumb") or {}
        check("floor: the agent page's crumb leads up to its session", ac.get("href") == "#/s/" + S1.sid and ac.get("text", "").startswith("← "), ac)
        check("floor: that session page, opened from the floor, says '← Factory' and the crumb leads back",
              cr.get("text") == "← Factory" and cr.get("href") == "#/factory" and fl.get("backHash") == "#/factory", (cr, fl.get("backHash")))
        ai = fl.get("alertInsp") or {}
        links = {x["key"]: x for x in ai.get("links", [])}
        check("floor: the alert opens the subagent's inspector (name, kind, Open agent link)",
              ai.get("title") == "Write the tests" and "Agent" in (ai.get("head") or "") and links.get("ag", {}).get("href") == "#/s/%s/a/c1" % S1.sid
              and links["ag"]["text"] == "Open agent", ai)
        check("floor: the inspector's Open agent link lands on the agent's page", fl.get("linkHash") == "#/s/%s/a/c1" % S1.sid, fl.get("linkHash"))

    # no hook alerts, /api/alerts 404
    if "errors" in na:
        nc1 = na["c1"] or {}
        check("noalerts: /api/alerts answered 404 and the floor drew all its nodes anyway",
              404 in na["alertsStatus"] and 200 not in na["alertsStatus"] and na["n"] >= 12 and not na["errors"], (na["alertsStatus"], na["n"], na["errors"]))
        check("noalerts: with no hook alert the subagent does not need you, the strip does not name it",
              " needs" not in (nc1.get("cls") or "") + " " and not any(x["node"] == c1id for x in na["alerts"]), (nc1.get("cls"), na["alerts"]))
        check("noalerts: the transcript AskUserQuestion still needs you, and the hub for a session with no transcript is not made",
              "needs you: answer a question" in ((na["s6"] or {}).get("aria") or "") and any(x["node"] == s6id for x in na["alerts"])
              and na["hub"] is None and na["hud"].get("needs") == "1", (na["s6"], na["hub"], na["hud"]))

    # old links, nav, keys
    if "errors" in rd:
        check("nav: exactly two links, Factory then Projects, no Live count and no Live or Sessions tab",
              [(x["view"], x["text"]) for x in rd["nav"]] == [("factory", "Factory"), ("projects", "Projects")] and rd["leftovers"] == {"liveCount": 0, "gone": 0}, (rd["nav"], rd["leftovers"]))
        check("nav: the floor is the current tab", [x["view"] for x in rd["nav"] if x["cur"] == "page"] == ["factory"], rd["nav"])
        check("redirect: #/live?p=alpha is replaced by #/factory?p=alpha and the floor is scoped to alpha",
              rd["hash1"] == "#/factory?p=alpha" and rd["scoped"] == ["p:alpha"] and rd["scopedReq"], (rd["hash1"], rd["scoped"], rd["scopedReq"]))
        check("redirect: no extra history entry (back goes to the floor it left, not to a #/live that bounces forward)", rd["backHash"] == "#/factory", rd["backHash"])
        check("redirect: a bookmark of #/live opens the floor", rd["cold"] == "#/factory", rd["cold"])
        check("keys: l and v are gone, f is Factory", rd["afterL"] == "#/factory" and rd["afterV"] == "#/factory" and rd["afterF"].startswith("#/factory"), (rd["afterL"], rd["afterV"], rd["afterF"]))
        hk = rd.get("helpKeys", [])
        check("keys: the help lists f, o s p d and /, and no l or v", {"f", "o", "s", "p", "d", "/"} <= set(hk) and "l" not in hk and "v" not in hk, hk)
        check("redirect: no page errors", not rd.get("errors"), rd.get("errors"))

    # flicker
    if "errors" in fk:
        af = fk["after"]
        ch = af["muts"]["childList"]
        print(f"flicker: {fk['polls']} polls watched, {fk['before']['nodes']} nodes, childList={len(ch)}, "
              f"attributes changed={af['muts']['attributes'][:6]} (same-value rewrites {af['muts']['same']}), characterData={af['muts']['characterData']}, animations={af['anims']}, "
              f"max node move={af['move']:.2f}")
        check("flicker: watched 3 or more /api/graph polls", fk["polls"] >= 3, fk["polls"])
        check("flicker: no node added, removed or replaced across polls with unchanged data (floor, inspector, strips)",
              not ch and not af["replaced"] and af["count"] == fk["before"]["nodes"], (ch[:5], af["replaced"][:3], af["count"], fk["before"]["nodes"]))
        check("flicker: only text ticks changed (no attribute took a new value)", af["muts"]["characterData"] > 0 and not af["muts"]["attributes"], (af["muts"]["characterData"], af["muts"]["attributes"][:6]))
        check("flicker: no entrance or exit animation ran during routine polls", not af["anims"], af["anims"])
        check("flicker: nothing moved and the camera stayed", af["move"] < 0.5 and af["cam"] == af["cam0"], (af["move"], af["cam0"], af["cam"]))
        sy = af["sync"]
        print(f"sync bar: {sy['resets']} fill restarts over {fk['polls']} polls, late frames={sy['late']}")
        check("sync bar: the fill restarts once per applied poll", fk["polls"] - 1 <= sy["resets"] <= fk["polls"] + 1, (sy, fk["polls"]))
        check("sync bar: visual only (aria-hidden, no role on the indicator)", sy["barHidden"] == "true" and sy["role"] is None, sy)
        check("sync bar: routine polls announce nothing and show no 'Updated' text", sy["liveText"] == "" and sy["text"] == "", sy)
        check("flicker: the selected text in the inspector is kept", af["sel"])
        check("flicker: the hovered node is the same element and still hovered", af["hoverKept"], fk["hovered"])
        check("flicker: page errors none", not fk.get("errors"), fk.get("errors"))
        t0, t1, t2 = fk["t0"], fk["t1"], fk["t2"]
        check("recent actions: held still under the pointer when a call arrives", t1["first"] == t0["first"] and t1["top"] == t0["top"] and t1["n"] == t0["n"], (t0, t1))
        check("recent actions: leaving them shows the new call first", "c3_extra1" in t2["first"] and 0 < t2["n"] <= 8, t2)


def main():
    try:
        st = test_states()
        test_tree(st)
        test_spark_feed(st)
        test_linger_and_incremental()
        test_endpoint()
        test_floor_payload()
        test_browser()
        test_latency()
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
