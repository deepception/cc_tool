"""Software-factory core: work orders, the line and the order ledger, read-only.

The one module that knows the on-disk formats of cc_tool's software factory
(docs/superpowers/specs/2026-10-25-software-factory-design.md, section 5) and
folds them into the order dicts the dashboard and bin/cc-factory show.

Formats (all under a project root; the ledger under its git common dir)
  order   factory/orders/FO-NNN-<slug>.md   `---` frontmatter (id, title,
          priority, created, line, status) and a markdown body. The FILE NAME's
          number is the order id (the filesystem keeps it unique); a
          frontmatter id is informational. The file is the spec. Its status
          seeds the fold only when it is queued or spec: a gate status
          (approved, done, ...) comes only from the ledger, so an order file
          cannot pass a human gate by saying so.
  line    factory/line.json   {"version": 1, "lines": {name: {"stations":
          [...], "gates": {...}, "retries": n, "threshold": x}}} merged over
          DEFAULT_LINE (threshold, the holdout pass mark in (0, 1], is kept
          only when a line sets it; line_threshold() reads it, default 1;
          "run" and "test", the commands verify uses, are kept only as plain
          one-line strings: line_command()).
  ledger  <git-common-dir>/cc_tool/factory/ledger.jsonl   one JSON object per
          line, append-only, written only by bin/cc-factory. The common dir is
          shared by the main checkout and every linked worktree, so every lane
          writes one ledger and every reader sees all of it.
fold() turns the ledger events of one order, its file and its line into a
single order dict: status, per-station progress, gate, lane, rework count.

Who reads it
  lib/cc_dash/graph.py   project_orders / line_config / ledger_path, polled
                         every few seconds by the read-only dashboard.
  bin/cc-factory         loads THIS file by path (importlib), hence no import
                         of any other cc_dash module, and the writer side of
                         the formats lives there, not here.
  tests/test_factory.py

Guarantees
  - Pure stdlib, no side effects at import, no module-level state besides a
    32-entry ledger cache behind a lock.
  - Read-only: nothing is ever created, written, chmod-ed or renamed.
  - Never raises on bad input: every public function degrades to []/None/defaults.
  - Bounded: at most MAX_ORDERS order files (the highest-numbered), MAX_ORDER_BYTES
    each, MAX_LEDGER_BYTES of ledger tail, MAX_LINE_JSON_BYTES of line.json,
    4 KiB per .git / commondir file, ledger lines over 64 KiB skipped, 5000
    entries per directory scan.
  - Safe reads: files open O_RDONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC and must be
    regular (a FIFO or device cannot hang or feed us); the ledger must also be
    owned by us; factory/ and factory/orders/ must be real directories (lstat).
"""
import copy
import datetime
import itertools
import json
import math
import os
import re
import stat
import threading
import time
import unicodedata

# ── Vocabulary (the frozen contract) ──────────────────────────────────────────
STATUSES = ("queued", "spec", "approved", "building", "verifying", "review", "done")
OFF_PATH = ("blocked", "rejected", "failed")
ALL_STATUSES = STATUSES + OFF_PATH
EVENTS = ("created", "status", "station", "gate", "lane", "note")
STATES = ("start", "pass", "fail", "waiting")
GATES = ("spec", "merge")
STATION_STATUSES = ("pending", "active", "done", "failed", "skipped")
SEED_STATUSES = ("queued", "spec")  # the only frontmatter statuses the fold starts from
PRIORITIES = ("p1", "p2", "p3")
EFFORTS = ("low", "medium", "high", "xhigh", "max")

NOTE_MAX = 500
BRIEFING_MAX = 12000         # the merge-gate briefing on a status event (cc-factory record writes it, show prints it)
CAUSES = ("infra",)          # a station `fail` that was not the work's fault (an API error, a skipped agent)
RECOMMENDATIONS = ("merge", "needs-scenarios", "needs-human-check", "rework", "reject", "rerun")
FLAGS = ("holdout-leak",)    # a note event that marks a finding of cc-factory record
MAX_ORDERS = 200
MAX_ORDER_BYTES = 256 * 1024
MAX_LEDGER_BYTES = 4 * 1024 * 1024
MAX_LINE_JSON_BYTES = 64 * 1024
ORDERS_DIR = "factory/orders"
LINE_FILE = "factory/line.json"
HOLDOUT_DIR = ".factory-holdout"
LEDGER_REL = ("cc_tool", "factory", "ledger.jsonl")  # under the git common dir

# Design doc 5.2 as a dict, in the normalised shape line_config() returns (every
# station spells out `holdout`; `effort` appears only on the station that sets it).
DEFAULT_LINE = {"version": 1, "lines": {"default": {
    "stations": [
        {"name": "plan", "title": "Plan", "model": "opus", "holdout": False},
        {"name": "build", "title": "Build", "model": "sonnet", "holdout": False, "effort": "medium"},
        {"name": "test", "title": "Test", "model": "sonnet", "holdout": False},
        {"name": "verify", "title": "Verify", "model": "opus", "holdout": True},
        {"name": "review", "title": "Review", "model": "opus", "holdout": False},
    ],
    "gates": {"spec": "human", "merge": "human"},
    "retries": 1,
}}}

