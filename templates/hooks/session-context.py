#!/usr/bin/env python3
"""Inject project context at session start (SessionStart hook).

Emits a compact additionalContext block with:
  - Current git branch, dirty status, recent commits
  - Sensitive files present in project root (reinforces the permissions.deny list)
  - Auto-detected quality commands from package.json / pyproject.toml / Makefile
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
PROTECTED = {"main", "master", "production", "release"}
parts: list[str] = []


def run(cmd: list[str]) -> str:
    try:
        out = subprocess.run(
            cmd, cwd=project_dir, capture_output=True, text=True, timeout=2,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


# ── Git state ──────────────────────────────────────────────────────────────
if (project_dir / ".git").exists():
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"]) or "?"
    dirty = len([l for l in run(["git", "status", "--porcelain"]).splitlines() if l])
    recent = run(["git", "log", "--oneline", "-3"])
    line = f"Branch: {branch}"
    if dirty:
        line += f" ({dirty} uncommitted changes)"
    if branch in PROTECTED:
        line += " — protected branch: create a feature branch before committing"
    parts.append(line)
    if recent:
        parts.append("Recent commits:\n" + "\n".join(f"  {l}" for l in recent.splitlines()))


# ── Sensitive files in project root ────────────────────────────────────────
sensitive: list[str] = []
for p in (".env", ".env.local", ".env.production", "id_rsa", "id_ed25519"):
    if (project_dir / p).exists():
        sensitive.append(p)
for ext in ("*.pem", "*.key"):
    for p in list(project_dir.glob(ext))[:5]:
        sensitive.append(p.name)
if sensitive:
    parts.append(f"Sensitive files present (denied by permissions): {', '.join(sensitive)}")


# ── Quality commands ───────────────────────────────────────────────────────
commands: list[str] = []

pkg_json = project_dir / "package.json"
if pkg_json.exists():
    try:
        scripts = json.loads(pkg_json.read_text()).get("scripts", {})
        for s in ("format", "lint", "test", "typecheck", "check"):
            if s in scripts:
                commands.append(f"{s}: npm run {s}")
    except (json.JSONDecodeError, OSError):
        pass

pyproject = project_dir / "pyproject.toml"
if pyproject.exists():
    try:
        content = pyproject.read_text(errors="replace")
        if "[tool.ruff" in content:
            commands.append("format: ruff format .")
            commands.append("lint: ruff check .")
        if "[tool.pytest" in content or "pytest" in content:
            commands.append("test: pytest")
        if "[tool.mypy" in content:
            commands.append("typecheck: mypy .")
    except OSError:
        pass

makefile = project_dir / "Makefile"
if makefile.exists():
    try:
        content = makefile.read_text(errors="replace")
        for target in ("format", "lint", "test", "check", "typecheck"):
            if re.search(rf"^{target}:", content, re.M):
                commands.append(f"{target}: make {target}")
    except OSError:
        pass

if commands:
    seen: set[str] = set()
    uniq = [c for c in commands if not (c in seen or seen.add(c))]
    parts.append("Quality commands detected:\n" + "\n".join(f"  - {c}" for c in uniq))


# ── Emit ───────────────────────────────────────────────────────────────────
if not parts:
    sys.exit(0)

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": "\n\n".join(parts),
    }
}))
