#!/usr/bin/env python3
"""FO-004: one journey through the factory from the dashboard alone, with no human typing.

Two registered fixture projects. A is a copy of the cc_tool tree that is itself a git repository (so a merged
lane can change cc-dash's own code, as cc_tool does when it is built with the factory); B is a plain repository.
Orders are made and moved with cc-factory. Every gate decision goes through the dashboard's real HTTP API
(GET and POST /api/control/gate) and the real ticket child (bin/cc-factory with --ticket-fd), with the terminal and
process checks satisfied by this test's own controlled process tree exactly as tests/factory_gate_cases.py and
tests/gate_fixture.py do. The state directory is a fixture one; the real ~/.local/state/cc_tool is untouched
(StateGuard).

  1. spec gate approve                                (project A)
  2. run recorded to the merge gate                   (a fixture workflow result; the lane changes cc-dash's code)
  3. Approve merge                                    (the merge re-records cc-dash's code; the dashboard asks for a restart)
  4. restart                                          (a new dashboard process, as the Restart button starts one)
  5. a second order's spec approve                    (project B)
  6. re-approve after a spec edit                     (B)
  7. drop an approved order                           (B)
  8. no weakening: code edited by hand is not trusted (the box before a decision, and the child's own refusal)

Run: python3 tests/test_factory_journey.py   (exit 1 on any failed step; one line per step)

XFAIL maps a step to the order whose work it needs when that order is not merged yet: the step then prints
`XFAIL <step> (needs FO-00N)` and does not count as a failure, so nothing is silently skipped. FO-001 (the
dashboard merges) and FO-003 (the outside-step box) are merged at this test's base, so it is empty.
"""
import hashlib
import json
import os
import shutil
import sys
import time
from urllib.parse import quote

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from state_guard import StateGuard  # noqa: E402

GUARD = StateGuard()          # the real state, before anything below touches the environment
import factory_cli_cases as X  # noqa: E402  (its repositories, its CLI runner, its ledger readers)
import gate_fixture as G  # noqa: E402
import test_cc_dash_control as C  # noqa: E402  (its Srv: a cc-dash server in control mode)

XFAIL = {}      # {step number: "FO-00N"}
RESULTS = []    # (step, ok, detail)
SID_A, SID_B = "cccccccc-1111-2222-3333-444444444444", "dddddddd-1111-2222-3333-444444444444"


def step(n, what, ok, detail=""):
    if n in XFAIL and not ok:
        print("XFAIL %d. %s (needs %s)" % (n, what, XFAIL[n]))
        return
    RESULTS.append((n, ok, detail))
    print("%s %d. %s" % ("ok  " if ok else "FAIL", n, what) + ("" if ok else "\n      " + str(detail)[:600]))


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class Dash:
    """A paired, unlocked dashboard over both projects: what a person with the control code has, in one browser."""

    def __init__(self, cli, label_roots):
        self.s = C.Srv()
        self.s.pair()
        self.s.ctl._rate_ok = lambda _client: True
        now = time.time()
        for sid, root in label_roots:
            C.transcript(self.s.cfg, sid, root, [{"type": "user", "timestamp": C.iso(now - 30), "sessionId": sid, "cwd": root, "uuid": "j" + sid[:4],
                                                  "message": {"role": "user", "content": "hello"}}])
        self.s.store.refresh(force=True)
        self.labels = {os.path.realpath(p["root"]): p["project"] for p in self.s.srv.docs.projects()}
        self.s.srv.gates.cli = cli
        self.s.srv.gates.start = time.time()       # a process that started after the code and its record last changed

    def label(self, repo):
        return self.labels[os.path.realpath(repo)]

    def card(self, repo, oid):
        st, _, out = self.s.req("GET", "/api/control/gate?project=%s&order=%s" % (quote(self.label(repo)), oid))
        return st, json.loads(out)

    def decide(self, repo, oid, decision="approve", card=None, **kw):
        st, c = (200, card) if card else self.card(repo, oid)
        assert st == 200, c
        b = {"project": self.label(repo), "order": oid, "gate": c["gate"], "decision": decision, "confirm": decision, "view": c["view"]}
        b["spec_hash" if c["gate"] == "spec" else "lane_head"] = c.get("spec_hash", c.get("lane_head"))
        b.update(kw)
        return self.s.post("/api/control/gate", b)

    def close(self):
        self.s.close()


def gate_events(repo, oid):
    return [e for e in X.events(repo, oid) if e["event"] == "gate"]


