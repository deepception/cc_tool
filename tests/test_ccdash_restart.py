#!/usr/bin/env python3
"""`ccdash`, word for word as install.sh writes it, stops a running dashboard before it starts another.

A stub stands in for bin/cc-dash, so a real dashboard is never touched: the function's pattern is the
stub's path. In bash and zsh on this machine, each case checks:
  - the first ccdash starts one dashboard
  - a second ccdash stops it and leaves exactly one, the new one, with its arguments
  - a dashboard that ignores SIGTERM is killed after the wait, and the new one still starts
  - the same in zsh with ARGV0 exported (Cursor's terminal), and with USER unset
  - a dashboard that cannot be stopped, or a pgrep that fails, starts no second dashboard
  - a real cc-dash ends through its SIGTERM handler and comes back

Run:  python3 tests/test_ccdash_restart.py
"""
import atexit
import glob
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = tempfile.mkdtemp(prefix="cc-ccdash-restart-")
LINE = re.search(r"^CCDASH_LINE='(.*)'$", open(os.path.join(REPO, "install.sh"), encoding="utf-8").read(), re.M).group(1)
STUB = """#!/bin/bash
r="$(readlink -f "$0")"; [ "$0" = "$r" ] || exec /bin/bash "$r" "$@"
[ -n "$STUB_IGNORE_TERM" ] && trap '' TERM
echo "$$ $*" >> "$STUB_LOG"
exec 3>&-
while :; do sleep 0.1; done
"""
passed = failed = 0


def running(pattern):
    return [int(p) for p in subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True).stdout.split()]


def cleanup():
    """Runs however the test ends, so a regression leaves no stub or dashboard behind."""
    pids = running(BASE)
    for log in glob.glob(os.path.join(BASE, "*", "started")):
        pids += [pid for pid, _ in started(log)]
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    shutil.rmtree(BASE, ignore_errors=True)


atexit.register(cleanup)


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
        print("FAIL %s  %s" % (name, str(detail)[:400]))


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    with open("/proc/%d/stat" % pid) as fh:     # a killed stub stays a zombie until init reaps it
        return fh.read().rsplit(")", 1)[1].split()[0] != "Z"


def started(log):
    with open(log) as fh:
        return [(int(ln.split(" ", 1)[0]), ln.split(" ", 1)[1].strip()) for ln in fh]


def ccdash(shell, stub_dir, log, *args, ignore_term=False, set_env=None, drop_env=()):
    env = {k: v for k, v in os.environ.items() if k != "STUB_IGNORE_TERM" and k not in drop_env}
    env.update(PATH=stub_dir + ":" + os.environ["PATH"], STUB_LOG=log, XDG_STATE_HOME=os.path.join(stub_dir, "state"))
    env.update(set_env or {})
    if ignore_term:
        env["STUB_IGNORE_TERM"] = "1"
    return subprocess.run([shell, "-c", LINE + "\nccdash " + " ".join(args)], capture_output=True, text=True, env=env,
                          stdin=subprocess.DEVNULL, timeout=60)


shells = [s for s in ("bash", "zsh") if shutil.which(s)]
check("a shell to run ccdash in", bool(shells))
for shell in shells:
    stub_dir = os.path.join(BASE, shell)
    os.makedirs(stub_dir)
    stub = os.path.join(stub_dir, "cc-dash")
    with open(stub, "w") as fh:
        fh.write(STUB)
    os.chmod(stub, stat.S_IRWXU)
    log = os.path.join(stub_dir, "started")

    r = ccdash(shell, stub_dir, log)
    check("%s: the first ccdash starts a dashboard" % shell, r.returncode == 0 and len(started(log)) == 1, r.stderr)
    first = started(log)[0][0]
    check("%s: it is running" % shell, alive(first))

    r = ccdash(shell, stub_dir, log, "--control")
    runs = started(log)
    check("%s: the second ccdash starts another, with its arguments" % shell, r.returncode == 0 and len(runs) == 2
          and "--control" in runs[1][1], (r.stderr, runs))
    check("%s: the first dashboard was stopped" % shell, not alive(first))
    second = runs[1][0]
    check("%s: exactly the new one is left" % shell, alive(second))

    r = ccdash(shell, stub_dir, log, ignore_term=True)
    stubborn = started(log)[2][0]
    check("%s: a third starts in the same way" % shell, r.returncode == 0 and alive(stubborn) and not alive(second), r.stderr)
    t0 = time.time()
    r = ccdash(shell, stub_dir, log)
    took = time.time() - t0
    runs = started(log)
    check("%s: a dashboard that ignores SIGTERM is killed after the wait" % shell, not alive(stubborn) and took >= 9, took)
    check("%s: and the new one still starts" % shell, r.returncode == 0 and len(runs) == 4 and alive(runs[3][0]), (r.stderr, runs))
    for pid, _ in runs:
        if alive(pid):
            os.kill(pid, signal.SIGKILL)

