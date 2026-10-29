#!/usr/bin/env python3
"""bin/cc-docs-migrate: the docs-standard migration tool (contract v2 (v1 + the skeptic-review overrides)).

Every scenario runs the CLI as a subprocess in a throwaway git repository (temp HOME and a
private XDG_STATE_HOME, GIT_* variables removed) and checks:
  1. dry run: nothing written anywhere (snapshot of every path, dirs included, before == after),
     no log, no backups; --json is one object with the contract's keys, every walked doc, sorted
  2. classification and planned keys: spec / plan / task / tracker / guide / adr / note, spec
     status and links, plan source (no status), task (no status), created from the file name,
     a vault note gets only title, a merge keeps existing lines byte-identical and extends a
     one-line flow `links` list
  3. skips with exact reasons: both malformed forms (with the line number), generated file,
     referenced by code, symlink (target untouched), vault-owned, not covered
  4. link fixes: absolute and too-deep links become relative; https, [[wikilinks]] and links in
     a code fence unchanged; broken links are notes, not changes
  5. apply: exit 0, files as planned, the log (start / write / end, sha256 before and after match
     the disk), backups with the old bytes, docs/.migration-backup/.gitignore, skipped files and
     vault raw/index/log byte-identical, CRLF files keep CRLF
  6. plans.py: parse_plan before and after gives the same progress; is_plan unchanged
  7. idempotence: a second apply changes nothing (log byte-identical, nothing to write)
  8. undo: every migrated file restored byte-identical, the undo line, a second undo has nothing
     to undo, a file edited after the migration is kept
  9. no file is ever deleted: across dry run, apply, re-apply and undo the path set only grows
 10. refusals: dirty tree (exit 2, one BLOCKED line), --allow-dirty, non-git directory, symlinked
     docs/, --with-orders without factory/orders/, usage errors (exit 1)
 11. orders: exactly one order via cc-factory for the open backlog task, the order file, the
     task's order key, no second order on a rerun, undo keeps the order, a fresh apply links it
 12. --only plans: only plan entries, only plan files written

python3 tests/test_docs_migrate.py [--keep]
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.realpath("/proc/self/exe") if os.path.exists("/proc/self/exe") else sys.executable
CLI = os.path.join(REPO, "bin", "cc-docs-migrate")
FACTORY = os.path.join(REPO, "bin", "cc-factory")
RESULTS = []
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from state_guard import StateGuard  # noqa: E402

STATE_GUARD = StateGuard()  # before any environment override
BASE = os.path.realpath(tempfile.mkdtemp(prefix="cc-docs-migrate-"))
HOME = os.path.join(BASE, "home")
STATE = os.path.join(BASE, "state")
os.makedirs(HOME)
os.makedirs(STATE)
sys.path.insert(0, os.path.join(REPO, "lib"))
from cc_dash import plans as P  # noqa: E402

_counter = [0]


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    if not cond:
        print("FAIL %s  %s" % (name, str(detail)[:400]))


def env_for():
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("GIT_", "PYTHON", "CLAUDE_")) and k != "XDG_CONFIG_HOME"}
    env.update(HOME=HOME, XDG_STATE_HOME=STATE, XDG_CONFIG_HOME=os.path.join(HOME, ".config"),
               GIT_TERMINAL_PROMPT="0", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    return env


def git(cwd, *args):
    r = subprocess.run(["git", "-c", "commit.gpgsign=false"] + list(args), cwd=cwd, env=env_for(),
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), r.stderr))
    return r


def commit(d, msg="c"):
    git(d, "add", "-A")
    git(d, "commit", "-q", "--allow-empty", "-m", msg)


def run(args, cwd=None):
    return subprocess.run([PY, CLI] + list(args), cwd=cwd or BASE, env=env_for(), capture_output=True,
                          text=True, timeout=120)


def jrun(args, cwd=None):
    r = run(list(args) + ["--json"], cwd)
    try:
        obj = json.loads(r.stdout)
    except ValueError:
        obj = None
    return r, obj


def sha(b):
    return hashlib.sha256(b).hexdigest()


def rd(root, rel):
    with open(os.path.join(root, rel), "rb") as fh:
        return fh.read()


def wr(root, rel, data):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(data if isinstance(data, bytes) else data.encode("utf-8"))


def snap(root):
    """path -> (kind, payload) for everything but .git; walks with lstat, dirs included."""
    out = {}
    for dp, dns, fns in os.walk(root):
        if dp == root and ".git" in dns:
            dns.remove(".git")
        for n in dns + fns:
            p = os.path.join(dp, n)
            rel = os.path.relpath(p, root)
            st = os.lstat(p)
            if os.path.islink(p):
                out[rel] = ("l", os.readlink(p))
            elif os.path.isdir(p):
                out[rel] = ("d", None)
            else:
                out[rel] = ("f", rd(root, rel))
    return out


def user_files(s):
    """Snapshot entries that are migration artifacts excluded."""
    return {k: v for k, v in s.items() if not k.startswith("docs/.migration") and k != "docs/.migration-log.jsonl"
            and not k.startswith("factory")}


def entry(js, path):
    for e in (js or {}).get("docs", []):
        if e["path"] == path:
            return e
    return None


def fm_lines(data):
    t = data.decode("utf-8")
    ls = t.replace("\r\n", "\n").split("\n")
    if not ls or ls[0] != "---":
        return None
    for i in range(1, len(ls)):
        if ls[i] == "---":
            return ls[1:i]
    return None


def read_log(root):
    p = os.path.join(root, "docs", ".migration-log.jsonl")
    if not os.path.exists(p):
        return []
    out = []
    for ln in rd(root, "docs/.migration-log.jsonl").decode().split("\n"):
        if ln.strip():
            out.append(json.loads(ln))
    return out


# ---------------------------------------------------------------------------------- fixture
SPEC1 = "docs/superpowers/specs/2026-10-01-widget-design.md"
PLAN1 = "docs/superpowers/plans/2026-10-02-widget.md"
SPEC2 = "docs/superpowers/specs/2026-10-03-other-design.md"
PLAN2 = "docs/superpowers/plans/2026-10-04-other.md"
MON = "docs/tasks/backlog/monitoring.md"
SCAF = "docs/tasks/completed/scaffold.md"
SETUP = "docs/guides/setup.md"
BIGFM = "docs/bigfm.md"

PLAN1_TEXT = """# Widget Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development.