# ── Internal limits and patterns ──────────────────────────────────────────────
_MAX_ENTRIES = 5000          # directory entries looked at per scan
_MAX_LEDGER_LINE = 64 * 1024  # a ledger line longer than this is skipped unparsed
_MAX_GIT_FILE = 4096         # .git and commondir files
_MAX_STATIONS = 12
_MAX_FM_LINES = 100          # the closing `---` must sit within this many lines
_MAX_LINE_DEFS = 256         # line definitions looked at in line.json
_TITLE_MAX = 200
_HEADING_SCAN = 1024         # characters of a `# ` heading line looked at for a title
_STATION_TITLE_MAX = 40
_CACHE_MAX = 32

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)

# The slug may not hold a control character (C0, DEL, C1): such a name is not an order.
_ORDER_FILE_RE = re.compile("FO-([0-9]{1,6})(-[^/\x00-\x1f\x7f-\x9f]*)?\\.md")
_LEDGER_ORDER_RE = re.compile(r"FO-[0-9]{3,6}")
_ID_IN_RE = re.compile(r"(?:[Ff][Oo]-)?([0-9]{1,12})")
_STATION_RE = re.compile(r"[a-z][a-z0-9_-]{0,31}")
_LINE_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\[\]-]{0,63}")
_SHA_RE = re.compile(r"[0-9a-fA-F]{4,64}")
_FULL_SHA_RE = re.compile(r"[0-9a-fA-F]{40,64}")
_HASH_RE = re.compile(r"[0-9a-f]{64}")
_DATE_RE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")
_DQ_FALLBACK_RE = re.compile(r'"(.*?)"\s*(?:\s#.*)?', re.DOTALL)
_COMMENT_RE = re.compile(r"(?:^|\s)#")
# C0 controls except \t and \n, DEL, C1 controls, lone surrogates (a JSON "\ud800"
# escape would otherwise make json.dumps(ensure_ascii=False) or a utf-8 write fail).
_CTRL_RE = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\ud800-\udfff]")
_ANY_CTRL_RE = re.compile("[\x00-\x1f\x7f-\x9f\ud800-\udfff]")  # the same plus \t and \n (paths)

_DECODER = json.JSONDecoder()

_CACHE = {}  # ledger path -> (key, max_bytes, events); insertion order = recency
_CACHE_LOCK = threading.Lock()


# ── Small text helpers ────────────────────────────────────────────────────────
def norm_id(s):
    """"3" / "fo-3" / "FO-003" / " FO-0003 " -> "FO-003"; "" when s is not an
    id. Padded to three digits, at most six (an int is accepted, a bool is not)."""
    try:
        if isinstance(s, bool):
            return ""
        if isinstance(s, int):
            s = str(s)
        if not isinstance(s, str):
            return ""
        m = _ID_IN_RE.fullmatch(s.strip())
        if not m:
            return ""
        n = int(m.group(1))
        return "FO-%03d" % n if n <= 999999 else ""
    except Exception:  # noqa: BLE001 - public readers never raise
        return ""


def slugify(title, n=40):
    """Lowercase [a-z0-9]+ words joined by "-" (accents folded, everything else a
    separator), clipped to n at a "-" boundary when one exists; "order" when empty."""
    try:
        n = max(1, int(n))
    except Exception:  # noqa: BLE001
        n = 40
    try:
        s = unicodedata.normalize("NFKD", str(title if title is not None else ""))
        words = re.findall(r"[a-z0-9]+", s.encode("ascii", "ignore").decode("ascii").lower())
        slug = "-".join(words)
        if len(slug) > n:
            if slug[n] == "-":
                slug = slug[:n]
            else:
                cut = slug.rfind("-", 0, n)
                slug = slug[:cut] if cut > 0 else slug[:n]
        return slug.strip("-") or "order"
    except Exception:  # noqa: BLE001
        return "order"


def clip(s, n=NOTE_MAX):
    """str(s) without control characters (\\n and \\t stay), clipped to n chars;
    "" for None."""
    try:
        if s is None:
            return ""
        if not isinstance(s, str):
            s = str(s)
        n = int(n)
        if n <= 0:
            return ""
        return _CTRL_RE.sub("", s)[:n]
    except Exception:  # noqa: BLE001
        return ""


def _one_line(s, n):
    """clip(), whitespace (newlines included) collapsed to single spaces, <= n chars."""
    return " ".join(clip(s, 1 << 20).split())[:n].strip()


# ── Safe file access ──────────────────────────────────────────────────────────
def _open_regular(path, own=False):
    """(fd, fstat) for a regular file opened without following a final symlink
    or blocking on a FIFO; owned by us too when own. None on any problem."""
    try:
        fd = os.open(path, os.O_RDONLY | _NOFOLLOW | _NONBLOCK | _CLOEXEC)
    except (OSError, ValueError):
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or (own and st.st_uid != os.getuid()):
            os.close(fd)
            return None
    except OSError:
        os.close(fd)
        return None
    return fd, st