# cc-dash re-executes itself as `python -I <realpath>`, so a cc-dash reached through a symlink on PATH shows
# a different path in the process list than `command -v` prints.
for shell in shells:
    real_dir, link_dir = (os.path.join(BASE, "%s-%s" % (shell, n)) for n in ("real", "link"))
    os.makedirs(real_dir)
    os.makedirs(link_dir)
    with open(os.path.join(real_dir, "cc-dash"), "w") as fh:
        fh.write(STUB)
    os.chmod(os.path.join(real_dir, "cc-dash"), stat.S_IRWXU)
    os.symlink(os.path.join(real_dir, "cc-dash"), os.path.join(link_dir, "cc-dash"))
    log = os.path.join(link_dir, "started")
    ccdash(shell, link_dir, log)
    first = started(log)[0][0]
    r = ccdash(shell, link_dir, log)
    runs = started(log)
    check("%s: a symlinked cc-dash is stopped by the next ccdash" % shell, not alive(first), runs)
    check("%s: and exactly the new one is left" % shell, len(runs) == 2 and alive(runs[1][0]), runs)
    for pid, _ in runs:
        if alive(pid):
            os.kill(pid, signal.SIGKILL)


def stub_in(directory):
    os.makedirs(directory)
    with open(os.path.join(directory, "cc-dash"), "w") as fh:
        fh.write(STUB)
    os.chmod(os.path.join(directory, "cc-dash"), stat.S_IRWXU)
    return os.path.join(directory, "started")


def pgrep_stub(directory, body):
    os.makedirs(directory)
    with open(os.path.join(directory, "pgrep"), "w") as fh:
        fh.write("#!/bin/sh\n" + body)
    os.chmod(os.path.join(directory, "pgrep"), stat.S_IRWXU)
    return directory


# Cursor's AppImage terminal exports ARGV0, and zsh runs every external command under that name. pkill is pgrep
# picking its mode from argv[0], so there it printed pids, signalled nothing and exited 0, and pkill -KILL died on
# a usage error. The stop must not depend on what an external command is called.
CURSOR = {"ARGV0": "/home/lukasz/Files/applications/Cursor.AppImage"}
if "zsh" in shells:
    stub_dir = os.path.join(BASE, "argv0")
    log = stub_in(stub_dir)
    ccdash("zsh", stub_dir, log, ignore_term=True, set_env=CURSOR)
    stubborn = started(log)[0][0]
    r = ccdash("zsh", stub_dir, log, set_env=CURSOR)
    runs = started(log)
    check("ARGV0: a dashboard that ignores SIGTERM is killed", not alive(stubborn), (r.stdout, r.stderr))
    check("ARGV0: the next one starts", r.returncode == 0 and len(runs) == 2 and alive(runs[1][0]), (r.stdout, r.stderr, runs))
    r = ccdash("zsh", stub_dir, log, "--days", "3", set_env=CURSOR)
    runs = started(log)
    check("ARGV0: a dashboard that takes SIGTERM is stopped", not alive(runs[1][0]), (r.stdout, r.stderr))
    check("ARGV0: its replacement starts with its arguments", r.returncode == 0 and len(runs) == 3 and "--days 3" in runs[2][1]
          and alive(runs[2][0]), (r.stdout, r.stderr, runs))
    check("ARGV0: no pids or usage text on the terminal", r.stdout.startswith("cc-dash is opening") and not r.stderr,
          (r.stdout, r.stderr))

