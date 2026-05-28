#!/usr/bin/env python3
"""Pre-flight guards for Bash commands (PreToolUse hook).

Blocks:
  - git commit on protected branches (main/master/production/release), unless --amend
  - git push to protected branches
  - any git command using --no-verify (bypasses project pre-commit/pre-push hooks)
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


data = json.load(sys.stdin)
cmd = data.get("tool_input", {}).get("command", "")

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
