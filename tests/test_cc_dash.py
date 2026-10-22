#!/usr/bin/env python3
"""Parser, aggregation and server checks for bin/cc-dash (lib/cc_dash/).

Builds a small fake ${CLAUDE_CONFIG_DIR}/projects tree in a temp dir: one main
transcript with prompts, tool calls, a tool error, a guard refusal, an API
error, a malformed line and an Agent and a Workflow spawn; one Agent subagent
transcript with a .meta.json; one workflow agent with a journal; a repo whose
.git/cc_tool/activity.jsonl holds a guard decision. Then it checks the
numbers the dashboard shows, incremental re-reads after appends, live
detection, and the HTTP server (JSON endpoints, Host-header rejection, path
traversal, read-only methods). Nothing outside the temp dir is touched.

Run:  python3 tests/test_cc_dash.py
"""
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The interpreter is the kernel's: with zsh's exported ARGV0 (Cursor's terminal), sys.executable
# can name the editor's AppImage, and running it opens editor tabs instead of Python.
PY = os.path.realpath("/proc/self/exe") if os.path.exists("/proc/self/exe") else sys.executable
sys.path.insert(0, os.path.join(REPO, "lib"))
from cc_dash import pricing  # noqa: E402
from cc_dash.parser import Acc, FileCache  # noqa: E402
from cc_dash.server import make_server  # noqa: E402
from dash_pairing import auto_cookie  # noqa: E402  (tests/dash_pairing.py: every request carries the pairing cookie)
from cc_dash.store import Store, activity_logs  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


BASE = tempfile.mkdtemp(prefix="cc-dash-test-")
CFG = os.path.join(BASE, "claude")
WORK = os.path.join(BASE, "work", "demo")           # the project the session ran in
os.makedirs(os.path.join(WORK, ".git", "cc_tool"))
PDIR = os.path.join(CFG, "projects", WORK.replace("/", "-"))
SID = "11111111-2222-3333-4444-555555555555"
MAIN = os.path.join(PDIR, SID + ".jsonl")
SUBDIR = os.path.join(PDIR, SID, "subagents")
WFDIR = os.path.join(SUBDIR, "workflows", "wf_demo-1")
os.makedirs(WFDIR)
T0 = time.time() - 600                               # session started ten minutes ago


def rec(kind, t, **kw):
    r = {"type": kind, "timestamp": iso(t), "sessionId": SID, "cwd": WORK, "gitBranch": "feat/x",
         "version": "2.1.300", "entrypoint": "cli", "isSidechain": False, "uuid": f"u{t}"}
    r.update(kw)
    return r


def amsg(t, mid, model, content, usage, stop="tool_use"):
    return rec("assistant", t, message={"id": mid, "model": model, "role": "assistant", "content": content,
                                        "stop_reason": stop, "usage": usage})


def usage(inp, out, cread=0, cw5=0, cw1h=0):
    return {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cread,
            "cache_creation_input_tokens": cw5 + cw1h,
            "cache_creation": {"ephemeral_5m_input_tokens": cw5, "ephemeral_1h_input_tokens": cw1h}}


def tres(t, tid, text, err=False, tur=None):
    r = rec("user", t, message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": err}]})
    if tur is not None:
        r["toolUseResult"] = tur
    return r


OPUS_U1 = usage(10, 200, cread=50_000, cw5=1_000, cw1h=2_000)
main_lines = [
    rec("user", T0, origin={"kind": "human"}, message={"role": "user", "content": [{"type": "text", "text": "<ide_opened_file>x</ide_opened_file>Fix the login bug"}]}),
    # one API message split over two records (text block, then tool_use block) with the same id and usage
    amsg(T0 + 2, "msg_1", "claude-opus-5-5", [{"type": "text", "text": "Looking at the login handler."}], OPUS_U1),
    amsg(T0 + 3, "msg_1", "claude-opus-5-5", [{"type": "tool_use", "id": "tu_bash1", "name": "Bash", "input": {"command": "pytest -q"}}], OPUS_U1),
    tres(T0 + 5, "tu_bash1", "3 passed"),
    amsg(T0 + 6, "msg_2", "claude-opus-5-5", [{"type": "tool_use", "id": "tu_read1", "name": "Read", "input": {"file_path": "/nope.py"}}], usage(5, 40)),
    tres(T0 + 7, "tu_read1", "File does not exist.", err=True),
    amsg(T0 + 8, "msg_3", "claude-opus-5-5", [{"type": "tool_use", "id": "tu_bash2", "name": "Bash", "input": {"command": "rm -rf /"}}], usage(5, 40)),
    tres(T0 + 8.5, "tu_bash2", "PreToolUse:Bash hook error: BLOCKED: 'rm' aimed at '/'. Suggestion: name the files.", err=True),
    "{this line is not json",
    rec("system", T0 + 9, subtype="api_error", error={"formatted": "Overloaded"}, retryAttempt=1, maxRetries=10),
    amsg(T0 + 10, "msg_4", "claude-opus-5-5", [{"type": "tool_use", "id": "tu_agent1", "name": "Agent",
                                               "input": {"description": "Write the tests", "subagent_type": "general-purpose", "model": "sonnet", "prompt": "..."}}], usage(5, 60)),
    tres(T0 + 70, "tu_agent1", "done", tur={"status": "completed", "agentId": "abc123", "resolvedModel": "claude-sonnet-5-5", "totalTokens": 999, "totalDurationMs": 60000}),
    amsg(T0 + 71, "msg_5", "claude-opus-5-5", [{"type": "tool_use", "id": "tu_wf1", "name": "Workflow", "input": {"script": "export const meta = { name: 'review-sweep' }"}}], usage(5, 30)),
    tres(T0 + 72, "tu_wf1", "launched", tur={"status": "async_launched", "runId": "wf_demo-1", "workflowName": "review-sweep"}),
    amsg(T0 + 80, "msg_6", "claude-mystery-9", [{"type": "text", "text": "Done. The bug was a stale token."}], usage(100, 100), stop="end_turn"),
    {"type": "ai-title", "aiTitle": "Fix login bug", "sessionId": SID},
]
with open(MAIN, "w") as fh:
    for ln in main_lines:
        fh.write((ln if isinstance(ln, str) else json.dumps(ln)) + "\n")

SUB = os.path.join(SUBDIR, "agent-abc123.jsonl")
with open(SUB, "w") as fh:
    for r in [
        rec("user", T0 + 11, isSidechain=True, agentId="abc123", message={"role": "user", "content": "Write the tests for login"}),
        amsg(T0 + 20, "smsg_1", "claude-sonnet-5-5", [{"type": "tool_use", "id": "stu_1", "name": "Write", "input": {"file_path": "/w/t.py", "content": "x"}}], usage(3, 500, cread=10_000)),
        tres(T0 + 21, "stu_1", "ok"),
        amsg(T0 + 60, "smsg_2", "claude-sonnet-5-5", [{"type": "tool_use", "id": "stu_2", "name": "SubagentHandback", "input": {"message": "Tests written, 4 pass."}}], usage(3, 80, cread=10_000)),
    ]:
        fh.write(json.dumps(r) + "\n")
json.dump({"agentType": "general-purpose", "description": "Write the tests", "toolUseId": "tu_agent1", "model": "sonnet"},
          open(os.path.join(SUBDIR, "agent-abc123.meta.json"), "w"))
with open(os.path.join(WFDIR, "agent-w1.jsonl"), "w") as fh:
    fh.write(json.dumps(rec("user", T0 + 73, isSidechain=True, agentId="w1", message={"role": "user", "content": "Review file a"})) + "\n")
    fh.write(json.dumps(amsg(T0 + 75, "wmsg_1", "claude-haiku-5-5", [{"type": "text", "text": "partial"}], usage(1000, 10))) + "\n")
json.dump({"agentType": "workflow-subagent", "description": "review:a", "workflowPhase": "Review", "model": "haiku"},
          open(os.path.join(WFDIR, "agent-w1.meta.json"), "w"))
with open(os.path.join(WFDIR, "journal.jsonl"), "w") as fh:
    fh.write(json.dumps({"type": "started", "agentId": "w1", "label": "review:a"}) + "\n")
    fh.write(json.dumps({"type": "failed", "agentId": "w1"}) + "\n")
with open(os.path.join(WORK, ".git", "cc_tool", "activity.jsonl"), "w") as fh:
    fh.write(json.dumps({"ts": T0 + 8.2, "kind": "deny", "tool": "Bash", "hook": "bash-guard", "command": "rm -rf /", "message": "BLOCKED: no."}) + "\n")
    fh.write(json.dumps({"ts": T0 + 8.3, "kind": "tool", "session": SID[:12], "tool": "Bash", "target": "x", "ok": True}) + "\n")
    fh.write("garbage\n")

# A second, old session in another project: outside a 1-day window.
OLD_DIR = os.path.join(CFG, "projects", "-elsewhere-other")
os.makedirs(OLD_DIR)
OLD = os.path.join(OLD_DIR, "99999999-0000-0000-0000-000000000000.jsonl")
with open(OLD, "w") as fh:
    fh.write(json.dumps(rec("user", T0 - 5 * 86400, cwd="/elsewhere/other", message={"role": "user", "content": "old"})) + "\n")
os.utime(OLD, (time.time() - 5 * 86400, time.time() - 5 * 86400))


def snapshot(root):
    out = {}
    for d, _, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            st = os.stat(p)
            out[p] = (st.st_size, st.st_mtime_ns)
    return out


BEFORE = snapshot(BASE)


def test_pricing():
    c = pricing.message_cost("claude-opus-5-5", 10, 200, 50_000, 1_000, 2_000)
    exp = (10 * 4 + 200 * 20 + 50_000 * 0.20 + 1_000 * 5.0 + 2_000 * 8.0) / 1e6
    check("pricing: opus 5.5 with 5m and 1h cache writes", abs(c - exp) < 1e-12, f"{c} vs {exp}")
    check("pricing: dated id normalizes", pricing.normalize("claude-haiku-4-5-20251001") == "claude-haiku-4-5")
    check("pricing: [1m] suffix normalizes", pricing.normalize("claude-opus-5-5[1m]") == "claude-opus-5-5")
    lo = pricing.message_cost("claude-haiku-5-5", 50_000, 0, 0, 0, 0)
    hi = pricing.message_cost("claude-haiku-5-5", 150_000, 0, 0, 0, 0)
    check("pricing: haiku 5.5 long-context tier", abs(lo - 0.005) < 1e-9 and abs(hi - 0.075) < 1e-9, f"{lo} {hi}")
    check("pricing: unknown model is unpriced", pricing.message_cost("claude-mystery-9", 1, 1, 0, 0, 0) is None)
    check("pricing: fast mode doubles", abs(pricing.message_cost("claude-opus-5-5", 0, 1_000_000, 0, 0, 0, speed="fast") - 40.0) < 1e-9)
    check("pricing: display names", pricing.display_name("claude-opus-5-5") == "Opus 5.5" and pricing.display_name("claude-3-5-haiku") == "Haiku 3.5")


def test_parser():
    cache = FileCache()
    acc, changed = cache.get(MAIN, lambda: Acc(MAIN, "main"))
    check("parser: first read parses", changed and acc is not None)
    opus = acc.usage.get("claude-opus-5-5")
    check("parser: usage counted once per message id", opus and opus[0] == 5 and opus[1] == 10 + 5 + 5 + 5 + 5, str(opus))
    check("parser: cache reads and writes split", opus and opus[3] == 50_000 and opus[4] == 1_000 and opus[5] == 2_000, str(opus))
    t = acc.totals()
    check("parser: unknown model tokens are unpriced", t["unpriced_tokens"] == 200, str(t))
    check("parser: one human prompt (IDE tag stripped)", acc.prompts == 1 and acc.first_prompt == "Fix the login bug", acc.first_prompt)
    check("parser: tool calls counted", acc.tool_calls == 5, str(acc.tool_calls))
    check("parser: tool errors include the guard refusal", acc.tool_errors == 2, str(acc.tool_errors))
    check("parser: guard refusal recognized", acc.blocked == 1)
    check("parser: API error counted", acc.api_errors == 1)
    check("parser: malformed line skipped and counted", acc.bad_lines == 1)
    check("parser: ai-title read", acc.ai_title == "Fix login bug")
    check("parser: spawns recorded", [sp["kind"] for sp in acc.spawns] == ["agent", "workflow"])
    sp = acc.spawns[0]
    check("parser: Agent result linked", sp["agent_id"] == "abc123" and sp["status"] == "completed" and sp["model_requested"] == "sonnet", str(sp))
    check("parser: Workflow run id and name", acc.spawns[1]["run_id"] == "wf_demo-1" and acc.spawns[1]["description"] == "review-sweep", str(acc.spawns[1]))
    statuses = [e.get("status") for e in acc.events if e["k"] == "tool"]
    check("parser: tool statuses ok / error / blocked", statuses[:3] == ["ok", "error", "blocked"] and len(statuses) == 5, str(statuses))
    dur = [e.get("dur") for e in acc.events if e["k"] == "tool"][0]
    check("parser: tool duration from result timestamp", dur is not None and abs(dur - 2.0) < 0.01, str(dur))
    check("parser: last stop is end_turn", acc.last_stop == "end_turn" and acc.last_role == "assistant")

    # Incremental: append one complete line and one partial line.
    size0 = os.path.getsize(MAIN)
    read0 = cache.bytes_read
    line = json.dumps(amsg(T0 + 90, "msg_7", "claude-opus-5-5", [{"type": "tool_use", "id": "tu_grep", "name": "Grep", "input": {"pattern": "token"}}], usage(1, 1))) + "\n"
    partial = json.dumps(tres(T0 + 91, "tu_grep", "found"))
    with open(MAIN, "a") as fh:
        fh.write(line + partial[:25])
    acc2, changed = cache.get(MAIN, lambda: Acc(MAIN, "main"))
    check("incremental: same accumulator reused", acc2 is acc and changed)
    check("incremental: only appended bytes read", cache.bytes_read - read0 == len(line) + 25, f"{cache.bytes_read - read0} vs {len(line) + 25}")
    check("incremental: appended tool call counted", acc.tool_calls == 6 and acc.pending.get("tu_grep") is not None)
    with open(MAIN, "a") as fh:
        fh.write(partial[25:] + "\n")
    cache.get(MAIN, lambda: Acc(MAIN, "main"))
    check("incremental: completed partial line parsed", acc.pending.get("tu_grep") is None and acc.bad_lines == 1, f"bad={acc.bad_lines}")
    _, changed = cache.get(MAIN, lambda: Acc(MAIN, "main"))
    check("incremental: unchanged file not re-read", not changed)
    # Shrink: a rewritten (shorter) file is parsed from scratch.
    keep = open(MAIN).read()
    with open(MAIN, "w") as fh:
        fh.write(json.dumps(main_lines[0]) + "\n")
    acc3, _ = cache.get(MAIN, lambda: Acc(MAIN, "main"))
    check("incremental: shrunk file re-read from zero", acc3 is not acc and acc3.prompts == 1 and acc3.tool_calls == 0)
    with open(MAIN, "w") as fh:
        fh.write(keep)
    del size0


def test_store():
    st = Store(root=CFG, days=1)
    st.refresh(force=True)
    check("store: window excludes the old session", list(st.sessions) == [SID], str(list(st.sessions)))
    d = st.session_payload(SID)
    r = d["session"]
    check("store: subagents found (agent + workflow)", r["subagents"] == 2, str(r["subagents"]))
    tree = d["tree"]
    agent_child = tree[0]["children"][0] if tree and tree[0]["children"] else {}
    check("store: Agent subagent linked to its spawn", agent_child.get("agent_id") == "abc123", str(agent_child))
    check("store: subagent model from its transcript", agent_child.get("model") == "Sonnet 5.5" and agent_child.get("family") == "sonnet")
    check("store: handback marks the subagent done", agent_child.get("status") == "done", agent_child.get("status"))
    check("store: handback report is the subagent's last text", agent_child.get("last_text") == "Tests written, 4 pass.")
    wf = tree[1] if len(tree) > 1 else {}
    wkid = wf.get("children", [{}])[0] if wf.get("children") else {}
    check("store: workflow agent linked by run id", wkid.get("agent_id") == "w1" and wkid.get("phase") == "Review", str(wkid))
    check("store: journal 'failed' wins", wkid.get("status") == "failed", wkid.get("status"))
    sub_cost = pricing.message_cost("claude-sonnet-5-5", 3, 500, 10_000, 0, 0) + pricing.message_cost("claude-sonnet-5-5", 3, 80, 10_000, 0, 0)
    wf_cost = pricing.message_cost("claude-haiku-5-5", 1000, 10, 0, 0, 0)
    main_cost = sum(pricing.message_cost("claude-opus-5-5", *u) for u in [(10, 200, 50_000, 1_000, 2_000), (5, 40, 0, 0, 0), (5, 40, 0, 0, 0), (5, 60, 0, 0, 0), (5, 30, 0, 0, 0), (1, 1, 0, 0, 0)])
    check("store: session cost = main + subagents", abs(r["cost"] - (main_cost + sub_cost + wf_cost)) < 1e-4, f"{r['cost']} vs {main_cost + sub_cost + wf_cost}")
    check("store: unpriced tokens surfaced", r["unpriced_tokens"] == 200)
    check("store: guard event attributed from activity.jsonl", r["guard"] == 1 and d["guard"][0]["kind"] == "deny", str(d["guard"]))
    check("store: errors total (tool + API)", r["errors"] == 3, str(r["errors"]))
    check("store: title from ai-title", r["title"] == "Fix login bug")
    # test_parser appended a Grep call and its result, so the model holds the turn.
    check("store: live state 'working' after a tool result", r["state"] == "working", r["state"])
    with open(MAIN, "a") as fh:
        fh.write(json.dumps(amsg(T0 + 95, "msg_8", "claude-opus-5-5", [{"type": "text", "text": "Fixed."}], usage(1, 5), stop="end_turn")) + "\n")
    st.refresh(force=True)
    check("store: live state 'waiting' after end_turn", st.session_payload(SID)["session"]["state"] == "waiting")
    main_cost += pricing.message_cost("claude-opus-5-5", 1, 5, 0, 0, 0)
    live = st.live_payload()
    check("live: fresh session listed", [x["id"] for x in live["sessions"]] == [SID])
    ov = st.overview_payload()
    fams = {m["family"] for m in ov["models"]}
    check("overview: model mix has opus, sonnet, haiku", {"opus", "sonnet", "haiku"} <= fams, str(fams))
    check("overview: subagent mix counts", sorted((x["family"], x["count"]) for x in ov["sub_mix"]) == [("haiku", 1), ("sonnet", 1)], str(ov["sub_mix"]))
    check("overview: cost total matches buckets", abs(ov["totals"]["cost"] - round(main_cost + sub_cost + wf_cost, 4)) < 1e-3, str(ov["totals"]))
    a = st.agent_payload(SID, "abc123")
    check("agent payload: events and usage", a and len(a["events"]) >= 2 and a["usage"][0]["name"] == "Sonnet 5.5")
    check("agent payload: unknown agent -> None", st.agent_payload(SID, "nope") is None)
    # Stale: push every mtime back 20 minutes -> not live any more.
    old = time.time() - 1200
    for p in [MAIN, SUB, os.path.join(WFDIR, "agent-w1.jsonl")]:
        os.utime(p, (old, old))
    st.refresh(force=True)
    check("live: stale session drops out", st.live_payload()["sessions"] == [])
    check("live: stale subagent not 'running'", all(x.status != "running" for x in st.sessions[SID].subs))
    now = time.time()
    for p in [MAIN, SUB, os.path.join(WFDIR, "agent-w1.jsonl")]:
        os.utime(p, (now, now))
    st2 = Store(root=CFG, days=30, project="nomatch")
    st2.refresh(force=True)
    check("store: --project filter", st2.sessions == {})
    st3 = Store(root=CFG, days=30)
    st3.refresh(force=True)
    check("store: wider window includes the old session", len(st3.sessions) == 2)


def _req(port, path, host=None, method="GET"):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.putrequest(method, path, skip_host=True)
    c.putheader("Host", host or f"127.0.0.1:{port}")
    c.endheaders()
    r = c.getresponse()
    body = r.read()
    hdrs = dict(r.getheaders())
    c.close()
    return r.status, hdrs, body


def test_server():
    st = Store(root=CFG, days=1)
    srv = make_server(0, st)
    port = srv.port
    th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    th.start()
    auto_cookie(srv)
    try:
        s, h, b = _req(port, "/")
        check("server: index served", s == 200 and b"<title>cc-dash</title>" in b and "Content-Security-Policy" in h)
        check("server: no CORS header", "Access-Control-Allow-Origin" not in h)
        for path in ("/app.js", "/app.css"):
            s, _, _ = _req(port, path)
            check(f"server: {path} served", s == 200)
        s, _, b = _req(port, "/api/sessions")
        rows = json.loads(b)["sessions"] if s == 200 else []
        check("server: /api/sessions", s == 200 and rows and rows[0]["id"] == SID)
        for path in ("/api/meta", "/api/live", "/api/overview", f"/api/session?id={SID}", f"/api/agent?session={SID}&id=abc123"):
            s, h, b = _req(port, path)
            ok = s == 200 and h.get("Content-Type", "").startswith("application/json") and json.loads(b) is not None
            check(f"server: {path.split('?')[0]} json", ok, f"{s}")
        s, _, _ = _req(port, "/api/session?id=missing")
        check("server: unknown session -> 404", s == 404)
        s, _, _ = _req(port, "/api/sessions", host="evil.example.com")
        check("server: foreign Host rejected (DNS rebinding)", s == 421)
        s, _, _ = _req(port, "/api/sessions", host=f"evil.example.com:{port}")
        check("server: foreign Host with port rejected", s == 421)
        s, _, _ = _req(port, "/", host=f"localhost:{port}")
        check("server: localhost Host accepted", s == 200)
        s, _, _ = _req(port, "/", host="127.0.0.1:1")
        check("server: wrong port in Host rejected", s == 421)
        for path in ("/../../../../etc/passwd", "/%2e%2e/%2e%2e/etc/passwd", "/..%2f..%2fetc%2fpasswd", "/app.js/../../server.py",
                     "/server.py", "/web/app.js", "//etc/passwd"):
            s, _, b = _req(port, path)
            check(f"server: traversal {path} -> 404", s == 404 and b"root:" not in b, str(s))
        s, _, _ = _req(port, "/api/sessions", method="POST")
        check("server: POST refused", s == 405)
        s, _, _ = _req(port, "/api/sessions", method="DELETE")
        check("server: DELETE refused", s == 405)
        check("server: bound to loopback only", srv.server_address[0] == "127.0.0.1")
        # Port fallback: asking for the busy port yields a different free one.
        srv2 = make_server(port, st)
        check("server: busy port falls back to a free one", srv2.port != port and srv2.port > 0)
        srv2.server_close()
    finally:
        srv.shutdown()
        srv.server_close()


def test_cli():
    p = subprocess.run([PY, os.path.join(REPO, "bin", "cc-dash"), "--help"], capture_output=True, text=True, timeout=30)
    check("cli: --help exits 0 and names the flags", p.returncode == 0 and all(f in p.stdout for f in ("--port", "--no-open", "--days", "--project")), p.stderr[-200:])
    p = subprocess.run([PY, os.path.join(REPO, "bin", "cc-dash"), "--days", "0"], capture_output=True, text=True, timeout=30)
    check("cli: --days 0 rejected", p.returncode != 0)


def test_activity_logs():
    """The temp-dir fallback is shared with other users: only our own, private files count."""
    tmp = tempfile.mkdtemp(prefix="ccdash-act-")
    saved = tempfile.tempdir
    try:
        tempfile.tempdir = tmp
        root = os.path.join(tmp, "wt")  # a worktree: no .git directory
        os.makedirs(root)
        import hashlib
        base = os.path.join(tmp, "cc_tool", hashlib.sha1(root.encode()).hexdigest()[:12])
        os.makedirs(base, mode=0o755)
        os.chmod(base, 0o755)
        log = os.path.join(base, "activity.jsonl")
        open(log, "w").write("{}\n")
        os.chmod(log, 0o644)
        check("activity: private temp log is read", activity_logs(root) == [log], str(activity_logs(root)))
        os.chmod(log, 0o666)
        check("activity: world-writable temp log is ignored", activity_logs(root) == [])
        os.chmod(log, 0o644)
        os.chmod(base, 0o777)
        check("activity: world-writable temp dir is ignored", activity_logs(root) == [])
        os.chmod(base, 0o755)
        os.remove(log)
        os.symlink(os.path.join(tmp, "elsewhere.jsonl"), log)
        open(os.path.join(tmp, "elsewhere.jsonl"), "w").write("{}\n")
        check("activity: symlinked temp log is ignored", activity_logs(root) == [])
    finally:
        tempfile.tempdir = saved
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    try:
        for fn in (test_pricing, test_parser, test_store, test_activity_logs, test_server, test_cli):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                check(fn.__name__ + " raised", False, f"{type(e).__name__}: {e}")
        after = snapshot(BASE)
        # The tests themselves append to MAIN and touch mtimes; the dashboard must add no file.
        check("read-only: cc-dash created no files", set(after) == set(BEFORE), str(set(after) ^ set(BEFORE)))
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
    fails = [r for r in RESULTS if not r[1]]
    print("total=%d  pass=%d  fail=%d" % (len(RESULTS), len(RESULTS) - len(fails), len(fails)))
    for name, _, detail in fails:
        print("  FAIL %-55s %s" % (name, detail[:200]))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
