#!/usr/bin/env python3
"""bin/cc-setup --factory: the opt-in rollout of the software factory into a project.

Checks (HOME and XDG_STATE_HOME point at temp dirs; nothing global is touched, and a
StateGuard confirms the real projects.json is unchanged afterwards):
  - bash -n bin/cc-setup is clean; the usage comment names --factory; an unknown flag
    still exits 1 before anything is written
  - `cc-setup <proj> --factory` on a git project exits 0, runs `cc-factory init`
    (factory/line.json with a "default" line, factory/orders/, factory/README.md,
    factory/order-template.md), git-ignores .factory-holdout/ and .claude/worktrees/
    (and git agrees), creates the ledger dir under the git dir, deploys the /factory
    skill, and installs .claude/workflows/factory-line.js
  - re-running is idempotent: each .gitignore line once, a hand-edited line.json kept,
    an existing .gitignore without a final newline is appended to, never glued
  - plain `cc-setup <proj>` (no flag) creates no factory/ and edits no .gitignore for it,
    but git-ignores the run-state board, .cc-board/, once across setup and update
  - `cc-factory init <proj>` alone gives the same scaffold

Part of tests/test_factory.py (python3 tests/test_factory.py setup runs only this part;
python3 tests/factory_setup_cases.py runs it standalone).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETUP = os.path.join(REPO, "bin", "cc-setup")
FACTORY = os.path.join(REPO, "bin", "cc-factory")
WORKFLOW_SRC = os.path.join(REPO, ".claude", "workflows", "factory-line.js")
RESULTS = []
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from state_guard import StateGuard  # noqa: E402  (tests/state_guard.py)
STATE_GUARD = StateGuard()         # the real projects.json, before anything here runs cc-setup
BASE = tempfile.mkdtemp(prefix="cc-factory-setup-")
HOME = os.path.join(BASE, "home")
os.makedirs(HOME)

IGNORE_LINES = (".factory-holdout/", ".claude/worktrees/")


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    if not cond:
        print("FAIL %s  %s" % (name, str(detail)[:300]))


def env():
    e = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR",)}
    e["HOME"] = HOME
    e["XDG_STATE_HOME"] = os.path.join(BASE, "state")      # cc-setup registers the project there, never in yours
    return e


def new_project(name):
    proj = os.path.join(BASE, name)
    os.makedirs(proj)
    subprocess.run(["git", "init", "-q", proj], capture_output=True, env=env())
    return proj


def run(cmd, **kw):
    kw.setdefault("timeout", 600)
    return subprocess.run(cmd, capture_output=True, text=True, env=env(), stdin=subprocess.DEVNULL, **kw)


def tail(r):
    return (r.stderr or "")[-300:] + (r.stdout or "")[-300:]


def lines_of(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().split("\n")
    except OSError:
        return []


def check_scaffold(label, proj):
    """The files `cc-factory init` leaves in a project."""
    line_path = os.path.join(proj, "factory", "line.json")
    try:
        cfg = json.load(open(line_path, encoding="utf-8"))
    except (OSError, ValueError) as e:
        cfg = None
        check(label + ": factory/line.json parses", False, "%s: %s" % (type(e).__name__, e))
    if cfg is not None:
        line = (cfg.get("lines") or {}).get("default") or {}
        names = [s.get("name") for s in line.get("stations", [])]
        check(label + ": factory/line.json has a default line with the five stations",
              names == ["plan", "build", "test", "verify", "review"], names)
        check(label + ": the merge gate is human", (line.get("gates") or {}).get("merge") == "human", line.get("gates"))
    check(label + ": factory/orders/ exists", os.path.isdir(os.path.join(proj, "factory", "orders")))
    for f in ("README.md", "order-template.md"):
        check(label + ": factory/%s is written" % f, os.path.isfile(os.path.join(proj, "factory", f)))
    gi = lines_of(os.path.join(proj, ".gitignore"))
    for want in IGNORE_LINES:
        check(label + ": .gitignore lists %s exactly once" % want, gi.count(want) == 1, gi)
    check(label + ": the .gitignore comment line appears once",
          sum(1 for l in gi if l.startswith("# cc_tool factory")) == 1, gi)
    # git itself agrees, which is what keeps the holdout out of every lane.
    for probe in (".factory-holdout/FO-001/scenario.md", ".claude/worktrees/FO-001-x/f.txt"):
        r = run(["git", "-C", proj, "check-ignore", "-q", probe])
        check(label + ": git ignores " + probe.split("/")[0] + "/", r.returncode == 0, tail(r))
    check(label + ": the ledger dir exists under the git dir",
          os.path.isdir(os.path.join(proj, ".git", "cc_tool", "factory")))


def test_syntax_and_usage():
    r = subprocess.run(["bash", "-n", SETUP], capture_output=True, text=True)
    check("bash -n bin/cc-setup is clean", r.returncode == 0, r.stderr)
    head = lines_of(SETUP)[:6]
    usage = [l for l in head if l.startswith("# Usage:")]
    check("the usage comment names --factory", len(usage) == 1 and "[--factory]" in usage[0], usage)
    src = "\n".join(lines_of(SETUP))
    check("cc-setup has one --factory case arm", src.count("--factory) WITH_FACTORY=1; shift ;;") == 1)
    check("the factory section follows the vault section",
          0 <= src.find("cc-vault\" \"$PROJECT_DIR\"") < src.find("# ── 5b. Optional software factory"), "")
    # An unknown flag still exits 1 and writes nothing, with or without --factory.
    proj = new_project("badflag")
    for args in (["--nope"], ["--factory", "--nope"], ["--factory-x"]):
        r = run([SETUP, proj] + args, timeout=60)
        check("cc-setup %s exits 1" % " ".join(args), r.returncode == 1 and "Unknown flag" in r.stderr, tail(r))
    check("an unknown flag leaves the project untouched", sorted(os.listdir(proj)) == [".git"], os.listdir(proj))


def test_rollout():
    if shutil.which("git") is None:
        print("rollout: skipped (no git)")
        return
    if not os.access(FACTORY, os.X_OK):
        check("rollout: bin/cc-factory exists and is executable", False, FACTORY)
        return
    proj = new_project("rollout")
    # A .gitignore the project already has, with no final newline: the append must not glue onto it.
    with open(os.path.join(proj, ".gitignore"), "w") as fh:
        fh.write("node_modules/")
    r = run([SETUP, proj, "--factory"])
    check("rollout: cc-setup --factory exits 0", r.returncode == 0, tail(r))
    check("rollout: the Factory banner is printed", "── Factory ──" in r.stdout, r.stdout[-400:])
    check_scaffold("rollout", proj)
    gi = lines_of(os.path.join(proj, ".gitignore"))
    check("rollout: the project's own .gitignore line survives, on its own line", "node_modules/" in gi, gi)
    skill = os.path.join(proj, ".claude", "skills", "factory", "SKILL.md")
    check("rollout: the /factory skill is deployed", os.path.isfile(skill), skill)
    # Vendored MIT skills must arrive with their licence (cc-setup copies each skill folder whole).
    for sk in ("design-an-interface", "improve-codebase-architecture"):
        src = os.path.join(REPO, "templates", "skills", sk, "LICENSE")
        dst = os.path.join(proj, ".claude", "skills", sk, "LICENSE")
        same = os.path.isfile(dst) and os.path.isfile(src) and open(dst, "rb").read() == open(src, "rb").read()
        check("rollout: %s ships its LICENSE into the project" % sk, same, dst)
    if os.path.isfile(WORKFLOW_SRC):
        dst = os.path.join(proj, ".claude", "workflows", "factory-line.js")
        same = os.path.isfile(dst) and open(dst, "rb").read() == open(WORKFLOW_SRC, "rb").read()
        check("rollout: factory-line.js is installed into the project", same, dst)
    else:
        print("rollout: factory-line.js not in the cc_tool source yet; install check skipped")
    # The scaffold is usable straight away.
    r = run([FACTORY, "-C", proj, "list"], timeout=60)
    check("rollout: cc-factory list works in the new project", r.returncode == 0, tail(r))

    # Idempotent, and a hand-edited line is the project's: init skips present files without --force.
    line_path = os.path.join(proj, "factory", "line.json")
    cfg = json.load(open(line_path))
    cfg["lines"]["default"]["retries"] = 2
    with open(line_path, "w") as fh:
        json.dump(cfg, fh, indent=2)
    edited = open(line_path, "rb").read()
    r = run([SETUP, proj, "--factory"])
    check("rollout: re-running exits 0", r.returncode == 0, tail(r))
    gi2 = lines_of(os.path.join(proj, ".gitignore"))
    for want in IGNORE_LINES:
        check("rollout: re-running keeps %s once" % want, gi2.count(want) == 1, gi2)
    check("rollout: re-running keeps one comment line", sum(1 for l in gi2 if l.startswith("# cc_tool factory")) == 1, gi2)
    check("rollout: re-running keeps a hand-edited line.json", open(line_path, "rb").read() == edited)
    check("rollout: re-running keeps the orders dir", os.path.isdir(os.path.join(proj, "factory", "orders")))


def test_plain_setup_has_no_factory():
    if shutil.which("git") is None:
        return
    proj = new_project("plain")
    r = run([SETUP, proj])
    check("plain: cc-setup without the flag exits 0", r.returncode == 0, tail(r))
    check("plain: no factory/ directory", not os.path.exists(os.path.join(proj, "factory")), os.listdir(proj))
    check("plain: no Factory banner", "── Factory ──" not in r.stdout)
    gi = "\n".join(lines_of(os.path.join(proj, ".gitignore")))
    check("plain: .gitignore does not mention the holdout", ".factory-holdout" not in gi, gi)
    check("plain: factory-line.js is not installed", not os.path.exists(os.path.join(proj, ".claude", "workflows", "factory-line.js")))
    skill = os.path.join(proj, ".claude", "skills", "factory", "SKILL.md")
    check("plain: the /factory skill ships to every project", os.path.isfile(skill), skill)
    # The updater never opts a project into the factory.
    r = run([os.path.join(REPO, "bin", "cc-update-project"), proj])
    check("plain: cc-update-project exits 0", r.returncode == 0, tail(r))
    check("plain: cc-update-project creates no factory/", not os.path.exists(os.path.join(proj, "factory")), os.listdir(proj))
    # The run-state board (the managed block's "Run-state board") is git-ignored in every project, once.
    gi = lines_of(os.path.join(proj, ".gitignore"))
    check("plain: .cc-board/ is git-ignored exactly once after setup and update", gi.count(".cc-board/") == 1, gi)
    os.makedirs(os.path.join(proj, ".cc-board", "run", "lead-a"), exist_ok=True)
    r = run(["git", "-C", proj, "check-ignore", "-q", ".cc-board/run/lead-a/checkpoint.md"])
    check("plain: git ignores a board file", r.returncode == 0, tail(r))


def test_init_alone():
    if shutil.which("git") is None or not os.access(FACTORY, os.X_OK):
        return
    proj = new_project("init-alone")
    r = run([FACTORY, "init", proj], timeout=120)
    check("init: cc-factory init exits 0", r.returncode == 0, tail(r))
    check_scaffold("init", proj)
    r = run([FACTORY, "init", proj], timeout=120)
    check("init: a second init exits 0", r.returncode == 0, tail(r))
    gi = lines_of(os.path.join(proj, ".gitignore"))
    check("init: a second init adds no duplicate .gitignore lines", all(gi.count(w) == 1 for w in IGNORE_LINES), gi)


def main():
    try:
        for fn in (test_syntax_and_usage, test_rollout, test_plain_setup_has_no_factory, test_init_alone):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                check(fn.__name__ + " raised", False, "%s: %s" % (type(e).__name__, e))
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
    why = STATE_GUARD.problem()
    check("the real state dir's projects.json is untouched by this run", not why, why)
    fails = [r for r in RESULTS if not r[1]]
    print("total=%d  pass=%d  fail=%d" % (len(RESULTS), len(RESULTS) - len(fails), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
