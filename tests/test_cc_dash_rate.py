#!/usr/bin/env python3
"""Tool-call rate checks for cc-dash's Live agent rows (store.py _spark, app.js sparkline).

Each agent row on the Live board carries the last 15 one-minute buckets of
tool calls as {n, err, tools} (calls started in the minute, how many failed,
the top 3 tools by count), plus `rate` (calls started in the trailing 60
seconds) and `peak` (the busiest bucket). This builds synthetic transcripts
under a temp ${CLAUDE_CONFIG_DIR} with a fixed clock and checks bucket counts,
failures (errors and guard blocks), top tools and their tie order, the rate
window edges, the peak, the still-filling current minute, idle and silent
rows, the payload-size bound on a busy board, and incremental updates after
appending tool calls (only the new bytes are read). It also checks the burn
rule beside the rate: $/min from cost_min, and new tokens per minute (never
cache reads) only for an unpriced model, server side and in app.js rateText.

With headless Chrome and playwright-core present it opens the board on a busy
fixture: the rate figures, the lighter running minute, the red failed
segment, the hover and arrow-key tooltip (time range, calls, failed, top
tools), and that three polls with unchanged data add, remove or rewrite
nothing. Set CC_DASH_SHOTS=<dir> to save light and dark shots at 1440 and
390 px plus a tooltip shot. Nothing outside the temp dir is touched.

Run:  python3 tests/test_cc_dash_rate.py
"""
import json
import os
import re
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
from cc_dash.auth import Auth  # noqa: E402
from cc_dash.server import Handler  # noqa: E402
from cc_dash.store import Store  # noqa: E402
from dash_pairing import auto_token  # noqa: E402

RESULTS = []
PLAYWRIGHT = os.environ.get("CC_DASH_PLAYWRIGHT", "/home/lukasz/Files/hobby/color-analyzer/webapp/node_modules/playwright-core")
CHROME = os.environ.get("CC_DASH_CHROME", "/usr/bin/google-chrome")
SHOTS = os.environ.get("CC_DASH_SHOTS", "")


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


