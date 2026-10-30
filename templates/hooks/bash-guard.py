#!/usr/bin/env python3
"""Pre-flight guards for Bash commands (PreToolUse hook).

Blocks:
  - git commit on protected branches (main/master/production/release), unless --amend
  - git push to protected branches, including `git push origin HEAD` / `origin @`
  - pre-commit/pre-push hook bypasses: `--no-verify`, `git commit -n` (and short
    clusters like `-an`), and `git -c core.hooksPath=...`
  - reads of known secret files via readers Claude Code's Read-deny can't see
    (grep/awk/sort/cut/source/xargs pipelines/`< redirection`/inline `python -c`
    / `node -e`), e.g. `grep KEY .env`. Conservative by construction: it fires
    only on a clear secret-path reference, so `grep -r TODO src/` passes through.
  - destructive commands with no interactive form worth a prompt: `rm -rf` on a
    root / home / the repo / `.git`, `sudo rm`, deleting lockfiles, `.gitignore`,
    CI, Dockerfiles or migrations, `pkill <name>` / `killall` (every process by
    that name, including the harness), `git push --force` (suggests
    `--force-with-lease`), `curl … | sh`, `env | curl`, registry redirection
    (`npm config set registry`, writes to `.npmrc`/`pip.conf`), `mkfs`/`dd
    of=/dev/…`, `chmod 777`, shutdown.
  - starting cc-dash's control mode (`cc-dash --control` / `ccdash --control`,
    however wrapped; heredoc bodies and inline code are scanned too), so an
    agent cannot run its own approval dashboard. Defence in depth only: the
    control code, the control hook's checks on the dashboard process and
    cc-dash's verification of every decision (lib/cc_dash/auth.py, procs.py,
    control.py) are the real protection. The control directory, log and
    endpoints are not matched in command text (that can never be complete).

Pure entry point: classify(command, cwd) -> ("deny" | "ask" | "allow", reason)
runs the same checks without printing, exiting or logging; cc-dash's control
hook uses it to learn whether a permission prompt is one this guard asked
about.

Asks (hands the decision to the user; in an unattended run that is a refusal).
The rule is: ask before an install, a deletion, a GitHub API write or landing
work on a protected branch, nowhere else; plus the ways an allowlisted command
(`gh api`, `git diff/log/fetch …`) sends data elsewhere, runs a command or
writes a file, and a command the guard could not finish checking.
  - landing on a protected branch (the merge gate), by allowlist: with HEAD
    on main/master/production/release (or moved to one, or to an unknown
    branch such as `-` / @{-1}, earlier in the command), a git command runs
    without asking only if it cannot move the branch (reads, add/mv/stash,
    fetch without a refspec, branch listing or a new branch, checkout/switch
    without -B/-C, config reads, the --abort of a merge or rebase, `git pull
    --ff-only` from its own verified upstream); anything else asks, an alias
    or unknown subcommand, `commit --amend`, `-c` and GIT_DIR / GIT_CONFIG_*
    included. On any branch, a git command that is not a read and names a
    protected branch (a word, refs/heads/…, origin/…, a refspec side, a
    --track value, an attached option value) or a branch it cannot read
    (`-`, @{-1}, an expansion) asks, as do update-ref --stdin, symbolic-ref
    writes, remote add/set-branches/set-url/rename, and config writes to
    alias, remote, branch, url, pull, rebase, include* or core.hooksPath.
    Closed as classes, on any branch: refs from stdin (any `--stdin`, or a
    prefix of it, on a command that is not a read) or from plumbing
    (fast-import, receive-pack …), `fetch --update-head-ok`, `rebase
    --update-refs`, an option git would read as an abbreviation (an unknown
    long option of branch / checkout / switch / worktree / update-ref /
    symbolic-ref), `<remote>/<protected>` after any option, and an interactive
    rebase or one with --exec, an editor set for one command (-c
    sequence.editor / core.editor, GIT_SEQUENCE_EDITOR, GIT_EDITOR, EDITOR,
    VISUAL), whose todo list or editor can run any git command. On a protected
    branch, a git command asks whenever the command line has an assignment,
    any builtin that can assign a variable or change directory (export, read,
    printf, let, mapfile, getopts, cd, pushd, source …, wherever it stands),
    or the name HOME, XDG_CONFIG_HOME, PATH, GIT_*, EDITOR or VISUAL. Any git
    command shlex cannot parse asks.
    This is a text guard, with a documented residual class: a `cd` into
    another repository before the git command; `.git/config` or
    `~/.gitconfig` edited by hand through the shell (an alias or upstream
    git then reads without a git command to see); and git run by a script or
    `sh -c`. A git reference-transaction hook would close it. See
    _check_protected_landing.
  - deletions: any `rm`/`rmdir`/`unlink`/`shred` word or `-delete` /
    `--delete…` flag anywhere in the command (wrappers, `sh -c`, git aliases
    and xargs included), unless the whole command is `rm [-flags]
    /absolute/path …` with every path written literally and really located
    strictly below a scratch root (not /tmp itself nor a shared level such as
    /tmp/claude-<uid>); `git rm`, `git checkout -- <paths>`, `git restore
    <paths>`, `git worktree remove --force`, `git reset --hard`, `git clean
    -f`, `git checkout .`, `git branch -D`, `git stash drop|clear`, history
    rewrites; docker prune / `rm -f` / `down -v`; `kubectl delete --all` /
    drain; helm uninstall; terraform/pulumi destroy or auto-approve; cloud CLI
    deletes; DROP / TRUNCATE / DELETE-without-WHERE when a DB client is in the
    command; redis FLUSHALL; `crontab -e`.
  - installs: `npx <pkg>` when <pkg> is not on disk, because that downloads
    and executes a package nobody reviewed. Installed dev dependencies (`npx
    vitest`, `npx tsc`) pass, as does an exact pinned version already in npx's
    cache (`npx agent-device@0.21.20`) and `npx --no-install …`. An unpinned
    or tagged spec that is not installed (`npx pkg`, `pkg@latest`) asks.
  - GitHub API writes: `gh api` (allowlisted in settings.json for reads) with
    -X/--method other than GET, -f/-F fields without `-X GET` (gh POSTs
    them), --input, a -F value read from a file (`@…`), a method-override
    header, an option it does not know, a GraphQL query that is not written
    out, has a `$` or backtick or contains `mutation`, or anything bash
    could turn into other arguments: any `$`/backtick in a word (no variable
    is resolved), an unquoted glob or brace expansion, process substitution,
    `$'…'`, IFS; and a command name bash builds (`$G api`, `/usr/bin/g? api`).
    Also a request to another host, a way out for data under the same allow
    rule: --hostname, an endpoint with `://` (a full URL) other than
    https://api.github.com/…, and any mention of GH_HOST, GH_CONFIG_DIR,
    XDG_CONFIG_HOME or HOME other than a plain $HOME / ${HOME} read, or of a
    builtin or loop that sets a variable by a name bash may build (declare,
    typeset, local, export, readonly, read, mapfile, readarray, unset, eval,
    source / `.`, let, getopts, for, select, printf -v).
    Reads pass: `gh api repos/o/r`, `--paginate`, `--jq`, `-X GET … -f q=…`
    (a query string), `gh api graphql -f query='{ viewer { login } }'`.
    `gh api *` is allowlisted, so unlike the rest of this file this check
    fails closed on shell expansion (see check_gh_api, _gh_mark).
  - git options that turn an allowlisted read into running a command or
    writing a file, on any branch: `--exec`, `--upload-pack`,
    `--receive-pack` (and git's abbreviations of them, `--upload-p`), `clone
    -u`, `clone -c/--config`, and `--output` (`git diff --output=~/.zshrc`);
    anywhere in the arguments, past a `--` too, since parse-options takes a
    `--` as an option's value.
  - a command bash-guard cannot finish checking within HOOK_BUDGET_SECONDS
    (2.4 s of its registered 3 s timeout): a hook that times out lets the
    command run unchecked.
  - Deletions follow nothing: no variables, no `cd`, no caches (see
    check_deletes). npx follows variables, `cd` and subshells (see
    walk_commands), since a wrong guess there costs a download, not files; a
    directory or variable it cannot resolve counts as not installed. gh api
    follows what npx does, plus xargs/find -exec, `sh -c`, eval, trap and
    text fed to a shell; any word that is not literal asks.

Warns (additionalContext, never blocks):
  - shell writes into source files (`> x.ts`, `tee`, `sed -i`, `perl -pi`,
    `patch`, `git apply`), which dodge the post-edit typecheck and write guard.
  - a `.claude/guard-rules.json` that cannot be parsed (its rules are then not
    enforced, and the model is told so).

Project rules: `.claude/guard-rules.json` (schema in cc_hooklib.py) adds
per-project deny/ask/warn regexes on the command string — the `distill-rules`
skill writes it from CLAUDE.md imperatives. Built-in rules stay in code because
they need the tokenizer; the file is for "this repo uses pnpm, not yarn".

Every refusal has one shape: `BLOCKED: <what and why>. Suggestion: <safe
form>.` The managed CLAUDE.md block teaches the model to take the suggestion
instead of rephrasing the command around the guard. Decisions are appended to
`.git/cc_tool/activity.jsonl` (see cc_hooklib.py).

WHAT THIS IS NOT
----------------
A *static token inspector*, not a shell. It reads the command string with shlex
and reasons about tokens; it never expands anything. Anything that only becomes a
protected branch or a secret path *after* shell expansion is invisible to it, and
always will be — this is inherent to the approach, not a TODO:

    F=.env; grep KEY $F              grep KEY .en*  (glob / brace expansion)
    sh -c '...' / bash -c '...'      eval, heredocs fed to non-shell interpreters, base64 | sh
    git${IFS}commit                  computed paths: open(chr(46)+'env')

(Two checks fail closed instead, because an allow rule or the merge gate makes
them the only barrier: a git command that takes a branch asks when one of its
arguments is built by the shell (`git push origin $B`, `"$(echo master)"`), and
`gh api` asks on any expansion; see _check_protected_landing and check_gh_api.)

Every one of those requires the caller to be *actively evading* the guard. This
hook's job is the other threat model: stop the ordinary form of a dangerous
action — the command an agent writes when it simply is not thinking about which
branch it is on. It is a guardrail and defence-in-depth behind permissions.deny,
NOT containment for a hostile process. Do not describe it as a sandbox.

Also deliberately out of scope: cat/head/tail/sed/less on a secret path. Claude
Code's own Read-deny models those readers' file arguments; bash-guard only fills
the gap for readers it does not model. (If that assumption is ever falsified,
those four readers become unguarded and belong in _SECRET_READERS.)

Failure mode: fail-CLOSED on a payload it cannot understand (unparseable JSON,
missing/typed-wrong command, unexpected internal error) — it denies with an
explanation rather than letting the command through. It fails OPEN only where the
*shell string itself* is unparseable (unbalanced quotes); there a regex fallback
still covers git commit/push. A payload it simply has no opinion on (no
`command` key, empty command) exits 0 silently.
"""
import bisect
import fnmatch
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time

try:
    import cc_hooklib as lib
except Exception:  # noqa: BLE001 - the guard must work even if the helper is missing
    lib = None

PROTECTED = {"main", "master", "production", "release"}
# Longest command we tokenize. Beyond this we cannot analyse safely, so we warn
# loudly and defer to the user rather than silently allowing (see main()).
MAX_COMMAND_CHARS = 400_000
# Wall-clock budget for one run of the hook. settings.json registers it with a
# 3 s timeout, and a hook that times out lets the command run unchecked (under
# the allow rules: `gh api *`, `git rebase *` …), so past this budget the hook
# asks instead (see _arm_budget, main()).
HOOK_BUDGET_SECONDS = 2.4

_CMD_FOR_LOG = ""      # set by main() so deny/ask can log what they refused
_PAYLOAD_CWD = ""      # the Bash tool's working directory, from the hook payload
_CAPTURE = False       # classify(): deny()/ask() raise _Verdict instead of printing and exiting
_WARNINGS: list = []   # non-blocking notes, emitted together at the end


def _fmt(reason: str, suggestion: str) -> str:
    if lib:
        return lib.block_message(reason, suggestion)
    return f"BLOCKED: {reason}" + (f" Suggestion: {suggestion}" if suggestion else "")


def _log(kind: str, message: str) -> None:
    if lib:
        try:
            lib.log_event(lib.repo_root(), kind, tool="Bash", hook="bash-guard",
                          command=_CMD_FOR_LOG[:500], message=message[:300])
        except Exception:  # noqa: BLE001
            pass


class _Verdict(Exception):
    """classify()'s capture mode: the decision deny()/ask() would have printed."""

    def __init__(self, decision: str, reason: str):
        super().__init__(reason)
        self.decision, self.reason = decision, reason


def deny(reason: str, suggestion: str = "") -> None:
    """Refuse the command. The message follows one shape for every cc_tool guard:
    `BLOCKED: <what and why>. Suggestion: <the safe form>.` The managed CLAUDE.md
    block teaches the model to take the suggestion rather than rephrase around
    the guard."""
    msg = _fmt(reason, suggestion)
    if _CAPTURE:
        raise _Verdict("deny", msg)
    _disarm_budget()
    _log("deny", msg)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": msg,
        }
    }))
    sys.exit(0)


def ask(reason: str, suggestion: str = "") -> None:
    """Hand the decision to the user (destructive-but-sometimes-legitimate ops,
    or a command the guard cannot analyse)."""
    msg = _fmt(reason, suggestion).replace("BLOCKED:", "NEEDS APPROVAL:", 1)
    if _CAPTURE:
        raise _Verdict("ask", msg)
    _disarm_budget()
    _log("ask", msg)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": msg,
        }
    }))
    sys.exit(0)


def _out_of_budget(_signum, _frame) -> None:
    ask(f"bash-guard ran out of time ({HOOK_BUDGET_SECONDS} s) before it finished checking this command, "
        "and a hook that times out lets the command run unchecked. Approve only if you have read it",
        "split it into shorter commands")


def _arm_budget() -> None:
    """Ask, rather than time out, once HOOK_BUDGET_SECONDS have passed. Only the
    hook itself arms it (main()); classify() runs inside other hooks and never does."""
    if hasattr(signal, "setitimer"):
        signal.signal(signal.SIGALRM, _out_of_budget)
        signal.setitimer(signal.ITIMER_REAL, HOOK_BUDGET_SECONDS)


def _disarm_budget() -> None:
    """Called before a decision is printed, so the timer cannot print a second one."""
    if hasattr(signal, "setitimer"):
        signal.setitimer(signal.ITIMER_REAL, 0)


def warn(note: str) -> None:
    """Queue a non-blocking note; main() emits them as additionalContext."""
    _WARNINGS.append(note)


def flush_warnings() -> None:
    if not _WARNINGS or _CAPTURE:
        return
    _log("warn", " | ".join(_WARNINGS))
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": "\n".join(f"[bash-guard] {w}" for w in _WARNINGS),
        }
    }))


# ── Branch lookup ─────────────────────────────────────────────────────────────
# Memoised: `git commit -m x && git push` costs ONE subprocess, not two, which
# keeps the hook inside its 3 s registered timeout even when git is slow.
_BRANCH_CACHE: dict = {}
_BRANCH_LOOKUPS = 0
_MAX_BRANCH_LOOKUPS = 2  # 2 × 1 s worst case, under the registered 3 s timeout


def current_branch(repo_args=()) -> str:
    """Current branch of the repo an invocation targets ('' if undeterminable).

    `repo_args` carries that invocation's own repo-selecting global options
    (`-C <dir>`, `--git-dir=…`, `--work-tree=…`). This matters: `git -C other
    commit` commits in *other*, so checking the cwd's branch would be wrong in
    both directions — it would miss a commit to other's master and would block a
    commit to other's feature branch while cwd sits on master. We therefore ask
    git about the same repo the command targets. Only path-selecting options are
    forwarded (never `-c`/`--exec-path`), and rev-parse runs no hooks or pager.
    """
    global _BRANCH_LOOKUPS
    key = tuple(repo_args)
    if key in _BRANCH_CACHE:
        return _BRANCH_CACHE[key]
    if _BRANCH_LOOKUPS >= _MAX_BRANCH_LOOKUPS:
        deny(
            "this command targets more than "
            f"{_MAX_BRANCH_LOOKUPS} different git repositories, so bash-guard cannot "
            "check each one's branch inside its time budget. Run the git commands "
            "separately."
        )
    _BRANCH_LOOKUPS += 1
    try:
        out = subprocess.run(
            ["git", *repo_args, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=1,
        )
        branch = out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        branch = ""  # git missing / hung / not a repo — same as today: no opinion
    _BRANCH_CACHE[key] = branch
    return branch


# ── Tokenizing ────────────────────────────────────────────────────────────────
_SHELL_OP_CHARS = set("();<>|&\n")
# Operator tokens that DON'T end a pipeline (a pipeline is the unit for `xargs`).
_PIPE_TOKENS = {"|", "|&"}
_GIT_GLOBAL_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
# Repo-selecting global options only — these are safe to forward to rev-parse.
_GIT_REPO_VALUE_OPTS = {"-C", "--git-dir", "--work-tree"}
_GIT_REPO_INLINE_OPTS = ("--git-dir=", "--work-tree=")
_PUSH_VALUE_OPTS = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}
_GIT_CONFIG_READ_OPTS = {"--get", "--get-all", "--get-regexp", "--get-urlmatch", "--unset",
                         "--unset-all", "-l", "--list"}


_HEREDOC_OP_RE = re.compile(r"<<(-?)[ \t]*(\\?)(['\"]?)([A-Za-z0-9_.-]+)\3")
# A heredoc whose opening line runs a shell (`bash <<EOF`, `cat <<EOF | sh`,
# `ssh host <<EOF`) feeds its body in as commands; any other heredoc is data.
_HEREDOC_EXEC_RE = re.compile(r"(?:^|[\s|;&(])(?:\S*/)?(?:sh|bash|zsh|dash|ksh|fish|ssh)(?=$|[\s|;&)])")
_SUBST_RE = re.compile(r"\$\(([^()]*(?:\([^()]*\)[^()]*)*)\)|`([^`]*)`")
_COMMENT_CONTEXT = set(" \t\n;&|()<>")


def shell_prepass(cmd: str) -> str:
    """Rewrite a command string the way the shell reads it, before tokenizing.

    Quote-aware (with a context stack, so quotes nested inside `$(…)` or
    backticks open a fresh context as they do in bash), so a `#` or `<<` inside
    quotes stays text:
      - `\\<newline>` line continuations are joined (bash removes them), so
        `rm -rf \\<newline> ~` reads as one command, not `rm -rf` then `~`.
      - unquoted `#` comments are dropped up to, not including, the newline;
        shlex's own comment handling swallowed the newline and merged the next
        line into the comment's command.
      - a heredoc body is data unless its opening line runs a shell: data bodies
        are replaced by just the `$(…)`/backtick substitutions an unquoted
        delimiter would still execute; shell-fed bodies stay, as commands.
    """
    out: list = []
    i, n = 0, len(cmd)
    # Contexts: "top", "sub" ($(…)), "par" (plain parens inside a sub), "bq"
    # (backticks), '"', "'", "$'". Only "top"/"sub"/"par"/"bq" see comments.
    stack = ["top"]
    pending: list = []    # heredocs opened on the current top-level line: (delim, expands)
    line_start = 0        # index in `out` where the current line began
    term_index: dict = {}  # built on first heredoc: stripped line -> [(start, end)]

    def last_char() -> str:
        for piece in reversed(out):
            if piece:
                return piece[-1]
        return "\n"

    while i < n:
        c = cmd[i]
        ctx = stack[-1]
        if ctx in ("'", "$'"):
            if ctx == "$'" and c == "\\" and i + 1 < n:
                out.append(cmd[i:i + 2])
                i += 2
                continue
            out.append(c)
            i += 1
            if c == "'":
                stack.pop()
            continue
        if c == "\\" and i + 1 < n:
            nxt = cmd[i + 1]
            if nxt == "\n":
                i += 2
                continue
            if nxt == "\r" and cmd[i + 2:i + 3] == "\n":
                i += 3
                continue
            out.append(cmd[i:i + 2])
            i += 2
            continue
        if c == "$" and cmd[i + 1:i + 2] == "(" and cmd[i + 2:i + 3] != "(":
            stack.append("sub")
            out.append("$(")
            i += 2
            continue
        if c == "`":
            if ctx == "bq":
                stack.pop()
            else:
                stack.append("bq")
            out.append(c)
            i += 1
            continue
        if ctx == '"':
            out.append(c)
            i += 1
            if c == '"':
                stack.pop()
            continue
        # ── unquoted (top level, or inside $(…) / backticks) ──
        if c in ("'", '"'):
            stack.append(c)
            out.append(c)
            i += 1
            continue
        if c == "$" and cmd[i + 1:i + 2] == "'":
            stack.append("$'")
            out.append("$'")
            i += 2
            continue
        if c == "(" and ctx in ("sub", "par"):
            stack.append("par")
        elif c == ")" and ctx in ("sub", "par"):
            stack.pop()
        if c == "#" and last_char() in _COMMENT_CONTEXT:
            j = cmd.find("\n", i)
            i = n if j < 0 else j
            continue
        if cmd.startswith("<<<", i):
            out.append("<<<")
            i += 3
            continue
        if ctx == "top" and cmd.startswith("<<", i):
            m = _HEREDOC_OP_RE.match(cmd, i)
            if m:
                pending.append((m.group(4), not (m.group(2) or m.group(3))))
                out.append(m.group(0))
                i = m.end()
                continue
        out.append(c)
        i += 1
        if c == "\n" and ctx == "top":
            if pending:
                line = "".join(out[line_start:])
                if not term_index:
                    pos = 0
                    for text in cmd.split("\n"):
                        term_index.setdefault(text.strip(), []).append((pos, pos + len(text)))
                        pos += len(text) + 1
                i = _consume_heredocs(cmd, i, pending, line, out, term_index)
                pending = []
            line_start = len(out)
    return "".join(out)


