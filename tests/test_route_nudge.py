#!/usr/bin/env python3
"""templates/hooks/route-nudge.py: the warn-only nudge for an Agent call that names no model.

Runs the hook as Claude Code does (a subprocess fed JSON on stdin) and checks:
  - an Agent or Task call with no `model` (absent, empty, blank, wrong type) gets one
    PreToolUse additionalContext line, with no permissionDecision (it never asks or denies)
  - the line names the resolved default (CLAUDE_CODE_SUBAGENT_MODEL, the session model for
    Explore/Plan, an agent type's own definition) and teaches sonnet / haiku / opus
  - a call that names a model, other tools, other events, and garbage input print nothing
    and exit 0
  - the effort suffix appears only when the session effort is above medium and the call
    names no `effort`
  - latency of the hook's own work
  - templates/settings.json registers it (matcher Agent|Task, short timeout) in a form
    cc-setup recognises as cc_tool's, and bin/cc-setup and bin/cc-update-project roll it out
    to a temp project (HOME pointed at a temp dir; nothing global is touched)

Run:  python3 tests/test_route_nudge.py
"""
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The interpreter is the kernel's: with zsh's exported ARGV0 (Cursor's terminal), sys.executable
# can name the editor's AppImage, and running it opens editor tabs instead of Python.
PY = os.path.realpath("/proc/self/exe") if os.path.exists("/proc/self/exe") else sys.executable
HOOK = os.path.join(REPO, "templates", "hooks", "route-nudge.py")
SETTINGS = os.path.join(REPO, "templates", "settings.json")
RESULTS = []
BASE = tempfile.mkdtemp(prefix="cc-route-nudge-")
HOME = os.path.join(BASE, "home")
os.makedirs(HOME)


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    if not cond:
        print("FAIL %s  %s" % (name, str(detail)[:300]))


def run(payload, env_extra=None, raw=None, cwd=None):
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CODE_SUBAGENT_MODEL", "CLAUDE_PROJECT_DIR", "CLAUDE_CONFIG_DIR")}
    env["HOME"] = HOME
    env.update(env_extra or {})
    data = raw if raw is not None else json.dumps(payload).encode()
    t0 = time.perf_counter()
    p = subprocess.run([PY, HOOK], input=data, env=env, capture_output=True, timeout=10, cwd=cwd)
    return p, (time.perf_counter() - t0) * 1000


def ctx_of(p):
    """The additionalContext line, or None when nothing was printed."""
    out = p.stdout.decode()
    if not out.strip():
        return None
    o = json.loads(out)
    return o["hookSpecificOutput"]["additionalContext"]


def call(tool="Agent", **ti):
    return {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": ti, "session_id": "s", "cwd": BASE}


def test_nudge():
    p, _ = run(call(description="d", prompt="p"))
    out = json.loads(p.stdout.decode())
    hso = out["hookSpecificOutput"]
    check("missing model: exit 0", p.returncode == 0, p.returncode)
    check("missing model: PreToolUse additionalContext only, never a decision",
          hso["hookEventName"] == "PreToolUse" and set(hso) == {"hookEventName", "additionalContext"} and set(out) == {"hookSpecificOutput"}, out)
    msg = hso["additionalContext"]
    check("missing model: one line", "\n" not in msg and msg.startswith("Agent call without `model`"), msg)
    check("missing model: teaches sonnet, haiku and opus", all(("`%s`" % m) in msg for m in ("sonnet", "haiku", "opus")), msg)
    check("missing model: says to name it on every call (managed block wording)", "Name `model` on every Agent call" in msg and "never let the default pick it" in msg, msg)
    check("missing model: stderr quiet", p.stderr == b"", p.stderr)
    for tool in ("Task", "Agent"):
        p, _ = run(call(tool, description="d"))
        check("%s tool is covered" % tool, ctx_of(p) is not None, p.stdout)
    for label, ti in (("empty string", {"model": ""}), ("blank", {"model": "   "}), ("null", {"model": None}), ("number", {"model": 5})):
        p, _ = run(call(description="d", **ti))
        check("model %s counts as not named" % label, ctx_of(p) is not None and p.returncode == 0, p.stdout)


def test_default_resolution():
    p, _ = run(call(description="d"), {"CLAUDE_CODE_SUBAGENT_MODEL": "claude-opus-5-5"})
    check("default: general-purpose resolves to CLAUDE_CODE_SUBAGENT_MODEL", "default (claude-opus-5-5, from CLAUDE_CODE_SUBAGENT_MODEL)" in ctx_of(p), ctx_of(p))
    p, _ = run(call(description="d"))
    check("default: without the env var it says the session's model", "default (the session's model)" in ctx_of(p), ctx_of(p))
    p, _ = run(call(description="d", subagent_type="Explore"), {"CLAUDE_CODE_SUBAGENT_MODEL": "claude-opus-5-5"})
    check("default: Explore inherits the session model, not the env var", "the session's model, which Explore inherits" in ctx_of(p), ctx_of(p))
    proj = os.path.join(BASE, "proj")
    os.makedirs(os.path.join(proj, ".claude", "agents"))
    with open(os.path.join(proj, ".claude", "agents", "py-pro.md"), "w") as fh:
        fh.write("---\nname: py-pro\nmodel: sonnet\n---\nbody\n")
    with open(os.path.join(proj, ".claude", "agents", "same.md"), "w") as fh:
        fh.write("---\nname: same\nmodel: inherit\n---\nbody\n")
    p, _ = run(call(description="d", subagent_type="py-pro"), {"CLAUDE_PROJECT_DIR": proj, "CLAUDE_CODE_SUBAGENT_MODEL": "claude-opus-5-5"})
    check("default: an agent type's definition model wins", "default (sonnet, from the py-pro agent's definition)" in ctx_of(p), ctx_of(p))
    p, _ = run(call(description="d", subagent_type="same"), {"CLAUDE_PROJECT_DIR": proj, "CLAUDE_CODE_SUBAGENT_MODEL": "claude-opus-5-5"})
    check("default: model: inherit in a definition falls through", "from CLAUDE_CODE_SUBAGENT_MODEL" in ctx_of(p), ctx_of(p))
    p, _ = run(call(description="d", subagent_type="../../etc/passwd"), {"CLAUDE_PROJECT_DIR": proj})
    check("default: a path-like agent type is never opened", ctx_of(p) is not None and "definition" not in ctx_of(p), ctx_of(p))


def test_silence():
    for m in ("sonnet", "opus", "haiku", "claude-sonnet-5-5"):
        p, _ = run(call(description="d", model=m))
        check("model %s named: no output, exit 0" % m, p.returncode == 0 and p.stdout == b"" and p.stderr == b"", (p.stdout, p.stderr))
    other = [call("Bash", command="ls"), call("Read", file_path="/x"), call("Workflow", script="x"),
             {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {}},
             {"tool_name": "Agent"}, {"tool_name": "Agent", "tool_input": "oops"}, {"tool_name": "Agent", "tool_input": None}, {}]
    for i, pl in enumerate(other):
        p, _ = run(pl)
        if pl.get("tool_name") == "Agent" and isinstance(pl.get("tool_input"), dict) is False:
            check("odd Agent input %d: no output, exit 0" % i, p.returncode == 0 and p.stdout == b"", p.stdout)
        else:
            check("other call %d: no output, exit 0" % i, p.returncode == 0 and p.stdout == b"", p.stdout)
    # Agent with no tool_input dict at all
    p, _ = run({"tool_name": "Agent"})
    check("Agent without tool_input: silent", p.returncode == 0 and p.stdout == b"", p.stdout)
    for raw in (b"", b"   ", b"not json", b"[1,2]", b"null", b'{"tool_name": ', b"\xff\xfe\x00", b"{" * 5000, b'"a string"', b"123"):
        p, _ = run(None, raw=raw)
        check("garbage %r: exit 0, no output" % raw[:12], p.returncode == 0 and p.stdout == b"" and p.stderr == b"", (p.returncode, p.stdout, p.stderr))
    big = json.dumps(call(description="x" * 400000, prompt="y" * 400000)).encode()
    p, _ = run(None, raw=big)
    check("oversized input: exit 0", p.returncode == 0, p.returncode)


def test_effort():
    def msg(level, **ti):
        d = call(description="d", **ti)
        if level is not None:
            d["effort"] = {"level": level}
        p, _ = run(d)
        return ctx_of(p)
    for lv in ("high", "xhigh", "max"):
        m = msg(lv)
        check("effort %s, call names none: suffix added" % lv, m and ("runs at %s effort" % lv) in m and 'effort: "medium"' in m, m)
        check("effort %s: still one line" % lv, m and "\n" not in m, m)
    for lv in ("low", "medium"):
        m = msg(lv)
        check("effort %s: no suffix" % lv, m and "effort" not in m.replace("`effort`", "").replace("effort:", "") and "runs at" not in m, m)
    check("no effort in the input: no suffix", "runs at" not in (msg(None) or ""), msg(None))
    check("effort named in the call: no suffix", "runs at" not in (msg("xhigh", effort="medium") or ""), msg("xhigh", effort="medium"))
    check("blank effort in the call counts as not named", "runs at xhigh" in (msg("xhigh", effort="  ") or ""), msg("xhigh", effort="  "))
    p, _ = run(dict(call(description="d"), effort="xhigh"))
    check("effort as a bare string: no suffix, still the nudge", ctx_of(p) is not None and "runs at" not in ctx_of(p), p.stdout)
    p, _ = run(dict(call(description="d"), effort={"level": 5}))
    check("effort level of the wrong type: no suffix", ctx_of(p) is not None and "runs at" not in ctx_of(p), p.stdout)


def test_latency():
    xs = []
    for _ in range(25):
        _, ms = run(call(description="d"), {"CLAUDE_CODE_SUBAGENT_MODEL": "claude-opus-5-5"})
        xs.append(ms)
    base = []
    for _ in range(25):
        t0 = time.perf_counter()
        subprocess.run([PY, "-c", "pass"], capture_output=True)
        base.append((time.perf_counter() - t0) * 1000)
    own = statistics.median(xs) - statistics.median(base)
    print("latency: median %.1f ms per call, bare interpreter %.1f ms, own work about %.1f ms" % (statistics.median(xs), statistics.median(base), own))
    check("latency: own work under 20 ms (median over a bare interpreter start)", own < 20, own)
    # In-process: the work itself.
    sys.path.insert(0, os.path.dirname(HOOK))
    import importlib.util
    spec = importlib.util.spec_from_file_location("route_nudge", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    d = call(description="d")
    d["effort"] = {"level": "xhigh"}
    t0 = time.perf_counter()
    for _ in range(200):
        mod.nudge(d)
    per = (time.perf_counter() - t0) * 1000 / 200
    check("latency: nudge() itself under 2 ms", per < 2, per)


def test_registration():
    st = json.load(open(SETTINGS))
    entries = [e for e in st["hooks"]["PreToolUse"] if any("route-nudge.py" in h.get("command", "") for h in e.get("hooks", []))]
    check("settings: exactly one PreToolUse entry runs route-nudge.py", len(entries) == 1, entries)
    e = entries[0]
    check("settings: matcher is Agent|Task", e.get("matcher") == "Agent|Task", e.get("matcher"))
    h = e["hooks"][0]
    check("settings: command hook, short timeout, not async", h["type"] == "command" and h["timeout"] <= 3 and not h.get("async"), h)
    rx = re.compile(r"(?:\$\{?CLAUDE_PROJECT_DIR\}?\"?/|(?<![\w~./-])(?:\./)?)\.claude/hooks/([^\s\"']+)")
    check("settings: cc-setup recognises the script as cc_tool's", (rx.search(h["command"]) or [None, None])[1] == "route-nudge.py", h["command"])
    check("settings: matcher is plain names so Claude Code matches them exactly", re.fullmatch(r"[A-Za-z0-9_|, -]+", e["matcher"]) is not None)
    other = [x for x in st["hooks"]["PreToolUse"] if x is not e]
    check("settings: the other PreToolUse entries are untouched (Bash, Read, writes, dash-control)", len(other) >= 4, len(other))
    src = open(HOOK).read()
    check("hook: standard library only, no cc_hooklib import (keeps the interpreter start cheap)", "cc_hooklib" not in src and not re.search(r"^import (?!json|os|sys)", src, re.M))
    check("hook: never emits a permission decision", "permissionDecision" not in src.replace("no permissionDecision", "").replace("never asks", ""))


def test_rollout():
    if shutil.which("git") is None:
        print("rollout: skipped (no git)")
        return
    proj = os.path.join(BASE, "rollout")
    os.makedirs(proj)
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR",)}
    env["HOME"] = HOME
    subprocess.run(["git", "init", "-q", proj], capture_output=True, env=env)
    r = subprocess.run([os.path.join(REPO, "bin", "cc-setup"), proj], capture_output=True, text=True, env=env, timeout=400, stdin=subprocess.DEVNULL)
    check("rollout: cc-setup exits 0", r.returncode == 0, r.stderr[-400:] + r.stdout[-400:])
    hook = os.path.join(proj, ".claude", "hooks", "route-nudge.py")
    check("rollout: cc-setup copies the hook", os.path.isfile(hook) and open(hook).read() == open(HOOK).read())
    sp = os.path.join(proj, ".claude", "settings.json")
    s = json.load(open(sp))
    regs = [(e.get("matcher"), h["command"]) for e in s["hooks"]["PreToolUse"] for h in e["hooks"] if "route-nudge.py" in h["command"]]
    check("rollout: cc-setup registers it once with the Agent|Task matcher", regs == [("Agent|Task", "python3 $CLAUDE_PROJECT_DIR/.claude/hooks/route-nudge.py")], regs)
    # The deployed copy runs from the project the way Claude Code runs it.
    p = subprocess.run([PY, hook], input=json.dumps(call(description="d")).encode(), capture_output=True, env=dict(env, CLAUDE_PROJECT_DIR=proj))
    check("rollout: the deployed hook answers", ctx_of(p) is not None, p.stdout)
    # An older project: no registration, no script, and a project hook of its own beside it.
    s["hooks"]["PreToolUse"] = [e for e in s["hooks"]["PreToolUse"] if not any("route-nudge.py" in h["command"] for h in e["hooks"])]
    s["hooks"]["PreToolUse"].append({"matcher": "Agent", "hooks": [{"type": "command", "command": "python3 $CLAUDE_PROJECT_DIR/.claude/hooks/mine.py"}]})
    json.dump(s, open(sp, "w"), indent=2)
    os.remove(hook)
    with open(os.path.join(proj, ".claude", "hooks", "mine.py"), "w") as fh:
        fh.write("# a project's own hook\n")
    r = subprocess.run([os.path.join(REPO, "bin", "cc-update-project"), proj], capture_output=True, text=True, env=env, timeout=500, stdin=subprocess.DEVNULL)
    check("rollout: cc-update-project exits 0", r.returncode == 0, r.stderr[-400:] + r.stdout[-400:])
    s = json.load(open(sp))
    regs = [h["command"] for e in s["hooks"]["PreToolUse"] for h in e["hooks"] if "route-nudge.py" in h["command"]]
    check("rollout: cc-update-project restores the script and the registration", os.path.isfile(hook) and len(regs) == 1, regs)
    mine = [h["command"] for e in s["hooks"]["PreToolUse"] for h in e["hooks"] if "mine.py" in h["command"]]
    check("rollout: a project's own PreToolUse hook survives", len(mine) == 1, mine)
    r = subprocess.run([os.path.join(REPO, "bin", "cc-setup"), proj], capture_output=True, text=True, env=env, timeout=400, stdin=subprocess.DEVNULL)
    s2 = json.load(open(sp))
    regs2 = [h["command"] for e in s2["hooks"]["PreToolUse"] for h in e["hooks"] if "route-nudge.py" in h["command"]]
    check("rollout: re-running is idempotent (still one registration)", r.returncode == 0 and len(regs2) == 1, regs2)
    m = re.search(r"\((\d+) hooks\)", r.stdout)
    n_templates = len([f for f in os.listdir(os.path.dirname(HOOK)) if f.endswith((".py", ".sh")) and f not in ("cc_hooklib.py", "cc_procs.py")])
    check("rollout: the installed-hooks count matches the template scripts", m and int(m.group(1)) >= n_templates, (m and m.group(1), n_templates))


def main():
    try:
        for fn in (test_nudge, test_default_resolution, test_silence, test_effort, test_latency, test_registration, test_rollout):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                check(fn.__name__ + " raised", False, "%s: %s" % (type(e).__name__, e))
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
    fails = [r for r in RESULTS if not r[1]]
    print("total=%d  pass=%d  fail=%d" % (len(RESULTS), len(RESULTS) - len(fails), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
