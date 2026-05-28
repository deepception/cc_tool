#!/usr/bin/env python3
"""Warn when the session's context window is filling up (Stop hook).

Reads the latest token-usage figures from the session transcript and prints a
one-line warning to stderr (visible to the user, NOT added to Claude's context)
when usage crosses a threshold, suggesting /compact or /clear.

Silent below threshold and on any error — never breaks the session. Parses
Claude Code's internal transcript JSONL, which is not a stable public API; if
the format changes this no-ops gracefully.

Tunables (env vars):
  CONTEXT_USAGE_LIMIT     token budget to measure against (default 200000)
  CONTEXT_USAGE_WARN_PCT  warn at/above this percent (default 80)
"""
import json
import os
import sys

LIMIT = int(os.environ.get("CONTEXT_USAGE_LIMIT", "200000"))
WARN_PCT = int(os.environ.get("CONTEXT_USAGE_WARN_PCT", "80"))


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return

    transcript = data.get("transcript_path", "")
    if not transcript or not os.path.isfile(transcript):
        return

    latest = None
    try:
        with open(transcript, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                usage = entry.get("message", {}).get("usage")
                if isinstance(usage, dict):
                    latest = usage
    except OSError:
        return

    if not latest:
        return

    used = (
        latest.get("input_tokens", 0)
        + latest.get("cache_read_input_tokens", 0)
        + latest.get("cache_creation_input_tokens", 0)
    )
    if used <= 0 or LIMIT <= 0:
        return

    pct = round(used / LIMIT * 100)
    if pct < WARN_PCT:
        return

    print(
        f"[context-usage] {used // 1000}K / {LIMIT // 1000}K tokens ({pct}%). "
        f"Consider /compact or /clear before the window fills.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
