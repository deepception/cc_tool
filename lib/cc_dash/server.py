"""The cc-dash HTTP server: 127.0.0.1 only, GET only (plus control mode's POSTs), fixed asset map.

Transcripts hold secrets (commands, file contents, tokens pasted into
prompts), so the server
  - binds to 127.0.0.1 and never to a public interface,
  - answers only when the Host header is 127.0.0.1:<port> or localhost:<port>
    (a DNS-rebinding page on another origin sends its own hostname),
  - serves a fixed set of named files from web/ and nothing else; the request path is
    looked up in a dict, never joined onto a directory,
  - sends no CORS headers and a CSP that allows only its own origin.

Every /api/* request needs this launch's token (auth.py: paired through a
one-time code, sent as X-CC-Dash-Token, never a cookie). Pairing gives a
read-only token. With --control (control.py) it also serves /api/control/*,
whose POSTs need a control-scoped token (POST /api/control/unlock with the
control code cc-dash wrote to its terminal), Content-Type application/json and
an Origin equal to the dashboard's own. GET /api/control/verify is the hook's
question ("did the dashboard send this?"); it carries no token, answers only a
socket owned by this user, and confirms only what this launch issued. Without
--control those paths are 404 and every POST but /api/pair is 405.
"""
import argparse
import errno
import json
import os
import re
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import attention
from . import plans   # plan progress (/api/plans)
from .auth import Auth, local_pairing_supported, same_user
from .control import MAX_BODY, Control
from .docs import Docs, DocsError
from .store import Store, config_dir
from .parser import tilde

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/dom.js": ("dom.js", "text/javascript; charset=utf-8"),
    "/auth.js": ("auth.js", "text/javascript; charset=utf-8"),   # pairing and the X-CC-Dash-Token
    "/timeline.js": ("timeline.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    # alerts (attention.py): toast stack, alert drawer, title badge, desktop notifications
    "/alerts.js": ("alerts.js", "text/javascript; charset=utf-8"),
    "/alerts.css": ("alerts.css", "text/css; charset=utf-8"),
    "/alerts-worker.js": ("alerts-worker.js", "text/javascript; charset=utf-8"),
    # control mode (--control): action cards, message composer
    "/control.js": ("control.js", "text/javascript; charset=utf-8"),
    "/control.css": ("control.css", "text/css; charset=utf-8"),
    # Docs view (docs.py): Markdown renderer and the reader
    "/markdown.js": ("markdown.js", "text/javascript; charset=utf-8"),
    "/docs.js": ("docs.js", "text/javascript; charset=utf-8"),
    "/docs.css": ("docs.css", "text/css; charset=utf-8"),
    # plans (plans.py): plan list, plan detail, the Live and Overview hooks
    "/plans.js": ("plans.js", "text/javascript; charset=utf-8"),
    "/plans.css": ("plans.css", "text/css; charset=utf-8"),
    # session Agents tab (tree.py): recursive agent tree, its charts, the effort chip
    "/agents.js": ("agents.js", "text/javascript; charset=utf-8"),
    # Overview's Routing panel (routing.py): who picked each agent's model and effort
    "/routing.js": ("routing.js", "text/javascript; charset=utf-8"),
    "/routing.css": ("routing.css", "text/css; charset=utf-8"),
}
# img-src blob: is for Docs images (fetched with the token, shown as object URLs);
# frame-src 'self' is for the sandboxed HTML preview at /docs-frame/<ticket>, whose
# own response sets a stricter CSP with the `sandbox` directive (docs.py).
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
       "connect-src 'self'; font-src 'self'; frame-src 'self'; base-uri 'none'; form-action 'none'; "
       "frame-ancestors 'none'")
DEFAULT_PORT = 7878


