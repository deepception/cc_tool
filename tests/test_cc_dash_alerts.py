#!/usr/bin/env python3
"""cc-dash alerts: lib/cc_dash/attention.py and GET /api/alerts.

Builds a fake ${CLAUDE_CONFIG_DIR} (transcripts and a sessions/ registry) and a
fake attention log in a temp dir, then checks the open-alert state machine (each
open and each resolve rule, out-of-order lines, staleness, a dead process, the
registry's "answered" signal), the transcript fallback for projects without the
hook and its dedupe against hook alerts, incremental reads across a rotation,
log-file safety, the endpoint's exact shape, the static files and Host check,
and how fast an appended event (and a real hook run) reaches /api/alerts.

Run:  python3 tests/test_cc_dash_alerts.py
"""
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The interpreter is the kernel's: with zsh's exported ARGV0 (Cursor's terminal), sys.executable
# can name the editor's AppImage, and running it opens editor tabs instead of Python.
PY = os.path.realpath("/proc/self/exe") if os.path.exists("/proc/self/exe") else sys.executable
sys.path.insert(0, os.path.join(REPO, "lib"))
from cc_dash import attention  # noqa: E402
from cc_dash.attention import Attention  # noqa: E402
from cc_dash.server import make_server  # noqa: E402
from dash_pairing import auto_cookie  # noqa: E402  (tests/dash_pairing.py: every request carries the pairing cookie)
from cc_dash.store import Store  # noqa: E402

RESULTS = []
BASE = tempfile.mkdtemp(prefix="cc-dash-alerts-")
HOOK = os.path.join(REPO, "templates", "hooks", "attention.py")


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


class Env:
    """One fake config dir + attention log dir + clock per scenario."""

    def __init__(self, name):
        self.root = os.path.join(BASE, name)
        self.cfg = os.path.join(self.root, "claude")
        self.logdir = os.path.join(self.root, "state", "cc_tool")
        self.reg = os.path.join(self.cfg, "sessions")
        os.makedirs(os.path.join(self.cfg, "projects"))
        os.makedirs(self.logdir, mode=0o700)
        os.makedirs(self.reg)
        os.chmod(self.reg, 0o700)          # as Claude Code creates ~/.claude/sessions
        self.log = os.path.join(self.logdir, "attention.jsonl")
        self.now = [time.time()]
        self.store = Store(root=self.cfg, days=7, clock=lambda: self.now[0])
        self.att = Attention(self.store, log_dir=self.logdir, registry_dir=self.reg, clock=lambda: self.now[0])

    def emit(self, event, session, ts=None, **kw):
        rec = {"v": 1, "ts": round(self.now[0] if ts is None else ts, 3), "event": event, "session": session,
               "agent": kw.pop("agent", None), "agent_type": kw.pop("agent_type", None),
               "cwd": kw.pop("cwd", "/work/" + session[:4]), "transcript": ""}
        rec.update(kw)
        fd = os.open(self.log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        os.write(fd, (json.dumps(rec) + "\n").encode())
        os.close(fd)

    def alerts(self):
        self.store.refresh(force=True)
        self.att._reg_at = -1e18
        return self.att.payload()["alerts"]

    def transcript(self, sid, cwd, recs, sub=None):
        pdir = os.path.join(self.cfg, "projects", cwd.replace("/", "-"))
        path = os.path.join(pdir, sid + ".jsonl") if sub is None else os.path.join(pdir, sid, "subagents", f"agent-{sub}.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as fh:
            for r in recs:
                fh.write(json.dumps(r) + "\n")
        return path

    def register(self, pid, sid, status, waiting="", updated=None, proc_start=None):
        d = {"pid": pid, "sessionId": sid, "cwd": "/x", "kind": "interactive", "status": status,
             "statusUpdatedAt": int((self.now[0] if updated is None else updated) * 1000)}
        if waiting:
            d["waitingFor"] = waiting
        if proc_start is not None:
            d["procStart"] = proc_start
        p = os.path.join(self.reg, f"{pid}.json")
        with open(p, "w") as fh:
            json.dump(d, fh)
        os.chmod(p, 0o664)                 # as Claude Code writes them (umask 002)


def rec(sid, cwd, kind, t, **kw):
    r = {"type": kind, "timestamp": iso(t), "sessionId": sid, "cwd": cwd, "uuid": f"u{t}{kind}", "isSidechain": False}
    r.update(kw)
    return r


def tool_use(sid, cwd, t, tid, name, inp, **kw):
    return rec(sid, cwd, "assistant", t, message={"id": "m" + tid, "model": "claude-opus-5-5", "role": "assistant",
                                                  "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}],
                                                  "stop_reason": "tool_use", "usage": {"input_tokens": 1, "output_tokens": 1}}, **kw)


def tool_result(sid, cwd, t, tid, text="ok", **kw):
    return rec(sid, cwd, "user", t, message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": text}]}, **kw)


def prompt(sid, cwd, t, text="hi"):
    return rec(sid, cwd, "user", t, message={"role": "user", "content": text})


def ids(alerts):
    return sorted((a["kind"], a["agent_id"] or "", a["tool"] or "") for a in alerts)


# ── state machine ────────────────────────────────────────────────────────
def test_state_machine():
    e = Env("sm")
    S = "aaaa1111-0000-0000-0000-000000000001"
    t = e.now[0]
    # 1. permission opens, the same tool's PostToolUse resolves it
    e.emit("PermissionRequest", S, t, tool="Bash", sig="rm -rf build", message="rm -rf build", mode="default")
    a = e.alerts()
    check("permission: opens", ids(a) == [("permission", "", "Bash")], str(ids(a)))
    if a:
        x = a[0]
        check("permission: title and message", x["title"] == "Wants to run a command" and x["message"] == "rm -rf build", str(x))
        check("permission: source hook, project from cwd", x["source"] == "hook" and x["project"] == "aaaa", str(x))
        check("permission: since is ISO 8601 UTC", x["since"].endswith("Z") and abs(datetime.fromisoformat(x["since"].replace("Z", "+00:00")).timestamp() - t) < 0.01)
    e.emit("PostToolUse", S, t + 1, tool="Bash", sig="ls", tool_use_id="x")
    check("permission: another tool finishing does not resolve it", len(e.alerts()) == 1)
    e.emit("PostToolUse", S, t + 2, tool="Bash", sig="rm -rf build", tool_use_id="y")
    check("permission: resolved when that tool ran", e.alerts() == [])
    # 2. denied: no PostToolUse, the batch end resolves it
    e.emit("PermissionRequest", S, t + 3, tool="Write", sig="/w/a.py", message="/w/a.py")
    check("denied: open", len(e.alerts()) == 1)
    e.emit("PostToolBatch", S, t + 4)
    check("denied: PostToolBatch resolves it", e.alerts() == [])
    # 3. AskUserQuestion is a question; the late permission_prompt notification merges into it
    e.emit("PermissionRequest", S, t + 5, tool="AskUserQuestion", sig="Tea or coffee?", message="Tea or coffee?")
    e.emit("Notification", S, t + 11, ntype="permission_prompt", message="Claude needs your permission")
    a = e.alerts()
    check("question: one alert, kind question", ids(a) == [("question", "", "AskUserQuestion")], str(ids(a)))
    check("question: notification marks it, adds nothing", a and a[0]["notified"] is True and a[0]["title"] == "Has a question for you")
    e.emit("PostToolUse", S, t + 12, tool="AskUserQuestion", sig="Tea or coffee?")
    check("question: answered (tool ran) resolves", e.alerts() == [])
    check("question: the late notification did not reopen anything", e.alerts() == [])
    # 4. plan, resolved by Stop
    e.emit("PermissionRequest", S, t + 13, tool="ExitPlanMode", sig="", message="Refactor auth")
    a = e.alerts()
    check("plan: kind plan with the plan heading", ids(a) == [("plan", "", "ExitPlanMode")] and a[0]["message"] == "Refactor auth", str(a))
    e.emit("Stop", S, t + 14)
    check("plan: Stop resolves the main thread", e.alerts() == [])
    # 5. subagent scope
    e.emit("PermissionRequest", S, t + 15, tool="Bash", sig="make", agent="ag1", agent_type="Explore")
    e.emit("PostToolBatch", S, t + 16)            # main thread batch
    a = e.alerts()
    check("subagent: agent_id set, survives the main thread's batch", ids(a) == [("permission", "ag1", "Bash")], str(ids(a)))
    check("subagent: agent label falls back to its type", a and a[0]["agent_label"] == "Explore", str(a and a[0]))
    e.emit("Stop", S, t + 17)
    check("subagent: main Stop leaves it", len(e.alerts()) == 1)
    e.emit("SubagentStop", S, t + 18, agent="ag1")
    check("subagent: SubagentStop resolves it", e.alerts() == [])
    # 6. idle and a new prompt
    e.emit("Notification", S, t + 80, ntype="idle_prompt", message="Claude is waiting for your input")
    a = e.alerts()
    check("idle: opens", ids(a) == [("idle", "", "")] and a[0]["title"] == "Waiting for your input", str(a))
    e.emit("UserPromptSubmit", S, t + 90)
    check("idle: a new prompt resolves it", e.alerts() == [])
    # 7. API error
    e.emit("StopFailure", S, t + 91, error="rate_limit", message="429 Too Many Requests")
    a = e.alerts()
    check("error: opens with the error type", ids(a) == [("error", "", "")] and "rate limit" in a[0]["title"], str(a))
    e.emit("UserPromptSubmit", S, t + 92)
    check("error: next prompt resolves it", e.alerts() == [])
    # 8. a bare permission notification (e.g. sandbox network prompt) and its resolve
    e.emit("Notification", S, t + 200, ntype="permission_prompt", message="Claude needs your permission")
    a = e.alerts()
    check("notify-only permission: opens with no tool", ids(a) == [("permission", "", "")], str(ids(a)))
    e.emit("PostToolUse", S, t + 201, tool="Bash", sig="curl x")
    check("notify-only permission: next tool run resolves it", e.alerts() == [])
    # 9. a late notification right after a resolved prompt is ignored
    e.emit("PermissionRequest", S, t + 300, tool="Bash", sig="a")
    e.emit("PostToolUse", S, t + 301, tool="Bash", sig="a")
    e.emit("Notification", S, t + 306, ntype="permission_prompt", message="Claude needs your permission")
    check("notify grace: a notification 5 s after the answer opens nothing", e.alerts() == [])
    # 10. MCP elicitation
    e.emit("Elicitation", S, t + 400, server="db", message="Password?")
    a = e.alerts()
    check("elicitation: other, names the server", ids(a) == [("other", "", "")] and "db" in a[0]["title"], str(a))
    e.emit("ElicitationResult", S, t + 401, server="db")
    check("elicitation: result resolves it", e.alerts() == [])
    # 11. SessionEnd clears everything in the session, subagents included
    e.emit("PermissionRequest", S, t + 500, tool="Bash", sig="x")
    e.emit("PermissionRequest", S, t + 501, tool="Bash", sig="y", agent="ag2")
    check("session end: two open", len(e.alerts()) == 2)
    e.emit("SessionEnd", S, t + 502, reason="other")
    check("session end: all resolved", e.alerts() == [])
    # 12. out of order (async hooks): the batch end lands first, the older open after it
    S2 = "bbbb2222-0000-0000-0000-000000000002"
    e.emit("PostToolBatch", S2, t + 601)
    e.emit("PermissionRequest", S2, t + 600, tool="Bash", sig="late")
    check("out of order: an open older than a seen resolve stays closed", e.alerts() == [])
    e.emit("PostToolUse", S2, t + 701, tool="Bash", sig="z")
    e.emit("PermissionRequest", S2, t + 700, tool="Bash", sig="z")
    check("out of order: same for a tool that already finished", e.alerts() == [])
    # 13. two sessions at once, ids stable across polls, new id for a reopened scope
    e.emit("PermissionRequest", S, t + 800, tool="Bash", sig="p")
    e.emit("PermissionRequest", S2, t + 801, tool="Bash", sig="p")
    a1 = e.alerts()
    a2 = e.alerts()
    check("ids: two sessions, two alerts, newest first", [x["session"] for x in a1] == [S2, S], str([x["session"] for x in a1]))
    check("ids: stable across polls", [x["id"] for x in a1] == [x["id"] for x in a2])
    e.emit("PostToolUse", S, t + 802, tool="Bash", sig="p")
    e.emit("PermissionRequest", S, t + 803, tool="Bash", sig="p")
    a3 = e.alerts()
    check("ids: the same request asked again is a new alert", {x["id"] for x in a3 if x["session"] == S}.isdisjoint({x["id"] for x in a1}))
    # 14. no transcript yet: a worktree folds into its repo, as on the board (store.resolve_projects)
    S3 = "cdef3333-0000-0000-0000-000000000003"
    e.emit("PermissionRequest", S3, t + 900, tool="Bash", sig="w", cwd="/work/myrepo/.claude/worktrees/agent-x")
    a4 = [x for x in e.alerts() if x["session"] == S3]
    check("label: a worktree cwd without a transcript is labelled with its repo", a4 and a4[0]["project"] == "myrepo"
          and a4[0]["worktree"] == "agent-x", str(a4))


def test_staleness_and_registry():
    e = Env("stale")
    S = "cccc3333-0000-0000-0000-000000000003"
    t = e.now[0]
    e.emit("PermissionRequest", S, t, tool="Bash", sig="x")
    e.emit("Notification", S, t, ntype="idle_prompt", message="idle", agent=None)
    check("stale: both open at first", len(e.alerts()) == 2)
    e.now[0] = t + 2 * 3600
    a = e.alerts()
    check("stale: idle expires after an hour of silence, permission stays", ids(a) == [("permission", "", "Bash")], str(ids(a)))
    e.now[0] = t + 9 * 3600
    check("stale: permission expires after 8 h of silence", e.alerts() == [])
    e.emit("PermissionRequest", S, t + 9 * 3600, tool="Bash", sig="x2")
    check("stale: a new event in the session alerts again", len(e.alerts()) == 1)
    # dead process in the registry
    e2 = Env("dead")
    S2 = "dddd4444-0000-0000-0000-000000000004"
    e2.emit("PermissionRequest", S2, tool="Bash", sig="x")
    e2.register(999999, S2, "waiting", "permission prompt")
    check("registry: a dead pid drops the session's alerts", e2.alerts() == [])
    # pid reuse: alive pid, other start time
    e3 = Env("reuse")
    S3 = "eeee5555-0000-0000-0000-000000000005"
    e3.emit("PermissionRequest", S3, tool="Bash", sig="x")
    e3.register(os.getpid(), S3, "waiting", "permission prompt", proc_start="1")
    check("registry: a reused pid (start time differs) counts as gone", e3.alerts() == [] or not os.path.exists("/proc"))
    # answered: waiting seen, then busy with a later timestamp, tool still running
    e4 = Env("answered")
    S4 = "ffff6666-0000-0000-0000-000000000006"
    t4 = e4.now[0]
    e4.emit("PermissionRequest", S4, t4, tool="Bash", sig="sleep 20 && tee g")
    e4.register(os.getpid(), S4, "busy", updated=t4 - 5)
    check("registry: busy from before the prompt does not resolve it", len(e4.alerts()) == 1)
    e4.register(os.getpid(), S4, "waiting", "permission prompt", updated=t4 + 0.08)
    check("registry: waiting keeps it open", len(e4.alerts()) == 1)
    e4.register(os.getpid(), S4, "busy", updated=t4 + 3)
    check("registry: back to busy after it resolves it (granted, tool still running)", e4.alerts() == [])
    e4.emit("PostToolUse", S4, t4 + 20, tool="Bash", sig="sleep 20 && tee g")
    check("registry: the later PostToolUse is a no-op", e4.alerts() == [])
    # registry files others could rewrite are not trusted
    e6 = Env("regperm")
    S6 = "cdcd8888-0000-0000-0000-000000000008"
    e6.emit("PermissionRequest", S6, tool="Bash", sig="x")
    e6.register(999998, S6, "waiting", "permission prompt")
    os.chmod(e6.reg, 0o755)
    check("registry: a 0664 file in a directory others can enter is ignored", len(e6.alerts()) == 1)
    os.chmod(e6.reg, 0o700)
    check("registry: the same file in a 0700 directory is trusted (dead pid drops the alert)", e6.alerts() == [])
    # never saw it waiting: the session-wide status alone does not close a prompt
    e5 = Env("unseen")
    S5 = "abab7777-0000-0000-0000-000000000007"
    t5 = e5.now[0]
    e5.emit("PermissionRequest", S5, t5, tool="Bash", sig="x", agent="sub1")
    e5.register(os.getpid(), S5, "idle", updated=t5 + 4)
    check("registry: no waiting seen, a status change alone keeps it", len(e5.alerts()) == 1)


def test_transcript_fallback():
    e = Env("tx")
    t = e.now[0] - 30
    S = "1234aaaa-0000-0000-0000-000000000010"
    cwd = "/work/nohooks"
    e.transcript(S, cwd, [prompt(S, cwd, t), tool_use(S, cwd, t + 1, "toolu_q", "AskUserQuestion",
                                                       {"questions": [{"question": "Which DB?", "options": []}]})])
    a = e.alerts()
    check("transcript: pending AskUserQuestion is a question", ids(a) == [("question", "", "AskUserQuestion")], str(ids(a)))
    if a:
        check("transcript: source transcript, project from cwd", a[0]["source"] == "transcript" and a[0]["project"] == "nohooks", str(a[0]))
        check("transcript: message is the question", a[0]["message"] == "Which DB?", a[0]["message"])
        check("transcript: id is stable", a[0]["id"] == e.alerts()[0]["id"])
    e.transcript(S, cwd, [tool_result(S, cwd, t + 5, "toolu_q", "DB: Postgres")])
    check("transcript: answered (tool_result) resolves it", e.alerts() == [])
    # ExitPlanMode in a subagent transcript
    S2 = "1234bbbb-0000-0000-0000-000000000011"
    e.transcript(S2, cwd, [prompt(S2, cwd, t)])
    e.transcript(S2, cwd, [tool_use(S2, cwd, t + 2, "toolu_p", "ExitPlanMode", {}, isSidechain=True, agentId="sub7")], sub="sub7")
    a = [x for x in e.alerts() if x["session"] == S2]
    check("transcript: pending ExitPlanMode in a subagent is a plan with agent_id", ids(a) == [("plan", "sub7", "ExitPlanMode")], str(ids(a)))
    # A long-running tool is not a permission prompt
    S3 = "1234cccc-0000-0000-0000-000000000012"
    e.transcript(S3, cwd, [prompt(S3, cwd, t), tool_use(S3, cwd, t + 1, "toolu_b", "Bash", {"command": "sleep 600"})])
    check("transcript: a pending Bash call alone is not an alert", not [x for x in e.alerts() if x["session"] == S3])
    # ...unless the registry says the session waits on a permission prompt
    e.register(os.getpid(), S3, "waiting", "permission prompt", updated=t + 1.2)
    a = [x for x in e.alerts() if x["session"] == S3]
    check("registry fallback: waiting on a permission prompt is a permission alert", ids(a) == [("permission", "", "Bash")], str(ids(a)))
    check("registry fallback: names the pending command", a and a[0]["message"] == "sleep 600" and a[0]["source"] == "transcript", str(a))
    e.register(os.getpid(), S3, "busy", updated=t + 4)
    check("registry fallback: gone once the session is busy again", not [x for x in e.alerts() if x["session"] == S3])
    # A question that was followed by more activity is not pending any more
    S4 = "1234dddd-0000-0000-0000-000000000013"
    e.transcript(S4, cwd, [prompt(S4, cwd, t), tool_use(S4, cwd, t + 1, "toolu_x", "AskUserQuestion", {"questions": []}),
                           prompt(S4, cwd, t + 50, "never mind")])
    check("transcript: a question followed by a new prompt is not pending", not [x for x in e.alerts() if x["session"] == S4])
    # Dedupe: a session the hook covers never gets a transcript alert
    S5 = "1234eeee-0000-0000-0000-000000000014"
    e.transcript(S5, cwd, [prompt(S5, cwd, t), tool_use(S5, cwd, t + 1, "toolu_y", "AskUserQuestion",
                                                         {"questions": [{"question": "Q?"}]})])
    e.emit("PermissionRequest", S5, t + 1.05, tool="AskUserQuestion", sig="Q?", message="Q?", cwd=cwd)
    a = [x for x in e.alerts() if x["session"] == S5]
    check("dedupe: hook and transcript see the same question, one alert from the hook",
          len(a) == 1 and a[0]["source"] == "hook", str(a))
    e.emit("PostToolUse", S5, t + 9, tool="AskUserQuestion", sig="Q?")
    check("dedupe: once the hook resolves it, the stale transcript does not resurrect it",
          not [x for x in e.alerts() if x["session"] == S5])
    # Old transcript question: stale
    S6 = "1234ffff-0000-0000-0000-000000000015"
    p = e.transcript(S6, cwd, [prompt(S6, cwd, t - 10 * 3600),
                               tool_use(S6, cwd, t - 10 * 3600 + 1, "toolu_o", "AskUserQuestion", {"questions": []})])
    os.utime(p, (t - 10 * 3600, t - 10 * 3600))
    check("transcript: a question pending for 10 h in a silent session is stale", not [x for x in e.alerts() if x["session"] == S6])
    # project filter
    e.store.project = "nohooks"
    check("filter: --project keeps matching sessions", any(x["session"] == S2 for x in e.alerts()))
    e.store.project = "zzz"
    check("filter: --project drops others", e.alerts() == [])


def test_tail_and_safety():
    e = Env("tail")
    S = "77770000-0000-0000-0000-000000000020"
    t = e.now[0]
    e.emit("PermissionRequest", S, t, tool="Bash", sig="a")
    with open(e.log, "a") as fh:
        fh.write("{not json\n")
        fh.write('{"v":1,"ts":"bad","event":"Stop","session":"x"}\n')
    a = e.alerts()
    check("tail: malformed lines are skipped and counted", len(a) == 1 and e.att.tail.bad_lines == 1, str(e.att.tail.bad_lines))
    read0 = e.att.tail.bytes_read
    e.alerts()
    check("tail: a poll with nothing new reads nothing", e.att.tail.bytes_read == read0)
    # partial trailing line waits for its newline
    with open(e.log, "a") as fh:
        fh.write(json.dumps({"v": 1, "ts": t + 1, "event": "PostToolUse", "session": S, "tool": "Bash", "sig": "a"}))
    check("tail: a line without its newline is not read yet", len(e.alerts()) == 1)
    with open(e.log, "a") as fh:
        fh.write("\n")
    check("tail: completed line is read", e.alerts() == [])
    # rotation: the hook renames the log to .1 and starts a new one
    e.emit("PermissionRequest", S, t + 2, tool="Bash", sig="b")
    check("rotation: open before", len(e.alerts()) == 1)
    e.emit("PostToolUse", S, t + 3, tool="Bash", sig="b")        # lands in the old file, unread
    os.replace(e.log, e.log + ".1")
    e.emit("PermissionRequest", S, t + 4, tool="Bash", sig="c")
    a = e.alerts()
    check("rotation: the unread tail of .1 and the new log are both read, nothing replayed",
          ids(a) == [("permission", "", "Bash")] and a[0]["message"] == "c", str(a))
    check("rotation: tracked files follow the inodes", len(e.att.tail.offsets) == 2)
    # a cold start reads .1 then the log
    fresh = Attention(e.store, log_dir=e.logdir, registry_dir=e.reg, clock=lambda: e.now[0])
    fresh._reg_at = -1e18
    a2 = fresh.payload()["alerts"]
    check("cold start: same open set from .1 + log", ids(a2) == ids(a) and [x["id"] for x in a2] == [x["id"] for x in a], str(a2))
    # unsafe log files are ignored
    e2 = Env("unsafe")
    e2.emit("PermissionRequest", "s-unsafe", tool="Bash", sig="x")
    os.chmod(e2.log, 0o666)
    check("safety: a world-writable log is ignored", e2.alerts() == [])
    os.chmod(e2.log, 0o600)
    os.chmod(e2.logdir, 0o777)
    check("safety: a world-writable log dir is ignored", Attention(e2.store, log_dir=e2.logdir, registry_dir=e2.reg).payload()["alerts"] == [])
    os.chmod(e2.logdir, 0o700)
    e3 = Env("symlinked")
    real = os.path.join(e3.root, "real.jsonl")
    with open(real, "w") as fh:
        fh.write(json.dumps({"v": 1, "ts": e3.now[0], "event": "PermissionRequest", "session": "s-link", "tool": "Bash", "sig": "x"}) + "\n")
    os.chmod(real, 0o600)
    os.symlink(real, e3.log)
    check("safety: a symlinked log is ignored", e3.alerts() == [])


# ── endpoint ────────────────────────────────────────────────────────────
def get(port, path, host=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path, headers={"Host": host or f"127.0.0.1:{port}"})
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, dict(r.getheaders()), body


def test_endpoint():
    root = os.path.join(BASE, "srv")
    cfg = os.path.join(root, "claude")
    state = os.path.join(root, "state")
    os.makedirs(os.path.join(cfg, "projects"))
    saved = os.environ.get("XDG_STATE_HOME")
    os.environ["XDG_STATE_HOME"] = state
    try:
        store = Store(root=cfg, days=7)
        srv = make_server(0, store, fallback=True)
        import threading
        th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        th.start()
        port = srv.port
        auto_cookie(srv)
        try:
            st, hd, body = get(port, "/api/alerts")
            d = json.loads(body)
            check("endpoint: 200 JSON with alerts + now", st == 200 and d["alerts"] == [] and isinstance(d["now"], str)
                  and d["now"].endswith("Z"), body[:200])
            check("endpoint: no-store and CSP like every response", hd.get("Cache-Control") == "no-store" and "default-src 'none'" in hd.get("Content-Security-Policy", ""))
            st, _, _ = get(port, "/api/alerts", host="evil.example:80")
            check("endpoint: foreign Host header gets 421", st == 421, str(st))
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            c.request("POST", "/api/alerts", body=b"{}", headers={"Host": f"127.0.0.1:{port}"})
            r = c.getresponse()
            r.read()
            c.close()
            check("endpoint: POST is refused (read-only)", r.status == 405, str(r.status))
            for path, ctype in (("/alerts.js", "javascript"), ("/alerts.css", "text/css"), ("/alerts-worker.js", "javascript")):
                st, hd, body = get(port, path)
                check(f"static: {path} served", st == 200 and ctype in hd.get("Content-Type", "") and len(body) > 200, str(st))
            st, _, body = get(port, "/")
            check("static: index.html loads alerts.css and alerts.js", b'href="/alerts.css"' in body and b'src="/alerts.js"' in body)
            for bad in ("/alerts.json", "/web/alerts.js", "/../attention.py", "/alerts.js/../server.py"):
                st, _, _ = get(port, bad)
                check(f"static: {bad} is 404", st == 404, str(st))
            # shape, via the real hook
            sid = "9999aaaa-bbbb-cccc-dddd-eeeeffff0000"
            payload = {"session_id": sid, "hook_event_name": "PermissionRequest", "cwd": "/work/shape", "transcript_path": "/t",
                       "tool_name": "Bash", "tool_input": {"command": "<b>rm</b> -rf \"x\" & echo '<script>'"}, "agent_id": "agX",
                       "agent_type": "general-purpose"}
            env = dict(os.environ, XDG_STATE_HOME=state)
            t0 = time.time()
            subprocess.run([PY, HOOK], input=json.dumps(payload).encode(), env=env, check=True)
            seen = None
            while time.time() - t0 < 5:
                d = json.loads(get(port, "/api/alerts")[2])
                if d["alerts"]:
                    seen = time.time() - t0
                    break
                time.sleep(0.05)
            check("timing: a real hook run shows up in /api/alerts", seen is not None)
            if seen is not None:
                print(f"  hook process start to /api/alerts: {seen * 1000:.0f} ms (server side; the page polls every 1.5 s)")
                check("timing: hook to API under 1 s", seen < 1.0, f"{seen:.3f}")
                a = d["alerts"][0]
                want = {"id": str, "session": str, "agent_id": (str, type(None)), "project": str, "cwd": str, "kind": str,
                        "title": str, "message": str, "tool": (str, type(None)), "since": str, "source": str}
                bad = [k for k, ty in want.items() if k not in a or not isinstance(a[k], ty)]
                check("shape: every contract field with its type", not bad, str(bad))
                check("shape: values", a["session"] == sid and a["agent_id"] == "agX" and a["kind"] == "permission"
                      and a["tool"] == "Bash" and a["source"] == "hook" and a["project"] == "shape" and a["cwd"] == "/work/shape", str(a))
                check("shape: kind and source in their enums", a["kind"] in ("permission", "question", "plan", "idle", "error", "other")
                      and a["source"] in ("hook", "transcript"))
                check("shape: text passes through as data (escaped by the page)", a["message"].startswith("<b>rm</b>"), a["message"])
                datetime.fromisoformat(a["since"].replace("Z", "+00:00"))
            # resolve and appended-event timing, many rounds
            lat = []
            for i in range(10):
                t1 = time.time()
                rec = {"v": 1, "ts": round(t1, 3), "event": "PermissionRequest", "session": sid, "agent": None,
                       "tool": "Bash", "sig": f"cmd{i}", "message": f"cmd{i}", "cwd": "/work/shape"}
                fd = os.open(os.path.join(state, "cc_tool", "attention.jsonl"), os.O_WRONLY | os.O_APPEND)
                os.write(fd, (json.dumps(rec) + "\n").encode())
                os.close(fd)
                while time.time() - t1 < 5:
                    d = json.loads(get(port, "/api/alerts")[2])
                    if any(x["message"] == f"cmd{i}" for x in d["alerts"]):
                        lat.append(time.time() - t1)
                        break
                    time.sleep(0.02)
                rec.update(event="PostToolUse", ts=round(time.time(), 3))
                fd = os.open(os.path.join(state, "cc_tool", "attention.jsonl"), os.O_WRONLY | os.O_APPEND)
                os.write(fd, (json.dumps(rec) + "\n").encode())
                os.close(fd)
            check("timing: every appended event visible", len(lat) == 10, str(len(lat)))
            if lat:
                print(f"  appended line to /api/alerts: max {max(lat) * 1000:.0f} ms over {len(lat)} rounds")
                check("timing: appended event visible within 1 s", max(lat) < 1.0, f"{max(lat):.3f}")
            time.sleep(1.1)
            d = json.loads(get(port, "/api/alerts")[2])
            check("resolve: resolved alerts disappear from the endpoint",
                  not any(x["message"].startswith("cmd") for x in d["alerts"]), str([x["message"] for x in d["alerts"]]))
            check("read-only: the endpoint wrote nothing under the config dir",
                  sorted(os.listdir(cfg)) == ["projects"], str(os.listdir(cfg)))
        finally:
            srv.shutdown()
            srv.server_close()
    finally:
        if saved is None:
            os.environ.pop("XDG_STATE_HOME", None)
        else:
            os.environ["XDG_STATE_HOME"] = saved


def test_state_dir():
    saved = os.environ.get("XDG_STATE_HOME")
    try:
        os.environ["XDG_STATE_HOME"] = "/abs/state"
        check("state_dir: XDG_STATE_HOME", attention.state_dir() == "/abs/state/cc_tool")
        os.environ["XDG_STATE_HOME"] = "rel"
        check("state_dir: relative value ignored", attention.state_dir() == os.path.join(os.path.expanduser("~"), ".local", "state", "cc_tool"))
    finally:
        if saved is None:
            os.environ.pop("XDG_STATE_HOME", None)
        else:
            os.environ["XDG_STATE_HOME"] = saved


def main():
    try:
        for fn in (test_state_machine, test_staleness_and_registry, test_transcript_fallback, test_tail_and_safety,
                   test_endpoint, test_state_dir):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                check(fn.__name__ + " raised", False, f"{type(e).__name__}: {e}")
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
    fails = [r for r in RESULTS if not r[1]]
    print("total=%d  pass=%d  fail=%d" % (len(RESULTS), len(RESULTS) - len(fails), len(fails)))
    for name, _, detail in fails:
        print("  FAIL %-66s %s" % (name, detail[:240]))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
