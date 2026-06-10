#!/usr/bin/env python3
"""Post-edit fast typecheck/lint (PostToolUse hook on Edit|Write|MultiEdit).

WHAT IT DOES
  After an edit, runs a single FAST project checker over the project so that
  invented imports / undefined symbols fail loudly instead of lurking until a
  later run. It is the "Layer 3" feedback loop from the anti-hallucination
  playbook: a hook whose output is returned to Claude's context.

  Project type is sniffed from config at the repo root:
    - tsconfig.json                 → `tsc --noEmit`
    - pyproject.toml / setup.cfg /
      ruff.toml / .ruff.toml         → `ruff check`   (only if ruff is configured/available)
    - Cargo.toml                     → `cargo check`
  The checker only runs when the edited file's language matches (e.g. tsc only
  after editing .ts/.tsx, not after a .md edit). If no recognized config is
  present, or the edited file isn't a matching source file, it exits silently.

  INFORMATIONAL ONLY — never blocks. It prints a concise note back to Claude
  ONLY when the checker reports errors (naming the tool + first few lines), and
  stays completely silent on success to keep noise near zero. Any failure,
  timeout, or missing tool is swallowed: the hook never crashes a session.

  TIMEOUT BACK-OFF (tsc only) — a killed `tsc --noEmit` persists nothing, so on
  a repo whose cold check exceeds TIMEOUT every edit would silently re-pay the
  full stall. After a tsc timeout the hook drops a tiny marker in .git/ and
  skips the check for 30 minutes, then re-probes once. Delete the marker (or
  raise TIMEOUT below, plus the matching hook timeout in settings.json) to
  re-enable immediately. `cargo check` keeps its per-edit retry: killed runs
  still persist incremental progress in target/, so repeated attempts converge
  on their own.

OPT-IN / REMOVABLE
  This hook is deliberately lightweight and safe to remove. To disable it,
  delete the matching PostToolUse entry from .claude/settings.json (and,
  optionally, this file from .claude/hooks/). cc_tool prizes low noise — if the
  short timeout proves too tight for a large repo, raise TIMEOUT below (and
  the matching hook timeout in settings.json) or remove the hook entirely.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

TIMEOUT = 10  # seconds; the registered hook timeout (settings.json) is slightly higher
MAX_ERR_LINES = 12  # cap the output we surface to Claude
SKIP_TTL = 30 * 60  # seconds; after a tsc timeout, skip re-running the check this long


def _silent_exit():
    sys.exit(0)


def _find_repo_root(start: str) -> str:
    """Walk up from `start` to the first dir containing a .git, else CLAUDE_PROJECT_DIR."""
    d = os.path.abspath(start)
    if os.path.isfile(d):
        d = os.path.dirname(d)
    while True:
        if os.path.isdir(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())


def _skip_marker(root: str, tool: str) -> str:
    """Marker path under .git/ — never committed, never in the worktree, dies
    with the clone. Empty string when there is no .git dir (then no skip logic)."""
    git_dir = os.path.join(root, ".git")
    return os.path.join(git_dir, f"cc-hook-skip-{tool}") if os.path.isdir(git_dir) else ""


def _skip_fresh(marker: str) -> bool:
    if not marker:
        return False
    try:
        return (time.time() - os.path.getmtime(marker)) < SKIP_TTL
    except OSError:
        return False


def _remember_timeout(marker: str) -> None:
    if not marker:
        return
    try:
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write(
                f"post-edit-typecheck.py: project check exceeded {TIMEOUT}s; "
                f"skipped for {SKIP_TTL // 60}min. Safe to delete.\n"
            )
    except OSError:
        pass  # fail-open: worst case is today's behavior (a silent stall per edit)


def _surface(tool: str, output: str) -> None:
    """Return a concise error note to Claude's context (additionalContext)."""
    output = re.sub(r"\x1b\[[0-9;]*m", "", output)  # defense-in-depth: strip ANSI codes
    lines = [ln for ln in output.splitlines() if ln.strip()]
    head = lines[:MAX_ERR_LINES]
    more = "" if len(lines) <= MAX_ERR_LINES else f"\n… ({len(lines) - MAX_ERR_LINES} more lines)"
    note = (
        f"[post-edit-typecheck] `{tool}` reported errors after your edit:\n"
        + "\n".join(head)
        + more
        + "\nFix these before continuing (verify symbols/imports actually exist)."
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": note,
        }
    }))
    sys.exit(0)


