#!/usr/bin/env python3
"""Allow/ask/warn/deny matrix for templates/hooks/write-guard.py.

Covers location rules (system paths, home dotfiles, .git/, outside-repo),
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


BASE = tempfile.mkdtemp(prefix="writeguard-matrix-")
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

FAKE_GH = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
FAKE_ANT = "sk-ant-api03-" + "qwertyuiopasdfghjklzxcvbnm1234"

CASES = [
    # (id, section, root, tool, tool_input, session, expected)
    ("L01", "location", ROOT, "Write", {"file_path": A_TS, "content": "hi"}, "s", A),
    ("L02", "location", ROOT, "Write", {"file_path": "/etc/hosts", "content": "x"}, "s", D),
    ("L03", "location", ROOT, "Edit", {"file_path": os.path.join(HOME, ".bashrc"), "old_string": "a", "new_string": "b"}, "s", D),
    ("L04", "location", ROOT, "Write", {"file_path": os.path.join(ROOT, ".git", "config"), "content": "x"}, "s", D),
    ("L05", "location", ROOT, "Write", {"file_path": "/tmp/cc-wg-test/notes.txt", "content": "x"}, "s", A),
    ("L06", "location", ROOT, "Write", {"file_path": os.path.join(OUTSIDE, "x.txt"), "content": "x"}, "s", K),
    ("L07", "location", WARNROOT, "Write", {"file_path": os.path.join(OUTSIDE, "x.txt"), "content": "x"}, "s", W),
    ("L08", "location", ROOT, "Write", {"file_path": os.path.join(HOME, ".claude", "projects", "p", "memory", "m.md"), "content": "x"}, "s", A),
    ("L09", "location", ROOT, "Write", {"file_path": os.path.join(HOME, ".ssh", "config"), "content": "x"}, "s", D),
    ("L10", "location", ROOT, "Write", {"file_path": "/usr/local/bin/tool", "content": "x"}, "s", D),
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
]


def run_case(root, tool, tool_input, session):
    if isinstance(tool_input, str) and tool_input.startswith("@@RAW@@"):
        payload = tool_input[len("@@RAW@@"):]
    else:
        payload = json.dumps({"session_id": session, "hook_event_name": "PreToolUse",
                              "tool_name": tool, "tool_input": tool_input})
    env = dict(os.environ, CLAUDE_PROJECT_DIR=root)
    p = subprocess.run([sys.executable, GUARD], input=payload, capture_output=True,
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
    for cid, section, root, tool, ti, session, expected in CASES:
        got, detail = run_case(root, tool, ti, session)
        if got != expected:
            fails.append((cid, section, expected, got, detail[:160]))
        elif got == D and "BLOCKED:" not in detail:
            fails.append((cid, section, "BLOCKED: prefix", got, detail[:160]))
    print("total=%d  pass=%d  fail=%d" % (len(CASES), len(CASES) - len(fails), len(fails)))
    for cid, section, exp, got, detail in fails:
        print("  %-5s %-10s expected %-6s got %-8s  %s" % (cid, section, exp, got, detail))
    return 1 if fails else 0


try:
    sys.exit(main())
finally:
    shutil.rmtree(BASE, ignore_errors=True)
    shutil.rmtree(OUTSIDE, ignore_errors=True)