def main():
    base = X.BASE
    os.environ["XDG_STATE_HOME"] = X.STATE         # the ticket child and the dashboard's code check read the fixture record
    os.environ["HOME"] = X.HOME
    cli = G.make_tree(base, "journey")
    a_repo = os.path.dirname(os.path.dirname(cli))          # project A is the tree: its code is cc-dash's code
    X.git(a_repo, "init", "-q")
    X.git(a_repo, "config", "user.name", "t")
    X.git(a_repo, "config", "user.email", "t@t")
    X.git(a_repo, "add", "-A")
    X.git(a_repo, "commit", "-q", "-m", "tree")
    r = X.cli(a_repo, "init")
    assert r.returncode == 0, r.stderr
    X.git(a_repo, "add", "-A", "--", "factory", ".gitignore")
    X.git(a_repo, "commit", "-q", "--allow-empty", "-m", "factory", rc=None)
    b_repo = os.path.realpath(X.mkrepo("journey-b"))
    X.git(b_repo, "config", "user.name", "t")
    X.git(b_repo, "config", "user.email", "t@t")
    G.plant_record(a_repo, a_repo, state=X.STATE)          # both projects are set up from A's checkout (cc-update-project)
    G.plant_record(b_repo, a_repo, state=X.STATE)
    os.makedirs(os.path.join(X.STATE, "cc_tool"), mode=0o700, exist_ok=True)
    with open(os.path.join(X.STATE, "cc_tool", "projects.json"), "w") as fh:
        json.dump({"v": 1, "projects": [a_repo, b_repo]}, fh)
    graph = os.path.join(a_repo, "lib", "cc_dash", "graph.py")
    rec = os.path.join(X.STATE, "cc_tool", "cc_dash_code.json")
    time.sleep(1.2)         # the record is older than the dashboard that starts now (code_after_start's margin)
    roots = [(SID_A, a_repo), (SID_B, b_repo)]
    dash = Dash(cli, roots)
    try:
        # 1. spec gate approve
        o1 = X.new_order(a_repo, "Journey: change the graph")
        assert X.cli(a_repo, "mark", o1, "spec", "--no-holdout").returncode == 0
        st, out = dash.decide(a_repo, o1)
        ev = gate_events(a_repo, o1)
        step(1, "spec gate approved from the dashboard (%s, via the ticket child)" % o1,
             st == 200 and X.order_of(a_repo, o1)["status"] == "approved" and len(ev) == 1 and ev[0].get("via") == "dashboard", (st, out, ev))

        # 2. a run recorded to the merge gate
        r = X.cli(a_repo, "lane", o1)
        assert r.returncode == 0, r.stderr
        wt = r.stdout.rstrip("\n").splitlines()[-1]
        # Like a real lane: cc-dash's code plus files in bin/ and templates/hooks/ that are not part of it.
        for rel in ("lib/cc_dash/graph.py", "bin/cc-factory", "templates/hooks/journey-hook.py"):
            with open(os.path.join(wt, rel), "a", encoding="utf-8") as fh:
                fh.write("\n# the journey's change\n")
        X.git(wt, "add", "-A")
        X.git(wt, "commit", "-q", "-m", "%s: change graph" % o1)
        head = X.git(wt, "rev-parse", "HEAD").stdout.strip()
        res = X.v2_result(o1, X.order_of(a_repo, o1)["lane"], ids=())
        r = X.record_saved(a_repo, o1, res, run_id="wf_journey01")
        o = X.order_of(a_repo, o1)
        step(2, "run recorded to the merge gate (status %s, reviewed head %s)" % (o["status"], head[:10]),
             r.returncode == 0 and o["status"] == "review", (r.returncode, r.stderr[-500:], r.stdout[-300:]))

        # 3. Approve merge of a lane that changes cc-dash's code
        st, card = dash.card(a_repo, o1)
        st2, out = dash.decide(a_repo, o1, card=card) if st == 200 else (st, card)
        second = X.git(a_repo, "rev-parse", "HEAD^2", rc=None).stdout.strip()
        after = json.loads(X.read(rec))["checkouts"][a_repo]["files"].get("lib/cc_dash/graph.py")
        st3, nxt = dash.card(a_repo, o1)
        step(3, "Approve merge: the lane is merged, recorded, cc-dash's code re-recorded to the merged tree, and the dashboard asks for a restart",
             st == 200 and st2 == 200 and second == head and X.order_of(a_repo, o1)["status"] == "done" and after == sha(graph)
             and out.get("restart") is True and (out.get("outside") or {}).get("kind") == "restart",
             (st, st2, out, second == head, X.order_of(a_repo, o1)["status"], after == sha(graph)))
        o1b = X.new_order(a_repo, "Journey: after the merge")
        X.cli(a_repo, "mark", o1b, "spec", "--no-holdout")
        st, c = dash.card(a_repo, o1b)
        step(3, "... and until it restarts, a card shows the one restart step and decides nothing",
             st == 200 and (c.get("code_outside") or {}).get("kind") == "restart" and dash.decide(a_repo, o1b, card=c)[0] == 409, c.get("code_outside"))

        # 4. restart: a new dashboard process (what the Restart button starts) over the same record
        dash.close()
        time.sleep(1.3)
        dash = Dash(cli, roots)
        st, c = dash.card(a_repo, o1b)
        stb, cb = dash.card(b_repo, X.new_order(b_repo, "Journey B: probe"))
        step(4, "restart: the new dashboard carries no code box in either project, with no command typed",
             st == 200 and c.get("code_outside") is None and stb in (200, 409) and cb.get("code_outside") is None, (c.get("code_outside"), cb.get("code_outside")))

        # 5. a second order's spec approve in the other project
        o2 = X.new_order(b_repo, "Journey B: add a flag")
        assert X.cli(b_repo, "mark", o2, "spec", "--no-holdout").returncode == 0
        st, out = dash.decide(b_repo, o2)
        step(5, "second project: spec gate approved from the dashboard (%s)" % o2,
             st == 200 and X.order_of(b_repo, o2)["status"] == "approved" and gate_events(b_repo, o2)[-1].get("via") == "dashboard", (st, out))

        # 6. re-approve after a spec edit
        with open(X.F.find_order(b_repo, o2), "a", encoding="utf-8") as fh:
            fh.write("\nAlso accept --verbose.\n")
        st, c = dash.card(b_repo, o2)
        kind = c.get("kind")
        st2, out = dash.decide(b_repo, o2, card=c) if st == 200 else (st, c)
        last = gate_events(b_repo, o2)[-1]
        step(6, "spec edited after approval: the card says changed, and a re-approve records the new hash",
             st == 200 and kind == "changed" and st2 == 200 and last.get("spec_hash") == c.get("spec_hash") and len(gate_events(b_repo, o2)) == 2, (st, kind, st2, out, last))

        # 7. drop an approved order
        st, c = dash.card(b_repo, o2)
        st2, out = dash.decide(b_repo, o2, "reject", card=c) if st == 200 else (st, c)
        step(7, "an approved order dropped from the dashboard",
             st == 200 and c.get("kind") == "drop" and st2 == 200 and X.order_of(b_repo, o2)["status"] == "rejected", (st, c.get("kind"), st2, out))

        # 8. no weakening: an edit made any other way is never trusted, however the dashboard is restarted
        with open(graph, "a", encoding="utf-8") as fh:
            fh.write("\n# edited by hand (or by an agent's Bash)\n")
        dash.close()
        time.sleep(1.3)
        dash = Dash(cli, roots)
        o3 = X.new_order(b_repo, "Journey B: after a hand edit")
        assert X.cli(b_repo, "mark", o3, "spec", "--no-holdout").returncode == 0
        n0 = len(X.events(b_repo))
        st, c = dash.card(b_repo, o3)
        box = c.get("code_outside") or {}
        st2, out = dash.decide(b_repo, o3, card=c)
        step(8, "edited cc-dash code, dashboard restarted: the card shows the one record-code box and a decision is refused, no ledger event",
             st == 200 and box.get("kind") == "record-code" and [x["command"] for x in box["steps"]] == ["cc-dash --record-code", "ccdash --control"]
             and st2 == 409 and len(X.events(b_repo)) == n0 and X.order_of(b_repo, o3)["status"] == "spec", (st, box, st2, out))
        dash.s.srv.gates._code_outside = lambda main="": None       # with the dashboard's early warning off, the child still refuses
        st3, out3 = dash.decide(b_repo, o3, card=c)
        step(8, "... and the real ticket child refuses too (its parent is not your dashboard), still no ledger event",
             st3 == 409 and "not your dashboard" in out3.get("error", "") and "not the code you approved" in out3.get("error", "")
             and len(X.events(b_repo)) == n0, (st3, out3))
    finally:
        dash.close()
    why = GUARD.problem() or X.STATE_GUARD.problem()
    step(9, "the real ~/.local/state/cc_tool was untouched", not why, why)
    failed = [r for r in RESULTS if not r[1]]
    print("journey: %d steps, %d failed" % (len(RESULTS), len(failed)))
    for d in (X.BASE, C.BASE):
        shutil.rmtree(d, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
