#!/usr/bin/env python3
"""Project grouping, the project filter list, project-scoped Overview, and
timeline order for bin/cc-dash (lib/cc_dash/).

Builds a fake ${CLAUDE_CONFIG_DIR}/projects tree in a temp dir with sessions
in several projects:
  alpha                         a main checkout (.git directory), two sessions
  alpha/.claude/worktrees/wt1   a Claude Code worktree of alpha
  alpha-s4-wt/web               a sibling checkout of alpha that no longer exists
  alpha-tools                   a separate repo whose name starts with "alpha-"
  beta                          a main checkout with a guard log
  linked                        a linked git worktree of beta (.git file)
  gamma                         a plain directory, no git
  one/app and two/app           two repos with the same base name
Then it checks how sessions are grouped, the /api/sessions project list,
/api/overview?project= scoping (totals equal the sum of that project's
sessions), that the query value is only a filter (traversal-looking values
match nothing and never fail), the per-project numbers, and the timeline
order (newest first, runs of tool calls reversed inside, the handed-back
report on top). Nothing outside the temp dir is touched.

Run:  python3 tests/test_cc_dash_projects.py
      python3 tests/test_cc_dash_projects.py --fixture DIR   (only write the tree, for a browser check)
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
from urllib.parse import quote

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "lib"))
from cc_dash.server import make_server  # noqa: E402
from dash_pairing import auto_cookie  # noqa: E402  (tests/dash_pairing.py: every request carries the pairing cookie)
from cc_dash.store import Store, resolve_projects  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def usage(inp, out, cread=0):
    return {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cread,
            "cache_creation_input_tokens": 0, "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0}}


def write_session(cfg, cwd, sid, t0, model="claude-opus-5-5", tools=3, errors=0, title=None, mtime=None, sub=None):
    """One main transcript: a prompt, `tools` Bash calls (the first `errors` fail), a closing reply.
    sub=(agent_id, model) adds an Agent spawn and its subagent transcript ending in a handback."""
    pdir = os.path.join(cfg, "projects", cwd.replace("/", "-"))
    os.makedirs(pdir, exist_ok=True)

    def rec(kind, t, **kw):
        r = {"type": kind, "timestamp": iso(t), "sessionId": sid, "cwd": cwd, "gitBranch": "main",
             "version": "2.1.300", "entrypoint": "cli", "isSidechain": False, "uuid": f"{sid}-{t}"}
        r.update(kw)
        return r

    lines = [rec("user", t0, origin={"kind": "human"}, message={"role": "user", "content": [{"type": "text", "text": title or ("Work in " + cwd)}]})]
    t = t0 + 1
    for i in range(tools):
        tid = f"tu_{sid[:4]}_{i}"
        lines.append(rec("assistant", t, message={"id": f"m_{sid[:4]}_{i}", "model": model, "role": "assistant", "stop_reason": "tool_use",
                                                  "content": [{"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": f"echo {i}"}}],
                                                  "usage": usage(10, 100, cread=1000)}))
        lines.append(rec("user", t + 1, message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": "ok", "is_error": i < errors}]}))
        t += 2
    if sub:
        aid, smodel = sub
        lines.append(rec("assistant", t, message={"id": f"m_{sid[:4]}_ag", "model": model, "role": "assistant", "stop_reason": "tool_use",
                                                  "content": [{"type": "tool_use", "id": "tu_ag_" + aid, "name": "Agent",
                                                               "input": {"description": "Helper", "subagent_type": "general-purpose", "prompt": "help"}}],
                                                  "usage": usage(5, 50)}))
        lines.append(rec("user", t + 30, message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_ag_" + aid, "content": "done"}]},
                         toolUseResult={"status": "completed", "agentId": aid}))
        sdir = os.path.join(pdir, sid, "subagents")
        os.makedirs(sdir, exist_ok=True)
        with open(os.path.join(sdir, f"agent-{aid}.jsonl"), "w") as fh:
            srec = lambda kind, tt, **kw: rec(kind, tt, isSidechain=True, agentId=aid, **kw)  # noqa: E731
            fh.write(json.dumps(srec("user", t + 1, message={"role": "user", "content": "help"})) + "\n")
            fh.write(json.dumps(srec("assistant", t + 2, message={"id": f"s_{aid}_1", "model": smodel, "role": "assistant", "stop_reason": "tool_use",
                                                                   "content": [{"type": "tool_use", "id": f"st_{aid}_1", "name": "Read", "input": {"file_path": "/x"}}],
                                                                   "usage": usage(3, 30, cread=500)})) + "\n")
            fh.write(json.dumps(srec("user", t + 3, message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"st_{aid}_1", "content": "x"}]})) + "\n")
            fh.write(json.dumps(srec("assistant", t + 4, message={"id": f"s_{aid}_2", "model": smodel, "role": "assistant", "stop_reason": "tool_use",
                                                                   "content": [{"type": "tool_use", "id": f"st_{aid}_2", "name": "SubagentHandback", "input": {"message": "All done: the report."}}],
                                                                   "usage": usage(3, 30)})) + "\n")
        json.dump({"agentType": "general-purpose", "description": "Helper", "toolUseId": "tu_ag_" + aid}, open(os.path.join(sdir, f"agent-{aid}.meta.json"), "w"))
        t += 31
    lines.append(rec("assistant", t, message={"id": f"m_{sid[:4]}_end", "model": model, "role": "assistant", "stop_reason": "end_turn",
                                              "content": [{"type": "text", "text": "Done."}], "usage": usage(10, 20)}))
    path = os.path.join(pdir, sid + ".jsonl")
    with open(path, "w") as fh:
        for ln in lines:
            fh.write(json.dumps(ln) + "\n")
    if mtime:
        os.utime(path, (mtime, mtime))
    return path


def build_fixture(base, now=None):
    """Write the fake tree under base; return (cfg dir, {name: path}, session ids by project)."""
    now = now or time.time()
    cfg = os.path.join(base, "claude")
    work = os.path.join(base, "work")
    paths = {n: os.path.join(work, n) for n in ("alpha", "alpha-tools", "beta", "linked", "gamma")}
    paths["wt1"] = os.path.join(paths["alpha"], ".claude", "worktrees", "wt1")
    paths["sib"] = os.path.join(work, "alpha-s4-wt", "web")          # never created: a deleted worktree
    paths["app1"] = os.path.join(work, "one", "app")
    paths["app2"] = os.path.join(work, "two", "app")
    for n in ("alpha", "alpha-tools", "beta", "app1", "app2"):
        os.makedirs(os.path.join(paths[n], ".git"), exist_ok=True)
    os.makedirs(os.path.join(paths["beta"], ".git", "worktrees", "linked"), exist_ok=True)
    os.makedirs(paths["linked"], exist_ok=True)
    with open(os.path.join(paths["linked"], ".git"), "w") as fh:
        fh.write("gitdir: " + os.path.join(paths["beta"], ".git", "worktrees", "linked") + "\n")
    os.makedirs(paths["gamma"], exist_ok=True)
    os.makedirs(paths["wt1"], exist_ok=True)
    t = now - 3000
    ids = {}

    def add(proj, cwd_key, sid, **kw):
        write_session(cfg, paths[cwd_key], sid, kw.pop("t0", t), **kw)
        ids.setdefault(proj, []).append(sid)

    add("alpha", "alpha", "aaaaaaaa-0000-0000-0000-000000000001", tools=4, errors=1, sub=("sub1", "claude-sonnet-5-5"))
    add("alpha", "alpha", "aaaaaaaa-0000-0000-0000-000000000002", tools=2, model="claude-sonnet-5-5", t0=t + 100)
    add("alpha", "wt1", "aaaaaaaa-0000-0000-0000-000000000003", tools=1, t0=t + 200)
    add("alpha", "sib", "aaaaaaaa-0000-0000-0000-000000000004", tools=2, errors=2, model="claude-haiku-5-5", t0=t + 300)
    add("alpha-tools", "alpha-tools", "cccccccc-0000-0000-0000-000000000001", tools=1, t0=t + 400)
    add("beta", "beta", "bbbbbbbb-0000-0000-0000-000000000001", tools=5, t0=t + 500)
    add("beta", "linked", "bbbbbbbb-0000-0000-0000-000000000002", tools=1, t0=t + 600)
    add("gamma", "gamma", "dddddddd-0000-0000-0000-000000000001", tools=2, t0=t + 700)
    add("one/app", "app1", "eeeeeeee-0000-0000-0000-000000000001", tools=1, t0=t + 800)
    add("two/app", "app2", "eeeeeeee-0000-0000-0000-000000000002", tools=1, t0=t + 900)
    os.makedirs(os.path.join(paths["beta"], ".git", "cc_tool"), exist_ok=True)
    with open(os.path.join(paths["beta"], ".git", "cc_tool", "activity.jsonl"), "w") as fh:
        fh.write(json.dumps({"ts": t + 500 + 2, "kind": "deny", "tool": "Bash", "hook": "bash-guard", "command": "rm -rf /", "message": "no"}) + "\n")
        fh.write(json.dumps({"ts": t + 500 + 4, "kind": "ask", "tool": "Bash", "hook": "bash-guard", "command": "git push", "message": "ask"}) + "\n")
    return cfg, paths, ids


def get(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path, headers={"Host": f"127.0.0.1:{port}"})
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, body


def main():
    base = tempfile.mkdtemp(prefix="cc-dash-proj-")
    try:
        run(base)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    fails = [r for r in RESULTS if not r[1]]
    for name, ok, detail in RESULTS:
        print(("PASS " if ok else "FAIL ") + name + ("" if ok or not detail else "  -- " + detail))
    print(f"total={len(RESULTS)}  pass={len(RESULTS) - len(fails)}  fail={len(fails)}")
    return 1 if fails else 0


def run(base):
    cfg, paths, ids = build_fixture(base)

    # ── grouping rule ─────────────────────────────────────────────────────
    where, labels = resolve_projects(set(paths.values()) | {""})
    lab = lambda k: labels[where[paths[k]][0]]  # noqa: E731
    wt = lambda k: where[paths[k]][1]  # noqa: E731
    check("group: main checkout is its own project", lab("alpha") == "alpha" and wt("alpha") == "")
    check("group: .claude/worktrees/<name> folds into the repo", lab("wt1") == "alpha" and wt("wt1") == "wt1", str(where[paths["wt1"]]))
    check("group: deleted sibling checkout <repo>-s4-wt/web folds into the repo", lab("sib") == "alpha" and wt("sib") == "s4-wt/web", str(where[paths["sib"]]))
    check("group: a separate repo named alpha-tools stays separate", lab("alpha-tools") == "alpha-tools" and wt("alpha-tools") == "")
    check("group: linked git worktree (.git file) folds into its repo", lab("linked") == "beta" and wt("linked") == "linked", str(where[paths["linked"]]))
    check("group: plain directory is its own project", lab("gamma") == "gamma")
    check("group: same base name in two places gets the parent prepended", {lab("app1"), lab("app2")} == {"one/app", "two/app"}, f"{lab('app1')} {lab('app2')}")
    check("group: empty cwd is 'unknown'", labels[where[""][0]] == "unknown")

    st = Store(root=cfg, days=7)
    st.refresh(force=True)
    sp = st.sessions_payload()
    plist = {p["project"]: p for p in sp["projects"]}
    check("sessions api: one entry per project, sorted by name",
          [p["project"] for p in sp["projects"]] == sorted(plist, key=str.lower) and set(plist) == {"alpha", "alpha-tools", "beta", "gamma", "one/app", "two/app"},
          str(list(plist)))
    a = plist.get("alpha", {})
    check("sessions api: alpha counts its worktrees", a.get("sessions") == 4 and a.get("main") == 2
          and [(w["name"], w["sessions"]) for w in a.get("worktrees", [])] == [("s4-wt/web", 1), ("wt1", 1)], json.dumps(a))
    check("sessions api: rows carry project and worktree", all(r["project"] in plist for r in sp["sessions"])
          and {r["worktree"] for r in sp["sessions"] if r["project"] == "alpha"} == {"", "wt1", "s4-wt/web"})
    check("sessions api: counts add up to the session total", sum(p["sessions"] for p in sp["projects"]) == len(sp["sessions"]) == 10)

    # ── scoped overview: totals equal the sum of that project's session rows ──
    rows = sp["sessions"]
    all_ov = st.overview_payload()
    check("overview: unscoped covers every session", all_ov["totals"]["sessions"] == 10 and all_ov["scope"] == {"project": "", "known": True})
    check("overview: lists every project, not a top 8", {p["project"] for p in all_ov["projects"]} == set(plist))
    for proj in ("alpha", "beta", "gamma"):
        ov = st.overview_payload(proj)
        mine = [r for r in rows if r["project"] == proj]
        t = ov["totals"]
        exp = {"sessions": len(mine), "cost": round(sum(r["cost"] for r in mine), 4), "tokens": sum(r["tokens"] for r in mine),
               "tool_calls": sum(r["tool_calls_all"] for r in mine), "tool_errors": sum(r["tool_errors"] for r in mine),
               "subagents": sum(r["subagents"] for r in mine), "prompts": sum(r["prompts"] for r in mine)}
        got = {k: t[k] for k in exp}
        check(f"overview?project={proj}: totals equal the sum of its sessions", all(abs(got[k] - exp[k]) < 1e-6 for k in exp), f"{got} vs {exp}")
        check(f"overview?project={proj}: only that project in the table", [p["project"] for p in ov["projects"]] == [proj] and ov["scope"]["known"])
        hours_cost = sum(c for hh in ov["hours"] for c in hh["c"].values())
        check(f"overview?project={proj}: cost chart sums to the scoped cost", abs(hours_cost - t["cost"]) < 1e-3, f"{hours_cost} {t['cost']}")
        check(f"overview?project={proj}: project picker list stays complete", {p["project"] for p in ov["project_list"]} == set(plist))
    sums = {k: sum(p[k] for p in all_ov["projects"]) for k in ("sessions", "tool_calls", "tokens")}
    check("overview: per-project rows add up to the totals", all(sums[k] == all_ov["totals"][k] for k in sums), f"{sums}")

    # ── per-project math ──────────────────────────────────────────────────
    pa = next(p for p in all_ov["projects"] if p["project"] == "alpha")
    # session 1: 4 Bash + 1 Agent, its subagent Read + SubagentHandback; then 2, 1 and 2 Bash calls
    check("project: tool calls across main, subagent and worktree sessions", pa["tool_calls"] == 5 + 2 + 2 + 1 + 2, str(pa["tool_calls"]))
    check("project: error rate = failed / calls", pa["tool_errors"] == 3 and abs(pa["error_rate"] - round(3 / pa["tool_calls"], 4)) < 1e-9, f"{pa['tool_errors']} {pa['error_rate']}")
    check("project: subagents counted", pa["subagents"] == 1)
    check("project: model mix has Opus, Sonnet and Haiku tokens", set(pa["fam_tokens"]) == {"opus", "sonnet", "haiku"} and all(v > 0 for v in pa["fam_tokens"].values()), str(pa["fam_tokens"]))
    check("project: model mix sums to its tokens", sum(pa["fam_tokens"].values()) == pa["tokens"], f"{sum(pa['fam_tokens'].values())} {pa['tokens']}")
    check("project: per-hour cost sums to its cost", abs(sum(c for _, c in pa["cost_hours"]) - pa["cost"]) < 1e-3)
    check("project: active time is the sum of its sessions", abs(pa["active"] - round(sum(r["active"] for r in rows if r["project"] == "alpha"), 1)) < 0.2)
    check("project: last active is the newest session", abs(pa["last_active"] - max(r["last_write"] for r in rows if r["project"] == "alpha")) < 1e-6)
    wts = {w["worktree"]: w for w in pa["worktrees"]}
    check("project: worktrees folded under it (main, wt1, s4-wt/web)", set(wts) == {"", "wt1", "s4-wt/web"}, str(list(wts)))
    check("project: worktree rows add up to the project", sum(w["sessions"] for w in wts.values()) == pa["sessions"]
          and abs(sum(w["cost"] for w in wts.values()) - pa["cost"]) < 1e-3)
    check("project: a failing worktree has its own error rate", wts.get("s4-wt/web", {}).get("error_rate") == 1.0)
    pb = next(p for p in all_ov["projects"] if p["project"] == "beta")
    check("project: guard catches attributed to beta", pb["guard"] == 2 and pb["guard_kinds"]["deny"] == 1 and pb["guard_kinds"]["ask"] == 1, json.dumps(pb["guard_kinds"]))
    pg = next(p for p in all_ov["projects"] if p["project"] == "gamma")
    check("project: no worktrees means nothing folded", pg["worktrees"] == [])

    # ── HTTP: the query value is a filter only ────────────────────────────
    srv = make_server(0, Store(root=cfg, days=7), fallback=False)
    port = srv.port
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
    auto_cookie(srv)
    try:
        code, body = get(port, "/api/overview?project=beta")
        d = json.loads(body)
        check("http: /api/overview?project=beta scopes", code == 200 and d["totals"]["sessions"] == 2 and d["scope"]["project"] == "beta")
        code, body = get(port, "/api/sessions")
        check("http: /api/sessions has the project list", code == 200 and len(json.loads(body)["projects"]) == 6)
        for raw in ("../../etc/passwd", "/etc/passwd", "..%2F..%2Fetc", "%00", "beta%00", "%2e%2e%2f", "<script>alert(1)</script>",
                    "a" * 5000, "%E2%80%AE", "%ff%fe", "beta&project=alpha", "C:%5Cwindows"):
            code, body = get(port, "/api/overview?project=" + (raw if "%" in raw else quote(raw, safe="&=")))
            try:
                d = json.loads(body)
            except ValueError:
                d = {}
            ok = code == 200 and d.get("totals", {}).get("sessions", -1) >= 0 and len(d.get("scope", {}).get("project", "")) <= 200
            if raw.startswith("beta&"):
                ok = ok and d["scope"]["project"] == "beta"     # first value wins
            else:
                ok = ok and d["totals"]["sessions"] == 0 and d["scope"]["known"] is False
            check(f"http: project={raw[:24]!r} is just a non-matching filter", ok, f"{code} {body[:120]!r}")
        code, body = get(port, "/api/overview?project=")
        check("http: empty project means all projects", code == 200 and json.loads(body)["totals"]["sessions"] == 10)
        for path, want in (("/dom.js", 200), ("/timeline.js", 200), ("/patch.js", 404), ("/../store.py", 404)):
            code, _ = get(port, path)
            check(f"http: GET {path} -> {want}", code == want, str(code))
        code, body = get(port, "/api/agent?session=aaaaaaaa-0000-0000-0000-000000000001&id=sub1")
        ev = json.loads(body)["events"] if code == 200 else []
        ts = [e.get("t") or 0 for e in ev]
        check("http: agent events come oldest first, ending with the handed-back report",
              ev and ts == sorted(ts) and ev[-1].get("k") == "text" and ev[-1].get("report"), json.dumps(ev[-1:])[:200])
    finally:
        srv.shutdown()
        srv.server_close()

    # ── the DOM gets text only ────────────────────────────────────────────
    web = os.path.join(REPO, "lib", "cc_dash", "web")
    for f in ("app.js", "dom.js", "timeline.js"):
        src = open(os.path.join(web, f), encoding="utf-8").read()
        check(f"web: {f} never writes HTML strings", "innerHTML" not in src and "outerHTML" not in src and "insertAdjacentHTML" not in src)

    # ── timeline order (pure JS in web/timeline.js, run in node when present) ──
    node = shutil.which("node")
    if not node:
        check("timeline: node not installed, order checks skipped", True)
        return
    events = [
        {"k": "prompt", "t": 1, "text": "go"},
        {"k": "tool", "t": 2, "name": "Read", "id": "a"},
        {"k": "tool", "t": 3, "name": "Grep", "id": "b"},
        {"k": "tool", "t": 4, "name": "Bash", "id": "c", "status": "error"},
        {"k": "text", "t": 5, "text": "thinking"},
        {"k": "tool", "t": 6, "name": "Agent", "id": "d", "spawn": True},
        {"k": "tool", "t": 7, "name": "Edit", "id": "e"},
        {"k": "text", "t": 8, "text": "All done: the report.", "report": True},
    ]
    script = (
        "require(process.argv[1]);"
        "const T = globalThis.ccTimeline; const ev = JSON.parse(process.argv[2]);"
        "const n = T.arrange(ev, {order: 'newest'}); const o = T.arrange(ev, {order: 'oldest'});"
        "const lim = T.arrange(ev, {order: 'newest', limit: 3}); const limO = T.arrange(ev, {order: 'oldest', limit: 3});"
        "const more = T.arrange(ev.concat([{k: 'text', t: 9, text: 'later'}]), {order: 'newest'});"
        "const p = T.arrange(ev, {order: 'newest', mode: 'problems'});"
        "const brief = (r) => r.shown.map((it) => it.type === 'group' ? 'g' + it.key + ':' + it.calls.map((c) => c.id).join('') : it.type + it.key);"
        "console.log(JSON.stringify({n: brief(n), o: brief(o), lim: brief(lim), limHidden: lim.hidden, limO: brief(limO), more: brief(more), p: brief(p),"
        " gt: n.shown.find((it) => it.type === 'group').t}));"
    )
    r = subprocess.run([node, "-e", script, os.path.join(REPO, "lib", "cc_dash", "web", "timeline.js"), json.dumps(events)],
                       capture_output=True, text=True, timeout=30)
    try:
        out = json.loads(r.stdout)
    except ValueError:
        out = {}
    check("timeline: node ran web/timeline.js", r.returncode == 0 and out, r.stderr[-300:])
    if not out:
        return
    check("timeline: newest first puts the latest event (the report) on top", out["n"][0] == "text7", str(out["n"]))
    check("timeline: newest first reverses the items", out["n"] == ["text7", "g6:e", "tool5", "text4", "g1:cba", "prompt0"], str(out["n"]))
    check("timeline: calls inside a run run newest first too", "g1:cba" in out["n"] and out["gt"] == 7, f"{out['n']} {out['gt']}")
    check("timeline: oldest first keeps transcript order", out["o"] == ["prompt0", "g1:abc", "text4", "tool5", "g6:e", "text7"], str(out["o"]))
    check("timeline: the limit keeps the newest entries and counts the older ones", out["lim"] == ["text7", "g6:e", "tool5"] and out["limHidden"] == 3, str(out["lim"]))
    check("timeline: oldest first with a limit still shows the newest entries", out["limO"] == ["tool5", "g6:e", "text7"], str(out["limO"]))
    check("timeline: an appended event lands on top and keeps the others' keys", out["more"] == ["text8"] + out["n"], str(out["more"]))
    check("timeline: problems filter keeps the failing run", out["p"] == ["g1:cba"], str(out["p"]))


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--fixture":
        os.makedirs(sys.argv[2], exist_ok=True)
        cfg, _, ids = build_fixture(sys.argv[2])
        print(json.dumps({"config": cfg, "ids": ids}))
        sys.exit(0)
    sys.exit(main())
