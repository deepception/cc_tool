"""The Docs view's server side: a read-only, per-project viewer of Markdown and HTML.

Every endpoint is a GET behind the pairing token (server.py). What may be read:

  - the project is one of the resolved projects (store.resolve_projects():
    worktrees fold into their repo, and a project's docs come from that main
    checkout). The client sends a project LABEL; it is looked up in that list
    and never used as a path.
  - `path` is relative to the project root, at most 1024 characters, with no
    NUL, backslash, leading '/' or '~', and no '.' or '..' segment. It must
    resolve (realpath, so symlinks are followed first) to a location inside
    the root's realpath; a symlink that leaves the root is refused, and the
    checks below run again on the resolved path.
  - no segment may start with '.', except the subtree .claude/skills/; no
    segment may be a skipped directory (.git, node_modules, .venv, dist, ...).
  - only .md/.markdown/.html/.htm/.txt (docs) and .png/.jpg/.jpeg/.gif/.webp/
    .svg/.avif/.bmp/.ico (assets); nothing bash-guard calls secret material
    (.env*, id_rsa*, *.pem, *.key, .git-credentials, .npmrc, .pypirc, .ssh/,
    .aws/credentials: see SECRET_PATH_RE, tested against bash-guard's own).
  - a regular file (never a FIFO or device), opened with O_NOFOLLOW, at most
    2 MB for a doc and 10 MB for an image.
  - text that looks like a live credential (write-guard's patterns: private
    key blocks, cloud and API keys, tokens, JWTs) is replaced by
    "[redacted: <kind>]" before it leaves the server, search included.

HTML documents are never sent to the dashboard page. The page asks for a
one-time frame ticket (/api/docs/frame) and points a sandboxed <iframe> at
/docs-frame/<ticket>, which answers with the document and its own CSP:
default-src 'none', inline styles only, images only from data:/blob:, no
script unless the viewer turned scripts on for that file, and the CSP
`sandbox` directive (never allow-same-origin), so the document always runs
in an opaque origin: it cannot read the dashboard's localStorage token or
call /api, even when opened on its own.
"""
import hashlib
import os
import re
import secrets
import stat
import threading
import time

DOC_EXT = {".md": "md", ".markdown": "md", ".html": "html", ".htm": "html", ".txt": "txt"}
IMG_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
           ".webp": "image/webp", ".svg": "image/svg+xml", ".avif": "image/avif", ".bmp": "image/bmp",
           ".ico": "image/x-icon"}
MAX_DOC = 2 * 1024 * 1024
MAX_IMG = 10 * 1024 * 1024
MAX_PATH = 1024

# Directories never walked or served (besides every dot-directory but .claude/skills).
SKIP_DIRS = {"node_modules", "dist", "build", "out", "__pycache__", "venv", "env", "site-packages",
             "target", "coverage", "htmlcov", "bower_components", "jspm_packages", "Pods", "DerivedData",
             "vendor", "third_party", "__snapshots__", "tmp", "temp", "logs"}
# Dot-directories are skipped wholesale; listed here only to document the usual ones.
SKIP_DOT = {".git", ".venv", ".next", ".nuxt", ".cache", ".pytest_cache", ".mypy_cache", ".ruff_cache",
            ".tox", ".turbo", ".svelte-kit", ".parcel-cache", ".gradle", ".idea", ".vscode", ".terraform",
            ".dart_tool", ".expo", ".angular", ".claude/worktrees"}

# Text files that are build inputs, not documents (left out of the tree; still served).
NOT_DOC_TXT = re.compile(r"(?i)^(?:requirements.*|constraints.*|cmakelists|robots|dependencies|third_party_notices|.*\.min)\.txt$")

# Walk and search bounds.
WALK_MAX_ENTRIES = 40000      # directory entries looked at
WALK_MAX_FILES = 4000         # documents listed
WALK_MAX_DEPTH = 10
TREE_TTL = 2.0                # seconds a tree is served from cache without looking
TREE_MAX_AGE = 60.0           # after this a tree is always walked again
SEARCH_MAX_FILES = 1500
SEARCH_MAX_BYTES = 24 * 1024 * 1024
SEARCH_MAX_RESULTS = 200
SEARCH_PER_FILE = 5
SEARCH_SECS = 2.5
LINK_MAX_FILES = 2000         # Markdown files read for the backlink index
TEXT_CACHE_BYTES = 48 * 1024 * 1024
TICKET_TTL = 60.0

