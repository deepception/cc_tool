"""Gates from the dashboard: a human passes a factory order's spec or merge gate in control mode.

The gates exist so an agent cannot approve its own work. `cc-factory approve` proves a human at a
terminal; this path has to prove the same thing about a browser, without giving an agent's tool call
anything to use. bin/cc-factory stays the only ledger writer, so the proof has to reach that process.

  GET  /api/control/gate?project=&order=   a card: the full order body at the spec gate (and for a
                                           re-approval or a drop), the stored merge-gate briefing at the
                                           merge gate, the value they hash to, and a one-time view id.
                                           Never a scenario: nothing here reads the holdout.

Which orders have a card is one rule, factory.decisions(), called by view() and again by _problem() on the
order as it is at that moment: spec gate waiting (approve, reject); merge gate waiting (approve, reject,
rework); an approved, failed or blocked order whose spec changed since approval (re-approve, drop: the spec
gate's ticket on the current spec hash); a queued, approved, failed or blocked order (drop only: reject at
the spec gate; a queued order is never approved, intake is unfinished). A building or verifying order has no
card, and a hand-built POST for one is refused with 409 before any ticket exists.
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
dashboard (the user-wide code record, procs.dashboard_problem, procs.code_after_start) and be in control
mode (procs.control_mode_problem: --control and a controlling terminal, without which main() never
starts control mode, so never serves this path). An agent's Bash call has a shell for a parent, however
its flags read. The ledger event carries via=dashboard and the ticket id, and cc-factory refuses an id
it has already seen.

Approve merge merges. After every check above and the typed word, and before the ticket child starts, this
process runs git merge --no-ff <the reviewed lane head> in the project's main checkout, on the branch its HEAD
is on, through selfheal._git (hooks off, no transport, no editor). It is refused, with nothing written, when
the checkout has uncommitted changes to tracked files or a merge, rebase or cherry-pick in progress, HEAD is
detached, the repository config (includes resolved) has a filter or merge key ending in clean, smudge,
process or driver in any subsection, or .git/info/attributes has any attribute line. The merge itself runs with
-c submodule.recurse=false (git 2.43's merge has no --no-recurse-submodules, so a submodule's own config under
.git/modules is never reached), with attributes taken only from the empty tree (--attr-source=<empty tree>),
core.attributesFile and system attributes off, and signature checks off. So no .gitattributes in the tree, and no
global or system attributes file, can name a filter or merge driver, whatever the config says by merge time: a
driver a writer adds to the config after the check is not reached through attributes. git has no switch that
ignores .git/info/attributes (checked on git 2.43: it is still read under --attr-source), so its refusal stays,
and one window is left: a writer to .git that adds an info/attributes line and a driver between the check and
the merge. That needs write access to .git, the same-user limit below. The merge commit carries the time this
process took immediately before the merge, rounded up to the next whole second, as its committer time, and the merge
starts no earlier than that second (a wait of under 1 s). Every git call on this path (the checks, the status, the
merge, the git merge --abort after a conflict and the reads after it) runs with the same --attr-source and submodule.recurse=false
(the empty-tree id comes from a hash-object of empty stdin with --no-filters, which reads no attributes), so the abort's checkout
cannot run a filter added after the check either. On a conflict it runs git merge --abort and the checkout is as it was. A head already in HEAD is not merged again. It never pushes and never touches
the lane branch or worktree. The child's approve --gate merge then finds the merge (merged_lane) and records
it. The terminal path is unchanged: you merge, cc-factory records.

The code record is ONE file for every project (cc_procs.code_record_path: $XDG_STATE_HOME/cc_tool/cc_dash_code.json,
0600, read by cc_procs.read_code_record with the old manifest's rules). It moves in two ways only. (1) A human at their
own terminal: `cc-dash --record-code` (also cc-setup, cc-update-project), which refuse under procs.recording_problem. (2)
This path: the cc-factory child of an Approve merge that merged a reviewed lane into the cc_tool checkout itself. Merging
changes cc-dash's code on disk, so the child (bin/cc-factory merge_code_delta) works out from git alone which files the
merge brought in unchanged from the reviewed commit (HEAD is a two-parent merge of exactly the reviewed head, HEAD's
tree equals the tree of a clean `git merge-tree --write-tree` of the first parent and the reviewed commit, the file
differs between merge-base(first parent, reviewed) and the reviewed commit, differs from HEAD's first parent and equals
the reviewed commit's), judges its parent against the record plus that delta,
writes the ledger event and re-records exactly those files. The files and directories the merge changed are exempt
from the dashboard's change-time check (procs.code_after_start) only from the merge commit's time on (that rounded-up timestamp, never earlier than the moment cc-dash took it): anything changed
before it and after the dashboard started is still refused. Then the dashboard, which runs the old code, tells you it must
restart (the 409 box before any decision, `code_outside`, and a Restart button: POST /api/control/restart, which only a
control token may call and only when the code is the recorded one but newer than this process). Why this is no weakening:
an agent's Bash write to cc-dash's code (or a hand edit, `git pull`, an evil merge) is in no delta, so the disk differs
from the record and every decision is refused (here with the record-code box, and again by the child), and no tool call
can write the record (write-guard and bash-guard name it: bash-guard denies any command that names the record unless it only reads it (an allowlisted
reader with no output redirect or tee) or names it in a commit message, and any cc-dash --record-code however it is started or beside a program word it cannot read ($(...), a backtick, a $ variable); --record-code and cc-setup refuse under Claude Code; a ticket
needs a dashboard that passes the code check). A restart after such an edit passes nothing: the new process still faces
the old record. Only code a human approved is trusted: recorded from their terminal, or the exact tree they merged.

IDE terminals. procs.terminal_problem rescans once when a holder vanished; a holder that vanishes again on the rescan
is never assumed gone: the check refuses ("kept changing; try again"). The scans for hidden holders
(procs.socket_holders, pty_master_scan) list /proc once more when a pending unreadable process exits, so a child it
forked meanwhile is judged too. Such a newcomer that cannot be read counts as hidden at once; the checks that
act on a scan (procs.peer_holder_problem, terminal_problem) then run one fresh scan, and refuse if anything is hidden in it. A claude CLI holder passes only when it
inherited the master from the terminal host, every master descriptor of it has O_CLOEXEC and ptrace_scope is 1 or more:
no tool call of that claude inherits the descriptor (CLOEXEC closes it at exec; /proc/<pid>/fd/<n> of a ptmx opens a new
pty, not that master), a descendant cannot ptrace or pidfd_getfd its ancestor, and a descendant of claude holding the
master is still refused. So no agent tool call gets to read the control code.

What this cannot stop is the same-user limit of docs/guides/factory.md: a process running as you that
rewrites its own argv and environment to pass as cc-dash, or edits bin/cc-factory, or appends to the
ledger with a shell. None of them needs this path. It is no harder to pass than the terminal path, only
different: both first want a process out of Claude Code's lineage. Then the terminal path wants a code
read and typed back on a pseudo-terminal, which any such process can make. This path wants no code, but a
parent running the recorded interpreter with its argv reading as a control-mode dashboard, on a pseudo-terminal
held by a program that is not a generic interpreter or terminal driver (a live check, procs.terminal_problem
with the rules cc-update-project records by, replacing the per-project list of recorded terminals), and
holding the socket alone. The live check is weaker than the recorded list: an agent that starts a
non-interpreter terminal program (tmux, an xterm) with a scrubbed environment, detached from Claude Code's
lineage, now passes where an unrecorded program used to be refused. That sits inside the same-user limit
above, and is the trade the user chose so that any terminal in any directory covers every project.

A refusal that needs the user outside the dashboard carries `outside` ({kind, why, where, steps, then}, built from
the order, the paths and the reviewed head, never parsed from text); one the dashboard can resolve itself carries
`reload`; an unrecognised cc-factory BLOCKED line carries `blocked` ({reason, suggestion}). `error` stays the raw text.

Users: server.py (GET and POST /api/control/gate), web/gates.js, tests/test_cc_dash_control.py.
"""
import json
import math
import os
import re
import secrets
import shlex
import signal
import socket
import subprocess
import threading
import time