class Handler(BaseHTTPRequestHandler):
    server_version = "cc-dash"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # The store and the allowed Host values hang off the server object.
    def log_message(self, fmt, *args):  # noqa: D401 - quiet unless asked
        if getattr(self.server, "verbose", False):
            sys.stderr.write("cc-dash: " + (fmt % args) + "\n")

    def _host_ok(self):
        host = (self.headers.get("Host") or "").strip().lower()
        return host in self.server.allowed_hosts

    def _send(self, code, body, ctype, extra=None, csp=CSP):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", csp)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, separators=(",", ":"), default=str), "application/json; charset=utf-8")

    def _deny(self, code, msg):
        self._send(code, msg + "\n", "text/plain; charset=utf-8")

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self._host_ok():
            return self._deny(421, "Misdirected request: cc-dash only answers on 127.0.0.1 and localhost.")
        parts = urlsplit(self.path)
        path = parts.path
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        store = self.server.store
        ctl = getattr(self.server, "control", None)
        auth = getattr(self.server, "auth", None)
        try:
            if path in ASSETS:
                name, ctype = ASSETS[path]
                with open(os.path.join(WEB, name), "rb") as fh:
                    return self._send(200, fh.read(), ctype)
            if path == "/pair":                 # the shell; the page reads #code= and POSTs /api/pair
                with open(os.path.join(WEB, "index.html"), "rb") as fh:
                    return self._send(200, fh.read(), "text/html; charset=utf-8")
            if path.startswith("/docs-frame/"):     # a one-time ticket from /api/docs/frame, no token
                return self._docs_frame(path[len("/docs-frame/"):])
            if ctl is not None and path == "/api/control/verify":   # the hook's check, no token (see _verify)
                return self._verify(ctl, q)
            # Every data endpoint needs this launch's token in X-CC-Dash-Token (auth.py).
            sess = auth.session(self.headers) if auth is not None else None
            if sess is None:
                if path.startswith("/api/"):
                    return self._json(401, {"error": "Not paired.", "repair": True})
                return self._deny(404, "Not found.")
            if path == "/api/meta":
                meta = store.meta()
                meta["control"] = {"enabled": ctl is not None, "paired": True, "scope": sess.get("scope") or "read"}
                if ctl is not None:
                    meta["control"].update(auth.control_state(self.headers))
                return self._json(200, meta)
            if path == "/api/alerts":  # open "needs you" alerts, see attention.py
                return self._json(200, attention.alerts_payload(store, ctl.annotate if ctl else None))
            if ctl is not None and path == "/api/control/state":
                return self._json(200, ctl.state(q.get("session", "")[:100]))
            if path.startswith("/api/docs/"):
                return self._docs(path, q)
            if path == "/api/live":
                return self._json(200, store.live_payload())
            if path == "/api/sessions":
                return self._json(200, store.sessions_payload())
            if path == "/api/overview":
                # A filter value compared with project labels, never a path.
                return self._json(200, store.overview_payload(q.get("project", "")))
            if path == "/api/routing":          # routing.py: who chose each agent's model and effort
                return self._json(200, store.routing_payload(q.get("project", "")))
            if path == "/api/session":
                p = store.session_payload(q.get("id", ""))
                return self._json(200, p) if p else self._json(404, {"error": "No session with that id in the lookback window."})
            if path == "/api/session/tree":     # the recursive agent tree (tree.py)
                p = store.tree_payload(q.get("id", ""))
                return self._json(200, p) if p else self._json(404, {"error": "No session with that id in the lookback window."})
            if path == "/api/agent":
                p = store.agent_payload(q.get("session", ""), q.get("id", ""))
                return self._json(200, p) if p else self._json(404, {"error": "No subagent with that id in this session."})
            if path in ("/api/plans", "/api/plans/detail"):   # plans.py: plan progress and milestones
                return self._plans(path, q, store)
        except BrokenPipeError:
            return None
        except Exception as e:  # noqa: BLE001 - one bad transcript must not take the server down
            if getattr(self.server, "verbose", False):
                import traceback
                traceback.print_exc()
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})
        return self._deny(404, "Not found.")

    def _docs(self, path, q):
        """GET /api/docs/{projects,tree,file,search,asset,frame}: see docs.py for what may be read."""
        docs = self.server.docs
        project = q.get("project", "")
        try:
            if path == "/api/docs/projects":
                return self._json(200, {"projects": [dict(p, root=tilde(p["root"])) for p in docs.projects()]})
            if path == "/api/docs/tree":
                return self._json(200, docs.tree(project))
            if path == "/api/docs/file":
                return self._json(200, docs.file(project, q.get("path", ""), q.get("known", "")[:64]))
            if path == "/api/docs/search":
                return self._json(200, docs.search(project, q.get("q", "")))
            if path == "/api/docs/frame":
                return self._json(200, docs.frame_ticket(project, q.get("path", ""), q.get("scripts") == "1"))
            if path == "/api/docs/asset":
                data, ctype = docs.asset(project, q.get("path", ""))
                return self._send(200, data, ctype, {"Content-Disposition": "attachment"},
                                  csp="default-src 'none'; sandbox")
        except DocsError as e:
            return self._json(e.status, {"error": e.msg})
        return self._deny(404, "Not found.")

    def _docs_frame(self, ticket):
        """A project's HTML document for the sandboxed preview frame, with its own strict CSP."""
        site = (self.headers.get("Sec-Fetch-Dest") or "").lower()
        if site and site != "iframe":
            return self._deny(403, "This preview only opens inside the cc-dash Docs view.")
        try:
            body, csp = self.server.docs.frame(ticket[:100])
        except DocsError as e:
            return self._send(e.status, e.msg + "\n", "text/plain; charset=utf-8",
                              csp="default-src 'none'; frame-ancestors 'self'; sandbox")
        return self._send(200, body, "text/html; charset=utf-8", csp=csp)
    def _plans(self, path, q, store):
        """GET /api/plans?project=[&all=1] and /api/plans/detail?project=&path= (plans.py checks the path)."""
        al = attention.alerts_payload(store)["alerts"]
        eng = plans.for_store(store)
        if path == "/api/plans":
            return self._json(200, eng.payload(q.get("project", ""), al, everything=q.get("all") == "1"))
        p = eng.detail(q.get("project", "")[:200], q.get("path", "")[:1024], al)
        return self._json(200, p) if p else self._json(404, {"error": "No plan at that path in that project."})

    def _read_body(self):
        """The JSON object body, or (status, error) when there is none."""
        try:
            n = int(self.headers.get("Content-Length") or "-1")
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY:
            return None, (413 if n > MAX_BODY else 411, {"error": "Body missing or too large."})
        try:
            body = json.loads(self.rfile.read(n) or b"null")
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return None, (400, {"error": "The body must be a JSON object."})
        return body, None

    def _pair(self, auth):
        """POST /api/pair {"code"}: spend a pairing code for this launch's token."""
        why = auth.check_origin(self.headers, (self.headers.get("Host") or "").strip().lower())
        if why:
            return self._json(403, {"error": "Refused: bad " + why + "."})
        body, err = self._read_body()
        if err:
            return self._json(*err)
        token = auth.exchange(body.get("code"))
        if token is None:
            return self._json(403, {"error": "This pairing link has expired or was already used. Press Enter in the "
                                             "terminal running cc-dash for a new one, or restart cc-dash."})
        return self._json(200, {"token": token})

    def _pair_local(self, auth):
        """POST /api/pair-local {}: a token for a browser run by this same user (Linux)."""
        why = auth.check_origin(self.headers, (self.headers.get("Host") or "").strip().lower())
        if why:
            return self._json(403, {"error": "Refused: bad " + why + "."})
        _body, err = self._read_body()
        if err:
            return self._json(*err)
        token = auth.exchange_local(self.client_address)
        if token is None:
            return self._json(403, {"error": "This browser does not run as the user who started cc-dash."})
        return self._json(200, {"token": token})

    def _verify(self, ctl, q):
        """GET /api/control/verify?kind=dec|msg|stop&id=...: dash-control.py asks before
        it acts on a file. The caller must send the lease's per-launch key
        (X-CC-Dash-Lease; the lease is 0600, so other users cannot read it) and,
        where the kernel can say (Linux), own the connecting socket: an unknown
        owner is refused. The hook in turn checks that this process owns the
        socket that answered. A query never consumes a record."""
        import hmac
        key = self.headers.get("X-CC-Dash-Lease") or ""
        if not key or not hmac.compare_digest(key, ctl.key):
            return self._json(403, {"ok": False})
        if local_pairing_supported() and not same_user(self.client_address, self.server.port):
            return self._json(403, {"ok": False})
        code, out = ctl.verify(q)
        return self._json(code, out)

    def _unlock(self, auth):
        """POST /api/control/unlock {"code"}: the control code from cc-dash's terminal
        buys a control-scoped token (auth.py)."""
        why = auth.check_origin(self.headers, (self.headers.get("Host") or "").strip().lower())
        if why:
            return self._json(403, {"error": "Refused: bad " + why + "."})
        body, err = self._read_body()
        if err:
            return self._json(*err)
        code, out = auth.unlock(self.headers, body.get("code"))
        ctl = getattr(self.server, "control", None)
        if ctl is not None and code in (200, 403, 423):
            ctl.log(auth.client(self.headers), "unlock", result="control unlocked" if code == 200 else
                    ("wrong control code" if code == 403 else "this token is locked after wrong control codes"))
        return self._json(code, out)

    def _no_body(self):
        if not self._host_ok():
            return self._deny(421, "Misdirected request.")
        if getattr(self.server, "control", None) is None and urlsplit(self.path).path.startswith("/api/control/"):
            return self._deny(404, "Not found.")
        self._deny(405, "cc-dash is read-only.")

    POSTS = ("/api/control/decide", "/api/control/message", "/api/control/cancel", "/api/control/stop")

    def do_POST(self):
        ctl = getattr(self.server, "control", None)
        auth = getattr(self.server, "auth", None)
        path = urlsplit(self.path).path
        if not self._host_ok():
            return self._deny(421, "Misdirected request.")
        if path == "/api/pair" and auth is not None:
            return self._pair(auth)
        if path == "/api/pair-local" and auth is not None and local_pairing_supported():
            return self._pair_local(auth)
        if path == "/api/control/unlock" and ctl is not None and auth is not None:
            return self._unlock(auth)
        if ctl is None or auth is None or path not in self.POSTS:
            return self._no_body()
        why = auth.check_write(self.headers, (self.headers.get("Host") or "").strip().lower())
        if why == "token":
            return self._json(401, {"error": "Not paired.", "repair": True})
        if why == "scope":
            return self._json(403, {"error": "Enter the control code from your terminal to act.", "unlock": True})
        if why:
            return self._json(403, {"error": "Refused: bad " + why + "."})
        client = auth.client(self.headers)
        body, err = self._read_body()
        if err:
            return self._json(*err)
        try:
            if path == "/api/control/decide":
                code, out = ctl.decide(client, body)
            elif path == "/api/control/message":
                code, out = ctl.message(client, body)
            elif path == "/api/control/stop":
                code, out = ctl.stop_turn(client, body)
            else:
                code, out = ctl.cancel(client, body)
        except Exception as e:  # noqa: BLE001 - report, never take the server down
            code, out = 500, {"error": f"{type(e).__name__}: {e}"}
        return self._json(code, out)

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _no_body


class DashServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port, store, verbose=False):
        super().__init__(("127.0.0.1", port), Handler)
        self.store = store
        self.verbose = verbose
        self.control = None
        real = self.server_address[1]
        self.port = real
        self.allowed_hosts = {f"127.0.0.1:{real}", f"localhost:{real}"}
        self.auth = Auth(real)
        self.docs = Docs(store)


def make_server(port, store, verbose=False, fallback=True):
    """Bind 127.0.0.1:port; when it is taken and fallback is on, take any free port."""
    try:
        return DashServer(port, store, verbose)
    except OSError as e:
        if not fallback or e.errno not in (errno.EADDRINUSE, errno.EACCES):
            raise
        return DashServer(0, store, verbose)


def parse_args(argv):
    p = argparse.ArgumentParser(
        prog="cc-dash",
        description="Local dashboard of your Claude Code sessions, subagents, tokens, cost and cc_tool guard "
                    "catches (read-only unless --control). Serves on 127.0.0.1 until Ctrl-C; open the printed "
                    "pairing link to use it.",
        allow_abbrev=False)     # --con / --c are not --control: an abbreviation must not slip past bash-guard
    p.add_argument("--port", type=int, default=DEFAULT_PORT,
                   help=f"port to listen on (default {DEFAULT_PORT}; a free port is used if it is taken)")
    p.add_argument("--no-open", action="store_true", help="do not open the browser")
    p.add_argument("--days", type=float, default=7, help="lookback window in days (default 7)")
    p.add_argument("--project", default="", help="only sessions whose working directory contains this text")
    p.add_argument("--verbose", action="store_true", help="log each request to stderr")
    p.add_argument("--control", action="store_true",
                   help="control mode: approve or deny permission prompts, answer questions and plans, and send "
                        "messages to sessions from the dashboard (needs the dash-control.py hook; pair with the "
                        "printed link)")
    p.add_argument("--control-wait", type=float, default=300, metavar="SECS",
                   help="control mode: how long a waiting permission hook waits for the dashboard (default 300)")
    p.add_argument("--ready-fd", type=int, default=None, metavar="FD", help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    if not (5 <= a.control_wait <= 590):
        p.error("--control-wait must be 5-590 seconds")
    if a.days <= 0:
        p.error("--days must be positive")
    if not (0 <= a.port <= 65535):
        p.error("--port must be 0-65535")
    if a.ready_fd is not None and a.ready_fd < 3:
        p.error("--ready-fd must be 3 or more")
    return a


def _ready(a):
    """Close --ready-fd: the shell function `ccdash` waits on it so the control
    code is on the terminal before its prompt comes back. Nothing is written to it."""
    if a.ready_fd is not None:
        try:
            os.close(a.ready_fd)
        except OSError:
            pass
        a.ready_fd = None


# What Claude Code puts in the environment of its Bash tool's processes
# (checked against `env` from a headless `claude -p` Bash call, CLI 2.1.30x).
CLAUDE_ENV_SIGNALS = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PID",
                      "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_CHILD_SESSION")
