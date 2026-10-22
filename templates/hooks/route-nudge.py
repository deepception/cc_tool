#!/usr/bin/env python3
"""Nudge, never block: an Agent call that names no `model` (PreToolUse, matcher Agent|Task).

The managed CLAUDE.md block says to name `model` on every Agent call and never
let the default pick it: left unnamed, a `general-purpose` agent falls back to
CLAUDE_CODE_SUBAGENT_MODEL (Opus here) and `Explore`/`Plan` inherit the session
model, so an arm that never needed Opus runs on it unnoticed. This hook adds one
line of context to such a call so the next one names its model. It never asks,
never denies, never edits the call, and prints nothing for a call that names a
model.

Output is hookSpecificOutput.additionalContext, with no permissionDecision, so
the call proceeds exactly as before and Claude reads the line next to the tool
result. When the call also names no `effort` and the session's effort (the
`effort.level` Claude Code puts in the hook input) is above medium, the line
adds that subagents inherit it. Nothing else is read: no transcript, no files
beyond the agent type's definition header when the call names a type.

Quiet by construction: exit 0 on any error or odd input, no imports beyond the
standard library's json/os/sys, no state, no log.
"""
import json
import os
import sys

SPAWN_TOOLS = ("Agent", "Task")
ABOVE_MEDIUM = ("high", "xhigh", "max")
INHERITS_SESSION = ("Explore", "Plan")


def _definition_model(agent_type, cwd):
    """The `model:` in an agent type's definition header, or '' (a plain header read, no parsing beyond that)."""
    if not agent_type or not agent_type.replace("-", "").replace("_", "").replace(".", "").isalnum():
        return ""
    roots = [os.path.join(os.environ.get("CLAUDE_PROJECT_DIR") or cwd or "", ".claude", "agents"),
             os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"), "agents")]
    for d in roots:
        try:
            with open(os.path.join(d, agent_type + ".md"), encoding="utf-8", errors="replace") as fh:
                if fh.readline().strip() != "---":
                    continue
                for _ in range(40):
                    line = fh.readline()
                    if not line or line.strip() == "---":
                        break
                    k, _, v = line.partition(":")
                    if k.strip().lower() == "model":
                        v = v.strip().strip("'\"")
                        return "" if v.lower() == "inherit" else v
        except OSError:
            continue
    return ""


def default_model(agent_type, cwd):
    """What the call will run on with no `model`, as far as the hook can tell."""
    d = _definition_model(agent_type, cwd)
    if d:
        return "%s, from the %s agent's definition" % (d, agent_type)
    if agent_type in INHERITS_SESSION:
        return "the session's model, which %s inherits" % agent_type
    env = (os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL") or "").strip()
    return "%s, from CLAUDE_CODE_SUBAGENT_MODEL" % env if env else "the session's model"


def nudge(data):
    """The line to add for this hook input, or ''."""
    if not isinstance(data, dict) or data.get("tool_name") not in SPAWN_TOOLS:
        return ""
    ti = data.get("tool_input")
    if not isinstance(ti, dict):
        return ""
    model = ti.get("model")
    if isinstance(model, str) and model.strip():
        return ""
    at = ti.get("subagent_type") if isinstance(ti.get("subagent_type"), str) else ""
    msg = ("Agent call without `model`: it runs on the default (%s). Name `model` on every Agent call and never let the "
           "default pick it (managed block, Model routing): `sonnet` for a spec'd execution arm with a check, `haiku` for "
           "a read-only locating or extracting arm, `opus` for judgment." % default_model(at or "general-purpose", data.get("cwd") or ""))
    eff = data.get("effort")
    level = eff.get("level") if isinstance(eff, dict) else None
    named = ti.get("effort")
    if isinstance(level, str) and level in ABOVE_MEDIUM and not (isinstance(named, str) and named.strip()):
        msg += (" The session runs at %s effort and subagents inherit it, so also consider `effort: \"medium\"` for "
                "spec'd and read-only arms." % level)
    return msg


def main():
    try:
        raw = sys.stdin.read(1_000_000)
        msg = nudge(json.loads(raw)) if raw.strip() else ""
        if msg:
            sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": msg}}))
    except Exception:  # noqa: BLE001 - a nudge must never break a tool call
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