def _run(cmd, cwd, timeout_marker=""):
    """Run a checker; return combined output if it FAILED, else None. Never raises.
    A timeout stays silent; if `timeout_marker` is set it is written so later
    edits skip a check that cannot finish (see _skip_fresh)."""
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        _remember_timeout(timeout_marker)
        return None  # stay silent; the marker stops the per-edit re-pay
    except (FileNotFoundError, OSError):
        return None  # tool vanished mid-run → stay silent
    if proc.returncode == 0:
        return None
    out = (proc.stdout or "") + (proc.stderr or "")
    return out.strip() or None


def _pyproject_uses_ruff(root: str) -> bool:
    """Cheap heuristic: ruff configured in pyproject.toml / setup.cfg, or a standalone
    ruff.toml/.ruff.toml exists. (PATH availability is checked by the caller.)"""
    for fn in ("pyproject.toml", "setup.cfg", "ruff.toml", ".ruff.toml"):
        p = os.path.join(root, fn)
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8", errors="ignore") as fh:
                    if "ruff" in fh.read():
                        return True
            except OSError:
                pass
    # Standalone ruff config files imply ruff even without the substring check above.
    return os.path.isfile(os.path.join(root, "ruff.toml")) or os.path.isfile(
        os.path.join(root, ".ruff.toml")
    )


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        _silent_exit()

    tool_input = data.get("tool_input", {}) or {}
    path = tool_input.get("file_path") or tool_input.get("path") or ""
    if not path:
        _silent_exit()

    ext = os.path.splitext(path)[1].lower()
    root = _find_repo_root(path)

    # ── TypeScript ────────────────────────────────────────────────────────────
    if ext in (".ts", ".tsx", ".mts", ".cts") and os.path.isfile(
        os.path.join(root, "tsconfig.json")
    ):
        # Prefer the project-local compiler; never fall back to npx (it can run a
        # squatted 'tsc' package and surface its banner as bogus "errors").
        local_tsc = shutil.which("tsc", path=os.path.join(root, "node_modules", ".bin"))
        if local_tsc:
            cmd = [local_tsc, "--noEmit"]
        elif shutil.which("tsc"):
            cmd = ["tsc", "--noEmit"]
        else:
            _silent_exit()  # no compiler available — silent exit per missing-tool contract
        marker = _skip_marker(root, "tsc")
        if _skip_fresh(marker):
            _silent_exit()  # a recent run couldn't finish within TIMEOUT; don't re-pay per edit
        out = _run(cmd, root, timeout_marker=marker)
        if out:
            _surface("tsc --noEmit", out)
        _silent_exit()

    # ── Python (ruff only — fast; never invoke a type checker here) ────────────
    if ext in (".py", ".pyi") and any(
        os.path.isfile(os.path.join(root, fn))
        for fn in ("pyproject.toml", "setup.cfg", "ruff.toml", ".ruff.toml")
    ):
        if not shutil.which("ruff") or not _pyproject_uses_ruff(root):
            _silent_exit()
        # Check just the edited file — fast and focused on what changed.
        out = _run(["ruff", "check", path], root)
        if out:
            _surface("ruff check", out)
        _silent_exit()

    # ── Rust ───────────────────────────────────────────────────────────────────
    if ext == ".rs" and os.path.isfile(os.path.join(root, "Cargo.toml")):
        if not shutil.which("cargo"):
            _silent_exit()
        # `cargo check` can be slow on a cold build, but killed runs persist
        # incremental progress in target/, so per-edit retries self-heal — no
        # skip marker here, unlike tsc.
        out = _run(["cargo", "check", "--quiet"], root)
        if out:
            _surface("cargo check", out)
        _silent_exit()

    _silent_exit()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Absolute backstop: a hook must never crash the session.
        sys.exit(0)