from . import factory as F
from . import procs
from . import selfheal
from .parser import tilde

DRIVER_SECTIONS = ("filter", "merge")                       # config sections whose keys can name a program
DRIVER_KEYS = ("clean", "smudge", "process", "driver")      # ... the keys that do, in any subsection
CC_TOOL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
DECISIONS = ("approve", "reject")
VALUE_KEY = {"spec": "spec_hash", "merge": "lane_head"}
TICKET_TTL = 30.0              # seconds a minted ticket can be redeemed
VIEW_TTL = 3600.0              # a card stays decidable this long: a long spec takes a while to read
MAX_VIEWS = 200
MAX_NOTE = 2000                # characters taken from the dashboard (cc-factory clips what it records to 500)
MAX_REQUEST = 4096             # bytes of the child's question on the ticket socket
CHILD_TIMEOUT = 60.0
CHILD_ENV_KEEP = ("HOME", "USER", "LOGNAME", "PATH", "LANG", "LANGUAGE", "TZ", "XDG_STATE_HOME")
NO_PROC = "Factory gates need Linux's /proc to tell your dashboard from any other program of yours, so they work on Linux only."


ORDER_ID = re.compile(r"^FO-\d{1,6}\Z")
BUSY_ABORT = {"merge": "git merge --abort", "cherry-pick": "git cherry-pick --abort", "revert": "git revert --abort",
              "rebase": "git rebase --abort", "rebase or am": "git am --abort", "bisect": "git bisect reset",
              "sequenced pick": "git cherry-pick --abort"}


