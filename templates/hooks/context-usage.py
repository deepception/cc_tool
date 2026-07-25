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
                          from the session model — 1,000,000 for the families in
                          NATIVE_1M_FAMILIES (Opus 5, Sonnet 5, Fable 5,
                          Mythos 5, Opus 4.8, Opus 4.7), 200,000 otherwise.
                          Set this to override.
  CONTEXT_USAGE_WARN_PCT  warn at/above this percent (default 80)
"""
import json
import os
import sys

WARN_PCT = int(os.environ.get("CONTEXT_USAGE_WARN_PCT", "80"))


# Model families Claude Code runs with a 1M-token context window. Everything
# else gets the 200K floor.
#
# Ground truth is the CLI's OWN model registry, not the API docs — the two
# disagree. Opus 4.6 and Sonnet 4.6 serve 1M through the API but are
# configured at 200K in the harness, and this hook measures a *harness*
# session. Re-check once per model release against the shipped binary:
#
#     grep -ao 'context:{window:[0-9]*[^}]*}' "$(command -v claude)" | sort -u
#
# That check is 30 seconds and is ground truth; both previous versions of this
# mapping were wrong because they were written from recall instead.
#
# Family substrings, so Bedrock/Vertex-prefixed and date-suffixed ids match.
# Note "opus-5" does not match "claude-opus-4-5", nor "sonnet-5" "claude-sonnet-4-5".
NATIVE_1M_FAMILIES = (
    "opus-5", "sonnet-5", "fable-5", "mythos-5",   # the lineup in use
    "opus-4-8", "opus-4-7",                        # still 1M in-harness
)


def context_limit(model):
    """Token budget for the warning.

    An explicit CONTEXT_USAGE_LIMIT always wins. Otherwise the model decides:
    families in NATIVE_1M_FAMILIES get 1M, everything else the 200K floor. A
    bracketed variant suffix (e.g. '[1m]', passed through by harnesses
    <2.1.173) is stripped before matching.

    Unknown models get 200K deliberately. The two failure directions are not
    symmetric in the way this hook's earlier comment claimed: over-estimating
    the window means the warning can *never* fire, and a guard that silently
    never fires is worse than one that fires early — you notice a false alarm
    and can set CONTEXT_USAGE_LIMIT, but you never notice silence. A new 1M
    model therefore warns early until it is added above; that is the cheap
    failure, and the model-recalibration workflow catches it each release.
    """
    env_limit = os.environ.get("CONTEXT_USAGE_LIMIT")
    if env_limit:
        try:
            return int(env_limit)
        except ValueError:
            pass
    base = (model or "").split("[", 1)[0]  # tolerate a '[1m]' variant suffix
    if any(family in base for family in NATIVE_1M_FAMILIES):
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
