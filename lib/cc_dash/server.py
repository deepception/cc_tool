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
an Origin equal to the dashboard's own. The hook does not use TCP: it talks to
cc-dash over <control dir>/hook.sock (HookServer), a Unix socket this process
makes in its 0700 control directory. There GET /api/control/verify is the hook's
question ("did the dashboard send this?") and POST /api/control/request hands
over the request it waits on; the kernel names the connecting process
(SO_PEERCRED, and SO_PEERPIDFD against pid reuse), which must be your user and,
for a request, the hook process the request names.
Without --control those paths are 404 and every POST but /api/pair is 405.

The POSTs, all through auth.check_write: /api/control/decide, message, cancel,
stop, and /api/control/selfheal, which starts one self-heal run (selfheal.py:
a headless `claude -p` that proposes harness patches and applies none; its
body only gives a number of days), and /api/control/selfheal/apply, which applies
one finished run's proposal and commits it after a typed confirm (SelfHeal.apply
re-checks everything). GET /api/selfheal and /api/selfheal/run
show its state and a run's validated proposals to any paired token.

/api/control/gate (gates.py): GET shows a work order's gate card, POST takes a human's
decision at that gate, after a typed confirm, and hands it to the cc-factory it starts.
"""
import argparse
import errno
import json
import os
import re
import socketserver
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import attention
from . import gates     # factory gates from the dashboard (/api/control/gate)
from . import graph     # the Factory floor (/api/graph)
from . import procs
from . import plans   # plan progress (/api/plans)
from . import selfheal  # self-heal runs (/api/selfheal, POST /api/control/selfheal)
from .auth import Auth, local_pairing_supported
from .control import MAX_BODY, MAX_REQUEST_BODY, Control
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
    "/gates.js": ("gates.js", "text/javascript; charset=utf-8"),   # gates.py: a work order's gate card
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
    # Factory floor (graph.py, /api/graph): the graph renderer, the floor view and its stage
    "/graph.js": ("graph.js", "text/javascript; charset=utf-8"),
    "/factory.js": ("factory.js", "text/javascript; charset=utf-8"),
    "/factory.css": ("factory.css", "text/css; charset=utf-8"),
    # self-heal (selfheal.py): the header control and its results drawer
    "/selfheal.js": ("selfheal.js", "text/javascript; charset=utf-8"),
    "/selfheal.css": ("selfheal.css", "text/css; charset=utf-8"),
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
        if getattr(self.server, "hook_channel", False):
            return self._hook("GET")
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
            if ctl is not None and path == "/api/control/gate":     # gates.py: one order's gate card
                return self._json(*self.server.gates.view(auth.client(self.headers), {k: q.get(k, "")[:200] for k in ("project", "order")},
                                                          sess.get("scope") == "control"))
            if path.startswith("/api/docs/"):
                return self._docs(path, q)
            if path == "/api/live":
                return self._json(200, store.live_payload())
            # A server object without a SelfHeal (a test harness's own) answers as one that never ran.
            sh = getattr(self.server, "selfheal", None)
            if path == "/api/selfheal":         # selfheal.py: the trigger's state, the last runs
                return self._json(200, sh.status() if sh is not None else {
                    "enabled": False, "budget_usd": selfheal.DEFAULT_BUDGET, "timeout_secs": selfheal.TIMEOUT_SECS,
                    "now": time.time(), "running": None, "last": None, "last_completed": None, "next_since": None})
            if path == "/api/selfheal/run":     # one run's validated proposals; the id is checked before any path
                return self._json(*(sh.run_view(q.get("id", "")[:64]) if sh is not None else
                                    (404, {"error": "No self-heal run with that id."})))
            # The windowed endpoints below take ?days= (Store.clamp_days: a bad value means the launch
            # window, never a 400); every other endpoint ignores it.
            days = q.get("days", "")[:16]
            if path == "/api/graph":            # graph.py: the Factory floor, one graph of everything live
                # A filter value compared with project labels, never a path.
                return self._json(200, graph.payload(store, q.get("project", "")[:200], all_projects=q.get("all") == "1",
                                                     days=days))
            if path == "/api/sessions":
                return self._json(200, store.sessions_payload(days))
            if path == "/api/overview":
                # A filter value compared with project labels, never a path.
                return self._json(200, store.overview_payload(q.get("project", ""), days))
            if path == "/api/routing":          # routing.py: who chose each agent's model and effort
                return self._json(200, store.routing_payload(q.get("project", ""), days))
            if path == "/api/session":
                p = store.session_payload(q.get("id", ""), days)
                return self._json(200, p) if p else self._json(404, {"error": "No session with that id in the lookback window."})
            if path == "/api/session/tree":     # the recursive agent tree (tree.py)
                p = store.tree_payload(q.get("id", ""), days)
                return self._json(200, p) if p else self._json(404, {"error": "No session with that id in the lookback window."})
            if path == "/api/agent":
                p = store.agent_payload(q.get("session", ""), q.get("id", ""), days)
                return self._json(200, p) if p else self._json(404, {"error": "No subagent with that id in this session."})
            if path in ("/api/plans", "/api/plans/detail"):   # plans.py: plan progress and milestones
                return self._plans(path, q, store, days)
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
    def _plans(self, path, q, store, days=""):
        """GET /api/plans?project=[&all=1][&days=] and /api/plans/detail?project=&path=[&days=]
        (plans.py checks the path)."""
        al = attention.alerts_payload(store)["alerts"]
        eng = plans.for_store(store)
        if path == "/api/plans":
            return self._json(200, eng.payload(q.get("project", ""), al, everything=q.get("all") == "1", days=days))
        p = eng.detail(q.get("project", "")[:200], q.get("path", "")[:1024], al, days=days)
        return self._json(200, p) if p else self._json(404, {"error": "No plan at that path in that project."})

    def _read_body(self, limit=MAX_BODY):
        """The JSON object body, or (status, error) when there is none."""
        try:
            n = int(self.headers.get("Content-Length") or "-1")
        except ValueError:
            n = -1
        if n < 0 or n > limit:
            return None, (413 if n > limit else 411, {"error": "Body missing or too large."})
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

    # ── the hook's channel: <control dir>/hook.sock (HookServer), never TCP ──
    def _hook(self, method):
        """GET /api/control/verify and POST /api/control/request from a
        dash-control.py hook, over the Unix socket cc-dash made in its 0700
        control directory (procs.bind_private_socket). The kernel says who
        connected (SO_PEERCRED, fixed when the connection was made): it must be
        your user, and send the lease's per-launch key (X-CC-Dash-Lease). Every
        answer carries X-CC-Dash-Proof, an HMAC with that key over the hook's
        nonce and the body, so the answer is bound to the key and to this very
        exchange, on top of the hook's own SO_PEERCRED check that it reached the
        lease's pid. A query never consumes a record."""
        import hmac
        ctl = self.server.control
        parts = urlsplit(self.path)
        nonce = self.headers.get("X-CC-Dash-Nonce") or ""
        key = self.headers.get("X-CC-Dash-Lease") or ""

        def answer(code, obj):
            data = json.dumps(obj, separators=(",", ":"), default=str).encode()
            proof = hmac.new(ctl.key.encode(), nonce.encode() + b"\n" + data, "sha256").hexdigest()
            self._send(code, data, "application/json; charset=utf-8", extra={"X-CC-Dash-Proof": proof})

        if not self._host_ok() or ctl is None:
            return self._deny(421, "Misdirected request.")
        if not key or not hmac.compare_digest(key, ctl.key) or not re.match(r"^[0-9a-f]{32,128}$", nonce):
            return self._json(403, {"ok": False})
        pid, uid = procs.peer_cred(self.request)
        if uid != os.getuid():
            return answer(403, {"ok": False})
        if method == "GET" and parts.path == "/api/control/verify":
            code, out = ctl.verify({k: v[0] for k, v in parse_qs(parts.query).items()})
            return answer(code, out)
        if method == "POST" and parts.path == "/api/control/request":
            body, err = self._read_body(MAX_REQUEST_BODY)
            if err:
                return answer(*err)
            pidfd = procs.peer_pidfd(self.request)
            try:
                named = body.get("pid")
                if not isinstance(named, int) or isinstance(named, bool) or pid is None or named != pid:
                    peer, why = None, ("the process that connected (%s) is not process %s, which the request names"
                                       % ("pid %d" % pid if pid else "unknown", named))
                else:
                    # And that process alone holds the client end (sock_diag peer inode, one /proc/*/fd
                    # scan): a socket shared with another process does not say who wrote the request.
                    why = procs.peer_holder_problem(self.request, pid)
                    peer = None if why else pid
                # With a pidfd (Linux >= 6.5): the process checked is the one that connected, still alive,
                # so its pid was not reused by another process meanwhile.
                still = (lambda: procs.pidfd_pid(pidfd) == pid) if pidfd is not None else None
                code, out = ctl.receive_request(body, peer_pid=peer, peer_why=why, still=still)
            finally:
                if pidfd is not None:
                    os.close(pidfd)
            return answer(code, out)
        return answer(404, {"ok": False})

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

    POSTS = ("/api/control/decide", "/api/control/message", "/api/control/cancel", "/api/control/stop",
             "/api/control/selfheal", "/api/control/selfheal/apply", "/api/control/gate")

    def do_POST(self):
        if getattr(self.server, "hook_channel", False):
            return self._hook("POST")
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
        if any(auth.mentions_code(t) for t in _typed_texts(body)):
            # Never echoed, logged with the text or written to a file: the code stays on the terminal.
            ctl.log(client, "refused", kind=path.rsplit("/", 1)[-1], session=body.get("session") if isinstance(body.get("session"), str) else None,
                    result="text containing the control code was not sent")
            return self._json(400, {"error": "That text contains your control code. The code only goes into the unlock "
                                             "field; nothing else on this page or from an agent should ever ask for it. "
                                             "Nothing was sent.", "code_in_text": True})
        try:
            if path == "/api/control/decide":
                code, out = ctl.decide(client, body)
            elif path == "/api/control/message":
                code, out = ctl.message(client, body)
            elif path == "/api/control/stop":
                code, out = ctl.stop_turn(client, body)
            elif path == "/api/control/selfheal":
                sh = getattr(self.server, "selfheal", None)
                code, out = sh.trigger(client, body) if sh is not None else (404, {"error": "Self-heal is not available."})
            elif path == "/api/control/selfheal/apply":     # one proposal's checked patch, applied and committed
                sh = getattr(self.server, "selfheal", None)
                code, out = sh.apply(client, body) if sh is not None else (404, {"error": "Self-heal is not available."})
            elif path == "/api/control/gate":
                code, out = self.server.gates.decide(client, body)
            else:
                code, out = ctl.cancel(client, body)
        except Exception as e:  # noqa: BLE001 - report, never take the server down
            code, out = 500, {"error": f"{type(e).__name__}: {e}"}
        return self._json(code, out)

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _no_body


