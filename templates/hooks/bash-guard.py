#!/usr/bin/env python3
"""Pre-flight guards for Bash commands (PreToolUse hook).

Blocks:
  - git commit on protected branches (main/master/production/release), unless --amend
  - git push to protected branches
  - any git command using --no-verify (bypasses project pre-commit/pre-push hooks)
"""
import json
import re
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

if re.search(r"(^|[\s;&|])git\s+push\b", cmd):
    m = re.search(r"git\s+push(?:\s+-\S+)*(?:\s+(\S+)(?:\s+(\S+))?)?", cmd)
    target = ""
    if m and m.group(2):
        target = m.group(2).split(":")[-1].lstrip("+")
    else:
        target = current_branch()
    if target in PROTECTED:
        deny(
            f"Refusing to push to protected branch '{target}'. "
            f"Open a pull request from a feature branch instead."
        )

sys.exit(0)
