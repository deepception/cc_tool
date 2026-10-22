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
--json` release, alert annotation, and a full round trip with the real hook.

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

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "lib"))
from cc_dash import auth as auth_mod  # noqa: E402
from cc_dash.control import Control  # noqa: E402
from cc_dash.server import make_server  # noqa: E402
from cc_dash.store import Store  # noqa: E402

HOOK = os.path.join(REPO, "templates", "hooks", "dash-control.py")
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

    def __init__(self, control=True, agents_cmd=None, sockets=True, port=0):
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
                               agents_cmd=agents_cmd, sockets=sockets)
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
        p = self.path("req", rid + ".json")
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(fd, json.dumps(req).encode())
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
            out = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r)\n"
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
              and all(stat.S_IMODE(os.stat(os.path.join(d, x)).st_mode) == 0o700 for x in ("req", "dec", "res", "msg", "dlv", "asked", "sock", "stop", "stopped")))
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
    cmd = [sys.executable, "-c", "import sys;print(open(sys.argv[1]).read())", script]

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
        p = subprocess.Popen([sys.executable, HOOK], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
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


def test_stop_cleans_lease():
    s = Srv()
    s.close()
    check("stop(): lease and pending removed on shutdown", not os.path.exists(os.path.join(s.state, "cc_tool", "control", "lease")))


def main():
    try:
        for fn in (test_pairing, test_write_checks, test_decide, test_messages_and_stop, test_socket_transport,
                   test_agents_release_and_annotate, test_round_trip_with_hook, test_stop_cleans_lease):
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
