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

Asks (hands the decision to the user; in an unattended run that is a refusal):
  - `git reset --hard`, `git clean -f`, `git checkout .`, `git branch -D`,
    `git stash drop|clear`, history rewrites; `rm -r node_modules`/`.venv`/…;
    docker prune / `rm -f` / `down -v`; `kubectl delete --all` / drain; helm
    uninstall; terraform/pulumi destroy or auto-approve; cloud CLI deletes;
    DROP / TRUNCATE / DELETE-without-WHERE when a DB client is in the command;
    redis FLUSHALL; `crontab -e`.

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
import os
import re
import shlex
import subprocess
import sys

try:
    import cc_hooklib as lib
except Exception:  # noqa: BLE001 - the guard must work even if the helper is missing
    lib = None

PROTECTED = {"main", "master", "production", "release"}
# Longest command we tokenize. Beyond this we cannot analyse safely, so we warn
# loudly and defer to the user rather than silently allowing (see main()).
MAX_COMMAND_CHARS = 400_000

_CMD_FOR_LOG = ""      # set by main() so deny/ask can log what they refused
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


def deny(reason: str, suggestion: str = "") -> None:
    """Refuse the command. The message follows one shape for every cc_tool guard:
    `BLOCKED: <what and why>. Suggestion: <the safe form>.` The managed CLAUDE.md
    block teaches the model to take the suggestion rather than rephrase around
    the guard."""
    msg = _fmt(reason, suggestion)
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
    _log("ask", msg)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": msg,
        }
    }))
    sys.exit(0)


def warn(note: str) -> None:
    """Queue a non-blocking note; main() emits them as additionalContext."""
    _WARNINGS.append(note)


def flush_warnings() -> None:
    if not _WARNINGS:
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
            "bypass pre-commit/pre-push hooks via --no-verify. "
            "Fix the underlying failure (lint/format/test) instead of skipping the check."
        )
    if re.search(r"(^|[\s;&|])git\s+commit\b", cmd) and not re.search(r"--amend\b", cmd):
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
# Deleting these is never "just cleaning up": lockfiles, ignore rules, CI, container
# and migration files carry state that is expensive or impossible to recreate.
_PROTECTED_BASENAMES = {
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb", "bun.lock",
    "uv.lock", "poetry.lock", "Pipfile.lock", "Cargo.lock", "Gemfile.lock",
    "go.sum", "composer.lock", "flake.lock", "mix.lock",
    ".gitignore", ".gitattributes", "Dockerfile", "docker-compose.yml",
    "docker-compose.yaml", "compose.yml", "compose.yaml",
    ".gitlab-ci.yml", "Jenkinsfile", ".travis.yml",
}
_PROTECTED_PATH_PARTS = ("/.github/workflows/", "/migrations/", "/.github/")
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
            while i < n and (seg[i].startswith("-") or (name == "env" and "=" in seg[i])):
                i += 1
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
        if t.startswith("/") and (t in _RM_SYSTEM_PREFIXES or t.startswith(tuple(p + "/" for p in _RM_SYSTEM_PREFIXES))):
            deny(f"'rm' aimed at system path '{t}'", "the project never needs files under there removed")
        if _is_protected_file(t):
            deny(f"deleting '{t}' throws away state that is expensive to recreate (lockfile, ignore "
                 "rules, CI config, container file, or migration)",
                 "if it truly must go, ask the user to delete it; regenerate lockfiles with the package manager")
        if recursive and t.rsplit("/", 1)[-1] in ("node_modules", ".venv", "venv", "target"):
            ask(f"'rm -r' on '{t}' deletes a build/dependency tree; reinstalling is slow but not destructive",
                "confirm this is intended rather than a stuck build that a clean rebuild would fix")


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


def _check_git_destructive(tokens) -> None:
    for sub, _gopts, args, _repo in git_invocations(tokens):
        if sub == "push":
            forced = "--force" in args or any(
                "f" in _short_flags(a) and "F" not in _short_flags(a) for a in args
                if _short_flags(a) and a not in ("-u",))
            if forced and "--force-with-lease" not in args and not any(a.startswith("--force-with-lease=") for a in args):
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
        ):
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
        for i, nm in enumerate(names):
            later = names[i + 1:]
            if nm in _FETCHERS and any(x in _INTERPRETERS for x in later):
                deny("downloading a script and piping it straight into a shell or interpreter runs "
                     "unreviewed remote code",
                     "download to a file, read it, then run it deliberately; or install through the package manager")
            if nm in _ENV_DUMPERS and any(x in _NET_SINKS for x in later):
                deny("piping the environment (which holds every secret this shell can see) into a network tool "
                     "is exfiltration", "print the one variable you need, never the whole environment")
            if nm in ("cat", "grep", "awk", "base64", "xxd") and any(x in _NET_SINKS for x in later):
                if any(_is_secret_token(t) for s in stages[:i + 1] for t in s[1:]):
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
        else:
            _check_infra(real, wrappers)
    _check_pipelines(tokens)
    _check_redirect_targets(tokens)
    _check_sql(cmd, tokens)
    if "git" in set(tokens):
        _check_git_destructive(tokens)


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
    for sub, _g, args, _r in git_invocations(tokens) if "git" in set(tokens) else []:
        if sub == "apply" and "--check" not in args and "--stat" not in args:
            warn(_bypass_note("the patched files", "git apply"))
            return


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
    global _CMD_FOR_LOG
    cmd = read_command()
    if not cmd.strip():
        sys.exit(0)
    _CMD_FOR_LOG = cmd
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
        check_project_rules(cmd)
        flush_warnings()
        sys.exit(0)

    check_secret_read(tokens)
    if "git" in set(tokens):  # cheap gate before the invocation walk
        check_git(tokens)
    check_destructive(cmd, tokens)
    check_project_rules(cmd)
    check_write_bypass(tokens)
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
            "Refusing rather than running it unchecked — fix the hook, then retry."
        )
