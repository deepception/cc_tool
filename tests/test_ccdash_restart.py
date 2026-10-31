#!/usr/bin/env python3
"""`ccdash`, word for word as install.sh writes it, stops a running dashboard before it starts another.

A stub stands in for bin/cc-dash, so a real dashboard is never touched: the function's pattern is the
stub's path. In bash and zsh on this machine, each case checks:
  - the first ccdash starts one dashboard
  - a second ccdash stops it and leaves exactly one, the new one, with its arguments
  - a dashboard that ignores SIGTERM is killed after the wait, and the new one still starts

Run:  python3 tests/test_ccdash_restart.py
"""
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
[ -n "$STUB_IGNORE_TERM" ] && trap '' TERM
echo "$$ $*" >> "$STUB_LOG"
exec 3>&-
while :; do sleep 0.1; done
"""
passed = failed = 0


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


def ccdash(shell, stub_dir, log, *args, ignore_term=False):
    env = {k: v for k, v in os.environ.items() if k != "STUB_IGNORE_TERM"}
    env.update(PATH=stub_dir + ":" + os.environ["PATH"], STUB_LOG=log, XDG_STATE_HOME=os.path.join(stub_dir, "state"))
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

shutil.rmtree(BASE, ignore_errors=True)
print("%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
