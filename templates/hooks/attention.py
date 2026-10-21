#!/usr/bin/env python3
"""Attention events for cc-dash: when an agent needs you, and when it moved on.

Registered (async, so it never delays the session) on the events that open or
close a "needs you" state, and appends one JSON line per event to a per-user log
that `cc-dash` tails across every project:

    ${XDG_STATE_HOME:-~/.local/state}/cc_tool/attention.jsonl

Opens:  PermissionRequest (a permission dialog, also the dialog behind
        AskUserQuestion and ExitPlanMode), Notification (permission_prompt,
        idle_prompt, elicitation dialogs, agent_needs_input, ...), Elicitation,
        StopFailure (the turn died on an API error).
Closes: PostToolUse / PostToolUseFailure (that tool ran), PostToolBatch (every
        call in the batch was granted or denied), UserPromptSubmit (a new
        prompt), Stop / SubagentStop (the turn ended), ElicitationResult,
        SessionEnd. cc-dash turns the stream into open alerts; see
        lib/cc_dash/attention.py.

Line shape: {"v": 1, "ts", "event", "session", "agent", "agent_type", "cwd",
"transcript", ...event fields: "tool", "sig", "tool_use_id", "message",
"title", "ntype", "mode", "error", "server", "reason"}.

The log is user-private: the directory is created 0700 and the file 0600,
neither is followed if it is a symlink, a file that is not ours or not a plain
single-link file is left alone, and the log rotates to .1 past 5 MB. Same
convention as activity-log.py: silent (never prints to Claude), never blocks,
every error swallowed, exit 0. It does not import cc_hooklib: that pulls in
tempfile, and this hook runs on every tool call, so it keeps to os/json/time
(about 20 ms, nearly all of it interpreter start-up).
"""
import json
import os
import stat
import sys
import time

MAX_BYTES = 5_000_000
CLIP = 400
TARGET_KEYS = ("file_path", "notebook_path", "path", "url", "pattern", "query", "skill",
               "description", "prompt", "command", "message")


def state_dir():
    base = os.environ.get("XDG_STATE_HOME") or ""
    if not os.path.isabs(base):  # the XDG spec says to ignore a relative value
        home = os.path.expanduser("~")
        if not os.path.isabs(home):
            return ""
        base = os.path.join(home, ".local", "state")
    return os.path.join(base, "cc_tool")


def private_dir(d):
    """Create d as 0700; True only when it is a real directory we own."""
    try:
        os.makedirs(os.path.dirname(d), mode=0o700, exist_ok=True)
    except OSError:
        pass
    try:
        os.mkdir(d, 0o700)
    except FileExistsError:
        pass
    except OSError:
        return False
    st = os.lstat(d)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        return False  # a symlink (S_ISLNK) or someone else's directory
    if st.st_mode & 0o077:
        os.chmod(d, 0o700)
    return True


def append(path, line):
    try:
        st = os.lstat(path)
        if stat.S_ISREG(st.st_mode) and st.st_size > MAX_BYTES:
            os.replace(path, path + ".1")  # rename never follows a symlink at either end
    except OSError:
        pass
    flags = (os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0))
    fd = os.open(path, flags, 0o600)  # O_NOFOLLOW: a planted symlink raises ELOOP
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_nlink != 1:
            return
        if st.st_mode & 0o077:
            os.fchmod(fd, 0o600)
        os.write(fd, line)  # one write on an O_APPEND fd: lines from parallel hooks do not interleave
    finally:
        os.close(fd)


def clip(s, n=CLIP):
    s = s if isinstance(s, str) else ("" if s is None else str(s))
    s = " ".join(s.split()) if len(s) < 4 * n else " ".join(s[: 4 * n].split())
    return s if len(s) <= n else s[: n - 1] + "…"


def target(tool, ti):
    """What the call acts on; also the key that pairs a permission with its tool run."""
    if not isinstance(ti, dict):
        return ""
    if tool == "Bash":
        return clip(ti.get("command"), 300)
    if tool == "AskUserQuestion":
        qs = ti.get("questions")
        if isinstance(qs, list) and qs and isinstance(qs[0], dict):
            return clip(qs[0].get("question"), 300)
        return ""
    if tool == "ExitPlanMode":
        return ""  # the plan text is injected for hooks and differs between events
    for k in TARGET_KEYS:
        v = ti.get(k)
        if isinstance(v, str) and v:
            return clip(v, 300)
    return ""


def plan_title(ti):
    plan = ti.get("plan") if isinstance(ti, dict) else ""
    for ln in (plan or "").splitlines():
        ln = ln.strip().lstrip("#").strip()
        if ln:
            return clip(ln, 200)
    return ""


def build(d):
    ev = d.get("hook_event_name") or ""
    sid = d.get("session_id") or ""
    if not ev or not isinstance(sid, str) or not sid:
        return None
    rec = {"v": 1, "ts": round(time.time(), 3), "event": ev, "session": sid[:100],
           "agent": (d.get("agent_id") or None), "agent_type": d.get("agent_type") or None,
           "cwd": d.get("cwd") or "", "transcript": d.get("transcript_path") or ""}
    tool = d.get("tool_name") or ""
    ti = d.get("tool_input") if isinstance(d.get("tool_input"), dict) else {}
    if ev in ("PermissionRequest", "PostToolUse", "PostToolUseFailure"):
        rec["tool"] = tool
        rec["sig"] = target(tool, ti)
        if d.get("tool_use_id"):
            rec["tool_use_id"] = d["tool_use_id"]
        if ev == "PermissionRequest":
            rec["mode"] = d.get("permission_mode") or ""
            rec["message"] = plan_title(ti) if tool == "ExitPlanMode" else rec["sig"]
            if isinstance(d.get("mcp_server"), dict):
                rec["server"] = clip(d["mcp_server"].get("name"), 80)
    elif ev == "Notification":
        rec["ntype"] = d.get("notification_type") or ""
        rec["title"] = clip(d.get("title"), 120)
        rec["message"] = clip(d.get("message"))
    elif ev == "Elicitation":
        rec["server"] = clip(d.get("mcp_server_name"), 80)
        rec["message"] = clip(d.get("message"))
        rec["mode"] = d.get("mode") or ""
    elif ev == "ElicitationResult":
        rec["server"] = clip(d.get("mcp_server_name"), 80)
    elif ev == "StopFailure":
        rec["error"] = clip(d.get("error"), 60)
        rec["message"] = clip(d.get("error_details") or d.get("last_assistant_message"))
    elif ev == "SessionEnd":
        rec["reason"] = d.get("reason") or ""
    return rec


def main():
    d = json.loads(sys.stdin.read() or "null")
    if not isinstance(d, dict):
        return
    rec = build(d)
    if rec is None:
        return
    sd = state_dir()
    if not sd or not private_dir(sd):
        return
    line = (json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8", "replace")
    append(os.path.join(sd, "attention.jsonl"), line)


if __name__ == "__main__":
    try:
        main()
    except BaseException:  # noqa: BLE001 - never affect the session, whatever happens
        pass
    sys.exit(0)
