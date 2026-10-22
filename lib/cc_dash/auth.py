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

Two scopes. Every token from pairing (the link, or same-user pairing on Linux)
is READ-ONLY: every GET, nothing that changes state. "Same user" is not "the
human": your agents run as you too, and anything that is written to a file
(the log holds the pairing link) can be read by them. Control mode's writes
(decide, message, cancel, stop) need a CONTROL token, which only the control
code buys:

  - With --control, cc-dash makes a per-launch control code (three groups of
    four from an alphabet with no 0/O/1/I/L, about 59 bits) and writes it to
    its controlling terminal (/dev/tty) only: never to stdout or the log, never
    to a file, never into argv or the environment. No terminal, no control mode.
  - The page POSTs it to /api/control/unlock with its read token and gets a
    control token, which it keeps in memory only (localStorage is a file in the
    browser profile that a same-user process can read). A reload asks for the
    code again; the code is valid for this launch and never after it.
  - Wrong codes: compared in constant time; each read token has its own small
    budget (one try a second, 5 wrong tries, then that token can no longer try;
    a reload pairs a fresh one). Nothing is shared between tokens, so another
    principal (an agent with its own pair-local token) can neither lock the
    human out nor use up the human's tries: the right code from a token that
    has not spent its own budget is always accepted. Same-user pairing is not
    rate-limited (a cap would let an agent lock the human's browser out); fresh
    tokens buy an agent 5 tries each, and the code's ~59 bits put guessing far
    beyond any launch's life even at thousands of tokens a second. The token
    table is bounded: past MAX_READ_TOKENS the oldest read tokens are dropped
    (a dropped page pairs itself again). The code is valid for this launch only.
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
CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"   # no 0/O, 1/I/L: read off a terminal and typed
CODE_GROUPS, CODE_GROUP_LEN = 3, 4
CODE_MAX_TRIES = 5          # wrong control codes per read token, then that token cannot try again
CODE_MIN_GAP = 1.0          # seconds between two tries from one token
MAX_READ_TOKENS = 5000      # read tokens kept; the oldest go first (control tokens are never dropped)


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
        self.tokens = {}           # token -> {"client", "scope": "read" | "control"}
        self.control_code = None   # this launch's control code (memory only), None without --control
        self.control_unlocks = 0

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
            return self._issue("read")

    def exchange_local(self, client_addr):
        """Pair without a code when the connecting socket belongs to this user (Linux).

        The kernel's socket table says which user owns the browser's end of the
        connection; another local user is refused. Other websites cannot get here
        (Origin, Sec-Fetch-Site and Host are checked first, and the token is only
        readable by the dashboard's own origin). The new token, or None."""
        if not same_user(client_addr, self.port):
            return None
        with self.lock:
            return self._issue("read")      # same uid is not the human: never control

    def _issue(self, scope):
        token = secrets.token_urlsafe(32)
        self.tokens[token] = {"client": hashlib.sha256(token.encode()).hexdigest()[:8], "scope": scope}
        if scope == "read":
            reads = [t for t, s in self.tokens.items() if s.get("scope") == "read"]
            for old in reads[:max(0, len(reads) - MAX_READ_TOKENS)]:     # dicts keep insertion order
                del self.tokens[old]
        return token

    # ── the control code ───────────────────────────────────────────────────
    def enable_control(self):
        """Make this launch's control code. The caller shows it on the terminal only."""
        code = "-".join("".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_GROUP_LEN))
                        for _ in range(CODE_GROUPS))
        with self.lock:
            self.control_code = code.replace("-", "")
        return code

    def mentions_code(self, text):
        """True when text carries this launch's control code: its letters and
        digits, case-folded, contain the code's (separators and spacing do not
        hide it). The code only ever goes into the unlock field; a card or a
        message that gets you to type it elsewhere is phishing for it."""
        code = self.control_code
        if not code or not isinstance(text, str):
            return False
        return code in "".join(ch for ch in text.upper() if ch.isalnum())

    def control_state(self, headers):
        """This token's standing with the control code: {"locked", "tries_left"}."""
        s = self.session(headers) or {}
        wrong = s.get("wrong", 0)
        return {"locked": self.control_code is not None and wrong >= CODE_MAX_TRIES,
                "tries_left": max(0, CODE_MAX_TRIES - wrong)}

    def unlock(self, headers, code):
        """(http status, body) for POST /api/control/unlock {"code"}: a control token
        for the right code. Needs a paired (read) token; wrong tries lock the code."""
        sess = self.session(headers)
        if sess is None:
            return 401, {"error": "Not paired.", "repair": True}
        norm = "".join(ch for ch in code.upper() if ch.isalnum()) if isinstance(code, str) else ""
        if not norm or len(norm) > 64:
            return 400, {"error": "Type the control code shown in the terminal where cc-dash --control runs."}
        locked = {"error": "Too many wrong codes from this tab. Reload the page to pair again, then type the code "
                           "shown in the terminal.", "locked": True}
        with self.lock:
            if self.control_code is None:
                return 404, {"error": "Control mode is off."}
            if sess.get("wrong", 0) >= CODE_MAX_TRIES:
                return 423, locked
            # Only this token's own budget applies; nothing shared stands before the
            # comparison, so a correct code is never refused because of someone else.
            now = self.clock()
            if now - sess.get("last", -1e18) < CODE_MIN_GAP:
                return 429, {"error": "One try a second; try again."}
            sess["last"] = now
            if not hmac.compare_digest(norm.encode(), self.control_code.encode()):
                sess["wrong"] = sess.get("wrong", 0) + 1
                left = CODE_MAX_TRIES - sess["wrong"]
                if left <= 0:
                    return 423, locked
                return 403, {"error": "That is not the control code (%d %s left)." % (left, "try" if left == 1 else "tries"),
                             "tries_left": left}
            self.control_unlocks += 1
            return 200, {"token": self._issue("control"), "scope": "control"}

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

    def scope(self, headers):
        s = self.session(headers)
        return s["scope"] if s else None

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
        """'' when a POST may change state, else the reason it may not: "token"
        (not paired), "scope" (a read-only token), or a bad origin header."""
        s = self.session(headers)
        if s is None:
            return "token"
        if s.get("scope") != "control":
            return "scope"
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


def same_user(client_addr, server_port):
    """True when the kernel says the connecting socket belongs to this user (Linux)."""
    uid = peer_uid(client_addr, server_port)
    return uid is not None and hasattr(os, "getuid") and uid == os.getuid()


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