def _read_n(fd, n):
    """Up to n bytes from fd (a regular file may return short reads)."""
    chunks, left = [], n
    while left > 0:
        b = os.read(fd, min(left, 1 << 20))
        if not b:
            break
        chunks.append(b)
        left -= len(b)
    return b"".join(chunks)


def _read_small(path, cap):
    """The bytes of a regular file of at most cap bytes, else None (missing,
    unsafe, or larger than cap)."""
    got = _open_regular(path)
    if got is None:
        return None
    fd, _ = got
    try:
        data = _read_n(fd, cap + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    return data if len(data) <= cap else None


def _is_real_dir(path):
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except (OSError, ValueError):
        return False


def _orders_dir(root):
    """<root>/factory/orders when factory/ and factory/orders/ are both real
    directories (never symlinks), else None."""
    try:
        fdir = os.path.join(os.path.abspath(root), "factory")
        odir = os.path.join(fdir, "orders")
        return odir if _is_real_dir(fdir) and _is_real_dir(odir) else None
    except Exception:  # noqa: BLE001
        return None


# ── Git: where the shared ledger lives ────────────────────────────────────────
def _first_line(data):
    return data.decode("utf-8", "replace").split("\n", 1)[0].strip()


def _valid_common(d):
    return os.path.isdir(d) and os.path.isfile(os.path.join(d, "HEAD"))


def git_common_dir(root):
    """realpath of the git common dir of root, or None. A `.git` directory is its
    own common dir; a `.git` FILE ("gitdir: X", relative to root) names the
    per-worktree dir X, whose `commondir` file (relative to X) points at the
    shared one (no commondir: X itself, as for a submodule). A symlinked `.git`,
    an oversize or malformed file, or a target without HEAD gives None. The git
    environment variables are deliberately not consulted."""
    try:
        if not isinstance(root, str) or not root or "\x00" in root:
            return None
        git = os.path.join(root, ".git")
        st = os.lstat(git)
        if stat.S_ISLNK(st.st_mode):
            return None
        if stat.S_ISDIR(st.st_mode):
            common = git
        elif stat.S_ISREG(st.st_mode):
            data = _read_small(git, _MAX_GIT_FILE)
            if data is None:
                return None
            line = _first_line(data)
            if not line.startswith("gitdir:"):
                return None
            gitdir = line[len("gitdir:"):].strip()
            if not gitdir:
                return None
            gitdir = os.path.join(root, gitdir)  # absolute values win the join
            try:
                os.lstat(os.path.join(gitdir, "commondir"))
            except FileNotFoundError:
                common = gitdir
            else:
                data = _read_small(os.path.join(gitdir, "commondir"), _MAX_GIT_FILE)
                rel = _first_line(data) if data is not None else ""
                if not rel:
                    return None
                common = os.path.join(gitdir, rel)
        else:
            return None
        return os.path.realpath(common) if _valid_common(common) else None
    except Exception:  # noqa: BLE001
        return None


def ledger_path(root):
    """<git common dir>/cc_tool/factory/ledger.jsonl, or None when root has no
    usable git dir. Nothing is created, not even the directories."""
    common = git_common_dir(root)
    return os.path.join(common, *LEDGER_REL) if common else None


def main_checkout(root):
    """The main worktree root when root is a linked worktree (its common dir is
    "<X>/.git" and <X>/.git is that very directory), else realpath(root)."""
    try:
        common = git_common_dir(root)
        if common and os.path.basename(common) == ".git":
            top = os.path.dirname(common)
            dot = os.path.join(top, ".git")
            if os.path.isdir(dot) and not os.path.islink(dot) and os.path.realpath(dot) == common:
                return top
        return os.path.realpath(root)
    except Exception:  # noqa: BLE001
        return root if isinstance(root, str) else ""


# ── The line (factory/line.json) ──────────────────────────────────────────────
def _norm_station(raw, seen):
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    if not isinstance(name, str) or not _STATION_RE.fullmatch(name) or name in seen:
        return None
    title = raw.get("title")
    title = _one_line(title, _STATION_TITLE_MAX) if isinstance(title, str) else ""
    model = raw.get("model")
    st = {"name": name,
          "title": title or name.capitalize(),
          "model": model if isinstance(model, str) and _MODEL_RE.fullmatch(model) else "",
          "holdout": raw.get("holdout") is True}
    effort = raw.get("effort")
    if isinstance(effort, str) and effort in EFFORTS:
        st["effort"] = effort
    return st


def _norm_line(raw):
    """One line definition in the contract's shape, or None without a valid station."""
    if not isinstance(raw, dict) or not isinstance(raw.get("stations"), list):
        return None
    stations, seen = [], set()
    for s in raw["stations"]:
        st = _norm_station(s, seen)
        if st is not None:
            seen.add(st["name"])
            stations.append(st)
            if len(stations) == _MAX_STATIONS:
                break
    if not stations:
        return None
    gates = raw.get("gates")
    spec = "auto" if isinstance(gates, dict) and gates.get("spec") == "auto" else "human"
    r = raw.get("retries")
    retries = 1 if isinstance(r, bool) or not isinstance(r, int) else max(0, min(3, r))
    line = {"stations": stations, "gates": {"spec": spec, "merge": "human"}, "retries": retries}
    th = raw.get("threshold")
    if not isinstance(th, bool) and isinstance(th, (int, float)) and math.isfinite(th) and 0 < th <= 1:
        line["threshold"] = float(th)
    for key in COMMAND_KEYS:
        cmd = line_command(raw.get(key))
        if cmd:
            line[key] = cmd
    return line


# Optional per-line commands the verify station uses to run and test the lane: written by a human in
# factory/line.json, never by a station, so they are the only run instructions verify is given.
COMMAND_KEYS = ("run", "test")
_COMMAND_MAX = 300


def line_command(v):
    """A line.json `run` / `test` value as kept: a plain one-line string (no control characters, at most
    300 characters, not starting with '-'), stripped; '' for anything else."""
    if not isinstance(v, str):
        return ""
    s = v.strip()
    if not s or len(s) > _COMMAND_MAX or s.startswith("-") or any(ord(c) < 32 or 127 <= ord(c) < 160 for c in s):
        return ""
    return s


def line_threshold(line):
    """The holdout pass mark of a line: its `threshold` (a number in (0, 1]) when
    line.json sets one, else 1 (every scenario must be satisfied)."""
    try:
        th = line.get("threshold")
        if not isinstance(th, bool) and isinstance(th, (int, float)) and math.isfinite(th) and 0 < th <= 1:
            return float(th)
    except Exception:  # noqa: BLE001
        pass
    return 1.0


def line_config(root):
    """{"version": 1, "lines": {name: line}}: DEFAULT_LINE's lines, then every
    valid line of <root>/factory/line.json (replacing a same-named one, so a
    project can redefine "default"). The merge gate is always "human". A missing,
    symlinked, oversize, non-JSON or wrongly typed file contributes nothing. The
    result is a private copy: callers may mutate it."""
    cfg = copy.deepcopy(DEFAULT_LINE)
    try:
        base = os.path.join(os.path.abspath(root), *LINE_FILE.split("/"))
        if not _is_real_dir(os.path.dirname(base)):
            return cfg
        data = _read_small(base, MAX_LINE_JSON_BYTES)
        doc = json.loads(data.decode("utf-8", "replace")) if data is not None else None
        lines = doc.get("lines") if isinstance(doc, dict) else None
        if not isinstance(lines, dict):
            return cfg
        for i, (name, raw) in enumerate(lines.items()):
            if i >= _MAX_LINE_DEFS:
                break
            if isinstance(name, str) and _LINE_NAME_RE.fullmatch(name):
                line = _norm_line(raw)
                if line is not None:
                    cfg["lines"][name] = line
    except Exception:  # noqa: BLE001 - a bad line.json never takes the reader down
        pass
    return cfg


def line_for(cfg, name):
    """cfg["lines"][name] when present, else the "default" line (DEFAULT_LINE's
    when cfg is malformed). The dict is shared: treat it as read-only."""
    try:
        lines = cfg["lines"]
        if isinstance(name, str) and name in lines:
            return lines[name]
        return lines["default"]
    except Exception:  # noqa: BLE001
        return DEFAULT_LINE["lines"]["default"]


# ── Order files ───────────────────────────────────────────────────────────────
def _scalar(v):
    """A frontmatter value as text: JSON-decoded when "double-quoted" (YAML's
    double-quoted style is a JSON dialect; unparseable escapes fall back to the
    raw text between the quotes), '...' unquoted with '' for ', else the text up
    to a ` #` comment."""
    v = v.strip()
    if v.startswith('"'):
        try:
            obj, _ = _DECODER.raw_decode(v)
            if isinstance(obj, str):
                return obj
        except ValueError:
            pass
        m = _DQ_FALLBACK_RE.fullmatch(v)
        return m.group(1) if m else v[1:]
    if v.startswith("'"):
        out, i = [], 1
        while i < len(v):
            if v[i] == "'":
                if v[i + 1:i + 2] == "'":
                    out.append("'")
                    i += 2
                    continue
                break
            out.append(v[i])
            i += 1
        return "".join(out)
    m = _COMMENT_RE.search(v)
    return (v[:m.start()] if m else v).strip()


def _date_ok(s):
    m = _DATE_RE.fullmatch(s)
    if not m:
        return False
    try:
        datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return True
    except ValueError:
        return False


def _meta_value(key, raw):
    """The validated value of a known frontmatter key (the default when invalid)."""
    if key == "id":
        return norm_id(raw)
    if key == "title":
        return _one_line(raw, _TITLE_MAX)
    if key == "priority":
        p = raw.strip().lower()
        return p if p in PRIORITIES else "p2"
    if key == "created":
        s = raw.strip()
        return s if _date_ok(s) else ""
    if key == "line":
        s = raw.strip()
        return s if _LINE_NAME_RE.fullmatch(s) else "default"
    s = raw.strip().lower()  # status
    return s if s in ALL_STATUSES else None


_META_KEYS = ("id", "title", "priority", "created", "line", "status")


def parse_order_text(text):
    """(meta, body) of an order file's text. Frontmatter is a first line `---`
    to the next `---` line within 100 lines of it (CRLF and a BOM tolerated); without
    a closing line there is none (meta {}, body = the whole text). Lines are
    `key: value` split on the first colon; only the known keys are kept, each
    validated: priority p1|p2|p3 else "p2", line a line name else "default",
    status in ALL_STATUSES else None, id/created "" when malformed, title one
    line of at most 200 characters. A key absent from the file is absent from
    meta. The body is newline-normalised (\\n) and without the BOM."""
    try:
        if isinstance(text, (bytes, bytearray)):
            text = bytes(text).decode("utf-8", "replace")
        if not isinstance(text, str):
            return {}, ""
        if text.startswith("﻿"):
            text = text[1:]
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")
        if lines[0].rstrip() != "---":
            return {}, text
        end = next((i for i in range(1, min(len(lines), _MAX_FM_LINES + 1))
                    if lines[i].rstrip() == "---"), None)
        if end is None:
            return {}, text
        raw = {}
        for ln in lines[1:end]:
            if not ln.strip() or ln[0] in " \t#":
                continue  # blank, comment, or an indented (nested) line
            key, sep, val = ln.partition(":")
            key = key.strip().lower()
            if sep and key in _META_KEYS and key not in raw:
                raw[key] = _scalar(val)
        return {k: _meta_value(k, raw[k]) for k in _META_KEYS if k in raw}, "\n".join(lines[end + 1:])
    except Exception:  # noqa: BLE001
        return {}, ""


def _scan_orders(root):
    """(orders_dir, [(num, name, is_regular_file)]) for every entry named like
    an order, looking at no more than 5000 entries; (None, []) when factory/ or
    factory/orders/ is missing or a symlink."""
    odir = _orders_dir(root)
    found = []
    if odir is None:
        return None, found
    try:
        with os.scandir(odir) as it:
            for e in itertools.islice(it, _MAX_ENTRIES):
                m = _ORDER_FILE_RE.fullmatch(e.name)
                if not m:
                    continue
                try:
                    reg = e.is_file(follow_symlinks=False)
                except OSError:
                    reg = False
                found.append((int(m.group(1)), e.name, reg))
    except OSError:
        pass
    return odir, found


def _pick(found):
    """[(num, name)] of the regular files, one per number (the first name wins),
    ascending, at most MAX_ORDERS of them (the highest numbers)."""
    first = {}
    for num, name, reg in sorted(found, key=lambda x: (x[0], x[1])):
        if reg and num not in first:
            first[num] = name
    return [(n, first[n]) for n in sorted(first)][-MAX_ORDERS:]


def order_files(root):
    """[(num, abs_path)] ascending: the regular, non-symlink files named
    FO-<n>[-slug].md in factory/orders, one per number (the first name wins),
    the MAX_ORDERS highest-numbered at most."""
    try:
        odir, found = _scan_orders(root)
        return [(n, os.path.join(odir, name)) for n, name in _pick(found)]
    except Exception:  # noqa: BLE001
        return []


def _ledger_max_num(root):
    max_num = 0
    lp = ledger_path(root)
    if lp:
        for ev in read_ledger(lp):
            max_num = max(max_num, int(ev["order"][3:]))
    return max_num


def max_order_num(root):
    """The highest FO number among ALL names in factory/orders that look like
    orders (of any file type, uncapped) and all ledger order ids; 0 when none.
    cc-factory's next id is this plus one, so a skipped symlink never frees a number."""
    try:
        _, found = _scan_orders(root)
        top = max([n for n, _, _ in found] or [0])
        return max(top, _ledger_max_num(root))
    except Exception:  # noqa: BLE001
        return 0


def find_order(root, oid):
    """Absolute path of the order file for oid (numeric match, so "3" finds
    FO-003-x.md), searching every name, not only the newest MAX_ORDERS; None."""
    try:
        want = norm_id(oid)
        if not want:
            return None
        num = int(want[3:])
        odir, found = _scan_orders(root)
        names = sorted(name for n, name, reg in found if n == num and reg)
        return os.path.join(odir, names[0]) if names else None
    except Exception:  # noqa: BLE001
        return None


def _strip_closing_hashes(s):
    """s without a closing `#` run that whitespace precedes ("Title ##" -> "Title",
    "C#" stays), like markdown's closing sequence. String operations only: a regex
    such as \\s+#+\\s*$ is quadratic on a long whitespace run."""
    t = s.rstrip()
    u = t.rstrip("#")
    if len(u) < len(t) and u and u[-1].isspace():
        return u.rstrip()
    return s


def _heading(body):
    """The first `# ` heading of a markdown body outside code fences, one line.
    Only the first _HEADING_SCAN characters of a heading line are looked at."""
    fence = False
    for ln in body.split("\n"):
        if ln.lstrip().startswith(("```", "~~~")):
            fence = not fence
        elif not fence and ln.startswith("# "):
            h = _one_line(_strip_closing_hashes(ln[2:2 + _HEADING_SCAN]), _TITLE_MAX)
            if h:
                return h
    return ""


def _load_order(root, odir, name):
    """The read_order dict for odir/name (a name _ORDER_FILE_RE accepted), or None."""
    m = _ORDER_FILE_RE.fullmatch(name)
    if not m:
        return None
    path = os.path.join(odir, name)
    got = _open_regular(path)
    if got is None:
        return None
    fd, st = got
    try:
        data = _read_n(fd, MAX_ORDER_BYTES)
    except OSError:
        return None
    finally:
        os.close(fd)
    meta, body = parse_order_text(data.decode("utf-8", "replace"))
    oid = norm_id(int(m.group(1)))
    return {
        "id": oid,
        "title": meta.get("title") or _heading(body) or oid,
        "priority": meta.get("priority") or "p2",
        "line": meta.get("line") or "default",
        "status": meta.get("status"),
        "created_date": meta.get("created") or "",
        "path": ORDERS_DIR + "/" + name,
        "abs": path,
        "body": body,
        "mtime": st.st_mtime,
    }


def read_order(root, path):
    """The order file at path (inside <root>/factory/orders, named like an
    order) as {"id", "title", "priority", "line", "status" (frontmatter or
    None), "created_date", "path" (relative to root), "abs", "body", "mtime"}.
    At most MAX_ORDER_BYTES are read (a larger file is truncated, its
    frontmatter still parses). None when unreadable or unsafe (a symlink, a
    FIFO, outside the orders directory)."""
    try:
        odir = _orders_dir(root)
        if odir is None or not isinstance(path, str):
            return None
        path = os.path.abspath(path)
        if os.path.dirname(path) != odir:
            return None
        return _load_order(root, odir, os.path.basename(path))
    except Exception:  # noqa: BLE001
        return None


# ── Ledger ────────────────────────────────────────────────────────────────────
def _finite_t(t):
    if isinstance(t, bool) or not isinstance(t, (int, float)):
        return None
    try:
        t = float(t)  # a huge int raises OverflowError
    except OverflowError:
        return None
    return t if math.isfinite(t) and t > 0 else None


def valid_event(rec):
    """The validated event for a decoded ledger line, or None when the line must
    be skipped. Kept only with a finite positive numeric `t`, an `order`
    matching ^FO-\\d{3,6}$ (returned as norm_id spells it) and an `event` in
    EVENTS. Optional fields are validated one by one and dropped alone when
    invalid (status in ALL_STATUSES, station a station name, state in STATES,
    gate in GATES, run_id/branch one clipped line with whitespace collapsed,
    worktree a path without control characters, its spaces kept, clipped to
    1024, base a hex sha, note clipped to NOTE_MAX); unknown keys are dropped.
    Written by cc-factory record and the gates (also validated one by one):
    attempt an int 1..999, cause in CAUSES, recommendation in RECOMMENDATIONS,
    flag in FLAGS, satisfaction a number in [0, 1], scenarios an int
    0..100000, no_holdout only True, merge_sha / lane_head / target full hex
    shas, spec_hash 64 lowercase hex digits, briefing clipped to BRIEFING_MAX
    (newlines kept)."""
    try:
        if not isinstance(rec, dict):
            return None
        t = _finite_t(rec.get("t"))
        order = rec.get("order")
        ev = rec.get("event")
        if (t is None or not isinstance(order, str) or not _LEDGER_ORDER_RE.fullmatch(order)
                or not isinstance(ev, str) or ev not in EVENTS):
            return None
        out = {"t": t, "order": norm_id(order), "event": ev}
        for key, allowed in (("status", ALL_STATUSES), ("state", STATES), ("gate", GATES)):
            v = rec.get(key)
            if isinstance(v, str) and v in allowed:
                out[key] = v
        v = rec.get("station")
        if isinstance(v, str) and _STATION_RE.fullmatch(v):
            out["station"] = v
        for key, n in (("by", 40), ("run_id", 80), ("branch", 200)):
            v = rec.get(key)
            if isinstance(v, str):
                v = _one_line(v, n)
                if v and not (key == "branch" and v.startswith("-")):
                    out[key] = v
        v = rec.get("worktree")
        if isinstance(v, str):
            v = _ANY_CTRL_RE.sub("", v)[:1024]  # a path: two spaces in a row are part of it
            if v.strip():
                out["worktree"] = v
        v = rec.get("base")
        if isinstance(v, str) and _SHA_RE.fullmatch(v):
            out["base"] = v.lower()
        v = rec.get("note")
        if isinstance(v, str):
            v = clip(v, NOTE_MAX).strip()
            if v:
                out["note"] = v
        for key, allowed in (("cause", CAUSES), ("recommendation", RECOMMENDATIONS), ("flag", FLAGS)):
            v = rec.get(key)
            if isinstance(v, str) and v in allowed:
                out[key] = v
        for key, hi in (("attempt", 999), ("scenarios", 100000)):
            v = rec.get(key)
            if isinstance(v, int) and not isinstance(v, bool) and (1 if key == "attempt" else 0) <= v <= hi:
                out[key] = v
        v = rec.get("satisfaction")
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and 0 <= v <= 1:
            out["satisfaction"] = float(v)
        if rec.get("no_holdout") is True:
            out["no_holdout"] = True
        for key in ("merge_sha", "lane_head", "target"):
            v = rec.get(key)
            if isinstance(v, str) and _FULL_SHA_RE.fullmatch(v):
                out[key] = v.lower()
        v = rec.get("spec_hash")
        if isinstance(v, str) and _HASH_RE.fullmatch(v):
            out["spec_hash"] = v
        v = rec.get("briefing")
        if isinstance(v, str):
            v = clip(v, BRIEFING_MAX).strip()
            if v:
                out["briefing"] = v
        return out
    except Exception:  # noqa: BLE001
        return None


def _parse_ledger(data):
    events = []
    for raw in data.split(b"\n"):
        if len(raw) > _MAX_LEDGER_LINE:
            continue
        raw = raw.strip()
        if raw[:1] != b"{":
            continue
        try:
            ev = valid_event(json.loads(raw.decode("utf-8", "replace")))
        except Exception:  # noqa: BLE001 - bad JSON, a RecursionError, a huge int
            continue
        if ev is not None:
            events.append(ev)
    return events


def _read_tail(fd, size, cap):
    """The last <= cap bytes of fd, starting at a line boundary: when the file is
    larger than cap the first (possibly partial) line is dropped."""
    if size <= cap:
        return _read_n(fd, cap)
    os.lseek(fd, size - cap - 1, os.SEEK_SET)  # one byte early: was the cut on a boundary?
    data = _read_n(fd, cap + 1)
    if data[:1] == b"\n":
        return data[1:]
    nl = data.find(b"\n")
    return data[nl + 1:] if nl >= 0 else b""


def read_ledger(path, max_bytes=MAX_LEDGER_BYTES):
    """The valid events of a ledger file in file order, from its last max_bytes
    (at most MAX_LEDGER_BYTES; the partial first line of a larger file is
    dropped). [] on any problem: missing, a symlink, not a regular file, not
    owned by us, a FIFO. Results are cached per path while (inode, size,
    mtime_ns) are unchanged; the list and its dicts are shared with the cache,
    so callers must treat them as read-only."""
    try:
        if not isinstance(path, str) or not path:
            return []
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
            max_bytes = MAX_LEDGER_BYTES
        max_bytes = min(max_bytes, MAX_LEDGER_BYTES)
        got = _open_regular(path, own=True)
        if got is None:
            return []
        fd, st = got
        try:
            key = (path, st.st_ino, st.st_size, st.st_mtime_ns)
            with _CACHE_LOCK:
                hit = _CACHE.get(path)
            if hit is not None and hit[0] == key and hit[1] == max_bytes:
                return hit[2]
            data = _read_tail(fd, st.st_size, max_bytes)
        finally:
            os.close(fd)
        events = _parse_ledger(data)
        with _CACHE_LOCK:
            _CACHE.pop(path, None)
            while len(_CACHE) >= _CACHE_MAX:
                del _CACHE[next(iter(_CACHE))]
            _CACHE[path] = (key, max_bytes, events)
        return events
    except Exception:  # noqa: BLE001
        return []


# ── Fold: ledger events -> one order dict ─────────────────────────────────────
def _stations_of(line):
    """[(name, title)] of a line in order, unique names; DEFAULT_LINE's when the
    line has no usable station."""
    out, seen = [], set()
    try:
        for s in line["stations"]:
            name = s["name"]
            if isinstance(name, str) and name and name not in seen:
                title = s.get("title")
                out.append((name, title if isinstance(title, str) and title else name.capitalize()))
                seen.add(name)
    except Exception:  # noqa: BLE001
        out = []
    if not out:
        out = [(s["name"], s["title"]) for s in DEFAULT_LINE["lines"]["default"]["stations"]]
    return out


def _date_to_time(s):
    m = _DATE_RE.fullmatch(s) if isinstance(s, str) else None
    if not m or not _date_ok(s):
        return None
    try:
        return float(time.mktime((int(m.group(1)), int(m.group(2)), int(m.group(3)), 0, 0, 0, 0, 0, -1)))
    except (OverflowError, ValueError, OSError):
        return None


def _fold(order, events, line):
    oid = order["id"]
    evs = [e for e in events if isinstance(e, dict) and e.get("order") == oid]
    sts = _stations_of(line)
    names = [n for n, _ in sts]
    index = {n: i for i, n in enumerate(names)}
    st_status = ["pending"] * len(sts)
    st_t = [None] * len(sts)

    fm = order.get("status")
    status = fm if fm in SEED_STATUSES else "queued"  # a gate status only ever comes from the ledger
    run_id, lane, rework = "", None, 0
    fail_seen, last_station, last_gate = False, "", None

    for e in evs:
        if e.get("run_id"):
            run_id = e["run_id"]
        s = e.get("status")
        if isinstance(s, str) and s in ALL_STATUSES:
            status = s
        kind, t = e.get("event"), e.get("t")
        if kind == "lane" and (e.get("branch") or e.get("worktree")):
            lane = {"branch": e.get("branch") or "", "worktree": e.get("worktree") or ""}
        state = e.get("state")
        if kind in ("station", "status") and e.get("station") in index and state in STATES:
            i = index[e["station"]]
            if state == "start":
                if fail_seen:
                    rework += 1
                    fail_seen = False
                st_status[i] = "active"
                for j in range(i):
                    if st_status[j] == "active":
                        st_status[j] = "done"
                for j in range(i + 1, len(sts)):
                    if st_status[j] in ("active", "done", "failed"):
                        st_status[j], st_t[j] = "pending", None
            elif state == "pass":
                st_status[i] = "done"
            elif state == "fail":
                st_status[i] = "failed"
                # An infrastructure failure (cause infra: an API error, a skipped agent) ends the run
                # without judging the work, so the start of the rerun after it is not a rework.
                if e.get("cause") not in CAUSES:
                    fail_seen = True
            else:
                st_status[i] = "active"
            st_t[i] = t
            last_station = e["station"]
        elif kind == "gate" and state in STATES:
            g = e.get("gate")
            if g not in GATES:
                g = e.get("station")
            if g in GATES:
                last_gate = (g, state)

    if status == "done":
        st_status = ["done" if s == "active" else "skipped" if s == "pending" else s for s in st_status]
    elif status == "failed":
        st_status = ["failed" if s == "active" else s for s in st_status]

    active = next((names[i] for i, s in enumerate(st_status) if s == "active"), "")
    station = active or (last_station if status in ("building", "verifying", "review", "blocked", "failed") else "")

    if status in ("done", "rejected"):
        gate = None
    elif last_gate is not None and last_gate[1] == "waiting":
        gate = {"name": last_gate[0], "waiting": True}
    elif status == "spec" and ((line or {}).get("gates") or {}).get("spec", "human") == "human":
        gate = {"name": "spec", "waiting": True}
    elif status == "review" and "active" not in st_status:
        gate = {"name": "merge", "waiting": True}
    else:
        gate = None

    created = next((e.get("t") for e in evs if e.get("event") == "created"), None)
    if created is None:
        created = _date_to_time(order.get("created_date"))
    updated = evs[-1].get("t") if evs else None
    if updated is None:
        mt = order.get("mtime")
        updated = float(mt) if isinstance(mt, (int, float)) and not isinstance(mt, bool) else None

    return {
        "id": oid,
        "title": order.get("title") or oid,
        "priority": order.get("priority") or "p2",
        "status": status,
        "path": order.get("path") or "",
        "created": created,
        "updated": updated,
        "station": station,
        "stations": [{"name": n, "title": ti, "status": st_status[i], "t": st_t[i]}
                     for i, (n, ti) in enumerate(sts)],
        "gate": gate,
        "lane": lane,
        "run_id": run_id,
        "rework": rework,
        "events": len(evs),
        "line": order.get("line") or "default",
    }


def fold(order, events, line, now=None):
    """One order dict from a read_order() dict, ledger events and its line. Only
    events whose `order` equals the order's id count, in the given order. Status
    starts at the frontmatter's when that is queued or spec (anything else, a
    gate status included, starts at "queued") and the events move it; each
    station is pending/active/done/failed/skipped: a `start` closes the active
    stations before it and resets the later ones (counting a rework when a
    `fail` preceded, unless that fail's cause was infra), `done` skips what
    never ran, a human gate is waiting at
    status "spec" (line gates.spec == "human") or at "review" with no active
    station. `now` is accepted for the caller's convenience and unused: the
    fold is a pure function of its inputs. The inputs are not modified."""
    try:
        return _fold(order, events, line)
    except Exception:  # noqa: BLE001 - degrade to a plain queued order
        oid = order.get("id", "") if isinstance(order, dict) else ""
        sts = _stations_of(None)
        return {"id": oid, "title": oid, "priority": "p2", "status": "queued", "path": "",
                "created": None, "updated": None, "station": "",
                "stations": [{"name": n, "title": ti, "status": "pending", "t": None} for n, ti in sts],
                "gate": None, "lane": None, "run_id": "", "rework": 0, "events": 0, "line": "default"}


def project_orders(root, now):
    """One fold() per order file under <root>/factory/orders, ascending by id
    number: [] when that directory is absent (or factory/ or orders/ is a
    symlink). The ledger comes from the git common dir of root, so it is the
    same whether root is the main checkout or any linked worktree. An
    unreadable order file is skipped; bounded as the module says."""
    try:
        odir, found = _scan_orders(root)
        if odir is None:
            return []
        cfg = line_config(root)
        lp = ledger_path(root)
        by_order = {}
        for ev in (read_ledger(lp) if lp else []):
            by_order.setdefault(ev["order"], []).append(ev)
        out = []
        for _, name in _pick(found):
            try:
                order = _load_order(root, odir, name)
                if order is not None:
                    out.append(fold(order, by_order.get(order["id"], []), line_for(cfg, order["line"]), now))
            except Exception:  # noqa: BLE001 - skip the one bad order
                continue
        return out
    except Exception:  # noqa: BLE001
        return []
