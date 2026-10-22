#!/usr/bin/env python3
"""Plan progress for bin/cc-dash (lib/cc_dash/plans.py, /api/plans).

Builds, in a temp dir, a git repo with Superpowers-style plans and a fake
${CLAUDE_CONFIG_DIR}/projects tree whose transcripts work on them:
  widget   docs/superpowers/plans/2026-10-20-widget.md: two phases, four tasks,
           a code fence holding "- [ ]" lines that must not count, a loose
           checklist after the tasks. Session LIVE (written a minute ago)
           invokes subagent-driven-development, reads the plan, spawns
           "Implement Task 1: Parser" (a subagent that ticks Task 1 with Edit
           and commits), "Review Task 1 ...", ticks Task 2 Step 1 itself,
           spawns a still-running "Implement Task 2: Store" and keeps a
           TodoWrite list. Two commits fall in the two tasks' windows.
  gadget   three tasks, no checkboxes. Session OLD (five hours ago) only keeps
           a TodoWrite list naming them: progress must come from the todos.
  ledger   two tasks with unticked boxes; session LEDGER appends an SDD ledger
           line "Task 1: complete (commits a..b)" and the ledger file is on disk.
  unticked an older SDD run that never ticks: Task 1's implementer finished, so
           it is done; a "Task 2: <unrelated name>" spawn is not this plan's.
  done     every box ticked, no session.     idle  nothing ticked, no session.
Then it checks parsing, linkage and tick times, todo-only progress, status
rules (live, paused, blocked through a stubbed alert, done, not started),
the ETA rule (hidden under two finished tasks), commit mapping, and the
endpoints: token required, path safety (traversal, absolute, symlink,
non-.md, oversize, unknown project).

Run:  python3 tests/test_cc_dash_plans.py
      python3 tests/test_cc_dash_plans.py --fixture DIR   (only write the tree, for a browser check)
"""
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "lib"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cc_dash import plans as P  # noqa: E402
from cc_dash.server import make_server  # noqa: E402
from cc_dash.store import Store  # noqa: E402
from dash_pairing import pair  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


USAGE = {"input_tokens": 10, "output_tokens": 50, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}

WIDGET = """# Widget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `widget` end to end.

**Spec:** docs/superpowers/specs/2026-10-20-widget-design.md

## Global Constraints

- Keep it small.

## Phase 1: Core

### Task 1: Parser

**Files:** `src/parser.py`

- [ ] **Step 1: Write the failing test**
- [ ] **Step 2: Commit**

### Task 2: Store

- [ ] **Step 1: Write the failing test**
- [ ] **Step 2: Commit**

## Phase 2: UI

### Task 3: View `render()`

```markdown
- [ ] **Step 9: inside a fence, not a step**
# Not a heading
```

~~~
- [x] also fenced
~~~

- [ ] **Step 1: Write the failing test**

### Task 4: Docs

- [ ] **Step 1: Write the docs**

## Self-review

- [ ] loose item, not inside a task
"""

GADGET = """# Gadget Plan

### Task 1: Alpha wiring
Do alpha.

### Task 2: Beta storage
Do beta.

### Task 3: Gamma screen
Do gamma.
"""

LEDGER_PLAN = """# Ledger Plan

### Task 1: First thing
- [ ] **Step 1: Do it**

### Task 2: Second thing
- [ ] **Step 1: Do it**
"""

DONE_PLAN = """# Done Plan

### Task 1: One
- [x] **Step 1: A**

### Task 2: Two
- [X] **Step 1: B**
"""

UNTICKED_PLAN = """# Unticked Plan

### Task 1: Alpha wiring
- [ ] **Step 1: A**

### Task 2: Beta storage
- [ ] **Step 1: B**
"""

IDLE_PLAN = """# Idle Plan

### Task 1: One
- [ ] **Step 1: A**
- [ ] **Step 2: B**
"""


def git(repo, *args, t=None):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
    if t is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = "@%d +0000" % int(t)
    return subprocess.run(["git", "-C", repo] + list(args), env=env, capture_output=True, text=True, check=True).stdout.strip()


