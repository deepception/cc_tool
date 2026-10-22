#!/usr/bin/env python3
"""Guard the templates cc_tool copies into projects against em-dashes (U+2014).

The managed CLAUDE.md block is replaced wholesale on every cc-update-project, so
a project cannot remove its em-dashes locally, and prose the agent reads every
session teaches it a style. The scaffold templates are copied once but seed new
projects, so they stay clean too. En-dashes in ranges (2–5) are fine.

Also checks that the snippet still carries the markers cc-setup and
cc-update-project search for: the start PREFIX and the full end marker, and
that hook message strings (not docstrings or comments) carry no em-dash, and that
every file under templates/skills/ is clean apart from a small allowlist.

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

# Project skills are copied into every project and read by the agent each
# session, and cc-update-project refreshes unmodified ones, so a project cannot
# remove an em-dash locally. Every file under templates/skills/ is clean except
# the allowlist below. Add to it only for verbatim vendored files or for code
# that depends on the character, never for prose cc_tool wrote.
SKILLS = os.path.join(REPO, "templates", "skills")

# Whole files kept byte-for-byte as upstream shipped them.
VENDORED_VERBATIM = {
    # SKILL.md header: "Copied verbatim ... cc_tool has made no changes to the body."
    "scroll-world/SKILL.md",
    # docs/guides/layout.md: scroll-world references/ are "vendored verbatim".
    "scroll-world/references/index-template.html",
    "scroll-world/references/knockout.py",
    "scroll-world/references/pipeline.md",
    "scroll-world/references/prompts.md",
    "scroll-world/references/scrub-engine.js",
}

# Single lines of cc_tool code where the character is data, not prose. Matched
# by exact line content, so any other em-dash in the same file still fails.
FUNCTIONAL_LINES = {
    # Treats an em-dash in user copy as a word separator when counting words.
    "hyperframes-movie/scripts/reading_audit.py": {
        '    return len([w for w in text.replace("·", " ").replace("—", " ").split() if any(ch.isalnum() for ch in w)])',
    },
}

for rel in sorted(VENDORED_VERBATIM | set(FUNCTIONAL_LINES)):
    check(f"allowlisted skill file exists: {rel}", os.path.isfile(os.path.join(SKILLS, rel)))

for root, _dirs, files in sorted(os.walk(SKILLS)):
    for fname in sorted(files):
        path = os.path.join(root, fname)
        rel = os.path.relpath(path, SKILLS).replace(os.sep, "/")
        if rel in VENDORED_VERBATIM:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except UnicodeDecodeError:
            continue  # binary asset
        allowed = FUNCTIONAL_LINES.get(rel, set())
        hits = [str(n) for n, line in enumerate(lines, 1) if EM_DASH in line and line not in allowed]
        check(f"no em-dash in skills/{rel}", not hits, "lines " + ", ".join(hits))

# Skill frontmatter must be valid YAML with string values: a ": " left unquoted
# in a description (easy to introduce when rewriting a dash as a colon) breaks
# the whole block, and an unquoted `[a|b]` argument-hint parses as a list.
try:
    import yaml
except ImportError:
    yaml = None
    print("note: PyYAML not installed, skill frontmatter check skipped")
if yaml is not None:
    for path in sorted(os.listdir(os.path.join(REPO, "templates", "skills"))):
        rel = os.path.join("templates", "skills", path, "SKILL.md")
        if not os.path.isfile(os.path.join(REPO, rel)):
            continue
        text = read(rel)
        try:
            fm = yaml.safe_load(text.split("\n---", 1)[0][3:])
        except yaml.YAMLError as e:
            check(f"frontmatter parses in {rel}", False, str(e).splitlines()[0])
            continue
        check(f"frontmatter parses in {rel}", isinstance(fm, dict))
        for key in ("name", "description", "argument-hint"):
            if isinstance(fm, dict) and key in fm:
                check(f"{key} is a string in {rel}", isinstance(fm[key], str), type(fm[key]).__name__)

print(f"{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
