#!/usr/bin/env python3
"""Append current year to web searches that lack temporal anchoring (PreToolUse hook)."""
import json
import re
import sys
from datetime import datetime

data = json.load(sys.stdin)
query = data.get("tool_input", {}).get("query", "")
year = str(datetime.now().year)

has_year = re.search(r"\b20\d{2}\b", query)
has_temporal = any(w in query.lower() for w in ["latest", "recent", "current", "new", "now", "today"])

modified_query = f"{query} {year}" if not has_year and not has_temporal else query

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "modifiedToolInput": {"query": modified_query}
    }
}))
