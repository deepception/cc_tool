#!/usr/bin/env python3
"""templates/hooks/dash-control.py: cc-dash control mode, hook side.

Runs the hook as Claude Code does (a subprocess fed JSON on stdin) with HOME,
XDG_STATE_HOME and CLAUDE_CONFIG_DIR in a temp dir, and plays the dashboard by
hand (lease, decision files). Checks lease gating (missing, expired, dead pid,
unsafe modes, symlinks), the request/decision handoff for permissions,
questions and plans, release, self-exit when the prompt is answered in the
terminal (attention.py's log, the session registry), lease loss, the timeout,
safe-file refusals, bash-guard's reason without a second log line, message
delivery (mid-turn, at a stop, at the next prompt, subagent targeting, one
delivery under parallel hooks), the settings.json registrations and their
shell guard, and the added latency per tool call with and without a dashboard.

Run:  python3 tests/test_control_hook.py
"""
import json
import os
import re
import shutil
import stat
import statistics
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "templates", "hooks", "dash-control.py")
SETTINGS = os.path.join(REPO, "templates", "settings.json")
RESULTS = []
BASE = tempfile.mkdtemp(prefix="cc-control-hook-")
SID = "0f0e0d0c-1111-2222-3333-444455556666"
AGENT = "a1b2c3d4e5f6a7b8c"
TIMINGS = {}


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), str(detail)))


