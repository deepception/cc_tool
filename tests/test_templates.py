#!/usr/bin/env python3
"""Guard the templates cc_tool copies into projects against em-dashes (U+2014).

The managed CLAUDE.md block is replaced wholesale on every cc-update-project, so
a project cannot remove its em-dashes locally, and prose the agent reads every
session teaches it a style. The scaffold templates are copied once but seed new
projects, so they stay clean too. En-dashes in ranges (2–5) are fine.

Also checks that the snippet still carries the markers cc-setup and
cc-update-project search for: the start PREFIX and the full end marker, and
that hook message strings (not docstrings or comments) carry no em-dash.

Run:  python3 tests/test_templates.py
"""
import ast
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EM_DASH = "—"
START_PREFIX = "<!-- cc_tool:snippet:start"
END_MARKER = "<!-- cc_tool:snippet:end -->"

CLEAN_FILES = [
    "templates/CLAUDE_snippet.md",
    "templates/CLAUDE_template.md",
    "templates/vault/AUTOMATION.md",
    "templates/vault/README.md",
    "templates/vault/index.md",
    "templates/vault/log.md",
]

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
        print(f"FAIL {name}" + (f": {detail}" if detail else ""))


def read(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return f.read()


for rel in CLEAN_FILES:
    text = read(rel)
    hits = [str(n) for n, line in enumerate(text.splitlines(), 1) if EM_DASH in line]
    check(f"no em-dash in {rel}", not hits, "lines " + ", ".join(hits))

# Hook messages reach the agent as often as the managed block does. Docstrings
# and comments are for maintainers, so only non-docstring string literals count.
HOOKS = os.path.join(REPO, "templates", "hooks")
for name in sorted(os.listdir(HOOKS)):
    if not name.endswith(".py"):
        continue
    tree = ast.parse(read(os.path.join("templates", "hooks", name)))
    docstrings = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant):
            docstrings.add(id(body[0].value))
    hits = sorted({str(node.lineno) for node in ast.walk(tree)
                   if isinstance(node, ast.Constant) and isinstance(node.value, str)
                   and EM_DASH in node.value and id(node) not in docstrings})
    check(f"no em-dash in hook message strings of {name}", not hits, "lines " + ", ".join(hits))

snippet = read("templates/CLAUDE_snippet.md")
check("snippet has one start marker prefix", snippet.count(START_PREFIX) == 1)
check("snippet has one end marker", snippet.count(END_MARKER) == 1)
check("snippet starts with the start marker", snippet.startswith(START_PREFIX))
check("snippet start marker is a closed comment line",
      snippet.splitlines()[0].rstrip().endswith("-->"))
check("snippet ends with the end marker", snippet.rstrip().endswith(END_MARKER))

print(f"{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
