#!/usr/bin/env python3
"""bin/cc-factory: the software factory's command line (orders, gates, lanes, ledger).

Every scenario runs the CLI as a subprocess in a throwaway git repository (temp HOME and
XDG_STATE_HOME, GIT_* variables removed) and checks:
  - init: scaffold files, the two .gitignore lines added once, the ledger directory at 0700,
    factory-line.js copied / refreshed / left alone (a fake cc_tool with its own git history),
    --force, a non-git directory, a symlinked factory/
  - new: FO-001, FO-002 ..., the file parses back through the library for awkward titles
    (colons, #, YAML indicators, {{id}} in the title expanded exactly once), the created event,
    a custom order template, unknown lines, numbering that never reuses an id, parallel `new`
  - list / status / show: the table, done hidden without --all, --json, short ids, latency
  - mark: statuses, station start / pass, note-only events, notes clipped to 500 and redacted,
    a bad status refused (exit 2, BLOCKED), usage errors (exit 1); review refused (only record of
    a passing run writes it); spec refused with fewer than 3 scenarios unless --no-holdout
  - approve / reject: gate inference, the folded status and gate after each, refusals
  - human gates: the real CLI refuses approve / reject (both gates, --rework) with CLAUDECODE=1;
    a cc_procs stub that gives a reason refuses with it; a missing, raising or silent cc_procs
    fails closed; without /proc only Claude Code's variables and a terminal on stdin count. The
    allowed paths run a copy of the CLI tree whose cc_procs says "a human" (make_tree): every
    approve / reject here goes through it, or the test writes the state into the ledger directly
  - spec hash: approve records sha256(body, line, threshold); run-args and record refuse after
    a change; re-approval; an approval without a hash; an automatic spec gate
  - merge gate: refused until the lane head is in the target; merge_sha for --no-ff, a
    fast-forward and --into REF; empty, missing and absent lanes
  - lane: refused before the spec gate; a branch and a worktree after it; idempotent; the same
    ledger seen from the worktree; --base; an existing branch; GIT_DIR in the environment ignored
  - holdout: 0700 directories, the path printed last, refused when not git-ignored
  - run-args: the JSON shape, refused without a lane, a tampered lane refused
  - record: contract-2 results in a real lane: review only when every station passed the final
    attempt and verify's report matches the scenario files on disk (19 failing checks, each
    recorded as blocked with exit 2); vacuous or no verify -> needs-scenarios; infra -> cause
    infra, no rework, "rerun"; the briefing from git (log, diffstat, capped), the S-ids and
    review's unclipped decision text, stored for show; the feedback leak flag; old results never
    open the merge gate; error / failed / blocked mappings; bad input
  - --workflow-run: the saved run under CLAUDE_CONFIG_DIR (or ~/.claude), found under the main
    checkout's project directory or by a bounded scan; symlinked, loose, unfinished, other
    workflow, other order, other lane, oversize, ambiguous and missing files refused
  - safety: a planted symlink, a swapped directory, a hard link or a FIFO at the ledger path
    -> exit 2 with the target untouched; permissions tightened; a hostile title runs nothing;
    order text never reaches the ledger; static checks of the source

Part of tests/test_factory.py (python3 tests/test_factory.py cli runs only this part;
python3 tests/factory_cli_cases.py runs it standalone).
"""
import fcntl
import json
import os
import re
import select
import shutil
import stat
import subprocess
import sys
import tempfile
import termios
import threading
import time

sys.dont_write_bytecode = True
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The kernel's interpreter: with zsh's exported ARGV0, sys.executable can name an editor's AppImage.
PY = os.path.realpath("/proc/self/exe") if os.path.exists("/proc/self/exe") else sys.executable
CLI = os.path.join(REPO, "bin", "cc-factory")
LIB = os.path.join(REPO, "lib", "cc_dash", "factory.py")
RESULTS = []
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from state_guard import StateGuard  # noqa: E402  (tests/state_guard.py)
STATE_GUARD = StateGuard()  # the real projects.json, before anything here runs
BASE = os.path.realpath(tempfile.mkdtemp(prefix="cc-factory-cli-"))
HOME = os.path.join(BASE, "home")
STATE = os.path.join(BASE, "state")
os.makedirs(HOME)
os.makedirs(STATE)
_PYC_DIR = os.path.join(REPO, "lib", "cc_dash", "__pycache__")
PYC_BEFORE = sorted(f for f in (os.listdir(_PYC_DIR) if os.path.isdir(_PYC_DIR) else []) if f.startswith("factory."))

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("cc_factory_lib", LIB)
F = importlib.util.module_from_spec(_spec)
sys.modules["cc_factory_lib"] = F
_spec.loader.exec_module(F)

_counter = [0]
EM_DASH = chr(0x2014)  # spelled by code point: this file must not contain the character itself


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    if not cond:
        print("FAIL %s  %s" % (name, str(detail)[:400]))


def env_for(extra=None):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("GIT_", "PYTHON", "CLAUDE_")) and k not in ("XDG_CONFIG_HOME",)}
    env["HOME"] = HOME
    env["XDG_STATE_HOME"] = STATE
    env["XDG_CONFIG_HOME"] = os.path.join(HOME, ".config")
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.update(extra or {})
    return env


def git(cwd, *args, rc=0):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"] + list(args),
                       cwd=cwd, env=env_for(), capture_output=True, text=True, timeout=60)
    if rc is not None and r.returncode != rc:
        raise RuntimeError("git %s failed (%d): %s" % (" ".join(args), r.returncode, r.stderr))
    return r


def mkrepo(name="repo", init=True):
    """A fresh git repository with one commit (and, with init, `cc-factory init` already run)."""
    _counter[0] += 1
    d = os.path.join(BASE, "%s-%d" % (name, _counter[0]))
    os.makedirs(d)
    git(d, "init", "-q")
    git(d, "commit", "-q", "--allow-empty", "-m", "initial")
    if init:
        r = cli(d, "init")
        if r.returncode != 0:
            raise RuntimeError("init failed: %s %s" % (r.stdout, r.stderr))
    return d


PROCS = os.path.join(REPO, "templates", "hooks", "cc_procs.py")


def make_tree(name, procs_src):
    """A copy of the CLI tree whose templates/hooks/cc_procs.py is `procs_src` (None: no cc_procs at all):
    bin/cc-factory copied, lib/, templates/factory/ and .claude/workflows/ linked to this checkout.
    approve and reject refuse under Claude Code (these tests run under it), and nothing in the real
    CLI turns that off, so the allowed paths run through a tree like this one."""
    d = os.path.join(BASE, "tree-" + name)
    os.makedirs(os.path.join(d, "bin"))
    os.makedirs(os.path.join(d, "templates", "hooks"))
    os.makedirs(os.path.join(d, ".claude"))
    shutil.copy(CLI, os.path.join(d, "bin", "cc-factory"))
    os.symlink(os.path.join(REPO, "lib"), os.path.join(d, "lib"))
    os.symlink(os.path.join(REPO, "templates", "factory"), os.path.join(d, "templates", "factory"))
    os.symlink(os.path.join(REPO, ".claude", "workflows"), os.path.join(d, ".claude", "workflows"))
    if procs_src is not None:
        write(os.path.join(d, "templates", "hooks", "cc_procs.py"), procs_src)
    return os.path.join(d, "bin", "cc-factory")


STUB_HUMAN = "def have_proc():\n    return True\n\n\ndef recording_problem():\n    return ''\n\n\ndef claude_env(env):\n    return ''\n"
# Made in main(): the tree whose cc_procs says "a human, in their own terminal", and trees whose cc_procs gives
# a reason, raises on import, is missing, answers None, raises in the check, or is the real one on a system without /proc.
HUMAN_CLI = REASON_CLI = BROKEN_CLI = MISSING_CLI = NONE_CLI = THROWS_CLI = NOPROC_CLI = None


def cli(repo, *args, stdin=None, env_extra=None, cwd=None, timeout=60, script=None, typed=None, tty=True):
    """Run the CLI. approve and reject run as the human would: through the stub tree (the real CLI refuses
    here, see test_human_gates), in a session of their own, on a fresh pseudo-terminal that is their
    controlling terminal, where `typed` (default: the order id) is typed once the confirmation question
    appears. tty=False gives them no terminal at all."""
    gate = bool(args) and args[0] in ("approve", "reject")
    if script is None and gate:
        script = HUMAN_CLI
    cmd = [PY, script or CLI] + (["-C", repo] if repo else []) + list(args)
    # cwd defaults to BASE (not a repository), so -C is what selects the project.
    if not gate:
        return subprocess.run(cmd, cwd=cwd or BASE, env=env_for(env_extra), input=stdin,
                              capture_output=True, text=True, timeout=timeout)
    return on_tty(cmd, cwd or BASE, env_for(env_extra), stdin, timeout, CODE if typed is None else typed, tty)


CODE = object()   # on_tty's `typed`: read the code the CLI shows on the terminal, and type that back
CODE_RE = re.compile(rb"Type the code ([A-Z0-9]+) and press Enter to confirm")


def on_tty(cmd, cwd, env, stdin, timeout, typed, tty, stdin_tty=False, ahead=None):
    """cmd in a new session: with tty, its controlling terminal is a new pty whose master this side holds,
    and `typed` + Enter goes in once the CLI has asked (after its flush of typed-ahead input). `ahead` is
    written to the terminal at once, before the question. stdin_tty: stdin is that pty too (else a pipe).
    The terminal output is returned as .tty on the CompletedProcess."""
    if not tty:
        r = subprocess.run(cmd, cwd=cwd, env=env, input=stdin, capture_output=True, text=True, timeout=timeout, start_new_session=True)
        r.tty = ""
        return r
    master, slave = os.openpty()

    def ctty():
        fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
    p = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=slave if stdin_tty else subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, start_new_session=True, pass_fds=(slave,), preexec_fn=ctty)
    os.close(slave)
    if ahead is not None:
        os.write(master, ahead.encode())
    got = {}
    t = threading.Thread(target=lambda: got.update(zip(("out", "err"), p.communicate(input=None if stdin_tty else (stdin or ""), timeout=timeout))))
    t.start()
    seen, sent, deadline = b"", False, time.time() + timeout
    while time.time() < deadline:
        ready, _, _ = select.select([master], [], [], 0.05)
        if ready:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            seen += chunk
            if not sent and typed is not None and b"to confirm" in seen:
                m = CODE_RE.search(seen)
                answer = (m.group(1).decode() if m else "") if typed is CODE else typed
                os.write(master, (answer + "\n").encode())
                sent = True
        elif not t.is_alive():
            break
    t.join(timeout)
    os.close(master)
    r = subprocess.CompletedProcess(cmd, p.returncode, got.get("out", ""), got.get("err", ""))
    r.tty = seen.decode("utf-8", "replace")
    return r


def record_saved(repo, oid, res, run_id=None, env_extra=None):
    """Record `res` the way /factory does, the only way a run reaches the merge gate: saved as Claude Code's
    run file (args from cc-factory run-args) under a private CLAUDE_CONFIG_DIR, read with --workflow-run."""
    _counter[0] += 1
    run_id = run_id or "wf_saved-%05d" % _counter[0]
    cfg = os.path.join(BASE, "claude-cfg-saved")
    ra = cli(repo, "run-args", oid)
    if ra.returncode != 0:
        raise RuntimeError("run-args failed: %s" % ra.stderr)
    p = os.path.join(cfg, "projects", re.sub(r"[^A-Za-z0-9]", "-", repo), "00000000-0000-4000-8000-000000000000", "workflows", run_id + ".json")
    write(p, json.dumps({"runId": run_id, "workflowName": "factory-line", "status": "completed", "args": json.loads(ra.stdout)["args"],
                         "result": res, "agentCount": 5, "logs": [], "durationMs": 1}))
    os.chmod(p, 0o600)
    return cli(repo, "record", oid, "--workflow-run", run_id, env_extra=dict(env_extra or {}, CLAUDE_CONFIG_DIR=cfg))