class Box:
    """A temp HOME with the control dir a running cc-dash --control would make."""
    n = 0

    def __init__(self, lease=True, wait=300):
        Box.n += 1
        self.root = os.path.join(BASE, "box%d" % Box.n)
        self.home = os.path.join(self.root, "home")
        self.state = os.path.join(self.root, "state")
        self.cfg = os.path.join(self.root, "claude")
        self.ctl = os.path.join(self.state, "cc_tool", "control")
        for d in (self.home, os.path.join(self.cfg, "sessions")):
            os.makedirs(d)
        os.makedirs(os.path.join(self.state, "cc_tool"), mode=0o700)
        os.makedirs(self.ctl, mode=0o700)
        for sub in ("req", "dec", "res", "msg", "dlv", "asked", "sock", "stop", "stopped"):
            os.mkdir(os.path.join(self.ctl, sub), 0o700)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
        self.env.update(HOME=self.home, XDG_STATE_HOME=self.state, CLAUDE_CONFIG_DIR=self.cfg)
        if lease:
            self.lease(wait=wait)

    def lease(self, expires=None, pid=None, wait=300, mode=0o600):
        p = os.path.join(self.ctl, "lease")
        if os.path.lexists(p):
            os.unlink(p)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        os.write(fd, json.dumps({"v": 1, "pid": pid or os.getpid(), "launch": "test", "port": 1,
                                 "expires": expires if expires is not None else time.time() + 3600, "wait": wait}).encode())
        os.close(fd)
        os.chmod(p, mode)

    def path(self, *p):
        return os.path.join(self.ctl, *p)

    def write(self, rel, obj, mode=0o600):
        p = self.path(rel)
        fd = os.open(p + ".t", os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        os.write(fd, json.dumps(obj).encode())
        os.close(fd)
        os.chmod(p + ".t", mode)
        os.replace(p + ".t", p)

    def reqs(self):
        return sorted(n for n in os.listdir(self.path("req")) if n.endswith(".json"))

    def wait_req(self, timeout=8):
        end = time.time() + timeout
        while time.time() < end:
            r = self.reqs()
            if r:
                with open(self.path("req", r[0])) as fh:
                    return json.load(fh)
            time.sleep(0.03)
        return None

    def res(self, rid, timeout=3):
        end = time.time() + timeout
        p = self.path("res", rid + ".json")
        while time.time() < end:
            if os.path.exists(p):
                try:
                    with open(p) as fh:
                        return json.load(fh)
                except ValueError:
                    pass
            time.sleep(0.03)
        return None

    def attention(self, **rec):
        p = os.path.join(self.state, "cc_tool", "attention.jsonl")
        r = {"v": 1, "ts": round(time.time(), 3), "session": SID, "agent": None}
        r.update(rec)
        fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        os.write(fd, (json.dumps(r) + "\n").encode())
        os.close(fd)

    def run(self, payload, raw=None, timeout=20):
        data = raw if raw is not None else json.dumps(payload).encode()
        t0 = time.perf_counter()
        p = subprocess.run([sys.executable, HOOK], input=data, env=self.env, capture_output=True, timeout=timeout)
        return p, (time.perf_counter() - t0) * 1000

    def start(self, payload):
        p = subprocess.Popen([sys.executable, HOOK], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=self.env)
        p.stdin.write(json.dumps(payload).encode())
        p.stdin.close()
        p.stdin = None
        return p


def base(ev, **kw):
    d = {"session_id": SID, "transcript_path": "/p/t.jsonl", "cwd": BASE, "hook_event_name": ev,
         "permission_mode": "default"}
    d.update(kw)
    return d


def perm(tool="Bash", ti=None, **kw):
    return base("PermissionRequest", tool_name=tool, tool_input=ti if ti is not None else {"command": "ls -la", "description": "list"}, **kw)


def finish(p, timeout=5):
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        out, err = p.communicate()
        return None, out, err
    return p.returncode, out, err


def jout(out):
    try:
        return json.loads(out or b"null")
    except ValueError:
        return None


# ── lease gating ───────────────────────────────────────────────────────────
def test_gating():
    b = Box(lease=False)
    p, ms = b.run(perm())
    check("no lease: exit 0, silent, fast", p.returncode == 0 and p.stdout == b"" and ms < 2000, (p.returncode, p.stdout, ms))
    check("no lease: no request written", b.reqs() == [])
    open(b.path("pending"), "w").close()
    b.run(base("PostToolUse", tool_name="Bash", tool_input={"command": "ls"}))
    check("no lease: leftover pending flag removed", not os.path.exists(b.path("pending")))

    for label, kw in (("expired", {"expires": time.time() - 1}), ("dead pid", {"pid": 2 ** 22 + 12345}),
                      ("lease 0644", {"mode": 0o644})):
        b = Box(lease=False)
        b.lease(**kw)
        p, ms = b.run(perm())
        check("lease %s: no-op" % label, p.returncode == 0 and p.stdout == b"" and b.reqs() == [] and ms < 3000, (p.stdout, ms))

    b = Box(lease=False)
    real = os.path.join(b.root, "elsewhere")
    with open(real, "w") as fh:
        json.dump({"pid": os.getpid(), "expires": time.time() + 99}, fh)
    os.chmod(real, 0o600)
    os.symlink(real, b.path("lease"))
    p, _ = b.run(perm())
    check("lease symlink: not followed, no-op", p.stdout == b"" and b.reqs() == [])

    b = Box()
    os.chmod(b.ctl, 0o755)
    p, _ = b.run(perm())
    check("control dir 0755: no-op", p.stdout == b"" and b.reqs() == [])

    b = Box()
    for raw in (b"", b"not json", b"[1,2]", b"\xff\xfe"):
        p, _ = b.run(None, raw=raw)
        check("garbage stdin %r: exit 0, silent" % raw[:8], p.returncode == 0 and p.stdout == b"" and p.stderr == b"")


# ── permission handoff ─────────────────────────────────────────────────────
def test_allow_deny():
    b = Box()
    t0 = time.time()
    p = b.start(perm(agent_id=None))
    r = b.wait_req()
    check("allow: request written", r is not None, r)
    if r is None:
        p.kill()
        return
    TIMINGS["request file after start (ms)"] = round((time.time() - t0) * 1000)
    st = os.stat(b.path("req", r["id"] + ".json"))
    check("allow: request file 0600", stat.S_IMODE(st.st_mode) == 0o600, oct(st.st_mode))
    check("allow: request fields", r["session"] == SID and r["tool"] == "Bash" and r["kind"] == "permission"
          and r["input"]["command"] == "ls -la" and r["sig"] == "ls -la" and r["guard"] == ""
          and 299 < r["deadline"] - r["ts"] < 301 and r["agent_id"] is None and isinstance(r["pid"], int)
          and re.match(r"^[0-9a-f]{64}$", r["hash"]) and r["ask_rule"] == "", r)
    check("allow: request id shape", re.match(r"^[0-9a-f]{6,16}-[0-9a-f]{12}$", r["id"]), r["id"])
    time.sleep(0.3)
    check("allow: hook still waiting", p.poll() is None)
    t1 = time.time()
    b.write("dec/%s.json" % r["id"], {"id": r["id"], "hash": r["hash"], "action": "allow"})
    rc, out, _ = finish(p)
    TIMINGS["decision file -> hook exit (ms)"] = round((time.time() - t1) * 1000)
    o = jout(out)
    check("allow: prints the allow decision", rc == 0 and o == {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                                                                       "decision": {"behavior": "allow"}}}, out)
    check("allow: no updatedInput for a command", "updatedInput" not in json.dumps(o))
    check("allow: exits within 1 s of the decision", time.time() - t1 < 1.0, time.time() - t1)
    res = b.res(r["id"])
    check("allow: request removed, outcome applied", b.reqs() == [] and res and res["outcome"] == "applied" and res["action"] == "allow", res)
    check("allow: decision file consumed", not os.path.exists(b.path("dec", r["id"] + ".json")))

    b = Box()
    p = b.start(perm(agent_id=AGENT, agent_type="Explore"))
    r = b.wait_req()
    check("subagent: agent fields in the request", r and r["agent_id"] == AGENT and r["agent_type"] == "Explore", r)
    b.write("dec/%s.json" % r["id"], {"id": r["id"], "hash": r["hash"], "action": "deny", "message": "use the Makefile target"})
    rc, out, _ = finish(p)
    o = jout(out) or {}
    dec = (o.get("hookSpecificOutput") or {}).get("decision") or {}
    check("deny: behavior deny with the reason for Claude", dec.get("behavior") == "deny" and "use the Makefile target" in dec.get("message", "")
          and "cc-dash" in dec.get("message", ""), out)
    check("deny: outcome applied", (b.res(r["id"]) or {}).get("outcome") == "applied")

    b = Box()
    p = b.start(perm())
    r = b.wait_req()
    b.write("dec/%s.json" % r["id"], {"id": "ffffffffffff-000000000000", "hash": r["hash"], "action": "allow"})
    time.sleep(0.6)
    check("decision for another id: ignored, still waiting", p.poll() is None)
    b.write("dec/%s.json" % r["id"], {"id": r["id"], "hash": "0" * 64, "action": "allow"})
    time.sleep(0.6)
    check("decision with another call's hash: ignored, still waiting", p.poll() is None)
    b.write("dec/%s.json" % r["id"], {"id": r["id"], "action": "allow"})
    time.sleep(0.6)
    check("decision without a hash: ignored, still waiting", p.poll() is None)
    b.write("dec/%s.json" % r["id"], {"id": r["id"], "hash": r["hash"], "action": "answer", "answers": {"x": "y"}})
    time.sleep(0.6)
    check("an action that does not fit the kind: ignored, still waiting", p.poll() is None)
    b.write("dec/%s.json" % r["id"], {"id": r["id"], "hash": r["hash"], "action": "release"})
    rc, out, _ = finish(p)
    check("release: silent exit", rc == 0 and out == b"", out)
    check("release: outcome released", (b.res(r["id"]) or {}).get("outcome") == "released")

    b = Box()
    p = b.start(perm())
    r = b.wait_req()
    b.write("dec/%s.json" % r["id"], {"id": r["id"], "hash": r["hash"], "action": "release", "why": "terminal"})
    rc, out, _ = finish(p)
    check("release (terminal, from claude agents): silent, outcome terminal", out == b"" and (b.res(r["id"]) or {}).get("outcome") == "terminal")