def _typed_texts(body):
    """Every piece of text a control POST carries that a person typed: a deny
    reason, a message, a gate's note, each answer (and the question it answers)."""
    out = [body.get(k) for k in ("message", "text", "note") if isinstance(body.get(k), str)]
    ans = body.get("answers")
    if isinstance(ans, dict):
        out += [x for kv in ans.items() for x in kv if isinstance(x, str)]
    return out


class DashServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port, store, verbose=False, budget=selfheal.DEFAULT_BUDGET):
        super().__init__(("127.0.0.1", port), Handler)
        self.store = store
        self.verbose = verbose
        self.selfheal = selfheal.SelfHeal(store, budget=budget, writable=False)
        self.control = None
        real = self.server_address[1]
        self.port = real
        self.allowed_hosts = {f"127.0.0.1:{real}", f"localhost:{real}"}
        self.auth = Auth(real)
        self.docs = Docs(store)

    @property
    def control(self):
        return self._control

    @control.setter
    def control(self, ctl):
        # Self-heal writes its state only in control mode, where its trigger exists, and audits there.
        self._control = ctl
        self.selfheal.writable = ctl is not None
        self.selfheal.control = ctl
        self.gates = gates.Gates(ctl, self.docs) if ctl is not None else None


HOOK_HOST = "cc-dash-hook"


class HookServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """The hook's channel: HTTP over <control dir>/hook.sock, serving only
    Handler._hook (verify and request). Made by this process in its 0700
    control directory (procs.bind_private_socket), 0600, removed on close."""
    daemon_threads = True
    hook_channel = True

    def __init__(self, ctl):
        path = os.path.join(ctl.dir, procs.HOOK_SOCK)
        socketserver.UnixStreamServer.__init__(self, path, Handler, bind_and_activate=False)
        self.socket.close()
        self.socket, self.ident = procs.bind_private_socket(ctl.dir)
        self.server_address = path
        self.control, self.store, self.auth, self.verbose = ctl, ctl.store, None, False
        self.allowed_hosts = {HOOK_HOST}

    def server_close(self):
        super().server_close()
        try:                                   # ours only: the very file this process bound
            st = os.lstat(self.server_address)
            if (st.st_dev, st.st_ino) == self.ident:
                os.unlink(self.server_address)
        except OSError:
            pass


def start_hook_channel(ctl):
    """Make <control dir>/hook.sock, serve it on a thread, and name it in the
    lease (ctl.hook_sock); returns the HookServer."""
    from .control import ensure_private_dir
    ensure_private_dir(ctl.dir)
    hs = HookServer(ctl)
    ctl.hook_sock = hs.server_address
    threading.Thread(target=hs.serve_forever, kwargs={"poll_interval": 0.2}, name="cc-dash-hook", daemon=True).start()
    return hs


