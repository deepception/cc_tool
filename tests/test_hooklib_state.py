#!/usr/bin/env python3
"""cc_hooklib's state directory for a git worktree (where .git is a file).

It used to be <tempdir>/cc_tool/<tag>: shared /tmp, created without a mode or an
ownership check, written with plain open(), so another local user could prepare
the directory or plant symlinks in it. Now it is
${XDG_STATE_HOME:-~/.local/state}/cc_tool/repos/<tag>, 0700, used only when lstat
shows a private directory of ours, and every file is opened with O_NOFOLLOW.

Each case runs the helpers in a fresh process with its own XDG_STATE_HOME, HOME
and TMPDIR, and plants the same thing at the old /tmp location too, so a version
that still used it fails here.

Run:  python3 tests/test_hooklib_state.py
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.realpath("/proc/self/exe") if os.path.exists("/proc/self/exe") else sys.executable
HOOKS = os.path.join(REPO, "templates", "hooks")
BASE = tempfile.mkdtemp(prefix="cc-hooklib-state-")
RESULTS = []

RUN = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
if sys.argv[3] == "foreign":
    real = os.getuid()
    os.getuid = lambda: real + 1          # every directory and file now belongs to "someone else"
import cc_hooklib as lib
root = sys.argv[2]
lib.log_event(root, "test", message="hello")
lib.save_session(root, "s1", {"seen": {"/x": 1}, "failed": {}, "notes": {}})
print(json.dumps({"dir": lib.state_dir(root), "session": lib.load_session(root, "s1")}))
"""


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), str(detail)))


def files_under(d):
    out = []
    for dp, _dn, fn in os.walk(d):
        out += [os.path.join(dp, f) for f in fn]
    return out


def case(tag, plant=None, mode="normal"):
    """(result json, xdg dir, the attacker's dir) after running the helpers with `plant`
    applied to the new and the old location first."""
    top = os.path.join(BASE, tag)
    xdg, home, tmp, attacker = (os.path.join(top, n) for n in ("xdg", "home", "tmp", "attacker"))
    root = os.path.join(top, "wt")
    for d in (xdg, home, tmp, attacker, root):
        os.makedirs(d)
    with open(os.path.join(root, ".git"), "w") as fh:          # a worktree: .git is a file
        fh.write("gitdir: /nowhere\n")
    t = hashlib.sha1(root.encode()).hexdigest()[:12]
    new, old = os.path.join(xdg, "cc_tool", "repos", t), os.path.join(tmp, "cc_tool", t)
    if plant:
        for d in (new, old):
            plant(d, attacker)
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    env.update(XDG_STATE_HOME=xdg, HOME=home, TMPDIR=tmp)
    p = subprocess.run([PY, "-c", RUN, HOOKS, root, mode], env=env, capture_output=True, text=True, timeout=30)
    try:
        out = json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        out = {"error": p.stderr[-400:]}
    return out, new, old, attacker


def link_dir(d, attacker):
    os.makedirs(os.path.dirname(d), exist_ok=True)
    os.symlink(attacker, d)


def loose_dir(d, attacker):
    os.makedirs(d)
    os.chmod(d, 0o777)


def link_files(d, attacker):
    os.makedirs(d, mode=0o700)
    os.chmod(d, 0o700)
    for name in ("activity.jsonl", "session-s1.json"):
        os.symlink(os.path.join(attacker, name), os.path.join(d, name))


def main():
    try:
        out, new, old, attacker = case("plain")
        check("a worktree's state goes to ${XDG_STATE_HOME}/cc_tool/repos/<tag>, 0700",
              out.get("dir") == new and os.path.isfile(os.path.join(new, "activity.jsonl"))
              and (os.lstat(new).st_mode & 0o777) == 0o700, out)
        check("nothing is written under the old shared temp location", not os.path.exists(old), files_under(os.path.dirname(old)))
        check("session state round-trips", out.get("session", {}).get("seen") == {"/x": 1}, out)
        check("the files are 0600", all((os.lstat(f).st_mode & 0o777) == 0o600 for f in files_under(new)),
              [(f, oct(os.lstat(f).st_mode)) for f in files_under(new)])

        out, new, old, attacker = case("symlinked-dir", link_dir)
        check("a symlink planted at the state directory is not used: nothing lands in its target",
              out.get("dir") == "" and files_under(attacker) == [], (out, files_under(attacker)))

        out, new, old, attacker = case("loose", loose_dir)
        check("a state directory open to others (0777) is not used: nothing written in it",
              out.get("dir") == "" and files_under(new) == [] and files_under(old) == [], (out, files_under(new), files_under(old)))

        out, new, old, attacker = case("foreign", mode="foreign")
        check("a state directory owned by someone else is not used: nothing written",
              out.get("dir") == "" and files_under(new) == [] and not os.path.exists(old),
              (out, files_under(os.path.dirname(new)), files_under(os.path.dirname(old)) if os.path.exists(old) else []))

        out, new, old, attacker = case("symlinked-files", link_files)
        check("symlinks planted for the activity log and a session file are not followed (O_NOFOLLOW)",
              files_under(attacker) == [], (out, files_under(attacker)))
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
    fails = [r for r in RESULTS if not r[1]]
    print("total=%d  pass=%d  fail=%d" % (len(RESULTS), len(RESULTS) - len(fails), len(fails)))
    for name, _ok, detail in fails:
        print("  FAIL %-70s %s" % (name, detail[:500]))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