def _consume_heredocs(cmd, i, pending, line, out, term_index) -> int:
    """Handle the bodies of the heredocs opened on `line`; return where commands resume.

    Terminator matching is lenient (surrounding whitespace ignored), so a body
    can only end early, which reads leftover body lines as commands: a false
    positive, never a skipped command. An unterminated heredoc is left as
    commands for the same reason.
    """
    feeds_shell = bool(_HEREDOC_EXEC_RE.search(line))
    for delim, expands in pending:
        spans = term_index.get(delim, [])
        k = bisect.bisect_left(spans, (i, -1))
        if k >= len(spans):
            return i
        body_end, term_end = spans[k]
        body = cmd[i:body_end]
        if feeds_shell:
            out.append(shell_prepass(body))
        elif expands:
            # `\`` and `\$` are literal characters in an unquoted heredoc, not
            # the start of a substitution (markdown written through `cat <<EOF`
            # is full of escaped backticks around command names).
            live = re.sub(r"\\[`$\\]", "", body)
            subs = "".join((a or b) + "\n" for a, b in _SUBST_RE.findall(live))
            if subs:
                out.append(subs)
        i = min(term_end + 1, len(cmd))
    return i


_CONTINUATION_OPS = {"|", "|&", "&&", "||"}


def _normalize_ops(tokens):
    """shlex glues a newline onto the operator before it (`|\\n`, `;\\n`) and
    keeps a newline after an operator as its own token. A newline after `|`,
    `&&` or `||` continues the command; anywhere else it separates like `;`."""
    out = []
    for tok in tokens:
        if "\n" in tok and _is_op(tok):
            core = tok.replace("\n", "")
            if core:
                out.append(core)
            elif out and (out[-1] in _CONTINUATION_OPS or out[-1] == "\n"):
                continue
            else:
                out.append("\n")
        else:
            out.append(tok)
    return out


def tokenize(cmd: str):
    """shlex tokens of an already shell_prepass()ed string, or None if the
    shell string itself is unparseable.

    punctuation_chars makes ();<>|& standalone tokens, so chained commands,
    redirections and quoted text don't bleed into the parse. A newline separates
    commands exactly like ';' (shlex would otherwise read it as whitespace and
    merge "cd /tmp\\npkill node" into one command whose first word is `cd`).
    """
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars="();<>|&\n")
        lex.whitespace = " \t\r"
        lex.whitespace_split = True
        lex.commenters = ""  # comments were removed by shell_prepass, quote-aware
        return _normalize_ops(list(lex))
    except ValueError:
        return None


def _is_op(tok: str) -> bool:
    return bool(tok) and all(c in _SHELL_OP_CHARS for c in tok)


def _cmd_name(tok: str) -> str:
    """Basename of a command token, so `/bin/grep` and `./grep` still read as grep."""
    return tok.rsplit("/", 1)[-1] if "/" in tok else tok


def split_commands(tokens):
    """[(simple-command tokens, pipeline-group id)].

    A simple command ends at any shell operator token. A pipeline group is a run
    of simple commands joined only by `|`/`|&`, so `find … | xargs cat` is one
    group while `a && b` is two.
    """
    out = []
    cur: list = []
    group = 0
    for tok in tokens:
        if _is_op(tok):
            if cur:
                out.append((cur, group))
                cur = []
            if tok not in _PIPE_TOKENS:
                group += 1
        else:
            cur.append(tok)
    if cur:
        out.append((cur, group))
    return out


# ── Secret-read guard ─────────────────────────────────────────────────────────
# Patterns matching a path token that names known secret material. Anchored to a
# path boundary (start, '/', or '~/') so a substring like "environment" or
# "keystore" doesn't trip ".env"/"id_rsa". Each must look like a real path
# reference, not an arbitrary flag value.
_SECRET_PATH_RE = re.compile(
    r"""(?:^|/|~/)        # path boundary
        (?:
            \.env(?:\.(?!(?:example|sample|template|dist)$)[^/\s]+)?  # .env, .env.local … (not .env.example)
          | id_rsa[^/\s]*          # id_rsa, id_rsa.pub …
          | \.git-credentials
          | \.npmrc
          | \.pypirc
        )$
    """,
    re.VERBOSE,
)
# Suffix/dir patterns checked separately (apply anywhere in the token's basename).
_SECRET_SUFFIX_RE = re.compile(r"\.(?:pem|key)$")
_SECRET_SUBSTR = (
    ".aws/credentials",
    ".ssh/",
)
# A bare "~/.ssh" or ".aws/credentials" reference (no trailing component) also counts.
_SECRET_EXACT = (".ssh",)
# Cheap superset of every literal the three rules above can match. One search over
# the whole token stream gates the entire secret scan, so an ordinary large
# command (a heredoc that writes prose, a long pipeline) never pays for it.
_SECRET_HINT_RE = re.compile(
    r"\.env|id_rsa|\.git-credentials|\.npmrc|\.pypirc|\.pem|\.key|\.ssh|\.aws/credentials"
)


def _is_secret_token(tok: str) -> bool:
    """True if a path-like token clearly references secret material."""
    # Strip surrounding quotes shlex may leave on partial tokens.
    t = tok.strip().strip("'\"")
    if not t:
        return False
    if _SECRET_PATH_RE.search(t):
        return True
    base = t.rsplit("/", 1)[-1]
    if _SECRET_SUFFIX_RE.search(base):
        return True
    if any(s in t for s in _SECRET_SUBSTR):
        return True
    # Trailing ~/.ssh or .ssh as the final path component.
    cleaned = t.rstrip("/")
    if cleaned.rsplit("/", 1)[-1] in _SECRET_EXACT and (
        "/" in cleaned or cleaned.startswith("~") or cleaned in _SECRET_EXACT
    ):
        # Only treat a lone ".ssh" path component as secret when it looks like a
        # home-relative path, to avoid matching an unrelated "ssh" subcommand arg.
        if cleaned in _SECRET_EXACT or cleaned.startswith("~") or "/.ssh" in cleaned:
            return True
    return False


# Readers whose file arguments Claude's recognized-file-command deny does NOT cover.
# (cat/head/tail/sed are already handled by Read-deny, so we don't re-police them.)
_SECRET_READERS = {
    "grep", "egrep", "fgrep", "rg", "ag",
    "awk", "gawk", "nawk",
    "source", ".",
    "dd", "od", "xxd", "hexdump", "strings", "base64", "cut", "tr", "sort", "uniq",
}
# grep-family readers take a search PATTERN as their first non-flag argument;
# that token is not a file path (grep -r ".env" src/ searches FOR the string).
_PATTERN_FIRST_READERS = {"grep", "egrep", "fgrep", "rg", "ag"}
# Interpreters that can read files via inline code; we scan their inline string for
# a secret path literal.
_INLINE_INTERP = {"python", "python3", "node", "ruby", "perl", "php", "deno", "bun"}
_EVAL_FLAGS = {"-c", "-e", "--eval", "--exec"}
_INPUT_REDIRECTS = {"<", "0<"}


def _quoted(tok: str) -> str:
    return tok.strip(chr(39) + chr(34))


def _scan_simple_command(seg):
    """Deny reader→secret-file pairs inside ONE simple command.

    Linear in len(seg): the per-token facts (is-flag, is-secret) are computed
    once and the "first secret argument after position k" question is answered
    from a suffix table, instead of re-scanning the tail for every reader token.
    That is the whole of the old quadratic blow-up.

    Returns (has_xargs, secret_file_tokens) for the pipeline-level xargs rule.
    """
    n = len(seg)
    is_arg = [not t.startswith("-") for t in seg]
    is_secret = [is_arg[i] and _is_secret_token(seg[i]) for i in range(n)]
    next_secret = [n] * (n + 1)
    next_arg = [n] * (n + 1)
    for i in range(n - 1, -1, -1):
        next_secret[i] = i if is_secret[i] else next_secret[i + 1]
        next_arg[i] = i if is_arg[i] else next_arg[i + 1]

    readers: dict = {}          # reader name -> first index (earliest dominates)
    pattern_pos = set()         # positions consumed as a grep-family search pattern
    interp_at = n               # earliest inline interpreter
    eval_at = -1                # latest -c/-e whose payload names a secret
    has_xargs = False
    for i, tok in enumerate(seg):
        name = _cmd_name(tok)
        if name in _SECRET_READERS and not (name in (".", "source") and i != 0):
            # '.'/'source' only count at command position; as an argument
            # (find . …, rsync … .) they are not readers.
            readers.setdefault(name, i)
            if name in _PATTERN_FIRST_READERS:
                p = next_arg[i + 1]
                if p < n:
                    pattern_pos.add(p)
        elif name == "xargs":
            has_xargs = True
        if name in _INLINE_INTERP and i < interp_at:
            interp_at = i
        if tok in _EVAL_FLAGS and i > eval_at and i + 1 < n and _inline_has_secret(seg[i + 1]):
            eval_at = i

    # 1) reader followed by a secret-path argument
    for name, i in sorted(readers.items(), key=lambda kv: kv[1]):
        start = i + 1
        if name in _PATTERN_FIRST_READERS:
            p = next_arg[start]
            if p >= n:
                continue          # nothing but flags after it
            start = p + 1         # its first non-flag argument is the pattern
        j = next_secret[start]
        if j < n:
            deny(
                f"read secret material ('{_quoted(seg[j])}') "
                f"via '{name}'. These files (.env, keys, credential stores) are "
                f"blocked for a reason; don't exfiltrate or echo their contents."
            )

    # 2) inline interpreter code: python -c "open('.env')", node -e "...id_rsa..."
    if interp_at < n and eval_at > interp_at:
        deny(
            "inline interpreter code that opens secret material "
            "(.env / keys / credential stores). Read of these paths is "
            "blocked; don't bypass it via -c/-e."
        )

    secret_files = [seg[i] for i in range(n) if is_secret[i] and i not in pattern_pos]
    return has_xargs, secret_files


def check_secret_read(tokens) -> None:
    """Deny Bash commands that read a known secret path via an uncovered reader.

    Conservative by construction: requires BOTH a recognized reader/interpreter
    (or an input redirection, or an xargs pipeline) AND a clear secret-path
    token, so normal commands pass untouched.
    """
    if not tokens:
        return
    if not _SECRET_HINT_RE.search("\n".join(tokens)):
        return  # no token even mentions secret material — nothing to police

    # 0) input redirection: `grep KEY < .env`, `while read l; …; done < .env`.
    #    The reader never names the file, so the scans below cannot see it.
    #    Input only — `cat > .env <<EOF` legitimately *writes* one.
    for i, tok in enumerate(tokens):
        if tok in _INPUT_REDIRECTS and i + 1 < len(tokens):
            target = tokens[i + 1]
            if not _is_op(target) and _is_secret_token(target):
                deny(
                    f"feed secret material ('{_quoted(target)}') into a "
                    f"command via input redirection. These files (.env, keys, "
                    f"credential stores) are blocked; don't read them from Bash."
                )

    # 1/2) per simple command, then the pipeline-level xargs rule.
    xargs_groups = set()
    group_secrets: dict = {}
    for seg, gid in split_commands(tokens):
        has_xargs, secret_files = _scan_simple_command(seg)
        if has_xargs:
            xargs_groups.add(gid)
        if secret_files:
            group_secrets.setdefault(gid, []).extend(secret_files)
    # `find . -name .env | xargs cat` — the secret token sits in an earlier stage
    # of the pipeline and the reader behind xargs is opaque to us, so any secret
    # FILE token anywhere in an xargs pipeline is refused. Tokens consumed as a
    # grep-family search pattern are excluded, so `grep -rn '\.env' src/ | xargs
    # wc -l` (searching the codebase FOR the string) still passes.
    for gid in sorted(xargs_groups):
        hits = group_secrets.get(gid)
        if hits:
            deny(
                f"'xargs' pipeline that targets secret material "
                f"('{_quoted(hits[0])}')."
            )


def _inline_has_secret(code: str) -> bool:
    """Scan an inline -c/-e string for a quoted secret-path literal."""
    for m in re.finditer(r"""['"]([^'"]+)['"]""", code):
        if _is_secret_token(m.group(1)):
            return True
    return False


# ── git guard ─────────────────────────────────────────────────────────────────
# Short `git commit` flags that take no value, so a cluster made only of these
# can be split safely. `-n` inside such a cluster IS --no-verify. Anything with a
# value-taking letter (-m, -F, -t, -C, -S …) is left alone: `git commit -am 'msg'`
# must never be misread as a bypass.
_COMMIT_VALUELESS_SHORTS = set("aeinopqsuvz")


def _is_short_no_verify(tok: str) -> bool:
    if not tok.startswith("-") or tok.startswith("--") or len(tok) < 2:
        return False
    body = tok[1:]
    return "n" in body and all(c in _COMMIT_VALUELESS_SHORTS for c in body)


def git_invocations(tokens):
    """[(subcommand, global-opts, args, repo_args)] for every real `git …` call.

    global-opts is [(opt, value|None)]; args stops at the end of the simple
    command. Scanning resumes just after the subcommand (as the old push parser
    did), so a nested `git` later in the same simple command is still found.
    """
    n = len(tokens)
    out = []
    i = 0
    while i < n:
        if _cmd_name(tokens[i]) != "git":
            i += 1
            continue
        j = i + 1
        gopts = []
        while j < n and tokens[j].startswith("-"):
            if tokens[j] in _GIT_GLOBAL_VALUE_OPTS:
                gopts.append((tokens[j], tokens[j + 1] if j + 1 < n else ""))
                j += 2
            else:
                gopts.append((tokens[j], None))
                j += 1
        if j >= n or _is_op(tokens[j]):
            i = j + 1
            continue
        sub = tokens[j]
        args = []
        k = j + 1
        while k < n and not _is_op(tokens[k]):
            args.append(tokens[k])
            k += 1
        repo_args = []
        for opt, val in gopts:
            if opt in _GIT_REPO_VALUE_OPTS and val is not None:
                repo_args += [opt, val]
            elif opt.startswith(_GIT_REPO_INLINE_OPTS):
                repo_args.append(opt)
        out.append((sub, gopts, args, tuple(repo_args)))
        i = j + 1
    return out


def check_git(tokens) -> None:
    invocations = git_invocations(tokens)
    if not invocations:
        return

    # 1) verify bypasses, scoped to the git invocation's own argv (so a commit
    #    MESSAGE that mentions --no-verify is not a bypass).
    for sub, gopts, args, _repo in invocations:
        gopt_names = [o for o, _v in gopts]
        if "--no-verify" in args or "--no-verify" in gopt_names or (
            sub == "commit" and any(_is_short_no_verify(a) for a in args)
        ):
            deny(
                "bypass pre-commit/pre-push hooks via --no-verify (or its "
                "short form -n). Fix the underlying failure (lint/format/test) instead "
                "of skipping the check."
            )
        for opt, val in gopts:
            if opt == "-c" and val is not None and val.lower().startswith("core.hookspath="):
                deny(
                    "'git -c core.hooksPath=…': it disables the project's "
                    "pre-commit/pre-push hooks just like --no-verify. Fix the "
                    "underlying failure instead of skipping the check."
                )

    # 1b) persistent form of the same bypass: `git config core.hooksPath <dir>`.
    #     Reading or unsetting it is harmless; pointing it at nothing disables
    #     every hook; pointing it at a directory is how repos install .githooks,
    #     so that goes to the user.
    for sub, _gopts, args, _repo in invocations:
        if sub != "config":
            continue
        keys = [k for k, a in enumerate(args) if a.lower() == "core.hookspath"]
        if not keys or args[:1] in (["get"], ["unset"], ["list"]) or any(
                a in _GIT_CONFIG_READ_OPTS for a in args):
            continue
        values = [a for a in args[keys[0] + 1:] if not a.startswith("-")]
        if not values:
            continue  # `git config core.hooksPath` only prints the value
        if values[0].strip() in ("", "/dev/null") or values[0].lower() == "nul":
            deny(
                "'git config core.hooksPath' pointed at nothing disables the project's "
                "pre-commit/pre-push hooks for every future command, the persistent form of "
                "--no-verify. Fix the underlying failure instead of skipping the check."
            )
        ask(f"'git config core.hooksPath {values[0]}' changes which hooks run for every future "
            "git command in this repo",
            "confirm the directory holds the project's own hooks (a checked-in .githooks is the usual case)")

    # 2) commit to a protected branch
    for sub, _gopts, args, repo in invocations:
        if sub != "commit" or "--amend" in args:
            continue
        branch = current_branch(repo)
        if branch in PROTECTED:
            where = f" (target repo: {' '.join(repo)})" if repo else ""
            deny(
                f"commit directly to protected branch '{branch}'{where}. "
                f"Create a feature branch first: git checkout -b <branch-name>"
            )

    # 3) push to a protected branch
    dests = []
    needs_current = []
    push_all = False
    for sub, _gopts, args, repo in invocations:
        if sub != "push":
            continue
        positionals = []
        k = 0
        while k < len(args):
            t = args[k]
            if t.startswith("-"):
                if t in ("--all", "--mirror"):
                    push_all = True
                elif t in _PUSH_VALUE_OPTS:
                    k += 1  # consume the option's value
                k += 1
                continue
            positionals.append(t)
            k += 1
        refspecs = positionals[1:]  # positionals[0] is the remote
        if not refspecs:
            needs_current.append(repo)  # no refspec → pushes the current branch
        for refspec in refspecs:
            dst = refspec.split(":")[-1].lstrip("+")
            if dst.startswith("refs/heads/"):
                dst = dst[len("refs/heads/"):]
            if dst in ("HEAD", "@"):
                needs_current.append(repo)  # `git push origin HEAD` → current branch
            else:
                dests.append(dst)

    if push_all:
        deny(
            "'git push --all'/'--mirror': it updates every branch, "
            "including protected ones (main/master/production/release). "
            "Push specific feature branches instead."
        )
    for repo in needs_current:
        dests.append(current_branch(repo))
    for target in dests:
        if target in PROTECTED:
            deny(
                f"push to protected branch '{target}'. "
                f"Open a pull request from a feature branch instead."
            )

    # 4) landing work on a protected branch without a commit or a push: a merge
    #    and its relatives move it. Asked, not denied (after the denials above,
    #    so `git merge --squash x && git commit` is still refused).
    _check_protected_landing(tokens, invocations)

    # 5) options that turn an allow-listed git read into running a command or
    #    writing a file, on any branch (templates/settings.json allows `git
    #    diff/log/stash/fetch/rebase *` with no prompt). rebase --exec / -x are
    #    asked by (4) already.
    #    The whole argument list is scanned, `--` or not: git's parse-options
    #    takes a `--` as the value of an option that wants one (`git fetch -o --
    #    --upload-pack=…`, `git push --repo -- --exec=…` run it), and no
    #    subcommand is trusted to stop there. A path named like an option after
    #    `--` (`git diff -- --output`) costs a prompt.
    for sub, _gopts, args, _repo in invocations:
        for a in args:
            if a.startswith("--") and len(a) > 3:
                name = a.split("=", 1)[0]
                # git takes any unique prefix of a long option (`--upload-p`, `--exe`)
                hit = next((opt for opt in _GIT_EXEC_LONG if opt.startswith(name)), None)
                if hit:
                    ask(f"'git {sub} {name}' {_GIT_EXEC_LONG[hit]}",
                        "run the command or write the file as its own step, where it is visible")
                if sub == "clone" and "--config".startswith(name) and len(name) > 4:
                    ask(f"'git clone {name}' {_GIT_CLONE_CONFIG}", "clone without it, then set the configuration")
            elif sub == "clone" and "u" in _short_flags(a):
                ask(f"'git clone -u' {_GIT_EXEC_LONG['--upload-pack']}", "clone without -u")
            elif sub == "clone" and "c" in _short_flags(a):
                ask(f"'git clone -c' {_GIT_CLONE_CONFIG}", "clone without it, then set the configuration")


# Long options that run a command or write a file, and what they do. Matched by
# prefix, since git's option parser takes any unique abbreviation (the diff
# options take none, so `git diff --out=f` only fails; it asks all the same).
_GIT_EXEC_LONG = {
    "--output": "writes its output to any file, outside write-guard",
    "--exec": "runs a shell command (push, archive: as the remote helper; rebase: after each commit)",
    "--upload-pack": "runs a shell command as the remote helper",
    "--receive-pack": "runs a shell command as the remote helper",
}
_GIT_CLONE_CONFIG = ("sets configuration the clone itself uses, which can name a command to run "
                     "(core.sshCommand, a credential helper)")

