#!/usr/bin/env python3
"""cc-dash control mode and pairing: lib/cc_dash/auth.py, control.py, server.py.

Starts real servers on 127.0.0.1 over a fake ${CLAUDE_CONFIG_DIR} and a temp
XDG_STATE_HOME. Checks pairing (single use, 60 s expiry, POST-only exchange
with Origin and Content-Type checks, no cookie ever), that every /api/* needs
the X-CC-Dash-Token in both modes while the static shell does not, that a
token is worthless on another port and never re-issued, the 0600 pairing file
and an opener argv without the code, the write checks (token, Origin,
Sec-Fetch-Site, Content-Type, X-CC-Dash-Token, Host, body size), 404 for every
control path without --control, the lease and directory modes, decision
writing and its hash, the typed confirm for flagged requests, allow-for-session
rules, the approval rate limit, outcomes read back from the hook, the audit log,
the message queue (queued, delivered, seen, cancel, the pending flag), the
inbox-socket transport against a real Unix socket, Stop, the `claude agents
--json` release, alert annotation, a full round trip with the real hook, and
gates from the dashboard (gates.py): both gates end to end through a real
cc-factory child, every refusal (a read-only token, the typed word, a stale
hash, a moved lane, a replayed POST, no /proc), what the child gets (argv,
environment, inherited fds, a ticket that works once), the real CLI refusing a
server that is not cc-dash, and the card in a browser (skipped without node,
playwright-core and Chrome). tests/factory_gate_cases.py has the CLI's side.

Run:  python3 tests/test_cc_dash_control.py
"""
import http.client
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The interpreter is the kernel's: with zsh's exported ARGV0 (Cursor's terminal), sys.executable
# can name the editor's AppImage, and running it opens editor tabs instead of Python.
PY = os.path.realpath("/proc/self/exe") if os.path.exists("/proc/self/exe") else sys.executable
sys.path.insert(0, os.path.join(REPO, "lib"))
from cc_dash import auth as auth_mod  # noqa: E402
from cc_dash.control import Control  # noqa: E402
from cc_dash.server import make_server, start_hook_channel  # noqa: E402
from hook_channel import hook_call  # noqa: E402  (tests/hook_channel.py: the hook side of the hook socket)
from cc_dash.store import Store  # noqa: E402
import gate_fixture  # noqa: E402  (tests/gate_fixture.py: a tree whose cc_procs passes the test process as a dashboard)

HOOK = os.path.join(REPO, "templates", "hooks", "dash-control.py")
CLI = os.path.join(REPO, "bin", "cc-factory")
RESULTS = []
BASE = tempfile.mkdtemp(prefix="cc-dash-control-")
SID = "aaaaaaaa-1111-2222-3333-444444444444"
SUB = "a0123456789abcdef"


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), str(detail)))


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def transcript(cfg, sid, cwd, recs, sub=None):
    pdir = os.path.join(cfg, "projects", cwd.replace("/", "-"))
    path = os.path.join(pdir, sid + ".jsonl") if sub is None else os.path.join(pdir, sid, "subagents", "agent-%s.jsonl" % sub)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")


