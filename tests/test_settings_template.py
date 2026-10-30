#!/usr/bin/env python3
"""Shape checks for templates/settings.json against bin/cc-setup's _RETIRED list.

Claude Code matches only Edit(path) for file permission checks, so a
Write(path) rule matches nothing and makes every session start print a
warning. These checks keep the template free of such rules, keep it from
re-shipping anything cc-setup retires, and keep each retired Write path
covered by its Edit counterpart. Read-only: nothing is written.

Run:  python3 tests/test_settings_template.py
"""
import ast
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(REPO, "templates", "settings.json")
CC_SETUP = os.path.join(REPO, "bin", "cc-setup")
DEVCONTAINER = os.path.join(REPO, "templates", "devcontainer", "managed-settings.json")

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
        print("FAIL: %s%s" % (name, (" -- " + detail) if detail else ""))


def load_retired():
    """Parse the _RETIRED dict literal out of bin/cc-setup's text."""
    text = open(CC_SETUP, encoding="utf-8").read()
    m = re.search(r"^_RETIRED = (\{.*?^\})", text, re.S | re.M)
    if not m:
        return None
    return ast.literal_eval(m.group(1))


def rules_of(path):
    perms = json.load(open(path, encoding="utf-8")).get("permissions", {})
    return {section: list(perms.get(section, [])) for section in ("allow", "ask", "deny")}


template = rules_of(TEMPLATE)
retired = load_retired()

check("_RETIRED parsed from bin/cc-setup", isinstance(retired, dict) and retired)
retired = retired or {}

# 1. No Write(path) rule anywhere in the shipped permissions.
write_rules = [(sec, r) for sec, rs in template.items() for r in rs if r.startswith("Write(")]
check("template has no Write( permission rule", not write_rules, str(write_rules))
if os.path.isfile(DEVCONTAINER):
    dc = rules_of(DEVCONTAINER)
    dc_write = [(sec, r) for sec, rs in dc.items() for r in rs if r.startswith("Write(")]
    check("devcontainer managed-settings has no Write( rule", not dc_write, str(dc_write))

# 2. Nothing retired is still shipped by the template.
for section, rules in retired.items():
    for r in rules:
        check("retired %s rule not in template: %s" % (section, r), r not in template.get(section, []))

# 3. Every retired Write rule keeps an Edit counterpart in deny.
retired_writes = [r for rs in retired.values() for r in rs if r.startswith("Write(")]
check("_RETIRED lists Write( rules", len(retired_writes) >= 6, str(retired_writes))
for r in retired_writes:
    edit = "Edit(" + r[len("Write("):]
    check("Edit counterpart in deny for %s" % r, edit in template["deny"])

# 4. No duplicate rules within a section.
for section, rs in template.items():
    dupes = sorted({r for r in rs if rs.count(r) > 1})
    check("no duplicate %s rules" % section, not dupes, str(dupes))

# 5. This repo's own .claude/settings.json (what a session working on cc_tool runs under)
#    pre-approves nothing that dumps the environment, runs as root, installs plugins or
#    names one machine's home directory, and nothing the template denies.
OWN = os.path.join(REPO, ".claude", "settings.json")
if os.path.isfile(OWN):
    own = rules_of(OWN)
    risky = re.compile(r"^Bash\((?:env|printenv|set|export -p|sudo\b|claude plugin\b|claude mcp\b)")
    bad = [r for r in own["allow"] if risky.match(r)]
    check(".claude/settings.json allows no env dump, sudo or plugin/MCP install", not bad, str(bad))
    home = [r for r in own["allow"] if "/home/" in r or "/Users/" in r]
    check(".claude/settings.json names no machine's home directory", not home, str(home))
    denied = sorted(set(own["allow"]) & set(template["deny"]))
    check(".claude/settings.json allows nothing the template denies", not denied, str(denied))

print("%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