def append(repo, oid, event, **kw):
    """One ledger line written directly (a test that only needs the state, not the command that makes it)."""
    rec = dict({"t": time.time(), "order": oid, "event": event, "by": "cc-factory"}, **kw)
    with open(ledger_file(repo), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def force_review(repo, oid):
    """Status review straight into the ledger: mark refuses it, and record writes it only for a passing run.
    Like record, it names the reviewed lane head (the lane branch's tip now) when the order has a lane."""
    lane = next((e for e in reversed(events(repo, oid)) if e.get("event") == "lane" and e.get("branch")), None)
    head = git(repo, "rev-parse", "refs/heads/" + lane["branch"], rc=None).stdout.strip() if lane else ""
    append(repo, oid, "status", status="review", **({"lane_head": head} if re.fullmatch(r"[0-9a-f]{40,64}", head) else {}))


def blocked(r):
    return r.returncode == 2 and r.stderr.startswith("BLOCKED: ") and " Suggestion: " in r.stderr and r.stderr.rstrip().endswith(".")


def ledger_file(repo):
    return os.path.join(repo, ".git", "cc_tool", "factory", "ledger.jsonl")


def events(repo, order=None):
    out = []
    try:
        with open(ledger_file(repo), encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    e = json.loads(line)
                    if order is None or e.get("order") == order:
                        out.append(e)
    except FileNotFoundError:
        pass
    return out


def order_of(repo, oid):
    for o in F.project_orders(repo, time.time()):
        if o["id"] == oid:
            return o
    return None


def new_order(repo, title, *extra):
    r = cli(repo, "new", *extra, "--", title)  # `--`: a title may start with a dash
    if r.returncode != 0:
        raise RuntimeError("new failed: %s" % r.stderr)
    return r.stdout.split()[0]


def finish(repo, oid):
    """Take an order to done: the spec gate (approve), then review and the merge gate's pass written straight
    into the ledger (test_merge_gate takes the real path: a run, a merge, approve --gate merge)."""
    r = cli(repo, "approve", oid)
    if r.returncode != 0:
        raise RuntimeError("approve failed: %s" % r.stderr)
    force_review(repo, oid)
    append(repo, oid, "gate", gate="merge", state="pass", status="done")


def scenarios(repo, oid, texts=None):
    """The order's holdout directory (made by cc-factory holdout) with one S<n>-<slug>.md per text."""
    r = cli(repo, "holdout", oid)
    if r.returncode != 0:
        raise RuntimeError("holdout failed: %s" % r.stderr)
    hd = r.stdout.rstrip("\n").splitlines()[-1]
    for i, text in enumerate(texts if texts is not None else ["Scenario one.\n", "Scenario two.\n", "Scenario three.\n"], 1):
        write(os.path.join(hd, "S%02d-case-%d.md" % (i, i)), text)
    return hd


def mode(path):
    return stat.S_IMODE(os.lstat(path).st_mode)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def make_fake_cc_tool():
    """A throwaway cc_tool checkout (the CLI, the library, the templates) whose git history ships
    two versions of factory-line.js, to test init's refresh rule independently of this repo's history."""
    d = os.path.join(BASE, "fake-cc-tool")
    os.makedirs(os.path.join(d, "bin"))
    os.makedirs(os.path.join(d, "lib", "cc_dash"))
    shutil.copy(CLI, os.path.join(d, "bin", "cc-factory"))
    os.chmod(os.path.join(d, "bin", "cc-factory"), 0o755)
    shutil.copy(LIB, os.path.join(d, "lib", "cc_dash", "factory.py"))
    shutil.copytree(os.path.join(REPO, "templates", "factory"), os.path.join(d, "templates", "factory"))
    git(d, "init", "-q")
    js = os.path.join(d, ".claude", "workflows", "factory-line.js")
    write(js, "// factory-line v1\n")
    git(d, "add", "-A")
    git(d, "commit", "-q", "-m", "v1")
    write(js, "// factory-line v2\n")
    git(d, "commit", "-q", "-am", "v2")
    return d, os.path.join(d, "bin", "cc-factory"), js


# ── init ──────────────────────────────────────────────────────────────────────
def test_cli_basics():
    r = subprocess.run([PY, CLI, "--help"], capture_output=True, text=True, env=env_for(), timeout=30)
    check("--help exits 0 and lists the subcommands", r.returncode == 0 and all(c in r.stdout for c in (
        "init", "new", "list", "show", "mark", "approve", "reject", "lane", "holdout", "run-args", "record")), r.stderr)
    r = subprocess.run([PY, CLI], capture_output=True, text=True, env=env_for(), timeout=30)
    check("no subcommand: usage error, exit 1", r.returncode == 1, (r.returncode, r.stderr))
    r = subprocess.run([PY, CLI, "frobnicate"], capture_output=True, text=True, env=env_for(), timeout=30)
    check("unknown subcommand: exit 1", r.returncode == 1, r.returncode)
    d = mkrepo()
    r = cli(d, "new")
    check("new without a title: exit 1", r.returncode == 1, (r.returncode, r.stderr))
    r = cli(d, "show", "banana")
    check("show with a malformed id: exit 1", r.returncode == 1 and "not an order id" in r.stderr, (r.returncode, r.stderr))
    r = cli(os.path.join(BASE, "no-such-dir"), "list")
    check("-C on a missing directory: exit 1", r.returncode == 1, (r.returncode, r.stderr))
    plain = os.path.join(BASE, "not-a-repo")
    os.makedirs(plain)
    r = cli(plain, "list")
    check("outside a git repository: BLOCKED, exit 2", blocked(r), (r.returncode, r.stderr))
    sub = os.path.join(d, "sub", "deeper")
    os.makedirs(sub)
    r = cli(None, "list", cwd=sub)
    check("no -C: the nearest ancestor with a .git is the project", r.returncode == 0 and "no orders yet" in r.stdout, (r.returncode, r.stdout, r.stderr))
    r = cli(d, "list", "-C", d)
    check("-C is accepted after the subcommand too", r.returncode == 0, r.stderr)


def test_init():
    d = mkrepo(init=False)
    r = cli(d, "init")
    check("init: exit 0", r.returncode == 0, r.stderr + r.stdout)
    for rel in ("factory/README.md", "factory/line.json", "factory/order-template.md", "factory/orders/.gitkeep"):
        check("init: %s exists" % rel, os.path.isfile(os.path.join(d, rel)), rel)
    check("init: README, line.json and the order template are the shipped templates",
          all(read(os.path.join(d, "factory", f)) == read(os.path.join(REPO, "templates", "factory", f))
              for f in ("README.md", "line.json", "order-template.md")))
    check("init: line.json is the default line (the library's DEFAULT_LINE)", F.line_config(d) == F.DEFAULT_LINE, F.line_config(d))
    json.loads(read(os.path.join(d, "factory", "line.json")))
    check("init: the ledger directory exists at 0700", os.path.isdir(os.path.dirname(ledger_file(d))) and mode(os.path.dirname(ledger_file(d))) == 0o700,
          oct(mode(os.path.dirname(ledger_file(d)))) if os.path.isdir(os.path.dirname(ledger_file(d))) else "missing")
    gi = read(os.path.join(d, ".gitignore"))
    check("init: .gitignore gets both lines and one comment", gi.splitlines().count(".factory-holdout/") == 1
          and gi.splitlines().count(".claude/worktrees/") == 1 and gi.count("# cc_tool factory: holdout scenarios and lane worktrees") == 1, gi)
    check("init: next steps are printed", "cc-factory new" in r.stdout and "cc-factory approve" in r.stdout and "/factory run" in r.stdout, r.stdout[-400:])
    check("init: the order directory is ignored-free, the holdout and worktrees are ignored",
          git(d, "check-ignore", "-q", "--", ".factory-holdout/FO-001", rc=None).returncode == 0
          and git(d, "check-ignore", "-q", "--", ".claude/worktrees/FO-001-x", rc=None).returncode == 0
          and git(d, "check-ignore", "-q", "--", "factory/orders/FO-001-x.md", rc=None).returncode == 1)
    # Second run: nothing duplicated, local edits kept.
    write(os.path.join(d, "factory", "README.md"), "mine\n")
    r2 = cli(d, "init")
    gi2 = read(os.path.join(d, ".gitignore"))
    check("init again: exit 0 and .gitignore unchanged", r2.returncode == 0 and gi2 == gi, gi2)
    check("init again: an edited README is kept", read(os.path.join(d, "factory", "README.md")) == "mine\n" and "kept" in r2.stdout, r2.stdout)
    r3 = cli(d, "init", "--force")
    check("init --force: README, line and template replaced", r3.returncode == 0
          and read(os.path.join(d, "factory", "README.md")) == read(os.path.join(REPO, "templates", "factory", "README.md")), r3.stdout)
    # .gitignore without a trailing newline, one line already there under another spelling.
    d2 = mkrepo(init=False)
    write(os.path.join(d2, ".gitignore"), "node_modules\n/.factory-holdout")
    cli(d2, "init")
    gi = read(os.path.join(d2, ".gitignore"))
    check("init: appends after a missing newline and skips a line already ignored (other spelling)",
          gi == "node_modules\n/.factory-holdout\n# cc_tool factory: holdout scenarios and lane worktrees\n.claude/worktrees/\n", repr(gi))
    # Not a git repository: files, no ledger, still exit 0.
    plain = os.path.join(BASE, "plain-project")
    os.makedirs(plain)
    r = cli(plain, "init", plain)
    check("init in a directory that is not a git repository: scaffold, exit 0, says so",
          r.returncode == 0 and "not a git repository" in r.stdout and os.path.isfile(os.path.join(plain, "factory", "line.json")), r.stdout + r.stderr)
    # A symlinked factory/ is refused.
    d3 = mkrepo(init=False)
    target = os.path.join(BASE, "elsewhere")
    os.makedirs(target)
    os.symlink(target, os.path.join(d3, "factory"))
    r = cli(d3, "init")
    check("init: a symlinked factory/ is refused (BLOCKED, exit 2) and nothing lands in its target", blocked(r) and os.listdir(target) == [], (r.returncode, r.stderr, os.listdir(target)))
    # PROJECT positional.
    d4 = mkrepo(init=False)
    r = cli(None, "init", d4, cwd=BASE)
    check("init PROJECT: scaffolds that project", r.returncode == 0 and os.path.isfile(os.path.join(d4, "factory", "README.md")), r.stderr)


def test_init_line_js():
    fake, fake_cli, fake_js = make_fake_cc_tool()
    v1, v2 = "// factory-line v1\n", "// factory-line v2\n"

    def proj():
        return mkrepo("jsproj", init=False)

    def dst(d):
        return os.path.join(d, ".claude", "workflows", "factory-line.js")

    d = proj()
    r = cli(d, "init", script=fake_cli)
    check("line.js: copied into a project that has none", r.returncode == 0 and read(dst(d)) == v2 and "copied" in r.stdout, r.stdout)
    r = cli(d, "init", script=fake_cli)
    check("line.js: identical -> up to date", read(dst(d)) == v2 and "up to date" in r.stdout, r.stdout)
    d = proj()
    write(dst(d), v1)
    r = cli(d, "init", script=fake_cli)
    check("line.js: an older shipped version is refreshed", read(dst(d)) == v2 and "refreshed" in r.stdout, r.stdout)
    d = proj()
    write(dst(d), "// my edits\n")
    r = cli(d, "init", script=fake_cli)
    check("line.js: a locally edited copy is left alone and init says so",
          r.returncode == 0 and read(dst(d)) == "// my edits\n" and "left alone" in r.stdout and "--force" in r.stdout, r.stdout)
    r = cli(d, "init", "--force", script=fake_cli)
    check("line.js: --force replaces the edited copy", read(dst(d)) == v2 and "replaced" in r.stdout, r.stdout)
    d = proj()
    os.makedirs(os.path.dirname(dst(d)))
    os.symlink(os.path.join(BASE, "nowhere.js"), dst(d))
    r = cli(d, "init", script=fake_cli)
    check("line.js: a symlink in the project is not followed or replaced", r.returncode == 0 and os.path.islink(dst(d)) and not os.path.exists(os.path.join(BASE, "nowhere.js")), r.stdout)
    # The project IS cc_tool: its own copy, never touched.
    r = cli(fake, "init", script=fake_cli)
    check("line.js: when the project is cc_tool itself nothing is copied", r.returncode == 0 and read(fake_js) == v2 and "own copy" in r.stdout, r.stdout)
    # No source in this cc_tool checkout yet: skipped with a note, init still succeeds.
    os.unlink(fake_js)
    d = proj()
    r = cli(d, "init", script=fake_cli)
    check("line.js: source missing -> skipped with a note, exit 0", r.returncode == 0 and "skipped" in r.stdout and not os.path.exists(dst(d)), r.stdout + r.stderr)
    # The real checkout: when the source exists init copies it byte for byte.
    real_js = os.path.join(REPO, ".claude", "workflows", "factory-line.js")
    if os.path.isfile(real_js):
        d = proj()
        r = cli(d, "init")
        check("line.js (this checkout): copied byte for byte", read(dst(d)) == read(real_js), r.stdout)
    else:
        print("note: .claude/workflows/factory-line.js not in this checkout yet; the copy check is skipped")


# ── new ───────────────────────────────────────────────────────────────────────
def test_new():
    d = mkrepo()
    r = cli(d, "new", "Add CSV export to the reports page", "--priority", "p1")
    check("new: first order is FO-001 and the path is printed", r.returncode == 0
          and r.stdout == "FO-001  factory/orders/FO-001-add-csv-export-to-the-reports-page.md\n", (r.stdout, r.stderr))
    r = cli(d, "new", "Second", "thing")
    check("new: the words of the title join; second order is FO-002", r.returncode == 0 and r.stdout.startswith("FO-002  factory/orders/FO-002-second-thing.md"), r.stdout)
    path = os.path.join(d, "factory", "orders", "FO-001-add-csv-export-to-the-reports-page.md")
    meta, body = F.parse_order_text(read(path))
    check("new: the file parses back with id, title, priority, line, created",
          meta.get("id") == "FO-001" and meta.get("title") == "Add CSV export to the reports page" and meta.get("priority") == "p1"
          and meta.get("line") == "default" and meta.get("created") == time.strftime("%Y-%m-%d"), meta)
    check("new: the body has the three headings", all(h in body for h in ("## Intent", "## Acceptance criteria", "## Notes")), body)
    evs = events(d, "FO-001")
    check("new: one created event (status queued, by cc-factory, numeric t)", len(evs) == 1 and evs[0]["event"] == "created" and evs[0]["status"] == "queued"
          and evs[0]["by"] == "cc-factory" and isinstance(evs[0]["t"], float), evs)
    check("new: the ledger file is 0600", mode(ledger_file(d)) == 0o600, oct(mode(ledger_file(d))))
    o = order_of(d, "FO-001")
    check("new: the fold sees a queued order", o and o["status"] == "queued" and o["title"] == "Add CSV export to the reports page", o)

    awkward = [
        "Fix: the parser (see #12)", "Colon at end:", "# starts with hash", "- starts with a dash", "[bracketed] title", "{braces} title",
        "'single quoted'", '"double quoted" start', "it's 100% done", "@mention first", "& ampersand", "*star", "!bang", "|pipe", ">gt",
        "%percent", "`tick`", "? question", "null", "true", "123", "1e3", "0x1F", "~", "yes", "a: b: c # d", "tab\there",
        "trailing quote\"", "back\\slash", "unicode café 日本", "--- three dashes", "---",
    ]
    bad = []
    for t in awkward:
        oid = new_order(d, t)
        m, _ = F.parse_order_text(read(F.find_order(d, oid)))
        want = " ".join(t.split())
        if m.get("title") != want or m.get("id") != oid or m.get("priority") != "p2" or m.get("line") != "default":
            bad.append((t, m.get("title")))
    check("new: %d awkward titles all parse back exactly (colons, #, YAML indicators, scalars)" % len(awkward), not bad, bad)

    oid = new_order(d, "{{id}} {{title}} {{line}} {{priority}} {{created}}")
    text = read(F.find_order(d, oid))
    m, _ = F.parse_order_text(text)
    check("new: placeholders are substituted once (a title containing them is not expanded again)",
          m.get("title") == "{{id}} {{title}} {{line}} {{priority}} {{created}}" and ("id: %s\n" % oid) in text, text[:300])
    oid = new_order(d, "x\n\t y\x1b[31m z\x07" + "w" * 300)
    m, _ = F.parse_order_text(read(F.find_order(d, oid)))
    check("new: whitespace and control characters collapse; the title is cut at 200", m.get("title", "").startswith("x y [31m z") and len(m.get("title", "")) <= 200
          and "\x1b" not in m.get("title", "") and "\x07" not in m.get("title", ""), m.get("title"))

    r = cli(d, "new", "x", "--priority", "p9")
    check("new: a bad priority is a usage error (exit 1)", r.returncode == 1, (r.returncode, r.stderr))
    r = cli(d, "new", "x", "--line", "nope")
    check("new: an unknown line is refused (BLOCKED, exit 2) and no file appears", blocked(r) and "default" in r.stderr, (r.returncode, r.stderr))
    r = cli(d, "new", "   \n  ")
    check("new: an empty title is a usage error", r.returncode == 1, (r.returncode, r.stderr))

    # Another line in line.json.
    cfg = json.loads(read(os.path.join(d, "factory", "line.json")))
    cfg["lines"]["fast"] = {"stations": [{"name": "build", "title": "Build", "model": "sonnet"}], "gates": {"spec": "auto"}, "retries": 0}
    write(os.path.join(d, "factory", "line.json"), json.dumps(cfg))
    oid = new_order(d, "Quick job", "--line", "fast")
    m, _ = F.parse_order_text(read(F.find_order(d, oid)))
    check("new: --line names a line from line.json and is written to the frontmatter", m.get("line") == "fast", m)

    # The project's own template wins over cc_tool's.
    write(os.path.join(d, "factory", "order-template.md"),
          '---\nid: {{id}}\ntitle: "{{title}}"\npriority: {{priority}}\ncreated: {{created}}\nline: {{line}}\n---\n# {{title}} ({{id}})\n\nCustom body.\n')
    oid = new_order(d, 'He said "hi": ok')
    text = read(F.find_order(d, oid))
    m, body = F.parse_order_text(text)
    check("new: the project's order-template.md is used; a quoted {{title}} stays valid; the heading shows the plain title",
          m.get("title") == 'He said "hi": ok' and 'He said "hi": ok (%s)' % oid in body and "Custom body." in body, text)
    os.unlink(os.path.join(d, "factory", "order-template.md"))
    oid = new_order(d, "Back to the shipped template")
    check("new: without the project template, cc_tool's is used", "## Acceptance criteria" in read(F.find_order(d, oid)))

    # Numbers are never reused, even when a file is deleted or numbers have gaps.
    d2 = mkrepo()
    new_order(d2, "one")
    new_order(d2, "two")
    os.unlink(F.find_order(d2, "FO-002"))
    check("new: a deleted order's number is not reused (the ledger remembers it)", new_order(d2, "three") == "FO-003")
    write(os.path.join(d2, "factory", "orders", "FO-010-handwritten.md"), "---\ntitle: by hand\n---\nbody\n")
    check("new: continues after the highest number present", new_order(d2, "after the gap") == "FO-011")
    # Not initialised.
    bare = mkrepo(init=False)
    r = cli(bare, "new", "x")
    check("new: refused until `cc-factory init` (BLOCKED, exit 2, says to run init)", blocked(r) and "cc-factory init" in r.stderr, (r.returncode, r.stderr))
    check("new: a refused new leaves no ledger or order file behind", not os.path.exists(os.path.join(bare, "factory")) and not events(bare))
    # Parallel `new`: distinct numbers, one file each.
    d3 = mkrepo()
    procs = [subprocess.Popen([PY, CLI, "-C", d3, "new", "parallel job %d" % i], env=env_for(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for i in range(8)]
    outs = [p.communicate(timeout=120) for p in procs]
    ids = sorted(o[0].split()[0] for o in outs if o[0].strip())
    check("new: 8 parallel runs all succeed", all(p.returncode == 0 for p in procs), [o[1] for o in outs])
    check("new: 8 parallel runs get 8 distinct, consecutive numbers", ids == ["FO-%03d" % i for i in range(1, 9)], ids)
    check("new: 8 parallel runs leave 8 files and 8 created events", len(os.listdir(os.path.join(d3, "factory", "orders"))) == 8 + 1
          and len([e for e in events(d3) if e["event"] == "created"]) == 8)


# ── list / show ───────────────────────────────────────────────────────────────
def test_list_show():
    d = mkrepo()
    r = cli(d, "list")
    check("list: an empty project says so, exit 0", r.returncode == 0 and "no orders yet" in r.stdout, r.stdout)
    bare = mkrepo(init=False)
    r = cli(bare, "list")
    check("list: before init it points at cc-factory init, exit 0", r.returncode == 0 and "cc-factory init" in r.stdout, r.stdout)
    a = new_order(d, "Alpha order", "--priority", "p1")
    b = new_order(d, "Beta order")
    c = new_order(d, "Gamma order\x1b[2J spoof")
    cli(d, "mark", b, "building", "--station", "build", "--state", "start")
    finish(d, c)
    r = cli(d, "list")
    lines = r.stdout.splitlines()
    check("list: header columns ID PRI STATUS STATION GATE LANE TITLE", lines[0].split() == ["ID", "PRI", "STATUS", "STATION", "GATE", "LANE", "TITLE"], lines[0])
    check("list: queued and building shown, done hidden", a in r.stdout and b in r.stdout and c not in r.stdout, r.stdout)
    check("list: columns are aligned", len({ln.index("queued") for ln in lines if " queued " in ln}) == 1
          and lines[1].index("p1") == lines[2].index("p2"), lines)
    row = next(ln for ln in lines if ln.startswith(b))
    check("list: the building row shows its station", row.split()[2:4] == ["building", "build"], row)
    check("list: count footer (and the hidden-finished hint)", "3 orders" in lines[-1] and "1 building" in lines[-1] and "1 done" in lines[-1] and "--all" in lines[-1], lines[-1])
    r = cli(d, "list", "--all")
    check("list --all: shows the done order too, escape sequences stripped", c in r.stdout and "\x1b" not in r.stdout, r.stdout)
    r2 = cli(d, "status")
    check("status is an alias of list", r2.stdout == cli(d, "list").stdout and r2.returncode == 0, r2.stdout)
    r = cli(d, "list", "--json")
    arr = json.loads(r.stdout)
    check("list --json: every order (done included), the library's dict shape", [o["id"] for o in arr] == [a, b, c]
          and set(arr[0]) == {"id", "title", "priority", "status", "path", "created", "updated", "station", "stations", "gate", "lane",
                              "run_id", "rework", "events", "line"}, arr[0] if arr else r.stdout)
    check("list --json equals project_orders", [o["status"] for o in arr] == [o["status"] for o in F.project_orders(d, time.time())])

    r = cli(d, "show", b)
    check("show: id, title, status, station, file, stations, last events", r.returncode == 0 and r.stdout.startswith("%s  Beta order" % b)
          and "building" in r.stdout and "factory/orders/" in r.stdout and "holdout   0 scenarios" in r.stdout
          and "stations" in r.stdout and "last 2 events" in r.stdout and "status=building" in r.stdout, r.stdout)
    check("show: short ids are accepted", cli(d, "show", "2").stdout == r.stdout)
    r = cli(d, "show", b, "--events", "1")
    check("show --events 1: one event", "last 1 event" in r.stdout and "status=building" in r.stdout and "created" not in r.stdout.split("last 1 event")[1], r.stdout)
    hd = os.path.join(d, ".factory-holdout", b)
    os.makedirs(hd)
    write(os.path.join(hd, "s1.md"), "scenario\n")
    write(os.path.join(hd, "s2.md"), "scenario\n")
    write(os.path.join(hd, "notes.txt"), "not a scenario\n")
    os.symlink(os.path.join(hd, "s1.md"), os.path.join(hd, "link.md"))
    r = cli(d, "show", b, "--json")
    j = json.loads(r.stdout)
    listed = next(o for o in json.loads(cli(d, "list", "--json").stdout) if o["id"] == b)
    check("show --json: the order dict of `list --json` at the top level, plus holdout_scenarios (md files only, symlinks not counted), "
          "recent_events and briefing (null before a recorded review)",
          j["id"] == b and j["status"] == "building" and j["holdout_scenarios"] == 2 and len(j["recent_events"]) == 2 and j["briefing"] is None
          and j["recent_events"][-1]["status"] == "building"
          and {k: v for k, v in j.items() if k not in ("holdout_scenarios", "recent_events", "briefing")} == listed, j)
    r = cli(d, "show", "FO-099")
    check("show: an unknown id is exit 1 and names the order", r.returncode == 1 and "FO-099" in r.stderr, (r.returncode, r.stderr))
    # A symlink in factory/orders is not an order.
    os.symlink("/etc/passwd", os.path.join(d, "factory", "orders", "FO-009-sneaky.md"))
    r = cli(d, "show", "9")
    check("show: an order file that is a symlink does not exist as far as the tool is concerned", r.returncode == 1, (r.returncode, r.stdout))
    r = cli(d, "list")
    check("list: a symlinked order file is not listed", "FO-009" not in r.stdout and r.returncode == 0, r.stdout)
    # Control characters: a file name carrying them is not an order; machine JSON is ASCII; refusals print clean paths.
    write(os.path.join(d, "factory", "orders", "FO-020-\x1b]0;PWNED\x07\x9b31m.md"), "---\ntitle: ok\n---\nbody\n")
    u = new_order(d, "Café ✓ été")
    r = cli(d, "list", "--json")
    arr = json.loads(r.stdout)
    check("list --json: ASCII only, and the title parses back exactly", r.stdout.isascii()
          and next(o for o in arr if o["id"] == u)["title"] == "Café ✓ été", r.stdout[-300:])
    check("list --json: a file name with control characters is not an order", "FO-020" not in r.stdout, r.stdout[-300:])
    r = cli(d, "show", u, "--json")
    check("show --json: ASCII only", r.returncode == 0 and r.stdout.isascii(), r.stdout[:200])
    check("show: a control-character file name is no order (exit 1)", cli(d, "show", "20").returncode == 1)
    odd = os.path.join(BASE, "not-a-repo-\x1b]0;PWNED\x07\x9b31m")
    os.makedirs(odd)
    r = cli(odd, "list")
    check("refusal: a path with escape sequences is printed without control characters",
          blocked(r) and "\x1b" not in r.stderr and "\x07" not in r.stderr and "\x9b" not in r.stderr and "not-a-repo-" in r.stderr, repr(r.stderr[:200]))
    r = cli(os.path.join(odd, "missing"), "list")
    check("usage error: a -C path with escape sequences is printed without control characters",
          r.returncode == 1 and "\x1b" not in r.stderr and "\x9b" not in r.stderr, repr(r.stderr[:200]))

    # Latency with 50 orders.
    big = mkrepo()
    for i in range(1, 51):
        write(os.path.join(big, "factory", "orders", "FO-%03d-job-%d.md" % (i, i)),
              "---\nid: FO-%03d\ntitle: Job number %d\npriority: p%d\ncreated: 2026-10-25\nline: default\n---\n## Intent\nx\n" % (i, i, 1 + i % 3))
    cli(big, "mark", "FO-007", "building", "--station", "build", "--state", "start")
    times = []
    for _ in range(3):
        t0 = time.perf_counter()
        r = cli(big, "list")
        times.append(time.perf_counter() - t0)
    check("list with 50 orders: correct and fast (< 2 s per call, worst of 3 %.2f s)" % max(times), r.returncode == 0 and "50 orders" in r.stdout and max(times) < 2.0, (max(times), r.stderr))
    t0 = time.perf_counter()
    r = cli(big, "list", "--json")
    check("list --json with 50 orders under 2 s", r.returncode == 0 and len(json.loads(r.stdout)) == 50 and time.perf_counter() - t0 < 2.0)


# ── mark ──────────────────────────────────────────────────────────────────────
def test_mark():
    d = mkrepo()
    o1 = new_order(d, "Marked order")
    r = cli(d, "mark", o1, "building")
    check("mark: a status", r.returncode == 0 and order_of(d, o1)["status"] == "building", (r.stdout, r.stderr))
    e = events(d, o1)[-1]
    check("mark: one status event by cc-factory", e["event"] == "status" and e["status"] == "building" and e["by"] == "cc-factory" and "station" not in e, e)
    r = cli(d, "mark", o1, "--station", "build", "--state", "start", "--run-id", "wf_ab12cd")
    e = events(d, o1)[-1]
    check("mark: station start without a status is a station event", r.returncode == 0 and e["event"] == "station" and e["station"] == "build"
          and e["state"] == "start" and e["run_id"] == "wf_ab12cd" and "status" not in e, e)
    o = order_of(d, o1)
    check("mark: the fold shows build active and the run id", o["station"] == "build" and [s["status"] for s in o["stations"]][:2] == ["pending", "active"] and o["run_id"] == "wf_ab12cd", o)
    cli(d, "mark", o1, "--station", "build", "--state", "pass")
    cli(d, "mark", o1, "verifying", "--station", "test", "--state", "start", "--note", "running pytest")
    e = events(d, o1)[-1]
    o = order_of(d, o1)
    check("mark: status with a station and state is one status event carrying both", e["event"] == "status" and e["status"] == "verifying"
          and e["station"] == "test" and e["state"] == "start" and e["note"] == "running pytest", e)
    check("mark: build is done, test active, status verifying", [s["status"] for s in o["stations"]][:3] == ["pending", "done", "active"] and o["status"] == "verifying", o["stations"])
    n_before = len(events(d, o1))
    r = cli(d, "mark", o1, "--note", "just a thought")
    e = events(d, o1)[-1]
    check("mark: only --note writes a note event and moves nothing", r.returncode == 0 and e["event"] == "note" and e["note"] == "just a thought"
          and len(events(d, o1)) == n_before + 1 and order_of(d, o1)["status"] == "verifying", e)
    r = cli(d, "mark", o1, "--note", "n" * 2000)
    check("mark: a 2000-character note is clipped to 500", r.returncode == 0 and len(events(d, o1)[-1]["note"]) == 500, len(events(d, o1)[-1].get("note", "")))
    secrets = ["sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123", "sk-abcdefghijklmnopqrstuvwxyz012345", "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
               "github_pat_11ABCDEFG0123456789_abcdefghij", "AKIAIOSFODNN7EXAMPLE", "xoxb-1234567890-abcdefghij", "hunter2hunter2",
               "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC", "Bearer abcdefghijklmnopqrstuvwxyz012345"]
    note = ("deploy with %s and %s, then %s %s %s and %s; password = %s; \"api_key\": \"%s\"; " % (
        secrets[0], secrets[1], secrets[2], secrets[3], secrets[4], secrets[5], secrets[6], "zzz-top-secret-zzz")
        + "-----BEGIN RSA PRIVATE KEY----- %s -----END RSA PRIVATE KEY----- and %s done" % (secrets[7], secrets[8]))
    r = cli(d, "mark", o1, "--note", note)
    got = events(d, o1)[-1].get("note", "")
    leaked = [s for s in secrets + ["zzz-top-secret-zzz"] if s in got]
    check("mark: secret shapes in a note are redacted, the rest of the note survives", r.returncode == 0 and not leaked and "[redacted]" in got
          and "deploy with" in got and "done" in got and "PRIVATE KEY" not in got, (leaked, got))
    r = cli(d, "mark", o1, "--note", "token usage: 1200 tokens; pushed to origin")
    check("mark: ordinary prose is not mangled", events(d, o1)[-1]["note"] == "token usage: 1200 tokens; pushed to origin", events(d, o1)[-1])

    n_before = len(events(d))
    r = cli(d, "mark", o1, "shipped")
    check("mark: an invalid status is BLOCKED (exit 2) and lists the valid ones", blocked(r) and "building" in r.stderr and "rejected" in r.stderr, (r.returncode, r.stderr))
    r2 = cli(d, "mark", o1, "--station", "nope", "--state", "start")
    check("mark: an unknown station is BLOCKED (exit 2)", blocked(r2) and "plan" in r2.stderr, (r2.returncode, r2.stderr))
    r3 = cli(d, "mark", o1, "--station", "build")
    check("mark: --station without --state is a usage error (exit 1)", r3.returncode == 1, (r3.returncode, r3.stderr))
    r4 = cli(d, "mark", o1, "--station", "build", "--state", "maybe")
    check("mark: a bad --state is a usage error (exit 1)", r4.returncode == 1, (r4.returncode, r4.stderr))
    r5 = cli(d, "mark", o1)
    check("mark: nothing to record is a usage error (exit 1)", r5.returncode == 1 and "nothing to record" in r5.stderr, (r5.returncode, r5.stderr))
    r6 = cli(d, "mark", o1, "building", "--run-id", "r" * 200)
    check("mark: a run id the ledger would clip is BLOCKED, not silently altered", blocked(r6), (r6.returncode, r6.stderr))
    r7 = cli(d, "mark", "FO-077", "building")
    check("mark: an unknown order is exit 1", r7.returncode == 1, (r7.returncode, r7.stderr))
    r8 = cli(d, "mark", o1, "approved")
    check("mark approved: BLOCKED, it is the spec gate's decision (points at cc-factory approve ID)",
          blocked(r8) and ("cc-factory approve %s." % o1) in r8.stderr and "gate" in r8.stderr, (r8.returncode, r8.stderr))
    r9 = cli(d, "mark", o1, "done", "--note", "shipped it")
    check("mark done: BLOCKED, it is the merge gate's decision (points at approve ID --gate merge)",
          blocked(r9) and ("cc-factory approve %s --gate merge" % o1) in r9.stderr, (r9.returncode, r9.stderr))
    r10 = cli(d, "mark", o1, "approved", "--station", "build", "--state", "pass")
    check("mark approved with a station: still BLOCKED", blocked(r10), (r10.returncode, r10.stderr))
    check("mark: none of the refused calls wrote anything", len(events(d)) == n_before, (len(events(d)), n_before))
    check("mark: approved and done are never written by mark", not [e for e in events(d) if e["event"] != "gate" and e.get("status") in ("approved", "done")])
    r = cli(d, "mark", o1, "failed", "--branch", "factory/FO-001-x", "--worktree", "/some/where")
    e = events(d, o1)[-1]
    check("mark: --branch and --worktree are recorded as given", r.returncode == 0 and e.get("branch") == "factory/FO-001-x" and e.get("worktree") == "/some/where", e)
    r = cli(d, "mark", o1, "queued")
    check("mark: it is the escape hatch (any status but the gates' and review, also after failed)", r.returncode == 0 and order_of(d, o1)["status"] == "queued")
    n_before = len(events(d))
    r = cli(d, "mark", o1, "review")
    check("mark review: BLOCKED, it is the line's verdict that only record of a passing run writes",
          blocked(r) and "review is the line's verdict; only cc-factory record of a passing run writes it" in r.stderr
          and ("cc-factory record %s --workflow-run" % o1) in r.stderr, (r.returncode, r.stderr))
    r = cli(d, "mark", o1, "review", "--station", "review", "--state", "pass")
    check("mark review with a station: still BLOCKED, nothing written", blocked(r) and len(events(d)) == n_before, (r.returncode, r.stderr))
    r = cli(d, "mark", o1, "nope")
    check("mark: the list of valid statuses offers neither review nor the gates'", blocked(r) and "review" not in r.stderr.split("use one of:")[1]
          and "approved" not in r.stderr.split("use one of:")[1], r.stderr)
    r = cli(d, "mark", o1, "--station", "review", "--state", "pass")
    check("mark: a review *station* event is still fine (it is not the status)", r.returncode == 0 and order_of(d, o1)["status"] == "queued", r.stderr)

    # The spec gate wants hidden scenarios to check the work against.
    s1 = new_order(d, "Spec with scenarios")
    r = cli(d, "mark", s1, "spec")
    check("mark spec: BLOCKED with no holdout scenarios on a line with a verify station (says how many, how to add them, --no-holdout)",
          blocked(r) and "0 holdout scenarios" in r.stderr and "at least 3" in r.stderr and ("cc-factory holdout %s" % s1) in r.stderr
          and "--no-holdout" in r.stderr and order_of(d, s1)["status"] == "queued", (r.returncode, r.stderr))
    hd = scenarios(d, s1, ["one\n", "two\n"])
    r = cli(d, "mark", s1, "spec")
    check("mark spec: 2 scenarios are still too few", blocked(r) and "2 holdout scenarios" in r.stderr, (r.returncode, r.stderr))
    os.symlink(os.path.join(hd, "S01-case-1.md"), os.path.join(hd, "S09-link.md"))
    write(os.path.join(hd, "S08-notes.txt"), "not a scenario\n")
    r = cli(d, "mark", s1, "spec")
    check("mark spec: a symlinked .md and a .txt do not count", blocked(r) and "2 holdout scenarios" in r.stderr, (r.returncode, r.stderr))
    write(os.path.join(hd, "S03-case-3.md"), "three\n")
    r = cli(d, "mark", s1, "spec")
    e = events(d, s1)[-1]
    check("mark spec: 3 scenarios pass; the event records the count", r.returncode == 0 and e["status"] == "spec" and e["scenarios"] == 3
          and "no_holdout" not in e and "3 holdout scenarios" in r.stdout and order_of(d, s1)["gate"] == {"name": "spec", "waiting": True}, (r.stdout, r.stderr, e))
    s2 = new_order(d, "Spec without scenarios")
    r = cli(d, "mark", s2, "spec", "--no-holdout")
    e = events(d, s2)[-1]
    check("mark spec --no-holdout: accepted with none, the choice and the count are on the event", r.returncode == 0 and e["status"] == "spec"
          and e["no_holdout"] is True and e["scenarios"] == 0 and "--no-holdout" in r.stdout, (r.stdout, r.stderr, e))
    r = cli(d, "mark", s2, "building", "--no-holdout")
    check("mark --no-holdout without status spec: usage error (exit 1)", r.returncode == 1 and "spec" in r.stderr, (r.returncode, r.stderr))
    cfg = json.loads(read(os.path.join(d, "factory", "line.json")))
    cfg["lines"]["lite"] = {"stations": [{"name": "build", "title": "Build"}, {"name": "review", "title": "Review"}]}
    write(os.path.join(d, "factory", "line.json"), json.dumps(cfg))
    s3 = new_order(d, "No verify station", "--line", "lite")
    r = cli(d, "mark", s3, "spec")
    check("mark spec: a line with no verify station needs no scenarios", r.returncode == 0 and "scenarios" not in events(d, s3)[-1], (r.returncode, r.stderr))


# ── approve / reject ──────────────────────────────────────────────────────────
def lane_with_commit(repo, oid, n=1, subject=None):
    """The order's lane (cc-factory lane) with n commits on it; (worktree, branch)."""
    r = cli(repo, "lane", oid)
    if r.returncode != 0:
        raise RuntimeError("lane failed: %s" % r.stderr)
    wt = r.stdout.rstrip("\n").splitlines()[-1]
    branch = order_of(repo, oid)["lane"]["branch"]
    for i in range(n):
        write(os.path.join(wt, "src", "%s-%d.txt" % (oid, i)), "change %d of %s\n" % (i, oid))
        git(wt, "add", "-A")
        git(wt, "commit", "-q", "-m", subject or ("%s: change %d" % (oid, i)))
    return wt, branch


def test_gates():
    d = mkrepo()
    a = new_order(d, "Spec gate order")
    r = cli(d, "mark", a, "spec", "--no-holdout")
    o = order_of(d, a)
    check("gate: status spec waits at the spec gate", o["status"] == "spec" and o["gate"] == {"name": "spec", "waiting": True}, o)
    r = cli(d, "approve", a, "--note", "looks right")
    e = events(d, a)[-1]
    o = order_of(d, a)
    check("approve: gate inferred as spec; one gate event {gate spec, state pass, status approved}",
          r.returncode == 0 and e["event"] == "gate" and e["gate"] == "spec" and e["state"] == "pass" and e["status"] == "approved" and e["note"] == "looks right", (r.stderr, e))
    check("approve spec: folded status approved, no gate waiting", o["status"] == "approved" and o["gate"] is None, o)
    check("approve spec: says what comes next", "cc-factory lane %s" % a in r.stdout, r.stdout)
    r = cli(d, "approve", a, "--gate", "merge")
    check("approve --gate merge outside review: BLOCKED (exit 2)", blocked(r), (r.returncode, r.stderr))
    cli(d, "mark", a, "building")
    r = cli(d, "approve", a, "--gate", "spec")
    check("approve --gate spec while building: BLOCKED", blocked(r), (r.returncode, r.stderr))

    b = new_order(d, "Merge gate order")
    cli(d, "approve", b)
    _wt, bb = lane_with_commit(d, b)
    cli(d, "mark", b, "--station", "review", "--state", "pass")
    force_review(d, b)
    o = order_of(d, b)
    check("merge gate: review with no active station waits at the merge gate", o["status"] == "review" and o["gate"] == {"name": "merge", "waiting": True}, o)
    git(d, "merge", "-q", "--no-ff", "-m", "merge %s" % b, bb)
    r = cli(d, "approve", b)
    e = events(d, b)[-1]
    o = order_of(d, b)
    check("approve: gate inferred as merge when the order is in review; status done", r.returncode == 0 and e["gate"] == "merge" and e["state"] == "pass" and e["status"] == "done", (r.stderr, e))
    check("approve merge: folded done, no gate, no station left pending (skipped or done)", o["status"] == "done" and o["gate"] is None
          and all(s["status"] in ("done", "skipped") for s in o["stations"]), o)
    r = cli(d, "mark", b, "blocked", "--note", "reopen")
    check("mark on a done order: BLOCKED, it stays done", blocked(r) and "done" in r.stderr and order_of(d, b)["status"] == "done", (r.returncode, r.stderr))
    r = cli(d, "approve", b)
    check("approve on a done order: BLOCKED", blocked(r) and "done" in r.stderr, (r.returncode, r.stderr))
    r = cli(d, "reject", b)
    check("reject on a done order: BLOCKED", blocked(r), (r.returncode, r.stderr))

    c = new_order(d, "Rejected at spec")
    r = cli(d, "reject", c, "--note", "not now")
    e = events(d, c)[-1]
    check("reject: gate inferred as spec; {gate spec, state fail, status rejected}", r.returncode == 0 and e["gate"] == "spec" and e["state"] == "fail"
          and e["status"] == "rejected" and e["note"] == "not now" and order_of(d, c)["status"] == "rejected", (r.stderr, e))
    check("reject: a rejected order has no gate", order_of(d, c)["gate"] is None)
    r = cli(d, "approve", c)
    check("approve on a rejected order: BLOCKED", blocked(r) and "rejected" in r.stderr, (r.returncode, r.stderr))
    r = cli(d, "reject", c)
    check("reject on a rejected order: BLOCKED", blocked(r), (r.returncode, r.stderr))
    r = cli(d, "mark", c, "queued")
    check("mark on a rejected order: BLOCKED, the rejection stands", blocked(r) and "rejected" in r.stderr and order_of(d, c)["status"] == "rejected", (r.returncode, r.stderr))
    r = cli(d, "mark", c, "--note", "dropped for the quarter")
    check("a note on a rejected order still records and moves nothing", r.returncode == 0 and order_of(d, c)["status"] == "rejected", (r.returncode, r.stderr))

    e1 = new_order(d, "Rework at merge")
    cli(d, "approve", e1)
    force_review(d, e1)
    r = cli(d, "reject", e1, "--rework", "--note", "tests too thin")
    ev = events(d, e1)[-1]
    o = order_of(d, e1)
    check("reject --rework at the merge gate: gate fail event with status approved (it can run again)", r.returncode == 0 and ev["gate"] == "merge"
          and ev["state"] == "fail" and ev["status"] == "approved" and o["status"] == "approved" and o["gate"] is None, (r.stderr, ev, o))
    r = cli(d, "reject", e1, "--rework")
    check("reject --rework while not in review: it infers the spec gate, so --rework is a usage error (exit 1)", r.returncode == 1, (r.returncode, r.stderr))
    f1 = new_order(d, "Reject at merge")
    cli(d, "approve", f1)
    force_review(d, f1)
    r = cli(d, "reject", f1, "--gate", "merge")
    check("reject --gate merge: status rejected", r.returncode == 0 and order_of(d, f1)["status"] == "rejected" and events(d, f1)[-1]["gate"] == "merge", r.stderr)
    r = cli(d, "mark", f1, "blocked", "--note", "reopen")
    check("mark after a merge-gate rejection: BLOCKED, and run-args stays refused", blocked(r) and order_of(d, f1)["status"] == "rejected"
          and blocked(cli(d, "run-args", f1)), (r.returncode, r.stderr))
    g1 = new_order(d, "Merge gate while queued")
    r = cli(d, "reject", g1, "--gate", "merge")
    check("reject --gate merge outside review: BLOCKED", blocked(r), (r.returncode, r.stderr))
    r = cli(d, "approve", "FO-088")
    check("approve: unknown order is exit 1", r.returncode == 1, (r.returncode, r.stderr))
    r = cli(d, "approve", g1, "--gate", "other")
    check("approve: a bad --gate is a usage error (exit 1)", r.returncode == 1, (r.returncode, r.stderr))
    # A failed order can be approved again and re-run.
    h1 = new_order(d, "Failed then retried")
    cli(d, "approve", h1)
    cli(d, "mark", h1, "failed")
    r = cli(d, "approve", h1)
    check("approve spec on a failed order sends it back to approved", r.returncode == 0 and order_of(d, h1)["status"] == "approved", (r.stderr, order_of(d, h1)))
    # An order marked past the gate without an approval: the spec gate is not behind it, so it can still be approved.
    k1 = new_order(d, "Marked building unapproved")
    cli(d, "mark", k1, "building")
    r = cli(d, "approve", k1, "--gate", "spec")
    check("approve --gate spec on an order marked building with no approval: accepted (the gate was never passed)",
          r.returncode == 0 and order_of(d, k1)["status"] == "approved" and events(d, k1)[-1]["event"] == "gate", (r.returncode, r.stderr))
    r = cli(d, "approve", k1, "--into", "HEAD")
    check("approve --into at the spec gate: usage error (exit 1)", r.returncode == 1 and "merge gate" in r.stderr, (r.returncode, r.stderr))


# ── human gates: approve and reject refuse under Claude Code ──────────────────
REASON_STUB = STUB_HUMAN.replace("def recording_problem():\n    return ''", "def recording_problem():\n    return 'it is a test double that says no'")


def test_human_gates():
    d = mkrepo()
    a = new_order(d, "Human only")
    n0 = len(events(d))
    under = {"CLAUDECODE": "1"}
    for args, decision, cmd in ((("approve", a), "passing %s's spec gate" % a, "cc-factory approve %s --gate spec" % a),
                                (("reject", a), "rejecting %s at the spec gate" % a, "cc-factory reject %s --gate spec" % a),
                                (("approve", a, "--note", "fine"), "passing %s's spec gate" % a, "cc-factory approve %s --gate spec --note ..." % a)):
        r = cli(d, *args, script=CLI, env_extra=under)
        check("human gate: the real `%s` with CLAUDECODE=1 is BLOCKED in the house style, naming the decision, the reason and the "
              "command to run in your own terminal" % " ".join(args),
              blocked(r) and r.stderr.startswith("BLOCKED: %s is a human's decision, and this cc-factory was not started from your own terminal (" % decision)
              and "CLAUDECODE" in r.stderr and ("Suggestion: run it from your own terminal: %s" % cmd) in r.stderr, (r.returncode, r.stderr))
    # Who runs it comes before the order's state: a merge-gate call on an order that is not in review gets
    # the human refusal, not "the merge gate opens when a run reaches review" (the trial's headless agent
    # saw only the state message, which invites fixing the state and retrying).
    for args, decision in ((("approve", a, "--gate", "merge"), "passing %s's merge gate" % a),
                           (("reject", a, "--gate", "merge", "--rework"), "sending %s back from the merge gate for rework" % a)):
        r = cli(d, *args, script=CLI, env_extra=under)
        check("human gate: `%s` on an order not in review, under Claude Code, gets the human refusal before any state check" % " ".join(args),
              blocked(r) and ("BLOCKED: %s is a human's decision" % decision) in r.stderr and "reaches review" not in r.stderr,
              (r.returncode, r.stderr))
    check("human gate: the refusals wrote nothing", len(events(d)) == n0, (len(events(d)), n0))
    # Past the origin rules, the decision needs a random code, shown only on the controlling terminal, typed back there.
    n1 = len(events(d))
    r = cli(d, "approve", a, tty=False, stdin=a + "\n")
    check("human gate: no controlling terminal -> BLOCKED; nothing on stdin counts (only /dev/tty is asked)",
          blocked(r) and "has no terminal of its own to confirm on" in r.stderr and "run it from your own terminal and type the code it shows" in r.stderr,
          (r.returncode, r.stderr))
    r = cli(d, "approve", a, typed="WRONG2")   # O is not in the code's alphabet, so it can never match
    m1 = CODE_RE.search(r.tty.encode())
    check("human gate: a wrong code typed on the terminal -> BLOCKED (the code was shown there: 6 characters of the unambiguous alphabet)",
          blocked(r) and "what was typed on its terminal is not the code it showed there" in r.stderr
          and m1 and re.fullmatch(rb"[23456789ABCDEFGHJKMNPQRSTUVWXYZ]{6}", m1.group(1)), (r.returncode, r.stderr, r.tty[-300:]))
    r = cli(d, "approve", a, typed=a)
    check("human gate: the order id (predictable) is not the code -> BLOCKED", blocked(r) and "is not the code it showed there" in r.stderr, (r.returncode, r.stderr))
    r = cli(d, "approve", a, typed="")
    check("human gate: an empty answer -> BLOCKED", blocked(r) and "is not the code" in r.stderr, (r.returncode, r.stderr))
    check("human gate: unconfirmed decisions wrote nothing", len(events(d)) == n1, (len(events(d)), n1))
    # The stub tree (cc_procs says: a human, in a terminal) with the shown code typed back lets the same calls through. A line
    # typed ahead of the question is discarded: had it been read, it would have been the (wrong) answer.
    r = on_tty([PY, HUMAN_CLI, "-C", d, "approve", a], BASE, env_for(None), None, 60, CODE, True, ahead="ZZZZZZ\n")
    m2 = CODE_RE.search(r.tty.encode())
    check("human gate: the stub tree approves once the shown code is typed back, after a line typed ahead was discarded "
          "(exit 0, the gate event written; the question names the spec hash)",
          r.returncode == 0 and events(d, a)[-1]["event"] == "gate" and "pass %s's spec gate (approve the spec as it is now, on the default line, sha256" % a in r.tty,
          (r.returncode, r.stderr, r.tty[-300:]))
    led = read(ledger_file(d))
    check("human gate: the code is fresh each time and never reaches stdout, stderr or the ledger (the terminal only)",
          m1 and m2 and m1.group(1) != m2.group(1)
          and all(c.decode() not in r.stdout + r.stderr + led for c in (m1.group(1), m2.group(1))), (r.stdout, r.stderr))
    force_review(d, a)
    for args, decision in ((("approve", a, "--gate", "merge"), "passing %s's merge gate" % a),
                           (("reject", a, "--gate", "merge", "--rework"), "sending %s back from the merge gate for rework" % a),
                           (("reject", a, "--gate", "merge"), "rejecting %s at the merge gate" % a)):
        r = cli(d, *args, script=CLI, env_extra=under)
        check("human gate: the real `%s` under Claude Code is BLOCKED (%s)" % (" ".join(args), decision),
              blocked(r) and ("BLOCKED: %s is a human's decision" % decision) in r.stderr, (r.returncode, r.stderr))
    if os.environ.get("CLAUDECODE"):
        # Without Claude Code's variables the real CLI still runs the full origin rules: claude is among its parents here.
        r = cli(d, "reject", a, "--gate", "merge", script=CLI, env_extra={"CLAUDECODE": ""})
        check("human gate: the real CLI without Claude Code's variables is still BLOCKED under Claude Code (its parents)",
              blocked(r) and "is a human's decision" in r.stderr and order_of(d, a)["status"] == "review", (r.returncode, r.stderr))
    # cc_procs's own answer is the reason.
    r = cli(d, "reject", a, "--gate", "merge", script=REASON_CLI)
    check("human gate: a cc_procs that gives a reason refuses with that reason", blocked(r)
          and "(it is a test double that says no)" in r.stderr and order_of(d, a)["status"] == "review", (r.returncode, r.stderr))
    for tree, label, needle in ((BROKEN_CLI, "raises on import", "could not be loaded"), (MISSING_CLI, "is missing", "could not be loaded"),
                                (NONE_CLI, "answers None", "gave no answer"), (THROWS_CLI, "raises in the check", "cannot tell who started it")):
        r = cli(d, "reject", a, "--gate", "merge", script=tree)
        check("human gate: fails closed when cc_procs %s" % label, blocked(r) and needle in r.stderr and order_of(d, a)["status"] == "review",
              (r.returncode, r.stderr))
    # Without /proc (macOS) nothing tells your terminal from an agent's (script(1) gives an agent a terminal and clean
    # variables, and it can read the code off its own pty), so the gates refuse outright, as control mode does.
    n2 = len(events(d))
    noproc = [PY, NOPROC_CLI, "-C", d, "reject", a, "--gate", "merge", "--rework"]
    for label, env_extra, stdin_tty in (("under Claude Code's variables", under, False), ("with clean variables", {"CLAUDECODE": ""}, False),
                                        ("with clean variables and a terminal on stdin too", {"CLAUDECODE": ""}, True)):
        r = on_tty(noproc, BASE, env_for(env_extra), None, 60, CODE, True, stdin_tty=stdin_tty)
        check("human gate, no /proc: refused outright %s, on a terminal of its own, before any code is shown" % label,
              blocked(r) and "this system has no /proc, which factory gates need to tell your terminal from an agent's" in r.stderr
              and "Linux only" in r.stderr and "on a Linux machine" in r.stderr and "Type the code" not in r.tty, (r.returncode, r.stderr, r.tty[-200:]))
    r = cli(d, "approve", a, "--gate", "merge", script=NOPROC_CLI, env_extra={"CLAUDECODE": ""})
    check("human gate, no /proc: approve is refused the same way", blocked(r) and "has no /proc" in r.stderr, (r.returncode, r.stderr))
    check("human gate, no /proc: nothing was written, the order still waits at the merge gate",
          len(events(d)) == n2 and order_of(d, a)["status"] == "review", (len(events(d)), n2))
    src = read(CLI)
    check("source: the only environment variable cc-factory reads is CLAUDE_CONFIG_DIR (nothing turns the gate check off)",
          re.findall(r"os\.environ\.get\(\s*[\"']([A-Za-z_]+)", src) == ["CLAUDE_CONFIG_DIR"] and "os.getenv" not in src
          and re.findall(r"os\.environ\[", src) == [], re.findall(r"os\.environ\.get\(\s*[\"']([A-Za-z_]+)", src))
    check("source: cc_procs is loaded by path from this checkout, as cc-setup does", 'spec_from_file_location("cc_procs", PROCS_PATH)' in src
          and 'os.path.join(CC_TOOL_DIR, "templates", "hooks", "cc_procs.py")' in src)


# ── lane ──────────────────────────────────────────────────────────────────────
def worktrees(d):
    out = {}
    cur = None
    for line in git(d, "worktree", "list", "--porcelain").stdout.splitlines():
        if line.startswith("worktree "):
            cur = os.path.realpath(line[9:])
            out[cur] = ""
        elif line.startswith("branch ") and cur:
            out[cur] = line[7:]
    return out


def test_lane():
    d = mkrepo()
    a = new_order(d, "Lane order, with: punctuation!")
    r = cli(d, "lane", a)
    check("lane: refused before the spec gate (BLOCKED, exit 2, says to approve)", blocked(r) and ("cc-factory approve %s" % a) in r.stderr, (r.returncode, r.stderr))
    check("lane: a refused lane creates no branch or worktree", len(worktrees(d)) == 1 and not os.path.exists(os.path.join(d, ".claude", "worktrees")))
    cli(d, "approve", a)
    head = git(d, "rev-parse", "HEAD").stdout.strip()
    r = cli(d, "lane", a)
    slug = F.slugify("Lane order, with: punctuation!")
    wt = os.path.join(d, ".claude", "worktrees", "%s-%s" % (a, slug))
    branch = "factory/%s-%s" % (a, slug)
    check("lane: exit 0 and the last stdout line is the worktree path", r.returncode == 0 and r.stdout.rstrip("\n").splitlines()[-1] == wt, (r.stdout, r.stderr))
    wts = worktrees(d)
    check("lane: git worktree list shows the new worktree on branch factory/FO-001-<slug>", wts.get(wt) == "refs/heads/" + branch, wts)
    check("lane: the worktree is a checkout of the base", os.path.isdir(wt) and git(wt, "rev-parse", "HEAD").stdout.strip() == head)
    lanes = [e for e in events(d, a) if e["event"] == "lane"]
    check("lane: one lane event with branch, worktree and base sha", len(lanes) == 1 and lanes[0]["branch"] == branch and lanes[0]["worktree"] == wt
          and lanes[0]["base"] == head, lanes)
    check("lane: the fold carries the lane", order_of(d, a)["lane"] == {"branch": branch, "worktree": wt}, order_of(d, a))
    st = git(d, "status", "--porcelain", "-uall").stdout
    check("lane: the lane directory does not show up as untracked (it is ignored)", ".claude/worktrees" not in st and ".factory-holdout" not in st, st)
    r2 = cli(d, "lane", a)
    check("lane again: reuses the worktree, same last line, no second lane event", r2.returncode == 0 and r2.stdout.rstrip("\n").splitlines()[-1] == wt
          and "reused" in r2.stdout and len([e for e in events(d, a) if e["event"] == "lane"]) == 1, (r2.stdout, r2.stderr))
    git(d, "commit", "-q", "--allow-empty", "-m", "main moved on")
    r3 = cli(d, "lane", a)
    check("lane again after main moved: still the same lane, the recorded base is kept, no new event", r3.returncode == 0
          and len([e for e in events(d, a) if e["event"] == "lane"]) == 1 and order_of(d, a)["lane"]["branch"] == branch, (r3.stdout, r3.stderr))
    r = cli(d, "lane", a, "--json")
    j = json.loads(r.stdout)
    check("lane --json: order, branch, worktree, base, created false", j == {"order": a, "branch": branch, "worktree": wt, "base": head, "created": False}, r.stdout)

    # From inside the worktree: the same ledger, seen from the main checkout.
    r = cli(wt, "mark", a, "building", "--station", "build", "--state", "start", "--note", "from the lane", cwd=wt)
    check("lane: `cc-factory -C <worktree> mark` works", r.returncode == 0, (r.stdout, r.stderr))
    e = events(d, a)[-1]
    check("lane: the event landed in the main checkout's ledger", e.get("note") == "from the lane" and e["station"] == "build", e)
    check("lane: nothing was written to a ledger of the worktree's own", not os.path.exists(os.path.join(wt, ".git", "cc_tool")) and os.path.isfile(os.path.join(wt, ".git")))
    check("lane: the main checkout sees the order building at build", order_of(d, a)["status"] == "building" and order_of(d, a)["station"] == "build")
    r = cli(None, "list", cwd=wt)
    check("lane: `list` from inside the worktree shows the same orders (read from the main checkout)", a in r.stdout and "building" in r.stdout, (r.stdout, r.stderr))
    r = cli(wt, "lane", a, cwd=wt)
    check("lane: run from the worktree it finds the same lane", r.returncode == 0 and r.stdout.rstrip("\n").splitlines()[-1] == wt, (r.stdout, r.stderr))
    r = cli(None, "init", cwd=wt)
    check("init from a worktree scaffolds the main checkout, not the worktree", r.returncode == 0 and "main checkout" in r.stdout
          and not os.path.exists(os.path.join(wt, "factory", "README.md")) or os.path.isfile(os.path.join(wt, "factory", "README.md")), (r.stdout, r.stderr))

    # --base, refs, odd input.
    b = new_order(d, "Based order")
    cli(d, "approve", b)
    older = git(d, "rev-parse", "HEAD~1").stdout.strip()
    r = cli(d, "lane", b, "--base", "HEAD~1")
    lane_ev = [e for e in events(d, b) if e["event"] == "lane"]
    check("lane --base REF: resolved to a sha and recorded", r.returncode == 0 and lane_ev and lane_ev[0]["base"] == older
          and git(os.path.join(d, ".claude", "worktrees", "%s-based-order" % b), "rev-parse", "HEAD").stdout.strip() == older, (r.stderr, lane_ev))
    c = new_order(d, "Bad base")
    cli(d, "approve", c)
    r = cli(d, "lane", c, "--base", "no-such-ref")
    check("lane --base with an unknown ref: BLOCKED, nothing created", blocked(r) and not os.path.exists(os.path.join(d, ".claude", "worktrees", "%s-bad-base" % c)), (r.returncode, r.stderr))
    r = cli(d, "lane", c, "--base", "--upload-pack=touch /tmp/x")
    check("lane --base starting with a dash: BLOCKED (never handed to git as an option)", blocked(r) or r.returncode == 1, (r.returncode, r.stderr))
    # An existing branch with no worktree is attached.
    e1 = new_order(d, "Has branch")
    cli(d, "approve", e1)
    git(d, "branch", "factory/%s-has-branch" % e1)
    r = cli(d, "lane", e1)
    check("lane: an existing branch without a worktree gets one", r.returncode == 0 and worktrees(d).get(os.path.join(d, ".claude", "worktrees", "%s-has-branch" % e1)) == "refs/heads/factory/%s-has-branch" % e1, (r.stdout, r.stderr))
    # A path in the way.
    f1 = new_order(d, "In the way")
    cli(d, "approve", f1)
    os.makedirs(os.path.join(d, ".claude", "worktrees", "%s-in-the-way" % f1))
    r = cli(d, "lane", f1)
    check("lane: a directory in the way is BLOCKED, not clobbered", blocked(r), (r.returncode, r.stderr))
    # Terminal orders have no lane.
    g1 = new_order(d, "Finished")
    finish(d, g1)
    check("lane: a done order is refused", blocked(cli(d, "lane", g1)))
    h1 = new_order(d, "Turned down")
    cli(d, "reject", h1)
    check("lane: a rejected order is refused", blocked(cli(d, "lane", h1)))
    for st in ("building", "verifying", "review", "failed", "blocked"):
        k = new_order(d, "status %s" % st)
        cli(d, "approve", k)
        if st == "review":
            force_review(d, k)
        else:
            cli(d, "mark", k, st)
        r = cli(d, "lane", k)
        check("lane: allowed from status %s after the spec gate" % st, r.returncode == 0, (r.returncode, r.stderr))
    # The spec gate is the gate event, not the status: mark and the frontmatter cannot open it.
    for st in ("building", "failed", "blocked", "review"):
        k = new_order(d, "unapproved %s" % st)
        if st == "review":
            force_review(d, k)
        else:
            cli(d, "mark", k, st)
        r = cli(d, "lane", k)
        check("lane: refused from status %s set by mark with no spec approval (BLOCKED, says approve)" % st,
              blocked(r) and ("cc-factory approve %s" % k) in r.stderr and "approval" in r.stderr
              and not [e for e in events(d, k) if e["event"] == "lane"], (r.returncode, r.stderr))
    fm = os.path.join(d, "factory", "orders", "FO-090-from-a-pr.md")
    write(fm, "---\nid: FO-090\ntitle: Innocent change\nstatus: approved\n---\n## Intent\nx\n")
    write(os.path.join(d, "factory", "orders", "FO-091-merge-me.md"), "---\ntitle: Never ran\nstatus: review\n---\n## Intent\nx\n")
    check("frontmatter: an order file saying approved folds to queued", order_of(d, "FO-090")["status"] == "queued", order_of(d, "FO-090"))
    check("frontmatter: an order file saying review has no merge gate", order_of(d, "FO-091")["status"] == "queued" and order_of(d, "FO-091")["gate"] is None)
    r = cli(d, "lane", "FO-090")
    check("frontmatter: lane refuses an order whose file says approved", blocked(r) and not os.path.exists(os.path.join(d, ".claude", "worktrees", "FO-090-innocent-change")), (r.returncode, r.stderr))
    r = cli(d, "approve", "FO-091", "--gate", "merge")
    check("frontmatter: approve --gate merge refuses an order whose file says review (it never ran)", blocked(r), (r.returncode, r.stderr))
    r = cli(d, "approve", "FO-090")
    check("frontmatter: after a real approve the lane is made", r.returncode == 0 and cli(d, "lane", "FO-090").returncode == 0)
    # A line with an automatic spec gate does not need the approval.
    cfg = json.loads(read(os.path.join(d, "factory", "line.json")))
    cfg["lines"]["fast"] = {"stations": [{"name": "build", "title": "Build", "model": "sonnet"}], "gates": {"spec": "auto"}}
    write(os.path.join(d, "factory", "line.json"), json.dumps(cfg))
    m = new_order(d, "Auto gate", "--line", "fast")
    r = cli(d, "lane", m)
    check("lane: a line whose spec gate is auto needs no approval", r.returncode == 0, (r.returncode, r.stderr))
    # GIT_DIR in the caller's environment does not redirect git.
    other = mkrepo("decoy", init=False)
    n = new_order(d, "Env order")
    cli(d, "approve", n)
    r = cli(d, "lane", n, env_extra={"GIT_DIR": os.path.join(other, ".git"), "GIT_WORK_TREE": other, "GIT_INDEX_FILE": os.path.join(other, ".git", "index")})
    check("lane: GIT_DIR / GIT_WORK_TREE / GIT_INDEX_FILE in the environment are ignored (the lane is in the right repository)",
          r.returncode == 0 and ("refs/heads/factory/%s-env-order" % n) in worktrees(d).values()
          and not git(other, "branch", "--list", "factory/*").stdout.strip(), (r.stdout, r.stderr))

    # Scenarios tracked in git would be checked out into the lane: refused before anything is made.
    t = mkrepo("tracked")
    write(os.path.join(t, ".factory-holdout", "FO-001", "S01-secret.md"), "SECRET SCENARIO\n")
    git(t, "add", "-f", ".factory-holdout")
    git(t, "commit", "-q", "-m", "scenarios committed by mistake")
    a = new_order(t, "Tracked holdout")
    cli(t, "approve", a)
    r = cli(t, "lane", a)
    check("lane: refused when the base commit tracks files under .factory-holdout/ (BLOCKED, says how to untrack)",
          blocked(r) and "tracked" in r.stderr and "git rm -r --cached .factory-holdout" in r.stderr
          and len(worktrees(t)) == 1 and not [e for e in events(t, a) if e["event"] == "lane"], (r.returncode, r.stderr))
    r = cli(t, "holdout", a)
    check("holdout: the tracked case says tracked, not 'not git-ignored'", blocked(r) and "tracked" in r.stderr
          and "not git-ignored" not in r.stderr and "git rm -r --cached .factory-holdout" in r.stderr, (r.returncode, r.stderr))
    git(t, "rm", "-r", "-q", "--cached", ".factory-holdout")
    git(t, "commit", "-q", "-m", "untrack the scenarios")
    r = cli(t, "lane", a)
    wt = r.stdout.rstrip("\n").splitlines()[-1] if r.returncode == 0 else ""
    check("lane: after untracking, the lane is made and holds no scenario", r.returncode == 0 and os.path.isdir(wt)
          and not os.path.exists(os.path.join(wt, ".factory-holdout")), (r.returncode, r.stderr))
    check("holdout: after untracking, holdout works again", cli(t, "holdout", a).returncode == 0)

    # A repository path with two spaces in a row: the lane is recorded exactly, and run-args finds it.
    _counter[0] += 1
    sp = os.path.join(BASE, "two  spaces-%d" % _counter[0])
    os.makedirs(sp)
    git(sp, "init", "-q")
    git(sp, "commit", "-q", "--allow-empty", "-m", "initial")
    cli(sp, "init")
    a = new_order(sp, "Spaced out")
    cli(sp, "approve", a)
    r = cli(sp, "lane", a)
    wt = os.path.join(sp, ".claude", "worktrees", "%s-spaced-out" % a)
    lanes = [e for e in events(sp, a) if e["event"] == "lane"]
    check("lane: a repo path with two spaces in a row works and the ledger keeps the exact path",
          r.returncode == 0 and r.stdout.rstrip("\n").splitlines()[-1] == wt and lanes and lanes[-1]["worktree"] == wt
          and order_of(sp, a)["lane"]["worktree"] == wt, (r.returncode, r.stderr, lanes))
    r2 = cli(sp, "run-args", a)
    check("run-args: finds the lane under a path with two spaces", r2.returncode == 0 and json.loads(r2.stdout)["args"]["worktree"] == wt, (r2.returncode, r2.stderr))
    # A path the ledger cannot hold refuses before git creates anything.
    _counter[0] += 1
    tab = os.path.join(BASE, "tab\there-%d" % _counter[0])
    os.makedirs(tab)
    git(tab, "init", "-q")
    git(tab, "commit", "-q", "--allow-empty", "-m", "initial")
    cli(tab, "init")
    a = new_order(tab, "Tabbed")
    cli(tab, "approve", a)
    r = cli(tab, "lane", a)
    check("lane: a worktree path the ledger cannot record (a tab) is refused before git worktree add",
          blocked(r) and len(worktrees(tab)) == 1 and not os.path.exists(os.path.join(tab, ".claude", "worktrees", "%s-tabbed" % a))
          and not git(tab, "branch", "--list", "factory/*").stdout.strip(), (r.returncode, r.stderr))


# ── holdout ───────────────────────────────────────────────────────────────────
def test_holdout():
    d = mkrepo()
    a = new_order(d, "Holdout order")
    r = cli(d, "holdout", a)
    top = os.path.join(d, ".factory-holdout")
    hd = os.path.join(top, a)
    check("holdout: exit 0, the last stdout line is the directory", r.returncode == 0 and r.stdout.rstrip("\n").splitlines()[-1] == hd, (r.stdout, r.stderr))
    check("holdout: both levels exist at 0700", os.path.isdir(hd) and mode(top) == 0o700 and mode(hd) == 0o700, (oct(mode(top)), oct(mode(hd))))
    check("holdout: git ignores it", git(d, "check-ignore", "-q", "--", ".factory-holdout/%s/s1.md" % a, rc=None).returncode == 0)
    os.chmod(hd, 0o755)
    os.chmod(top, 0o755)
    r = cli(d, "holdout", a)
    check("holdout again: loose modes are tightened to 0700", r.returncode == 0 and mode(top) == 0o700 and mode(hd) == 0o700, (oct(mode(top)), oct(mode(hd))))
    write(os.path.join(hd, "s1.md"), "scenario one\n")
    git(d, "add", "-A")
    check("holdout: scenarios are never staged by `git add -A`", ".factory-holdout" not in git(d, "ls-files").stdout)
    r = cli(d, "holdout", "FO-050")
    check("holdout: an unknown order is exit 1", r.returncode == 1, (r.returncode, r.stderr))
    # Not ignored: refuse before creating anything.
    d2 = mkrepo()
    b = new_order(d2, "Unignored")
    write(os.path.join(d2, ".gitignore"), "# nothing here\n")
    r = cli(d2, "holdout", b)
    check("holdout: refused when .factory-holdout/ is not git-ignored (BLOCKED, exit 2, points at init)", blocked(r) and "init" in r.stderr, (r.returncode, r.stderr))
    check("holdout: a refused holdout creates nothing", not os.path.exists(os.path.join(d2, ".factory-holdout")))
    # A symlink in the way is not followed.
    d3 = mkrepo()
    c = new_order(d3, "Symlinked")
    elsewhere = os.path.join(BASE, "holdout-elsewhere")
    os.makedirs(elsewhere)
    os.symlink(elsewhere, os.path.join(d3, ".factory-holdout"))
    r = cli(d3, "holdout", c)
    check("holdout: a symlinked .factory-holdout is BLOCKED and its target stays empty", blocked(r) and os.listdir(elsewhere) == [], (r.returncode, r.stderr, os.listdir(elsewhere)))


# ── run-args ──────────────────────────────────────────────────────────────────
def test_run_args():
    d = mkrepo()
    a = new_order(d, "Args order")
    r = cli(d, "run-args", a)
    check("run-args: refused without a lane (BLOCKED, exit 2, says to run lane)", blocked(r) and ("cc-factory lane %s" % a) in r.stderr, (r.returncode, r.stderr))
    cli(d, "approve", a)
    cli(d, "lane", a)
    script = os.path.join(d, ".claude", "workflows", "factory-line.js")
    real_js = os.path.join(REPO, ".claude", "workflows", "factory-line.js")
    if os.path.lexists(script):
        os.unlink(script)  # init copied one; test the fallback first
    r = cli(d, "run-args", a)
    if os.path.isfile(real_js):
        check("run-args: with no project copy, scriptPath is cc_tool's factory-line.js", r.returncode == 0 and json.loads(r.stdout)["scriptPath"] == real_js, (r.stdout[:300], r.stderr))
    else:
        check("run-args: with no factory-line.js anywhere it is refused", blocked(r), (r.returncode, r.stderr))
    write(script, "// project copy\n")
    r = cli(d, "run-args", a)
    check("run-args: exit 0", r.returncode == 0, (r.stdout, r.stderr))
    j = json.loads(r.stdout)
    args = j["args"]
    lane = order_of(d, a)["lane"]
    head = git(d, "rev-parse", "HEAD").stdout.strip()
    check("run-args: name and scriptPath (the project's copy wins)", j["name"] == "factory-line" and j["scriptPath"] == script and set(j) == {"name", "scriptPath", "args"}, j.get("scriptPath"))
    check("run-args: args has exactly the contract keys (threshold: the line's, default 1)", set(args) == {
        "order", "title", "spec", "root", "worktree", "branch", "base", "holdout", "stations", "retries", "line", "threshold",
        "planDoc", "orderPath"} and args["threshold"] == 1, sorted(args))
    check("run-args: planDoc and orderPath follow contract A (created date, lower-case id, the order file's slug)",
          args["orderPath"] == "factory/orders/%s-args-order.md" % a
          and args["planDoc"] == "docs/superpowers/plans/%s-%s-args-order.md" % (time.strftime("%Y-%m-%d"), a.lower()), (args["planDoc"], args["orderPath"]))
    check("run-args: order, title, root, worktree, branch, base, holdout, retries, line", args["order"] == a and args["title"] == "Args order" and args["root"] == d
          and args["worktree"] == lane["worktree"] and args["branch"] == lane["branch"] and args["base"] == head
          and args["holdout"] == os.path.join(d, ".factory-holdout", a) and args["retries"] == 1 and args["line"] == "default", args)
    check("run-args: spec is the order body without the frontmatter", args["spec"].startswith("## Intent") and "id: " not in args["spec"] and "---" not in args["spec"].splitlines()[0], args["spec"][:80])
    check("run-args: stations are the line's, with models", [s["name"] for s in args["stations"]] == ["plan", "build", "test", "verify", "review"]
          and args["stations"][1]["model"] == "sonnet" and args["stations"][3]["holdout"] is True, args["stations"])
    check("run-args: one line of JSON", len(r.stdout.strip().splitlines()) == 1)
    os.unlink(script)
    os.symlink(os.path.join(BASE, "evil.js"), script)
    r = cli(d, "run-args", a)
    if os.path.isfile(real_js):
        check("run-args: a symlinked project copy is not used", r.returncode == 0 and json.loads(r.stdout)["scriptPath"] == real_js, r.stdout[:200])
    os.unlink(script)
    # A tampered ledger cannot point the run at an arbitrary directory.
    write(script, "// project copy\n")
    b = new_order(d, "Tampered")
    cli(d, "approve", b)
    with open(ledger_file(d), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"t": time.time(), "order": b, "event": "lane", "branch": "factory/x", "worktree": "/tmp", "base": "abcd1234"}) + "\n")
    r = cli(d, "run-args", b)
    check("run-args: a lane that is not a registered worktree is refused", blocked(r), (r.returncode, r.stderr, r.stdout[:200]))
    # A lane whose HEAD tracks scenarios (committed in the lane with add -f) is refused.
    wt_a = order_of(d, a)["lane"]["worktree"]
    write(os.path.join(wt_a, ".factory-holdout", a, "S01-leak.md"), "LEAKED\n")
    git(wt_a, "add", "-f", ".factory-holdout")
    git(wt_a, "commit", "-q", "-m", "%s: oops" % a)
    r = cli(d, "run-args", a)
    check("run-args: refused when the lane's HEAD tracks files under .factory-holdout/ (BLOCKED, says how to remove them)",
          blocked(r) and "tracked" in r.stderr and "rm -r" in r.stderr and not r.stdout.strip(), (r.returncode, r.stderr))
    git(wt_a, "rm", "-r", "-q", ".factory-holdout")
    git(wt_a, "commit", "-q", "-m", "%s: remove" % a)
    check("run-args: accepted again once the lane no longer tracks them", cli(d, "run-args", a).returncode == 0)
    # A lane with no spec approval (made by hand, then marked building) gets no run.
    u = new_order(d, "Unapproved lane")
    uwt = os.path.join(d, ".claude", "worktrees", "%s-by-hand" % u)
    git(d, "worktree", "add", "-q", "-b", "factory/%s-by-hand" % u, uwt)
    with open(ledger_file(d), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"t": time.time(), "order": u, "event": "lane", "branch": "factory/%s-by-hand" % u,
                             "worktree": os.path.realpath(uwt), "base": head, "by": "cc-factory"}) + "\n")
    cli(d, "mark", u, "building")
    r = cli(d, "run-args", u)
    check("run-args: refused without the spec gate's pass in the ledger, even with a lane and status building (BLOCKED, says approve)",
          blocked(r) and ("cc-factory approve %s" % u) in r.stderr and not r.stdout.strip(), (r.returncode, r.stderr))
    cli(d, "approve", u, "--gate", "spec")
    check("run-args: accepted after the approval", cli(d, "run-args", u).returncode == 0)
    # Machine JSON is ASCII-only and round-trips a non-ASCII title.
    n = new_order(d, "Café export ✓")
    cli(d, "approve", n)
    cli(d, "lane", n)
    r = cli(d, "run-args", n)
    check("run-args: ASCII-only JSON that parses back to the exact title", r.returncode == 0 and r.stdout.isascii()
          and json.loads(r.stdout)["args"]["title"] == "Café export ✓", r.stdout[:200])
    # Terminal orders.
    finish(d, a)
    check("run-args: a done order is refused", blocked(cli(d, "run-args", a)))
    # The worktree is gone.
    c = new_order(d, "Vanished")
    cli(d, "approve", c)
    lane_out = cli(d, "lane", c).stdout.strip().splitlines()[-1]
    shutil.rmtree(lane_out)
    r = cli(d, "run-args", c)
    check("run-args: a missing worktree directory is refused", blocked(r), (r.returncode, r.stderr))
    r = cli(d, "lane", c)
    check("lane: a registered but missing worktree is BLOCKED with the prune hint", blocked(r) and "prune" in r.stderr, (r.returncode, r.stderr))