# ── landing work on a protected branch (merge gate) ───────────────────────────
# Commit and push to main/master/production/release are denied, but a merge into
# one moves it just as well, and a human merges at the factory's merge gate. The
# gate works by allowlist, not by listing the dangerous commands:
#   (A) HEAD on a protected branch (or moved to one, or to an unknown branch
#       such as `-` or @{-1}, earlier in the command line): a git command runs
#       without asking only if it cannot move a branch (_landing_allowed): reads,
#       add/mv/rm/restore/clean/apply/stash/push (their own checks still apply),
#       fetch with no refspec, branch listing or creating a new branch, checkout
#       or switch without -B/-C, tag listing, config reads, remote -v/show,
#       worktree list, reset of the index, --abort/--quit of a merge, rebase,
#       cherry-pick, revert or am, and `git pull --ff-only` from the branch's
#       own verified upstream. Anything else asks: an unknown subcommand (so
#       every alias), commit --amend, a global -c / --config-env, or a GIT_*
#       environment prefix.
#   (B) Any branch: a command that is not a read and whose arguments name a
#       protected branch (a word, refs/heads/<p>, <remote>/<p>, either side of a
#       refspec or a range, the value of --track / -t / -u, an option's attached
#       value) asks, and so does one that names a branch by position (`-`,
#       @{-1}) or through an expansion or glob; also update-ref --stdin,
#       symbolic-ref writes, remote add / set-branches / set-url / rename,
#       config -e and config writes to alias.*, remote.*, branch.*, url.*,
#       pull.*, include*, core.hooksPath (directly, or as -c / --config-env /
#       GIT_CONFIG_* for one command).
#       Also as classes, on any branch: refs from stdin (--stdin or a prefix of
#       it, on anything but a read) and ref-writing plumbing (_GIT_REF_PLUMBING),
#       fetch --update-head-ok, rebase --update-refs (and rebase.* config), a
#       long option of branch / checkout / switch / worktree / update-ref /
#       symbolic-ref that is not in _GIT_KNOWN_LONG (git takes `--tra` for
#       --track), and `<remote>/<protected>` as the last path segment of any
#       argument, whatever option precedes it; an interactive rebase, rebase
#       --exec, pull --rebase=interactive, and an editor set for one command
#       (-c sequence.editor / core.editor, GIT_SEQUENCE_EDITOR, GIT_EDITOR,
#       EDITOR, VISUAL): a todo list or an editor can run any git command. On
#       a protected branch, a command line that may change what git inherits
#       (_env_touched: an assignment, any variable-assigning or directory-
#       changing builtin as any word git does not take itself, or the name
#       HOME, XDG_CONFIG_HOME, PATH, GIT_*, EDITOR or VISUAL) asks, and the pull
#       exception refuses it.
#   (C) Option clusters and --opt=value are split before matching; a git
#       subcommand that is not a plain word asks, and so does any git command in
#       a command line shlex cannot read (check_landing_unparseable).
# This is a text guard. Residual class, documented: a `cd` into another
# repository before the git command (like the commit check, the repository is
# the cwd or the -C / --git-dir the invocation names); `.git/config` or
# `~/.gitconfig` edited by hand through the shell; and git run by `sh -c` or a
# script. A git reference-transaction hook would close it (docs/guides/factory.md,
# Not done yet).
_GIT_READS = {
    "status", "log", "diff", "show", "rev-parse", "rev-list", "ls-files", "ls-tree", "ls-remote", "grep",
    "blame", "annotate", "describe", "shortlog", "cat-file", "for-each-ref", "merge-base", "help", "version",
    "show-ref", "show-branch", "name-rev", "whatchanged", "range-diff", "cherry", "diff-tree", "diff-files",
    "diff-index", "count-objects", "check-ignore", "check-attr", "check-ref-format", "var", "format-patch",
    "archive", "fsck", "verify-commit", "verify-tag", "difftool"}
# Change only the work tree, the index or the stash: allowed on a protected branch
# (the deletion guard and the others still apply).
_GIT_TREE_ONLY = {"add", "mv", "rm", "restore", "clean", "apply", "stash", "push"}
# Take paths, not branches: their arguments are not matched against branch names.
_GIT_PATH_ARGS = {"add", "mv", "rm", "restore", "clean", "apply", "commit"}
# Take branches or commits as arguments: one built by an expansion or a glob asks.
_GIT_REF_TAKERS = {"merge", "rebase", "pull", "fetch", "push", "reset", "update-ref", "symbolic-ref", "branch",
                   "checkout", "switch", "worktree", "cherry-pick", "revert", "tag", "replace", "notes", "bisect"}
_MERGE_FAMILY = {"merge", "rebase", "cherry-pick", "revert", "am"}
_MERGE_BACKOUT = {"--abort", "--quit", "--show-current-patch"}
_BRANCH_MOVERS = {"-f", "--force", "-m", "-M", "--move", "-c", "-C", "--copy", "-u", "--set-upstream-to",
                  "--unset-upstream", "--edit-description", "-t", "--track"}
# Plumbing that writes refs from a stream or a remote, and other ways a ref can be
# rewritten wholesale: asked on any branch.
_GIT_REF_PLUMBING = {"fast-import", "receive-pack", "unpack-objects", "index-pack", "fetch-pack", "send-pack",
                     "upload-pack", "http-fetch", "http-push", "replace"}
# Every long option of the commands that create, move or repoint a branch. git takes
# any unambiguous prefix of an option (`--tra` for --track), so an option not in its
# command's list (an abbreviation, or one bash-guard does not know) asks.
_GIT_KNOWN_LONG = {
    "branch": {"--color", "--abbrev", "--column", "--sort", "--merged", "--contains", "--points-at", "--format",
               "--show-current", "--list", "--all", "--remotes", "--verbose", "--quiet", "--delete", "--force",
               "--move", "--copy", "--track", "--set-upstream-to", "--unset-upstream", "--edit-description",
               "--create-reflog", "--recurse-submodules", "--ignore-case", "--omit-empty"},
    "checkout": {"--quiet", "--progress", "--force", "--ours", "--theirs", "--track", "--guess", "--overlay",
                 "--detach", "--orphan", "--ignore-skip-worktree-bits", "--merge", "--conflict", "--patch",
                 "--ignore-other-worktrees", "--overwrite-ignore", "--recurse-submodules", "--pathspec-from-file",
                 "--pathspec-file-nul"},
    "switch": {"--create", "--force-create", "--detach", "--guess", "--force", "--discard-changes", "--merge",
               "--conflict", "--quiet", "--progress", "--track", "--orphan", "--ignore-other-worktrees",
               "--recurse-submodules"},
    "worktree": {"--force", "--detach", "--checkout", "--lock", "--reason", "--orphan", "--quiet", "--track",
                 "--guess-remote", "--porcelain", "--verbose", "--expire", "--dry-run", "--relative-paths"},
    "update-ref": {"--no-deref", "--create-reflog"},
    "symbolic-ref": {"--quiet", "--short", "--recurse", "--delete"},
}
_PULL_FF_SAFE = {"--ff-only", "-q", "--quiet", "-v", "--verbose", "--progress", "--no-progress",
                 "--stat", "-n", "--no-stat", "--prune", "-p", "--no-tags"}
_NETWORK_URL_RE = re.compile(r"^(?:https://|ssh://|git@[\w.-]+:)")
# Configuration that decides what a git command does to a branch: aliases, remotes,
# upstreams, URL rewriting, pull's and rebase's modes (rebase.updateRefs), the
# editors that run an interactive rebase's todo list, included files, hooks.
_LANDING_CONFIG_RE = re.compile(r"^(?:alias|remote|branch|url|pull|rebase|sequence|include|includeif)(?:\.|$)"
                                r"|^core\.(?:hookspath|editor)$", re.I)
_REV_SUFFIX_RE = re.compile(r"(?:[~^][0-9]*|@\{[^}]*\})+$")
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+?=")
# Builtins that can assign a variable or change the directory, so what a later git
# inherits: its configuration (HOME, XDG_CONFIG_HOME, GIT_*), the git binary itself
# (PATH), its editor, or its repository. Matched as any word that is not one of
# git's own arguments, not by where a command can start (if / while / && / || …).
_VAR_ASSIGNERS = {"env", "export", "declare", "typeset", "local", "readonly", "set", "unset", "source", ".", "eval",
                  "read", "mapfile", "readarray", "getopts", "printf", "let", "cd", "pushd", "popd", "shopt",
                  "alias", "enable", "trap", "exec", "builtin"}
# Variables that point git at other configuration, another git or another editor.
_GIT_ENV_NAMES_RE = re.compile(r"(?<![A-Za-z0-9_])(?:HOME|XDG_CONFIG_HOME|PATH|GIT_[A-Za-z0-9_]*|EDITOR|VISUAL)"
                               r"(?![A-Za-z0-9_])")
# Editors set for one command: they run an interactive rebase's todo list or a commit
# message editor, which can be any program, git commands that move a branch included.
_EDITOR_ENV_RE = re.compile(r"^(?:GIT_SEQUENCE_EDITOR|GIT_EDITOR|EDITOR|VISUAL)=")
_GIT_SUB_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_LANDING_SUGGESTION = ("land work on a protected branch through a pull request (the factory's merge gate), or "
                       "confirm with the user that this change to it is intended")


def _protected_named(tok: str) -> str:
    """The protected branch `tok` names, or '': the word itself, an option's
    attached value (`--force-create=master`, `-Bmaster`), either side of a
    refspec or a range, with `+`, `^` and revision suffixes (`~1`, `^`, `@{u}`)
    removed, and as the last segment of a path: `refs/heads/master`,
    `refs/remotes/<r>/master` and `<remote>/master` all count, whatever option
    stands before them (git makes a local master from `--track <remote>/master`,
    and takes `--tra` for --track)."""
    cands = [tok]
    if tok.startswith("--") and "=" in tok:
        cands.append(tok.split("=", 1)[1])
    elif tok.startswith("-") and len(tok) > 2:
        cands += [tok[k:] for k in range(2, len(tok))]
    for c in cands:
        for part in re.split(r":|\.\.\.?", c):
            p = _REV_SUFFIX_RE.sub("", part.lstrip("+^")).rsplit("/", 1)[-1]
            if p in PROTECTED:
                return p
    return ""


def _abbreviates(flags, full: str) -> bool:
    """A long option in `flags` is `full` or a prefix git would take for it (`--upd` for --update-refs)."""
    return any(f.startswith("--") and len(f) > 2 and full.startswith(f) for f in flags)


def _git_word_positions(tokens) -> set:
    """Indexes of the words git itself takes (everything after a `git` up to the
    next operator): `git add .` passes `.` to git, it sources nothing."""
    out, k = set(), 0
    while k < len(tokens):
        if _cmd_name(tokens[k]) == "git":
            k += 1
            while k < len(tokens) and not _is_op(tokens[k]):
                out.add(k)
                k += 1
        else:
            k += 1
    return out


def _env_touched(tokens) -> str:
    """What in this command line may change the environment or directory a git
    command inherits, or '': an assignment, or a builtin that can assign or
    change directory (export, read, printf -v, let, cd, source …), as any word
    that is not one of git's own arguments, wherever it stands (after if,
    while, &&, || …); or any mention of HOME, XDG_CONFIG_HOME, PATH, GIT_*,
    EDITOR or VISUAL. Fails closed: `echo export` counts too."""
    gitwords = _git_word_positions(tokens)
    for k, t in enumerate(tokens):
        if _is_op(t) or k in gitwords:
            continue
        if _ASSIGNMENT_RE.match(t) or _cmd_name(t) in _VAR_ASSIGNERS:
            return t
    m = _GIT_ENV_NAMES_RE.search(" ".join(tokens))
    return m.group(0) if m else ""


def _git_flags(args) -> set:
    """The options in `args` before `--`, clusters split (`-fM` is -f and -M) and
    long ones without their `=value`."""
    out = set()
    for a in args:
        if a == "--":
            break
        if a.startswith("--"):
            out.add(a.split("=", 1)[0])
        elif a.startswith("-") and len(a) > 1:
            out.update("-" + c for c in a[1:])
    return out


def _git_config_values(repo, pattern):
    """{lowercased key: [values]} for config keys matching `pattern` in the repo
    `repo` (its -C / --git-dir options), or None when git cannot say."""
    try:
        out = subprocess.run(["git", *repo, "config", "--get-regexp", pattern],
                             capture_output=True, text=True, timeout=1)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode not in (0, 1):
        return None
    vals: dict = {}
    for line in out.stdout.splitlines():
        k, _, v = line.partition(" ")
        vals.setdefault(k.lower(), []).append(v)
    return vals


def _pull_from_own_upstream(args, gopts, repo, branch, tokens, invocations) -> bool:
    """True for `git pull --ff-only` (quiet/verbose/prune flags only, no remote or
    refspec) on `branch` whose upstream git can vouch for: the same branch on a
    named remote with one network URL, no URL rewriting, no rebase setting, and
    nothing in this command line that could change any of that first."""
    if "--ff-only" not in args or any(a not in _PULL_FF_SAFE for a in args):
        return False
    if any(not (o in _GIT_REPO_VALUE_OPTS or o.startswith(_GIT_REPO_INLINE_OPTS) or o in ("--no-pager", "-P"))
           for o, _v in gopts):
        return False
    if _env_touched(tokens) or any(s in ("config", "remote", "branch") for s, _g, _a, _r in invocations):
        return False                              # HOME=… / GIT_*=… / export …: other configuration
    cfg = _git_config_values(repo, r"^(branch\.%s\.(remote|merge|rebase)|pull\.rebase|remote\..*\.url"
                                   r"|url\..*\.(push)?insteadof)$" % re.escape(branch))
    if cfg is None or any(k.startswith("url.") for k in cfg):
        return False
    remote = (cfg.get(f"branch.{branch}.remote") or [""])[-1]
    urls = cfg.get(f"remote.{remote.lower()}.url") or []
    if remote in ("", ".") or len(urls) != 1 or not _NETWORK_URL_RE.match(urls[0]):
        return False
    if (cfg.get(f"branch.{branch}.merge") or [""])[-1] != f"refs/heads/{branch}":
        return False
    return all(v.lower() in ("false", "0", "no", "off")
               for k in (f"branch.{branch}.rebase", "pull.rebase") for v in cfg.get(k, []))


def _positionals(args):
    out = []
    for k, a in enumerate(args):
        if a == "--":
            return out, args[k + 1:]
        if not a.startswith("-"):
            out.append(a)
    return out, []


def _config_writes(args) -> bool:
    """`git config <args>` sets or renames something (a read or an unset is not:
    removing a key cannot add an alias, a remote or an upstream)."""
    words = [a for a in args if not a.startswith("-")]
    if words[:1] in (["get"], ["list"], ["unset"]):
        return False
    return words[:1] in (["set"], ["rename-section"], ["remove-section"]) or (
        len(words) >= 2 and not any(a in _GIT_CONFIG_READ_OPTS for a in args))


def _git_reads(sub, args, pos) -> bool:
    """A git command that only reads: never a target in (B), always allowed in (A)."""
    if sub in _GIT_READS:
        return True
    if sub == "reflog":
        return args[:1] not in (["expire"], ["delete"])
    if sub == "symbolic-ref":
        return len(pos) <= 1 and not ({"-d", "--delete"} & _git_flags(args))
    return False


def _landing_allowed(sub, gopts, args, pos, flags, repo, branch, tokens, invocations) -> bool:
    """(A): may this run with HEAD on protected `branch` (or an unknown one)?"""
    if _git_reads(sub, args, pos) or sub in _GIT_TREE_ONLY:
        return True
    if sub in _MERGE_FAMILY:
        return bool(flags & _MERGE_BACKOUT)
    if sub == "fetch":                            # a remote at most: no explicit refspec, none from stdin
        return len(pos) <= 1 and not _abbreviates(flags, "--stdin")
    if sub == "branch":
        return not (flags & _BRANCH_MOVERS)
    if sub in ("checkout", "switch"):
        return not (flags & {"-B", "-C", "--force-create"})
    if sub == "tag":
        return not pos or bool(flags & {"-l", "--list", "-v", "--verify"})
    if sub == "config":
        return not _config_writes(args) and not (flags & {"-e", "--edit"})
    if sub == "remote":
        return args[:1] in ([], ["-v"], ["--verbose"], ["show"], ["get-url"])
    if sub == "worktree":
        return args[:1] == ["list"]
    if sub == "reset":
        return not [p for p in pos if p not in ("HEAD", "@")]
    if sub == "pull":
        return branch in PROTECTED and _pull_from_own_upstream(args, gopts, repo, branch, tokens, invocations)
    return False


def _landing_target_problem(sub, args, gopts, tokens) -> str:
    """(B): why this git command may point a protected branch somewhere, on any branch, or ''."""
    flags = _git_flags(args)
    for opt, val in gopts:
        key = (val or "").split("=", 1)[0] if opt == "-c" else (
            opt.split("=", 1)[1].split("=", 1)[0] if opt.startswith("--config-env=") else "")
        if (opt == "-c" or opt.startswith("--config-env")) and (not key or _LANDING_CONFIG_RE.match(key)):
            return f"sets '{key or opt}' for this one command, which can change what it does to a branch"
    env = next((t for t in tokens if re.match(r"^GIT_(?:CONFIG\w*|EXEC_PATH)=", t)), "")
    if env:
        return f"sets {env.split('=', 1)[0]}, which can add aliases or configuration for this command"
    editor = next((t for t in tokens if _EDITOR_ENV_RE.match(t)), "")
    if editor:
        return (f"sets {editor.split('=', 1)[0]}, an editor git runs as a program (a rebase todo list or a "
                "message), which can run git commands that move any branch")
    if sub == "rebase" and (flags & {"-i", "-x"} or _abbreviates(flags, "--interactive")
                            or _abbreviates(flags, "--exec") or _abbreviates(flags, "--edit-todo")):
        return ("runs an interactive rebase or --exec commands, whose todo list can run git commands that move "
                "any branch")
    if sub == "pull" and any(re.match(r"^--r[a-z-]*=i", a) for a in args):
        return "pulls with an interactive rebase, whose todo list can run git commands that move any branch"
    if sub in _GIT_REF_PLUMBING and not (sub == "replace" and flags & {"-l", "--list"}):
        return f"runs 'git {sub}', plumbing that can write any ref, a protected branch included"
    if _abbreviates(flags, "--stdin") and not _git_reads(sub, args, _positionals(args)[0]):
        return "takes its refs or updates from stdin, where bash-guard cannot see whether one is a protected branch"
    if sub == "fetch" and _abbreviates(flags, "--update-head-ok"):
        return "may update the checked-out branch (--update-head-ok)"
    if sub == "rebase" and _abbreviates(flags, "--update-refs"):
        return "moves every branch that points into the rebased commits (--update-refs), a protected one included"
    known = _GIT_KNOWN_LONG.get(sub)
    odd = sorted(f for f in flags if known is not None and f.startswith("--") and f not in known
                 and not (f.startswith("--no-") and "--" + f[5:] in known)) if known is not None else []
    if odd:
        return (f"uses '{odd[0]}', not one of git {sub}'s options as bash-guard knows them (git also takes an "
                "abbreviation, such as --tra for --track), so it may move a branch")
    if sub == "symbolic-ref" and not _git_reads(sub, args, _positionals(args)[0]):
        return "repoints a symbolic ref (HEAD), so later commands can land on a protected branch"
    if sub == "remote" and args[:1] in (["add"], ["set-branches"], ["set-url"], ["rename"]):
        return f"runs 'remote {args[0]}', which changes what a branch fetches and pulls"
    if sub == "config":
        if flags & {"-e", "--edit"}:
            return "opens the configuration in an editor"
        key = next((a for a in args if not a.startswith("-") and _LANDING_CONFIG_RE.match(a)), "")
        if key and _config_writes(args):
            return f"writes '{key}', configuration that decides what git commands do to a branch"
        return ""
    pos, _paths = _positionals(args)
    if _git_reads(sub, args, pos) or sub in _GIT_PATH_ARGS:
        return ""
    before = args[:args.index("--")] if "--" in args else args
    listing = sub in ("branch", "tag") and bool(flags & {"-l", "--list", "--contains", "--merged", "--no-merged",
                                                         "--points-at", "-a", "-r", "--all", "--remotes"})
    # A plain switch only moves HEAD (to `-`, @{-1} or "$B", (A) then treats it as an
    # unknown branch); a checkout that creates or resets a branch names it.
    switch_only = sub in ("checkout", "switch") and not (
        flags & {"-b", "-B", "-c", "-C", "--force-create", "--orphan", "-t", "--track"})
    for a in before:
        name = _protected_named(a)
        if name:
            return f"names protected branch '{name}' ('{a[:40]}')"
        if switch_only:
            continue
        if sub in _GIT_REF_TAKERS and (a == "-" or "@{-" in a):
            return f"names a branch by position ('{a}'), which could be a protected one"
        if sub in _GIT_REF_TAKERS and not listing and re.search(r"[$`*?\[]", a):
            return f"names a branch the shell builds ('{a[:40]}'), which could be a protected one"
    return ""


def check_landing_unparseable(cmd: str) -> None:
    """(C) For a command shlex cannot read: any git command in it asks, whatever
    the branch (the regex cannot tell a harmless form from the others)."""
    if re.search(r"(?:^|[\s;&|(`])(?:\S*/)?git(?:\s|$)", cmd):
        ask("bash-guard could not parse this command (unbalanced quotes), so it cannot tell whether the git command "
            "in it moves a protected branch", _LANDING_SUGGESTION)


def _check_protected_landing(tokens, invocations) -> None:
    on: dict = {}     # repo -> the branch a checkout earlier in this command line moved HEAD to
    for sub, gopts, args, repo in invocations:
        shown = f"git {sub} {' '.join(args)}"[:70]
        if not _GIT_SUB_RE.match(sub):
            ask(f"'{shown}': the git subcommand is not written out plainly, so bash-guard cannot tell what runs",
                _LANDING_SUGGESTION)
        why = _landing_target_problem(sub, args, gopts, tokens)
        if why:
            ask(f"'{shown}' {why}", _LANDING_SUGGESTION)
        pos, _paths = _positionals(args)
        flags = _git_flags(args)
        branch = on.get(repo) or current_branch(repo)
        if branch in PROTECTED or branch.startswith("("):
            if any(o == "-c" or o.startswith("--config-env") for o, _v in gopts):
                ask(f"'{shown}' sets configuration (-c) for one command on protected branch '{branch}'",
                    _LANDING_SUGGESTION)
            env = _env_touched(tokens)
            if env:
                ask(f"'{shown}' runs on protected branch '{branch}' after '{env.split('=', 1)[0][:30]}' changes its "
                    "environment, which can point git at other configuration (HOME, XDG_CONFIG_HOME, GIT_*) or "
                    "another git", _LANDING_SUGGESTION)
            if not _landing_allowed(sub, gopts, args, pos, flags, repo, branch, tokens, invocations):
                hint = (" (only `git pull --ff-only` from its own upstream runs without asking)" if sub == "pull"
                        else "")
                ask(f"'{shown}' on protected branch '{branch}' is not one of the git commands that cannot move "
                    f"it{hint}", _LANDING_SUGGESTION)
        if sub in ("checkout", "switch"):
            target = "-" if "-" in args[:args.index("--") if "--" in args else len(args)] else (pos[0] if pos else "")
            if target == "-" or "@{-" in target or re.search(r"[$`*?\[]", target):
                on[repo] = "(an unknown branch)"
            elif _protected_named(target):
                on[repo] = _protected_named(target)


