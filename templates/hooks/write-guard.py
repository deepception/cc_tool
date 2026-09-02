#!/usr/bin/env python3
"""Pre-flight guard for file writes (PreToolUse hook on Edit|Write|MultiEdit).

Blocks (deny):
  - writes to system paths (/etc, /usr, /bin, /boot, /dev, /proc, /sys, …)
  - writes to the user's shell/credential dotfiles (~/.ssh/*, ~/.bashrc,
    ~/.zshrc, ~/.profile, ~/.gitconfig, ~/.aws/*, ~/.npmrc, ~/.config/gh/*)
  - writes inside the repo's .git/ directory
  - secrets in the proposed content: private-key blocks, AWS/GitHub/Slack/
    Anthropic/OpenAI/Google/Stripe keys, JWTs, and `api_key = "<long literal>"`
    style assignments. Placeholder-looking values (EXAMPLE, your_, xxx, <…>,
    ${…}) pass, as do writes to `.env*` files (that is where a real value
    belongs — and Claude Code's own Read-deny keeps it out of context).
Asks (hands the decision to the user; a refusal in unattended runs):
  - writes outside the project root that are not scratch (/tmp, $TMPDIR, the
    Claude scratchpad, ~/.claude/). Tune with "write_outside_repo":
    "ask"|"warn"|"off" in .claude/guard-rules.json — set "warn" if you work
    with additional working directories.
Warns (additionalContext, never blocks):
  - stale read: the target file changed on disk after Claude last read or
    wrote it this session (a formatter, a shell write, another agent). The
    edit is probably built on an old picture of the file — re-read first.
  - red check pending: the post-edit typecheck reported errors after an edit to
    a DIFFERENT file and no edit has touched it since. Fix that first, so
    errors do not pile up across files (interlinked calls this the "bash-edit
    obligation"; here it is a nudge, not a gate).
  - project rules from .claude/guard-rules.json with tool Write/Edit/MultiEdit
    (field "file_path" or "content"), at their own deny/ask/warn level.

Same philosophy as bash-guard.py: static, best-effort, fails OPEN on anything
it cannot parse (a malformed payload never blocks an edit — Claude Code's own
tool already validates it), and never crashes the session. Session state and
the activity log live in .git/cc_tool/ via cc_hooklib.py.
"""
import json
import os
import re
import sys

try:
    import cc_hooklib as lib
except Exception:  # noqa: BLE001
    lib = None

_SYSTEM_PREFIXES = ("/bin", "/boot", "/dev", "/etc", "/lib", "/lib32", "/lib64", "/opt",
                    "/proc", "/root", "/sbin", "/sys", "/usr", "/var/lib", "/var/log")
_HOME_DOTFILES = {".bashrc", ".zshrc", ".profile", ".bash_profile", ".zprofile", ".zshenv",
                  ".gitconfig", ".npmrc", ".pypirc", ".netrc", ".git-credentials"}
_HOME_DOTDIRS = (".ssh", ".aws", ".gnupg", ".config/gh", ".kube", ".docker")
_SCRATCH_ALLOW = ("/tmp/", "/var/tmp/", "/var/folders/", "/private/tmp/")

_SECRET_PATTERNS = [
    ("private key block", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY(?: BLOCK)?-----")),
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("OpenAI API key", re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Stripe live key", re.compile(r"\b[sr]k_live_[0-9a-zA-Z]{24,}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("credential assignment", re.compile(
        r"(?i)\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|"
        r"private[_-]?key|password|passwd)\b\s*[:=]\s*['\"]([A-Za-z0-9_\-/+=.]{20,})['\"]")),
]
_PLACEHOLDER_RE = re.compile(
    r"(?i)example|placeholder|your[_-]?|xxx|changeme|dummy|redacted|fake|sample|test[_-]?key|"
    r"<[^>]*>|\$\{|\{\{|\bTODO\b|0000000000|1234567890")


def _out(decision: str, reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }}))
    sys.exit(0)


def _msg(reason: str, suggestion: str = "") -> str:
    if lib:
        return lib.block_message(reason, suggestion)
    return f"BLOCKED: {reason}" + (f" Suggestion: {suggestion}" if suggestion else "")


def _log(root: str, kind: str, path: str, message: str) -> None:
    if lib:
        lib.log_event(root, kind, tool=TOOL, hook="write-guard", path=path, message=message[:300])


def deny(root: str, path: str, reason: str, suggestion: str = "") -> None:
    m = _msg(reason, suggestion)
    _log(root, "deny", path, m)
    _out("deny", m)


def ask(root: str, path: str, reason: str, suggestion: str = "") -> None:
    m = _msg(reason, suggestion).replace("BLOCKED:", "NEEDS APPROVAL:", 1)
    _log(root, "ask", path, m)
    _out("ask", m)


WARNINGS: list = []
TOOL = "Write"


def warn(note: str) -> None:
    WARNINGS.append(note)


def _home() -> str:
    return os.path.expanduser("~")


def _under(path: str, prefix: str) -> bool:
    prefix = prefix.rstrip("/")
    return path == prefix or path.startswith(prefix + "/")


