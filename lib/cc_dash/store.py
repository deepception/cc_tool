"""Discovery, linkage and the JSON payloads behind the dashboard.

Layout read (never written) under ${CLAUDE_CONFIG_DIR:-~/.claude}/projects/:
  <encoded-cwd>/<session>.jsonl                          main transcript
  <encoded-cwd>/<session>/subagents/agent-<id>.jsonl     Agent/Task subagent
  <encoded-cwd>/<session>/subagents/agent-<id>.meta.json agentType, description, toolUseId, model
  <encoded-cwd>/<session>/subagents/workflows/<run>/agent-<id>.jsonl   Workflow agents
  <encoded-cwd>/<session>/subagents/workflows/<run>/journal.jsonl      started/result/failed per agent
  <encoded-cwd>/<session>/workflows/<run>.json           workflow run summary

A subagent links to the tool call that spawned it through meta.toolUseId (or
the agentId in that call's result); Workflow agents link through the run id
in the Workflow call's result. cc_tool guard decisions come from
<repo>/.git/cc_tool/activity.jsonl (see templates/hooks/cc_hooklib.py) and are
attributed to the session in that repo whose tool calls are closest in time.
"""
import bisect
import hashlib
import json
import math
import os
import threading
import time

from . import pricing
from . import routing
from . import tree as agent_tree
from .parser import Acc, FileCache, LineLog, clip, tilde

LIVE_SECS = 600          # a transcript written in the last 10 minutes is live
WORKING_SECS = 90        # ...and "working" if the last write is this recent
GUARD_MATCH_SECS = 120   # a guard event must sit this close to one of the session's tool calls
MIN_REFRESH = 1.0
DAYS_MAX = 30.0          # the widest window a request may ask for (or --days, when that is wider)
DAYS_OPTIONS = (1.0, 3.0, 7.0, 14.0, 30.0)   # the page's window picker, plus the launch value
# A widening parses without Store.lock but holds the interpreter (GIL) while it does, and a reader's
# refresh gives the GIL up on every stat: at the 5 ms switch interval each one would wait up to 5 ms
# to get it back. So the parse steps aside, every WIDEN_SLICE lines, while anyone holds or waits for
# Store.lock, for at most WIDEN_YIELD seconds at a time (so busy readers slow it down, never stop it).
WIDEN_SLICE = 64
WIDEN_YIELD = 0.5


class StoreLock:
    """Store.lock: a re-entrant lock that also counts the acquisitions holding or waiting for it
    (`wanted`), so a widening's parse can step aside for them (wait_idle). Used with `with`, or
    acquire(blocking, timeout) and release() as threading.RLock."""

    def __init__(self):
        self._lock = threading.RLock()
        self._cond = threading.Condition(threading.Lock())
        self.wanted = 0

    def acquire(self, blocking=True, timeout=-1):
        with self._cond:
            self.wanted += 1
        try:
            ok = self._lock.acquire(blocking, timeout)
        except BaseException:
            self._settle()
            raise
        if not ok:
            self._settle()
        return ok

    def release(self):
        self._lock.release()
        self._settle()

    def _settle(self):
        with self._cond:
            self.wanted -= 1
            if not self.wanted:
                self._cond.notify_all()

    __enter__ = acquire

    def __exit__(self, *exc):
        self.release()

    def wait_idle(self, timeout):
        """Block (without the GIL) until nobody holds or waits for the lock, at most `timeout`
        seconds; return at once on the thread that holds it."""
        if self._lock._is_owned():
            return
        with self._cond:
            self._cond.wait_for(lambda: not self.wanted, timeout)


class _YieldingCache(FileCache):
    """The widening's private FileCache: it parses in slices of WIDEN_SLICE lines and, between
    slices, waits while Store.lock is wanted, so the launch window's readers run at full speed."""

    def __init__(self, lock):
        super().__init__()
        self._store_lock = lock

    def _lines(self, acc, data):
        lines = data.split(b"\n")
        lk = self._store_lock
        for i in range(0, len(lines), WIDEN_SLICE):
            if lk.wanted:
                lk.wait_idle(WIDEN_YIELD)
            FileCache._lines(acc, b"\n".join(lines[i:i + WIDEN_SLICE]))

# Agent-level live state (the Live view's Agents board).
LINGER_SECS = 120        # a finished or failed agent stays on the board this long, then drops
STALL_TOOL_SECS = 660    # a tool call pending this long with no transcript write is "stalled?" (Bash caps at 10 min)
STALL_LONG_SECS = 1800   # ...for tools that are allowed to wait (Monitor, TaskOutput)
STALL_GEN_SECS = 420     # a model turn in flight this long with no write is "stalled?"
SHOW_STALLED_SECS = 1800 # a stalled or blocked-on-you agent leaves the board this long after its last write
SPARK_MINS = 15          # tool calls per minute over the last 15 minutes
SPARK_TOP = 3            # tools named per spark minute; the rest only count
SPARK_NAME = 40          # a named tool is clipped to this many characters
RATE_SECS = 60           # "calls/min": tool calls started in the trailing 60 seconds
BURN_SECS = 300          # burn rate over the last 5 minutes
FEED_MAX = 50            # entries in the live activity feed
_USER_WAIT = {"AskUserQuestion": ("question", "Answer a question"), "ExitPlanMode": ("plan", "Review the plan")}
_LONG_WAIT = {"Monitor", "TaskOutput"}
_TURN_END = {"end_turn", "stop_sequence", "refusal"}
_FINAL_TOOLS = {"StructuredOutput"}   # an SDK run ends on this call's result; nothing is generated after it
RUNNING = ("tool", "generating", "waiting")


def config_dir():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")


def project_of(cwd):
    """(project root, label, worktree name) from the path alone. Worktrees under .claude/worktrees/ fold into their repo.

    Store.refresh() refines this with resolve_projects(), which also folds git
    worktrees and sibling checkouts (<repo>-s4-wt/web) into their repo."""
    if not cwd:
        return "", "unknown", ""
    marker = "/.claude/worktrees/"
    if marker in cwd:
        root, wt = cwd.split(marker, 1)
        return root, os.path.basename(root) or root, wt.split("/")[0]
    return cwd, os.path.basename(cwd.rstrip("/")) or cwd, ""


def _is_repo(path):
    """A main git checkout: <path>/.git is a directory."""
    try:
        return os.path.isdir(os.path.join(path, ".git"))
    except (OSError, ValueError):
        return False


def _gitdir_parent(cwd):
    """For a linked git worktree, whose .git is a file reading
    'gitdir: <repo>/.git/worktrees/<name>', return <repo>; else None.
    Reads at most 4 KB of that one file and follows nothing."""
    g = os.path.join(cwd, ".git")
    try:
        if os.path.islink(g) or not os.path.isfile(g):
            return None
        with open(g, encoding="utf-8", errors="replace") as fh:
            line = fh.read(4096).strip()
    except (OSError, ValueError):
        return None
    if not line.startswith("gitdir:"):
        return None
    gd = line[len("gitdir:"):].strip()
    if not os.path.isabs(gd):
        gd = os.path.normpath(os.path.join(cwd, gd))
    marker = "/.git/worktrees/"
    if marker not in gd:
        return None                      # a submodule or something else: not a worktree
    return gd.split(marker, 1)[0] or None


def _sibling_repo(cwd, known):
    """A checkout made next to its repo (<parent>/<repo>-<suffix>[/<name>]) belongs to <repo>.

    Looks at the working directory and its parent: if a directory name starts
    with '<repo>-' and <parent>/<repo> is a main checkout (or a project root
    already resolved in this scan), returns (repo root, worktree label), e.g.
    ~/x/color-analyzer-s4-wt/web -> (~/x/color-analyzer, 's4-wt/web').
    The longest matching repo name wins."""
    a = cwd.rstrip("/")
    for _ in range(2):
        parent, base = os.path.split(a)
        if not parent or not base:
            break
        for i in reversed([i for i, ch in enumerate(base) if ch == "-" and i > 0]):
            name = base[:i]
            root = os.path.join(parent, name)
            if root in known or _is_repo(root):
                rel = os.path.relpath(cwd, parent)
                return root, rel[len(name) + 1:]
        a = parent
    return None