def check_git_unparseable(cmd: str) -> None:
    """Best-effort regex checks for a shell string shlex could not tokenize.

    Deliberately the pre-existing (looser) logic: an unbalanced quote is not a
    reason to start blocking ordinary work, but it is also not a reason to stop
    looking for a push to master.
    """
    if not re.search(r"(^|[\s;&|])(?:\S*/)?git\s", cmd):
        return
    if re.search(r"(^|[\s;&|])(?:\S*/)?git\s+push\b[^|;&\n]*?(?:\s--force(?=\s|$)|\s-[a-zA-Z]*f[a-zA-Z]*(?=\s|$))", cmd):
        deny("'git push --force' overwrites whatever the remote has, including commits you have not seen",
             "git push --force-with-lease (refuses if the remote moved), and only on a branch nobody else builds on")
    if re.search(r"(^|\s)--no-verify(\s|$)", cmd):
        deny(
            "bypass pre-commit/pre-push hooks via --no-verify. "
            "Fix the underlying failure (lint/format/test) instead of skipping the check."
        )
    if re.search(r"(^|[\s;&|])(?:\S*/)?git\s+commit\b", cmd) and not re.search(r"--amend\b", cmd):
        branch = current_branch()
        if branch in PROTECTED:
            deny(
                f"commit directly to protected branch '{branch}'. "
                f"Create a feature branch first: git checkout -b <branch-name>"
            )
    if "push" not in cmd:
        return
    m = re.search(r"git\s+(?:-\S+\s+)*push(?:\s+-\S+)*(?:\s+(\S+)(?:\s+(\S+))?)?", cmd)
    if not m:
        return
    if re.search(r"(^|\s)(--all|--mirror)(\s|$)", cmd):
        deny(
            "'git push --all'/'--mirror': it updates every branch, "
            "including protected ones (main/master/production/release). "
            "Push specific feature branches instead."
        )
    if m.group(2):
        dst = m.group(2).split(":")[-1].lstrip("+")
        targets = [current_branch() if dst in ("HEAD", "@") else dst]
    else:
        targets = [current_branch()]
    for target in targets:
        if target in PROTECTED:
            deny(
                f"push to protected branch '{target}'. "
                f"Open a pull request from a feature branch instead."
            )


# ── Destructive-command guard ─────────────────────────────────────────────────
# Same static, token-level philosophy as the git and secret guards: catch the
# ordinary form of a dangerous action, name the safe form, never pretend to be
# a sandbox. Two tiers:
#   deny — no legitimate interactive form worth a prompt (pipe-to-shell, env
#          exfiltration, wiping a disk, rm -rf on a root, name-based process
#          kills that hit every process by that name, registry redirection)
#   ask  — destructive but sometimes exactly what the user wants (git reset
#          --hard, git clean, docker prune, terraform destroy, DROP TABLE); the
#          user decides, and in an unattended run "ask" resolves to a refusal.
_WRAPPERS = {"sudo", "env", "nohup", "command", "exec", "time", "nice", "ionice", "doas"}
_WRAPPERS_WITH_VALUE = {"timeout": 1, "nice": 0, "ionice": 0}
# Wrapper flags that consume the next token, so `sudo -u root rm -rf /` still
# resolves to rm rather than to `root`.
_WRAPPER_VALUE_FLAGS = {
    "sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-T", "--user", "--group"},
    "doas": {"-u", "-C"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "-n", "-p", "--class", "--classdata", "--pid"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "env": {"-u", "-C", "-S", "--unset", "--chdir", "--split-string"},
}
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish"}
_INTERPRETERS = _SHELLS | {"python", "python3", "node", "perl", "ruby", "php", "deno", "bun"}
_FETCHERS = {"curl", "wget", "fetch"}
_NET_SINKS = {"curl", "wget", "nc", "ncat", "netcat", "telnet", "ssh", "socat"}
_ENV_DUMPERS = {"env", "printenv", "set", "export", "declare"}
_DB_CLIENTS = {"psql", "mysql", "mariadb", "sqlite3", "sqlcmd", "mongosh", "mongo", "redis-cli",
               "clickhouse-client", "cqlsh", "prisma", "sequelize", "knex", "drizzle-kit"}
_RM_CATASTROPHIC = {"/", "/*", "~", "~/", "~/*", "$HOME", "$HOME/", "$HOME/*", "*", ".", "..",
                    "./", "../", ".git", "./.git", ".claude", "./.claude"}
_RM_SYSTEM_PREFIXES = ("/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/opt", "/proc",
                       "/root", "/sbin", "/sys", "/usr", "/var")
# Per-user temp space under /var (macOS $TMPDIR lives in /var/folders).
_SCRATCH_PREFIXES = ("/var/tmp/", "/var/folders/")
# find predicates that select files by name, path, age, size or owner — enough to
# turn `find . -delete` (the whole project) into a targeted clean-up. -type is not
# one: `find . -type f -delete` still empties the project.
_FIND_NARROWING = {"-name", "-iname", "-path", "-ipath", "-wholename", "-iwholename", "-regex",
                   "-iregex", "-newer", "-mtime", "-mmin", "-atime", "-amin", "-ctime", "-cmin",
                   "-size", "-empty", "-user", "-group", "-perm", "-samefile", "-lname", "-ilname"}
# Deleting these is never "just cleaning up": lockfiles, ignore rules, CI, container
# and migration files carry state that is expensive or impossible to recreate.
_PROTECTED_BASENAMES = {
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb", "bun.lock",
    "uv.lock", "poetry.lock", "Pipfile.lock", "Cargo.lock", "Gemfile.lock",
    "go.sum", "composer.lock", "flake.lock", "mix.lock",
    ".gitignore", ".gitattributes", "Dockerfile", "docker-compose.yml",
    "docker-compose.yaml", "compose.yml", "compose.yaml",
    ".gitlab-ci.yml", "Jenkinsfile", ".travis.yml", "dependabot.yml", "dependabot.yaml",
}
_PROTECTED_PATH_PARTS = ("/.github/workflows/", "/.github/actions/", "/migrations/")
_PKG_CONFIG_FILES = {".npmrc", ".yarnrc", ".yarnrc.yml", "pip.conf", "pip.ini", ".pypirc",
                     "config.toml"}  # config.toml only when under .cargo/ (checked below)
_SQL_DROP_RE = re.compile(r"\b(?:DROP\s+(?:DATABASE|SCHEMA|TABLE)|TRUNCATE(?:\s+TABLE)?)\b", re.I)
_SQL_DELETE_NO_WHERE_RE = re.compile(
    r"\bDELETE\s+FROM\s+[`\"\w.]+\s*(?:;|$|['\"])", re.I | re.M)
_MONGO_DROP_RE = re.compile(r"\.(?:dropDatabase|drop)\s*\(", re.I)


def _strip_wrappers(seg):
    """`sudo -E env FOO=1 timeout 5 rm -rf x` -> ['rm', '-rf', 'x'] plus the
    wrappers seen (so `sudo rm` can be judged)."""
    seen = set()
    i = 0
    n = len(seg)
    while i < n:
        name = _cmd_name(seg[i])
        if name in _WRAPPERS or name in _WRAPPERS_WITH_VALUE:
            seen.add(name)
            i += 1
            skip = _WRAPPERS_WITH_VALUE.get(name, 0)
            # consume the wrapper's own flags / assignments / value
            vflags = _WRAPPER_VALUE_FLAGS.get(name, set())
            while i < n and (seg[i].startswith("-") or (name == "env" and "=" in seg[i])):
                i += 2 if seg[i] in vflags else 1
            i += skip
            continue
        break
    if i >= n:
        return seg, set()  # a bare `env` / `sudo` / `exec` IS the command, not a wrapper
    return seg[i:], seen


def _short_flags(tok: str) -> str:
    """Letters of a short flag cluster ('-rf' -> 'rf'); '' for long flags/args."""
    if tok.startswith("-") and not tok.startswith("--") and len(tok) > 1:
        return tok[1:]
    return ""


def _norm_path(tok: str) -> str:
    t = _quoted(tok)
    if len(t) > 1:
        t = t.rstrip("/") or "/"
    return t


def _is_protected_file(t: str) -> bool:
    base = t.rsplit("/", 1)[-1]
    if base in _PROTECTED_BASENAMES:
        return True
    rel = t[2:] if t.startswith("./") else t
    padded = "/" + rel.lstrip("/") + "/"
    return any(part in padded for part in _PROTECTED_PATH_PARTS)


def _check_rm(seg, wrappers) -> None:
    flags = "".join(_short_flags(t) for t in seg[1:])
    recursive = "r" in flags or "R" in flags or "--recursive" in seg
    targets = [t for t in seg[1:] if not t.startswith("-")]
    if "sudo" in wrappers or "doas" in wrappers:
        deny("'sudo rm' deletes as root; nothing an agent needs to remove requires that",
             "delete only files the project owns, without sudo")
    for raw in targets:
        t = _norm_path(raw)
        if t in _RM_CATASTROPHIC or t.startswith(("~/.", "$HOME/.")) and t.count("/") == 1:
            deny(f"'rm' aimed at '{t}' would remove a whole tree that is not the project's to delete "
                 "(a root, the home directory, the repo itself, or its .git/.claude state)",
                 "name the specific files or directories to remove")
        if _is_system_path(t):
            deny(f"'rm' aimed at system path '{t}'", "the project never needs files under there removed")
        if _is_protected_file(t):
            deny(f"deleting '{t}' throws away state that is expensive to recreate (lockfile, ignore "
                 "rules, CI config, container file, or migration)",
                 "if it truly must go, ask the user to delete it; regenerate lockfiles with the package manager")
        if recursive and t.rsplit("/", 1)[-1] in ("node_modules", ".venv", "venv", "target"):
            ask(f"'rm -r' on '{t}' deletes a build/dependency tree; reinstalling is slow but not destructive",
                "confirm this is intended rather than a stuck build that a clean rebuild would fix")


def _is_system_path(t: str) -> bool:
    if not t.startswith("/") or t.startswith(_SCRATCH_PREFIXES):
        return False
    return t in _RM_SYSTEM_PREFIXES or t.startswith(tuple(p + "/" for p in _RM_SYSTEM_PREFIXES))


def _check_find_delete(seg) -> None:
    """`find <roots> <predicates> -delete` (or -exec rm). Roots are the operands
    before the first predicate. The project directory (`.`) is fine to clean
    when a name/path/age/size test narrows what goes; a whole tree that is not
    the project's is refused whatever the predicates."""
    roots = []
    for t in seg[1:]:
        if t.startswith(("-", "(", "!")):
            break
        roots.append(_norm_path(t))
    narrowed = any(t in _FIND_NARROWING for t in seg)
    for r in roots or ["."]:
        if r in (".", "./") and narrowed:
            continue
        if r in _RM_CATASTROPHIC or _is_system_path(r):
            deny(f"'find {r} … -delete' removes files under a whole tree that is not the project's to delete",
                 "narrow it with -name/-path, or run the same find without -delete first and delete "
                 "the listed paths explicitly")


def _check_kill(seg) -> None:
    name = _cmd_name(seg[0])
    args = [t for t in seg[1:] if not t.startswith("-")]
    flags = " ".join(t for t in seg[1:] if t.startswith("-"))
    if name in ("pkill", "killall"):
        if name == "pkill" and re.search(r"(^|\s)-f\b", flags):
            return  # full-command-line match: targeted enough
        if args:
            deny(f"'{name} {args[0]}' kills EVERY process named '{args[0]}', including editors, "
                 "other agent sessions, and the harness running this hook",
                 f"target one process: kill <pid>, or pkill -f '<the exact command line>'")
    if name == "kill" and len(seg) >= 3 and seg[-1] == "-1":
        # `kill -9 -1`, `kill -TERM -1`, `kill -- -1`: pid -1 is "every process I own"
        deny("'kill … -1' signals every process the user owns", "kill a specific pid")


def _restore_staged_only(sub, args) -> bool:
    """`git restore --staged .` only unstages; add --worktree/-W and it discards."""
    if sub != "restore":
        return False
    shorts = "".join(_short_flags(a) for a in args)
    staged = "--staged" in args or "S" in shorts
    worktree = "--worktree" in args or "W" in shorts
    return staged and not worktree


def _check_git_destructive(tokens) -> None:
    for sub, _gopts, args, _repo in git_invocations(tokens):
        if sub == "push":
            forced = "--force" in args or any(
                "f" in _short_flags(a) and "F" not in _short_flags(a) for a in args
                if _short_flags(a) and a not in ("-u",))
            forced = forced or any(a.startswith("+") and len(a) > 1 for a in args)
            if forced:
                deny("'git push --force' overwrites whatever the remote has, including commits you have not seen",
                     "git push --force-with-lease (refuses if the remote moved), and only on a branch nobody else builds on")
        elif sub == "reset" and "--hard" in args:
            ask("'git reset --hard' discards every uncommitted change in the working tree",
                "git stash first, or reset a single path: git checkout -- <file>")
        elif sub == "clean" and (any("f" in _short_flags(a) for a in args) or "--force" in args):
            ask("'git clean -f' permanently deletes untracked files (new work that was never committed)",
                "run 'git clean -n' first to list what would go, or delete specific files")
        elif sub in ("checkout", "restore") and "." in args and all(
            a in (".", "--") or a.startswith("-") for a in args
        ) and not _restore_staged_only(sub, args):
            ask(f"'git {sub} .' throws away every uncommitted change in the working tree",
                f"git {sub} -- <specific file>, or git stash to keep the work")
        elif sub == "branch" and ("-D" in args or ("--delete" in args and "--force" in args)):
            ask("'git branch -D' deletes a branch even if its commits are unmerged (they become unreachable)",
                "git branch -d (refuses to drop unmerged work), or confirm the branch is merged first")
        elif sub == "stash" and args[:1] in (["drop"], ["clear"]):
            ask(f"'git stash {args[0]}' permanently discards stashed work", "git stash list first; pop or apply what is still needed")
        elif sub in ("filter-branch", "filter-repo"):
            ask(f"'git {sub}' rewrites history for every commit", "confirm with the user; this cannot be undone once pushed")


def _check_pipelines(tokens) -> None:
    """Pipeline-shape rules: fetch-then-execute and env-dump-then-send."""
    groups: dict = {}
    for seg, gid in split_commands(tokens):
        cmd, _w = _strip_wrappers(seg)
        if cmd:
            groups.setdefault(gid, []).append(cmd)
    for stages in groups.values():
        names = [_cmd_name(s[0]) for s in stages]
        # What runs after each stage, and whether a secret was named up to it, in
        # one pass each way: rescanning the rest of the pipeline from every stage
        # was quadratic, and a long enough `a | a | … ; git reset --hard` ran
        # past the hook's timeout, which lets the command through unchecked.
        interp_after = [False] * len(names)
        sink_after = [False] * len(names)
        for i in range(len(names) - 2, -1, -1):
            interp_after[i] = interp_after[i + 1] or names[i + 1] in _INTERPRETERS
            sink_after[i] = sink_after[i + 1] or names[i + 1] in _NET_SINKS
        secret_so_far = False
        for i, nm in enumerate(names):
            secret_so_far = secret_so_far or any(_is_secret_token(t) for t in stages[i][1:])
            if nm in _FETCHERS and interp_after[i]:
                deny("downloading a script and piping it straight into a shell or interpreter runs "
                     "unreviewed remote code",
                     "download to a file, read it, then run it deliberately; or install through the package manager")
            if nm in _ENV_DUMPERS and sink_after[i]:
                deny("piping the environment (which holds every secret this shell can see) into a network tool "
                     "is exfiltration", "print the one variable you need, never the whole environment")
            if nm in ("cat", "grep", "awk", "base64", "xxd") and sink_after[i] and secret_so_far:
                deny("sending secret material to a network tool is exfiltration",
                     "never transmit credential files")


def _check_infra(seg, wrappers) -> None:
    name = _cmd_name(seg[0])
    args = seg[1:]
    joined = " ".join(args)
    if name == "docker":
        if re.search(r"\b(system|volume|image|container|network|builder)\s+prune\b", joined) or \
                re.search(r"\bvolume\s+rm\b", joined) or \
                (re.search(r"^(rm|rmi)\b", joined) and re.search(r"(^|\s)(-f|--force)\b", joined)) or \
                re.search(r"\bcompose\b.*\bdown\b.*(\s-v\b|--volumes\b)", joined):
            ask("this docker command deletes containers, images, or volumes (volumes hold databases)",
                "docker stop / docker compose down without -v, or confirm the data is disposable")
    elif name == "kubectl":
        if re.search(r"^(delete|drain)\b", joined) and (
                re.search(r"--all\b", joined) or re.search(r"\b(namespace|ns)\b", joined) or joined.startswith("drain")):
            ask("this kubectl command removes many resources or a whole namespace/node",
                "delete the one resource by name, and confirm the context is not production")
    elif name == "helm" and re.search(r"^(uninstall|delete)\b", joined):
        ask("'helm uninstall' removes a whole release", "confirm the release and cluster with the user")
    elif name in ("terraform", "tofu", "pulumi"):
        if re.search(r"^(destroy|down)\b", joined) or re.search(r"\b(-auto-approve|--auto-approve|--yes|-y)\b", joined):
            ask(f"'{name} {args[0] if args else ''}' changes or destroys real infrastructure without a review step",
                "run plan/preview first and let the user apply")
    elif name in ("aws", "gcloud", "az"):
        if re.search(r"\b(delete|rm|remove|terminate|destroy|purge)\b", joined) and (
                re.search(r"\b(--recursive|--force|--yes|-y|--quiet|-q)\b", joined) or "s3" in args[:1]):
            ask("this cloud CLI command deletes remote resources", "confirm the target with the user first")
    elif name in ("mkfs", "wipefs", "shred", "fdisk", "parted") or name.startswith("mkfs."):
        deny(f"'{name}' destroys a disk or partition", "the project never needs this")
    elif name == "dd" and any(a.startswith("of=/dev/") for a in args):
        deny("'dd' writing to a block device destroys it", "write to a file instead")
    elif name in ("shutdown", "reboot", "halt", "poweroff") or (name == "systemctl" and args[:1] in (["poweroff"], ["reboot"], ["halt"])):
        deny(f"'{name}' takes the machine down", "the project never needs this")
    elif name == "chmod":
        if any(a in ("777", "a+rwx", "o+rwx", "-R777") for a in args):
            deny("'chmod 777' makes files world-writable", "chmod 755 for executables, 644 for files, and only the ones that need it")
    elif name == "crontab" and any(a in ("-e", "-r") for a in args):
        ask("'crontab' edits or removes the user's scheduled jobs (persistence outside the project)",
            "propose the entry and let the user add it")
    elif name in ("npm", "pnpm", "yarn") and re.search(r"^config\s+set\s+registry\b", joined):
        deny("changing the package registry can redirect every install to an attacker-controlled source",
             "ask the user to change registry settings by hand")
    elif name == "pip" and re.search(r"^config\s+set\s+.*index-url", joined):
        deny("changing pip's index URL can redirect every install to an attacker-controlled source",
             "ask the user to change registry settings by hand")
    elif name in _DB_CLIENTS or name in ("mongo", "mongosh"):
        if name == "redis-cli" and any(_quoted(a).lower() in ("flushall", "flushdb") for a in args):
            ask("'redis-cli FLUSHALL/FLUSHDB' wipes every key", "delete the specific keys, or confirm the instance is disposable")


def _check_sql(cmd: str, tokens) -> None:
    """DROP/TRUNCATE/DELETE-without-WHERE, only when a DB client is in the command
    (an `echo`, a grep for the string, or a migration file edit is not a query)."""
    if not any(_cmd_name(t) in _DB_CLIENTS for t in tokens):
        return
    if _SQL_DROP_RE.search(cmd):
        ask("this command runs DROP or TRUNCATE against a database", "confirm the target database with the user; back it up first")
    if _SQL_DELETE_NO_WHERE_RE.search(cmd):
        ask("this command runs DELETE FROM without a WHERE clause (every row goes)", "add a WHERE clause, or confirm the wipe is intended")
    if _MONGO_DROP_RE.search(cmd):
        ask("this command drops a MongoDB database or collection", "confirm the target with the user")


def _check_redirect_targets(tokens) -> None:
    """`>`/`>>`/`tee` into package-manager config = registry redirection."""
    for i, tok in enumerate(tokens):
        if tok in (">", ">>") and i + 1 < len(tokens):
            t = _norm_path(tokens[i + 1])
            base = t.rsplit("/", 1)[-1]
            if base in _PKG_CONFIG_FILES and (base != "config.toml" or ".cargo" in t):
                deny(f"writing to '{t}' can redirect dependency resolution to another registry",
                     "ask the user to change package-manager config by hand")
    for seg, _gid in split_commands(tokens):
        cmd, _w = _strip_wrappers(seg)
        if cmd and _cmd_name(cmd[0]) == "tee":
            for a in cmd[1:]:
                if not a.startswith("-"):
                    base = _norm_path(a).rsplit("/", 1)[-1]
                    if base in _PKG_CONFIG_FILES and base != "config.toml":
                        deny(f"writing to '{a}' can redirect dependency resolution to another registry",
                             "ask the user to change package-manager config by hand")


def check_destructive(cmd: str, tokens) -> None:
    for seg, _gid in split_commands(tokens):
        real, wrappers = _strip_wrappers(seg)
        if not real:
            continue
        name = _cmd_name(real[0])
        if name == "rm":
            _check_rm(real, wrappers)
        elif name in ("kill", "pkill", "killall"):
            _check_kill(real)
        elif name == "find" and ("-delete" in real or ("-exec" in real and "rm" in real)):
            _check_find_delete(real)
        else:
            _check_infra(real, wrappers)
    _check_pipelines(tokens)
    _check_redirect_targets(tokens)
    _check_sql(cmd, tokens)
    if any(_cmd_name(t) == "git" for t in tokens):
        _check_git_destructive(tokens)


