"""Open "needs you" alerts behind GET /api/alerts.

Three sources, merged per session:

1. The attention hook's event log (source "hook"). templates/hooks/attention.py
   appends one line per Claude Code hook event to
   ${XDG_STATE_HOME:-~/.local/state}/cc_tool/attention.jsonl (rotated to .1 at
   5 MB). This module tails both files by inode and offset, so a poll reads only
   appended bytes and a rotation neither loses nor replays lines, and runs a
   state machine over the events:
     open     PermissionRequest (kind from the tool: AskUserQuestion is a
              "question", ExitPlanMode a "plan", anything else "permission"),
              Notification (permission_prompt when no hook alert covers it,
              idle_prompt, elicitation dialogs, agent_needs_input, ...),
              Elicitation, StopFailure ("error")
     resolve  PostToolUse/PostToolUseFailure of the same tool and target,
              PostToolBatch for the agent (every call in the batch was granted
              or denied), UserPromptSubmit and Stop for the main thread,
              SubagentStop for that subagent, ElicitationResult, SessionEnd.
   Hooks run async, so lines can land slightly out of order; every rule compares
   timestamps, and an open older than a resolve already seen for its scope is
   dropped.

2. Claude Code's session registry, ${CLAUDE_CONFIG_DIR:-~/.claude}/sessions/
   <pid>.json. Undocumented, so best effort only: an interactive session writes
   "status": "waiting" with "waitingFor": "permission prompt" while a dialog is
   up and flips back to "busy" the moment it is answered. That is the only
   signal for "permission granted, tool still running", which no hook event
   carries (PostToolUse fires when the tool finishes). It also tells a crashed
   session (registered pid gone) from a quiet one.

3. Transcripts (source "transcript") for sessions with no hook events, i.e.
   projects without cc_tool's hooks: an AskUserQuestion or ExitPlanMode
   tool_use with no tool_result yet, as the newest thing in its transcript, is a
   "question" or "plan"; a registry entry waiting on a permission prompt is a
   "permission". A pending tool call alone is never read as a permission
   prompt: a long-running tool looks the same.

Alerts expire when their session goes silent (no transcript write and no hook
event) for STALE[kind] seconds, or when the session's registered process is
gone.
"""
import hashlib
import json
import os
import stat
import threading
import time
from datetime import datetime, timezone

from .parser import clip, tilde
from .store import resolve_projects

LOG_NAME = "attention.jsonl"
MAX_LINE = 64 * 1024
CHUNK = 4 * 1024 * 1024
BLOCKING = ("permission", "question", "plan", "other")
STALE = {"permission": 8 * 3600, "question": 8 * 3600, "plan": 8 * 3600, "other": 8 * 3600,
         "error": 2 * 3600, "idle": 3600}
NOTIFY_GRACE = 20.0        # a permission_prompt notification this soon after a resolved prompt is late, not new
MEMO_SECS = 6 * 3600       # how long resolve marks are kept for out-of-order opens
REGISTRY_TTL = 1.0         # re-read the session registry at most this often
QUESTION_TOOLS = {"AskUserQuestion": "question", "ExitPlanMode": "plan"}
NOTE_KINDS = {               # Notification types that ask for the user, and how to word them
    "idle_prompt": ("idle", "Waiting for your input"),
    "elicitation_dialog": ("other", "An MCP server asks for input"),
    "elicitation_url_dialog": ("other", "An MCP server asks you to open a link"),
    "agent_needs_input": ("other", "A background agent needs your input"),
    "quota_auto_resume_stale": ("other", "Usage limit reset, press Enter to continue"),
    "quota_auto_resume_disabled": ("other", "Stopped waiting for the usage limit"),
}
NOTE_CLOSE = {"elicitation_complete", "elicitation_response"}


def state_dir():
    """Where templates/hooks/attention.py writes. Keep the two in step."""
    base = os.environ.get("XDG_STATE_HOME") or ""
    if not os.path.isabs(base):
        base = os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "cc_tool")


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _private_file(path):
    """lstat when path is a plain file we own that nobody else can write, else None."""
    try:
        st = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        return None
    if st.st_mode & 0o022:
        return None
    return st