def resolve_projects(cwds):
    """({cwd: (root, worktree)}, {root: label}) for every working directory seen.

    Grouping rule, first match wins:
      1. <repo>/.claude/worktrees/<name>[/...]  -> repo, worktree <name>
      2. a main checkout (.git is a directory)  -> itself
      3. a linked worktree (.git is a file pointing at <repo>/.git/worktrees/..) -> repo
      4. a sibling checkout <repo>-<suffix>[/<name>] next to a repo -> repo,
         worktree '<suffix>[/<name>]'; this also catches worktrees deleted since
      5. anything else is its own project
    A label is the root's base name; roots sharing a base name get their
    parent directory prepended, so the filter list never merges two repos."""
    out = {}
    pending = []
    known = set()
    for cwd in cwds:
        if not cwd:
            out[cwd] = ("", "")
            continue
        root, _, wt = project_of(cwd)
        if wt:
            out[cwd] = (root, wt)
            known.add(root)
        elif _is_repo(cwd):
            out[cwd] = (cwd.rstrip("/") or cwd, "")
            known.add(out[cwd][0])
        else:
            parent = _gitdir_parent(cwd)
            if parent:
                out[cwd] = (parent, os.path.basename(cwd.rstrip("/")))
                known.add(parent)
            else:
                pending.append(cwd)
    for cwd in pending:
        out[cwd] = _sibling_repo(cwd, known) or (cwd.rstrip("/") or cwd, "")
    by_base = {}
    for root, _ in out.values():
        if root:
            by_base.setdefault(os.path.basename(root) or root, set()).add(root)
    labels = {"": "unknown"}
    for base, roots in by_base.items():
        for root in roots:
            labels[root] = base if len(roots) == 1 else os.path.basename(os.path.dirname(root)) + "/" + base
    return out, labels


def activity_logs(root):
    """Where cc_hooklib.state_dir() puts activity.jsonl for this project root."""
    if not root:
        return []
    git = os.path.join(root, ".git")
    if os.path.isdir(git):
        base, shared = os.path.join(git, "cc_tool"), False
    else:
        # A worktree: ${XDG_STATE_HOME:-~/.local/state}/cc_tool/repos/<tag> (cc_hooklib.state_dir).
        tag = hashlib.sha1(root.encode("utf-8", "replace")).hexdigest()[:12]
        base, shared = os.path.join(_state_base(), "repos", tag), True
    if shared and not _own_private(base, 0o077):     # cc_hooklib makes it 0700 and uses nothing looser
        return []
    out = []
    for name in ("activity.jsonl.1", "activity.jsonl"):
        p = os.path.join(base, name)
        if os.path.isfile(p) and (not shared or _own_private(p)):
            out.append(p)
    return out


def _state_base():
    """${XDG_STATE_HOME:-~/.local/state}/cc_tool, as cc_hooklib computes it."""
    base = os.environ.get("XDG_STATE_HOME") or ""
    if not os.path.isabs(base):
        base = os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "cc_tool")


