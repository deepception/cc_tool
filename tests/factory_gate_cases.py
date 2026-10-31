#!/usr/bin/env python3
"""bin/cc-factory's gates from the dashboard: approve and reject with --ticket-fd.

--ticket-fd is how cc-dash hands a human's decision to the child it starts, and the flag alone must
prove nothing. This test process plays the parent that passes the socket:
  - the real CLI: an agent-like process is refused whatever it offers (Claude Code's variables, a
    socket of its own with a made-up ticket, a pipe, a closed or low fd, a socket whose other end is
    another process, a planted lease and hook.sock it never looks at), and writes nothing
  - a copy of the tree whose cc_procs passes the parent as a dashboard (tests/gate_fixture.py), with
    the kernel's peer check, the variables, /proc, the pin and the manifest still real: a good ticket
    passes both gates and reject and rework; a forged, expired, replayed or mismatched one, a stale
    hash, a moved lane and an unmerged lane are refused; no /proc refuses; a parent that fails, or a
    cc_procs that raises or says nothing, fails closed; a missing or tampered record refuses
  - the launch: the `ccdash` line install.sh writes, typed into bash and zsh on a pseudo-terminal, starts
    a stand-in for cc-dash, and the real control_mode_problem and peer_holder_problem pass it. Without
    --control, without a terminal, or with its socket shared, they refuse. An agent may not start
    control mode, so this is the nearest thing to the real path that can run here.
  - terminal approval does not change: without the flag it still asks for the code on /dev/tty

The server's side (minting, spending, the child's argv and environment) is in tests/test_cc_dash_control.py.

Part of tests/test_factory.py (python3 tests/test_factory.py gates runs only this part;
python3 tests/factory_gate_cases.py runs it standalone).
"""
import json
import os
import re
import select
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import factory_cli_cases as X  # noqa: E402  (its repositories, its CLI runner, its ledger readers)
import gate_fixture as G  # noqa: E402

RESULTS = X.RESULTS
check = X.check
PY, CLI = X.PY, X.CLI
TICKET = "0123456789abcdef0123456789abcdef"


def cc_env(extra=None):
    """The environment cc-dash gives its child, more or less: no Claude Code, whatever this test runs under."""
    env = {k: v for k, v in X.env_for().items() if not k.startswith("CLAUDE")}
    env.update(extra or {})
    return env


def as_child(script, repo, *args, answer=None, env_extra=None, fd=None, timeout=60):
    """Run `script` as cc-dash starts it: python -I, a session of its own, a socket on --ticket-fd whose other
    end is this process. `answer(request)` gives the reply (a dict, bytes sent as they are, or None to hang
    up); r.asked lists what was asked."""
    ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    asked = []

    def serve():
        buf = b""
        try:
            while True:
                chunk = ours.recv(4096)
                if not chunk:
                    return
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    req = json.loads(line)
                    asked.append(req)
                    a = answer(req) if answer else None
                    if a is None:
                        ours.shutdown(socket.SHUT_RDWR)
                        return
                    ours.sendall(a if isinstance(a, bytes) else json.dumps(a).encode() + b"\n")
        except OSError:
            pass
    n = theirs.fileno() if fd is None else fd
    p = subprocess.Popen([PY, "-I", script, "-C", repo] + list(args) + ["--ticket-fd", str(n)], cwd=X.BASE, env=cc_env(env_extra),
                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, pass_fds=(theirs.fileno(),),
                         start_new_session=True)
    theirs.close()
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    out, err = p.communicate(timeout=timeout)
    ours.close()
    return subprocess.CompletedProcess(p.args, p.returncode, out, err), asked


def good(oid, gate, decision, value, rework=False, note="", tid=TICKET, expires=None):
    def answer(_req):
        return {"ok": True, "ticket": {"id": tid, "order": oid, "gate": gate, "decision": decision, "rework": rework, "note": note,
                                       "value": value, "expires": expires if expires is not None else time.time() + 30}}
    return answer


def hash_of(repo, oid):
    """The spec hash cc-factory would store, through the library."""
    path = X.F.find_order(repo, oid)
    raw = X.F.read_order(repo, path)
    return X.F.spec_hash(raw["body"], X.F.line_for(X.F.line_config(repo), raw.get("line")))