def _private_dir(path):
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if not stat.S_ISDIR(st.st_mode):
        return False
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        return False
    return not (st.st_mode & 0o022)


class Tail:
    """Read appended lines from a rotating log, keyed by (dev, inode)."""

    def __init__(self, directory):
        self.dir = directory
        self.offsets = {}          # (dev, ino) -> bytes consumed
        self.bad_lines = 0
        self.bytes_read = 0

    def read(self):
        """New records, oldest file first."""
        out = []
        if not _private_dir(self.dir):
            return out
        live = set()
        for name in (LOG_NAME + ".1", LOG_NAME):
            path = os.path.join(self.dir, name)
            st = _private_file(path)
            if st is None:
                continue
            key = (st.st_dev, st.st_ino)
            live.add(key)
            off = self.offsets.get(key, 0)
            if st.st_size < off:
                off = 0            # truncated in place
            if st.st_size > off:
                self.offsets[key] = self._consume(path, key, off, st.st_size, out)
            else:
                self.offsets[key] = off
        for k in list(self.offsets):
            if k not in live:
                del self.offsets[k]
        return out

    def _consume(self, path, key, off, size, out):
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        except OSError:
            return off
        with os.fdopen(fd, "rb") as fh:
            st = os.fstat(fh.fileno())
            if (st.st_dev, st.st_ino) != key:
                return off         # swapped between lstat and open; next poll sees the new file
            fh.seek(off)
            carry = b""
            while off + len(carry) < size:
                chunk = fh.read(min(CHUNK, size - off - len(carry)))
                if not chunk:
                    break
                self.bytes_read += len(chunk)
                buf = carry + chunk
                cut = buf.rfind(b"\n")
                if cut < 0:
                    carry = buf
                    if len(carry) > MAX_LINE:     # a runaway line: skip it
                        off += len(carry)
                        carry = b""
                        self.bad_lines += 1
                    continue
                for line in buf[:cut].split(b"\n"):
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        self.bad_lines += 1
                        continue
                    if isinstance(rec, dict):
                        out.append(rec)
                off += cut + 1
                carry = buf[cut + 1:]
        return off


