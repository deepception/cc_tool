#!/usr/bin/env python3
"""cc-dash's two token scopes, the control code, and the hook's verify channel
(v0.2.1: lib/cc_dash/auth.py, server.py, control.py; templates/hooks/dash-control.py).

Pairing (the one-time link, or same-user pairing on Linux) buys a READ-ONLY
token: every GET, nothing that changes state. Your agents run as you, so "same
uid" is not "the human". Control mode's writes need a control token, bought
with the per-launch control code that cc-dash writes to its terminal only.

Checks: a pair-local or link token cannot decide, message, stop or cancel; the
unlock endpoint (needs a read token, the dashboard's Origin, JSON); wrong codes
lock after 5 tries, one try a second, constant-time compare; the right code
gives control scope; GET /api/control/verify (no token, same user only)
confirms only what this launch issued, binds a decision to what the card
showed, confirms a message or stop once, and logs a forged file; planted msg/
and stop/ files are never shown; a request file rewritten after the dashboard
read it cannot borrow the approval (real hook); and a real `cc-dash --control`
process in a pty: the code is on the terminal, and nowhere in the log, the
state dir, HOME, its argv or its environment; it refuses to start without a
terminal; --ready-fd closes without carrying the code.

Run:  python3 tests/test_cc_dash_scopes.py
"""
import http.client
import json
import os
import pty  # noqa: F401  (documents the dependency: a pty is how a terminal is simulated)
import re
import select
import shutil
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
from cc_dash import auth as auth_mod  # noqa: E402
from cc_dash.control import Control, shown_digest  # noqa: E402
from cc_dash.server import make_server  # noqa: E402
from cc_dash.store import Store  # noqa: E402

HOOK = os.path.join(REPO, "templates", "hooks", "dash-control.py")
CC_DASH = os.path.join(REPO, "bin", "cc-dash")
RESULTS = []
BASE = tempfile.mkdtemp(prefix="cc-dash-scopes-")
SID = "bbbbbbbb-1111-2222-3333-444444444444"
CODE_RE = re.compile(r"\b([2-9A-HJKMNP-Z]{4}-[2-9A-HJKMNP-Z]{4}-[2-9A-HJKMNP-Z]{4})\b")


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), str(detail)))


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def make_cfg(root, cwd="/work/demo"):
    cfg = os.path.join(root, "claude")
    pdir = os.path.join(cfg, "projects", cwd.replace("/", "-"))
    os.makedirs(pdir, exist_ok=True)
    now = time.time()
    with open(os.path.join(pdir, SID + ".jsonl"), "w") as fh:
        fh.write(json.dumps({"type": "user", "timestamp": iso(now - 60), "sessionId": SID, "cwd": cwd, "uuid": "p1",
                             "message": {"role": "user", "content": "hello"}}) + "\n")
        fh.write(json.dumps({"type": "assistant", "timestamp": iso(now - 59), "sessionId": SID, "cwd": cwd, "uuid": "a1",
                             "message": {"id": "m1", "model": "claude-haiku-5-5", "role": "assistant",
                                         "content": [{"type": "text", "text": "hi"}], "stop_reason": "end_turn",
                                         "usage": {"input_tokens": 1, "output_tokens": 1}}}) + "\n")
    return cfg


