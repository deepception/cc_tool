"""The cc-dash HTTP server: 127.0.0.1 only, GET only, fixed asset map.

Transcripts hold secrets (commands, file contents, tokens pasted into
prompts), so the server
  - binds to 127.0.0.1 and never to a public interface,
  - answers only when the Host header is 127.0.0.1:<port> or localhost:<port>
    (a DNS-rebinding page on another origin sends its own hostname),
  - serves a fixed set of named files from web/ and nothing else; the request path is
    looked up in a dict, never joined onto a directory,
  - sends no CORS headers and a CSP that allows only its own origin.
"""
import argparse
import errno
import json
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import attention
from .store import Store, config_dir
from .parser import tilde

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/dom.js": ("dom.js", "text/javascript; charset=utf-8"),
    "/timeline.js": ("timeline.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    # alerts (attention.py): toast stack, alert drawer, title badge, desktop notifications
    "/alerts.js": ("alerts.js", "text/javascript; charset=utf-8"),
    "/alerts.css": ("alerts.css", "text/css; charset=utf-8"),
    "/alerts-worker.js": ("alerts-worker.js", "text/javascript; charset=utf-8"),
}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
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

    def _send(self, code, body, ctype, extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", CSP)
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
        try:
            if path in ASSETS:
                name, ctype = ASSETS[path]
                with open(os.path.join(WEB, name), "rb") as fh:
                    return self._send(200, fh.read(), ctype)
            if path == "/api/meta":
                return self._json(200, store.meta())
            if path == "/api/alerts":  # open "needs you" alerts, see attention.py
                return self._json(200, attention.alerts_payload(store))
            if path == "/api/live":
                return self._json(200, store.live_payload())
            if path == "/api/sessions":
                return self._json(200, store.sessions_payload())
            if path == "/api/overview":
                # A filter value compared with project labels, never a path.
                return self._json(200, store.overview_payload(q.get("project", "")))
            if path == "/api/session":
                p = store.session_payload(q.get("id", ""))
                return self._json(200, p) if p else self._json(404, {"error": "No session with that id in the lookback window."})
            if path == "/api/agent":
                p = store.agent_payload(q.get("session", ""), q.get("id", ""))
                return self._json(200, p) if p else self._json(404, {"error": "No subagent with that id in this session."})
        except BrokenPipeError:
            return None
        except Exception as e:  # noqa: BLE001 - one bad transcript must not take the server down
            if getattr(self.server, "verbose", False):
                import traceback
                traceback.print_exc()
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})
        return self._deny(404, "Not found.")

    def _no_body(self):
        if not self._host_ok():
            return self._deny(421, "Misdirected request.")
        self._deny(405, "cc-dash is read-only.")

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _no_body


class DashServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port, store, verbose=False):
        super().__init__(("127.0.0.1", port), Handler)
        self.store = store
        self.verbose = verbose
        real = self.server_address[1]
        self.port = real
        self.allowed_hosts = {f"127.0.0.1:{real}", f"localhost:{real}"}


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
        description="Local, read-only dashboard of your Claude Code sessions, subagents, tokens, "
                    "cost and cc_tool guard catches. Serves on 127.0.0.1 until Ctrl-C.")
    p.add_argument("--port", type=int, default=DEFAULT_PORT,
                   help=f"port to listen on (default {DEFAULT_PORT}; a free port is used if it is taken)")
    p.add_argument("--no-open", action="store_true", help="do not open the browser")
    p.add_argument("--days", type=float, default=7, help="lookback window in days (default 7)")
    p.add_argument("--project", default="", help="only sessions whose working directory contains this text")
    p.add_argument("--verbose", action="store_true", help="log each request to stderr")
    a = p.parse_args(argv)
    if a.days <= 0:
        p.error("--days must be positive")
    if not (0 <= a.port <= 65535):
        p.error("--port must be 0-65535")
    return a


def main(argv=None):
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass
    a = parse_args(sys.argv[1:] if argv is None else argv)
    root = config_dir()
    projects = os.path.join(root, "projects")
    if not os.path.isdir(projects):
        print(f"cc-dash: no transcripts at {tilde(projects)} (set CLAUDE_CONFIG_DIR if Claude Code keeps them elsewhere).",
              file=sys.stderr)
    store = Store(root=root, days=a.days, project=a.project)
    t0 = time.time()
    store.refresh(force=True)
    scan = store.last_scan
    print(f"cc-dash: read {scan['files']} transcripts ({scan['bytes'] / 1e6:.0f} MB) from "
          f"{tilde(projects)} in {time.time() - t0:.1f}s; {len(store.sessions)} sessions in the last "
          f"{a.days:g} days" + (f" matching '{a.project}'" if a.project else "") + ".")
    try:
        srv = make_server(a.port, store, a.verbose)
    except OSError as e:
        print(f"cc-dash: cannot listen on 127.0.0.1:{a.port}: {e.strerror}", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{srv.port}/"
    if srv.port != a.port and a.port:
        print(f"cc-dash: port {a.port} is busy, using {srv.port}.")
    print(f"cc-dash: serving {url}  (Ctrl-C to stop)")
    if not a.no_open:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\ncc-dash: stopped.")
    finally:
        srv.server_close()
    return 0
