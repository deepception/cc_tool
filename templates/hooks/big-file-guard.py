#!/usr/bin/env python3
"""Warn before reading very large files without pagination (PreToolUse Read hook).

Non-blocking: emits additionalContext so Claude sees the note but the read proceeds.
Saves context tokens by nudging toward Grep-then-Read-with-offset/limit for big files.
"""
import json
import os
import sys

THRESHOLD_BYTES = 200_000  # ≈ 2000–2500 lines at typical density

data = json.load(sys.stdin)
tool_input = data.get("tool_input", {})
path = tool_input.get("file_path", "")

if tool_input.get("offset") is not None or tool_input.get("limit") is not None:
    sys.exit(0)

try:
    size = os.path.getsize(path)
except OSError:
    sys.exit(0)

if size >= THRESHOLD_BYTES:
    kb = size // 1024
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": (
                f"Note: {path} is ~{kb}KB. Reading without offset/limit will consume "
                f"significant context. Prefer Grep to locate the relevant region, then "
                f"Read with offset/limit."
            ),
        }
    }))