class Srv:
    n = 0

    def __init__(self):
        Srv.n += 1
        self.root = os.path.join(BASE, "s%d" % Srv.n)
        self.cfg = make_cfg(self.root)
        self.state = os.path.join(self.root, "state")
        self.cwd = "/work/demo"
        self.store = Store(root=self.cfg, days=7)
        self.store.refresh(force=True)
        self.srv = make_server(0, self.store)
        self.port = self.srv.port
        self.ctl = Control(self.store, self.port, ctl_dir=os.path.join(self.state, "cc_tool", "control"), agents_cmd=None,
                           hook_check=False)   # requests written by hand; test_planted_request checks the real rule
        self.ctl.start(thread=False)
        self.srv.control = self.ctl
        self.code = self.srv.auth.enable_control()
        self.t = [time.time()]
        self.srv.auth.clock = lambda: self.t[0]
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def path(self, *p):
        return os.path.join(self.ctl.dir, *p)

    def req(self, method, path, body=None, token=None, headers=None, lease=True):
        """lease: send the lease key with /api/control/verify, as the hook does."""
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": "127.0.0.1:%d" % self.port}
        if token:
            h["X-CC-Dash-Token"] = token
        if lease and path.startswith("/api/control/verify"):
            h["X-CC-Dash-Lease"] = self.ctl.key
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h.update({"Content-Type": "application/json", "Origin": "http://127.0.0.1:%d" % self.port,
                      "Sec-Fetch-Site": "same-origin"})
        h.update(headers or {})
        c.request(method, path, body=data, headers={k: v for k, v in h.items() if v is not None})
        r = c.getresponse()
        out = r.read()
        c.close()
        try:
            return r.status, json.loads(out)
        except ValueError:
            return r.status, out

    def pair_link(self):
        return self.req("POST", "/api/pair", {"code": self.srv.auth.new_code()})[1].get("token")

    def pair_local(self):
        return self.req("POST", "/api/pair-local", {})[1].get("token")

    def unlock(self, token, code, advance=2.0):
        self.t[0] += advance                      # a fake clock: the one-try-a-second gap passes between test tries
        return self.req("POST", "/api/control/unlock", {"code": code}, token=token)

    def fake_request(self, ti=None):
        ti = ti or {"command": "rm -rf build"}
        rid = "%x-%s" % (int(time.time() * 1000), os.urandom(6).hex())
        req = {"v": 1, "id": rid, "ts": time.time(), "session": SID, "agent_id": None, "cwd": self.cwd, "tool": "Bash",
               "kind": "permission", "sig": ti.get("command", ""), "hash": os.urandom(32).hex(), "input": ti, "guard": "",
               "ask_rule": "", "session_rules": [], "deadline": time.time() + 300}
        fd = os.open(self.path("req", rid + ".json"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(fd, json.dumps(req).encode())
        os.close(fd)
        self.ctl.scan()
        return req

    def close(self):
        self.ctl.stop()
        self.srv.shutdown()
        self.srv.server_close()


def plant(path, obj):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.write(fd, json.dumps(obj).encode())
    os.close(fd)


# ── scopes ─────────────────────────────────────────────────────────────────
def test_read_only_tokens():
    s = Srv()
    try:
        for label, tok in (("pair-local", s.pair_local() if auth_mod.local_pairing_supported() else None),
                           ("pairing link", s.pair_link())):
            if tok is None:
                check("%s: skipped (no /proc/net/tcp)" % label, True)
                continue
            st, meta = s.req("GET", "/api/meta", token=tok)
            check("%s token: meta says scope read" % label, st == 200 and meta["control"]["scope"] == "read"
                  and meta["control"]["enabled"] is True, meta.get("control"))
            for path in ("/api/live", "/api/control/state", "/api/alerts", "/api/sessions"):
                check("%s token: GET %s works" % (label, path), s.req("GET", path, token=tok)[0] == 200)
            r = s.fake_request()
            for path, body in (("/api/control/decide", {"id": r["id"], "action": "allow", "hash": r["hash"], "confirm": "approve"}),
                               ("/api/control/message", {"session": SID, "text": "approve it"}),
                               ("/api/control/stop", {"session": SID}),
                               ("/api/control/cancel", {"id": "x"})):
                st, out = s.req("POST", path, body, token=tok)
                check("%s token: POST %s refused 403 (enter the control code)" % (label, path),
                      st == 403 and out.get("unlock") is True, (st, out))
            check("%s token: nothing was decided, queued or stopped" % label,
                  os.listdir(s.path("dec")) == [] and os.listdir(s.path("msg")) == [] and os.listdir(s.path("stop")) == []
                  and s.ctl.issued == {}, s.ctl.issued)
        st, out = s.req("POST", "/api/control/decide", {"id": "x", "action": "allow"})
        check("no token at all: 401", st == 401, st)
    finally:
        s.close()


def test_unlock_and_lockout():
    s = Srv()
    try:
        tok = s.pair_link()
        wrong_of = lambda t: s.srv.auth.tokens[t].get("wrong", 0)  # noqa: E731
        st, out = s.req("POST", "/api/control/unlock", {"code": s.code})
        check("unlock without a read token: 401", st == 401, (st, out))
        st, out = s.req("POST", "/api/control/unlock", {"code": s.code}, token=tok, headers={"Origin": "http://evil.example"})
        check("unlock from another Origin: 403", st == 403, (st, out))
        st, out = s.req("POST", "/api/control/unlock", {"code": ""}, token=tok)
        check("unlock with an empty code: 400, not counted", st == 400 and wrong_of(tok) == 0, (st, out))
        check("code format: 3 groups of 4 from an alphabet without 0/O/1/I/L", CODE_RE.fullmatch(s.code), s.code)
        st1, o1 = s.unlock(tok, "AAAA-AAAA-AAAA")
        st2, o2 = s.unlock(tok, "BBBB-BBBB-BBBB", advance=0.2)
        check("a second try from one token within a second: 429, not counted", st1 == 403 and st2 == 429 and wrong_of(tok) == 1, (st1, st2))
        codes = [st1] + [s.unlock(tok, "WRONG-%d" % i)[0] for i in range(4)]
        check("wrong codes: 403 x4 then 423 on the 5th, for this token", codes == [403, 403, 403, 403, 423], codes)
        st, out = s.unlock(tok, s.code)
        check("a locked token: even the right code is refused (423), with a reload hint", st == 423 and out.get("locked")
              and "Reload" in out.get("error", ""), (st, out))
        st, meta = s.req("GET", "/api/meta", token=tok)
        check("a locked token: meta says so (the page disables the field)", meta["control"]["locked"] is True, meta["control"])
        a = [x for x in s.ctl.audit if x.get("action") == "unlock"]
        check("audit: every unlock try is logged", len(a) >= 5 and any("locked" in x.get("result", "") for x in a), a[-1:])

        # Per token, not global: an agent's pair-local token locking itself does not lock the human.
        agent = s.pair_local() if auth_mod.local_pairing_supported() else s.pair_link()
        agent_codes = [s.unlock(agent, "ZZZZ-ZZZZ-ZZZ%d" % (i + 2))[0] for i in range(6)]
        check("an agent's token: locked after its own 5 wrong codes", agent_codes == [403, 403, 403, 403, 423, 423], agent_codes)
        human = s.pair_local() if auth_mod.local_pairing_supported() else s.pair_link()
        st, meta = s.req("GET", "/api/meta", token=human)
        check("another (fresh) token is not locked by those tries", meta["control"]["locked"] is False and meta["control"]["tries_left"] == 5,
              meta["control"])
        st, out = s.unlock(human, s.code)
        check("the human's fresh token unlocks with the right code", st == 200 and out.get("scope") == "control", (st, out))
        # Nothing shared stands before the comparison: an agent hammering wrong codes
        # from many tokens in the same instant does not delay the human's right code.
        toks = [s.pair_link() for _ in range(12)]
        s.t[0] += 5
        burst = [s.req("POST", "/api/control/unlock", {"code": "QQQQ-QQQQ-QQQQ"}, token=t)[0] for t in toks]
        human2 = s.pair_link()
        st, out = s.req("POST", "/api/control/unlock", {"code": s.code}, token=human2)        # same instant, no clock advance
        check("an agent hammering wrong codes, then the human's token with the right code: 200 at once",
              burst == [403] * 12 and st == 200 and out.get("scope") == "control", (burst, st, out))
    finally:
        s.close()

    s = Srv()
    try:
        tok = s.pair_link()
        st, out = s.unlock(tok, s.code.lower().replace("-", " "))
        ctok = out.get("token") if isinstance(out, dict) else None
        check("the right code (any case, spaces) gives a control token", st == 200 and ctok and out.get("scope") == "control", (st, out))
        check("the control token differs from the read token", ctok != tok)
        st, meta = s.req("GET", "/api/meta", token=ctok)
        check("control token: meta says scope control", meta["control"]["scope"] == "control", meta["control"])
        r = s.fake_request({"command": "ls"})
        st, out = s.req("POST", "/api/control/decide", {"id": r["id"], "action": "allow", "hash": r["hash"]}, token=ctok)
        check("control token: decide works", st == 200, (st, out))
        st, out = s.req("POST", "/api/control/message", {"session": SID, "text": "hi"}, token=ctok)
        check("control token: message works", st == 200, (st, out))
        st, out = s.req("POST", "/api/control/decide", {"id": r["id"], "action": "deny"}, token=tok)
        check("the read token stays read-only after an unlock", st == 403, st)
        st, out = s.unlock(tok, s.code)
        check("the code stays valid for this launch (a reload asks for it again)", st == 200, (st, out))
        other = Srv()
        try:
            st, _ = other.req("POST", "/api/control/decide", {"id": r["id"], "action": "allow"}, token=ctok)
            check("a control token is worthless on another launch (401)", st == 401, st)
            check("each launch makes its own code", other.code != s.code)
        finally:
            other.close()
    finally:
        s.close()


def test_pair_local_cap():
    if not auth_mod.local_pairing_supported():
        check("pair-local cap: skipped (no /proc/net/tcp)", True)
        return
    s = Srv()
    try:
        got = [s.req("POST", "/api/pair-local", {})[0] for _ in range(40)]
        check("same-user pairing is never rate-limited (an agent minting tokens cannot lock the human out)",
              got == [200] * 40, got)
        s.t[0] += 61
        check("same-user pairing: available again a minute later", s.req("POST", "/api/pair-local", {})[0] == 200)
    finally:
        s.close()


def test_verify_channel():
    s = Srv()
    try:
        tok = s.pair_link()
        ctok = s.unlock(tok, s.code)[1]["token"]
        r = s.fake_request({"command": "make deploy"})
        q = "/api/control/verify?kind=dec&id=%s&hash=%s&shown=%s" % (r["id"], r["hash"], shown_digest(r))
        st, out = s.req("GET", q)
        check("verify: a decision nobody sent is 404", st == 404 and out.get("ok") is False, (st, out))
        check("verify: the forged file is logged in the audit", any(a.get("action") == "refused" and a.get("item") == r["id"] for a in s.ctl.audit))
        st, out = s.req("POST", "/api/control/decide", {"id": r["id"], "action": "deny", "message": "use make stage", "hash": r["hash"]}, token=ctok)
        st, out = s.req("GET", q, lease=False)
        check("verify without the lease key: 403 (only who can read the 0600 lease may ask)", st == 403, (st, out))
        st, out = s.req("GET", q, headers={"X-CC-Dash-Lease": "0" * 32})
        check("verify with a wrong lease key: 403", st == 403, (st, out))
        st, out = s.req("GET", q)
        check("verify: a decision sent from a control-unlocked page is confirmed with its content",
              st == 200 and out.get("ok") and out.get("action") == "deny" and out.get("message") == "use make stage", (st, out))
        check("verify: idempotent for a decision", s.req("GET", q)[0] == 200)
        st, _ = s.req("GET", "/api/control/verify?kind=dec&id=%s&hash=%s&shown=%s" % (r["id"], "0" * 64, shown_digest(r)))
        check("verify: another call's hash is 404", st == 404)
        changed = dict(r, input={"command": "ls"})
        st, _ = s.req("GET", "/api/control/verify?kind=dec&id=%s&hash=%s&shown=%s" % (r["id"], r["hash"], shown_digest(changed)))
        check("verify: a different card (request rewritten after it was shown) is 404", st == 404)
        if auth_mod.local_pairing_supported():
            real = auth_mod.peer_uid
            auth_mod.peer_uid = lambda *a, **k: os.getuid() + 1
            try:
                check("verify: another user's socket is refused (403), even with the key", s.req("GET", q)[0] == 403)
            finally:
                auth_mod.peer_uid = real
            auth_mod.peer_uid = lambda *a, **k: None
            try:
                check("verify: a socket whose owner cannot be found is refused (fails closed)", s.req("GET", q)[0] == 403)
            finally:
                auth_mod.peer_uid = real
        st, out = s.req("POST", "/api/control/message", {"session": SID, "text": "run the linter"}, token=ctok)
        mid = out["message"]["id"]
        mq = "/api/control/verify?kind=msg&id=%s&session=%s&agent=" % (mid, SID)
        st1, o1 = s.req("GET", "/api/control/verify?kind=msg&id=%s&session=%s&agent=a0123" % (mid, SID))
        st0, _ = s.req("GET", mq, lease=False)                     # a third party, without the key, goes first
        st2, o2 = s.req("GET", mq)
        st3, _ = s.req("GET", mq)
        check("verify: a message for another agent is 404", st1 == 404, (st1, o1))
        check("verify: a third party's query does not burn the message; the hook still gets cc-dash's text",
              st0 == 403 and st2 == 200 and o2.get("text") == "run the linter" and st3 == 200, (st0, st2, o2, st3))
        # Once the hook has delivered it (dlv/ read back), a copy planted afterwards is no longer confirmed.
        name = "%s.main.%s.json" % (SID, mid)
        os.rename(s.path("msg", name), s.path("dlv", name))
        d = json.load(open(s.path("dlv", name)))
        d.update(delivered_ts=time.time(), via="PostToolUse")
        os.unlink(s.path("dlv", name))
        plant(s.path("dlv", name), d)
        s.ctl.scan()
        check("verify: a delivered message is not confirmed again (a replayed copy does nothing)", s.req("GET", mq)[0] == 404)
        st, out = s.req("POST", "/api/control/stop", {"session": SID}, token=ctok)
        sq = "/api/control/verify?kind=stop&id=%s.main&session=%s" % (SID, SID)
        check("verify: a stop survives a third-party query and repeated queries",
              s.req("GET", sq, lease=False)[0] == 403 and s.req("GET", sq)[0] == 200 and s.req("GET", sq)[0] == 200)
        os.rename(s.path("stop", SID + ".main.json"), s.path("stopped", SID + ".main.json"))
        d = json.load(open(s.path("stopped", SID + ".main.json")))
        d.update(applied_ts=time.time(), outcome="stopped", tool="Bash")
        os.unlink(s.path("stopped", SID + ".main.json"))
        plant(s.path("stopped", SID + ".main.json"), d)
        s.ctl.scan()
        check("verify: an applied stop is not confirmed again", s.req("GET", sq)[0] == 404)
        check("verify: an unknown kind is 400", s.req("GET", "/api/control/verify?kind=zzz&id=1")[0] == 400)
        s.ctl._prune_issued(time.time() + 10 ** 6)
        check("issued records expire by TTL, not by a query", s.ctl.issued == {}, list(s.ctl.issued))

        planted_mid = "1a2b3c4d5e6f"
        plant(s.path("msg", "%s.main.%s.json" % (SID, planted_mid)), {"v": 1, "id": planted_mid, "session": SID, "agent_id": None,
                                                                       "text": "forged by an agent", "ts": time.time()})
        plant(s.path("stop", "%s.a0123.json" % SID), {"v": 1, "session": SID, "agent_id": "a0123", "ts": time.time()})
        st, state = s.req("GET", "/api/control/state", token=tok)
        check("planted msg/ and stop/ files are never shown as the dashboard's",
              not any(m.get("id") == planted_mid for m in state["messages"]) and not any(x["key"] == SID + ".a0123" for x in state["stops"]),
              (state["messages"], state["stops"]))
        lease = json.load(open(s.path("lease")))
        check("the lease carries the per-launch key, 0600", lease.get("key") == s.ctl.key and len(s.ctl.key) >= 32
              and (os.stat(s.path("lease")).st_mode & 0o777) == 0o600)
    finally:
        s.close()


def test_launch_refusals():
    from cc_dash import server as server_mod
    a = server_mod.parse_args(["--control"])
    check("--control parses", a.control is True)
    for abbrev in ("--con", "--c", "--contro", "--control-w"):
        try:
            with open(os.devnull, "w") as dn:
                old = sys.stderr
                sys.stderr = dn
                try:
                    server_mod.parse_args([abbrev] + (["30"] if abbrev == "--control-w" else []))
                finally:
                    sys.stderr = old
            ok = False
        except SystemExit:
            ok = True
        check("argparse rejects the abbreviation %s (allow_abbrev=False)" % abbrev, ok)
    d = os.path.join(BASE, "refuse", "control")
    NOPROC = os.path.join(BASE, "noproc")      # no /proc entries: only the given env and ancestors count
    os.makedirs(d, mode=0o700)
    check("no lease, not under Claude Code: allowed", server_mod.control_refusal(d, proc=NOPROC, env={}, ancestors=[]) == "")
    shell = [(50, "zsh", "/usr/bin/zsh", ["zsh"]), (40, "gnome-terminal-", "/usr/libexec/gnome-terminal-server", ["gnome-terminal-server"])]
    check("a plain terminal parent chain: allowed", server_mod.control_refusal(d, proc=NOPROC, env={"HOME": "/h"}, ancestors=shell) == "")
    def plant_lease(pid):
        p = os.path.join(d, "lease")
        if os.path.lexists(p):
            os.unlink(p)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(fd, json.dumps({"pid": pid, "expires": time.time() + 10 ** 9, "port": 7878}).encode())
        os.close(fd)

    # F5 (final review): a planted lease naming any live pid (pid 1 is always alive)
    # used to block the human's `cc-dash --control` for good.
    notes = []
    plant_lease(1)
    why = server_mod.control_refusal(d, env={}, ancestors=[], note=notes.append)
    check("a planted lease naming a live process that is not cc-dash (pid 1): not counted", "holds the control lease" not in why, why)
    check("a planted lease: removed, with a note for the terminal",
          not os.path.exists(os.path.join(d, "lease")) and notes and "not a cc-dash dashboard" in notes[0], notes)
    sleeper = subprocess.Popen([PY, "-c", "import time; time.sleep(30)"])
    try:
        plant_lease(sleeper.pid)
        why = server_mod.control_refusal(d, env={}, ancestors=[], note=notes.append)
        check("a lease naming a live Python that is not cc-dash: removed, not counted",
              "holds the control lease" not in why and not os.path.exists(os.path.join(d, "lease")), why)
        # The same program check as the hook: a pid that IS cc-dash from this checkout
        # (fake /proc: python -I <repo>/bin/cc-dash, clean environment, a terminal shell parent).
        lp = os.path.join(BASE, "leaseproc")
        for pid, comm, ppid, argv, exe in ((sleeper.pid, "python3", 70, ["python3", "-I", os.path.join(REPO, "bin", "cc-dash"), "--control"], PY),
                                           (70, "zsh", 60, ["zsh"], "/usr/bin/zsh"),
                                           (60, "gnome-terminal-", 1, ["gnome-terminal-server"], "/usr/libexec/gnome-terminal-server")):
            os.makedirs(os.path.join(lp, str(pid)), exist_ok=True)
            with open(os.path.join(lp, str(pid), "stat"), "w") as fh:
                fh.write("%d (%s) S %d %d %d 34816 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 0 0 0\n" % (pid, comm, ppid, 70 if pid != 60 else 60, 70 if pid != 60 else 60))
            with open(os.path.join(lp, str(pid), "cmdline"), "wb") as fh:
                fh.write(b"\0".join(a.encode() for a in argv) + b"\0")
            with open(os.path.join(lp, str(pid), "environ"), "wb") as fh:
                fh.write(b"HOME=/h\0PATH=/usr/bin\0")
            os.symlink(exe, os.path.join(lp, str(pid), "exe"))
            os.symlink(REPO, os.path.join(lp, str(pid), "cwd"))
        plant_lease(sleeper.pid)
        why = server_mod.control_refusal(d, proc=lp, env={}, ancestors=[])
        check("a live lease held by a real cc-dash of this checkout: takeover refused", "holds the control lease" in why, why)
    finally:
        sleeper.kill()
        sleeper.wait()
    check("the holder gone: allowed again", server_mod.control_refusal(d, proc=NOPROC, env={}, ancestors=[]) == "")
    if os.path.exists(os.path.join(d, "lease")):
        os.unlink(os.path.join(d, "lease"))
    for var in server_mod.CLAUDE_ENV_SIGNALS:
        why = server_mod.control_refusal(d, proc=NOPROC, env={var: "x"}, ancestors=[])
        check("Claude Code's Bash environment (%s set): refused with the plain message" % var,
              why.startswith("cc-dash --control must be started from your own terminal, not from inside a Claude Code session") and var in why, why)
    for label, chain in (("the native claude binary", [(9, "claude", "/opt/x/native-binary/claude", ["/opt/x/native-binary/claude"])]),
                         ("node running the claude CLI", [(9, "node", "/usr/bin/node", ["node", "/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js"])]),
                         ("node running a `claude` shim", [(9, "node", "/usr/bin/node", ["node", "/home/u/.npm/bin/claude", "--model", "x"])])):
        why = server_mod.control_refusal(d, proc=NOPROC, env={}, ancestors=shell[:1] + chain + shell[1:])
        check("an ancestor that is %s, several levels up: refused" % label, "not from inside a Claude Code session" in why, why)
    check("node running something else is not mistaken for Claude Code", server_mod.control_refusal(
        d, proc=NOPROC, env={}, ancestors=[(9, "node", "/usr/bin/node", ["node", "/srv/app/server.js"])]) == "")

    # A fake /proc: the parent chain is clean (double-forked, re-parented to the user
    # manager), but the session or process-group leader still descends from claude.
    fp = os.path.join(BASE, "fakeproc")

    def proc(pid, comm, ppid, pgrp, sid, tty, argv, exe):
        os.makedirs(os.path.join(fp, str(pid)), exist_ok=True)
        with open(os.path.join(fp, str(pid), "stat"), "w") as fh:
            fh.write("%d (%s) S %d %d %d %d -1 4194560 0 0 0 0 0 0 0 0 20 0 1 0 0 0 0\n" % (pid, comm, ppid, pgrp, sid, tty))
        with open(os.path.join(fp, str(pid), "cmdline"), "wb") as fh:
            fh.write(b"\0".join(a.encode() for a in argv) + b"\0")
        link = os.path.join(fp, str(pid), "exe")
        if os.path.lexists(link):
            os.unlink(link)
        os.symlink(exe, link)

    def build(sid_leader_parent, me_tty=34816, me_pgrp=300, me_sid=200, me_ppid=50):
        shutil.rmtree(fp, ignore_errors=True)
        proc(50, "systemd", 1, 50, 50, 0, ["/usr/lib/systemd/systemd", "--user"], "/usr/lib/systemd/systemd")
        proc(100, "claude", 90, 100, 90, 34816, ["/opt/claude/claude"], "/opt/claude/claude")
        proc(110, "gnome-terminal-", 50, 110, 110, 0, ["/usr/libexec/gnome-terminal-server"], "/usr/libexec/gnome-terminal-server")
        proc(200, "zsh", sid_leader_parent, 200, 200, 34816, ["zsh"], "/usr/bin/zsh")
        proc(300, "bash", 200, 300, 200, 34816, ["bash"], "/usr/bin/bash")
        proc(400, "python3", me_ppid, me_pgrp, me_sid, me_tty, ["python3", "cc-dash", "--control"], "/usr/bin/python3")
    build(sid_leader_parent=100)
    why = server_mod.control_refusal(d, env={}, proc=fp, me=400)
    check("fake /proc: a clean parent chain but a session leader that descends from claude: refused",
          "session leader descends from process 100" in why, why)
    build(sid_leader_parent=110, me_sid=400, me_pgrp=300)
    proc(300, "bash", 100, 300, 300, 34816, ["bash"], "/usr/bin/bash")
    why = server_mod.control_refusal(d, env={}, proc=fp, me=400)
    check("fake /proc: a process-group leader that descends from claude: refused", "process-group leader descends" in why, why)
    build(sid_leader_parent=110, me_tty=0, me_sid=400, me_pgrp=400)
    why = server_mod.control_refusal(d, env={}, proc=fp, me=400)
    check("fake /proc: re-parented to the user manager with no controlling terminal: refused",
          "must be started from your own terminal" in why, why)
    # What `ccdash` really does (measured): own process group, the shell's session,
    # re-parented to the user manager, the terminal still open.
    build(sid_leader_parent=110, me_sid=200, me_pgrp=400)
    why = server_mod.control_refusal(d, env={}, proc=fp, me=400)
    check("fake /proc: detached into its own process group, shell's session, terminal open (what `ccdash` does): allowed", why == "", why)
    build(sid_leader_parent=110, me_sid=400, me_pgrp=400)
    why = server_mod.control_refusal(d, env={}, proc=fp, me=400)
    check("fake /proc: leads a fresh session (setsid) and was re-parented: refused", "leads a fresh session" in why, why)
    build(sid_leader_parent=110, me_ppid=300)
    why = server_mod.control_refusal(d, env={}, proc=fp, me=400)
    check("fake /proc: started from a terminal shell, leaders clean: allowed", why == "", why)


def test_card_binding_with_real_hook():
    """The hook binds a decision to what the card showed: a request file rewritten
    (benign text, same hash) cannot borrow an approval for the real call."""
    s = Srv()
    try:
        tok = s.pair_link()
        ctok = s.unlock(tok, s.code)[1]["token"]
        env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
        env.update(XDG_STATE_HOME=s.state, CLAUDE_CONFIG_DIR=s.cfg, HOME=s.root,
                   CC_DASH_LEASE_ANY_PROGRAM="1")   # the dashboard here runs inside this test process
        payload = {"session_id": SID, "transcript_path": "/x", "cwd": s.cwd, "hook_event_name": "PermissionRequest",
                   "permission_mode": "default", "tool_name": "Bash", "tool_input": {"command": "git push --force origin main"}}
        p = subprocess.Popen([PY, HOOK], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
        p.stdin.write(json.dumps(payload).encode())
        p.stdin.close()
        p.stdin = None
        path, req = None, None
        end = time.time() + 8
        while time.time() < end and req is None:
            for n in os.listdir(s.path("req")):
                if n.endswith(".json"):
                    path = s.path("req", n)
                    req = json.load(open(path))
            time.sleep(0.03)
        check("binding: the hook wrote its request", req is not None)
        # An agent rewrites the request before cc-dash reads it: benign text, same id and hash.
        forged = dict(req, input={"command": "ls"}, sig="ls")
        os.unlink(path)
        plant(path, forged)
        s.ctl.scan()
        shown = next(x for x in s.req("GET", "/api/control/state", token=tok)[1]["requests"] if x["id"] == req["id"])
        check("binding: the dashboard shows the forged text", shown["input"] == {"command": "ls"}, shown["input"])
        st, out = s.req("POST", "/api/control/decide", {"id": req["id"], "action": "allow", "hash": req["hash"], "confirm": "approve"},
                        token=ctok)   # bash-guard refuses this command, so the card asks for the typed approve
        time.sleep(1.0)
        check("binding: the approval of the forged card is not applied to the real call", st == 200 and p.poll() is None, (st, p.poll()))
        # Only `sig`, the one-line summary, rewritten: the card does not show it (it shows the input,
        # and an alert row's text comes from the input too), so it is not in the digest, and the
        # card a human approves still shows the real call.
        forged_sig = dict(req, sig="npm test", id="0" * 12 + "-" + "a" * 12, status="open")
        check("binding: a forged summary leaves every field the card shows unchanged", shown_digest(forged_sig) == shown_digest(req))
        with s.ctl.lock:
            s.ctl.requests[forged_sig["id"]] = forged_sig
        rows = [{"_match": (SID, None, "Bash", "npm test"), "message": "npm test"}]
        s.ctl.annotate(rows)
        with s.ctl.lock:
            del s.ctl.requests[forged_sig["id"]]
        check("binding: an alert row linked through the forged summary shows the real command, not the summary",
              rows[0].get("message") == req["input"]["command"], rows[0])
        p.terminate()
        p.communicate(timeout=5)
    finally:
        s.close()


# ── a real cc-dash --control process ───────────────────────────────────────
def _env(root):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "XDG_STATE"))}
    env.update(HOME=os.path.join(root, "home"), XDG_STATE_HOME=os.path.join(root, "state"), CLAUDE_CONFIG_DIR=make_cfg(root),
               PYTHONDONTWRITEBYTECODE="1")
    os.makedirs(env["HOME"], exist_ok=True)
    return env