def check_location(root: str, path: str, settings: dict) -> None:
    real = os.path.realpath(path)
    home = _home()
    if any(_under(real, p) for p in _SYSTEM_PREFIXES):
        deny(root, path, f"writing to system path '{real}'", "the project never needs files there changed")
    if home and _under(real, home):
        rel = os.path.relpath(real, home)
        if rel in _HOME_DOTFILES or any(_under(rel, d) for d in _HOME_DOTDIRS):
            deny(root, path, f"writing to '{real}' changes the user's shell, git, or credential setup",
                 "show the user the snippet and let them add it")
    git_dir = os.path.join(root, ".git")
    if _under(real, git_dir) and not _under(real, os.path.join(git_dir, "cc_tool")):
        deny(root, path, f"writing inside .git/ ('{os.path.relpath(real, root)}')",
             "use git commands for repository state; hooks belong in the project's hook manager config")
    if _under(real, root):
        return
    if home and _under(real, os.path.join(home, ".claude")):
        return  # Claude's own memory / settings
    tmpdir = os.environ.get("TMPDIR", "")
    if any(h in real + "/" for h in _SCRATCH_ALLOW) or (tmpdir and _under(real, os.path.realpath(tmpdir))):
        return
    mode = settings.get("write_outside_repo", "ask")
    if mode == "off":
        return
    reason = (f"'{real}' is outside the project root ({root}); an agent working on this project "
              "normally has no business writing there")
    if mode == "warn":
        warn(reason + ". Confirm this is intended.")
    else:
        ask(root, path, reason, 'set "write_outside_repo": "warn" in .claude/guard-rules.json if you work '
                              "across several directories")


def _placeholder(match_text: str, line: str) -> bool:
    return bool(_PLACEHOLDER_RE.search(match_text) or _PLACEHOLDER_RE.search(line))


def check_secrets(root: str, path: str, contents: list) -> None:
    base = os.path.basename(path)
    if base.startswith(".env") or any(tag in base for tag in (".example", ".sample", ".template")):
        return
    for text in contents:
        if not text:
            continue
        for label, rx in _SECRET_PATTERNS:
            for m in rx.finditer(text):
                start = text.rfind("\n", 0, m.start()) + 1
                end = text.find("\n", m.end())
                line = text[start:end if end != -1 else len(text)]
                if _placeholder(m.group(0), line):
                    continue
                deny(root, path, f"the proposed content contains what looks like a real secret ({label}) — "
                                 f"line: {line.strip()[:80]!r}",
                     "read it from an environment variable or a gitignored .env file; if this is a "
                     "fixture, make it obviously fake (EXAMPLE/xxx)")


def check_trajectory(root: str, session_id: str, path: str) -> None:
    if not lib:
        return
    state = lib.load_session(root, session_id)
    real = os.path.realpath(path)
    seen = state["seen"].get(real)
    now_mtime = lib.file_mtime(real)
    if seen and now_mtime and now_mtime > float(seen) + 0.5:
        warn(f"stale read: {os.path.relpath(real, root) if _under(real, root) else real} changed on disk "
             "after you last read or wrote it this session (formatter, shell write, or another agent). "
             "Re-read it before editing so the change is built on the current contents.")
    for fpath, _ts in state["failed"].items():
        if fpath != real:
            rel = os.path.relpath(fpath, root) if _under(fpath, root) else fpath
            warn(f"red check pending: the post-edit check reported errors after your last edit to {rel} "
                 "and nothing has touched it since. Fix that before editing other files.")
            break


def check_project_rules(root: str, path: str, contents: list) -> None:
    if not lib:
        return
    rules, settings, err = lib.load_rules(root)
    if err:
        warn(f".claude/guard-rules.json could not be loaded ({err}); project rules are NOT enforced.")
        return
    fields = {"file_path": path, "content": "\n".join(c for c in contents if c)}
    for rule, matched in lib.match_rules(rules, TOOL, fields):
        reason = f"[{rule.get('id', '?')}] {rule['reason']} (matched '{matched[:60]}')"
        action = rule.get("action", "warn")
        if action == "deny":
            deny(root, path, reason, rule.get("suggestion", ""))
        elif action == "ask":
            ask(root, path, reason, rule.get("suggestion", ""))
        else:
            warn(reason + (f" Suggestion: {rule['suggestion']}" if rule.get("suggestion") else ""))


def main() -> None:
    global TOOL
    try:
        data = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        sys.exit(0)  # not our payload shape — fail open, the tool validates its own input
    if not isinstance(data, dict):
        sys.exit(0)
    TOOL = data.get("tool_name") or "Write"
    ti = data.get("tool_input") or {}
    if not isinstance(ti, dict):
        sys.exit(0)
    path = ti.get("file_path") or ti.get("path") or ""
    if not isinstance(path, str) or not path:
        sys.exit(0)
    root = lib.repo_root() if lib else (os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    settings: dict = {}
    if lib:
        _r, settings, _e = lib.load_rules(root)
    contents = [ti.get("content"), ti.get("new_string")]
    for e in ti.get("edits") or []:
        if isinstance(e, dict):
            contents.append(e.get("new_string"))
    contents = [c for c in contents if isinstance(c, str)]

    check_location(root, path, settings)
    check_secrets(root, path, contents)
    check_project_rules(root, path, contents)
    check_trajectory(root, data.get("session_id") or "", path)
    if WARNINGS:
        _log(root, "warn", path, " | ".join(WARNINGS))
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": "\n".join(f"[write-guard] {w}" for w in WARNINGS),
        }}))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 - a guard on edits must never crash the session
        sys.exit(0)
