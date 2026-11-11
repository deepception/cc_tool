"""A copy of the cc_tool tree for tests of gates from the dashboard (not a test itself).

These tests run under Claude Code, where no process can pass as a real dashboard, and nothing in
the real CLI turns that check off. So the allowed paths run a copy of the tree whose cc_procs.py is
the real file with three rules overridden (`make_tree`, like factory_cli_cases.make_tree): the parent
passes dashboard_problem, code_after_start and control_mode_problem. Everything else stays real in
the copy: the kernel's peer checks, the Claude Code variables, /proc, the pin, and the user-wide code record, which
`plant_record` writes into a state directory for that tree (a real copy of lib/, since the record refuses
symlinks). PASS_AS_CC_DASH leaves control_mode_problem real, for a parent that has to earn it.

  make_tree(base, name, extra)   the copy's bin/cc-factory; `extra` is appended to its cc_procs.py
  plant_record(project, root, state)
                                 <project>/.claude/hooks/{cc_tool_root,cc_dash_manifest.json} and <state>/cc_tool/cc_dash_code.json for checkout `root`
  claude_churn(base, threads)    context manager: short-lived processes under a program named `claude`, each
                                 unreadable in /proc/<pid>/fd for ~30 ms, then a zombie for ~50 ms (a headless
                                 `claude -p` exiting); yields an object whose `started` counts them
  python3 tests/gate_fixture.py churn SECONDS    the same loop on its own (default 600, at most 3600), to run
                                 the gate suites beside it; prints how many processes it started
"""
import contextlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROCS = os.path.join(REPO, "templates", "hooks", "cc_procs.py")
PASS_AS_CC_DASH = ('\n\ndef dashboard_problem(pid, root, proc="/proc", python=None, cache=None):\n    return ""\n'
                   '\n\ndef code_after_start(pid, root, manifest_path, proc="/proc", start=None, exempt=(), exempt_since=None):\n    return ""\n')
PASS_AS_DASHBOARD = PASS_AS_CC_DASH + '\n\ndef control_mode_problem(pid, proc="/proc"):\n    return ""\n'


def load_procs():
    spec = importlib.util.spec_from_file_location("cc_procs_gate_fixture", PROCS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_tree(base, name="gate", extra=PASS_AS_DASHBOARD):
    d = os.path.realpath(os.path.join(base, "tree-" + name))
    os.makedirs(os.path.join(d, "bin"))
    os.makedirs(os.path.join(d, "templates", "hooks"))
    for rel in ("bin/cc-factory", "bin/cc-dash"):
        shutil.copy(os.path.join(REPO, rel), os.path.join(d, rel))
    shutil.copytree(os.path.join(REPO, "lib"), os.path.join(d, "lib"), ignore=shutil.ignore_patterns("__pycache__"))
    os.symlink(os.path.join(REPO, "templates", "factory"), os.path.join(d, "templates", "factory"))
    with open(PROCS, encoding="utf-8") as fh:
        src = fh.read()
    with open(os.path.join(d, "templates", "hooks", "cc_procs.py"), "w", encoding="utf-8") as fh:
        fh.write(src + extra)
    return os.path.join(d, "bin", "cc-factory")


def plant_record(project, root, state=None):
    """What cc-setup leaves for checkout `root`: beside the project's hooks the pin and the old per-project manifest
    (no longer judged), and in `state` (default $XDG_STATE_HOME, which must be set: a test never touches the real
    one) the user-wide code record every check reads."""
    procs = load_procs()
    base = state or os.environ.get("XDG_STATE_HOME")
    if not base:
        raise RuntimeError("plant_record needs a state directory (state=, or XDG_STATE_HOME set to a fixture)")
    procs.write_code_record(root, procs.build_manifest(root, procs.python_record(), []),
                            path=os.path.join(base, "cc_tool", procs.CODE_RECORD_NAME))
    hooks = os.path.join(project, ".claude", "hooks")
    os.makedirs(hooks, exist_ok=True)
    for name, text in ((procs.PIN_NAME, os.path.realpath(root) + "\n"),
                       (procs.MANIFEST_NAME, json.dumps(procs.build_manifest(root, procs.python_record(), [])))):
        path = os.path.join(hooks, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(path, 0o644)      # whatever the umask: the readers refuse a file others can write


CHURN_CODE = "import ctypes, time; ctypes.CDLL(None).prctl(4, 0, 0, 0, 0); time.sleep(0.03)"


class Churn:
    started = 0


@contextlib.contextmanager
def claude_churn(base=None, threads=2):
    """Start `threads` loops that each run a non-dumpable child under a copy of /bin/sh named `claude`: its
    fd table cannot be read while it lives, and it stays a zombie until the shell reaps it."""
    base = base or tempfile.mkdtemp(prefix="cc-churn-")
    claude = os.path.join(base, "churn-bin", "claude")
    os.makedirs(os.path.dirname(claude), exist_ok=True)
    shutil.copy("/bin/sh", claude)
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    stop, churn, lock = threading.Event(), Churn(), threading.Lock()

    def loop():
        while not stop.is_set():
            subprocess.run([claude, "-c", '"$0" -I -c "$1" & sleep 0.08; wait', sys.executable, CHURN_CODE], env=env,
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            with lock:
                churn.started += 1

    ts = [threading.Thread(target=loop, daemon=True) for _ in range(threads)]
    for t in ts:
        t.start()
    try:
        yield churn
    finally:
        stop.set()
        for t in ts:
            t.join(timeout=10)


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "churn":
        secs = min(float(sys.argv[2]) if len(sys.argv) > 2 else 600.0, 3600.0)
        with claude_churn() as c:
            time.sleep(secs)
        print("churn: %d processes started" % c.started)
    else:
        print(__doc__)
