#!/usr/bin/env python3
"""Allow/ask/warn/deny matrix for templates/hooks/write-guard.py.

Covers location rules (system paths, home dotfiles, .git/, outside-repo, the
repo's other git worktrees),
secrets in proposed content (real-looking vs placeholder), the trajectory
warnings (stale read, red check pending) seeded through cc_hooklib, and
project rules from .claude/guard-rules.json. Nothing is ever written by the
guard itself; fixtures live in a temp dir and are removed afterwards.

Run:  python3 tests/test_write_guard.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The interpreter is the kernel's: with zsh's exported ARGV0 (Cursor's terminal), sys.executable
# can name the editor's AppImage, and running it opens editor tabs instead of Python.
PY = os.path.realpath("/proc/self/exe") if os.path.exists("/proc/self/exe") else sys.executable
HOOKS = os.path.join(REPO, "templates", "hooks")
GUARD = os.path.join(HOOKS, "write-guard.py")
sys.path.insert(0, HOOKS)
import cc_hooklib as lib  # noqa: E402

D, A, K, W = "DENY", "ALLOW", "ASK", "WARN"


def _git(cwd, *args):
    subprocess.run(("git",) + args, cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _repo(path):
    os.makedirs(os.path.join(path, "src"), exist_ok=True)
    _git(path, "init", "-q", "-b", "feature/w")
    open(os.path.join(path, "src", "a.ts"), "w").write("export const a = 1;\n")
    open(os.path.join(path, "src", "b.ts"), "w").write("export const b = 2;\n")
    return path


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from state_guard import StateGuard  # noqa: E402  (tests/state_guard.py)
STATE_GUARD = StateGuard()
BASE = tempfile.mkdtemp(prefix="writeguard-matrix-")
# Every hook run here, and the paths the cases name, use a temp state dir, never the real one.
# Not under /tmp (scratch paths are allowed by design); removed in the finally below.
STATE = os.path.join(REPO, "tests", ".wg-state")
os.environ["XDG_STATE_HOME"] = STATE
ROOT = _repo(os.path.join(BASE, "repo"))
# Must NOT live under /tmp (scratch paths are allowed by design), so it sits in
# the tests dir and is removed in the finally below.
OUTSIDE = os.path.join(REPO, "tests", ".wg-outside")
os.makedirs(OUTSIDE, exist_ok=True)
WARNROOT = _repo(os.path.join(BASE, "repo_warn"))
os.makedirs(os.path.join(WARNROOT, ".claude"), exist_ok=True)
json.dump({"write_outside_repo": "warn", "rules": [
    {"id": "no-minified", "tool": ["Write", "Edit", "MultiEdit"], "field": "file_path",
     "regex": r"\.min\.js$", "action": "deny", "reason": "Minified files are build output.",
     "suggestion": "edit the source and rebuild"},
    {"id": "no-console", "tool": ["Write", "Edit", "MultiEdit"], "field": "content",
     "regex": r"\bconsole\.log\(", "action": "warn", "reason": "Use the logger."},
]}, open(os.path.join(WARNROOT, ".claude", "guard-rules.json"), "w"))
HOME = os.path.expanduser("~")
ASKROOT = _repo(os.path.join(BASE, "repo_ask"))
os.makedirs(os.path.join(ASKROOT, ".claude"), exist_ok=True)
json.dump({"write_outside_repo": "ask"}, open(os.path.join(ASKROOT, ".claude", "guard-rules.json"), "w"))

# Worktree fixtures. Like OUTSIDE they must not live under /tmp, or the scratch
# rule would allow them before the worktree rule is ever consulted.
WT_MAIN = _repo(os.path.join(REPO, "tests", ".wg-wt-main"))
WT_LINKED = os.path.join(REPO, "tests", ".wg-wt-linked")
_git(WT_MAIN, "add", "-A")
_git(WT_MAIN, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-qm", "init")
_git(WT_MAIN, "worktree", "add", "-q", "-b", "wt/linked", WT_LINKED)

A_TS = os.path.join(ROOT, "src", "a.ts")
B_TS = os.path.join(ROOT, "src", "b.ts")
SESSION = "wg-test-session"

# Trajectory seeds: a.ts "seen" 100s before its real mtime (stale), b.ts seen at its mtime.
_state = lib.load_session(ROOT, SESSION)
_state["seen"][os.path.realpath(A_TS)] = lib.file_mtime(A_TS) - 100
_state["seen"][os.path.realpath(B_TS)] = lib.file_mtime(B_TS)
lib.save_session(ROOT, SESSION, _state)
RED = "wg-red-session"
_red = lib.load_session(ROOT, RED)
_red["failed"][os.path.realpath(A_TS)] = time.time()
lib.save_session(ROOT, RED, _red)

_XDG = os.environ.get("XDG_STATE_HOME") or ""
CC_STATE = os.path.join(_XDG if os.path.isabs(_XDG) else os.path.join(HOME, ".local", "state"), "cc_tool")

CTL_LINK = os.path.join(ROOT, "src", "ctl-link")      # a symlink to cc-dash's control directory
os.symlink(os.path.join(CC_STATE, "control"), CTL_LINK)

FAKE_GH = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
FAKE_ANT = "sk-ant-api03-" + "qwertyuiopasdfghjklzxcvbnm1234"

CASES = [
    # (id, section, root, tool, tool_input, session, expected)
    ("L01", "location", ROOT, "Write", {"file_path": A_TS, "content": "hi"}, "s", A),
    ("L02", "location", ROOT, "Write", {"file_path": "/etc/hosts", "content": "x"}, "s", D),
    ("L03", "location", ROOT, "Edit", {"file_path": os.path.join(HOME, ".bashrc"), "old_string": "a", "new_string": "b"}, "s", D),
    ("L04", "location", ROOT, "Write", {"file_path": os.path.join(ROOT, ".git", "config"), "content": "x"}, "s", D),
    ("L05", "location", ROOT, "Write", {"file_path": "/tmp/cc-wg-test/notes.txt", "content": "x"}, "s", A),
    ("L06", "location", ROOT, "Write", {"file_path": os.path.join(OUTSIDE, "x.txt"), "content": "x"}, "s", W),
    ("L06b", "location", ASKROOT, "Write", {"file_path": os.path.join(OUTSIDE, "x.txt"), "content": "x"}, "s", K),
    ("L07", "location", WARNROOT, "Write", {"file_path": os.path.join(OUTSIDE, "x.txt"), "content": "x"}, "s", W),
    ("L08", "location", ROOT, "Write", {"file_path": os.path.join(HOME, ".claude", "projects", "p", "memory", "m.md"), "content": "x"}, "s", A),
    ("L09", "location", ROOT, "Write", {"file_path": os.path.join(HOME, ".ssh", "config"), "content": "x"}, "s", D),
    ("L10", "location", ROOT, "Write", {"file_path": "/usr/local/bin/tool", "content": "x"}, "s", D),
    # ── Other worktrees of the same repo count as the project ─────────────
    ("G01", "worktree", WT_MAIN, "Edit", {"file_path": os.path.join(WT_LINKED, "src", "a.ts"), "old_string": "1", "new_string": "3"}, "s", A),
    ("G02", "worktree", WT_MAIN, "Write", {"file_path": os.path.join(WT_LINKED, "src", "new", "c.ts"), "content": "x"}, "s", A),
    ("G03", "worktree", WT_LINKED, "Write", {"file_path": os.path.join(WT_MAIN, "src", "a.ts"), "content": "x"}, "s", A),
    ("G04", "worktree", WT_LINKED, "Write", {"file_path": os.path.join(WT_LINKED, "src", "a.ts"), "content": "x"}, "s", A),
    ("G05", "worktree", WT_LINKED, "Write", {"file_path": os.path.join(OUTSIDE, "x.txt"), "content": "x"}, "s", W),
    ("G06", "worktree", WT_MAIN, "Write", {"file_path": os.path.join(OUTSIDE, "x.txt"), "content": "x"}, "s", W),
    ("G07", "worktree", WT_MAIN, "Write", {"file_path": os.path.join(WT_LINKED, "src", "a.ts"), "content": f'token = "{FAKE_GH}"'}, "s", D),
    ("G08", "worktree", WT_LINKED, "Write", {"file_path": os.path.join(WT_MAIN, ".git", "config"), "content": "x"}, "s", D),
    ("G09", "worktree", ROOT, "Write", {"file_path": os.path.join(WT_LINKED, "src", "a.ts"), "content": "x"}, "s", W),
    # ── Secrets in content ────────────────────────────────────────────────
    ("S01", "secret", ROOT, "Write", {"file_path": A_TS, "content": "-----BEGIN RSA PRIVATE KEY-----\nMIIE"}, "s", D),
    ("S02", "secret", ROOT, "Write", {"file_path": A_TS, "content": 'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"'}, "s", A),
    ("S03", "secret", ROOT, "Write", {"file_path": A_TS, "content": f'token = "{FAKE_GH}"'}, "s", D),
    ("S04", "secret", ROOT, "Write", {"file_path": A_TS, "content": f'key = "{FAKE_ANT}"'}, "s", D),
    ("S05", "secret", ROOT, "Write", {"file_path": os.path.join(ROOT, ".env"), "content": f'KEY={FAKE_ANT}'}, "s", A),
    ("S06", "secret", ROOT, "Write", {"file_path": A_TS, "content": 'password = "correct-horse-battery-staple-99"'}, "s", D),
    ("S07", "secret", ROOT, "Write", {"file_path": A_TS, "content": 'password = "${DB_PASSWORD}"'}, "s", A),
    ("S08", "secret", ROOT, "Edit", {"file_path": B_TS, "old_string": "b", "new_string": f"const t = '{FAKE_GH}';"}, "s", D),
    ("S09", "secret", ROOT, "MultiEdit", {"file_path": B_TS, "edits": [
        {"old_string": "b", "new_string": "c"}, {"old_string": "2", "new_string": f"'{FAKE_GH}'"}]}, "s", D),
    ("S10", "secret", ROOT, "Write", {"file_path": A_TS, "content": 'api_key = "your_api_key_here_replace_me"'}, "s", A),
    ("S11", "secret", ROOT, "Write", {"file_path": os.path.join(ROOT, "config.example.json"), "content": f'{{"token": "{FAKE_GH}"}}'}, "s", A),
    ("S12", "secret", ROOT, "Write", {"file_path": A_TS, "content": "const jwt = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U';"}, "s", D),
    # ── Trajectory warnings ───────────────────────────────────────────────
    ("T01", "trajectory", ROOT, "Edit", {"file_path": A_TS, "old_string": "1", "new_string": "3"}, SESSION, W),
    ("T02", "trajectory", ROOT, "Edit", {"file_path": B_TS, "old_string": "2", "new_string": "3"}, SESSION, A),
    ("T03", "trajectory", ROOT, "Edit", {"file_path": B_TS, "old_string": "2", "new_string": "3"}, RED, W),
    ("T04", "trajectory", ROOT, "Edit", {"file_path": A_TS, "old_string": "1", "new_string": "3"}, RED, A),
    # ── Project rules ─────────────────────────────────────────────────────
    ("J01", "rules", WARNROOT, "Write", {"file_path": os.path.join(WARNROOT, "dist", "app.min.js"), "content": "x"}, "s", D),
    ("J02", "rules", WARNROOT, "Write", {"file_path": os.path.join(WARNROOT, "src", "c.ts"), "content": "console.log(1)"}, "s", W),
    ("J03", "rules", WARNROOT, "Write", {"file_path": os.path.join(WARNROOT, "src", "c.ts"), "content": "log(1)"}, "s", A),
    # ── Malformed payloads (fail open: the tool validates its own input) ──
    ("M01", "malformed", ROOT, "Write", "@@RAW@@not json", "s", A),
    ("M02", "malformed", ROOT, "Write", "@@RAW@@{\"tool_input\": null}", "s", A),
    ("M03", "malformed", ROOT, "Write", {"content": "no path"}, "s", A),
    ("L11", "location", ROOT, "Write", {"file_path": os.path.join(OUTSIDE, "tmp", "evil.py"), "content": "x"}, "s", W),
    # ── v0.2.1: cc-dash's control channel cannot be forged with Write/Edit ─
    ("C01", "ccdash", ROOT, "Write", {"file_path": os.path.join(CC_STATE, "control", "dec", "1-abc.json"), "content": '{"action":"allow"}'}, "s", D),
    ("C02", "ccdash", ROOT, "Edit", {"file_path": os.path.join(CC_STATE, "control", "lease"), "old_string": "a", "new_string": "b"}, "s", D),
    ("C03", "ccdash", ROOT, "MultiEdit", {"file_path": os.path.join(CC_STATE, "control", "msg", "s.main.1.json"), "edits": []}, "s", D),
    ("C04", "ccdash", ROOT, "Write", {"file_path": os.path.join(CC_STATE, "cc-dash.log"), "content": "x"}, "s", D),
    ("C07", "ccdash", ROOT, "NotebookEdit", {"notebook_path": os.path.join(CC_STATE, "control", "req", "1-abc.json"), "new_source": "x"}, "s", D),
    ("C08", "ccdash", ROOT, "Write", {"file_path": os.path.join(CTL_LINK, "dec", "1-abc.json"), "content": "{}"}, "s", D),  # a symlink into it
    ("C09", "ccdash", ROOT, "NotebookEdit", {"notebook_path": os.path.join(ROOT, "nb.ipynb"), "new_source": "x"}, "s", A),  # notebooks: location checks only
    # every path-like field is checked, not the first one present (a decoy must not stand in)
    ("C15", "ccdash", ROOT, "Write", {"file_path": os.path.join(CC_STATE, "projects.json"), "content": "{}"}, "s", D),  # cc-dash's project registry
    ("C10", "ccdash", ROOT, "NotebookEdit", {"file_path": A_TS, "notebook_path": os.path.join(CC_STATE, "control", "lease"), "new_source": "x"}, "s", D),
    ("C11", "ccdash", ROOT, "NotebookEdit", {"file_path": os.path.join(CC_STATE, "control", "lease"), "notebook_path": os.path.join(ROOT, "nb.ipynb"), "new_source": "x"}, "s", D),
    ("C12", "ccdash", ROOT, "Write", {"file_path": A_TS, "path": os.path.join(CC_STATE, "control", "dec", "1-a.json"), "content": "x"}, "s", D),
    ("C13", "ccdash", ROOT, "NotebookEdit", {"file_path": A_TS, "notebook_path": os.path.join(ROOT, ".git", "config"), "new_source": "x"}, "s", D),
    ("C14", "ccdash", ROOT, "NotebookEdit", {"notebook_path": "/etc/x.ipynb", "new_source": "x"}, "s", D),
    ("C05", "ccdash", ROOT, "Write", {"file_path": os.path.join(ROOT, "src", "control", "x.ts"), "content": "x"}, "s", A),
    ("C06", "ccdash", ROOT, "Write", {"file_path": os.path.join(CC_STATE, "notes.txt"), "content": "x"}, "s", W),  # outside the repo: a note, not a block
]


# The cc_tool checkout a project was set up from (cc_tool_root beside the hook): its code is off limits
# from other projects. A copy of the guard beside a pin naming a fake checkout.
FAKE_ROOT = os.path.join(BASE, "fake_cc_tool")
for _d in ("bin", "lib/cc_dash", "templates/hooks", "docs"):
    os.makedirs(os.path.join(FAKE_ROOT, _d), exist_ok=True)
_git(FAKE_ROOT, "init", "-q", "-b", "feature/x")
open(os.path.join(FAKE_ROOT, "CHANGELOG.md"), "w").write("a\n")
_git(FAKE_ROOT, "add", "-A")
_git(FAKE_ROOT, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-qm", "init")
FAKE_WT = os.path.join(BASE, "fake_cc_tool_wt")
_git(FAKE_ROOT, "worktree", "add", "-q", "-b", "wt/x", FAKE_WT)
PIN_HOOKS = os.path.join(BASE, "pinned-hooks")
os.makedirs(PIN_HOOKS)
for _f in ("write-guard.py", "cc_hooklib.py"):
    shutil.copy(os.path.join(HOOKS, _f), os.path.join(PIN_HOOKS, _f))
with open(os.path.join(PIN_HOOKS, "cc_tool_root"), "w") as _fh:
    _fh.write(FAKE_ROOT + "\n")
os.chmod(os.path.join(PIN_HOOKS, "cc_tool_root"), 0o600)
PIN_GUARD = os.path.join(PIN_HOOKS, "write-guard.py")
PIN_CASES = [
    ("K01", "checkout", ROOT, "Write", {"file_path": os.path.join(FAKE_ROOT, "lib", "cc_dash", "procs.py"), "content": "x"}, "s", D),
    ("K02", "checkout", ROOT, "Edit", {"file_path": os.path.join(FAKE_ROOT, "bin", "cc-dash"), "old_string": "a", "new_string": "b"}, "s", D),
    ("K03", "checkout", ROOT, "MultiEdit", {"file_path": os.path.join(FAKE_ROOT, "templates", "hooks", "dash-control.py"), "edits": []}, "s", D),
    ("K04", "checkout", ROOT, "NotebookEdit", {"notebook_path": os.path.join(FAKE_ROOT, "lib", "x.ipynb"), "new_source": "x"}, "s", D),
    ("K05", "checkout", ROOT, "NotebookEdit", {"file_path": A_TS, "notebook_path": os.path.join(FAKE_ROOT, "bin", "x.ipynb"), "new_source": "x"}, "s", D),
    ("K06", "checkout", ROOT, "Write", {"file_path": os.path.join(FAKE_ROOT, "docs", "notes.md"), "content": "x"}, "s", D),   # anywhere in the checkout
    ("K09", "checkout", ROOT, "Write", {"file_path": os.path.join(FAKE_ROOT, "templates", "skills", "x", "SKILL.md"), "content": "x"}, "s", D),
    ("K10", "checkout", ROOT, "Edit", {"file_path": os.path.join(FAKE_ROOT, "CHANGELOG.md"), "old_string": "a", "new_string": "b"}, "s", D),
    ("K11", "checkout", ROOT, "Write", {"file_path": os.path.join(FAKE_ROOT, "new-root-file.py"), "content": "x"}, "s", D),
    ("K12", "checkout", FAKE_WT, "Write", {"file_path": os.path.join(FAKE_ROOT, "lib", "cc_dash", "x.py"), "content": "x"}, "s", A),  # a worktree of cc_tool: developing it
    ("K13", "checkout", ROOT, "Write", {"file_path": os.path.join(BASE, "fake_cc_tool_sibling", "x.py"), "content": "x"}, "s", A),  # a sibling, not the checkout
    ("K07", "checkout", FAKE_ROOT, "Write", {"file_path": os.path.join(FAKE_ROOT, "lib", "cc_dash", "procs.py"), "content": "x"}, "s", A),  # developing cc_tool itself
    ("K08", "checkout", ROOT, "Write", {"file_path": A_TS, "content": "hi"}, "s", A),
]


def run_case(root, tool, tool_input, session, guard=None):
    if isinstance(tool_input, str) and tool_input.startswith("@@RAW@@"):
        payload = tool_input[len("@@RAW@@"):]
    else:
        payload = json.dumps({"session_id": session, "hook_event_name": "PreToolUse",
                              "tool_name": tool, "tool_input": tool_input})
    # A temp state dir: a worktree's hook state goes under XDG_STATE_HOME, never the real one.
    env = dict(os.environ, CLAUDE_PROJECT_DIR=root)
    p = subprocess.run([PY, guard or GUARD], input=payload, capture_output=True,
                       text=True, cwd=root, timeout=60, env=env)
    if p.returncode != 0:
        tail = p.stderr.strip().splitlines()[-1] if p.stderr.strip() else ""
        return "ERR%d" % p.returncode, tail
    out = p.stdout
    if '"permissionDecision": "deny"' in out:
        return D, out.strip()
    if '"permissionDecision": "ask"' in out:
        return K, out.strip()
    if "additionalContext" in out:
        return W, out.strip()
    return A, out.strip()


def main():
    fails = []
    for cid, section, root, tool, ti, session, expected, guard in [c + (None,) for c in CASES] + [c + (PIN_GUARD,) for c in PIN_CASES]:
        got, detail = run_case(root, tool, ti, session, guard)
        if got != expected:
            fails.append((cid, section, expected, got, detail[:160]))
        elif got == D and "BLOCKED:" not in detail:
            fails.append((cid, section, "BLOCKED: prefix", got, detail[:160]))
    # A secret deny never repeats the secret: not in the reason shown to the model, not in activity.jsonl.
    n_extra = 0
    for cid, key, content in (("R01", FAKE_GH, f'token = "{FAKE_GH}"'), ("R02", FAKE_ANT, f'\n\nkey = "{FAKE_ANT}"')):
        n_extra += 1
        got, detail = run_case(ROOT, "Write", {"file_path": A_TS, "content": content}, "redact")
        try:
            log = open(os.path.join(lib.state_dir(ROOT), "activity.jsonl"), encoding="utf-8").read()
        except OSError:
            log = ""
        leaked = [w for w in (key, key[4:20]) if w in detail or w in log]
        if got != D or leaked or "line " not in detail or "secret" not in log:
            fails.append((cid, "redact", "DENY without the secret", got, ("leaked %s; " % leaked) + detail[:160]))
    why = STATE_GUARD.problem()
    n_extra += 1
    if why:
        fails.append(("STATE", "guard", "untouched", "changed", why))
    total = len(CASES) + len(PIN_CASES) + n_extra
    print("total=%d  pass=%d  fail=%d" % (total, total - len(fails), len(fails)))
    for cid, section, exp, got, detail in fails:
        print("  %-5s %-10s expected %-6s got %-8s  %s" % (cid, section, exp, got, detail))
    return 1 if fails else 0


try:
    sys.exit(main())
finally:
    shutil.rmtree(BASE, ignore_errors=True)
    shutil.rmtree(OUTSIDE, ignore_errors=True)
    shutil.rmtree(STATE, ignore_errors=True)
    shutil.rmtree(WT_LINKED, ignore_errors=True)
    shutil.rmtree(WT_MAIN, ignore_errors=True)