def set_order_line(repo, oid, line):
    """What an agent with write access does: edit the `line:` of an order file."""
    o = order_of(repo, oid)
    p = os.path.join(repo, o["path"])
    text = read(p)
    if re.search(r"(?m)^line:", text):
        new = re.sub(r"(?m)^line:.*$", "line: %s" % line, text, count=1)
    else:   # the shipped template writes no line: key, so an agent adds one
        new = re.sub(r"(?m)^(created:.*)$", r"\1\nline: %s" % line, text, count=1)
    check("pin setup: the edit changed the order file", new != text or ("line: %s" % line) in text, text[:200])
    write(p, new)


def test_quick_and_pin():
    # The quick line: from the shipped template and from the built-in default, the same shape.
    tpl = json.loads(read(os.path.join(REPO, "templates", "factory", "line.json")))["lines"]["quick"]
    d = mkrepo()
    cfg = F.line_config(d)
    q = cfg["lines"]["quick"]
    for src, line in (("template", F.line_config(d)["lines"]["quick"]), ("built-in default", F.DEFAULT_LINE["lines"]["quick"])):
        check("quick (%s): plan, build, test, review; spec auto, merge human; 1 retry" % src,
              [s["name"] for s in line["stations"]] == ["plan", "build", "test", "review"] and line["gates"] == {"spec": "auto", "merge": "human"}
              and line["retries"] == 1 and not any(s.get("holdout") for s in line["stations"]), line)
    dflt = cfg["lines"]["default"]["stations"]
    check("quick: same models and efforts as the default line",
          all({k: s.get(k) for k in ("model", "effort")} == {k: next(x for x in dflt if x["name"] == s["name"]).get(k) for k in ("model", "effort")}
              for s in q["stations"]) and [s["model"] for s in tpl["stations"]] == [s["model"] for s in q["stations"]], q["stations"])
    # Without a line.json the built-in default serves the quick line too.
    os.unlink(os.path.join(d, "factory", "line.json"))
    check("quick: the built-in default has it when factory/line.json is gone", "quick" in F.line_config(d)["lines"])
    write(os.path.join(d, "factory", "line.json"), json.dumps({"version": 1, "lines": json.loads(read(os.path.join(REPO, "templates", "factory", "line.json")))["lines"]}))

    # A quick order: pinned in the created event, no approval needed, merge on a no-verify line.
    qo = new_order(d, "Quick change", "--line", "quick")
    created = next(e for e in events(d, qo) if e["event"] == "created")
    check("pin: the created event stores the line", created.get("line") == "quick", created)
    check("pin: a default order's created event stores 'default'", next(e for e in events(d, new_order(d, "Plain")) if e["event"] == "created").get("line") == "default")
    wt, branch = lane_with_commit(d, qo)
    hist = [{"station": "plan", "state": s, "attempt": 1, "note": ""} for s in ("start", "pass")]
    hist += [{"station": k, "state": s, "attempt": 1, "note": ""} for k in ("build", "test", "review") for s in ("start", "pass")]
    r = record_saved(d, qo, v2_result(qo, order_of(d, qo)["lane"], ids=(), history=hist) | {"holdout": None})
    last = events(d, qo)[-1]
    check("quick: a passing run with no holdout records review with recommendation merge, line named in the briefing",
          r.returncode == 0 and last["status"] == "review" and last["recommendation"] == "merge" and "Line: quick." in last.get("briefing", "")
          and "no hidden scenarios by design" in last["briefing"], (r.returncode, r.stderr, last))

    # The downgrade: an approved default order whose file is edited to `line: quick`.
    a = new_order(d, "Pinned order")
    scenarios(d, a)
    cli(d, "mark", a, "spec")
    cli(d, "approve", a)
    cli(d, "lane", a)
    ok_before = cli(d, "run-args", a)
    check("pin: run-args works while the line is unchanged", ok_before.returncode == 0, ok_before.stderr)
    set_order_line(d, a, "quick")
    n_ev = len(events(d))
    for what, r in (("lane", cli(d, "lane", a)), ("run-args", cli(d, "run-args", a)),
                    ("record", cli(d, "record", a, stdin=json.dumps(v2_result(a, order_of(d, a)["lane"]))))):
        check("pin: %s is refused after the order's line is flipped to quick" % what,
              blocked(r) and "created on the default line" in r.stderr and "quick" in r.stderr and not r.stdout.strip(), (r.returncode, r.stderr, r.stdout[:200]))
    check("pin: a refused flip writes nothing to the ledger", len(events(d)) == n_ev, len(events(d)) - n_ev)
    # Flipping to a line that does not exist is a change too: the name is pinned, whatever approval exists.
    set_order_line(d, a, "nope")
    for what, r in (("run-args", cli(d, "run-args", a)), ("lane", cli(d, "lane", a))):
        check("pin: an unknown line name on an approved default order is refused by %s" % what,
              blocked(r) and "created on the default line" in r.stderr, (r.returncode, r.stderr))
    write(os.path.join(d, order_of(d, a)["path"]), re.sub(r"(?m)^line:.*\n", "", read(os.path.join(d, order_of(d, a)["path"]))))
    check("pin: removing the key reads as default, the pinned line, so it runs", cli(d, "run-args", a).returncode == 0)
    set_order_line(d, a, "default")
    check("pin: restoring the line lets run-args through again", cli(d, "run-args", a).returncode == 0)
    # Flip before approval: approve itself refuses, so the spec gate never blesses the flipped line, and lane stays refused.
    fl = new_order(d, "Flipped before approval")
    scenarios(d, fl)
    cli(d, "mark", fl, "spec")
    set_order_line(d, fl, "quick")
    n_ev = len(events(d))
    r = cli(d, "approve", fl)
    check("pin: approve refuses an order whose line was flipped before approval, naming the creation line",
          blocked(r) and "created on the default line" in r.stderr and "quick" in r.stderr and not r.stdout.strip(), (r.returncode, r.stderr, r.stdout[:200]))
    check("pin: ...and writes no spec-gate pass", not any(e.get("event") == "gate" and e.get("state") == "pass" for e in events(d, fl)) and len(events(d)) == n_ev)
    r = cli(d, "lane", fl)
    check("pin: lane stays refused for the flipped, unapproved order", blocked(r) and "created on the default line" in r.stderr, (r.returncode, r.stderr))
    set_order_line(d, fl, "default")
    # A quick order flipped UP to default is refused too (the pin is the creation line, whichever way).
    set_order_line(d, qo, "default")
    r = cli(d, "run-args", qo)
    check("pin: a quick order edited to default is refused as well", blocked(r) and "created on the quick line" in r.stderr, (r.returncode, r.stderr))
    set_order_line(d, qo, "quick")

    def strip_pin(oid):
        """Make the order look like one created before the pin: no line, no line hash on its created event."""
        out = []
        for ln in read(ledger_file(d)).splitlines():
            e = json.loads(ln)
            if e.get("order") == oid and e.get("event") == "created":
                e.pop("line", None)
                e.pop("line_hash", None)
            out.append(json.dumps(e))
        write(ledger_file(d), "\n".join(out) + "\n")

    def refused_everywhere(label, oid, needle):
        res = (("lane", cli(d, "lane", oid)), ("run-args", cli(d, "run-args", oid)),
               ("record", cli(d, "record", oid, stdin=json.dumps(v2_result(oid, order_of(d, oid).get("lane") or {"branch": "x", "worktree": "/x"})))))
        for what, r in res:
            check("pin: %s: %s is refused (BLOCKED, %s)" % (label, what, needle), blocked(r) and needle in r.stderr and not r.stdout.strip(),
                  (r.returncode, r.stderr, r.stdout[:200]))

    def edit_line_json(fn):
        path = os.path.join(d, "factory", "line.json")
        cfg_ = json.loads(read(path))
        fn(cfg_["lines"])
        write(path, json.dumps(cfg_))

    # The line's definition is pinned with its name: an approved default order, then factory/line.json is rewritten.
    spec_auto = new_order(d, "Spec gate weakened")
    scenarios(d, spec_auto)
    cli(d, "mark", spec_auto, "spec")
    cli(d, "approve", spec_auto)
    cli(d, "lane", spec_auto)
    saved = read(os.path.join(d, "factory", "line.json"))
    edit_line_json(lambda ls: ls["default"].__setitem__("gates", {"spec": "auto", "merge": "human"}))
    refused_everywhere("gates.spec set to auto on default after approval", spec_auto, "file a new order")
    write(os.path.join(d, "factory", "line.json"), saved)
    check("pin: restoring line.json lets the order through again", cli(d, "run-args", spec_auto).returncode == 0)
    edit_line_json(lambda ls: ls["default"].__setitem__("stations", [s for s in ls["default"]["stations"] if s["name"] != "verify"]))
    refused_everywhere("verify dropped from the line after approval", spec_auto, "file a new order")
    write(os.path.join(d, "factory", "line.json"), saved)

    # A pre-pin order (no line hash): only the stored spec hash protects it.
    strip_pin(spec_auto)
    edit_line_json(lambda ls: ls["default"].__setitem__("gates", {"spec": "auto", "merge": "human"}))
    refused_everywhere("pre-pin approved order, gates.spec set to auto", spec_auto, "file a new order")
    write(os.path.join(d, "factory", "line.json"), saved)
    check("pin: a pre-pin approved order whose hash still matches runs", cli(d, "run-args", spec_auto).returncode == 0)

    # A pre-pin order that was approved cannot be flipped to quick.
    old_ap = new_order(d, "Old approved")
    strip_pin(old_ap)
    scenarios(d, old_ap)
    cli(d, "mark", old_ap, "spec")
    cli(d, "approve", old_ap)
    cli(d, "lane", old_ap)
    check("pin: the pre-pin approved order runs while untouched", cli(d, "run-args", old_ap).returncode == 0)
    set_order_line(d, old_ap, "quick")
    refused_everywhere("pre-pin approved order flipped to quick", old_ap, "file a new order")

    # A quick order whose definition changed after creation.
    edit_line_json(lambda ls: ls["quick"]["stations"][1].__setitem__("model", "opus"))
    refused_everywhere("quick order, line definition changed after creation", qo, "file a new order")
    write(os.path.join(d, "factory", "line.json"), saved)
    check("pin: ...and runs again once the definition is restored", cli(d, "run-args", qo).returncode == 0)

    # Fail closed: no exemption for an order from before the pin, approved or not.
    old = new_order(d, "Before the pin")
    strip_pin(old)
    check("pin: the old order's created event has no line", "line" not in next(e for e in events(d, old) if e["event"] == "created"))
    refused_everywhere("old never-approved order, no pin", old, "file a new order")
    set_order_line(d, old, "quick")
    refused_everywhere("old never-approved order flipped to quick", old, "file a new order")

    # A pinned line with no line hash is refused.
    nohash = new_order(d, "Pin without hash")
    strip_pin(nohash)
    lines_ = []
    for ln in read(ledger_file(d)).splitlines():
        e = json.loads(ln)
        if e.get("order") == nohash and e.get("event") == "created":
            e["line"] = "default"
        lines_.append(json.dumps(e))
    write(ledger_file(d), "\n".join(lines_) + "\n")
    refused_everywhere("line pinned without a line hash", nohash, "file a new order")

    # No created event at all (fell out of the ledger tail): refused unless a spec pass still matches.
    def drop_created(oid):
        keep = [ln for ln in read(ledger_file(d)).splitlines()
                if not (json.loads(ln).get("order") == oid and json.loads(ln).get("event") == "created")]
        write(ledger_file(d), "\n".join(keep) + "\n")

    nocreated = new_order(d, "Created event gone")
    drop_created(nocreated)
    refused_everywhere("no created event, never approved", nocreated, "file a new order")
    gone_ap = new_order(d, "Created gone but approved")
    scenarios(d, gone_ap)
    cli(d, "mark", gone_ap, "spec")
    cli(d, "approve", gone_ap)
    cli(d, "lane", gone_ap)
    drop_created(gone_ap)
    check("pin: no created event but a matching spec-gate pass: still runs", cli(d, "run-args", gone_ap).returncode == 0)
    # The ledger library keeps a valid line and drops a bad one.
    ev = F.valid_event({"t": 1.0, "order": "FO-001", "event": "created", "line": "quick"})
    bad = F.valid_event({"t": 1.0, "order": "FO-001", "event": "created", "line": "../x; rm"})
    check("pin: valid_event keeps a line name and drops a malformed one", ev.get("line") == "quick" and "line" not in bad, (ev, bad))