def at_spec(repo, title):
    oid = X.new_order(repo, title)
    r = X.cli(repo, "mark", oid, "spec", "--no-holdout")
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    return oid


def at_merge(repo, title, approve_cli):
    """An order at the merge gate: approved, a lane with one commit, the review recorded with its head."""
    oid = at_spec(repo, title)
    r, _ = as_child(approve_cli, repo, "approve", oid, "--gate", "spec", answer=good(oid, "spec", "approve", hash_of(repo, oid), tid=os.urandom(16).hex()))
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    r = X.cli(repo, "lane", oid)
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    wt = r.stdout.rstrip("\n").splitlines()[-1]
    X.git(wt, "commit", "-q", "--allow-empty", "-m", "%s: work" % oid)
    head = X.git(wt, "rev-parse", "HEAD").stdout.strip()
    X.append(repo, oid, "status", status="review", lane_head=head, briefing="what the review said")
    return oid, wt, head


def test_agent_like():
    d = X.mkrepo()
    G.plant_record(d, G.REPO)        # the real record for the real checkout: every real check gets its turn
    a = at_spec(d, "An agent wants this approved")
    n0 = len(X.events(d))
    forged = good(a, "spec", "approve", hash_of(d, a))
    for verb in ("approve", "reject"):
        r, asked = as_child(CLI, d, verb, a, "--gate", "spec", answer=forged, env_extra={"CLAUDECODE": "1"})
        check("agent-like: the real `%s --ticket-fd` with Claude Code's variables is BLOCKED, naming them, and asks no one" % verb,
              X.blocked(r) and "has no valid ticket from your dashboard" in r.stderr and "CLAUDECODE" in r.stderr and asked == [], (r.returncode, r.stderr, asked))
    r, asked = as_child(CLI, d, "approve", a, "--gate", "spec", answer=forged)
    check("agent-like: without those variables a made-up ticket from its own parent is BLOCKED: the parent is no dashboard, and it never asks",
          X.blocked(r) and "its parent is not your dashboard" in r.stderr and asked == [], (r.returncode, r.stderr, asked))
    check("agent-like: ... the refusal points at the terminal, the same advice as every other refusal",
          "Suggestion: run it from your own terminal: cc-factory approve %s --gate spec" % a in r.stderr, r.stderr)

    rp, wp = os.pipe()
    os.write(wp, json.dumps(forged(None)).encode() + b"\n")
    os.close(wp)
    env = cc_env()
    r = subprocess.run([PY, "-I", CLI, "-C", d, "approve", a, "--gate", "spec", "--ticket-fd", str(rp)], cwd=X.BASE, env=env, pass_fds=(rp,),
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    os.close(rp)
    check("agent-like: a made-up ticket in a pipe is BLOCKED (not a socket)", X.blocked(r) and "is not a socket" in r.stderr, (r.returncode, r.stderr))
    for n, label in ((47, "closed"), (0, "stdin"), (1, "stdout"), (2, "stderr"), (-1, "negative")):
        r = subprocess.run([PY, "-I", CLI, "-C", d, "approve", a, "--gate", "spec", "--ticket-fd=%d" % n], cwd=X.BASE, env=env,
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        check("agent-like: --ticket-fd on a %s fd is BLOCKED" % label, X.blocked(r), (r.returncode, r.stderr))
    r = subprocess.run([PY, "-I", CLI, "-C", d, "approve", a, "--ticket-fd", "made-up"], cwd=X.BASE, env=env, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=60)
    check("agent-like: a word for --ticket-fd is a usage error (exit 1)", r.returncode == 1, (r.returncode, r.stderr))

    # A socket whose other end is another process: what connecting to any listener, hook.sock included, gives.
    sock_path = os.path.join(X.BASE, "planted.sock")
    listener = subprocess.Popen([PY, "-c", "import socket,sys\ns=socket.socket(socket.AF_UNIX)\ns.bind(sys.argv[1])\ns.listen(1)\n"
                                 "print('up',flush=True)\nc,_=s.accept()\nc.recv(10)\n", sock_path], stdout=subprocess.PIPE, text=True)
    listener.stdout.readline()
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.connect(sock_path)
    r = subprocess.run([PY, "-I", CLI, "-C", d, "approve", a, "--gate", "spec", "--ticket-fd", str(c.fileno())], cwd=X.BASE, env=env, pass_fds=(c.fileno(),),
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    c.close()
    listener.kill()
    listener.wait()
    check("agent-like: a socket connected to another process is BLOCKED: its other end is not this process's parent",
          X.blocked(r) and "is not the process that started it" in r.stderr, (r.returncode, r.stderr))

    # A planted lease and hook.sock in the state directory: the CLI reads neither.
    ctl = os.path.join(X.STATE, "cc_tool", "control")
    os.makedirs(ctl, mode=0o700, exist_ok=True)
    hook = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    hook.bind(os.path.join(ctl, "hook.sock"))
    hook.listen(1)
    hook.setblocking(False)
    fd = os.open(os.path.join(ctl, "lease"), os.O_WRONLY | os.O_CREAT, 0o600)
    os.write(fd, json.dumps({"pid": os.getpid(), "key": "k" * 32, "sock": os.path.join(ctl, "hook.sock"), "expires": time.time() + 600}).encode())
    os.close(fd)
    r, asked = as_child(CLI, d, "approve", a, "--gate", "spec", answer=forged)
    try:
        hook.accept()
        touched = True
    except BlockingIOError:
        touched = False
    hook.close()
    check("agent-like: a planted lease and hook.sock change nothing: still BLOCKED, and the socket was never connected to",
          X.blocked(r) and not touched and asked == [], (r.returncode, r.stderr, touched))
    check("agent-like: none of those wrote anything, and the order still waits at its gate", len(X.events(d)) == n0 and X.order_of(d, a)["status"] == "spec",
          (len(X.events(d)), n0))

    r = subprocess.run([PY, CLI, "approve", "--help"], env=env, capture_output=True, text=True, timeout=60)
    check("agent-like: --help does not offer --ticket-fd", r.returncode == 0 and "ticket" not in r.stdout, r.stdout[-400:])
    src = X.read(CLI)
    check("source: the CLI reads no lease, no state directory and no socket path", "XDG_STATE_HOME" not in src and "hook.sock" not in src and ".connect(" not in src)
    check("source: the only --ticket-fd consumer goes through the kernel's peer check", "procs.peer_cred(sock)" in src and "os.getppid()" in src)


def test_good_ticket():
    d = X.mkrepo()
    G.plant_record(d, os.path.dirname(os.path.dirname(GATE_CLI)))
    a = at_spec(d, "Passed from the dashboard")
    h = hash_of(d, a)
    r, asked = as_child(GATE_CLI, d, "approve", a, "--gate", "spec", answer=good(a, "spec", "approve", h, note="scope is right"))
    e = X.events(d, a)[-1]
    check("ticket: a good ticket passes the spec gate with no terminal and no code", r.returncode == 0 and X.order_of(d, a)["status"] == "approved"
          and "spec gate passed: approved" in r.stdout, (r.returncode, r.stdout, r.stderr))
    check("ticket: the child asked for exactly its decision", len(asked) == 1 and {k: asked[0].get(k) for k in ("op", "order", "gate", "decision", "rework")}
          == {"op": "redeem", "order": a, "gate": "spec", "decision": "approve", "rework": False}, asked)
    check("ticket: the gate event says where it came from and which ticket, with the hash and the note from the ticket",
          e.get("event") == "gate" and e.get("via") == "dashboard" and e.get("ticket") == TICKET and e.get("spec_hash") == h and e.get("note") == "scope is right", e)
    r = X.cli(d, "show", a)
    check("ticket: show names the dashboard", "via=dashboard" in r.stdout, r.stdout[-400:])
    b = at_spec(d, "Rejected from the dashboard")
    r, _ = as_child(GATE_CLI, d, "reject", b, "--gate", "spec", answer=good(b, "spec", "reject", hash_of(d, b), note="too broad, password=hunter2"))
    e = X.events(d, b)[-1]
    check("ticket: reject at the spec gate, the note redacted as any note", r.returncode == 0 and X.order_of(d, b)["status"] == "rejected"
          and e.get("via") == "dashboard" and "hunter2" not in e.get("note", "x"), (r.returncode, r.stderr, e))

    m, wt, head = at_merge(d, "Merge from the dashboard", GATE_CLI)
    r, _ = as_child(GATE_CLI, d, "approve", m, "--gate", "merge", answer=good(m, "merge", "approve", head, tid=os.urandom(16).hex()))
    check("ticket: approve before the merge is BLOCKED with the exact git merge --no-ff command, nothing merged, order still in review",
          X.blocked(r) and ("git merge --no-ff %s" % head) in r.stderr and X.order_of(d, m)["status"] == "review", (r.returncode, r.stderr))
    X.git(d, "merge", "-q", "--no-ff", "-m", "merge the lane", head)
    r, _ = as_child(GATE_CLI, d, "approve", m, "--gate", "merge", answer=good(m, "merge", "approve", head, tid=os.urandom(16).hex()))
    e = X.events(d, m)[-1]
    check("ticket: after the human's merge, approve records it: done, with the merge sha and the ticket",
          r.returncode == 0 and X.order_of(d, m)["status"] == "done" and e.get("merge_sha") and e.get("lane_head") == head and e.get("via") == "dashboard",
          (r.returncode, r.stderr, e))
    n = len(X.events(d))
    m2, _wt2, head2 = at_merge(d, "Sent back from the dashboard", GATE_CLI)
    r, _ = as_child(GATE_CLI, d, "reject", m2, "--gate", "merge", "--rework", answer=good(m2, "merge", "reject", head2, rework=True, tid=os.urandom(16).hex()))
    check("ticket: reject --rework at the merge gate goes back to approved", r.returncode == 0 and X.order_of(d, m2)["status"] == "approved", (r.returncode, r.stderr))
    m3, _wt3, head3 = at_merge(d, "Dropped from the dashboard", GATE_CLI)
    r, _ = as_child(GATE_CLI, d, "reject", m3, "--gate", "merge", answer=good(m3, "merge", "reject", head3, tid=os.urandom(16).hex()))
    check("ticket: reject at the merge gate drops it", r.returncode == 0 and X.order_of(d, m3)["status"] == "rejected" and len(X.events(d)) > n, (r.returncode, r.stderr))


def test_bad_tickets():
    d = X.mkrepo()
    G.plant_record(d, os.path.dirname(os.path.dirname(GATE_CLI)))
    a = at_spec(d, "Forged tickets")
    other = at_spec(d, "Another order")
    h = hash_of(d, a)
    n0 = len(X.events(d))

    def changed(**kw):
        def answer(req):
            t = good(a, "spec", "approve", h)(req)
            t["ticket"].update(kw)
            return t
        return answer
    cases = (("for another order", changed(order=other), "another decision"), ("for the other gate", changed(gate="merge"), "another decision"),
             ("for the other decision", changed(decision="reject"), "another decision"), ("with rework set", changed(rework=True), "another decision"),
             ("with a stale spec hash", changed(value="0" * 64), "no longer what is there"), ("expired", changed(expires=time.time() - 1), "expired"),
             ("with an expiry that is not a number", changed(expires="soon"), "expired"), ("with a malformed id", changed(id="../../etc"), "not a ticket"),
             ("with no note", changed(note=None), "not a complete ticket"),
             ("that is not one", lambda _r: {"ok": True, "ticket": "yes"}, "not a ticket"), ("refused by the dashboard", lambda _r: {"ok": False, "error": "this ticket was already used"}, "already used"),
             ("never given (the other end hung up)", None, "gave no ticket"), ("in broken JSON", lambda _r: b"{not json\n", "gave no ticket"),
             ("that never ends", lambda _r: b"x" * 200000, "gave no ticket"))
    for label, answer, needle in cases:
        r, _ = as_child(GATE_CLI, d, "approve", a, "--gate", "spec", answer=answer)
        check("forged: a ticket %s is BLOCKED" % label, X.blocked(r) and needle in r.stderr, (label, r.returncode, r.stderr))
    check("forged: none of those wrote anything", len(X.events(d)) == n0 and X.order_of(d, a)["status"] == "spec", (len(X.events(d)), n0))

    r, _ = as_child(GATE_CLI, d, "approve", a, "--gate", "spec", "--into", "HEAD", answer=good(a, "spec", "approve", h))
    check("forged: --into beside a ticket is a usage error", r.returncode == 1, (r.returncode, r.stderr))
    r, _ = as_child(GATE_CLI, d, "approve", a, "--gate", "spec", answer=good(a, "spec", "approve", h), env_extra={"CLAUDECODE": "1"})
    check("forged: Claude Code's variables refuse even a parent that passes as a dashboard", X.blocked(r) and "CLAUDECODE" in r.stderr, (r.returncode, r.stderr))

    # Replay: a ticket the ledger has already seen, offered again for an order back at the gate.
    r, _ = as_child(GATE_CLI, d, "approve", a, "--gate", "spec", answer=good(a, "spec", "approve", h))
    check("replay: the first use passes", r.returncode == 0, (r.returncode, r.stderr))
    X.cli(d, "mark", a, "spec", "--no-holdout")
    r, _ = as_child(GATE_CLI, d, "approve", a, "--gate", "spec", answer=good(a, "spec", "approve", h))
    check("replay: the same ticket again is BLOCKED, by the ledger, even with the order back at its gate", X.blocked(r) and "already used" in r.stderr, (r.returncode, r.stderr))

    # What the human saw moved between the server's check and the write.
    b = at_spec(d, "Edited after the human saw it")
    h2 = hash_of(d, b)
    path = X.F.find_order(d, b)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\nAlso delete the tests.\n")
    r, _ = as_child(GATE_CLI, d, "approve", b, "--gate", "spec", answer=good(b, "spec", "approve", h2))
    check("stale: a spec edited after the human saw it is BLOCKED by the CLI too", X.blocked(r) and "no longer what is there" in r.stderr
          and X.order_of(d, b)["status"] == "spec", (r.returncode, r.stderr))
    r, _ = as_child(GATE_CLI, d, "reject", b, "--gate", "spec", answer=good(b, "spec", "reject", h2))
    check("stale: ... and a rejection of what is no longer there", X.blocked(r) and "no longer what is there" in r.stderr, (r.returncode, r.stderr))
    m, wt, head = at_merge(d, "A lane that moves", GATE_CLI)
    X.git(wt, "commit", "-q", "--allow-empty", "-m", "%s: one more" % m)
    r, _ = as_child(GATE_CLI, d, "approve", m, "--gate", "merge", answer=good(m, "merge", "approve", head, tid=os.urandom(16).hex()))
    check("stale: a lane moved after the review is BLOCKED", X.blocked(r) and "moved after the review" in r.stderr and X.order_of(d, m)["status"] == "review", (r.returncode, r.stderr))


def test_fail_closed():
    d = X.mkrepo()
    tree = os.path.dirname(os.path.dirname(GATE_CLI))
    G.plant_record(d, tree)
    a = at_spec(d, "Fail closed")
    h = hash_of(d, a)
    n0 = len(X.events(d))
    ticket = good(a, "spec", "approve", h)
    for label, extra, needle in (("says no", 'def dashboard_problem(*a, **k):\n    return "it is a test double that says no"\n', "says no"),
                                 ("answers None", 'def dashboard_problem(*a, **k):\n    return None\n', "gave no answer"),
                                 ("raises", 'def dashboard_problem(*a, **k):\n    raise OSError("cannot look")\n', "could not be read")):
        cli = G.make_tree(X.BASE, "dash-" + label.split()[0], G.PASS_AS_DASHBOARD + "\n\n" + extra)   # code_after_start says '': only this rule is left
        G.plant_record(d, os.path.dirname(os.path.dirname(cli)))
        r, asked = as_child(cli, d, "approve", a, "--gate", "spec", answer=ticket)
        check("fail closed: a dashboard check that %s refuses, and no ticket is asked for" % label, X.blocked(r) and needle in r.stderr and asked == [], (r.returncode, r.stderr))
    for rule in ("peer_holder_problem", "control_mode_problem"):
        cli = G.make_tree(X.BASE, "dash-" + rule, G.PASS_AS_DASHBOARD + '\n\ndef %s(*a, **k):\n    return None\n' % rule)
        G.plant_record(d, os.path.dirname(os.path.dirname(cli)))
        r, asked = as_child(cli, d, "approve", a, "--gate", "spec", answer=ticket)
        check("fail closed: %s answering None refuses, and no ticket is asked for" % rule, X.blocked(r) and "gave no answer" in r.stderr and asked == [], (r.returncode, r.stderr))
    noproc = G.make_tree(X.BASE, "gate-noproc", G.PASS_AS_DASHBOARD + '\n\ndef have_proc():\n    return False\n')
    G.plant_record(d, os.path.dirname(os.path.dirname(noproc)))
    r, asked = as_child(noproc, d, "approve", a, "--gate", "spec", answer=ticket)
    check("no /proc: the dashboard path refuses like the terminal path, before any ticket", X.blocked(r) and "has no /proc" in r.stderr and "Linux only" in r.stderr and asked == [], (r.returncode, r.stderr))
    broken = G.make_tree(X.BASE, "gate-broken", "\nraise RuntimeError('broken on purpose')\n")
    r, _ = as_child(broken, d, "approve", a, "--gate", "spec", answer=ticket)
    check("fail closed: a cc_procs that cannot be loaded refuses", X.blocked(r) and "could not be loaded" in r.stderr, (r.returncode, r.stderr))

    G.plant_record(d, tree)
    man = os.path.join(d, ".claude", "hooks", "cc_dash_manifest.json")
    saved = X.read(man)
    os.unlink(man)
    r, _ = as_child(GATE_CLI, d, "approve", a, "--gate", "spec", answer=ticket)
    check("record: no manifest beside the hooks refuses", X.blocked(r) and "cc_dash_manifest.json" in r.stderr, (r.returncode, r.stderr))
    os.symlink(os.path.join(X.BASE, "nowhere.json"), man)
    r, _ = as_child(GATE_CLI, d, "approve", a, "--gate", "spec", answer=ticket)
    check("record: a symlinked manifest refuses", X.blocked(r) and "cc_dash_manifest.json" in r.stderr, (r.returncode, r.stderr))
    os.unlink(man)
    X.write(man, saved)
    os.chmod(man, 0o666)
    r, _ = as_child(GATE_CLI, d, "approve", a, "--gate", "spec", answer=ticket)
    check("record: a manifest others can write refuses", X.blocked(r) and "cc_dash_manifest.json" in r.stderr, (r.returncode, r.stderr))
    os.chmod(man, 0o644)
    pin = os.path.join(d, ".claude", "hooks", "cc_tool_root")
    os.chmod(pin, 0o644)
    X.write(pin, "/somewhere/else\n")
    r, _ = as_child(GATE_CLI, d, "approve", a, "--gate", "spec", answer=ticket)
    check("record: a project pinned to another checkout refuses", X.blocked(r) and "not set up from the cc_tool checkout" in r.stderr, (r.returncode, r.stderr))
    G.plant_record(d, tree)
    code = os.path.join(tree, "lib", "cc_dash", "gates.py")
    with open(code, "a", encoding="utf-8") as fh:
        fh.write("\n# changed after cc-setup ran\n")
    r, _ = as_child(GATE_CLI, d, "approve", a, "--gate", "spec", answer=ticket)
    check("record: cc-dash's code changed since the record was made refuses", X.blocked(r) and "changed since cc-setup ran" in r.stderr, (r.returncode, r.stderr))
    check("fail closed: none of those wrote anything", len(X.events(d)) == n0 and X.order_of(d, a)["status"] == "spec", (len(X.events(d)), n0))


# Stands in for cc-dash, and is no cc-dash: no server, no lease, no control code, nothing a hook would believe. It starts
# as bin/cc-dash does (the re-exec, then own_process_group and --ready-fd in server.py) and starts cc-factory as gates.py
# does (_run), so the rules that read a process judge what the real launch leaves behind.
STAND_IN = r"""#!/usr/bin/env python3
import json, os, socket, subprocess, sys

exe = os.path.realpath("/proc/self/exe")
if not sys.flags.isolated:
    os.execve(exe, [exe, "-I", os.path.realpath(__file__)] + sys.argv[1:], dict(os.environ))
with open(os.environ["STAND_IN"]) as fh:
    spec = json.load(fh)
if "--ready-fd" in sys.argv:
    if not any(os.isatty(fd) for fd in (0, 1, 2)) and os.getpgrp() != os.getpid():
        os.setpgid(0, 0)
    os.close(int(sys.argv[sys.argv.index("--ready-fd") + 1]))
ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
sharer = subprocess.Popen(["sleep", "60"], pass_fds=(ours.fileno(),)) if spec["share"] else None
p = subprocess.Popen([exe, "-I", spec["cli"], "-C", spec["repo"]] + spec["args"] + ["--ticket-fd", str(theirs.fileno())], cwd=spec["repo"],
                     env=spec["env"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                     pass_fds=(theirs.fileno(),), start_new_session=True)
theirs.close()
with ours.makefile("rb") as f:
    asked = f.readline()
if asked:
    ours.sendall(json.dumps(spec["answer"]).encode() + b"\n")
_out, err = p.communicate(timeout=60)
ours.close()
if sharer:
    sharer.kill()
    sharer.wait()
with open("/proc/self/stat") as fh:
    st = fh.read().rpartition(")")[2].split()
with open(spec["report"] + ".new", "w") as fh:
    json.dump({"rc": p.returncode, "err": err, "asked": bool(asked), "pid": os.getpid(), "pgrp": int(st[2]), "sid": int(st[3]), "tty": int(st[4])}, fh)
os.rename(spec["report"] + ".new", spec["report"])
"""
SHELLS = {"bash": ["bash", "--norc", "--noprofile", "-i"], "zsh": ["zsh", "-f", "-i"]}


def ccdash_line():
    """The `ccdash` function, word for word as install.sh writes it into your shell's profile."""
    return re.search(r"^CCDASH_LINE='(.*)'$", X.read(os.path.join(X.REPO, "install.sh")), re.M).group(1)


def plant_stand_in(name, repo, cli, args, answer, share=False):
    """(script, environment, report path) of a stand-in named cc-dash, first on the PATH of that environment."""
    d = os.path.join(X.BASE, "stand-in-" + name)
    script, spec, report = os.path.join(d, "bin", "cc-dash"), os.path.join(d, "spec.json"), os.path.join(d, "report.json")
    X.write(script, STAND_IN)
    os.chmod(script, 0o755)
    X.write(spec, json.dumps({"cli": cli, "repo": repo, "args": args, "answer": answer, "share": share, "report": report, "env": cc_env()}))
    return script, cc_env({"PATH": os.path.dirname(script) + os.pathsep + os.environ["PATH"], "STAND_IN": spec, "TERM": "dumb"}), report


def on_terminal(shell, typed, env, report):
    """`typed` at the prompt of an interactive shell on a pseudo-terminal of its own, as a terminal program runs your
    shell: it leads a session with that terminal as its controlling one, and stays at its prompt until the report
    is there. The stand-in's report, or {} with what the terminal showed when none came."""
    pid, master = os.forkpty()
    if pid == 0:
        os.execvpe(SHELLS[shell][0], SHELLS[shell], env)
    os.write(master, typed.encode())
    seen, end = b"", time.time() + 60
    while time.time() < end and not os.path.exists(report):
        if select.select([master], [], [], 0.05)[0]:
            try:
                seen += os.read(master, 4096)
            except OSError:
                break
    os.close(master)
    os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
    return json.loads(X.read(report)) if os.path.exists(report) else {"seen": seen[-600:]}


def test_real_launch():
    d = X.mkrepo()
    cli = G.make_tree(X.BASE, "gate-launch", G.PASS_AS_CC_DASH)     # the parent's identity is taken as read; control mode and the socket are not
    G.plant_record(d, os.path.dirname(os.path.dirname(cli)))
    for shell in [s for s in SHELLS if shutil.which(s)]:
        a = at_spec(d, "Approved from %s" % shell)
        _script, env, report = plant_stand_in(shell, d, cli, ["approve", a, "--gate", "spec"], good(a, "spec", "approve", hash_of(d, a), tid=os.urandom(16).hex())(None))
        r = on_terminal(shell, ccdash_line() + "\nccdash --control\n", env, report)
        check("launch: `ccdash --control` in %s starts a parent the real control-mode and socket rules pass: approved" % shell,
              r.get("rc") == 0 and X.order_of(d, a)["status"] == "approved", r)
        check("launch: in %s it keeps the shell's terminal and session, in a process group of its own" % shell,
              r.get("tty", 0) != 0 and r.get("sid") != r.get("pid") and r.get("pgrp") == r.get("pid"), r)
    a = at_spec(d, "Refused below")
    args, answer = ["approve", a, "--gate", "spec"], good(a, "spec", "approve", hash_of(d, a))(None)
    n0 = len(X.events(d))
    _script, env, report = plant_stand_in("no-flag", d, cli, args, answer)
    r = on_terminal("bash", ccdash_line() + "\nccdash --control-wait 300\n", env, report)
    check("control mode: a parent on a terminal without --control is refused, and no ticket is asked for",
          r.get("rc") == 2 and "was not started with --control" in r.get("err", "") and r.get("tty", 0) != 0 and r.get("asked") is False, r)
    script, env, report = plant_stand_in("no-terminal", d, cli, args, answer)
    subprocess.run([script, "--control"], env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=90, start_new_session=True)
    r = json.loads(X.read(report))
    check("control mode: a parent with --control and no controlling terminal is refused, and no ticket is asked for",
          r.get("rc") == 2 and "no controlling terminal" in r.get("err", "") and r.get("tty") == 0 and r.get("asked") is False, r)
    _script, env, report = plant_stand_in("shared", d, cli, args, answer, share=True)
    r = on_terminal("bash", ccdash_line() + "\nccdash --control\n", env, report)
    check("socket: a parent that shares its end of the socket with another process is refused, and no ticket is asked for",
          r.get("rc") == 2 and "is not its parent's alone" in r.get("err", "") and r.get("tty", 0) != 0 and r.get("asked") is False, r)
    check("control mode: none of those wrote anything", len(X.events(d)) == n0 and X.order_of(d, a)["status"] == "spec", (len(X.events(d)), n0))


def test_terminal_unchanged():
    d = X.mkrepo()
    a = at_spec(d, "Still a terminal away")
    r = X.cli(d, "approve", a, typed="WRONG2")
    check("terminal: without --ticket-fd approve still asks for the code on the terminal, and a wrong one refuses",
          X.blocked(r) and "is not the code it showed there" in r.stderr, (r.returncode, r.stderr))
    r = X.cli(d, "approve", a)
    e = X.events(d, a)[-1]
    check("terminal: the shown code typed back approves, and the event says nothing about a dashboard",
          r.returncode == 0 and e.get("event") == "gate" and "via" not in e and "ticket" not in e, (r.returncode, r.stderr, e))
    r = X.cli(d, "reject", a, "--gate", "spec", script=CLI, env_extra={"CLAUDECODE": "1"})
    check("terminal: the real CLI under Claude Code still refuses, in the same words", X.blocked(r)
          and "this cc-factory was not started from your own terminal" in r.stderr, (r.returncode, r.stderr))


GATE_CLI = None


def main():
    global GATE_CLI
    try:
        X.make_trees()
        GATE_CLI = G.make_tree(X.BASE)
        for fn in (test_agent_like, test_good_ticket, test_bad_tickets, test_fail_closed, test_real_launch, test_terminal_unchanged):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                check(fn.__name__ + " raised", False, "%s: %s" % (type(e).__name__, e))
    finally:
        shutil.rmtree(X.BASE, ignore_errors=True)
    fails = [r for r in RESULTS if not r[1]]
    print("total=%d  pass=%d  fail=%d" % (len(RESULTS), len(RESULTS) - len(fails), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