**Goal:** Build the widget.

**Spec:** docs/superpowers/specs/2026-10-01-widget-design.md

See the [design](../docs/superpowers/specs/2026-10-01-widget-design.md) for context.

## Phase 1

### Task 1: Parser

- [x] **Step 1: Write the failing test**
- [x] **Step 2: Implement**

### Task 2: Store

- [x] **Step 1: Schema**
- [ ] **Step 2: Wire it, see [spec](../docs/superpowers/specs/2026-10-01-widget-design.md)**

```markdown
- [ ] example inside a fence
- [ ] another example inside a fence
```

## Phase 2

### Task 3: API

- [ ] **Step 1: Routes**
- [ ] **Step 2: Tests**

### Task 4: Docs

- [ ] **Step 1: Write them**
"""

PLAN2_TEXT = """# Other Plan

**Goal:** Do the other thing.

**Spec:** `docs/superpowers/specs/2026-10-03-other-design.md`

### Task 1: One

- [ ] **Step 1: a**
- [ ] **Step 2: b**
"""


def build(root_name="proj", factory=False):
    _counter[0] += 1
    root = os.path.join(BASE, "%s-%d" % (root_name, _counter[0]))
    os.makedirs(root)
    git(root, "init", "-q")
    w = lambda rel, text: wr(root, rel, text)  # noqa: E731
    w("README.md", "# Project\n\nSee [setup](docs/guides/setup.md).\n")
    w(SPEC1, "# Widget Design\n\n**Status:** Approved\n\n## Context\nWhy.\n\n## Decision\nWhat.\n")
    w(PLAN1, PLAN1_TEXT)
    w(SPEC2, "---\nsummary: other\nlinks: [docs/x.md]\n---\n# Other Design\n\nBody.\n")
    w(PLAN2, PLAN2_TEXT)
    w(MON, "# Monitoring\n\n## Summary\nAdd basic uptime monitoring to the widget service.\n\n## Checklist\n"
           "- [ ] Add uptime probe\n- [ ] Alert on 5xx\n- [x] Pick tool\n")
    w(SCAF, "# Scaffold\n\n- [x] Create repo\n- [x] Add CI\n")
    w("docs/tasks/README.md", "# Tasks\n\nIndex of tasks.\n")
    w(SETUP, "# Setup Guide\n\nSee the [backlog item](%s/%s) and [site](https://example.com/a.md).\n"
             "Also [[wiki-link]] and [gone](missing/file.md) and [idea](notes/idea.md).\n\n"
             "```\n[inside fence](%s/docs/adr/0001-use-sqlite.md)\n```\n" % (root, MON, root))
    w("docs/TASKS.md", "# Tasks\n\n- [ ] one\n- [ ] two\n- [x] three\n")
    w("docs/notes/idea.md", "# An Idea\n\nJust a thought.\n")
    w("docs/adr/0001-use-sqlite.md", "# 1. Use SQLite\n\n**Status:** Accepted\n\n## Context\nx\n\n## Decision\ny\n")
    w("docs/malformed.md", "---\ntitle: never closed\n# Heading\nBody\n")
    w("docs/badkey.md", "---\ntitle: x\nthis is not a pair\n---\n# Bad\n")
    w("docs/seo/GUIDELINES.md", "# SEO Guidelines\n\nRules.\n")
    w("scripts/check_seo.py", "# reads GUIDELINES.md\nprint('seo')\n")
    w("docs/generated.md", "DO NOT EDIT: generated by build\n# Generated\n")
    w("docs/crlf.md", b"# CRLF Doc\r\n\r\nBody line.\r\n")
    w("docs/existing-fm.md", "---\nsummary: x\nlinks: [docs/x.md]\n---\n# Existing\n")
    w("docs/noheading.md", "Just text without a heading.\n")
    w("docs/colon.md", "## Plan: part one\n\nBody.\n")
    w("docs/AUDIT-2026-FIXPLAN.md", "# Audit Fix Plan\n\n- [ ] a\n- [ ] b\n- [ ] c\n")
    w("docs/style/GUIDELINES-draft.md", "# Style Draft\n\nWords.\n")
    w("docs/superpowers/plans/2026-10-03-round/LOG.md", "# Round log\n\n- 10:00 started\n")
    w("docs/superpowers/plans/2026-10-03-round/reviews/spec-review.md", "# Spec review\n\nLooks fine.\n")
    w("docs/plans/rollout/PLAN.md", "# Rollout\n\nShip it in two waves.\n")
    w(BIGFM, "---\n" + "".join("k%d: v\n" % i for i in range(99)) + "---\n# Big\n")
    w("vault/notes/idea-b.md", "# Idea B\n\nSee [setup](docs/guides/setup.md).\n")
    w("vault/README.md", "# Vault\n")
    w("vault/index.md", "# Index\n\n- [[idea-a]]\n- [[idea-b]]\n")
    w("vault/log.md", "# Log\n\n- did a thing\n")
    w("vault/inbox/.gitkeep", "")
    w("vault/raw/2026-07-09-import/orig.md", "# Original\n\nraw text\n")
    w("vault/notes/idea-a.md", "---\nsummary: a thing\ntype: research\nstatus: seed\ndate: 2026-07-09\ntags: [a]\n---\n"
                               "# Idea A\n\nSee [[other-note]].\n")
    w("vault/daily/2026-07-10.md", "# 2026-07-10\n\nToday.\n")
    os.symlink("guides/setup.md", os.path.join(root, "docs", "link.md"))
    commit(root, "fixture")
    if factory:
        r = subprocess.run([PY, FACTORY, "-C", root, "init"], env=env_for(), capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError("cc-factory init failed: %s %s" % (r.stdout, r.stderr))
        commit(root, "factory init")
    return root


def walked(root):
    out = []
    for top in ("docs", "vault"):
        for dp, dns, fns in os.walk(os.path.join(root, top)):
            dns[:] = [d for d in dns if not d.startswith(".")]
            for n in fns:
                if n.lower().endswith(".md"):
                    out.append(os.path.relpath(os.path.join(dp, n), root))
    return sorted(out)


def plan_sig(text):
    p = P.parse_plan(text)
    return (p["title"], p["mode"], p["checkboxes"], p["steps_total"], p["steps_done"],
            [(t["id"], len(t["steps"]), t["done_steps"]) for t in p["tasks"]],
            p["goal"], [(t["title"], [s["text"] for s in t["steps"]]) for t in p["tasks"]])


# ---------------------------------------------------------------------------------- cases
PATH_HISTORY = []


def track(label, root):
    cur = set(snap(root))
    if PATH_HISTORY:
        gone = PATH_HISTORY[-1][1] - cur
        check("no file deleted: after %s" % label, not gone, sorted(gone)[:5])
    PATH_HISTORY.append((label, cur))


TOP_KEYS = {"v", "project", "mode", "run", "dirty", "docs", "summary", "warnings"}
ENTRY_KEYS = {"path", "tree", "type", "plan", "reason", "frontmatter", "add", "links", "order", "notes", "written"}
SUM_KEYS = {"order_candidates", "status_lines", "docs", "write", "add", "merge", "link_files", "links_fixed", "orders_new", "orders_linked",
            "skipped", "broken", "orphans", "adr_candidates"}


def case_main():
    root = build()
    S0 = snap(root)
    PATH_HISTORY.clear()
    track("fixture", root)
    # --- 1 dry run
    r = run([root])
    check("dry text: exit 0", r.returncode == 0, r.stderr)
    check("dry text: header and summary", r.stdout.startswith("cc-docs-migrate: " + root)
          and "summary:" in r.stdout.strip().split("\n")[-1], r.stdout[:200])
    check("dry text: vault-owned grouped", re.search(r"vault/raw/.*vault-owned", r.stdout) and "orig.md" not in r.stdout,
          r.stdout)
    S1 = snap(root)
    check("dry text: snapshot identical", S1 == S0, [k for k in set(S0) | set(S1) if S0.get(k) != S1.get(k)][:5])
    r, js = jrun([root])
    check("dry json: exit 0", r.returncode == 0, r.stderr)
    check("dry json: stdout is exactly one JSON object", js is not None and isinstance(js, dict), r.stdout[:200])
    S2 = snap(root)
    check("dry json: snapshot identical", S2 == S0)
    check("dry: no log, no backups", not os.path.exists(os.path.join(root, "docs/.migration-log.jsonl"))
          and not os.path.exists(os.path.join(root, "docs/.migration-backup")))
    track("dry run", root)
    if js is None:
        check("dry json parses", False, r.stdout[:200] + r.stderr[:200])
        return
    check("json top keys", set(js) == TOP_KEYS, set(js) ^ TOP_KEYS)
    check("json v/project/mode/run/dirty", js["v"] == 1 and js["project"] == root and js["mode"] == "dry-run"
          and js["run"] is None and js["dirty"] is False, (js["project"], js["mode"], js["run"], js["dirty"]))
    check("json summary keys", set(js["summary"]) == SUM_KEYS, set(js["summary"]) ^ SUM_KEYS)
    check("json entry keys", all(set(e) == ENTRY_KEYS for e in js["docs"]),
          [e["path"] for e in js["docs"] if set(e) != ENTRY_KEYS][:3])
    paths = [e["path"] for e in js["docs"]]
    check("json docs sorted", paths == sorted(paths))
    check("json lists every walked doc", paths == walked(root), sorted(set(paths) ^ set(walked(root)))[:6])
    check("json: root README not an entry", "README.md" not in paths)
    check("json summary docs count", js["summary"]["docs"] == len(paths))
    check("json: no entry written in dry run", not any(e["written"] for e in js["docs"]))

    # --- 2 classification
    E = lambda p: entry(js, p) or {"type": None, "plan": None, "add": {}, "links": [], "notes": [], "reason": None, "order": None}  # noqa: E731,E501
    for p, t in ((SPEC1, "spec"), (PLAN1, "plan"), (MON, "task"), (SCAF, "task"), ("docs/TASKS.md", "tracker"),
                 (SETUP, "guide"), ("docs/adr/0001-use-sqlite.md", "adr"), ("docs/notes/idea.md", "note"),
                 ("vault/notes/idea-a.md", "research")):
        check("type of %s is %s" % (p, t), E(p)["type"] == t, E(p)["type"])
    s1 = E(SPEC1)
    check("spec: plan write, add", s1["plan"] == "write" and s1["frontmatter"] == "add", (s1["plan"], s1["frontmatter"]))
    check("spec: only type and created added (no status, links, title)",
          s1["add"] == {"type": "spec", "created": "2026-10-01"}, s1["add"])
    check("spec: adr-candidate note", {"kind": "adr-candidate"} in s1["notes"], s1["notes"])
    st = [n for n in s1["notes"] if n["kind"] == "status"]
    check("spec: Status line reported as a status note", len(st) == 1 and st[0]["line"] == 3
          and "Approved" in st[0]["value"], s1["notes"])
    pl = E(PLAN1)
    check("plan: only type and created added (no source, no status)",
          pl["add"] == {"type": "plan", "created": "2026-10-02"}, pl["add"])
    tk = E(MON)
    check("task: type only plus created, no status or title", tk["add"].get("type") == "task"
          and "status" not in tk["add"] and "title" not in tk["add"], tk["add"])
    check("task: no order without --with-orders", tk["order"] is None and "order" not in tk["add"], (tk["order"], tk["add"]))
    adr = E("docs/adr/0001-use-sqlite.md")
    check("adr: no status added, status note instead", "status" not in adr["add"]
          and any(n["kind"] == "status" for n in adr["notes"]), (adr["add"], adr["notes"]))
    vn = E("vault/notes/idea-a.md")
    check("vault note with frontmatter: never written", vn["plan"] == "ok" and vn["frontmatter"] is None and not vn["add"],
          (vn["plan"], vn["frontmatter"], vn["add"]))
    vb = E("vault/notes/idea-b.md")
    check("vault note without frontmatter: type note block", vb["plan"] == "write" and vb["frontmatter"] == "add"
          and vb["add"].get("type") == "note" and "title" not in vb["add"] and "status" not in vb["add"], vb)
    check("vault: no link rewrites planned", vb["links"] == [], vb["links"])
    check("vault notes linked from vault/index.md are not orphans",
          {"kind": "orphan"} not in vn["notes"] and {"kind": "orphan"} not in vb["notes"], (vn["notes"], vb["notes"]))
    ex = E("docs/existing-fm.md")
    check("existing frontmatter: merge adds type, not title (has an H1), links untouched",
          ex["frontmatter"] == "merge" and "links" not in ex["add"] and "title" not in ex["add"]
          and ex["add"].get("type") == "note", ex["add"])
    s2 = E(SPEC2)
    check("spec with flow links: links not extended", "links" not in s2["add"] and s2["add"].get("created") == "2026-10-03", s2["add"])
    check("plan2: no source", "source" not in E(PLAN2)["add"], E(PLAN2)["add"])
    nh = E("docs/noheading.md")
    check("no H1: title added from the stem", nh["add"].get("title") == "noheading" and nh["add"].get("type") == "note", nh["add"])
    cn = E("docs/colon.md")
    check("no H1 (H2 only): title from the first heading, plain parsed string in JSON",
          cn["add"].get("title") == "Plan: part one", cn["add"])
    check("json add values are plain strings", all(isinstance(v, str) for e in js["docs"] for v in e["add"].values()),
          [(e["path"], e["add"]) for e in js["docs"] if not all(isinstance(v, str) for v in e["add"].values())][:3])
    check("V11: AUDIT-...-FIXPLAN.md types as plan", E("docs/AUDIT-2026-FIXPLAN.md")["type"] == "plan",
          E("docs/AUDIT-2026-FIXPLAN.md")["type"])
    check("V11: GUIDELINES-draft.md is not a guide", E("docs/style/GUIDELINES-draft.md")["type"] == "note",
          E("docs/style/GUIDELINES-draft.md")["type"])
    for rel, want in (("docs/superpowers/plans/2026-10-03-round/LOG.md", "log"),
                      ("docs/superpowers/plans/2026-10-03-round/reviews/spec-review.md", "report"),
                      ("docs/plans/rollout/PLAN.md", "plan")):
        check("V11: a plans folder's %s types as %s, not by the folder alone" % (rel.split("/", 3)[-1], want),
              E(rel)["type"] == want, E(rel)["type"])
    check("orphan note on idea.md", {"kind": "orphan"} in E("docs/notes/idea.md")["notes"], E("docs/notes/idea.md")["notes"])
    check("setup.md is not an orphan (README links it)", {"kind": "orphan"} not in E(SETUP)["notes"])
    bg = entry(js, BIGFM)
    check("V14: merge past line 101 skipped", bg is not None and bg["plan"] == "skip"
          and bg["reason"] == "frontmatter would exceed 100 lines", bg and (bg["plan"], bg["reason"]))
    check("orphan note on idea.md", {"kind": "orphan"} in E("docs/notes/idea.md")["notes"], E("docs/notes/idea.md")["notes"])
    check("setup.md is not an orphan (README links it)", {"kind": "orphan"} not in E(SETUP)["notes"])

    # --- 3 skips
    for p, why in (("docs/malformed.md", "malformed frontmatter: no closing --- within 100 lines"),
                   ("docs/badkey.md", "malformed frontmatter: line 3 is not key: value"),
                   ("docs/generated.md", "generated file"),
                   ("docs/seo/GUIDELINES.md", "referenced by code: scripts/check_seo.py"),
                   ("docs/link.md", "symlink"),
                   ("vault/index.md", "vault-owned"), ("vault/log.md", "vault-owned"),
                   ("vault/README.md", "vault-owned"), ("vault/daily/2026-07-10.md", "vault-owned"),
                   ("vault/raw/2026-07-09-import/orig.md", "vault-owned"),
                   ("docs/tasks/README.md", "not covered: README.md")):
        e = entry(js, p)
        check("skip %s: %s" % (p, why), e is not None and e["plan"] == "skip" and e["reason"] == why,
              e and (e["plan"], e["reason"]))

    # --- 4 links (planned)
    check("links: absolute link planned", any(l["to"] == "../tasks/backlog/monitoring.md" for l in E(SETUP)["links"]),
          E(SETUP)["links"])
    check("links: too-deep link on a paragraph line planned (the step-line one is not)",
          [l["to"] for l in E(PLAN1)["links"]] == ["../specs/2026-10-01-widget-design.md"], E(PLAN1)["links"])
    check("links: exactly one fix in setup.md (https, wikilink, fence, broken untouched)", len(E(SETUP)["links"]) == 1,
          E(SETUP)["links"])
    setup_lines = PLAN1_TEXT.split("\n")
    deeps = [i + 1 for i, l in enumerate(setup_lines) if "../docs/superpowers" in l]
    check("links: line number of the paragraph link", E(PLAN1)["links"][0]["line"] == deeps[0], (E(PLAN1)["links"], deeps))
    pb = [n for n in E(PLAN1)["notes"] if n["kind"] == "broken"]
    check("links: step-line link reported broken with the hand-fix hint",
          any(n["line"] == deeps[1] and n["hint"] == "on a checkbox or heading line: fix it by hand" for n in pb), pb)
    broken = [n for n in E(SETUP)["notes"] if n["kind"] == "broken"]
    check("broken: missing/file.md reported", any(n["target"] == "missing/file.md" and n["line"] == 4 for n in broken), broken)
    check("broken: notes/idea.md hint", any(n["target"] == "notes/idea.md" and "docs/notes/idea.md" in (n.get("hint") or "")
                                           for n in broken), broken)
    check("broken: wikilink and https not reported", len(broken) == 2, broken)

    # --- 5 apply
    r, ja = jrun([root, "--apply"])
    check("apply: exit 0", r.returncode == 0, r.stderr + r.stdout[:300])
    if ja is None:
        check("apply json parses", False, r.stdout[:300])
        return
    run_id = ja.get("run")
    check("apply json: mode and run", ja["mode"] == "apply" and bool(run_id), (ja["mode"], run_id))
    S3 = snap(root)
    track("apply", root)
    written = [e["path"] for e in ja["docs"] if e["plan"] == "write"]
    check("apply: planned writes equal dry-run plan",
          sorted(written) == sorted(e["path"] for e in js["docs"] if e["plan"] == "write"), written)
    check("apply: written flag", all(e["written"] == (e["plan"] == "write") for e in ja["docs"]))
    check("apply: summary write count", ja["summary"]["write"] == len(written), ja["summary"])
    for e in ja["docs"]:
        p = e["path"]
        if e["plan"] == "write":
            check("apply: %s changed" % p, S3[p][1] != S0[p][1])
            fl = fm_lines(S3[p][1].replace(b"\r\n", b"\n"))
            ok = fl is not None
            for k, v in e["add"].items():
                if k == "order":
                    continue
                ok = ok and any(l.startswith(k + ":") for l in (fl or []))
            check("apply: %s has the planned keys" % p, ok, (e["add"], fl))
        else:
            if S0[p][0] == "f":
                check("apply: %s byte-identical (%s)" % (p, e["plan"]), S3[p] == S0[p])
    check("apply: symlink intact", S3["docs/link.md"] == ("l", "guides/setup.md"), S3["docs/link.md"])
    for p in ("vault/index.md", "vault/log.md", "vault/README.md", "vault/raw/2026-07-09-import/orig.md",
              "vault/daily/2026-07-10.md", "docs/generated.md", "docs/malformed.md", "docs/badkey.md",
              "docs/seo/GUIDELINES.md", "docs/tasks/README.md"):
        check("apply: %s untouched" % p, S3[p] == S0[p])
    # links on disk
    setup = S3[SETUP][1].decode()
    check("apply links: absolute link relative", "](../tasks/backlog/monitoring.md)" in setup and (root + "/" + MON) not in setup, setup)
    check("apply links: https unchanged", "[site](https://example.com/a.md)" in setup)
    check("apply links: wikilink and broken unchanged", "[[wiki-link]]" in setup and "[gone](missing/file.md)" in setup
          and "[idea](notes/idea.md)" in setup)
    check("apply links: fence line unchanged", "[inside fence](%s/docs/adr/0001-use-sqlite.md)" % root in setup)
    pt = S3[PLAN1][1].decode()
    check("apply links: paragraph link fixed", "[design](../specs/2026-10-01-widget-design.md)" in pt)
    check("apply links: step-line link left as written", "[spec](../docs/superpowers/specs/2026-10-01-widget-design.md)" in pt)
    check("apply: vault note links not rewritten", b"[setup](docs/guides/setup.md)" in S3["vault/notes/idea-b.md"][1])
    # frontmatter shapes
    sp = S3[SPEC1][1].decode()
    check("apply: spec frontmatter at top, body unchanged", sp.startswith("---\ntype: spec\n") and sp.endswith(S0[SPEC1][1].decode())
          and not any(l.startswith(("status:", "title:", "links:")) for l in fm_lines(S3[SPEC1][1])), sp[:200])
    nhd = S3["docs/noheading.md"][1].decode()
    check("no H1: title line written, body unchanged", "\ntitle: noheading\n" in nhd and nhd.endswith("---\nJust text without a heading.\n"), nhd)
    cd = S3["docs/colon.md"][1].decode()
    check("title with a colon is written JSON-quoted", '\ntitle: "Plan: part one"\n' in cd, cd)
    check("V14: bigfm untouched", S3[BIGFM] == S0[BIGFM])
    ef = S3["docs/existing-fm.md"][1]
    check("merge: existing lines byte-identical", ef.startswith(b"---\nsummary: x\nlinks: [docs/x.md]\n")
          and ef.endswith(b"---\n# Existing\n"), ef)
    keyorder = [l.split(":")[0] for l in fm_lines(ef)]
    order_ref = ["type", "title", "status", "created", "source", "links", "order"]
    new_keys = keyorder[2:]  # after the two existing lines
    check("merge: new keys in the contract order", new_keys == sorted(new_keys, key=order_ref.index) and "type" in new_keys, keyorder)
    s2t = S3[SPEC2][1].decode()
    check("spec2: existing lines kept, links line untouched, nothing but type/created added",
          s2t.startswith("---\nsummary: other\nlinks: [docs/x.md]\n") and s2t.endswith("---\n# Other Design\n\nBody.\n")
          and "title:" not in s2t and "status:" not in s2t, s2t)
    check("vault note with frontmatter byte-identical", S3["vault/notes/idea-a.md"] == S0["vault/notes/idea-a.md"])
    vbt = S3["vault/notes/idea-b.md"][1].decode()
    check("vault note without frontmatter: type: note block, body unchanged",
          vbt.startswith("---\ntype: note\n") and vbt.endswith(S0["vault/notes/idea-b.md"][1].decode())
          and "title:" not in vbt.split("---")[1], vbt)
    cr = S3["docs/crlf.md"][1]
    head, _, rest = cr.partition(b"---\r\n# CRLF")
    check("crlf: header uses CRLF only", cr.startswith(b"---\r\n") and b"\n" not in head.replace(b"\r\n", b"")
          and cr.endswith(b"# CRLF Doc\r\n\r\nBody line.\r\n"), cr)
    # log
    log = read_log(root)
    check("log: start / write... / end", log and log[0]["event"] == "start" and log[-1]["event"] == "end"
          and all(l["event"] == "write" for l in log[1:-1]) and len(log) >= 3, [l["event"] for l in log])
    if log and log[0]["event"] == "start":
        st = log[0]
        check("log: start fields", st["v"] == 1 and st["run"] == run_id and st["tool"] == "cc-docs-migrate"
              and "root" not in st and st["args"] == {"only": None, "with_orders": False, "allow_dirty": False}
              and re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", st["at"]) and st["at"].endswith("Z"), st)
        check("log: run id format", re.match(r"^\d{8}T\d{6}Z-[0-9a-f]{6}$", run_id or ""), run_id)
        check("log: end count", log[-1]["files"] == len(log) - 2 == len(written), (log[-1], len(written)))
        check("log: paths are exactly the written set", sorted(l["path"] for l in log[1:-1]) == sorted(written))
        for l in log[1:-1]:
            p = l["path"]
            check("log %s: before/after match disk" % p, l["before"] == sha(S0[p][1]) and l["after"] == sha(S3[p][1]), l)
            bk = l["backup"]
            check("log %s: backup holds the old bytes" % p, bk == "docs/.migration-backup/%s/%s.orig" % (run_id, p)
                  and os.path.isfile(os.path.join(root, bk)) and rd(root, bk) == S0[p][1], bk)
            check("log %s: actions subset in order" % p, l["actions"] and l["actions"] == [a for a in ("frontmatter", "links", "order") if a in l["actions"]], l["actions"])
    check("backup .gitignore is *", os.path.isfile(os.path.join(root, "docs/.migration-backup/.gitignore"))
          and rd(root, "docs/.migration-backup/.gitignore").strip() == b"*")
    # --- 6 plans.py
    for p in (PLAN1, PLAN2):
        before, after = P.parse_plan(S0[p][1].decode()), P.parse_plan(S3[p][1].decode())
        check("plans.py: %s same progress" % p, plan_sig(S0[p][1].decode()) == plan_sig(S3[p][1].decode()),
              (plan_sig(S0[p][1].decode()), plan_sig(S3[p][1].decode())))
        check("plans.py: %s is_plan unchanged" % p, P.is_plan(before, "plans") == P.is_plan(after, "plans") is True)
    p1 = plan_sig(S3[PLAN1][1].decode())
    check("plans.py: after apply 4 tasks, 7 steps, 3 done (fence lines not counted)",
          len(p1[5]) == 4 and p1[3] == 7 and p1[4] == 3, p1)

    # --- 7 idempotence
    commit(root, "migrated")
    S4 = snap(root)
    log_before = rd(root, "docs/.migration-log.jsonl")
    r, jb = jrun([root, "--apply"])
    check("re-apply: exit 0", r.returncode == 0, r.stderr)
    S5 = snap(root)
    check("re-apply: no file changes at all", S5 == S4, [k for k in set(S4) | set(S5) if S4.get(k) != S5.get(k)][:5])
    check("re-apply: log byte-identical", rd(root, "docs/.migration-log.jsonl") == log_before)
    check("re-apply: nothing to write", jb is not None and jb["summary"]["write"] == 0 and jb["summary"]["links_fixed"] == 0
          and jb["summary"]["orders_new"] == 0 and not any(e["plan"] == "write" for e in jb["docs"]), jb and jb["summary"])
    check("re-apply: no run id needed", jb is not None and not any(e["written"] for e in jb["docs"]))
    check("re-apply: the tracked migration log is not a code reference",
          jb is not None and not any("migration" in (e["reason"] or "") for e in jb["docs"]),
          jb and [(e["path"], e["reason"]) for e in jb["docs"] if "migration" in (e["reason"] or "")][:3])
    r2 = run([root, "--apply"])
    check("re-apply text: exit 0", r2.returncode == 0 and snap(root) == S4, r2.stderr)
    track("re-apply", root)

    # --- 8 undo
    r, ju = jrun([root, "--undo", "docs/.migration-log.jsonl"], cwd=BASE)
    check("undo: exit 0", r.returncode == 0, r.stderr + r.stdout[:300])
    S6 = snap(root)
    bad = [k for k in S0 if S0[k][0] == "f" and S6.get(k) != S0[k]]
    check("undo: every original file byte-identical", not bad, bad[:5])
    check("undo: symlink intact", S6["docs/link.md"] == ("l", "guides/setup.md"))
    log2 = read_log(root)
    check("undo: undo line appended", log2 and log2[-1]["event"] == "undo" and log2[-1]["of"] == run_id
          and sorted(log2[-1]["restored"]) == sorted(written) and not log2[-1]["kept"], log2[-1] if log2 else None)
    check("undo json", ju is not None and ju["mode"] == "undo" and ju["of"] == run_id
          and sorted(ju["restored"]) == sorted(written) and ju["kept"] == [], ju)
    check("undo: backups left in place", os.path.isdir(os.path.join(root, "docs/.migration-backup", run_id)))
    track("undo", root)
    log_after = rd(root, "docs/.migration-log.jsonl")
    r = run([root, "--undo", "docs/.migration-log.jsonl"], cwd=BASE)
    check("2nd undo: exit 0, nothing to undo", r.returncode == 0 and "nothing to undo" in r.stdout, r.stdout + r.stderr)
    check("2nd undo: nothing written", rd(root, "docs/.migration-log.jsonl") == log_after and snap(root) == S6)
    r, ju2 = jrun([root, "--undo", "docs/.migration-log.jsonl"], cwd=BASE)
    check("2nd undo json: nulls and empty lists", ju2 is not None and ju2["run"] is None and ju2["of"] is None
          and ju2["restored"] == [] and ju2["already"] == [] and ju2["kept"] == [], ju2)
    track("2nd undo", root)


def case_undo_kept():
    root = build()
    S0 = snap(root)
    r, ja = jrun([root, "--apply"])
    check("undo-kept: apply exit 0", r.returncode == 0, r.stderr)
    p = "docs/notes/idea.md"
    wr(root, p, rd(root, p) + b"edited later\n")
    edited = rd(root, p)
    r, ju = jrun([root, "--undo", "docs/.migration-log.jsonl"], cwd=BASE)
    check("undo-kept: exit 0", r.returncode == 0, r.stderr)
    check("undo-kept: edited file not overwritten", rd(root, p) == edited)
    check("undo-kept: reported kept", ju is not None and any(k["path"] == p and "changed since" in k["why"]
                                                              for k in ju["kept"]), ju and ju["kept"])
    check("undo-kept: the other files restored", all(rd(root, k) == v[1] for k, v in S0.items()
                                                      if v[0] == "f" and k != p), "")
    r = run([root, "--undo", "docs/.migration-log.jsonl"], cwd=BASE)
    check("undo-kept: a further undo has nothing to undo", r.returncode == 0 and "nothing to undo" in r.stdout, r.stdout)


def case_refusals():
    # dirty
    root = build()
    wr(root, "docs/notes/idea.md", rd(root, "docs/notes/idea.md") + b"dirty edit\n")
    S = snap(root)
    r, jd = jrun([root])
    check("dirty: dry run still works and says dirty", r.returncode == 0 and jd is not None and jd["dirty"] is True, r.stderr)
    check("dirty: dry run wrote nothing", snap(root) == S)
    r = run([root, "--apply"])
    lines = [l for l in r.stderr.split("\n") if l.strip()]
    check("dirty: exit 2", r.returncode == 2, (r.returncode, r.stderr))
    check("dirty: one BLOCKED line with Suggestion", len(lines) == 1 and lines[0].startswith("BLOCKED: ")
          and "Suggestion:" in lines[0] and "dirty" in lines[0], r.stderr)
    check("dirty: nothing written", snap(root) == S)
    r = run([root, "--apply", "--allow-dirty"])
    check("allow-dirty: proceeds", r.returncode == 0 and os.path.exists(os.path.join(root, "docs/.migration-log.jsonl")), r.stderr)
    log = read_log(root)
    check("allow-dirty: logged", log and log[0]["args"]["allow_dirty"] is True, log[:1])
    # non-git
    ng = os.path.join(BASE, "nogit")
    shutil.copytree(build(), ng, symlinks=True, ignore=shutil.ignore_patterns(".git"))
    S = snap(ng)
    r = run([ng, "--apply"])
    lines = [l for l in r.stderr.split("\n") if l.strip()]
    check("non-git apply: exit 2, BLOCKED, not a git repository", r.returncode == 2 and len(lines) == 1
          and lines[0].startswith("BLOCKED: ") and "not a git repository" in lines[0], r.stderr)
    check("non-git apply: nothing written", snap(ng) == S)
    r = run([ng, "--apply", "--allow-dirty"])
    check("non-git apply --allow-dirty: still refused (V7)", r.returncode == 2 and "not a git repository" in r.stderr
          and r.stderr.startswith("BLOCKED: "), (r.returncode, r.stderr))
    check("non-git apply --allow-dirty: nothing written", snap(ng) == S)
    r, jn = jrun([ng])
    check("non-git dry run: exit 0, dirty null, warn", r.returncode == 0 and jn is not None and jn["dirty"] is None
          and jn["warnings"], r.stderr)
    r = run([ng])
    check("non-git dry run text: warn line", r.returncode == 0 and re.search(r"^warn", r.stdout, re.M), r.stdout[:300])
    check("non-git dry run: nothing written", snap(ng) == S)
    # subdirectory of a repo (V6)
    sub = build()
    S = snap(sub)
    for flags in ([], ["--apply"], ["--apply", "--allow-dirty"]):
        r = run([os.path.join(sub, "docs")] + flags)
        lines = [l for l in r.stderr.split("\n") if l.strip()]
        check("subdirectory %s: exit 2, one BLOCKED line" % (flags or "dry run"), r.returncode == 2 and len(lines) == 1
              and lines[0].startswith("BLOCKED: ") and "not the top of its git work tree" in lines[0], (r.returncode, r.stderr))
    check("subdirectory: nothing written", snap(sub) == S)
    # docs symlink
    _counter[0] += 1
    ds = os.path.join(BASE, "docslink-%d" % _counter[0])
    os.makedirs(ds)
    git(ds, "init", "-q")
    wr(ds, "real_docs/a.md", "# A\n")
    os.symlink("real_docs", os.path.join(ds, "docs"))
    commit(ds)
    S = snap(ds)
    r = run([ds, "--apply"])
    lines = [l for l in r.stderr.split("\n") if l.strip()]
    check("docs symlink: exit 2 naming docs/", r.returncode == 2 and len(lines) == 1 and lines[0].startswith("BLOCKED: ")
          and "docs/" in lines[0], r.stderr)
    check("docs symlink: nothing written", snap(ds) == S)
    # with-orders without factory
    root = build()
    S = snap(root)
    r = run([root, "--apply", "--with-orders"])
    lines = [l for l in r.stderr.split("\n") if l.strip()]
    check("with-orders: exit 2, factory/orders/ and cc-factory init", r.returncode == 2 and len(lines) == 1
          and "factory/orders/" in lines[0] and "cc-factory init" in lines[0] and lines[0].startswith("BLOCKED: "), r.stderr)
    check("with-orders: nothing written", snap(root) == S)
    r = run([root, "--with-orders"])
    check("with-orders dry run: warns, exit 0", r.returncode == 0 and "factory/orders/" in r.stdout, r.stdout[:300])
    # usage
    root = build()
    for label, args in (("bad --only", [root, "--only", "bogus"]),
                        ("--undo with --apply", [root, "--undo", "docs/.migration-log.jsonl", "--apply"]),
                        ("missing project dir", [os.path.join(BASE, "does-not-exist")]),
                        ("no arguments", [])):
        r = run(args, cwd=root)
        check("usage: %s exit 1" % label, r.returncode == 1, (r.returncode, r.stderr[:200]))
        check("usage: %s message prefix" % label, r.stderr.startswith("cc-docs-migrate: ") and "Traceback" not in r.stderr,
              r.stderr[:200])
    check("usage: nothing written", not os.path.exists(os.path.join(root, "docs/.migration-log.jsonl")))
    r = run([root, "--undo", "docs/nope.jsonl"], cwd=BASE)
    check("undo: missing log refused (exit 2 BLOCKED)", r.returncode == 2 and r.stderr.startswith("BLOCKED: "), (r.returncode, r.stderr))


def case_orders():
    root = build(factory=True)
    S0 = snap(root)
    r, jn = jrun([root])
    mon = entry(jn, MON)
    check("orders: without --with-orders a note, JSON order null", mon is not None and mon["order"] is None
          and {"kind": "order-candidate", "open": 2} in mon["notes"] and "order" not in mon["add"], mon and (mon["order"], mon["notes"]))
    check("orders: summary order_candidates 1", jn["summary"]["order_candidates"] == 1 and jn["summary"]["orders_new"] == 0, jn["summary"])
    r, jd = jrun([root, "--with-orders"])
    mon = entry(jd, MON)
    check("orders dry: planned new order", mon is not None and mon["order"] is not None and mon["order"]["new"] is True
          and mon["order"]["id"] is None and mon["order"]["priority"] == "p3" and mon["order"]["open"] == 2
          and mon["order"]["title"] == "Monitoring", mon and mon["order"])
    check("orders dry: add shows (new)", mon and mon["add"].get("order") == "(new)", mon and mon["add"])
    check("orders dry: only the open backlog task has an order",
          [e["path"] for e in jd["docs"] if e["order"]] == [MON], [e["path"] for e in jd["docs"] if e["order"]])
    check("orders dry: summary orders_new 1", jd["summary"]["orders_new"] == 1, jd["summary"])
    check("orders dry: no order file yet", not any(n.startswith("FO-") for n in os.listdir(os.path.join(root, "factory/orders"))))
    root2 = build(factory=True)
    r = run([root2, "--apply"])
    check("orders: apply without --with-orders creates no order", r.returncode == 0
          and not [n for n in os.listdir(os.path.join(root2, "factory/orders")) if n.startswith("FO-")]
          and b"order:" not in rd(root2, MON), r.stderr)
    r, ja = jrun([root, "--apply", "--with-orders"])
    check("orders apply: exit 0", r.returncode == 0, r.stderr + r.stdout[:300])
    lst = subprocess.run([PY, FACTORY, "-C", root, "list", "--json"], env=env_for(), capture_output=True, text=True)
    orders = json.loads(lst.stdout or "[]")
    check("orders apply: exactly one order via cc-factory", len(orders) == 1 and orders[0]["id"] == "FO-001"
          and orders[0]["title"] == "Monitoring", orders)
    files = [n for n in os.listdir(os.path.join(root, "factory/orders")) if n.startswith("FO-001-")]
    check("orders apply: one order file", len(files) == 1, files)
    if files:
        of = rd(root, "factory/orders/" + files[0]).decode()
        check("order file: source", re.search(r"^source: docs/tasks/backlog/monitoring\.md$", of, re.M), of)
        check("order file: acceptance criteria with the open items",
              "## Acceptance criteria\n- [ ] Add uptime probe\n- [ ] Alert on 5xx\n" in of and "Pick tool" not in of, of)
        check("order file: intent and notes sections", "## Intent\n" in of and "## Notes\n" in of
              and "Add basic uptime monitoring" in of, of)
    mt = rd(root, MON).decode()
    check("task got order: FO-001", "\norder: FO-001\n" in mt and mt.startswith("---\n"), mt)
    check("order not added to others", all(b"order: FO" not in rd(root, p) for p in (SCAF, "docs/TASKS.md", PLAN1)))
    log = read_log(root)
    ow = [l for l in log if l.get("order") == "FO-001"]
    check("orders log: order file and task carry the order id", len(ow) == 2 and any(l["before"] is None and l["backup"] is None
                                                                               for l in ow)
          and any(l["path"] == MON for l in ow), ow)
    run_id = log[0]["run"]
    commit(root, "migrated")
    S1 = snap(root)
    r, jb = jrun([root, "--apply", "--with-orders"])
    check("orders rerun: exit 0, no second order", r.returncode == 0 and jb["summary"]["orders_new"] == 0
          and len([n for n in os.listdir(os.path.join(root, "factory/orders")) if n.startswith("FO-")]) == 1, r.stderr)
    check("orders rerun: nothing changed", snap(root) == S1)
    # undo
    r, ju = jrun([root, "--undo", "docs/.migration-log.jsonl"], cwd=BASE)
    check("orders undo: exit 0", r.returncode == 0, r.stderr)
    kept = {k["path"]: k["why"] for k in (ju or {}).get("kept", [])}
    ok_path = [p for p in kept if p.startswith("factory/orders/FO-001-")]
    check("orders undo: order file kept and reported", len(ok_path) == 1 and "created by the migration" in kept[ok_path[0]]
          and "FO-001" in kept[ok_path[0]] and "cc-factory reject FO-001" in kept[ok_path[0]], kept)
    check("orders undo: order file still on disk", bool(files) and os.path.isfile(os.path.join(root, "factory/orders", files[0])))
    check("orders undo: task restored", rd(root, MON) == S0[MON][1])
    commit(root, "after undo")
    r, jc = jrun([root, "--apply", "--with-orders"])
    mon = entry(jc, MON)
    check("fresh apply: links the existing order", mon is not None and mon["order"] and mon["order"]["new"] is False
          and mon["order"]["id"] == "FO-001", mon and mon["order"])
    check("fresh apply: no FO-002", not any(n.startswith("FO-002") for n in os.listdir(os.path.join(root, "factory/orders"))),
          os.listdir(os.path.join(root, "factory/orders")))
    check("fresh apply: summary linked", jc is not None and jc["summary"]["orders_linked"] == 1
          and jc["summary"]["orders_new"] == 0, jc and jc["summary"])
    check("fresh apply: task carries order FO-001 again", "\norder: FO-001\n" in rd(root, MON).decode())
    lst = subprocess.run([PY, FACTORY, "-C", root, "list", "--json"], env=env_for(), capture_output=True, text=True)
    check("fresh apply: still one order in the ledger", len(json.loads(lst.stdout or "[]")) == 1, lst.stdout)


def case_only_plans():
    root = build()
    S0 = snap(root)
    r, jd = jrun([root, "--only", "plans"])
    check("only plans dry: exit 0", r.returncode == 0, r.stderr)
    paths = [e["path"] for e in (jd or {}).get("docs", [])]
    in_plans = sorted(k for k in S0 if S0[k][0] == "f" and k.startswith("docs/superpowers/plans/") and k.endswith(".md"))
    check("only plans dry: only plan entries", paths == in_plans and PLAN1 in paths and PLAN2 in paths
          and all(e["tree"] == "plans" for e in jd["docs"]), paths)
    r, ja = jrun([root, "--apply", "--only", "plans"])
    check("only plans apply: exit 0", r.returncode == 0, r.stderr)
    S1 = snap(root)
    changed = sorted(k for k in S0 if S0[k][0] == "f" and S1.get(k) != S0[k])
    check("only plans apply: only plan files changed", changed and PLAN1 in changed and PLAN2 in changed
          and all(k.startswith("docs/superpowers/plans/") for k in changed), changed)
    check("only plans apply: the plans carry type: plan, no source", S1[PLAN1][1].startswith(b"---\ntype: plan\n")
          and b"source:" not in S1[PLAN1][1].split(b"\n# Widget Plan")[0])
    log = read_log(root)
    check("only plans apply: log args", log and log[0]["args"]["only"] == "plans", log[:1])
    for tree, expect in (("specs", {SPEC1, SPEC2}), ("vault", {"vault/notes/idea-b.md"})):
        r, jt = jrun([root, "--only", tree])
        got = {e["path"] for e in jt["docs"] if e["plan"] == "write"} if jt else None
        check("only %s dry: entries are that tree only" % tree, jt is not None and all(e["tree"] == tree for e in jt["docs"])
              and got == expect, got)


CASES = [case_main, case_undo_kept, case_refusals, case_orders, case_only_plans]


def main():
    keep = "--keep" in sys.argv[1:]
    if not os.path.exists(CLI):
        for fn in CASES:
            check("%s: bin/cc-docs-migrate exists" % fn.__name__, False, CLI)
    else:
        for fn in CASES:
            try:
                fn()
            except BaseException as e:  # noqa: BLE001 - one failure per case that cannot run
                import traceback
                check("%s ran to the end" % fn.__name__, False,
                      "%s: %s @ %s" % (type(e).__name__, e, traceback.extract_tb(e.__traceback__)[-1].lineno))
    why = STATE_GUARD.problem()
    check("state guard: the real cc_tool state dir is untouched", not why, why)
    if keep:
        print("kept: " + BASE)
    else:
        shutil.rmtree(BASE, ignore_errors=True)
    failed = sum(1 for r in RESULTS if not r[1])
    print("%d passed, %d failed" % (len(RESULTS) - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