# ── record ────────────────────────────────────────────────────────────────────
def v2_history(attempts=1, fail_at="verify"):
    """A contract-2 history: plan once in attempt 1, then build, test, verify, review per attempt; every
    attempt before the last stops at a fail of `fail_at`."""
    h = [{"station": "plan", "state": "start", "attempt": 1, "note": "plan"},
         {"station": "plan", "state": "pass", "attempt": 1, "note": "planned"}]
    for a in range(1, attempts + 1):
        for s in ("build", "test", "verify", "review"):
            h.append({"station": s, "state": "start", "attempt": a, "note": "%s attempt %d" % (s, a)})
            if a < attempts and s == fail_at:
                h.append({"station": s, "state": "fail", "attempt": a, "note": "%s failed" % s})
                break
            h.append({"station": s, "state": "pass", "attempt": a, "note": "%s ok" % s})
    return h


LONG_SUMMARY = "The change adds the flag and its tests. " * 50 + "END-OF-REVIEW-SUMMARY"
LONG_NEXT = "Confirm the empty-input behaviour as a product decision before merging. " * 75 + "END-OF-NEXT-STEPS"
LONG_FIX = "Tighten the assertion so it checks the printed value, not only the exit code. " * 15 + "END-OF-SUGGESTION"


def review_report(**over):
    rep = {"verdict": "pass", "summary": LONG_SUMMARY, "meetsAcceptanceCriteria": True, "uncommittedChanges": False,
           "issues": [{"severity": "should-fix", "location": "tests/test_cli.py:40", "problem": "the assertion is weak",
                       "showsFailure": "make the function return 0 and the test still passes", "suggestion": LONG_FIX},
                      {"severity": "nit", "location": "README.md:12", "problem": "an example uses other counts",
                       "showsFailure": "", "suggestion": "update the example"}],
           "nextSteps": LONG_NEXT, "testRun": {"ran": True, "passed": True, "command": "make test",
                                               "output": "\n".join("test_%02d PASSED" % i for i in range(25)) + "\n25 passed"}}
    rep.update(over)
    return rep