# ── Command walk: working directory and literal variables ─────────────────────
# The npx and deletion checks need to know where a simple command runs and what
# its `$VAR` arguments are. Agents write `S=/tmp/…/scratchpad; rm -rf $S/x` and
# `V=0.1.22; npx pkg@$V`, so a literal assignment earlier in the same command is
# followed, as is `cd <dir>`. Anything the walk cannot know (`$(…)`, a loop
# variable, an unset name) resolves to None and the caller treats it as unknown.
_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+?=")
_SHELL_KEYWORDS = {"do", "then", "else", "elif", "if", "while", "until", "!", "{", "}", "done", "fi"}
_DECLARERS = {"export", "local", "readonly", "declare", "typeset"}
_SINGLE_QUOTED_DOLLAR = False  # set by main(): the raw command has a $ inside '…'
_PREPPED = ""                  # set by main(): the command after shell_prepass
_SPLITS_OR_GLOBS = re.compile(r"[\s*?\[{]")
_VAR_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def _strip_assignments(seg):
    """Drop leading `NAME=value` words: `CI=1 npx vitest` runs npx."""
    i = 0
    while i < len(seg) and _ASSIGN_RE.match(seg[i]):
        i += 1
    return seg[i:]


def _has_single_quoted_dollar(cmd: str) -> bool:
    """True when a `$` sits inside single quotes, outside double quotes (a `'`
    inside "…" is literal and opens nothing)."""
    quote = None
    i = 0
    while i < len(cmd):
        c = cmd[i]
        if quote == "'":
            if c == "'":
                quote = None
            elif c == "$":
                return True
        elif c == "\\":
            i += 1  # escaped character, outside single quotes
        elif quote == '"':
            if c == '"':
                quote = None
        elif c in ("'", '"'):
            quote = c
        i += 1
    return False


def _expand(tok: str, env: dict):
    """`tok` with `$NAME`/`${NAME}` substituted from `env`, or None when any
    reference is unknown or the token holds a command substitution.

    shlex has already removed the quotes, so `'$S'` (literal in bash) and
    `"$S"` (expanded) look the same here. When the raw command single-quotes a
    `$` anywhere, every `$` token is treated as unknowable."""
    if "`" in tok or "$(" in tok:
        return None
    if "$" in tok and _SINGLE_QUOTED_DOLLAR:
        return None
    unknown = []

    def sub(m):
        val = env.get(m.group(1) or m.group(2))
        # Unquoted, bash would word-split or glob a value with spaces or
        # wildcards; shlex has dropped the quotes, so either way it's unknown.
        if val is None or _SPLITS_OR_GLOBS.search(val):
            unknown.append(m.group(0))
            return ""
        return val
    out = _VAR_REF_RE.sub(sub, tok)
    if unknown or "$" in out:  # $1, $@, $? and friends stay unknown too
        return None
    return out


def _track_cd(cwd, args):
    """Directory after `cd <args>` (already expanded), or None when it cannot be
    known statically (`cd -`, a path that does not exist)."""
    operands = [a for a in args if a not in ("-L", "-P", "-e", "-@", "--")]
    if len(operands) != 1 or operands[0] is None or operands[0] == "-":
        return None
    target = os.path.expanduser(operands[0])
    if not os.path.isabs(target):
        if not cwd:
            return None
        target = os.path.join(cwd, target)
    raw = target
    target = os.path.normpath(target)
    if ".." in raw.split(os.sep) and os.path.realpath(target) != os.path.realpath(raw):
        return None  # `link/..`: logical (default) and physical (set -P) cd disagree
    return target if os.path.isdir(target) else None


def _scoped_segments(tokens):
    """('cmd', words) for each simple command and ('op', token) for each shell
    operator between them, in order."""
    cur: list = []
    for tok in tokens:
        if _is_op(tok):
            if cur:
                yield "cmd", cur
                cur = []
            yield "op", tok
        else:
            cur.append(tok)
    if cur:
        yield "cmd", cur


_OPENERS = {"if", "while", "until", "for", "select", "case", "{"}
_CLOSERS = {"fi", "done", "esac", "}"}
_CWD = "\0cwd"     # the working directory rides in env, so it obeys the same rules
_DIRS = "\0dirs"   # pushd/popd stack (a tuple, so subshell copies don't share it)
# Commands that can set or clear variables (or the directory) in ways the walk
# does not model. After one of these, every variable and the cwd are unknown.
_OPAQUE_CMDS = {"eval", "source", ".", "shopt", "let", "exec", "trap", "alias", "enable",
                "declare", "typeset", "local", "readonly", "export", "builtin"}
# Assign only the names they are given (read E PW, unset X, printf -v X …).
_NAMED_ASSIGNERS = {"read", "mapfile", "readarray", "getopts", "unset"}
_READ_VALUE_OPTS = {"-a", "-d", "-i", "-n", "-N", "-p", "-t", "-u", "-C", "-c", "-O", "-s"}


def _named_targets(cmd) -> set:
    """The variable names a read/mapfile/getopts/unset/printf -v call sets."""
    name = _cmd_name(cmd[0])
    out: set = set()
    args = cmd[1:]
    if name == "printf":
        if "-v" in args and args.index("-v") + 1 < len(args):
            out.add(args[args.index("-v") + 1])
        return out
    k = 0
    while k < len(args):
        if args[k] in _READ_VALUE_OPTS:
            if args[k] == "-a" and k + 1 < len(args):
                out.add(args[k + 1])
            k += 2
            continue
        if not args[k].startswith("-"):
            out.add(args[k])
        k += 1
    if name == "read" and not out:
        out.add("REPLY")
    return out
def _defined_functions(tokens) -> set:
    """Names defined as shell functions, read from the token structure (a
    bare word followed by the `()` operator, or `function name`), so `f()`
    inside a quoted python -c string is not a definition."""
    out = set()
    for i, t in enumerate(tokens or ()):
        if _is_op(t):
            continue
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        prev = tokens[i - 1] if i > 0 else ""
        if nxt.startswith("()") and (not prev or _is_op(prev) or prev in ("{", "}")):
            out.add(t)
        elif t == "function" and nxt and not _is_op(nxt) and (not prev or _is_op(prev)):
            out.add(nxt)
    return out


def walk_commands(tokens):
    """Yield (simple command, cwd or None, variables) in execution order.

    A variable or the working directory counts as known only where bash is
    certain to have set it before the command runs:
      - not in a pipeline element or a backgrounded command (both subshells);
      - inside an if/case/brace body, only for the rest of that body (it
        may not run), and never in a loop body (each pass starts where the
        last one left off);
      - after `&&`/`||` only within the rest of the same and-or list
        (`false && S=x; rm $S` runs rm with S unset), and never after `||`;
      - `( … )` and `$( … )` scope both;
      - a command the walk does not model (eval, source, read, printf -v,
        unset, builtin, …) makes everything unknown from there on, and a
        function definition anywhere makes everything unknown from the start,
        since the walk cannot tell when its body runs.
    """
    env = {"HOME": os.path.expanduser("~"), _CWD: _PAYLOAD_CWD or os.getcwd(), _DIRS: ()}
    if os.environ.get("TMPDIR"):
        env["TMPDIR"] = os.environ["TMPDIR"]
    # A function's body is walked where it is defined, as a body that may not
    # run; what it can set (outside its own subshells) is recorded, and a call
    # forgets exactly that. A body that runs something the walk does not model
    # (eval, another function …) makes its calls forget everything.
    functions = _defined_functions(tokens)
    effects: dict = {}     # function name -> names its body sets, or None for "anything"
    opaque_fns: set = set()  # functions whose body runs something the walk does not model
    defining = None        # name whose body the next `{` / `(` opens
    pending_def = None     # (name, defined conditionally?, previous effects or "absent")
    stack: list = []
    bodies: list = []   # open if/case/brace/loop bodies: [kind, names set inside]
    list_or = list_and = after_pipe = False
    list_assigns_only = True   # everything so far in this and-or list was a plain assignment
    conditional: set = set()
    last_set: list = []

    def forget_all():
        for k in list(env):
            if k != _DIRS:
                env[k] = None
        env[_DIRS] = ()

    def in_function_body():
        return next((b[2] for b in reversed(bodies) if len(b) > 2), None)

    def call(fname):
        names = effects.get(fname)
        if names is None:
            forget_all()
        else:
            for n in names:
                env[n] = None

    segments = list(_scoped_segments(tokens))
    for i, (kind, seg) in enumerate(segments):
        if kind == "op":
            if defining is not None and seg != "()":
                defining = None  # `f() ( … )` or similar: no brace body to learn from; calls stay opaque
            for ch in seg:
                if ch == "(":
                    stack.append((dict(env), [[b[0], set(b[1])] + b[2:] for b in bodies],
                                  dict(effects), set(opaque_fns)))
                elif ch == ")" and stack:
                    env, bodies, effects, opaque_fns = stack.pop()  # a subshell's definitions die with it
            op = seg.strip("()")
            if op in ("|", "|&", "&"):
                for name in last_set:
                    env[name] = None   # pipeline element / background job: a subshell
            after_pipe = op in ("|", "|&")
            if op == "&&":
                list_and = True
            elif op == "||":
                list_or = True
                for name in conditional | set(last_set):
                    env[name] = None
            elif op in (";", "\n", "&", ";;", ";&", ";;&") or not op:
                for name in conditional:
                    env[name] = None
                conditional, list_and, list_or = set(), False, False
                list_assigns_only = True
            last_set = []
            continue
        last_set = []
        words = list(seg)
        nxt = segments[i + 1] if i + 1 < len(segments) else ("op", "")
        if (len(words) == 1 and nxt[0] == "op" and nxt[1].startswith("()")) or words[:1] == ["function"]:
            defining = words[-1]  # `name()` / `function name`: a definition, not a call
            # Defined conditionally (inside a body, after &&/||, in a pipeline):
            # a later call may reach this body or the previous one, so the two
            # are merged when the body closes. (Subshell definitions are undone
            # by the subshell snapshot.)
            pending_def = (defining, bool(bodies or list_and or list_or or after_pipe),
                           effects[defining] if defining in effects else "absent")
            effects[defining] = None   # until its body has been walked
            continue
        while words and words[0] in _SHELL_KEYWORDS | _OPENERS | _CLOSERS:
            w = words[0]
            if w in _OPENERS:
                bodies.append([w, set(), defining, pending_def] if defining and w == "{" else [w, set()])
                defining = None
            elif w in _CLOSERS and bodies:
                body = bodies.pop()
                for name in body[1]:
                    env[name] = None
                if bodies:
                    bodies[-1][1] |= body[1]   # what a nested body may set, its parent may too
                if len(body) > 2:
                    fname, conditional_def, prev = body[3]
                    new = None if fname in opaque_fns else set(body[1])
                    if conditional_def and prev != "absent":
                        new = None if new is None or prev is None else new | prev
                    effects[fname] = new
            elif w in ("else", "elif") and bodies:
                for name in bodies[-1][1]:
                    env[name] = None   # the other branch: what `then` set never happened
                bodies[-1][1] = set()
            words = words[1:]
        if words and words[0] in ("for", "select", "case"):
            bodies.append([words[0], set()])
        if not words:
            continue
        in_loop = any(b[0] in ("while", "until", "for", "select", "case") for b in bodies)
        certain = not after_pipe and not list_or and not in_loop

        def assign(name, value):
            env[name] = value if certain else None
            if list_and and not list_assigns_only:
                conditional.add(name)   # `S=x && T=y` cannot fail, so T stays set after the list
            if bodies:
                bodies[-1][1].add(name)
            last_set.append(name)

        env["PWD"] = env[_CWD]
        body = words[1:] if words[0] in _DECLARERS else words
        if body and all(_ASSIGN_RE.match(t) for t in body):
            for t in body:
                name, val = t.split("=", 1)
                assign(name.rstrip("+"), _expand(val, env))
            continue
        list_assigns_only = False
        cmd, _w = _strip_wrappers(_strip_assignments(words))
        cmd = _strip_assignments(cmd)
        if not cmd:
            continue
        name = _cmd_name(cmd[0])
        fn = in_function_body()
        if name in _NAMED_ASSIGNERS or (name == "printf" and "-v" in cmd):
            for n in _named_targets(cmd):
                env[n] = None
                last_set.append(n)
                if bodies:
                    bodies[-1][1].add(n)
            yield cmd, env[_CWD], env
            continue
        if name in ("export", "readonly") and all(re.match(r"^[A-Za-z_]\w*$", a) for a in cmd[1:]):
            yield cmd, env[_CWD], env   # `export NAME`: marks it, changes nothing
            continue
        if name in _OPAQUE_CMDS or cmd[0] in functions:
            if fn is not None:
                opaque_fns.add(fn)   # walked, not run: remember that a call can do anything
                yield cmd, env[_CWD], env
                continue
            if cmd[0] in functions and name not in _OPAQUE_CMDS:
                call(cmd[0])
                yield cmd, env[_CWD], env
                continue
            forget_all()
            yield cmd, None, env
            continue
        if name in ("cd", "pushd", "popd"):
            cwd = env[_CWD]
            # also a `CDPATH=… cd x` prefix, which _strip_assignments removed above
            relative_cdpath = ("CDPATH" in env or os.environ.get("CDPATH")
                               or any(t.startswith(("CDPATH=", "CDPATH+=")) for t in words))
            if name == "popd":
                dirs = env[_DIRS]
                assign(_CWD, dirs[-1] if dirs else None)
                env[_DIRS] = dirs[:-1]
            else:
                args = [_expand(a, env) for a in cmd[1:]]
                plain = [a for a in args if a not in ("-L", "-P", "-e", "-@", "--")]
                if relative_cdpath and plain and plain[0] and not plain[0].startswith(("/", "./", "../", "~")):
                    new = None   # CDPATH may send a relative cd somewhere else
                else:
                    new = _track_cd(cwd, args)
                if name == "pushd":
                    env[_DIRS] = env[_DIRS] + (cwd,) if certain else ()
                assign(_CWD, new)
            continue
        if name in ("for", "select") and len(cmd) > 1:
            env[cmd[1]] = None  # loop variable: a different value each pass
        yield cmd, env[_CWD], env


# ── npx guard: ask only when it would download ────────────────────────────────
# `npx <bin>` runs the project's own dev dependency when one is installed, and
# silently downloads and executes a registry package when it is not. Only the
# second is an install, so only that is handed to the user. Resolution follows
# npm's: the nearest node_modules walking up from the command's directory, which
# the guard stops at the repository root so a stray ~/node_modules never vouches
# for a package. A pinned exact version (`pkg@1.2.3`) that is already in npx's
# own cache (~/.npm/_npx) runs from there without a download, so it passes too;
# an unpinned name or a tag (`@latest`) may fetch a newer release, so it asks.
_NPX_NEVER_INSTALL = {"--no-install", "--no"}
# Options that move npx to another project or registry: the guard cannot tell
# what is installed there, so the user decides.
_NPX_OPAQUE = ("--prefix", "-w", "--workspace", "--workspaces", "-ws", "--registry")
# Command position only (start, or right after an operator), so prose that
# mentions npx inside an unparseable heredoc does not raise a prompt.
_NPX_UNPARSEABLE_RE = re.compile(r"(?:^|[;&|(\n])[ \t]*(?:[A-Za-z_]\w*=\S*[ \t]+)*(?:\S*/)?npx(?:\s|$)")
_EXACT_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?$")
_NPX_SUGGESTION = ("add it to the project's devDependencies first (that install is the step to approve), "
                   "pin an exact version that is already cached, or run the installed binary with "
                   "'npx --no-install <bin>', which never downloads")


def _split_spec(spec: str):
    """'@scope/pkg@1.2.3' -> ('@scope/pkg', '1.2.3'); 'pkg' -> ('pkg', None)."""
    at = spec.find("@", 1)
    return (spec, None) if at < 0 else (spec[:at], spec[at + 1:])


def _version_at(package_json: str) -> str:
    try:
        with open(package_json, encoding="utf-8") as fh:
            return str(json.load(fh).get("version") or "")
    except (OSError, ValueError, AttributeError):
        return ""


_NPM_NAME_RE = re.compile(r"^(?:@[a-z0-9][a-z0-9._~-]*/)?[a-z0-9][a-z0-9._~-]*$")


def _npx_cached(name: str, version: str) -> bool:
    """True when npx's own cache already holds exactly name@version."""
    import glob
    cache = os.environ.get("npm_config_cache") or os.path.join(os.path.expanduser("~"), ".npm")
    pattern = os.path.join(glob.escape(cache), "_npx", "*", "node_modules", name, "package.json")
    return any(_version_at(p) == version for p in glob.glob(pattern))


def _npx_installed(spec: str, cwd: str) -> bool:
    """True when `npx <spec>` resolves to something already on disk."""
    if spec.startswith(("./", "../", "/", "~/")):
        return os.path.exists(os.path.join(cwd, os.path.expanduser(spec)))
    name, version = _split_spec(spec)
    scoped = name.startswith("@")
    if not _NPM_NAME_RE.match(name):
        return False  # not a valid package name: nothing local or cached can vouch for it
    if ":" in spec or ("/" in name and not scoped) or (version is not None and not _EXACT_VERSION_RE.match(version)):
        return False  # tag (@latest), range, URL, git or github shorthand: npx may fetch it
    home = os.path.expanduser("~")
    d = cwd
    while True:
        if d == home and d != cwd:
            break
        modules = os.path.join(d, "node_modules")
        manifest = os.path.join(modules, name, "package.json")
        if version is None:
            if os.path.exists(manifest) or (not scoped and os.path.exists(os.path.join(modules, ".bin", name))):
                return True
        elif _version_at(manifest) == version:
            return True
        parent = os.path.dirname(d)
        if parent == d or os.path.exists(os.path.join(d, ".git")):
            break
        d = parent
    return version is not None and _npx_cached(name, version)


def _check_npx(cmd, cwd, env) -> None:
    args = cmd[1:]
    packages, target, call = [], None, False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            target = args[i + 1] if i + 1 < len(args) else None
            break
        if a in _NPX_NEVER_INSTALL:
            return
        if a in _NPX_OPAQUE or a.startswith(tuple(o + "=" for o in _NPX_OPAQUE)):
            ask(f"'npx {a}' runs against another project or registry, so bash-guard cannot confirm "
                "the package is already installed there", _NPX_SUGGESTION)
        if a in ("-p", "--package"):
            if i + 1 < len(args):
                packages.append(args[i + 1])
            i += 2
            continue
        if a.startswith("--package="):
            packages.append(a.split("=", 1)[1])
        elif a in ("-c", "--call"):
            call = True
            i += 2
            continue
        elif a.startswith("--call="):
            call = True
        elif not a.startswith("-"):
            target = a
            break
        i += 1
    # With -p the packages are what gets installed; the positional is only the
    # command to run from them. `npx -c '<cmd>'` alone installs nothing.
    specs = packages or ([] if call or target is None else [target])
    if not specs:
        return
    if not cwd:
        ask(f"bash-guard cannot tell which directory 'npx {specs[0]}' runs in (the cd before it is not a "
            "literal, existing path), so it cannot confirm the package is installed there rather than "
            "downloaded", "cd to a literal path, or use 'npx --no-install <bin>', which never downloads")
    for raw in specs:
        spec = _expand(raw, env)
        if spec is None:
            ask(f"bash-guard cannot resolve 'npx {raw}' (a variable it cannot see the value of), so it "
                "cannot confirm that exact package is already on disk", "write the version literally, or "
                "assign it earlier in the same command (V=1.2.3; npx pkg@$V)")
        if not _npx_installed(spec, cwd):
            ask(f"'npx {spec}' would download and run a package that is not installed in this project "
                f"(nothing for it in node_modules from {cwd} up to the repository root, nor that exact "
                "version in the npx cache)", _NPX_SUGGESTION)


def check_npx(tokens) -> None:
    for cmd, cwd, env in walk_commands(tokens):
        if _cmd_name(cmd[0]) == "npx":
            _check_npx(cmd, cwd, env)


# ── gh api guard: ask before a request that can change GitHub ─────────────────
# settings.json allows `Bash(gh api *)` so reads run without a prompt; the same
# rule lets `gh api -X DELETE repos/o/r` through, so a call this guard cannot
# read as a read asks. gh parses flags with spf13/pflag and so does
# _gh_api_parse: options anywhere before `--` (after the endpoint too), the last
# -X/--method wins, short options cluster (`-iXPOST`) and take a value attached
# (`-XPOST`, `-X=POST`) or as the next word, long ones `--method=POST` or
# `--method POST`, never abbreviated (pflag has no prefix matching: `--meth` is
# an error gh stops on, and an option this guard does not know asks). A read:
#   - no --input, no -F value read from a file or stdin (`@path`, `@-`), no
#     header naming a method (X-HTTP-Method-Override and the like);
#   - -X/--method, when given, is GET (any case);
#   - -f/-F fields only with an explicit GET, where gh sends them as the query
#     string, not a body (`gh api -X GET search/issues -f q=…` is the documented
#     search form), or on the GraphQL endpoint (`graphql`, `/graphql`, a URL
#     whose path is /graphql), where reads are POSTs too;
#   - every `query` field is written out in the command with no `$` or
#     backtick at all, and has no `mutation` keyword (a query with GraphQL
#     variables asks: inline the values). GraphQL names have no escapes and
#     the `{ … }` shorthand is always a query, so a mutation cannot run without
#     that word in the text (the word inside a string or comment asks too:
#     nothing here parses GraphQL).
# The call is found where npx is (walk_commands) and also, as the deletion and
# cc-dash checks do, in commands handed to xargs / find -exec / setsid, in
# $( … ) and backticks inside quoted words, and, when the command runs a
# shell, eval, xargs, env -S or ssh, in any string that names gh api (sh -c,
# echo … | sh, bash <<< …).
# `gh api *` is allowlisted, so this guard is the only barrier, and unlike the
# rest of this file it fails closed on shell expansion. It reads its own
# tokens (_gh_mark): every character bash takes literally (inside quotes, or
# escaped) is replaced by a private-use marker before shlex runs, so a quoted
# '>' or '|' is a word, never a redirection or a pipe, an fd number counts
# only when it touches its operator (`2>&1`, not `-q 1 >x`), and a `$` or
# backtick still in a word is one bash expands. Every word of a gh api call
# must be literal: any expansion asks, in double quotes too, and no variable
# is ever resolved (a harmless-looking value still splits on a changed IFS,
# into arguments such as -X DELETE); so does an unquoted glob or brace
# expansion, a process substitution, arithmetic, a parenthesis after a word, a
# $( … ) with more words after it, any mention of IFS, and quoting shlex
# cannot follow ($'…', quotes inside a "$( … )"). In a command line that
# mentions gh api (the word api, after gh or after anything bash builds), a
# command whose name, or a wrapper's target, bash builds at run time (`$G api`,
# `` `echo gh` api ``, `/usr/bin/g? api`, `$HOME/bin/gh`) asks: it could be gh.
_GH_API_SHORT = {"X": "method", "F": "field", "f": "raw", "H": "header", "q": "", "t": "", "p": ""}
_GH_API_SHORT_BOOL = {"i", "h"}
_GH_API_LONG = {"method": "method", "field": "field", "raw-field": "raw", "header": "header", "input": "input",
                "jq": "", "template": "", "preview": "", "cache": "", "hostname": "hostname"}