def _own_private(path, bits=0o022):
    """True for a non-symlink owned by us without any of the mode `bits`
    (default: group/others cannot write). A worktree's log is read only when it
    and its state directory are provably ours."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if os.path.islink(path) or (hasattr(os, "getuid") and st.st_uid != os.getuid()):
        return False
    return not (st.st_mode & bits)


class Journal:
    """subagents/workflows/<run>/journal.jsonl: per-agent outcome."""

    def __init__(self):
        self.outcome = {}
        self.label = {}
        self.phase = {}
        self.bad_lines = 0

    def feed(self, rec):
        if not isinstance(rec, dict):
            return
        aid = rec.get("agentId")
        t = rec.get("type")
        if not aid:
            return
        if t == "started":
            self.outcome.setdefault(aid, "running")
            if rec.get("label"):
                self.label[aid] = clip(rec["label"], 200)
            if rec.get("phase"):
                self.phase[aid] = clip(rec["phase"], 80)
        elif t == "result":
            self.outcome[aid] = "done"
        elif t == "failed":
            self.outcome[aid] = "failed"


class Sub:
    __slots__ = ("path", "agent_id", "meta", "acc", "run_id", "mtime", "parent_tool", "parent_acc", "spawn", "status")

    def __init__(self, path, agent_id, meta, acc, run_id, mtime):
        self.path, self.agent_id, self.meta, self.acc = path, agent_id, meta, acc
        self.run_id, self.mtime = run_id, mtime
        self.parent_tool = None
        self.parent_acc = None      # the transcript (main or subagent) that made the spawn call
        self.spawn = None           # that spawn call's record
        self.status = ""


class Session:
    def __init__(self, sid, path, projdir, acc, mtime):
        self.sid, self.path, self.projdir, self.acc, self.mtime = sid, path, projdir, acc, mtime
        self.subs = []
        self.workflows = {}
        self.journals = {}
        self.guard = []             # guard events in the launch window (Store._guard_in for any other)
        self.guard_pairs = []       # (event index, event) this session wins among every held session
        self.root, self.label, self.worktree = project_of(acc.cwd)

    @property
    def last_write(self):
        return max([self.mtime] + [s.mtime for s in self.subs])


class Store:
    """Every session within `span` days, parsed once and kept current by refresh().

    Windows: --days is the launch window (`days`); `sessions` holds exactly the sessions in it,
    and the live board, alerts and control read only that. Each windowed payload takes its own
    `days` (clamp_days), so two pages on different windows are both right at once: a session is
    in a window when its main transcript was written inside it, and every per-record filter
    (hour buckets, timeline events, guard events, subagent starts) uses the same cutoff. The
    holding (`held`) covers `span` days: it starts at `days`, only grows (ensure_window, up to
    `days_max`), and is what refresh() scans, links, labels and evicts by.

    Locks: `lock` (StoreLock) guards the holding and every accumulator; `_widen_lock` only makes
    widenings one at a time. A widening parses holding only `_widen_lock`, takes `lock` just for
    the merge, and between slices of its parse steps aside while anyone wants `lock`.
    ensure_window() runs before `lock` (or any lock taken before it, as plans' and attention's
    are) is taken, never under it."""

    def __init__(self, root=None, days=7.0, project="", clock=time.time):
        self.root = root or config_dir()
        self.projects_dir = os.path.join(self.root, "projects")
        self.days = float(days)
        self.days_max = max(DAYS_MAX, self.days)
        self.days_options = sorted(d for d in set(DAYS_OPTIONS) | {self.days} if d <= self.days_max)
        self.span = self.days             # the widest window held so far: grows (ensure_window), never shrinks
        self.project = (project or "").lower()
        self.clock = clock
        self.cache = FileCache()
        self.side_cache = FileCache()     # journals and activity logs
        self._json_cache = {}             # path -> (mtime_ns, data) for meta and workflow json
        self._tree_cache = {}             # session id -> (key, tree.build() result)
        self._route_prof = {}             # (transcript path, role) -> (signature, routing.profile())
        self._route_cache = {}            # (project, days) -> (key, payload)
        self.sessions = {}                # the launch window (--days): live board, alerts, control
        self.held = {}                    # every session within `span` days; `sessions` is a subset
        self._guard_alt = []              # contested guard events: (index, event, overall winner, candidates)
        self.lock = StoreLock()
        self._widen_lock = threading.Lock()
        self._last = 0.0
        self.last_scan = {"secs": 0.0, "files": 0, "parsed": 0, "bytes": 0}
        self.last_widen = None

    # ── windows ───────────────────────────────────────────────────────────
    def clamp_days(self, raw=None):
        """A request's window in days: `days` when raw is absent, empty, not a number, not finite or
        not positive; at most `days_max`. Never raises: a bad value is coerced like other GET params."""
        if raw is None or isinstance(raw, bool):
            return self.days
        try:
            v = float(raw)
        except (TypeError, ValueError, OverflowError):
            return self.days
        if not math.isfinite(v) or v <= 0:
            return self.days
        return min(v, self.days_max)

    def window_sessions(self, days=None, now=None):
        """The held sessions inside a `days` window (None: the launch window), in scan order.
        Complete once ensure_window(days) has run; read it under `lock`."""
        cutoff = (self.clock() if now is None else now) - self.clamp_days(days) * 86400
        return [s for s in self.held.values() if s.mtime >= cutoff]

    def _window_get(self, sid, days, now):
        """One held session when it is inside the window, else None. `sid` is only a dict key."""
        s = self.held.get(sid) if isinstance(sid, str) else None
        return s if s is not None and s.mtime >= now - days * 86400 else None

    def ensure_window(self, days=None):
        """Hold every session of a `days` window; True when this call widened the holding.

        Returns at once when the span covers it (no lock taken). Otherwise one widening at a time
        (`_widen_lock`, never `lock`): the older transcripts are parsed into private caches with
        no lock held, then `lock` is held only to merge them and rescan. A caller asking for an
        even wider window waits for the running widening, then widens the rest."""
        d = self.clamp_days(days)
        if d <= self.span:
            return False
        with self._widen_lock:
            if d <= self.span:
                return False
            self._widen(d)
            return True

    def _widen(self, days):
        t0 = time.time()
        old = self.span
        cache, side, js, files, parsed = self._prescan(self.clock() - days * 86400)
        nbytes = cache.bytes_read + side.bytes_read
        t1 = time.time()
        with self.lock:
            for p, e in cache.entries.items():
                self.cache.entries.setdefault(p, e)
            for p, e in side.entries.items():
                self.side_cache.entries.setdefault(p, e)
            for p, v in js.items():
                self._json_cache.setdefault(p, v)
            self.cache.bytes_read += cache.bytes_read
            self.side_cache.bytes_read += side.bytes_read
            self.span = max(self.span, days)
            self.refresh(force=True)
            lock_secs = time.time() - t1
        self.last_widen = {"from": old, "to": self.span, "secs": round(time.time() - t0, 3), "files": files,
                           "parsed": parsed, "bytes": nbytes, "lock_secs": round(lock_secs, 3)}

    def _prescan(self, cutoff):
        """Parse what a window back to cutoff needs and the caches lack, into private caches, with no
        lock held: main transcripts, their subagents (workflow ones too), journals, meta and run json,
        and the guard logs of repos only those sessions worked in. (caches..., files, parsed)."""
        cache, side, js = _YieldingCache(self.lock), _YieldingCache(self.lock), {}
        have, have_side = self.cache.entries, self.side_cache.entries   # membership reads only
        files = parsed = 0
        cwds = set()
        for sid, (st, path, projdir) in self._find(cutoff).items():
            if path in have:
                continue
            files += 1
            acc, changed = cache.get(path, lambda p=path: Acc(p, "main"), st)
            parsed += changed
            if acc is None or not self._wanted(acc, projdir):
                continue
            n, p = self._load_subs(Session(sid, path, projdir, acc, st.st_mtime), set(), cache, side, js)
            files += n
            parsed += p
            if acc.cwd:
                cwds.add(acc.cwd)
        for cwd in cwds:                      # guard logs of repos that only the older sessions worked in
            for p in activity_logs(cwd):
                if p not in have_side:
                    side.get(p, LineLog)
        return cache, side, js, files, parsed

    # ── scanning ──────────────────────────────────────────────────────────
    def _find(self, cutoff):
        """{session id: (stat, path, project dir)} for main transcripts written at or after cutoff.
        Reads the file system only, so it may run without the lock."""
        found = {}
        try:
            projdirs = [e for e in os.scandir(self.projects_dir) if e.is_dir(follow_symlinks=False)]
        except OSError:
            projdirs = []
        for pd in projdirs:
            try:
                entries = list(os.scandir(pd.path))
            except OSError:
                continue
            for e in entries:
                if not e.name.endswith(".jsonl"):
                    continue
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                if st.st_mtime < cutoff:
                    continue
                sid = e.name[:-6]
                prev = found.get(sid)
                # The same session file copied into a second project dir is a copy: keep the newest.
                if prev is None or st.st_mtime > prev[0].st_mtime:
                    found[sid] = (st, e.path, pd.path)
        return found

    def _wanted(self, acc, projdir):
        """The --project filter: the working directory or the project dir's name contains the text."""
        return not self.project or self.project in (acc.cwd or "").lower() \
            or self.project in os.path.basename(projdir).lower()

    def refresh(self, force=False):
        with self.lock:
            now = self.clock()
            if not force and now - self._last < MIN_REFRESH:
                return
            t0 = time.time()
            b0 = self.cache.bytes_read + self.side_cache.bytes_read
            self._last = now
            cutoff = now - self.span * 86400
            launch = now - self.days * 86400
            seen_paths = set()
            nfiles = parsed = 0
            sessions = {}
            for sid, (st, path, projdir) in self._find(cutoff).items():
                nfiles += 1
                acc, changed = self.cache.get(path, lambda p=path: Acc(p, "main"), st)
                parsed += changed
                if acc is None:
                    continue
                seen_paths.add(path)
                if not self._wanted(acc, projdir):
                    continue
                s = Session(sid, path, projdir, acc, st.st_mtime)
                n, p = self._load_subs(s, seen_paths)
                nfiles += n
                parsed += p
                sessions[sid] = s
            # Drop cache entries for files that left the span.
            for p in list(self.cache.entries):
                if p not in seen_paths:
                    del self.cache.entries[p]
            self.held = sessions
            self.sessions = {sid: s for sid, s in sessions.items() if s.mtime >= launch}
            # Labels over everything held, so a project keeps its name in every window.
            where, labels = resolve_projects({s.acc.cwd or "" for s in sessions.values()})
            for s in sessions.values():
                s.root, s.worktree = where[s.acc.cwd or ""]
                s.label = labels.get(s.root) or "unknown"
            self._link()
            self._load_guard(cutoff, launch)
            self.last_scan = {"secs": round(time.time() - t0, 3), "files": nfiles, "parsed": parsed,
                              "bytes": self.cache.bytes_read + self.side_cache.bytes_read - b0}

    def _json(self, path, cache=None):
        cache = self._json_cache if cache is None else cache
        try:
            st = os.stat(path)
        except OSError:
            return None
        c = cache.get(path)
        if c and c[0] == st.st_mtime_ns:
            return c[1]
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = None
        cache[path] = (st.st_mtime_ns, data)
        return data

    def _load_subs(self, s, seen_paths, cache=None, side=None, js=None):
        """Subagents, journals and workflow runs of one session. `cache`, `side` and `js` stand in
        for the store's own caches: ensure_window parses into private ones with no lock held."""
        cache = self.cache if cache is None else cache
        side = self.side_cache if side is None else side
        base = os.path.join(s.projdir, s.sid)
        sdir = os.path.join(base, "subagents")
        nfiles = parsed = 0
        dirs = [(sdir, "")]
        wdir = os.path.join(sdir, "workflows")
        if os.path.isdir(wdir):
            try:
                for e in os.scandir(wdir):
                    if e.is_dir(follow_symlinks=False):
                        dirs.append((e.path, e.name))
            except OSError:
                pass
        for d, run_id in dirs:
            try:
                entries = list(os.scandir(d))
            except OSError:
                continue
            for e in entries:
                n = e.name
                if n == "journal.jsonl" and run_id:
                    j, _ = side.get(e.path, Journal)
                    if j is not None:
                        s.journals[run_id] = j
                    seen_paths.add(e.path)
                    continue
                if not (n.startswith("agent-") and n.endswith(".jsonl")):
                    continue
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                nfiles += 1
                acc, changed = cache.get(e.path, lambda p=e.path: Acc(p, "sub"), st)
                parsed += changed
                if acc is None:
                    continue
                seen_paths.add(e.path)
                aid = n[len("agent-"):-len(".jsonl")]
                meta = self._json(os.path.join(d, f"agent-{aid}.meta.json"), js) or {}
                s.subs.append(Sub(e.path, aid, meta if isinstance(meta, dict) else {}, acc, run_id, st.st_mtime))
        wf = os.path.join(base, "workflows")
        if os.path.isdir(wf):
            try:
                for e in os.scandir(wf):
                    if e.name.endswith(".json"):
                        data = self._json(e.path, js)
                        if isinstance(data, dict):
                            s.workflows[data.get("runId") or e.name[:-5]] = data
            except OSError:
                pass
        s.subs.sort(key=lambda x: (x.acc.first_ts or x.mtime))
        return nfiles, parsed

    def _link(self):
        now = self.clock()
        for s in self.held.values():
            by_tool = {}
            by_agent = {}
            by_run = {}
            owners = [s.acc] + [x.acc for x in s.subs]
            for a in owners:
                for sp in a.spawns:
                    by_tool[sp["id"]] = (a, sp)
                    if sp.get("agent_id"):
                        by_agent[sp["agent_id"]] = (a, sp)
                    if sp.get("run_id"):
                        by_run[sp["run_id"]] = (a, sp)
            for x in s.subs:
                tid = x.meta.get("toolUseId")
                hit = by_tool.get(tid) if tid else None
                hit = hit or by_agent.get(x.agent_id)
                if hit is None and x.run_id:
                    hit = by_run.get(x.run_id)
                x.parent_tool = hit[1]["id"] if hit else None
                x.parent_acc, x.spawn = (hit[0], hit[1]) if hit else (None, None)
                x.status = self._sub_status(s, x, hit[1] if hit else None, now)

    def _sub_status(self, s, x, sp, now):
        j = s.journals.get(x.run_id) if x.run_id else None
        if j is not None and j.outcome.get(x.agent_id) in ("done", "failed"):
            return j.outcome[x.agent_id]
        a = x.acc
        if a.handed_back:
            return "done"
        if sp is not None and sp.get("kind") == "agent":
            if sp["status"] == "completed":
                return "done"
            if sp["status"] == "error":
                return "failed"
        if a.last_stop == "end_turn" and not a.pending and a.last_role == "assistant":
            return "done"
        if now - x.mtime < LIVE_SECS:
            return "running"
        return "stopped"

    def _load_guard(self, cutoff, launch):
        """Give each guard event at or after cutoff (the span) to the held session in its repo whose
        tool calls sit closest, within GUARD_MATCH_SECS (a tie goes to the later session).

        A window must choose among its own sessions only, so an event that several sessions match
        keeps its candidates (_guard_alt), and _guard_in() hands it to the best one inside a window
        that leaves the overall winner out. s.guard is the launch window's list."""
        by_root = {}
        for s in self.held.values():
            s.guard, s.guard_pairs = [], []
            if s.acc.cwd:
                by_root.setdefault(s.acc.cwd, []).append(s)
        alt = []
        idx = 0
        for root, sess in by_root.items():
            events = []
            for p in activity_logs(root):
                log, _ = self.side_cache.get(p, LineLog)
                if log is not None:
                    events.extend(log.events)
            if not events:
                continue
            spans = []
            for s in sess:
                ts = sorted([t for a in [s.acc] + [x.acc for x in s.subs] for t in a.tool_ts])
                lo = min([a.first_ts for a in [s.acc] + [x.acc for x in s.subs] if a.first_ts] or [0])
                hi = max([a.last_ts for a in [s.acc] + [x.acc for x in s.subs] if a.last_ts] or [0])
                spans.append((s, ts, lo, hi))
            for ev in events:
                t = ev["t"]
                if t < cutoff:
                    continue
                idx += 1
                cands = []                    # (distance, -scan order, session): the smallest wins
                for order, (s, ts, lo, hi) in enumerate(spans):
                    if not (lo - GUARD_MATCH_SECS <= t <= hi + GUARD_MATCH_SECS):
                        continue
                    i = bisect.bisect_left(ts, t)       # ts[i - 1] < t <= ts[i]
                    d = t - ts[i - 1] if i else GUARD_MATCH_SECS + 1
                    if i < len(ts) and ts[i] - t < d:
                        d = ts[i] - t
                    if d <= GUARD_MATCH_SECS:
                        cands.append((d, -order, s))
                if not cands:
                    continue
                best = (min(cands, key=lambda c: c[:2]) if len(cands) > 1 else cands[0])[2]
                best.guard_pairs.append((idx, ev))
                if len(cands) > 1:
                    alt.append((idx, ev, best, cands))
        self._guard_alt = alt
        for s in self.held.values():
            s.guard_pairs.sort(key=lambda p: (p[1]["t"], p[0]))
        extras = self._guard_extras(launch)
        for s in self.sessions.values():
            s.guard = self._guard_in(s, launch, extras)

    def _guard_extras(self, cutoff):
        """{session id: [(index, event)]} for one window: contested events at or after cutoff whose
        overall winner is outside the window, each given to its best match inside. Under `lock`."""
        out = {}
        for idx, ev, best, cands in self._guard_alt:
            if ev["t"] < cutoff or best.mtime >= cutoff:
                continue
            inside = [c for c in cands if c[2].mtime >= cutoff]
            if inside:
                out.setdefault(min(inside, key=lambda c: c[:2])[2].sid, []).append((idx, ev))
        return out

    @staticmethod
    def _guard_in(s, cutoff, extras):
        """One session's guard events in a window (its cutoff and _guard_extras(cutoff)), oldest first."""
        pairs = [p for p in s.guard_pairs if p[1]["t"] >= cutoff]
        more = extras.get(s.sid)
        if more:
            pairs = sorted(pairs + more, key=lambda p: (p[1]["t"], p[0]))
        return [ev for _, ev in pairs]

    def _guards(self, sessions, cutoff):
        """{session id: guard events in the window} for the given sessions. Under `lock`."""
        extras = self._guard_extras(cutoff)
        return {s.sid: self._guard_in(s, cutoff, extras) for s in sessions}

    # ── shaping ───────────────────────────────────────────────────────────
    def _state(self, s, now):
        age = now - s.last_write
        if age >= LIVE_SECS:
            return ""
        a = s.acc
        running_subs = any(x.status == "running" for x in s.subs)
        if a.pending or running_subs:
            return "working"
        if a.last_role == "assistant" and a.last_stop == "end_turn":
            return "waiting"
        if age < WORKING_SECS:
            return "working"
        return "idle"

    @staticmethod
    def _usage_rows(accs):
        """Usage per model. "tokens" is new tokens (input + cache writes + output); cache_read stays apart,
        and output_estimated is the part of output that comes from the content estimate (parser.py)."""
        merged = {}
        est = {}
        for a in accs:
            for m, u in a.usage.items():
                r = merged.get(m)
                if r is None:
                    r = merged[m] = [0, 0, 0, 0, 0, 0, 0.0]
                for i in range(7):
                    r[i] += u[i]
            for m, (n, extra) in a.est_by_model().items():
                e = est.setdefault(m, [0, 0])
                e[0] += n
                e[1] += extra
        rows = []
        for m, u in merged.items():
            priced = pricing.has_price(m)
            e = est.get(m, [0, 0])
            rows.append({"model": m, "name": pricing.display_name(m), "family": pricing.family(m),
                         "msgs": u[0], "input": u[1], "output": u[2], "cache_read": u[3],
                         "cache_write_5m": u[4], "cache_write_1h": u[5],
                         "tokens": u[1] + u[2] + u[4] + u[5], "output_estimated": e[1], "est_msgs": e[0],
                         "cost": round(u[6], 6) if priced else None})
        rows.sort(key=lambda r: -r["tokens"])
        return rows

    @staticmethod
    def _tot(rows):
        cost = sum(r["cost"] or 0 for r in rows)
        return {"tokens": sum(r["tokens"] for r in rows), "cost": round(cost, 4),
                "unpriced_tokens": sum(r["tokens"] for r in rows if r["cost"] is None),
                "input": sum(r["input"] for r in rows), "output": sum(r["output"] for r in rows),
                "cache_read": sum(r["cache_read"] for r in rows),
                "cache_write": sum(r["cache_write_5m"] + r["cache_write_1h"] for r in rows),
                "output_estimated": sum(r["output_estimated"] for r in rows), "est_msgs": sum(r["est_msgs"] for r in rows)}

    def _title(self, a):
        return a.custom_title or a.agent_name or a.ai_title or a.summary or a.first_prompt or "(no prompt)"

    def _row(self, s, now, guard=None):
        """One session's row; `guard` is its guard events in the request's window (default: launch)."""
        a = s.acc
        accs = [a] + [x.acc for x in s.subs]
        usage = self._usage_rows(accs)
        tot = self._tot(usage)
        models = [r["name"] for r in usage if r["tokens"] > 0]
        start = min([x.first_ts for x in accs if x.first_ts] or [s.mtime])
        end = max([x.last_ts for x in accs if x.last_ts] or [s.mtime])
        guard = s.guard if guard is None else guard
        guard_kinds = {"deny": 0, "ask": 0, "warn": 0}
        for g in guard:
            guard_kinds[g["kind"]] += 1
        workflows = sum(1 for sp in a.spawns if sp["kind"] == "workflow")
        return {
            "id": s.sid, "project": s.label, "project_root": tilde(s.root), "worktree": s.worktree,
            "cwd": tilde(a.cwd), "branch": a.branch, "title": clip(self._title(a), 200),
            "first_prompt": a.first_prompt, "start": start, "end": end,
            "duration": max(0.0, end - start), "active": round(a.active_secs, 1), "last_write": s.last_write,
            "models": models, "model": pricing.display_name(a.model_main()) if a.models else "",
            "family": pricing.family(a.model_main()) if a.models else "other",
            "prompts": a.prompts, "api_msgs": sum(x.api_msgs for x in accs),
            "tool_calls": a.tool_calls, "tool_calls_all": sum(x.tool_calls for x in accs),
            "tool_errors": sum(x.tool_errors for x in accs), "api_errors": sum(x.api_errors for x in accs),
            "errors": sum(x.tool_errors + x.api_errors for x in accs),
            "blocked": sum(x.blocked for x in accs),
            "subagents": len(s.subs), "workflows": workflows,
            "tokens": tot["tokens"], "cost": tot["cost"], "unpriced_tokens": tot["unpriced_tokens"],
            "cache_read": tot["cache_read"], "input": tot["input"], "output": tot["output"], "cache_write": tot["cache_write"],
            "output_estimated": tot["output_estimated"], "est_msgs": tot["est_msgs"], "context": a.context,
            "guard": len(guard), "guard_kinds": guard_kinds,
            "state": self._state(s, now), "entrypoint": a.entrypoint, "version": a.version,
            "interrupts": a.interrupts, "compactions": a.compactions,
        }

    def _sub_row(self, s, x, now):
        a = x.acc
        usage = self._usage_rows([a])
        tot = self._tot(usage)
        m = a.model_main()
        j = s.journals.get(x.run_id) if x.run_id else None
        desc = x.meta.get("description") or (j.label.get(x.agent_id) if j else "") or a.first_prompt
        return {
            "agent_id": x.agent_id, "description": clip(desc, 200),
            "agent_type": x.meta.get("agentType") or ("workflow-subagent" if x.run_id else ""),
            "model": pricing.display_name(m) if m else "", "family": pricing.family(m) if m else "other",
            "model_requested": x.meta.get("model") or "", "phase": x.meta.get("workflowPhase") or "",
            "run_id": x.run_id, "parent_tool": x.parent_tool, "depth": x.meta.get("spawnDepth") or 1,
            "background": x.meta.get("requestShape") == "background",
            "start": a.first_ts, "end": a.last_ts,
            "duration": (a.last_ts - a.first_ts) if a.first_ts and a.last_ts else 0,
            "active": round(a.active_secs, 1),
            "tokens": tot["tokens"], "cost": tot["cost"], "unpriced_tokens": tot["unpriced_tokens"],
            "cache_read": tot["cache_read"], "input": tot["input"], "output": tot["output"], "cache_write": tot["cache_write"],
            "output_estimated": tot["output_estimated"], "est_msgs": tot["est_msgs"], "context": a.context,
            "tool_calls": a.tool_calls, "tool_errors": a.tool_errors, "blocked": a.blocked,
            "status": x.status, "last_tool": a.last_tool, "last_text": a.last_text,
            "age": now - x.mtime, "worktree": x.meta.get("worktreeBranch") or "",
        }

    def _tree(self, s, now):
        """Spawn calls in order, each carrying the subagents it started."""
        rows = {x.agent_id: self._sub_row(s, x, now) for x in s.subs}
        by_parent = {}
        for x in s.subs:
            by_parent.setdefault(x.parent_tool, []).append(rows[x.agent_id])

        def spawn_node(sp):
            node = dict(sp)
            node["children"] = []
            if sp["kind"] == "workflow":
                wf = s.workflows.get(sp.get("run_id")) or {}
                node["workflow"] = {
                    "name": wf.get("workflowName") or sp["description"], "status": wf.get("status") or sp["status"],
                    "duration_ms": wf.get("durationMs"), "agent_count": wf.get("agentCount"),
                    "total_tokens": wf.get("totalTokens"), "tool_calls": wf.get("totalToolCalls"),
                    "default_model": pricing.display_name(wf.get("defaultModel") or "") if wf.get("defaultModel") else "",
                    "phases": [p.get("title") for p in (wf.get("phases") or []) if isinstance(p, dict)],
                }
            for child in by_parent.get(sp["id"], []):
                c = dict(child)
                owner = next((x for x in s.subs if x.agent_id == child["agent_id"]), None)
                c["spawns"] = [spawn_node(sp2) for sp2 in owner.acc.spawns] if owner else []
                node["children"].append(c)
            return node

        tree = [spawn_node(sp) for sp in s.acc.spawns]
        all_spawn_ids = {sp["id"] for a in [s.acc] + [x.acc for x in s.subs] for sp in a.spawns}
        orphans = [r for pid, lst in by_parent.items() if pid is None or pid not in all_spawn_ids for r in lst]
        return tree, orphans

    @staticmethod
    def _window_tools(a, since):
        """Per-tool [calls, errors, dur_sum, dur_n] from timeline events at or after `since`."""
        out = {}
        for e in a.events:
            if e.get("k") != "tool" or (e.get("t") or 0) < since:
                continue
            r = out.setdefault(e["name"], [0, 0, 0.0, 0])
            r[0] += 1
            if e.get("status") in ("error", "blocked"):
                r[1] += 1
            if e.get("dur") is not None:
                r[2] += e["dur"]
                r[3] += 1
        return out

    @classmethod
    def _tools(cls, accs_main, accs_sub, since=None):
        out = {}
        for accs, col in ((accs_main, "calls"), (accs_sub, "sub_calls")):
            for a in accs:
                stats = a.tools if since is None or (a.first_ts or 0) >= since else cls._window_tools(a, since)
                for name, (calls, errs, dsum, dn) in stats.items():
                    r = out.get(name)
                    if r is None:
                        r = out[name] = {"name": name, "calls": 0, "sub_calls": 0, "errors": 0, "dur": 0.0, "dur_n": 0}
                    r[col] += calls
                    r["errors"] += errs
                    r["dur"] += dsum
                    r["dur_n"] += dn
        rows = []
        for r in out.values():
            r["avg_dur"] = round(r["dur"] / r["dur_n"], 2) if r["dur_n"] else None
            del r["dur"], r["dur_n"]
            rows.append(r)
        rows.sort(key=lambda r: -(r["calls"] + r["sub_calls"]))
        return rows

    # ── live agents ───────────────────────────────────────────────────────
    @staticmethod
    def _sub_outcome(s, x):
        """'done' or 'failed' when something authoritative says the subagent finished, else ''."""
        j = s.journals.get(x.run_id) if x.run_id else None
        if j is not None and j.outcome.get(x.agent_id) in ("done", "failed"):
            return j.outcome[x.agent_id]
        a = x.acc
        if a.handed_back:
            return "done"
        sp = x.spawn
        if sp is not None and sp.get("kind") == "agent":
            if sp["status"] == "completed":
                return "done"
            if sp["status"] == "error":
                return "failed"
        if not a.pending:
            if a.last_kind == "assistant" and a.last_msg_stop in _TURN_END:
                return "done"
            if a.last_kind == "interrupt":
                return "failed"
        return ""

    @staticmethod
    def _activity(a, mtime):
        """What one transcript is doing by itself: (state, since, tool, stall cap, needs).

        A pending tool_use is a running tool (a pending Agent/Task call means it
        waits on its subagent); a turn ended by end_turn or an interrupt is idle;
        anything else (a tool result, a prompt, a half-written message) means the
        model is generating the next step."""
        if a.pending:
            evs = list(a.pending.values())
            ev = max(evs, key=lambda e: e.get("t") or 0)
            t0 = ev.get("t") or mtime
            tool = {"name": ev["name"], "target": ev.get("target") or "", "t": t0, "n": len(evs)}
            if ev["name"] in _USER_WAIT:
                kind, title = _USER_WAIT[ev["name"]]
                return "tool", t0, tool, None, {"kind": kind, "title": title, "message": tool["target"], "source": "transcript"}
            if ev.get("spawn"):
                return "waiting", t0, tool, "subtree", None
            return "tool", t0, tool, STALL_LONG_SECS if ev["name"] in _LONG_WAIT else STALL_TOOL_SECS, None
        k = a.last_kind
        if (k == "interrupt" or (k == "assistant" and a.last_msg_stop in _TURN_END) or (not k and a.kind == "main")
                or (k == "tool_result" and a.last_tool and a.last_tool["name"] in _FINAL_TOOLS)):
            return "idle", a.last_ts or mtime, None, None, None
        return "generating", a.last_ts or mtime, None, STALL_GEN_SECS, None

    @staticmethod
    def _spark(a, now):
        """Per-minute tool-call buckets, oldest first, plus the current rate and the peak.

        Each bucket is {n, err, tools}: calls started in that clock minute, how
        many of them failed (an error result or a guard block), and the top
        SPARK_TOP tools by count (ties by name). The last bucket is the current,
        still-filling minute. rate counts the calls started in the last
        RATE_SECS, a trailing window, so it is current at every poll and never
        reads a partial minute as a drop; peak is the busiest bucket. Walks the
        timeline from the end and stops at the first event older than the
        window, so the cost follows the last 15 minutes, not the transcript."""
        end = (int(now // 60) + 1) * 60
        start = end - SPARK_MINS * 60
        n, err, tools = [0] * SPARK_MINS, [0] * SPARK_MINS, [{} for _ in range(SPARK_MINS)]
        rate = 0
        for e in reversed(a.events):   # record order, so near-sorted: stop at the first older event
            t = e.get("t")
            if not t:
                continue
            if t < start:
                break
            if e.get("k") != "tool":
                continue
            i = int((t - start) // 60)
            if not 0 <= i < SPARK_MINS:
                continue
            n[i] += 1
            if e.get("status") in ("error", "blocked"):
                err[i] += 1
            name = (e.get("name") or "?")[:SPARK_NAME]
            tools[i][name] = tools[i].get(name, 0) + 1
            if t >= now - RATE_SECS:
                rate += 1
        buckets = [{"n": n[i], "err": err[i],
                    "tools": dict(sorted(tools[i].items(), key=lambda kv: (-kv[1], kv[0]))[:SPARK_TOP])}
                   for i in range(SPARK_MINS)]
        return {"spark": buckets, "spark_end": end, "rate": rate, "peak": max(n)}

    @staticmethod
    def _burn(a, now):
        """Per-minute rates over the last BURN_SECS.

        cost_min is the estimated dollars per minute and is what the board
        shows: it prices cache reads at their own (low) rate. new_min counts
        new tokens only (input, cache writes and output), the fallback for an
        unpriced model. tok_min also counts cache reads, which re-count the
        whole context on every call, so it runs about 100x new_min; it is
        kept for the breakdown and never shown as the rate. unpriced_min is
        the part of new_min from messages with no price."""
        cut = now - BURN_SECS
        tok = new = out = unpriced = 0
        cost = 0.0
        for mid in reversed(a.msg_order):
            if a.msg_ts.get(mid, 0) < cut:
                break
            u = a.msg_usage.get(mid)
            if u:
                inp, o, _cread, cw5, cw1h = u[1]
                fresh = inp + o + cw5 + cw1h
                tok += sum(u[1])
                new += fresh
                out += o
                if u[2] is None:
                    unpriced += fresh
                else:
                    cost += u[2]
        span = max(60.0, min(float(BURN_SECS), now - (a.first_ts or now)))
        return {"tok_min": round(tok * 60 / span), "new_min": round(new * 60 / span), "out_min": round(out * 60 / span),
                "unpriced_min": round(unpriced * 60 / span), "cost_min": round(cost * 60 / span, 4)}

    def _session_agents(self, s, now):
        """(visible agent nodes in tree order, workflow headers); ([], []) when nothing is on the board."""
        main = {"key": s.sid, "acc": s.acc, "mtime": s.mtime, "x": None, "parent": None}
        key_of = {id(s.acc): s.sid}
        for x in s.subs:
            key_of[id(x.acc)] = s.sid + "/" + x.agent_id
        nodes = [main]
        for x in s.subs:
            parent = key_of.get(id(x.parent_acc)) if x.parent_acc is not None else None
            nodes.append({"key": key_of[id(x.acc)], "acc": x.acc, "mtime": x.mtime, "x": x,
                          "parent": parent if parent and parent != key_of[id(x.acc)] else s.sid})
        kids = {}
        for n in nodes[1:]:
            kids.setdefault(n["parent"], []).append(n)

        def resolve(n, path):
            if "state" in n:
                return n
            path = path | {n["key"]}
            ch = [resolve(c, path) for c in kids.get(n["key"], []) if c["key"] not in path]
            running = [c for c in ch if c["state"] in RUNNING]
            n["kids"] = ch
            n["latest"] = max([n["mtime"]] + [c["latest"] for c in ch])
            a = n["acc"]
            outcome = self._sub_outcome(s, n["x"]) if n["x"] is not None else ""
            if outcome:
                state, since, tool, cap, needs = outcome, a.last_ts or n["mtime"], None, None, None
            else:
                state, since, tool, cap, needs = self._activity(a, n["mtime"])
                if state == "idle" and n["x"] is not None:
                    state = "done"
                elif state == "idle" and running:
                    state = "waiting"
            stalled_for = None
            if state in RUNNING and cap:
                quiet = now - (n["latest"] if cap == "subtree" else n["mtime"])
                if quiet > (STALL_TOOL_SECS if cap == "subtree" else cap):
                    state, stalled_for = "stalled", round(quiet)
            if state in ("done", "failed"):
                vis = now - since < LINGER_SECS
            elif state == "idle":
                vis = now - n["mtime"] < LIVE_SECS
            elif state == "stalled" or needs:
                vis = now - n["latest"] < SHOW_STALLED_SECS
            else:
                vis = True
            n.update(state=state, since=since, tool=tool, needs=needs, stalled_for=stalled_for,
                     waiting_on=len(running), vis=vis, start=a.first_ts or n["mtime"])
            return n

        for n in nodes:              # the main thread first; anything its subtree missed gets a state too
            resolve(n, frozenset())
        if not any(n["vis"] for n in nodes):
            return [], []
        by_key = {n["key"]: n for n in nodes}
        for n in nodes:              # keep the tree connected: every ancestor of a visible agent shows too
            if n["vis"]:
                p, seen = n["parent"], set()
                while p and p not in seen:
                    seen.add(p)
                    by_key[p]["vis"] = True
                    p = by_key[p]["parent"]
        main["vis"] = True

        def order(ch):
            """Siblings by start time; a workflow's agents stay together, placed where its first agent started."""
            run_start = {}
            for c in ch:
                if c["x"].run_id:
                    run_start[c["x"].run_id] = min(run_start.get(c["x"].run_id, c["start"]), c["start"])
            return sorted(ch, key=lambda c: (run_start.get(c["x"].run_id, c["start"]), c["x"].run_id, c["start"]))

        out, wfs, placed = [], {}, set()

        def walk(n, depth):
            if n["key"] in placed:
                return
            placed.add(n["key"])
            n["depth"] = depth
            out.append(n)
            for c in order([c for c in n.get("kids", []) if c["vis"]]):
                r = c["x"].run_id
                if r and r not in wfs:
                    wfs[r] = {"run_id": r, "session": s.sid, "parent": n["key"], "depth": depth + 1,
                              "spawn": c["x"].spawn}
                walk(c, depth + 2 if r else depth + 1)

        walk(main, 0)
        for n in nodes:              # a visible agent the walk did not reach goes under the main thread
            if n["vis"] and n["key"] not in placed:
                walk(n, 1)
        heads = []
        for r, w in wfs.items():
            info = s.workflows.get(r) or {}
            j = s.journals.get(r)
            sp = w.pop("spawn") or {}
            mine = [n for n in out if n["x"] is not None and n["x"].run_id == r]
            w.update(name=info.get("workflowName") or sp.get("description") or r, status=info.get("status") or "",
                     phases=[p.get("title") for p in (info.get("phases") or []) if isinstance(p, dict) and p.get("title")],
                     total=info.get("agentCount") or (len(j.outcome) if j else len(mine)),
                     finished=sum(1 for o in (j.outcome.values() if j else []) if o in ("done", "failed")),
                     running=sum(1 for n in mine if n["state"] in RUNNING),
                     failed=sum(1 for o in (j.outcome.values() if j else []) if o == "failed"))
            heads.append(w)
        return out, heads

    def _agent_row(self, s, n, now):
        a, x = n["acc"], n["x"]
        m = a.last_model or a.model_main()
        tot = a.totals()
        if x is None:
            label, typ, kind, phase, bg = self._title(a), "main", "main", "", False
        else:
            j = s.journals.get(x.run_id) if x.run_id else None
            label = x.meta.get("description") or (j.label.get(x.agent_id) if j else "") or a.first_prompt or x.agent_id
            typ = x.meta.get("agentType") or ("workflow-subagent" if x.run_id else "subagent")
            kind = "workflow" if x.run_id else "sub"
            phase = x.meta.get("workflowPhase") or (j.phase.get(x.agent_id) if j else "") or ""
            bg = x.meta.get("requestShape") == "background" or bool(x.spawn and x.spawn.get("background"))
        # Effort on the board only when recorded or asked for: an inferred one would be noise on every row.
        effort, effort_src = a.effort or "", "recorded" if a.effort else ""
        if not effort and x is not None:
            req = (x.spawn or {}).get("effort_requested") or x.meta.get("effort")
            effort, effort_src = (req, "requested") if isinstance(req, str) and req else ("", "")
        return {
            "key": n["key"], "session": s.sid, "agent_id": x.agent_id if x is not None else None,
            "parent": n["parent"], "kind": kind, "depth": n["depth"], "run_id": x.run_id if x is not None else "",
            "phase": phase, "label": clip(label, 200), "type": typ, "background": bg, "project": s.label,
            "model": pricing.display_name(m) if m else "", "family": pricing.family(m) if m else "other",
            "effort": clip(effort, 20), "effort_source": effort_src, "state": n["state"], "since": n["since"], "tool": n["tool"], "needs": n["needs"],
            "waiting_on": n["waiting_on"], "stalled_for": n["stalled_for"],
            "start": n["start"], "last_write": n["mtime"], "age": now - n["mtime"],
            "tokens": tot["tokens"], "cache_read": tot["cache_read"], "output_estimated": tot["output_estimated"],
            "cost": round(tot["cost"], 4), "burn": self._burn(a, now),
            "tool_calls": a.tool_calls, "errors": a.tool_errors + a.api_errors,
            **self._spark(a, now), "last_tool": a.last_tool, "last_text": a.last_text,
        }

    @staticmethod
    def _feed(rows_accs, now):
        """The newest tool calls across the board's agents, newest first, at most FEED_MAX."""
        cut = now - SPARK_MINS * 60
        out = []
        for row, a in rows_accs:
            got = 0
            for e in reversed(a.events):
                t = e.get("t") or 0
                if t < cut or got >= FEED_MAX:
                    break
                if e.get("k") != "tool":
                    continue
                got += 1
                out.append({"t": t, "key": row["key"], "id": e.get("id") or "", "label": row["label"],
                            "kind": row["kind"], "project": row["project"], "family": row["family"],
                            "name": e["name"], "target": e.get("target") or "", "status": e.get("status") or "",
                            "dur": e.get("dur")})
        out.sort(key=lambda r: -r["t"])
        return out[:FEED_MAX]

    def _board(self, now):
        """Agents, their session groups, workflow headers, the activity feed and state counts."""
        agents, groups, workflows, accs = [], [], [], []
        counts = {"working": 0, "needs": 0, "idle": 0, "done": 0, "failed": 0, "stalled": 0}
        for s in self.sessions.values():
            if now - s.last_write >= SHOW_STALLED_SECS:
                continue
            nodes, heads = self._session_agents(s, now)
            if not nodes:
                continue
            rows = [self._agent_row(s, n, now) for n in nodes]
            for r, n in zip(rows, nodes):
                accs.append((r, n["acc"]))
                counts["needs" if r["needs"] else "working" if r["state"] in RUNNING else r["state"]] += 1
            all_tot = [x.totals() for x in [s.acc] + [y.acc for y in s.subs]]
            starts = [x.first_ts for x in [s.acc] + [y.acc for y in s.subs] if x.first_ts]
            groups.append({
                "session": s.sid, "project": s.label, "worktree": s.worktree, "branch": s.acc.branch,
                "cwd": tilde(s.acc.cwd), "title": clip(self._title(s.acc), 200),
                "start": min(starts or [s.mtime]), "last_write": max(r["last_write"] for r in rows),
                "cost": round(sum(t["cost"] for t in all_tot), 4), "tokens": sum(t["tokens"] for t in all_tot),
                "agents": len(rows), "running": sum(1 for r in rows if r["state"] in RUNNING),
                "needs": sum(1 for r in rows if r["needs"]),
            })
            agents.extend(rows)
            workflows.extend(heads)
        groups.sort(key=lambda g: (-g["needs"], -(g["running"] > 0), -g["last_write"]))
        return {"agents": agents, "groups": groups, "workflows": workflows,
                "feed": self._feed(accs, now), "counts": counts}

    # ── payloads ──────────────────────────────────────────────────────────
    def meta(self):
        self.refresh()
        return {"root": tilde(self.root), "days": self.days, "days_max": self.days_max,
                "days_options": list(self.days_options), "days_span": self.span, "widen": self.last_widen,
                "project": self.project, "now": self.clock(), "live_secs": LIVE_SECS, "pricing": pricing.table(),
                "scan": self.last_scan, "sessions": len(self.sessions)}

    def open_window(self, days):
        """(days, now, cutoff) for a windowed payload: clamped, held (ensure_window), refreshed.
        Called before `lock` is taken, never under it."""
        d = self.clamp_days(days)
        self.ensure_window(d)
        self.refresh()
        now = self.clock()
        return d, now, now - d * 86400

    def sessions_payload(self, days=None):
        d, now, cutoff = self.open_window(days)
        with self.lock:
            win = self.window_sessions(d, now)
            guards = self._guards(win, cutoff)
            rows = [self._row(s, now, guards[s.sid]) for s in win]
            projects = self._project_list(win)
        rows.sort(key=lambda r: -r["last_write"])
        return {"now": now, "days": d, "sessions": rows, "projects": projects, "scan": self.last_scan}

    @staticmethod
    def _project_list(sessions):
        """The filter list: one entry per project, its worktrees folded under it, sorted by name."""
        out = {}
        for s in sessions:
            p = out.get(s.label)
            if p is None:
                p = out[s.label] = {"project": s.label, "root": tilde(s.root), "sessions": 0, "main": 0,
                                    "last_active": 0.0, "worktrees": {}}
            p["sessions"] += 1
            p["last_active"] = max(p["last_active"], s.last_write)
            if s.worktree:
                p["worktrees"][s.worktree] = p["worktrees"].get(s.worktree, 0) + 1
            else:
                p["main"] += 1
        rows = []
        for p in out.values():
            p["worktrees"] = [{"name": k, "sessions": v} for k, v in sorted(p["worktrees"].items())]
            rows.append(p)
        rows.sort(key=lambda r: r["project"].lower())
        return rows

    def live_payload(self):
        self.refresh()
        now = self.clock()
        out = []
        with self.lock:
            for s in self.sessions.values():
                if now - s.last_write >= LIVE_SECS:
                    continue
                a = s.acc
                row = self._row(s, now)
                pend = None
                if a.pending:
                    ev = max(a.pending.values(), key=lambda e: e.get("t") or 0)
                    pend = {"name": ev["name"], "target": ev["target"], "t": ev["t"]}
                subs = [self._sub_row(s, x, now) for x in s.subs if x.status == "running"]
                recent = [e for e in a.events[-6:] if e["k"] in ("tool", "text", "prompt", "error", "command")]
                row.update({"last_model": pricing.display_name(a.last_model) if a.last_model else "",
                            "last_family": pricing.family(a.last_model) if a.last_model else "other",
                            "last_tool": a.last_tool, "last_text": a.last_text, "pending": pend,
                            "running_subs": subs, "recent": recent, "age": now - s.last_write})
                out.append(row)
            board = self._board(now)
        out.sort(key=lambda r: r["age"])
        return {"now": now, "live_secs": LIVE_SECS, "sessions": out, **board,
                "limits": {"linger": LINGER_SECS, "stall_tool": STALL_TOOL_SECS, "stall_gen": STALL_GEN_SECS,
                           "spark_mins": SPARK_MINS, "spark_top": SPARK_TOP, "rate_secs": RATE_SECS,
                           "feed_max": FEED_MAX}}

    def scope_sessions(self, project, days=None, now=None):
        """Sessions of a `days` window (None: the launch window) in one project (exact label
        match), or all of them for ''.

        `project` comes from a query string: it is compared with labels and
        never used as a path, so any value is safe and an unknown one just
        matches nothing."""
        win = self.window_sessions(days, now)
        if not project:
            return win
        return [s for s in win if s.label == project]

    @staticmethod
    def _blank_group(project, worktree):
        return {"project": project, "worktree": worktree, "sessions": 0, "live": 0, "active": 0.0,
                "cost": 0.0, "tokens": 0, "unpriced_tokens": 0, "tool_calls": 0, "tool_errors": 0,
                "subagents": 0, "prompts": 0, "guard": 0, "guard_kinds": {"deny": 0, "ask": 0, "warn": 0},
                "fam_tokens": {}, "cost_hours": {}, "last_active": 0.0}

    @staticmethod
    def _add_group(dst, src):
        for k in ("sessions", "live", "active", "cost", "tokens", "unpriced_tokens", "tool_calls",
                  "tool_errors", "subagents", "prompts", "guard"):
            dst[k] += src[k]
        for k, v in src["guard_kinds"].items():
            dst["guard_kinds"][k] += v
        for k, v in src["fam_tokens"].items():
            dst["fam_tokens"][k] = dst["fam_tokens"].get(k, 0) + v
        for k, v in src["cost_hours"].items():
            dst["cost_hours"][k] = dst["cost_hours"].get(k, 0.0) + v
        dst["last_active"] = max(dst["last_active"], src["last_active"])

    @staticmethod
    def _shape_group(g):
        g["cost"] = round(g["cost"], 4)
        g["active"] = round(g["active"], 1)
        g["cost_hours"] = [[h, round(c, 6)] for h, c in sorted(g["cost_hours"].items()) if c]
        g["error_rate"] = round(g["tool_errors"] / g["tool_calls"], 4) if g["tool_calls"] else 0.0
        return g

    def overview_payload(self, project="", days=None):
        project = (project or "")[:200]
        d, now, cutoff = self.open_window(days)
        hc = int(cutoff // 3600)
        hours = {}
        models = {}
        effort_msgs = {}                 # model -> {effort: messages sent with it}, from api-request records
        sub_mix = {}
        sub_types = {}
        groups = {}
        tot = {"sessions": 0, "live": 0, "cost": 0.0, "tokens": 0, "tool_calls": 0, "tool_errors": 0,
               "api_errors": 0, "subagents": 0, "prompts": 0, "unpriced_tokens": 0, "blocked": 0, "active": 0.0,
               "cache_read": 0}
        guard = {"deny": 0, "ask": 0, "warn": 0}
        tools_main, tools_sub = [], []
        with self.lock:
            win = self.window_sessions(d, now)
            project_list = [{"project": p["project"], "sessions": p["sessions"], "worktrees": len(p["worktrees"])}
                            for p in self._project_list(win)]
            scoped = win if not project else [s for s in win if s.label == project]
            guards = self._guards(scoped, cutoff)
            for s in scoped:
                g = self._blank_group(s.label, s.worktree)
                g["sessions"] = 1
                g["active"] = s.acc.active_secs
                g["last_active"] = s.last_write
                tot["sessions"] += 1
                if now - s.last_write < LIVE_SECS:
                    tot["live"] += 1
                    g["live"] = 1
                accs = [(s.acc, False)] + [(x.acc, True) for x in s.subs]
                win_subs = [x for x in s.subs if (x.acc.first_ts or x.mtime) >= cutoff]
                tools_main.append(s.acc)
                tools_sub.extend(x.acc for x in s.subs)
                tot["subagents"] += len(win_subs)
                g["subagents"] = len(win_subs)
                tot["prompts"] += s.acc.prompts
                g["prompts"] = s.acc.prompts
                for ev in guards[s.sid]:
                    guard[ev["kind"]] += 1
                    g["guard_kinds"][ev["kind"]] += 1
                g["guard"] = len(guards[s.sid])
                started = min([a.first_ts for a, _ in accs if a.first_ts] or [s.mtime])
                hs = int(started // 3600)
                if hs >= hc:
                    hours.setdefault(hs, {"c": {}, "tok": 0, "tools": 0, "err": 0, "msgs": 0, "sess": 0})["sess"] += 1
                for a, is_sub in accs:
                    # Everything below counts only what happened inside the window.
                    tot["blocked"] += a.blocked
                    tot["api_errors"] += a.api_errors
                    for h, b in a.hours.items():
                        if h < hc:
                            continue
                        dst = hours.setdefault(h, {"c": {}, "tok": 0, "tools": 0, "err": 0, "msgs": 0, "sess": 0})
                        for k in ("tok", "tools", "err", "msgs"):
                            dst[k] += b[k]
                        g["tool_calls"] += b["tools"]
                        for fam, c in b["c"].items():
                            dst["c"][fam] = dst["c"].get(fam, 0.0) + c
                            g["cost"] += c
                            g["cost_hours"][h] = g["cost_hours"].get(h, 0.0) + c
                        g["tokens"] += b["tok"]
                        tot["cache_read"] += b.get("cr", 0)
                        for m, per in b.get("e", {}).items():   # messages with a recorded effort
                            em = effort_msgs.setdefault(m, {})
                            for eff, cnt in per.items():
                                em[eff] = em.get(eff, 0) + cnt
                        for m, (tok, cost) in b.get("m", {}).items():
                            r = models.setdefault(m, {"model": m, "name": pricing.display_name(m),
                                                      "family": pricing.family(m), "main_tokens": 0, "sub_tokens": 0,
                                                      "cost": 0.0, "priced": pricing.has_price(m)})
                            r["sub_tokens" if is_sub else "main_tokens"] += tok
                            r["cost"] += cost
                            fam = pricing.family(m)
                            g["fam_tokens"][fam] = g["fam_tokens"].get(fam, 0) + tok
                            if not r["priced"]:
                                g["unpriced_tokens"] += tok
                    g["tool_errors"] += sum(1 for e in a.events if e.get("k") == "tool"
                                            and e.get("status") in ("error", "blocked") and (e.get("t") or 0) >= cutoff)
                for x in win_subs:
                    m = x.acc.model_main()
                    fam = pricing.family(m) if m else "other"
                    r = sub_mix.setdefault(fam, {"family": fam, "count": 0, "tokens": 0, "cost": 0.0, "models": {}})
                    r["count"] += 1
                    t = self._tot(self._usage_rows([x.acc]))
                    r["tokens"] += t["tokens"]
                    r["cost"] += t["cost"]
                    if m:
                        nm = pricing.display_name(m)
                        r["models"][nm] = r["models"].get(nm, 0) + 1
                    at = x.meta.get("agentType") or ("workflow-subagent" if x.run_id else "unknown")
                    st = sub_types.setdefault(at, {"type": at, "count": 0, "failed": 0})
                    st["count"] += 1
                    if x.status in ("failed", "stopped"):
                        st["failed"] += 1
                tot["cost"] += g["cost"]
                tot["tokens"] += g["tokens"]
                tot["tool_calls"] += g["tool_calls"]
                tot["tool_errors"] += g["tool_errors"]
                tot["active"] += g["active"]
                key = (s.label, s.worktree)
                if key in groups:
                    self._add_group(groups[key], g)
                else:
                    groups[key] = g
            tools = self._tools(tools_main, tools_sub, since=cutoff)
        projects = {}
        for (label, wt), g in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1])):
            p = projects.get(label)
            if p is None:
                p = projects[label] = self._blank_group(label, "")
                p["worktrees"] = []
            self._add_group(p, g)
            p["worktrees"].append(g)
        for p in projects.values():
            p["worktrees"] = [self._shape_group(g) for g in p["worktrees"]]
            if len(p["worktrees"]) == 1 and not p["worktrees"][0]["worktree"]:
                p["worktrees"] = []          # only the main checkout: nothing to fold
            self._shape_group(p)
        for r in models.values():
            r["effort"] = effort_msgs.get(r["model"], {})
            r["cost"] = round(r["cost"], 4) if r["priced"] else None
            if not r["priced"]:
                tot["unpriced_tokens"] += r["main_tokens"] + r["sub_tokens"]
        tot["cost"] = round(tot["cost"], 4)
        tot["active"] = round(tot["active"], 1)
        known = not project or any(p["project"] == project for p in project_list)
        return {
            "now": now, "days": d, "cutoff": cutoff, "totals": tot, "guard": guard,
            "scope": {"project": project, "known": known},
            "project_list": project_list,
            "hours": [{"h": h, **v} for h, v in sorted(hours.items())],
            "models": sorted(models.values(), key=lambda r: -(r["main_tokens"] + r["sub_tokens"])),
            "sub_mix": sorted(sub_mix.values(), key=lambda r: -r["count"]),
            "sub_types": sorted(sub_types.values(), key=lambda r: -r["count"]),
            "tools": tools[:14], "projects": sorted(projects.values(), key=lambda r: -r["cost"]),
        }

    def agent_tree(self, s, now):
        """tree.build() for one session, rebuilt only when a transcript, journal or run file changed
        (or every 10 s, since running/stopped depends on the clock)."""
        key = (s.last_write, len(s.subs), sum(len(j.outcome) for j in s.journals.values()),
               sum(1 for j in s.journals.values() for o in j.outcome.values() if o != "running"),
               len(s.workflows), int(now // 10))
        hit = self._tree_cache.get(s.sid)
        if hit and hit[0] == key:
            return hit[1]
        t = agent_tree.build(self, s, now)
        if len(self._tree_cache) > 64:
            self._tree_cache.clear()
        self._tree_cache[s.sid] = (key, t)
        return t

    def routing_payload(self, project="", days=None):
        """routing.build() for one scope and window, rebuilt when a transcript grew or every 15 s."""
        project = (project or "")[:200]
        d, now, _cutoff = self.open_window(days)
        with self.lock:
            key = (sum(s.acc.api_msgs + sum(x.acc.api_msgs for x in s.subs) for s in self.held.values()),
                   len(self.held), int(now // 15))
            hit = self._route_cache.get((project, d))
            if hit and hit[0] == key:
                return hit[1]
            out = routing.build(self, project, now, self._route_prof, d)
            if len(self._route_cache) > 32:
                self._route_cache.clear()
            self._route_cache[(project, d)] = (key, out)
            return out

    def tree_payload(self, sid, days=None):
        d, now, _cutoff = self.open_window(days)
        with self.lock:
            s = self._window_get(sid, d, now)
            if s is None:
                return None
            t = self.agent_tree(s, now)
            return {**t, "now": now, "days": d,
                    "effort": agent_tree.session_effort(s, t["nodes"][0] if t["nodes"] else None)}

    def session_payload(self, sid, days=None):
        d, now, cutoff = self.open_window(days)
        with self.lock:
            s = self._window_get(sid, d, now)
            if s is None:
                return None
            guard = self._guards([s], cutoff)[s.sid]
            row = self._row(s, now, guard)
            tree, orphans = self._tree(s, now)
            usage_main = self._usage_rows([s.acc])
            usage_sub = self._usage_rows([x.acc for x in s.subs])
            at = self.agent_tree(s, now)
            return {
                "now": now, "days": d, "session": row, "events": s.acc.events, "dropped_events": s.acc.dropped_events,
                "tree": tree, "orphans": orphans,
                "agents": {k: v for k, v in at["summary"].items() if k not in ("matrix", "thinking", "levels", "efforts")},
                "effort": agent_tree.session_effort(s, at["nodes"][0] if at["nodes"] else None),
                "tools": self._tools([s.acc], [x.acc for x in s.subs]),
                "usage": {"main": usage_main, "sub": usage_sub,
                          "all": self._usage_rows([s.acc] + [x.acc for x in s.subs])},
                "guard": guard, "cost_state": s.acc.cost_state,
                "bad_lines": s.acc.bad_lines + sum(x.acc.bad_lines for x in s.subs),
                "path": tilde(s.path),
            }

    def agent_payload(self, sid, agent_id, days=None):
        d, now, _cutoff = self.open_window(days)
        with self.lock:
            s = self._window_get(sid, d, now)
            if s is None:
                return None
            x = next((x for x in s.subs if x.agent_id == agent_id), None)
            if x is None:
                return None
            node = next((n for n in self.agent_tree(s, now)["nodes"] if n["id"] == "a:" + agent_id), None) or {}
            return {"now": now, "days": d, "session_id": sid, "session_title": clip(self._title(s.acc), 200),
                    "agent": self._sub_row(s, x, now), "events": x.acc.events,
                    "node": {k: node.get(k) for k in ("effort", "thinking", "sub", "level", "link", "fast_msgs", "models", "effort_log")},
                    "dropped_events": x.acc.dropped_events, "tools": self._tools([x.acc], []),
                    "usage": self._usage_rows([x.acc]), "bad_lines": x.acc.bad_lines,
                    "children": [self._sub_row(s, y, now) for y in s.subs
                                 if y.parent_tool and any(sp["id"] == y.parent_tool for sp in x.acc.spawns)],
                    "path": tilde(x.path)}