BASE = tempfile.mkdtemp(prefix="cc-dash-rate-test-")
CFG = os.path.join(BASE, "claude")
T = (int(time.time()) // 60) * 60 + 30            # "now": 30 s into a wall-clock minute
NOW = [float(T)]
M0 = T - 30                                        # start of the current minute
END = M0 + 60                                      # spark_end: the end of the current minute


class World:
    """One session's transcripts; each file's mtime is its last record's time."""

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

    def tool(self, t, tid, name, inp=None, agent=None, model="claude-opus-5-5", usage=None):
        return self.rec("assistant", t, agent, message={
            "id": f"m-{tid}", "model": model, "role": "assistant", "stop_reason": "tool_use",
            "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp or {"command": "x"}}],
            "usage": usage or {"input_tokens": 10, "output_tokens": 100, "cache_read_input_tokens": 1000}})

    def result(self, t, tid, text="ok", agent=None, err=False, tur=None):
        r = self.rec("user", t, agent, message={"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": err}]})
        if tur is not None:
            r["toolUseResult"] = tur
        return r

    def text(self, t, mid, text, agent=None, model="claude-opus-5-5"):
        return self.rec("assistant", t, agent, message={"id": mid, "model": model, "role": "assistant", "stop_reason": "end_turn",
                                                        "content": [{"type": "text", "text": text}],
                                                        "usage": {"input_tokens": 10, "output_tokens": 50}})

    def calls(self, t, tid, name, agent=None, err=False, blocked=False, model="claude-opus-5-5", usage=None):
        """A tool call and its result one second later."""
        text = "BLOCKED: rule 3" if blocked else ("boom" if err else "ok")
        return [self.tool(t, tid, name, agent=agent, model=model, usage=usage),
                self.result(t + 1, tid, text, agent=agent, err=err or blocked)]

    @staticmethod
    def write(path, records, append=False):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a" if append else "w") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")
        last = max(datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")).timestamp() for r in records)
        os.utime(path, (last, last))

    def write_main(self, records, append=False):
        self.write(self.main, records, append)

    def write_sub(self, aid, records, meta):
        self.write(os.path.join(self.subdir, f"agent-{aid}.jsonl"), records)
        with open(os.path.join(self.subdir, f"agent-{aid}.meta.json"), "w") as fh:
            json.dump(meta, fh)


# ── R1: one main thread with a known minute-by-minute history ───────────────
# Minute index i covers [END - (15 - i) * 60, END - (14 - i) * 60); 14 is the current minute.
def mstart(i):
    return END - (15 - i) * 60


R1 = World("rate", "22222222-0000-0000-0000-000000000001")
recs = [R1.prompt(mstart(0) - 120, "Do the work")]
recs += R1.calls(mstart(0) - 90, "old1", "Read")                     # before the window: not counted
recs += R1.calls(mstart(3) + 5, "a1", "Read")
recs += R1.calls(mstart(3) + 20, "a2", "Read")
# Minute 9: Bash x4 (one failed, one blocked by a guard), Read x2, Edit x1, Grep x1 -> top 3 Bash, Read, Edit (Edit < Grep by name)
for k, (name, err, blk) in enumerate([("Bash", False, False), ("Bash", True, False), ("Read", False, False), ("Bash", False, True),
                                      ("Grep", False, False), ("Edit", False, False), ("Read", False, False), ("Bash", False, False)]):
    recs += R1.calls(mstart(9) + 2 + k * 6, f"b{k}", name, err=err, blocked=blk)
# Minute 13 (the last full one): 3 calls, one at 10 s (outside the 60 s rate window), two inside it
recs += R1.calls(mstart(13) + 10, "c0", "Read")     # T - 80
recs += R1.calls(mstart(13) + 31, "c1", "Read")     # T - 59: inside
recs += R1.calls(mstart(13) + 45, "c2", "Edit")     # T - 45: inside
# Minute 14 (current, 30 s in): 2 calls so far
recs += R1.calls(M0 + 5, "d0", "Bash")
recs.append(R1.tool(M0 + 20, "d1", "Bash"))         # still running: counts as a call
R1.write_main(recs)

# ── R2: idle (turn ended), last calls six minutes ago ─────────────────────
R2 = World("idle", "22222222-0000-0000-0000-000000000002")
r2 = [R2.prompt(T - 500, "Look around")]
for k in range(5):
    r2 += R2.calls(T - 410 + k * 3, f"i{k}", "Glob")
r2.append(R2.text(T - 360, "i-end", "Done looking."))
R2.write_main(r2)

# ── R3: a session that has made no tool calls at all ─────────────────────
R3 = World("silent", "22222222-0000-0000-0000-000000000003")
R3.write_main([R3.prompt(T - 40, "Hello")])

# ── R4: burn rule: a priced and an unpriced subagent with big cache reads ──
R4 = World("burn", "22222222-0000-0000-0000-000000000004")
BIG = {"input_tokens": 10, "output_tokens": 100, "cache_read_input_tokens": 100000, "cache_creation_input_tokens": 500}
R4.write_main([R4.prompt(T - 200, "go"),
               R4.tool(T - 190, "sp_p", "Agent", {"description": "priced", "prompt": "p"}),
               R4.tool(T - 189, "sp_u", "Agent", {"description": "unpriced", "prompt": "p"})])
r4p = [R4.prompt(T - 188, "priced", agent="bp")]
r4u = [R4.prompt(T - 188, "unpriced", agent="bu")]
for k in range(6):
    r4p += R4.calls(T - 180 + k * 20, f"bp{k}", "Read", agent="bp", usage=dict(BIG))
    r4u += R4.calls(T - 180 + k * 20, f"bu{k}", "Read", agent="bu", model="acme-unknown-model-1", usage=dict(BIG))
r4p.append(R4.tool(T - 5, "bp_run", "Bash", agent="bp", usage=dict(BIG)))
r4u.append(R4.tool(T - 5, "bu_run", "Bash", agent="bu", model="acme-unknown-model-1", usage=dict(BIG)))
R4.write_sub("bp", r4p, {"agentType": "general-purpose", "description": "priced", "toolUseId": "sp_p"})
R4.write_sub("bu", r4u, {"agentType": "general-purpose", "description": "unpriced", "toolUseId": "sp_u"})

os.environ["CLAUDE_CONFIG_DIR"] = CFG


def board(st):
    p = st.live_payload()
    return p, {a["key"]: a for a in p["agents"]}


def test_buckets(st):
    p, A = board(st)
    a = A[R1.sid]
    sp = a["spark"]
    check("buckets: 15 one-minute buckets, spark_end is the end of the current minute", len(sp) == 15 and a["spark_end"] == END, (len(sp), a["spark_end"] - T))
    check("buckets: each bucket is {n, err, tools}", all(set(b) == {"n", "err", "tools"} for b in sp), sp[0])
    want = [0] * 15
    want[3], want[9], want[13], want[14] = 2, 8, 3, 2
    check("buckets: calls land in their minutes; a call before the window is not counted", [b["n"] for b in sp] == want, [b["n"] for b in sp])
    check("errors: an error result and a guard block both count as failed", sp[9]["err"] == 2 and sum(b["err"] for b in sp) == 2, [b["err"] for b in sp])
    check("tools: top 3 by count, ties by name (Edit before Grep)", list(sp[9]["tools"].items()) == [("Bash", 4), ("Read", 2), ("Edit", 1)], sp[9]["tools"])
    check("tools: an empty minute names no tools", sp[0] == {"n": 0, "err": 0, "tools": {}}, sp[0])
    check("tools: a minute with one tool names it", sp[13]["tools"] == {"Read": 2, "Edit": 1} and sp[3]["tools"] == {"Read": 2}, (sp[13], sp[3]))
    check("partial minute: the current minute holds the calls made so far, the running one included",
          sp[14] == {"n": 2, "err": 0, "tools": {"Bash": 2}}, sp[14])
    check("rate: calls started in the trailing 60 s (T-59, T-45, T-25, T-10), not T-80", a["rate"] == 4, a["rate"])
    check("peak: the busiest bucket", a["peak"] == 8, a["peak"])
    check("limits: spark_top and rate_secs are published", p["limits"]["spark_top"] == 3 and p["limits"]["rate_secs"] == 60, p["limits"])
    i = A[R2.sid]
    check("idle row: state idle, rate 0, its earlier calls still in the history", i["state"] == "idle" and i["rate"] == 0 and i["peak"] == 5
          and sum(b["n"] for b in i["spark"]) == 5, (i["state"], i["rate"], i["peak"]))
    z = A[R3.sid]
    check("silent row: all buckets empty, rate 0, peak 0", all(b == {"n": 0, "err": 0, "tools": {}} for b in z["spark"]) and z["rate"] == 0 and z["peak"] == 0, z["spark"][-1])


def test_rate_edges():
    """The rate window is [now - 60, now]: move the clock and watch calls leave it."""
    st = Store(days=1, clock=lambda: NOW[0])
    try:
        NOW[0] = T + 15      # T-45 is now 60 s old (kept), T-59 is 74 s old (gone)
        _, A = board(st)
        check("rate edge: a call exactly 60 s old still counts", A[R1.sid]["rate"] == 3, A[R1.sid]["rate"])
        NOW[0] = T + 40      # the current minute rolls over at T+30: buckets shift by one
        _, A = board(st)
        sp = A[R1.sid]["spark"]
        check("rollover: the minute that was running becomes a full one, a fresh empty minute starts",
              sp[13]["n"] == 2 and sp[14]["n"] == 0 and A[R1.sid]["spark_end"] == END + 60, [b["n"] for b in sp])
        check("rollover: the rate keeps counting the last 60 s across the boundary (T-10 seen from T+40; T-25 has left)", A[R1.sid]["rate"] == 1, A[R1.sid]["rate"])
    finally:
        NOW[0] = float(T)


def test_incremental():
    st = Store(days=1, clock=lambda: NOW[0])
    board(st)
    b0 = st.cache.bytes_read
    size0 = os.path.getsize(R1.main)
    NOW[0] = T + 10
    extra = R1.calls(T + 2, "e0", "Write", err=True) + R1.calls(T + 5, "e1", "Write")
    # the running Bash call (d1) finishes too
    extra.insert(0, R1.result(T + 1, "d1", "done"))
    World.write(R1.main, extra, append=True)
    st.refresh(force=True)
    _, A = board(st)
    added = os.path.getsize(R1.main) - size0
    a = A[R1.sid]
    check("incremental: appended tool calls re-read only the new bytes", st.cache.bytes_read - b0 == added, (st.cache.bytes_read - b0, added))
    check("incremental: the current minute gains the new calls and the failure", a["spark"][14]["n"] == 4 and a["spark"][14]["err"] == 1
          and a["spark"][14]["tools"] == {"Bash": 2, "Write": 2}, a["spark"][14])
    check("incremental: the rate follows (T-45 ... T+5 seen from T+10)", a["rate"] == 5, a["rate"])
    NOW[0] = float(T)


def test_size():
    """A busy board: 60 agents, each calling 8 tools with long MCP names every few seconds for 15 minutes."""
    d = tempfile.mkdtemp(prefix="busy-", dir=BASE)
    cfg = os.path.join(d, "claude")
    names = [f"mcp__claude_ai_Some_Very_Long_Server_Name__tool_number_{k}_with_a_long_suffix" for k in range(8)]
    for s in range(15):
        w = World(f"busy{s}", f"33333333-0000-0000-0000-{s:012d}")
        w.cwd = os.path.join(d, "work", f"busy{s}")
        w.pdir = os.path.join(cfg, "projects", w.cwd.replace("/", "-"))
        w.main, w.subdir = os.path.join(w.pdir, w.sid + ".jsonl"), os.path.join(w.pdir, w.sid, "subagents")
        main = [w.prompt(T - 1000, "go")]
        for j in range(3):
            main += [w.tool(T - 990 + j, f"sp{s}_{j}", "Agent", {"description": f"part {j}", "run_in_background": True, "prompt": "p"}),
                     w.result(T - 989 + j, f"sp{s}_{j}", "launched", tur={"status": "async_launched", "agentId": f"x{j}"})]
        main.append(w.tool(T - 2, f"m{s}_run", "Bash"))
        w.write_main(main)
        for j in range(3):
            r = [w.prompt(T - 985, "part", agent=f"x{j}")]
            for k in range(300):                       # every 3 s: 20 calls a minute
                r += w.calls(T - 960 + k * 3, f"x{s}{j}_{k}", names[k % 8], agent=f"x{j}", err=(k % 7 == 0))
            r.append(w.tool(T - 1, f"x{s}{j}_run", names[0], agent=f"x{j}"))
            w.write_sub(f"x{j}", r, {"agentType": "general-purpose", "description": f"part {j}", "toolUseId": f"sp{s}_{j}"})
    st = Store(root=cfg, days=1, clock=lambda: NOW[0])
    p = st.live_payload()
    busy = [a for a in p["agents"] if a["kind"] == "sub"]
    spark_bytes = [len(json.dumps(a["spark"], separators=(",", ":"))) for a in p["agents"]]
    bare = [len(json.dumps([b["n"] for b in a["spark"]], separators=(",", ":"))) for a in p["agents"]]
    total = len(json.dumps(p, separators=(",", ":"), default=str))
    worst = max(spark_bytes)
    print(f"size: {len(p['agents'])} agents, live payload {total / 1024:.1f} KB; spark per agent: max {worst} B, "
          f"mean {sum(spark_bytes) / len(spark_bytes):.0f} B (bare counts: mean {sum(bare) / len(bare):.0f} B); "
          f"board total for sparks {sum(spark_bytes) / 1024:.1f} KB vs {sum(bare) / 1024:.1f} KB before")
    check("size: the busy fixture is busy (20 calls a minute per subagent)", len(busy) == 45 and all(a["peak"] == 20 for a in busy), [a["peak"] for a in busy][:3])
    check("size: never more than 3 tools named per bucket", all(len(b["tools"]) <= 3 for a in p["agents"] for b in a["spark"]))
    check("size: tool names are clipped to 40 characters", all(len(k) <= store_mod.SPARK_NAME == 40 for a in p["agents"] for b in a["spark"] for k in b["tools"]))
    check("size: a bucket's named counts never exceed its total", all(sum(b["tools"].values()) <= b["n"] for a in p["agents"] for b in a["spark"]))
    # bound: 15 x {"n":999,"err":999,"tools":{3 x "<40 chars>":999}} ~ 15 x 185 B
    check("size: one agent's spark stays under 3 KB however busy", worst <= 3000, worst)
    st2 = Store(root=cfg, days=1, clock=lambda: NOW[0])
    st2.refresh(force=True)
    times = []
    for _ in range(5):
        st2._last = 0
        t0 = time.perf_counter()
        st2.live_payload()
        times.append((time.perf_counter() - t0) * 1000)
    med = sorted(times)[2]
    print(f"size: warm live_payload median {med:.1f} ms with {len(p['agents'])} busy agents")
    check("cost: the busy board still builds well inside a poll", med < 400, f"{med:.1f} ms")


def test_burn(st):
    _, A = board(st)
    bp, bu = A[R4.sid + "/bp"]["burn"], A[R4.sid + "/bu"]["burn"]
    # 7 messages, each 10 in + 100 out + 500 cache write (+100000 cache read), over the
    # 188 s since the agent's first record (the window is min(5 minutes, its age))
    new_min = round(7 * 610 * 60 / 188)
    check("burn: new_min counts input, cache writes and output, never cache reads", bp["new_min"] == new_min == bu["new_min"], (bp, new_min))
    check("burn: tok_min (with cache reads) is far larger and kept only for the breakdown", bp["tok_min"] > 100 * bp["new_min"], bp)
    check("burn: a priced model has a cost per minute and no unpriced part", bp["cost_min"] > 0 and bp["unpriced_min"] == 0, bp)
    check("burn: an unpriced model has no cost and all its new tokens are unpriced", bu["cost_min"] == 0 and bu["unpriced_min"] == bu["new_min"] > 0, bu)
    # app.js rateText: run the real functions in node
    if not shutil.which("node"):
        print("burn (js): skipped (no node)")
        return
    src = open(os.path.join(REPO, "lib", "cc_dash", "web", "app.js")).read()

    def fn(name):
        m = re.search(r"\n  function " + name + r"\(", src)
        i, depth = src.index("{", m.end()), 0
        for j in range(i, len(src)):
            depth += {"{": 1, "}": -1}.get(src[j], 0)
            if depth == 0:
                return src[m.start():j + 1]
        raise ValueError(name)

    js = "\n".join(fn(n) for n in ("money", "compact", "rateText", "burnTitle")) + """
const cases = JSON.parse(process.argv[2]);
console.log(JSON.stringify(cases.map((b) => [rateText(b), burnTitle(b)])));
"""
    path = os.path.join(BASE, "burn.js")
    with open(path, "w") as fh:
        fh.write(js)
    cases = [bp, bu, {"tok_min": 0, "new_min": 0, "out_min": 0, "unpriced_min": 0, "cost_min": 0},
             {"tok_min": 1280000, "new_min": 9000, "out_min": 2000, "unpriced_min": 0, "cost_min": 0.4321}]
    r = subprocess.run(["node", path, json.dumps(cases)], capture_output=True, text=True, timeout=30)
    out = json.loads(r.stdout) if r.returncode == 0 else None
    check("burn (js): rateText ran", out is not None, r.stderr[-400:])
    if out:
        check("burn (js): a priced row shows $/min", out[0][0].endswith("/min") and out[0][0].startswith(("$", "<$")), out[0][0])
        check("burn (js): an unpriced row falls back to 'new tok/min' from new_min", out[1][0] == f"{bu['new_min'] / 1000:.1f}K new tok/min".replace(".0K", "K"), out[1][0])
        check("burn (js): no activity shows nothing", out[2][0] == "", out[2][0])
        check("burn (js): a context-heavy row shows dollars, not the 1.28M cache-read figure", out[3][0] == "$0.43/min" and "1.3M" not in out[3][0], out[3][0])
        check("burn (js): the tooltip breaks it down (new tokens, then cache reads on top)", "New tokens: 9K" in out[3][1] and "Cache reads come on top: 1.3M" in out[3][1], out[3][1])


# ── browser ─────────────────────────────────────────────────────────────────
def busy_fixture(root, t):
    """A small board that reads well: a lead with three subagents at different paces, failures, an idle session."""
    def make(name, sid):
        w = World(name, sid)
        w.cwd = os.path.join(root, "work", name)
        os.makedirs(w.cwd, exist_ok=True)
        w.pdir = os.path.join(root, "claude", "projects", w.cwd.replace("/", "-"))
        w.main, w.subdir = os.path.join(w.pdir, w.sid + ".jsonl"), os.path.join(w.pdir, w.sid, "subagents")
        return w
    w = make("shop-api", "44444444-0000-0000-0000-000000000001")
    main = [w.prompt(t - 880, "Ship the checkout refactor")]
    for j, d in enumerate(["Port the payment client", "Write the integration tests", "Review the migration"]):
        main += [w.tool(t - 870 + j, f"sp{j}", "Agent", {"description": d, "run_in_background": True, "prompt": "p"}),
                 w.result(t - 869 + j, f"sp{j}", "launched", tur={"status": "async_launched", "agentId": f"x{j}"})]
    for k in range(30):
        main += w.calls(t - 860 + k * 9, f"lead{k}", ["Read", "Grep", "Bash"][k % 3])
    main.append(w.tool(t - 40, "lead_wait", "Agent", {"description": "Check the docs", "prompt": "p"}))
    w.write_main(main)
    paces = {0: (4, ["Bash", "Read", "Edit", "Grep", "Bash", "Write"]), 1: (7, ["Bash", "Bash", "Read", "mcp__claude_ai_Claude_Docs__batch"]), 2: (19, ["Read", "Grep"])}
    for j, (step, names) in paces.items():
        r = [w.prompt(t - 868, "part", agent=f"x{j}")]
        k = 0
        tt = t - 860 + j * 2
        while tt < t - 3:
            burst = j == 0 and (t - 600) < tt < (t - 420)      # a quiet stretch in the busy one
            if not burst:
                r += w.calls(tt, f"x{j}_{k}", names[k % len(names)], agent=f"x{j}", err=(j == 1 and k % 5 == 3), blocked=(j == 0 and k % 23 == 11))
            k += 1
            tt += step
        r.append(w.tool(t - 2, f"x{j}_run", "Bash", {"command": "pytest -q tests/"}, agent=f"x{j}"))
        w.write_sub(f"x{j}", r, {"agentType": "general-purpose", "description": ["Port the payment client", "Write the integration tests", "Review the migration"][j], "toolUseId": f"sp{j}"})
    v = make("docs-site", "44444444-0000-0000-0000-000000000002")
    rr = [v.prompt(t - 700, "Tidy the guides")]
    for k in range(12):
        rr += v.calls(t - 690 + k * 20, f"v{k}", ["Read", "Edit"][k % 2])
    rr.append(v.text(t - 300, "v-end", "Guides tidied."))
    v.write_main(rr)


class StubHandler(Handler):
    def do_GET(self):  # noqa: N802
        if self.path == "/__test/code":     # test hook: a fresh one-time pairing code per browser context
            return self._json(200, {"code": self.server.auth.new_code()})
        if self.path.startswith("/api/alerts"):
            if self.server.auth.session(self.headers) is None:
                return self._json(401, {"error": "Not paired."})
            return self._json(200, {"alerts": [], "now": time.time()})
        return super().do_GET()


def _server(st):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
    srv.daemon_threads = True
    srv.store, srv.verbose = st, False
    srv.port = srv.server_address[1]
    srv.allowed_hosts = {f"127.0.0.1:{srv.port}", f"localhost:{srv.port}"}
    srv.auth, srv.control = Auth(srv.port), None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


BROWSER_JS = r"""
const { chromium } = require(process.env.PW);
(async () => {
  const out = {};
  const browser = await chromium.launch({ executablePath: process.env.CHROME, headless: true });
  const port = process.env.PORT;
  const open = async (w, h, scheme) => {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: scheme });
    const page = await ctx.newPage();
    page.errs = [];
    page.on('pageerror', (e) => page.errs.push(String(e)));
    const { code } = await (await fetch(`http://127.0.0.1:${port}/__test/code`)).json();
    await page.goto(`http://127.0.0.1:${port}/pair#code=${code}`, { waitUntil: 'load' });
    try { await page.waitForSelector('.agroup:not(.loose) .arow[data-key] svg.spark', { timeout: 15000, state: 'attached' }); } catch (e) { throw new Error('no board: ' + page.errs.join(' / ') + ' / ' + (await page.evaluate(() => document.body.innerText.slice(0, 400)))); }
    await page.waitForTimeout(800);
    return { ctx, page };
  };
  const S = process.env.SHOTS;
  {
    const { ctx, page } = await open(1440, 900, 'light');
    out.rows = await page.evaluate(() => [...document.querySelectorAll('.agroup:not(.loose) .arow[data-key]')].map((r) => {
      const svg = r.querySelector('svg.spark');
      return { key: r.dataset.key, rate: r.querySelector('.a-rate').textContent, faint: r.querySelector('.a-rate').classList.contains('faint'),
        bars: svg.querySelectorAll('.sb').length, paths: svg.querySelectorAll('path').length,
        now: svg.querySelectorAll('.sb')[14].getAttribute('class'), errs: [...svg.querySelectorAll('.sp-err')].filter((p) => p.getAttribute('d')).length,
        nowFill: getComputedStyle(svg.querySelectorAll('.sb')[14]).fill, pastFill: (() => { const p = [...svg.querySelectorAll('.sb.sp')][0]; return p ? getComputedStyle(p).fill : ''; })(),
        errFill: (() => { const p = [...svg.querySelectorAll('.sp-err')].find((x) => x.getAttribute('d')); return p ? getComputedStyle(p).fill : ''; })(),
        label: svg.getAttribute('aria-label') };
    }));
    // Hover: the busiest subagent, a minute with failures if it has one.
    const key = process.env.HOVER_KEY;
    const box = await page.evaluate((k) => {
      const svg = document.querySelector(`.arow[data-key="${k}"] svg.spark`);
      const bars = [...svg.querySelectorAll('.sb')];
      const errs = [...svg.querySelectorAll('.sp-err')];
      let i = errs.findIndex((p, j) => p.getAttribute('d') && j < 14);
      if (i < 0) i = 12;
      const r = bars[i].getBoundingClientRect();
      return { x: r.left + r.width / 2, y: r.top + 4, i };
    }, key);
    await page.mouse.move(box.x, box.y);
    await page.waitForTimeout(250);
    out.tip = await page.evaluate(() => { const t = document.querySelector('#tip'); return { hidden: t.hidden, title: t.querySelector('.tt') && t.querySelector('.tt').textContent, rows: [...t.querySelectorAll('.tr')].map((r) => r.textContent), text: t.textContent }; });
    out.tip.i = box.i;
    out.hov = await page.evaluate(() => document.querySelectorAll('svg.spark .sb.hov').length);
    if (S) {
      const r = await page.evaluate((k) => { const b = document.querySelector(`.arow[data-key="${k}"]`).closest('.agroup').getBoundingClientRect(); return { x: b.left, y: b.top, width: b.width, height: b.height }; }, key);
      await page.screenshot({ path: S + '/tooltip-light.png', clip: { x: Math.max(0, r.x + r.width - 560), y: Math.max(0, r.y), width: 760, height: Math.min(420, r.height + 200) } });
    }
    await page.mouse.move(5, 5);
    await page.waitForTimeout(150);
    out.tipAfterLeave = await page.evaluate(() => document.querySelector('#tip').hidden);
    // Keyboard: focus the row, arrow left twice from the running minute.
    await page.focus(`.arow[data-key="${key}"]`);
    await page.keyboard.press('ArrowLeft');
    await page.keyboard.press('ArrowLeft');
    await page.waitForTimeout(150);
    out.key = await page.evaluate(() => { const t = document.querySelector('#tip'); const hov = document.querySelector('svg.spark .sb.hov'); return { hidden: t.hidden, title: t.querySelector('.tt') && t.querySelector('.tt').textContent, idx: hov ? [...hov.parentNode.querySelectorAll('.sb')].indexOf(hov) : -1 }; });
    await page.keyboard.press('Escape');
    await page.waitForTimeout(100);
    out.keyEsc = await page.evaluate(() => document.querySelector('#tip').hidden && !document.querySelector('svg.spark .sb.hov'));
    // No flicker: frozen data, three or more polls, nothing added, removed or rewritten.
    await page.evaluate(() => document.activeElement.blur());
    let polls = 0;
    page.on('request', (rq) => { if (rq.url().includes('/api/live')) polls += 1; });
    await page.waitForTimeout(3500);
    await page.evaluate(() => {
      window.__m = { childList: 0, attributes: {}, added: 0, removed: 0 };
      new MutationObserver((ms) => { for (const m of ms) {
        if (m.type === 'childList') { window.__m.childList += 1; window.__m.added += m.addedNodes.length; window.__m.removed += m.removedNodes.length; }
        else if (m.type === 'attributes') window.__m.attributes[m.attributeName] = (window.__m.attributes[m.attributeName] || 0) + 1;
      } }).observe(document.querySelector('#main'), { subtree: true, childList: true, attributes: true });
    });
    const p0 = polls;
    await page.waitForTimeout(10500);
    out.flicker = await page.evaluate(() => window.__m);
    out.flicker.polls = polls - p0;
    if (S) await page.screenshot({ path: S + '/board-1440-light.png', fullPage: true });
    out.errors = page.errs;
    await ctx.close();
  }
  for (const [w, h, scheme] of [[1440, 900, 'dark'], [390, 844, 'light'], [390, 844, 'dark']]) {
    const { ctx, page } = await open(w, h, scheme);
    const info = await page.evaluate(() => {
      const r = document.querySelector('.agroup:not(.loose) .arow[data-key]');
      const rate = r.querySelector('.a-rate'), svg = r.querySelector('svg.spark');
      return { rateShown: rate.getClientRects().length > 0, sparkShown: svg.getClientRects().length > 0, overflow: document.documentElement.scrollWidth > innerWidth,
        rateRight: rate.getBoundingClientRect().right, rowRight: r.getBoundingClientRect().right };
    });
    if (S) await page.screenshot({ path: `${S}/board-${w}-${scheme}.png`, fullPage: true });
    out[`v${w}${scheme}`] = { ...info, errors: page.errs };
    await ctx.close();
  }
  await browser.close();
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def test_browser():
    if not (os.path.isdir(PLAYWRIGHT) and os.path.exists(CHROME) and shutil.which("node")):
        print("browser: skipped (no playwright-core, Chrome or node)")
        return
    root = tempfile.mkdtemp(prefix="busy-ui-", dir=BASE)
    t = time.time()
    busy_fixture(root, t)
    frozen = t
    st = Store(root=os.path.join(root, "claude"), days=1, clock=lambda: frozen)
    srv = _server(st)
    auto_token(srv)
    js = os.path.join(BASE, "rate_browser.js")
    with open(js, "w") as fh:
        fh.write(BROWSER_JS)
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
    hover = "44444444-0000-0000-0000-000000000001/x1"
    env = dict(os.environ, PW=PLAYWRIGHT, CHROME=CHROME, PORT=str(srv.port), SHOTS=SHOTS, HOVER_KEY=hover)
    try:
        r = subprocess.run(["node", js], capture_output=True, text=True, timeout=180, env=env)
    finally:
        srv.shutdown()
        srv.server_close()
    if r.returncode != 0:
        check("browser: page check ran", False, r.stderr[-800:])
        return
    out = json.loads(r.stdout.strip().splitlines()[-1])
    rows = {x["key"]: x for x in out["rows"]}
    api = {a["key"]: a for a in st.live_payload()["agents"]}
    check("browser: no page errors", not out["errors"] and not any(out[k]["errors"] for k in out if k.startswith("v")), out["errors"])
    check("browser: every row has a 15-minute spark with two paths per minute", rows and all(x["bars"] == 15 and x["paths"] == 30 for x in rows.values()), [(x["bars"], x["paths"]) for x in rows.values()])
    check("browser: the rate figure is the payload's rate as 'N calls/min'", all(x["rate"] == f"{api[k]['rate']} call{'' if api[k]['rate'] == 1 else 's'}/min" for k, x in rows.items()), [(x["rate"], api[k]["rate"]) for k, x in rows.items()])
    busy = rows[hover]
    check("browser: a working row's rate is in the secondary tone, an idle row's is faint",
          not busy["faint"] and rows["44444444-0000-0000-0000-000000000002"]["faint"], (busy, rows["44444444-0000-0000-0000-000000000002"]["rate"]))
    check("browser: the running minute is drawn lighter than the finished ones", "sp-now" in busy["now"] and busy["nowFill"] != busy["pastFill"] and busy["pastFill"], (busy["now"], busy["nowFill"], busy["pastFill"]))
    check("browser: failed calls draw a red top segment", busy["errs"] > 0 and busy["errFill"] and busy["errFill"] != busy["pastFill"], (busy["errs"], busy["errFill"]))
    check("browser: the spark's label reads the counts for screen readers", "Tool calls per minute" in busy["label"] and "failed" in busy["label"], busy["label"])
    tip = out["tip"]
    b = api[hover]["spark"][tip["i"]]
    t0 = api[hover]["spark_end"] - (15 - tip["i"]) * 60
    want_title = datetime.fromtimestamp(t0).strftime("%H:%M") + "–" + datetime.fromtimestamp(t0 + 60).strftime("%H:%M")
    check("tooltip: shows on hover with the minute's time range", not tip["hidden"] and tip["title"] == want_title, (tip["title"], want_title))
    check("tooltip: total calls and failed calls", tip["rows"][0] == f"Tool calls{b['n']}" and (not b["err"] or tip["rows"][1] == f"Failed{b['err']}"), tip["rows"])
    check("tooltip: nothing but the title and rows (no stray text)", tip["text"] == tip["title"] + "".join(tip["rows"]), tip["text"])
    named = [f"{k}{v}" for k, v in b["tools"].items()]
    check("tooltip: the top tools of that minute with counts", all(n in tip["rows"] for n in named) and len(named) >= 2, (tip["rows"], named))
    check("tooltip: the hovered minute is marked, and the tooltip goes when the pointer leaves", out["hov"] == 1 and out["tipAfterLeave"], (out["hov"], out["tipAfterLeave"]))
    k = out["key"]
    check("keyboard: arrow keys on a focused row walk the minutes with the same tooltip", not k["hidden"] and k["idx"] == 13 and k["title"], k)
    check("keyboard: Escape closes it", out["keyEsc"])
    f = out["flicker"]
    print(f"flicker: {f['polls']} polls, childList={f['childList']} (+{f['added']} -{f['removed']}), attributes={f['attributes']}")
    check("flicker: 3+ polls with unchanged data add or remove no node", f["polls"] >= 3 and f["added"] == 0 and f["removed"] == 0, f)
    check("flicker: and rewrite no attribute", not f["attributes"], f["attributes"])
    d = out["v1440dark"]
    check("dark 1440: spark and rate shown", d["sparkShown"] and d["rateShown"], d)
    for name in ("v390light", "v390dark"):
        p = out[name]
        check(f"phone ({name[4:]}): only the rate figure, no horizontal scroll", p["rateShown"] and not p["sparkShown"] and not p["overflow"], p)


def main():
    try:
        st = Store(days=1, clock=lambda: NOW[0])
        test_buckets(st)
        test_rate_edges()
        test_incremental()
        test_burn(Store(days=1, clock=lambda: NOW[0]))
        test_size()
        test_browser()
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