class Attention:
    def __init__(self, store, log_dir=None, registry_dir=None, clock=None):
        self.store = store
        self.clock = clock or getattr(store, "clock", None) or time.time
        self.tail = Tail(log_dir or state_dir())
        self.registry_dir = registry_dir or os.path.join(store.root, "sessions")
        self.lock = threading.Lock()
        self.open = {}             # key -> alert dict
        self.scope_reset = {}      # (session, agent) -> ts of the latest resolve-everything event
        self.session_end = {}      # session -> SessionEnd ts
        self.tool_done = {}        # (session, agent, tool, sig) -> ts the tool finished
        self.recent_closed = {}    # (session, agent) -> ts a blocking alert last closed
        self.hook_seen = {}        # session -> latest hook event ts (coverage and liveness)
        self.meta = {}             # session -> {"cwd", "transcript"} from hook events
        self._reg = {}
        self._reg_at = 0.0
        self._pruned = 0.0

    # ── hook events ────────────────────────────────────────────────────────
    def ingest(self, recs):
        recs.sort(key=lambda r: r.get("ts") if isinstance(r.get("ts"), (int, float)) else 0)
        for r in recs:
            try:
                self._apply(r)
            except Exception:  # noqa: BLE001 - one odd line must not stop the rest
                continue

    def _apply(self, r):
        ev = r.get("event")
        sid = r.get("session")
        ts = r.get("ts")
        if not isinstance(ev, str) or not isinstance(sid, str) or not sid or not isinstance(ts, (int, float)):
            return
        ts = float(ts)
        agent = r.get("agent") if isinstance(r.get("agent"), str) and r.get("agent") else None
        self.hook_seen[sid] = max(ts, self.hook_seen.get(sid, 0.0))
        m = self.meta.setdefault(sid, {"cwd": "", "transcript": ""})
        if isinstance(r.get("cwd"), str) and r["cwd"]:
            m["cwd"] = r["cwd"]
        if isinstance(r.get("transcript"), str) and r["transcript"]:
            m["transcript"] = r["transcript"]
        tool = r.get("tool") if isinstance(r.get("tool"), str) else ""
        sig = r.get("sig") if isinstance(r.get("sig"), str) else ""

        if ev == "PermissionRequest":
            kind = QUESTION_TOOLS.get(tool, "permission")
            self._open(("tool", sid, agent, tool, sig), r, ts, kind, tool or None, clip(r.get("message") or sig, 400),
                       sig=sig, title="")
        elif ev == "Notification":
            nt = r.get("ntype") or ""
            if nt == "permission_prompt":
                self._notify_permission(r, sid, agent, ts)
            elif nt in NOTE_KINDS:
                kind, title = NOTE_KINDS[nt]
                key = ("idle", sid, None) if kind == "idle" else ("note", sid, agent, nt)
                self._open(key, r, ts, kind, None, clip(r.get("message"), 400), title=title)
            elif nt in NOTE_CLOSE:
                self._close(lambda k, a: a["session"] == sid and k[0] in ("elicit", "note")
                            and (k[0] == "elicit" or k[3].startswith("elicitation")), ts)
        elif ev == "Elicitation":
            server = clip(r.get("server"), 80)
            self._open(("elicit", sid, agent, server), r, ts, "other", None, clip(r.get("message"), 400),
                       title=("MCP server " + server + " asks for input") if server else "An MCP server asks for input")
        elif ev == "ElicitationResult":
            server = clip(r.get("server"), 80)
            self._close(lambda k, a: a["session"] == sid and ((k[0] == "elicit" and k[3] == server)
                                                               or (k[0] == "note" and k[3].startswith("elicitation"))), ts)
        elif ev == "StopFailure":
            err = clip(r.get("error"), 60) or "unknown"
            self._open(("error", sid, None), r, ts, "error", None, clip(r.get("message"), 400),
                       title="Stopped on an API error (" + err.replace("_", " ") + ")")
        elif ev in ("PostToolUse", "PostToolUseFailure"):
            self.tool_done[(sid, agent, tool, sig)] = ts
            self._close(lambda k, a: a["session"] == sid and (
                (a["agent_id"] == agent and ((k[0] == "tool" and k[3] == tool and k[4] == sig) or k[0] == "notify"))
                or (agent is None and k[0] in ("idle", "error"))), ts)
        elif ev == "PostToolBatch":
            self._reset(sid, agent, ts)
        elif ev in ("UserPromptSubmit", "Stop"):
            self._reset(sid, None, ts)
        elif ev == "SubagentStop":
            if agent:
                self._reset(sid, agent, ts)
        elif ev == "SessionEnd":
            self.session_end[sid] = max(ts, self.session_end.get(sid, 0.0))
            self._close(lambda k, a: a["session"] == sid, ts)

    def _resolved_after(self, sid, agent, ts, tool=None, sig=None):
        """True when a resolve for this scope was already seen at or after ts."""
        if self.session_end.get(sid, -1.0) >= ts or self.scope_reset.get((sid, agent), -1.0) >= ts:
            return True
        return tool is not None and self.tool_done.get((sid, agent, tool, sig), -1.0) >= ts

    def _open(self, key, r, ts, kind, tool, message, sig=None, title=""):
        sid, agent = key[1], key[2]
        if self._resolved_after(sid, agent, ts, tool if key[0] == "tool" else None, sig):
            return
        a = self.open.get(key)
        if a is not None:
            a["since_ts"] = min(a["since_ts"], ts)
            if message and not a["message"]:
                a["message"] = message
            return
        self.open[key] = {
            "session": sid, "agent_id": agent, "agent_type": r.get("agent_type") or None,
            "kind": kind, "tool": tool, "title": title, "message": message or "",
            "since_ts": ts, "source": "hook", "mode": r.get("mode") or "",
            "server": r.get("server") or "", "notified": False,
        }

    def _notify_permission(self, r, sid, agent, ts):
        # PermissionRequest fires as the dialog opens; the notification ~6 s later.
        # It only adds an alert when nothing covers it, e.g. a sandbox network prompt.
        for k, a in self.open.items():
            if a["session"] == sid and a["kind"] in ("permission", "question", "plan"):
                a["notified"] = True
                return
        last = max([t for (s, _), t in self.recent_closed.items() if s == sid] or [-1.0])
        if ts - last < NOTIFY_GRACE:
            return
        self._open(("notify", sid, agent), r, ts, "permission", None, clip(r.get("message"), 400),
                   title=clip(r.get("title"), 120))

    def _close(self, pred, ts):
        for k in [k for k, a in self.open.items() if a["since_ts"] <= ts and pred(k, a)]:
            a = self.open.pop(k)
            if a["kind"] in BLOCKING:
                self.recent_closed[(a["session"], a["agent_id"])] = ts

    def _reset(self, sid, agent, ts):
        key = (sid, agent)
        self.scope_reset[key] = max(ts, self.scope_reset.get(key, 0.0))
        self._close(lambda k, a: a["session"] == sid and a["agent_id"] == agent, ts)

    def _prune(self, now):
        if now - self._pruned < 60:
            return
        self._pruned = now
        for d in (self.scope_reset, self.session_end, self.tool_done, self.recent_closed):
            for k in [k for k, t in d.items() if now - t > MEMO_SECS]:
                del d[k]
        for sid in [s for s, t in self.hook_seen.items() if now - t > 2 * 86400]:
            del self.hook_seen[sid]
            self.meta.pop(sid, None)

    # ── session registry (~/.claude/sessions/<pid>.json) ───────────────────
    def registry(self, now):
        if now - self._reg_at < REGISTRY_TTL:
            return self._reg
        self._reg_at = now
        out = {}
        try:
            dst = os.lstat(self.registry_dir)
            names = os.listdir(self.registry_dir) if _private_dir(self.registry_dir) else []
        except OSError:
            dst, names = None, []
        # Claude Code writes these files 0664 inside a 0700 directory: only the
        # directory's mode decides who can reach them. With a directory others
        # can enter, a file they can write is not trusted.
        closed = dst is not None and not (dst.st_mode & 0o011)
        for n in names:
            if not n.endswith(".json") or not n[:-5].isdigit():
                continue
            path = os.path.join(self.registry_dir, n)
            try:
                fst = os.lstat(path)
            except OSError:
                continue
            if not stat.S_ISREG(fst.st_mode) or (hasattr(os, "getuid") and fst.st_uid != os.getuid()):
                continue
            if (fst.st_mode & 0o022) and not closed:
                continue
            try:
                with open(path, encoding="utf-8") as fh:
                    d = json.load(fh)
            except (OSError, ValueError):
                continue
            if not isinstance(d, dict) or not isinstance(d.get("sessionId"), str):
                continue
            pid = d.get("pid")
            if not isinstance(pid, int) or pid <= 0:
                continue
            upd = d.get("statusUpdatedAt") or d.get("updatedAt") or 0
            rec = {"pid": pid, "alive": _alive(pid, d.get("procStart")),
                   "status": d.get("status") if isinstance(d.get("status"), str) else "",
                   "waiting_for": d.get("waitingFor") if isinstance(d.get("waitingFor"), str) else "",
                   "updated": (upd / 1000.0) if isinstance(upd, (int, float)) else 0.0,
                   "cwd": d.get("cwd") if isinstance(d.get("cwd"), str) else ""}
            prev = out.get(d["sessionId"])
            if prev is None or (rec["alive"], rec["updated"]) > (prev["alive"], prev["updated"]):
                out[d["sessionId"]] = rec
        self._reg = out
        return out

    # ── the payload ────────────────────────────────────────────────────────
    def payload(self, annotate=None):
        store = self.store
        store.refresh()
        with self.lock, store.lock:   # store.lock: a refresh on another thread mutates the transcript accumulators
            now = self.clock()
            self.ingest(self.tail.read())
            self._prune(now)
            reg = self.registry(now)
            sessions = store.sessions
            out = []
            for key, a in list(self.open.items()):
                s = sessions.get(a["session"])
                why = self._expired(a, s, reg.get(a["session"]), now)
                if why:
                    del self.open[key]
                    if why == "answered":
                        self.recent_closed[(a["session"], a["agent_id"])] = now
                    continue
                out.append(self._row(key, a, s, now, "hook"))
            hooked = set(self.hook_seen)
            covered = {a["session"] for a in out}
            for row in self._derived(sessions, reg, hooked | covered, now):
                out.append(row)
        if store.project:
            out = [r for r in out if store.project in (r.get("_cwd_raw") or "").lower()]
        if annotate is not None:
            annotate(out)          # control mode: which open request each alert is
        for r in out:
            r.pop("_cwd_raw", None)
            r.pop("_match", None)
        out.sort(key=lambda r: -r["since_ts"])
        return {"alerts": out, "now": iso(now), "now_ts": now,
                "log": {"dir": tilde(self.tail.dir), "bad_lines": self.tail.bad_lines}}

    def _activity(self, sid, s):
        t = self.hook_seen.get(sid, 0.0)
        if s is not None:
            t = max(t, s.last_write)
        return t

    def _expired(self, a, s, r, now):
        if r is not None and not r["alive"]:
            return "process gone"
        if now - self._activity(a["session"], s) > STALE.get(a["kind"], STALE["other"]):
            return "stale"
        if r is None or a["kind"] not in ("permission", "question", "plan"):
            return ""
        # The registry is session-wide, so it only closes an alert after this
        # dashboard saw the session waiting on it: the dialog was up, now it is not.
        if r["status"] == "waiting" and r["updated"] >= a["since_ts"] - 2:
            a["seen_waiting"] = True
        elif a.get("seen_waiting") and r["status"] and r["updated"] > a["since_ts"]:
            return "answered"      # the user answered; the tool may still be running
        return ""

    def _row(self, key, a, s, now, source):
        sid = a["session"]
        cwd = (s.acc.cwd if s is not None and s.acc.cwd else "") or self.meta.get(sid, {}).get("cwd", "")
        label, worktree, title = "unknown", "", ""
        if s is None and cwd:
            # No transcript yet: label it the way the board does (worktrees fold into their repo).
            where, labels = resolve_projects({cwd} | {x.acc.cwd for x in self.store.sessions.values() if x.acc.cwd})
            root, worktree = where.get(cwd, ("", ""))
            label = labels.get(root) or _project_label(cwd)
        if s is not None:
            label, worktree, title = s.label, s.worktree, _session_title(s.acc)
        raw = "|".join([sid, str(a["agent_id"]), "|".join(str(x) for x in key[3:]), a["kind"], "%.3f" % a["since_ts"]])
        return {
            "id": ("h-" if source == "hook" else "t-") + hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16],
            "session": sid, "agent_id": a["agent_id"], "project": label, "cwd": tilde(cwd),
            "kind": a["kind"], "title": a["title"] or _title(a), "message": a["message"],
            "tool": a["tool"], "since": iso(a["since_ts"]), "source": source,
            "since_ts": a["since_ts"], "age": max(0.0, now - a["since_ts"]),
            "agent_type": a.get("agent_type"), "agent_label": _agent_label(s, a["agent_id"], a.get("agent_type")),
            "session_title": title, "worktree": worktree, "notified": a.get("notified", False),
            "_cwd_raw": cwd, "_match": (sid, a["agent_id"], key[3] or None, key[4]) if key[0] == "tool" and source == "hook" else None,
        }

    def _derived(self, sessions, reg, skip, now):
        """Transcript and registry alerts for sessions the hook does not cover."""
        rows = []
        for sid, s in sessions.items():
            if sid in skip:
                continue
            r = reg.get(sid)
            if r is not None and not r["alive"]:
                continue
            found = []
            for acc, agent in [(s.acc, None)] + [(x.acc, x.agent_id) for x in s.subs]:
                ev = _waiting_tool(acc)
                if ev is not None and ev["name"] in QUESTION_TOOLS:
                    found.append((ev, agent))
            for ev, agent in found:
                since = ev.get("t") or s.last_write
                kind = QUESTION_TOOLS[ev["name"]]
                if now - max(since, s.last_write) > STALE[kind]:
                    continue
                if r is not None and r["status"] and r["status"] != "waiting" and r["updated"] > since + 1:
                    continue       # answered; the tool_result is not on disk yet
                a = {"session": sid, "agent_id": agent, "agent_type": None, "kind": kind,
                     "tool": ev["name"], "title": "", "message": ev.get("target") or "", "since_ts": since,
                     "notified": False}
                rows.append(self._row(("t", sid, agent, ev.get("id") or ""), a, s, now, "transcript"))
            if found or r is None or r["status"] != "waiting" or "permission" not in r["waiting_for"]:
                continue
            since = r["updated"] or s.last_write
            if now - max(since, s.last_write) > STALE["permission"]:
                continue
            pend, agent = None, None
            for acc, aid in [(s.acc, None)] + [(x.acc, x.agent_id) for x in s.subs]:
                for e in acc.pending.values():
                    if pend is None or (e.get("t") or 0) > (pend.get("t") or 0):
                        pend, agent = e, aid
            a = {"session": sid, "agent_id": agent, "agent_type": None, "kind": "permission",
                 "tool": pend["name"] if pend else None, "title": "",
                 "message": (pend.get("target") if pend else "") or r["waiting_for"], "since_ts": since,
                 "notified": False}
            rows.append(self._row(("reg", sid, agent, "%.3f" % since), a, s, now, "transcript"))
        return rows