def make_server(port, store, verbose=False, fallback=True, budget=selfheal.DEFAULT_BUDGET):
    """Bind 127.0.0.1:port; when it is taken and fallback is on, take any free port."""
    try:
        return DashServer(port, store, verbose, budget)
    except OSError as e:
        if not fallback or e.errno not in (errno.EADDRINUSE, errno.EACCES):
            raise
        return DashServer(0, store, verbose, budget)


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
    p.add_argument("--days", type=float, default=7,
                   help="starting lookback window in days (default 7); the page can switch to 1, 3, 7, 14 or 30")
    p.add_argument("--project", default="", help="only sessions whose working directory contains this text")
    p.add_argument("--verbose", action="store_true", help="log each request to stderr")
    p.add_argument("--control", action="store_true",
                   help="control mode: approve or deny permission prompts, answer questions and plans, and send "
                        "messages to sessions from the dashboard (needs the dash-control.py hook; pair with the "
                        "printed link)")
    p.add_argument("--control-wait", type=float, default=300, metavar="SECS",
                   help="control mode: how long a waiting permission hook waits for the dashboard (default 300)")
    p.add_argument("--selfheal-budget", type=float, default=selfheal.DEFAULT_BUDGET, metavar="USD",
                   help="control mode: the spending cap of one self-heal run (default %g, %g-%g)"
                        % (selfheal.DEFAULT_BUDGET, selfheal.BUDGET_MIN, selfheal.BUDGET_MAX))
    p.add_argument("--ready-fd", type=int, default=None, metavar="FD", help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    if not (5 <= a.control_wait <= 590):
        p.error("--control-wait must be 5-590 seconds")
    if not (selfheal.BUDGET_MIN <= a.selfheal_budget <= selfheal.BUDGET_MAX):     # NaN and inf fail it too
        p.error("--selfheal-budget must be %g-%g dollars" % (selfheal.BUDGET_MIN, selfheal.BUDGET_MAX))
    if not 0 < a.days < float("inf"):       # NaN fails every comparison: no NaN or infinite cutoff
        p.error("--days must be a positive number")
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


# Process checks live in templates/hooks/cc_procs.py (procs.py loads it), the same file
# cc-setup copies beside the control hook, so both judge a launch the same way.
CLAUDE_ENV_SIGNALS = procs.CLAUDE_ENV_SIGNALS
INSIDE_CLAUDE = "cc-dash --control must be started from your own terminal, not from inside a Claude Code session"
NO_PROC = ("--control needs /proc to identify the dashboard and its hooks, so control mode works on Linux only. "
           "Run cc-dash without --control for the read-only dashboard; permission prompts stay in the terminal.")
_proc_stat = procs.proc_stat
_ancestors = procs.ancestors
_is_claude = procs.is_claude
# This checkout: lib/cc_dash/server.py -> the repo (bin/cc-dash finds lib/ the same way).
CC_TOOL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))