def where_(app, path):
    """Where a next step is done: app is "terminal" or "claude"; path is the tilde'd checkout ('' when unknown)."""
    return {"app": app, "path": path or ""}


def outside(kind, why, at, steps, then=""):
    """One "Do this outside ccdash" box as data: what and why, where, each command in order, and what to do back here."""
    return {"kind": kind, "why": why, "where": at,
            "steps": [{"command": x["command"], "where": x.get("where") or at} for x in steps], "then": then}


def sh(p):
    """A path for a shell command line: quoted, with a leading ~/ left alone so the shell still expands it."""
    return "~/" + shlex.quote(p[2:]) if p.startswith("~/") else shlex.quote(p)


def decide_command(oid, decision, gate, rework=False):
    return "cc-factory %s %s --gate %s%s" % (decision, oid, gate, " --rework" if rework else "")


def blocked_parts(text):
    """{reason, suggestion} from cc-factory's `BLOCKED: <reason> Suggestion: <what to do>` line, else None."""
    if not isinstance(text, str) or not text.startswith("BLOCKED: "):
        return None
    reason, _sep, suggestion = text[len("BLOCKED: "):].partition(" Suggestion: ")
    return {"reason": reason.strip(), "suggestion": suggestion.strip()}


def _safe_rel(p):
    return bool(p) and not any(ord(c) < 32 or ord(c) == 127 for c in p) and not p.startswith(("-", "/")) \
        and ".." not in p.split("/") and not p.endswith("…")


def order_outside(o):
    """The box for a stuck order from the floor's sanitised order dict, or None: a run that may be dead, a build
    that needs a file deleted, a lane that changed under verify, or any other blocked or failed order."""
    try:
        oid, status = o.get("id") or o.get("order") or "", o.get("status")
        if not ORDER_ID.match(oid):
            return None
        main = o.get("main") or ""
        lane = (o.get("lane") or {}).get("worktree") or ""
        note = ((o.get("why") or {}).get("note") or "")
        term, claude = where_("terminal", main), where_("claude", main)
        rerun = {"command": "/factory run %s" % oid, "where": claude}
        if status in ("building", "verifying"):
            return outside("dead-run", "Only when the run is dead (its Claude Code session ended or crashed): mark it failed so "
                           "the line can run it again.", term,
                           [{"command": 'cc-factory mark %s failed --note "run died"' % oid}, rerun])
        if status == "blocked" and note.startswith("needs-deletion:") and lane:
            paths = [p.strip() for p in note[len("needs-deletion:"):].split(",")]
            paths = [p for p in paths if _safe_rel(p)]
            if paths:
                steps = [{"command": "git -C %s rm -- %s" % (sh(lane), shlex.quote(p))} for p in paths]
                steps.append({"command": "git -C %s commit -m %s" % (sh(lane), shlex.quote("%s: remove %s" % (oid, ", ".join(paths))))})
                return outside("needs-deletion", "The build needs %s removed, and no station may delete a file: check the removal "
                               "is right, then delete it in the lane and commit." % ", ".join(paths), term, steps + [rerun],
                               "The rerun continues from the lane's commits.")
        if status == "blocked" and note.startswith("lane-tampered:"):
            steps = ([{"command": "git -C %s status" % sh(lane)}, {"command": "git -C %s diff" % sh(lane)}] if lane
                     else [{"command": "git worktree list"}])
            return outside("lane-tampered", "The lane worktree changed while verify ran the lane's code in its throwaway copy, "
                           "so read the lane before anything else.", term, steps,
                           "Then rewrite the scenarios before any rerun, or drop the order.")
        if status in ("blocked", "failed"):
            return outside("rerun", "The run stopped: fix the cause shown under Why, then rerun it. The rerun continues from "
                           "the lane's commits.", claude, [rerun])
    except Exception:
        return None
    return None