# $USER is not a given (bash does not set it), and an empty -u is a pgrep usage error.
for shell in shells:
    stub_dir = os.path.join(BASE, "%s-nouser" % shell)
    log = stub_in(stub_dir)
    ccdash(shell, stub_dir, log, drop_env=("USER", "LOGNAME"))
    r = ccdash(shell, stub_dir, log, drop_env=("USER", "LOGNAME"))
    runs = started(log)
    check("%s: with USER unset the old dashboard is stopped" % shell, not alive(runs[0][0]), (r.stderr, runs))
    check("%s: with USER unset the new one starts" % shell, r.returncode == 0 and len(runs) == 2, (r.stderr, runs))

# Fail closed: a pgrep that errors says nothing about what is running, and a pid that outlives SIGKILL is running.
# Starting anyway would leave two dashboards and a lease that names the wrong one.
for shell in shells:
    stub_dir = os.path.join(BASE, "%s-closed" % shell)
    log = stub_in(stub_dir)
    broken = pgrep_stub(os.path.join(stub_dir, "broken"), "echo 'pgrep: broken' >&2\nexit 3\n")
    stuck = pgrep_stub(os.path.join(stub_dir, "stuck"), "echo 99999999\nexit 0\n")
    ccdash(shell, stub_dir, log)
    first = started(log)[0][0]
    r = ccdash(shell, broken + ":" + stub_dir, log)
    runs = started(log)
    check("%s: a failing pgrep starts no second dashboard" % shell, len(runs) == 1 and alive(first), runs)
    check("%s: ccdash fails and says why" % shell, r.returncode != 0 and "pgrep" in r.stderr, r.stderr)
    r = ccdash(shell, stuck + ":" + stub_dir, log)
    runs = started(log)
    check("%s: a pid that will not die starts no second dashboard" % shell, len(runs) == 1, runs)
    check("%s: ccdash fails and names it" % shell, r.returncode != 0 and "99999999" in r.stderr, r.stderr)

# The real program in the form it really runs (`python -I <path>`), from a copy so no other dashboard matches.
# Control mode refuses to start under Claude Code, so this is the plain mode; both end through the same handler.
for shell in shells:
    real = os.path.join(BASE, "%s-program" % shell)
    bin_dir = os.path.join(real, "bin")
    os.makedirs(bin_dir)
    program = shutil.copy2(os.path.join(REPO, "bin", "cc-dash"), bin_dir)
    shutil.copytree(os.path.join(REPO, "lib"), os.path.join(real, "lib"), ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(os.path.join(REPO, "templates", "hooks"), os.path.join(real, "templates", "hooks"))
    dash_log = os.path.join(bin_dir, "state", "cc_tool", "cc-dash.log")
    env_over = dict(CURSOR, CLAUDE_CONFIG_DIR=os.path.join(real, "claude"))
    args = ("--no-open", "--port", "0")

    def serving(banners):
        for _ in range(100):
            if open(dash_log).read().count("cc-dash: serving") >= banners:
                return True
            time.sleep(0.1)
        return False

    ccdash(shell, bin_dir, "/dev/null", *args, set_env=env_over)
    first = running(program)
    check("%s: a real cc-dash is serving" % shell, len(first) == 1 and serving(1), (first, open(dash_log).read()))
    r = ccdash(shell, bin_dir, "/dev/null", *args, set_env=env_over)
    second = running(program)
    check("%s: exactly a new real cc-dash is left" % shell, len(second) == 1 and second != first, (first, second, r.stderr))
    check("%s: the old one ended through its SIGTERM handler, which drops the lease" % shell,
          "cc-dash: stopped." in open(dash_log).read(), open(dash_log).read())
    check("%s: the new one is serving" % shell, serving(2), open(dash_log).read())
    for pid in second:
        os.kill(pid, signal.SIGKILL)

# A dashboard started from a terminal app inherits the app's pty masters; they must not survive into cc-dash,
# or it looks like the holder of those terminals to the control lease check.
probe = (
    "import os, sys; sys.path.insert(0, %r)\n"
    "from cc_dash.server import close_inherited_fds\n"
    "m, s = os.openpty(); r, w = os.pipe()\n"
    "close_inherited_fds({r})\n"
    "print(os.path.exists('/proc/self/fd/%%d' %% m), os.path.exists('/proc/self/fd/%%d' %% r),"
    " os.path.exists('/proc/self/fd/0'))\n"
) % os.path.join(REPO, "lib")
r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
check("an inherited pty master is closed, the kept descriptor and stdio stay", r.stdout.split() == ["False", "True", "True"], r)

print("%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
