"""Control mode (cc-dash --control): act on agents from the dashboard.

Off unless cc-dash runs with --control; without it every /api/control/* path is
a 404. Who may call these endpoints is auth.py's job: every POST needs a
control-scoped token (bought with the control code shown only on cc-dash's
terminal), plus the Origin and Content-Type checks.

Files are a doorbell, not an authority. Anything running as you can write into
the control directory, so the hook never acts on a decision, message or stop
file alone: it asks this process (GET /api/control/verify, answered from
`issued`, the memory of what control-scoped requests made in this launch) and
checks that the answer came from the process the lease names (the socket's
owner in /proc). A planted file without a matching record does nothing, and
files this launch did not issue are not shown either. A decision is also bound
to what the card showed (a digest of tool, input, cwd and flags), so a request
file rewritten under the dashboard's eyes cannot borrow an approval.

The lease. A 0700 directory ${XDG_STATE_HOME:-~/.local/state}/cc_tool/control/
holds a 0600 `lease` (pid, launch id, expiry ~6 s ahead, the hook's wait)
rewritten every 2 s. templates/hooks/dash-control.py does nothing unless the
lease is fresh and its pid alive, so a stopped or crashed dashboard releases
every waiting hook within seconds.

Permission prompts (also questions and plans). The hook writes req/<id>.json,
with a sha256 of tool + canonical input, and polls dec/<id>.json; decide()
writes the decision there by atomic rename, carrying that hash, and the hook's
outcome comes back in res/<id>.json (applied, terminal, released, lease,
timeout). A request that bash-guard asked about (its marker in asked/) or that
matches a permissions.ask rule needs a typed "approve", never just a click, and
never gets "allow for this session". Approvals are rate-limited per client.

Messages. To a session's main thread they go over the session's inbox socket
(cross-session messaging, the documented way for a script to post into a
session) when cc-dash can find it: sock/<session>.json recorded at SessionStart,
or the pid from `claude agents --json`. The session starts a turn at once if it
is idle, or reads it after the running tool. The receiving agent sees it framed
as a message from another session, not as a prompt you typed. Otherwise (no
socket, or a subagent) they are files msg/<session>.<agent|main>.<id>.json that
the hook claims into dlv/ and delivers as additionalContext after the next tool
call, at the end of the turn, or with the next prompt.

Stop. stop/<session>.<agent|main>.json makes the hook deny the next tool call
with continue:false: the turn stops at the next tool call. `pending` exists
while msg/ or stop/ holds anything, so the hooks' shell guard skips python
otherwise.

Terminal answers. The hook exits by itself when the prompt is answered in the
terminal; as a documented backstop, while a request is open this module polls
`claude agents --json` every 2 s and releases a request whose session it saw
"waiting" and now sees busy or idle.

Audit. Every dashboard action and its result is appended to control/audit.jsonl
(0600): when, the launch id, the client, what, the target session and agent,
the decision or message text, and the result.
"""
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import stat
import subprocess
import threading
import time

from .attention import state_dir

SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
REQ_ID = re.compile(r"^[0-9a-f]{6,16}-[0-9a-f]{12}$")
LEASE_EVERY = 2.0
LEASE_TTL = 6.0
SCAN_EVERY = 0.4
AGENTS_EVERY = 2.0
KEEP_RESOLVED = 600.0          # resolved requests stay in the state for the UI this long
MSG_TTL = 24 * 3600.0          # queued or delivered messages older than this are dropped
STOP_TTL = 600.0               # a stop with no tool call to land on expires
MAX_BODY = 64 * 1024
MAX_TEXT = 8000
MAX_REASON = 4000
RATE_N, RATE_SECS = 6, 10.0    # at most this many approvals per client in this window
AUDIT_KEEP = 2000              # audit lines kept in memory (the file keeps everything)
AUDIT_MAX_BYTES = 5_000_000
SUBDIRS = ("req", "dec", "res", "msg", "dlv", "asked", "sock", "stop", "stopped")
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
CLOEXEC = getattr(os, "O_CLOEXEC", 0)
SHOWN_KEYS = ("session", "agent_id", "agent_type", "cwd", "tool", "kind", "sig", "input", "input_truncated", "mode", "guard", "ask_rule")
DECIDE_ACTIONS = {"permission": ("allow", "allow_session", "deny", "release"), "plan": ("allow", "deny", "release"),
                  "question": ("answer", "deny", "release")}
APPROVALS = ("allow", "allow_session", "answer")


