"""Who may read cc-dash: pairing and the per-launch token.

Transcripts hold secrets and 127.0.0.1 is reachable by every local user, so
reading needs a per-launch secret, not only the Host check. A cookie is no good
for that: cookies are scoped by host, not port, so a browser sends a
127.0.0.1 cookie to any other local user's server on another port. Instead:

  - At start cc-dash makes a pairing code (works once, for 60 s) and prints
    http://127.0.0.1:<port>/pair#code=<code> to its own terminal. To open the
    browser it writes that redirect into a 0600 file in a 0700 directory and
    opens the file:// path, so the code never appears in a process's argv
    (which other users can read in /proc). The file is deleted once the code is
    used or expires. Enter in cc-dash's terminal prints a new link.
  - The page reads the code from the URL fragment (never sent in a request
    line or a Referer), strips it with history.replaceState, and POSTs it once
    to /api/pair (JSON, same-origin). The answer is a token the page keeps in
    localStorage, which is scoped to scheme, host AND port: another port cannot
    read it. New tabs in the same browser share it.
  - Every /api/* request must carry it as X-CC-Dash-Token. Nothing re-issues it
    on any other credential. A restart forgets every token, and the page shows
    "Re-pair".
  - State changes (control mode) are additionally POSTs with Content-Type
    application/json, an Origin equal to the dashboard's own origin and, when
    the browser sends it, Sec-Fetch-Site: same-origin; plus the Host check.
  - The static shell (index.html, scripts, styles) carries no data and loads
    without the token, so it can pair or say "Re-pair".
"""
import hashlib
import hmac
import html
import os
import secrets
import stat
import threading
import time

PAIR_TTL = 60.0
HEADER = "X-CC-Dash-Token"


def _private_dir(d):
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
    return d


