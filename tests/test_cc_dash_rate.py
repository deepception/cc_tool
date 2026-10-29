#!/usr/bin/env python3
"""Tool-call rate checks for cc-dash's agent rows (store.py _spark, GET /api/live) and for the
Factory floor's inspector, which draws the same figures from /api/graph (graph.py).

Each agent row on the board (/api/live) carries the last 15 one-minute buckets of
tool calls as {n, err, tools} (calls started in the minute, how many failed,
the top 3 tools by count), plus `rate` (calls started in the trailing 60
seconds) and `peak` (the busiest bucket). This builds synthetic transcripts
under a temp ${CLAUDE_CONFIG_DIR} with a fixed clock and checks bucket counts,
failures (errors and guard blocks), top tools and their tie order, the rate
window edges, the peak, the still-filling current minute, idle and silent
rows, the payload-size bound on a busy board, and incremental updates after
appending tool calls (only the new bytes are read). It also checks the burn
rule beside the rate: $/min from cost_min, and new tokens per minute (never
cache reads) only for an unpriced model, server side and in factory.js rate()
(extracted and run in node with app.js's money and compact).

The floor shows a node's calls as a bare count per minute and no failed segment
or tooltip, so with headless Chrome and playwright-core present it opens the
floor on a busy fixture (frozen clock; /api/graph from the same store) and checks
the inspector of a busy agent, an agent on an unpriced model, their sessions and
an idle session: the sparkline (one bar per minute, the counts in its label, the
running minute styled apart, bars ordered like the counts), the Burn row and the
rate on a session's face as the payload's figure ($/min, else new tokens a minute),
no Burn row for an idle session, light and dark, the phone's bottom sheet, and
that three polls with unchanged data leave the open inspector's nodes alone.
Set CC_DASH_SHOTS=<dir> to save light and dark shots at 1440 and 390 px.
Nothing outside the temp dir is touched.

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
from cc_dash import graph as graph_mod  # noqa: E402
from cc_dash import store as store_mod  # noqa: E402
from cc_dash.auth import Auth  # noqa: E402
from cc_dash.server import Handler  # noqa: E402
from cc_dash.store import Store  # noqa: E402

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
os.environ["XDG_STATE_HOME"] = os.path.join(BASE, "state")   # attention.py reads the hooks' log from here: no real one is ever read
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
                fh.write(json.dumps(r, separators=(",", ":")) + "\n")      # compact, as Claude Code writes (the parser pre-filters api-request lines by prefix)
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
    # factory.js rate(): run the real function in node, with the app's own money and compact
    if not shutil.which("node"):
        print("burn (js): skipped (no node)")
        return
    web = os.path.join(REPO, "lib", "cc_dash", "web")
    fx = open(os.path.join(web, "factory.js")).read()
    app = open(os.path.join(web, "app.js")).read()
    try:
        # factory.js wraps its helpers around ctx (C.money, C.compact); here C is the app's own two formatters.
        parts = [stmt(app, "money"), stmt(app, "compact"), stmt(fx, "num"), stmt(fx, "money"), stmt(fx, "compact"), stmt(fx, "rate")]
    except ValueError as e:
        check("burn (js): rate() and the helpers it calls found in factory.js", False, str(e))
        return
    # In the page, C is the app's context: C.money and C.compact are app.js's two functions (here inside their own scope,
    # so factory.js's own money and compact consts, which call them through C, stay what they are).
    js = ("const C = (() => {\n" + parts[0] + "\n" + parts[1] + "\nreturn { money, compact };\n})();\n"
          + "\n".join(parts[2:]) + """
