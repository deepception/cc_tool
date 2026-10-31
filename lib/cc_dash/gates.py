"""Gates from the dashboard: a human passes a factory order's spec or merge gate in control mode.

The gates exist so an agent cannot approve its own work. `cc-factory approve` proves a human at a
terminal; this path has to prove the same thing about a browser, without giving an agent's tool call
anything to use. bin/cc-factory stays the only ledger writer, so the proof has to reach that process.

  GET  /api/control/gate?project=&order=   a card: the full order body at the spec gate, the stored
                                           merge-gate briefing at the merge gate, the value they hash
                                           to, and a one-time view id. Never a scenario: nothing here
                                           reads .factory-holdout/.
  POST /api/control/gate                   {project, order, gate, decision, confirm, view, spec_hash |
                                           lane_head, rework?, note?}

A POST has passed auth.check_write (a control token, bought only with the code on cc-dash's terminal,
plus Origin and Content-Type) before it gets here. decide() then wants the typed word, /proc, the
approvals rate limit, the view id this client was given for this very order and value (single use, so
a replayed POST is refused even when the order came back to the same gate), the order waiting at that
gate, and the value recomputed here equal to the one the human saw: a spec edited since, or a lane head
moved since, is refused before anything is minted. Only then a ticket exists.

The ticket. In this process's memory only: an id, what was decided, on which value, expiring in 30 s.
cc-dash starts bin/cc-factory itself, with a fixed argv (this interpreter, -I, the checkout's script,
validated id and gate, never a note), an allowlisted environment (child_env: no CLAUDE*, PYTHON* or LD_*,
and git told to run no transport, no lazy fetch) and one end of a socketpair on --ticket-fd. The child
asks once on that socket; the ticket is marked used before the answer goes out, and a second question
on it is refused. It reaches nothing else: no file, no argv, no variable, nothing to read in /proc, and
/proc/<pid>/fd/<n> of a socket cannot be reopened.

Why that proves a human (the check is in cc-factory, dashboard_ticket, not here). A lease and hook.sock
are files anything running as you can plant, so the child reads neither: the kernel says who made the
other end of the inherited socket (SO_PEERCRED), that must be its parent (getppid), the parent alone
must hold it (procs.peer_holder_problem), and the parent must pass the control hook's own rules for a
dashboard (the project's manifest, procs.dashboard_problem, procs.code_after_start) and be in control
mode (procs.control_mode_problem: --control and a controlling terminal, without which main() never
starts control mode, so never serves this path). An agent's Bash call has a shell for a parent, however
its flags read. The ledger event carries via=dashboard and the ticket id, and cc-factory refuses an id
it has already seen.

The merge gate does not merge: approve records the merge a human made (cc-factory's merged_lane, which
refuses with the exact git merge --no-ff <sha> otherwise).

What this cannot stop is the same-user limit of docs/guides/factory.md: a process running as you that
rewrites its own argv and environment to pass as cc-dash, or edits bin/cc-factory, or appends to the
ledger with a shell. None of them needs this path. It is no harder to pass than the terminal path, only
different: both first want a process out of Claude Code's lineage. Then the terminal path wants a code
read and typed back on a pseudo-terminal, which any such process can make. This path wants no code, but a
parent running the recorded interpreter with its argv reading as cc-dash --control, on a terminal your
recorded programs hold, and holding the socket alone.

Users: server.py (GET and POST /api/control/gate), web/gates.js, tests/test_cc_dash_control.py.
"""
import json
import os
import secrets
import signal
import socket
import subprocess
import threading
import time

from . import factory as F
from . import procs
from . import selfheal
from .parser import tilde

CC_TOOL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
DECISIONS = ("approve", "reject")
VALUE_KEY = {"spec": "spec_hash", "merge": "lane_head"}
TICKET_TTL = 30.0              # seconds a minted ticket can be redeemed
VIEW_TTL = 3600.0              # a card stays decidable this long: a long spec takes a while to read
MAX_VIEWS = 200
MAX_NOTE = 2000                # characters taken from the dashboard (cc-factory clips what it records to 500)
MAX_REQUEST = 4096             # bytes of the child's question on the ticket socket
CHILD_TIMEOUT = 60.0
CHILD_ENV_KEEP = ("HOME", "USER", "LOGNAME", "PATH", "LANG", "LANGUAGE", "TZ")
NO_PROC = "Factory gates need Linux's /proc to tell your dashboard from any other program of yours, so they work on Linux only."