class Transcript:
    """Builds one transcript file's records."""

    def __init__(self, sid, cwd, agent=None):
        self.sid, self.cwd, self.agent = sid, cwd, agent
        self.lines = []
        self.n = 0

    def rec(self, kind, t, **kw):
        self.n += 1
        r = {"type": kind, "timestamp": iso(t), "sessionId": self.sid, "cwd": self.cwd, "gitBranch": "main", "version": "2.1.300",
             "entrypoint": "cli", "isSidechain": bool(self.agent), "uuid": "%s-%s-%d" % (self.sid, self.agent, self.n)}
        if self.agent:
            r["agentId"] = self.agent
        r.update(kw)
        self.lines.append(r)

    def prompt(self, t, text):
        self.rec("user", t, message={"role": "user", "content": [{"type": "text", "text": text}]})

    def tool(self, t, name, inp, result="ok", tur=None, pending=False, model="claude-opus-5-5"):
        self.n += 1
        tid = "tu_%s_%s_%d" % (self.sid[:4], self.agent or "m", self.n)
        self.rec("assistant", t, message={"id": "msg_" + tid, "model": model, "role": "assistant", "stop_reason": "tool_use",
                                          "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}], "usage": USAGE})
        if not pending:
            extra = {"toolUseResult": tur} if tur is not None else {}
            self.rec("user", t + 2, message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": result}]}, **extra)
        return tid

    def reply(self, t, text, model="claude-opus-5-5"):
        self.n += 1
        self.rec("assistant", t, message={"id": "msg_r_%s_%d" % (self.sid[:4], self.n), "model": model, "role": "assistant",
                                          "stop_reason": "end_turn", "content": [{"type": "text", "text": text}], "usage": USAGE})

    def write(self, path, mtime):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            for r in self.lines:
                fh.write(json.dumps(r) + "\n")
        os.utime(path, (mtime, mtime))


def build_fixture(base, now=None):
    now = now or time.time()
    cfg = os.path.join(base, "cfg")
    repo = os.path.join(base, "proj")
    plans_dir = os.path.join(repo, "docs", "superpowers", "plans")
    os.makedirs(plans_dir)
    files = {"widget": "2026-10-20-widget.md", "gadget": "2026-10-19-gadget.md", "ledger": "2026-10-18-ledger.md",
             "done": "2026-10-17-done.md", "idle": "2026-10-16-idle.md", "unticked": "2026-10-13-unticked.md"}
    texts = {"widget": WIDGET, "gadget": GADGET, "ledger": LEDGER_PLAN, "done": DONE_PLAN, "idle": IDLE_PLAN, "unticked": UNTICKED_PLAN}
    t0 = now - 900
    git(repo, "init", "-q", "-b", "main")
    for k, f in files.items():
        with open(os.path.join(plans_dir, f), "w") as fh:
            fh.write(texts[k])
    with open(os.path.join(repo, "README.md"), "w") as fh:
        fh.write("# proj\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "Add plans", t=t0 - 3000)
    base_sha = git(repo, "rev-parse", "--short", "HEAD")
    # the ledger plan's task 1 commit (an hour before the live session)
    with open(os.path.join(repo, "ledger.txt"), "w") as fh:
        fh.write("1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "Ledger task one", t=t0 - 2400)
    ledger_sha = git(repo, "rev-parse", "--short", "HEAD")
    sdd = os.path.join(repo, ".superpowers", "sdd", "2026-10-18-ledger")
    os.makedirs(sdd)
    with open(os.path.join(sdd, "plan-path"), "w") as fh:
        fh.write("docs/superpowers/plans/2026-10-18-ledger.md\n")
    with open(os.path.join(sdd, "progress.md"), "w") as fh:
        fh.write("# SDD ledger — plan: docs/superpowers/plans/2026-10-18-ledger.md\n\nTask 1: complete (commits %s..%s, review clean)\n" % (base_sha, ledger_sha))
    # the widget's commits: one inside Task 1's subagent run, one inside Task 2's
    with open(os.path.join(repo, "parser.py"), "w") as fh:
        fh.write("x = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "Add the parser", t=t0 + 180)
    c1 = git(repo, "rev-parse", "--short", "HEAD")
    with open(os.path.join(repo, "store.py"), "w") as fh:
        fh.write("y = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "Add the store", t=t0 + 520)
    c2 = git(repo, "rev-parse", "--short", "HEAD")
    # tick the widget's file the way the transcript says (Task 1 both steps, Task 2 step 1)
    wpath = os.path.join(plans_dir, files["widget"])
    txt = WIDGET.replace("### Task 1: Parser\n\n**Files:** `src/parser.py`\n\n- [ ] **Step 1: Write the failing test**\n- [ ] **Step 2: Commit**",
                         "### Task 1: Parser\n\n**Files:** `src/parser.py`\n\n- [x] **Step 1: Write the failing test**\n- [x] **Step 2: Commit**")
    txt = txt.replace("### Task 2: Store\n\n- [ ] **Step 1: Write the failing test**", "### Task 2: Store\n\n- [x] **Step 1: Write the failing test**")
    with open(wpath, "w") as fh:
        fh.write(txt)

    pdir = os.path.join(cfg, "projects", repo.replace("/", "-"))
    ids = {"live": "11111111-aaaa-4aaa-8aaa-000000000001", "old": "22222222-bbbb-4bbb-8bbb-000000000002",
           "ledger": "33333333-cccc-4ccc-8ccc-000000000003", "unticked": "44444444-dddd-4ddd-8ddd-000000000004"}
    # LIVE: executes the widget with subagent-driven-development
    m = Transcript(ids["live"], repo)
    m.prompt(t0, "Execute the widget plan")
    m.tool(t0 + 5, "Skill", {"skill": "superpowers:subagent-driven-development"})
    m.tool(t0 + 10, "Read", {"file_path": wpath, "limit": 700})
    sp1 = m.tool(t0 + 20, "Agent", {"description": "Implement Task 1: Parser", "prompt": "You are implementing Task 1: Parser", "subagent_type": "general-purpose", "model": "sonnet"},
                 result="done", tur={"status": "completed", "agentId": "a1", "resolvedModel": "claude-sonnet-5-5"})
    sp2 = m.tool(t0 + 230, "Agent", {"description": "Review Task 1 (spec + quality)", "prompt": "Review", "subagent_type": "general-purpose"},
                 result="clean", tur={"status": "completed", "agentId": "r1"})
    m.tool(t0 + 290, "TodoWrite", {"todos": [{"content": "Task 1: Parser", "status": "completed", "activeForm": "Parsing"},
                                             {"content": "Task 2: Store", "status": "in_progress", "activeForm": "Storing"},
                                             {"content": "Task 3: View", "status": "pending", "activeForm": "Viewing"}]})
    sp3 = m.tool(t0 + 300, "Agent", {"description": "Implement Task 2: Store", "prompt": "You are implementing Task 2: Store", "subagent_type": "general-purpose"},
                 result="Async agent launched", tur={"status": "async_launched", "agentId": "a2"})
    m.tool(t0 + 400, "Edit", {"file_path": wpath, "old_string": "### Task 2: Store\n\n- [ ] **Step 1: Write the failing test**",
                              "new_string": "### Task 2: Store\n\n- [x] **Step 1: Write the failing test**"})
    m.reply(now - 60, "Task 2 is running.")
    m.write(os.path.join(pdir, ids["live"] + ".jsonl"), now - 60)
    sdir = os.path.join(pdir, ids["live"], "subagents")
    a1 = Transcript(ids["live"], repo, agent="a1")
    a1.prompt(t0 + 21, "You are implementing Task 1: Parser")
    a1.tool(t0 + 60, "Read", {"file_path": wpath}, model="claude-sonnet-5-5")
    a1.tool(t0 + 150, "Edit", {"file_path": wpath,
                               "old_string": "- [ ] **Step 1: Write the failing test**\n- [ ] **Step 2: Commit**\n\n### Task 2: Store",
                               "new_string": "- [x] **Step 1: Write the failing test**\n- [x] **Step 2: Commit**\n\n### Task 2: Store"}, model="claude-sonnet-5-5")
    a1.tool(t0 + 178, "Bash", {"command": "git commit -qm 'Add the parser'"}, model="claude-sonnet-5-5")
    a1.tool(t0 + 200, "SubagentHandback", {"message": "Task 1 done"}, model="claude-sonnet-5-5")
    a1.write(os.path.join(sdir, "agent-a1.jsonl"), t0 + 200)
    with open(os.path.join(sdir, "agent-a1.meta.json"), "w") as fh:
        json.dump({"agentType": "general-purpose", "description": "Implement Task 1: Parser", "toolUseId": sp1}, fh)
    r1 = Transcript(ids["live"], repo, agent="r1")
    r1.prompt(t0 + 231, "Review Task 1")
    r1.tool(t0 + 240, "Read", {"file_path": wpath})
    r1.tool(t0 + 280, "SubagentHandback", {"message": "clean"})
    r1.write(os.path.join(sdir, "agent-r1.jsonl"), t0 + 280)
    with open(os.path.join(sdir, "agent-r1.meta.json"), "w") as fh:
        json.dump({"agentType": "general-purpose", "description": "Review Task 1 (spec + quality)", "toolUseId": sp2}, fh)
    a2 = Transcript(ids["live"], repo, agent="a2")
    a2.prompt(t0 + 301, "You are implementing Task 2: Store")
    a2.tool(t0 + 518, "Bash", {"command": "git commit -qm 'Add the store'"}, model="claude-sonnet-5-5")
    a2.tool(now - 30, "Bash", {"command": "pytest -q"}, pending=True, model="claude-sonnet-5-5")
    a2.write(os.path.join(sdir, "agent-a2.jsonl"), now - 30)
    with open(os.path.join(sdir, "agent-a2.meta.json"), "w") as fh:
        json.dump({"agentType": "general-purpose", "description": "Implement Task 2: Store", "toolUseId": sp3}, fh)

    # OLD: the gadget, todos only, five hours ago
    g0 = now - 5 * 3600
    gpath = os.path.join(plans_dir, files["gadget"])
    o = Transcript(ids["old"], repo)
    o.prompt(g0, "Do the gadget plan")
    o.tool(g0 + 10, "Read", {"file_path": gpath})
    todo = lambda a, b, c: {"todos": [{"content": "Task 1: Alpha wiring", "status": a, "activeForm": "a"},  # noqa: E731
                                      {"content": "Task 2: Beta storage", "status": b, "activeForm": "b"},
                                      {"content": "Task 3: Gamma screen", "status": c, "activeForm": "c"}]}
    o.tool(g0 + 20, "TodoWrite", todo("in_progress", "pending", "pending"))
    o.tool(g0 + 620, "TodoWrite", todo("completed", "in_progress", "pending"))
    o.tool(g0 + 1520, "TodoWrite", todo("completed", "completed", "in_progress"))
    o.reply(g0 + 1600, "Paused before Gamma.")
    o.write(os.path.join(pdir, ids["old"] + ".jsonl"), g0 + 1600)

    # LEDGER: writes the SDD ledger line for the ledger plan
    l0 = now - 4000
    lp = Transcript(ids["ledger"], repo)
    lp.prompt(l0, "Run the ledger plan")
    lp.tool(l0 + 10, "Read", {"file_path": os.path.join(plans_dir, files["ledger"])})
    lp.tool(l0 + 900, "Bash", {"command": "echo 'Task 1: complete (commits %s..%s, review clean)' >> .superpowers/sdd/2026-10-18-ledger/progress.md" % (base_sha, ledger_sha)})
    lp.reply(l0 + 950, "Task 1 is in the ledger.")
    lp.write(os.path.join(pdir, ids["ledger"] + ".jsonl"), l0 + 950)

    # UNTICKED: an older SDD run that never ticks boxes or keeps a ledger; its second spawn says
    # "Task 2" but names something no plan's Task 2 shares a word with (the lead's own numbering)
    u0 = now - 7200
    un = Transcript(ids["unticked"], repo)
    un.prompt(u0, "Run the unticked plan")
    un.tool(u0 + 10, "Read", {"file_path": os.path.join(plans_dir, files["unticked"])})
    usp1 = un.tool(u0 + 20, "Agent", {"description": "Implement Task 1: alpha wiring", "prompt": "x", "subagent_type": "general-purpose"},
                   result="done", tur={"status": "completed", "agentId": "u1"})
    un.tool(u0 + 400, "Agent", {"description": "Implement Task 2: harness raw-reply capture", "prompt": "x", "subagent_type": "general-purpose"},
            result="done", tur={"status": "completed", "agentId": "u2"})
    un.reply(u0 + 900, "Done.")
    un.write(os.path.join(pdir, ids["unticked"] + ".jsonl"), u0 + 900)
    # its Task 1 implementer was resumed after a 90-minute pause: about 3 minutes of work over 93 minutes of wall clock
    usdir = os.path.join(pdir, ids["unticked"], "subagents")
    u1 = Transcript(ids["unticked"], repo, agent="u1")
    u1.prompt(u0 + 21, "Implement Task 1: alpha wiring")
    u1.tool(u0 + 120, "Read", {"file_path": os.path.join(plans_dir, files["unticked"])}, model="claude-sonnet-5-5")
    u1.tool(u0 + 120 + 5400, "Bash", {"command": "pytest -q"}, model="claude-sonnet-5-5")
    u1.tool(u0 + 5600, "SubagentHandback", {"message": "Task 1 done"}, model="claude-sonnet-5-5")
    u1.write(os.path.join(usdir, "agent-u1.jsonl"), u0 + 5600)
    with open(os.path.join(usdir, "agent-u1.meta.json"), "w") as fh:
        json.dump({"agentType": "general-purpose", "description": "Implement Task 1: alpha wiring", "toolUseId": usp1}, fh)

    # files for the path-safety checks: a symlink to a plan outside the repo, an oversize plan, a non-.md file
    outside = os.path.join(base, "outside.md")
    with open(outside, "w") as fh:
        fh.write("# Outside\n\n### Task 1: Secret\n- [ ] **Step 1: x**\n- [ ] **Step 2: y**\n")
    os.symlink(outside, os.path.join(plans_dir, "2026-10-15-evil.md"))
    with open(os.path.join(plans_dir, "2026-10-14-huge.md"), "w") as fh:
        fh.write("# Huge\n\n### Task 1: Big\n- [ ] **Step 1: x**\n- [ ] **Step 2: y**\n" + ("filler line\n" * 200000))
    with open(os.path.join(plans_dir, "notes.txt"), "w") as fh:
        fh.write("- [ ] a\n- [ ] b\n")
    os.mkfifo(os.path.join(plans_dir, "2026-10-13-fifo.md"))   # opening it for reading would block forever
    return {"cfg": cfg, "repo": repo, "ids": ids, "files": files, "now": now, "t0": t0, "c1": c1, "c2": c2,
            "ledger_sha": ledger_sha, "g0": g0, "l0": l0}


def rel(f):
    return "docs/superpowers/plans/" + f


def run(base):
    fx = build_fixture(base)
    files, ids, t0, now = fx["files"], fx["ids"], fx["t0"], fx["now"]

    # ── parsing ───────────────────────────────────────────────────────────
    p = P.parse_plan(WIDGET)
    check("parse: title from the first # heading", p["title"] == "Widget Implementation Plan", p["title"])
    check("parse: goal keeps inline code", p["goal"] == "Build the `widget` end to end.", p["goal"])
    check("parse: four tasks with ids and titles", [(t["id"], t["title"]) for t in p["tasks"]] == [("1", "Parser"), ("2", "Store"), ("3", "View `render()`"), ("4", "Docs")],
          str([(t["id"], t["title"]) for t in p["tasks"]]))
    check("parse: checkboxes inside ``` and ~~~ fences do not count", [len(t["steps"]) for t in p["tasks"]] == [2, 2, 1, 1], str([len(t["steps"]) for t in p["tasks"]]))
    check("parse: a # line inside a fence is not a heading", all("Not a heading" not in (t["title"] or "") for t in p["tasks"]) and p["steps_total"] == 6)
    check("parse: two milestones from the phase headings", [m["title"] for m in p["milestones"]] == ["Phase 1: Core", "Phase 2: UI"] and p["milestones"][1]["tasks"] == [2, 3],
          str(p["milestones"]))
    check("parse: a checkbox outside every task is loose, not counted", len(p["loose"]) == 1 and p["loose"][0]["section"] == "Self-review", str(p["loose"]))
    check("parse: step text loses the bold markers", p["tasks"][0]["steps"][0]["text"] == "Step 1: Write the failing test", p["tasks"][0]["steps"][0]["text"])
    v = P.parse_plan("# V\n\n## Task 7: Level two\n- [X] upper case tick\n\n### Task A1: Letter id\n- [ ] a\n\n### Task 10 [LINUX]: Tagged\n- [x] b\n"
                     "\n````markdown\n```\n- [ ] nested fence\n```\n- [ ] still fenced\n````\n- [ ] after\n")
    check("parse: ## Task, letter ids and [TAG] headings", [(t["id"], t["title"]) for t in v["tasks"]] == [("7", "Level two"), ("A1", "Letter id"), ("10", "Tagged")],
          str([(t["id"], t["title"]) for t in v["tasks"]]))
    check("parse: [X] counts as done", v["tasks"][0]["done_steps"] == 1)
    check("parse: a ```` fence holding ``` fences hides all its checkboxes", [len(t["steps"]) for t in v["tasks"]] == [1, 1, 2], str([len(t["steps"]) for t in v["tasks"]]))
    c = P.parse_plan("# List\n\n## Backend\n### Auth\n- [x] a\n- [ ] b\n### Billing\n- [ ] c\n\n## Web\n### Pages\n- [x] d\n")
    check("parse: without Task headings, headings that own checkboxes are the tasks", c["mode"] == "checklist" and [t["title"] for t in c["tasks"]] == ["Auth", "Billing", "Pages"],
          str([t["title"] for t in c["tasks"]]))
    check("parse: checklist milestones are the parent headings", [m["title"] for m in c["milestones"]] == ["Backend", "Web"], str(c["milestones"]))
    led = P.parse_ledger("# SDD ledger\nTask 1: complete (commits abc1234..def5678, review clean)\nTask 2: fix round 1/5 (x)\nTask 3: minor (deferred): y\n")
    check("ledger: only 'complete' lines, with the commit range", list(led) == ["1"] and led["1"]["commits"] == ("abc1234", "def5678"), str(led))

    # ── tool signals ───────────────────────────────────────────────────────
    sg = P.tool_signal("Edit", {"file_path": "/r/p.md", "old_string": "### Task 2: X\n- [ ] **Step 1: A**\n- [ ] B", "new_string": "### Task 2: X\n- [x] **Step 1: A**\n- [ ] B"}, 5)
    check("signal: an Edit flipping one box records that tick and its task", sg["tick"] == [["**Step 1: A**", "2"]], str(sg))
    check("signal: Read of a non-.md file is ignored", P.tool_signal("Read", {"file_path": "/r/x.py"}, 1) is None)
    sed = P.tool_signal("Bash", {"command": 'P=docs/superpowers/plans/x.md; sed -i "10,20s/^- \\[ \\] \\*\\*Step/- [x] **Step/" $P'}, 1)
    check("signal: sed -i writing [x] into a named plan is a tick", sed and sed.get("tick") and sed["md"] == ["docs/superpowers/plans/x.md"], str(sed))
    grep = P.tool_signal("Bash", {"command": 'grep -c "^- \\[x\\]" docs/superpowers/plans/x.md 2>/dev/null'}, 1)
    check("signal: grep counting [x] is not a tick", grep and not grep.get("tick"), str(grep))
    lg = P.tool_signal("Bash", {"command": "echo 'Task 3: complete (commits a..b)' >> $W/progress.md"}, 1)
    check("signal: a ledger line appended in Bash", lg and lg.get("ledger") == ["3"], str(lg))
    td = P.tool_signal("TodoWrite", {"todos": [{"content": "Task 1: A", "status": "completed", "activeForm": "a"}]}, 1)
    check("signal: TodoWrite keeps content and status", td["todos"] == [("Task 1: A", "completed")], str(td))
    tc = P.tool_signal("TaskUpdate", {"taskId": "2", "status": "completed"}, 1)
    check("signal: TaskUpdate keeps id and status", tc["id"] == "2" and tc["status"] == "completed", str(tc))

    # ── linkage, ticks, status ─────────────────────────────────────────────
    st = Store(root=fx["cfg"], days=7)
    st.refresh(force=True)
    eng = P.Plans(st)
    box = {}
    th = threading.Thread(target=lambda: box.update(p=eng.payload("", alerts=[], everything=True)), daemon=True)
    th.start()
    th.join(20)
    check("discover: a FIFO named like a plan does not hang the scan", "p" in box)
    pay = box.get("p") or {"plans": []}
    by = {x["key"]: x for x in pay["plans"]}
    check("discover: the six plans, not the symlink, the FIFO, the oversize file or notes.txt",
          set(by) == {rel(f) for f in files.values()}, str(sorted(by)))
    w = by.get(rel(files["widget"]), {})
    check("widget: steps 3 of 6 from the checkboxes", w.get("steps") == {"done": 3, "total": 6} and w["source"]["steps"] == "plan checkboxes", str(w.get("steps")))
    check("widget: 1 of 4 tasks done", w.get("tasks") == {"done": 1, "total": 4}, str(w.get("tasks")))
    check("widget: live while its session writes", w.get("status") == "live" and w.get("live"), w.get("status"))
    check("widget: current task is Task 2, run by its running implementer", (w.get("current") or {}).get("pos") == 2
          and w["current"]["who"]["running"] and w["current"]["who"]["agent_id"] == "a2", str(w.get("current")))
    check("widget: the implementer's model is shown by name", (w.get("current") or {}).get("who", {}).get("model") == "Sonnet 5.5", str(w.get("current")))
    check("widget: ETA hidden with fewer than two finished tasks", w.get("eta") is None and w.get("median_task_secs") is None, str(w.get("eta")))
    check("widget: milestone segments weighted by steps", [(s_["done"], s_["total"]) for s_ in w.get("segments", [])] == [(3, 4), (0, 2)], str(w.get("segments")))
    check("widget: the executing session is listed and drives it from the main thread",
          [(e["session"], e["agents"]) for e in w.get("executors", [])] == [(ids["live"], [None])], str(w.get("executors")))
    check("chips: the live session carries the widget chip", pay["chips"].get(ids["live"], {}).get("key") == rel(files["widget"])
          and pay["chips"][ids["live"]]["pos"] == 2, str(pay["chips"].get(ids["live"])))
    d = eng.detail("proj", rel(files["widget"]), alerts=[])
    tl = {t["pos"]: t for t in d["plan"]["task_list"]}
    check("ticks: Task 1 finished at its subagent's Edit", tl[1]["done"] and tl[1]["finished_src"] == "tick" and abs(tl[1]["finished"] - (t0 + 150)) < 1,
          "%s %s" % (tl[1]["finished_src"], tl[1]["finished"]))
    check("ticks: each ticked step carries its tick time", [round(s_["t"] - t0) if s_["t"] else None for s_ in tl[1]["steps"]] == [150, 150]
          and round(tl[2]["steps"][0]["t"] - t0) == 400 and tl[2]["steps"][1]["t"] is None, str([s_["t"] for s_ in tl[2]["steps"]]))
    check("agents: Task 1 has its implementer and reviewer, Task 2 its running implementer",
          [(a["role"], a["status"]) for a in tl[1]["agents"]] == [("implement", "done"), ("review", "done")]
          and [(a["agent_id"], a["status"]) for a in tl[2]["agents"]] == [("a2", "running")], str(tl[1]["agents"]))
    check("agents: Task 1 started at its spawn", abs(tl[1]["started"] - (t0 + 20)) < 1, str(tl[1]["started"]))
    check("commits: the parser commit maps to Task 1", [c_["short"] for c_ in tl[1]["commits"]] == [fx["c1"]], str(tl[1]["commits"]))
    check("commits: the store commit maps to Task 2 (running agent window)", [c_["short"] for c_ in tl[2]["commits"]] == [fx["c2"]], str(tl[2]["commits"]))
    check("detail: the todo item is attached to its task", tl[2].get("todo") == "in_progress", str(tl[2].get("todo")))
    check("detail: series has Task 1's finish", len(d["plan"]["series"]) == 1)

    g = by.get(rel(files["gadget"]), {})
    check("todo-only: gadget shows 2 of 3 tasks from the todo list", g.get("tasks") == {"done": 2, "total": 3} and g["source"]["tasks"] == "todo list",
          "%s %s" % (g.get("tasks"), g.get("source")))
    check("todo-only: no checkboxes means no step count", g.get("steps") == {"done": 0, "total": 0})
    check("status: gadget paused after five quiet hours", g.get("status") == "paused", g.get("status"))
    check("ETA: shown with two timed tasks, marked as no finish time while paused", g.get("eta") and g["eta"]["at"] is None and g["median_task_secs"] == 750
          and g["eta"]["secs_left"] == 750, str(g.get("eta")) + " " + str(g.get("median_task_secs")))
    gd = eng.detail("proj", rel(files["gadget"]), alerts=[])
    gt = {t["pos"]: t for t in gd["plan"]["task_list"]}
    check("todo-only: finish times come from the todo updates", gt[1]["finished_src"] == "todo" and round(gt[1]["finished"] - fx["g0"]) == 620, str(gt[1]))

    lg_ = by.get(rel(files["ledger"]), {})
    check("ledger: Task 1 done from the SDD ledger though its box is unticked", lg_.get("tasks") == {"done": 1, "total": 2} and lg_["source"]["tasks"] == "SDD ledger",
          "%s %s" % (lg_.get("tasks"), lg_.get("source")))
    check("ledger: the bar measures tasks when no box is ticked", lg_.get("unit") == "tasks", lg_.get("unit"))
    ld = eng.detail("proj", rel(files["ledger"]), alerts=[])
    lt = ld["plan"]["task_list"][0]
    check("ledger: finish time from the Bash that wrote the ledger line", lt["finished_src"] == "ledger" and round(lt["finished"] - fx["l0"]) == 900, str(lt))
    check("ledger: commits from the ledger's commit range", [c_["short"] for c_ in lt["commits"]] == [fx["ledger_sha"]], str(lt["commits"]))

    un_ = by.get(rel(files["unticked"]), {})
    check("nobody ticks: a task whose implementer finished counts as done when no box was ever ticked",
          un_.get("tasks") == {"done": 1, "total": 2} and un_["source"]["tasks"] == "subagent finished", "%s %s" % (un_.get("tasks"), un_.get("source")))
    ud = eng.detail("proj", rel(files["unticked"]), alerts=[])
    u1t = ud["plan"]["task_list"][0]
    check("duration: a resumed subagent's 90-minute pause is not working time (active from its transcript, wall clock kept apart)",
          u1t.get("wall") and u1t["wall"] > 5000 and u1t.get("active") is not None and u1t["active"] < 600, "%s %s" % (u1t.get("active"), u1t.get("wall")))
    check("task owner: a 'Task 2: <unrelated name>' spawn is not this plan's Task 2", ud["plan"]["task_list"][1]["agents"] == [],
          str(ud["plan"]["task_list"][1]["agents"]))
    check("status: every box ticked is done", by.get(rel(files["done"]), {}).get("status") == "done")
    check("status: nothing ticked and no session is not started", by.get(rel(files["idle"]), {}).get("status") == "not_started")
    recent = eng.payload("", alerts=[])
    check("list: plans without activity are left out unless asked", rel(files["idle"]) not in {x["key"] for x in recent["plans"]} and recent["older"] >= 1,
          str([x["key"] for x in recent["plans"]]))
    check("list: order is live first, done last", [x["status"] for x in pay["plans"]].index("live") == 0 and pay["plans"][-1]["status"] in ("done", "not_started"),
          str([x["status"] for x in pay["plans"]]))
    blk = eng.payload("proj", alerts=[{"session": ids["live"], "agent_id": "a2", "kind": "permission", "title": "Approve Bash", "message": "pytest -q"}])
    wb = next(x for x in blk["plans"] if x["key"] == rel(files["widget"]))
    check("status: an open alert on the executing session makes it blocked, with the decision's target",
          wb["status"] == "blocked" and wb["blocked"]["session"] == ids["live"] and wb["blocked"]["agent_id"] == "a2", str(wb.get("blocked")))
    t1 = time.time()
    for _ in range(20):
        eng.payload("", alerts=[])
    check("cache: a re-poll with nothing changed is cheap", (time.time() - t1) / 20 < 0.05, "%.3fs" % ((time.time() - t1) / 20))

    # ── endpoints ─────────────────────────────────────────────────────────
    srv = make_server(0, st, fallback=False)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        tok = pair(srv)

        def get(path, token=tok):
            c = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=20)
            hdr = {"Host": "127.0.0.1:%d" % srv.port}
            if token:
                hdr["X-CC-Dash-Token"] = token
            c.request("GET", path, headers=hdr)
            r = c.getresponse()
            body = r.read()
            c.close()
            try:
                return r.status, json.loads(body)
            except ValueError:
                return r.status, body
        s1, _ = get("/api/plans", token=None)
        check("endpoint: /api/plans without the token is 401", s1 == 401, str(s1))
        s2, _ = get("/api/plans/detail?project=proj&path=" + quote(rel(files["widget"])), token="wrong")
        check("endpoint: a wrong token is 401", s2 == 401, str(s2))
        s3, b3 = get("/api/plans")
        check("endpoint: /api/plans with the token lists the plans", s3 == 200 and any(x["key"] == rel(files["widget"]) for x in b3["plans"]), str(s3))
        s4, b4 = get("/api/plans?project=proj&all=1")
        check("endpoint: ?project= and all=1", s4 == 200 and len(b4["plans"]) == 6, str(len(b4.get("plans", []))) if isinstance(b4, dict) else str(b4))
        s5, b5 = get("/api/plans/detail?project=proj&path=" + quote(rel(files["widget"])))
        check("endpoint: detail of a plan", s5 == 200 and b5["plan"]["title"] == "Widget Implementation Plan", str(s5))
        bad = {
            "traversal": "docs/superpowers/plans/../../../outside.md",
            "dotdot only": "../outside.md",
            "absolute": os.path.join(base, "outside.md"),
            "symlink out of the repo": rel("2026-10-15-evil.md"),
            "non-.md": rel("notes.txt"),
            "oversize": rel("2026-10-14-huge.md"),
            "a .md that is not a plan": "README.md",
            "NUL": "docs/x\x00.md",
        }
        for name, path in bad.items():
            sc, _ = get("/api/plans/detail?project=proj&path=" + quote(path))
            check("path safety: %s is 404" % name, sc == 404, str(sc))
        sc, _ = get("/api/plans/detail?project=nope&path=" + quote(rel(files["widget"])))
        check("path safety: an unknown project is 404", sc == 404, str(sc))
        sc, _ = get("/api/plans/detail?project=&path=" + quote(rel(files["widget"])))
        check("path safety: no project is 404", sc == 404, str(sc))
        sc, b = get("/api/plans?project=" + quote("../../etc"))
        check("endpoint: a traversal-looking project filter matches nothing", sc == 200 and b["plans"] == [], str(sc))
        sc, _ = get("/plans.js", token=None)
        check("assets: plans.js is served without a token (it carries no data)", sc == 200, str(sc))
    finally:
        srv.shutdown()
        srv.server_close()


def main():
    base = tempfile.mkdtemp(prefix="cc-dash-plans-")
    try:
        run(base)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    fails = [r for r in RESULTS if not r[1]]
    for name, ok, detail in RESULTS:
        print(("PASS " if ok else "FAIL ") + name + ("" if ok or not detail else "  -- " + detail))
    print(f"total={len(RESULTS)}  pass={len(RESULTS) - len(fails)}  fail={len(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--fixture":
        os.makedirs(sys.argv[2], exist_ok=True)
        fx = build_fixture(sys.argv[2])
        print(json.dumps({"config": fx["cfg"], "repo": fx["repo"], "ids": fx["ids"]}))
        sys.exit(0)
    sys.exit(main())
