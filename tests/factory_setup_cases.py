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
  - plain `cc-setup <proj>` scaffolds the factory by default; --no-factory writes
    .claude/no-factory and later plain runs and updates honour it; --factory clears it;
    a non-git project, a missing /proc (CC_SETUP_PROC_STATUS, test-only) skip with exit 0;
    an init failure warns in default mode and aborts under --factory; reruns change nothing
  - .cc-board/ is git-ignored once across setup and update
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


def snapshot_tree(proj):
    out = {}
    # settings.json is skipped: cc-update-permissions rewrites it on an update, which is not this task's concern.
    for d, dirs, files in os.walk(proj):
        dirs[:] = [x for x in dirs if x != ".git"]
        for f in files:
            p = os.path.join(d, f)
            if f == "settings.json":
                continue
            out[os.path.relpath(p, proj)] = open(p, "rb").read()
    return out


def test_default_on():
    if shutil.which("git") is None:
        return
    proj = new_project("plain")
    r = run([SETUP, proj])
    check("default: cc-setup without a flag exits 0", r.returncode == 0, tail(r))
    check("default: the Factory banner is printed", "── Factory ──" in r.stdout, r.stdout[-400:])
    check_scaffold("default", proj)
    check("default: no opt-out marker", not os.path.exists(os.path.join(proj, ".claude", "no-factory")))
    check("default: factory-line.js is installed",
          os.path.isfile(os.path.join(proj, ".claude", "workflows", "factory-line.js")))
    skill = os.path.join(proj, ".claude", "skills", "factory", "SKILL.md")
    check("default: the /factory skill ships", os.path.isfile(skill), skill)
    # A rerun and an update change nothing.
    before = snapshot_tree(proj)
    r = run([SETUP, proj])
    check("default: a rerun exits 0", r.returncode == 0, tail(r))
    r2 = run([os.path.join(REPO, "bin", "cc-update-project"), proj])
    check("default: cc-update-project exits 0", r2.returncode == 0, tail(r2))
    check("default: a rerun and an update change no file", snapshot_tree(proj) == before,
          sorted(k for k, v in snapshot_tree(proj).items() if before.get(k) != v))
    # The run-state board (the managed block's "Run-state board") is git-ignored in every project, once.
    gi = lines_of(os.path.join(proj, ".gitignore"))
    check("default: .cc-board/ is git-ignored exactly once after setup and update", gi.count(".cc-board/") == 1, gi)
    os.makedirs(os.path.join(proj, ".cc-board", "run", "lead-a"), exist_ok=True)
    r = run(["git", "-C", proj, "check-ignore", "-q", ".cc-board/run/lead-a/checkpoint.md"])
    check("default: git ignores a board file", r.returncode == 0, tail(r))
    # An update on a project with a factory but no workflow copy installs it (a hand-edited copy is kept: init's rule).
    js = os.path.join(proj, ".claude", "workflows", "factory-line.js")
    os.remove(js)
    run([os.path.join(REPO, "bin", "cc-update-project"), proj])
    if os.path.isfile(WORKFLOW_SRC):
        check("default: cc-update-project restores factory-line.js",
              os.path.isfile(js) and open(js, "rb").read() == open(WORKFLOW_SRC, "rb").read())


def test_opt_out():
    if shutil.which("git") is None:
        return
    proj = new_project("optout")
    marker = os.path.join(proj, ".claude", "no-factory")
    r = run([SETUP, proj, "--no-factory"])
    check("opt-out: --no-factory exits 0", r.returncode == 0, tail(r))
    check("opt-out: the marker is written", os.path.isfile(marker))
    check("opt-out: no factory/ and no Factory banner",
          not os.path.exists(os.path.join(proj, "factory")) and "── Factory ──" not in r.stdout, r.stdout[-300:])
    before = snapshot_tree(proj)
    r = run([SETUP, proj])
    check("opt-out: a plain rerun exits 0 and says it skipped", r.returncode == 0 and "factory skipped" in r.stdout, tail(r))
    check("opt-out: a plain rerun scaffolds nothing", not os.path.exists(os.path.join(proj, "factory")))
    r = run([os.path.join(REPO, "bin", "cc-update-project"), proj])
    check("opt-out: cc-update-project honours the marker",
          r.returncode == 0 and not os.path.exists(os.path.join(proj, "factory")), tail(r))
    check("opt-out: reruns change no file", snapshot_tree(proj) == before,
          sorted(k for k, v in snapshot_tree(proj).items() if before.get(k) != v))
    r = run([SETUP, proj, "--factory"])
    check("opt-out: --factory exits 0, removes the marker and scaffolds",
          r.returncode == 0 and not os.path.exists(marker) and os.path.isfile(os.path.join(proj, "factory", "line.json")),
          tail(r))
    r = run([SETUP, proj, "--factory", "--no-factory"], timeout=60)
    check("opt-out: --factory with --no-factory exits 1", r.returncode == 1, tail(r))
    # cc-update-project passes --no-factory on.
    proj2 = new_project("optout-update")
    run([SETUP, proj2, "--no-factory"])
    os.remove(os.path.join(proj2, ".claude", "no-factory"))
    r = run([os.path.join(REPO, "bin", "cc-update-project"), proj2, "--no-factory"])
    check("opt-out: cc-update-project --no-factory exits 0 and writes the marker",
          r.returncode == 0 and os.path.isfile(os.path.join(proj2, ".claude", "no-factory"))
          and not os.path.exists(os.path.join(proj2, "factory")), tail(r))


