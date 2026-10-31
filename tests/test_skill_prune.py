#!/usr/bin/env python3
"""bin/cc-setup prunes the skills cc_tool retired, and only when that is safe.

Each case builds a temp project, lays an old retired skill into .claude/skills/ from this
repo's own git history, runs bin/cc-setup on it (HOME and XDG_STATE_HOME point at temp
dirs; a StateGuard confirms the real projects.json is unchanged), and checks the result:
  - an unmodified old copy is removed, and the run says how many were pruned
  - a copy with one edited line stays, with a "Left untouched" line
  - a copy with an extra file stays
  - a same-named directory that matches no shipped version (a user's own skill) stays
  - a copy holding a symlink stays
  - the live skills are installed either way
and that a version only a side branch holds (the commits of a squashed release) counts as shipped:
  - a copy deployed from it is refreshed, and the file only that branch shipped goes
  - a user's file that merely shares that file's name stays

Run:  python3 tests/test_skill_prune.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETUP = os.path.join(REPO, "bin", "cc-setup")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from state_guard import StateGuard  # noqa: E402

GUARD = StateGuard()
BASE = tempfile.mkdtemp(prefix="cc-skill-prune-")
HOME = os.path.join(BASE, "home")
os.makedirs(HOME)
RETIRED = ("skills-audit", "dynamic-workflows", "e2e-testing", "ui-ux-review", "frontend-review")
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
        print("FAIL %s  %s" % (name, str(detail)[:400]))


def env():
    e = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    e["HOME"] = HOME
    e["XDG_STATE_HOME"] = os.path.join(BASE, "state")
    return e


def git(*args):
    return subprocess.run(["git", "-C", REPO, *args], capture_output=True, text=True).stdout


def old_copy(name):
    """{relative path: bytes} of the newest commit of this repo that shipped templates/skills/<name>/."""
    prefix = "templates/skills/%s/" % name
    for commit in git("log", "--format=%H", "--", prefix).split():
        paths = [l.partition("\t")[2] for l in git("ls-tree", "-r", commit, prefix).splitlines()]
        if paths:
            out = {}
            for p in paths:
                blob = subprocess.run(["git", "-C", REPO, "show", "%s:%s" % (commit, p)], capture_output=True)
                out[p[len(prefix):]] = blob.stdout
            return out
    return {}


def new_project(label, files_by_skill):
    proj = os.path.join(BASE, label)
    os.makedirs(proj)
    subprocess.run(["git", "init", "-q", proj], capture_output=True, env=env())
    for skill, files in files_by_skill.items():
        for rel, data in files.items():
            dest = os.path.join(proj, ".claude", "skills", skill, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as fh:
                fh.write(data)
    return proj


def setup(proj):
    return subprocess.run([SETUP, proj], capture_output=True, text=True, env=env(), stdin=subprocess.DEVNULL, timeout=600)


def skill_dir(proj, name):
    return os.path.join(proj, ".claude", "skills", name)


olds = {n: old_copy(n) for n in RETIRED}
for n, files in olds.items():
    check("history holds a shipped copy of %s" % n, bool(files) and "SKILL.md" in files)

# 1. every retired skill, unmodified: all pruned
proj = new_project("clean", olds)
r = setup(proj)
check("clean: cc-setup exits 0", r.returncode == 0, r.stderr[-300:] + r.stdout[-300:])
for n in RETIRED:
    check("clean: %s pruned" % n, not os.path.exists(skill_dir(proj, n)))
check("clean: the run reports the prune count", "pruned 5 retired" in r.stdout, r.stdout[-600:])
check("clean: no Left untouched line", "Left untouched: .claude/skills" not in r.stdout, r.stdout[-600:])
check("clean: app-qa is installed with its references", os.path.isfile(
    os.path.join(skill_dir(proj, "app-qa"), "references", "e2e.md")))
check("clean: loop-engineering carries the workflow patterns", os.path.isfile(
    os.path.join(skill_dir(proj, "loop-engineering"), "references", "workflow-patterns.md")))
r2 = setup(proj)
check("clean: second run exits 0 and prunes nothing", r2.returncode == 0 and "pruned" not in r2.stdout, r2.stdout[-400:])

# 2. one edited line: stays
edited = dict(olds["e2e-testing"])
edited["SKILL.md"] = edited["SKILL.md"].replace(b"E2E Testing", b"E2E Testing (mine)", 1)
check("edited: the fixture really differs", edited["SKILL.md"] != olds["e2e-testing"]["SKILL.md"])
proj = new_project("edited", {"e2e-testing": edited, "skills-audit": olds["skills-audit"]})
r = setup(proj)
check("edited: cc-setup exits 0", r.returncode == 0, r.stderr[-300:] + r.stdout[-300:])
check("edited: the edited copy stays", os.path.isfile(os.path.join(skill_dir(proj, "e2e-testing"), "SKILL.md")))
with open(os.path.join(skill_dir(proj, "e2e-testing"), "SKILL.md"), "rb") as fh:
    check("edited: its content is untouched", fh.read() == edited["SKILL.md"])
check("edited: one Left untouched line names it", r.stdout.count("Left untouched: .claude/skills/e2e-testing") == 1,
      r.stdout[-600:])
check("edited: the clean sibling is still pruned", not os.path.exists(skill_dir(proj, "skills-audit")))
check("edited: the run reports 1 pruned", "pruned 1 retired" in r.stdout, r.stdout[-600:])

# 3. an extra file: stays
proj = new_project("extra", {"ui-ux-review": olds["ui-ux-review"]})
with open(os.path.join(skill_dir(proj, "ui-ux-review"), "notes.md"), "w") as fh:
    fh.write("mine\n")
r = setup(proj)
check("extra: cc-setup exits 0", r.returncode == 0, r.stderr[-300:] + r.stdout[-300:])
check("extra: the copy and its extra file stay", os.path.isfile(os.path.join(skill_dir(proj, "ui-ux-review"), "notes.md"))
      and os.path.isfile(os.path.join(skill_dir(proj, "ui-ux-review"), "SKILL.md")))
check("extra: Left untouched line printed", "Left untouched: .claude/skills/ui-ux-review" in r.stdout, r.stdout[-600:])

# 4. a user's own skill that reuses a retired name: stays
mine = {"SKILL.md": b"---\nname: frontend-review\ndescription: my own\n---\n\nMy own review.\n"}
proj = new_project("own", {"frontend-review": mine})
r = setup(proj)
check("own: cc-setup exits 0", r.returncode == 0, r.stderr[-300:] + r.stdout[-300:])
with open(os.path.join(skill_dir(proj, "frontend-review"), "SKILL.md"), "rb") as fh:
    check("own: the user's skill stays untouched", fh.read() == mine["SKILL.md"])
check("own: Left untouched line printed", "Left untouched: .claude/skills/frontend-review" in r.stdout, r.stdout[-600:])

# 5. a symlink inside an otherwise unmodified copy (snapshot skips it): stays
proj = new_project("link", {"skills-audit": olds["skills-audit"]})
os.symlink(os.path.join(BASE, "home"), os.path.join(skill_dir(proj, "skills-audit"), "elsewhere"))
r = setup(proj)
check("link: cc-setup exits 0", r.returncode == 0, r.stderr[-300:] + r.stdout[-300:])
check("link: the copy holding a symlink stays", os.path.isfile(os.path.join(skill_dir(proj, "skills-audit"), "SKILL.md"))
      and os.path.islink(os.path.join(skill_dir(proj, "skills-audit"), "elsewhere")))
check("link: Left untouched line printed", "Left untouched: .claude/skills/skills-audit" in r.stdout, r.stdout[-600:])

# 6-7. versions that live only on a side branch, as the commits of a squashed release do. In a clone, so
# the history can hold a branch this checkout's HEAD never reaches.
CLONE = os.path.join(BASE, "cc")
subprocess.run(["git", "clone", "-q", REPO, CLONE], capture_output=True, env=env(), check=True)
shutil.copy2(SETUP, os.path.join(CLONE, "bin", "cc-setup"))   # this checkout's script over that history


def clone_git(*args):
    subprocess.run(["git", "-C", CLONE, "-c", "user.name=t", "-c", "user.email=t@t", *args], capture_output=True, env=env(), check=True)


template = os.path.join(CLONE, "templates", "skills", "factory", "SKILL.md")
with open(template, "rb") as fh:
    released = fh.read()
side_only = os.path.join("references", "side-only.md")
on_side = released + b"\nA line from a branch that was squashed.\n"
clone_git("switch", "-q", "-c", "side")
with open(template, "wb") as fh:
    fh.write(on_side)
os.makedirs(os.path.dirname(os.path.join(os.path.dirname(template), side_only)), exist_ok=True)
with open(os.path.join(os.path.dirname(template), side_only), "wb") as fh:
    fh.write(b"shipped only on the side branch\n")
clone_git("add", "templates/skills/factory")
clone_git("commit", "-q", "-m", "side")
clone_git("switch", "-q", "-")
newest = released + b"\nThe next release.\n"
with open(template, "wb") as fh:
    fh.write(newest)
clone_git("commit", "-q", "-am", "next")


def clone_setup(proj):
    return subprocess.run([os.path.join(CLONE, "bin", "cc-setup"), proj], capture_output=True, text=True, env=env(),
                          stdin=subprocess.DEVNULL, timeout=600)


def skill_file(proj, rel):
    with open(os.path.join(skill_dir(proj, "factory"), rel), "rb") as fh:
        return fh.read()


# 6. a copy deployed from that branch is a shipped copy, not a local edit, and its dropped file goes
proj = new_project("side", {"factory": {"SKILL.md": on_side, side_only: b"shipped only on the side branch\n"}})
r = clone_setup(proj)
check("side: cc-setup exits 0", r.returncode == 0, r.stderr[-300:] + r.stdout[-300:])
check("side: the copy is refreshed to the current template", skill_file(proj, "SKILL.md") == newest, r.stdout[-600:])
check("side: the file only that branch shipped is removed", not os.path.exists(os.path.join(skill_dir(proj, "factory"), side_only)))
check("side: it is not reported as a local edit", "locally modified" not in r.stdout, r.stdout[-600:])

# 7. a user's own file that only shares its name with that branch's file stays through a refresh
proj = new_project("namesake", {"factory": {"SKILL.md": released, side_only: b"my own notes\n"}})
r = clone_setup(proj)
check("namesake: cc-setup exits 0", r.returncode == 0, r.stderr[-300:] + r.stdout[-300:])
check("namesake: the skill itself is refreshed", skill_file(proj, "SKILL.md") == newest, r.stdout[-600:])
check("namesake: the user's file stays", os.path.exists(os.path.join(skill_dir(proj, "factory"), side_only))
      and skill_file(proj, side_only) == b"my own notes\n")

why = GUARD.problem()
check("the real projects.json is untouched", not why, why)
shutil.rmtree(BASE, ignore_errors=True)
print("%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