INSIDE_CLAUDE = "cc-dash --control must be started from your own terminal, not from inside a Claude Code session"


def _proc_stat(pid, proc="/proc"):
    """{comm, ppid, pgrp, sid, tty} from /proc/<pid>/stat, or None."""
    try:
        with open("%s/%d/stat" % (proc, pid), encoding="utf-8", errors="replace") as fh:
            st = fh.read()
        comm = st[st.index("(") + 1:st.rindex(")")]
        f = st[st.rindex(")") + 2:].split()
        return {"comm": comm, "ppid": int(f[1]), "pgrp": int(f[2]), "sid": int(f[3]), "tty": int(f[4])}
    except (OSError, ValueError, IndexError):
        return None


def _ancestors(pid=None, n=64, proc="/proc"):
    """(pid, comm, exe, argv) of pid (default: this process's parent) and each of
    its ancestors up to init, from /proc; [] elsewhere."""
    out, pid = [], os.getppid() if pid is None else pid
    for _ in range(n):
        if pid <= 1:
            break
        st = _proc_stat(pid, proc)
        if st is None:
            break
        try:
            with open("%s/%d/cmdline" % (proc, pid), "rb") as fh:
                argv = [a.decode("utf-8", "replace") for a in fh.read().split(b"\0") if a]
        except OSError:
            argv = []
        try:
            exe = os.readlink("%s/%d/exe" % (proc, pid))
        except OSError:
            exe = ""
        out.append((pid, st["comm"], exe, argv))
        pid = st["ppid"]
    return out