# ── safe files ──────────────────────────────────────────────────────────────
def ensure_private_dir(d):
    try:
        os.makedirs(os.path.dirname(d), mode=0o700, exist_ok=True)
    except OSError:
        pass
    try:
        os.mkdir(d, 0o700)
    except FileExistsError:
        pass
    st = os.lstat(d)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise PermissionError("%s is not a directory we own" % d)
    if st.st_mode & 0o077:
        os.chmod(d, 0o700)


def private_dir(d):
    try:
        st = os.lstat(d)
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and not (st.st_mode & 0o077)


def read_json(path, limit=1024 * 1024):
    try:
        fd = os.open(path, os.O_RDONLY | NOFOLLOW | CLOEXEC | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077 or st.st_size > limit:
            return None
        raw = b""
        while len(raw) <= limit:
            b = os.read(fd, 65536)
            if not b:
                break
            raw += b
    except OSError:
        return None
    finally:
        os.close(fd)
    try:
        d = json.loads(raw)
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def write_json(path, obj):
    """Atomic: a 0600 temp file (O_EXCL, no symlink follow) renamed over path."""
    tmp = "%s.%d.%s.tmp" % (path, os.getpid(), secrets.token_hex(4))
    data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8", "replace")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW | CLOEXEC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def append_line(path, obj):
    try:
        st = os.lstat(path)
        if stat.S_ISREG(st.st_mode) and st.st_size > AUDIT_MAX_BYTES:
            os.replace(path, path + ".1")
    except OSError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | NOFOLLOW | CLOEXEC, 0o600)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_nlink != 1:
            return
        if st.st_mode & 0o077:
            os.fchmod(fd, 0o600)
        os.write(fd, (json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8", "replace"))
    finally:
        os.close(fd)


def unlink(path):
    try:
        os.unlink(path)
        return True
    except OSError:
        return False


def shown_digest(r):
    """sha256 of what a decision card shows; dash-control.py's shown_digest() computes the same."""
    canon = json.dumps({k: r.get(k) for k in SHOWN_KEYS}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8", "replace")).hexdigest()


def _str(v, n):
    return v[:n] if isinstance(v, str) else ""


def _names(d, suffix=".json"):
    if not private_dir(d):
        return []
    try:
        return [n for n in os.listdir(d) if n.endswith(suffix)]
    except OSError:
        return []


# ── message transports ──────────────────────────────────────────────────────
class SocketTransport:
    """Cross-session messaging: one JSON line on the session's inbox socket.
    Main threads only (the socket belongs to the session), and only a socket
    that is ours, in a directory nobody else can write."""
    name = "socket"

    def __init__(self, control):
        self.c = control

    def path(self, session, agent):
        return None if agent else self.c.find_socket(session)

    def send(self, msg, path):
        text = "Message from the user, sent from the cc-dash dashboard:\n" + msg["text"]
        line = json.dumps({"type": "user", "message": {"role": "user", "content": text}}, ensure_ascii=False) + "\n"
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.settimeout(2.0)
            s.connect(path)
            s.sendall(line.encode("utf-8", "replace"))
            try:
                s.shutdown(socket.SHUT_WR)
                s.settimeout(0.3)
                s.recv(512)
            except OSError:
                pass
        finally:
            s.close()
        return "delivered"


class HookQueue:
    """Files dash-control.py delivers: after the next tool call, at the end of
    the turn, or with the next prompt."""
    name = "hook"

    def __init__(self, control):
        self.c = control

    def send(self, msg, _path=None):
        name = "%s.%s.%s.json" % (msg["session"], msg["agent_id"] or "main", msg["id"])
        write_json(os.path.join(self.c.dir, "msg", name), msg)
        self.c.set_pending(True)
        return "queued"


def _socket_ok(path):
    """A Unix socket we own whose directory nobody else can write."""
    try:
        st = os.lstat(path)
        dst = os.lstat(os.path.dirname(path))
    except OSError:
        return False
    return (stat.S_ISSOCK(st.st_mode) and st.st_uid == os.getuid() and stat.S_ISDIR(dst.st_mode)
            and dst.st_uid == os.getuid() and not (dst.st_mode & 0o022))


# ── the controller ──────────────────────────────────────────────────────────
class Control:
    def __init__(self, store, port, ctl_dir=None, wait=300.0, clock=time.time, agents_cmd="auto", sockets=True):
        self.store = store
        self.port = port
        self.dir = ctl_dir or os.path.join(state_dir(), "control")
        self.wait = float(wait)
        self.clock = clock
        self.launch = secrets.token_hex(8)
        # Per-launch key, written only to the 0600 lease: the hook sends it with every
        # verify request, so another local user (who cannot read the lease) cannot ask.
        self.key = secrets.token_hex(16)
        self.lock = threading.RLock()
        self.requests = {}         # request id -> request dict (+ "status", "decision")
        self.messages = {}         # message id -> message dict (+ "state")
        self.stops = {}            # "<session>.<agent|main>" -> stop dict
        self.audit = []
        self.approvals = {}        # client -> [ts of recent approvals]
        # What this launch issued through control-scoped requests (and its own
        # releases), the only thing GET /api/control/verify confirms:
        # ("dec", request id) / ("msg", message id) / ("stop", "<session>.<agent|main>").
        self.issued = {}
        self.socket = SocketTransport(self) if sockets else None
        self.queue = HookQueue(self)
        if agents_cmd == "auto":
            exe = shutil.which("claude")
            agents_cmd = [exe, "agents", "--json"] if exe else None
        self.agents_cmd = agents_cmd
        self.agents = {}           # session id -> {"status", "waiting_for", "pid"}
        self._agents_at = 0.0
        self._lease_at = 0.0
        self._stop = threading.Event()
        self._thread = None
        self.audit_path = os.path.join(self.dir, "audit.jsonl")

    # ── lifecycle ──────────────────────────────────────────────────────────
    def start(self, thread=True):
        ensure_private_dir(self.dir)
        for sub in SUBDIRS:
            ensure_private_dir(os.path.join(self.dir, sub))
        self._load_audit()
        self.write_lease()
        self.scan()
        if thread:
            self._thread = threading.Thread(target=self._loop, name="cc-dash-control", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        lease = read_json(os.path.join(self.dir, "lease"), 4096)
        if lease is not None and lease.get("launch") == self.launch:
            unlink(os.path.join(self.dir, "lease"))
        unlink(os.path.join(self.dir, "pending"))

    def _loop(self):
        while not self._stop.wait(SCAN_EVERY):
            try:
                if self.clock() - self._lease_at >= LEASE_EVERY:
                    self.write_lease()
                self.scan()
                self.poll_agents()
            except Exception:  # noqa: BLE001 - the loop must survive a bad file
                pass

    def write_lease(self):
        now = self.clock()
        self._lease_at = now
        write_json(os.path.join(self.dir, "lease"),
                   {"v": 1, "pid": os.getpid(), "launch": self.launch, "port": self.port, "key": self.key,
                    "expires": round(now + LEASE_TTL, 3), "wait": self.wait, "written": round(now, 3)})

    def set_pending(self, on):
        p = os.path.join(self.dir, "pending")
        if on:
            if not os.path.lexists(p):
                try:
                    os.close(os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW | CLOEXEC, 0o600))
                except OSError:
                    pass
        else:
            unlink(p)

    # ── audit ──────────────────────────────────────────────────────────────
    def _load_audit(self):
        try:
            fd = os.open(self.audit_path, os.O_RDONLY | NOFOLLOW | CLOEXEC)
        except OSError:
            return
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
                return
            size = st.st_size
            os.lseek(fd, max(0, size - 1024 * 1024), 0)
            raw = b""
            while True:
                b = os.read(fd, 1 << 20)
                if not b:
                    break
                raw += b
        finally:
            os.close(fd)
        lines = raw.split(b"\n")
        if size > 1024 * 1024:
            lines = lines[1:]
        out = []
        for ln in lines:
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            if isinstance(d, dict):
                out.append(d)
        self.audit = out[-AUDIT_KEEP:]

    def log(self, client, action, **fields):
        rec = {"ts": round(self.clock(), 3), "launch": self.launch, "client": client, "action": action}
        rec.update({k: v for k, v in fields.items() if v is not None})
        with self.lock:
            self.audit.append(rec)
            del self.audit[:-AUDIT_KEEP]
        try:
            append_line(self.audit_path, rec)
        except OSError:
            pass
        return rec

    # ── scanning the handoff directories ───────────────────────────────────
    def scan(self):
        now = self.clock()
        with self.lock:
            self._scan_requests(now)
            self._scan_messages(now)
            self._scan_stops(now)
            self._prune_issued(now)
            self.set_pending(bool(_names(os.path.join(self.dir, "msg")) or _names(os.path.join(self.dir, "stop"))))
            for n in _names(os.path.join(self.dir, "asked")):
                p = os.path.join(self.dir, "asked", n)
                try:
                    if now - os.lstat(p).st_mtime > 300:
                        unlink(p)
                except OSError:
                    pass

    def _scan_requests(self, now):
        req_dir, res_dir = os.path.join(self.dir, "req"), os.path.join(self.dir, "res")
        present = set()
        for n in _names(req_dir):
            rid = n[:-5]
            if not REQ_ID.match(rid):
                continue
            present.add(rid)
            if rid in self.requests:
                continue
            d = read_json(os.path.join(req_dir, n))
            if d is None or d.get("id") != rid or not isinstance(d.get("session"), str) or not isinstance(d.get("hash"), str):
                continue
            d["status"] = "open"
            d["seen_at"] = now
            self.requests[rid] = d
        results = {}
        for n in _names(res_dir):
            rid = n[:-5]
            if not REQ_ID.match(rid):
                continue
            d = read_json(os.path.join(res_dir, n), 65536)
            unlink(os.path.join(res_dir, n))
            if d is not None and d.get("id") == rid:
                results[rid] = d
        for rid, res in results.items():
            r = self.requests.get(rid)
            if r is None:          # opened and closed between two scans
                r = self.requests[rid] = {"id": rid, "session": res.get("session") or "", "agent_id": res.get("agent_id"),
                                          "kind": "permission", "tool": "", "input": {}, "ts": res.get("ts") or now,
                                          "status": "open", "seen_at": now}
            self._resolve(r, res.get("outcome") or "closed", res.get("ts") or now)
        for rid, r in list(self.requests.items()):
            if r["status"] in ("open", "sent") and rid not in present and rid not in results:
                # Gone without an outcome file (its session ended, say): give the
                # hook a moment to write one before calling the request closed.
                r.setdefault("gone_at", now)
                if now - r["gone_at"] > 3.0:
                    self._resolve(r, "closed", now)
            elif r["status"] not in ("open", "sent") and now - r.get("resolved_at", now) > KEEP_RESOLVED:
                del self.requests[rid]

    def _resolve(self, r, outcome, ts):
        if r["status"] not in ("open", "sent"):
            return
        r["status"] = outcome if outcome in ("applied", "terminal", "released", "lease", "timeout", "closed") else "closed"
        r["resolved_at"] = ts
        if r.get("decision"):
            self.log(r["decision"].get("client"), "result", request=r["id"], session=r.get("session"),
                     agent_id=r.get("agent_id"), tool=r.get("tool"), kind=r.get("kind"), result=r["status"])

    def _scan_messages(self, now):
        msg_dir, dlv_dir = os.path.join(self.dir, "msg"), os.path.join(self.dir, "dlv")
        queued = _names(msg_dir)
        delivered = _names(dlv_dir)
        mid_of = lambda n: n.rsplit(".", 2)[-2] if n.count(".") >= 3 else ""  # noqa: E731
        seen_q = set()
        for n in queued:
            mid = mid_of(n)
            m = self.messages.get(mid)
            if m is None:
                continue          # not queued by this launch (planted, or a previous run's): never shown
            seen_q.add(mid)
            if now - (m.get("ts") or now) > MSG_TTL and unlink(os.path.join(msg_dir, n)):
                m["state"] = "expired"
                self.log(m.get("client"), "result", message=mid, session=m.get("session"), agent_id=m.get("agent_id"), result="expired")
        dset = set()
        for n in delivered:
            mid = mid_of(n)
            dset.add(mid)
            m = self.messages.get(mid)
            if m is not None and m["state"] in ("delivered", "seen") and m.get("delivered_ts"):
                if now - m["delivered_ts"] > MSG_TTL:
                    unlink(os.path.join(dlv_dir, n))
                continue
            if m is None:
                continue
            d = read_json(os.path.join(dlv_dir, n), 65536)
            if d is None or d.get("id") != mid:
                continue
            first = m["state"] in ("queued", "delivering")
            d = {k: v for k, v in d.items() if k in ("delivered_ts", "via", "tool")}
            m = self.messages[mid] = dict(m, **d)
            m["file"] = n
            if d.get("delivered_ts"):
                m["state"] = "delivered"
                if first:
                    self.log(m.get("client"), "result", message=mid, session=m.get("session"),
                             agent_id=m.get("agent_id"), result="delivered", via=d.get("via"))
            else:
                m["state"] = "delivering"
        for mid, m in list(self.messages.items()):
            if m["state"] == "queued" and mid not in seen_q and mid not in dset:
                m.setdefault("gone_at", now)
                if now - m["gone_at"] > 2.0:
                    m["state"] = "cancelled" if m.get("cancelled") else "lost"
            if m["state"] in ("cancelled", "lost", "expired", "seen", "delivered") and now - (m.get("ts") or now) > MSG_TTL:
                del self.messages[mid]

    def _scan_stops(self, now):
        for sub, state in (("stop", "pending"), ("stopped", None)):
            for n in _names(os.path.join(self.dir, sub)):
                key = n[:-5]
                s = self.stops.get(key)
                if s is None:
                    continue      # not queued by this launch: never shown
                if sub == "stop":
                    if now - (s.get("ts") or now) > STOP_TTL and unlink(os.path.join(self.dir, sub, n)):
                        s["state"] = "expired"
                        self.issued.pop(("stop", key), None)
                    continue
                if s["state"] != "pending":
                    if now - (s.get("applied_ts") or now) > STOP_TTL:
                        unlink(os.path.join(self.dir, sub, n))
                    continue
                d = read_json(os.path.join(self.dir, sub, n), 4096)
                if d is None or not d.get("applied_ts"):
                    continue
                d = {k: v for k, v in d.items() if k in ("applied_ts", "outcome", "tool")}
                self.stops[key] = dict(s, **d, state=d.get("outcome") or "stopped")
                self.log(s.get("client"), "result", session=s.get("session"), agent_id=s.get("agent_id"),
                         stop=key, result=self.stops[key]["state"], tool=d.get("tool"))

    # ── `claude agents --json`: the documented status source ───────────────
    def _agents_now(self):
        if not self.agents_cmd:
            return None
        try:
            out = subprocess.run(self.agents_cmd, capture_output=True, text=True, timeout=5).stdout
            rows = json.loads(out or "[]")
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
        status = {}
        for r in rows if isinstance(rows, list) else []:
            if isinstance(r, dict) and isinstance(r.get("sessionId"), str):
                status[r["sessionId"]] = {"status": r.get("status") or "", "waiting_for": r.get("waitingFor") or "",
                                          "pid": r.get("pid") if isinstance(r.get("pid"), int) else None}
        self.agents = status
        self._agents_at = self.clock()
        return status

    def poll_agents(self, force=False):
        now = self.clock()
        with self.lock:
            waiting = [r for r in self.requests.values() if r["status"] == "open"]
        if not self.agents_cmd or not waiting or (not force and now - self._agents_at < AGENTS_EVERY):
            return
        status = self._agents_now()
        if status is None:
            self._agents_at = now
            return
        with self.lock:
            for r in waiting:
                st = status.get(r["session"])
                if st is None:
                    continue
                if st["status"] == "waiting":
                    r["seen_waiting"] = True
                elif r.get("seen_waiting") and st["status"] in ("busy", "idle") and r["status"] == "open":
                    dec = {"id": r["id"], "hash": r["hash"], "action": "release", "why": "terminal", "ts": now}
                    self.issued[("dec", r["id"])] = dict(dec, shown=None, issued_at=now)
                    try:
                        write_json(os.path.join(self.dir, "dec", r["id"] + ".json"), dec)
                    except OSError:
                        pass

    def find_socket(self, session):
        """The session's inbox socket: recorded at SessionStart, else derived from its pid."""
        d = read_json(os.path.join(self.dir, "sock", session + ".json"), 4096)
        if d is not None and d.get("session") == session and isinstance(d.get("socket"), str) and _socket_ok(d["socket"]):
            return d["socket"]
        info = self.agents.get(session)
        if info is None or self.clock() - self._agents_at > 5:
            info = (self._agents_now() or {}).get(session)
        pid = (info or {}).get("pid")
        if not pid:
            return None
        uid = os.getuid()
        dirs = [os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/run/user/%d" % uid, "cc-socks"),
                "/run/user/%d/cc-socks" % uid, "/tmp/cc-socks-%d" % uid]
        for dd in dirs:
            p = os.path.join(dd, "%d.sock" % pid)
            if _socket_ok(p):
                return p
        return None

    # ── actions ────────────────────────────────────────────────────────────
    def _rate_ok(self, client):
        now = self.clock()
        recent = [t for t in self.approvals.get(client, []) if now - t < RATE_SECS]
        if len(recent) >= RATE_N:
            self.approvals[client] = recent
            return False
        recent.append(now)
        self.approvals[client] = recent
        return True

    def decide(self, client, body):
        """(http status, response) for POST /api/control/decide."""
        rid = body.get("id")
        action = body.get("action")
        if not isinstance(rid, str) or not REQ_ID.match(rid):
            return 400, {"error": "Unknown request id."}
        self.scan()
        with self.lock:
            r = self.requests.get(rid)
            if r is None:
                return 404, {"error": "No open request with that id."}
            if r["status"] != "open":
                return 409, {"error": "This request is no longer open.", "status": r["status"]}
            kind = r.get("kind") if r.get("kind") in DECIDE_ACTIONS else "permission"
            if action not in DECIDE_ACTIONS[kind]:
                return 400, {"error": "That action does not apply to a %s." % kind}
            if body.get("hash") is not None and body.get("hash") != r["hash"]:
                return 409, {"error": "The request changed; reload it."}
            flagged = r.get("guard") or r.get("ask_rule")
            if action == "allow_session" and (flagged or not r.get("session_rules")):
                return 400, {"error": "Allow for this session is not offered for this request."}
            if action in ("allow", "allow_session") and flagged and body.get("confirm") != "approve":
                return 428, {"error": "This command was flagged (%s); type approve to confirm." % ("bash-guard" if r.get("guard") else "an ask rule"),
                             "guard": r.get("guard") or "", "ask_rule": r.get("ask_rule") or ""}
            if action in APPROVALS and not self._rate_ok(client):
                return 429, {"error": "Too many approvals in a row; wait a few seconds."}
            dec = {"id": rid, "hash": r["hash"], "action": action, "ts": round(self.clock(), 3), "launch": self.launch}
            if action == "deny":
                dec["message"] = _str(body.get("message"), MAX_REASON).strip()
            if action == "answer":
                answers = body.get("answers")
                qs = (r.get("input") or {}).get("questions") or []
                texts = {q.get("question") for q in qs if isinstance(q, dict)}
                if not isinstance(answers, dict) or not answers or not all(
                        isinstance(k, str) and k in texts and isinstance(v, str) and v.strip() and len(v) <= 2000
                        for k, v in answers.items()):
                    return 400, {"error": "Answers must map each question's text to a non-empty answer."}
                dec["answers"] = {k: v.strip() for k, v in answers.items()}
            self.issued[("dec", rid)] = dict(dec, shown=shown_digest(r), issued_at=self.clock())
            try:
                write_json(os.path.join(self.dir, "dec", rid + ".json"), dec)
            except OSError as e:
                self.issued.pop(("dec", rid), None)
                return 500, {"error": "Could not write the decision: %s" % e.strerror}
            r["status"] = "sent"
            r["decision"] = {"action": action, "ts": dec["ts"], "client": client, "message": dec.get("message"),
                             "answers": dec.get("answers")}
            self.log(client, action, request=rid, session=r.get("session"), agent_id=r.get("agent_id"),
                     tool=r.get("tool"), kind=kind, target=_target_text(r), cwd=r.get("cwd") or None,
                     message=dec.get("message") or None, answers=dec.get("answers"), guard=r.get("guard") or None,
                     ask_rule=r.get("ask_rule") or None, rules=r.get("session_rules") if action == "allow_session" else None,
                     confirmed=True if flagged and action != "deny" else None, result="sent")
            return 200, {"ok": True, "request": self._req_view(r)}

    def _known(self, sid, agent):
        self.store.refresh()          # a session that started after the last poll
        with self.store.lock:
            s = self.store.sessions.get(sid)
            return s is not None and (agent is None or any(x.agent_id == agent for x in s.subs))

    def _target(self, body):
        sid = body.get("session")
        agent = body.get("agent_id") or None
        if not isinstance(sid, str) or not SAFE_ID.match(sid):
            return None, (400, {"error": "Unknown session."})
        if agent is not None and (not isinstance(agent, str) or not SAFE_ID.match(agent)):
            return None, (400, {"error": "Unknown agent."})
        if not self._known(sid, agent):
            return None, (404, {"error": "No session (or subagent) with that id in the lookback window."})
        return (sid, agent), None

    def message(self, client, body):
        t, err = self._target(body)
        if err:
            return err
        sid, agent = t
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            return 400, {"error": "The message is empty."}
        if len(text) > MAX_TEXT:
            return 400, {"error": "Messages are limited to %d characters." % MAX_TEXT}
        now = self.clock()
        mid = "%x%s" % (int(now * 1000), secrets.token_hex(4))
        msg = {"v": 1, "id": mid, "session": sid, "agent_id": agent, "text": text.strip(), "ts": round(now, 3),
               "launch": self.launch, "client": client}
        sock = self.socket.path(sid, agent) if self.socket else None
        state, transport, note = None, None, None
        if sock:
            try:
                state, transport = self.socket.send(msg, sock), self.socket.name
            except OSError as e:
                note = "inbox socket failed (%s); queued for the hooks" % (e.strerror or type(e).__name__)
        with self.lock:
            if state is None:
                self.issued[("msg", mid)] = {"id": mid, "session": sid, "agent_id": agent, "text": msg["text"], "issued_at": now}
                try:
                    state, transport = self.queue.send(msg), self.queue.name
                except OSError as e:
                    self.issued.pop(("msg", mid), None)
                    return 500, {"error": "Could not queue the message: %s" % e.strerror}
            m = self.messages[mid] = dict(msg, state=state, transport=transport,
                                          file="%s.%s.%s.json" % (sid, agent or "main", mid))
            if state == "delivered":
                m.update(delivered_ts=round(self.clock(), 3), via="socket")
            self.log(client, "message", message=mid, session=sid, agent_id=agent, text=msg["text"],
                     transport=transport, note=note, result=state)
            return 200, {"ok": True, "message": self._msg_view(m)}

    def cancel(self, client, body):
        mid = body.get("id")
        with self.lock:
            m = self.messages.get(mid) if isinstance(mid, str) else None
            if m is None:
                return 404, {"error": "No message with that id."}
            if m["state"] != "queued" or not unlink(os.path.join(self.dir, "msg", m["file"])):
                return 409, {"error": "Already delivered.", "state": m["state"]}
            m["state"] = "cancelled"
            m["cancelled"] = True
            self.issued.pop(("msg", mid), None)
            self.log(client, "cancel", message=mid, session=m.get("session"), agent_id=m.get("agent_id"), result="cancelled")
            self.scan()
            return 200, {"ok": True, "message": self._msg_view(m)}

    def stop_turn(self, client, body):
        t, err = self._target(body)
        if err:
            return err
        sid, agent = t
        key = "%s.%s" % (sid, agent or "main")
        now = self.clock()
        with self.lock:
            s = {"v": 1, "session": sid, "agent_id": agent, "ts": round(now, 3), "launch": self.launch, "client": client}
            self.issued[("stop", key)] = {"key": key, "session": sid, "agent_id": agent, "ts": s["ts"], "issued_at": now}
            try:
                unlink(os.path.join(self.dir, "stopped", key + ".json"))
                write_json(os.path.join(self.dir, "stop", key + ".json"), s)
            except OSError as e:
                return 500, {"error": "Could not queue the stop: %s" % e.strerror}
            self.stops[key] = dict(s, state="pending")
            self.set_pending(True)
            self.log(client, "stop", session=sid, agent_id=agent, stop=key, result="pending")
            return 200, {"ok": True, "stop": self._stop_view(key, self.stops[key])}

    # ── the hook asks before it acts ───────────────────────────────────────
    def _prune_issued(self, now):
        """Records live until their TTL (never until a first query: a query by
        anyone else must not burn the human's decision, message or stop)."""
        ttl = {"dec": self.wait + KEEP_RESOLVED, "msg": MSG_TTL, "stop": STOP_TTL}
        for k in [k for k, r in self.issued.items() if now - r.get("issued_at", now) > ttl.get(k[0], 0)]:
            del self.issued[k]

    def verify(self, q):
        """(http status, body) for GET /api/control/verify: the record this launch
        issued for a decision, message or stop file, or 404. The hook applies the
        answer, never the file. A query never consumes a record (the server
        checked the caller's lease key); a message or stop already seen applied
        (its dlv/ or stopped/ file read back) is no longer confirmed, so a copy of
        the genuine file planted afterwards does nothing."""
        kind, vid = q.get("kind") or "", (q.get("id") or "")[:200]
        with self.lock:
            if kind == "dec":
                rec = self.issued.get(("dec", vid))
                if rec is not None and rec["hash"] == q.get("hash") and rec.get("shown") in (None, q.get("shown")):
                    return 200, dict({k: rec.get(k) for k in ("id", "hash", "action", "message", "answers", "why")}, ok=True)
            elif kind == "msg":
                rec = self.issued.get(("msg", vid))
                m = self.messages.get(vid) or {}
                if rec is not None and rec["session"] == q.get("session") and (rec["agent_id"] or "") == (q.get("agent") or "") \
                        and m.get("state") in ("queued", "delivering"):
                    return 200, dict({k: v for k, v in rec.items() if k != "issued_at"}, ok=True)
            elif kind == "stop":
                rec = self.issued.get(("stop", vid))
                if rec is not None and (self.stops.get(vid) or {}).get("state") == "pending":
                    return 200, dict({k: v for k, v in rec.items() if k != "issued_at"}, ok=True)
            else:
                return 400, {"ok": False, "error": "Unknown kind."}
        self.log(None, "refused", kind=kind, item=vid, session=q.get("session") or None,
                 result="no matching dashboard action: a file nobody sent from the dashboard was ignored")
        return 404, {"ok": False}

    # ── the state the UI polls ─────────────────────────────────────────────
    def state(self, session=""):
        self.scan()
        self.store.refresh()          # transcripts decide "seen"; cheap when nothing changed
        with self.lock:
            reqs = [self._req_view(r) for r in self.requests.values()]
            msgs = [dict(m) for m in self.messages.values() if not session or m.get("session") == session]
            stops = [self._stop_view(k, s) for k, s in self.stops.items() if not session or s.get("session") == session]
            audit = [a for a in self.audit if not session or a.get("session") == session][-200:]
        self._mark_seen(msgs)
        reqs.sort(key=lambda r: r.get("ts") or 0)
        msgs.sort(key=lambda m: m.get("ts") or 0)
        return {"launch": self.launch, "now": self.clock(), "requests": reqs,
                "messages": [self._msg_view(m) for m in msgs][-300:], "stops": stops, "audit": audit}

    def _mark_seen(self, msgs):
        """Delivered becomes seen once the agent wrote an assistant message after delivery."""
        with self.store.lock:
            for m in msgs:
                if m["state"] != "delivered" or not m.get("delivered_ts"):
                    continue
                s = self.store.sessions.get(m["session"])
                if s is None:
                    continue
                acc = s.acc if not m.get("agent_id") else next((x.acc for x in s.subs if x.agent_id == m["agent_id"]), None)
                if acc is None or not acc.msg_order:
                    continue
                last = acc.msg_ts.get(acc.msg_order[-1]) or 0
                if last > m["delivered_ts"]:
                    m["state"] = "seen"
                    m["seen_ts"] = last
                    with self.lock:
                        live = self.messages.get(m["id"])
                        if live is not None:
                            live.update(state="seen", seen_ts=last)

    @staticmethod
    def _req_view(r):
        keep = ("id", "ts", "session", "agent_id", "agent_type", "cwd", "tool", "kind", "sig", "hash", "input",
                "input_truncated", "mode", "deadline", "guard", "ask_rule", "session_rules", "server", "status", "resolved_at")
        v = {k: r.get(k) for k in keep if k in r}
        d = r.get("decision")
        if d:
            v["decision"] = {"action": d["action"], "ts": d["ts"], "message": d.get("message")}
        return v

    @staticmethod
    def _msg_view(m):
        keep = ("id", "session", "agent_id", "text", "ts", "state", "delivered_ts", "via", "tool", "seen_ts", "transport")
        return {k: m.get(k) for k in keep if k in m}

    @staticmethod
    def _stop_view(key, s):
        return {"key": key, "session": s.get("session"), "agent_id": s.get("agent_id"), "ts": s.get("ts"),
                "state": s.get("state"), "applied_ts": s.get("applied_ts"), "tool": s.get("tool")}

    # ── alerts: which open request an alert row is ─────────────────────────
    def annotate(self, rows):
        with self.lock:
            live = [r for r in self.requests.values() if r["status"] in ("open", "sent")]
        for row in rows:
            m = row.get("_match")
            if not m:
                continue
            for r in live:
                if (r.get("session"), r.get("agent_id") or None, r.get("tool") or None, r.get("sig") or "") == m:
                    row["request"] = r["id"]
                    break


def _target_text(r):
    ti = r.get("input") if isinstance(r.get("input"), dict) else {}
    if r.get("tool") == "Bash":
        return _str(ti.get("command"), 2000)
    if r.get("kind") == "plan":
        return _str(ti.get("plan"), 300)
    return _str(r.get("sig"), 300)