def test_question_plan():
    qs = [{"question": "Which color?", "header": "Color", "options": [{"label": "Red"}, {"label": "Blue"}], "multiSelect": False},
          {"question": "Which sizes?", "header": "Size", "options": [{"label": "S"}, {"label": "M"}], "multiSelect": True}]
    b = Box()
    p = b.start(perm("AskUserQuestion", {"questions": qs}))
    r = b.wait_req()
    check("question: kind question, questions in the request", r and r["kind"] == "question" and r["input"]["questions"] == qs, r)
    b.write("dec/%s.json" % r["id"], {"id": r["id"], "hash": r["hash"], "action": "answer", "answers": {"Not a question": "x"}})
    time.sleep(0.6)
    check("question: answers to unknown questions ignored, still waiting", p.poll() is None)
    b.write("dec/%s.json" % r["id"], {"id": r["id"], "hash": r["hash"], "action": "answer", "answers": {"Which color?": "Blue", "Which sizes?": "S, M"}})
    rc, out, _ = finish(p)
    dec = ((jout(out) or {}).get("hookSpecificOutput") or {}).get("decision") or {}
    up = dec.get("updatedInput") or {}
    check("question: allow with the original questions echoed", dec.get("behavior") == "allow" and up.get("questions") == qs, out)
    check("question: answers by question text, multi-select comma-joined", up.get("answers") == {"Which color?": "Blue", "Which sizes?": "S, M"}, up)

    plan = {"plan": "# Refactor\n1. Split the module\n2. Add tests", "planFilePath": "/tmp/p.md"}
    b = Box()
    p = b.start(perm("ExitPlanMode", plan))
    r = b.wait_req()
    check("plan: kind plan, plan text in the request", r and r["kind"] == "plan" and r["input"]["plan"].startswith("# Refactor"), r)
    b.write("dec/%s.json" % r["id"], {"id": r["id"], "hash": r["hash"], "action": "allow"})
    rc, out, _ = finish(p)
    dec = ((jout(out) or {}).get("hookSpecificOutput") or {}).get("decision") or {}
    check("plan approve: allow with the input echoed unchanged", dec.get("behavior") == "allow" and dec.get("updatedInput") == plan, out)

    b = Box()
    p = b.start(perm("ExitPlanMode", plan))
    r = b.wait_req()
    b.write("dec/%s.json" % r["id"], {"id": r["id"], "hash": r["hash"], "action": "deny", "message": "keep the public API"})
    rc, out, _ = finish(p)
    dec = ((jout(out) or {}).get("hookSpecificOutput") or {}).get("decision") or {}
    check("plan reject: deny carrying the feedback", dec.get("behavior") == "deny" and "keep the public API" in dec.get("message", "")
          and "rejected the plan" in dec.get("message", ""), out)

    big = {"file_path": "/x/big.txt", "content": "z" * 400000}
    b = Box()
    p = b.start(perm("Write", big))
    r = b.wait_req()
    check("huge input: request keeps a shortened copy and says so", r and r["input_truncated"] and len(r["input"]["content"]) < 5000
          and r["sig"] == "/x/big.txt", (r or {}).get("input_truncated"))
    b.write("dec/%s.json" % r["id"], {"id": r["id"], "hash": r["hash"], "action": "release"})
    finish(p)


