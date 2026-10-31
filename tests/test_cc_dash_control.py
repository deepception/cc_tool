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
        factory(s, self.repo, "init")
        now = time.time()
        transcript(s.cfg, GATE_SID, self.repo, [{"type": "user", "timestamp": iso(now - 30), "sessionId": GATE_SID, "cwd": self.repo, "uuid": "g1",
                                                 "message": {"role": "user", "content": "hello"}}])
        s.store.refresh(force=True)
        self.label = next(p["project"] for p in s.srv.docs.projects() if os.path.realpath(p["root"]) == self.repo)
        cli = tree_cli or GATE_CLI
        gate_fixture.plant_record(self.repo, os.path.dirname(os.path.dirname(cli)))
        s.srv.gates.cli = cli

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
        check("gate card: the merge gate shows the stored briefing, the reviewed head and the exact merge command",
              st == 200 and c["gate"] == "merge" and "Review passed." in c["briefing"] and c["lane_head"] == head and c["merge_command"] == "git merge --no-ff %s" % head
              and c["moved"] is False, c)
        st, out = shop.decide(m)
        check("merge gate: approve before the merge is refused with the exact command, and the order stays in review",
              st == 409 and ("git merge --no-ff %s" % head) in out.get("error", "") and shop.status(m) == "review", (st, out))
        check("merge gate: nothing was merged by anyone", git(s, shop.repo, "rev-list", "--count", "HEAD") == "1")
        git(s, shop.repo, "merge", "-q", "--no-ff", "-m", "merge", head)
        st, out = shop.decide(m)
        e = gate_events(shop, m)[-1]
        check("merge gate: approve after the human's merge: done, with the merge sha and the reviewed head", st == 200 and shop.status(m) == "done" and e.get("merge_sha")
              and e.get("lane_head") == head and e.get("via") == "dashboard", (st, out, e))
        r, _wt, _h = shop.at_merge("Send me back")
        st, out = shop.decide(r, "reject", rework=True, note="ignores filters")
        check("merge gate: reject with rework goes back to approved", st == 200 and shop.status(r) == "approved" and gate_events(shop, r)[-1]["note"] == "ignores filters", (st, out))
        d, _wt, _h = shop.at_merge("Drop me")
        st, out = shop.decide(d, "reject")
        check("merge gate: reject drops the order", st == 200 and shop.status(d) == "rejected", (st, out))
        st, out = shop.card(d)
        check("gate card: an order at no gate has no card", st == 409 and "not waiting at a gate" in out.get("error", ""), (st, out))
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
        check("refused: an order that left its gate after the card was read, no ticket", st == 409 and "not waiting at the spec gate" in out.get("error", "")
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
        s.srv.gates.ticket_ttl = -1
        st, out = shop.decide(a)
        check("ticket: expired, the server hands nothing over and the CLI refuses", st == 409 and "expired" in out.get("error", "") and gate_events(shop, a) == [], (st, out))
        s.srv.gates.ticket_ttl = 30
        # The real CLI from a server that is no real dashboard: this very test process. This refusal comes after the
        # socket check, so reaching it also shows that the socket _run makes is held by the server alone.
        gate_fixture.plant_record(shop.repo, REPO)
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
  ok('card: and the exact merge command', (await page.textContent('dialog.ctl-review .ctl-more code')).startsWith('git merge --no-ff '));
  await page.click('text=Approve merge…');
  await page.fill('dialog.ctl-review .ctl-confirm input', 'approve');
  await go.click();
  await page.waitForSelector('dialog.ctl-review .k-gate > .ctl-err:not([hidden])');
  const refused = await page.textContent('dialog.ctl-review .k-gate > .ctl-err');
  ok('merge: approve before merging shows the refusal with the command', refused.includes('git merge --no-ff '), refused);
  await shot('gate-merge-refused');
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


def main():
    global GATE_CLI
    try:
        GATE_CLI = gate_fixture.make_tree(BASE)
        for fn in (test_pairing, test_write_checks, test_decide, test_messages_and_stop, test_socket_transport,
                   test_agents_release_and_annotate, test_round_trip_with_hook, test_refusal_on_terminal, test_no_proc_refused,
                   test_stop_cleans_lease, test_gates_end_to_end, test_gate_refusals, test_gate_child, test_gate_ui):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                check(fn.__name__ + " raised", False, f"{type(e).__name__}: {e}")
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
    fails = [r for r in RESULTS if not r[1]]
    print("total=%d  pass=%d  fail=%d" % (len(RESULTS), len(RESULTS) - len(fails), len(fails)))
    for name, _, detail in fails:
        print("  FAIL %-60s %s" % (name, detail[:600]))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