def child_env(environ=None):
    """The child's whole environment: nothing an agent could have put in cc-dash's, and git set up the way
    self-heal runs it (the ledger's repository is the one lanes share, so its config is an agent's to write)."""
    env = os.environ if environ is None else environ
    out = {k: env[k] for k in CHILD_ENV_KEEP if isinstance(env.get(k), str)}
    out.update(selfheal.GIT_SAFE_ENV)
    return out


def merge_clock():
    """The merge commit's committer time: now rounded up to the next whole second, returned once that second has
    begun (a wait of under 1 s). merge_code_delta reads it back as HEAD's %ct, the bound of code_after_start's
    exemption, so the bound is never earlier than this moment, and no file the merge writes is older than it."""
    t = math.ceil(time.time())
    while True:
        left = t - time.time()
        if left <= 0:
            break
        time.sleep(left)
    return t


class Gates:
    """`cli` is the script to start (tests point it at a copy of the tree); nothing outside this process can set it."""

    def __init__(self, control, docs, cli=None, clock=time.time, ticket_ttl=TICKET_TTL):
        self.ctl = control
        self.docs = docs
        self.cli = cli or os.path.join(CC_TOOL_DIR, "bin", "cc-factory")
        self.start = None                # when this process started (tests set it; else procs.process_start)
        self.clock = clock
        self.ticket_ttl = ticket_ttl
        self.lock = threading.Lock()
        self.views = {}                  # view id -> what that card showed, and to whom
        self.busy = threading.Lock()     # one decision at a time: two would both see the order at its gate

    # ── the code record ────────────────────────────────────────────────────
    def code_root(self):
        """The cc_tool checkout whose code this dashboard runs: the one the cc-factory it starts belongs to."""
        return os.path.dirname(os.path.dirname(os.path.realpath(self.cli)))

    def _code_outside(self, main=""):
        """The box for code that is not what you approved, or None. For the user's benefit only, so a decision is not
        attempted in vain: the check that counts is cc-factory's, in the child (bin/cc-factory dashboard_problem).
        Two cases: the code on disk differs from the user-wide record (one command re-records it for every project,
        run from your terminal), or it matches but changed after this dashboard started (a merge you approved
        re-recorded it): the dashboard only needs a restart."""
        root = self.code_root()
        term = where_("terminal", "")
        try:
            record, why = procs.read_code_record(root)
            why = why or procs.manifest_problem(root, record)
            if why:
                return outside("record-code", why.rstrip(".") + ". Only code you approved is trusted, so nothing is decided until it "
                               "is recorded.", term, [{"command": procs.RECORD_COMMAND}, {"command": "ccdash --control"}],
                               "Then unlock the dashboard again with the new code shown on your terminal.")
            why = procs.code_after_start(os.getpid(), root, procs.code_record_path(), start=self.start)
        except Exception:  # noqa: BLE001 - the child decides; this is only the early warning
            return None
        if why:
            box = outside("restart", "cc-dash's code changed after this dashboard started (a merge you approved re-recorded it), so "
                          "it must restart before it can decide again.", term, [{"command": "ccdash --control"}],
                          "Or press Restart here; then unlock the dashboard again with the new code shown on your terminal.")
            return dict(box, restart=True)
        return None

    def restart(self):
        """(http status, response) for POST /api/control/restart: 200 only when a restart is all that is needed."""
        box = self._code_outside()
        if box is None or not box.get("restart"):
            return 409, {"error": "There is no restart to do: the code on disk is not the code you approved, or it has not changed "
                                  "since this dashboard started.", "outside": box}
        return 200, {"ok": True}

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
    def _allowed(s):
        """What the dashboard may decide on the order as it is now: factory.decisions(), the one rule."""
        return F.decisions(s["order"])

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
        a = self._allowed(s)
        if not a:
            return 409, {"error": "%s has no decision for you on the dashboard (status %s)." % (s["oid"], s["order"]["status"])}
        gate = a["gate"]
        facts = self._facts(s, gate)
        extra = {}
        if gate == "merge":
            g = self._merge_git(s["main"])[0] or selfheal._git   # same protections as the merge on every call
            rc, br, _e = g(s["main"], ["symbolic-ref", "-q", "--short", "HEAD"])
            head = facts["lane_head"]
            rc2 = g(s["main"], ["merge-base", "--is-ancestor", head, "HEAD"])[0] if head else 1
            kind, mb = self._merge_block(s["main"])
            extra = {"into": {"branch": br.strip() if not rc else "", "checkout": tilde(s["main"])},
                     "merged": rc2 == 0, "merge_blocked": mb,
                     "merge_outside": self._merge_outside(s, kind, head) if mb or not head else None}
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
        return 200, dict(facts, **extra, code_outside=self._code_outside(s["main"]), kind=a["kind"], decisions={k: a[k] for k in ("approve", "reject", "rework")},
                         spec_changed=bool(o.get("spec_changed")), project=s["project"], root=tilde(s["main"]), order=s["oid"], title=o["title"], priority=o["priority"],
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
            s, _e = self._load(b.get("project"), order)
            box = outside("no-proc", NO_PROC + " Start the dashboard on a Linux machine to decide here.", where_("terminal", tilde(s["main"])),
                          [{"command": "ccdash --control"}], "Then open the address it prints and decide on the card there.") if s else None
            return refuse(403, NO_PROC, **({"outside": box} if box else {}))
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
            box = self._code_outside(s["main"])
            if box:                      # before the view is spent, the merge or any ticket: the card still reloads
                return refuse(409, box["why"], outside=box)
            problem = self._problem(client, b, s, gate, decision)
            if problem:
                code, msg, *rest = problem
                return refuse(code, msg, **(rest[0] if rest else {}))
            value = b[VALUE_KEY[gate]]
            merged = ""
            if (gate, decision) == ("merge", "approve"):
                merged, why, kind = self._merge(s, value)
                if why:
                    return refuse(409, why, outside=self._merge_outside(s, kind, value))
            ticket = {"id": secrets.token_hex(16), "main": s["main"], "order": s["oid"], "gate": gate, "decision": decision,
                      "rework": rework, "note": note.strip(), "value": value, "expires": self.clock() + self.ticket_ttl, "used": False}
            rc, out, errs = self._run(ticket)
            done = dict(what, order=s["oid"], gate=gate, rework=rework or None, note=F.clean_note(ticket["note"]) or None, value=value, ticket=ticket["id"], merge=merged or None)
            if rc is None:
                self.ctl.log(client, "gate", kind="gate", result="cc-factory did not finish: %s" % errs[:300], **done)
                return 504, {"error": "cc-factory did not finish: %s" % errs[:300]}
            text = selfheal._line(errs if rc else out.partition("\n")[0], 1500)
            self.ctl.log(client, "gate", kind="gate", result=text if rc else "recorded: %s" % text, **done)
            if rc:
                msg = text or "cc-factory refused (exit %d)." % rc
                oid, term = s["oid"], where_("terminal", tilde(s["main"]))
                if merged and merged != "already":
                    msg = ("Merged %s into the checkout, but cc-factory did not record it: %s Record it from your terminal: "
                           "cc-factory approve %s --gate merge." % (merged[:12], msg, oid))
                    extra = {"outside": outside("record-merge", "The reviewed commit is merged into the checkout, but cc-factory did "
                                                "not record it, so record it from your own terminal.", term,
                                                [{"command": decide_command(oid, "approve", "merge")}])}
                elif "what the dashboard showed is no longer what is there" in text:
                    extra = {"reload": True}
                elif "no valid ticket from your dashboard" in text or "not started from your own terminal" in text:
                    extra = {"outside": outside("decide-in-terminal", "cc-factory did not take this decision from the dashboard (the "
                                                "reason is under Details), so make it from your own terminal: it shows a code to type "
                                                "back.", term, [{"command": decide_command(oid, decision, gate, rework)}],
                                                "Then reload this card.")}
                else:
                    bp = blocked_parts(text)
                    extra = {"blocked": bp} if bp else {}
                return 409, dict({"error": msg, "refused": True}, **extra)
            s2, _err = self._load(b.get("project"), order)
            out = {"ok": True, "order": s["oid"], "gate": gate, "decision": decision, "result": text,
                   "status": s2["order"]["status"] if s2 else ""}
            after = self._code_outside(s["main"]) if (gate, decision) == ("merge", "approve") else None
            if after and after.get("restart"):     # the merge re-recorded cc-dash's own code: this process runs the old one
                out.update(restart=True, outside=after)
            return 200, out
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
            return 409, "This card is out of date or was already used. Reload it.", {"reload": True}
        a = self._allowed(s)
        if a is None or a["gate"] != gate:
            return 409, "%s is no longer waiting for that decision." % s["oid"], {"reload": True}
        if not a[decision] or (b.get("rework") and not a["rework"]):
            return 409, "%s does not allow that decision now." % s["oid"], {"reload": True}
        key = VALUE_KEY[gate]
        facts = self._facts(s, gate)
        seen = b.get(key)
        if not isinstance(seen, str) or seen != v["value"] or seen != facts[key]:
            return 409, ("The spec changed since you read it. Reload it and read it again." if gate == "spec" else
                         "The review changed since you read it. Reload it and read it again."), {"reload": True}
        if gate == "merge" and decision == "approve":
            if not facts["lane_head"]:
                return 409, "This review recorded no lane head, so no commit could pass. Send it back for another run.", {"outside": self._merge_outside(s, "no-head", "")}
            if not facts["briefing"]:
                return 409, "This review stored no briefing, so there is nothing here to decide from. Use cc-factory in your own terminal.", {"outside": self._merge_outside(s, "briefing", facts["lane_head"])}
            if facts["moved"]:
                return 409, "The lane moved after the review (or cannot be read), so it would pass commits no station reviewed. Send it back for another run."
        return None

    # ── the merge ──────────────────────────────────────────────────────────
    @staticmethod
    def _merge_git(main):
        """(g, prefix) for every git call on the dashboard merge path: g(root, args, **kw) is selfheal._git with
        --attr-source=<empty tree> and -c submodule.recurse=false in front, so no call here (checks, status, the merge,
        the abort after a conflict, the reads after it) reads tree attributes or recurses into a submodule. (None, [])
        when git cannot give the empty tree. The bootstrap hash-object reads no attributes: empty stdin, no --path,
        --no-filters, and a tree object is never filtered."""
        rc, empty, _e = selfheal._git(main, ["-c", "submodule.recurse=false", "hash-object", "-t", "tree", "--stdin", "--no-filters"])
        empty = empty.strip()
        if rc or not selfheal.SHA_RE.fullmatch(empty):
            return None, []
        prefix = ["--attr-source=" + empty, "-c", "submodule.recurse=false"]
        return (lambda root, args, **kw: selfheal._git(root, prefix + list(args), **kw)), prefix

    @staticmethod
    def _merge_problem(main):
        """The text of _merge_block: why a merge in `main` would be refused right now, '' when none."""
        return Gates._merge_block(main)[1]

    @staticmethod
    def _merge_block(main):
        """(kind, why) for a merge in `main` refused right now, ('', '') when none: a merge, rebase or cherry-pick in
        progress, a detached HEAD, a filter or merge driver key (DRIVER_SECTIONS, DRIVER_KEYS) in any subsection of
        the repository's config with includes resolved, attributes in .git/info/attributes (git has no switch to
        ignore that file in a merge: --attr-source covers only the tree), or uncommitted changes to tracked files.
        Every call here runs with the empty attribute source and submodule.recurse=false (_merge_git). The merge itself
        (_merge) cannot be reached through tree attributes whatever the config says by then. Fails closed when git cannot
        list the config."""
        g, _p = Gates._merge_git(main)
        if g is None:
            return "git", "git cannot read %s." % tilde(main)
        rc, gd, _e = g(main, ["rev-parse", "--absolute-git-dir"])
        if rc or not gd.strip():
            return "git", "git cannot read %s." % tilde(main)
        busy = [n for n in selfheal.GIT_BUSY if os.path.lexists(os.path.join(gd.strip(), n))]
        if busy:
            name = selfheal.GIT_BUSY[busy[0]]
            return "busy:" + name, "%s is in the middle of a %s: finish or abort it first." % (tilde(main), name)
        if g(main, ["symbolic-ref", "-q", "HEAD"])[0]:
            return "detached", "HEAD in %s is detached: check out the branch to merge into first." % tilde(main)
        rc, out, _e = g(main, ["config", "--includes", "--list", "-z", "--name-only"])
        if rc:
            return "git", "git cannot read the config of %s; merge in your terminal." % tilde(main)
        for key in out.split("\0"):
            k = key.strip().lower()
            if k and k.split(".", 1)[0] in DRIVER_SECTIONS and k.rsplit(".", 1)[-1] in DRIVER_KEYS:
                return "driver", ("The repository config of %s defines a filter or merge driver (%s), which would run a program; "
                                  "merge in your terminal." % (tilde(main), key.strip()))
        rc, ap, _e = g(main, ["rev-parse", "--git-path", "info/attributes"])
        if rc or not ap.strip():
            return "git", "git cannot read %s." % tilde(main)
        ap = os.path.join(main, ap.strip())
        try:
            with open(ap, encoding="utf-8", errors="replace") as fh:
                lines = [ln.strip() for ln in fh.read().splitlines()]
        except FileNotFoundError:
            lines = []
        except OSError:
            lines = ["?"]
        if any(ln and not ln.startswith("#") for ln in lines):
            return "attributes", ("%s has attributes in its info/attributes file, which a merge cannot be told to ignore; "
                                  "merge in your terminal." % tilde(main))
        # status can run a clean filter; g reads attributes from the empty tree, so a driver added to the config after
        # the check above is not reachable through the tree's attributes.
        rc, out, _e = g(main, ["status", "--porcelain=v1", "-z", "--untracked-files=no"])
        if rc or out:
            return "dirty", "%s has uncommitted changes to tracked files: commit or stash them first." % tilde(main)
        return "", ""

    def _merge(self, s, head):
        """(result, why, kind): merge exactly the reviewed `head` with --no-ff into the branch HEAD is on in the main
        checkout. result is "already" when head is in HEAD (nothing to do) or the new HEAD. Never pushes, never
        touches the lane. On a conflict the merge is aborted, with the same protections, and the checkout is as it was. kind names a refusal
        for _merge_outside."""
        main = s["main"]
        kind, why = self._merge_block(main)
        if why:
            return "", why, kind
        g, prefix = self._merge_git(main)
        if g is None:
            return "", "git cannot read %s, so the dashboard cannot merge." % tilde(main), "git"
        if g(main, ["merge-base", "--is-ancestor", head, "HEAD"])[0] == 0:
            return "already", "", ""
        ident = selfheal._git_identity(main, prefix)
        if not ident:
            return "", "git has no user.name and user.email in %s, so it cannot make a merge commit." % tilde(main), "identity"
        rc, br, _e = g(main, ["symbolic-ref", "-q", "--short", "HEAD"])
        before = g(main, ["rev-parse", "HEAD"])[1].strip()
        # Attributes come from the empty tree and recursion is off on every call (g), including the abort's checkout, since
        # the config may change after _merge_block. git 2.43's merge accepts no --no-recurse-submodules: the -c is the switch.
        t0 = merge_clock()
        rc, _o, err = g(main, ["merge", "--no-ff", "--no-edit", "--no-verify-signatures", "--no-gpg-sign", "--no-stat", head],
                        identity=ident, committer_date=t0)
        if rc:
            gd = g(main, ["rev-parse", "--absolute-git-dir"])[1].strip()
            if gd and os.path.lexists(os.path.join(gd, "MERGE_HEAD")):
                if g(main, ["merge", "--abort"])[0]:
                    return "", "The merge into %s conflicted and git could not abort it: run git merge --abort in %s." % (br.strip(), tilde(main)), "abort-failed"
            return "", ("The merge into %s did not apply (%s). The merge was aborted and %s is as it was; merge it in your terminal."
                        % (br.strip(), selfheal._line(err, 200) or "conflict", tilde(main))), "conflict"
        after = g(main, ["rev-parse", "HEAD"])[1].strip()
        second = g(main, ["rev-parse", "--verify", "-q", "HEAD^2"])[1].strip()
        if after == before or second != head:
            return "", "The merge did not produce the reviewed commit as a parent; check %s." % tilde(main), "no-parent"
        return after, "", ""

    def _merge_outside(self, s, kind, head):
        """The box for a merge refused as `kind` (a _merge_block or _merge kind): what to do in the main checkout."""
        m = tilde(s["main"])
        term = where_("terminal", m)
        if not head:
            return outside("send-back", "This review recorded no lane head, so there is no reviewed commit to merge: send the order "
                           "back for another run.", term, [{"command": decide_command(s["oid"], "reject", "merge", rework=True)}],
                           "Or press Reject and send it back on this card, then run it again.")
        record = {"command": decide_command(s["oid"], "approve", "merge")}
        again = "Then reload this card and press Approve merge again."
        merge = {"command": "git merge --no-ff %s" % head}
        if kind == "dirty":
            return outside("dirty-checkout", "%s has uncommitted changes to tracked files, and the dashboard only merges into a clean "
                           "checkout." % m, term, [{"command": "git status --short --untracked-files=no"}, {"command": "git stash push"}],
                           "Then reload this card and press Approve merge again; git stash pop brings the changes back afterwards.")
        if kind.startswith("busy:"):
            name = kind[5:]
            return outside("git-busy", "%s is in the middle of a %s: finish it, or abort it." % (m, name), term,
                           [{"command": "git status"}, {"command": BUSY_ABORT.get(name, "git status")}], again)
        if kind == "abort-failed":
            return outside("git-busy", "The merge conflicted and git could not abort it in %s." % m, term,
                           [{"command": "git merge --abort"}], again)
        if kind == "conflict":
            return outside("merge-conflict", "The reviewed commit %s conflicts with the branch, so the merge was aborted and %s is as it "
                           "was: merge it yourself and resolve the conflicts, or send it back for rework here." % (head[:12], m), term,
                           [merge, {"command": "git status"}, {"command": "git commit --no-edit"}],
                           "Then reload this card: it reads as merged, and Approve merge only records it.")
        why = {"detached": "HEAD in %s is detached: check out the branch to merge into, then merge the reviewed commit and record it "
                           "from a terminal." % m,
               "driver": "The repository's git config could run a program during a merge, so the dashboard does not merge here: merge "
                         "the reviewed commit yourself and record it.",
               "attributes": "The repository's git attributes could run a program during a merge, so the dashboard does not merge here: "
                             "merge the reviewed commit yourself and record it.",
               "identity": "git has no user.name and user.email in %s, so it cannot make a merge commit: set them, then merge the "
                           "reviewed commit and record it." % m,
               "no-parent": "The merge did not produce the reviewed commit as a parent: check %s, then merge it yourself and record "
                            "it." % m,
               "briefing": "This review stored no briefing, so there is nothing here to decide from: merge the reviewed commit "
                           "yourself and record it."}.get(kind, "git cannot read %s, so the dashboard cannot merge: merge the reviewed "
                                                          "commit yourself and record it." % m)
        return outside("merge-in-terminal", why, term, [merge, record], "Then reload this card.")

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