def read_manifest(path, limit=1 << 20):
    """(manifest dict, '') read the way the control hook reads its own: opened
    without following a symlink, a plain file we own that nobody else can write,
    under `limit` bytes; else (None, why not)."""
    import stat as stat_mod
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    except FileNotFoundError:
        return None, ""
    except OSError as e:
        return None, "cannot be opened safely (%s)" % (e.strerror or type(e).__name__)
    try:
        st = os.fstat(fd)
        if not stat_mod.S_ISREG(st.st_mode):
            return None, "is not a plain file"
        if st.st_uid != os.getuid():
            return None, "is not yours"
        if st.st_mode & 0o022:
            return None, "can be written by others"
        if st.st_size >= limit:
            return None, "is too large"
        raw = os.read(fd, limit)
    except OSError as e:
        return None, "cannot be read (%s)" % (e.strerror or type(e).__name__)
    finally:
        os.close(fd)
    try:
        m = json.loads(raw)
    except ValueError:
        return None, "is not JSON"
    return (m, "") if isinstance(m, dict) else (None, "is not a JSON object")


def recorded_terminals(state=None, unsafe=None):
    """The terminal hosts cc-setup recorded for any project it registered (the
    terminal_hosts of each <project>/.claude/hooks/cc_dash_manifest.json listed
    in projects.json): you run cc-dash from one terminal for many projects, and
    cc-dash itself belongs to none of them. Each manifest is read as the hook
    reads its own (read_manifest); an unsafe one is skipped and (path, why) is
    appended to `unsafe`. Each project's hook checks its own protected record,
    which is what binds; this is cc-dash's own first look."""
    from .control import read_json
    state = state or attention.state_dir()
    reg = read_json(os.path.join(state, "projects.json"), 1 << 20) or {}
    out = []
    for proj in reg.get("projects") or []:
        if not isinstance(proj, str) or not os.path.isabs(proj):
            continue
        path = os.path.join(proj, ".claude", "hooks", procs.MANIFEST_NAME)
        m, why = read_manifest(path)
        if m is None:
            if why and unsafe is not None:
                unsafe.append((path, why))
            continue
        hosts = m.get("terminal_hosts")
        out += [h for h in hosts if isinstance(h, dict)] if isinstance(hosts, list) else []
    return procs.merge_terminals(out, [])


def control_refusal(ctl_dir=None, env=None, ancestors=None, proc="/proc", me=None, root=None, note=None, terminals=None,
                    unsafe=None):
    """Why control mode must not start here, or ''.

    Control mode is how the human approves agents, so it refuses (1) to take
    over from a dashboard that already holds a live lease (the human stops that
    one first; `ccdash` does), and (2) to start from inside Claude Code: its
    shells carry CLAUDECODE=1 and descend from the `claude` process. A lease
    counts as live only when its pid passes the same program check the hook
    applies (procs.dashboard_problem: cc-dash from this checkout, launched as
    cc-dash launches itself, from outside Claude Code); any other lease is
    removed, with a note (`note`, a callable, gets the text). Both are raised
    bars, not walls: a program running as you can unset the variable and
    detach. See docs/guides/commands.md (Control mode, Security).

    Before either, it refuses where there is no /proc (procs.have_proc; NO_PROC):
    nothing could then tell this process, a lease's pid or a hook from any
    other program of yours, so control mode is Linux-only. `proc` fakes the
    process entries for tests, not that check."""
    from .control import read_json, unlink
    if not procs.have_proc():
        return NO_PROC
    env = os.environ if env is None else env
    d = ctl_dir or os.path.join(attention.state_dir(), "control")
    # The terminals the holder of this process's terminal may be (procs.terminal_problem).
    terminals = recorded_terminals(os.path.dirname(d), unsafe) if terminals is None else terminals
    lease_path = os.path.join(d, "lease")
    lease = read_json(lease_path, 4096)
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
            why = procs.dashboard_problem(pid, root or CC_TOOL_DIR, proc, terminals=terminals)
            if not why:
                return ("another cc-dash --control (pid %d) is running and holds the control lease. Stop it first "
                        "(Ctrl-C in its terminal; `ccdash --control` does this for you)." % pid)
            unlink(lease_path)
            if note is not None:
                note("cc-dash: removed a control lease that named pid %d, which is not a cc-dash dashboard (%s). "
                     "Something running as you wrote it; see the audit log once control mode is on." % (pid, why))
    me = os.getpid() if me is None else me
    st = _proc_stat(me, proc)
    if st is None:
        # Its own process cannot be inspected, so nothing shows how it was started: refused. The
        # environment and the given parents still pick the plainer message when they already tell.
        why = procs.claude_env(env) or next(("process %d, %s, is among its parents" % (p, comm)
                                             for p, comm, exe, argv in ancestors or [] if _is_claude(comm, exe, argv)), "")
        return (INSIDE_CLAUDE + " (%s)." % why) if why else \
            "cc-dash --control must be started from your own terminal: its own process (%d) cannot be inspected." % me
    if ancestors is None:
        ancestors, complete = procs.chain(st["ppid"], proc=proc)
        if not complete:
            return ("cc-dash --control must be started from your own terminal: its parent chain cannot be read "
                    "to the end (a process on the way is gone or hidden).")
    # The rules the hook applies to the dashboard and cc-setup to a terminal it records
    # (procs.origin_problem), in the same order: Claude Code's variables; the claude CLI
    # among its parents or its session and process-group leaders' parents (they outlive
    # a double fork; one that cannot be inspected counts); a terminal driver (script,
    # expect ...) that was itself detached; then whoever holds the master side of this
    # terminal, who reads the control code: one of your recorded terminals, and not
    # Claude Code or something it started; a fresh session after a re-parent; detached
    # with no controlling terminal left.
    inside, why = procs.origin_problem(me, proc, env=env, parents=ancestors,
                                       terminal=lambda: procs.terminal_problem(me, proc, terminals=terminals))
    if inside:
        return INSIDE_CLAUDE + " (%s)." % why
    if why:
        return "cc-dash --control must be started from your own terminal: %s." % why
    return ""