class Auth:
    def __init__(self, port, clock=time.time, pair_dir=None):
        self.port = port
        self.clock = clock
        self.pair_dir = pair_dir
        self.lock = threading.Lock()
        self.codes = {}            # pairing code -> [expiry, pairing file or None]
        self.tokens = {}           # token -> {"client"}

    # ── pairing codes ──────────────────────────────────────────────────────
    def new_code(self):
        code = secrets.token_urlsafe(24)
        with self.lock:
            self._cleanup()
            self.codes[code] = [self.clock() + PAIR_TTL, None]
        return code

    def pair_url(self, code=None):
        return "http://127.0.0.1:%d/pair#code=%s" % (self.port, code or self.new_code())

    def pair_file(self, code):
        """A 0600 file:// page that redirects to the pairing URL, so a browser can be
        opened on it without the code in any argv. Deleted once the code is spent."""
        from .attention import state_dir
        d = _private_dir(self.pair_dir or _browser_pair_dir() or os.path.join(state_dir(), "pair"))
        path = os.path.join(d, "pair-%s.html" % secrets.token_hex(8))
        url = html.escape(self.pair_url(code), quote=True)
        page = ('<!doctype html><meta charset="utf-8"><meta name="referrer" content="no-referrer">'
                '<meta http-equiv="refresh" content="0;url=%s"><title>cc-dash</title>'
                '<p>Opening cc-dash&hellip; <a href="%s">continue</a></p>' % (url, url))
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            os.write(fd, page.encode())
        finally:
            os.close(fd)
        with self.lock:
            if code in self.codes:
                self.codes[code][1] = path
        return path

    def _drop(self, entry):
        if entry and entry[1]:
            try:
                os.unlink(entry[1])
            except OSError:
                pass

    def _cleanup(self):
        now = self.clock()
        for c in [c for c, e in self.codes.items() if e[0] <= now]:
            self._drop(self.codes.pop(c))

    def cleanup(self):
        with self.lock:
            self._cleanup()

    def exchange(self, code):
        """Spend a pairing code; the new token, or None when unknown, used or expired."""
        with self.lock:
            entry = self.codes.pop(code, None) if isinstance(code, str) and code else None
            self._drop(entry)
            self._cleanup()
            if entry is None or entry[0] < self.clock():
                return None
            return self._issue()

    def exchange_local(self, client_addr):
        """Pair without a code when the connecting socket belongs to this user (Linux).

        The kernel's socket table says which user owns the browser's end of the
        connection; another local user is refused. Other websites cannot get here
        (Origin, Sec-Fetch-Site and Host are checked first, and the token is only
        readable by the dashboard's own origin). The new token, or None."""
        uid = peer_uid(client_addr, self.port)
        if uid is None or not hasattr(os, "getuid") or uid != os.getuid():
            return None
        with self.lock:
            return self._issue()

    def _issue(self):
        token = secrets.token_urlsafe(32)
        self.tokens[token] = {"client": hashlib.sha256(token.encode()).hexdigest()[:8]}
        return token

    # ── checking requests ──────────────────────────────────────────────────
    def session(self, headers):
        """{"client"} when the request carries this launch's token, else None."""
        tok = headers.get(HEADER) or ""
        if not tok:
            return None
        with self.lock:
            for known, s in self.tokens.items():
                if hmac.compare_digest(tok, known):
                    return s
        return None

    def client(self, headers):
        s = self.session(headers)
        return s["client"] if s else None

    @staticmethod
    def check_origin(headers, host_value):
        """'' when a POST comes from the dashboard's own page as JSON, else why not."""
        origin = (headers.get("Origin") or "").strip().lower()
        if not origin or origin != "http://" + host_value:
            return "origin"
        site = (headers.get("Sec-Fetch-Site") or "").strip().lower()
        if site and site != "same-origin":
            return "sec-fetch-site"
        ctype = (headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            return "content-type"
        return ""

    def check_write(self, headers, host_value):
        """'' when a POST may change state, else the reason it may not."""
        if self.session(headers) is None:
            return "token"
        return self.check_origin(headers, host_value)


def _browser_pair_dir():
    """A private folder the default browser's sandbox can read, or None.

    Snap browsers (Ubuntu's Firefox and Chromium) cannot open files under hidden
    folders in $HOME such as ~/.local, and Flatpak browsers see only their own
    data folder, so the pairing page goes into the browser's own per-user dir."""
    import re
    import subprocess
    try:
        out = subprocess.run(["xdg-settings", "get", "default-web-browser"], capture_output=True,
                             text=True, timeout=2).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    home = os.path.expanduser("~")
    m = re.fullmatch(r"([a-z0-9][a-z0-9-]*)_[\w.-]+\.desktop", out)
    if m and os.path.isdir(os.path.join(home, "snap", m.group(1))):
        return os.path.join(home, "snap", m.group(1), "common", "cc-dash-pair")
    m = re.fullmatch(r"([A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+){2,})\.desktop", out)
    if m and os.path.isdir(os.path.join(home, ".var", "app", m.group(1))):
        return os.path.join(home, ".var", "app", m.group(1), "cache", "cc-dash-pair")
    return None


def local_pairing_supported():
    return os.path.exists("/proc/net/tcp")


def _hex_ip4(ip):
    parts = ip.split(".")
    if len(parts) != 4:
        return None
    try:
        return "".join("%02X" % int(x) for x in reversed(parts))
    except ValueError:
        return None


def peer_uid(client_addr, server_port, tables=("/proc/net/tcp", "/proc/net/tcp6")):
    """The uid owning the client end of a 127.0.0.1 connection to server_port, or None.

    /proc/net/tcp lists every socket as `local_address rem_address st ... uid`;
    the browser's socket has local = its (ip, port) and remote = (127.0.0.1, server_port)."""
    try:
        ip, port = client_addr[0], int(client_addr[1])
    except (TypeError, ValueError, IndexError):
        return None
    if ip.startswith("::ffff:"):
        ip = ip[7:]
    want_ip = _hex_ip4(ip)
    if want_ip is None:
        return None
    want_local4 = "%s:%04X" % (want_ip, port)
    want_local6 = "0000000000000000FFFF0000%s:%04X" % (want_ip, port)
    want_rport = "%04X" % int(server_port)
    for table in tables:
        try:
            with open(table, encoding="ascii", errors="replace") as f:
                next(f, None)
                for line in f:
                    cols = line.split()
                    if len(cols) < 8:
                        continue
                    if cols[1].upper() in (want_local4, want_local6) and cols[2].upper().endswith(":" + want_rport):
                        try:
                            return int(cols[7])
                        except ValueError:
                            return None
        except OSError:
            continue
    return None