_GH_API_LONG_BOOL = {"include", "paginate", "slurp", "silent", "verbose", "help"}
_GH_ROOT_BOOLS = {"-h", "--help", "--version"}
_GRAPHQL_MUTATION_RE = re.compile(r"(?<![_0-9A-Za-z])mutation(?![_0-9A-Za-z])")
_REDIRECT_OP_RE = re.compile(r"^(?:[<>]|>>|>\||[<>]&|&>>?|<>|<<-?|<<<)$")
# _gh_mark's markers: a character bash reads literally becomes chr(_GH_PUA + its
# code); _GH_FD prefixes an fd number glued to its redirection operator.
_GH_LITERAL = set("$`<>|&;(){}*?[]\n\"'")
_GH_PUA = 0xE000
_GH_FD = ""
_GH_PUA_RE = re.compile("[-]")
_GH_WORD_END = set(" \t\n;&|()<>")
_GH_RESERVED = {"if", "then", "else", "elif", "while", "until", "do", "in", "time", "!", "{"}
_CODE_READERS = _SHELLS | {"eval", "source", ".", "xargs", "parallel", "script", "watch", "flock", "su",
                           "runuser", "ssh", "env", "trap"}
_GH_API_UNPARSEABLE_RE = re.compile(
    r"(?:^|[;&|(\n])[ \t]*(?:[A-Za-z_]\w*=\S*[ \t]+)*(?:\S*/)?gh[ \t]+(?:-\S*[ \t]+)*api(?:\s|$)")
_GH_API_SUGGESTION = ("confirm the request with the user. Reads run without approval: `gh api <endpoint>` "
                      "(GET), `gh api -X GET <endpoint> -f key=value` (query parameters), and `gh api graphql "
                      "-f query='…'` with the query written out, values inline (no $ variables) and no mutation")


def _gh_lit(c: str) -> str:
    return chr(_GH_PUA + ord(c)) if c in _GH_LITERAL else c


def _gh_plain(s: str) -> str:
    """Text with _gh_mark's markers turned back into the characters they stand for."""
    return _GH_PUA_RE.sub(lambda m: "" if m.group(0) == _GH_FD else chr(ord(m.group(0)) - _GH_PUA), s)


def _gh_mark(s: str):
    """(text, odd) for a shell_prepass()ed command: `s` with every character
    bash reads literally (in quotes, or escaped) that shlex or this guard would
    read as shell syntax replaced by a marker, and an fd number that touches its
    redirection operator (`2>`, not `2 >`) prefixed with _GH_FD. `odd` names
    quoting shlex does not split the way bash does ('' when there is none)."""
    out: list = []
    odd = "private-use characters" if _GH_PUA_RE.search(s) else ""
    stack = ["top"]
    i, n = 0, len(s)
    while i < n:
        c, ctx = s[i], stack[-1]
        if ctx in ("'", "$'"):
            if ctx == "$'" and c == "\\" and i + 1 < n:
                out.append("\\" + _gh_lit(s[i + 1]))
                i += 2
                continue
            if c == "'":
                stack.pop()
                out.append(c)
            else:
                out.append(_gh_lit(c))
            i += 1
            continue
        if ctx == '"':
            if c == "\\" and i + 1 < n:
                nxt = s[i + 1]
                out.append("\\" + (nxt if nxt in '"\\' else _gh_lit(nxt)))
                i += 2
                continue
            if c == '"':
                stack.pop()
                out.append(c)
            elif c == "$" and s[i + 1:i + 2] == "(" and s[i + 2:i + 3] != "(":
                stack.append("sub")
                out.append("$(")
                i += 2
                continue
            elif c == "`":
                stack.append("bq")
                out.append(c)
            elif c == "$":
                out.append(c)
            else:
                out.append(_gh_lit(c))
            i += 1
            continue
        # unquoted: the top level, or inside $( … ) / ( … ) / backticks
        if c == "\\" and i + 1 < n:
            out.append("\\" + _gh_lit(s[i + 1]))
            i += 2
            continue
        if c in ("'", '"'):
            if '"' in stack:
                odd = odd or 'quotes inside a "$( … )" or "` … `"'
            stack.append(c)
            out.append(c)
            i += 1
            continue
        if c == "$" and s[i + 1:i + 2] == "'":
            odd = odd or "ANSI-C quoting ($'…')"
            stack.append("$'")
            out.append("$'")
            i += 2
            continue
        if c == "$" and s[i + 1:i + 2] == "(" and s[i + 2:i + 3] != "(":
            stack.append("sub")
            out.append("$(")
            i += 2
            continue
        if c == "`":
            if ctx == "bq":
                stack.pop()
            else:
                stack.append("bq")
        elif c == "(" and ctx in ("sub", "par"):
            stack.append("par")
        elif c == ")" and ctx in ("sub", "par"):
            stack.pop()
        elif c.isdigit() and (not out or out[-1][-1:] in _GH_WORD_END):
            j = i
            while j < n and s[j].isdigit():
                j += 1
            if j < n and s[j] in "<>":
                out.append(_GH_FD + s[i:j])   # `2>&1`: the fd number belongs to the operator
                i = j
                continue
        out.append(c)
        i += 1
    return "".join(out), odd


def _without_redirects(tokens):
    """_gh_mark tokens minus redirections and their targets (`2>&1`, `> out.json`,
    `<<< text`), so the words after one stay in their command: bash runs
    `gh api 2>/dev/null x -X DELETE` as one call. Quoted '>' was marked, so it
    stays a word; an fd number goes only when _gh_mark glued it to its operator."""
    out, k = [], 0
    while k < len(tokens):
        if tokens[k].startswith(_GH_FD):
            k += 1          # the fd number of `2>&1`
            continue
        if _REDIRECT_OP_RE.match(tokens[k]):
            k += 2
            continue
        out.append(tokens[k])
        k += 1
    return out


def _gh_stream_problem(tokens):
    """Why `tokens` (_gh_mark tokens, redirections removed) hold an operator that
    does not end a simple command, so words after it may still be arguments of
    a gh api call; else None."""
    subst: list = []    # for each open parenthesis: does it open a $( … )?
    for k, t in enumerate(tokens):
        if not _is_op(t):
            continue
        for ci, ch in enumerate(t):
            if ch == "(":
                subst.append(ci == 0 and k > 0 and not _is_op(tokens[k - 1]) and tokens[k - 1].endswith("$"))
            elif ch == ")":
                closes_subst = subst.pop() if subst else False
                if closes_subst and ci == len(t) - 1 and k + 1 < len(tokens) and not _is_op(tokens[k + 1]):
                    # shlex split `$(echo gh) api …` apart: the words after `)` belong to its command
                    return ("has a command substitution $( … ) with more words of the same command after it, "
                            "which bash-guard cannot place")
        if "<" in t or ">" in t:
            return f"has '{t}' (a process substitution or an operator bash-guard cannot place in the call)"
        if "((" in t:
            return "has arithmetic (( … )), which bash-guard cannot place in the call"
        prev = tokens[k - 1] if k else ""
        if (t.startswith("(") and not t.startswith("()") and prev and not _is_op(prev)
                and not prev.endswith("$") and prev not in _GH_RESERVED):
            return f"has a parenthesis after '{_gh_plain(prev)[:30]}' (an extended glob or an array), which bash-guard cannot place in the call"
    return None


def _gh_expands(tok: str) -> str:
    """What in `tok` (a _gh_mark token) bash may expand into other words: a `$`
    or backtick (variables, substitutions, in double quotes too), a glob or a
    brace expansion; '' when it is a literal word. No value is ever resolved:
    a variable that looks harmless can still split on a changed IFS."""
    if "$" in tok or "`" in tok:
        return "a shell expansion"
    if re.search(r"[*?\[]", tok):
        return "an unquoted glob"
    if "{" in tok and "}" in tok and ("," in tok or ".." in tok):
        return "an unquoted brace expansion"
    return ""


def _gh_api_args(cmd):
    """The arguments of a `gh api` call, or None. Options before `api` are
    passed on to it, as cobra does (it takes an option it does not know to
    have a value)."""
    args = cmd[1:]
    k = 0
    while k < len(args):
        a = args[k]
        if a == "--" or a == "-" or not a.startswith("-"):
            return args[:k] + args[k + 1:] if a == "api" else None
        k += 2 if "=" not in a and a not in _GH_ROOT_BOOLS and (a.startswith("--") or len(a) == 2) else 1
    return None


def _gh_api_parse(words):
    """({kind: [values]}, positionals, first option gh does not have or None),
    reading `words` as pflag does (see the comment above)."""
    seen = {"method": [], "field": [], "raw": [], "header": [], "input": [], "hostname": []}
    pos, k = [], 0
    while k < len(words):
        a = words[k]
        k += 1
        if a == "--":
            pos += words[k:]
            break
        if a.startswith("--"):
            name, eq, val = a[2:].partition("=")
            if name in _GH_API_LONG_BOOL:
                continue
            if name not in _GH_API_LONG:
                return seen, pos, a
            if not eq:
                if k >= len(words):
                    break   # "flag needs an argument": gh stops there
                val, k = words[k], k + 1
            if _GH_API_LONG[name]:
                seen[_GH_API_LONG[name]].append(val)
        elif a.startswith("-") and len(a) > 1:
            for j in range(1, len(a)):
                c, rest = a[j], a[j + 1:]
                if c in _GH_API_SHORT_BOOL:
                    if len(rest) > 1 and rest[0] == "=":
                        break   # -i=true
                    continue
                if c not in _GH_API_SHORT:
                    return seen, pos, "-" + c
                if rest:
                    val = rest[1:] if len(rest) > 1 and rest[0] == "=" else rest
                elif k < len(words):
                    val, k = words[k], k + 1
                else:
                    break
                if _GH_API_SHORT[c]:
                    seen[_GH_API_SHORT[c]].append(val)
                break
        else:
            pos.append(a)
    return seen, pos, None


def _graphql_endpoint(ep: str) -> bool:
    """True when gh sends `ep` to GitHub's GraphQL endpoint: `graphql`,
    `/graphql`, or a URL whose path is /graphql (GHES: /api/graphql), with dot
    segments and %-escapes resolved."""
    from urllib.parse import unquote
    path = ep.split("?", 1)[0].split("#", 1)[0]
    if "://" in path:
        path = path.split("://", 1)[1].partition("/")[2]
    segs: list = []
    for s in unquote(path).lower().split("/"):
        if s == "..":
            segs = segs[:-1]
        elif s not in ("", "."):
            segs.append(s)
    return segs in (["graphql"], ["api", "graphql"])


def _graphql_query_problem(field: str):
    """Why a -f/-F field may carry a GraphQL query that is not a plain read, or
    None. `field` is plain text: what bash expands was settled before."""
    key, eq, value = field.partition("=")
    if key.split("[", 1)[0] != "query" or not eq:
        return None
    if key != "query":
        return f"passes its GraphQL query as '{key}'"
    if "$" in value or "`" in value:
        cut = min(i for i in (value.find("$"), value.find("`")) if i >= 0)
        return (f"has a $ or backtick in its GraphQL query ('{value[cut:cut + 24]}'); write the values inline "
                "instead of GraphQL variables")
    if _GRAPHQL_MUTATION_RE.search(value):
        return "runs a GraphQL mutation"
    return None


def _gh_api_write(args):
    """Why this `gh api` call may change something on GitHub, or None for a
    read. `args` are _gh_mark tokens; every one must be a literal word."""
    from urllib.parse import unquote_plus
    words = []
    for a in args:
        what = _gh_expands(a)
        if what:
            return (f"has {what} in an argument ('{_gh_plain(a)[:40]}'), which bash can turn into other or more "
                    "arguments, such as -X DELETE; bash-guard reads only literal words in a gh api call")
        words.append(_gh_plain(a))
    seen, pos, unknown = _gh_api_parse(words)
    if unknown:
        return f"passes an option bash-guard does not know ('{unknown}'), so it cannot tell what the request does"
    if seen["hostname"]:
        return f"sends the request to another host (--hostname {seen['hostname'][-1][:40]}), not github.com"
    # gh takes an endpoint with :// anywhere in it as the whole URL, whatever its host
    url = next((p for p in pos if "://" in p and not p.startswith("https://api.github.com/")), None)
    if url:
        return f"sends the request to a full URL ('{url[:40]}'), which can name any host"
    if seen["input"]:
        return "sends a request body with --input"
    for f in seen["field"]:
        if f.partition("=")[2].startswith("@"):
            return f"reads the field '{f.partition('=')[0]}' from a file or stdin (-F …=@), so bash-guard cannot see what it sends"
    for h in seen["header"]:
        if "method" in h.split(":", 1)[0].lower():
            return f"sets the header '{h[:60]}', which can override the request method"
    method = seen["method"][-1] if seen["method"] else None
    if method is not None and method.upper() != "GET":
        return f"sets the request method to '{method}'"
    graphql = [p for p in pos if _graphql_endpoint(p)]
    for p in graphql:
        qs = p.partition("?")[2]
        if "$" in qs or "`" in qs or _GRAPHQL_MUTATION_RE.search(unquote_plus(qs)):
            return "puts a GraphQL query in the URL that bash-guard cannot read as a plain query"
    fields = seen["field"] + seen["raw"]
    if not fields:
        return None
    if method is None and not graphql:
        return "sends -f/-F fields, which gh turns into a POST body unless --method GET is given"
    for f in fields:
        why = _graphql_query_problem(f)
        if why:
            return why
    if method is None and not any(f.partition("=")[0] == "query" for f in fields):
        return "posts fields to the GraphQL endpoint with no query written out"
    return None


def _gh_api_cmd(cmd, depth=0):
    """(command, why) for a gh api call in `cmd`, or in a command it hands a
    runner (xargs, find -exec, setsid …), that may not be a read; else None.
    Only called for a command line that mentions gh api (_mentions_gh_api), so
    a command whose name (or a wrapper's target) bash builds at run time
    (`$G api …`, `` `echo gh` api ``, `/usr/bin/g? api`, `$HOME/bin/gh`)
    asks: nothing here can tell it is not gh."""
    shown = _gh_plain(" ".join(cmd))
    inner = _strip_assignments(_strip_wrappers(_strip_assignments(cmd))[0]) or cmd
    for name in dict.fromkeys((cmd[0], inner[0])):
        what = _gh_expands(name)
        if what:
            return shown, (f"runs a command whose name has {what} ('{_gh_plain(name)[:30]}') in a command line that "
                           "mentions gh api, so bash-guard cannot tell it is not gh api")
    if _cmd_name(_gh_plain(inner[0])) == "gh":
        args = _gh_api_args(inner)
        why = _gh_api_write(args) if args is not None else None
        return (shown, why) if why else None
    # find ends an -exec at the word `;` however it was quoted (`\;`, ';'); _gh_mark marked it.
    semi = _gh_lit(";")
    for handed in _handed_commands([";" if t == semi else t for t in inner]) if depth < 6 else ():
        hit = _gh_api_cmd(handed, depth + 1) if handed else None
        if hit:
            return hit
    return None


_GH_WORD_RE = re.compile(r"(?<![\w.-])gh(?![\w.-])")
_API_WORD_RE = re.compile(r"(?<![\w.-])api(?![\w.-])")
_IFS_RE = re.compile(r"(?<![A-Za-z0-9_])IFS(?![A-Za-z0-9_])")
# What picks gh's host or its config (hosts.yml names the default host and its token):
# any mention of GH_HOST, GH_CONFIG_DIR or XDG_CONFIG_HOME, and of HOME except a
# plain read ($HOME, ${HOME}): HOME=, HOME+=, export/read/declare/printf -v HOME,
# `for HOME in`, ${HOME:=…} all count, since no assignment form is modelled.
# Builtins and loops that assign a variable whose name may be built at run time
# (`n=HOME; declare "$n=/x"`, `read "$v"`, `for "$v" in`): in a command line that
# mentions gh api, any of them asks (printf only with -v).
_GH_NAME_SETTERS = {"declare", "typeset", "local", "export", "readonly", "read", "mapfile", "readarray", "unset",
                    "eval", "source", ".", "let", "getopts", "for", "select"}
_GH_HOST_ENV_RE = re.compile(r"(?<![A-Za-z0-9_])(?:GH_HOST|GH_CONFIG_DIR|XDG_CONFIG_HOME)(?![A-Za-z0-9_])"
                             r"|(?<![A-Za-z0-9_$])(?<!\$\{)HOME(?![A-Za-z0-9_])|\$\{HOME(?!\})")
# Variables gh (Go's HTTP stack) reads to pick a proxy or trust store, matched in any case: a line that
# names one may route the request, token and all, through someone else's server.
_GH_PROXY_ENV_RE = re.compile(r"(?<![A-Za-z0-9_])(?:HTTPS?_PROXY|ALL_PROXY|NO_PROXY|SSL_CERT_(?:FILE|DIR))(?![A-Za-z0-9_])",
                              re.IGNORECASE)
_GH_ASSIGN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\+?=")
# Every expansion, so it can stand in for any text: `…`, $( … ), ${ … }, $'…', $NAME, $1, $@ …
_GH_EXPANSION_RE = re.compile(r"`[^`]*`?|\$\([^()]*(?:\([^()]*\)[^()]*)*\)?|\$\{[^}]*\}?|\$'[^']*'?"
                              r"|\$[A-Za-z_][A-Za-z0-9_]*|\$[0-9@*#?$!-]?")


def _gh_skeleton(text: str) -> str:
    """`text` with each expansion replaced by \\x01 and quotes and backslashes
    removed: `g""h`, `g\\h` read as gh, `g$Xh` as g\\x01."""
    return re.sub(r"[\"'\\]", "", _GH_EXPANSION_RE.sub("\x01", _gh_plain(text)))


def _mentions_gh_api(text: str) -> bool:
    """The word api appears, and before it the word gh or anything bash builds
    at run time (an expansion, a glob) that could become gh; read once with
    every expansion standing in for any text, and once with only the shell's
    punctuation dropped, so code inside $( … ) and backticks counts too. The
    fail-closed rules apply only to such a command."""
    opened = re.sub(r"[\"'\\]", "", re.sub(r"[$`(){}]", " ", _gh_plain(text)))
    for sk in (_gh_skeleton(text), opened):
        apis = [m.start() for m in _API_WORD_RE.finditer(sk)]
        if not apis:
            continue
        head = sk[:apis[-1]]
        if _GH_WORD_RE.search(head.replace("\x01", "")) or "\x01" in head or re.search(r"[*?\[{]", head):
            return True
    return False


def _gh_api_around(plain, k):
    """True when gh api is mentioned in `plain` outside the assignment at k and its value: the value's
    command substitution, which the tokens split (`X=$`, `(`, …, `)`), is skipped to its closing paren."""
    j = k + 1
    if _gh_plain(plain[k]).endswith("$") and j < len(plain) and _gh_plain(plain[j]).startswith("("):
        depth = 0
        while j < len(plain):
            text = _gh_plain(plain[j])
            depth += text.count("(") - text.count(")")
            j += 1
            if depth <= 0:
                break
    join = lambda ts: " ".join(_gh_plain(x) for x in ts)
    return _mentions_gh_api(join(plain[:k])) or _mentions_gh_api(join(plain[j:]))


def _gh_api_problem(prepped: str, depth=0):
    """(command, why) for the first gh api call in the shell_prepass()ed
    `prepped` that may not be a read, else None. Reads its own tokens
    (_gh_mark), not the shared quote-stripped ones."""
    if depth > 4:
        return "(nested code)", "is nested too deeply for bash-guard to read"
    marked, odd = _gh_mark(prepped)
    if odd:
        return prepped, f"uses {odd}, which bash-guard cannot split the way bash does"
    if _IFS_RE.search(re.sub(r"[\"'\\]", "", _gh_plain(prepped))):
        return prepped, "touches IFS, which changes how bash splits words into arguments"
    host_env = _GH_HOST_ENV_RE.search(re.sub(r"[\"'\\]", "", _gh_plain(prepped)))
    if host_env:
        return prepped, (f"touches {host_env.group(0).rstrip('=')}, which can make gh send the request to "
                         "another host")
    proxy_env = _GH_PROXY_ENV_RE.search(re.sub(r"[\"'\\]", "", _gh_plain(prepped)))
    if proxy_env:
        return prepped, (f"touches {proxy_env.group(0)}, which can route gh's request (and its token) through "
                         "another server")
    tokens = tokenize(marked)
    if tokens is None:
        return prepped, "could not be parsed (unbalanced quotes)"
    plain = _without_redirects(tokens)
    # Any variable set where a command starts (`X=1 gh api …`, `X=1; gh api …`, `env X=1 gh api …`): gh
    # reads more of its environment than any list here can keep up with, so none of it is trusted.
    # The one exception: an assignment that only captures gh's output (`X=$(gh api user)`), where gh api
    # appears nowhere else in the line, so X is set after gh ran.
    start = True
    for k, t in enumerate(plain):
        if _is_op(t):
            start = True
            continue
        w = _gh_plain(t)
        captures = bool(_GH_ASSIGN_RE.match(w)) and not _gh_api_around(plain, k)
        if start and (w == "env" or (_GH_ASSIGN_RE.match(w) and not captures)):
            return prepped, (f"sets a variable ({w.split('=', 1)[0] if w != 'env' else 'env'}) in the same command "
                             "line, and gh reads its environment to pick the host, proxy and credentials")
        start = w in _GH_RESERVED or bool(start and _GH_ASSIGN_RE.match(w))
    why = _gh_stream_problem(plain)
    if why:
        return prepped, why
    words = [_gh_plain(t) for t in plain if not _is_op(t)]
    # anywhere (`builtin read "$v"`, `command declare …`), but `.` only where a command starts:
    # `gh api x --jq .` is a jq filter
    sourced = any(_gh_plain(t) == "." and (k == 0 or _is_op(plain[k - 1]) or _gh_plain(plain[k - 1]) in _GH_RESERVED)
                  for k, t in enumerate(plain))
    setter = next((w for w in words if w in _GH_NAME_SETTERS - {"."}), None) or ("." if sourced else None) or (
        "printf -v" if "printf" in words and any(w.startswith("-v") for w in words) else None)
    if setter:
        return prepped, (f"runs '{setter}', which can set any variable, HOME or GH_HOST among them, through a "
                         "name bash builds (`declare \"$n=…\"`), so gh may send the request to another host")
    reads_code = False
    for cmd, _cwd, _env in walk_commands(plain):
        hit = _gh_api_cmd(cmd)
        if hit:
            return hit
        reads_code = reads_code or _cmd_name(_gh_plain(cmd[0])) in _CODE_READERS
    # Code bash runs that the walk reads as text: $( … ) and `…` inside a word,
    # and, when the command runs a shell, eval, xargs, ssh …, any string.
    nested = [a or b for a, b in _SUBST_RE.findall(" ".join(tokens))]
    if reads_code:
        nested += [t for t in tokens if not _is_op(t) and re.search(r"\s", t)]
    for code in nested:
        code = _gh_plain(code)
        if not _mentions_gh_api(code):
            continue
        hit = _gh_api_problem(shell_prepass(code), depth + 1)
        if hit:
            return hit
    return None