def own_process_group():
    """Lead a process group of our own when none of stdin, stdout and stderr is
    a terminal (so leaving the terminal's foreground group cannot stop cc-dash
    on a read or a write; the control code goes to /dev/tty with SIGTTOU
    ignored): `ccdash` starts cc-dash in a pipeline whose group leader, a
    subshell, exits at once, and the control hook refuses a dashboard whose
    process-group leader it cannot inspect. cc-dash run directly in a terminal
    already leads its own group, or follows a wrapper that is still alive."""
    try:
        if not any(os.isatty(fd) for fd in (0, 1, 2)) and os.getpgrp() != os.getpid():
            os.setpgid(0, 0)
    except OSError:
        pass


def launch_note():
    """A warning for the terminal when this dashboard is not in the form the
    control hook trusts (procs.dashboard_problem): `<interpreter> -I
    <checkout>/bin/cc-dash`, bin/cc-dash's own re-exec. Whether the interpreter
    is the one cc-setup recorded only the hook can tell (the record is per
    project, in its protected .claude/hooks/). '' when it is, or when there is
    no /proc to tell."""
    if not os.path.exists("/proc/self/exe"):
        return ""
    exe = os.path.realpath("/proc/self/exe")
    want = os.path.join(CC_TOOL_DIR, "bin", "cc-dash")
    if (procs.cmdline(os.getpid()) or [])[:3] != [exe, "-I", want]:
        return ("cc-dash: warning: started as `%s`, not the way bin/cc-dash starts itself (%s -I %s); the control hook "
                "will not trust it. Start it with cc-dash or ccdash." % (" ".join((procs.cmdline(os.getpid()) or [])[:3]), exe, want))
    return ""


def tty_write(text, tty="/dev/tty"):
    """Write text to the controlling terminal and nowhere else (the control code's
    channel). False when this process has no terminal."""
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
        os.write(fd, text.encode())
        return True
    except OSError:
        return False
    finally:
        if old is not None:
            signal.signal(signal.SIGTTOU, old)
        os.close(fd)


def show_control_code(code, tty="/dev/tty"):
    """Write the control code to the controlling terminal and nowhere else: not
    stdout (often a log file, as with `ccdash`), not stderr, not a file. False
    when this process has no terminal."""
    return tty_write("\r\n  cc-dash control code:  %s\r\n  Type it into the dashboard to approve, answer or message "
                     "(shown only here; never written to a file or a log).\r\n\r\n" % code, tty)