# Starts cc-dash detached from this process tree, as a terminal would: the
# launcher forks, exits, and the child (reparented away from us) becomes a
# session leader whose controlling terminal is the given pty ("-": none).
# cc-dash --control refuses to start under a Claude Code process, and these
# tests may themselves run under one.
LAUNCHER = r"""
import os, sys
tty, log, pidw = sys.argv[1], sys.argv[2], int(sys.argv[3]); cmd = sys.argv[4:]
if os.fork():
    os._exit(0)
os.setsid()
fd = os.open(tty if tty != "-" else os.devnull, os.O_RDWR)
os.dup2(fd, 0)
lf = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
os.dup2(lf, 1); os.dup2(lf, 2)
# Like a terminal: a "shell" leads the session and owns the pty, cc-dash runs as its child.
child = os.fork()
if child:
    os.write(pidw, str(child).encode()); os.close(pidw)
    os.closerange(3, 1024)             # the "shell" holds no extra fds (e.g. the --ready-fd pipe)
    try:
        os.waitpid(child, 0)
    finally:
        os._exit(0)
os.close(pidw)
os.execvpe(cmd[0], cmd, os.environ)
"""


def _launch_detached(cmd, env, log, tty="-", keep=()):
    pr, pw = os.pipe()
    subprocess.run([PY, "-c", LAUNCHER, tty, log, str(pw)] + cmd, env=env, pass_fds=(pw,) + tuple(keep),
                   timeout=30, check=True)
    os.close(pw)
    pid = int(os.read(pr, 32) or b"0")
    os.close(pr)
    return pid


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _stop(pid):
    try:
        os.kill(pid, 15)
    except OSError:
        return
    end = time.time() + 10
    while time.time() < end and _alive(pid):
        try:
            if os.waitpid(pid, os.WNOHANG)[0]:
                break
        except ChildProcessError:
            pass
        time.sleep(0.1)


