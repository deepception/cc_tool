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
import shutil
import subprocess
import sys
import time
from pathlib import Path

project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
PROTECTED = {"main", "master", "production", "release"}
parts: list[str] = []

# Shared budget for ALL subprocess work, not per call: this hook is registered
# with "timeout": 3 in settings.json, and three independent 2 s timeouts could
# reach 6 s on a large or cold repo (git status --porcelain is the usual
# straggler) — the hook then gets killed and injects no context at all. One 2 s
# deadline across every run() keeps the worst case inside the registration with
# room for interpreter start-up.
_DEADLINE = time.monotonic() + 2.0


def run(cmd: list[str]) -> str:
    remaining = _DEADLINE - time.monotonic()
    if remaining <= 0.05:
        return ""
    try:
        out = subprocess.run(
            cmd, cwd=project_dir, capture_output=True, text=True, timeout=remaining,
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
        line += "; protected branch: create a feature branch before committing"
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


# ── Self-writing vault state (vault skill), if present ─────────────────────
vault = project_dir / "vault"
if (vault / "inbox").is_dir():
    inbox = [p for p in (vault / "inbox").iterdir() if p.is_file() and p.name != ".gitkeep"]
    line = f"Vault: {len(inbox)} unprocessed inbox file(s)" if inbox else "Vault: inbox empty"
    for sub, label in (("daily", "latest digest"), ("synthesis", "latest synthesis")):
        d = vault / sub
        if d.is_dir():
            newest = max((p.name for p in d.iterdir() if p.suffix == ".md"), default=None)
            if newest:
                line += f"; {label}: vault/{sub}/{newest}"
    parts.append(line)


# ── Software factory (factory skill), if the project has one ───────────────
_ORDER_ID = re.compile(r"^FO-[0-9]{1,6}$")
_ORDER_STATUSES = {"queued", "spec", "approved", "building", "verifying", "review", "done",
                   "blocked", "rejected", "failed"}
_MAX_ORDERS = 8


def _open_orders() -> str:
    """One line of open order ids and statuses, from ids/enums only: a title or any other
    free text from the ledger never reaches session context. Any failure returns ''."""
    remaining = _DEADLINE - time.monotonic()
    if remaining <= 0.05 or not shutil.which("cc-factory"):
        return ""
    try:
        out = subprocess.run(["cc-factory", "list", "--json"], cwd=project_dir, capture_output=True,
                             text=True, timeout=min(2.0, remaining))
        orders = json.loads(out.stdout) if out.returncode == 0 else []
        items: list[str] = []
        for o in orders:
            oid, status = o.get("id"), o.get("status")
            if not (isinstance(oid, str) and _ORDER_ID.match(oid)) or status not in _ORDER_STATUSES:
                continue
            if status in ("done", "rejected"):
                continue
            gate = o.get("gate")
            gname = gate.get("name") if isinstance(gate, dict) and gate.get("waiting") else None
            items.append(f"{oid} at the {gname} gate" if gname in ("spec", "merge") else f"{oid} {status}")
        if not items:
            return ""
        shown = "; ".join(items[:_MAX_ORDERS])
        if len(items) > _MAX_ORDERS:
            shown += f"; +{len(items) - _MAX_ORDERS} more"
        return f"Open orders: {shown}."
    except Exception:
        return ""


if (project_dir / "factory" / "line.json").is_file():
    parts.append("Software factory: work that needs a spec or a plan starts with the factory skill (intake); "
                 "small direct changes skip it.")
    orders_line = _open_orders()
    if orders_line:
        parts.append(orders_line)


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