def v2_result(oid, lane, attempts=1, ids=("S01", "S02", "S03"), satisfied=None, holdout=None, **over):
    """A passing contract-2 result for the order's lane (status review, recommendation merge)."""
    sat = list(satisfied) if satisfied is not None else [True] * len(ids)
    n, ok = len(ids), sum(1 for x in sat if x)
    res = {"contract": 2, "order": oid, "title": "t", "branch": lane["branch"], "worktree": lane["worktree"], "base": "",
           "status": "review", "reason": "", "recommendation": "merge" if n else "needs-scenarios",
           "summary": "The line passed on attempt %d. " % attempts + "Holdout satisfaction 1.00 on the scenarios. " * 60 + "END-OF-LINE-SUMMARY",
           "satisfaction": (ok / n) if n else None, "attempts": attempts, "rework": attempts - 1, "history": v2_history(attempts),
           "stations": {"review": {"name": "review", "title": "Review", "model": "opus", "status": "done", "report": review_report()}},
           "holdout": {"given": True, "attempt": attempts, "holdoutDir": "ok" if n else "empty", "scenarioFiles": n,
                       "scenarios": [{"id": i, "satisfied": s} for i, s in zip(ids, sat)], "satisfied": ok, "total": n,
                       "satisfaction": (ok / n) if n else None, "threshold": 1, "vacuous": n == 0, "executed": bool(n), "sandbox": "ok" if n else "not-run",
                       "git": "ok" if n else "not-run"},
           "feedback": []}
    if holdout:
        res["holdout"].update(holdout)
    res.update(over)
    return res


