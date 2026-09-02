#!/usr/bin/env python3
"""Shared helpers for the cc_tool hooks (not a hook itself).

Imported by bash-guard.py, write-guard.py, activity-log.py and
post-edit-typecheck.py, which all live in the same .claude/hooks/ directory, so
`import cc_hooklib` resolves without any path setup. Everything here is
best-effort: a failure inside a helper must never take a hook down, so every
disk operation swallows its own errors.

What it provides
  repo_root()            the project root (CLAUDE_PROJECT_DIR, else the nearest
                         ancestor with a .git, else cwd)
  state_dir(root)        <root>/.git/cc_tool/ — gitignored by construction, dies
                         with the clone; a temp dir when .git is not a directory
  log_event(...)         append one JSON line to the activity log
  load_session/save_session
                         per-session trajectory state (files seen with their
                         mtime, files whose post-edit check was red)
  load_rules(root)       project rules from .claude/guard-rules.json
  match_rules(...)       apply those rules to a tool call's fields
  block_message(...)     the one BLOCKED: … Suggestion: … format every guard uses
"""
import hashlib
import json
import os
import re
import tempfile
import time

ACTIVITY_LOG = "activity.jsonl"
ACTIVITY_MAX_BYTES = 5_000_000  # rotate to .1 past this; the log is a debugging aid, not a ledger
SESSION_TTL = 7 * 24 * 3600     # stale session files older than this are pruned opportunistically


def repo_root(start: str = "") -> str:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    d = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return os.path.abspath(start or os.getcwd())
        d = parent


def state_dir(root: str) -> str:
    """Directory for hook state. Under .git/ when that is a real directory
    (never committed, never in the worktree); otherwise a per-root temp dir
    (git worktrees have a .git *file*)."""
    git = os.path.join(root, ".git")
    if os.path.isdir(git):
        d = os.path.join(git, "cc_tool")
    else:
        tag = hashlib.sha1(root.encode("utf-8", "replace")).hexdigest()[:12]
        d = os.path.join(tempfile.gettempdir(), "cc_tool", tag)
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


# ── Block message format ──────────────────────────────────────────────────────
def block_message(reason: str, suggestion: str = "") -> str:
    """`BLOCKED: <reason> Suggestion: <safe alternative>` — one shape for every
    guard, so the managed CLAUDE.md block can teach the model to read it."""
    reason = reason.strip()
    if not reason.endswith((".", "!", "?")):
        reason += "."
    msg = f"BLOCKED: {reason}"
    if suggestion:
        suggestion = suggestion.strip()
        if not suggestion.endswith((".", "!", "?")):
            suggestion += "."
        msg += f" Suggestion: {suggestion}"
    return msg


# ── Activity log ──────────────────────────────────────────────────────────────
def log_event(root: str, kind: str, **fields) -> None:
    """Append one line: {"ts", "kind", ...fields}. Never raises."""
    try:
        path = os.path.join(state_dir(root), ACTIVITY_LOG)
        try:
            if os.path.getsize(path) > ACTIVITY_MAX_BYTES:
                os.replace(path, path + ".1")
        except OSError:
            pass
        rec = {"ts": round(time.time(), 3), "kind": kind}
        rec.update(fields)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except Exception:  # noqa: BLE001 - logging must never affect the decision
        pass


# ── Session trajectory state ──────────────────────────────────────────────────
def _session_path(root: str, session_id: str) -> str:
    sid = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "unknown")[:80]
    return os.path.join(state_dir(root), f"session-{sid}.json")