def test_marker_symlinks():
    if shutil.which("git") is None:
        return
    # A planted marker symlink must not be written through.
    proj = new_project("marker-link")
    victim = os.path.join(BASE, "victim.txt")
    with open(victim, "wb") as fh:
        fh.write(b"precious\n")
    os.makedirs(os.path.join(proj, ".claude"))
    marker = os.path.join(proj, ".claude", "no-factory")
    os.symlink(victim, marker)
    r = run([SETUP, proj, "--no-factory"])
    check("symlink: --no-factory with a marker symlink exits 0 and says so",
          r.returncode == 0 and "is a symlink" in r.stdout, tail(r))
    check("symlink: the link target's bytes are unchanged", open(victim, "rb").read() == b"precious\n")
    check("symlink: no factory/ was scaffolded", not os.path.exists(os.path.join(proj, "factory")))
    # --factory removes the link itself, never its target.
    r = run([SETUP, proj, "--factory"])
    check("symlink: --factory removes the link, keeps the target",
          r.returncode == 0 and not os.path.lexists(marker) and open(victim, "rb").read() == b"precious\n", tail(r))
    # A dangling marker symlink still counts as opted out.
    proj = new_project("marker-dangling")
    os.makedirs(os.path.join(proj, ".claude"))
    os.symlink(os.path.join(BASE, "does-not-exist"), os.path.join(proj, ".claude", "no-factory"))
    r = run([SETUP, proj])
    check("symlink: a dangling marker symlink counts as opted out",
          r.returncode == 0 and "factory skipped" in r.stdout and not os.path.exists(os.path.join(proj, "factory")),
          tail(r))
    check("symlink: the dangling target was not created", not os.path.lexists(os.path.join(BASE, "does-not-exist")))
    # A symlinked .claude/ is refused for the marker.
    proj = new_project("claude-link")
    real = os.path.join(BASE, "real-claude")
    os.makedirs(real)
    os.symlink(real, os.path.join(proj, ".claude"))
    r = run([SETUP, proj, "--no-factory"])
    check("symlink: a symlinked .claude/ is refused for the marker",
          r.returncode == 0 and ".claude is a symlink" in r.stdout and not os.path.lexists(os.path.join(real, "no-factory")),
          tail(r))


def test_safe_skips():
    if shutil.which("git") is None:
        return
    nogit = os.path.join(BASE, "nogit")
    os.makedirs(nogit)
    r = run([SETUP, nogit])
    check("skip: a non-git project exits 0", r.returncode == 0, tail(r))
    check("skip: a non-git project says why and gets no factory/",
          "factory skipped: not a git work tree" in r.stdout and not os.path.exists(os.path.join(nogit, "factory")),
          r.stdout[-300:])
    proj = new_project("noproc")
    e = env()
    e["CC_SETUP_PROC_STATUS"] = os.path.join(BASE, "no-such-proc-status")    # test-only stand-in for /proc/self/status
    r = subprocess.run([SETUP, proj], capture_output=True, text=True, env=e, stdin=subprocess.DEVNULL, timeout=600)
    check("skip: a missing /proc exits 0 with the Linux line",
          r.returncode == 0 and "factory skipped: its gates need Linux /proc; this project stays on Superpowers" in r.stdout,
          tail(r))
    check("skip: a missing /proc scaffolds nothing", not os.path.exists(os.path.join(proj, "factory")))
    # An init failure in default mode warns and setup carries on; under --factory it aborts.
    proj = new_project("initfail")
    os.symlink(os.path.join(BASE, "elsewhere"), os.path.join(proj, "factory"))   # init refuses a symlinked factory/
    r = run([SETUP, proj])
    check("skip: an init failure in default mode warns and exits 0",
          r.returncode == 0 and "factory scaffold failed" in r.stdout, tail(r))
    r = run([SETUP, proj, "--factory"])
    check("skip: the same failure under --factory aborts", r.returncode != 0, tail(r))