def _alive(pid, proc_start):
    """Is pid running, and is it the same process (start time matches)?"""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    except OSError:
        return False
    if proc_start is None:
        return True
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii", errors="replace") as fh:
            fields = fh.read().rsplit(")", 1)[1].split()
        return str(fields[19]) == str(proc_start)
    except (OSError, IndexError):
        return True                # no /proc (macOS): trust kill(0)


def _waiting_tool(acc):
    """The pending tool call that is the newest thing in the transcript, else None."""
    if not acc.pending:
        return None
    ev = max(acc.pending.values(), key=lambda e: e.get("t") or 0)
    for later in reversed(acc.events):
        if later is ev:
            return ev
        if later.get("k") != "tool" or later.get("status") != "pending":
            return None            # something happened after it: not waiting on it
    return None


def _project_label(cwd):
    if not cwd:
        return "unknown"
    marker = "/.claude/worktrees/"
    root = cwd.split(marker, 1)[0] if marker in cwd else cwd
    return os.path.basename(root.rstrip("/")) or root


def _session_title(a):
    return clip(a.custom_title or a.agent_name or a.ai_title or a.summary or a.first_prompt or "", 160)


def _agent_label(s, agent_id, agent_type):
    if not agent_id:
        return "main"
    if s is not None:
        for x in s.subs:
            if x.agent_id == agent_id:
                return clip(x.meta.get("description") or x.meta.get("agentType") or agent_type or "subagent", 80)
    return clip(agent_type or "subagent", 80)


def _title(a):
    kind, tool = a["kind"], a.get("tool")
    if kind == "permission":
        if tool == "Bash":
            return "Wants to run a command"
        if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
            return "Wants to edit a file"
        if tool in ("WebFetch", "WebSearch"):
            return "Wants to go online"
        return ("Wants to use " + tool) if tool else "Needs your permission"
    if kind == "question":
        return "Has a question for you"
    if kind == "plan":
        return "Plan ready for your approval"
    if kind == "idle":
        return "Waiting for your input"
    if kind == "error":
        return "Stopped on an API error"
    return "Needs your attention"


def for_store(store):
    """One Attention per Store, created on first use (server.py calls this)."""
    att = getattr(store, "_attention", None)
    if att is None:
        att = store._attention = Attention(store)
    return att


def alerts_payload(store, annotate=None):
    return for_store(store).payload(annotate)