def check_gh_api(prepped: str) -> None:
    if not _mentions_gh_api(prepped):
        return
    hit = _gh_api_problem(prepped)
    if hit:
        shown, why = hit
        shown = shown if len(shown) <= 80 else shown[:77] + "..."
        ask(f"'{shown}' may change data on GitHub or send it elsewhere: it {why}", _GH_API_SUGGESTION)


# ── Deletion guard: every deletion asks, except one literal scratch form ──────
# cc_tool's rule for prompts: the agent asks before it installs something,
# deletes something, writes to GitHub's API or lands work on a protected
# branch (above), and nowhere else. The
# deny tier above still refuses the catastrophic forms (a root, home, the
# repo, .git, lockfiles, CI).
#
# v0.0.23–v0.0.26 tried to exempt more (variables, cd, caches, find), deciding
# from a parse of the command that a deletion was harmless. Five review rounds
# each found a new way for that parse and bash to disagree, every one a
# deletion that went through without asking. So the exemption is now a single
# shape that needs no shell semantics at all:
#
#     the whole command is `rm [-flags] /absolute/path …`, every path written
#     out literally (no quotes, $, globs, ~, braces, backslashes), and every
#     path's real location strictly below a scratch root (/tmp, $TMPDIR, the
#     Claude scratchpad) but not one of its shared levels (/tmp itself,
#     /tmp/claude-<uid>, /tmp/claude-<uid>/<project>, the macOS T/ levels).
#
# Everything else that deletes asks. Detection is a word scan, not a parse:
# any rm/rmdir/unlink/shred word, or a -delete / --delete… /
# --remove-source-files flag, anywhere in the command (wrappers, sh -c, env -S,
# xargs, git aliases included) counts; git commit messages are skipped. git's
# own discards (rm, checkout -- / restore <paths>, worktree remove --force)
# ask as well.
_DELETE_UNPARSEABLE_RE = re.compile(
    r"(?:^|[;&|(\n])[ \t]*(?:[A-Za-z_]\w*=\S*[ \t]+)*(?:\S*/)?(?:rm|rmdir|unlink)(?:\s|$)")
_SCRATCH_ROOTS = ("/tmp", "/var/tmp", "/var/folders", "/private/tmp", "/private/var/folders")
_DELETERS = {"rm", "rmdir", "unlink", "shred"}
_DELETE_FLAG_RE = re.compile(r"^(?:-delete|--delete(?:-\w+)*|--remove-source-files)$")
_SCRATCH_FLOOR_RE = re.compile(r"^(?:/private)?/tmp/claude-\d+(?:/[^/]+)?$|^(?:/private)?/var/folders(?:/[^/]+){0,3}$")
# The one exempt shape, matched on the raw command text.
_EXEMPT_RM_RE = re.compile(r"^[ \t]*rm(?:[ \t]+-[A-Za-z]+)*(?:[ \t]+--)?((?:[ \t]+/[A-Za-z0-9._+@%,:/=-]+)+)[ \t]*$")
_DELETE_SUGGESTION = ("confirm the deletion with the user. Only a plain `rm <absolute path under the session "
                      "scratchpad or /tmp/<dir>>`, alone on its line, runs without approval")


def _scratch_roots():
    roots = list(_SCRATCH_ROOTS)
    tmpdir = os.environ.get("TMPDIR")
    if tmpdir:
        roots.append(os.path.realpath(tmpdir))
    return roots


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _scratch_floor(real: str) -> bool:
    """True for a scratch root or one of its shared upper levels."""
    if real in {os.path.realpath(r) for r in _scratch_roots()} or real in _SCRATCH_ROOTS:
        return True
    tmpdir = os.environ.get("TMPDIR")
    if tmpdir and _within(os.path.realpath(tmpdir), real):
        return True   # TMPDIR itself or one of its ancestors
    return bool(_SCRATCH_FLOOR_RE.match(real))


def _scratch_path(path: str) -> bool:
    """Strictly below a scratch root and not a shared level, on the real path."""
    real = os.path.realpath(path)
    return not _scratch_floor(real) and any(_within(real, r) for r in _scratch_roots())


def _ask_delete(what: str, targets) -> None:
    shown = ", ".join(_quoted(t) for t in targets[:4]) + (" …" if len(targets) > 4 else "")
    ask(f"{what} {shown}", _DELETE_SUGGESTION)


def _words_of(tok: str):
    """A token split the way a nested shell would see it, stripped of the
    punctuation that can sit around a command word (`!rm`, `'rm`, `(rm`)."""
    for w in re.split(r"[\s;&|()`=!]+", tok):
        w = w.strip("'\"{}")
        if w:
            yield w


def _delete_markers(tokens) -> list:
    """Every token that deletes something (by index)."""
    skip = set()
    for i, tok in enumerate(tokens):
        if tok in ("-m", "--message") and i + 1 < len(tokens) and any(
                _cmd_name(t) == "git" for t in tokens[max(0, i - 6):i]):
            skip.add(i + 1)   # a commit message is text, never run
    out = []
    for i, tok in enumerate(tokens):
        if i in skip or _is_op(tok):
            continue
        if any(_cmd_name(w) in _DELETERS or _DELETE_FLAG_RE.match(w) for w in _words_of(tok)):
            out.append(i)
    return out


def _exempt_rm(cmd: str) -> bool:
    """The one shape that deletes without asking (see the comment above)."""
    m = _EXEMPT_RM_RE.match(cmd)
    if not m:
        return False
    paths = m.group(1).split()
    return bool(paths) and all(".." not in p.split("/") and _scratch_path(p) for p in paths)


def check_deletes(tokens) -> None:
    # git's own discards first: they ask with a specific message
    for sub, _gopts, args, _repo in (git_invocations(tokens) if any(_cmd_name(t) == "git" for t in tokens) else []):
        if sub == "worktree" and args[:1] == ["remove"] and (
                "--force" in args or any("f" in _short_flags(a) for a in args[1:])):
            _ask_delete("'git worktree remove --force' deletes the worktree and any uncommitted work in",
                        [a for a in args[1:] if not a.startswith("-")])
        elif sub == "rm" and "--cached" not in args:
            _ask_delete("'git rm' deletes from the working tree", [a for a in args if not a.startswith("-")])
        elif sub in ("checkout", "restore"):
            if "--" in args:
                paths = args[args.index("--") + 1:]
            elif sub == "restore":
                paths = [a for a in args if not a.startswith("-")]
            else:
                paths = []  # `git checkout <branch>` switches branches; nothing is discarded
            if paths and not _restore_staged_only(sub, args):
                _ask_delete(f"'git {sub}' discards uncommitted changes to", paths)
    markers = _delete_markers(tokens)
    if not markers:
        return
    # `git rm --cached` only unstages: its `rm` is the git subcommand word
    git_cached = {i for i in markers if tokens[i] == "rm" and i > 0 and _cmd_name(tokens[i - 1]) == "git"
                  and "--cached" in tokens[i:]}
    if not set(markers) - git_cached:
        return
    if _exempt_rm(_CMD_FOR_LOG):
        return
    shown = [tokens[m] for m in markers if m not in git_cached]
    ask("this command deletes something (" + ", ".join("'" + _quoted(t)[:50] + "'" for t in shown[:3]) +
        "). Deletions ask unless the whole command is `rm <absolute path under scratch>`", _DELETE_SUGGESTION)



# ── Deletion words bash builds ────────────────────────────────────────────────
# The scan above finds `rm` written out. Bash can also build the word at run
# time: `{rm,-rf,x}` (brace expansion), `r?m` / `/bin/r[m]` (pathname
# expansion), `$'\x72m'` (ANSI-C quoting), `r\m`, `${X}m`. Earlier versions
# chased these through the command structure, a shell or runner at a time, and
# each review found another carrier: `xargs --process-slot-var V sh -c …`,
# `mksh -c`, `env -S`, `echo … | sh`, `bash <<< …`.
#
# This check does not look at structure. It reads every word of the raw
# command text, inside quotes and nested strings too, works out what bash could
# expand that word to, and asks when an expansion is a deletion command. So the
# carrier does not matter: any shell, runner, pipe or here-string.
#
# What counts as "could expand to": a brace expansion that yields the word; a
# glob or `$var`/`$(…)` pattern that matches it and still carries at least one
# literal letter (`r?m`, `${X}m`); an escape that decodes to it. A bare `*` or
# `$X` carries no letter and is left alone; a command word like that is the
# command-word check's job.
_ANSI_C_RE = re.compile(r"\$'((?:[^'\\]|\\.)*)'")
_PARAM_RE = re.compile(r"\$\{[^}]*\}|\$\([^)]*\)|\$[A-Za-z_][A-Za-z0-9_]*|\$[0-9@*#?$!-]|`[^`]*`")
_FRAGMENT_SPLIT_RE = re.compile(r"[\s;&|()<>`]+")   # `…` bodies are their own words, like $( … )
_MAX_EXPANSIONS = 512
_ANSI_ESC = {"a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n", "r": "\r",
             "t": "\t", "v": "\v", "\\": "\\", "'": "'", '"': '"', "?": "?"}


def _decode_ansi_c(body: str) -> str:
    r"""bash's $'…' escapes: \xHH, \NNN octal, \uHHHH, \UHHHHHHHH, \cX and the letters."""
    out, i = [], 0
    while i < len(body):
        ch = body[i]
        if ch != "\\" or i + 1 >= len(body):
            out.append(ch)
            i += 1
            continue
        nx = body[i + 1]
        m = (re.match(r"x([0-9a-fA-F]{1,2})", body[i + 1:]) or re.match(r"u([0-9a-fA-F]{1,4})", body[i + 1:])
             or re.match(r"U([0-9a-fA-F]{1,8})", body[i + 1:]))
        if m:
            try:
                out.append(chr(int(m.group(1), 16)))
            except (ValueError, OverflowError):
                pass
            i += 1 + len(m.group(0))
            continue
        m = re.match(r"([0-7]{1,3})", body[i + 1:])
        if m:
            out.append(chr(int(m.group(1), 8) & 0xFF))
            i += 1 + len(m.group(0))
            continue
        if nx == "c" and i + 2 < len(body):
            out.append(chr(ord(body[i + 2]) & 0x1F))
            i += 3
            continue
        out.append(_ANSI_ESC.get(nx, "\\" + nx))
        i += 2
    return "".join(out)


class _TooComplex(Exception):
    """The built-word scan ran out of its time budget."""


_BUILT_DEADLINE = [0.0]


def _budget() -> None:
    if time.monotonic() > _BUILT_DEADLINE[0]:
        raise _TooComplex


def _brace_span(s: str):
    """(start, end, alternatives) of the first brace group bash would expand, or None.

    Braces are matched in one pass with a stack. A draft rescanned from every
    `{`, which was quadratic: 20,000 unclosed `{` took 6.8 s, past the hook's
    3 s timeout, and a hook that times out lets the command run."""
    stack, pairs = [], {}
    for j, ch in enumerate(s):
        if ch == "{" and not (j > 0 and s[j - 1] == "$"):
            stack.append(j)
        elif ch == "}" and stack:
            pairs[stack.pop()] = j
    for i in sorted(pairs):
        _budget()
        j = pairs[i]
        inner = s[i + 1:j]
        parts, depth, last = [], 0, 0
        for k, ch in enumerate(inner):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            elif ch == "," and depth == 0:
                parts.append(inner[last:k])
                last = k + 1
        if parts:
            parts.append(inner[last:])
            return i, j, parts
        seq = re.fullmatch(r"(-?\w+)\.\.(-?\w+)(?:\.\.-?\d+)?", inner)
        if seq:
            a, b = seq.group(1), seq.group(2)
            if len(a) == 1 and len(b) == 1 and a.isalpha() and b.isalpha():
                lo, hi = sorted((ord(a), ord(b)))
                return i, j, [chr(k) for k in range(lo, hi + 1)][:128]
            if a.lstrip("-").isdigit() and b.lstrip("-").isdigit():
                lo, hi = sorted((int(a), int(b)))
                return i, j, [str(k) for k in range(lo, min(hi, lo + 127) + 1)]
    return None


def _brace_expand(s: str, out: list) -> None:
    _budget()
    if len(out) >= _MAX_EXPANSIONS:
        return
    span = _brace_span(s)
    if span is None:
        out.append(s)
        return
    a, b, parts = span
    for alt in parts:
        _brace_expand(s[:a] + alt + s[b + 1:], out)
        if len(out) >= _MAX_EXPANSIONS:
            return


def _built_deleter(frag: str):
    """The deletion command `frag` can expand to without being written out, or None."""
    decoded = _ANSI_C_RE.sub(lambda m: _decode_ansi_c(m.group(1)), frag)
    unquoted = decoded.replace('"', "").replace("'", "")
    plain = re.sub(r"\\(.)", r"\1", unquoted)
    # Quotes at the edge of a word only delimit a string ('rm the old helper');
    # quotes inside it (r''m) are what build a new word.
    raw_base = frag.strip("'\"").rsplit("/", 1)[-1]
    expansions: list = []
    _brace_expand(plain, expansions)
    for e in expansions:
        for word in _FRAGMENT_SPLIT_RE.split(e):          # a decoded escape can carry spaces
            base = word.rsplit("/", 1)[-1]
            if not base:
                continue
            if _PARAM_RE.search(base) or any(ch in base for ch in "*?["):
                pattern = _PARAM_RE.sub("*", base)
                # `[m]` is one literal letter; a wider class is not
                letters = re.findall(r"[A-Za-z]", re.sub(r"\[([A-Za-z])\]", r"\1", re.sub(r"\[[^\]]{2,}\]", "", pattern)))
                if not letters:
                    continue                                 # `*`, `$X`: no literal letter
                if not _PARAM_RE.search(base) and "/" not in word and len(letters) < 2:
                    # A pure glob matches files, not commands on PATH: `s*` / `\s*` (a sed
                    # regex) only reaches `shred` through a file planted here. Directory
                    # globs (`/bin/r[m]`) and two-letter ones (`r?m`) still count.
                    continue
                hit = next((d for d in sorted(_DELETERS) if fnmatch.fnmatchcase(d, pattern)), None)
                if hit:
                    return hit
            elif base in _DELETERS and raw_base != base:
                # Any change from the raw word counts: braces, escapes, backslashes and
                # quotes too. `r''m` or `$'r'm` inside a string piped to a shell reaches
                # that shell as `rm`; a draft that compared after removing quotes let it by.
                return base
    return None


def check_built_deleters(text: str) -> None:
    _BUILT_DEADLINE[0] = time.monotonic() + 0.8
    try:
        _check_built_deleters(text)
    except _TooComplex:
        ask("this command is too complex for bash-guard to check, within its time limit, for deletion "
            "commands that bash would build at run time (brace, glob or escape expansion)",
            "split it into simpler commands, or write any deletion out plainly")


def _check_built_deleters(text: str) -> None:
    # No commit-message exemption: matched on raw text, it also blanked `$(…)` inside
    # a double-quoted message (which bash runs first) and "git … -m" inside a string
    # piped to sh. A message whose words expand to a deleter asks, rarely.
    # $'…' first, on the whole text: its body can hold spaces, so splitting into
    # words before decoding would cut `$'\x72m -rf x'` apart and never decode it.
    for m in _ANSI_C_RE.finditer(text):
        _budget()
        # Checked even when nothing is escaped: shlex reads `$'rm -rf x'` as the one
        # word `$rm -rf x`, so the written-out scan above never sees that `rm`.
        decoded = _decode_ansi_c(m.group(1))
        for word in _FRAGMENT_SPLIT_RE.split(decoded):
            base = word.rsplit("/", 1)[-1]
            hit = base if base in _DELETERS else (_built_deleter(word) if word else None)
            if hit:
                ask(f"'{m.group(0)[:60]}' becomes '{hit}' when bash (or a shell it is handed to) "
                    "runs it, so this command deletes something without writing the command out",
                    "write the deletion out plainly (rm <path>) so it can be reviewed; " + _DELETE_SUGGESTION)
    text = _ANSI_C_RE.sub(lambda m: "'" + _decode_ansi_c(m.group(1)).replace("'", "") + "'", text)
    for frag in _FRAGMENT_SPLIT_RE.split(text):
        _budget()
        if not frag or not re.search(r"[{}*?\[\\$'\"]", frag):
            continue                                         # nothing for bash to expand
        hit = _built_deleter(frag)
        if hit:
            ask(f"'{frag[:60]}' expands to '{hit}' when bash (or a shell it is handed to) runs it, "
                "so this command deletes something without writing the command out",
                "write the deletion out plainly (rm <path>) so it can be reviewed; " + _DELETE_SUGGESTION)

# ── Command words bash builds itself ──────────────────────────────────────────
# The deletion scan looks for the word `rm`; bash can build that word at run
# time: `$'\x72m'`, `{rm,-rf,x}`, `r?m` or `/bin/r[m]` (pathname expansion),
# `${X}m`, `$P`, `$(echo rm)`. v0.0.25 tried to resolve `$P` from the
# command's own assignments; review found that per-name tracking failing open
# again, so there is no resolution at all now: the word that runs must be
# written out plainly.
#
# Checked: the first word of every simple command, at any depth: inside
# `$( … )`, `<( … )` / `>( … )`, `sh -c '…'`, `eval '…'`, behind `coproc`,
# and the command that xargs, find -exec, parallel, watch, timeout-style
# runners and the like are handed. A plain word is letters, digits and
# `_./+@%:,=-`, optionally starting with `~/` when nothing in the command can
# touch HOME; `[`, `[[`, `!`, `{`, `}` pass; `case` patterns are skipped.
#
# Threat-model bound, stated once: a guard that reads command text cannot see
# what an interpreter decides at run time. `python3 -c 'shutil.rmtree(…)'`,
# `perl -e rmtree`, `node -e 'fs.rmSync(…)'`, `mv x /dev/null`, `truncate
# -s0`, `: > f` delete or empty files without any of these words. Those are
# left to Claude Code's own permission layer and auto-mode classifier.
_GLOB_OR_BRACE_RE = re.compile(r"[*?\[\]{}]")
_PLAIN_COMMAND_WORD_RE = re.compile(r"^[A-Za-z0-9_./+@%:,=-]+$")
_SHELL_WORDS_OK = {"[", "[[", "]]", "!", "{", "}", "((", "))"}
_NESTED_SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
# Commands that run their first operand as a command (options skipped; the
# value-taking options below consume their value).
_RUNNERS = {"xargs": {"-I", "-i", "-L", "-l", "-n", "-P", "-s", "-d", "-E", "-e", "-a", "--arg-file",
                      "--delimiter", "--max-args", "--max-procs", "--replace"},
            "parallel": {"-j", "--jobs", "-S", "--sshlogin", "-a", "--arg-file", "-I"},
            "watch": {"-n", "--interval", "-d"},
            "flock": {"-w", "--wait", "-E", "-c"},
            "setsid": set(), "stdbuf": {"-i", "-o", "-e"}, "chroot": set(), "unshare": set(),
            "taskset": set(), "strace": {"-o", "-e", "-p", "-s"}, "ltrace": {"-o", "-e"},
            "busybox": set(), "nsenter": {"-t", "--target"}, "runuser": {"-u", "-g"},
            "systemd-run": {"-u", "--unit", "-p", "--property"}, "coproc": set()}
_HOME_TOUCHERS = {"read", "mapfile", "readarray", "unset", "eval", "source", ".", "declare", "typeset",
                  "local", "export", "readonly", "printf", "let", "trap", "alias"}


def _simple_commands_at(tokens):
    """(token index range, command words) for each simple command, keywords,
    leading assignments and wrappers stripped."""
    start = 0
    for i in range(len(tokens) + 1):
        if i == len(tokens) or _is_op(tokens[i]):
            words = list(tokens[start:i])
            while words and words[0] in _SHELL_KEYWORDS | _OPENERS | _CLOSERS | {"coproc"}:
                words = words[1:]
            cmd, _w = _strip_wrappers(_strip_assignments(words))
            cmd = _strip_assignments(cmd)
            if cmd:
                yield range(start, i), cmd
            start = i + 1


def _plain_word(w: str, home_safe: bool) -> bool:
    if w in _SHELL_WORDS_OK:
        return True
    if w.startswith("~/"):
        return home_safe and bool(_PLAIN_COMMAND_WORD_RE.match(w[2:])) and not _GLOB_OR_BRACE_RE.search(w)
    return bool(_PLAIN_COMMAND_WORD_RE.match(w)) and not _GLOB_OR_BRACE_RE.search(w)


