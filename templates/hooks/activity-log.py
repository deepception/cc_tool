#!/usr/bin/env python3
"""Activity log + session trajectory (PostToolUse hook, every tool).

Two jobs, both silent (this hook never prints to Claude):

1. Append one line per tool call to .git/cc_tool/activity.jsonl:
     {"ts", "kind": "tool", "session", "tool", "target", "ok"}
   `target` is the file path, the first 200 chars of a Bash command, or the
   pattern for search tools. bash-guard.py and write-guard.py append their
   deny/ask/warn decisions to the same file, so the log answers "what did the
   guards actually catch this week?" with grep, and gives a loop or an
   unattended run a disk record of what happened. Rotates at 5 MB; lives under
   .git/ so it is never committed and dies with the clone.

2. Record what Claude has seen: after a Read, Edit, Write or MultiEdit the
   file's current mtime goes into the session state. write-guard.py compares
   that to the mtime at the next edit — if the file moved on since, the edit is
   about to be built on a stale picture (a formatter, a `sed -i`, another
   agent) and Claude is told to re-read. post-edit-typecheck.py records red
   checks in the same state.

Any failure is swallowed; the hook must never affect the session.
"""
import json
import os
import sys

try:
    import cc_hooklib as lib
except Exception:  # noqa: BLE001
    sys.exit(0)

_FILE_TOOLS = {"Read", "Edit", "Write", "MultiEdit", "NotebookEdit"}


def _target(tool: str, ti: dict) -> str:
    if tool == "Bash":
        return (ti.get("command") or "")[:200]
    for key in ("file_path", "path", "notebook_path", "pattern", "url", "skill", "prompt", "description"):
        v = ti.get(key)
        if isinstance(v, str) and v:
            return v[:200]
    return ""


def _ok(resp) -> bool:
    if isinstance(resp, dict):
        if resp.get("is_error") or resp.get("error"):
            return False
        if "success" in resp:
            return bool(resp["success"])
    return True


def main() -> None:
    data = json.load(sys.stdin)
    if not isinstance(data, dict):
        return
    tool = data.get("tool_name") or ""
    ti = data.get("tool_input") or {}
    if not isinstance(ti, dict):
        ti = {}
    root = lib.repo_root()
    session = data.get("session_id") or ""
    lib.log_event(root, "tool", session=session[:12], tool=tool,
                  target=_target(tool, ti), ok=_ok(data.get("tool_response")))
    if tool in _FILE_TOOLS:
        path = ti.get("file_path") or ti.get("path") or ti.get("notebook_path") or ""
        if isinstance(path, str) and path:
            real = os.path.realpath(path)
            mtime = lib.file_mtime(real)
            if mtime:
                state = lib.load_session(root, session)
                state["seen"][real] = mtime
                lib.save_session(root, session, state)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        pass
    sys.exit(0)