const cases = JSON.parse(process.argv[2]);
console.log(JSON.stringify(cases.map((b) => rate(b))));
""")
    path = os.path.join(BASE, "burn.js")
    with open(path, "w") as fh:
        fh.write(js)
    cases = [bp, bu, {"tok_min": 0, "new_min": 0, "out_min": 0, "unpriced_min": 0, "cost_min": 0},
             {"tok_min": 1280000, "new_min": 9000, "out_min": 2000, "unpriced_min": 0, "cost_min": 0.4321},
             {"tok_min": 1280000, "new_min": 0, "out_min": 0, "unpriced_min": 0, "cost_min": 0},
             {"tok_min": 0, "new_min": 0, "out_min": 0, "unpriced_min": 0, "cost_min": 0.004},
             None]
    r = subprocess.run(["node", path, json.dumps(cases)], capture_output=True, text=True, timeout=30)
    out = json.loads(r.stdout) if r.returncode == 0 else None
    check("burn (js): factory.js rate() ran", out is not None, (r.stderr or r.stdout)[-400:])
    if out:
        check("burn (js): a priced agent shows $/min", out[0] == fmt_money(bp["cost_min"]) + "/min" and out[0].endswith("/min") and out[0].startswith(("$", "<$")), out[0])
        check("burn (js): an unpriced agent falls back to 'new tok/min' from new_min", out[1] == fmt_compact(bu["new_min"]) + " new tok/min" == f"{bu['new_min'] / 1000:.1f}K new tok/min".replace(".0K", "K"), out[1])
        check("burn (js): no activity shows nothing", out[2] == "", out[2])
        check("burn (js): a context-heavy agent shows dollars, not the 1.28M cache-read figure", out[3] == "$0.43/min" and "1.3M" not in out[3], out[3])
        check("burn (js): cache reads alone (tok_min, no new tokens, no cost) show nothing", out[4] == "", out[4])
        check("burn (js): a cost under a cent reads '<$0.01/min'", out[5] == "<$0.01/min", out[5])
        check("burn (js): no burn object shows nothing", out[6] == "", out[6])


def stmt(text, name):
    """The top-level (two-space indent) `function name(...) {...}` or `const name = ...;` of a script, found by
    scanning brackets and strings to its end. Raises ValueError when it is not there."""
    m = re.search(r"\n  (?:function %s\(|const %s =)" % (re.escape(name), re.escape(name)), text)
    if not m:
        raise ValueError("no top-level %s in the script" % name)
    start = m.start() + 3                         # past the newline and the two spaces
    fn = text.startswith("function", start)
    depth, i, seen_body, quote = 0, start, False, None
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = None
        elif ch in "'\"`":
            quote = ch
        elif ch in "([{":
            depth += 1
            seen_body = seen_body or ch == "{"
        elif ch in ")]}":
            depth -= 1
            if fn and depth == 0 and ch == "}" and seen_body:
                return text[start:i + 1]
        elif ch == ";" and depth == 0 and not fn:
            return text[start:i + 1]
        i += 1
    raise ValueError("unterminated %s" % name)


def fmt_money(v):
    """app.js money(v)."""
    if v == 0:
        return "$0"
    if v < 0.01:
        return "<$0.01"
    return "$%.2f" % v if v < 100 else "$%s" % format(round(v), ",")


def fmt_compact(n):
    """app.js compact(n) for the sizes a rate takes."""
    if n < 1000:
        return str(round(n))
    if n < 1e6:
        return (("%.1f" % (n / 1e3)) if n < 1e4 else ("%.0f" % (n / 1e3))).removesuffix(".0") + "K"
    return (("%.1f" % (n / 1e6)) if n < 1e7 else ("%.0f" % (n / 1e6))).removesuffix(".0") + "M"


def fmt_rate(b):
    """factory.js rate(b): dollars a minute, else new tokens a minute, never tok_min."""
    if not b:
        return ""
    if b["cost_min"] > 0:
        return fmt_money(b["cost_min"]) + "/min"
    return fmt_compact(b["new_min"]) + " new tok/min" if b["new_min"] > 0 else ""


# ── browser ─────────────────────────────────────────────────────────────────
UNPRICED = "acme-unknown-model-1"
SHOP, DOCS, LAB = "44444444-0000-0000-0000-000000000001", "44444444-0000-0000-0000-000000000002", "44444444-0000-0000-0000-000000000003"


def busy_fixture(root, t):
    """A small floor that reads well: a lead with three subagents at different paces and failures (shop-api),
    an idle session (docs-site) and a lead with one subagent on a model that has no price (lab-bench)."""
    def make(name, sid):
        w = World(name, sid)
        w.cwd = os.path.join(root, "work", name)
        os.makedirs(w.cwd, exist_ok=True)
        w.pdir = os.path.join(root, "claude", "projects", w.cwd.replace("/", "-"))
        w.main, w.subdir = os.path.join(w.pdir, w.sid + ".jsonl"), os.path.join(w.pdir, w.sid, "subagents")
        return w
    w = make("shop-api", SHOP)
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
        if j == 1:      # the effort this agent's requests carried (Claude Code writes an api-request record before them)
            r.append({"type": "api-request", "sessionId": SHOP, "timestamp": iso(t - 867),
                      "params": {"model": "claude-opus-5-5", "thinking": {"type": "adaptive", "display": "updates"}, "output_config": {"effort": "high"}}})
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
    v = make("docs-site", DOCS)
    rr = [v.prompt(t - 700, "Tidy the guides")]
    for k in range(12):
        rr += v.calls(t - 690 + k * 20, f"v{k}", ["Read", "Edit"][k % 2])
    rr.append(v.text(t - 300, "v-end", "Guides tidied."))
    v.write_main(rr)
    # lab-bench: both threads on a model without a price, with big cache reads that must never show up as a rate
    z = make("lab-bench", LAB)
    big = {"input_tokens": 400, "output_tokens": 900, "cache_read_input_tokens": 200000, "cache_creation_input_tokens": 3000}
    z.write_main([z.prompt(t - 400, "Calibrate the bench"),
                  z.tool(t - 395, "lab_sp", "Agent", {"description": "Run the sweep", "prompt": "p"}, model=UNPRICED, usage=dict(big))])
    ru = [z.prompt(t - 394, "sweep", agent="u1")]
    for k in range(8):
        ru += z.calls(t - 300 + k * 30, f"u{k}", "Read", agent="u1", model=UNPRICED, usage=dict(big))
    ru.append(z.tool(t - 3, "u_run", "Bash", {"command": "make bench"}, agent="u1", model=UNPRICED, usage=dict(big)))
    z.write_sub("u1", ru, {"agentType": "general-purpose", "description": "Run the sweep", "toolUseId": "lab_sp"})


class StubHandler(Handler):
    def do_GET(self):  # noqa: N802
        if self.path == "/__test/code":     # test hook: a fresh one-time pairing code per browser context
            return self._json(200, {"code": self.server.auth.new_code()})
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
const first = (e) => String((e && e.message) || e).split('\n')[0].slice(0, 300);
const IDS = JSON.parse(process.env.IDS);
const out = {};
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME, headless: true });
  const port = process.env.PORT;
  const S = process.env.SHOTS;
  const HELPERS = () => {
    const txt = (el) => (el ? el.textContent.replace(/\s+/g, ' ').trim() : null);
    const q = (id) => document.querySelector('.gx-node[data-id=' + JSON.stringify(id) + ']');
    window.__t = { txt, q };
  };
  const open = async (w, h, scheme) => {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: scheme });
    await ctx.addInitScript(HELPERS);
    const page = await ctx.newPage();
    page.errs = [];
    page.on('pageerror', (e) => page.errs.push(first(e)));
    const { code } = await (await fetch(`http://127.0.0.1:${port}/__test/code`)).json();
    await page.goto(`http://127.0.0.1:${port}/pair#code=${code}`, { waitUntil: 'load' });   // pairing lands on #/factory
    try { await page.waitForSelector('.gx-node[data-id]', { timeout: 15000 }); } catch (e) { throw new Error('no floor: ' + page.errs.join(' / ') + ' / ' + (await page.evaluate(() => document.body.innerText.slice(0, 300)))); }
    await page.waitForTimeout(800);
    return { ctx, page };
  };
  // Open a node's inspector the way a user does: on a desktop, centre it and click it; on a phone, pick it in the list.
  const pick = async (page, id, phone) => {
    if (phone) {
      await page.click('.fx-mode button[data-mode="list"]');
      await page.waitForSelector('.fx-li[data-node=' + JSON.stringify(id) + ']', { timeout: 4000 });
      await page.click('.fx-li[data-node=' + JSON.stringify(id) + ']');
    } else {
      const p = await page.evaluate((id) => {
        const el = __t.q(id);
        if (!el) return null;
        try { window.ccFactory.graph().center(id, { animate: false }); } catch (e) { /* no engine */ }
        return true;
      }, id);
      if (!p) throw new Error('no node ' + id);
      await page.waitForTimeout(250);
      const pt = await page.evaluate((id) => { const r = __t.q(id).getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; }, id);
      await page.mouse.click(pt.x, pt.y);
    }
    await page.waitForFunction(() => { const a = document.querySelector('aside.fx-inspector'); return a && !a.hidden; }, null, { timeout: 4000 });
    await page.waitForTimeout(400);
  };
  const close = async (page) => {
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => { const a = document.querySelector('aside.fx-inspector'); return !a || a.hidden; }, null, { timeout: 4000 });
  };
  // What the open inspector shows for the sparkline and the burn.
  const read = (page) => page.evaluate(() => {
    const a = document.querySelector('aside.fx-inspector'), svg = a.querySelector('svg.fx-spark');
    const rects = svg ? [...svg.querySelectorAll('rect')] : [];
    const fill = (el) => (el ? getComputedStyle(el).fill : null);
    const burn = [...a.querySelectorAll('.fx-row')].find((r) => r.dataset.key === 'Burn');
    const sr = svg ? svg.getBoundingClientRect() : null, ar = a.getBoundingClientRect(), br = burn ? burn.getBoundingClientRect() : null;
    return { title: __t.txt(a.querySelector('.fx-insp-title')), head: __t.txt(a.querySelector('.fx-insp-kind')),
      tags: [...a.querySelectorAll('.fx-insp-kind .tag')].map((x) => x.textContent.trim()),
      burn: burn ? __t.txt(burn.querySelector('.fx-row-v')) : null,
      spark: svg ? { n: rects.length, cls: rects.map((r) => r.getAttribute('class')), h: rects.map((r) => +r.getAttribute('height')), label: svg.getAttribute('aria-label'),
        shown: svg.getClientRects().length > 0, fill: { sp: fill(rects.find((r) => r.classList.contains('sp'))), sp0: fill(rects.find((r) => r.classList.contains('sp0'))),
          now: fill(rects.find((r) => r.classList.contains('sp-now'))) },
        inside: sr.left >= ar.left - 1 && sr.right <= ar.right + 1 } : null,
      burnInside: br ? br.left >= ar.left - 1 && br.right <= ar.right + 1 && br.height > 0 : null,
      overflow: document.documentElement.scrollWidth > innerWidth };
  });
  const step = async (name, fn) => { const r = {}; try { await fn(r); } catch (e) { r.error = first(e); } out[name] = r; };

  // The inspector of each kind of node, light, 1440 px.
  await step('light', async (r) => {
    const { ctx, page } = await open(1440, 900, 'light');
    try {
      r.faces = await page.evaluate((ids) => {
        const o = {};
        for (const k of ['busySess', 'unpSess', 'idleSess']) { const f = __t.q(ids[k]); o[k] = f ? { rate: f.querySelector('.fx-rate') ? f.querySelector('.fx-rate').textContent.trim() : null } : null; }
        return o;
      }, IDS);
      for (const k of ['busy', 'unp', 'busySess', 'unpSess', 'idleSess']) {
        await pick(page, IDS[k], false);
        r[k] = await read(page);
        if (S && k === 'busy') await page.screenshot({ path: S + '/inspector-1440-light.png' });
        await close(page);
      }
      // The open inspector of a busy agent through three polls with unchanged data.
      await pick(page, IDS.busy, false);
      let polls = 0;
      page.on('request', (rq) => { if (new URL(rq.url()).pathname === '/api/graph') polls += 1; });
      await page.evaluate(() => {
        window.__m = { childList: 0, added: 0, removed: 0, changed: [], same: 0 };
        const insp = document.querySelector('aside.fx-inspector');
        window.__rects = [...insp.querySelectorAll('svg.fx-spark rect')];
        new MutationObserver((ms) => {
          const last = new Map();
          for (const m of ms) {
            if (m.type === 'childList') { window.__m.childList += 1; window.__m.added += m.addedNodes.length; window.__m.removed += m.removedNodes.length; }
            else if (m.type === 'attributes') {
              let per = last.get(m.target);
              if (!per) last.set(m.target, per = new Map());
              const prev = per.get(m.attributeName);
              if (prev) { if (prev.oldValue === m.oldValue) window.__m.same += 1; else window.__m.changed.push(prev.attributeName); }
              per.set(m.attributeName, m);
            }
          }
          for (const [el, per] of last) for (const [name, m] of per) { if (m.oldValue === el.getAttribute(name)) window.__m.same += 1; else window.__m.changed.push(name + ' on ' + el.tagName.toLowerCase()); }
        }).observe(insp, { subtree: true, childList: true, attributes: true, attributeOldValue: true });
      });
      const p0 = polls;
      await page.waitForTimeout(12500);
      r.flicker = await page.evaluate(() => Object.assign({}, window.__m, { kept: [...document.querySelectorAll('aside.fx-inspector svg.fx-spark rect')].every((x, i) => x === window.__rects[i]) }));
      r.flicker.polls = polls - p0;
      r.errors = page.errs;
    } finally { await ctx.close(); }
  });
  // The same bars in the dark theme.
  await step('dark', async (r) => {
    const { ctx, page } = await open(1440, 900, 'dark');
    try {
      await pick(page, IDS.busy, false);
      r.busy = await read(page);
      if (S) await page.screenshot({ path: S + '/inspector-1440-dark.png' });
      r.errors = page.errs;
    } finally { await ctx.close(); }
  });
  // A phone: the bottom sheet shows the bars and the burn without a horizontal scroll.
  for (const scheme of ['light', 'dark']) {
    await step('phone-' + scheme, async (r) => {
      const { ctx, page } = await open(390, 844, scheme);
      try {
        await pick(page, IDS.busy, true);
        r.busy = await read(page);
        if (S) await page.screenshot({ path: S + '/inspector-390-' + scheme + '.png' });
        r.errors = page.errs;
      } finally { await ctx.close(); }
    });
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
    t = (int(time.time()) // 60) * 60 - 30       # the middle of the previous minute: never ahead of the page's own clock
    busy_fixture(root, t)
    st = Store(root=os.path.join(root, "claude"), days=1, clock=lambda: t)
    pay = graph_mod.payload(st)
    nodes = {n["id"]: n for n in pay["nodes"]}
    ids = {"busy": "a:%s/x1" % SHOP, "unp": "a:%s/u1" % LAB, "busySess": "s:" + SHOP, "unpSess": "s:" + LAB, "idleSess": "s:" + DOCS}
    srv = _server(st)
    js = os.path.join(BASE, "rate_browser.js")
    with open(js, "w") as fh:
        fh.write(BROWSER_JS)
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
    env = dict(os.environ, PW=PLAYWRIGHT, CHROME=CHROME, PORT=str(srv.port), SHOTS=SHOTS, IDS=json.dumps(ids))
    try:
        r = subprocess.run(["node", js], capture_output=True, text=True, timeout=240, env=env)
    finally:
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
        print(json.dumps(out, indent=1)[:9000])
    li = out["light"]
    check("browser: no page errors", not any(s.get("errors") for s in out.values()), [s.get("errors") for s in out.values() if s.get("errors")])
    if "busy" not in li:
        return
    bn, un = nodes[ids["busy"]], nodes[ids["unp"]]
    check("fixture: the busy agent is priced and running, the other has no price and still burns new tokens",
          bn["state"] == "tool" and bn["burn"]["cost_min"] > 0 and un["state"] == "tool" and un["burn"]["cost_min"] == 0 and un["burn"]["new_min"] > 0
          and un["burn"]["tok_min"] > 20 * un["burn"]["new_min"], (bn["burn"], un["burn"]))

    def spark_ok(k, who):
        """The sparkline of a node as the inspector draws it, against the payload's counts: a bar a minute, the
        empty minutes flat and apart from the rest, more calls never a shorter bar, the counts in the label."""
        sp, arr = who["spark"], nodes[ids[k]]["spark"]
        h, cls = sp["h"], [c.split() for c in sp["cls"]]
        zero, some = [i for i, v in enumerate(arr) if v == 0], [i for i, v in enumerate(arr) if v > 0]
        why = (arr, h, sp["cls"], sp["label"])
        if not (sp["n"] == len(arr) == 15 and len(cls) == 15):
            return False, why
        if not all(("sp0" in cls[i]) == (arr[i] == 0) for i in range(14)):                       # finished minutes: sp or sp0
            return False, why
        if not (("sp0" in cls[14]) if arr[14] == 0 else ("sp-now" in cls[14])):                  # the running one: sp-now when it has calls
            return False, why
        if not (", ".join(str(v) for v in arr) in sp["label"] and "Tool calls per minute" in sp["label"]):
            return False, why
        if zero and some and not max(h[i] for i in zero) < min(h[i] for i in some):
            return False, why
        if any(arr[i] < arr[j] and h[i] > h[j] for i in range(15) for j in range(15)):
            return False, why
        return True, why
    busy = li["busy"]
    ok, why = spark_ok("busy", busy)
    check("floor: the busy agent's sparkline has a bar per minute, empty minutes flat, taller bars for more calls, the counts in its label",
          ok and len(set(busy["spark"]["h"])) > 3 and nodes[ids["busy"]]["spark"][-1] > 0, why)
    f = busy["spark"]["fill"]
    check("floor: the running minute is drawn apart from the finished ones", "sp-now" in busy["spark"]["cls"][-1] and f["now"] and f["sp"] and f["now"] != f["sp"], f)
    d = out["dark"]["busy"]["spark"]["fill"] if "busy" in out["dark"] else {}
    check("floor (dark): the running minute is still drawn apart, and the finished bars differ from light", d.get("now") and d.get("now") != d.get("sp") and d.get("sp") != f["sp"], (d, f))
    check("floor: the inspector head names the kind, the model and the recorded effort (a chip)",
          bn["effort"] == "high" and all(w in (busy["head"] or "") for w in ("Agent", "Opus", "effort high")), (bn["effort"], busy["head"]))
    want = fmt_rate(bn["burn"])
    check("floor: the busy agent's Burn row starts with its $/min", want.endswith("/min") and want.startswith(("$", "<$")) and (busy["burn"] or "").startswith(want), (want, busy["burn"]))
    ub = li["unp"]
    uwant = fmt_rate(un["burn"])
    check("floor: an agent on an unpriced model shows new tokens a minute, from new_min",
          uwant.endswith(" new tok/min") and (ub["burn"] or "").startswith(uwant), (uwant, ub["burn"]))
    check("floor: cache reads never count: the unpriced agent's rate is not its tok_min", fmt_compact(un["burn"]["tok_min"]) not in (ub["burn"] or "").split(" · ")[0], (ub["burn"], un["burn"]))
    ok, why = spark_ok("unp", ub)
    check("floor: the unpriced agent's sparkline reads like the busy one's", ok, why)
    fc = li["faces"]
    sess = nodes[ids["busySess"]]
    check("floor: a running session's face shows the payload's rate: $/min when priced",
          fc["busySess"]["rate"] == fmt_rate(sess["burn"]) and fc["busySess"]["rate"].endswith("/min"), (fc["busySess"], sess["burn"]))
    usess = nodes[ids["unpSess"]]
    check("floor: ...and new tokens a minute when the model has no price",
          fc["unpSess"]["rate"] == fmt_rate(usess["burn"]) and fc["unpSess"]["rate"].endswith(" new tok/min"), (fc["unpSess"], usess["burn"]))
    check("floor: a session's inspector has the same Burn row", (li["busySess"]["burn"] or "").startswith(fmt_rate(sess["burn"])) and (li["unpSess"]["burn"] or "").startswith(fmt_rate(usess["burn"])),
          (li["busySess"]["burn"], li["unpSess"]["burn"]))
    idle = nodes[ids["idleSess"]]
    check("floor: an idle session has no rate on its face and no Burn row", idle["state"] == "idle" and fc["idleSess"]["rate"] is None and li["idleSess"]["burn"] is None,
          (idle["state"], fc["idleSess"], li["idleSess"]["burn"]))
    ok, why = spark_ok("idleSess", li["idleSess"])
    check("floor: an idle session's sparkline keeps its earlier calls", ok and sum(idle["spark"]) > 0, why)
    fl = li["flicker"]
    print(f"flicker: {fl['polls']} polls with the inspector open, childList={fl['childList']} (+{fl['added']} -{fl['removed']}), changed attributes={fl['changed']}, same-value rewrites={fl['same']}")
    check("flicker: 3+ polls with unchanged data add or remove no node in the open inspector and the bars stay the same elements",
          fl["polls"] >= 3 and fl["added"] == 0 and fl["removed"] == 0 and fl["kept"], fl)
    check("flicker: and no attribute takes a new value", not fl["changed"], fl["changed"])
    for name in ("phone-light", "phone-dark"):
        p = out[name].get("busy")
        if p:
            check(f"phone ({name[6:]}): the bottom sheet shows the bars and the burn row inside the sheet, no horizontal scroll",
                  p["spark"]["shown"] and p["spark"]["inside"] and p["burnInside"] and not p["overflow"] and (p["burn"] or "").startswith(want), p)


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