def commit_empty(repo):
    subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init"],
                   capture_output=True, env=env())


def test_enclosing_repo():
    if shutil.which("git") is None:
        return
    # A package inside a repository: skipped with one line, nothing written above it.
    mono = new_project("mono")
    pkg = os.path.join(mono, "pkg")
    os.makedirs(pkg)
    r = run([SETUP, pkg])
    check("enclosing: a subdirectory of a repo exits 0 and names the repository",
          r.returncode == 0 and "factory skipped: " in r.stdout and "is inside the git repository" in r.stdout
          and os.path.realpath(mono) in r.stdout and "pass --factory" in r.stdout, tail(r))
    check("enclosing: no factory/ or .gitignore appears above or in the subdirectory",
          not os.path.exists(os.path.join(mono, "factory")) and not os.path.exists(os.path.join(mono, ".gitignore"))
          and not os.path.exists(os.path.join(pkg, "factory")), os.listdir(mono))
    # A $HOME-style dotfiles repo around a project directory.
    dots = new_project("dotfiles")
    proj = os.path.join(dots, "code", "app")
    os.makedirs(proj)
    r = run([SETUP, proj])
    check("enclosing: a project under a dotfiles-style repo is skipped and writes nothing above",
          r.returncode == 0 and "is inside the git repository" in r.stdout and not os.path.exists(os.path.join(dots, "factory"))
          and not os.path.exists(os.path.join(dots, ".gitignore")), tail(r))
    # --factory keeps today's behaviour on the subdirectory.
    r = run([SETUP, pkg, "--factory"])
    check("enclosing: --factory on a subdirectory still scaffolds", r.returncode == 0
          and os.path.isfile(os.path.join(mono, "factory", "line.json")), tail(r))
    # A marker at the root of a repo is honoured from a linked worktree.
    main_ = new_project("wt-main")
    commit_empty(main_)
    r = run([SETUP, main_, "--no-factory"])
    check("enclosing: the main checkout opts out", os.path.isfile(os.path.join(main_, ".claude", "no-factory")), tail(r))
    wt = os.path.join(BASE, "wt-linked")
    r = run(["git", "-C", main_, "worktree", "add", "-q", "-b", "side", wt])
    check("enclosing: the linked worktree was made", r.returncode == 0, tail(r))
    r = run([SETUP, wt])
    check("enclosing: a linked worktree honours the marker at the main checkout",
          r.returncode == 0 and "factory skipped: .claude/no-factory exists in the main checkout" in r.stdout
          and not os.path.exists(os.path.join(wt, "factory")), tail(r))
    # Without a marker anywhere, a linked worktree is its own top level and scaffolds.
    main2 = new_project("wt-main2")
    commit_empty(main2)
    wt2 = os.path.join(BASE, "wt-linked2")
    run(["git", "-C", main2, "worktree", "add", "-q", "-b", "side2", wt2])
    r = run([SETUP, wt2])
    check("enclosing: a linked worktree with no marker scaffolds (init puts the scaffold in the main checkout)",
          r.returncode == 0 and "── Factory ──" in r.stdout and os.path.isfile(os.path.join(main2, "factory", "line.json")), tail(r))
    # --no-factory on a project that already has the scaffold says it stays.
    kept = new_project("scaffold-kept")
    run([SETUP, kept])
    r = run([SETUP, kept, "--no-factory"])
    check("enclosing: --no-factory on a scaffolded project says the scaffold and routing stay",
          r.returncode == 0 and "routing stays on until you remove factory/line.json" in r.stdout, tail(r))


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
        for fn in (test_syntax_and_usage, test_rollout, test_default_on, test_opt_out, test_marker_symlinks, test_safe_skips, test_enclosing_repo, test_init_alone):
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
