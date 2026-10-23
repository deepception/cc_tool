#!/usr/bin/env python3
"""templates/hooks/attention.py: the event lines, the private log, and silence.

Runs the hook as Claude Code does (a subprocess fed JSON on stdin) with HOME and
XDG_STATE_HOME pointed at a temp dir, once per hook event it is registered for,
and checks the lines it appends, the 0700 directory and 0600 file, rotation at
5 MB, refusal to follow a symlink or write through a hard link or FIFO, that it
prints nothing, that it exits 0 on garbage, and how long it takes. Also checks
templates/settings.json registers it for every event it handles.

Run:  python3 tests/test_attention_hook.py
"""
import json
import os
import shutil
import stat
import statistics
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The interpreter is the kernel's: with zsh's exported ARGV0 (Cursor's terminal), sys.executable
# can name the editor's AppImage, and running it opens editor tabs instead of Python.
PY = os.path.realpath("/proc/self/exe") if os.path.exists("/proc/self/exe") else sys.executable
HOOK = os.path.join(REPO, "templates", "hooks", "attention.py")
SETTINGS = os.path.join(REPO, "templates", "settings.json")
RESULTS = []
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from state_guard import StateGuard  # noqa: E402  (tests/state_guard.py)
STATE_GUARD = StateGuard()         # the real state dir, before anything here runs a hook
BASE = tempfile.mkdtemp(prefix="cc-attention-hook-")
SID = "0f0e0d0c-1111-2222-3333-444455556666"


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def run(payload, env_extra=None, raw=None, home=None):
    home = home or os.path.join(BASE, "home")
    os.makedirs(home, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k != "XDG_STATE_HOME"}
    env["HOME"] = home
    env.update(env_extra or {})
    data = raw if raw is not None else json.dumps(payload).encode()
    t0 = time.perf_counter()
    p = subprocess.run([PY, HOOK], input=data, env=env, capture_output=True, timeout=10)
    return p, (time.perf_counter() - t0) * 1000


def lines(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return [json.loads(x) for x in fh if x.strip()]
    except OSError:
        return []


def base(ev, **kw):
    d = {"session_id": SID, "transcript_path": "/p/t.jsonl", "cwd": "/work/demo", "hook_event_name": ev,
         "permission_mode": "default"}
    d.update(kw)
    return d


def test_events():
    state = os.path.join(BASE, "state1")
    env = {"XDG_STATE_HOME": state}
    log = os.path.join(state, "cc_tool", "attention.jsonl")
    cases = [
        ("PermissionRequest Bash", base("PermissionRequest", tool_name="Bash",
                                        tool_input={"command": "rm -rf build", "description": "x"},
                                        permission_suggestions=[]),
         {"event": "PermissionRequest", "tool": "Bash", "sig": "rm -rf build", "message": "rm -rf build", "mode": "default"}),
        ("PermissionRequest AskUserQuestion", base("PermissionRequest", tool_name="AskUserQuestion",
                                                   tool_input={"questions": [{"question": "Tea or coffee?", "header": "Drink",
                                                                              "options": [{"label": "Tea"}]}]}),
         {"tool": "AskUserQuestion", "sig": "Tea or coffee?", "message": "Tea or coffee?"}),
        ("PermissionRequest ExitPlanMode", base("PermissionRequest", tool_name="ExitPlanMode",
                                                tool_input={"plan": "\n## Refactor auth\n1. x", "planFilePath": "/p.md"}),
         {"tool": "ExitPlanMode", "sig": "", "message": "Refactor auth"}),
        ("PermissionRequest in a subagent", base("PermissionRequest", tool_name="Edit", agent_id="a1b2c3", agent_type="Explore",
                                                 tool_input={"file_path": "/work/demo/x.py", "old_string": "a", "new_string": "b"}),
         {"agent": "a1b2c3", "agent_type": "Explore", "sig": "/work/demo/x.py"}),
        ("PostToolUse", base("PostToolUse", tool_name="Bash", tool_input={"command": "rm -rf build"},
                             tool_response={"stdout": "x" * 5000}, tool_use_id="toolu_9"),
         {"event": "PostToolUse", "tool": "Bash", "sig": "rm -rf build", "tool_use_id": "toolu_9"}),
        ("PostToolUseFailure", base("PostToolUseFailure", tool_name="Bash", tool_input={"command": "false"},
                                    tool_use_id="toolu_10", error="Exit code 1"),
         {"event": "PostToolUseFailure", "sig": "false"}),
        ("PostToolBatch", base("PostToolBatch", tool_calls=[{"tool_name": "Read"}]), {"event": "PostToolBatch"}),
        ("Notification permission_prompt", base("Notification", message="Claude needs your permission",
                                                notification_type="permission_prompt"),
         {"ntype": "permission_prompt", "message": "Claude needs your permission"}),
        ("Notification idle_prompt", base("Notification", message="Claude is waiting for your input",
                                          notification_type="idle_prompt", title="Waiting"),
         {"ntype": "idle_prompt", "title": "Waiting"}),
        ("Elicitation", base("Elicitation", mcp_server_name="db", message="Password?", mode="form"),
         {"server": "db", "message": "Password?", "mode": "form"}),
        ("ElicitationResult", base("ElicitationResult", mcp_server_name="db", action="accept", content={"pw": "s3cret"}),
         {"server": "db"}),
        ("StopFailure", base("StopFailure", error="rate_limit", error_details="429 Too Many Requests"),
         {"error": "rate_limit", "message": "429 Too Many Requests"}),
        ("UserPromptSubmit", base("UserPromptSubmit", prompt="hello"), {"event": "UserPromptSubmit"}),
        ("Stop", base("Stop", stop_hook_active=False, last_assistant_message="done"), {"event": "Stop"}),
        ("SubagentStop", base("SubagentStop", agent_id="a1b2c3", agent_type="Explore"), {"agent": "a1b2c3"}),
        ("SessionEnd", base("SessionEnd", reason="prompt_input_exit"), {"reason": "prompt_input_exit"}),
    ]
    for name, payload, want in cases:
        before = len(lines(log))
        p, _ = run(payload, env)
        got = lines(log)
        check(f"{name}: exit 0", p.returncode == 0, str(p.returncode))
        check(f"{name}: prints nothing", p.stdout == b"" and p.stderr == b"", repr(p.stdout[:80] + p.stderr[:80]))
        check(f"{name}: one line appended", len(got) == before + 1, f"{before} -> {len(got)}")
        if len(got) == before + 1:
            rec = got[-1]
            check(f"{name}: common fields", rec.get("v") == 1 and rec.get("session") == SID and rec.get("cwd") == "/work/demo"
                  and isinstance(rec.get("ts"), float) and abs(rec["ts"] - time.time()) < 30, str(rec))
            bad = {k: (rec.get(k), v) for k, v in want.items() if rec.get(k) != v}
            check(f"{name}: fields", not bad, str(bad))
            check(f"{name}: no raw tool output or answers", "x" * 100 not in json.dumps(rec) and "s3cret" not in json.dumps(rec))
            if "agent" not in want:
                check(f"{name}: agent is null on the main thread", rec.get("agent") is None, str(rec.get("agent")))
    st_dir = os.stat(os.path.join(state, "cc_tool"))
    st_log = os.stat(log)
    check("modes: directory is 0700", stat.S_IMODE(st_dir.st_mode) == 0o700, oct(st_dir.st_mode))
    check("modes: log is 0600", stat.S_IMODE(st_log.st_mode) == 0o600, oct(st_log.st_mode))
    long = "y" * 5000
    run(base("PermissionRequest", tool_name="Bash", tool_input={"command": long}), env)
    rec = lines(log)[-1]
    check("clip: a huge command is clipped", len(rec["sig"]) <= 300 and len(rec["message"]) <= 400, str(len(rec["sig"])))


def test_locations():
    home = os.path.join(BASE, "home2")
    run(base("Stop"), home=home)
    default = os.path.join(home, ".local", "state", "cc_tool", "attention.jsonl")
    check("location: ~/.local/state/cc_tool without XDG_STATE_HOME", len(lines(default)) == 1, default)
    run(base("Stop"), {"XDG_STATE_HOME": "relative/dir"}, home=home)
    check("location: a relative XDG_STATE_HOME is ignored (XDG spec)", len(lines(default)) == 2
          and not os.path.exists(os.path.join(REPO, "relative")))
    # A loose pre-existing directory and file get tightened.
    state = os.path.join(BASE, "state-loose")
    d = os.path.join(state, "cc_tool")
    os.makedirs(d)
    os.chmod(d, 0o755)
    f = os.path.join(d, "attention.jsonl")
    open(f, "w").close()
    os.chmod(f, 0o644)
    run(base("Stop"), {"XDG_STATE_HOME": state})
    check("modes: a 0755 directory is tightened to 0700", stat.S_IMODE(os.stat(d).st_mode) == 0o700, oct(os.stat(d).st_mode))
    check("modes: a 0644 log is tightened to 0600", stat.S_IMODE(os.stat(f).st_mode) == 0o600, oct(os.stat(f).st_mode))
    check("modes: and the line still lands", len(lines(f)) == 1)


def test_rotation():
    state = os.path.join(BASE, "state-rot")
    d = os.path.join(state, "cc_tool")
    os.makedirs(d, mode=0o700)
    f = os.path.join(d, "attention.jsonl")
    with open(f, "w") as fh:
        line = json.dumps({"v": 1, "ts": 1.0, "event": "Stop", "session": "old"}) + "\n"
        fh.write(line * (5_000_000 // len(line) + 10))
    os.chmod(f, 0o600)
    size = os.path.getsize(f)
    run(base("Stop"), {"XDG_STATE_HOME": state})
    check("rotation: past 5 MB the log moves to .1", os.path.isfile(f + ".1") and os.path.getsize(f + ".1") == size)
    got = lines(f)
    check("rotation: the new log holds just the new line", len(got) == 1 and got[0]["session"] == SID, str(len(got)))
    check("rotation: new file is 0600", stat.S_IMODE(os.stat(f).st_mode) == 0o600)


def test_refusals():
    # The log file is a symlink: never followed.
    state = os.path.join(BASE, "state-link")
    d = os.path.join(state, "cc_tool")
    os.makedirs(d, mode=0o700)
    victim = os.path.join(BASE, "victim.txt")
    with open(victim, "w") as fh:
        fh.write("keep\n")
    os.symlink(victim, os.path.join(d, "attention.jsonl"))
    p, _ = run(base("Stop"), {"XDG_STATE_HOME": state})
    check("symlink: a symlinked log is not followed", open(victim).read() == "keep\n")
    check("symlink: hook still exits 0 silently", p.returncode == 0 and not p.stdout and not p.stderr)
    # The cc_tool directory is a symlink: nothing is written through it.
    state2 = os.path.join(BASE, "state-dirlink")
    os.makedirs(state2)
    target = os.path.join(BASE, "elsewhere")
    os.makedirs(target)
    os.symlink(target, os.path.join(state2, "cc_tool"))
    p, _ = run(base("Stop"), {"XDG_STATE_HOME": state2})
    check("symlink: a symlinked state directory is refused", os.listdir(target) == [], str(os.listdir(target)))
    check("symlink: refused silently with exit 0", p.returncode == 0 and not p.stdout and not p.stderr)
    # A hard link to another file: not written.
    state3 = os.path.join(BASE, "state-hard")
    d3 = os.path.join(state3, "cc_tool")
    os.makedirs(d3, mode=0o700)
    other = os.path.join(BASE, "other.txt")
    with open(other, "w") as fh:
        fh.write("keep\n")
    os.chmod(other, 0o600)
    os.link(other, os.path.join(d3, "attention.jsonl"))
    run(base("Stop"), {"XDG_STATE_HOME": state3})
    check("hardlink: a log with a second link is left alone", open(other).read() == "keep\n")
    # A FIFO where the log should be: the hook must not hang on it.
    if hasattr(os, "mkfifo"):
        state4 = os.path.join(BASE, "state-fifo")
        d4 = os.path.join(state4, "cc_tool")
        os.makedirs(d4, mode=0o700)
        os.mkfifo(os.path.join(d4, "attention.jsonl"), 0o600)
        p, ms = run(base("Stop"), {"XDG_STATE_HOME": state4})
        check("fifo: a FIFO log does not block the hook", p.returncode == 0 and ms < 3000, f"{ms:.0f} ms")


def test_garbage():
    state = os.path.join(BASE, "state-garbage")
    env = {"XDG_STATE_HOME": state}
    for name, raw in [("empty stdin", b""), ("not JSON", b"{nope"), ("a list", b"[1,2]"), ("binary", bytes(range(256))),
                      ("null", b"null"), ("no session id", json.dumps({"hook_event_name": "Stop"}).encode()),
                      ("no event name", json.dumps({"session_id": SID}).encode()),
                      ("odd types", json.dumps({"session_id": SID, "hook_event_name": "PermissionRequest",
                                                "tool_name": 5, "tool_input": "str"}).encode())]:
        p, _ = run(None, env, raw=raw)
        check(f"garbage: {name} exits 0", p.returncode == 0, str(p.returncode))
        check(f"garbage: {name} prints nothing", not p.stdout and not p.stderr, repr(p.stdout[:60] + p.stderr[:60]))
    got = lines(os.path.join(state, "cc_tool", "attention.jsonl"))
    check("garbage: only the well-formed one (odd types) is logged", len(got) == 1, str(got))
    # Unwritable state home: still silent and exit 0.
    p, _ = run(base("Stop"), {"XDG_STATE_HOME": "/proc/cc-tool-nope"})
    check("garbage: an unwritable state dir exits 0 silently", p.returncode == 0 and not p.stdout and not p.stderr)


def test_speed():
    state = os.path.join(BASE, "state-speed")
    payload = base("PostToolUse", tool_name="Bash", tool_input={"command": "ls"}, tool_response={"stdout": "z" * 20000},
                   tool_use_id="t")
    times = [run(payload, {"XDG_STATE_HOME": state})[1] for _ in range(25)]
    bare = []
    for _ in range(25):
        t0 = time.perf_counter()
        subprocess.run([PY, "-c", "pass"], capture_output=True)
        bare.append((time.perf_counter() - t0) * 1000)
    med, bmed = statistics.median(times), statistics.median(bare)
    print(f"  hook latency: median {med:.1f} ms (bare interpreter {bmed:.1f} ms, so the hook's own work is {med - bmed:.1f} ms)")
    check("speed: hook's own work over interpreter start-up under 30 ms", med - bmed < 30, f"{med:.1f} vs {bmed:.1f}")


def test_settings():
    with open(SETTINGS) as fh:
        hooks = json.load(fh)["hooks"]
    want = {"PermissionRequest", "Notification", "PostToolUse", "PostToolUseFailure", "PostToolBatch", "UserPromptSubmit",
            "Stop", "SubagentStop", "StopFailure", "SessionEnd", "Elicitation", "ElicitationResult"}
    found = {}
    for ev, entries in hooks.items():
        for e in entries:
            for h in e.get("hooks") or []:
                if "attention.py" in h.get("command", ""):
                    found[ev] = (e.get("matcher"), h)
    check("settings: attention.py registered for every event it handles", set(found) == want, str(set(found) ^ want))
    check("settings: every registration matches all tools/types", all(m in ("", None) for m, _ in found.values()))
    check("settings: async everywhere except SessionEnd (which -p would cancel)",
          all(h.get("async") is True for ev, (_, h) in found.items() if ev != "SessionEnd")
          and not found.get("SessionEnd", (None, {}))[1].get("async"))
    check("settings: one timeout value (cc-setup keys kept timeouts by command)",
          len({h.get("timeout") for _, h in found.values()}) == 1)
    check("settings: command form matches the other hooks",
          all(h["command"] == "python3 $CLAUDE_PROJECT_DIR/.claude/hooks/attention.py" for _, h in found.values()))


def main():
    try:
        for fn in (test_events, test_locations, test_rotation, test_refusals, test_garbage, test_speed, test_settings):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                check(fn.__name__ + " raised", False, f"{type(e).__name__}: {e}")
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
    why = STATE_GUARD.problem()
    check("the real state dir is untouched by this run", not why, why)
    fails = [r for r in RESULTS if not r[1]]
    print("total=%d  pass=%d  fail=%d" % (len(RESULTS), len(RESULTS) - len(fails), len(fails)))
    for name, _, detail in fails:
        print("  FAIL %-60s %s" % (name, detail[:200]))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
