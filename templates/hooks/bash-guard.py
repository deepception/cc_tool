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

WHAT THIS IS NOT
----------------
A *static token inspector*, not a shell. It reads the command string with shlex
and reasons about tokens; it never expands anything. Anything that only becomes a
protected branch or a secret path *after* shell expansion is invisible to it, and
always will be — this is inherent to the approach, not a TODO:

    B=master; git push origin $B     git push origin "$(echo master)" / `...` / $'mas\\x74er'
    F=.env; grep KEY $F              grep KEY .en*  (glob / brace expansion)
    sh -c '...' / bash -c '...'      eval, heredoc-fed interpreters, base64 | sh
    git${IFS}commit                  computed paths: open(chr(46)+'env')

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
import json
import re
import shlex
import subprocess
import sys

PROTECTED = {"main", "master", "production", "release"}
# Longest command we tokenize. Beyond this we cannot analyse safely, so we warn
# loudly and defer to the user rather than silently allowing (see main()).
MAX_COMMAND_CHARS = 400_000


def deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def ask(reason: str) -> None:
    """Hand the decision to the user (used when the guard cannot decide itself)."""
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


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
            "Refusing: this command targets more than "
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
_SHELL_OP_CHARS = set("();<>|&")
# Operator tokens that DON'T end a pipeline (a pipeline is the unit for `xargs`).
_PIPE_TOKENS = {"|", "|&"}
_GIT_GLOBAL_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
# Repo-selecting global options only — these are safe to forward to rev-parse.
_GIT_REPO_VALUE_OPTS = {"-C", "--git-dir", "--work-tree"}
_GIT_REPO_INLINE_OPTS = ("--git-dir=", "--work-tree=")
_PUSH_VALUE_OPTS = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}


def tokenize(cmd: str):
    """shlex tokens, or None if the shell string itself is unparseable.

    punctuation_chars makes ();<>|& (and ';') standalone tokens, so chained
    commands, redirections, comments and quoted text don't bleed into the parse.
    """
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        return list(lex)
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
            \.env(?:\.[^/\s]+)?    # .env, .env.local, .env.production …
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
                f"Refusing to read secret material ('{_quoted(seg[j])}') "
                f"via '{name}'. These files (.env, keys, credential stores) are "
                f"blocked for a reason; don't exfiltrate or echo their contents."
            )

    # 2) inline interpreter code: python -c "open('.env')", node -e "...id_rsa..."
    if interp_at < n and eval_at > interp_at:
        deny(
            "Refusing inline interpreter code that opens secret material "
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
                    f"Refusing to feed secret material ('{_quoted(target)}') into a "
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
                f"Refusing 'xargs' pipeline that targets secret material "
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
        if tokens[i] != "git":
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
                "Refusing to bypass pre-commit/pre-push hooks via --no-verify (or its "
                "short form -n). Fix the underlying failure (lint/format/test) instead "
                "of skipping the check."
            )
        for opt, val in gopts:
            if opt == "-c" and val is not None and val.lower().startswith("core.hookspath="):
                deny(
                    "Refusing 'git -c core.hooksPath=…': it disables the project's "
                    "pre-commit/pre-push hooks just like --no-verify. Fix the "
                    "underlying failure instead of skipping the check."
                )

    # 2) commit to a protected branch
    for sub, _gopts, args, repo in invocations:
        if sub != "commit" or "--amend" in args:
            continue
        branch = current_branch(repo)
        if branch in PROTECTED:
            where = f" (target repo: {' '.join(repo)})" if repo else ""
            deny(
                f"Refusing to commit directly to protected branch '{branch}'{where}. "
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
            "Refusing 'git push --all'/'--mirror': it updates every branch, "
            "including protected ones (main/master/production/release). "
            "Push specific feature branches instead."
        )
    for repo in needs_current:
        dests.append(current_branch(repo))
    for target in dests:
        if target in PROTECTED:
            deny(
                f"Refusing to push to protected branch '{target}'. "
                f"Open a pull request from a feature branch instead."
            )


def check_git_unparseable(cmd: str) -> None:
    """Best-effort regex checks for a shell string shlex could not tokenize.

    Deliberately the pre-existing (looser) logic: an unbalanced quote is not a
    reason to start blocking ordinary work, but it is also not a reason to stop
    looking for a push to master.
    """
    if not re.search(r"(^|[\s;&|])git\s", cmd):
        return
    if re.search(r"(^|\s)--no-verify(\s|$)", cmd):
        deny(
            "Refusing to bypass pre-commit/pre-push hooks via --no-verify. "
            "Fix the underlying failure (lint/format/test) instead of skipping the check."
        )
    if re.search(r"(^|[\s;&|])git\s+commit\b", cmd) and not re.search(r"--amend\b", cmd):
        branch = current_branch()
        if branch in PROTECTED:
            deny(
                f"Refusing to commit directly to protected branch '{branch}'. "
                f"Create a feature branch first: git checkout -b <branch-name>"
            )
    if "push" not in cmd:
        return
    m = re.search(r"git\s+(?:-\S+\s+)*push(?:\s+-\S+)*(?:\s+(\S+)(?:\s+(\S+))?)?", cmd)
    if not m:
        return
    if re.search(r"(^|\s)(--all|--mirror)(\s|$)", cmd):
        deny(
            "Refusing 'git push --all'/'--mirror': it updates every branch, "
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
                f"Refusing to push to protected branch '{target}'. "
                f"Open a pull request from a feature branch instead."
            )


# ── Entry point ───────────────────────────────────────────────────────────────
_PARSE_FAIL = (
    "bash-guard could not read the tool payload ({why}), so it cannot check this "
    "command for protected-branch writes or secret reads. Refusing rather than "
    "letting it through unchecked — check the hook or re-run the command."
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
    cmd = tool_input["command"]
    if not isinstance(cmd, str):
        deny(_PARSE_FAIL.format(why=f"command was {type(cmd).__name__}, not a string"))
    return cmd


def main() -> None:
    cmd = read_command()
    if not cmd.strip():
        sys.exit(0)
    if len(cmd) > MAX_COMMAND_CHARS:
        # Never silently allow what we did not analyse.
        ask(
            f"bash-guard did not inspect this command: it is {len(cmd):,} characters, "
            f"over the {MAX_COMMAND_CHARS:,}-character analysis limit. Approve only if "
            f"you are sure it does not commit/push to a protected branch or read secrets."
        )

    tokens = tokenize(cmd)
    if tokens is None:
        # Unparseable shell string (e.g. unbalanced quotes). The secret scan
        # can't guess at it — that half stays fail-open, as before — but the git
        # half still gets its regex fallback.
        check_git_unparseable(cmd)
        sys.exit(0)

    check_secret_read(tokens)
    if "git" in set(tokens):  # cheap gate before the invocation walk
        check_git(tokens)
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
            "Refusing rather than running it unchecked — fix the hook, then retry."
        )
