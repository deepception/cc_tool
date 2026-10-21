#!/usr/bin/env python3
"""Live agents board checks for cc-dash (lib/cc_dash/store.py live payload).

Builds a fake ${CLAUDE_CONFIG_DIR}/projects tree in a temp dir with a fixed
clock, one session per situation the board must tell apart: a lead waiting on
a subagent that runs a tool, a nested subagent, a finished background agent
that lingers then drops, a workflow with done, running and failed agents, an
idle session, a crashed one (pending tool, no writes: "stalled?"), a stuck
model turn, an AskUserQuestion, an SDK run that ended on StructuredOutput, a
background agent still running under an old main thread, and a busy session
for the feed cap. Then it checks state derivation, tree linkage, sparkline
buckets, the feed, incremental re-reads after appends, the /api/live shape and
latency with many live transcripts, and (when headless Chrome and
playwright-core are present) that a stubbed GET /api/alerts payload marks the
matching agent "Needs you" in the browser. Nothing outside the temp dir is
touched.

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
from cc_dash import store as store_mod  # noqa: E402
from cc_dash.server import Handler, make_server  # noqa: E402
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
    check("spark: calls land in their minutes", c1["spark"] == [0] * 11 + [1, 1, 2, 0], c1["spark"])
    s9 = A[S9.sid]["spark"]
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
    try:
        code, body = _get(srv.port, "/api/live")
        d = json.loads(body)
        check("endpoint: /api/live 200", code == 200, code)
        check("endpoint: board keys present", all(k in d for k in ("sessions", "agents", "groups", "workflows", "feed", "counts", "limits")), list(d))
        need = {"key", "session", "agent_id", "parent", "kind", "depth", "run_id", "phase", "label", "type", "background", "project",
                "model", "family", "state", "since", "tool", "needs", "waiting_on", "stalled_for", "start", "last_write", "age",
                "tokens", "cost", "burn", "tool_calls", "errors", "spark", "spark_end", "last_tool", "last_text"}
        check("endpoint: every agent row has the full shape", all(need <= set(a) for a in d["agents"]), need - set(d["agents"][0]))
        check("endpoint: groups carry project and title", all({"session", "project", "title", "cost", "agents", "running", "needs"} <= set(g) for g in d["groups"]))
        check("endpoint: sessions list unchanged for the Sessions mode", isinstance(d["sessions"], list) and all("running_subs" in s for s in d["sessions"]))
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


# ── browser: stubbed /api/alerts marks the agent ─────────────────────────────
STUB_ALERTS = {"alerts": [
    {"id": "a1", "session": S1.sid, "agent_id": "c1", "project": "alpha", "cwd": "/w/alpha", "kind": "permission",
     "title": "Claude needs your permission to use Bash", "message": "pytest -q tests/", "tool": "Bash", "since": iso(T - 30), "source": "hook"},
    {"id": "a2", "session": "deadbeef-0000-0000-0000-00000000dead", "agent_id": None, "project": "elsewhere", "cwd": "/w/elsewhere", "kind": "idle",
     "title": "Claude is waiting for your input", "message": "", "tool": None, "since": iso(T - 90), "source": "hook"},
], "now": T}


class StubHandler(Handler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/__test/append"):
            # Test hook: one more tool call in the nested subagent's transcript.
            t = max(self.server.store.clock(), T) + 1   # newer than every synthetic record
            n = getattr(self.server, "appended", 0) + 1
            self.server.appended = n
            World.write(S1.sub_path("c3"), [S1.tool(t, f"c3_extra{n}", "Grep", {"pattern": f"extra{n}"}, agent="c3", model="claude-haiku-5-5")], append=True)
            self.server.store._last = 0
            return self._json(200, {"ok": n})
        if self.path.startswith("/api/alerts"):
            if not self._host_ok():
                return self._deny(421, "no")
            return self._json(200, self.server.alerts) if self.server.alerts is not None else self._deny(404, "Not found.")
        return super().do_GET()


def _stub_server(st, alerts):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
    srv.daemon_threads = True
    srv.store, srv.verbose, srv.alerts = st, False, alerts
    srv.port = srv.server_address[1]
    srv.allowed_hosts = {f"127.0.0.1:{srv.port}", f"localhost:{srv.port}"}
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


BROWSER_JS = r"""
const { chromium } = require(process.env.PW);
(async () => {
  const out = {};
  const browser = await chromium.launch({ executablePath: process.env.CHROME, headless: true });
  for (const [name, port] of [['alerts', process.env.PORT_A], ['noalerts', process.env.PORT_B]]) {
    const page = await (await browser.newContext({ viewport: { width: 1440, height: 1000 }, colorScheme: 'light' })).newPage();
    const errs = [];
    page.on('pageerror', (e) => errs.push(String(e)));
    await page.goto(`http://127.0.0.1:${port}/#/live`, { waitUntil: 'load' });
    await page.waitForSelector('.arow[data-key]', { timeout: 15000 });
    await page.waitForTimeout(name === 'alerts' ? 3000 : 1500);
    const r = await page.evaluate(async () => {
      // The fixture is time-relative, so an agent can age out between one API read and the
      // render that follows it: compare the DOM with a fresh read until both agree (or ~10 s).
      let api, rows;
      for (let i = 0; i < 3; i++) {
        await new Promise((ok) => setTimeout(ok, 3500));   // let the page render a poll
        api = await (await fetch('/api/live')).json();
        rows = [...document.querySelectorAll('.agroup:not(.loose) .arow[data-key]')];
        if (rows.length === api.agents.length) break;
      }
      const needs = rows.filter((x) => x.classList.contains('c-needs')).map((x) => ({ key: x.dataset.key, text: x.querySelector('.a-state').textContent }));
      const groups = [...document.querySelectorAll('.agroup')];
      return { rows: rows.length, apiAgents: api.agents.length, needs, firstGroup: groups[0] ? groups[0].querySelector('.agroup-h').textContent : '',
        secondGroupKeys: groups[1] ? [...groups[1].querySelectorAll('.arow[data-key]')].map((x) => x.dataset.key) : [],
        wf: [...document.querySelectorAll('.wfrow')].map((x) => x.textContent), feed: document.querySelectorAll('.fi').length,
        sub: document.querySelector('.page-head .sub').textContent, sparks: document.querySelectorAll('svg.spark').length };
    });
    if (name === 'alerts') {
      await page.hover('.feed');
      await page.waitForTimeout(300);
      r.pausedNote = await page.textContent('.feed-note');
      await page.evaluate(() => window.scrollTo(0, 0));
      if (process.env.SHOTS) await page.screenshot({ path: process.env.SHOTS + '/synthetic-needs-light.png', fullPage: true });
      await page.mouse.move(5, 5);
      await page.emulateMedia({ colorScheme: 'dark' });
      await page.waitForTimeout(300);
      if (process.env.SHOTS) await page.screenshot({ path: process.env.SHOTS + '/synthetic-needs-dark.png', fullPage: true });
      await page.click('.arow[data-key$="/c1"]');
      await page.waitForTimeout(800);
      r.clickHash = await page.evaluate(() => location.hash);
    }
    r.errors = errs;
    out[name] = r;
  }

  // No-flicker check: data frozen on the server, so routine polls must not
  // add, remove or replace a single node, and no entrance animation may run.
  {
    const page = await (await browser.newContext({ viewport: { width: 1440, height: 700 }, colorScheme: 'light' })).newPage();
    const errs = [];
    page.on('pageerror', (e) => errs.push(String(e)));
    let polls = 0;
    page.on('request', (rq) => { if (rq.url().includes('/api/live')) polls += 1; });
    await page.goto(`http://127.0.0.1:${process.env.PORT_C}/#/live`, { waitUntil: 'load' });
    await page.waitForSelector('.arow[data-key]', { timeout: 15000 });
    await page.waitForTimeout(4000);          // first render and its one follow-up poll settle
    await page.evaluate(() => window.scrollTo(0, 260));
    await page.evaluate(() => {               // select a label's text
      const el = document.querySelector('.arow[data-key] .a-label');
      const rg = document.createRange(); rg.selectNodeContents(el);
      const sel = getSelection(); sel.removeAllRanges(); sel.addRange(rg);
      window.__selText = sel.toString();
    });
    await page.hover('.arow[data-key]:nth-child(2)');
    const before = await page.evaluate(() => {
      const root = document.querySelector('#main');
      window.__muts = { childList: [], attributes: {}, characterData: 0 };
      window.__anims = [];
      document.addEventListener('animationstart', (e) => window.__anims.push(e.animationName + ' on ' + (e.target.className.baseVal != null ? e.target.className.baseVal : e.target.className)), true);
      const desc = (n) => n.nodeType === 1 ? n.tagName.toLowerCase() + '.' + String(n.className.baseVal != null ? n.className.baseVal : n.className).split(' ').join('.') : '#' + n.nodeType + ':' + (n.nodeValue || '').slice(0, 20);
      new MutationObserver((ms) => { for (const m of ms) {
        if (m.type === 'childList') window.__muts.childList.push({ target: desc(m.target), added: [...m.addedNodes].map(desc), removed: [...m.removedNodes].map(desc) });
        else if (m.type === 'attributes') window.__muts.attributes[m.attributeName] = (window.__muts.attributes[m.attributeName] || 0) + 1;
        else window.__muts.characterData += 1;
      } }).observe(root, { subtree: true, childList: true, attributes: true, characterData: true });
      window.__hovered = document.querySelector('.arow[data-key]:nth-child(2)');
      return { scrollY: window.scrollY, rows: document.querySelectorAll('.arow[data-key]').length };
    });
    const p0 = polls;
    await page.waitForTimeout(12500);         // three or more /api/live polls
    const after = await page.evaluate(() => ({ muts: window.__muts, anims: window.__anims, scrollY: window.scrollY,
      sel: getSelection().toString() === window.__selText && window.__selText.length > 0,
      hoverKept: window.__hovered.isConnected && window.__hovered.matches(':hover'),
      rows: document.querySelectorAll('.arow[data-key]').length }));
    after.polls = polls - p0;
    after.before = before;
    // Feed while hovered: a new call arrives, the list must not move.
    await page.hover('.feed-list');
    const f0 = await page.evaluate(() => ({ first: document.querySelector('.fi').dataset.key, top: document.querySelector('.fi').getBoundingClientRect().top }));
    await page.evaluate(() => fetch('/__test/append'));
    await page.waitForTimeout(4000);
    const f1 = await page.evaluate(() => ({ first: document.querySelector('.fi').dataset.key, top: document.querySelector('.fi').getBoundingClientRect().top, note: document.querySelector('.feed-note').textContent }));
    await page.mouse.move(5, 650);
    await page.waitForTimeout(3500);
    const f2 = await page.evaluate(() => ({ first: document.querySelector('.fi').dataset.key }));
    after.feed = { f0, f1, f2 };
    // Same watch on the Sessions mode cards.
    await page.keyboard.press('v');
    await page.waitForSelector('.card[data-key]', { timeout: 10000 });
    await page.waitForTimeout(4000);
    const p1 = polls;
    await page.evaluate(() => { window.__muts = { childList: [], attributes: {}, characterData: 0 }; window.__anims = []; });
    await page.waitForTimeout(12500);
    after.cards = await page.evaluate(() => ({ muts: window.__muts, anims: window.__anims, n: document.querySelectorAll('.card[data-key]').length }));
    after.cards.polls = polls - p1;
    after.errors = errs;
    out.flicker = after;
  }
  await browser.close();
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def test_browser():
    if not (os.path.isdir(PLAYWRIGHT) and os.path.exists(CHROME) and shutil.which("node")):
        print("browser: skipped (no playwright-core, Chrome or node)")
        return
    st = Store(days=1, clock=time.time)  # the page computes timers from its own clock
    a = _stub_server(st, STUB_ALERTS)
    b = _stub_server(st, None)
    frozen = [time.time()]
    c = _stub_server(Store(days=1, clock=lambda: frozen[0]), STUB_ALERTS)   # data stays put unless /__test/append runs
    js = os.path.join(BASE, "browser_check.js")
    with open(js, "w") as fh:
        fh.write(BROWSER_JS)
    env = dict(os.environ, PW=PLAYWRIGHT, CHROME=CHROME, PORT_A=str(a.port), PORT_B=str(b.port), PORT_C=str(c.port), SHOTS=SHOTS)
    try:
        r = subprocess.run(["node", js], capture_output=True, text=True, timeout=180, env=env)
    finally:
        for srv in (a, b, c):
            srv.shutdown()
            srv.server_close()
    if r.returncode != 0:
        check("browser: page check ran", False, r.stderr[-800:])
        return
    out = json.loads(r.stdout.strip().splitlines()[-1])
    al, na = out["alerts"], out["noalerts"]
    check("browser: no page errors", not al["errors"] and not na["errors"], al["errors"] + na["errors"])
    check("browser: one row per agent on the board", al["rows"] == al["apiAgents"] >= 12, (al["rows"], al["apiAgents"]))
    c1 = [n for n in al["needs"] if n["key"] == S1.sid + "/c1"]
    check("browser: alert marks the matching subagent 'Needs you: approve Bash'", c1 and "Needs you: approve Bash" in c1[0]["text"], al["needs"])
    check("browser: transcript AskUserQuestion also shows as needs you", any(n["key"] == S6.sid for n in al["needs"]), al["needs"])
    check("browser: an alert for a session not on the board gets its own group first", al["firstGroup"].startswith("Needs you"), al["firstGroup"])
    check("browser: the session with the alerted agent sorts right after", al["secondGroupKeys"][:1] in ([S1.sid], [S6.sid]), al["secondGroupKeys"][:2])
    check("browser: summary counts the agents that need you", "3 need you" in al["sub"], al["sub"])
    check("browser: workflow header row rendered", any("review-sweep" in w and "2 of 3 finished" in w for w in al["wf"]), al["wf"])
    check("browser: feed shows at most 50 entries", 0 < al["feed"] <= 50, al["feed"])
    check("browser: a sparkline per agent", al["sparks"] == al["rows"], (al["sparks"], al["rows"]))
    check("browser: feed pauses on hover", (al.get("pausedNote") or "").startswith("Paused"), al.get("pausedNote"))
    check("browser: clicking a subagent opens its timeline", al.get("clickHash") == f"#/s/{S1.sid}/a/c1", al.get("clickHash"))
    fl = out["flicker"]
    print(f"flicker: {fl['polls']} polls watched, childList={len(fl['muts']['childList'])}, "
          f"attributes={fl['muts']['attributes']}, characterData={fl['muts']['characterData']}, animations={fl['anims']}")
    check("flicker: watched 3 or more /api/live polls", fl["polls"] >= 3, fl["polls"])
    check("flicker: no node added, removed or replaced across polls with unchanged data", not fl["muts"]["childList"], fl["muts"]["childList"][:5])
    check("flicker: only text ticks (characterData) changed", fl["muts"]["characterData"] > 0 and not fl["muts"]["attributes"], fl["muts"])
    check("flicker: no entrance or exit animation ran during routine polls", not fl["anims"], fl["anims"])
    check("flicker: scroll position kept", fl["scrollY"] == fl["before"]["scrollY"] and fl["scrollY"] > 0, (fl["before"]["scrollY"], fl["scrollY"]))
    check("flicker: text selection kept", fl["sel"])
    check("flicker: hovered row is the same node and still hovered", fl["hoverKept"])
    check("flicker: page errors none", not fl["errors"], fl["errors"])
    f = fl["feed"]
    check("flicker: hovered feed does not move when a call arrives", f["f1"]["first"] == f["f0"]["first"] and f["f1"]["top"] == f["f0"]["top"], f)
    check("flicker: hovered feed says a call is waiting", f["f1"]["note"].startswith("Paused, 1 new call"), f["f1"]["note"])
    check("flicker: leaving the feed shows the new call on top", "c3_extra1" in f["f2"]["first"], f["f2"])
    cd = fl["cards"]
    print(f"flicker (sessions mode): {cd['polls']} polls, {cd['n']} cards, childList={len(cd['muts']['childList'])}, "
          f"attributes={cd['muts']['attributes']}, characterData={cd['muts']['characterData']}, animations={cd['anims']}")
    check("flicker: sessions-mode cards are patched in place too", cd["polls"] >= 3 and cd["n"] > 0 and not cd["muts"]["childList"]
          and not cd["muts"]["attributes"] and not cd["anims"], cd)
    check("browser: 404 from /api/alerts shows no needs-you from alerts", not any(n["key"] == S1.sid + "/c1" for n in na["needs"]) and
          any(n["key"] == S6.sid for n in na["needs"]), na["needs"])


def main():
    try:
        st = test_states()
        test_tree(st)
        test_spark_feed(st)
        test_linger_and_incremental()
        test_endpoint()
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