def failed_history():
    """Retries ran out: plan once, then build passes and test fails in attempts 1 and 2."""
    h = [{"station": "plan", "state": "start", "attempt": 1, "note": "plan"}, {"station": "plan", "state": "pass", "attempt": 1, "note": "planned"}]
    for a in (1, 2):
        h += [{"station": "build", "state": "start", "attempt": a, "note": ""}, {"station": "build", "state": "pass", "attempt": a, "note": ""},
              {"station": "test", "state": "start", "attempt": a, "note": ""}, {"station": "test", "state": "fail", "attempt": a, "note": "red"}]
    return h


def failed_result(oid, **over):
    res = {"contract": 2, "order": oid, "status": "failed", "reason": "", "recommendation": "rework", "summary": "ran out of retries",
           "satisfaction": None, "attempts": 2, "rework": 1, "history": failed_history(), "stations": {}, "holdout": None,
           "feedback": []}
    res.update(over)
    return res


def ready(d, title, n_commits=1, texts=None, subject=None):
    """An order with 3 scenarios, marked spec, approved, and a lane with commits; (oid, lane dict)."""
    oid = new_order(d, title)
    scenarios(d, oid, texts)
    r = cli(d, "mark", oid, "spec")
    if r.returncode != 0:
        raise RuntimeError("mark spec failed: %s" % r.stderr)
    r = cli(d, "approve", oid)
    if r.returncode != 0:
        raise RuntimeError("approve failed: %s" % r.stderr)
    lane_with_commit(d, oid, n_commits, subject)
    return oid, order_of(d, oid)["lane"]