def assistant(sid, cwd, t, text, mid, **kw):
    r = {"type": "assistant", "timestamp": iso(t), "sessionId": sid, "cwd": cwd, "uuid": "u" + mid,
         "message": {"id": mid, "model": "claude-haiku-5-5", "role": "assistant", "content": [{"type": "text", "text": text}],
                     "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}}}
    r.update(kw)
    return r


class Srv:
    """A cc-dash server (optionally in control mode) with its own state dir."""
    n = 0

    def __init__(self, control=True, agents_cmd=None, sockets=True, port=0, hook_check=False):
        Srv.n += 1
        self.root = os.path.join(BASE, "s%d" % Srv.n)
        self.cfg = os.path.join(self.root, "claude")
        self.state = os.path.join(self.root, "state")
        os.makedirs(os.path.join(self.cfg, "projects"), exist_ok=True)
        self.cwd = "/work/demo"
        now = time.time()
        transcript(self.cfg, SID, self.cwd, [{"type": "user", "timestamp": iso(now - 60), "sessionId": SID, "cwd": self.cwd,
                                               "uuid": "p1", "message": {"role": "user", "content": "hello"}},
                                              assistant(SID, self.cwd, now - 59, "hi", "m1")])
        transcript(self.cfg, SID, self.cwd, [assistant(SID, self.cwd, now - 50, "sub hi", "s1", isSidechain=True, agentId=SUB)], sub=SUB)
        self.store = Store(root=self.cfg, days=7)
        self.store.refresh(force=True)
        self.srv = make_server(port, self.store, fallback=True)
        self.port = self.srv.port
        self.ctl = None
        if control:
            self.ctl = Control(self.store, self.port, ctl_dir=os.path.join(self.state, "cc_tool", "control"),
                               agents_cmd=agents_cmd, sockets=sockets, hook_check=hook_check)
            self.hook = start_hook_channel(self.ctl)          # the hook's channel, <control dir>/hook.sock
            self.ctl.start(thread=False)
            self.srv.control = self.ctl
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.token = None

    def path(self, *p):
        return os.path.join(self.ctl.dir, *p)

    def pair(self, code=None, control=True):
        """Pair (a read-only token); in control mode also type the control code, as
        the human does, and keep the control token."""
        st, out = self.post("/api/pair", {"code": code or self.srv.auth.new_code()}, token=False)
        self.token = out.get("token") if isinstance(out, dict) else None
        self.read_token = self.token
        if control and self.ctl is not None and st == 200:
            if self.srv.auth.control_code is None:
                self.control_code = self.srv.auth.enable_control()
            st2, out2 = self.post("/api/control/unlock", {"code": self.control_code})
            if st2 == 200:
                self.token = out2["token"]
        return st, out

    def req(self, method, path, body=None, token=True, headers=None, raw=None, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": host or "127.0.0.1:%d" % self.port}
        if token and self.token:
            h["X-CC-Dash-Token"] = self.token
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            h.update({"Content-Type": "application/json", "Origin": "http://127.0.0.1:%d" % self.port,
                      "Sec-Fetch-Site": "same-origin"})
        h.update(headers or {})
        h = {k: v for k, v in h.items() if v is not None}
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        out = r.read()
        c.close()
        return r.status, dict(r.getheaders()), out

    def post(self, path, body, **kw):
        st, _, out = self.req("POST", path, body, **kw)
        try:
            return st, json.loads(out)
        except ValueError:
            return st, out

    def state_(self):
        return json.loads(self.req("GET", "/api/control/state")[2])

    def fake_request(self, tool="Bash", ti=None, kind="permission", guard="", ask_rule="", rules=None, agent=None):
        ti = ti if ti is not None else {"command": "ls -la"}
        rid = "%x-%s" % (int(time.time() * 1000), os.urandom(6).hex())
        req = {"v": 1, "id": rid, "ts": time.time(), "pid": os.getpid(), "session": SID, "agent_id": agent, "cwd": self.cwd,
               "tool": tool, "kind": kind, "sig": ti.get("command", ""), "hash": os.urandom(32).hex(), "input": ti,
               "guard": guard, "ask_rule": ask_rule, "session_rules": rules or [], "deadline": time.time() + 300}
        # As the hook does: handed over over the hook socket with the lease key (POST
        # /api/control/request), then the doorbell req/<id>.json holding only the id.
        st, _out, _h = hook_call(self.ctl.dir, self.ctl.key, "POST", "/api/control/request", req)
        assert st == 200, st
        p = self.path("req", rid + ".json")
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(fd, json.dumps({"v": 2, "id": rid}).encode())
        os.close(fd)
        self.ctl.scan()
        return req

    def write_res(self, rid, outcome):
        p = self.path("res", rid + ".json")
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(fd, json.dumps({"id": rid, "session": SID, "outcome": outcome, "ts": time.time()}).encode())
        os.close(fd)
        os.unlink(self.path("req", rid + ".json")) if os.path.exists(self.path("req", rid + ".json")) else None

    def close(self):
        if self.ctl:
            self.ctl.stop()
            self.hook.shutdown()
            self.hook.server_close()
        self.srv.shutdown()
        self.srv.server_close()


def audit_lines(s):
    try:
        with open(s.ctl.audit_path) as fh:
            return [json.loads(x) for x in fh if x.strip()]
    except OSError:
        return []


# ── pairing and read auth (both modes) ─────────────────────────────────────
def test_pairing():
    s = Srv(control=False)
    try:
        st, hd, body = s.req("GET", "/")
        check("shell: / is served without a token (no data in it)", st == 200 and b"<title>cc-dash</title>" in body)
        st, hd, body = s.req("GET", "/pair")
        check("shell: /pair serves the same shell (the code stays in the fragment)", st == 200 and b"/auth.js" in body)
        for path in ("/api/live", "/api/sessions", "/api/meta", "/api/alerts", "/api/overview", "/api/session?id=" + SID):
            st, _, body = s.req("GET", path)
            check("read-only mode: %s needs the token (401)" % path, st == 401 and b"repair" in body, (st, body[:80]))
        st, out = s.post("/api/pair", {"code": "nope"}, token=False)
        check("pair: an unknown code is refused", st == 403, out)
        code = s.srv.auth.new_code()
        st, out = s.post("/api/pair", {"code": code}, token=False, headers={"Origin": "http://evil.example"})
        check("pair: another site's Origin is refused (the code stays unspent)", st == 403 and code in s.srv.auth.codes, out)
        st, out = s.post("/api/pair", {"code": code}, token=False, headers={"Content-Type": "text/plain"})
        check("pair: a non-JSON Content-Type is refused", st == 403, out)
        st, hd, _ = s.req("POST", "/api/pair", {"code": code}, token=False)
        st2, out2 = s.post("/api/pair", {"code": code}, token=False)
        check("pair: a code works once", st == 200 and st2 == 403, (st, st2))
        check("pair: no Set-Cookie is ever sent", "Set-Cookie" not in hd and "set-cookie" not in {k.lower() for k in hd}, hd)
        s.pair()
        tok = s.token
        check("pair: the answer is a token", isinstance(tok, str) and len(tok) > 30)
        st, _, body = s.req("GET", "/api/live")
        check("paired: /api/live 200 with X-CC-Dash-Token", st == 200)
        st, _, body = s.req("GET", "/api/meta")
        check("paired: meta says control is off", st == 200 and json.loads(body)["control"] == {"enabled": False, "paired": True, "scope": "read"})
        st, _, _ = s.req("GET", "/api/live", token=False, headers={"Cookie": "ccdash_%d=%s" % (s.port, tok)})
        check("a token sent as a cookie is ignored (cookies are not port-scoped)", st == 401, st)
        st, _, _ = s.req("GET", "/api/auth")
        check("no endpoint re-issues the token (/api/auth is gone)", st == 404, st)
        st, _, _ = s.req("GET", "/api/live", token=False, headers={"X-CC-Dash-Token": tok[:-2] + "zz"})
        check("a wrong token: 401", st == 401)
        clock = [time.time()]
        a = auth_mod.Auth(1, clock=lambda: clock[0], pair_dir=os.path.join(s.root, "pairdir"))
        c2 = a.new_code()
        clock[0] += 61
        check("pair: a code expires after 60 s", a.exchange(c2) is None)
        check("read-only mode: /api/control/state is 404 even paired", s.req("GET", "/api/control/state")[0] == 404)
        st, _, _ = s.req("POST", "/api/control/decide", {"id": "x"})
        check("read-only mode: POST /api/control/decide is 404", st == 404, st)
        st, _, _ = s.req("POST", "/api/live", raw=b"{}")
        check("read-only mode: other POSTs stay 405", st == 405, st)
        check("Host check still applies (421)", s.req("GET", "/api/live", host="evil.example:%d" % s.port)[0] == 421)

        other = Srv(control=False)
        try:
            other.token = tok
            check("another port: this launch's token is worthless there", other.req("GET", "/api/live")[0] == 401)
        finally:
            other.close()

        # the pairing file: 0600 in a 0700 dir, code only inside it, gone once spent or expired
        pdir = os.path.join(s.root, "pairfiles")
        a2 = auth_mod.Auth(s.port, pair_dir=pdir)
        code = a2.new_code()
        path = a2.pair_file(code)
        content = open(path).read()
        check("pair file: 0600 in a 0700 directory", stat.S_IMODE(os.stat(path).st_mode) == 0o600 and stat.S_IMODE(os.stat(pdir).st_mode) == 0o700)
        check("pair file: redirects to /pair#code=... (the code in the fragment)", ("/pair#code=" + code) in content and "refresh" in content)
        check("pair file: the path the browser opener gets carries no code", code not in path)
        a2.exchange(code)
        check("pair file: deleted once the code is used", not os.path.exists(path))
        clock2 = [time.time()]
        a3 = auth_mod.Auth(s.port, clock=lambda: clock2[0], pair_dir=pdir)
        p3 = a3.pair_file(a3.new_code())
        clock2[0] += 61
        a3.cleanup()
        check("pair file: deleted once the code expires", not os.path.exists(p3))
        if True:
            out = subprocess.run([PY, "-c", "import sys; sys.path.insert(0, %r)\n"
                                  "import webbrowser, threading, cc_dash.server as m\n"
                                  "opened=[]\nwebbrowser.open=lambda u,*a,**k: opened.append(u)\n"
                                  "class T:\n def __init__(s,t,f): s.f=f\n def start(s): s.f()\n"
                                  "m.threading.Timer=T\n"
                                  "m.DashServer.serve_forever=lambda self, poll_interval=0.5: None\n"
                                  "m.main(['--port','0','--days','1'])\nprint('OPENED', opened)\n" % os.path.join(REPO, "lib")],
                                 capture_output=True, text=True, timeout=60,
                                 env=dict(os.environ, CLAUDE_CONFIG_DIR=s.cfg, XDG_STATE_HOME=os.path.join(s.root, "mainstate")))
        line = [x for x in out.stdout.splitlines() if x.startswith("OPENED")]
        printed = [x for x in out.stdout.splitlines() if "/pair#code=" in x]
        code_printed = printed[0].split("#code=")[1].strip() if printed else "?"
        # Linux pairs a same-user browser through /api/pair-local, so it opens the plain
        # dashboard URL; elsewhere a file:// pairing page. Never the code itself.
        want = "http://127.0.0.1:" if auth_mod.local_pairing_supported() else "file://"
        check("cc-dash opens the plain URL (Linux) or a file:// pairing page, never the code", line and want in line[0]
              and code_printed not in line[0] and "#code=" not in line[0], (line, printed))
        check("cc-dash prints the pairing link to its own terminal", bool(printed))
    finally:
        s.close()


# ── control: writes, decisions, audit ──────────────────────────────────────
def test_write_checks():
    s = Srv()
    try:
        d = s.ctl.dir
        check("control dir 0700, subdirs 0700", stat.S_IMODE(os.stat(d).st_mode) == 0o700
              and all(stat.S_IMODE(os.stat(os.path.join(d, x)).st_mode) == 0o700 for x in ("req", "dec", "res", "msg", "dlv", "sock", "stop", "stopped")))
        lease = json.load(open(s.path("lease")))
        check("lease 0600 with pid, launch, expiry and wait", stat.S_IMODE(os.stat(s.path("lease")).st_mode) == 0o600
              and lease["pid"] == os.getpid() and lease["launch"] == s.ctl.launch and lease["expires"] > time.time() and lease["wait"] == 300, lease)
        check("unpaired: control state is 401", s.req("GET", "/api/control/state")[0] == 401)
        s.pair()
        check("paired: control state 200", s.req("GET", "/api/control/state")[0] == 200)
        check("paired: meta says control is on", json.loads(s.req("GET", "/api/meta")[2])["control"]["enabled"] is True)
        r = s.fake_request()
        body = {"id": r["id"], "action": "deny"}
        cases = [
            ("no Origin", {"Origin": None}, 403), ("another site's Origin", {"Origin": "http://evil.example"}, 403),
            ("Origin with another port", {"Origin": "http://127.0.0.1:1"}, 403),
            ("Sec-Fetch-Site cross-site", {"Sec-Fetch-Site": "cross-site"}, 403),
            ("Content-Type text/plain", {"Content-Type": "text/plain"}, 403),
            ("form Content-Type", {"Content-Type": "application/x-www-form-urlencoded"}, 403),
            ("no token", {"X-CC-Dash-Token": None}, 401), ("wrong token", {"X-CC-Dash-Token": "nope"}, 401),
        ]
        for label, hdr, want in cases:
            st, out = s.post("/api/control/decide", body, headers=hdr)
            check("POST refused: %s -> %d" % (label, want), st == want, (st, out))
        st, _, _ = s.req("POST", "/api/control/decide", raw=b"{}", headers={"Content-Type": "application/json", "Content-Length": "70000",
                         "Origin": "http://127.0.0.1:%d" % s.port, "X-CC-Dash-Token": s.token})
        check("POST refused: body over 64 KB", st == 413, st)
        st, _, _ = s.req("POST", "/api/control/decide", raw=b"[1]", headers={"Content-Type": "application/json",
                         "Origin": "http://127.0.0.1:%d" % s.port, "X-CC-Dash-Token": s.token})
        check("POST refused: a JSON body that is not an object", st == 400, st)
        st, _, _ = s.req("POST", "/api/control/decide", body, host="evil.example:%d" % s.port)
        check("POST refused: foreign Host", st == 421, st)
        check("nothing was decided by the refused requests", os.listdir(s.path("dec")) == [])
        st, out = s.post("/api/control/decide", body, headers={"Origin": "http://localhost:%d" % s.port}, host="localhost:%d" % s.port)
        check("localhost origin with the localhost Host works", st == 200, (st, out))
    finally:
        s.close()


def test_decide():
    s = Srv()
    try:
        s.pair()
        r = s.fake_request()
        st, out = s.post("/api/control/decide", {"id": r["id"], "action": "answer", "answers": {}})
        check("decide: an action that does not fit the kind is 400", st == 400, out)
        st, out = s.post("/api/control/decide", {"id": r["id"], "action": "allow", "hash": "f" * 64})
        check("decide: a stale hash is 409", st == 409, out)
        st, out = s.post("/api/control/decide", {"id": r["id"], "action": "allow", "hash": r["hash"]})
        check("decide: allow 200, status sent", st == 200 and out["request"]["status"] == "sent", out)
        p = s.path("dec", r["id"] + ".json")
        d = json.load(open(p))
        check("decide: decision file 0600 carrying the request's hash", stat.S_IMODE(os.stat(p).st_mode) == 0o600
              and d["action"] == "allow" and d["hash"] == r["hash"] and d["id"] == r["id"], d)
        st, out = s.post("/api/control/decide", {"id": r["id"], "action": "deny"})
        check("decide: a second decision is 409", st == 409, out)
        a = audit_lines(s)
        check("audit: 0600, one line with launch, client, action, target, result sent",
              stat.S_IMODE(os.stat(s.ctl.audit_path).st_mode) == 0o600 and a and a[-1]["action"] == "allow" and a[-1]["launch"] == s.ctl.launch
              and len(a[-1]["client"]) == 8 and a[-1]["session"] == SID and a[-1]["target"] == "ls -la" and a[-1]["result"] == "sent", a[-1:])
        s.write_res(r["id"], "applied")
        s.ctl.scan()
        rq = [x for x in s.state_()["requests"] if x["id"] == r["id"]][0]
        check("outcome: applied read back from res/", rq["status"] == "applied" and not os.path.exists(s.path("res", r["id"] + ".json")), rq)
        check("audit: the result is logged too", audit_lines(s)[-1]["action"] == "result" and audit_lines(s)[-1]["result"] == "applied")

        r = s.fake_request()
        s.write_res(r["id"], "terminal")
        s.ctl.scan()
        check("outcome: answered in the terminal", [x for x in s.state_()["requests"] if x["id"] == r["id"]][0]["status"] == "terminal")

        r = s.fake_request()
        os.unlink(s.path("req", r["id"] + ".json"))
        s.ctl.scan()
        st1 = [x for x in s.state_()["requests"] if x["id"] == r["id"]][0]["status"]
        s.ctl.requests[r["id"]]["gone_at"] = time.time() - 5
        s.ctl.scan()
        st2 = [x for x in s.state_()["requests"] if x["id"] == r["id"]][0]["status"]
        check("outcome: a request that vanished without a result is closed after 3 s", st1 == "open" and st2 == "closed", (st1, st2))

        g = s.fake_request(ti={"command": "rm -rf build"}, guard="NEEDS APPROVAL: deletes build/")
        st, out = s.post("/api/control/decide", {"id": g["id"], "action": "allow"})
        check("guard-asked: a plain allow is 428 and shows the reason", st == 428 and "deletes build" in out.get("guard", ""), out)
        st, out = s.post("/api/control/decide", {"id": g["id"], "action": "allow", "confirm": True})
        check("guard-asked: confirm=true (a click) is not enough", st == 428, out)
        st, out = s.post("/api/control/decide", {"id": g["id"], "action": "allow", "confirm": "approve"})
        check("guard-asked: a typed approve goes through", st == 200, out)
        check("audit: guard reason and confirmed recorded", audit_lines(s)[-1].get("guard") and audit_lines(s)[-1].get("confirmed") is True)

        k = s.fake_request(ti={"command": "npm install x"}, ask_rule="Bash(npm install*)")
        st, out = s.post("/api/control/decide", {"id": k["id"], "action": "allow"})
        check("ask rule: a plain allow is 428", st == 428 and out.get("ask_rule") == "Bash(npm install*)", out)
        st, out = s.post("/api/control/decide", {"id": k["id"], "action": "allow_session", "confirm": "approve"})
        check("ask rule: never allow-for-session", st == 400, out)

        n = s.fake_request(ti={"command": "ln -s a b"}, rules=["Bash(ln *)"])
        st, out = s.post("/api/control/decide", {"id": n["id"], "action": "allow_session"})
        check("allow_session: offered when the hook found session rules", st == 200 and json.load(open(s.path("dec", n["id"] + ".json")))["action"] == "allow_session", out)
        n2 = s.fake_request(ti={"command": "ln -s c d"})
        st, out = s.post("/api/control/decide", {"id": n2["id"], "action": "allow_session"})
        check("allow_session: refused when there is no rule to add", st == 400, out)

        q = s.fake_request("AskUserQuestion", {"questions": [{"question": "Which?", "options": [{"label": "A"}, {"label": "B"}]}]}, kind="question")
        st, out = s.post("/api/control/decide", {"id": q["id"], "action": "answer", "answers": {"Other?": "A"}})
        check("question: answers must name the request's questions", st == 400, out)
        st, out = s.post("/api/control/decide", {"id": q["id"], "action": "answer", "answers": {"Which?": "B"}})
        check("question: a valid answer is written", st == 200 and json.load(open(s.path("dec", q["id"] + ".json")))["answers"] == {"Which?": "B"}, out)

        s.ctl.approvals.clear()
        codes = []
        for i in range(8):
            x = s.fake_request(ti={"command": "true %d" % i})
            codes.append(s.post("/api/control/decide", {"id": x["id"], "action": "allow"})[0])
        check("rate limit: at most 6 approvals in 10 s, then 429", codes[:6] == [200] * 6 and codes[6:] == [429, 429], codes)
        x = s.fake_request(ti={"command": "true deny"})
        check("rate limit: denials are never limited", s.post("/api/control/decide", {"id": x["id"], "action": "deny"})[0] == 200)
    finally:
        s.close()


def test_messages_and_stop():
    s = Srv(sockets=False)
    try:
        s.pair()
        st, out = s.post("/api/control/message", {"session": "bbbbbbbb-0000-0000-0000-000000000000", "text": "hi"})
        check("message: an unknown session is 404", st == 404, out)
        st, out = s.post("/api/control/message", {"session": "../../etc", "text": "hi"})
        check("message: a session id with path characters is 400", st == 400, out)
        st, out = s.post("/api/control/message", {"session": SID, "text": "   "})
        check("message: empty text is 400", st == 400, out)
        st, out = s.post("/api/control/message", {"session": SID, "text": "x" * 9000})
        check("message: over 8000 characters is 400", st == 400, out)
        st, out = s.post("/api/control/message", {"session": SID, "text": "run the linter too"})
        m = out.get("message", {})
        name = "%s.main.%s.json" % (SID, m.get("id"))
        check("message (hook transport): queued as msg/<session>.main.<id>.json, 0600", st == 200 and m["state"] == "queued" and m["transport"] == "hook"
              and stat.S_IMODE(os.stat(s.path("msg", name)).st_mode) == 0o600, out)
        check("message: the pending flag exists", os.path.exists(s.path("pending")))
        st, out = s.post("/api/control/message", {"session": SID, "agent_id": SUB, "text": "subagent note"})
        check("message to a subagent: named for that agent", st == 200 and os.path.exists(s.path("msg", "%s.%s.%s.json" % (SID, SUB, out["message"]["id"]))), out)
        st, out = s.post("/api/control/message", {"session": SID, "agent_id": "nosuchagent0", "text": "x"})
        check("message to an unknown subagent: 404", st == 404, out)
        st, out = s.post("/api/control/cancel", {"id": out.get("message", {}).get("id", "x")})
        sub_id = [x for x in s.state_()["messages"] if x["agent_id"] == SUB][0]["id"]
        st, out = s.post("/api/control/cancel", {"id": sub_id})
        s.ctl.scan()
        check("cancel: a queued message is removed", st == 200 and not os.path.exists(s.path("msg", "%s.%s.%s.json" % (SID, SUB, sub_id))), out)

        # the hook claims the main message
        os.rename(s.path("msg", name), s.path("dlv", name))
        d = json.load(open(s.path("dlv", name)))
        d.update(delivered_ts=time.time(), via="PostToolUse", tool="Bash")
        with open(s.path("dlv", name), "w") as fh:
            json.dump(d, fh)
        os.chmod(s.path("dlv", name), 0o600)
        s.ctl.scan()
        mm = [x for x in s.state_()["messages"] if x["id"] == m["id"]][0]
        check("message: delivered (via PostToolUse) once the hook claims it", mm["state"] == "delivered" and mm["via"] == "PostToolUse", mm)
        check("message: the pending flag goes when nothing is queued", not os.path.exists(s.path("pending")))
        check("cancel: a delivered message is 409", s.post("/api/control/cancel", {"id": m["id"]})[0] == 409)
        time.sleep(0.05)
        transcript(s.cfg, SID, s.cwd, [assistant(SID, s.cwd, time.time() + 1, "ok, running the linter", "m9")])
        s.store.refresh(force=True)
        mm = [x for x in s.state_()["messages"] if x["id"] == m["id"]][0]
        check("message: seen once the agent writes after delivery", mm["state"] == "seen", mm)
        a = [x for x in audit_lines(s) if x.get("message") == m["id"]]
        check("audit: message text and its delivery are logged", a and a[0]["action"] == "message" and a[0]["text"] == "run the linter too"
              and any(x.get("result") == "delivered" for x in a), a)

        st, out = s.post("/api/control/stop", {"session": SID})
        check("stop: queued as stop/<session>.main.json, pending flag set", st == 200 and out["stop"]["state"] == "pending"
              and os.path.exists(s.path("stop", SID + ".main.json")) and os.path.exists(s.path("pending")), out)
        os.rename(s.path("stop", SID + ".main.json"), s.path("stopped", SID + ".main.json"))
        d = json.load(open(s.path("stopped", SID + ".main.json")))
        d.update(applied_ts=time.time(), outcome="stopped", tool="Bash")
        with open(s.path("stopped", SID + ".main.json"), "w") as fh:
            json.dump(d, fh)
        os.chmod(s.path("stopped", SID + ".main.json"), 0o600)
        s.ctl.scan()
        sp = [x for x in s.state_()["stops"] if x["key"] == SID + ".main"][0]
        check("stop: applied state read back", sp["state"] == "stopped" and sp["tool"] == "Bash", sp)
    finally:
        s.close()


def test_socket_transport():
    s = Srv()
    try:
        s.pair()
        sdir = tempfile.mkdtemp(prefix="cc-socks-", dir="/tmp")
        os.chmod(sdir, 0o700)
        sp = os.path.join(sdir, "4242.sock")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(sp)
        srv.listen(1)
        got = []

        def accept():
            c, _ = srv.accept()
            buf = b""
            while not buf.endswith(b"\n"):
                b = c.recv(4096)
                if not b:
                    break
                buf += b
            got.append(buf)
            c.close()
        th = threading.Thread(target=accept, daemon=True)
        th.start()
        rec = s.path("sock", SID + ".json")
        fd = os.open(rec, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(fd, json.dumps({"session": SID, "socket": sp, "pid": 4242, "ts": time.time()}).encode())
        os.close(fd)
        st, out = s.post("/api/control/message", {"session": SID, "text": "hello idle session"})
        th.join(3)
        m = out.get("message", {})
        line = json.loads(got[0]) if got else {}
        check("socket: main-thread message goes over the recorded inbox socket", st == 200 and m["transport"] == "socket" and m["state"] == "delivered", out)
        check("socket: one JSON line {type:user, message:{role:user, content}}", line.get("type") == "user" and line["message"]["role"] == "user"
              and "hello idle session" in line["message"]["content"], got)
        st, out = s.post("/api/control/message", {"session": SID, "agent_id": SUB, "text": "for the subagent"})
        check("socket: a subagent message still uses the hook queue", st == 200 and out["message"]["transport"] == "hook", out)
        os.chmod(sdir, 0o777)
        st, out = s.post("/api/control/message", {"session": SID, "text": "unsafe dir"})
        check("socket: a world-writable socket directory is not trusted (falls back to the queue)", st == 200 and out["message"]["transport"] == "hook", out)
        os.chmod(sdir, 0o700)
        srv.close()
        os.unlink(sp)
        st, out = s.post("/api/control/message", {"session": SID, "text": "gone socket"})
        check("socket: a missing socket falls back to the queue", st == 200 and out["message"]["transport"] == "hook", out)
        shutil.rmtree(sdir, ignore_errors=True)
    finally:
        s.close()


def test_agents_release_and_annotate():
    script = os.path.join(BASE, "agents.json")
    cmd = [PY, "-c", "import sys;print(open(sys.argv[1]).read())", script]

    def status(st):
        with open(script, "w") as fh:
            json.dump([{"pid": 1, "sessionId": SID, "status": st, "kind": "interactive"}], fh)
    s = Srv(agents_cmd=cmd)
    try:
        s.pair()
        r = s.fake_request()
        status("waiting")
        s.ctl.poll_agents(force=True)
        check("agents: waiting does not release", not os.path.exists(s.path("dec", r["id"] + ".json")))
        status("busy")
        s.ctl.poll_agents(force=True)
        d = json.load(open(s.path("dec", r["id"] + ".json"))) if os.path.exists(s.path("dec", r["id"] + ".json")) else {}
        check("agents: waiting -> busy releases the hook as answered in the terminal", d.get("action") == "release" and d.get("why") == "terminal"
              and d.get("hash") == r["hash"], d)
        rows = [{"_match": (SID, None, "Bash", "ls -la")}, {"_match": (SID, None, "Bash", "other")}, {"_match": None}]
        s.ctl.annotate(rows)
        check("annotate: the alert for the same call gets the request id, others none", rows[0].get("request") == r["id"]
              and "request" not in rows[1] and "request" not in rows[2], rows)
    finally:
        s.close()


def test_round_trip_with_hook():
    s = Srv()
    try:
        s.pair()
        env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
        env.update(XDG_STATE_HOME=s.state, CLAUDE_CONFIG_DIR=s.cfg, HOME=s.root,
                   CC_DASH_LEASE_ANY_PROGRAM="1")   # the dashboard here runs inside this test process
        payload = {"session_id": SID, "transcript_path": "/x", "cwd": s.cwd, "hook_event_name": "PermissionRequest",
                   "permission_mode": "default", "tool_name": "Bash", "tool_input": {"command": "make deploy"}}
        p = subprocess.Popen([PY, HOOK], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
        p.stdin.write(json.dumps(payload).encode())
        p.stdin.close()
        p.stdin = None
        req = None
        t0 = time.time()
        while time.time() - t0 < 8 and req is None:
            req = next((x for x in s.state_()["requests"] if x["status"] == "open" and x["sig"] == "make deploy"), None)
            time.sleep(0.05)
        check("round trip: the hook's request shows in the control state", req is not None)
        t1 = time.time()
        st, out = s.post("/api/control/decide", {"id": req["id"], "action": "allow", "hash": req["hash"]})
        o, _ = p.communicate(timeout=5)
        dt = time.time() - t1
        s.ctl.scan()
        final = next(x for x in s.state_()["requests"] if x["id"] == req["id"])
        check("round trip: the hook prints allow within 1 s of the POST", st == 200 and b'"allow"' in o and dt < 1.0, (st, o, dt))
        check("round trip: the dashboard shows applied", final["status"] == "applied", final)
    finally:
        s.close()


def test_refusal_on_terminal():
    """A refused control launch shows its reason on the terminal (where the code would
    have been), keeps it in the log (stderr), and tells `ccdash` through --ready-fd."""
    import io
    import types
    from cc_dash import server as server_mod
    master, slave = os.openpty()
    r, w = os.pipe()
    err, old = io.StringIO(), sys.stderr
    sys.stderr = err
    try:
        a = types.SimpleNamespace(ready_fd=w)
        server_mod.refuse_control(a, "cc-dash: cc-dash --control must be started from your own terminal: test reason.",
                                  tty=os.ttyname(slave))
    finally:
        sys.stderr = old
    shown = os.read(master, 4096).decode()
    os.close(master)
    os.close(slave)
    told = os.read(r, 64)
    os.close(r)
    check("refusal: the reason is written to the terminal", "test reason" in shown, shown)
    check("refusal: ... and to stderr (cc-dash.log with ccdash)", "test reason" in err.getvalue())
    check("refusal: --ready-fd says `refused` and is closed (ccdash returns, without 'opening')",
          told == b"refused" and a.ready_fd is None, told)


def test_no_proc_refused():
    """Without /proc (macOS) nothing can tell cc-dash, a lease's pid or a hook from
    any program of yours (an agent can write a lease naming its own pid and key and
    listen on hook.sock), so control mode fails closed: control_refusal refuses,
    with or without a live lease (it used to take any live lease pid for cc-dash,
    and to start after checking the environment alone), `cc-dash --control` exits
    with the reason on its terminal, and a Control asked to check hooks shows no
    request. The read-only dashboard still works. The system without /proc is
    played by patching procs.have_proc."""
    import io
    from cc_dash import procs
    from cc_dash import server as server_mod
    from cc_dash import control as control_mod
    d = os.path.join(BASE, "noproc", "control")
    os.makedirs(d, mode=0o700)
    real = procs.have_proc
    sleeper = subprocess.Popen([PY, "-c", "import time; time.sleep(30)"])
    try:
        check("no /proc: here, on Linux, there is one (so the patch below is what plays its absence)", real() is True)
        procs.have_proc = lambda: False
        check("no /proc: lib/cc_dash sees the patched check (one seam: procs.have_proc)",
              server_mod.procs.have_proc() is False and control_mod.procs.have_proc() is False)
        why = server_mod.control_refusal(d, env={}, ancestors=[], note=lambda _t: None)
        check("no /proc: control_refusal refuses to start (no lease, a clean environment)", why == server_mod.NO_PROC, why)
        check("no /proc: the refusal names /proc, Linux, and the read-only way to run", "/proc" in why and "Linux" in why
              and "without --control" in why and "prompts stay in the terminal" in why, why)
        why = server_mod.control_refusal(d, env={}, ancestors=[], proc=os.path.join(BASE, "noproc", "fakeproc"), me=400,
                                         note=lambda _t: None)
        check("no /proc: a fake process tree passed as `proc` does not stand in for /proc", why == server_mod.NO_PROC, why)
        lease = os.path.join(d, "lease")
        fd = os.open(lease, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(fd, json.dumps({"pid": sleeper.pid, "expires": time.time() + 600, "port": 1}).encode())
        os.close(fd)
        notes = []
        why = server_mod.control_refusal(d, env={}, ancestors=[], note=notes.append)
        check("no /proc: a live lease gets the same refusal, not taken for a running cc-dash",
              why == server_mod.NO_PROC and "holds the control lease" not in why, why)
        check("no /proc: ... and that lease is left alone (nothing could judge it)", os.path.exists(lease) and notes == [], notes)

        # `cc-dash --control` itself: refused on its terminal and in the log, exit 1, nothing started.
        shown, err, old_err = [], io.StringIO(), sys.stderr
        saved = (server_mod.tty_write, server_mod.own_process_group)
        old_env = {k: os.environ.get(k) for k in ("CLAUDE_CONFIG_DIR", "XDG_STATE_HOME")}
        root = os.path.join(BASE, "noproc", "main")
        os.makedirs(os.path.join(root, "claude", "projects"), exist_ok=True)
        os.makedirs(os.path.join(root, "state"), mode=0o700, exist_ok=True)
        server_mod.tty_write = lambda text, tty="/dev/tty": shown.append(text) or True
        server_mod.own_process_group = lambda: None          # leave this test process's group alone
        os.environ.update(CLAUDE_CONFIG_DIR=os.path.join(root, "claude"), XDG_STATE_HOME=os.path.join(root, "state"))
        sys.stderr = err
        try:
            rc = server_mod.main(["--control", "--no-open", "--port", "0"])
        finally:
            sys.stderr = old_err
            server_mod.tty_write, server_mod.own_process_group = saved
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        check("no /proc: cc-dash --control exits 1", rc == 1, rc)
        check("no /proc: ... with the reason on its terminal, where the control code would have been",
              any("cc-dash: " + server_mod.NO_PROC in t for t in shown), shown)
        check("no /proc: ... and in the log (stderr)", "cc-dash: " + server_mod.NO_PROC in err.getvalue(), err.getvalue())
        check("no /proc: ... and no control code, lease or hook socket", not any("control code:" in t for t in shown)
              and not os.path.lexists(os.path.join(root, "state", "cc_tool", "control", "lease"))
              and not os.path.lexists(os.path.join(root, "state", "cc_tool", "control", "hook.sock")), shown)

        # A Control asked to check hooks: keeps the check, and shows nothing (403, audited).
        s = Srv(hook_check=True)
        try:
            check("no /proc: Control(hook_check=True) keeps the hook check (not silently off)", s.ctl.hook_check is True)
            ti = {"command": "make deploy"}
            rid = "%x-%s" % (int(time.time() * 1000), os.urandom(6).hex())
            req = {"v": 1, "id": rid, "ts": time.time(), "pid": os.getpid(), "session": SID, "cwd": s.cwd, "tool": "Bash",
                   "kind": "permission", "sig": "make deploy", "hash": os.urandom(32).hex(), "input": ti,
                   "deadline": time.time() + 300}
            st, _out, _h = hook_call(s.ctl.dir, s.ctl.key, "POST", "/api/control/request", req)
            fd = os.open(s.path("req", rid + ".json"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.write(fd, json.dumps({"v": 2, "id": rid}).encode())
            os.close(fd)
            s.ctl.scan()
            check("no /proc: a request handed over on the hook socket is refused (403)", st == 403, st)
            check("no /proc: ... and not shown, also after its doorbell rings",
                  rid not in s.ctl.requests and s.ctl.state()["requests"] == [], s.ctl.state()["requests"])
            audit = [a for a in audit_lines(s) if a.get("item") == rid and a.get("action") == "refused"]
            check("no /proc: ... and audited once, saying why", len(audit) == 1 and "/proc" in audit[0].get("result", "")
                  and "Linux" in audit[0].get("result", ""), audit)
            code, _ = s.ctl.decide("human", {"id": rid, "action": "allow", "hash": req["hash"]})
            check("no /proc: ... and it cannot be approved", code == 404, code)
        finally:
            s.close()
        s = Srv(hook_check=False)          # requests written by hand (tests): still taken
        try:
            r = s.fake_request()
            check("no /proc: hook_check=False still takes requests (the hand-written test setup)", r["id"] in s.ctl.requests)
        finally:
            s.close()
        s = Srv(control=False)
        try:
            s.pair(control=False)
            st, _, body = s.req("GET", "/api/sessions")
            check("no /proc: the read-only dashboard still pairs and serves", st == 200 and isinstance(json.loads(body), (list, dict)),
                  (st, body[:80]))
        finally:
            s.close()
    finally:
        procs.have_proc = real
        sleeper.kill()
        sleeper.wait()


def test_stop_cleans_lease():
    s = Srv()
    s.close()
    check("stop(): lease and pending removed on shutdown", not os.path.exists(os.path.join(s.state, "cc_tool", "control", "lease")))
    check("stop(): the hook socket is removed on shutdown", not os.path.lexists(os.path.join(s.state, "cc_tool", "control", "hook.sock")))


# ── gates from the dashboard ───────────────────────────────────────────────
GATE_CLI = None      # a tree whose cc_procs passes this test process as a dashboard (tests/gate_fixture.py)
GATE_SID = "bbbbbbbb-1111-2222-3333-444444444444"


def shell_env(s):
    return {k: v for k, v in dict(os.environ, HOME=s.root, XDG_STATE_HOME=s.state, GIT_TERMINAL_PROMPT="0").items() if not k.startswith("GIT_DIR")}


def git(s, cwd, *args):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"] + list(args), cwd=cwd, env=shell_env(s),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def factory(s, repo, *args):
    r = subprocess.run([PY, CLI, "-C", repo] + list(args), env=shell_env(s), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout


class Shop:
    """A project this server knows (a transcript with its cwd), with the factory set up and a record for GATE_CLI's tree."""

    def __init__(self, s, tree_cli=None):
        self.s = s
        self.repo = os.path.realpath(os.path.join(s.root, "sklep-żółć"))     # the child runs under LC_ALL=C: a path like this must survive it
        os.makedirs(self.repo)
        git(s, self.repo, "init", "-q")
        git(s, self.repo, "commit", "-q", "--allow-empty", "-m", "initial")
        git(s, self.repo, "config", "user.name", "t")
        git(s, self.repo, "config", "user.email", "t@t")
        factory(s, self.repo, "init")
        now = time.time()
        transcript(s.cfg, GATE_SID, self.repo, [{"type": "user", "timestamp": iso(now - 30), "sessionId": GATE_SID, "cwd": self.repo, "uuid": "g1",
                                                 "message": {"role": "user", "content": "hello"}}])
        s.store.refresh(force=True)
        self.label = next(p["project"] for p in s.srv.docs.projects() if os.path.realpath(p["root"]) == self.repo)
        cli = tree_cli or GATE_CLI
        gate_fixture.plant_record(self.repo, os.path.dirname(os.path.dirname(cli)))
        s.srv.gates.cli = cli
        s.srv.gates.start = time.time() + 2     # this process "started" after the record (the dashboard's own code-age check)

    def at_spec(self, title="Add CSV export"):
        oid = factory(self.s, self.repo, "new", title).split()[0]
        factory(self.s, self.repo, "mark", oid, "spec", "--no-holdout")
        return oid

    def ledger(self, oid=None):
        out = []
        try:
            with open(os.path.join(self.repo, ".git", "cc_tool", "factory", "ledger.jsonl")) as fh:
                out = [json.loads(x) for x in fh if x.strip()]
        except OSError:
            pass
        return [e for e in out if oid is None or e.get("order") == oid]

    def status(self, oid):
        return json.loads(factory(self.s, self.repo, "show", oid, "--json"))["status"]

    def card(self, oid):
        st, _, out = self.s.req("GET", "/api/control/gate?project=%s&order=%s" % (quote(self.label), oid))
        return st, json.loads(out)

    def body(self, card, decision="approve", **kw):
        b = {"project": self.label, "order": card["order"], "gate": card["gate"], "decision": decision, "confirm": decision, "view": card["view"]}
        b["spec_hash" if card["gate"] == "spec" else "lane_head"] = card.get("spec_hash", card.get("lane_head"))
        b.update(kw)
        return b

    def decide(self, oid, decision="approve", **kw):
        st, c = self.card(oid)
        assert st == 200, c
        return self.s.post("/api/control/gate", self.body(c, decision, **kw))

    def at_merge(self, title="Add CSV export"):
        """An order at the merge gate, the way a run leaves it: approved from the dashboard, a lane with a commit, the review event."""
        oid = self.at_spec(title)
        st, out = self.decide(oid)
        assert st == 200, out
        wt = factory(self.s, self.repo, "lane", oid).rstrip("\n").splitlines()[-1]
        git(self.s, wt, "commit", "-q", "--allow-empty", "-m", "%s: work" % oid)
        head = git(self.s, wt, "rev-parse", "HEAD")
        rec = {"t": time.time(), "order": oid, "event": "status", "status": "review", "lane_head": head, "recommendation": "merge",
               "briefing": "Review passed.\nHoldout: S01 satisfied.", "by": "cc-factory"}
        with open(os.path.join(self.repo, ".git", "cc_tool", "factory", "ledger.jsonl"), "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        return oid, wt, head


def gate_srv():
    """A paired, unlocked server. Tests here decide often, so the approvals limit is widened except in its own test."""
    s = Srv()
    s.pair()
    s.ctl._rate_ok = lambda _client: True
    return s


def gate_events(shop, oid):
    return [e for e in shop.ledger(oid) if e["event"] == "gate"]


def test_gates_end_to_end():
    s = gate_srv()
    try:
        shop = Shop(s)
        a = shop.at_spec()
        path = next(n for n in os.listdir(os.path.join(shop.repo, "factory", "orders")) if n.startswith(a))
        with open(os.path.join(shop.repo, "factory", "orders", path), "a") as fh:
            fh.write("\nThe whole body, down to this last line.\n")
        st, c = shop.card(a)
        check("gate card: the spec gate shows the whole order body, its path, hash, view id, line and stations",
              st == 200 and c["gate"] == "spec" and c["body"].endswith("down to this last line.\n") and len(c["spec_hash"]) == 64 and len(c["view"]) == 32
              and c["path"].startswith("factory/orders/") and c["line"] == "default" and c["stations"], c)
        # The hash covers the line, so the card names what approving it accepts.
        lines = os.path.join(shop.repo, "factory", "line.json")
        saved = open(lines).read()
        doc = json.loads(saved)
        doc["lines"]["default"].update(test="make planted", threshold=0.5)
        with open(lines, "w") as fh:
            json.dump(doc, fh)
        st, c2 = shop.card(a)
        check("gate card: the line's test command and pass mark are shown, and changing them changes the hash", st == 200 and c2["commands"] == {"test": "make planted"}
              and c2["threshold"] == 0.5 and c2["spec_hash"] != c["spec_hash"], {k: c2.get(k) for k in ("commands", "threshold")})
        with open(lines, "w") as fh:
            fh.write(saved)
        st, out = shop.decide(a, note="scope is right")
        e = gate_events(shop, a)
        check("spec gate: approve end to end through a real cc-factory child: 200, status approved", st == 200 and out.get("status") == "approved" and shop.status(a) == "approved", (st, out))
        check("spec gate: one gate event, naming the dashboard and its ticket, with the note and the hash the card showed",
              len(e) == 1 and e[0]["via"] == "dashboard" and len(e[0]["ticket"]) == 32 and e[0]["note"] == "scope is right" and e[0]["spec_hash"] == c["spec_hash"], e)
        audit = [x for x in audit_lines(s) if x.get("action") == "gate"]
        check("audit: the decision is logged with order, gate, value, ticket and result",
              audit and audit[-1].get("order") == a and audit[-1].get("value") == c["spec_hash"] and audit[-1].get("ticket") == e[0]["ticket"] and "recorded" in audit[-1].get("result", ""), audit[-1:])
        b = shop.at_spec("Too broad")
        st, out = shop.decide(b, "reject", note="podziel to, zażółć")
        check("spec gate: reject end to end, a non-ASCII note and project path intact", st == 200 and shop.status(b) == "rejected" and gate_events(shop, b)[-1]["state"] == "fail"
              and gate_events(shop, b)[-1]["note"] == "podziel to, zażółć", (st, out))
        k = shop.at_spec("Holds a secret")
        key = "sk-ant-" + "k" * 24
        st, out = shop.decide(k, "reject", note="leaked %s and password=hunter2" % key)
        kept = gate_events(shop, k)[-1]["note"]
        logged = [x for x in audit_lines(s) if x.get("action") == "gate"][-1]
        with open(s.ctl.audit_path, encoding="utf-8") as fh:
            on_disk = fh.read()
        check("audit: a note is redacted as the ledger redacts it: the same text, and neither secret on disk",
              st == 200 and "[redacted]" in kept and logged.get("note") == kept and key not in on_disk and "hunter2" not in on_disk, (st, kept, logged))

        m, wt, head = shop.at_merge("Merge me")
        st, c = shop.card(m)
        branch = git(s, shop.repo, "symbolic-ref", "--short", "HEAD")
        check("gate card: the merge gate shows the briefing, the reviewed head, the branch and checkout it would merge into",
              st == 200 and c["gate"] == "merge" and c["kind"] == "merge" and "Review passed." in c["briefing"] and c["lane_head"] == head
              and c["moved"] is False and c["into"]["branch"] == branch and c["into"]["checkout"].endswith("sklep-żółć") and c["merged"] is False
              and c["merge_blocked"] == "" and c["decisions"] == {"approve": True, "reject": True, "rework": True}, c)
        before = git(s, shop.repo, "rev-parse", "HEAD")
        st, out = shop.decide(m)
        e = gate_events(shop, m)[-1]
        after = git(s, shop.repo, "rev-parse", "HEAD")
        check("merge gate: Approve merge merges exactly the reviewed head with --no-ff, then records it: done, via dashboard",
              st == 200 and shop.status(m) == "done" and git(s, shop.repo, "rev-parse", "HEAD^2") == head and git(s, shop.repo, "rev-parse", "HEAD^1") == before
              and after != before and e.get("lane_head") == head and e.get("via") == "dashboard" and e.get("merge_sha") == after, (st, out, e))
        check("merge gate: the lane branch and worktree are untouched and nothing was pushed", os.path.isdir(wt) and git(s, wt, "rev-parse", "HEAD") == head)
        r, _wt, _h = shop.at_merge("Send me back")
        st, out = shop.decide(r, "reject", rework=True, note="ignores filters")
        check("merge gate: reject with rework goes back to approved", st == 200 and shop.status(r) == "approved" and gate_events(shop, r)[-1]["note"] == "ignores filters", (st, out))
        d, _wt, _h = shop.at_merge("Drop me")
        st, out = shop.decide(d, "reject")
        check("merge gate: reject drops the order", st == 200 and shop.status(d) == "rejected", (st, out))
        st, out = shop.card(d)
        check("gate card: a rejected order has no card", st == 409 and "no decision for you" in out.get("error", ""), (st, out))
    finally:
        s.close()


def test_gate_refusals():
    s = gate_srv()
    calls = []
    try:
        shop = Shop(s)
        real_run = s.srv.gates._run
        s.srv.gates._run = lambda t: calls.append(t) or real_run(t)      # a ticket is minted only where _run is called
        a = shop.at_spec()
        st, c = shop.card(a)
        token = s.token
        s.token = s.read_token
        st, out = s.post("/api/control/gate", shop.body(c))
        s.token = token
        check("refused: a read-only token (403, and the page is told to ask for the code)", st == 403 and out.get("unlock") is True, (st, out))
        s.token = None
        st, out = s.post("/api/control/gate", shop.body(c), token=False)
        s.token = token
        check("refused: no token", st == 401, (st, out))
        st, out = s.post("/api/control/gate", shop.body(c), headers={"Origin": "http://evil.example"})
        check("refused: another site's Origin", st == 403 and "origin" in out.get("error", ""), (st, out))
        for word in ("", "reject", "APPROVE", "yes", None):
            st, out = s.post("/api/control/gate", shop.body(c, confirm=word))
            check("refused: confirm %r is not the typed word (428)" % (word,), st == 428 and out.get("confirm") == "approve", (st, out))
        st, out = s.post("/api/control/gate", shop.body(c, decision="pass", confirm="pass"))
        check("refused: an unknown decision", st == 400, (st, out))
        st, out = s.post("/api/control/gate", shop.body(c, rework=True))
        check("refused: rework on an approval", st == 400, (st, out))
        st, out = s.post("/api/control/gate", shop.body(c, note="x" * 5000))
        check("refused: a note past the limit", st == 400, (st, out))
        st, out = s.post("/api/control/gate", shop.body(c, note="the code is " + s.control_code))
        check("refused: a note holding the control code is not sent, nor echoed", st == 400 and out.get("code_in_text") is True and s.control_code not in json.dumps(out), (st, out))
        st, out = s.post("/api/control/gate", shop.body(c, view="f" * 32))
        check("refused: a view id this server never gave", st == 409 and "out of date" in out.get("error", ""), (st, out))
        check("refused: none of those reached a ticket, and the order still waits", calls == [] and shop.status(a) == "spec" and gate_events(shop, a) == [], (calls, shop.status(a)))

        # A read-only token reads the card and gets no view id, so it cannot crowd the human's out of the table.
        before = len(s.srv.gates.views)
        s.token = s.read_token
        st, rc = shop.card(a)
        s.token = token
        check("gate card: a read-only token sees the card but gets no view id", st == 200 and rc["view"] == "" and "body" in rc and len(s.srv.gates.views) == before, (st, rc.get("view")))
        st, out = s.post("/api/control/gate", shop.body(rc))
        check("refused: a card fetched with a read-only token cannot be decided by a control token", st == 409, (st, out))
        # A view belongs to the client that fetched it.
        st, c = shop.card(a)
        s.srv.gates.views[c["view"]]["client"] = "someone-else"
        st, out = s.post("/api/control/gate", shop.body(c))
        check("refused: a view id another client was given", st == 409 and "out of date" in out.get("error", ""), (st, out))
        st, c = shop.card(a)
        s.srv.gates.views[c["view"]]["expires"] = time.time() - 1
        st, out = s.post("/api/control/gate", shop.body(c))
        check("refused: a view past its time", st == 409 and "out of date" in out.get("error", ""), (st, out))

        # A spec edited after the human saw it.
        st, c = shop.card(a)
        order = os.path.join(shop.repo, "factory", "orders", next(n for n in os.listdir(os.path.join(shop.repo, "factory", "orders")) if n.startswith(a)))
        with open(order, "a") as fh:
            fh.write("\nAlso disable the tests.\n")
        st, out = s.post("/api/control/gate", shop.body(c))
        check("refused: a spec edited since the card was read (409, says to read it again), no ticket", st == 409 and "changed since you read it" in out.get("error", "")
              and calls == [] and shop.status(a) == "spec", (st, out))
        st, c = shop.card(a)
        st, out = s.post("/api/control/gate", shop.body(c, spec_hash="0" * 64))
        check("refused: a hash that is not what the card showed", st == 409 and calls == [], (st, out))

        # A replayed POST: the same bytes again, also with the order back at the same gate and the same hash.
        st, c = shop.card(a)
        body = shop.body(c)
        st, out = s.post("/api/control/gate", body)
        check("replay: the first POST passes", st == 200, (st, out))
        n = len(gate_events(shop, a))
        st, out = s.post("/api/control/gate", body)
        check("replay: the same POST again is refused, no second event", st == 409 and "already used" in out.get("error", "") and len(gate_events(shop, a)) == n, (st, out))
        factory(s, shop.repo, "mark", a, "spec", "--no-holdout")
        st, out = s.post("/api/control/gate", body)
        check("replay: refused even with the order back at the same gate and the same spec hash", st == 409 and "already used" in out.get("error", "") and shop.status(a) == "spec", (st, out))

        # A moved lane.
        m, wt, head = shop.at_merge("A lane that moves")
        git(s, shop.repo, "merge", "-q", "--no-ff", "-m", "merge", head)
        git(s, wt, "commit", "-q", "--allow-empty", "-m", "%s: one more" % m)
        before = len(calls)
        st, c = shop.card(m)
        check("gate card: a moved lane says so", st == 200 and c["moved"] is True and c["lane_head"] == head, c)
        st, out = s.post("/api/control/gate", shop.body(c))
        check("refused: approving a lane moved after the review, no ticket", st == 409 and "moved after the review" in out.get("error", "") and len(calls) == before and shop.status(m) == "review", (st, out))
        st, c = shop.card(m)
        st, out = s.post("/api/control/gate", shop.body(c, lane_head="a" * 40))
        check("refused: a lane head that is not the reviewed one", st == 409 and len(calls) == before, (st, out))
        st, out = shop.decide(m, "reject", rework=True)
        check("a moved lane can still be sent back", st == 200 and shop.status(m) == "approved", (st, out))

        # A review with no briefing, or no recorded head: nothing to decide from.
        n2, _wt, h2 = shop.at_merge("Nothing to read")
        git(s, shop.repo, "merge", "-q", "--no-ff", "-m", "merge", h2)       # merged, so only this rule stands in the way
        with open(os.path.join(shop.repo, ".git", "cc_tool", "factory", "ledger.jsonl"), "a") as fh:
            fh.write(json.dumps({"t": time.time(), "order": n2, "event": "status", "status": "review", "lane_head": h2, "by": "cc-factory"}) + "\n")
        st, out = shop.decide(n2)
        check("refused: a merge gate with no stored briefing is not passed from here", st == 409 and "stored no briefing" in out.get("error", "") and shop.status(n2) == "review", (st, out))

        # The order left its gate between reading the card and deciding.
        w = shop.at_spec("Left its gate")
        st, c = shop.card(w)
        factory(s, shop.repo, "mark", w, "blocked")
        st, out = s.post("/api/control/gate", shop.body(c))
        check("refused: an order that left its gate after the card was read, no ticket", st == 409 and "does not allow that decision" in out.get("error", "")
              and shop.status(w) == "blocked" and gate_events(shop, w) == [], (st, out))

        # Odd ids.
        for q in ("project=nope&order=FO-001", "project=%s&order=../../etc/passwd" % quote(shop.label), "project=%s&order=FO-999" % quote(shop.label), "project=&order="):
            st, _, raw = s.req("GET", "/api/control/gate?" + q)
            check("gate card: %s is refused" % q, st in (400, 404), (st, raw[:120]))
        st, out = s.post("/api/control/gate", {"project": ["x"], "order": {"a": 1}, "gate": "spec", "decision": "approve", "confirm": "approve"})
        check("refused: a project and order that are not strings", st == 400, (st, out))

        # One decision at a time.
        z = shop.at_spec("Busy")
        st, c = shop.card(z)
        s.srv.gates.busy.acquire()
        st, out = s.post("/api/control/gate", shop.body(c))
        s.srv.gates.busy.release()
        check("refused: while another decision is being recorded", st == 409 and shop.status(z) == "spec", (st, out))

        # Scenario text never appears.
        h = os.path.join(shop.repo, ".factory-holdout", z)
        os.makedirs(h, mode=0o700)
        with open(os.path.join(h, "S01-hidden.md"), "w") as fh:
            fh.write("SCENARIO-TEXT-NEVER-SHOWN\n")
        st, _, raw = s.req("GET", "/api/control/gate?project=%s&order=%s" % (quote(shop.label), z))
        st2, out = shop.decide(z)
        with open(s.ctl.audit_path) as fh:
            log = fh.read()
        check("holdout: scenario text is in no card, no answer and no audit line", st == 200 and st2 == 200 and b"SCENARIO-TEXT" not in raw
              and "SCENARIO-TEXT" not in json.dumps(out) and "SCENARIO-TEXT" not in log, (st, st2))
    finally:
        s.close()
    # The approvals limit, as for decide: six in ten seconds.
    s = Srv()
    try:
        s.pair()
        codes = [s.post("/api/control/gate", {"project": "x", "order": "FO-001", "gate": "spec", "decision": "approve", "confirm": "approve"})[0] for _ in range(8)]
        check("refused: the approvals rate limit applies (429 after six)", codes[:6] == [404] * 6 and codes[6:] == [429, 429], codes)
    finally:
        s.close()
    # No control mode: the path does not exist.
    s = Srv(control=False)
    try:
        s.pair(control=False)
        check("no --control: GET /api/control/gate is a 404", s.req("GET", "/api/control/gate?project=x&order=FO-001")[0] == 404)
        st, _, _ = s.req("POST", "/api/control/gate", {"project": "x"})
        check("no --control: POST /api/control/gate is a 404", st == 404, st)
    finally:
        s.close()
    # No /proc.
    from cc_dash import procs
    real = procs.have_proc
    s = gate_srv()
    try:
        shop = Shop(s)
        a = shop.at_spec()
        st, c = shop.card(a)
        procs.have_proc = lambda: False
        st, out = s.post("/api/control/gate", shop.body(c))
        check("no /proc: refused before any ticket, the order still waits", st == 403 and "/proc" in out.get("error", "") and "Linux" in out.get("error", "")
              and shop.status(a) == "spec", (st, out))
    finally:
        procs.have_proc = real
        s.close()


# What a stand-in for bin/cc-factory reports about how it was started, and what asking twice on its socket gets.
PROBE = r"""
import json, os, socket, sys


def is_open(n):
    try:
        os.fstat(n)
        return True
    except OSError:
        return False


fds = [n for n in range(256) if is_open(n)]       # by fstat: listing /proc/self/fd opens one of its own
fd = int(sys.argv[sys.argv.index("--ticket-fd") + 1])
args = sys.argv[1:]
gate, verb, order = args[args.index("--gate") + 1], args[2], args[3]
s = socket.socket(fileno=fd)
ask = json.dumps({"v": 1, "op": "redeem", "order": order, "gate": gate, "decision": verb, "rework": False}).encode() + b"\n"
answers = []
f = s.makefile("rb")
for q in (ask, ask):
    s.sendall(q)
    answers.append(json.loads(f.readline()))
report = {"argv": args, "env": dict(os.environ), "fds": fds, "fd": fd, "ppid": os.getppid(),
          "sid": os.getsid(0), "pid": os.getpid(), "answers": answers, "isolated": sys.flags.isolated}
with open(os.environ["HOME"] + "/.probe.json", "w") as fh:
    json.dump(report, fh)
print("probed")
"""


def test_gate_child():
    s = gate_srv()
    saved = {k: os.environ.get(k) for k in ("PYTHONPATH", "LD_PRELOAD", "CLAUDECODE", "ANTHROPIC_API_KEY", "HOME", "GIT_DIR")}
    try:
        shop = Shop(s)
        probe = os.path.join(s.root, "probe.py")
        with open(probe, "w") as fh:
            fh.write(PROBE)
        s.srv.gates.cli = probe
        s.srv.gates._code_outside = lambda main="": None      # the probe is no cc_tool tree, so it has no code record to be judged by
        a = shop.at_spec()
        st, c = shop.card(a)
        os.environ.update(PYTHONPATH="/evil", CLAUDECODE="1", ANTHROPIC_API_KEY="sk-not-for-the-child", HOME=s.root, GIT_DIR="/evil/.git")
        try:
            st, out = s.post("/api/control/gate", shop.body(c, note="--gate merge --rework"))
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        with open(os.path.join(s.root, ".probe.json")) as fh:
            p = json.load(fh)
        check("child: a fixed argv: -C, the project, the verb, the id, the gate, the fd; a note never reaches it",
              p["argv"] == ["-C", shop.repo, "approve", a, "--gate", "spec", "--ticket-fd", str(p["fd"])], p["argv"])
        check("child: the ticket reaches it on the socket only: not in its argv or environment",
              p["answers"][0].get("ok") is True and p["answers"][0]["ticket"]["id"] not in json.dumps([p["argv"], p["env"]]), p["answers"])
        bad = [k for k in p["env"] if k.startswith(("CLAUDE", "PYTHON", "LD_", "ANTHROPIC")) or k == "GIT_DIR"]
        check("child: an allowlisted environment: none of Claude Code's, Python's, the loader's or an API key", bad == [] and p["isolated"] == 1, bad)
        check("child: git told to run no transport and no lazy fetch", p["env"].get("GIT_ALLOW_PROTOCOL") == "" and p["env"].get("GIT_NO_LAZY_FETCH") == "1", p["env"])
        check("child: it inherits stdin, stdout, stderr and the ticket socket, nothing else", set(p["fds"]) == {0, 1, 2, p["fd"]}, (p["fds"], p["fd"]))
        check("child: cc-dash is its parent, and it has a session of its own (no way to the dashboard's terminal)", p["ppid"] == os.getpid() and p["sid"] == p["pid"], (p["ppid"], p["sid"], p["pid"]))
        check("ticket: the first question gets it, carrying what was decided and the note", p["answers"][0]["ticket"]["value"] == c["spec_hash"]
              and p["answers"][0]["ticket"]["note"] == "--gate merge --rework" and p["answers"][0]["ticket"]["decision"] == "approve", p["answers"][0])
        check("ticket: a replay on the same socket is refused (single use)", p["answers"][1].get("ok") is False and "already used" in p["answers"][1].get("error", ""), p["answers"][1])
        check("ticket: short-lived", 0 < p["answers"][0]["ticket"]["expires"] - time.time() <= 30, p["answers"][0]["ticket"]["expires"])
        check("the stand-in wrote no ledger event", gate_events(shop, a) == [], gate_events(shop, a))

        # The server's own half of the ticket, apart from the CLI that checks the same things again.
        def ticket(**kw):
            return dict({"id": "a" * 32, "order": a, "gate": "spec", "decision": "approve", "rework": False, "note": "", "value": "v",
                         "expires": time.time() + 30, "used": False}, **kw)

        def ask(**kw):
            return json.dumps(dict({"op": "redeem", "order": a, "gate": "spec", "decision": "approve", "rework": False}, **kw)).encode()
        redeem = s.srv.gates._redeem
        check("ticket: the server hands over a good one", redeem(ticket(), ask()).get("ok") is True)
        check("ticket: the server refuses one past its time", "expired" in redeem(ticket(expires=time.time() - 1), ask()).get("error", ""))
        for label, q in (("another order", ask(order="FO-999")), ("another gate", ask(gate="merge")), ("another decision", ask(decision="reject")),
                         ("rework", ask(rework=True)), ("not a redeem", ask(op="peek")), ("not JSON", b"{nope")):
            t = ticket()
            check("ticket: the server refuses a question about %s" % label, "another decision" in redeem(t, q).get("error", ""))
            check("ticket: ... and that spends it: it never answers a second question", "already used" in redeem(t, ask()).get("error", ""))

        # A real child of the dashboard (ppid and kernel peer both right) that dials hook.sock instead of the socket it was given:
        # the listener is cc-dash too, so even a parent that passes as a dashboard gets no ticket there: it speaks HTTP, wants the key, and has no redeem.
        dial = os.path.join(s.root, "dial.py")
        with open(dial, "w") as fh:
            fh.write("import os, runpy, socket, sys\n"
                     "c = socket.socket(socket.AF_UNIX)\n"
                     "c.connect(%r)\n"
                     "fd = sys.argv.index('--ticket-fd') + 1\n"
                     "sys.argv[fd] = str(c.fileno())\n"
                     "os.set_inheritable(c.fileno(), True)\n"
                     "os.execv(sys.executable, [sys.executable, '-I', %r] + sys.argv[1:])\n" % (s.path("hook.sock"), GATE_CLI))
        s.srv.gates.cli = dial
        b = shop.at_spec("Dials the hook socket")
        st, out = shop.decide(b)
        check("a real child of the dashboard dialing hook.sock gets no ticket: BLOCKED, nothing written, whatever its peer says",
              st == 409 and "BLOCKED" in out.get("error", "") and "no ticket" in out.get("error", "") and gate_events(shop, b) == [] and shop.status(b) == "spec", (st, out))

        # Past its time: the real CLI gets a refusal from the server.
        s.srv.gates.cli = GATE_CLI
        del s.srv.gates._code_outside
        s.srv.gates.ticket_ttl = -1
        st, out = shop.decide(a)
        check("ticket: expired, the server hands nothing over and the CLI refuses", st == 409 and "expired" in out.get("error", "") and gate_events(shop, a) == [], (st, out))
        s.srv.gates.ticket_ttl = 30
        # The real CLI from a server that is no real dashboard: this very test process. This refusal comes after the
        # socket check, so reaching it also shows that the socket _run makes is held by the server alone.
        gate_fixture.plant_record(shop.repo, REPO)
        s.srv.gates.start = time.time() + 2
        s.srv.gates.cli = CLI
        st, out = shop.decide(a)
        check("the real CLI refuses a parent that is not cc-dash, whatever the server says: BLOCKED, nothing written",
              st == 409 and "BLOCKED" in out.get("error", "") and "its parent is not your dashboard" in out.get("error", "") and gate_events(shop, a) == [] and shop.status(a) == "spec", (st, out))
        audit = [x for x in audit_lines(s) if x.get("action") == "gate"][-1]
        check("audit: a refusal by the CLI is logged too", "BLOCKED" in audit.get("result", "") and audit.get("order") == a, audit)
    finally:
        s.close()


GATE_UI = r"""
const { chromium } = require(process.env.PW);
const results = [];
const ok = (name, cond, detail) => results.push([name, !!cond, String(detail === undefined ? '' : detail).slice(0, 300)]);
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME, headless: true });
  const page = await (await browser.newContext({ viewport: { width: 1280, height: 860 } })).newPage();
  const errs = [];
  page.on('pageerror', (e) => errs.push(String(e)));
  await page.goto(process.env.BASE + '/pair#code=' + process.env.CODE);
  await page.waitForFunction(() => window.ccControl && window.ccControl.on());
  const E = JSON.parse(process.env.GATE_E2E);
  // The real Work view: /api/plans, then plans.js's gate slot, then this button (contract C).
  await page.evaluate((p) => { location.hash = '#/plans?p=' + encodeURIComponent(p); }, E.project);
  const specBtn = '[data-ctl-gate][data-ctl-order="' + E.spec + '"]';
  await page.waitForSelector(specBtn);
  ok('work view: an order at the spec gate gets the button, in place of the terminal commands', (await page.textContent(specBtn)) === 'Review spec gate');
  ok('action: nothing for an order at no gate', (await page.evaluate(() => window.ccGates.action({ id: 'FO-1', gate: null }))) === null);
  await page.click(specBtn);
  await page.waitForSelector('dialog.ctl-review[open] .k-gate');
  const text = await page.textContent('dialog.ctl-review .ctl-plan-text');
  ok('card: the whole order body', text.includes('LAST-LINE-OF-THE-SPEC'), text.slice(-120));
  ok('card: markup in the order stays text', text.includes('<img src=x onerror=') && (await page.locator('dialog.ctl-review img').count()) === 0);
  ok('card: invisible characters are shown and called out', text.includes('U+202E') && (await page.textContent('dialog.ctl-review')).includes('Hidden characters'));
  ok('card: what the hash covers besides the text is named', (await page.textContent('dialog.ctl-review .ctl-more')).includes('plan, build, test, verify, review'), await page.textContent('dialog.ctl-review .ctl-more'));
  ok('card: the code field while locked, and no acting control', await page.isVisible('dialog.ctl-review .ctl-code') && !(await page.isVisible('dialog.ctl-review .ctl-acts .btn.primary')));
  await page.fill('dialog.ctl-review .ctl-code', process.env.CONTROL);
  await page.click('dialog.ctl-review .ctl-lock .btn');
  await page.waitForSelector('dialog.ctl-review .ctl-acts .btn.primary');
  await page.waitForTimeout(200);       // the card's view id is fetched again for the new token
  const shot = async (name) => { if (process.env.SHOTS) await page.screenshot({ path: process.env.SHOTS + '/' + name + '.png' }); };
  await shot('gate-spec');
  await page.emulateMedia({ colorScheme: 'dark' });
  await shot('gate-spec-dark');
  await page.emulateMedia({ colorScheme: 'light' });
  await page.fill('dialog.ctl-review textarea', 'read it all');
  await page.click('text=Approve spec…');
  const go = page.locator('dialog.ctl-review .ctl-confirm .btn.warn-solid');
  ok('confirm: disabled until the word is typed', await go.isDisabled());
  await page.fill('dialog.ctl-review .ctl-confirm input', 'appro');
  ok('confirm: part of the word does nothing', await go.isDisabled());
  await page.keyboard.press('Escape');
  ok('confirm: Escape closes the confirm first, the dialog stays', await page.isVisible('dialog.ctl-review') && !(await page.isVisible('dialog.ctl-review .ctl-confirm')));
  await page.click('text=Approve spec…');
  ok('confirm: opened again it asks for the whole word', (await page.inputValue('dialog.ctl-review .ctl-confirm input')) === '');
  await page.fill('dialog.ctl-review .ctl-confirm input', 'approve');
  await go.click();
  await page.waitForSelector('dialog.ctl-review .ctl-status.s-done');
  ok('decide: the card says what was recorded', (await page.textContent('dialog.ctl-review .ctl-status')).includes('spec gate passed'), await page.textContent('dialog.ctl-review'));
  await page.keyboard.press('Escape');
  ok('dialog: Escape closes it', !(await page.isVisible('dialog.ctl-review')));

  const mergeBtn = '[data-ctl-gate][data-ctl-order="' + E.merge + '"]';
  await page.waitForSelector(mergeBtn);
  ok('work view: the order at the merge gate gets its button too', (await page.textContent(mergeBtn)) === 'Review merge gate');
  await page.click(mergeBtn);
  await page.waitForSelector('dialog.ctl-review[open] .k-gate');
  const b = await page.textContent('dialog.ctl-review .ctl-plan-text');
  ok('card: the merge gate shows the stored briefing', b.includes('Review passed.'), b);
  const into = await page.textContent('dialog.ctl-review .ctl-more');
  ok('card: names the merge it would run, the branch and the checkout', into.includes('git merge --no-ff into') && into.includes('sklep'), into);
  await page.click('text=Approve merge…');
  ok('merge: the confirm says it merges, naming the branch and the checkout', (await page.textContent('dialog.ctl-review .ctl-confirm p')).includes('runs git merge --no-ff'), '');
  await page.click('dialog.ctl-review .ctl-confirm .btn.quiet');
  await shot('gate-merge-confirm');
  await page.setViewportSize({ width: 390, height: 844 });
  await shot('gate-merge-phone');
  ok('phone: no horizontal scroll', await page.evaluate(() => document.querySelector('dialog.ctl-review').scrollWidth <= window.innerWidth && document.documentElement.scrollWidth <= window.innerWidth));
  await page.waitForTimeout(300);       // the spent view id is replaced
  await page.click('text=Send back for rework…');
  await page.fill('dialog.ctl-review .ctl-confirm input', 'reject');
  await page.locator('dialog.ctl-review .ctl-confirm .btn.warn-solid').click();
  await page.waitForSelector('dialog.ctl-review .ctl-status.s-done');
  ok('merge: send back from the same card, on the fresh view id', true);
  ok('control off: action is null', await page.evaluate(() => { const c = window.ccControl; window.ccControl = { on: () => false }; const r = window.ccGates.action({ id: 'FO-1', gate: { name: 'spec', waiting: true } }); window.ccControl = c; return r === null; }));
  ok('no page errors', errs.length === 0, errs.join(' | '));
  await browser.close();
})().catch((e) => { ok('the walk finished', false, e && e.stack); }).finally(() => { console.log(JSON.stringify(results)); });
"""


def test_gate_ui():
    pw = os.environ.get("CC_DASH_PLAYWRIGHT", "/home/lukasz/Files/hobby/color-analyzer/webapp/node_modules/playwright-core")
    chrome = os.environ.get("CC_DASH_CHROME", "/usr/bin/google-chrome")
    html = open(os.path.join(REPO, "lib", "cc_dash", "web", "index.html")).read()
    js = open(os.path.join(REPO, "lib", "cc_dash", "web", "gates.js")).read()
    code = "\n".join(ln for ln in js.split("\n") if not ln.lstrip().startswith(("/*", "*")) and not ln.startswith("   "))
    check("gates.js: loaded after control.js, before plans.js", html.index("/control.js") < html.index("/gates.js") < html.index("/plans.js"))
    check("gates.js: nothing from the payload becomes HTML", "innerHTML" not in code and "insertAdjacentHTML" not in code and "outerHTML" not in code)
    if not (os.path.isdir(pw) and os.path.exists(chrome) and shutil.which("node")):
        print("browser: skipped (no playwright-core, Chrome or node)")
        return
    s = Srv()
    try:
        s.ctl._rate_ok = lambda _client: True
        s.control_code = s.srv.auth.enable_control()
        s.pair(control=False)                # a throwaway unlock below needs a token: the page pairs itself
        s.token = None
        st, out = s.post("/api/pair", {"code": s.srv.auth.new_code()}, token=False)
        s.token = out["token"]
        st, out = s.post("/api/control/unlock", {"code": s.control_code})
        s.token = out["token"]
        shop = Shop(s)
        spec = shop.at_spec("Hostile spec")
        order = os.path.join(shop.repo, "factory", "orders", next(n for n in os.listdir(os.path.join(shop.repo, "factory", "orders")) if n.startswith(spec)))
        with open(order, "a", encoding="utf-8") as fh:
            fh.write("\n<img src=x onerror=alert(1)>\nReads ‮one way\nLAST-LINE-OF-THE-SPEC\n")
        merge, _wt, _head = shop.at_merge("Merge in the browser")
        script = os.path.join(BASE, "gate-ui.js")
        with open(script, "w") as fh:
            fh.write(GATE_UI)
        env = dict(os.environ, PW=pw, CHROME=chrome, BASE="http://127.0.0.1:%d" % s.port, CODE=s.srv.auth.new_code(), CONTROL=s.control_code,
                   GATE_E2E=json.dumps({"project": shop.label, "spec": spec, "merge": merge}), SHOTS=os.environ.get("CC_DASH_SHOTS", ""))
        r = subprocess.run(["node", script], env=env, capture_output=True, text=True, timeout=120, cwd=BASE)
        try:
            rows = json.loads(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            check("gate UI: the walk ran", False, (r.stdout[-400:], r.stderr[-400:]))
            return
        for name, good, detail in rows:
            check("gate UI: " + name, good, detail)
        check("gate UI: the browser's decisions reached the ledger, the note too", shop.status(spec) == "approved" and shop.status(merge) == "approved"
              and gate_events(shop, spec)[-1].get("note") == "read it all", (shop.status(spec), shop.status(merge)))
    finally:
        s.close()


def order_file(shop, oid):
    d = os.path.join(shop.repo, "factory", "orders")
    return os.path.join(d, next(n for n in os.listdir(d) if n.startswith(oid)))


def edit_order(shop, oid, text="\nAn edit made after approval.\n"):
    with open(order_file(shop, oid), "a") as fh:
        fh.write(text)


def order_in(shop, title, state):
    """An order in `state`: queued, approved, approved+changed, failed, blocked, failed+changed, building, verifying."""
    oid = factory(shop.s, shop.repo, "new", title).split()[0]
    if state == "queued":
        return oid
    factory(shop.s, shop.repo, "mark", oid, "spec", "--no-holdout")
    st, out = shop.decide(oid)
    assert st == 200, out
    base = state.split("+")[0]
    if base != "approved":
        factory(shop.s, shop.repo, "mark", oid, base)
    if state.endswith("+changed"):
        edit_order(shop, oid)
    return oid


def test_gate_new_states():
    """Every state of the factory table has a card and a decision path; building and verifying have none."""
    s = gate_srv()
    calls = []
    try:
        shop = Shop(s)
        real_run = s.srv.gates._run
        s.srv.gates._run = lambda t: calls.append(t) or real_run(t)
        sentinel = "SENTINEL-HOLDOUT-NEVER-SHOWN"

        def plant(oid):
            h = os.path.join(shop.repo, ".factory-holdout", oid)
            os.makedirs(h, mode=0o700, exist_ok=True)
            with open(os.path.join(h, "S01-x.md"), "w") as fh:
                fh.write(sentinel + "\n")

        def leak(oid):
            bodies = []
            for path in ("/api/graph", "/api/plans", "/api/control/gate?project=%s&order=%s" % (quote(shop.label), oid)):
                bodies.append(s.req("GET", path)[2])
            return any(sentinel.encode() in (b if isinstance(b, bytes) else str(b).encode()) for b in bodies)

        # kind, decisions on the card
        for state, kind, dec in (("queued", "drop", (False, True)), ("approved", "drop", (False, True)), ("failed", "drop", (False, True)), ("blocked", "drop", (False, True)),
                                 ("approved+changed", "changed", (True, True)), ("failed+changed", "changed", (True, True)), ("blocked+changed", "changed", (True, True))):
            oid = order_in(shop, "State %s" % state, state)
            plant(oid)
            st, c = shop.card(oid)
            check("card %s: kind %s, approve %s, reject %s, gate spec" % (state, kind, dec[0], dec[1]),
                  st == 200 and c["kind"] == kind and (c["decisions"]["approve"], c["decisions"]["reject"]) == dec and c["gate"] == "spec"
                  and c["spec_changed"] == state.endswith("+changed") and len(c["spec_hash"]) == 64, c)
            if kind == "changed":
                check("card %s: the whole current order body" % state, "An edit made after approval." in c["body"], c.get("body", "")[-80:])
            check("holdout: %s: the sentinel is in no graph, plans or card response" % state, not leak(oid))
            if dec[0]:
                st, out = shop.decide(oid)
                e = gate_events(shop, oid)[-1]
                check("re-approve %s: 200, approved, a pass event with via dashboard, a ticket and the new spec hash" % state,
                      st == 200 and shop.status(oid) == "approved" and e["state"] == "pass" and e["via"] == "dashboard" and len(e["ticket"]) == 32
                      and e["spec_hash"] == c["spec_hash"], (st, out, e))
                st, c2 = shop.card(oid)
                check("re-approve %s: afterwards only a drop remains" % state, st == 200 and c2["kind"] == "drop", c2)
            else:
                n0 = len(calls)
                st, out = s.post("/api/control/gate", shop.body(c, "approve"))
                check("drop-only %s: a hand-built approve is refused, no ticket" % state, st == 409 and len(calls) == n0, (st, out))
                st, out = shop.decide(oid, "reject")
                e = gate_events(shop, oid)[-1]
                check("drop %s: 200, rejected, a fail event with via dashboard and a ticket" % state,
                      st == 200 and shop.status(oid) == "rejected" and e["state"] == "fail" and e["via"] == "dashboard" and len(e["ticket"]) == 32, (st, out, e))
        for state in ("building", "verifying"):
            oid = order_in(shop, "Running %s" % state, "approved")
            factory(s, shop.repo, "mark", oid, state)
            plant(oid)
            st, c = shop.card(oid)
            check("card %s: no decision on the dashboard (409)" % state, st == 409 and "no decision for you" in c.get("error", ""), (st, c))
            before = len(calls)
            fake = {"project": shop.label, "order": oid, "gate": "spec", "decision": "reject", "confirm": "reject", "view": "a" * 32, "spec_hash": "0" * 64}
            st, out = s.post("/api/control/gate", fake)
            check("hand-built POST for %s: 409, no ticket" % state, st == 409 and len(calls) == before, (st, out))
            check("holdout: %s: the sentinel is in no response" % state, not leak(oid))
        # A decision whose order changed state between the card and the POST.
        a = order_in(shop, "Moves under the card", "approved+changed")
        st, c = shop.card(a)
        factory(s, shop.repo, "mark", a, "building")
        before = len(calls)
        st, out = s.post("/api/control/gate", shop.body(c))
        check("refused: an order that went building after the card, 409, no ticket", st == 409 and len(calls) == before and shop.status(a) == "building", (st, out))
        q = order_in(shop, "Queued under the card", "approved")
        st, c = shop.card(q)
        factory(s, shop.repo, "mark", q, "queued")
        before = len(calls)
        st, out = s.post("/api/control/gate", shop.body(c, "approve"))
        check("refused: an approve built from a drop card for an order that is queued now", st == 409 and len(calls) == before, (st, out))
    finally:
        s.close()


def test_gate_new_state_refusals():
    """The refusal matrix of test_gate_refusals, for each new state."""
    s = gate_srv()
    calls = []
    try:
        shop = Shop(s)
        real_run = s.srv.gates._run
        s.srv.gates._run = lambda t: calls.append(t) or real_run(t)
        token = s.token
        for state in ("queued", "approved", "failed", "blocked", "approved+changed", "failed+changed", "blocked+changed"):
            oid = order_in(shop, "Refusals %s" % state, state)
            dec = "approve" if state.endswith("+changed") else "reject"
            st, c = shop.card(oid)
            other = order_in(shop, "Other %s" % state, state)
            st5, oc = shop.card(other)
            st, c = shop.card(oid)
            n, evs = len(calls), len(gate_events(shop, oid))
            s.token = s.read_token
            st1, out = s.post("/api/control/gate", shop.body(c, dec))
            s.token = None
            st2, _ = s.post("/api/control/gate", shop.body(c, dec), token=False)
            s.token = token
            check("refused %s: a read-only token (403) and no token (401)" % state, st1 == 403 and st2 == 401, (st1, st2))
            st3, out = s.post("/api/control/gate", shop.body(c, dec, confirm="yes"))
            check("refused %s: without the typed word (428)" % state, st3 == 428, (st3, out))
            st4, out = s.post("/api/control/gate", shop.body(c, dec, view="f" * 32))
            check("refused %s: another view id (409)" % state, st4 == 409, (st4, out))
            st6, out = s.post("/api/control/gate", dict(shop.body(c, dec), view=oc["view"]))
            check("refused %s: another order's view id (409)" % state, st6 == 409, (st6, out))
            st, c = shop.card(oid)
            edit_order(shop, oid, "\nChanged after the card.\n")
            st7, out = s.post("/api/control/gate", shop.body(c, dec))
            check("refused %s: a changed spec hash (409)" % state, st7 == 409 and len(calls) == n, (st7, out))
            st, c = shop.card(oid)
            st8, out = s.post("/api/control/gate", shop.body(c, dec))
            if st8 == 200:
                body = shop.body(c, dec)
                st9, out = s.post("/api/control/gate", body)
                check("refused %s: a spent view id (409)" % state, st9 == 409, (st9, out))
            check("refused %s: nothing but the final decision reached a ticket" % state, len(calls) == n + 1 and len(gate_events(shop, oid)) == evs + 1, (len(calls) - n, state))
    finally:
        s.close()


def merge_fixture(s, title="Merge case"):
    shop = Shop(s)
    oid, wt, head = shop.at_merge(title)
    return shop, oid, wt, head


def test_approve_merge():
    """Approve merge runs the merge itself, in fixture repositories only, and refuses where it must."""
    s = gate_srv()
    calls = []
    try:
        shop = Shop(s)
        real_run = s.srv.gates._run
        s.srv.gates._run = lambda t: calls.append(t) or real_run(t)
        tracked = os.path.join(shop.repo, "tracked.txt")
        with open(tracked, "w") as fh:
            fh.write("one\n")
        git(s, shop.repo, "add", "tracked.txt")
        git(s, shop.repo, "commit", "-q", "-m", "track")

        def refused(oid, why, label, expect):
            st, c = shop.card(oid)
            head0 = git(s, shop.repo, "rev-parse", "HEAD")
            n = len(calls)
            st2, out = s.post("/api/control/gate", shop.body(c)) if c.get("decisions", {}).get("approve") and not c.get("merge_blocked") else shop.decide(oid)
            if st2 == 200:
                check(label, False, (st2, out))
                return
            check(label, st2 == 409 and expect in out.get("error", "") and len(calls) == n and shop.status(oid) == "review"
                  and git(s, shop.repo, "rev-parse", "HEAD") == head0, (st2, out))

        # dirty tracked file
        m, wt, head = shop.at_merge("Dirty")
        with open(tracked, "a") as fh:
            fh.write("uncommitted\n")
        st, c = shop.card(m)
        check("merge card: a dirty tracked file shows merge_blocked", st == 200 and "uncommitted changes" in c["merge_blocked"], c)
        refused(m, "dirty", "merge refused: uncommitted tracked changes, HEAD unchanged, nothing written", "uncommitted")
        check("merge refused: the file is as the human left it", open(tracked).read() == "one\nuncommitted\n")
        git(s, shop.repo, "checkout", "-q", "--", "tracked.txt")
        # merge in progress
        gd = git(s, shop.repo, "rev-parse", "--absolute-git-dir")
        open(os.path.join(gd, "MERGE_HEAD"), "w").write(head + "\n")
        refused(m, "busy", "merge refused: a merge already in progress", "middle of a merge")
        os.unlink(os.path.join(gd, "MERGE_HEAD"))
        os.makedirs(os.path.join(gd, "rebase-merge"))
        refused(m, "rebase", "merge refused: a rebase in progress", "middle of a rebase")
        os.rmdir(os.path.join(gd, "rebase-merge"))
        # detached HEAD
        main_branch = git(s, shop.repo, "symbolic-ref", "--short", "HEAD")
        git(s, shop.repo, "checkout", "-q", "--detach")
        refused(m, "detached", "merge refused: HEAD is detached", "detached")
        git(s, shop.repo, "checkout", "-q", main_branch)
        # a filter driver in the repository config
        git(s, shop.repo, "config", "filter.evil.clean", "touch /tmp/never")
        refused(m, "filter", "merge refused: a filter defined in the repository config", "filter or merge driver")
        git(s, shop.repo, "config", "--unset", "filter.evil.clean")
        # drivers a narrow pattern missed: dotted subsections, includes, attributes naming a driver, info/attributes
        md, wtd, _h = shop.at_merge("Drivers")
        for name, text in ((".gitattributes", "a.dat filter=a.b\ni.dat filter=inc\np.dat filter=plain\nm.dat merge=mdrv\n"),
                           ("a.dat", "a\n"), ("i.dat", "i\n"), ("p.dat", "p\n"), ("m.dat", "m\n")):
            with open(os.path.join(wtd, name), "w") as fh:
                fh.write(text)
        git(s, wtd, "add", ".")
        git(s, wtd, "commit", "-q", "-m", "attributes")
        rec = {"t": time.time(), "order": md, "event": "status", "status": "review", "lane_head": git(s, wtd, "rev-parse", "HEAD"),
               "recommendation": "merge", "briefing": "Review passed.\nHoldout: S01 satisfied.", "by": "cc-factory"}
        with open(os.path.join(shop.repo, ".git", "cc_tool", "factory", "ledger.jsonl"), "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        gitdir = os.path.join(shop.repo, ".git")
        markers = [os.path.join(BASE, "marker-%s" % n) for n in ("dotted", "include", "includeif", "driver", "attrs")]
        porcelain0 = git(s, shop.repo, "status", "--porcelain", "--untracked-files=no")

        def driver_case(label, expect, mk):
            check("driver: %s: _merge_problem names it" % label, expect in s.srv.gates._merge_problem(shop.repo))
            refused(md, "driver", "merge refused: %s" % label, expect)
            check("driver: %s: no marker, tree untouched" % label,
                  not os.path.exists(mk) and git(s, shop.repo, "status", "--porcelain", "--untracked-files=no") == porcelain0)

        git(s, shop.repo, "config", "filter.a.b.smudge", "touch %s; cat" % markers[0])
        git(s, shop.repo, "config", "filter.a.b.clean", "cat")
        driver_case("a filter in a dotted subsection", "filter or merge driver", markers[0])
        git(s, shop.repo, "config", "--remove-section", "filter.a.b")
        with open(os.path.join(gitdir, "drv.inc"), "w") as fh:
            fh.write('[filter "inc"]\n\tsmudge = touch %s; cat\n' % markers[1])
        git(s, shop.repo, "config", "include.path", "drv.inc")
        driver_case("a filter reached through include.path", "filter or merge driver", markers[1])
        git(s, shop.repo, "config", "--unset", "include.path")
        incond = "includeIf.gitdir:%s.path" % os.path.join(shop.repo, ".git")
        git(s, shop.repo, "config", incond, "drv.inc")
        driver_case("a filter reached through includeIf", "filter or merge driver", markers[1])
        git(s, shop.repo, "config", "--unset", incond)
        os.unlink(os.path.join(gitdir, "drv.inc"))
        git(s, shop.repo, "config", "merge.mdrv.driver", "touch %s; false" % markers[3])
        driver_case("a merge driver named in .gitattributes with its program in config", "filter or merge driver", markers[3])
        git(s, shop.repo, "config", "--unset", "merge.mdrv.driver")
        os.makedirs(os.path.join(gitdir, "info"), exist_ok=True)
        with open(os.path.join(gitdir, "info", "attributes"), "w") as fh:
            fh.write("p.dat filter=plain\n")
        driver_case("attributes in info/attributes", "info/attributes", markers[4])
        os.unlink(os.path.join(gitdir, "info", "attributes"))
        check("driver: with everything undone, _merge_problem is empty", s.srv.gates._merge_problem(shop.repo) == "", s.srv.gates._merge_problem(shop.repo))
        st, out = shop.decide(md)
        check("driver: the positive control merges the reviewed head and no marker exists",
              st == 200 and git(s, shop.repo, "rev-parse", "HEAD^2") == git(s, wtd, "rev-parse", "HEAD") and not any(os.path.exists(m) for m in markers), (st, out))
        # a conflict is aborted
        with open(os.path.join(wt, "tracked.txt"), "w") as fh:
            fh.write("lane side\n")
        git(s, wt, "add", "tracked.txt")
        git(s, wt, "commit", "-q", "-m", "lane edit")
        lane_head = git(s, wt, "rev-parse", "HEAD")
        rec = {"t": time.time(), "order": m, "event": "status", "status": "review", "lane_head": lane_head, "recommendation": "merge",
               "briefing": "Review passed.\nHoldout: S01 satisfied.", "by": "cc-factory"}
        with open(os.path.join(shop.repo, ".git", "cc_tool", "factory", "ledger.jsonl"), "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        with open(tracked, "w") as fh:
            fh.write("main side\n")
        git(s, shop.repo, "commit", "-q", "-am", "main edit")
        head1 = git(s, shop.repo, "rev-parse", "HEAD")
        st, out = shop.decide(m)
        check("merge conflict: 409, says it aborted and the checkout is as it was; HEAD, tree and MERGE_HEAD clean; no event",
              st == 409 and "aborted" in out.get("error", "") and "as it was" in out.get("error", "") and git(s, shop.repo, "rev-parse", "HEAD") == head1
              and git(s, shop.repo, "status", "--porcelain", "--untracked-files=no") == "" and not os.path.exists(os.path.join(gd, "MERGE_HEAD"))
              and shop.status(m) == "review", (st, out))
        # already merged: only records
        m2, wt2, head2 = shop.at_merge("Already merged")
        git(s, shop.repo, "merge", "-q", "--no-ff", "-m", "by hand", head2)
        tip = git(s, shop.repo, "rev-parse", "HEAD")
        st, c = shop.card(m2)
        check("merge card: an already-merged head says so", st == 200 and c["merged"] is True, c)
        st, out = shop.decide(m2)
        check("already merged: records without a new commit", st == 200 and shop.status(m2) == "done" and git(s, shop.repo, "rev-parse", "HEAD") == tip, (st, out))
        # the lane moved
        m3, wt3, head3 = shop.at_merge("Moved lane")
        git(s, wt3, "commit", "-q", "--allow-empty", "-m", "more")
        tip = git(s, shop.repo, "rev-parse", "HEAD")
        st, out = shop.decide(m3)
        check("lane moved: refused as before, nothing merged", st == 409 and "moved after the review" in out.get("error", "") and git(s, shop.repo, "rev-parse", "HEAD") == tip, (st, out))
        # a merge that succeeded but whose record is refused says how to record it
        m4, wt4, head4 = shop.at_merge("Record refused")
        real = s.srv.gates._run
        s.srv.gates._run = lambda t: (1, "", "cc-factory said no")
        st, out = shop.decide(m4)
        s.srv.gates._run = real
        check("merge made but not recorded: the 409 says the merge exists and the terminal command that records it",
              st == 409 and "Merged" in out.get("error", "") and "cc-factory approve %s --gate merge" % m4 in out.get("error", "") and git(s, shop.repo, "rev-parse", "HEAD^2") == head4, (st, out))
        check("the dashboard never pushed or touched the lanes", all(os.path.isdir(w) for w in (wt, wt2, wt3, wt4)))
    finally:
        s.close()


def test_gate_stress():
    """20 decisions while short-lived processes named `claude` start and exit beside them: none is refused as a hidden process."""
    s = gate_srv()
    try:
        shop = Shop(s)
        specs = [shop.at_spec("Stress spec %d" % i) for i in range(10)]
        queued = [factory(s, shop.repo, "new", "Stress queued %d" % i).split()[0] for i in range(10)]
        results = []
        with gate_fixture.claude_churn(os.path.join(BASE, "churn")) as churn:
            for oid in specs:
                results.append((oid, "approve") + shop.decide(oid))
            for oid in queued:
                results.append((oid, "reject") + shop.decide(oid, "reject"))
        ok = 0
        for oid, dec, st, out in results:
            es = gate_events(shop, oid)
            if st == 200 and es and es[-1].get("via") == "dashboard" and len(es[-1].get("ticket", "")) == 32:
                ok += 1
        check("stress: 20 decisions under churn, all 200 with a dashboard gate event and a ticket", len(results) == 20 and ok == 20,
              [(o, d, st, str(out)[:120]) for o, d, st, out in results if st != 200][:3])
        check("stress: no response mentions a hidden process", not any("hidden process" in json.dumps(r[3]) for r in results))
        check("stress: the churn really ran (more than 20 processes started)", churn.started > 20, churn.started)
    finally:
        s.close()


def test_outside_kinds():
    """gates.order_outside and blocked_parts: the next step a stuck order needs, built from facts, with nothing run."""
    from cc_dash import gates
    key = {"kind", "why", "where", "steps", "then"}

    def box(**kw):
        o = {"id": "FO-007", "status": "queued", "main": "~/p", "lane": None, "why": None}
        o.update(kw)
        return gates.order_outside(o)
    x = box(status="verifying")
    check("outside: a verifying order gets dead-run: mark failed, then rerun in a Claude Code session",
          x and x["kind"] == "dead-run" and x["steps"][0]["command"] == 'cc-factory mark FO-007 failed --note "run died"'
          and x["steps"][1]["command"] == "/factory run FO-007" and x["steps"][1]["where"]["app"] == "claude"
          and x["where"] == {"app": "terminal", "path": "~/p"} and "Only when the run is dead" in x["why"], x)
    lane = "~/p/.claude/worktrees/FO-009-x"
    x = box(id="FO-009", status="blocked", lane={"worktree": lane}, why={"note": "needs-deletion: app/old.py, docs/x y.md"})
    cmds = [st["command"] for st in x["steps"]] if x else []
    check("outside: needs-deletion: git rm per path in the lane, a commit, then the rerun",
          x and x["kind"] == "needs-deletion" and cmds == ["git -C %s rm -- app/old.py" % lane, "git -C %s rm -- 'docs/x y.md'" % lane,
                                                           "git -C %s commit -m 'FO-009: remove app/old.py, docs/x y.md'" % lane, "/factory run FO-009"], cmds)
    x = box(id="FO-009", status="blocked", lane={"worktree": lane}, why={"note": "needs-deletion: x; rm -rf ~, ../../etc/passwd, -rf, /abs, ok.txt"})
    cmds = [st["command"] for st in x["steps"]] if x else []
    check("outside: hostile paths are shell-quoted or dropped", x and "git -C %s rm -- 'x; rm -rf ~'" % lane in cmds
          and not any("passwd" in c or "-- -rf" in c or "/abs" in c for c in cmds) and "git -C %s rm -- ok.txt" % lane in cmds, cmds)
    x = box(status="blocked", why={"note": "lane-tampered: HEAD moved"}, lane={"worktree": lane})
    check("outside: lane-tampered: status and diff of the lane, then rewrite the scenarios",
          x and x["kind"] == "lane-tampered" and [st["command"] for st in x["steps"]] == ["git -C %s status" % lane, "git -C %s diff" % lane]
          and "scenarios" in x["then"], x)
    x = box(status="failed")
    check("outside: a failed order gets rerun in a Claude Code session in the main checkout",
          x and x["kind"] == "rerun" and x["steps"][0]["command"] == "/factory run FO-007" and x["steps"][0]["where"] == {"app": "claude", "path": "~/p"}, x)
    check("outside: no main means an empty path", box(status="failed", main="")["where"]["path"] == "")
    check("outside: queued, review and done orders have none; a bad id has none",
          all(box(status=t) is None for t in ("queued", "approved", "review", "done", "rejected")) and gates.order_outside({"id": "x; rm", "status": "failed"}) is None)
    boxes = [box(status="verifying"), box(status="failed"), box(status="blocked", lane={"worktree": lane}, why={"note": "needs-deletion: a.py"}),
             box(status="blocked", why={"note": "lane-tampered: x"})]
    check("outside: every box has exactly the contract's keys, every step a command and where",
          all(set(b) == key and all(set(st) == {"command", "where"} for st in b["steps"]) for b in boxes), boxes)
    check("outside: blocked_parts splits the reason and the suggestion; other text is None",
          gates.blocked_parts("BLOCKED: odd thing. Suggestion: do this.") == {"reason": "odd thing.", "suggestion": "do this."}
          and gates.blocked_parts("nope") is None and gates.blocked_parts("BLOCKED: only.") == {"reason": "only.", "suggestion": ""})


def test_outside_refusals():
    """Refusals at the gates carry a structured next step (or Reload), and error keeps the raw text."""
    from cc_dash import procs
    from cc_dash.parser import tilde
    s = gate_srv()
    seen = []
    try:
        shop = Shop(s)
        gates = s.srv.gates
        real_run = gates._run
        tracked = os.path.join(shop.repo, "tracked.txt")
        with open(tracked, "w") as fh:
            fh.write("one\n")
        git(s, shop.repo, "add", "tracked.txt")
        git(s, shop.repo, "commit", "-q", "-m", "track")
        m, wt, head = shop.at_merge("Dirty then conflict")
        with open(tracked, "a") as fh:
            fh.write("uncommitted\n")
        st, c = shop.card(m)
        mo = c.get("merge_outside") or {}
        seen.append(mo)
        cmds = [x["command"] for x in mo.get("steps", [])]
        check("outside: a dirty checkout: the card has a dirty-checkout box in a terminal in the main checkout",
              st == 200 and mo.get("kind") == "dirty-checkout" and mo["where"] == {"app": "terminal", "path": tilde(shop.repo)}
              and "git stash push" in cmds and "Approve merge" in mo["then"] and "uncommitted" in c["merge_blocked"], c)
        st, out = s.post("/api/control/gate", shop.body(shop.card(m)[1]))
        check("outside: ...and the POST's 409 carries it, with the raw text in error", st == 409 and out.get("outside", {}).get("kind") == "dirty-checkout"
              and "uncommitted" in out.get("error", ""), (st, out))
        git(s, shop.repo, "checkout", "-q", "--", "tracked.txt")
        gd = git(s, shop.repo, "rev-parse", "--absolute-git-dir")
        open(os.path.join(gd, "MERGE_HEAD"), "w").write(head + "\n")
        mo = shop.card(m)[1].get("merge_outside") or {}
        seen.append(mo)
        check("outside: a merge in progress: git-busy with git merge --abort", mo.get("kind") == "git-busy" and "git merge --abort" in [x["command"] for x in mo["steps"]], mo)
        os.unlink(os.path.join(gd, "MERGE_HEAD"))
        os.makedirs(os.path.join(gd, "rebase-merge"))
        mo = shop.card(m)[1].get("merge_outside") or {}
        seen.append(mo)
        check("outside: a rebase in progress: git-busy with git rebase --abort", mo.get("kind") == "git-busy" and "git rebase --abort" in [x["command"] for x in mo["steps"]], mo)
        os.rmdir(os.path.join(gd, "rebase-merge"))
        check("outside: a clean checkout has no merge_outside", shop.card(m)[1].get("merge_outside") is None)
        # a conflict is aborted
        with open(os.path.join(wt, "tracked.txt"), "w") as fh:
            fh.write("lane side\n")
        git(s, wt, "add", "tracked.txt")
        git(s, wt, "commit", "-q", "-m", "lane edit")
        lane_head = git(s, wt, "rev-parse", "HEAD")
        rec = {"t": time.time(), "order": m, "event": "status", "status": "review", "lane_head": lane_head, "recommendation": "merge",
               "briefing": "Review passed.\nHoldout: S01 satisfied.", "by": "cc-factory"}
        with open(os.path.join(shop.repo, ".git", "cc_tool", "factory", "ledger.jsonl"), "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        with open(tracked, "w") as fh:
            fh.write("main side\n")
        git(s, shop.repo, "commit", "-q", "-am", "main edit")
        st, out = shop.decide(m)
        o = out.get("outside") or {}
        seen.append(o)
        check("outside: a merge conflict (aborted): merge-conflict, first step git merge --no-ff <reviewed head>, error still says aborted",
              st == 409 and o.get("kind") == "merge-conflict" and o["steps"][0]["command"] == "git merge --no-ff " + lane_head and "aborted" in out.get("error", ""), (st, out))
        # merged, but the record is refused
        r, _wt, _rh = shop.at_merge("Record refused")
        gates._run = lambda t: (1, "", "cc-factory said no")
        st, out = shop.decide(r)
        o = out.get("outside") or {}
        seen.append(o)
        check("outside: merged but cc-factory refused the record: record-merge with cc-factory approve <id> --gate merge",
              st == 409 and o.get("kind") == "record-merge" and o["steps"][0]["command"] == "cc-factory approve %s --gate merge" % r and "Merged" in out.get("error", ""), (st, out))
        # ticket refused at the spec gate: the command is built from the order
        a = shop.at_spec("Ticket refused")
        gates._run = lambda t: (2, "", "BLOCKED: approve is a human's decision, and this cc-factory has no valid ticket from your dashboard (x). "
                                       "Suggestion: run it from your own terminal: cc-factory approve FO-1 --gate spec.")
        st, out = shop.decide(a)
        o = out.get("outside") or {}
        seen.append(o)
        check("outside: a refused ticket: decide-in-terminal, the command built from the order, raw text kept",
              st == 409 and o.get("kind") == "decide-in-terminal" and o["steps"][0]["command"] == "cc-factory approve %s --gate spec" % a and "no valid ticket" in out.get("error", ""), (st, out))
        # an unknown refusal: two labelled parts
        b = shop.at_spec("Unknown refusal")
        moon = "BLOCKED: the moon is full. Suggestion: wait."
        gates._run = lambda t: (2, "", moon)
        st, c = shop.card(b)
        body = shop.body(c)
        st, out = s.post("/api/control/gate", body)
        check("outside: an unrecognised BLOCKED refusal: reason and suggestion as parts, no outside box",
              st == 409 and out.get("blocked") == {"reason": "the moon is full.", "suggestion": "wait."} and "outside" not in out and "the moon" in out["error"], (st, out))
        # what the dashboard can resolve itself: Reload and no command
        st, out = s.post("/api/control/gate", body)
        check("outside: a spent view id: reload, no outside, no blocked", st == 409 and out.get("reload") is True and "outside" not in out and "blocked" not in out, (st, out))
        gates._run = real_run
        st, c = shop.card(b)
        path = next(n for n in os.listdir(os.path.join(shop.repo, "factory", "orders")) if n.startswith(b))
        with open(os.path.join(shop.repo, "factory", "orders", path), "a") as fh:
            fh.write("\nedited after reading\n")
        st, out = s.post("/api/control/gate", shop.body(c))
        check("outside: a spec edited after reading: reload, no outside", st == 409 and out.get("reload") is True and "outside" not in out and "changed" in out.get("error", ""), (st, out))
        # no /proc
        real = procs.have_proc
        a2 = shop.at_spec("No proc")
        st, c = shop.card(a2)
        procs.have_proc = lambda: False
        try:
            st, out = s.post("/api/control/gate", shop.body(c))
        finally:
            procs.have_proc = real
        o = out.get("outside") or {}
        seen.append(o)
        check("outside: no /proc: 403, no-proc box that says how to start control mode", st == 403 and o.get("kind") == "no-proc"
              and [x["command"] for x in o["steps"]] == ["ccdash --control"] and "/proc" in out.get("error", ""), (st, out))
        check("outside: no command in any box has a newline", seen and all("\n" not in st_["command"] for o in seen for st_ in o.get("steps", [])), seen)
    finally:
        s.close()


# ── FO-004: the code record, the restart, IDE terminals ──────────────────────
def test_code_box():
    """Code that is not what you approved shows one box on the card before a decision is tried, and a decision is refused
    with no ticket and no event; code that only changed after the dashboard started asks for a restart."""
    s = gate_srv()
    f = os.path.join(os.path.dirname(os.path.dirname(GATE_CLI)), "lib", "cc_dash", "graph.py")
    with open(f, encoding="utf-8") as fh:
        original = fh.read()
    try:
        shop = Shop(s)
        a = shop.at_spec()
        st, c = shop.card(a)
        check("code box: code that matches the record shows no box", st == 200 and c.get("code_outside") is None, c.get("code_outside"))
        with open(f, "a", encoding="utf-8") as fh:
            fh.write("\n# edited by hand\n")
        st, c = shop.card(a)
        box = c.get("code_outside") or {}
        cmds = [x["command"] for x in box.get("steps", [])]
        check("code box: edited code shows ONE box: record it once for all projects, then restart, no per-project command",
              st == 200 and box.get("kind") == "record-code" and cmds == ["cc-dash --record-code", "ccdash --control"]
              and "cc-update-project" not in json.dumps(box), box)
        n = len(shop.ledger(a))
        st, out = s.post("/api/control/gate", shop.body(c))
        check("code box: a decision is refused with the same box, and nothing is written",
              st == 409 and (out.get("outside") or {}).get("kind") == "record-code" and len(shop.ledger(a)) == n and shop.status(a) == "spec", (st, out))
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(original)
        s.srv.gates.start = time.time() + 2     # the restore just set a new ctime; a slow run must not outlive the +2 s set at start
        st, c = shop.card(a)
        check("code box: with the code back as recorded, the box is gone", st == 200 and c.get("code_outside") is None, c.get("code_outside"))
        rec = os.path.join(os.environ["XDG_STATE_HOME"], "cc_tool", "cc_dash_code.json")
        s.srv.gates.start = os.path.getctime(rec) - 5
        st, c = shop.card(a)
        box = c.get("code_outside") or {}
        check("code box: code that matches but is newer than this dashboard asks for a restart (a button, one command)",
              box.get("kind") == "restart" and box.get("restart") is True and [x["command"] for x in box["steps"]] == ["ccdash --control"], box)
    finally:
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(original)
        s.close()


def test_restart_endpoint():
    from cc_dash import server as server_mod
    s = gate_srv()
    f = os.path.join(os.path.dirname(os.path.dirname(GATE_CLI)), "lib", "cc_dash", "graph.py")
    with open(f, encoding="utf-8") as fh:
        original = fh.read()
    try:
        Shop(s)
        control = s.token
        s.token = s.read_token
        st, _out = s.post("/api/control/restart", {})
        check("restart: a read-only token is refused", st == 403 and not s.srv.restart_requested, st)
        s.token = control
        st, out = s.post("/api/control/restart", {})
        check("restart: with nothing to restart for, 409 and no restart", st == 409 and not s.srv.restart_requested, (st, out))
        with open(f, "a", encoding="utf-8") as fh:
            fh.write("\n# edited by hand\n")
        st, out = s.post("/api/control/restart", {})
        check("restart: while the code is not the recorded code, 409: a restart would pass nothing", st == 409 and not s.srv.restart_requested, (st, out))
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(original)
        s.srv.gates.start = os.path.getctime(os.path.join(os.environ["XDG_STATE_HOME"], "cc_tool", "cc_dash_code.json")) - 5
        st, out = s.post("/api/control/restart", {})
        check("restart: recorded code newer than the dashboard: 200, and the server was asked to restart", st == 200 and s.srv.restart_requested is True, (st, out))
        check("restart: the new process is started without --ready-fd",
              server_mod.restart_argv(["--control", "--ready-fd", "5", "--no-open"]) == ["--control", "--no-open"]
              and server_mod.restart_argv(["--ready-fd=7", "--port", "1"]) == ["--port", "1"])
    finally:
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(original)
        s.close()


def ide_proc(fp, claude_flags="02104002", scope="1", extra=(), holders_extra=None):
    """A fake /proc for an IDE terminal. Cursor's pty host (110) holds the masters of two tabs and leaks them into the shells it
    starts (zsh 200, this dashboard's); a sibling tab's zsh (250) runs a claude (300) that inherited the master of pts 0 from 110
    (descriptor flags `claude_flags`). `scope` is kernel.yama.ptrace_scope ('' for no such file)."""
    shutil.rmtree(fp, ignore_errors=True)
    rows = [(50, "systemd", 1, 50, 50, 0, ["/usr/lib/systemd/systemd"], "/usr/lib/systemd/systemd"),
            (110, "cursor", 50, 110, 110, 0, ["/usr/share/cursor/cursor"], "/usr/share/cursor/cursor"),
            (200, "zsh", 110, 200, 200, 34816, ["zsh", "-i"], "/usr/bin/zsh"),
            (250, "zsh", 110, 250, 250, 34817, ["zsh", "-i"], "/usr/bin/zsh"),
            (300, "claude", 250, 250, 250, 34817, ["claude"], "/usr/local/bin/claude"),
            (400, "python3", 200, 400, 200, 34816, [PY, "-I", "/x/bin/cc-dash", "--control"], PY)] + list(extra)
    holders = {110: [(0, "0100002"), (5, "0100002")], 200: [(0, "0100002"), (5, "0100002")], 300: [(0, claude_flags)]}
    holders.update(holders_extra or {})
    for pid, comm, ppid, pgrp, sid, tty, argv, exe in rows:
        d = os.path.join(fp, str(pid))
        os.makedirs(os.path.join(d, "fd"))
        os.makedirs(os.path.join(d, "fdinfo"))
        with open(os.path.join(d, "stat"), "w") as fh:
            fh.write("%d (%s) S %d %d %d %d -1 4194560 0 0 0 0 0 0 0 0 20 0 1 0 0 0 0\n" % (pid, comm, ppid, pgrp, sid, tty))
        with open(os.path.join(d, "cmdline"), "wb") as fh:
            fh.write(b"\0".join(a.encode() for a in argv) + b"\0")
        with open(os.path.join(d, "status"), "w") as fh:
            fh.write("Name:\t%s\nUid:\t%d\t%d\t%d\t%d\n" % ((comm,) + (os.getuid(),) * 4))
        with open(os.path.join(d, "environ"), "wb") as fh:
            fh.write(b"HOME=/h\0PATH=/usr/bin\0")
        os.symlink(exe, os.path.join(d, "exe"))
        for i, (index, flags) in enumerate(holders.get(pid, ())):
            os.symlink("/dev/ptmx", os.path.join(d, "fd", str(10 + i)))
            with open(os.path.join(d, "fdinfo", str(10 + i)), "w") as fh:
                fh.write("pos:\t0\nflags:\t%s\nmnt_id:\t29\nino:\t90\ntty-index:\t%d\n" % (flags, index))
    if scope:
        os.makedirs(os.path.join(fp, "sys", "kernel", "yama"))
        with open(os.path.join(fp, "sys", "kernel", "yama", "ptrace_scope"), "w") as fh:
            fh.write(scope + "\n")
    return fp


def test_ide_terminal():
    procs = gate_fixture.load_procs()
    fp = os.path.join(BASE, "ideproc")
    ide_proc(fp)
    why = procs.terminal_problem(400, proc=fp)
    check("IDE: a claude in a sibling tab that inherited the master with O_CLOEXEC (ptrace_scope 1) passes", why == "", why)
    ide_proc(fp, scope="2")
    check("IDE: ... and under ptrace_scope 2", procs.terminal_problem(400, proc=fp) == "")
    ide_proc(fp, claude_flags="0100002")
    why = procs.terminal_problem(400, proc=fp)
    check("IDE: a claude holder whose master descriptor lacks O_CLOEXEC is refused, naming claude", "claude" in why and "process 300" in why, why)
    ide_proc(fp, scope="0")
    why = procs.terminal_problem(400, proc=fp)
    check("IDE: ptrace_scope 0 refuses the same claude", "claude" in why and "process 300" in why, why)
    ide_proc(fp, scope="")
    check("IDE: no ptrace_scope file (no Yama) refuses it too (fail closed)", "claude" in procs.terminal_problem(400, proc=fp))
    ide_proc(fp, extra=[(310, "node", 300, 250, 250, 34817, ["node", "x.js"], "/usr/bin/node")], holders_extra={310: [(0, "02104002")]})
    why = procs.terminal_problem(400, proc=fp)
    check("IDE: a descendant of claude holding the master is still refused, even with O_CLOEXEC", "descends from" in why and "process 310" in why, why)
    ide_proc(fp, extra=[(320, "claude", 1, 320, 320, 0, ["claude"], "/usr/local/bin/claude")], holders_extra={320: [(0, "02104002")]})
    why = procs.terminal_problem(400, proc=fp)
    check("IDE: a claude that did not inherit from a terminal host (it IS the holder) is refused", "claude" in why and "process 320" in why, why)

    # A helper (cpuUsage.sh's sed) holds the master at scan time and is gone when it is read.
    ide_proc(fp)
    real, calls = procs.pty_master_scan, []

    def scan(index, proc="/proc", late=None):
        calls.append(index)
        found = real(index, proc, late=late)
        return (sorted(found[0] + [777]) if len(calls) == 1 else found[0], found[1])
    procs.pty_master_scan = scan
    try:
        why = procs.terminal_problem(400, proc=fp)
        check("IDE: a holder that vanished during the scan is rescanned once, and the terminal passes", why == "" and len(calls) == 2, (why, calls))
        ide_proc(fp, extra=[(778, "helper", 999, 778, 778, 0, ["helper"], "/usr/bin/helper")], holders_extra={778: [(0, "0100002")]})
        calls.clear()
        why = procs.terminal_problem(400, proc=fp)
        check("IDE: a holder that is still there with an unreadable parent chain is never assumed gone: refused", "parent chain" in why and "process 778" in why, why)
    finally:
        procs.pty_master_scan = real
    # A holder that vanishes on the rescan as well is never assumed gone.
    ide_proc(fp)
    calls.clear()

    def scan_twice(index, proc="/proc", late=None):
        calls.append(index)
        found = real(index, proc, late=late)
        return (sorted(found[0] + [777]), found[1])
    procs.pty_master_scan = scan_twice
    try:
        why = procs.terminal_problem(400, proc=fp)
        check("IDE: a holder that vanishes on both scans refuses (kept changing), never read as gone",
              "kept changing" in why and len(calls) == 2, (why, calls))
    finally:
        procs.pty_master_scan = real


def test_control_refusal_fix():
    """Every refusal to start control mode ends with one plain sentence saying what to do, and none points at cc-update-project."""
    from cc_dash import server as server_mod
    d = os.path.join(BASE, "refusal-fix", "control")
    os.makedirs(d, mode=0o700)
    fp = os.path.join(BASE, "refproc")
    ide_proc(fp, claude_flags="0100002")
    cases = [("Claude env", dict(env={"CLAUDECODE": "1"}, ancestors=[]), "where Claude Code is not running"),
             ("Claude parent", dict(env={}, ancestors=[(9, "claude", "/usr/local/bin/claude", ["claude"])]), "where Claude Code is not running"),
             ("IDE holder", dict(env={}, proc=fp, me=400), "outside the IDE")]
    for label, kw, fix in cases:
        why = server_mod.control_refusal(d, note=lambda _t: None, **kw)
        check("refusal: %s ends with one fix sentence" % label, why.endswith(".") and fix in why.rsplit(". ", 1)[-1] and "cc-update-project" not in why, why)
    for text in ("a hidden process descending from Claude Code could hold this terminal", "the parent chain of process 4 cannot be read",
                 "process 5 is detached", "the terminal showing the control code (/dev/pts/0) is held by /usr/bin/sed (process 2)"):
        fix = server_mod._fix_for(text)
        check("refusal: a fix sentence for %r" % text[:40], fix.endswith(".") and fix[0].isupper() and "cc-update-project" not in fix, fix)


def _write_review(shop, oid, head, briefing="Review passed.\nHoldout: S01 satisfied."):
    rec = {"t": time.time(), "order": oid, "event": "status", "status": "review", "lane_head": head, "recommendation": "merge",
           "briefing": briefing, "by": "cc-factory"}
    with open(os.path.join(shop.repo, ".git", "cc_tool", "factory", "ledger.jsonl"), "a") as fh:
        fh.write(json.dumps(rec) + "\n")


def test_merge_attr_window():
    """FO-005: a filter driver added to the repository config between _merge_block and the merge is not run through the
    tree's .gitattributes (the merge takes attributes from the empty tree), and the merge turns submodule recursion off."""
    from cc_dash import gates as gates_mod
    s = gate_srv()
    real_git = gates_mod.selfheal._git
    try:
        shop = Shop(s)
        m, wt, _h = shop.at_merge("Attr window")
        marker = os.path.join(BASE, "marker-window")
        with open(os.path.join(wt, ".gitattributes"), "w") as fh:
            fh.write("*.txt filter=evil merge=evilm\n")
        with open(os.path.join(wt, "w.txt"), "w") as fh:
            fh.write("window\n")
        git(s, wt, "add", ".")
        git(s, wt, "commit", "-q", "-m", "attributes")
        head = git(s, wt, "rev-parse", "HEAD")
        _write_review(shop, m, head)
        st, c = shop.card(m)
        body = shop.body(c)
        armed, ran, argvs, at_run = [], [], [], []
        real_block = gates_mod.Gates._merge_block

        def block(main):
            res = real_block(main)
            if armed and res == ("", "") and not ran:
                def writer():
                    for k, v in (("filter.evil.smudge", "touch %s; cat" % marker), ("filter.evil.clean", "cat"),
                                 ("merge.evilm.driver", "touch %s; false" % marker)):
                        subprocess.run(["git", "config", k, v], cwd=shop.repo, check=True)
                th = threading.Thread(target=writer)
                th.start()
                th.join()
                ran.append(1)
            return res

        def spy(root, args, *a, **kw):
            argvs.append(list(args))
            return real_git(root, args, *a, **kw)
        s.srv.gates._merge_block = block
        gates_mod.selfheal._git = spy
        real_run = s.srv.gates._run
        s.srv.gates._run = lambda t: at_run.append(os.path.exists(marker)) or (1, "", "stub")
        armed.append(1)
        st, out = s.post("/api/control/gate", body)
        s.srv.gates._run = real_run
        gates_mod.selfheal._git = real_git
        merge_argv = next((a for a in argvs if "merge" in a and "--no-ff" in a), [])
        check("merge window: the driver was added between the check and the merge, and the merge still made the commit",
              ran and git(s, shop.repo, "rev-parse", "HEAD^2") == head, (st, out, ran))
        check("merge window: no marker file after the merge (attributes come from the empty tree)", at_run == [False] and not os.path.exists(marker), at_run)
        check("merge window: the merge ran with submodule.recurse=false and an empty --attr-source",
              "-c" in merge_argv and "submodule.recurse=false" in merge_argv and any(a.startswith("--attr-source=") for a in merge_argv), merge_argv)
    finally:
        gates_mod.selfheal._git = real_git
        s.close()


def test_merge_clock_bound():
    """FO-005: the merge clock rounds up, so a code file changed in the same second as cc-dash's timestamp, before it,
    is not exempt (the real code_after_start, not the gate_fixture stub)."""
    from cc_dash import gates as gates_mod
    procs = gate_fixture.load_procs()
    cli = gate_fixture.make_tree(BASE, "clock")
    tree = os.path.dirname(os.path.dirname(cli))
    gate_fixture.plant_record(tree, tree)
    manifest = os.path.join(os.environ["XDG_STATE_HOME"], "cc_tool", procs.CODE_RECORD_NAME)
    target = os.path.join(tree, "lib", "cc_dash", "graph.py")
    time.sleep(0.1)  # the tree and the record predate the change by more than the margin below
    for _ in range(40):
        if time.time() % 1 < 0.3:
            break
        time.sleep(0.05)
    with open(target, "a", encoding="utf-8") as fh:
        fh.write("\n# clock\n")
    ts = time.time()
    c = os.stat(target).st_ctime_ns / 1e9
    since = gates_mod.merge_clock()
    check("merge clock: the bound is no earlier than the timestamp and the second has begun", since >= ts and time.time() >= since, (since, ts))
    exempt = ("lib/cc_dash/graph.py", "lib/cc_dash", "lib")
    why = procs.code_after_start(os.getpid(), tree, manifest, start=c + procs.START_MARGIN - 0.01, exempt=exempt, exempt_since=since)
    check("merge clock: a code file changed in the same second as the timestamp, before it, is still refused", "graph.py" in why, why)
    if int(c) == int(ts):
        why = procs.code_after_start(os.getpid(), tree, manifest, start=c + procs.START_MARGIN - 0.01, exempt=exempt, exempt_since=int(ts))
        check("merge clock: control: the old floored bound would have exempted it", "graph.py" not in why, why)


def test_merge_commit_time():
    """FO-005: the merge commit's committer time is the rounded-up clock, taken no earlier than just before the
    merge, and every file the merge writes is no older than it."""
    s = gate_srv()
    try:
        shop = Shop(s)
        m, wt, _h = shop.at_merge("Commit time")
        with open(os.path.join(wt, "m.txt"), "w") as fh:
            fh.write("m\n")
        git(s, wt, "add", ".")
        git(s, wt, "commit", "-q", "-m", "m")
        head = git(s, wt, "rev-parse", "HEAD")
        _write_review(shop, m, head)
        st, c = shop.card(m)
        body = shop.body(c)
        real_run = s.srv.gates._run
        s.srv.gates._run = lambda t: (1, "", "stub")
        try:
            before = time.time()
            st, out = s.post("/api/control/gate", body)
        finally:
            s.srv.gates._run = real_run
        ct = int(git(s, shop.repo, "show", "-s", "--format=%ct", "HEAD"))
        check("merge commit time: the merge happened", git(s, shop.repo, "rev-parse", "HEAD^2") == head, (st, out))
        check("merge commit time: rounded-up clock, within 2 s of the request", ct >= before and ct - before < 2, (ct, before))
        check("merge commit time: the merged file is no older than the commit time",
              os.stat(os.path.join(shop.repo, "m.txt")).st_ctime >= ct, ct)
    finally:
        s.close()


def test_merge_submodule():
    """FO-005: with submodule.recurse=true and a submodule whose own config names a filter driver, the merge writes no marker."""
    s = gate_srv()
    try:
        shop = Shop(s)
        sub = os.path.join(s.root, "subsrc")
        os.makedirs(sub)
        git(s, sub, "init", "-q")
        git(s, sub, "commit", "-q", "--allow-empty", "-m", "sub initial")
        git(s, shop.repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", sub, "sub")
        git(s, shop.repo, "commit", "-q", "-m", "add submodule")
        git(s, shop.repo, "config", "submodule.recurse", "true")
        marker = os.path.join(BASE, "marker-submodule")
        git(s, os.path.join(shop.repo, "sub"), "config", "filter.evil.smudge", "touch %s; cat" % marker)
        git(s, os.path.join(shop.repo, "sub"), "config", "filter.evil.clean", "cat")
        with open(os.path.join(sub, ".gitattributes"), "w") as fh:
            fh.write("*.txt filter=evil\n")
        with open(os.path.join(sub, "s.txt"), "w") as fh:
            fh.write("sub\n")
        git(s, sub, "add", ".")
        git(s, sub, "commit", "-q", "-m", "attributes")
        newsub = git(s, sub, "rev-parse", "HEAD")
        m, wt, _h = shop.at_merge("Submodule")
        git(s, os.path.join(shop.repo, "sub"), "fetch", "-q", sub)
        git(s, wt, "update-index", "--add", "--cacheinfo", "160000,%s,sub" % newsub)
        git(s, wt, "commit", "-q", "-m", "bump submodule")
        _write_review(shop, m, git(s, wt, "rev-parse", "HEAD"))
        real_run = s.srv.gates._run
        at_run = []
        s.srv.gates._run = lambda t: at_run.append(os.path.exists(marker)) or (1, "", "stub")
        st, out = shop.decide(m)
        s.srv.gates._run = real_run
        check("merge submodule: recurse=true and a filter in the submodule's own config: no marker file, merged or refused",
              not os.path.exists(marker) and at_run in ([], [False]), (st, out, at_run))
    finally:
        s.close()


def test_merge_abort_window():
    """FO-005: the git merge --abort after a conflict runs with the same protections as the merge: a filter added to the
    config after the check is not run by the abort's checkout."""
    from cc_dash import gates as gates_mod
    s = gate_srv()
    real_git = gates_mod.selfheal._git
    try:
        shop = Shop(s)
        m, wt, _h = shop.at_merge("Abort window")
        marker = os.path.join(BASE, "marker-abort")
        with open(os.path.join(wt, ".gitattributes"), "w") as fh:
            fh.write("*.txt filter=evil merge=evilm\n")
        with open(os.path.join(wt, "clash.txt"), "w") as fh:
            fh.write("lane side\n")
        git(s, wt, "add", ".")
        git(s, wt, "commit", "-q", "-m", "lane clash")
        head = git(s, wt, "rev-parse", "HEAD")
        _write_review(shop, m, head)
        st, c = shop.card(m)
        body = shop.body(c)
        with open(os.path.join(shop.repo, "clash.txt"), "w") as fh:
            fh.write("main side\n")
        git(s, shop.repo, "add", "clash.txt")
        git(s, shop.repo, "commit", "-q", "-m", "main clash")
        before = git(s, shop.repo, "rev-parse", "HEAD")
        armed, ran, argvs, at_run = [], [], [], []
        real_block = gates_mod.Gates._merge_block

        def block(main):
            res = real_block(main)
            if armed and res == ("", "") and not ran:
                def writer():
                    for k, v in (("filter.evil.smudge", "touch %s; cat" % marker), ("filter.evil.clean", "cat"),
                                 ("merge.evilm.driver", "touch %s; false" % marker)):
                        subprocess.run(["git", "config", k, v], cwd=shop.repo, check=True)
                th = threading.Thread(target=writer)
                th.start()
                th.join()
                ran.append(1)
            return res

        def spy(root, args, *a, **kw):
            argvs.append(list(args))
            return real_git(root, args, *a, **kw)
        s.srv.gates._merge_block = block
        gates_mod.selfheal._git = spy
        real_run = s.srv.gates._run
        s.srv.gates._run = lambda t: at_run.append(1) or (1, "", "stub")
        armed.append(1)
        st, out = s.post("/api/control/gate", body)
        s.srv.gates._run = real_run
        gates_mod.selfheal._git = real_git
        marker_now = os.path.exists(marker)
        for sec in ("filter.evil", "merge.evilm"):
            subprocess.run(["git", "config", "--remove-section", sec], cwd=shop.repo)
        err = (out or {}).get("error", "") if isinstance(out, dict) else str(out)
        check("merge abort: the driver was added after the check and the merge was refused", ran and st == 409 and "aborted" in err and "as it was" in err, (st, out, ran))
        check("merge abort: no marker file after the abort", not marker_now and not os.path.exists(marker), marker_now)
        check("merge abort: no ticket child ran", at_run == [], at_run)
        check("merge abort: the checkout is as it was",
              git(s, shop.repo, "rev-parse", "HEAD") == before and git(s, shop.repo, "status", "--porcelain", "--untracked-files=no") == ""
              and not os.path.exists(os.path.join(shop.repo, ".git", "MERGE_HEAD")), before)
        abort_argv = next((a for a in argvs if "merge" in a and "--abort" in a), [])
        check("merge abort: the abort ran with submodule.recurse=false and an empty --attr-source",
              "submodule.recurse=false" in abort_argv and any(a.startswith("--attr-source=") for a in abort_argv), abort_argv)
        first = next(i for i, v in enumerate(argvs) if "hash-object" in v)  # the merge path starts at its bootstrap
        rest = [v for v in argvs[first:] if "hash-object" not in v]
        check("merge abort: every git call besides the empty-tree bootstrap carries --attr-source",
              rest and all(any(a.startswith("--attr-source=") for a in v) for v in rest), [v for v in rest if not any(a.startswith("--attr-source=") for a in v)])
    finally:
        gates_mod.selfheal._git = real_git
        s.close()


def test_merge_no_head_box():
    """FO-005: an order at the merge gate whose review recorded no lane head never shows a git merge without a commit."""
    s = gate_srv()
    try:
        shop = Shop(s)
        m, _wt, _h = shop.at_merge("No head")
        _write_review(shop, m, "")
        st, c = shop.card(m)
        box = c.get("merge_outside") or {}
        cmds = [x["command"] for x in box.get("steps", [])]
        check("no head: the card's box tells you to send the order back", st == 200 and box.get("kind") == "send-back"
              and any(x.startswith("cc-factory reject %s --gate merge --rework" % m) for x in cmds), (st, box))
        st2, out = s.post("/api/control/gate", shop.body(c))
        cmds2 = cmds + [x["command"] for x in (out.get("outside") or {}).get("steps", [])]
        check("no head: the refusal is 409 with the same box, and no step is a git merge without a commit",
              st2 == 409 and (out.get("outside") or {}).get("kind") == "send-back"
              and not any(re.match(r"git merge --no-ff\s*$", x) for x in cmds2) and not c.get("merge_command"), (st2, out))
    finally:
        s.close()


def fake_rows(fp, rows):
    """A minimal fake /proc at fp from (pid, comm, ppid, pgrp, sid, tty, argv, exe) rows."""
    for pid, comm, ppid, pgrp, sid, tty, argv, exe in rows:
        d = os.path.join(fp, str(pid))
        os.makedirs(os.path.join(d, "fd"))
        with open(os.path.join(d, "stat"), "w") as fh:
            fh.write("%d (%s) S %d %d %d %d -1 4194560 0 0 0 0 0 0 0 0 20 0 1 0 0 0 0\n" % (pid, comm, ppid, pgrp, sid, tty))
        with open(os.path.join(d, "cmdline"), "wb") as fh:
            fh.write(b"\0".join(a.encode() for a in argv) + b"\0")
        with open(os.path.join(d, "status"), "w") as fh:
            fh.write("Name:\t%s\nUid:\t%d\t%d\t%d\t%d\n" % ((comm,) + (os.getuid(),) * 4))
        os.symlink(exe, os.path.join(d, "exe"))
    return fp


def test_restart_group_leader():
    """FO-005: Restart starts the new dashboard as its own process-group leader in the same session, so claude_lineage does not
    see a group whose leader is gone (a dashboard started directly from a shell leads its own group)."""
    procs = gate_fixture.load_procs()
    script = os.path.join(BASE, "restart-parent.py")
    with open(script, "w") as fh:
        fh.write("import contextlib, io, subprocess, sys\n"
                 "sys.path.insert(0, %r)\n"
                 "from cc_dash import server\n"
                 "server.RESTART_WAIT = 0\n"
                 "orig = subprocess.Popen\n"
                 "kids = []\n"
                 "def spy(argv, **kw):\n"
                 "    kw.update(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
                 "    p = orig([sys.executable, '-c', 'import time; time.sleep(30)'], **kw)\n"
                 "    kids.append(p.pid)\n"
                 "    return p\n"
                 "subprocess.Popen = spy\n"
                 "with contextlib.redirect_stdout(io.StringIO()):\n"
                 "    server.restart_dashboard([])\n"
                 "print(kids[0], flush=True)\n" % os.path.join(REPO, "lib"))
    parent = subprocess.run([sys.executable, "-I", script], capture_output=True, text=True, timeout=60, process_group=0)
    child = int(parent.stdout.strip().splitlines()[-1]) if parent.stdout.strip() else 0
    try:
        check("restart group: the parent started the child", child > 0, (parent.stdout, parent.stderr[-300:]))
        with open("/proc/%d/stat" % child) as fh:
            rest = fh.read().rsplit(")", 1)[1].split()
        pgrp, sid = int(rest[2]), int(rest[3])
        check("restart group: the child leads its own process group and shares the session (no new session)",
              pgrp == child and sid == os.getsid(0) and sid != child, (child, pgrp, sid, os.getsid(0)))
        fp = os.path.join(BASE, "restartproc")

        def tree(pg):
            shutil.rmtree(fp, ignore_errors=True)
            rows = [(50, "systemd", 1, 50, 50, 0, ["/usr/lib/systemd/systemd", "--user"], "/usr/lib/systemd/systemd"),
                    (110, "gnome-terminal-", 50, 110, 110, 0, ["/usr/libexec/gnome-terminal-server"], "/usr/libexec/gnome-terminal-server"),
                    (200, "zsh", 110, 200, 200, 34816, ["zsh"], "/usr/bin/zsh"),
                    (child, "python3", 50, pg, 200, 34816, ["python3", "-I", "dashboard"], PY)]
            return fake_rows(fp, rows)
        check("restart group: claude_lineage of a child that leads its own group is ''", procs.claude_lineage(child, proc=tree(child)) == "")
        why = procs.claude_lineage(child, proc=tree(4242))
        check("restart group: control: a group whose leader is gone is refused (the failure this avoids)", why != "", why)
        if not os.environ.get("CLAUDECODE"):
            check("restart group: the real claude_lineage of the child is ''", procs.claude_lineage(child) == "", procs.claude_lineage(child))
    finally:
        if child:
            try:
                os.kill(child, 9)
            except OSError:
                pass


def main():
    global GATE_CLI
    # The ticket child gets XDG_STATE_HOME (gates.CHILD_ENV_KEEP) and the dashboard's code check reads the user-wide
    # record from it: a fixture directory, never the real state.
    saved_state = os.environ.get("XDG_STATE_HOME")
    os.environ["XDG_STATE_HOME"] = os.path.join(BASE, "xdg-state")
    try:
        GATE_CLI = gate_fixture.make_tree(BASE)
        for fn in (test_pairing, test_write_checks, test_decide, test_messages_and_stop, test_socket_transport,
                   test_agents_release_and_annotate, test_round_trip_with_hook, test_refusal_on_terminal, test_no_proc_refused,
                   test_stop_cleans_lease, test_gates_end_to_end, test_gate_refusals, test_gate_new_states, test_gate_new_state_refusals, test_gate_stress,
                   test_approve_merge, test_outside_kinds, test_outside_refusals, test_gate_child, test_gate_ui, test_code_box, test_restart_endpoint, test_ide_terminal,
                   test_control_refusal_fix, test_merge_attr_window, test_merge_submodule, test_merge_abort_window, test_merge_no_head_box, test_restart_group_leader,
                   test_merge_clock_bound, test_merge_commit_time):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                check(fn.__name__ + " raised", False, f"{type(e).__name__}: {e}")
    finally:
        if saved_state is None:
            os.environ.pop("XDG_STATE_HOME", None)
        else:
            os.environ["XDG_STATE_HOME"] = saved_state
        shutil.rmtree(BASE, ignore_errors=True)
    fails = [r for r in RESULTS if not r[1]]
    print("total=%d  pass=%d  fail=%d" % (len(RESULTS), len(RESULTS) - len(fails), len(fails)))
    for name, _, detail in fails:
        print("  FAIL %-60s %s" % (name, detail[:600]))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