def _is_claude(comm, exe, argv):
    """The claude CLI itself, or node running it."""
    names = {comm, os.path.basename(exe or "")} | ({os.path.basename(argv[0])} if argv else set())
    if "claude" in names:
        return True
    if names & {"node", "nodejs", "bun"}:
        return any(re.search(r"(?:^|/)(?:claude(?:\.js|\.mjs)?|@anthropic-ai/claude-code(?:/|$))", a) for a in argv[1:3])
    return False


def control_refusal(ctl_dir=None, env=None, ancestors=None, proc="/proc", me=None):
    """Why control mode must not start here, or ''.

    Control mode is how the human approves agents, so it refuses (1) to take
    over from a dashboard that already holds a live lease (the human stops that
    one first; `ccdash` does), and (2) to start from inside Claude Code: its
    shells carry CLAUDECODE=1 and descend from the `claude` process. Both are
    raised bars, not walls: a program running as you can unset the variable and
    detach. See docs/guides/commands.md (Control mode, Security)."""
    from .control import read_json
    env = os.environ if env is None else env
    d = ctl_dir or os.path.join(attention.state_dir(), "control")
    lease = read_json(os.path.join(d, "lease"), 4096)
    if lease is not None and isinstance(lease.get("expires"), (int, float)) and lease["expires"] > time.time():
        pid = lease.get("pid")
        alive = False
        if isinstance(pid, int) and pid > 0 and pid != os.getpid():
            try:
                os.kill(pid, 0)
                alive = True
            except PermissionError:
                alive = True
            except OSError:
                alive = False
        if alive:
            return ("another cc-dash --control (pid %d) is running and holds the control lease. Stop it first "
                    "(Ctrl-C in its terminal; `ccdash --control` does this for you)." % pid)
    seen = [k for k in CLAUDE_ENV_SIGNALS if env.get(k)]
    if seen:
        return INSIDE_CLAUDE + " (its environment carries %s)." % ", ".join(seen)
    me = os.getpid() if me is None else me
    st = _proc_stat(me, proc)
    if ancestors is None:
        ancestors = _ancestors(st["ppid"], proc=proc) if st else _ancestors(proc=proc)
    for pid, comm, exe, argv in ancestors:
        if _is_claude(comm, exe, argv):
            return INSIDE_CLAUDE + " (process %d, %s, is among its parents)." % (pid, comm)
    if st is None:
        return ""
    # The session leader and the process-group leader outlive a double fork: if
    # either is, or descends from, the claude CLI, this was started inside it.
    for label, leader in (("session", st["sid"]), ("process-group", st["pgrp"])):
        if leader in (0, me):
            continue
        for pid, comm, exe, argv in _ancestors(leader, proc=proc):
            if _is_claude(comm, exe, argv):
                return INSIDE_CLAUDE + " (its %s leader descends from process %d, %s)." % (label, pid, comm)
    # A typical double-fork escape: re-parented to init or a user-manager subreaper
    # and no controlling terminal left.
    parent = _proc_stat(st["ppid"], proc) if st["ppid"] > 1 else None
    if st["tty"] == 0 and (st["ppid"] <= 1 or (parent or {}).get("comm") in ("systemd", "init")):
        return ("cc-dash --control must be started from your own terminal: this process was detached (re-parented "
                "to pid %d) and has no controlling terminal." % st["ppid"])
    return ""