# ── answered in the terminal ───────────────────────────────────────────────
def test_terminal():
    cases = [
        ("PostToolUse of the same call", {}, dict(event="PostToolUse", tool="Bash", sig="ls -la")),
        ("PostToolUseFailure of the same call", {}, dict(event="PostToolUseFailure", tool="Bash", sig="ls -la")),
        ("PostToolBatch (a denial)", {}, dict(event="PostToolBatch")),
        ("UserPromptSubmit (main thread)", {}, dict(event="UserPromptSubmit")),
        ("SubagentStop of that subagent", {"agent_id": AGENT}, dict(event="SubagentStop", agent=AGENT)),
        ("SessionEnd", {}, dict(event="SessionEnd")),
    ]
    for label, kw, rec in cases:
        b = Box()
        p = b.start(perm(**kw))
        r = b.wait_req()
        b.attention(event="PostToolUse", tool="Bash", sig="something else")          # another call: no effect
        b.attention(session="other-session", **{k: v for k, v in rec.items()})      # another session: no effect
        time.sleep(0.5)
        still = p.poll() is None
        t1 = time.time()
        b.attention(**rec)
        rc, out, _ = finish(p)
        dt = time.time() - t1
        res = b.res(r["id"]) or {}
        check("terminal: %s -> silent exit within 1 s" % label, still and rc == 0 and out == b"" and dt < 1.0
              and res.get("outcome") == "terminal", (still, out, round(dt, 2), res))
        TIMINGS.setdefault("terminal answer -> hook exit, attention log (ms)", []).append(round(dt * 1000))

    b = Box()
    p = b.start(perm(agent_id=AGENT))
    b.wait_req()
    b.attention(event="PostToolBatch", agent="someone-else")
    b.attention(event="UserPromptSubmit")
    time.sleep(0.6)
    check("terminal: a subagent's prompt ignores other agents' batches and main-thread prompts", p.poll() is None)
    p.kill()
    p.communicate()

    b = Box()
    reg = os.path.join(b.cfg, "sessions", "4242.json")

    def register(status):
        with open(reg + ".t", "w") as fh:
            json.dump({"pid": 4242, "sessionId": SID, "status": status, "statusUpdatedAt": int(time.time() * 1000)}, fh)
        os.replace(reg + ".t", reg)
    register("busy")
    p = b.start(perm())
    r = b.wait_req()
    time.sleep(1.2)
    check("registry: busy before the prompt does not count", p.poll() is None)
    register("waiting")
    time.sleep(0.5)
    check("registry: waiting keeps it waiting", p.poll() is None)
    t1 = time.time()
    register("busy")
    rc, out, _ = finish(p)
    dt = time.time() - t1
    check("registry: waiting -> busy exits silently within 1 s", out == b"" and dt < 1.0 and (b.res(r["id"]) or {}).get("outcome") == "terminal", round(dt, 2))
    TIMINGS["terminal answer -> hook exit, registry (ms)"] = round(dt * 1000)


def test_lease_loss_timeout_signal():
    b = Box()
    p = b.start(perm())
    r = b.wait_req()
    t1 = time.time()
    os.unlink(b.path("lease"))
    rc, out, _ = finish(p, timeout=5)
    dt = time.time() - t1
    check("lease gone: silent exit within 1.5 s", out == b"" and dt < 1.5 and (b.res(r["id"]) or {}).get("outcome") == "lease", round(dt, 2))
    TIMINGS["lease removed -> hook exit (ms)"] = round(dt * 1000)

    b = Box(wait=5)
    t0 = time.time()
    p = b.start(perm())
    r = b.wait_req()
    check("timeout: the lease's wait bounds the deadline", r and abs(r["deadline"] - r["ts"] - 5) < 0.1, r and r["deadline"] - r["ts"])
    rc, out, _ = finish(p, timeout=10)
    dt = time.time() - t0
    check("timeout: silent exit after about 5 s", out == b"" and 4.5 < dt < 7 and (b.res(r["id"]) or {}).get("outcome") == "timeout", round(dt, 2))
    check("timeout: request removed", b.reqs() == [])

    b = Box()
    p = b.start(perm())
    r = b.wait_req()
    p.terminate()                                  # Claude Code kills a hook at its timeout
    finish(p)
    check("SIGTERM: request removed and an outcome written", b.reqs() == [] and b.res(r["id"]) is not None)


def test_safe_files():
    b = Box()
    p = b.start(perm())
    r = b.wait_req()
    target = os.path.join(b.root, "evil.json")
    with open(target, "w") as fh:
        json.dump({"id": r["id"], "action": "allow"}, fh)
    os.chmod(target, 0o600)
    os.symlink(target, b.path("dec", r["id"] + ".json"))
    time.sleep(0.6)
    check("decision symlink: not followed", p.poll() is None)
    os.unlink(b.path("dec", r["id"] + ".json"))
    b.write("dec/%s.json" % r["id"], {"id": r["id"], "hash": r["hash"], "action": "allow"}, mode=0o644)
    time.sleep(0.6)
    check("decision readable by others (0644): ignored", p.poll() is None)
    os.unlink(b.path("dec", r["id"] + ".json"))
    os.chmod(b.path("dec"), 0o700)
    b.write("dec/%s.json" % r["id"], {"id": r["id"], "hash": r["hash"], "action": "allow"})
    rc, out, _ = finish(p)
    check("then a proper 0600 decision applies", b"allow" in out)

    b = Box()
    os.chmod(b.path("req"), 0o755)
    p, _ = b.run(perm())
    check("req dir not private: no request, silent", p.stdout == b"" and b.reqs() == [])