def load_session(root: str, session_id: str) -> dict:
    """{"seen": {path: mtime}, "failed": {path: ts}, "notes": {key: ts}}."""
    base = {"seen": {}, "failed": {}, "notes": {}}
    try:
        with open(_session_path(root, session_id), encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            for k in base:
                if isinstance(data.get(k), dict):
                    base[k] = data[k]
    except Exception:  # noqa: BLE001
        pass
    return base


def save_session(root: str, session_id: str, data: dict) -> None:
    try:
        path = _session_path(root, session_id)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
        _prune_sessions(os.path.dirname(path))
    except Exception:  # noqa: BLE001
        pass


def _prune_sessions(d: str) -> None:
    """Drop session files untouched for SESSION_TTL. Cheap; runs on every save."""
    try:
        now = time.time()
        for name in os.listdir(d):
            if name.startswith("session-") and name.endswith(".json"):
                p = os.path.join(d, name)
                if now - os.path.getmtime(p) > SESSION_TTL:
                    os.remove(p)
    except OSError:
        pass


def file_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


# ── Project rules (.claude/guard-rules.json) ──────────────────────────────────
# Schema (all keys except id/regex/reason optional):
#   {"rules": [
#     {"id": "no-yarn", "tool": "Bash", "field": "command",
#      "regex": "\\byarn\\b", "flags": "i", "action": "deny|ask|warn",
#      "reason": "This repo uses pnpm.", "suggestion": "pnpm install …",
#      "negate": ["\\byarn\\.lock\\b"], "enabled": true}
#   ],
#    "write_outside_repo": "ask|warn|off"}
#
# tool: "Bash", "Write", "Edit", "MultiEdit", or "*" (or a list of those).
# field: "command" (Bash), "file_path" / "content" (write tools). Default is
#        "command" for Bash rules and "file_path" for write-tool rules.
# negate: any of these regexes matching suppresses the rule (exceptions).
_RULES_CACHE: dict = {}
_VALID_ACTIONS = {"deny", "ask", "warn"}


def rules_path(root: str) -> str:
    return os.path.join(root, ".claude", "guard-rules.json")


def load_rules(root: str) -> tuple:
    """(rules: list[dict], settings: dict, error: str). A malformed file yields
    ([], {}, <why>) so the caller can surface it instead of silently ignoring
    the project's policy."""
    path = rules_path(root)
    if path in _RULES_CACHE:
        return _RULES_CACHE[path]
    result: tuple = ([], {}, "")
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                raise ValueError("top level is not an object")
            rules = []
            for i, r in enumerate(data.get("rules") or []):
                err = _validate_rule(r, i)
                if err:
                    raise ValueError(err)
                if r.get("enabled", True):
                    rules.append(r)
            settings = {k: v for k, v in data.items() if k != "rules"}
            result = (rules, settings, "")
        except Exception as exc:  # noqa: BLE001
            result = ([], {}, f"{type(exc).__name__}: {exc}")
    _RULES_CACHE[path] = result
    return result


def _validate_rule(r, i: int) -> str:
    if not isinstance(r, dict):
        return f"rule #{i} is not an object"
    rid = r.get("id") or f"#{i}"
    if not isinstance(r.get("regex"), str) or not r["regex"]:
        return f"rule {rid}: missing regex"
    if r.get("action", "warn") not in _VALID_ACTIONS:
        return f"rule {rid}: action must be deny|ask|warn"
    if not isinstance(r.get("reason"), str) or not r["reason"]:
        return f"rule {rid}: missing reason"
    try:
        rx = re.compile(r["regex"], _flags(r.get("flags", "")))
        if rx.search(""):
            return f"rule {rid}: regex matches the empty string"
        for n in r.get("negate") or []:
            re.compile(n, _flags(r.get("flags", "")))
    except re.error as exc:
        return f"rule {rid}: bad regex ({exc})"
    return ""


def _flags(s: str) -> int:
    f = 0
    for ch in s or "":
        f |= {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL, "x": re.VERBOSE}.get(ch, 0)
    return f


def _tool_matches(rule: dict, tool: str) -> bool:
    t = rule.get("tool", "Bash")
    tools = t if isinstance(t, list) else [t]
    return "*" in tools or tool in tools


def match_rules(rules: list, tool: str, fields: dict) -> list:
    """[(rule, matched_text)] for every enabled rule that fires on this call."""
    hits = []
    for r in rules:
        if not _tool_matches(r, tool):
            continue
        default_field = "command" if tool == "Bash" else "file_path"
        value = fields.get(r.get("field", default_field))
        if not isinstance(value, str) or not value:
            continue
        flags = _flags(r.get("flags", ""))
        m = re.search(r["regex"], value, flags)
        if not m:
            continue
        if any(re.search(n, value, flags) for n in r.get("negate") or []):
            continue
        hits.append((r, m.group(0)))
    return hits