def refuse_control(a, text, tty="/dev/tty"):
    """Control mode is refused: the reason goes to stderr (the log, with `ccdash`)
    and to the terminal, where the control code would have been, so `ccdash
    --control` does not look like it silently did nothing; then --ready-fd gets
    `refused` and is closed, so the `ccdash` function returns after it is shown
    without saying the dashboard is opening."""
    print(text, file=sys.stderr)
    tty_write("\r\n  %s\r\n\r\n" % text.replace("\n", "\r\n  "), tty)
    if a.ready_fd is not None:
        try:
            os.write(a.ready_fd, b"refused")
        except OSError:
            pass
    _ready(a)


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
    hook_srv = None
    try:
        srv = make_server(a.port, store, a.verbose, budget=a.selfheal_budget)
    except OSError as e:
        print(f"cc-dash: cannot listen on 127.0.0.1:{a.port}: {e.strerror}", file=sys.stderr)
        return 1
    if srv.port != a.port and a.port:
        print(f"cc-dash: port {a.port} is busy, using {srv.port}.")
    if a.control:
        own_process_group()
        unsafe = []           # project manifests skipped for the terminal list: noted now, audited once control starts
        why = control_refusal(note=lambda text: print(text, file=sys.stderr), unsafe=unsafe)
        for path, reason in unsafe:
            print("cc-dash: ignored %s for the recorded terminals: it %s." % (tilde(path), reason), file=sys.stderr)
        if why:
            refuse_control(a, "cc-dash: " + why)
            srv.server_close()
            return 1
        # The control code goes to the terminal only. Without one there is no way
        # to hand it to the human, so there is no control mode.
        if not show_control_code(srv.auth.enable_control()):
            refuse_control(a, "cc-dash: --control needs a terminal: the control code is shown there and never written to "
                              "a file or a log. Run cc-dash --control (or ccdash --control) from a terminal.")
            srv.server_close()
            return 1
        try:
            # CC_DASH_CONTROL_TRANSPORT=hook: messages only through the hooks (no inbox socket), for testing.
            srv.control = Control(store, srv.port, wait=a.control_wait,
                                  sockets=os.environ.get("CC_DASH_CONTROL_TRANSPORT") != "hook")
            hook_srv = start_hook_channel(srv.control)
            srv.control.start()
            for path, reason in unsafe:
                srv.control.log(None, "refused", kind="manifest", item=path,
                                result="a project manifest was ignored for the recorded terminals: it %s" % reason)
        except OSError as e:
            refuse_control(a, f"cc-dash: cannot start control mode: {e}")
            srv.server_close()
            return 1
        print("cc-dash: control mode is on: approvals, answers, plans and messages from the dashboard, after the "
              "control code shown on the terminal is typed into it.")
        print(f"cc-dash: audit log {tilde(srv.control.audit_path)}")
        try:
            srv.selfheal.status()     # now writable: settle a run a previous launch left (gone, or overdue)
        except OSError as e:
            print(f"cc-dash: self-heal state at {tilde(srv.selfheal.base)} is unusable ({e}); self-heal will not run "
                  "until that is fixed.", file=sys.stderr)
        note = launch_note()
        if note:
            print(note, file=sys.stderr)
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
        import signal
        # SIGTERM (`ccdash` stops the previous dashboard with pkill) ends it like Ctrl-C, so the
        # lease and the hook socket are removed rather than left behind.
        signal.signal(signal.SIGTERM, _interrupt)
    except (ValueError, OSError):
        pass
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\ncc-dash: stopped.")
    finally:
        if srv.control is not None:
            srv.control.stop()
        if hook_srv is not None:
            hook_srv.shutdown()
            hook_srv.server_close()
        srv.server_close()
    return 0


def _interrupt(*_a):
    raise KeyboardInterrupt


def _pair_on_enter(srv):
    for _line in sys.stdin:
        print("cc-dash: new pairing link (works once, for 60 s):\n  " + srv.auth.pair_url())


def _expire_codes(srv):
    while True:
        time.sleep(10)
        srv.auth.cleanup()          # also deletes the pairing file of an expired code