def child_env(environ=None):
    """The child's whole environment: nothing an agent could have put in cc-dash's, and git set up the way
    self-heal runs it (the ledger's repository is the one lanes share, so its config is an agent's to write)."""
    env = os.environ if environ is None else environ
    out = {k: env[k] for k in CHILD_ENV_KEEP if isinstance(env.get(k), str)}
    out.update(selfheal.GIT_SAFE_ENV)
    return out


class Gates:
    """`cli` is the script to start (tests point it at a copy of the tree); nothing outside this process can set it."""

    def __init__(self, control, docs, cli=None, clock=time.time, ticket_ttl=TICKET_TTL):
        self.ctl = control
        self.docs = docs
        self.cli = cli or os.path.join(CC_TOOL_DIR, "bin", "cc-factory")
        self.clock = clock
        self.ticket_ttl = ticket_ttl
        self.lock = threading.Lock()
        self.views = {}                  # view id -> what that card showed, and to whom
        self.busy = threading.Lock()     # one decision at a time: two would both see the order at its gate

    # ── reading the order ──────────────────────────────────────────────────
    def _load(self, project, order):
        """({main, oid, raw, events, line, order}, None) for a project label and an order id, or (None, (status, error))."""
        oid = F.norm_id(order) if isinstance(order, str) else None
        if not isinstance(project, str) or not project or not oid:
            return None, (400, {"error": "That is not a project and an order id."})
        root = next((p["root"] for p in self.docs.projects() if p["project"] == project), "")
        if not root:
            return None, (404, {"error": "No project with that name."})
        main = F.main_checkout(root)
        path = F.find_order(main, oid)
        raw = F.read_order(main, path) if path else None
        if raw is None:
            return None, (404, {"error": "No order %s in %s." % (oid, project)})
        lp = F.ledger_path(main)
        events = [e for e in F.read_ledger(lp) if e.get("order") == oid] if lp else []
        line = F.line_for(F.line_config(main), raw.get("line"))
        return {"main": main, "oid": oid, "raw": raw, "events": events, "line": line, "project": project,
                "order": F.fold(raw, events, line, self.clock())}, None

    @staticmethod
    def _waiting(s):
        """The name of the gate the order waits at, or ''."""
        g = s["order"].get("gate")
        return g["name"] if isinstance(g, dict) and g.get("waiting") and g.get("name") in VALUE_KEY else ""

    @staticmethod
    def _tip(main, branch):
        """The lane branch's commit, or ''. Through self-heal's git: no config of the repository's can run a program."""
        if not branch:
            return ""
        rc, out, _err = selfheal._git(main, ["rev-parse", "--verify", "--quiet", "refs/heads/%s^{commit}" % branch])
        out = out.strip()
        return out if not rc and F._FULL_SHA_RE.fullmatch(out) else ""

    def _facts(self, s, gate):
        """What the card shows at `gate`, from the same sources cc-factory judges by."""
        if gate == "spec":
            return {"spec_hash": F.spec_hash(s["raw"]["body"], s["line"]), "body": s["raw"]["body"], "path": s["raw"]["path"]}
        ev = next((e for e in reversed(s["events"]) if e.get("event") == "status" and e.get("status") == "review"), {})
        lane = next((e for e in reversed(s["events"]) if e.get("event") == "lane" and e.get("branch")), {})
        head, branch = ev.get("lane_head") or "", lane.get("branch") or ""
        return {"lane_head": head, "briefing": ev.get("briefing") or "", "branch": branch, "moved": self._tip(s["main"], branch) != head,
                "merge_command": "git merge --no-ff %s" % head if head else "", "recommendation": ev.get("recommendation") or "",
                "run_id": ev.get("run_id") or ""}

    def view(self, client, q, can_decide):
        """(http status, response) for GET /api/control/gate: the card, and for a control-scoped token the one-time
        id its decision must carry. A read-only token sees the card (nothing in it is news to that token) and
        gets no id, so it cannot crowd a human's out of the table. Besides the text, the card names what else the
        spec hash covers: the line's stations, retries, pass mark and the commands verify will run."""
        s, err = self._load(q.get("project"), q.get("order"))
        if err:
            return err
        gate = self._waiting(s)
        if not gate:
            return 409, {"error": "%s is not waiting at a gate." % s["oid"]}
        facts = self._facts(s, gate)
        vid = ""
        if can_decide:
            vid, now = secrets.token_hex(16), self.clock()
            with self.lock:
                for k in [k for k, v in self.views.items() if now >= v["expires"]]:
                    del self.views[k]
                for k in list(self.views)[:max(0, len(self.views) + 1 - MAX_VIEWS)]:     # dicts keep insertion order
                    del self.views[k]
                self.views[vid] = {"client": client, "main": s["main"], "order": s["oid"], "gate": gate,
                                   "value": facts[VALUE_KEY[gate]], "expires": now + VIEW_TTL}
        o, line = s["order"], s["line"]
        return 200, dict(facts, project=s["project"], root=tilde(s["main"]), order=s["oid"], title=o["title"], priority=o["priority"],
                         line=o["line"], status=o["status"], rework=o["rework"], gate=gate, view=vid, retries=line["retries"],
                         threshold=F.line_threshold(line), commands={k: line[k] for k in F.COMMAND_KEYS if line.get(k)},
                         stations=[{"name": x["name"], "title": x["title"], "status": x["status"]} for x in o["stations"]])

    # ── a decision ─────────────────────────────────────────────────────────
    def decide(self, client, body):
        """(http status, response) for POST /api/control/gate. Every outcome is audited."""
        b = body if isinstance(body, dict) else {}
        order, gate, decision = b.get("order"), b.get("gate"), b.get("decision")
        rework, note = b.get("rework", False), b.get("note", "")
        what = {"project": b.get("project") if isinstance(b.get("project"), str) else None, "order": order if isinstance(order, str) else None,
                "gate": gate if isinstance(gate, str) else None, "decision": decision if isinstance(decision, str) else None}

        def refuse(code, msg, **extra):
            self.ctl.log(client, "refused", kind="gate", result=msg[:500], **what)
            return code, dict({"error": msg}, **extra)
        if decision not in DECISIONS or gate not in VALUE_KEY:
            return refuse(400, "That is not a gate decision.")
        if b.get("confirm") != decision:
            return refuse(428, "Type %s to confirm." % decision, confirm=decision)
        if not procs.have_proc():
            return refuse(403, NO_PROC)
        if not isinstance(rework, bool) or (rework and (decision, gate) != ("reject", "merge")):
            return refuse(400, "Rework applies to rejecting at the merge gate.")
        if not isinstance(note, str) or len(note) > MAX_NOTE:
            return refuse(400, "A note is text, at most %d characters." % MAX_NOTE)
        if not self.ctl._rate_ok(client):
            return refuse(429, "Too many approvals in a row; wait a few seconds.")
        if not self.busy.acquire(blocking=False):
            return refuse(409, "Another gate decision is being recorded; try again in a moment.")
        try:
            s, err = self._load(b.get("project"), order)
            if err:
                return refuse(err[0], err[1]["error"])
            problem = self._problem(client, b, s, gate, decision)
            if problem:
                return refuse(*problem)
            value = b[VALUE_KEY[gate]]
            ticket = {"id": secrets.token_hex(16), "main": s["main"], "order": s["oid"], "gate": gate, "decision": decision,
                      "rework": rework, "note": note.strip(), "value": value, "expires": self.clock() + self.ticket_ttl, "used": False}
            rc, out, errs = self._run(ticket)
            done = dict(what, order=s["oid"], gate=gate, rework=rework or None, note=F.clean_note(ticket["note"]) or None, value=value, ticket=ticket["id"])
            if rc is None:
                self.ctl.log(client, "gate", kind="gate", result="cc-factory did not finish: %s" % errs[:300], **done)
                return 504, {"error": "cc-factory did not finish: %s" % errs[:300]}
            text = selfheal._line(errs if rc else out.partition("\n")[0], 1500)
            self.ctl.log(client, "gate", kind="gate", result=text if rc else "recorded: %s" % text, **done)
            if rc:
                return 409, {"error": text or "cc-factory refused (exit %d)." % rc, "refused": True}
            s2, _err = self._load(b.get("project"), order)
            return 200, {"ok": True, "order": s["oid"], "gate": gate, "decision": decision, "result": text,
                         "status": s2["order"]["status"] if s2 else ""}
        finally:
            self.busy.release()

    def _take_view(self, vid):
        with self.lock:
            return self.views.pop(vid, None) if isinstance(vid, str) else None

    def _problem(self, client, b, s, gate, decision):
        """(status, why) when this decision must not reach a ticket, else None. The view goes first and is
        spent whatever follows: a second POST of the same body is refused here, whatever state the order is in."""
        v = self._take_view(b.get("view"))
        if (v is None or self.clock() >= v["expires"] or v["client"] != client
                or (v["main"], v["order"], v["gate"]) != (s["main"], s["oid"], gate)):
            return 409, "This card is out of date or was already used. Reload it."
        if self._waiting(s) != gate:
            return 409, "%s is not waiting at the %s gate." % (s["oid"], gate)
        key = VALUE_KEY[gate]
        facts = self._facts(s, gate)
        seen = b.get(key)
        if not isinstance(seen, str) or seen != v["value"] or seen != facts[key]:
            return 409, ("The spec changed since you read it. Reload it and read it again." if gate == "spec" else
                         "The review changed since you read it. Reload it and read it again.")
        if gate == "merge" and decision == "approve":
            if not facts["lane_head"]:
                return 409, "This review recorded no lane head, so no commit could pass. Send it back for another run."
            if not facts["briefing"]:
                return 409, "This review stored no briefing, so there is nothing here to decide from. Use cc-factory in your own terminal."
            if facts["moved"]:
                return 409, "The lane moved after the review (or cannot be read), so it would pass commits no station reviewed. Send it back for another run."
        return None

    # ── the child ──────────────────────────────────────────────────────────
    def _argv(self, t, fd):
        exe = os.path.realpath("/proc/self/exe")
        return [exe, "-I", self.cli, "-C", t["main"], t["decision"], t["order"], "--gate", t["gate"]] + \
            (["--rework"] if t["rework"] else []) + ["--ticket-fd", str(fd)]

    def _run(self, t):
        """Start cc-factory for ticket `t` and answer it: (exit code, stdout, stderr); exit code None when it
        could not start or did not finish. A session of its own: it has no way to the terminal cc-dash holds."""
        ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            ours.settimeout(CHILD_TIMEOUT)
            try:
                p = subprocess.Popen(self._argv(t, theirs.fileno()), cwd=t["main"], env=child_env(), stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace",
                                     pass_fds=(theirs.fileno(),), start_new_session=True)
            except OSError as e:
                return None, "", "it could not start (%s)" % (e.strerror or e)
            finally:
                theirs.close()
            server = threading.Thread(target=self._serve, args=(ours, t), name="cc-dash-gate", daemon=True)
            server.start()
            try:
                out, err = p.communicate(timeout=CHILD_TIMEOUT)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except OSError:
                    pass
                p.communicate()
                return None, "", "it ran past %d seconds and was stopped" % CHILD_TIMEOUT
            server.join(2)
            return p.returncode, out, err
        finally:
            ours.close()

    def _serve(self, sock, t):
        """One JSON line in, one out, until the child hangs up: the first question spends the ticket."""
        buf = b""
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    return
                buf += chunk
                if len(buf) > MAX_REQUEST:
                    return
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    sock.sendall(json.dumps(self._redeem(t, line), separators=(",", ":")).encode("utf-8") + b"\n")
        except OSError:
            return

    def _redeem(self, t, line):
        """The answer to one question on the ticket socket: the ticket, once, while it lasts, for what it decides."""
        used, t["used"] = t["used"], True
        if used:
            return {"ok": False, "error": "this ticket was already used"}
        if self.clock() >= t["expires"]:
            return {"ok": False, "error": "this ticket has expired"}
        try:
            req = json.loads(line)
        except ValueError:
            req = None
        if not isinstance(req, dict) or req.get("op") != "redeem" or any(req.get(k) != t[k] for k in ("order", "gate", "decision", "rework")):
            return {"ok": False, "error": "this ticket is for another decision"}
        return {"ok": True, "ticket": {k: t[k] for k in ("id", "order", "gate", "decision", "rework", "note", "value", "expires")}}
