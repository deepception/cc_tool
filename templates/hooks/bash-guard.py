#!/usr/bin/env python3
"""Pre-flight guards for Bash commands (PreToolUse hook).

Blocks:
  - git commit on protected branches (main/master/production/release), unless --amend
  - git push to protected branches
  - any git command using --no-verify (bypasses project pre-commit/pre-push hooks)
  - reads of known secret files via tools Claude Code's Read-deny can't see
    (grep/awk/source/xargs cat/python -c open(...)/node -e ...), e.g. `grep KEY .env`.
    Claude's permission deny already covers Read + recognized cat/head/tail/sed, so
    this only fills the gap for arbitrary readers. Conservative: only fires on a
    clear secret-path reference, so benign commands (grep -r TODO src/) pass through.
"""
import json
import re
import shlex
import subprocess
import sys

PROTECTED = {"main", "master", "production", "release"}


def deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def current_branch() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


_SHELL_OP_CHARS = set("();<>|&")
_GIT_GLOBAL_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
_PUSH_VALUE_OPTS = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}


def parse_git_push(cmd: str):
    """Inspect every `git push` invocation in cmd, each scoped to its own command.

    Returns (found, dests, push_all, bare, parsed):
      found    — at least one real `git ... push` token sequence (not quoted text)
      dests    — destination refs explicitly named (the dst side of each refspec)
      push_all — True if any invocation uses `--all` / `--mirror` (updates all branches)
      bare     — True if any invocation names no refspec (so it pushes the current branch)
      parsed   — False only if tokenizing failed (caller falls back to regex)

    Tokenizing with punctuation_chars treats ();<>|& (and ';') as standalone
    tokens, so chained commands (`git push … && rm …`), comments, and quoted
    text don't bleed into the parse.
    """
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        return False, [], False, False, False

    found = False
    dests = []
    push_all = False
    bare = False
    n = len(tokens)
    i = 0
    while i < n:
        if tokens[i] != "git":
            i += 1
            continue
        j = i + 1
        while j < n and tokens[j].startswith("-"):
            j += 2 if tokens[j] in _GIT_GLOBAL_VALUE_OPTS else 1
        if j >= n or tokens[j] != "push":
            i = j + 1
            continue

        found = True
        positionals = []
        k = j + 1
        while k < n:
            t = tokens[k]
            if t and all(c in _SHELL_OP_CHARS for c in t):
                break  # end of this simple command
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
            bare = True  # no refspec → pushes the current branch
        for refspec in refspecs:
            dst = refspec.split(":")[-1].lstrip("+")
            if dst.startswith("refs/heads/"):
                dst = dst[len("refs/heads/"):]
            dests.append(dst)
        i = k

    return found, dests, push_all, bare, True


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


def check_secret_read(cmd: str) -> None:
    """Deny Bash commands that read a known secret path via an uncovered reader.

    Conservative by construction: requires BOTH a recognized reader/interpreter
    AND a clear secret-path token, so normal commands pass untouched.
    """
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        return  # unparseable (e.g. unbalanced quotes) — fail open, don't guess

    n = len(tokens)
    for i, tok in enumerate(tokens):
        # 1) reader followed somewhere by a secret-path argument
        if tok in _SECRET_READERS:
            if tok in (".", "source") and not (
                i == 0 or (tokens[i - 1] and all(c in _SHELL_OP_CHARS for c in tokens[i - 1]))
            ):
                continue  # '.'/'source' as an argument (find . ..., rsync ... .) is not a reader
            # First non-flag token after a grep-family command is the search pattern,
            # not a file — skip exactly one such token.
            skip_pattern = tok in _PATTERN_FIRST_READERS
            for nxt in tokens[i + 1:]:
                if nxt and all(c in _SHELL_OP_CHARS for c in nxt):
                    break  # don't cross into the next simple command
                if nxt.startswith("-"):
                    continue
                if skip_pattern:
                    skip_pattern = False
                    continue
                if _is_secret_token(nxt):
                    deny(
                        f"Refusing to read secret material ('{nxt.strip(chr(39) + chr(34))}') "
                        f"via '{tok}'. These files (.env, keys, credential stores) are "
                        f"blocked for a reason; don't exfiltrate or echo their contents."
                    )
        # 2) `xargs cat <secretfile>` and friends — flag a secret token anywhere
        #    that follows xargs (the piped reader is opaque to Read-deny).
        if tok == "xargs":
            for nxt in tokens[i + 1:]:
                if nxt and all(c in _SHELL_OP_CHARS for c in nxt):
                    break
                if not nxt.startswith("-") and _is_secret_token(nxt):
                    deny(
                        f"Refusing 'xargs' pipeline that targets secret material "
                        f"('{nxt.strip(chr(39) + chr(34))}')."
                    )
        # 3) inline interpreter code: python -c "open('.env')", node -e "...id_rsa..."
        if tok in _INLINE_INTERP:
            for j in range(i + 1, n):
                arg = tokens[j]
                if arg and all(c in _SHELL_OP_CHARS for c in arg):
                    break
                if arg in ("-c", "-e", "--eval", "--exec"):
                    if j + 1 < n and _inline_has_secret(tokens[j + 1]):
                        deny(
                            "Refusing inline interpreter code that opens secret material "
                            "(.env / keys / credential stores). Read of these paths is "
                            "blocked; don't bypass it via -c/-e."
                        )


def _inline_has_secret(code: str) -> bool:
    """Scan an inline -c/-e string for a quoted secret-path literal."""
    for m in re.finditer(r"""['"]([^'"]+)['"]""", code):
        if _is_secret_token(m.group(1)):
            return True
    return False


data = json.load(sys.stdin)
cmd = data.get("tool_input", {}).get("command", "")

# Secret-read guard runs for ALL Bash commands (not just git).
check_secret_read(cmd)

if not re.search(r"(^|[\s;&|])git\s", cmd):
    sys.exit(0)

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

# Cheap gate; parse_git_push is the source of truth for whether a real push exists
# (it also handles `git -C <dir> push`, which a `git\s+push` regex would miss).
if "push" in cmd and re.search(r"(^|[\s;&|])git\b", cmd):
    found, dests, push_all, bare, parsed = parse_git_push(cmd)
    if not parsed:
        # Tokenizing failed (e.g. unbalanced quotes) — best-effort regex fallback.
        m = re.search(r"git\s+(?:-\S+\s+)*push(?:\s+-\S+)*(?:\s+(\S+)(?:\s+(\S+))?)?", cmd)
        found = bool(m)
        if m and m.group(2):
            dests, bare = [m.group(2).split(":")[-1].lstrip("+")], False
        elif m:
            dests, bare = [], True
        push_all = bool(re.search(r"(^|\s)(--all|--mirror)(\s|$)", cmd))
    if found:
        if push_all:
            deny(
                "Refusing 'git push --all'/'--mirror': it updates every branch, "
                "including protected ones (main/master/production/release). "
                "Push specific feature branches instead."
            )
        targets = list(dests)
        if bare:  # no refspec → git pushes the current branch
            targets.append(current_branch())
        for target in targets:
            if target in PROTECTED:
                deny(
                    f"Refusing to push to protected branch '{target}'. "
                    f"Open a pull request from a feature branch instead."
                )

sys.exit(0)