def test_record():
    d = mkrepo()
    a, lane = ready(d, "Recorded order", n_commits=2)
    wt = lane["worktree"]
    write(os.path.join(wt, "src", "esc.txt"), "x\n")
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", "%s: colour \x1b]0;PWNED\x07\x1b[31mred" % a)
    head = git(d, "rev-parse", "refs/heads/" + lane["branch"]).stdout.strip()
    n0 = len(events(d, a))
    res = v2_result(a, lane, attempts=2)
    r = record_saved(d, a, res, run_id="wf_rec001")
    check("record: a passing contract-2 run, exit 0", r.returncode == 0, (r.stdout[-600:], r.stderr))
    evs = events(d, a)[n0:]
    st = [e for e in evs if e["event"] == "station"]
    check("record: one station event per history entry, then one status event, all with the run id", len(st) == len(res["history"])
          and len(evs) == len(st) + 1 and evs[-1]["event"] == "status" and all(e["run_id"] == "wf_rec001" for e in evs), [e["event"] for e in evs])
    check("record: station events carry the history's attempt and note", [e.get("attempt") for e in st] == [h["attempt"] for h in res["history"]]
          and st[0]["note"] == "plan", st[:2])
    last = evs[-1]
    check("record: status review with the recommendation, the satisfaction checked and the reviewed lane head",
          last["status"] == "review" and last["recommendation"] == "merge" and last["satisfaction"] == 1.0 and last["lane_head"] == head, last)
    check("record: the status note is the summary, clipped to 500", len(last["note"]) == 500 and last["note"].startswith("The line passed"), len(last["note"]))
    o = order_of(d, a)
    check("record: verify fail + build restart folds to rework 1; review, the merge gate is waiting", o["rework"] == 1 and o["status"] == "review"
          and o["gate"] == {"name": "merge", "waiting": True} and o["run_id"] == "wf_rec001" and all(s["status"] == "done" for s in o["stations"]), o)
    out = r.stdout
    check("record briefing: git log of base..branch (every commit subject, control characters stripped)",
          ("%s: change 0" % a) in out and ("%s: change 1" % a) in out and ("%s: colour" % a) in out and "PWNED" in out
          and "\x1b" not in out and "\x07" not in out and "git log --oneline" in out and "3 commits past base" in out, out[:1500])
    check("record briefing: git diff --stat base...branch", "git diff --stat" in out and ("src/%s-0.txt" % a) in out and "3 files changed" in out, out[:2000])
    check("record briefing: satisfaction and every scenario id, satisfied or not, no reasons",
          "satisfaction 1.00 (3 of 3 scenarios satisfied, threshold 1): S01 satisfied, S02 satisfied, S03 satisfied." in out, out)
    check("record briefing: review's verdict and its whole summary (not clipped)", "Review (opus): pass. " in out and LONG_SUMMARY in out)
    check("record briefing: every should-fix item, whole (location, problem, how it shows, the fix)",
          "Should-fix (1):" in out and "tests/test_cli.py:40: the assertion is weak" in out and LONG_FIX in out and "Shows it: make the function" in out, out)
    check("record briefing: the full nextSteps (%d characters), never clipped" % len(LONG_NEXT), ("Next steps: " + LONG_NEXT) in out)
    check("record briefing: review's own observed test run (command, the output's last lines)",
          "Review's own test run: ran and passed: make test" in out and "25 passed" in out and "earlier lines of output" in out, out[-2500:])
    check("record briefing: the line's summary, whole (the old 1500-character clip is gone)", "END-OF-LINE-SUMMARY" in out)
    check("record: prints the human's next commands, the merge against the reviewed sha", ("git merge --no-ff %s" % head) in out
          and lane["branch"] in out and ("cc-factory approve %s --gate merge" % a) in out
          and ("cc-factory reject %s --gate merge --rework" % a) in out, out[-600:])
    b = last.get("briefing", "")
    check("record: the briefing is stored on the status event (its own cap, %d characters here), with the git facts and the full nextSteps"
          % len(b), len(b) > 500 and LONG_NEXT in b and ("%s: change 1" % a) in b and "S03 satisfied" in b and len(b) <= F.BRIEFING_MAX, len(b))
    r = cli(d, "show", a)
    check("show: prints the stored merge-gate briefing", r.returncode == 0 and "merge-gate briefing of run wf_rec001" in r.stdout
          and LONG_NEXT in r.stdout and "S01 satisfied" in r.stdout, r.stdout[-800:])
    j = json.loads(cli(d, "show", a, "--json").stdout)
    check("show --json: the briefing as {text, run_id, t}", j["briefing"]["run_id"] == "wf_rec001" and LONG_NEXT in j["briefing"]["text"], j.get("briefing", {}).get("run_id"))

    # Only a saved Workflow run, checked against the order's lane and spec, opens the merge gate: a passing
    # result typed on stdin or into a file records as blocked.
    for how in ("stdin", "--file"):
        u, lu = ready(d, "Unverified via %s" % how)
        n0 = len(events(d, u))
        res = json.dumps(v2_result(u, lu))
        if how == "stdin":
            r = cli(d, "record", u, stdin=res)
        else:
            rf = os.path.join(BASE, "passing-%s.json" % u)
            write(rf, res)
            r = cli(d, "record", u, "--file", rf)
        new = events(d, u)[n0:]
        check("record from %s: a passing contract-2 result is recorded as blocked, exit 2, BLOCKED says only --workflow-run opens the merge gate" % how,
              blocked(r) and "can record only failed or blocked" in r.stderr and ("cc-factory record %s --workflow-run <runId>" % u) in r.stderr
              and order_of(d, u)["status"] == "blocked" and new and new[-1]["status"] == "blocked" and "lane_head" not in new[-1]
              and order_of(d, u)["gate"] is None, (r.returncode, r.stderr, order_of(d, u)["status"]))
    # A saved run whose args are not the order's approved spec does not open it either.
    s2, ls2 = ready(d, "Saved run, stale spec")
    p2 = F.find_order(d, s2)
    ra = json.loads(cli(d, "run-args", s2).stdout)["args"]
    ra["spec"] = ra["spec"] + "\n- [ ] a criterion the approved spec does not have\n"
    _counter[0] += 1
    cfg2 = os.path.join(BASE, "claude-cfg-stale")
    pf = os.path.join(cfg2, "projects", re.sub(r"[^A-Za-z0-9]", "-", d), "00000000-0000-4000-8000-000000000001", "workflows", "wf_stale-0001.json")
    write(pf, json.dumps({"runId": "wf_stale-0001", "workflowName": "factory-line", "status": "completed", "args": ra, "result": v2_result(s2, ls2)}))
    os.chmod(pf, 0o600)
    r = cli(d, "record", s2, "--workflow-run", "wf_stale-0001", env_extra={"CLAUDE_CONFIG_DIR": cfg2})
    check("record --workflow-run: a saved run with another spec than the approved one is blocked (the spec changed since approval)",
          blocked(r) and "the run's spec is not the order's current body" in r.stderr and order_of(d, s2)["status"] == "blocked"
          and os.path.isfile(p2), (r.returncode, r.stderr))
    # Mappings of the final status for results that do not reach the merge gate.
    def final(res, **kw):
        oid = new_order(d, "Mapping %d" % len(events(d)))
        cli(d, "approve", oid)
        res = dict(res, order=oid)
        r = cli(d, "record", oid, stdin=json.dumps(res), **kw)
        return oid, r, order_of(d, oid)
    oid, r, o = final(failed_result("x"))
    check("record: status failed -> failed, recommendation rework on the event, exit 0", r.returncode == 0 and o["status"] == "failed"
          and events(d, oid)[-1]["recommendation"] == "rework" and "ran out of retries on real station failures" in r.stdout, (r.stdout, o["status"]))
    oid, r, o = final(failed_result("x", status="blocked", reason="infeasible", recommendation="reject", error="plan: infeasible: conflicting criteria",
                                    history=v2_history(1)[:2]))
    check("record: blocked / infeasible -> blocked, recommendation reject, says revise or drop", r.returncode == 0 and o["status"] == "blocked"
          and events(d, oid)[-1]["recommendation"] == "reject" and "infeasible" in r.stdout and events(d, oid)[-1]["note"] == "plan: infeasible: conflicting criteria",
          (r.stdout, o["status"]))
    oid, r, o = final(failed_result("x", status="blocked", reason="args", recommendation="rerun", error="no lane worktree (args.worktree)", history=[]))
    check("record: blocked / args -> blocked, recommendation rerun, says regenerate the args", r.returncode == 0 and o["status"] == "blocked"
          and events(d, oid)[-1]["recommendation"] == "rerun" and "cc-factory run-args" in r.stdout, (r.stdout, o["status"]))
    oid, r, o = final({"status": "review", "recommendation": "merge", "error": "worktree is missing", "history": []})
    check("record: result.error -> blocked, with the error as the note", o["status"] == "blocked" and events(d, oid)[-1]["note"] == "worktree is missing", (o["status"], r.stdout))
    # Old results (no contract 2) never open the merge gate.
    for label, res in (("status review", {"status": "review", "recommendation": "merge", "summary": "fine", "history": v2_history(1)}),
                       ("no status, recommendation merge", {"recommendation": "merge", "history": []})):
        oid, r, o = final(res)
        check("record: an old result (%s, no contract 2) is blocked, exit 2, BLOCKED names the contract; its station events are kept" % label,
              blocked(r) and "contract 2" in r.stderr and o["status"] == "blocked" and "contract 2" in events(d, oid)[-1]["note"]
              and len([e for e in events(d, oid) if e["event"] == "station"]) == len(res["history"]), (r.returncode, r.stderr, o["status"]))
    oid, r, o = final({"recommendation": "reject", "history": []})
    check("record: an old result, recommendation reject -> failed", r.returncode == 0 and o["status"] == "failed", o["status"])
    oid, r, o = final(failed_result("x", status="weird"))
    check("record: a contract-2 result with an unknown status is blocked (exit 2)", blocked(r) and "is not review, failed or blocked" in r.stderr
          and o["status"] == "blocked", (r.returncode, r.stderr))
    # History filtering: unknown stations / states are skipped, notes redacted.
    oid = new_order(d, "History filtering")
    cli(d, "approve", oid)
    res = failed_result(oid, history=[
        {"station": "deploy", "state": "start"}, {"station": "build", "state": "waiting"}, {"station": "build", "state": "bogus"},
        {"station": 5, "state": "start"}, "junk", None, {"station": "build", "state": "start", "attempt": 1, "note": "key sk-abcdefghijklmnopqrstuvwxyz012345 here"},
        {"station": "build", "state": "pass", "attempt": True}])
    n0 = len(events(d, oid))
    r = cli(d, "record", oid, stdin=json.dumps(res))
    new = events(d, oid)[n0:]
    check("record: only valid station / state pairs of the line are written (2 station events + status)", r.returncode == 0 and len(new) == 3
          and [e.get("state") for e in new[:2]] == ["start", "pass"] and "skipped" in r.stdout, (len(new), r.stdout))
    check("record: a secret in a history note is redacted; a bool attempt is not an attempt", "sk-abc" not in json.dumps(new)
          and "[redacted]" in new[0]["note"] and new[0]["attempt"] == 1 and "attempt" not in new[1], new[:2])
    oid = new_order(d, "Long history")
    cli(d, "approve", oid)
    hist = [{"station": "build", "state": "start" if i % 2 == 0 else "pass", "attempt": 1, "note": str(i)} for i in range(250)]
    n0 = len(events(d, oid))
    r = cli(d, "record", oid, stdin=json.dumps(failed_result(oid, history=hist)))
    new = events(d, oid)[n0:]
    check("record: at most 100 station events (the latest ones) plus the status", r.returncode == 0 and len(new) == 101 and new[0]["note"] == "150"
          and new[99]["note"] == "249", (len(new), new[0].get("note")))
    # Bad input.
    oid = new_order(d, "Bad input")
    cli(d, "approve", oid)
    n0 = len(events(d))
    r = cli(d, "record", oid, stdin="this is not json")
    check("record: invalid JSON -> exit 1", r.returncode == 1 and "not valid JSON" in r.stderr, (r.returncode, r.stderr))
    r = cli(d, "record", oid, stdin="[1,2,3]")
    check("record: a JSON array -> exit 1", r.returncode == 1, (r.returncode, r.stderr))
    r = cli(d, "record", oid, stdin="[" * 100000)
    check("record: absurd nesting -> exit 1, not a traceback", r.returncode == 1 and "Traceback" not in r.stderr, (r.returncode, r.stderr[-200:]))
    r = cli(d, "record", oid, stdin=json.dumps({"order": "FO-999", "status": "review", "history": []}))
    check("record: a result for another order is BLOCKED", blocked(r) and "FO-999" in r.stderr, (r.returncode, r.stderr))
    r = cli(d, "record", oid, stdin='{"a":"' + "x" * (1024 * 1024 + 10) + '"}')
    check("record: over 1 MiB is BLOCKED", blocked(r), (r.returncode, r.stderr[-200:]))
    r = cli(d, "record", oid, stdin="")
    check("record: empty input -> exit 1", r.returncode == 1, (r.returncode, r.stderr))
    r = cli(d, "record", "FO-404", stdin="{}")
    check("record: unknown order -> exit 1", r.returncode == 1, (r.returncode, r.stderr))
    check("record: refused records wrote nothing", len(events(d)) == n0, (len(events(d)), n0))
    # --file, and a string-wrapped result (the Workflow tool may hand back text).
    rf = os.path.join(BASE, "result.json")
    write(rf, json.dumps(failed_result(oid, summary="from a file")))
    r = cli(d, "record", oid, "--file", rf)
    check("record --file: reads the result from the file", r.returncode == 0 and events(d, oid)[-1]["note"] == "from a file", (r.stdout, r.stderr))
    os.symlink(rf, rf + ".lnk")
    r = cli(d, "record", oid, "--file", rf + ".lnk")
    check("record --file: a symlink is refused", blocked(r), (r.returncode, r.stderr))
    r = cli(d, "record", oid, "--file", os.path.join(BASE, "missing.json"))
    check("record --file: a missing file is refused", blocked(r), (r.returncode, r.stderr))
    r = cli(d, "record", oid, stdin=json.dumps(json.dumps(failed_result(oid, summary="double encoded"))))
    check("record: a JSON-encoded string holding the result is accepted", r.returncode == 0 and events(d, oid)[-1]["note"] == "double encoded", (r.stdout, r.stderr))
    r = cli(d, "record", oid, stdin=json.dumps({"result": failed_result(oid, summary="wrapped")}))
    check("record: a {result: {...}} wrapper is accepted", r.returncode == 0 and events(d, oid)[-1]["note"] == "wrapped", (r.stdout, r.stderr))
    r = cli(d, "approve", a, "--gate", "merge")
    check("record -> approve --gate merge before merging: BLOCKED (the lane is not merged)", blocked(r) and "is not merged" in r.stderr, (r.returncode, r.stderr))
    r = cli(d, "reject", a, "--gate", "merge", "--rework")
    check("record -> reject --rework -> approved and ready for another run", r.returncode == 0 and order_of(d, a)["status"] == "approved", (r.stderr, order_of(d, a)["status"]))


def test_spec_hash():
    d = mkrepo()
    a = new_order(d, "Hashed spec")
    r = cli(d, "approve", a)
    e = events(d, a)[-1]
    check("approve: the spec gate's event carries a sha256 of the body, the line and the threshold", r.returncode == 0
          and re.fullmatch(r"[0-9a-f]{64}", e.get("spec_hash", "")) and ("spec sha256 %s" % e["spec_hash"][:16]) in r.stdout
          and "holdout: 0 scenarios" in r.stdout and "no hidden scenario will check the work" in r.stdout, (r.stdout, e))
    cli(d, "lane", a)
    check("run-args: accepted while the spec is the approved one", cli(d, "run-args", a).returncode == 0)
    p = F.find_order(d, a)
    write(p, read(p) + "\n- [ ] one more criterion, added after approval\n")
    r = cli(d, "run-args", a)
    check("run-args: the order body changed since approval -> BLOCKED (the spec changed since approval; re-approve)",
          blocked(r) and "the spec changed since approval" in r.stderr and "re-approve" in r.stderr
          and ("cc-factory approve %s" % a) in r.stderr and not r.stdout.strip(), (r.returncode, r.stderr))
    n0 = len(events(d, a))
    r = cli(d, "record", a, stdin=json.dumps(failed_result(a)))
    check("record: the spec changed since approval -> the run is recorded as blocked, exit 2, the note says re-approve",
          blocked(r) and "the spec changed since approval" in r.stderr and order_of(d, a)["status"] == "blocked"
          and "re-approve" in events(d, a)[-1]["note"] and len(events(d, a)) > n0 + 1, (r.returncode, r.stderr))
    r = cli(d, "approve", a)
    check("approve again (from blocked): a new hash, and run-args is accepted again", r.returncode == 0 and events(d, a)[-1]["spec_hash"] != e["spec_hash"]
          and cli(d, "run-args", a).returncode == 0, (r.returncode, r.stderr))
    cfg = json.loads(read(os.path.join(d, "factory", "line.json")))
    cfg["lines"]["default"]["threshold"] = 0.5
    write(os.path.join(d, "factory", "line.json"), json.dumps(cfg))
    r = cli(d, "run-args", a)
    check("run-args: a changed line threshold is a changed spec -> BLOCKED", blocked(r) and "the spec changed since approval" in r.stderr, (r.returncode, r.stderr))
    cli(d, "approve", a)
    r = cli(d, "run-args", a)
    check("run-args: after re-approval it hands the workflow the line's threshold", r.returncode == 0 and json.loads(r.stdout)["args"]["threshold"] == 0.5, r.stdout[-200:])
    check("run-args: no run or test key when the line declares none", not ({"run", "test"} & set(json.loads(r.stdout)["args"])), sorted(json.loads(r.stdout)["args"]))
    # The line's run / test commands (the only ones verify is handed) are part of what the spec gate approves.
    cfg["lines"]["default"]["test"] = "make test"
    cfg["lines"]["default"]["run"] = "python3 -m app\nrm -rf ~"   # not a plain one-line string: dropped by the loader
    write(os.path.join(d, "factory", "line.json"), json.dumps(cfg))
    r = cli(d, "run-args", a)
    check("run-args: a line test command added after approval is a changed spec -> BLOCKED", blocked(r) and "the spec changed since approval" in r.stderr,
          (r.returncode, r.stderr))
    cli(d, "approve", a)
    r = cli(d, "run-args", a)
    ra = json.loads(r.stdout)["args"] if r.returncode == 0 else {}
    check("run-args: after re-approval it hands verify the line's test command, and drops a run command that is not one plain line",
          ra.get("test") == "make test" and "run" not in ra and "rm -rf" not in r.stdout, r.stdout[-300:])
    cli(d, "mark", a, "building")
    r = cli(d, "approve", a)
    check("approve spec while building with the spec unchanged: BLOCKED (the gate is behind it)", blocked(r) and "behind it" in r.stderr, (r.returncode, r.stderr))
    write(p, read(p) + "\nA note added while building.\n")
    r = cli(d, "approve", a)
    check("approve spec while building with a changed spec: accepted (the approved spec is gone)", r.returncode == 0
          and order_of(d, a)["status"] == "approved" and cli(d, "run-args", a).returncode == 0, (r.returncode, r.stderr))
    b = new_order(d, "Old approval")
    append(d, b, "gate", gate="spec", state="pass", status="approved")
    cli(d, "lane", b)
    r = cli(d, "run-args", b)
    check("run-args: an approval without a spec hash (made before hashing) -> BLOCKED, re-approve", blocked(r)
          and "carries no spec hash" in r.stderr and "re-approve" in r.stderr, (r.returncode, r.stderr))
    cfg["lines"]["fast"] = {"stations": [{"name": "build", "title": "Build"}], "gates": {"spec": "auto"}}
    write(os.path.join(d, "factory", "line.json"), json.dumps(cfg))
    c = new_order(d, "Auto gate", "--line", "fast")
    cli(d, "lane", c)
    check("run-args: a line whose spec gate is automatic needs no approval and no hash", cli(d, "run-args", c).returncode == 0)


def test_merge_gate():
    d = mkrepo()
    a, lane = ready(d, "Merge me")
    br = lane["branch"]
    head = git(d, "rev-parse", "refs/heads/" + br).stdout.strip()
    r = record_saved(d, a, v2_result(a, lane), run_id="wf_m1")
    check("merge gate: the run reaches review", r.returncode == 0 and order_of(d, a)["status"] == "review", (r.stdout[-300:], r.stderr))
    n0 = len(events(d, a))
    check("record: the merge command it prints is against the reviewed sha", ("git merge --no-ff %s" % head) in r.stdout, r.stdout[-600:])
    r = cli(d, "approve", a, "--gate", "merge")
    check("approve --gate merge: BLOCKED while the lane is not merged into HEAD, suggesting git merge --no-ff <reviewed sha> from the main checkout",
          blocked(r) and "is not merged into" in r.stderr and ("git merge --no-ff %s" % head) in r.stderr and "main checkout" in r.stderr
          and len(events(d, a)) == n0 and order_of(d, a)["status"] == "review", (r.returncode, r.stderr))
    # The merge gate passes exactly the reviewed head: a lane that moved after the review is refused, merged or not.
    wt = lane["worktree"]
    write(os.path.join(wt, "src", "late.txt"), "added after the review\n")
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", "%s: a commit after the review" % a)
    late = git(wt, "rev-parse", "HEAD").stdout.strip()
    r = cli(d, "approve", a, "--gate", "merge")
    check("approve --gate merge: the lane branch moved after the review -> BLOCKED (reviewed and current heads named), nothing written",
          blocked(r) and "moved after the review" in r.stderr and head[:10] in r.stderr and late[:10] in r.stderr and "1 commit later" in r.stderr
          and len(events(d, a)) == n0, (r.returncode, r.stderr))
    pre = git(d, "rev-parse", "HEAD").stdout.strip()
    git(d, "merge", "-q", "--no-ff", "-m", "merge %s with the late commit" % a, br)
    r = cli(d, "approve", a, "--gate", "merge")
    check("approve --gate merge: the moved branch merged into HEAD -> still BLOCKED (unreviewed commits)", blocked(r) and "moved after the review" in r.stderr
          and len(events(d, a)) == n0, (r.returncode, r.stderr))
    git(wt, "reset", "-q", "--hard", head)
    r = cli(d, "approve", a, "--gate", "merge")
    check("approve --gate merge: branch put back at the reviewed head, but HEAD took in the later commit -> BLOCKED (the merge brought in %s)" % late[:10],
          blocked(r) and ("by merging %s, not the reviewed lane head %s" % (late[:10], head[:10])) in r.stderr and len(events(d, a)) == n0,
          (r.returncode, r.stderr))
    git(d, "reset", "-q", "--hard", pre)
    git(d, "merge", "-q", "--no-ff", "-m", "merge %s" % a, head)
    merge = git(d, "rev-parse", "HEAD").stdout.strip()
    git(d, "commit", "-q", "--allow-empty", "-m", "main moved on after the merge")
    tip = git(d, "rev-parse", "HEAD").stdout.strip()
    r = cli(d, "approve", a, "--gate", "merge", "--note", "merged and read")
    e = events(d, a)[-1]
    check("approve --gate merge after git merge --no-ff: done; the gate event records merge_sha (the merge commit), lane_head and target",
          r.returncode == 0 and e["gate"] == "merge" and e["status"] == "done" and e["merge_sha"] == merge and e["lane_head"] == head
          and e["target"] == tip and e["note"] == "merged and read" and order_of(d, a)["status"] == "done"
          and ("merged by %s" % merge[:10]) in r.stdout, (r.stdout, r.stderr, e))
    # A fast-forward: the merge sha is the lane head itself.
    b, lb = ready(d, "Fast forward")
    record_saved(d, b, v2_result(b, lb))
    bh = git(d, "rev-parse", "refs/heads/" + lb["branch"]).stdout.strip()
    git(d, "merge", "-q", "--ff-only", lb["branch"])
    r = cli(d, "approve", b, "--gate", "merge")
    e = events(d, b)[-1]
    check("approve --gate merge after a fast-forward: merge_sha is the lane head", r.returncode == 0 and e["merge_sha"] == bh == e["lane_head"]
          and "fast-forward" in r.stdout, (r.stdout, r.stderr, e))
    # --into REF: merged into a branch that is not checked out.
    c, lc = ready(d, "Into release")
    record_saved(d, c, v2_result(c, lc))
    ch = git(d, "rev-parse", "refs/heads/" + lc["branch"]).stdout.strip()
    git(d, "branch", "release-x", "HEAD")
    r = cli(d, "approve", c, "--gate", "merge", "--into", "release-x")
    check("approve --into REF: BLOCKED when REF does not contain the lane", blocked(r) and "release-x" in r.stderr, (r.returncode, r.stderr))
    git(d, "branch", "-f", "release", ch)
    r = cli(d, "approve", c, "--gate", "merge", "--into", "release")
    e = events(d, c)[-1]
    check("approve --into REF: done when REF contains the lane; target is REF's commit", r.returncode == 0 and e["target"] == ch and e["merge_sha"] == ch,
          (r.stdout, r.stderr, e))
    r = cli(d, "approve", c, "--gate", "merge", "--into", "--upload-pack=x")
    check("approve --into: an option-looking ref is refused (done orders refuse first, either way nothing runs)", r.returncode in (1, 2), (r.returncode, r.stderr))
    # A lane with nothing on it, a lane branch that is gone, no lane at all.
    e1 = new_order(d, "Empty lane")
    cli(d, "approve", e1)
    cli(d, "lane", e1)
    force_review(d, e1)
    r = cli(d, "approve", e1, "--gate", "merge")
    check("approve --gate merge: an empty lane (no commits past its base) is BLOCKED", blocked(r) and "no commits past its base" in r.stderr, (r.returncode, r.stderr))
    f1 = new_order(d, "Gone branch")
    cli(d, "approve", f1)
    wt, fb = lane_with_commit(d, f1)
    force_review(d, f1)
    git(d, "worktree", "remove", "--force", wt)
    git(d, "branch", "-D", fb)
    r = cli(d, "approve", f1, "--gate", "merge")
    check("approve --gate merge: a lane branch that no longer exists is BLOCKED (cannot check the merge)", blocked(r) and "cannot be read" in r.stderr, (r.returncode, r.stderr))
    g1 = new_order(d, "No lane")
    cli(d, "approve", g1)
    force_review(d, g1)
    r = cli(d, "approve", g1, "--gate", "merge")
    check("approve --gate merge: a review that records no reviewed lane head (here: no lane at all) is BLOCKED",
          blocked(r) and "records no reviewed lane head" in r.stderr, (r.returncode, r.stderr))


def test_record_checks():
    d = mkrepo()
    a, lane = ready(d, "Checked run")
    hd = os.path.join(d, ".factory-holdout", a)
    nref = [0]

    def refused(label, res, needle, stations=None):
        nref[0] += 1
        n0 = len(events(d, a))
        r = record_saved(d, a, res)
        new = events(d, a)[n0:]
        last = new[-1] if new else {}
        want = len(res.get("history") or []) if stations is None else stations
        check("record check: %s -> blocked, exit 2, BLOCKED names the check; the run's station events are still written" % label,
              blocked(r) and needle in r.stderr and last.get("status") == "blocked" and needle in last.get("note", "")
              and len([e for e in new if e["event"] == "station"]) == want and order_of(d, a)["gate"] is None
              and "briefing" not in last, (r.returncode, r.stderr[-400:], last.get("note")))

    base = lambda **kw: v2_result(a, lane, **kw)  # noqa: E731
    res = base()
    del res["contract"]
    refused("no contract", res, "contract 2")
    refused("contract as a string", base(contract="2"), "contract 2")
    res = base(attempts=2)
    res["history"] = [h for h in res["history"] if not (h["station"] == "review" and h["state"] == "pass" and h["attempt"] == 2)]
    refused("review has no pass in the final attempt", res, "station review has no pass in attempt 2, the final one")
    res = base(attempts=2)
    for h in res["history"][:2]:
        h["attempt"] = 2
    refused("plan passed only in attempt 2", res, "station plan has no pass in attempt 1")
    res = base()
    res["history"].append({"station": "test", "state": "fail", "attempt": 1, "note": "late"})
    refused("a fail in the final attempt", res, "the final attempt (1) also failed at test")
    res = base()
    for h in res["history"]:
        del h["attempt"]
    refused("no attempt numbers", res, "no attempt numbers")
    refused("no holdout object", base(holdout=None) | {"holdout": None}, "carries no holdout check")
    refused("scenarioFiles 2, 3 on disk", base(holdout={"scenarioFiles": 2}), "verify reported reading 2 scenario files, and .factory-holdout/%s holds 3" % a)
    refused("2 distinct verdicts for 3 files", base(ids=("S01", "S02"), holdout={"scenarioFiles": 3}, satisfaction=1.0),
            "verify returned 2 distinct verdicts for 3 scenario files")
    refused("no verdict for S03 (S04 instead)", base(ids=("S01", "S02", "S04")), "no verdict for S03")
    refused("S01 and S1 are one scenario", base(ids=("S01", "S1", "S02"), holdout={"scenarioFiles": 3}), "two verdicts for S01")
    refused("a verdict id that is no S-number", base(ids=("S01", "S02", "X3")), "is not {id: S<n>")
    refused("counts disagree with the verdicts", base(holdout={"satisfied": 2}), "disagree with its verdicts")
    refused("a threshold below the line's", base(holdout={"threshold": 0.5}), "below the line's 1")
    refused("a verdict below the threshold", base(satisfied=(True, True, False), satisfaction=2 / 3, holdout={"satisfaction": 2 / 3}),
            "(2 of 3 satisfied) are below the threshold 1")
    refused("the holdout check of an earlier attempt", base(attempts=2, holdout={"attempt": 1}), "from attempt 1, not the final attempt 2")
    refused("a satisfaction that disagrees", base(satisfaction=0.9), "satisfaction (0.9) disagrees")
    refused("a result for another branch", base(branch="factory/elsewhere"), "is for branch factory/elsewhere")
    write(os.path.join(hd, "notes.md"), "unnumbered\n")
    refused("an unnumbered scenario file", base(holdout={"scenarioFiles": 4}), "has no S-number in its name")
    os.unlink(os.path.join(hd, "notes.md"))
    r = record_saved(d, a, base(ids=("S1", "s2", "S003")))
    check("record check: S1 / s2 / S003 match S01 / S02 / S03 (compared as numbers) -> review", r.returncode == 0
          and order_of(d, a)["status"] == "review" and "S01 satisfied, S02 satisfied, S03 satisfied" in r.stdout, (r.returncode, r.stderr, r.stdout[-300:]))
    check("record checks: every one of the %d refused runs stayed in the ledger" % nref[0],
          len([e for e in events(d, a) if e.get("status") == "blocked"]) == nref[0], len([e for e in events(d, a) if e.get("status") == "blocked"]))

    # Static verdicts (verify ran no lane code in the sandbox: no bwrap, a failed probe, or a result without the
    # fields) open the merge gate with needs-human-check, never merge, whatever the result recommends.
    for label, ho in (("no bwrap (sandbox missing, executed false)", {"sandbox": "missing", "executed": False}),
                      ("a failed probe", {"sandbox": "failed", "executed": False}),
                      ("executed claimed without a working sandbox", {"sandbox": "failed", "executed": True}),
                      ("no sandbox or executed fields at all", {"sandbox": None, "executed": None}),
                      ("an object was missing (never fetched)", {"git": "objects-missing"}),
                      ("no git field", {"git": None})):
        s_, ls_ = ready(d, "Static %d" % len(events(d)))
        res = v2_result(s_, ls_, holdout=ho)
        res["holdout"] = {k: x for k, x in res["holdout"].items() if x is not None}
        r = record_saved(d, s_, res)
        last = events(d, s_)[-1]
        check("record: %s -> review with recommendation needs-human-check, never merge; the briefing says the verdicts are static" % label,
              r.returncode == 0 and last["status"] == "review" and last["recommendation"] == "needs-human-check"
              and "recommendation: needs-human-check" in r.stdout and "STATIC" in r.stdout and "STATIC" in last["briefing"]
              and "recommendation: merge" not in r.stdout, (r.returncode, r.stderr[-300:], last.get("recommendation")))
    # Vacuous: no scenario was judged, whatever the result recommends -> needs-scenarios.
    v = new_order(d, "Vacuous")
    cli(d, "holdout", v)
    cli(d, "mark", v, "spec", "--no-holdout")
    cli(d, "approve", v)
    lane_with_commit(d, v)
    lv = order_of(d, v)["lane"]
    r = record_saved(d, v, v2_result(v, lv, ids=(), recommendation="merge", satisfaction=None))
    last = events(d, v)[-1]
    check("record: a vacuous holdout (0 judged) is review with recommendation needs-scenarios, never merge, whatever the result says",
          r.returncode == 0 and last["status"] == "review" and last["recommendation"] == "needs-scenarios" and "satisfaction" not in last
          and "recommendation: needs-scenarios" in r.stdout, (r.returncode, r.stderr, last))
    check("record: needs-scenarios says plainly that no hidden scenario checked the work (and why)",
          "NO hidden scenario checked this work" in r.stdout and "holds no scenario files" in r.stdout and "--no-holdout" in r.stdout
          and "NO hidden scenario checked this work" in last["briefing"], r.stdout[-1500:])
    # A line with no verify station -> needs-scenarios too.
    cfg = json.loads(read(os.path.join(d, "factory", "line.json")))
    cfg["lines"]["lite"] = {"stations": [{"name": "build", "title": "Build"}, {"name": "review", "title": "Review"}]}
    cfg["lines"]["custom"] = {"stations": [{"name": "build", "title": "Build"}, {"name": "docs", "title": "Docs"}]}
    write(os.path.join(d, "factory", "line.json"), json.dumps(cfg))
    w = new_order(d, "No verify", "--line", "lite")
    cli(d, "approve", w)
    lane_with_commit(d, w)
    lw = order_of(d, w)["lane"]
    hist = [{"station": s, "state": t, "attempt": 1, "note": ""} for s in ("build", "review") for t in ("start", "pass")]
    r = record_saved(d, w, v2_result(w, lw, ids=(), history=hist, holdout=None) | {"holdout": None})
    check("record: a line with no verify station is review with merge, and the briefing says it has no hidden scenarios by design",
          r.returncode == 0 and events(d, w)[-1]["recommendation"] == "merge" and "recommendation: merge" in r.stdout
          and "Holdout: this line has no hidden scenarios by design; its build, review stations are all the checking there was." in r.stdout
          and "NO hidden scenario checked" not in r.stdout and "Line: lite." in r.stdout, (r.returncode, r.stderr, r.stdout[:600]))
    # A line WITH a verify station and no scenarios still needs them (the check above this block, on the default line).
    check("record: the merge-gate briefing stored in the ledger names the line", "Line: lite." in (events(d, w)[-1].get("briefing") or ""), events(d, w)[-1])
    x = new_order(d, "Custom station", "--line", "custom")
    cli(d, "approve", x)
    lane_with_commit(d, x)
    hist = [{"station": "build", "state": t, "attempt": 1, "note": ""} for t in ("start", "pass")]
    r = record_saved(d, x, v2_result(x, order_of(d, x)["lane"], ids=(), history=hist) | {"holdout": None})
    check("record: a station on the line that the run never passed (one the workflow cannot run) blocks review", blocked(r)
          and "station docs has no pass in attempt 1" in r.stderr, (r.returncode, r.stderr))
    # A lane with nothing on it.
    y = new_order(d, "Empty")
    scenarios(d, y)
    cli(d, "mark", y, "spec")
    cli(d, "approve", y)
    cli(d, "lane", y)
    r = record_saved(d, y, v2_result(y, order_of(d, y)["lane"]))
    check("record: a passing run on a lane with no commits past its base is blocked (nothing to merge)", blocked(r)
          and "built nothing to merge" in r.stderr, (r.returncode, r.stderr))
    # Long lists are capped in the briefing with "… N more".
    m, lm = ready(d, "Many commits", n_commits=42)
    r = record_saved(d, m, v2_result(m, lm))
    check("record briefing: more than 30 commits and 40 files are capped with '… N more'", r.returncode == 0
          and "42 commits past base" in r.stdout and "… 12 more" in r.stdout and "… 2 more" in r.stdout and "42 files changed" in r.stdout, r.stdout[:3000])

    # Infrastructure failures: cause infra on the station event, no rework counted, "rerun, no spec change".
    i, li = ready(d, "Infra")
    hist = v2_history(1)[:-1] + [{"station": "review", "state": "fail", "attempt": 1, "note": "no result from the review agent", "infra": True}]
    infra = {"contract": 2, "order": i, "status": "blocked", "reason": "infra", "recommendation": "rerun", "summary": "review died",
             "error": "review: the agent returned nothing (429 session limit)", "satisfaction": None, "attempts": 0, "rework": 0,
             "history": hist, "stations": {}, "holdout": None, "feedback": []}
    r = cli(d, "record", i, stdin=json.dumps(infra))
    st = [e for e in events(d, i) if e["event"] == "station"]
    check("record infra: exit 0, status blocked, the review fail carries cause infra (the passes do not)", r.returncode == 0
          and order_of(d, i)["status"] == "blocked" and st[-1]["state"] == "fail" and st[-1]["cause"] == "infra"
          and all("cause" not in e for e in st[:-1]) and events(d, i)[-1]["recommendation"] == "rerun", (r.returncode, r.stderr, st[-1:]))
    check("record infra: says it was an infrastructure failure, to rerun, and that no spec change is needed",
          "infrastructure failure" in r.stdout and "no spec change needed" in r.stdout and ("/factory run %s" % i) in r.stdout
          and "429 session limit" in r.stdout, r.stdout[-600:])
    r = record_saved(d, i, v2_result(i, li))
    check("record infra: the rerun after an infra failure is not a rework (rework 0)", r.returncode == 0 and order_of(d, i)["rework"] == 0
          and order_of(d, i)["status"] == "review", (r.returncode, r.stderr, order_of(d, i)["rework"]))
    j, lj = ready(d, "Real failure")
    cli(d, "record", j, stdin=json.dumps(failed_result(j)))
    r = record_saved(d, j, v2_result(j, lj))
    check("record: the rerun after a real failure is a rework (rework 2: one inside the failed run, one for the rerun)",
          r.returncode == 0 and order_of(d, j)["rework"] == 2, order_of(d, j)["rework"])

    # The feedback leak flag: shared word 5-grams with a scenario, the spec's own phrases excluded.
    k = new_order(d, "Leak check")
    p = F.find_order(d, k)
    write(p, read(p) + "\nReport the average word length of the input when the flag is given.\n")
    secret = "the reason should say there are no words to average and exit with status 1"
    scenarios(d, k, ["Run it on a file that is only whitespace: %s.\n" % secret,
                     "Report the average word length of the input when the flag is given, on a plain file.\n", "Three.\n"])
    cli(d, "mark", k, "spec")
    cli(d, "approve", k)
    lane_with_commit(d, k)
    fb = [{"attempt": 2, "from": "verify", "kind": "verify", "text": "Gaps: when the input is only whitespace %s, as the spec implies." % secret},
          {"attempt": 2, "from": "review", "kind": "review", "text": "Report the average word length of the input when the flag is given."}]
    r = record_saved(d, k, v2_result(k, order_of(d, k)["lane"], attempts=2, feedback=fb))
    flags = [e for e in events(d, k) if e.get("flag") == "holdout-leak"]
    led = read(ledger_file(d))
    check("leak flag: the run is still recorded (a flag, not a refusal)", r.returncode == 0 and order_of(d, k)["status"] == "review", (r.returncode, r.stderr))
    m1 = re.search(r"S01 \((\d+)\)", flags[0]["note"]) if flags else None
    check("leak flag: one note event naming the attempt, the station and the scenario id with a count of shared 5-grams",
          len(flags) == 1 and "attempt 2" in flags[0]["note"] and "from verify" in flags[0]["note"] and m1 and int(m1.group(1)) >= 10
          and "S02" not in flags[0]["note"] and flags[0]["event"] == "note", flags)
    check("leak flag: the shared text is never in the ledger or the output", "no words to average" not in led and "no words to average" not in r.stdout
          and "only whitespace" not in led and "possible holdout leak" in r.stdout, r.stdout[-800:])
    check("leak flag: feedback that only repeats the spec's own words (review's here) is not flagged",
          len(flags) == 1 and "from review" not in flags[0]["note"], flags)


def test_workflow_run():
    d = mkrepo()
    a, lane = ready(d, "Saved run")
    other = new_order(d, "Some other order")
    args = json.loads(cli(d, "run-args", a).stdout)["args"]
    _counter[0] += 1
    cfg = os.path.join(BASE, "claude-cfg-%d" % _counter[0])
    env = {"CLAUDE_CONFIG_DIR": cfg}
    enc = re.sub(r"[^A-Za-z0-9]", "-", d)
    check("workflow-run: the projects/ name encodes the cwd with every non-alphanumeric character as '-'",
          enc.startswith("-") and "/" not in enc and "." not in enc, enc)

    def put(run_id, where=None, mode=0o600, session="7721d9f0-c596-4633-badd-315ac669b4dd", **over):
        p = os.path.join(cfg, "projects", where or enc, session, "workflows", run_id + ".json")
        doc = {"runId": run_id, "timestamp": "2026-10-26T06:52:00Z", "taskId": "w1", "script": "// the script", "scriptPath": "/x/factory-line.js",
               "args": args, "result": v2_result(a, lane), "agentCount": 5, "logs": [], "durationMs": 1, "summary": "done",
               "workflowName": "factory-line", "status": "completed", "startTime": 1, "phases": [], "totalTokens": 1, "totalToolCalls": 1}
        doc.update(over)
        write(p, json.dumps(doc))
        os.chmod(p, mode)
        return p

    def rec(run_id, *extra, env_extra=env):
        return cli(d, "record", a, "--workflow-run", run_id, *extra, env_extra=env_extra)

    n0 = len(events(d))
    r = rec("wf_missing-001")
    check("workflow-run: a run with no saved file is BLOCKED (check the id, wait for the run)", blocked(r) and "saved no result" in r.stderr, (r.returncode, r.stderr))
    put("wf_good-0001")
    r = rec("wf_good-0001", "--run-id", "wf_other")
    check("workflow-run: --run-id that disagrees is a usage error (exit 1)", r.returncode == 1 and "disagrees" in r.stderr, (r.returncode, r.stderr))
    r = rec("wf_good-0001", "--file", os.path.join(BASE, "x.json"))
    check("workflow-run: with --file too is a usage error (exit 1)", r.returncode == 1, (r.returncode, r.stderr))
    for bad in ("wf_../x", "../wf_x", "wf_", "wf_a/b", "x" * 10):
        r = rec(bad)
        check("workflow-run: %r is not a run id (exit 1)" % bad, r.returncode == 1 and "not a Workflow run id" in r.stderr, (r.returncode, r.stderr))
    real = put("wf_real-0001", where="-elsewhere")
    link = os.path.join(cfg, "projects", enc, "7721d9f0-c596-4633-badd-315ac669b4dd", "workflows", "wf_link-0001.json")
    os.symlink(real, link)
    r = rec("wf_link-0001")
    check("workflow-run: a symlinked run file is BLOCKED (O_NOFOLLOW)", blocked(r) and "symlink" in r.stderr, (r.returncode, r.stderr))
    put("wf_loose-0001", mode=0o664)
    r = rec("wf_loose-0001")
    check("workflow-run: a group-writable run file is BLOCKED", blocked(r) and "group- or world-writable" in r.stderr, (r.returncode, r.stderr))
    put("wf_running-01", status="running")
    r = rec("wf_running-01")
    check("workflow-run: a run that is not completed is BLOCKED (it isn't finished)", blocked(r) and "isn't finished" in r.stderr
          and "running" in r.stderr, (r.returncode, r.stderr))
    put("wf_otherwf-01", workflowName="loop-until-clean")
    r = rec("wf_otherwf-01")
    check("workflow-run: a run of another workflow is BLOCKED", blocked(r) and "not the factory line" in r.stderr, (r.returncode, r.stderr))
    put("wf_wrongid-01", runId="wf_someone-else")
    r = rec("wf_wrongid-01")
    check("workflow-run: a file whose runId is another run's is BLOCKED", blocked(r) and "does not describe that run" in r.stderr, (r.returncode, r.stderr))
    put("wf_order-0001", args=dict(args, order=other))
    r = rec("wf_order-0001")
    check("workflow-run: a run of another order is BLOCKED, naming both", blocked(r) and ("ran %s, not %s" % (other, a)) in r.stderr, (r.returncode, r.stderr))
    for key, val in (("worktree", "/tmp/elsewhere"), ("root", "/tmp"), ("holdout", os.path.join(d, ".factory-holdout", other)),
                     ("branch", "factory/FO-099-x")):
        put("wf_args-%s" % key, args=dict(args, **{key: val}))
        r = rec("wf_args-%s" % key)
        check("workflow-run: args.%s that is not this order's lane is BLOCKED" % key, blocked(r) and ("args.%s" % key) in r.stderr, (r.returncode, r.stderr))
    put("wf_noresult-1", result="a string")
    r = rec("wf_noresult-1")
    check("workflow-run: a result that is not an object is BLOCKED", blocked(r) and "no result object" in r.stderr, (r.returncode, r.stderr))
    big = put("wf_big-000001")
    with open(big, "a", encoding="utf-8") as fh:
        fh.write(" " * (8 * 1024 * 1024))
    r = rec("wf_big-000001")
    check("workflow-run: a run file over 8 MiB is BLOCKED", blocked(r) and "over 8 MiB" in r.stderr, (r.returncode, r.stderr))
    check("workflow-run: every refusal so far wrote nothing", len(events(d)) == n0, (len(events(d)), n0))
    put("wf_spec-00001", args=dict(args, spec=args["spec"] + "\nan extra line the order does not have\n"))
    r = rec("wf_spec-00001")
    check("workflow-run: a run whose args.spec is not the approved order body is recorded as blocked (spec changed since approval)",
          blocked(r) and "the spec changed since approval" in r.stderr and order_of(d, a)["status"] == "blocked", (r.returncode, r.stderr))
    r = rec("wf_good-0001")
    evs = events(d, a)
    check("workflow-run: a good saved run is recorded (exit 0, review); the run id is the runId, on every event",
          r.returncode == 0 and order_of(d, a)["status"] == "review" and order_of(d, a)["run_id"] == "wf_good-0001"
          and evs[-1]["run_id"] == "wf_good-0001" and "(run wf_good-0001)" in r.stdout, (r.returncode, r.stderr, r.stdout[-300:]))
    r = rec("wf_good-0001", "--run-id", "wf_good-0001")
    check("workflow-run: --run-id equal to the runId is fine", r.returncode == 0, (r.returncode, r.stderr))
    put("wf_fallback-1", where="-tmp-some-other-cwd", session="aaaaaaaa-0000-0000-0000-000000000001", args=json.dumps(args))
    r = rec("wf_fallback-1")
    check("workflow-run: found by the bounded scan when not under the main checkout's directory; args saved as a JSON string are read",
          r.returncode == 0 and order_of(d, a)["run_id"] == "wf_fallback-1", (r.returncode, r.stderr))
    put("wf_twice-0001", where="-one")
    put("wf_twice-0001", where="-two")
    r = rec("wf_twice-0001")
    check("workflow-run: two saved files for one run id (outside the main checkout's directory) are BLOCKED as ambiguous",
          blocked(r) and "2 saved results" in r.stderr, (r.returncode, r.stderr))
    home_cfg = os.path.join(HOME, ".claude")
    saved = cfg
    cfg = home_cfg
    put("wf_home-00001")
    cfg = saved
    r = rec("wf_home-00001", env_extra={})
    check("workflow-run: without CLAUDE_CONFIG_DIR it looks in ~/.claude", r.returncode == 0 and order_of(d, a)["run_id"] == "wf_home-00001", (r.returncode, r.stderr))


# ── safety ────────────────────────────────────────────────────────────────────
def test_safety():
    # A planted symlink at the ledger path.
    d = mkrepo()
    a = new_order(d, "Victim")
    led = ledger_file(d)
    target = os.path.join(BASE, "victim.txt")
    write(target, "KEEP\n")
    os.unlink(led)
    os.symlink(target, led)
    n_files = len(os.listdir(os.path.join(d, "factory", "orders")))
    r = cli(d, "mark", a, "building")
    check("safety: a symlink at the ledger path -> exit 2 BLOCKED", blocked(r), (r.returncode, r.stderr))
    check("safety: ... and the symlink's target is unchanged", read(target) == "KEEP\n", read(target))
    r = cli(d, "new", "Should not exist")
    check("safety: `new` with a symlinked ledger is refused before it creates the order file",
          blocked(r) and len(os.listdir(os.path.join(d, "factory", "orders"))) == n_files and read(target) == "KEEP\n", (r.returncode, r.stderr))
    for sub in (("approve", a), ("reject", a), ("lane", a)):
        check("safety: `%s` with a symlinked ledger writes nothing" % sub[0], cli(d, *sub).returncode in (1, 2) and read(target) == "KEEP\n")
    r = cli(d, "record", a, stdin=json.dumps({"status": "review", "history": []}))
    check("safety: `record` with a symlinked ledger is refused, target intact", blocked(r) and read(target) == "KEEP\n", (r.returncode, r.stderr))
    r = cli(d, "list")
    check("safety: reads never follow the symlink (list shows the order as queued, exit 0)", r.returncode == 0 and "queued" in r.stdout, (r.returncode, r.stdout, r.stderr))
    os.unlink(led)

    # The factory ledger directory replaced by a symlink.
    d = mkrepo()
    a = new_order(d, "Dir swap")
    fdir = os.path.dirname(ledger_file(d))
    shutil.rmtree(fdir)
    elsewhere = os.path.join(BASE, "dir-elsewhere")
    os.makedirs(elsewhere)
    os.symlink(elsewhere, fdir)
    r = cli(d, "mark", a, "building")
    check("safety: the factory ledger dir replaced by a symlink -> exit 2", blocked(r), (r.returncode, r.stderr))
    check("safety: ... and nothing was written through it", os.listdir(elsewhere) == [], os.listdir(elsewhere))
    r = cli(d, "init")
    check("safety: init reports the symlinked ledger dir and exits 2", r.returncode == 2 and "ledger" in r.stderr + r.stdout, (r.returncode, r.stdout[-300:], r.stderr))
    check("safety: init did not write through it either", os.listdir(elsewhere) == [])
    # <common>/cc_tool replaced by a symlink.
    d = mkrepo()
    a = new_order(d, "Dir swap 2")
    cc = os.path.join(d, ".git", "cc_tool")
    shutil.rmtree(cc)
    elsewhere2 = os.path.join(BASE, "dir-elsewhere2")
    os.makedirs(elsewhere2)
    os.symlink(elsewhere2, cc)
    r = cli(d, "mark", a, "building")
    check("safety: .git/cc_tool replaced by a symlink -> exit 2, target empty", blocked(r) and os.listdir(elsewhere2) == [], (r.returncode, r.stderr, os.listdir(elsewhere2)))

    # A hard-linked ledger.
    d = mkrepo()
    a = new_order(d, "Hardlink")
    led = ledger_file(d)
    twin = os.path.join(BASE, "twin.jsonl")
    os.link(led, twin)
    before = read(twin)
    r = cli(d, "mark", a, "building")
    check("safety: a hard-linked ledger -> exit 2, the other name unchanged", blocked(r) and read(twin) == before, (r.returncode, r.stderr))
    os.unlink(twin)
    check("safety: unlinked again, writes work", cli(d, "mark", a, "building").returncode == 0)

    # A FIFO at the ledger path must not hang the tool.
    d = mkrepo()
    a = new_order(d, "Fifo")
    led = ledger_file(d)
    os.unlink(led)
    os.mkfifo(led)
    t0 = time.perf_counter()
    r = cli(d, "mark", a, "building", timeout=20)
    check("safety: a FIFO at the ledger path -> exit 2 promptly (no hang)", blocked(r) and time.perf_counter() - t0 < 10, (r.returncode, r.stderr))
    os.unlink(led)

    # A directory at the ledger path.
    os.mkdir(led)
    r = cli(d, "mark", a, "building")
    check("safety: a directory at the ledger path -> exit 2", blocked(r), (r.returncode, r.stderr))
    os.rmdir(led)

    # Permissions: a loose ledger and directory are tightened; a hook-made 0755 cc_tool/ is accepted as is.
    d = mkrepo()
    a = new_order(d, "Modes")
    led = ledger_file(d)
    os.chmod(led, 0o666)
    os.chmod(os.path.dirname(led), 0o755)
    cc = os.path.join(d, ".git", "cc_tool")
    os.chmod(cc, 0o755)
    r = cli(d, "mark", a, "building")
    check("safety: a 0666 ledger is chmodded to 0600 and the 0755 factory dir to 0700", r.returncode == 0 and mode(led) == 0o600 and mode(os.path.dirname(led)) == 0o700, (oct(mode(led)), oct(mode(os.path.dirname(led)))))
    check("safety: a cc_tool/ dir made 0755 by the hooks is accepted and left alone", mode(cc) == 0o755, oct(mode(cc)))
    d = mkrepo(init=False)
    os.makedirs(os.path.join(d, ".git", "cc_tool"), mode=0o755)
    os.chmod(os.path.join(d, ".git", "cc_tool"), 0o755)
    r = cli(d, "init")
    check("safety: init under a pre-existing 0755 cc_tool/ makes factory/ 0700 and leaves cc_tool/ alone", r.returncode == 0
          and mode(os.path.join(d, ".git", "cc_tool")) == 0o755 and mode(os.path.join(d, ".git", "cc_tool", "factory")) == 0o700)
    # A ledger-dir problem leaves the working tree files in place (init is partial, not destructive).
    # Exactly one write per event, a full line each.
    d = mkrepo()
    a = new_order(d, "Lines")
    cli(d, "mark", a, "building")
    raw = open(ledger_file(d), "rb").read()
    check("safety: every ledger line is a complete compact JSON object ending in a newline", raw.endswith(b"\n") and all(
        json.loads(ln) and b", " not in ln.split(b'"note"')[0] for ln in raw.splitlines()), raw[:200])
    check("safety: each record validates (t, order, event, by)", all(set(("t", "order", "event", "by")) <= set(json.loads(ln)) and F.valid_event(json.loads(ln)) for ln in raw.splitlines()))

    # A hostile title runs nothing.
    d = mkrepo()
    cwd = os.path.join(BASE, "inj-cwd")
    os.makedirs(cwd)
    title = "$(touch PWNED) `touch PWNED2` ; touch PWNED3 && echo $HOME | cat"
    r = cli(d, "new", title, cwd=cwd)
    check("injection: a shell-looking title is just a title (exit 0)", r.returncode == 0 and r.stdout.startswith("FO-001  factory/orders/FO-001-"), (r.stdout, r.stderr))
    name = r.stdout.split()[1].split("/")[-1]
    check("injection: the file name is a safe slug", re.fullmatch(r"FO-001-[a-z0-9-]+\.md", name) is not None and name.startswith("FO-001-touch-pwned-touch-pwned2-") and "$" not in name and "`" not in name, name)
    oid = "FO-001"
    cli(d, "approve", oid)
    r = cli(d, "lane", oid, cwd=cwd)
    check("injection: the lane for it is created under .claude/worktrees with a valid branch", r.returncode == 0
          and r.stdout.rstrip("\n").splitlines()[-1].startswith(os.path.join(d, ".claude", "worktrees") + os.sep)
          and git(d, "check-ref-format", "--branch", order_of(d, oid)["lane"]["branch"]).returncode == 0, (r.stdout, r.stderr))
    cli(d, "run-args", oid, cwd=cwd)
    cli(d, "show", oid, cwd=cwd)
    cli(d, "list", cwd=cwd)
    found = []
    for root, dirs, files in os.walk(BASE):
        for n in dirs + files:
            if n.startswith("PWNED"):
                found.append(os.path.join(root, n))
    check("injection: nothing was executed (no PWNED file anywhere)", not found, found)
    # Titles that would be dangerous as an option.
    r = cli(d, "new", "--", "--upload-pack=evil")
    check("injection: an option-looking title is a title", r.returncode == 0 and "upload-pack-evil" in r.stdout, (r.stdout, r.stderr))
    # An order's body never reaches the ledger.
    d = mkrepo()
    oid = new_order(d, "Body marker")
    p = F.find_order(d, oid)
    write(p, read(p) + "\nSECRET-BODY-MARKER sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123 $(touch PWNED4)\n")
    cli(d, "approve", oid)
    cli(d, "lane", oid)
    cli(d, "mark", oid, "building", "--station", "build", "--state", "start")
    cli(d, "run-args", oid)
    cli(d, "record", oid, stdin=json.dumps({"order": oid, "status": "review", "recommendation": "merge", "summary": "fine", "history": []}))
    led = open(ledger_file(d), encoding="utf-8").read()
    check("safety: order-file text never appears in the ledger", "SECRET-BODY-MARKER" not in led and "sk-ant" not in led and "PWNED4" not in led)
    check("safety: the order file itself is untouched by lifecycle commands", "SECRET-BODY-MARKER" in read(p))
    # Hostile environment variables do not reach the tool's own imports.
    d = mkrepo()
    evil = os.path.join(BASE, "evil-path")
    os.makedirs(evil)
    write(os.path.join(evil, "json.py"), "raise SystemExit(99)\n")
    r = cli(d, "list", env_extra={"PYTHONPATH": evil})
    check("safety: PYTHONPATH is dropped (isolated re-exec), a planted json.py does not run", r.returncode == 0, (r.returncode, r.stderr[-200:]))
    write(os.path.join(d, "json.py"), "raise SystemExit(97)\n")
    r = cli(d, "list", cwd=d)
    check("safety: a json.py in the current directory is not imported", r.returncode == 0, (r.returncode, r.stderr[-200:]))
    # A symlinked .claude/worktrees/ (a cloned repo could ship one) never places a lane elsewhere,
    # and a symlinked .claude/workflows/ never receives factory-line.js.
    d = mkrepo()
    oid = new_order(d, "Symlinked lanes")
    cli(d, "approve", oid)
    elsewhere = os.path.join(BASE, "elsewhere-%d" % _counter[0])
    os.makedirs(elsewhere)
    os.makedirs(os.path.join(d, ".claude"), exist_ok=True)
    os.symlink(elsewhere, os.path.join(d, ".claude", "worktrees"))
    r = cli(d, "lane", oid)
    check("safety: lane refuses a symlinked .claude/worktrees/ (BLOCKED) and creates nothing there",
          blocked(r) and os.listdir(elsewhere) == [] and not [e for e in events(d, oid) if e["event"] == "lane"], (r.returncode, r.stderr))
    d = mkrepo(init=False)
    wf_else = os.path.join(BASE, "wf-elsewhere-%d" % _counter[0])
    os.makedirs(wf_else)
    os.makedirs(os.path.join(d, ".claude"))
    os.symlink(wf_else, os.path.join(d, ".claude", "workflows"))
    write(os.path.join(wf_else, "factory-line.js"), "// someone else's file\n")
    r = cli(d, "init", "--force")
    check("safety: init --force does not write factory-line.js through a symlinked .claude/workflows/",
          read(os.path.join(wf_else, "factory-line.js")) == "// someone else's file\n" and sorted(os.listdir(wf_else)) == ["factory-line.js"]
          and "not a real directory" in r.stdout, (r.returncode, r.stdout[-300:]))
    # Static properties of the source.
    src = read(CLI)
    check("source: no shell execution (shell=True, os.system, os.popen)", not re.search(r"shell\s*=\s*True|os\.system|os\.popen", src))
    check("source: no em-dash anywhere", EM_DASH not in src)
    check("source: re-execs isolated and does not write bytecode", "-I" in src and "dont_write_bytecode = True" in src and "sys.flags.isolated" in src)
    check("source: loads only the library by path (no cc_dash package import)", "spec_from_file_location(\"cc_factory_lib\"" in src and "import cc_dash" not in src and "from cc_dash" not in src)
    for rel in ("templates/factory/README.md", "templates/factory/line.json", "templates/factory/order-template.md"):
        check("source: no em-dash in %s" % rel, EM_DASH not in read(os.path.join(REPO, rel)))
    check("source: the README names the guide and the commands", "docs/guides/factory.md" in read(os.path.join(REPO, "templates/factory/README.md"))
          and all(c in read(os.path.join(REPO, "templates/factory/README.md")) for c in ("cc-factory init", "cc-factory new", "cc-factory approve", ".factory-holdout", "ledger.jsonl")))
    check("source: the shipped order template carries every placeholder", all(("{{%s}}" % p) in read(os.path.join(REPO, "templates/factory/order-template.md")) for p in ("id", "title", "priority", "created", "line")))


def make_trees():
    global HUMAN_CLI, REASON_CLI, BROKEN_CLI, MISSING_CLI, NONE_CLI, THROWS_CLI, NOPROC_CLI
    HUMAN_CLI = make_tree("human", STUB_HUMAN)
    REASON_CLI = make_tree("reason", REASON_STUB)
    BROKEN_CLI = make_tree("broken", "raise RuntimeError('broken on purpose')\n")
    MISSING_CLI = make_tree("missing", None)
    NONE_CLI = make_tree("none", STUB_HUMAN.replace("def recording_problem():\n    return ''", "def recording_problem():\n    return None"))
    THROWS_CLI = make_tree("throws", STUB_HUMAN.replace("def recording_problem():\n    return ''",
                                                        "def recording_problem():\n    raise OSError('cannot look')"))
    NOPROC_CLI = make_tree("noproc", read(PROCS) + "\n\ndef have_proc():\n    return False\n")


def main():
    try:
        make_trees()
        for fn in (test_cli_basics, test_init, test_init_line_js, test_new, test_list_show, test_mark, test_gates, test_human_gates,
                   test_lane, test_holdout, test_run_args, test_quick_and_pin, test_spec_hash, test_record, test_merge_gate, test_record_checks,
                   test_workflow_run, test_safety):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                check(fn.__name__ + " raised", False, "%s: %s" % (type(e).__name__, e))
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
    pyc_after = sorted(f for f in (os.listdir(_PYC_DIR) if os.path.isdir(_PYC_DIR) else []) if f.startswith("factory."))
    check("the CLI left no bytecode for the library in lib/cc_dash/__pycache__", pyc_after == PYC_BEFORE, (PYC_BEFORE, pyc_after))
    why = STATE_GUARD.problem()
    check("the real state dir's projects.json is untouched by this run", not why, why)
    fails = [r for r in RESULTS if not r[1]]
    print("total=%d  pass=%d  fail=%d" % (len(RESULTS), len(RESULTS) - len(fails), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