def test_guard_reason():
    repo = os.path.join(BASE, "repo")
    os.makedirs(os.path.join(repo, ".git"), exist_ok=True)
    guard = os.path.join(REPO, "templates", "hooks", "bash-guard.py")
    b = Box()
    b.env["CLAUDE_PROJECT_DIR"] = repo
    pre = base("PreToolUse", tool_name="Bash", tool_input={"command": "rm -rf build"}, cwd=repo)
    g = subprocess.run([sys.executable, guard], input=json.dumps(pre).encode(), env=b.env, capture_output=True, timeout=10)
    check("bash-guard: asks about rm -rf build", b'"ask"' in g.stdout, g.stdout[:200])
    marks = os.listdir(b.path("asked"))
    check("bash-guard ask with a fresh lease: leaves one 0600 marker", len(marks) == 1
          and stat.S_IMODE(os.stat(b.path("asked", marks[0])).st_mode) == 0o600, marks)
    p = b.start(perm(ti={"command": "rm -rf build"}, cwd=repo))
    r = b.wait_req()
    check("bash-guard ask: its reason is in the request", r and "rm" in r["guard"] and r["guard"].startswith("NEEDS APPROVAL"), (r or {}).get("guard"))
    check("bash-guard ask: the marker is not consumed on read (it expires by age)", len(os.listdir(b.path("asked"))) == 1)
    p2 = b.start(perm(ti={"command": "rm -rf build"}, cwd=repo))
    end = time.time() + 8
    r2 = None
    while time.time() < end and r2 is None:
        for n in b.reqs():
            d2 = json.load(open(b.path("req", n)))
            if d2["id"] != r["id"]:
                r2 = d2
        time.sleep(0.05)
    check("bash-guard ask: a parallel identical call is flagged too", r2 and r2["guard"].startswith("NEEDS APPROVAL"), (r2 or {}).get("guard"))
    if r2:
        b.write("dec/%s.json" % r2["id"], {"id": r2["id"], "hash": r2["hash"], "action": "release"})
    finish(p2)
    check("bash-guard ask: no allow-for-session offered", r and r["session_rules"] == [], (r or {}).get("session_rules"))
    b.write("dec/%s.json" % r["id"], {"id": r["id"], "hash": r["hash"], "action": "allow_session"})
    time.sleep(0.6)
    check("bash-guard ask: allow_session refused by the hook, still waiting", p.poll() is None)
    b.write("dec/%s.json" % r["id"], {"id": r["id"], "hash": r["hash"], "action": "release"})
    finish(p)

    b2 = Box(lease=False)
    b2.env["CLAUDE_PROJECT_DIR"] = repo
    subprocess.run([sys.executable, guard], input=json.dumps(pre).encode(), env=b2.env, capture_output=True, timeout=10)
    check("bash-guard ask without a dashboard: no marker", os.listdir(b2.path("asked")) == [])

    p = b.start(perm(ti={"command": "ls"}, cwd=repo))
    r = b.wait_req()
    check("plain command: no guard reason", r and r["guard"] == "", (r or {}).get("guard"))
    b.write("dec/%s.json" % r["id"], {"id": r["id"], "hash": r["hash"], "action": "release"})
    finish(p)

    sys.path.insert(0, os.path.join(REPO, "templates", "hooks"))
    import importlib.util
    import cc_hooklib
    spec = importlib.util.spec_from_file_location("dash_control", HOOK)
    dc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dc)
    check("marker key: cc_hooklib and dash-control agree", cc_hooklib.guard_ask_key(SID, "rm -rf x") == dc.guard_key(SID, "rm -rf x"))