def show_control_code(code, tty="/dev/tty"):
    """Write the control code to the controlling terminal and nowhere else: not
    stdout (often a log file, as with `ccdash`), not stderr, not a file. False
    when this process has no terminal."""
    import signal
    try:
        fd = os.open(tty, os.O_WRONLY | getattr(os, "O_NOCTTY", 0) | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        return False
    old = None
    try:
        if not os.isatty(fd):
            return False
        if hasattr(signal, "SIGTTOU"):        # a background write must not stop us (stty tostop)
            old = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
        os.write(fd, ("\r\n  cc-dash control code:  %s\r\n  Type it into the dashboard to approve, answer or message "
                      "(shown only here; never written to a file or a log).\r\n\r\n" % code).encode())
        return True
    except OSError:
        return False
    finally:
        if old is not None:
            signal.signal(signal.SIGTTOU, old)
        os.close(fd)


def main(argv=None):
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass
    a = parse_args(sys.argv[1:] if argv is None else argv)
    if not a.control:
        _ready(a)                   # nothing to show on the terminal
    root = config_dir()
    projects = os.path.join(root, "projects")
    if not os.path.isdir(projects):
        print(f"cc-dash: no transcripts at {tilde(projects)} (set CLAUDE_CONFIG_DIR if Claude Code keeps them elsewhere).",
              file=sys.stderr)
    store = Store(root=root, days=a.days, project=a.project)
    try:
        srv = make_server(a.port, store, a.verbose)
    except OSError as e:
        print(f"cc-dash: cannot listen on 127.0.0.1:{a.port}: {e.strerror}", file=sys.stderr)
        return 1
    if srv.port != a.port and a.port:
        print(f"cc-dash: port {a.port} is busy, using {srv.port}.")
    if a.control:
        why = control_refusal()
        if why:
            print("cc-dash: " + why, file=sys.stderr)
            srv.server_close()
            return 1
        # The control code goes to the terminal only. Without one there is no way
        # to hand it to the human, so there is no control mode.
        if not show_control_code(srv.auth.enable_control()):
            print("cc-dash: --control needs a terminal: the control code is shown there and never written to a file "
                  "or a log. Run cc-dash --control (or ccdash --control) from a terminal.", file=sys.stderr)
            srv.server_close()
            return 1
        try:
            # CC_DASH_CONTROL_TRANSPORT=hook: messages only through the hooks (no inbox socket), for testing.
            srv.control = Control(store, srv.port, wait=a.control_wait,
                                  sockets=os.environ.get("CC_DASH_CONTROL_TRANSPORT") != "hook")
            srv.control.start()
        except OSError as e:
            print(f"cc-dash: cannot start control mode: {e}", file=sys.stderr)
            srv.server_close()
            return 1
        print("cc-dash: control mode is on: approvals, answers, plans and messages from the dashboard, after the "
              "control code shown on the terminal is typed into it.")
        print(f"cc-dash: audit log {tilde(srv.control.audit_path)}")
        _ready(a)
    t0 = time.time()
    store.refresh(force=True)
    scan = store.last_scan
    print(f"cc-dash: read {scan['files']} transcripts ({scan['bytes'] / 1e6:.0f} MB) from "
          f"{tilde(projects)} in {time.time() - t0:.1f}s; {len(store.sessions)} sessions in the last "
          f"{a.days:g} days" + (f" matching '{a.project}'" if a.project else "") + ".")
    code = srv.auth.new_code()
    print("cc-dash: serving http://127.0.0.1:%d/  (Ctrl-C to stop). Open this link to pair a browser "
          "(read-only; it works once, for 60 s):\n  %s" % (srv.port, srv.auth.pair_url(code)))
    if sys.stdin is not None and sys.stdin.isatty():
        print("cc-dash: press Enter here for a new pairing link.")
        threading.Thread(target=_pair_on_enter, args=(srv,), daemon=True).start()
    threading.Thread(target=_expire_codes, args=(srv,), daemon=True).start()
    if not a.no_open:
        # Open a 0600 file that redirects to the link, never the link itself: a
        # URL handed to the browser lands in argv, which other users can read.
        try:
            # On Linux the page pairs itself (POST /api/pair-local, checked against
            # the socket's owner), so the plain URL is enough and works in sandboxed
            # (snap, Flatpak) browsers that cannot follow a file:// redirect.
            target = ("http://127.0.0.1:%d/" % srv.port) if local_pairing_supported() \
                else "file://" + srv.auth.pair_file(code)
            threading.Timer(0.3, lambda: webbrowser.open(target)).start()
        except OSError as e:
            print(f"cc-dash: could not open the browser ({e}); open the link above.", file=sys.stderr)
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\ncc-dash: stopped.")
    finally:
        if srv.control is not None:
            srv.control.stop()
        srv.server_close()
    return 0


def _pair_on_enter(srv):
    for _line in sys.stdin:
        print("cc-dash: new pairing link (works once, for 60 s):\n  " + srv.auth.pair_url())


def _expire_codes(srv):
    while True:
        time.sleep(10)
        srv.auth.cleanup()          # also deletes the pairing file of an expired code