def _runner_index(cmd):
    """Index in `cmd` of the command a runner is handed, or None."""
    name = _cmd_name(cmd[0])
    if name == "find":
        for k, t in enumerate(cmd):
            if t in ("-exec", "-execdir", "-ok", "-okdir") and k + 1 < len(cmd):
                return k + 1
        return None
    if name not in _RUNNERS:
        return None
    takes_value = _RUNNERS[name]
    args = cmd[1:]
    k = 0
    if name == "flock" and args:
        k = 1   # flock <lockfile> <command>
    while k < len(args):
        a = args[k]
        if a == "--":
            return k + 2 if k + 1 < len(args) else None
        if a.startswith("-"):
            k += 2 if a in takes_value else 1
            continue
        if name == "watch" or name == "taskset" and re.match(r"^[0-9a-fx,-]+$", a):
            if name == "taskset":
                k += 1
                continue
        return k + 1
    return None


def _command_word_problem(tokens, depth=0):
    """The first command word bash could build into something else, or None."""
    if depth > 3:
        return "(nested too deep)"
    commands = list(_simple_commands_at(tokens))
    home_safe = not any(re.search(r"(?<![$\w{])HOME\b", t) for t in tokens if not _is_op(t)) and not any(
        _cmd_name(c[0]) in _HOME_TOUCHERS for _s, c in commands)
    in_case = 0
    for span, cmd in commands:
        prev = tokens[span.start - 1] if span.start > 0 else ""
        nxt = tokens[span.stop] if span.stop < len(tokens) else ""
        raw_first = tokens[span.start] if span.start < len(tokens) else ""
        if raw_first == "case" or cmd[0] == "case":
            in_case += 1
            continue        # `case <word> in <pattern>`: data, not a command
        if "esac" in tokens[span.start:span.stop]:
            in_case = max(0, in_case - 1)
        if in_case and nxt.startswith(")"):
            continue        # a case pattern such as `*.txt)`
        if prev and any(c in prev for c in "<>") and "(" not in prev:
            continue        # the word after > / < / << is a redirect target; <( … ) is not skipped
        w = cmd[0]
        if w == "$":
            continue        # `$` opens a $( … ) whose inner command is checked on its own
        problem = _cmd_problem(cmd, home_safe, depth)
        if problem:
            return problem
    return None


_FIND_ACTIONS = {"-exec", "-execdir", "-ok", "-okdir"}
# bash/sh options that take a value, so the word after them is not the -c code
_SHELL_VALUE_OPTS = {"-o", "+o", "-O", "+O", "--rcfile", "--init-file"}


def _shell_code(cmd):
    """The string a shell runs with -c, also when -c sits in a cluster (-lc, -ec,
    -xec) or behind -o/-O options; None when the shell runs a script file."""
    k = 1
    while k < len(cmd):
        a = cmd[k]
        if a in _SHELL_VALUE_OPTS:
            k += 2
            continue
        if a == "--" or not a.startswith(("-", "+")):
            return None
        if a.startswith("-") and not a.startswith("--") and "c" in a[1:]:
            return cmd[k + 1] if k + 1 < len(cmd) else None
        k += 1
    return None


def _handed_commands(cmd):
    """Every command `cmd` hands to something else to run: each find action
    (-exec/-execdir/-ok/-okdir, also the tail of one split off by `\\;`) and
    the command a runner such as xargs/setsid/watch is given."""
    out = []
    starts = [k for k, t in enumerate(cmd) if t in _FIND_ACTIONS and (k == 0 or _cmd_name(cmd[0]) == "find")]
    for k in starts:
        end = next((j for j in range(k + 1, len(cmd)) if cmd[j] in ("+", ";") or cmd[j] in _FIND_ACTIONS),
                   len(cmd))
        out.append(cmd[k + 1:end])
    if _cmd_name(cmd[0]) in _RUNNERS and _cmd_name(cmd[0]) != "find":
        idx = _runner_index(cmd)
        if idx is not None:
            out.append(cmd[idx:])
    return [h for h in out if h]


def _cmd_problem(cmd, home_safe, depth):
    """The first non-plain command word in one simple command, in the code it
    hands a shell or eval, or in any command it hands a runner or find action.
    Recursion is what closes `xargs sh -c '…'`, `find … -exec true \\; -exec …`
    and `xargs xargs …`: each handed command gets the full check, not just a
    look at its first word."""
    if depth > 6:
        return "(nested too deep)"
    w = cmd[0]
    if w in _FIND_ACTIONS:
        pass            # the tail of a find split off at `\\;`: checked as a handed command below
    elif w == "{}":
        return None     # find -exec {} / xargs -I{} {}: the found item, unchanged behaviour
    elif not _plain_word(w, home_safe):
        return w
    name = _cmd_name(w)
    code = " ".join(cmd[1:]) if name == "eval" else (_shell_code(cmd) if name in _NESTED_SHELLS else None)
    if code:
        inner = tokenize(shell_prepass(code))
        if inner is None:
            return code[:40]
        problem = _command_word_problem(inner, depth + 1)
        if problem:
            return problem
    for handed in _handed_commands(cmd):
        problem = _cmd_problem(handed, home_safe, depth + 1)
        if problem:
            return problem
    return None


def check_command_words(tokens) -> None:
    problem = _command_word_problem(tokens)
    if problem:
        ask(f"the command word '{_quoted(problem)[:60]}' is not written out plainly (a variable, quoting, "
            "a glob or a brace), so bash-guard cannot tell what runs",
            "write the command name out (rm, python3, ./script.sh); for a repeated long command define a "
            "shell function, e.g. pw(){ npx --yes @playwright/cli@1.2.3 \"$@\"; }")


# ── Shell-write bypass warning (non-blocking) ─────────────────────────────────
# Edits that go through Write/Edit get the post-edit typecheck, the write guard
# (secrets, confinement, stale-read), and the activity log. A `>` redirect,
# `tee`, `sed -i`, `perl -pi`, `patch` or `git apply` into a source file gets
# none of that. Warn, don't block: shell writes of scratch and generated files
# are normal, and this hook cannot tell a tracked file from a throwaway one.
_SOURCE_EXTS = {
    ".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte",
    ".py", ".pyi", ".rs", ".go", ".java", ".kt", ".kts", ".rb", ".php", ".cs",
    ".c", ".cc", ".cpp", ".h", ".hpp", ".swift", ".scala", ".ex", ".exs",
}
_SCRATCH_HINTS = ("/tmp/", "/scratchpad", "/var/folders/", "/dev/")
_INPLACE_EDITORS = {"sed", "perl", "gawk", "awk", "ex", "ed", "patch"}


def _is_source_target(tok: str) -> bool:
    t = _norm_path(tok)
    if any(h in t for h in _SCRATCH_HINTS) or t.startswith(("/tmp", "$TMPDIR", "${TMPDIR")):
        return False
    return os.path.splitext(t)[1].lower() in _SOURCE_EXTS


def _bypass_note(target: str, how: str) -> str:
    return (f"'{how}' writes {target} through the shell, so the post-edit typecheck, the write "
            "guard and the activity log do not see it. Use the Edit/Write tools for source files; "
            "shell writes are fine for scratch and generated output.")


def check_write_bypass(tokens) -> None:
    for i, tok in enumerate(tokens):
        if tok in (">", ">>", "1>", "2>", "&>") and i + 1 < len(tokens) and _is_source_target(tokens[i + 1]):
            warn(_bypass_note(_quoted(tokens[i + 1]), f"{tok} redirect"))
            return
    for seg, _gid in split_commands(tokens):
        cmd, _w = _strip_wrappers(seg)
        if not cmd:
            continue
        name = _cmd_name(cmd[0])
        args = cmd[1:]
        srcs = [a for a in args if not a.startswith("-") and _is_source_target(a)]
        if not srcs:
            continue
        if name == "tee":
            warn(_bypass_note(_quoted(srcs[0]), "tee"))
            return
        if name == "sed" and any(a.startswith("-i") or a == "--in-place" for a in args):
            warn(_bypass_note(_quoted(srcs[0]), "sed -i"))
            return
        if name == "perl" and any(a.startswith("-") and "i" in a[1:] and not a.startswith("--") for a in args):
            warn(_bypass_note(_quoted(srcs[0]), "perl -i"))
            return
        if name in ("gawk", "awk") and "inplace" in " ".join(args):
            warn(_bypass_note(_quoted(srcs[0]), "awk -i inplace"))
            return
        if name in ("ex", "ed", "patch"):
            warn(_bypass_note(_quoted(srcs[0]), name))
            return
    for sub, _g, args, _r in git_invocations(tokens) if any(_cmd_name(t) == "git" for t in tokens) else []:
        if sub == "apply" and "--check" not in args and "--stat" not in args:
            warn(_bypass_note("the patched files", "git apply"))
            return


# ── cc-dash control mode: no launches from an agent ──────────────────────────
# Your agents run as you. cc-dash --control shows its per-launch code on the
# terminal that started it, so an agent must not start one: refuse launching
# control mode (scanned on the raw command too, so heredoc bodies and
# `python3 -c` code count). cc-dash itself also refuses to start inside a
# Claude Code session. The control files and endpoints need no guard here: the
# hook acts only on the dashboard's own records and every write needs the code.
# `--control`, any prefix argparse might once have accepted (`--c`, `--con`, ...;
# cc-dash now sets allow_abbrev=False), and `--control=...`.
_CONTROL_FLAG_RE = re.compile(r"^--(?:c|co|con|cont|contr|contro|control)(?:=.*)?$", re.S)
_DASH_NAMES = {"cc-dash", "ccdash"}
# For text that is not shell (inline python/node code, heredoc bodies, an
# unparseable command): the name, then such a flag within the same stretch.
_RAW_LAUNCH_RE = re.compile(r"(?<![\w.-])(?:cc-dash|ccdash)(?![\w-])[^\n]{0,400}?(?<![\w-])"
                            r"--(?:c|co|con|cont|contr|contro|control)(?![\w-])", re.S)
_HEREDOC_ANY_RE = re.compile(r"<<-?[ \t]*['\"]?[A-Za-z_]")
# Python that reaches the modules which start a server or mint codes (reading
# transcripts with cc_dash.store / parser stays allowed), or runpy on cc-dash.
_RAW_PY_DASH_RE = re.compile(r"\bcc_dash\.(?:server|auth|control)\b|\bfrom\s+cc_dash\s+import\s+[^\n]*\b(?:server|auth|control)\b"
                             r"|\brunpy\b[\s\S]{0,300}?cc[-_]dash")
_EXPANSION_RE = re.compile(r"[$`*?\[\]{}]")
_LAUNCH_ASK: list = []      # cc-dash invocations whose arguments bash expands at run time


def _raw_launch(text):
    m = _RAW_LAUNCH_RE.search(text) or _RAW_PY_DASH_RE.search(text)
    return m.group(0)[:80] if m else None


def _launch_in_cmd(cmd, depth):
    """The cc-dash control-mode launch in one simple command (or in what it hands
    a shell, `script -c`, eval, a runner such as setsid/xargs, or an inline
    interpreter), else None."""
    if depth > 6 or not cmd:
        return None
    name = _cmd_name(cmd[0])
    args = [_quoted(a) for a in cmd[1:]]
    raw_args = list(cmd[1:])
    if name in _INLINE_INTERP:
        for k, a in enumerate(args):
            if a in _EVAL_FLAGS and k + 1 < len(args) and _raw_launch(args[k + 1]):
                return _raw_launch(args[k + 1])
            if a == "-m" and k + 1 < len(args) and args[k + 1].split(".")[0] == "cc_dash":   # python3 -m cc_dash.server
                name, args, raw_args = "cc-dash", args[k + 2:], raw_args[k + 2:]
                break
        else:
            rest = [a for a in args if not a.startswith("-")]
            if rest and _cmd_name(rest[0]) in _DASH_NAMES:      # python3 …/bin/cc-dash --control
                k = args.index(rest[0])
                name, args, raw_args = _cmd_name(rest[0]), args[k + 1:], raw_args[k + 1:]
    if name in _DASH_NAMES:
        if any(_CONTROL_FLAG_RE.match(a) for a in args):
            return " ".join([name] + args)[:80]
        if any(_EXPANSION_RE.search(a) for a in raw_args):
            _LAUNCH_ASK.append(" ".join([name] + raw_args)[:80])
    code = None
    if name in _NESTED_SHELLS:
        code = _shell_code(cmd)
    elif name == "script":                                     # script -qc '<command>' /dev/null
        for k, a in enumerate(args):
            if a.startswith("--command="):
                code = a.split("=", 1)[1]
            elif (a == "--command" or (a.startswith("-") and not a.startswith("--") and "c" in a[1:])) and k + 1 < len(args):
                code = args[k + 1]
    elif name == "eval":
        code = " ".join(args)
    if code:
        inner = tokenize(shell_prepass(code))
        hit = _launch_problem(inner, depth + 1) if inner is not None else _raw_launch(code)
        if hit:
            return hit
    for handed in _handed_commands(cmd):
        inner = _strip_assignments(_strip_wrappers(_strip_assignments(handed))[0])
        hit = _launch_in_cmd(inner, depth + 1)
        if hit:
            return hit
    return None


def _launch_problem(tokens, depth=0):
    if depth > 6:
        return None
    for i, tok in enumerate(tokens):
        if tok == "<<<" and i + 1 < len(tokens):               # bash <<< 'cc-dash --control'
            body = _quoted(tokens[i + 1])
            inner = tokenize(shell_prepass(body))
            hit = _launch_problem(inner, depth + 1) if inner is not None else _raw_launch(body)
            if hit:
                return hit
    for _span, cmd in _simple_commands_at(tokens):
        hit = _launch_in_cmd(cmd, depth)
        if hit:
            return hit
    return None


def _deny_launch(what):
    deny(f"starting cc-dash in control mode from an agent ('{what}'): control mode is the user's way to approve "
         "agents, and its code belongs on the user's terminal", "ask the user to run `ccdash --control` in their own "
         "terminal; `cc-dash` without --control (read-only) is fine")


def check_cc_dash_launch(tokens, cmd: str) -> None:
    hit = _launch_problem(tokens)
    # Heredoc bodies are not in the tokens, and text piped into a shell or runner
    # (`echo 'cc-dash --control' | sh`, `… | xargs sh -c '{}'`) becomes a command
    # at run time: in either case scan the raw text.
    feeds_code = any(_cmd_name(c[0]) in _NESTED_SHELLS | {"xargs", "parallel", "script", "eval", "source", "."}
                     for _s, c in _simple_commands_at(tokens))
    if not hit and (feeds_code or _HEREDOC_ANY_RE.search(cmd)):
        hit = _raw_launch(cmd)
    if hit:
        _deny_launch(hit)
    if _LAUNCH_ASK:
        # Name and flag matching is not a boundary: arguments bash builds at run
        # time could become --control. cc-dash itself also refuses to start control
        # mode under Claude Code; this asks before relying on that.
        ask(f"cc-dash is started with arguments the shell expands at run time ('{_LAUNCH_ASK[0]}'), so bash-guard "
            "cannot tell whether they turn on control mode", "write cc-dash's arguments out literally")


def check_cc_dash_launch_raw(cmd: str) -> None:
    """The unparseable-command fallback: the raw text scan."""
    hit = _raw_launch(cmd)
    if hit:
        _deny_launch(hit)


# ── Project rules (.claude/guard-rules.json) ──────────────────────────────────
def check_project_rules(cmd: str) -> None:
    if not lib:
        return
    rules, _settings, err = lib.load_rules(lib.repo_root())
    if err:
        warn(f".claude/guard-rules.json could not be loaded ({err}); project rules are NOT enforced "
             "until it is fixed.")
        return
    for rule, matched in lib.match_rules(rules, "Bash", {"command": cmd}):
        rid = rule.get("id", "?")
        reason = f"[{rid}] {rule['reason']} (matched '{matched[:60]}')"
        action = rule.get("action", "warn")
        if action == "deny":
            deny(reason, rule.get("suggestion", ""))
        elif action == "ask":
            ask(reason, rule.get("suggestion", ""))
        else:
            warn(reason + (f" Suggestion: {rule['suggestion']}" if rule.get("suggestion") else ""))


# ── Entry point ───────────────────────────────────────────────────────────────
_PARSE_FAIL = (
    "bash-guard could not read the tool payload ({why}), so it cannot check this "
    "command for protected-branch writes or secret reads. Refusing rather than "
    "letting it through unchecked. Check the hook or re-run the command."
)


def read_command() -> str:
    """The Bash command to inspect. Fails CLOSED on anything it cannot parse.

    Exits 0 silently only for a *well-formed* payload this guard has no opinion
    on: no `command` key at all (BashOutput/KillShell also match the "Bash"
    matcher regex; other tools would too) or an empty command. Every ordinary
    Bash payload has a string `command` and reaches the checks normally.
    """
    try:
        raw = sys.stdin.read()
    except Exception as exc:  # noqa: BLE001 - any stdin failure is a parse failure
        deny(_PARSE_FAIL.format(why=f"stdin unreadable: {type(exc).__name__}"))
    if not raw.strip():
        deny(_PARSE_FAIL.format(why="empty stdin"))
    try:
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        deny(_PARSE_FAIL.format(why=f"invalid JSON: {type(exc).__name__}"))
    if not isinstance(data, dict):
        deny(_PARSE_FAIL.format(why=f"payload was {type(data).__name__}, not an object"))
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        deny(_PARSE_FAIL.format(
            why=f"tool_input was {type(tool_input).__name__}, not an object"))
    if "command" not in tool_input:
        sys.exit(0)  # not a command-bearing tool call — nothing for this guard
    global _PAYLOAD_CWD
    if isinstance(data.get("cwd"), str):
        _PAYLOAD_CWD = data["cwd"]
    cmd = tool_input["command"]
    if not isinstance(cmd, str):
        deny(_PARSE_FAIL.format(why=f"command was {type(cmd).__name__}, not a string"))
    return cmd


def check_command(cmd: str) -> None:
    """Every check, in order, on one non-empty command. deny() and ask() end it
    (they exit, or raise _Verdict under classify()); returning means no objection."""
    global _CMD_FOR_LOG
    _CMD_FOR_LOG = cmd
    if len(cmd) > MAX_COMMAND_CHARS:
        # Never silently allow what we did not analyse.
        ask(
            f"bash-guard did not inspect this command: it is {len(cmd):,} characters, "
            f"over the {MAX_COMMAND_CHARS:,}-character analysis limit. Approve only if "
            f"you are sure it does not commit/push to a protected branch or read secrets."
        )

    prepped = shell_prepass(cmd)
    global _SINGLE_QUOTED_DOLLAR, _PREPPED
    _SINGLE_QUOTED_DOLLAR = _has_single_quoted_dollar(prepped)
    _PREPPED = prepped
    tokens = tokenize(prepped)
    if tokens is None:
        # Unparseable shell string (e.g. unbalanced quotes). The secret scan
        # can't guess at it — that half stays fail-open, as before — but the git
        # half still gets its regex fallback.
        check_git_unparseable(prepped)
        check_landing_unparseable(prepped)
        check_cc_dash_launch_raw(cmd)
        check_project_rules(cmd)
        if _NPX_UNPARSEABLE_RE.search(prepped):
            ask("bash-guard could not parse this command (unbalanced quotes), so it cannot confirm the "
                "'npx' in it runs an installed package rather than downloading one", _NPX_SUGGESTION)
        if _GH_API_UNPARSEABLE_RE.search(prepped):
            ask("bash-guard could not parse this command (unbalanced quotes), so it cannot tell whether the "
                "'gh api' call in it changes data on GitHub", _GH_API_SUGGESTION)
        check_built_deleters(prepped)
        if _DELETE_UNPARSEABLE_RE.search(prepped):
            ask("bash-guard could not parse this command (unbalanced quotes), so it cannot tell what the "
                "'rm' in it deletes", _DELETE_SUGGESTION)
        return

    check_secret_read(tokens)
    check_cc_dash_launch(tokens, cmd)
    if any(_cmd_name(t) == "git" for t in tokens):  # cheap gate before the invocation walk
        check_git(tokens)
    check_destructive(cmd, tokens)
    check_project_rules(cmd)
    check_npx(tokens)
    check_gh_api(prepped)
    check_deletes(tokens)
    check_built_deleters(prepped)
    check_command_words(tokens)
    check_write_bypass(tokens)


def classify(command: str, cwd: str = ""):
    """("deny" | "ask" | "allow", reason): what this hook decides for `command`
    run in `cwd`, without printing, exiting or logging. For cc-dash's control
    hook, which must know whether a permission prompt is one bash-guard asked
    about (that needs a typed confirm on the dashboard). A crash is a deny, as
    when the hook runs."""
    global _CAPTURE, _PAYLOAD_CWD, _WARNINGS, _BRANCH_LOOKUPS
    if not isinstance(command, str) or not command.strip():
        return "allow", ""
    _CAPTURE, _PAYLOAD_CWD, _WARNINGS, _BRANCH_LOOKUPS = True, cwd if isinstance(cwd, str) else "", [], 0
    _BRANCH_CACHE.clear()
    try:
        check_command(command)
    except _Verdict as v:
        return v.decision, v.reason
    except Exception as exc:  # noqa: BLE001 - the hook denies on a crash; so does this
        return "deny", _fmt(f"bash-guard failed while checking this command ({type(exc).__name__}: {exc})", "")
    finally:
        _CAPTURE = False
    return "allow", ""


def main() -> None:
    _arm_budget()
    cmd = read_command()
    if not cmd.strip():
        sys.exit(0)
    check_command(cmd)
    _disarm_budget()
    flush_warnings()
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - SystemExit is not an Exception
        # A crash used to mean exit 1, which the harness treats as a non-blocking
        # error: the command ran unchecked. For the one hook the README calls the
        # enforced boundary, that is the wrong direction.
        deny(
            f"bash-guard failed while checking this command ({type(exc).__name__}: {exc}). "
            "Refusing rather than running it unchecked. Fix the hook, then retry."
        )