def test_ask_rule_and_session():
    proj = os.path.join(BASE, "askproj")
    os.makedirs(os.path.join(proj, ".claude"), exist_ok=True)
    with open(os.path.join(proj, ".claude", "settings.json"), "w") as fh:
        json.dump({"permissions": {"ask": ["Bash(npm install*)", "Bash(git push:*)", "WebFetch(domain:example.com)"]}}, fh)
    b = Box()
    b.env["CLAUDE_PROJECT_DIR"] = proj
    sugg = [{"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "ln *"}], "behavior": "allow", "destination": "localSettings"},
            {"type": "setMode", "mode": "acceptEdits", "destination": "session"}]
    for cmd, want in (("npm install left-pad", "Bash(npm install*)"), ("cd x && git push origin main", "Bash(git push:*)"),
                      ("ln -s a b", "")):
        p = b.start(perm(ti={"command": cmd}, cwd=proj, permission_suggestions=sugg))
        r = b.wait_req()
        check("ask rule: %r -> %r" % (cmd, want), r and r["ask_rule"] == want, (r or {}).get("ask_rule"))
        if want:
            check("ask rule: no allow-for-session for %r" % cmd, r["session_rules"] == [])
            b.write("dec/%s.json" % r["id"], {"id": r["id"], "hash": r["hash"], "action": "release"})
            finish(p)
            continue
        check("session rules: only Claude Code's addRules suggestion, shown as text", r["session_rules"] == ["Bash(ln *)"], r["session_rules"])
        b.write("dec/%s.json" % r["id"], {"id": r["id"], "hash": r["hash"], "action": "allow_session"})
        rc, out, _ = finish(p)
        dec = ((jout(out) or {}).get("hookSpecificOutput") or {}).get("decision") or {}
        check("allow_session: allow + updatedPermissions with destination session only, never setMode",
              dec.get("behavior") == "allow" and dec.get("updatedPermissions") == [
                  {"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "ln *"}], "behavior": "allow", "destination": "session"}], out)
    for cmd, label in (("FOO=1 npm install left-pad", "an env prefix"), ("(cd sub && npm install x)", "a subshell"),
                       ("npm\tinstall   x", "odd whitespace"), ("env CI=1 timeout 60 npm install y", "wrappers"),
                       ("sh -c 'npm install z'", "sh -c code"), ("true; echo a | xargs npm install", "an xargs handoff"),
                       ("ls\nnpm install w", "a newline")):
        p = b.start(perm(ti={"command": cmd}, cwd=proj))
        r = b.wait_req()
        check("ask rule through bash-guard's parser: %s still matches" % label, r and r["ask_rule"] == "Bash(npm install*)", (cmd, (r or {}).get("ask_rule")))
        if r:
            b.write("dec/%s.json" % r["id"], {"id": r["id"], "hash": r["hash"], "action": "release"})
        finish(p)
    for cmd, label in (("eval \"$CMD\"", "eval"), ("$TOOL install x", "a computed command name"), ("echo 'unbalanced", "unbalanced quotes")):
        p = b.start(perm(ti={"command": cmd}, cwd=proj))
        r = b.wait_req()
        check("ask rule fails closed on %s (flagged, not silently clean)" % label, r and r["ask_rule"].startswith("(not checked"), (cmd, (r or {}).get("ask_rule")))
        if r:
            b.write("dec/%s.json" % r["id"], {"id": r["id"], "hash": r["hash"], "action": "release"})
        finish(p)
    with open(os.path.join(proj, ".claude", "settings.local.json"), "w") as fh:
        fh.write("{ not json")
    p = b.start(perm(ti={"command": "ls"}, cwd=proj))
    r = b.wait_req()
    check("ask rule fails closed on an unreadable settings file", r and r["ask_rule"].startswith("(not checked: could not read"), (r or {}).get("ask_rule"))
    if r:
        b.write("dec/%s.json" % r["id"], {"id": r["id"], "hash": r["hash"], "action": "release"})
    finish(p)
    os.unlink(os.path.join(proj, ".claude", "settings.local.json"))
    p = b.start(perm("WebFetch", {"url": "https://api.example.com/x", "prompt": "p"}, cwd=proj))
    r = b.wait_req()
    check("ask rule: WebFetch(domain:...) matches a subdomain", r and r["ask_rule"] == "WebFetch(domain:example.com)", (r or {}).get("ask_rule"))
    b.write("dec/%s.json" % r["id"], {"id": r["id"], "hash": r["hash"], "action": "release"})
    finish(p)


def test_stop_and_socket():
    b = Box()
    b.write("stop/%s.main.json" % SID, {"v": 1, "session": SID, "agent_id": None, "ts": time.time()})
    open(b.path("pending"), "w").close()
    p, _ = b.run(base("PreToolUse", tool_name="Bash", tool_input={"command": "ls"}, agent_id=AGENT))
    check("stop: a subagent's tool call is not the main thread's", p.stdout == b"")
    p, _ = b.run(base("PreToolUse", tool_name="Bash", tool_input={"command": "ls"}))
    o = jout(p.stdout) or {}
    check("stop: next tool call denied with continue:false", o.get("continue") is False and
          (o.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny", p.stdout)
    d = json.load(open(b.path("stopped", "%s.main.json" % SID)))
    check("stop: recorded as stopped at that tool", d.get("outcome") == "stopped" and d.get("tool") == "Bash" and d.get("applied_ts"), d)
    p, _ = b.run(base("PreToolUse", tool_name="Bash", tool_input={"command": "ls"}))
    check("stop: applies once", p.stdout == b"")
    b.write("stop/%s.main.json" % SID, {"v": 1, "session": SID, "agent_id": None, "ts": time.time()})
    p, _ = b.run(base("Stop", stop_hook_active=False))
    d = json.load(open(b.path("stopped", "%s.main.json" % SID)))
    check("stop: a turn that ends first records 'turn ended' and prints nothing", p.stdout == b"" and d.get("outcome") == "turn ended", d)

    b = Box()
    b.env["CLAUDE_CODE_MESSAGING_SOCKET"] = "/run/user/1000/cc-socks/123.sock"
    b.env["CLAUDE_CODE_MESSAGING_TOKEN"] = "secret-token-value"
    p, _ = b.run(base("SessionStart", source="startup"))
    path = b.path("sock", SID + ".json")
    d = json.load(open(path)) if os.path.exists(path) else {}
    check("SessionStart: records the inbox socket path, 0600", d.get("socket") == "/run/user/1000/cc-socks/123.sock" and d.get("session") == SID
          and stat.S_IMODE(os.stat(path).st_mode) == 0o600 and p.stdout == b"", d)
    check("SessionStart: never stores the messaging token", "secret-token-value" not in open(path).read())


# ── messages ───────────────────────────────────────────────────────────────
def queue(b, text, agent=None, mid=None):
    mid = mid or "%x%s" % (int(time.time() * 1000), os.urandom(4).hex())
    b.write("msg/%s.%s.%s.json" % (SID, agent or "main", mid), {"v": 1, "id": mid, "session": SID, "agent_id": agent, "text": text, "ts": time.time()})
    open(b.path("pending"), "a").close()
    return mid


def test_messages():
    b = Box()
    mid = queue(b, "also run the linter")
    p, ms = b.run(base("PostToolUse", tool_name="Bash", tool_input={"command": "ls"}, tool_response={}))
    o = jout(p.stdout) or {}
    hso = o.get("hookSpecificOutput") or {}
    check("PostToolUse: delivers as additionalContext", hso.get("hookEventName") == "PostToolUse" and "also run the linter" in hso.get("additionalContext", "")
          and "cc-dash" in hso.get("additionalContext", ""), p.stdout)
    dl = os.path.join(b.ctl, "dlv", "%s.main.%s.json" % (SID, mid))
    d = json.load(open(dl)) if os.path.exists(dl) else {}
    check("PostToolUse: message moved to dlv with delivery info", d.get("via") == "PostToolUse" and d.get("delivered_ts") and d.get("tool") == "Bash", d)
    check("PostToolUse: dlv file 0600", os.path.exists(dl) and stat.S_IMODE(os.stat(dl).st_mode) == 0o600)
    p2, _ = b.run(base("PostToolUse", tool_name="Bash", tool_input={"command": "ls"}))
    check("PostToolUse: delivered once", p2.stdout == b"")

    b = Box()
    queue(b, "for the subagent", agent=AGENT)
    p, _ = b.run(base("PostToolUse", tool_name="Read", tool_input={"file_path": "/x"}))
    check("subagent message: not delivered to the main thread", p.stdout == b"")
    p, _ = b.run(base("PostToolUse", tool_name="Read", tool_input={"file_path": "/x"}, agent_id="someoneelse1"))
    check("subagent message: not delivered to another subagent", p.stdout == b"")
    p, _ = b.run(base("Stop", stop_hook_active=False))
    check("subagent message: not delivered by the main Stop", p.stdout == b"")
    p, _ = b.run(base("SubagentStop", agent_id=AGENT, agent_type="Explore", stop_hook_active=False))
    hso = (jout(p.stdout) or {}).get("hookSpecificOutput") or {}
    check("SubagentStop of that subagent: additionalContext with the message", hso.get("hookEventName") == "SubagentStop" and "for the subagent" in hso.get("additionalContext", ""), p.stdout)

    b = Box()
    queue(b, "one more thing")
    queue(b, "and another")
    p, _ = b.run(base("Stop", stop_hook_active=False))
    ctx = ((jout(p.stdout) or {}).get("hookSpecificOutput") or {}).get("additionalContext", "")
    check("Stop: additionalContext (not decision:block) with every queued message, oldest first",
          "decision" not in (jout(p.stdout) or {}) and ctx.find("one more thing") < ctx.find("and another") and "and another" in ctx, p.stdout)
    p, _ = b.run(base("Stop", stop_hook_active=True))
    check("Stop after the continuation: queue empty, stops normally", p.stdout == b"")

    b = Box()
    queue(b, "with the prompt")
    p, _ = b.run(base("UserPromptSubmit", prompt="hi"))
    hso = (jout(p.stdout) or {}).get("hookSpecificOutput") or {}
    check("UserPromptSubmit: additionalContext", hso.get("hookEventName") == "UserPromptSubmit" and "with the prompt" in hso.get("additionalContext", ""), p.stdout)

    b = Box()
    queue(b, "exactly once")
    procs = [subprocess.Popen([sys.executable, HOOK], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=b.env) for _ in range(6)]
    payload = json.dumps(base("PostToolUse", tool_name="Bash", tool_input={"command": "ls"})).encode()
    outs = [pp.communicate(payload, timeout=10)[0] for pp in procs]
    check("parallel PostToolUse hooks: exactly one delivers", sum(1 for x in outs if b"exactly once" in x) == 1, [len(x) for x in outs])

    b = Box()
    queue(b, "x" * 10, mid="deadbeef01")
    os.chmod(b.path("msg", "%s.main.deadbeef01.json" % SID), 0o644)
    p, _ = b.run(base("PostToolUse", tool_name="Bash", tool_input={"command": "ls"}))
    check("message file readable by others: not delivered", p.stdout == b"")

    b = Box()
    queue(b, "unsafe id")
    p, _ = b.run(base("PostToolUse", session_id="../../etc", tool_name="Bash", tool_input={"command": "ls"}))
    check("a session id with path characters: ignored", p.stdout == b"")

    b = Box(lease=False)
    queue(b, "no dashboard")
    p, _ = b.run(base("PostToolUse", tool_name="Bash", tool_input={"command": "ls"}))
    check("no lease: queued messages stay queued, nothing printed", p.stdout == b"" and len(os.listdir(b.path("msg"))) == 1)


# ── settings.json and latency ──────────────────────────────────────────────
def registered():
    with open(SETTINGS) as fh:
        hooks = json.load(fh)["hooks"]
    out = {}
    for ev, entries in hooks.items():
        for e in entries:
            for h in e["hooks"]:
                if "dash-control.py" in h.get("command", ""):
                    out[ev] = (e, h)
    return out


def test_settings():
    reg = registered()
    want = {"PermissionRequest", "SessionStart", "PreToolUse", "PostToolUse", "PostToolUseFailure", "UserPromptSubmit", "Stop", "SubagentStop"}
    check("settings: registered for exactly its events", set(reg) == want, sorted(reg))
    e, h = reg.get("PermissionRequest", ({}, {}))
    check("settings: PermissionRequest synchronous, no matcher, timeout 600 (above the 590 s longest wait), no onFailure",
          not h.get("async") and "matcher" not in e and h.get("timeout") == 600 and "onFailure" not in h, h)
    for ev in ("PermissionRequest", "SessionStart"):
        e, h = reg.get(ev, ({}, {}))
        check("settings: %s gated on the lease file" % ev, "control/lease" in h.get("command", ""), h.get("command"))
    for ev in want - {"PermissionRequest", "SessionStart"}:
        e, h = reg.get(ev, ({}, {}))
        check("settings: %s synchronous, every tool, gated on the pending flag" % ev,
              not h.get("async") and "matcher" not in e and "control/pending" in h.get("command", ""), h)
    rx = re.compile(r"(?:\$\{?CLAUDE_PROJECT_DIR\}?\"?/|(?<![\w~./-])(?:\./)?)\.claude/hooks/([^\s\"']+)")
    check("settings: cc-setup recognises the script as cc_tool's", all((rx.search(h["command"]) or [None, None])[1] == "dash-control.py" for _, h in reg.values()))
    check("settings: every command ends in || true (never a hook error)", all(h["command"].rstrip().endswith("|| true") for _, h in reg.values()))


def sh_cmd(b, ev):
    proj = os.path.join(b.root, "proj")
    hooks = os.path.join(proj, ".claude", "hooks")
    if not os.path.isdir(hooks):
        os.makedirs(os.path.dirname(hooks))
        shutil.copytree(os.path.join(REPO, "templates", "hooks"), hooks)
    env = dict(b.env, CLAUDE_PROJECT_DIR=proj)
    return registered()[ev][1]["command"], env


def timed_runs(cmd, env, payload, n=15):
    data = json.dumps(payload).encode()
    ts = []
    out = b""
    for _ in range(n):
        t0 = time.perf_counter()
        p = subprocess.run(["sh", "-c", cmd], input=data, env=env, capture_output=True, timeout=20)
        ts.append((time.perf_counter() - t0) * 1000)
        out = p.stdout
        if p.returncode != 0:
            return None, p
    return statistics.median(ts), out


def test_latency():
    b = Box(lease=False)
    cmd, env = sh_cmd(b, "PostToolUse")
    pt = base("PostToolUse", tool_name="Bash", tool_input={"command": "ls"}, tool_response={"stdout": "x"})
    off, out = timed_runs(cmd, env, pt)
    check("latency: shell guard with no dashboard exits 0 and prints nothing", off is not None and out == b"", out)
    b.lease()
    on_idle, out = timed_runs(cmd, env, pt)
    check("latency: dashboard on, nothing queued: still only the shell test", on_idle is not None and out == b"")
    open(b.path("pending"), "w").close()
    on_inbox, out = timed_runs(cmd, env, pt)
    check("latency: inbox flagged, nothing for this session: python runs, prints nothing", on_inbox is not None and out == b"")
    bare = statistics.median([_t(lambda: subprocess.run([sys.executable, "-c", "pass"], capture_output=True)) for _ in range(15)])
    cmdp, envp = sh_cmd(b, "PermissionRequest")
    os.unlink(b.path("lease"))
    perm_off, out = timed_runs(cmdp, envp, perm())
    check("latency: PermissionRequest with no dashboard: shell test only, silent", perm_off is not None and out == b"")
    TIMINGS["PostToolUse, no dashboard (ms, median)"] = round(off, 1)
    TIMINGS["PostToolUse, dashboard on, nothing queued (ms, median)"] = round(on_idle, 1)
    TIMINGS["PostToolUse, something queued elsewhere, python runs (ms, median)"] = round(on_inbox, 1)
    TIMINGS["PermissionRequest, no dashboard (ms, median)"] = round(perm_off, 1)
    TIMINGS["bare python3 -c pass (ms, median)"] = round(bare, 1)
    check("latency: no dashboard costs under 10 ms per tool call", off < 10, off)
    check("latency: dashboard on with nothing queued costs under 10 ms per tool call", on_idle < 10, on_idle)
    check("latency: with python it stays within 30 ms of a bare interpreter", on_inbox < bare + 30, (on_inbox, bare))


def _t(fn):
    t0 = time.perf_counter()
    fn()
    return (time.perf_counter() - t0) * 1000


def main():
    try:
        for fn in (test_gating, test_allow_deny, test_question_plan, test_terminal, test_lease_loss_timeout_signal,
                   test_safe_files, test_guard_reason, test_ask_rule_and_session, test_stop_and_socket, test_messages,
                   test_settings, test_latency):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                check(fn.__name__ + " raised", False, f"{type(e).__name__}: {e}")
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
    for k, v in TIMINGS.items():
        print("  timing: %-55s %s" % (k, v))
    fails = [r for r in RESULTS if not r[1]]
    print("total=%d  pass=%d  fail=%d" % (len(RESULTS), len(RESULTS) - len(fails), len(fails)))
    for name, _, detail in fails:
        print("  FAIL %-60s %s" % (name, detail[:1500]))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
