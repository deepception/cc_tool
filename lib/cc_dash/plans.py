"""Plan progress: implementation plans on disk joined with what the transcripts did to them.

What counts as a plan (read-only, bounded):
  - every *.md under docs/superpowers/plans/ (the Superpowers writing-plans
    location) with `Task N` headings or at least two checkboxes;
  - other *.md under docs/ with at least three checkboxes outside code
    fences, plus TASKS.md (cc_tool's on-disk task list for long runs) and
    docs/start-here.md at a checkout's root.
A project is a repo root from store.resolve_projects(); its worktrees (under
.claude/worktrees/, linked git worktrees, sibling <repo>-<suffix> checkouts
seen in transcripts) are searched too, and the same plan path in two
checkouts is one plan whose most advanced copy wins.

A plan parses into tasks (`### Task N: ...` headings, any level, ids like 7,
A1 or 10b; without them, every heading that directly owns checkboxes),
steps (`- [ ]` / `- [x]` lines, never inside ``` or ~~~ fences) and
milestones (the headings above the tasks, when there are two or more).

Transcript evidence (parser.Acc collects it through tool_signal()):
  - Read/Write/Edit of the plan, Bash commands and Skill args naming it;
  - Edit calls that flip `- [ ]` to `- [x]` (exact tick times), Bash
    sed/awk/tick.sh ticks, SDD ledger lines `Task N: complete (commits a..b)`;
  - Agent spawns whose description names `Task N` (subagent-driven-
    development's "Implement Task N: ...", "Review Task N ..."), with the
    subagent's model, start, end and outcome;
  - TodoWrite lists and TaskCreate/TaskUpdate calls;
  - the repo's .superpowers/sdd/<plan>/progress.md ledger on disk;
  - commits from `git log` (read-only) inside each task's window.

Ranking, per number:
  steps done      the plan's checkboxes (the only source of steps)
  task done       1. all its checkboxes ticked  2. the SDD ledger says complete
                  3. a todo item for it is completed (task without checkboxes)
                  4. its implementer subagent finished (plan without checkboxes
                     and without a ledger)
  task finished   1. the Edit that ticked its last step  2. the Bash command
  at              that wrote its ledger line  3. a Bash tick after its agents
                  ended  4. its last agent's end  5. the todo item's completion
A plan with neither tasks nor checkboxes shows its executing session's todo
list instead, and says so.
"""
import os
import posixpath
import re
import stat
import statistics
import subprocess
import threading
import time

from . import docs as docs_mod
from . import pricing
from .parser import ACTIVE_GAP, clip, tilde

_RFLAGS = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


def read_text(path, limit):
    """Up to `limit` chars of a regular file, or raise OSError. Never blocks on a FIFO or
    device and never follows a final symlink (a plan folder is project-writable)."""
    fd = os.open(path, _RFLAGS)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("not a regular file: " + path)
        with os.fdopen(fd, encoding="utf-8", errors="replace", closefd=True) as fh:
            fd = -1
            return fh.read(limit)
    finally:
        if fd >= 0:
            os.close(fd)

MAX_PLAN_BYTES = 2 * 1024 * 1024   # plans above this are skipped (the largest real one is ~0.75 MB)
MAX_DOC_FILES = 800                # *.md files looked at per checkout under docs/
MAX_DEPTH = 6                      # directory depth under docs/
DISCOVER_TTL = 20.0                # re-walk the docs trees at most this often
PAUSE_SECS = 2 * 3600              # an unfinished plan with no activity this long is paused
LIVE_SECS = 600                    # store.LIVE_SECS: a session written this recently is live
MAX_SEGMENTS = 40                  # the progress bar draws one segment per milestone up to this many
GIT_TIMEOUT = 5
EV_MAX = 8192                      # cached session evidence entries (per session and plan set) before a reset
SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "__pycache__", "dist", "build", "vendor", ".next",
             ".dart_tool", ".gradle", "Pods", "target", ".cache", "worktrees"}
GENERIC_NAMES = {"readme.md", "plan.md", "tasks.md", "todo.md", "log.md", "spec.md", "index.md", "notes.md",
                 "changelog.md", "claude.md", "agents.md", "progress.md", "kickoff.md", "start-here.md"}
EXEC_SKILLS = ("executing-plans", "subagent-driven-development")
META_BYTES = 16 * 1024             # a spec's or document's head read for its frontmatter and title
MAX_DOCUMENTS = 200                # the Work view's Documents list (newest)
MAX_BOARD = 60                     # ...and its Agent runs
MAX_META = 4000                    # cached head reads before a reset
DOC_PARSE_BYTES = 512 * 1024       # a document above this is typed by folder and name only
_BOARD_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_CTRL = re.compile("[\x00-\x1f\x7f-\x9f]")
_FM_KEY = re.compile(r"^(title|status|type)\s*:\s*(.*)$")
_DOC_TYPE = re.compile(r"^[a-z][a-z-]{0,19}$")

_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})(.*)$")
_HEAD = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_CB = re.compile(r"^(\s*)[-*+]\s+\[([ xX])\]\s+(.+?)\s*$")
_ID = r"([A-Za-z]{0,2}\d+(?:\.\d+)?[a-z]?)"
_TASK_HEAD = re.compile(r"^\**Task\s+" + _ID + r"\b\s*(?:\[[^\]]*\]\s*)?(?:[:.—–-]+\s*)?(.*?)\**$")
_TASK_REF = re.compile(r"\bTask\s+" + _ID + r"(?![\w.])")
_LEDGER = re.compile(r"\bTask\s+" + _ID + r"\**\s*:\s*complete\b([^\n]*)")
_RANGE = re.compile(r"\b([0-9a-f]{7,40})\.\.([0-9a-f]{7,40})\b")   # "(commits a..b, ...)" or "(a..b) — cherry-picked"
_MD_TOKEN = re.compile(r"[\w~./@+-]*[\w-]\.md\b")
_TICK_SH = re.compile(r"\btick(?:\.sh)?\s+((?:[A-Za-z]{0,2}\d+[a-z]?[\s,]*)+)")
# A command that writes "[x]" somewhere: sed -i / perl -pi, awk or a script that writes a file
# back (sub(), replace(), write, mv, a redirect). Only counts for a plan the command names.
_TICK_WRITE = re.compile(r"\s-p?i\b|\bsub\(|\.replace\(|\bwrite|\bmv\b|(?<![0-9&>])>{1,2}\s*[\w\"'$/.~]")
_GOAL = re.compile(r"^\*\*Goal:?\*\*:?\s*(.+)$")
_SPEC = re.compile(r"^\*\*Spec:?\*\*:?\s*(.+)$")
_HEX_RANGE = re.compile(r"^[0-9a-f]{7,40}\.\.[0-9a-f]{7,40}$")


# ── transcript signals (called by parser.Acc for every tool call) ─────────
def _flips(old, new):
    """[text, task id or None] for each checkbox that is `[ ]` in old and `[x]` in new; the task id is
    the `Task N` heading above that line inside the edited text, when the edit includes one."""
    if not (isinstance(old, str) and isinstance(new, str)) or "[" not in new:
        return []
    before = {}
    for line in old.split("\n"):
        m = _CB.match(line)
        if m:
            before[norm(m.group(3))] = m.group(2)
    out = []
    task = None
    for line in new.split("\n"):
        hm = _HEAD.match(line)
        if hm:
            tm = _TASK_HEAD.match(hm.group(2))
            task = tm.group(1) if tm else None
            continue
        m = _CB.match(line)
        if m and m.group(2) in "xX" and before.get(norm(m.group(3))) == " ":
            out.append([clip(m.group(3), 200), task])
    return out