def _read_pty(fd, secs, until=None):
    out, end = b"", time.time() + secs
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.1)
        if r:
            try:
                b = os.read(fd, 4096)
            except OSError:
                break
            if not b:
                break
            out += b
            if until and until(out):
                break
    return out


def _scan_tree(root, needles):
    hits = []
    for dp, _dn, fn in os.walk(root):
        for f in fn:
            p = os.path.join(dp, f)
            try:
                data = open(p, "rb").read()
            except OSError:
                continue
            if any(n in data for n in needles):
                hits.append(p)
    return hits


def test_real_process_code_never_on_disk():
    root = os.path.join(BASE, "proc")
    env = _env(root)
    log = os.path.join(root, "cc-dash.log")
    master, slave = os.openpty()
    rfd, wfd = os.pipe()
    pid = _launch_detached([PY, CC_DASH, "--control", "--no-open", "--port", "0", "--ready-fd", str(wfd)],
                           env, log, tty=os.ttyname(slave), keep=(wfd,))
    os.close(slave)
    os.close(wfd)
    try:
        term = _read_pty(master, 20, until=lambda b: CODE_RE.search(b.decode("utf-8", "replace")) is not None)
        m = CODE_RE.search(term.decode("utf-8", "replace"))
        code = m.group(1) if m else ""
        check("real cc-dash --control: the control code is written to its terminal", bool(code), term[-300:])
        r, _, _ = select.select([rfd], [], [], 15)
        data = os.read(rfd, 100) if r else None
        check("--ready-fd: closed (EOF) once the code is shown, carrying nothing", data == b"", data)
        port = None
        end = time.time() + 30
        while time.time() < end and port is None:
            mm = re.search(rb"serving http://127\.0\.0\.1:(\d+)/", open(log, "rb").read())
            port = int(mm.group(1)) if mm else None
            time.sleep(0.1)
        check("real cc-dash: serving (port in the log)", port is not None)
        needles = [code.encode(), code.replace("-", "").encode()] if code else [b"\0never"]
        check("the code is not in the log", not any(n in open(log, "rb").read() for n in needles))
        check("the code is not in any file under the state dir", _scan_tree(env["XDG_STATE_HOME"], needles) == [],
              _scan_tree(env["XDG_STATE_HOME"], needles))
        check("the code is not in any file under HOME or the config dir", _scan_tree(env["HOME"], needles) == []
              and _scan_tree(env["CLAUDE_CONFIG_DIR"], needles) == [])
        cmdline = open("/proc/%d/cmdline" % pid, "rb").read()
        environ = open("/proc/%d/environ" % pid, "rb").read()
        check("the code is not in the process's argv or environment", not any(n in cmdline or n in environ for n in needles), cmdline)
        if port and code:
            def call(method, path, body=None, token=None):
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                h = {"Host": "127.0.0.1:%d" % port, "Origin": "http://127.0.0.1:%d" % port, "Content-Type": "application/json",
                     "Sec-Fetch-Site": "same-origin"}
                if token:
                    h["X-CC-Dash-Token"] = token
                c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=h)
                rr = c.getresponse()
                out = json.loads(rr.read() or b"null")
                c.close()
                return rr.status, out
            st, out = call("POST", "/api/pair-local", {})
            tok = (out or {}).get("token")
            st2, out2 = call("POST", "/api/control/decide", {"id": "x", "action": "allow"}, token=tok)
            check("agent-style: pair-local then decide is refused (403, needs the code)", st == 200 and st2 == 403, (st, st2, out2))
            st3, out3 = call("POST", "/api/control/unlock", {"code": code}, token=tok)
            check("the code from the terminal unlocks control", st3 == 200 and out3.get("scope") == "control", (st3, out3))
        # A second control-mode cc-dash (an agent's, say) cannot take the lease over.
        log2 = os.path.join(root, "second.log")
        m2, s2 = os.openpty()
        pid2 = _launch_detached([PY, CC_DASH, "--control", "--no-open", "--port", "0"], env, log2, tty=os.ttyname(s2))
        os.close(s2)
        term2 = _read_pty(m2, 15)
        end = time.time() + 20
        while time.time() < end and _alive(pid2):
            time.sleep(0.1)
        out2 = open(log2, "rb").read()
        check("a second cc-dash --control while one holds a live lease: refused, no code shown",
              not _alive(pid2) and b"holds the control lease" in out2 and not CODE_RE.search(term2.decode("utf-8", "replace")), (out2[-300:], term2[-200:]))
        os.close(m2)
        lease = json.load(open(os.path.join(env["XDG_STATE_HOME"], "cc_tool", "control", "lease")))
        check("the first dashboard still holds the lease", lease.get("pid") == pid, lease.get("pid"))
    finally:
        _stop(pid)
        os.close(master)
        os.close(rfd)

    # Under a Claude Code shell (CLAUDECODE=1): refused, whatever the terminal.
    root3 = os.path.join(BASE, "incc")
    env3 = dict(_env(root3), CLAUDECODE="1")
    log3 = os.path.join(root3, "cc-dash.log")
    m3, s3 = os.openpty()
    pid3 = _launch_detached([PY, CC_DASH, "--control", "--no-open", "--port", "0"], env3, log3, tty=os.ttyname(s3))
    os.close(s3)
    term3 = _read_pty(m3, 10)
    end = time.time() + 20
    while time.time() < end and _alive(pid3):
        time.sleep(0.1)
    os.close(m3)
    check("CLAUDECODE=1: cc-dash --control refuses to start, no code shown", b"not from inside a Claude Code session" in open(log3, "rb").read()
          and not CODE_RE.search(term3.decode("utf-8", "replace")), open(log3, "rb").read()[-200:])

    # No terminal: no control mode, and no code anywhere.
    root2 = os.path.join(BASE, "notty")
    env2 = _env(root2)
    log2 = os.path.join(root2, "cc-dash.log")
    pid2 = _launch_detached([PY, CC_DASH, "--control", "--no-open", "--port", "0"], env2, log2)
    end = time.time() + 30
    while time.time() < end and _alive(pid2):
        time.sleep(0.1)
    out = open(log2, "rb").read()
    check("no terminal: cc-dash --control refuses to start (says why)", not _alive(pid2) and (b"needs a terminal" in out or b"was detached" in out), out[-200:])
    check("no terminal: nothing that looks like a code was printed", not CODE_RE.search(out.decode("utf-8", "replace")))


def main():
    try:
        for fn in (test_read_only_tokens, test_unlock_and_lockout, test_pair_local_cap, test_verify_channel, test_launch_refusals,
                   test_card_binding_with_real_hook, test_real_process_code_never_on_disk):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                check(fn.__name__ + " raised", False, "%s: %s" % (type(e).__name__, e))
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
    fails = [r for r in RESULTS if not r[1]]
    print("total=%d  pass=%d  fail=%d" % (len(RESULTS), len(RESULTS) - len(fails), len(fails)))
    for name, _, detail in fails:
        print("  FAIL %-60s %s" % (name, detail[:600]))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