# bash-guard.py's _SECRET_PATH_RE / _SECRET_SUFFIX_RE / _SECRET_SUBSTR, applied to
# every segment and the whole relative path (tests/test_cc_dash_docs.py checks they agree).
SECRET_PATH_RE = re.compile(
    r"(?:^|/)(?:\.env(?:\.[^/]+)?|id_rsa[^/]*|id_ed25519[^/]*|id_ecdsa[^/]*|id_dsa[^/]*|\.git-credentials|\.npmrc|\.pypirc|\.netrc)$")
SECRET_SUFFIX_RE = re.compile(r"\.(?:pem|key|p12|pfx|keystore|jks|kdbx)$", re.I)
SECRET_SUBSTR = (".aws/credentials", ".ssh/", ".gnupg/")
SECRET_NAME_RE = re.compile(r"^(?:credentials|secrets?)(?:\.[a-z0-9]+)?\.(?:txt|json|ya?ml)$", re.I)

# write-guard.py's _SECRET_PATTERNS (content), used to redact rather than refuse.
# Token boundaries, chosen per token so a key right next to '-', '_', '=', a quote or a
# line edge is still caught, while matching stays linear on a 2 MB file:
#   - before every token: not a letter or digit (the prefixes all start with one);
#   - after a variable-length token: not a character of its own class, so the greedy run
#     always ends where the lookahead holds and a failed attempt never rescans a long run;
#   - after a fixed-length token (AWS, Google): not a letter or digit.
# A JWT needs a '.' after a run that may hold '-' and '_', so a regex would rescan the
# run from every 'eyJ' inside it (quadratic on '-eyJ-eyJ...'); _redact_jwt finds dotted
# runs at run starts (linear) and looks for the token inside each one in Python.
_AN = r"(?<![A-Za-z0-9])"
_REDACT = [
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY(?: BLOCK)?-----"
                               r"[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY(?: BLOCK)?-----|\Z)")),
    ("AWS access key id", re.compile(_AN + r"(?:AKIA|ASIA)[0-9A-Z]{16}(?![0-9A-Za-z])")),
    ("GitHub token", re.compile(_AN + r"(?:gh[pousr]_[A-Za-z0-9]{36,}(?![A-Za-z0-9])|github_pat_[A-Za-z0-9_]{22,}(?![A-Za-z0-9_]))")),
    ("Slack token", re.compile(_AN + r"xox[baprs]-[A-Za-z0-9-]{10,}(?![A-Za-z0-9-])")),
    ("Anthropic API key", re.compile(_AN + r"sk-ant-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])")),
    ("OpenAI API key", re.compile(_AN + r"sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}(?![A-Za-z0-9_-])")),
    ("Google API key", re.compile(_AN + r"AIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z])")),
    ("Stripe live key", re.compile(_AN + r"[sr]k_live_[0-9a-zA-Z]{24,}(?![0-9a-zA-Z])")),
]
# A maximal run of base64url characters and dots (always matches to its end: linear).
_DOTTED = re.compile(r"(?<![A-Za-z0-9_.-])[A-Za-z0-9_.-]+")


def _redact_jwt(text):
    """Replace header.payload.signature (header and payload starting 'eyJ', 10+ more
    characters each, signature 10+) inside dotted runs; what precedes the header in its
    segment (e.g. 'x-' or 'KEY_') stays."""
    if "eyJ" not in text:
        return text

    def sub(m):
        if m.group(0).count(".") < 2 or "eyJ" not in m.group(0):
            return m.group(0)
        segs = m.group(0).split(".")
        out, i, changed = [], 0, False
        while i < len(segs):
            seg = segs[i]
            p = _jwt_start(seg)
            if (p >= 0 and i + 2 < len(segs) and segs[i + 1].startswith("eyJ") and len(segs[i + 1]) >= 13
                    and len(segs[i + 2]) >= 10):
                tok = ".".join([seg[p:], segs[i + 1], segs[i + 2]])
                if not _PLACEHOLDER_RE.search(tok):
                    out.append(seg[:p] + "[redacted: JWT]")
                    i += 3
                    changed = True
                    continue
            out.append(seg)
            i += 1
        return ".".join(out) if changed else m.group(0)
    return _DOTTED.sub(sub, text)