def tool_signal(name, ti, ts):
    """A small dict when this tool call can tie a transcript to a plan, else None. Must stay cheap."""
    if name in ("Read", "Write", "Edit", "MultiEdit"):
        p = ti.get("file_path")
        if not isinstance(p, str) or not p.lower().endswith(".md"):
            return None
        sig = {"k": name.lower(), "t": ts, "p": p}
        if name in ("Edit", "MultiEdit"):
            pairs = [(ti.get("old_string"), ti.get("new_string"))] if name == "Edit" else \
                [(e.get("old_string"), e.get("new_string")) for e in (ti.get("edits") or []) if isinstance(e, dict)]
            ticks, ledger = [], []
            for old, new in pairs:
                ticks.extend(_flips(old, new))
                if p.endswith("progress.md") and isinstance(new, str):
                    had = set(m.group(1) for m in _LEDGER.finditer(old or ""))
                    ledger.extend(m.group(1) for m in _LEDGER.finditer(new) if m.group(1) not in had)
            if ticks:
                sig["tick"] = ticks[:60]
            if ledger:
                sig["ledger"] = ledger[:20]
        return sig
    if name == "Bash":
        cmd = ti.get("command")
        if not isinstance(cmd, str):
            return None
        commit = "git commit" in cmd
        if ".md" not in cmd and "tick" not in cmd:
            return {"k": "commit", "t": ts} if commit else None
        sig = {"k": "bash", "t": ts, "md": list(dict.fromkeys(_MD_TOKEN.findall(cmd)))[:12]}
        if commit:
            sig["commit"] = True
        if "progress.md" in cmd or "ledger" in cmd:
            led = list(dict.fromkeys(m.group(1) for m in _LEDGER.finditer(cmd)))
            if led:
                sig["ledger"] = led[:20]
        if "[x]" in cmd and _TICK_WRITE.search(cmd):
            sig["tick"] = True
        tt = _TICK_SH.search(cmd)
        if tt:
            sig["tick"] = True
            sig["tick_tasks"] = re.findall(r"[A-Za-z]{0,2}\d+[a-z]?", tt.group(1))[:20]
        if not sig["md"] and not sig.get("tick"):
            return {"k": "commit", "t": ts} if commit else None
        return sig
    if name == "Skill":
        sk = ti.get("skill")
        if not isinstance(sk, str):
            return None
        args = ti.get("args")
        return {"k": "skill", "t": ts, "skill": clip(sk, 100), "args": clip(args, 400) if isinstance(args, str) else ""}
    if name == "TodoWrite":
        todos = ti.get("todos")
        if not isinstance(todos, list):
            return None
        items = [(clip(x.get("content") or x.get("activeForm") or "", 200), str(x.get("status") or "pending"))
                 for x in todos[:80] if isinstance(x, dict)]
        return {"k": "todo", "t": ts, "todos": items}
    if name == "TaskCreate":
        subj = ti.get("subject") or ti.get("description") or ti.get("activeForm")
        return {"k": "tcreate", "t": ts, "subject": clip(subj, 200)} if isinstance(subj, str) else None
    if name == "TaskUpdate":
        tid = ti.get("taskId") or ti.get("id")
        if tid is None:
            return None
        return {"k": "tupdate", "t": ts, "id": str(tid)[:20], "status": str(ti.get("status") or "")[:20],
                "subject": clip(ti.get("subject"), 200) if isinstance(ti.get("subject"), str) else ""}
    return None


def norm(text):
    """Comparable form of a checkbox or task text: no markup, lower case, single spaces."""
    t = re.sub(r"[*_`]", "", text or "").lower()
    return " ".join(t.split())[:160]


# ── parsing a plan file ───────────────────────────────────────────────────
def _clean(text):
    """Step or heading text for display: bold and italic markers removed, inline code kept."""
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", text or "")
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", t)
    return clip(t, 300)


def parse_plan(text):
    """{title, goal, spec, tasks, milestones, loose, steps, mode} from Markdown text."""
    lines = text.split("\n")
    heads, cbs = [], []
    title = goal = spec = ""
    fence = None
    for i, raw in enumerate(lines):
        s = raw.rstrip("\r")
        fm = _FENCE.match(s)
        if fence is not None:
            if fm and fm.group(1)[0] == fence[0] and len(fm.group(1)) >= fence[1] and not fm.group(2).strip():
                fence = None
            continue
        if fm:
            fence = (fm.group(1)[0], len(fm.group(1)))
            continue
        hm = _HEAD.match(s)
        if hm:
            heads.append((i, len(hm.group(1)), hm.group(2)))
            if not title and len(hm.group(1)) == 1:
                title = _clean(hm.group(2))
            continue
        cm = _CB.match(s)
        if cm:
            cbs.append((i, cm.group(2) in "xX", _clean(cm.group(3)), len(cm.group(1).expandtabs(4))))
            continue
        if not goal:
            gm = _GOAL.match(s.strip())
            if gm:
                goal = _clean(gm.group(1))
                continue
        if not spec:
            sm = _SPEC.match(s.strip())
            if sm:
                spec = clip(sm.group(1).strip().strip("`"), 300)
    if not title and heads:
        title = _clean(heads[0][2])
    task_heads = []
    for (i, lvl, txt) in heads:
        tm = _TASK_HEAD.match(txt)
        if tm:
            task_heads.append((i, lvl, tm.group(1), _clean(tm.group(2)) or "Task " + tm.group(1)))
    tasks, loose = [], []
    if task_heads:
        mode = "tasks"
        th_lines = {t[0] for t in task_heads}
        events = sorted([(i, "h", lvl, txt) for (i, lvl, txt) in heads] + [(c[0], "c", c) for c in cbs], key=lambda e: e[0])
        cur = None
        parent = None          # the latest non-task heading, a milestone candidate
        title_line = heads[0][0] if heads and heads[0][1] == 1 else -1
        loose_owner = None
        for ev in events:
            if ev[1] == "h":
                i, _, lvl, txt = ev
                if i in th_lines:
                    t = next(x for x in task_heads if x[0] == i)
                    ms = parent if parent and parent[1] < lvl else None
                    cur = {"id": t[2], "title": t[3], "level": lvl, "line": i + 1, "steps": [],
                           "milestone": ms[2] if ms else None, "ms_line": ms[0] if ms else None}
                    tasks.append(cur)
                    continue
                if cur is not None and lvl <= cur["level"]:
                    cur = None
                if i != title_line:
                    parent = (i, lvl, _clean(txt))
                loose_owner = _clean(txt)
            else:
                c = ev[2]
                step = {"text": c[2], "done": c[1], "line": c[0] + 1, "indent": c[3]}
                if cur is not None:
                    cur["steps"].append(step)
                else:
                    step["section"] = loose_owner or ""
                    loose.append(step)
    else:
        mode = "checklist"
        events = sorted([(i, "h", lvl, txt) for (i, lvl, txt) in heads] + [(c[0], "c", c) for c in cbs], key=lambda e: e[0])
        stack = []             # (level, text, line) of the headings above
        cur = None
        for ev in events:
            if ev[1] == "h":
                i, _, lvl, txt = ev
                while stack and stack[-1][0] >= lvl:
                    stack.pop()
                stack.append((lvl, _clean(txt), i))
                cur = None
                continue
            c = ev[2]
            if cur is None:
                owner = stack[-1] if stack else (0, "Checklist", -1)
                parents = [x for x in stack[:-1] if x[0] >= 2]
                ms = parents[-1] if parents else None
                cur = {"id": None, "title": owner[1], "level": owner[0], "line": owner[2] + 1, "steps": [],
                       "milestone": ms[1] if ms else None, "ms_line": ms[2] if ms else None}
                tasks.append(cur)
            cur["steps"].append({"text": c[2], "done": c[1], "line": c[0] + 1, "indent": c[3]})
    # milestones: the parent headings, when the tasks fall under two or more of them
    groups = []
    for n, t in enumerate(tasks):
        k = (t["ms_line"], t["milestone"])
        if not groups or groups[-1]["k"] != k:
            groups.append({"k": k, "title": t["milestone"] or "", "tasks": []})
        groups[-1]["tasks"].append(n)
    named = [g for g in groups if g["title"]]
    milestones = [{"title": g["title"] or "Before the first milestone", "tasks": g["tasks"]} for g in groups] if len(named) >= 2 else []
    for n, t in enumerate(tasks):
        t["pos"] = n + 1
        t.pop("ms_line", None)
        t["done_steps"] = sum(1 for s in t["steps"] if s["done"])
    return {"title": title or "Untitled plan", "goal": goal, "spec": spec, "mode": mode, "tasks": tasks,
            "milestones": milestones, "loose": loose[:200],
            "steps_total": sum(len(t["steps"]) for t in tasks), "steps_done": sum(t["done_steps"] for t in tasks),
            "checkboxes": len(cbs)}


def parse_ledger(text):
    """{task id: {"commits": (a, b) or None, "line": text}} from an SDD progress.md."""
    out = {}
    for m in _LEDGER.finditer(text or ""):
        rng = _RANGE.search(m.group(2) or "")
        out[m.group(1)] = {"commits": (rng.group(1), rng.group(2)) if rng else None, "line": clip(m.group(0), 200)}
    return out


def is_plan(parsed, where):
    """Whether a parsed file is worth tracking, by where it sits."""
    if parsed is None:
        return False
    if where == "planmode":     # docs/plans/: Claude Code's plan-mode files, tracked even with no checkbox
        return True
    if where == "plans":
        return parsed["mode"] == "tasks" or parsed["checkboxes"] >= 2
    if where == "root":
        return parsed["checkboxes"] >= 1
    return parsed["checkboxes"] >= 3


# ── the Work view's joins: specs, document heads, the run-state board ─────────
def spec_path(raw):
    """The normalised relative path a plan's `**Spec:**` value names, or '' when it names none, is
    absolute, '~'-based, escapes the checkout, passes through a dot-directory or is not Markdown."""
    s = (raw or "").strip()
    m = re.search(r"\]\(([^)\s]+)\)", s)
    tok = m.group(1) if m else (s.split() or [""])[0]
    tok = tok.strip("`'\"()<>[],;")
    if not tok or "\x00" in tok or "\\" in tok or tok[0] in "/~" or not tok.lower().endswith(".md"):
        return ""
    n = posixpath.normpath(tok)
    if n == ".." or n.startswith("../") or n.startswith("/"):
        return ""
    if any(seg.startswith(".") for seg in n.split("/")):
        return ""
    return n


def _plain(s, n):
    """Free text from a file made safe for the payload: no control characters, no live credential."""
    return clip(docs_mod.redact(_CTRL.sub(" ", s or "")), n)


