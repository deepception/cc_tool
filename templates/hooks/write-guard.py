#!/usr/bin/env python3
"""Pre-flight guard for file writes (PreToolUse hook on Edit|Write|MultiEdit|NotebookEdit).

Every path-like field in the tool input (file_path, path, notebook_path) is
checked against the location rules below, not just the first one present: a
decoy field must not stand in for the path the tool really writes.

Blocks (deny):
  - writes to system paths (/etc, /usr, /bin, /boot, /dev, /proc, /sys, …)
  - writes to the user's shell/credential dotfiles (~/.ssh/*, ~/.bashrc,
    ~/.zshrc, ~/.profile, ~/.gitconfig, ~/.aws/*, ~/.npmrc, ~/.config/gh/*)
  - writes inside the repo's .git/ directory (or another worktree's)
  - writes into cc-dash's control directory
    (${XDG_STATE_HOME:-~/.local/state}/cc_tool/control/) or its log, by
    Write, Edit, MultiEdit or NotebookEdit, judged on the real path of the
    exact path (symlinks resolved): the lease, decision, message and stop
    files there are how the dashboard talks to the dash-control hook, and an
    agent must not forge them (the hook also checks the dashboard process and
    cc-dash verifies every file; this is defence in depth).
  - writes into cc-dash's self-heal state
    (${XDG_STATE_HOME:-~/.local/state}/cc_tool/selfheal/), judged the same
    way: its run records, the evidence of a run and the patch copies
    cc-dash shows are cc-dash's alone (cc-dash also pins what a run wrote
    by sha256; Bash stays the documented same-user limit).
  - writes anywhere in the cc_tool checkout this project was set up from (the
    path in cc_tool_root beside this hook): it holds the code cc-dash and the
    hooks run, so an agent must not change it from another project. Unless the
    project IS that checkout or one of its worktrees: developing cc_tool
    itself is outside control mode's protection.
  - writes to cc-dash's project registry (.../cc_tool/projects.json).
  NotebookEdit gets the location checks (these denials, .git/, system paths,
  dotfiles, outside the project), not the content ones.
  - secrets in the proposed content: private-key blocks, AWS/GitHub/Slack/
    Anthropic/OpenAI/Google/Stripe keys, JWTs, and `api_key = "<long literal>"`
    style assignments. Placeholder-looking values (EXAMPLE, your_, xxx, <…>,
    ${…}) pass, as do writes to `.env*` files (that is where a real value
    belongs — and Claude Code's own Read-deny keeps it out of context).
Warns (additionalContext, never blocks):
  - writes outside the project root that are not scratch (/tmp, $TMPDIR, the
    Claude scratchpad, ~/.claude/), e.g. a sibling repository the user asked
    the session to work on. The repository's other git worktrees (`git
    worktree list`) count as the project, wherever they sit on disk, and get
    no note at all. cc_tool asks only before installs, deletions, GitHub
    API writes and merges into a protected branch, and the denials above already cover the system paths and
    dotfiles that matter, so the default is a note, not a prompt. Set "write_outside_repo": "ask" in
    .claude/guard-rules.json to be asked per file again, or "off" for silence.
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
import subprocess
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
# realpath() resolves macOS's /var and /tmp symlinks to /private/…, so both spellings.
_SCRATCH_ALLOW = ("/tmp/", "/var/tmp/", "/var/folders/", "/private/tmp/", "/private/var/tmp/",
                  "/private/var/folders/")

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


def repo_worktrees(root: str) -> list:
    """Real paths of every worktree of the repository `root` belongs to (main
    and linked), or [] when git is missing, slow, or `root` is not a repo.

    Only called for a path that is already outside `root` and not scratch, so
    the subprocess is off the hot path. An empty answer falls through to the
    outside-repo ask, which is the safe direction.
    """
    try:
        out = subprocess.run(["git", "-C", root, "worktree", "list", "--porcelain"],
                             capture_output=True, text=True, timeout=1)
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    return [os.path.realpath(line[len("worktree "):])
            for line in out.stdout.splitlines() if line.startswith("worktree ")]


def _cc_state_dir() -> str:
    """${XDG_STATE_HOME:-~/.local/state}/cc_tool, the rule cc-dash and its hooks use."""
    base = os.environ.get("XDG_STATE_HOME") or ""
    if not os.path.isabs(base):
        base = os.path.join(_home(), ".local", "state")
    return os.path.join(base, "cc_tool")


def check_cc_dash(root: str, path: str) -> None:
    real = os.path.realpath(path)
    sd = _cc_state_dir()
    for spot in {sd, os.path.realpath(sd)}:
        if _under(real, os.path.join(spot, "control")) or real in (os.path.join(spot, "cc-dash.log"),
                                                                    os.path.join(spot, "projects.json")):
            deny(root, path, f"writing '{real}' forges cc-dash's control channel (approvals, messages and stops "
                             "between the dashboard and its hook)",
                 "ask the user to approve or answer in their terminal or in the dashboard")
        if _under(real, os.path.join(spot, "selfheal")):
            deny(root, path, f"writing '{real}' forges cc-dash's self-heal state (run records, evidence and the "
                             "patches the dashboard shows)",
                 "self-heal runs are started from the dashboard or with cc-selfheal, and only cc-dash writes there")


def _pinned_root() -> str:
    """The real path in cc_tool_root beside this hook (written by cc-setup), or ''."""
    import stat
    p = os.path.join(os.path.dirname(os.path.realpath(__file__)), "cc_tool_root")
    try:
        fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return ""
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o022 or st.st_size > 4096:
            return ""
        raw = os.read(fd, 4096).decode("utf-8", "replace").strip()
    except OSError:
        return ""
    finally:
        os.close(fd)
    return os.path.realpath(raw) if os.path.isabs(raw) and "\n" not in raw else ""


def check_checkout(root: str, path: str) -> None:
    pin = _pinned_root()
    if not pin or os.path.realpath(root) == pin:
        return              # no pin, or this project is the cc_tool checkout itself
    real = os.path.realpath(path)
    if not _under(real, pin):
        return
    if any(os.path.realpath(wt) == pin for wt in repo_worktrees(root)):
        return              # a worktree of the cc_tool repository: developing cc_tool itself
    deny(root, path, f"writing '{real}' changes the cc_tool checkout whose code cc-dash and this "
                     "project's hooks run",
         "make cc_tool changes in a session opened in the cc_tool repository itself")


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
    if any((real + "/").startswith(h) for h in _SCRATCH_ALLOW) or (tmpdir and _under(real, os.path.realpath(tmpdir))):
        return
    mode = settings.get("write_outside_repo", "warn")
    if mode == "off":
        return
    for wt in repo_worktrees(root):
        if _under(real, wt):
            if _under(real, os.path.join(wt, ".git")):
                deny(root, path, f"writing inside .git/ of the worktree at '{wt}'",
                     "use git commands for repository state; hooks belong in the project's hook manager config")
            return  # another worktree of this repository is still the project
    reason = (f"'{real}' is outside the project root ({root}); an agent working on this project "
              "normally has no business writing there")
    if mode == "ask":
        ask(root, path, reason, 'set "write_outside_repo": "warn" in .claude/guard-rules.json if you work '
                              "across several directories")
    else:
        warn(reason + ". Confirm this is intended.")


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
                # Never the matched text: this reason is shown to the model and written to the
                # activity log. The label, the line number and the first 4 characters only.
                lineno = text.count("\n", 0, m.start()) + 1
                deny(root, path, f"the proposed content contains what looks like a real secret ({label}) "
                                 f"on line {lineno} of the new text, starting {m.group(0)[:4]!r}\u2026",
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
    # Every path-like field present, not the first: a decoy file_path must not hide a notebook_path.
    paths = [ti[k] for k in ("file_path", "path", "notebook_path") if isinstance(ti.get(k), str) and ti[k]]
    if not paths:
        sys.exit(0)
    path = paths[0]
    root = lib.repo_root() if lib else (os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    settings: dict = {}
    if lib:
        _r, settings, _e = lib.load_rules(root)
    contents = [ti.get("content"), ti.get("new_string")]
    for e in ti.get("edits") or []:
        if isinstance(e, dict):
            contents.append(e.get("new_string"))
    contents = [c for c in contents if isinstance(c, str)]

    for each in paths:
        check_cc_dash(root, each)
        check_checkout(root, each)
        check_location(root, each, settings)
    if TOOL != "NotebookEdit":             # notebooks: the location checks only
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