def _jwt_start(seg):
    """Where a JWT header starts in a dotted-run segment: an 'eyJ' at the start or after
    '-' or '_', with 10+ characters after it; -1 if none."""
    p = seg.find("eyJ")
    while p >= 0:
        if (p == 0 or seg[p - 1] in "-_") and len(seg) - p >= 13:
            return p
        p = seg.find("eyJ", p + 1)
    return -1
_PLACEHOLDER_RE = re.compile(r"(?i)example|placeholder|your[_-]?|xxx|changeme|dummy|redacted|fake|sample|"
                             r"test[_-]?key|0000000000|1234567890")
_REDACT_HINT = re.compile(r"PRIVATE KEY|AKIA|ASIA|gh[pousr]_|github_pat_|xox[baprs]-|sk-|AIza|_live_|eyJ")

WIKI_RE = re.compile(r"!?\[\[([^\[\]\n|#]+)(?:#[^\[\]\n|]*)?(?:\|[^\[\]\n]*)?\]\]")

FRAME_CSP = ("default-src 'none'; img-src data: blob:; style-src 'unsafe-inline'; font-src data:; "
             "base-uri 'none'; form-action 'none'; frame-ancestors 'self'; sandbox")
FRAME_CSP_SCRIPTS = ("default-src 'none'; img-src data: blob:; style-src 'unsafe-inline'; font-src data:; "
                     "script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'; "
                     "sandbox allow-scripts")


class DocsError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status = status
        self.msg = msg


def redact(text):
    """Replace live-looking credentials with [redacted: kind]; placeholders stay."""
    if not _REDACT_HINT.search(text):
        return text
    for label, rx in _REDACT:
        def sub(m, label=label):
            s = m.group(0)
            if label != "private key" and _PLACEHOLDER_RE.search(s):
                return s
            return "[redacted: %s]" % label
        text = rx.sub(sub, text)
    return _redact_jwt(text)


def section_of(rel):
    """Which tree section a relative path belongs to."""
    parts = rel.split("/")
    if parts[0] == "vault":
        return "vault"
    if parts[0] == "docs" and len(parts) > 2 and parts[1] == "superpowers":
        return "plans"
    if parts[0] == "docs":
        return "docs"
    if len(parts) == 1:
        return "root"
    if rel.startswith(".claude/skills/"):
        return "skills"
    return "other"


def _segment_ok(parts):
    """'' when every segment may be served, else why not."""
    in_skills = len(parts) > 2 and parts[0] == ".claude" and parts[1] == "skills"
    for i, seg in enumerate(parts):
        if seg in ("", ".", ".."):
            return "bad segment"
        if seg.startswith(".") and not (in_skills and i < 2):
            return "hidden file or directory"
        if seg in SKIP_DIRS and i < len(parts) - 1:
            return "skipped directory"
    return ""


def is_secret(rel):
    low = rel.lower()
    base = low.rsplit("/", 1)[-1]
    return bool(SECRET_PATH_RE.search(low) or SECRET_SUFFIX_RE.search(base)
                or any(s in "/" + low for s in SECRET_SUBSTR) or SECRET_NAME_RE.match(base))


