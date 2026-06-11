#!/usr/bin/env python3
"""Warn when the session's context window is filling up (Stop hook).

Reads the latest token-usage figures from the session transcript and prints a
one-line warning to stderr (visible to the user, NOT added to Claude's context)
when usage crosses a threshold, suggesting /compact or /clear.

Silent below threshold and on any error — never breaks the session. Parses
Claude Code's internal transcript JSONL, which is not a stable public API; if
the format changes this no-ops gracefully.

Tunables (env vars):
  CONTEXT_USAGE_LIMIT     token budget to measure against. When unset, derived
                          from the session model (1,000,000 for Opus 4.8 /
                          Fable 5, 200,000 otherwise); set this to override.
  CONTEXT_USAGE_WARN_PCT  warn at/above this percent (default 80)
"""
import json
import os
import sys

WARN_PCT = int(os.environ.get("CONTEXT_USAGE_WARN_PCT", "80"))


def context_limit(model):
    """Token budget for the warning.

    An explicit CONTEXT_USAGE_LIMIT always wins. Otherwise derive from the
    session model: Opus 4.8 and Fable 5 run a 1M-token window by default, while
    4.7-and-earlier use 200K. A bracketed variant suffix (e.g. '[1m]', passed
    through by harnesses <2.1.173) is stripped before matching, and family
    substrings are used so Bedrock/Vertex-prefixed ids also match. (Opus 4.8 on
    Microsoft Foundry serves 200K under the same model id, so this
    over-estimates for that minority — it under-warns rather than spamming
    false alarms, which is the safer failure.) Unknown models fall back to the
    200K floor.
    """
    env_limit = os.environ.get("CONTEXT_USAGE_LIMIT")
    if env_limit:
        try:
            return int(env_limit)
        except ValueError:
            pass
    if model:
        base = model.split("[", 1)[0]  # tolerate a '[1m]' variant suffix (harness <2.1.173 passes it through)
        if "opus-4-8" in base or "fable-5" in base:
            return 1_000_000
    return 200_000


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return

    transcript = data.get("transcript_path", "")
    if not transcript or not os.path.isfile(transcript):
        return

    latest = None
    latest_model = None
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
                message = entry.get("message")
                if not isinstance(message, dict):
                    continue
                usage = message.get("usage")
                if isinstance(usage, dict):
                    latest = usage
                model = message.get("model")
                if isinstance(model, str) and model:
                    latest_model = model
    except OSError:
        return

    if not latest:
        return

    limit = context_limit(latest_model)

    used = (
        latest.get("input_tokens", 0)
        + latest.get("cache_read_input_tokens", 0)
        + latest.get("cache_creation_input_tokens", 0)
    )
    if used <= 0 or limit <= 0:
        return

    pct = round(used / limit * 100)
    if pct < WARN_PCT:
        return

    print(
        f"[context-usage] {used // 1000}K / {limit // 1000}K tokens ({pct}%). "
        f"Consider /compact or /clear before the window fills.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