def head_meta(text):
    """{title, status, type} from a document's head: the frontmatter keys, the title falling back to the
    first H1. Values are plain one-line text."""
    lines = text.split("\n")
    out = {"title": "", "status": "", "type": ""}
    start = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, min(len(lines), 101)):
            if lines[i].strip() == "---":
                start = i + 1
                break
            m = _FM_KEY.match(lines[i].rstrip("\r"))
            if m:
                v = m.group(2).strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                    v = v[1:-1]
                out[m.group(1)] = v
    if not out["title"]:
        fence = False
        for ln in lines[start:]:
            if ln.lstrip().startswith(("```", "~~~")):
                fence = not fence
            elif not fence and ln.startswith("# "):
                out["title"] = ln[2:].strip()
                break
    return {"title": _plain(out["title"], 200), "status": _plain(out["status"], 40),
            "type": out["type"] if _DOC_TYPE.match(out["type"]) else ""}


# ── the engine ────────────────────────────────────────────────────────────
class Plans:
    def __init__(self, store, git=True):
        self.store = store
        self.git_enabled = git
        self.lock = threading.RLock()
        self._parse = {}       # abs path -> (size, mtime_ns, parsed or None)
        self._found = {}       # checkout -> (time, [(abs, where)])
        self._ledgers = {}     # checkout -> (time, {plan rel: ledger path})
        self._ledger_parse = {}
        self._git = {}         # args tuple + reflog stamp -> commits
        self._ev = {}          # (session id, plans signature) -> (signature, evidence); one per window's plan set
        self._meta = {}        # abs path -> (size, mtime_ns, head_meta); specs and Work documents
        self._docs = None      # a Docs tree reader, made on first use (the Work view's Documents)

    # discovery ------------------------------------------------------------
    @staticmethod
    def _checkout_of(cwd, root):
        """The checkout directory holding cwd: the nearest parent with a .git entry (at most 8 levels)."""
        d = cwd.rstrip("/")
        for _ in range(8):
            if not d or d == "/":
                break
            if os.path.lexists(os.path.join(d, ".git")):
                return d
            if d == root:
                break
            d = os.path.dirname(d)
        return None

    def checkouts(self, root, sessions):
        out = [root] if root and os.path.isdir(root) else []
        wt = os.path.join(root, ".claude", "worktrees")
        try:
            for e in os.scandir(wt):
                if e.is_dir(follow_symlinks=False):
                    out.append(e.path)
        except OSError:
            pass
        # linked git worktrees: <root>/.git/worktrees/<name>/gitdir names "<worktree>/.git"
        try:
            for e in os.scandir(os.path.join(root, ".git", "worktrees")):
                try:
                    wt = os.path.dirname(read_text(os.path.join(e.path, "gitdir"), 4096).strip())
                except OSError:
                    continue
                if wt and wt not in out and os.path.isdir(wt):
                    out.append(wt)
        except OSError:
            pass
        for s in sessions:
            cwd = s.acc.cwd or ""
            if cwd and cwd != root and os.path.isdir(cwd):
                c = self._checkout_of(cwd, root)
                if c and c not in out:
                    out.append(c)
        return out[:60]

    def _walk_md(self, base, limit):
        """*.md files under base, depth-bounded, no symlinked directories, skipping build and vendor dirs."""
        out = []
        base_depth = base.rstrip("/").count("/")
        for d, dirs, files in os.walk(base, followlinks=False):
            dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.startswith("."))
            if d.count("/") - base_depth >= MAX_DEPTH:
                dirs[:] = []
            for f in sorted(files):
                if f.lower().endswith(".md") and not os.path.islink(os.path.join(d, f)):   # a symlink could point anywhere
                    out.append(os.path.join(d, f))
                    if len(out) >= limit:
                        return out
        return out

    def discover(self, checkout):
        """[(abs path, where)] of candidate plan files in one checkout, cached for DISCOVER_TTL."""
        now = time.time()
        c = self._found.get(checkout)
        if c and now - c[0] < DISCOVER_TTL:
            return c[1]
        found = []
        plans_dir = os.path.join(checkout, "docs", "superpowers", "plans")
        seen = set()
        if os.path.isdir(plans_dir) and not os.path.islink(plans_dir):
            for p in self._walk_md(plans_dir, MAX_DOC_FILES):
                found.append((p, "plans"))
                seen.add(p)
        pm_dir = os.path.join(checkout, "docs", "plans")
        if os.path.isdir(pm_dir) and not os.path.islink(pm_dir):
            for p in self._walk_md(pm_dir, MAX_DOC_FILES):
                found.append((p, "planmode"))
                seen.add(p)
        docs = os.path.join(checkout, "docs")
        if os.path.isdir(docs) and not os.path.islink(docs):
            for p in self._walk_md(docs, MAX_DOC_FILES):
                if p not in seen:
                    found.append((p, "docs"))
        for name in ("TASKS.md", "docs/start-here.md"):
            p = os.path.join(checkout, name)
            if p not in seen and os.path.isfile(p) and not os.path.islink(p):
                found = [f for f in found if f[0] != p] + [(p, "root")]
        self._found[checkout] = (now, found)
        return found

    def parsed(self, path):
        """The cached parse of one file, by (size, mtime); None when unreadable or too big."""
        try:
            st = os.stat(path)
        except OSError:
            self._parse.pop(path, None)
            return None, None
        c = self._parse.get(path)
        if c and c[0] == st.st_size and c[1] == st.st_mtime_ns:
            return c[2], st
        res = None
        if st.st_size <= MAX_PLAN_BYTES:
            try:
                res = parse_plan(read_text(path, MAX_PLAN_BYTES + 1))
            except OSError:
                res = None
        self._parse[path] = (st.st_size, st.st_mtime_ns, res)
        return res, st

    def ledgers(self, checkout):
        """{plan path relative to the checkout: progress.md path} from .superpowers/sdd/*/."""
        now = time.time()
        c = self._ledgers.get(checkout)
        if c and now - c[0] < DISCOVER_TTL:
            return c[1]
        out = {}
        base = os.path.join(checkout, ".superpowers", "sdd")
        try:
            ents = [e for e in os.scandir(base) if e.is_dir(follow_symlinks=False)]
        except OSError:
            ents = []
        for e in ents[:200]:
            prog = os.path.join(e.path, "progress.md")
            if not os.path.isfile(prog):
                continue
            rel = ""
            try:
                rel = read_text(os.path.join(e.path, "plan-path"), 1024).strip()
            except OSError:
                try:
                    m = re.search(r"plan:\s*(\S+\.md)", read_text(prog, 4096).split("\n", 1)[0])
                    rel = m.group(1) if m else ""
                except OSError:
                    rel = ""
            if rel.startswith(checkout + "/"):
                rel = rel[len(checkout) + 1:]
            out[rel or ("docs/superpowers/plans/" + e.name + ".md")] = prog
        self._ledgers[checkout] = (now, out)
        return out

    def ledger(self, path):
        try:
            st = os.stat(path)
        except OSError:
            return {}, None
        c = self._ledger_parse.get(path)
        if c and c[0] == (st.st_size, st.st_mtime_ns):
            return c[1], st.st_mtime
        try:
            data = parse_ledger(read_text(path, MAX_PLAN_BYTES))
        except OSError:
            data = {}
        self._ledger_parse[path] = ((st.st_size, st.st_mtime_ns), data)
        return data, st.st_mtime

    def project_plans(self, project, root, sessions):
        """{rel path: plan record} for one project, the most advanced copy of each path chosen."""
        recs = {}
        for co in self.checkouts(root, sessions):
            leds = self.ledgers(co)
            for path, where in self.discover(co):
                parsed, st = self.parsed(path)
                if not is_plan(parsed, where):
                    continue
                rel = os.path.relpath(path, co)
                r = recs.get(rel)
                copy = {"abs": path, "checkout": co, "parsed": parsed, "mtime": st.st_mtime, "size": st.st_size}
                if r is None:
                    r = recs[rel] = {"rel": rel, "project": project, "root": root, "copies": [], "ledger": None}
                r["copies"].append(copy)
                if rel in leds and (r["ledger"] is None or os.path.getmtime(leds[rel]) > os.path.getmtime(r["ledger"])):
                    r["ledger"] = leds[rel]
        for r in recs.values():
            r["copies"].sort(key=lambda c: (c["parsed"]["steps_done"], c["mtime"]), reverse=True)
            best = r["copies"][0]
            r.update(abs=best["abs"], checkout=best["checkout"], parsed=best["parsed"], mtime=max(c["mtime"] for c in r["copies"]))
        return recs

    # evidence from transcripts -------------------------------------------------
    @staticmethod
    def _matcher(recs):
        abs_map, base_map = {}, {}
        for rel, r in recs.items():
            for c in r["copies"]:
                abs_map[os.path.normpath(c["abs"])] = rel
            base_map.setdefault(os.path.basename(rel), []).append(rel)
        stems = {os.path.splitext(os.path.basename(rel))[0]: rel for rel in recs}

        def match(p, cwd):
            if not p:
                return None
            q = os.path.expanduser(p)
            a = os.path.normpath(q if os.path.isabs(q) else os.path.join(cwd or "/", q))
            if a in abs_map:
                return abs_map[a]
            b = os.path.basename(a)
            cands = base_map.get(b)
            if cands:
                for rel in cands:
                    if a.endswith("/" + rel) or q == rel:
                        return rel
                if len(cands) == 1 and b.lower() not in GENERIC_NAMES:
                    return cands[0]
            if b == "progress.md" and "/sdd/" in a:
                return stems.get(os.path.basename(os.path.dirname(a)))
            return None
        return match, stems

    def _session_evidence(self, s, recs, plans_sig):
        """{rel: evidence} for one session; cached until its transcripts grow or the plan set changes."""
        accs = [(s.acc, None)] + [(x.acc, x) for x in s.subs]
        sig = (len(s.subs), tuple((len(getattr(a, "plan_sigs", ())), len(a.spawns), a.last_ts) for a, _ in accs),
               tuple(x.status for x in s.subs))
        c = self._ev.get((s.sid, plans_sig))
        if c and c[0] == sig:
            return c[1]
        match, stems = self._matcher(recs)
        refs = []          # (t, rel, acc index)
        ev = {}

        def E(rel):
            e = ev.get(rel)
            if e is None:
                e = ev[rel] = {"refs": [], "wrote": False, "ticks": [], "bash_ticks": [], "ledger": [], "todos": [],
                               "agents": [], "skill": False, "accs": set(), "commits": []}
            return e
        skills = []
        todo_sigs = []
        task_lists = {}
        for ai, (a, x) in enumerate(accs):
            cwd = a.cwd or s.acc.cwd or ""
            for g in getattr(a, "plan_sigs", ()):
                k, t = g["k"], g.get("t") or 0
                if k in ("read", "write", "edit", "multiedit"):
                    rel = match(g["p"], cwd)
                    if rel is None:
                        continue
                    e = E(rel)
                    if g["p"].endswith("progress.md") and "/sdd/" in g["p"]:
                        for tid in g.get("ledger", ()):
                            e["ledger"].append((t, tid, ai))
                        e["accs"].add(ai)
                        refs.append((t, rel, ai))
                        continue
                    e["refs"].append(t)
                    refs.append((t, rel, ai))
                    if k == "write":
                        e["wrote"] = True
                    if g.get("tick"):
                        e["ticks"].append((t, g["tick"], ai))
                        e["accs"].add(ai)
                elif k == "bash":
                    hit = set()
                    for tok in g.get("md", ()):
                        rel = match(tok, cwd)
                        if rel:
                            hit.add(rel)
                    for rel in hit:
                        E(rel)["refs"].append(t)
                        refs.append((t, rel, ai))
                    if g.get("ledger") or g.get("tick"):
                        tgt = set(hit)
                        if not tgt:
                            for stem, rel in stems.items():
                                if any(stem in tok for tok in g.get("md", ())):
                                    tgt.add(rel)
                        pending = {"t": t, "ai": ai, "g": g}
                        if tgt:
                            for rel in tgt:
                                self._bash_effect(E(rel), pending)
                        elif g.get("ledger") or not g.get("md"):
                            # a ledger line written through $W, or a tick through $P: the plan this transcript is on
                            todo_sigs.append(("bash", pending))
                    if g.get("commit"):
                        for rel in hit:
                            E(rel)["commits"].append(t)
                elif k == "skill":
                    name = g.get("skill") or ""
                    for tok in _MD_TOKEN.findall(g.get("args") or ""):
                        rel = match(tok, cwd)
                        if rel:
                            E(rel)["refs"].append(t)
                            refs.append((t, rel, ai))
                    if any(name.endswith(x) for x in EXEC_SKILLS):
                        skills.append((t, ai))
                elif k == "todo":
                    todo_sigs.append(("todo", {"t": t, "ai": ai, "items": g["todos"]}))
                elif k in ("tcreate", "tupdate"):
                    lst = task_lists.setdefault(ai, {"n": 0, "items": {}})
                    if k == "tcreate":
                        lst["n"] += 1
                        lst["items"][str(lst["n"])] = [g["subject"], "pending"]
                    else:
                        it = lst["items"].get(g["id"])
                        if it is None:
                            continue
                        if g["status"] == "deleted":
                            lst["items"].pop(g["id"], None)
                        else:
                            if g["status"]:
                                it[1] = g["status"]
                            if g["subject"]:
                                it[0] = g["subject"]
                    todo_sigs.append(("todo", {"t": t, "ai": ai, "items": [tuple(v) for v in lst["items"].values()]}))
        refs.sort()

        def owner(t, ai, need=None):
            """The plan this session was on at time t: the latest reference before it (this transcript wins a tie)."""
            before = [r for r in refs if r[0] <= t + 1 and (need is None or need(r[1]))]
            if before:
                return max(before, key=lambda r: (r[0], r[2] == ai))[1]
            after = [r for r in refs if need is None or need(r[1])]
            return after[0][1] if after else None
        has_task = lambda tid: (lambda rel: any(tt["id"] == tid for tt in recs[rel]["parsed"]["tasks"]))

        def task_owner(t, ai, tid, desc):
            """The plan a "Task N: <name>" spawn belongs to: the referenced plan whose Task N title shares the most
            words with <name>, else the latest one referenced before it."""
            words = _words(desc.split(":", 1)[1] if ":" in desc else "")
            best, score, cands = None, 0, 0
            if words:
                for rel in dict.fromkeys(r[1] for r in refs):
                    tt = next((x for x in recs[rel]["parsed"]["tasks"] if x["id"] == tid), None)
                    if tt is None:
                        continue
                    cands += 1
                    sc = len(words & _words(tt["title"]))
                    if sc > score:
                        best, score = rel, sc
            if best is None and len(words) >= 2 and cands:
                return None      # "Task 1: <a name no plan's Task 1 shares a word with>": the lead's own numbering
            return best or owner(t, ai, has_task(tid))
        for kind, p in todo_sigs:
            if kind == "bash":
                g = p["g"]
                need = None
                if g.get("ledger"):
                    need = has_task(g["ledger"][0])
                rel = owner(p["t"], p["ai"], need)
                if rel:
                    self._bash_effect(E(rel), p)
            else:
                rel = owner(p["t"], p["ai"])
                if rel:
                    E(rel)["todos"].append((p["t"], p["items"], p["ai"]))
        for t, ai in skills:
            rel = owner(t, ai)
            if rel:
                E(rel)["skill"] = True
                E(rel)["accs"].add(ai)
        # Agent spawns naming a task: one per subagent (or per spawn call when its transcript is missing)
        seen_spawns = set()
        acc_index = {id(a): i for i, (a, _) in enumerate(accs)}
        for x in s.subs:
            desc = x.meta.get("description") or (x.spawn or {}).get("description") or ""
            m = _TASK_REF.search(desc)
            if x.spawn:
                seen_spawns.add(x.spawn["id"])
            if not m:
                continue
            tid = m.group(1)
            pai = acc_index.get(id(x.parent_acc), 0)
            t0 = (x.spawn or {}).get("t") or x.acc.first_ts or x.mtime
            rel = task_owner(t0, pai, tid, desc)
            if not rel:
                continue
            mdl = x.acc.model_main() or (x.spawn or {}).get("resolved_model") or x.meta.get("model") or ""
            end = x.acc.last_ts if x.status in ("done", "failed", "stopped") else None
            E(rel)["agents"].append({"task": tid, "role": _role(desc), "agent_id": x.agent_id, "label": clip(desc, 200),
                                     "model": pricing.display_name(mdl) if mdl else "", "family": pricing.family(mdl) if mdl else "other", "start": t0, "end": end, "status": x.status, "parent": pai,
                                     "last_t": x.acc.last_ts or x.mtime,
                                     # working time, pauses over ACTIVE_GAP left out (a resumed subagent's transcript spans its idle hours)
                                     "active": round(x.acc.active_secs, 1)})
            E(rel)["accs"].add(pai)
        for ai, (a, _) in enumerate(accs):
            for sp in a.spawns:
                if sp["id"] in seen_spawns or sp["kind"] != "agent":
                    continue
                m = _TASK_REF.search(sp.get("description") or "")
                if not m:
                    continue
                rel = task_owner(sp["t"] or 0, ai, m.group(1), sp.get("description") or "")
                if not rel:
                    continue
                st = {"completed": "done", "error": "failed"}.get(sp["status"], "running" if sp["status"] in ("pending", "running") else sp["status"])
                E(rel)["agents"].append({"task": m.group(1), "role": _role(sp["description"]), "agent_id": sp.get("agent_id") or None,
                                         "label": sp["description"], "model": pricing.display_name(sp.get("resolved_model") or sp.get("model_requested") or ""),
                                         "family": pricing.family(sp.get("resolved_model") or sp.get("model_requested") or ""),
                                         "start": sp["t"], "end": None, "status": st, "parent": ai, "last_t": sp["t"]})
                E(rel)["accs"].add(ai)
        for e in ev.values():
            e["refs"].sort()
        if len(self._ev) > EV_MAX:
            self._ev.clear()
        self._ev[(s.sid, plans_sig)] = (sig, ev)
        return ev

    @staticmethod
    def _bash_effect(e, p):
        g = p["g"]
        for tid in g.get("ledger", ()):
            e["ledger"].append((p["t"], tid, p["ai"]))
        if g.get("tick"):
            e["bash_ticks"].append((p["t"], list(g.get("tick_tasks") or []), p["ai"]))
        e["accs"].add(p["ai"])

    # git -------------------------------------------------------------------------
    @staticmethod
    def _reflog_stamp(checkout):
        g = os.path.join(checkout, ".git")
        try:
            if os.path.isfile(g):
                line = read_text(g, 4096).strip()
                if line.startswith("gitdir:"):
                    gd = line[7:].strip()
                    g = gd if os.path.isabs(gd) else os.path.normpath(os.path.join(checkout, gd))
            return os.stat(os.path.join(g, "logs", "HEAD")).st_mtime_ns
        except OSError:
            return 0

    def git_log(self, checkout, rng=None, since=None, until=None, all_refs=False):
        """[{hash, short, t, subject}] newest first; read-only, bounded, cached by the reflog's mtime."""
        if not self.git_enabled or not checkout or not os.path.isdir(checkout):
            return []
        if rng is not None and not _HEX_RANGE.match(rng):
            return []
        key = (checkout, rng, int(since or 0) // 60, int(until or 0) // 60, all_refs, self._reflog_stamp(checkout))
        c = self._git.get(key)
        if c is not None:
            return c
        args = ["git", "--no-pager", "-C", checkout, "log", "--no-color", "-n", "300", "--format=%H%x1f%h%x1f%ct%x1f%s"]
        if rng:
            args.append(rng)
        else:
            args += ["--since=@%d" % int(since), "--until=@%d" % int(until)]
            if all_refs:
                args.append("--all")
        args.append("--")
        out = []
        try:
            r = subprocess.run(args, capture_output=True, timeout=GIT_TIMEOUT, text=True, errors="replace",
                               env=dict(os.environ, GIT_OPTIONAL_LOCKS="0", LC_ALL="C", GIT_TERMINAL_PROMPT="0"),
                               stdin=subprocess.DEVNULL)
            if r.returncode == 0:
                for line in r.stdout.splitlines():
                    p = line.split("\x1f")
                    if len(p) == 4:
                        out.append({"hash": p[0], "short": p[1], "t": float(p[2]), "subject": clip(p[3], 200)})
        except (OSError, subprocess.SubprocessError, ValueError):
            out = []
        if len(self._git) > 2000:
            self._git.clear()
        self._git[key] = out
        return out

    # assembling one plan -------------------------------------------------------------
    def build(self, r, sessions, alerts, now, detail=False):
        """The summary (and with detail=True the full record) of one plan record."""
        p = r["parsed"]
        tasks_src = p["tasks"]
        ledger, ledger_mtime = self.ledger(r["ledger"]) if r["ledger"] else ({}, None)
        sess_ev = []
        for s in sessions:
            e = self._ev_for(s, r)
            if e is not None:
                sess_ev.append((s, e))
        # executing: it ticked the plan, wrote its ledger, ran its tasks or kept a todo list for them; an
        # executing-plans / subagent-driven-development invocation alone counts only while the session is live
        executing = [(s, e) for s, e in sess_ev if e["ticks"] or e["bash_ticks"] or e["ledger"] or e["agents"]
                     or (e["skill"] and now - s.last_write < LIVE_SECS)
                     or (e["todos"] and (not tasks_src or _todos_match(e["todos"][-1][1], tasks_src)))]
        # per task state
        tasks = []
        by_id = {}
        for t in tasks_src:
            tt = {"id": t["id"], "title": t["title"], "pos": t["pos"], "line": t["line"], "milestone": t["milestone"],
                  "steps_total": len(t["steps"]), "steps_done": t["done_steps"], "agents": [], "commits": [],
                  "started": None, "finished": None, "finished_src": "", "done": False, "done_src": "",
                  "steps": t["steps"] if detail else None}
            if tt["steps_total"] and tt["steps_done"] == tt["steps_total"]:
                tt["done"], tt["done_src"] = True, "checkboxes"
            if t["id"] is not None and t["id"] in ledger:
                if not tt["done"]:
                    tt["done"], tt["done_src"] = True, "ledger"
                tt["ledger"] = ledger[t["id"]]
            tasks.append(tt)
            if t["id"] is not None:
                by_id.setdefault(t["id"], tt)
        # ticks: exact times; assign each ticked text to the first task (in order) with that unclaimed step
        claimed = set()
        step_owner = {}
        for ti_, t in enumerate(tasks_src):
            for si, st in enumerate(t["steps"]):
                step_owner.setdefault(norm(st["text"]), []).append((ti_, si))
        tick_times = {}       # (task index, step index) -> (t, session, acc)
        all_ticks = sorted(((t, texts, ai, s) for s, e in executing for (t, texts, ai) in e["ticks"]), key=lambda x: x[0])
        for (t, texts, ai, s) in all_ticks:       # oldest first: the first unclaimed matching step takes it
            for txt, tid in texts:
                cands = step_owner.get(norm(txt), [])
                if tid is not None:
                    cands = [c for c in cands if tasks_src[c[0]]["id"] == tid] or cands
                for c in cands:
                    if c not in claimed:
                        claimed.add(c)
                        tick_times[c] = (t, s, ai)
                        break
        for ti_, tt in enumerate(tasks):
            times = [tick_times[(ti_, si)][0] for si in range(tt["steps_total"]) if (ti_, si) in tick_times]
            if times:
                tt["started"] = min(times)
                if tt["done"] and (ti_, tt["steps_total"] - 1) in tick_times:
                    tt["finished"], tt["finished_src"] = max(times), "tick"
            if detail and tt["steps"]:
                tt["steps"] = [dict(st, t=tick_times[(ti_, si)][0] if (ti_, si) in tick_times else None) for si, st in enumerate(tt["steps"])]
        # agents per task
        sess_title = {}
        for s, e in executing:
            for ag in e["agents"]:
                tt = by_id.get(ag["task"])
                if tt is None:
                    continue
                a2 = dict(ag)
                a2["session"] = s.sid
                tt["agents"].append(a2)
            sess_title[s.sid] = s
        for tt in tasks:
            tt["agents"].sort(key=lambda a: a["start"] or 0)
            if tt["agents"]:
                st = min(a["start"] or now for a in tt["agents"])
                tt["started"] = min(tt["started"] or st, st)
        # ledger and Bash tick times
        led_t = {}
        for s, e in executing:
            for (t, tid, ai) in e["ledger"]:
                led_t.setdefault(tid, t)
        btick = sorted((t, tt_) for s, e in executing for (t, tt_, ai) in e["bash_ticks"])
        # todo lists (the newest snapshot of the session that last wrote one)
        todo_snaps = sorted(((t, items, s.sid) for s, e in executing for (t, items, ai) in e["todos"]), key=lambda x: x[0])
        todo_first = {}       # norm(content) -> {"in_progress": t, "completed": t}
        for t, items, sid in todo_snaps:
            for content, status in items:
                d = todo_first.setdefault(norm(content), {})
                d.setdefault(status, t)
        for tt in tasks:
            if not tt["done"] and tt["id"] is not None and tt["id"] in led_t and tt["id"] in ledger:
                tt["done"], tt["done_src"] = True, "ledger"
            todo = _todo_for(tt, todo_snaps[-1][1] if todo_snaps else [])
            if todo is not None:
                tt["todo"] = todo[1]
                tf = todo_first.get(norm(todo[0]), {})
                if not tt["started"] and (tf.get("in_progress") or tf.get("completed")):
                    tt["started"] = tf.get("in_progress") or tf.get("completed")
                if not tt["done"] and not tt["steps_total"] and todo[1] == "completed":
                    tt["done"], tt["done_src"] = True, "todo"
            # a plan nobody ticks (no checkbox ever ticked, no ledger): the task's last implementer finished
            # and nothing for it is still running
            impl = [a for a in tt["agents"] if a["role"] in ("implement", "fix")] or tt["agents"]
            if (not tt["done"] and not p["steps_done"] and not ledger and impl and impl[-1]["status"] == "done"
                    and not any(a["status"] == "running" for a in tt["agents"])):
                tt["done"], tt["done_src"] = True, "agent"
            if tt["done"] and not tt["finished"]:
                if tt["id"] is not None and tt["id"] in led_t:
                    tt["finished"], tt["finished_src"] = led_t[tt["id"]], "ledger"
                else:
                    ends = [a["end"] for a in tt["agents"] if a["end"]]
                    after = max(ends) if ends else (tt["started"] or 0)
                    bt = next((t for t, ids in btick if (tt["id"] in ids if ids else t >= after and after)), None)
                    if bt:
                        tt["finished"], tt["finished_src"] = bt, "bash tick"
                    elif ends and all(a["status"] in ("done", "failed", "stopped") for a in tt["agents"]):
                        tt["finished"], tt["finished_src"] = max(ends), "agent"
                    elif todo is not None and todo_first.get(norm(todo[0]), {}).get("completed"):
                        tt["finished"], tt["finished_src"] = todo_first[norm(todo[0])]["completed"], "todo"
        # todo-only plans: no tasks and no checkboxes, the executing session's todo list is the progress
        todo_mode = not tasks and bool(todo_snaps)
        if todo_mode:
            for n, (content, status) in enumerate(todo_snaps[-1][1]):
                tf = todo_first.get(norm(content), {})
                tasks.append({"id": None, "title": _clean(content), "pos": n + 1, "line": None, "milestone": None,
                              "steps_total": 0, "steps_done": 0, "agents": [], "commits": [], "todo": status,
                              "started": tf.get("in_progress") or tf.get("completed"),
                              "finished": tf.get("completed") if status == "completed" else None,
                              "finished_src": "todo" if status == "completed" else "",
                              "done": status == "completed", "done_src": "todo" if status == "completed" else "", "steps": [] if detail else None})
        # live agents and state
        running = []
        for tt in tasks:
            for a in tt["agents"]:
                if a["status"] == "running":
                    running.append((tt, a))
        n_tasks = len(tasks)
        n_done = sum(1 for t in tasks if t["done"])
        steps_total, steps_done = p["steps_total"], p["steps_done"]
        live_sessions = [s for s, e in executing if now - s.last_write < LIVE_SECS]
        # activity is what the executing sessions did; the file's mtime (git checkouts touch it) only stands
        # in when no session in the window executed the plan
        last_act = max([s.last_write for s, e in executing] + [t["finished"] or 0 for t in tasks]) if executing else r["mtime"]
        firsts = [min(e["refs"] + [a["start"] for a in e["agents"] if a["start"]] + [x[0] for x in e["ticks"]] or [s.acc.first_ts or now])
                  for s, e in executing]
        started = min([t["started"] for t in tasks if t["started"]] + firsts) if (firsts or any(t["started"] for t in tasks)) else None
        exec_sids = {s.sid for s, e in executing}
        block = None
        for al in alerts or ():
            if al.get("session") in exec_sids:
                block = {"session": al["session"], "agent_id": al.get("agent_id"), "kind": al.get("kind") or "",
                         "title": clip(al.get("title") or "", 120), "message": clip(al.get("message") or "", 200)}
                if al.get("kind") != "idle":
                    break
        all_done = n_tasks > 0 and n_done == n_tasks or (not n_tasks and steps_total and steps_done == steps_total)
        if all_done:
            status = "done"
        elif block is not None:
            status = "blocked"
        elif live_sessions:
            status = "live"
        elif executing and now - last_act < PAUSE_SECS:
            status = "active"
        elif n_done or steps_done or executing:
            status = "paused"
        else:
            status = "not_started"
        # current task: the earliest running task agent, else the first unfinished task
        cur = None
        if status != "done" and tasks:
            if running:
                tt, a = min(running, key=lambda x: x[0]["pos"])
            else:
                tt = next((t for t in tasks if not t["done"]), None)
                a = tt["agents"][-1] if tt and tt["agents"] else None
            if tt is not None:
                who = None
                if a is not None:
                    who = {"label": a["label"], "model": a["model"], "family": a.get("family") or "other", "running": a["status"] == "running", "since": a["start"],
                           "session": a["session"], "agent_id": a["agent_id"]}
                elif executing:
                    s0 = max(executing, key=lambda x: x[0].last_write)[0]
                    m = s0.acc.last_model or s0.acc.model_main()
                    who = {"label": "main thread", "model": pricing.display_name(m) if m else "", "family": pricing.family(m) if m else "other",
                           "running": s0 in live_sessions, "since": tt["started"],
                           "session": s0.sid, "agent_id": None}
                # later_done: tasks after this one already done (finished out of order), so the page can say
                # "first open task" instead of implying the plan is at its start
                cur = {"id": tt["id"], "title": tt["title"], "pos": tt["pos"], "who": who, "started": tt["started"],
                       "running": bool(running), "later_done": sum(1 for x in tasks if x["done"] and x["pos"] > tt["pos"])}
        # How long each finished task took, as working time: its agents' active time (pauses over 30 minutes
        # left out, the rule Sessions uses), else the wall clock when that has no such pause in it. A task
        # whose only measure is a wall clock with a long pause gets no duration, only its two times.
        for tt in tasks:
            wall = tt["finished"] - tt["started"] if tt["started"] and tt["finished"] and tt["finished"] >= tt["started"] else None
            acts = [a["active"] for a in tt["agents"] if a.get("active")]
            if acts:
                tt["active"] = round(min(sum(acts), wall) if wall is not None else sum(acts))
            else:
                tt["active"] = round(wall) if wall is not None and wall <= ACTIVE_GAP else None
            tt["wall"] = round(wall) if wall is not None else None
        # throughput and ETA: median working time of finished tasks
        durs = [t["active"] for t in tasks if t["done"] and t.get("active") is not None]
        median = statistics.median(durs) if len(durs) >= 2 else None
        eta = None
        if median is not None and status != "done" and n_tasks > n_done:
            left = median * (n_tasks - n_done)
            eta = {"secs_left": round(left), "at": (now + left) if status in ("live", "active", "blocked") else None,
                   "basis": len(durs)}
        # commits: ledger ranges first, then each task's time window in its checkout
        commits = []
        if detail:
            commits = self._commits(r, tasks, executing, sessions, now)
        exec_list = []
        for s, e in executing:
            # the transcript driving the plan: whoever spawned its task agents, else the main thread
            ais = sorted({a["parent"] for a in e["agents"]}) or [0]
            agent_ids = [None if ai == 0 else s.subs[ai - 1].agent_id for ai in ais if ai <= len(s.subs)]
            exec_list.append({"session": s.sid, "agents": agent_ids, "live": s in live_sessions,
                              "title": clip(_title(s), 160), "last_write": s.last_write})
        sessions_out = []
        for s, e in sess_ev:
            role = "executing" if s.sid in exec_sids else ("wrote" if e["wrote"] else "read")
            sessions_out.append({"id": s.sid, "role": role, "live": now - s.last_write < LIVE_SECS, "title": clip(_title(s), 160),
                                 "last_write": s.last_write, "first_ref": e["refs"][0] if e["refs"] else None})
        sessions_out.sort(key=lambda x: ({"executing": 0, "wrote": 1, "read": 2}[x["role"]], -x["last_write"]))
        # the bar measures steps while the plan's checkboxes are in use; a plan tracked elsewhere (ledger,
        # subagents, todos) with no box ticked is measured in tasks, or it would read 0% when it is done
        unit = "steps" if steps_total and (steps_done or not n_done) else "tasks"
        segs = _segments(p, tasks, todo_mode, unit == "steps")
        # The Docs view reads the project's main checkout (never .claude/worktrees), so "Open plan"
        # opens the same relative path there, when the main checkout has the file.
        rel_root = r["rel"] if os.path.isfile(os.path.join(r["root"], r["rel"])) else None
        src_tasks = sorted({t["done_src"] for t in tasks if t["done"]}) or (["todo list"] if todo_mode else [])
        out = {
            "key": r["rel"], "project": r["project"], "path": tilde(r["abs"]), "doc": rel_root, "title": p["title"],
            "goal": p["goal"], "mode": "todo" if todo_mode else p["mode"], "status": status, "live": bool(live_sessions),
            "blocked": block if status == "blocked" else None,
            "tasks": {"done": n_done, "total": n_tasks}, "steps": {"done": steps_done, "total": steps_total},
            "source": {"steps": "plan checkboxes" if steps_total else "", "tasks": ", ".join(_SRC_NAME.get(x, x) for x in src_tasks)},
            "current": cur, "segments": segs, "unit": unit, "started": started, "last_activity": last_act,
            "median_task_secs": round(median) if median is not None else None, "eta": eta,
            "executors": exec_list, "sessions": sessions_out[:30], "mtime": r["mtime"], "copies": len(r["copies"]),
            "ledger": tilde(r["ledger"]) if r["ledger"] else None,
        }
        spec = spec_path(p["spec"])
        info = self._spec_info(spec, (r["checkout"], r["root"])) if spec else None
        out.update(order=None, spec=spec, spec_title=info["title"] if info else "",
                   spec_status=info["status"] if info else "", spec_exists=info is not None)
        if detail:
            series = sorted(t["finished"] for t in tasks if t["done"] and t["finished"])
            out.update(task_list=[_task_out(t) for t in tasks], series=series, commits=commits, spec_text=p["spec"],
                       loose=p["loose"], copies_list=[{"path": tilde(c["abs"]), "steps_done": c["parsed"]["steps_done"],
                                                       "mtime": c["mtime"]} for c in r["copies"]],
                       done_untimed=sum(1 for t in tasks if t["done"] and not t["finished"]))
        return out

    def _ev_for(self, s, r):
        ev = self._cur_ev.get(s.sid) if hasattr(self, "_cur_ev") else None
        return ev.get(r["rel"]) if ev else None

    def _commits(self, r, tasks, executing, sessions, now):
        """Attach commits to tasks (in place) and return the plan's commit list, newest first."""
        cos = []
        for s, e in executing:
            co = self._checkout_of(s.acc.cwd or "", r["root"]) if s.acc.cwd else None
            if co and co not in cos:
                cos.append(co)
        if r["checkout"] not in cos:
            cos.append(r["checkout"])
        seen = {}
        for tt in tasks:
            led = tt.get("ledger") or {}
            rng = led.get("commits") if isinstance(led, dict) else None
            if rng:
                for co in cos:
                    got = self.git_log(co, rng="%s..%s" % rng)
                    if got:
                        for c in got:
                            if c["hash"] not in seen:
                                seen[c["hash"]] = dict(c, task=tt["pos"])
                        break
        # each task's windows: its agents' runs, else started..finished; an unfinished task without a
        # running agent gets no open-ended window
        windows = []
        for tt in tasks:
            spans = [(a["start"], a["end"] or (now if a["status"] == "running" else a.get("last_t") or a["start"]))
                     for a in tt["agents"] if a["start"]]
            if not spans and tt["started"] and tt["finished"]:
                spans = [(tt["started"], tt["finished"])]
            elif tt["started"] and tt["finished"]:
                spans.append((min(sp[0] for sp in spans), tt["finished"]))
            windows.extend((a, b, tt["pos"]) for a, b in spans if b >= a)
        if windows:
            lo = min(w[0] for w in windows) - 60
            hi = max(w[1] for w in windows) + 60
            for co in cos[:3]:
                for c in self.git_log(co, since=lo, until=hi):
                    if c["hash"] in seen:
                        continue
                    cand = [w for w in windows if w[0] - 30 <= c["t"] <= w[1] + 30]
                    if cand:
                        seen[c["hash"]] = dict(c, task=max(cand, key=lambda w: w[0])[2])
        by_pos = {tt["pos"]: tt for tt in tasks}
        for c in seen.values():
            tt = by_pos.get(c["task"])
            if tt is not None:
                tt["commits"].append(c)
        for tt in tasks:
            tt["commits"].sort(key=lambda c: c["t"])
        return sorted(seen.values(), key=lambda c: -c["t"])[:200]

    # payloads -------------------------------------------------------------------
    def _gather(self, project_filter, days=None, now=None):
        """[(project, root, sessions)] of a `days` window (None: the launch window), under the store's lock."""
        st = self.store
        groups = {}
        for s in st.window_sessions(days, now):
            if not s.root:
                continue
            if project_filter and s.label != project_filter:
                continue
            groups.setdefault((s.label, s.root), []).append(s)
        return [(lbl, root, sess) for (lbl, root), sess in sorted(groups.items())]

    def _evaluate(self, project_filter, alerts, detail_key=None, days=None):
        st = self.store
        days = st.clamp_days(days)
        st.ensure_window(days)        # before any lock: it may take the store's to merge a widening
        st.refresh()
        now = st.clock()
        out = []
        with self.lock, st.lock:
            groups = self._gather(project_filter, days, now)
            self._cur_ev = {}
            for label, root, sess in groups:
                recs = self.project_plans(label, root, sess)
                if not recs:
                    continue
                # Every copy's path: a window that holds more sessions can find more checkouts, and the
                # evidence matcher reads every copy, so one window's evidence never answers another's.
                psig = tuple(sorted((k, v["abs"], tuple(sorted(c["abs"] for c in v["copies"]))) for k, v in recs.items()))
                for s in sess:
                    ev = self._session_evidence(s, recs, psig)
                    if ev:
                        self._cur_ev.setdefault(s.sid, {}).update(ev)
                for rel, r in recs.items():
                    if detail_key is not None and rel != detail_key:
                        continue
                    linked = [s for s in sess if s.sid in self._cur_ev and rel in self._cur_ev[s.sid]]
                    out.append(self.build(r, linked, alerts, now, detail=detail_key is not None))
            self._cur_ev = {}
        return out, now

    def payload(self, project="", alerts=None, everything=False, days=None):
        project = (project or "")[:200]
        days = self.store.clamp_days(days)
        plans, now = self._evaluate(project, alerts, days=days)
        cutoff = now - days * 86400
        for p in plans:
            # git checkouts touch every file's mtime, so a recent mtime alone is not activity
            p["recent"] = bool(p["sessions"]) or p["status"] in ("live", "blocked", "active") \
                or (p["mtime"] >= cutoff and p["status"] != "not_started")
        rank = {"blocked": 0, "live": 1, "active": 2, "paused": 3, "not_started": 4, "done": 5}
        plans.sort(key=lambda p: (not p["recent"], rank[p["status"]] if p["status"] != "done" else 5, -(p["last_activity"] or 0)))
        shown = plans if everything else [p for p in plans if p["recent"]]
        chips = {}
        for p in plans:
            if p["status"] == "done" and now - (p["last_activity"] or 0) > LIVE_SECS:
                continue
            for ex in p["executors"]:
                chip = {"project": p["project"], "key": p["key"], "title": p["title"], "status": p["status"],
                        "pos": (p["current"] or {}).get("pos"), "done": p["tasks"]["done"], "total": p["tasks"]["total"],
                        "steps": p["steps"], "live": ex["live"], "unit": p["unit"],
                        "segments": [{"done": sg["done"], "total": sg["total"], "title": ""} for sg in p["segments"]]}
                for aid in ex["agents"]:
                    k = ex["session"] + ("/" + aid if aid else "")
                    old = chips.get(k)
                    if old is None or (chip["live"] and not old["live"]) or (p["status"] != "done" and old["status"] == "done"):
                        chips[k] = chip
        projects = {}
        for p in plans:
            d = projects.setdefault(p["project"], {"project": p["project"], "plans": 0, "active": 0, "recent": 0})
            d["plans"] += 1
            d["recent"] += p["recent"]
            d["active"] += p["status"] in ("live", "active", "blocked")
        try:
            orders, documents, board = self._work(project, plans, shown, days, now)
        except Exception:  # noqa: BLE001 - the plans list must survive a broken orders or documents read
            orders, documents, board = [], [], []
        return {"now": now, "days": days, "plans": shown, "older": len(plans) - len([p for p in plans if p["recent"]]),
                "total": len(plans), "chips": chips, "projects": sorted(projects.values(), key=lambda x: x["project"].lower()),
                "scope": {"project": project}, "pause_secs": PAUSE_SECS,
                "orders": orders, "documents": documents, "board": board}

    # the Work view: orders, documents and agent runs joined to the plans ----------------
    def _head(self, path):
        """head_meta of a file's first META_BYTES, cached by (size, mtime); None when unreadable."""
        try:
            st = os.stat(path, follow_symlinks=False)
        except OSError:
            return None
        c = self._meta.get(path)
        if c and c[0] == st.st_size and c[1] == st.st_mtime_ns:
            return c[2]
        try:
            meta = head_meta(read_text(path, META_BYTES))
        except OSError:
            return None
        if len(self._meta) >= MAX_META:
            self._meta.clear()
        self._meta[path] = (st.st_size, st.st_mtime_ns, meta)
        return meta

    def _spec_info(self, rel, bases):
        """head_meta of the spec `rel` (already through spec_path) in the first checkout holding it as a
        regular file whose real path stays inside that checkout; None when there is none. Bounded, never
        follows a symlink out."""
        for base in bases:
            if not base:
                continue
            real_base = os.path.realpath(base)
            real = os.path.realpath(os.path.join(real_base, rel))
            if not real.startswith(real_base.rstrip("/") + "/"):
                continue
            try:     # a symlink inside the checkout must not reach what the Docs viewer refuses (.factory-holdout, secrets)
                docs_mod.check_rel(os.path.relpath(real, real_base), ("doc",))
            except docs_mod.DocsError:
                continue
            meta = self._head(real)
            if meta is not None:
                return meta
        return None

    def _work(self, project, plans, shown, days, now):
        """(orders, documents, board) for Contract B, over the projects of the window."""
        from . import doctypes, graph   # graph imports this module; doctypes needs is_plan
        st = self.store
        with st.lock:
            groups = self._gather(project, days, now)
        fac = graph._factory()
        if self._docs is None:
            self._docs = docs_mod.Docs(st)
        orders, documents, board = [], [], []
        for label, root, _sess in groups:
            orders_p, documents_p, board_p = [], [], []   # one project's rows land only when its whole read worked
            try:
                mine = [p for p in plans if p["project"] == label]
                by_spec = {}
                for p in sorted(mine, key=lambda x: x["mtime"]):
                    if p["spec"]:
                        by_spec[p["spec"]] = p
                recs = {}
                if fac is not None:
                    try:
                        main = getattr(fac, "main_checkout", None)
                        for ev in fac.read_ledger(fac.ledger_path(main(root) if main else root)):
                            if ev.get("recommendation") in fac.RECOMMENDATIONS and isinstance(ev.get("order"), str):
                                recs[ev["order"]] = ev["recommendation"]
                    except Exception:  # noqa: BLE001
                        recs = {}
                for o in (graph._project_orders(st, fac, root, now) if fac is not None else []):
                    plan = by_spec.get(o["path"])
                    if plan is not None:
                        plan["order"] = o["id"]
                    lane = o["lane"] or {}
                    sts = [{"name": s["name"], "title": s["title"], "status": "pass" if s["status"] == "done" else s["status"]}
                           for s in o["stations"]]
                    orders_p.append({
                        "project": label, "id": o["id"], "title": o["title"], "status": o["status"], "priority": o["priority"],
                        "line": o["line"], "gate": o["gate"], "stations": sts,
                        "done": sum(1 for s in sts if s["status"] == "pass"), "total": len(sts), "rework": o["rework"],
                        "recommendation": recs.get(o["id"], ""), "branch": lane.get("branch", ""),
                        "worktree": lane.get("worktree", ""), "path": o["path"],
                        "plan": {"key": plan["key"], "path": plan["doc"] or "", "title": plan["title"], "tasks": plan["tasks"]} if plan else None,
                        "updated": o["updated"] or 0})
                # documents: the Docs tree minus what the Plans and Orders sections already show
                skip = {p["key"] for p in shown if p["project"] == label}
                skip |= {p["doc"] for p in shown if p["project"] == label and p["doc"]}
                tree = self._docs._tree(root)
                # Only docs/: the docs standard's home for what a project produces. The rest of the tree holds
                # scaffolding and source (factory/order-template.md, templates/, marketing/).
                cand = [f for f in tree["files"] if f["kind"] == "md" and f["section"] in ("docs", "plans") and f["path"] not in skip]
                cand.sort(key=lambda f: -f["mtime"])
                for f in cand[:MAX_DOCUMENTS]:
                    path = os.path.join(tree["real_root"], f["path"])
                    meta = self._head(path) or {"title": "", "status": "", "type": ""}
                    parsed = None
                    if f["size"] <= DOC_PARSE_BYTES:
                        parsed = self.parsed(path)[0]
                    dtype = meta["type"] or doctypes.classify(f["path"], parsed, meta["title"])
                    documents_p.append({"project": label, "path": f["path"], "type": dtype,
                                      "title": meta["title"] or clip(posixpath.splitext(posixpath.basename(f["path"]))[0], 200),
                                      "status": meta["status"], "updated": f["mtime"]})
                board_p.extend(self._board(label, root))
            except Exception:  # noqa: BLE001 - one broken project must not blank the others' rows
                continue
            orders += orders_p
            documents += documents_p
            board += board_p
        orders.sort(key=lambda o: -o["updated"])
        documents.sort(key=lambda d: -d["updated"])
        board.sort(key=lambda b: -b["updated"])
        return orders, documents[:MAX_DOCUMENTS], board[:MAX_BOARD]

    @staticmethod
    def _board(label, root):
        """[{project, run, label, path, head, updated}] from <root>/.cc-board/<run>/<label>/checkpoint.md;
        symlinks and odd names are skipped, `head` is the first non-empty line, redacted."""
        base = os.path.join(root, docs_mod.BOARD_DIR)
        out = []
        try:
            if not stat.S_ISDIR(os.lstat(base).st_mode):    # a .cc-board that is itself a symlink leads outside the root
                return out
            runs = [e for e in os.scandir(base) if e.is_dir(follow_symlinks=False) and _BOARD_NAME.match(e.name)]
        except OSError:
            return out
        for run in runs[:200]:
            try:
                labels = [e for e in os.scandir(run.path) if e.is_dir(follow_symlinks=False) and _BOARD_NAME.match(e.name)]
            except OSError:
                continue
            for lab in labels[:200]:
                cp = os.path.join(lab.path, "checkpoint.md")
                try:
                    st = os.stat(cp, follow_symlinks=False)
                    if not stat.S_ISREG(st.st_mode):
                        continue
                    text = read_text(cp, 8192)
                except OSError:
                    continue
                head = next((ln.strip() for ln in text.split("\n") if ln.strip()), "")
                out.append({"project": label, "run": run.name, "label": lab.name,
                            "path": "%s/%s/%s/checkpoint.md" % (docs_mod.BOARD_DIR, run.name, lab.name),
                            "head": _plain(head, 200), "updated": st.st_mtime})
        return out

    def resolve(self, project, path, days=None):
        """The plan key for (project, path) when it is safe, else None.

        project must be a resolved project label; path a relative path ending
        in .md, with no '..' segment, whose realpath lies inside one of the
        project's checkouts, under the size cap, and is a discovered plan."""
        if not isinstance(path, str) or not path or len(path) > 1024 or "\x00" in path or "\\" in path:
            return None
        if os.path.isabs(path) or not path.lower().endswith(".md"):
            return None
        if ".." in path.split("/"):
            return None
        st = self.store
        with st.lock:
            groups = self._gather(project, days)
        groups = [g for g in groups if g[0] == project]
        if not project or not groups:
            return None
        label, root, sess = groups[0]
        recs = self.project_plans(label, root, sess)
        allowed = [os.path.realpath(c) for c in self.checkouts(root, sess)]
        r = recs.get(path)
        if r is None:
            # the Docs-style path, relative to the project root (a copy in a worktree)
            r = next((rr for rr in recs.values() if any(os.path.relpath(c["abs"], root) == path for c in rr["copies"])), None)
        if r is None:
            return None
        for c in r["copies"]:
            real = os.path.realpath(c["abs"])
            if not any(real == a or real.startswith(a + "/") for a in allowed) or not real.lower().endswith(".md"):
                return None
            try:
                if os.path.getsize(real) > MAX_PLAN_BYTES:
                    return None
            except OSError:
                return None
        return r["rel"]

    def detail(self, project, path, alerts=None, days=None):
        days = self.store.clamp_days(days)
        self.store.ensure_window(days)    # resolve() reads the window's sessions
        key = self.resolve(project, path, days)
        if key is None:
            return None
        plans, now = self._evaluate(project, alerts, detail_key=key, days=days)
        if not plans:
            return None
        return {"now": now, "days": days, "plan": plans[0], "pause_secs": PAUSE_SECS}


_SRC_NAME = {"checkboxes": "plan checkboxes", "ledger": "SDD ledger", "todo": "todo list", "agent": "subagent finished"}


def _title(s):
    a = s.acc
    return a.custom_title or a.agent_name or a.ai_title or a.summary or a.first_prompt or s.sid


_STOP = {"the", "and", "for", "with", "task", "implement", "review", "fix", "round", "spec", "code", "quality", "from", "into"}


def _words(text):
    return {w for w in re.findall(r"[a-z0-9]{3,}", (text or "").lower()) if w not in _STOP}


def _role(desc):
    d = (desc or "").lower()
    if "review" in d:
        return "review"
    if "fix" in d:
        return "fix"
    if "implement" in d or "build" in d or "task" in d:
        return "implement"
    return "work"


def _todo_for(tt, items):
    """(content, status) of the todo item naming this task (by `Task N` or by title), else None."""
    if not items:
        return None
    for content, status in items:
        m = _TASK_REF.search(content)
        if m and tt["id"] is not None and m.group(1) == tt["id"]:
            return content, status
    nt = norm(tt["title"])
    if len(nt) >= 8:
        for content, status in items:
            nc = norm(content)
            if nt in nc or (len(nc) >= 8 and nc in nt):
                return content, status
    return None


def _todos_match(items, tasks):
    return any(_todo_for({"id": t["id"], "title": t["title"]}, items) for t in tasks)


def _segments(p, tasks, todo_mode, use_steps=True):
    """Progress bar segments: one per milestone (or per task), weighted by steps, filled by completion."""
    def unit(ts):
        tot = sum(t["steps_total"] for t in ts) if use_steps else 0
        if tot:
            return sum(t["steps_done"] for t in ts), tot
        return sum(1 for t in ts if t["done"]), len(ts)
    segs = []
    if p["milestones"] and not todo_mode:
        for m in p["milestones"]:
            ts = [tasks[i] for i in m["tasks"] if i < len(tasks)]
            d, tot = unit(ts)
            segs.append({"title": m["title"], "done": d, "total": tot, "tasks": len(ts), "tasks_done": sum(1 for t in ts if t["done"])})
    else:
        for t in tasks:
            d, tot = unit([t])
            segs.append({"title": t["title"] if t["id"] is None else "Task " + t["id"] + ": " + t["title"], "done": d, "total": tot,
                         "tasks": 1, "tasks_done": int(t["done"])})
    if len(segs) > MAX_SEGMENTS:
        d = sum(s["done"] for s in segs)
        tot = sum(s["total"] for s in segs)
        segs = [{"title": "All tasks", "done": d, "total": tot, "tasks": len(tasks), "tasks_done": sum(1 for t in tasks if t["done"])}]
    return segs


def _task_out(t):
    o = {k: t.get(k) for k in ("id", "title", "pos", "line", "milestone", "steps_total", "steps_done", "started", "finished",
                               "finished_src", "done", "done_src", "todo", "active", "wall")}
    o["agents"] = [{k: a.get(k) for k in ("session", "agent_id", "label", "role", "model", "family", "start", "end", "status", "active")} for a in t["agents"]]
    o["commits"] = [{k: c[k] for k in ("hash", "short", "t", "subject")} for c in t["commits"]]
    o["steps"] = t.get("steps") or []
    led = t.get("ledger")
    o["ledger"] = led["line"] if isinstance(led, dict) else None
    return o


_INSTANCES = {}


def for_store(store):
    """One Plans engine per store (its caches live as long as the server)."""
    p = _INSTANCES.get(id(store))
    if p is None or p.store is not store:
        p = _INSTANCES[id(store)] = Plans(store)
    return p