def check_rel(rel, kinds):
    """Validate a client path (str). Returns the normalised relative path or raises DocsError."""
    if not isinstance(rel, str) or not rel:
        raise DocsError(400, "A path is required.")
    if len(rel) > MAX_PATH or "\x00" in rel or "\\" in rel or any(ord(c) < 32 for c in rel):
        raise DocsError(400, "That path is not allowed.")
    if rel.startswith("/") or rel.startswith("~"):
        raise DocsError(400, "Paths are relative to the project.")
    parts = rel.split("/")
    if any(p in (".", "..") for p in parts):
        raise DocsError(400, "Paths may not contain . or .. segments.")
    parts = [p for p in parts if p]
    if not parts:
        raise DocsError(400, "A path is required.")
    rel = "/".join(parts)
    why = _segment_ok(parts)
    if why:
        raise DocsError(403, "Refused: %s." % why)
    ext = os.path.splitext(parts[-1])[1].lower()
    if ("doc" in kinds and ext in DOC_EXT) or ("img" in kinds and ext in IMG_EXT):
        pass
    else:
        raise DocsError(415, "Only Markdown, HTML, text and image files are served.")
    if is_secret(rel):
        raise DocsError(403, "Refused: that looks like secret material.")
    return rel


class Docs:
    def __init__(self, store, clock=time.time):
        self.store = store
        self.clock = clock
        self.lock = threading.Lock()
        self.trees = {}           # root -> {"t", "dirs": {path: mtime_ns}, "files": [...], "truncated"}
        self.texts = {}           # abs path -> (mtime_ns, size, text)
        self.text_bytes = 0
        self.links = {}           # root -> {"sig", "index": {stem: set(paths)}}
        self.tickets = {}         # ticket -> (expiry, abs path, scripts)

    # ── projects ──────────────────────────────────────────────────────────
    def projects(self):
        """[{project, root, sessions, last_active}] for every resolved project root on disk, over every
        held session (the store's span), so a project picked in a wider window opens here too."""
        st = self.store
        st.refresh()
        out = {}
        with st.lock:
            for s in st.held.values():
                root = getattr(s, "root", "") or ""
                label = getattr(s, "label", "") or ""
                if not root or not label or label == "unknown":
                    continue
                p = out.get(label)
                if p is None:
                    p = out[label] = {"project": label, "root": root, "sessions": 0, "last_active": 0.0}
                p["sessions"] += 1
                p["last_active"] = max(p["last_active"], s.last_write)
        rows = []
        home = os.path.realpath(os.path.expanduser("~"))
        for p in out.values():
            try:
                real = os.path.realpath(p["root"])
            except (OSError, ValueError):
                continue
            if real in ("/", "") or not os.path.isdir(real):
                continue
            p["home"] = real == home
            rows.append(p)
        rows.sort(key=lambda r: r["project"].lower())
        return rows

    def root_for(self, project):
        if not isinstance(project, str) or not project or len(project) > 300:
            raise DocsError(400, "A project is required.")
        for p in self.projects():
            if p["project"] == project:
                return p["root"]
        raise DocsError(404, "No project with that name in the lookback window.")

    # ── resolving a path ──────────────────────────────────────────────────
    def resolve(self, root, rel, kinds):
        """(normalised rel, absolute real path) for a path that may be served, else DocsError."""
        rel = check_rel(rel, kinds)
        real_root = os.path.realpath(root)
        try:
            real = os.path.realpath(os.path.join(real_root, rel))
        except (OSError, ValueError):
            raise DocsError(404, "Not found.")
        if real != real_root and not real.startswith(real_root.rstrip("/") + "/"):
            raise DocsError(403, "Refused: that path leaves the project.")
        real_rel = os.path.relpath(real, real_root).replace(os.sep, "/")
        if real_rel != rel:
            check_rel(real_rel, kinds)     # a symlink inside the project: its target passes the same rules
        return rel, real

    def _open(self, real, cap):
        """(bytes, stat) of a regular file of at most cap bytes."""
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            fd = os.open(real, flags)
        except FileNotFoundError:
            raise DocsError(404, "Not found.")
        except OSError:
            raise DocsError(403, "Refused: cannot open that file.")
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise DocsError(403, "Refused: not a regular file.")
            # `real` was checked as a realpath inside the project. If a directory on the way
            # was swapped for a symlink between that check and the open, the path no longer
            # resolves to itself, or it names a different file than the one opened: refuse.
            try:
                again = os.stat(real, follow_symlinks=False)
                moved = os.path.realpath(real) != real or (again.st_dev, again.st_ino) != (st.st_dev, st.st_ino)
            except OSError:
                moved = True
            if moved:
                raise DocsError(403, "Refused: the file changed while it was opened.")
            if st.st_size > cap:
                raise DocsError(413, "Too large to show (%d MB limit)." % (cap // (1024 * 1024)))
            chunks, n = [], 0
            while n <= cap:
                b = os.read(fd, min(1 << 20, cap + 1 - n))
                if not b:
                    break
                chunks.append(b)
                n += len(b)
            if n > cap:
                raise DocsError(413, "Too large to show (%d MB limit)." % (cap // (1024 * 1024)))
            return b"".join(chunks), st
        finally:
            os.close(fd)

    def _text(self, real, st=None):
        """Decoded, redacted text of a doc, cached by mtime and size."""
        if st is None:
            st = os.stat(real)
        with self.lock:
            hit = self.texts.get(real)
            if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
                return hit[2]
        data, st = self._open(real, MAX_DOC)
        text = redact(data.decode("utf-8", errors="replace"))
        with self.lock:
            old = self.texts.pop(real, None)
            if old:
                self.text_bytes -= len(old[2])
            self.texts[real] = (st.st_mtime_ns, st.st_size, text)
            self.text_bytes += len(text)
            while self.text_bytes > TEXT_CACHE_BYTES and self.texts:
                k = next(iter(self.texts))
                self.text_bytes -= len(self.texts.pop(k)[2])
        return text

    # ── tree ──────────────────────────────────────────────────────────────
    def _walk(self, root):
        real_root = os.path.realpath(root)
        dirs, files = {}, []
        seen = 0
        truncated = False
        stack = [("", 0)]
        while stack:
            rel_dir, depth = stack.pop()
            path = os.path.join(real_root, rel_dir) if rel_dir else real_root
            try:
                it = list(os.scandir(path))
                dirs[rel_dir] = os.stat(path).st_mtime_ns
            except OSError:
                continue
            for e in it:
                seen += 1
                if seen > WALK_MAX_ENTRIES or len(files) >= WALK_MAX_FILES:
                    truncated = True
                    break
                name = e.name
                rel = rel_dir + "/" + name if rel_dir else name
                try:
                    if e.is_symlink():
                        continue                     # never follow or list symlinks in the tree
                    if e.is_dir(follow_symlinks=False):
                        if depth + 1 > WALK_MAX_DEPTH or name in SKIP_DIRS:
                            continue
                        if rel == ".claude" or rel.startswith(".claude/"):
                            # Inside .claude only skills/ (never worktrees/, agents/, ...).
                            if rel not in (".claude", ".claude/skills") and not (
                                    rel.startswith(".claude/skills/") and not name.startswith(".")):
                                continue
                        elif name.startswith("."):
                            continue
                        stack.append((rel, depth + 1))
                        continue
                    if not e.is_file(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                ext = os.path.splitext(name)[1].lower()
                if ext not in DOC_EXT or (ext == ".txt" and NOT_DOC_TXT.match(name)):
                    continue
                parts = rel.split("/")
                if _segment_ok(parts) or is_secret(rel):
                    continue
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                files.append({"path": rel, "size": st.st_size, "mtime": st.st_mtime, "kind": DOC_EXT[ext],
                              "section": section_of(rel)})
            if truncated:
                break
        files.sort(key=lambda f: f["path"].lower())
        return {"t": self.clock(), "dirs": dirs, "files": files, "truncated": truncated, "real_root": real_root}

    def _tree(self, root):
        now = self.clock()
        with self.lock:
            c = self.trees.get(root)
        if c and now - c["t"] < TREE_TTL:
            return c
        if c and now - c["t"] < TREE_MAX_AGE:
            # Same directories (an added, removed or renamed file changes its directory's
            # mtime)? Then only re-stat the files for their mtimes.
            same = True
            for d, m in c["dirs"].items():
                try:
                    if os.stat(os.path.join(c["real_root"], d) if d else c["real_root"]).st_mtime_ns != m:
                        same = False
                        break
                except OSError:
                    same = False
                    break
            if same:
                files = []
                for f in c["files"]:
                    try:
                        st = os.stat(os.path.join(c["real_root"], f["path"]), follow_symlinks=False)
                    except OSError:
                        same = False
                        break
                    files.append(dict(f, size=st.st_size, mtime=st.st_mtime))
                if same:
                    c = dict(c, files=files, t=now)
                    with self.lock:
                        self.trees[root] = c
                    return c
        c = self._walk(root)
        with self.lock:
            self.trees[root] = c
            if len(self.trees) > 32:
                self.trees.pop(next(iter(self.trees)))
        return c

    def tree(self, project):
        root = self.root_for(project)
        c = self._tree(root)
        counts = {}
        for f in c["files"]:
            counts[f["section"]] = counts.get(f["section"], 0) + 1
        return {"project": project, "root": _tilde(root), "files": c["files"], "counts": counts,
                "truncated": c["truncated"], "limits": {"files": WALK_MAX_FILES, "depth": WALK_MAX_DEPTH}}

    # ── one document ──────────────────────────────────────────────────────
    def file(self, project, rel, known=""):
        root = self.root_for(project)
        rel, real = self.resolve(root, rel, ("doc",))
        try:
            st = os.stat(real)
        except OSError:
            raise DocsError(404, "Not found.")
        if not stat.S_ISREG(st.st_mode):
            raise DocsError(403, "Refused: not a regular file.")
        if st.st_size > MAX_DOC:
            raise DocsError(413, "Too large to show (2 MB limit).")
        version = "%d:%d" % (st.st_mtime_ns, st.st_size)
        kind = DOC_EXT[os.path.splitext(rel)[1].lower()]
        if known and known == version:
            return {"path": rel, "version": version, "unchanged": True}
        out = {"path": rel, "kind": kind, "version": version, "mtime": st.st_mtime, "size": st.st_size,
               "section": section_of(rel)}
        if kind == "html":
            return out                 # the content only ever goes to the sandboxed frame
        out["text"] = self._text(real, st)
        if kind == "md":
            out["backlinks"] = self.backlinks(root, rel)
        return out

    def asset(self, project, rel):
        root = self.root_for(project)
        rel, real = self.resolve(root, rel, ("img",))
        data, _ = self._open(real, MAX_IMG)
        return data, IMG_EXT[os.path.splitext(rel)[1].lower()]

    # ── HTML frames ───────────────────────────────────────────────────────
    def frame_ticket(self, project, rel, scripts):
        root = self.root_for(project)
        rel, real = self.resolve(root, rel, ("doc",))
        if DOC_EXT[os.path.splitext(rel)[1].lower()] != "html":
            raise DocsError(415, "Only HTML documents open in a frame.")
        self._open(real, MAX_DOC)                 # exists, regular, within the cap
        t = secrets.token_urlsafe(24)
        now = self.clock()
        with self.lock:
            for k in [k for k, v in self.tickets.items() if v[0] <= now]:
                del self.tickets[k]
            if len(self.tickets) > 64:
                self.tickets.pop(next(iter(self.tickets)))
            self.tickets[t] = (now + TICKET_TTL, real, bool(scripts))
        return {"url": "/docs-frame/" + t, "scripts": bool(scripts), "ttl": TICKET_TTL}

    def frame(self, ticket):
        """(html bytes, csp) for a one-time ticket, else DocsError."""
        with self.lock:
            entry = self.tickets.pop(ticket, None) if isinstance(ticket, str) else None
        if entry is None or entry[0] < self.clock():
            raise DocsError(404, "This preview link has expired. Reopen the document.")
        _, real, scripts = entry
        data, _ = self._open(real, MAX_DOC)
        text = redact(data.decode("utf-8", errors="replace"))
        return text.encode("utf-8"), (FRAME_CSP_SCRIPTS if scripts else FRAME_CSP)

    # ── backlinks ─────────────────────────────────────────────────────────
    def _link_index(self, root):
        c = self._tree(root)
        md = [f for f in c["files"] if f["kind"] == "md"][:LINK_MAX_FILES]
        sig = hashlib.sha1("\n".join("%s %s %s" % (f["path"], f["mtime"], f["size"]) for f in md).encode()).hexdigest()
        with self.lock:
            hit = self.links.get(root)
        if hit and hit["sig"] == sig:
            return hit["index"]
        index = {}
        for f in md:
            try:
                text = self._text(os.path.join(c["real_root"], f["path"]))
            except (DocsError, OSError):
                continue
            if "[[" not in text:
                continue
            for m in WIKI_RE.finditer(text):
                target = m.group(1).strip().lower()
                stem = target.rsplit("/", 1)[-1]
                stem = re.sub(r"\.(md|markdown)$", "", stem)
                index.setdefault(stem, set()).add(f["path"])
        with self.lock:
            self.links[root] = {"sig": sig, "index": index}
        return index

    def backlinks(self, root, rel):
        stem = os.path.splitext(rel.rsplit("/", 1)[-1])[0].lower()
        try:
            src = self._link_index(root).get(stem, set())
        except OSError:
            return []
        return sorted(p for p in src if p != rel)[:100]

    # ── search ────────────────────────────────────────────────────────────
    def search(self, project, q):
        root = self.root_for(project)
        q = (q or "").strip()
        if len(q) < 2:
            return {"q": q, "results": [], "files": 0, "truncated": False, "note": "Type at least 2 characters."}
        q = q[:200]
        ql = q.lower()
        rx = re.compile(re.escape(q), re.IGNORECASE)
        c = self._tree(root)
        t0 = time.monotonic()
        results, nfiles, nbytes = [], 0, 0
        truncated = False
        # Recently changed files first, so a capped search still covers what changes.
        files = sorted(c["files"], key=lambda f: -f["mtime"])
        for f in files:
            if nfiles >= SEARCH_MAX_FILES or nbytes >= SEARCH_MAX_BYTES or time.monotonic() - t0 > SEARCH_SECS:
                truncated = True
                break
            if f["size"] > MAX_DOC:
                continue
            nfiles += 1
            nbytes += f["size"]
            name_hit = ql in f["path"].lower()
            try:
                text = self._text(os.path.join(c["real_root"], f["path"]))
            except (DocsError, OSError):
                continue
            m = rx.search(text)
            if not m:
                if name_hit:
                    results.append({"path": f["path"], "line": 0, "text": "", "hits": [], "section": f["section"]})
                continue
            per = 0
            while m and per < SEARCH_PER_FILE:
                pos = m.start()
                ls = text.rfind("\n", 0, pos) + 1
                le = text.find("\n", pos)
                le = len(text) if le < 0 else le
                line_no = text.count("\n", 0, pos) + 1
                # Offsets come from the original text (str.lower() can change lengths), at most
                # 20 per line, and only the ~160 characters shown are kept.
                start = max(0, pos - ls - 32) if le - ls > 160 else 0
                end = min(le - ls, start + 160) if le - ls > 160 else le - ls
                line = text[ls + start:ls + end]
                hits = [[h.start(), h.end()] for _, h in zip(range(20), rx.finditer(line))]
                if start or end < le - ls:
                    pre = "…" if start else ""
                    line = pre + line + ("…" if end < le - ls else "")
                    hits = [[a + len(pre), b + len(pre)] for a, b in hits]
                results.append({"path": f["path"], "line": line_no, "text": line, "hits": hits, "section": f["section"]})
                per += 1
                if len(results) >= SEARCH_MAX_RESULTS:
                    break
                m = rx.search(text, le)
            if len(results) >= SEARCH_MAX_RESULTS:
                truncated = True
                break
        return {"q": q, "results": results, "files": nfiles, "bytes": nbytes, "truncated": truncated,
                "limits": {"files": SEARCH_MAX_FILES, "bytes": SEARCH_MAX_BYTES, "results": SEARCH_MAX_RESULTS,
                           "per_file": SEARCH_PER_FILE}}


def _tilde(p):
    home = os.path.expanduser("~")
    return "~" + p[len(home):] if p == home or p.startswith(home + "/") else p
