#!/usr/bin/env python3
"""The software-factory core: lib/cc_dash/factory.py, bin/cc-factory, cc-setup
--factory and .claude/workflows/factory-line.js, in five parts.

  lib       this file: the library (below)
  cli       tests/factory_cli_cases.py: bin/cc-factory end to end in temp git
            repos (init, new, list/show, mark, gates, lane, holdout, run-args,
            record, file modes and symlink / hard-link / FIFO refusal)
  gates     tests/factory_gate_cases.py: approve and reject with a dashboard's
            ticket: agent-like processes refused, forged tickets, fail closed
  setup     tests/factory_setup_cases.py: cc-setup --factory rollout into a
            temp project, idempotency, no factory/ without the flag
  workflow  tests/factory_workflow_cases.py: factory-line.js meta and syntax,
            and mock runs under node (labels per design 5.6, holdout fencing,
            rework, review on another model, the result contract)

Everything runs in temp dirs (XDG_STATE_HOME is pointed at one before anything
else happens, and a StateGuard confirms the real state dir was left alone).
Ledgers are written here as plain JSON lines; the CLI part drives the writer.
The library part:

  text       norm_id / slugify / clip
  orders     parse_order_text (the design doc's example verbatim, CRLF, BOM,
             quoting, comments, malformed input), read_order (truncation,
             title fallback, unsafe files)
  line       line_config normalisation and its safety rules, line_for
  ledger     valid_event / read_ledger: bad lines skipped, bad fields dropped,
             the 4 MiB tail, symlink / FIFO / foreign owner, the cache
  fold       one scenario per rule of the fold contract
  git        git_common_dir / ledger_path / main_checkout against fake .git
             layouts AND a real `git init` plus a real `git worktree add`
  project    project_orders end to end: caps, duplicates, symlinks, the ledger
             shared between a worktree and the main checkout, exact keys
  safety     read-only (the whole tree, .git included, is byte-identical after
             every public read), a random-bytes fuzz pass that never raises,
             a scan-cap check, a loose latency check

Run:  python3 tests/test_factory.py                 (all four parts, about two minutes)
      python3 tests/test_factory.py lib workflow    (only the named parts)
"""
import copy
import hashlib
import json
import os
import random
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "lib"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from state_guard import StateGuard  # noqa: E402

GUARD = StateGuard()  # before the env override: it records the REAL state dir
_STATE = tempfile.mkdtemp(prefix="factory-state-")
os.environ["XDG_STATE_HOME"] = _STATE

from cc_dash import factory as F  # noqa: E402

RESULTS = []
TEMPS = [_STATE]


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    if not cond:
        print("FAIL " + name + ("  " + str(detail) if detail else ""))


def mktemp(prefix):
    d = tempfile.mkdtemp(prefix=prefix)
    TEMPS.append(d)
    return d


def raises(fn, *a, **kw):
    """The exception text if fn raises, else ''."""
    try:
        fn(*a, **kw)
    except BaseException as e:  # noqa: BLE001
        return "%s: %s" % (type(e).__name__, e)
    return ""


# ── Fixtures ──────────────────────────────────────────────────────────────────
def put(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if isinstance(data, str):
        data = data.encode("utf-8")
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def fake_repo(base, name):
    """A directory with a minimal .git (a dir holding HEAD): enough for the readers."""
    root = os.path.join(base, name)
    put(os.path.join(root, ".git", "HEAD"), "ref: refs/heads/main\n")
    return root


ORDER_TEXT = """---
id: FO-003
title: Add CSV export to the reports page
priority: p2            # p1 | p2 | p3
created: 2026-10-25
line: default           # name of a line in factory/line.json
status: queued          # optional initial status; the ledger overrides it
---
## Intent
Why this exists and for whom.
## Acceptance criteria
- [ ] observable condition
## Notes
Constraints, links, prior art.
"""


def write_order(root, num, slug="x", text=None, title=None, extra=""):
    name = "FO-%03d%s.md" % (num, "-" + slug if slug else "")
    if text is None:
        text = "---\nid: FO-%03d\ntitle: %s\n%s---\n## Intent\nbody %d\n" % (
            num, title if title is not None else "Order %d" % num, extra, num)
    return put(os.path.join(root, "factory", "orders", name), text)


def ev(order, event, t, **kw):
    d = {"t": t, "order": order, "event": event, "by": "cc-factory"}
    d.update(kw)
    return d


def put_ledger(root, lines, via=None):
    """Write the ledger of root (path computed from `via` or root); lines are
    dicts (dumped), str or bytes (written as given)."""
    lp = F.ledger_path(via or root)
    out = []
    for ln in lines:
        if isinstance(ln, dict):
            ln = json.dumps(ln)
        if isinstance(ln, str):
            ln = ln.encode("utf-8")
        out.append(ln)
    put(lp, b"\n".join(out) + b"\n")
    return lp


def append_ledger(lp, d):
    with open(lp, "ab") as fh:
        fh.write(json.dumps(d).encode() + b"\n")


def snapshot(top):
    """Everything observable about a tree without following symlinks."""
    snap = {}
    for dp, dns, fns in os.walk(top):
        for n in dns + fns + ["."]:
            p = os.path.join(dp, n) if n != "." else dp
            try:
                st = os.lstat(p)
            except OSError:
                continue
            ent = [stat.S_IFMT(st.st_mode), st.st_mode & 0o7777, st.st_size, st.st_mtime_ns, st.st_ino]
            if stat.S_ISREG(st.st_mode):
                with open(p, "rb") as fh:
                    ent.append(hashlib.sha1(fh.read()).hexdigest())
            elif stat.S_ISLNK(st.st_mode):
                ent.append(os.readlink(p))
            snap[os.path.relpath(p, top)] = tuple(ent)
    return snap


def git_env(home):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"HOME": home, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TERMINAL_PROMPT": "0"})
    return env


def git(cwd, *args, home=None):
    cmd = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
           "-c", "init.defaultBranch=main"] + list(args)
    r = subprocess.run(cmd, cwd=cwd, env=git_env(home or _STATE), capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), r.stderr.strip()))
    return r.stdout.strip()


# ── norm_id / slugify / clip ──────────────────────────────────────────────────
def test_text_helpers():
    for raw, want in [("3", "FO-003"), ("fo-3", "FO-003"), ("FO-003", "FO-003"), (" FO-0003 ", "FO-003"),
                      ("Fo-12", "FO-012"), ("1234", "FO-1234"), ("FO-999999", "FO-999999"),
                      ("000000012", "FO-012"), (7, "FO-007"), ("\tFO-5\n", "FO-005"), ("0", "FO-000")]:
        check("norm_id(%r) == %r" % (raw, want), F.norm_id(raw) == want, F.norm_id(raw))
    for raw in ["", "  ", None, "x", "FO-", "FO-1a", "-3", "3.0", True, False, "FO--3", "FO-1000000",
                "1000000", "٣", "FO-٣", ["3"], {"id": 3}, -3, 3.5, "FO 3", "XFO-3", "FO-3-x"]:
        check("norm_id(%r) is empty" % (raw,), F.norm_id(raw) == "", F.norm_id(raw))

    check("slugify basic", F.slugify("Add CSV export to the reports page") == "add-csv-export-to-the-reports-page")
    check("slugify punctuation runs collapse", F.slugify("  Fix: the  (weird)   bug!! ") == "fix-the-weird-bug")
    check("slugify folds accents", F.slugify("Café Über à la carte") == "cafe-uber-a-la-carte")
    for raw in ["", None, "!!!", "   ", "中文", "---"]:
        check("slugify(%r) falls back to 'order'" % (raw,), F.slugify(raw) == "order", F.slugify(raw))
    check("slugify keeps digits", F.slugify("v2 API 10x") == "v2-api-10x")
    check("slugify clips at a dash boundary", F.slugify("alpha beta gamma delta", 12) == "alpha-beta")
    check("slugify clip exactly on a boundary", F.slugify("alpha beta gamma delta", 10) == "alpha-beta")
    check("slugify clip after the first word", F.slugify("alpha beta", 5) == "alpha")
    check("slugify hard-cuts one long word", F.slugify("a" * 100, 40) == "a" * 40)
    check("slugify hard-cuts a first word longer than n", F.slugify("abcdefgh ijk", 3) == "abc")
    check("slugify default n is 40", len(F.slugify("word " * 30)) <= 40)
    check("slugify never ends with a dash", not F.slugify("word " * 30).endswith("-"))
    check("slugify bad n degrades", F.slugify("hello world", "x") == "hello-world" and F.slugify("abc", 0) == "a")
    check("slugify of a non-string", F.slugify(12345) == "12345")

    check("clip(None) is ''", F.clip(None) == "")
    check("clip drops controls, keeps newline and tab",
          F.clip("a\x00b\x1bc\nd\te\x7ff\x85g\rh") == "abc\nd\tefgh", repr(F.clip("a\x00b\x1bc\nd\te\x7ff\x85g\rh")))
    check("clip clips to NOTE_MAX by default", len(F.clip("x" * 900)) == F.NOTE_MAX)
    check("clip n", F.clip("abcdef", 3) == "abc" and F.clip("abc", 0) == "" and F.clip("abc", -4) == "")
    check("clip str()s other types", F.clip(123) == "123" and F.clip(["a"]) == "['a']")
    check("clip drops lone surrogates", F.clip("a\ud800b") == "ab")
    check("clip keeps unicode", F.clip("naïve ☃") == "naïve ☃")
    check("clip counts after removing controls", F.clip("\x00" * 10 + "abc", 3) == "abc")
    check("clip bad n degrades", F.clip("abc", "zz") == "")


# ── parse_order_text / read_order ─────────────────────────────────────────────
def test_parse_order_text():
    meta, body = F.parse_order_text(ORDER_TEXT)
    check("design doc example: meta", meta == {"id": "FO-003", "title": "Add CSV export to the reports page",
                                               "priority": "p2", "created": "2026-10-25", "line": "default",
                                               "status": "queued"}, str(meta))
    check("design doc example: body", body.startswith("## Intent\n") and body.endswith("prior art.\n")
          and "---" not in body, repr(body[:40]))

    crlf = ORDER_TEXT.replace("\n", "\r\n")
    m2, b2 = F.parse_order_text(crlf)
    check("CRLF: same meta", m2 == meta, str(m2))
    check("CRLF: body has no CR", "\r" not in b2 and b2.startswith("## Intent\n"), repr(b2[:30]))
    m3, b3 = F.parse_order_text("﻿" + ORDER_TEXT)
    check("BOM: frontmatter still found", m3 == meta and b3 == body, str(m3))
    m4, b4 = F.parse_order_text("﻿" + crlf)
    check("BOM + CRLF", m4 == meta and b4 == body)
    m5, b5 = F.parse_order_text(ORDER_TEXT.encode("utf-8"))
    check("bytes input is decoded", m5 == meta and b5 == body)
    m6, _ = F.parse_order_text(ORDER_TEXT.replace("---\nid", "---   \nid", 1).replace("\n---\n##", "\n---\t\n##", 1))
    check("trailing blanks on the delimiter lines", m6 == meta, str(m6))

    def title_of(line):
        return F.parse_order_text("---\n%s\n---\nbody" % line)[0].get("title")

    check("JSON-quoted title with ': ' and '#'", title_of('title: "Fix: parse # of \\"things\\""') == 'Fix: parse # of "things"',
          title_of('title: "Fix: parse # of \\"things\\""'))
    check("JSON-quoted title with a trailing comment", title_of('title: "A: b" # trailing') == "A: b")
    check("JSON-quoted unicode escape", title_of('title: "caf\\u00e9"') == "café")
    check("JSON-quoted newline collapses", title_of('title: "two\\nlines\\tand tab"') == "two lines and tab")
    check("bad JSON escape falls back to stripping the quotes", title_of('title: "bad \\q escape"') == "bad \\q escape",
          title_of('title: "bad \\q escape"'))
    check("bad JSON escape with comment", title_of('title: "bad \\q" # c') == "bad \\q", title_of('title: "bad \\q" # c'))
    check("unterminated double quote", title_of('title: "never closed') == "never closed")
    check("single-quoted with ''", title_of("title: 'It''s: fine # ok'") == "It's: fine # ok", title_of("title: 'It''s: fine # ok'"))
    check("single-quoted plain", title_of("title: 'plain one'") == "plain one")
    check("single-quoted then comment", title_of("title: 'x y' # note") == "x y")
    check("unterminated single quote", title_of("title: 'open") == "open")
    check("unquoted ' #' comment is dropped", title_of("title: Do the thing   # a comment") == "Do the thing")
    check("unquoted '#' inside a word stays", title_of("title: Learn C# now") == "Learn C# now")
    check("unquoted value that is only a comment", title_of("title: # nothing") == "")
    check("colon in an unquoted value", title_of("title: Add: a thing") == "Add: a thing")
    check("key is case-insensitive", title_of("Title: Caps key") == "Caps key")
    check("title is collapsed to one line", title_of("title:    many    spaces   here  ") == "many spaces here")
    check("title control chars removed", title_of('title: "a\\u0000b\\u001bc"') == "abc", title_of('title: "a\\u0000b\\u001bc"'))
    check("title clipped to 200", len(title_of("title: " + "x" * 500)) == 200)
    check("empty title value", title_of("title:") == "")

    def meta_of(*lines):
        return F.parse_order_text("---\n" + "\n".join(lines) + "\n---\nbody")[0]

    check("bad priority -> p2", meta_of("priority: urgent")["priority"] == "p2")
    check("empty priority -> p2", meta_of("priority:")["priority"] == "p2")
    check("priority is case-insensitive", meta_of("priority: P1")["priority"] == "p1")
    check("priority p3 with comment", meta_of("priority: p3 # low")["priority"] == "p3")
    check("bad status -> None", meta_of("status: doing")["status"] is None)
    check("good status", meta_of("status: building")["status"] == "building" and meta_of("status: Rejected")["status"] == "rejected")
    check("every status is accepted", all(meta_of("status: %s" % s)["status"] == s for s in F.ALL_STATUSES))
    check("bad line -> default", meta_of("line: Bad Line!")["line"] == "default" and meta_of("line:")["line"] == "default")
    check("good line", meta_of("line: fast-track")["line"] == "fast-track")
    check("bad created -> ''", meta_of("created: 2026-13-45")["created"] == "" and meta_of("created: yesterday")["created"] == "")
    check("good created", meta_of("created: 2026-02-28")["created"] == "2026-02-28" and meta_of('created: "2026-10-25"')["created"] == "2026-10-25")
    check("leap day", meta_of("created: 2028-02-29")["created"] == "2028-02-29" and meta_of("created: 2027-02-29")["created"] == "")
    check("id is normalised", meta_of("id: 7")["id"] == "FO-007" and meta_of("id: fo-12")["id"] == "FO-012")
    check("bad id -> ''", meta_of("id: banana")["id"] == "")
    check("a key absent from the file is absent from meta", meta_of("title: only") == {"title": "only"})
    check("unknown keys, indented, comment and colon-less lines are ignored",
          meta_of("owner: me", "  title: nested", "# title: commented", "no colon here", "title: real") == {"title": "real"})
    check("duplicate key: first wins", meta_of("title: first", "title: second")["title"] == "first")
    check("meta never holds unknown keys", set(meta_of("a: 1", "b: 2", "id: 3")) == {"id"})

    nofm = "# Just a heading\nsome text\n"
    check("no frontmatter", F.parse_order_text(nofm) == ({}, nofm))
    check("empty text", F.parse_order_text("") == ({}, ""))
    unterminated = "---\ntitle: never closed\nstatus: done\nbody continues\n"
    check("unterminated frontmatter: meta {} and body is the whole text",
          F.parse_order_text(unterminated) == ({}, unterminated))
    far = "---\ntitle: too far\n" + "x: 1\n" * 150 + "---\nbody"
    check("closing line beyond 100 lines is unterminated", F.parse_order_text(far) == ({}, far))
    edge = "---\ntitle: edge\n" + "x: 1\n" * 98 + "---\nbody"
    check("a closing line 100 lines after the opening still counts", F.parse_order_text(edge) == ({"title": "edge"}, "body"))
    edge2 = "---\ntitle: edge\n" + "x: 1\n" * 99 + "---\nbody"
    check("a closing line 101 lines after the opening does not", F.parse_order_text(edge2)[0] == {})
    near = "---\n" + "x: 1\n" * 90 + "title: late\n---\nbody"
    check("closing line within 100 lines is found", F.parse_order_text(near)[0] == {"title": "late"})
    check("first line must be the delimiter", F.parse_order_text("\n---\ntitle: x\n---\nb")[0] == {})
    check("'----' is not a delimiter", F.parse_order_text("----\ntitle: x\n----\nb")[0] == {})
    check("empty frontmatter", F.parse_order_text("---\n---\nbody") == ({}, "body"))
    check("a '---' in the body stays in the body",
          F.parse_order_text("---\ntitle: t\n---\nbody\n---\nmore")[1] == "body\n---\nmore")
    for junk in [None, 5, 3.5, [], {}, object()]:
        check("parse_order_text(%s) degrades" % type(junk).__name__, F.parse_order_text(junk) == ({}, ""))

    rnd = random.Random(7)
    ok = True
    for i in range(300):
        blob = bytes(rnd.randrange(256) for _ in range(rnd.randrange(0, 400)))
        if i % 3 == 0:
            blob = b"---\n" + blob + b"\n---\n" + blob
        try:
            m, b = F.parse_order_text(blob)
            ok = ok and isinstance(m, dict) and isinstance(b, str)
            ok = ok and all(k in ("id", "title", "priority", "created", "line", "status") for k in m)
        except BaseException as e:  # noqa: BLE001
            ok = False
            check("garbage bytes parse (%d)" % i, False, repr(e))
            break
    check("garbage bytes: always a (dict, str)", ok)


def test_read_order():
    base = mktemp("factory-ro-")
    root = fake_repo(base, "proj")
    p = write_order(root, 3, "csv", text=ORDER_TEXT)
    o = F.read_order(root, p)
    check("read_order: keys", o is not None and set(o) == {"id", "title", "priority", "line", "status", "created_date",
                                                            "path", "abs", "body", "mtime"}, str(sorted(o or {})))
    check("read_order: values", o["id"] == "FO-003" and o["title"] == "Add CSV export to the reports page"
          and o["priority"] == "p2" and o["line"] == "default" and o["status"] == "queued"
          and o["created_date"] == "2026-10-25" and o["path"] == "factory/orders/FO-003-csv.md"
          and o["abs"] == p and o["body"].startswith("## Intent") and isinstance(o["mtime"], float), str(o))

    p2 = write_order(root, 9, "fm-id-ignored", text="---\nid: FO-001\ntitle: Whose id\n---\nb\n")
    check("the FILE NAME's number is the id, the frontmatter id is informational",
          F.read_order(root, p2)["id"] == "FO-009")
    p3 = write_order(root, 10, "", text="# The real heading #\ntext\n")
    o3 = F.read_order(root, p3)
    check("no frontmatter: title from the first '# ' heading", o3["title"] == "The real heading" and o3["status"] is None
          and o3["priority"] == "p2" and o3["line"] == "default" and o3["created_date"] == "", str(o3))
    p4 = write_order(root, 11, "fence", text="---\nstatus: spec\n---\n```\n# not a heading\n```\n## sub\n# Real\n")
    check("heading inside a code fence is skipped", F.read_order(root, p4)["title"] == "Real")
    p5 = write_order(root, 12, "bare", text="---\nstatus: spec\n---\nno heading at all\n")
    check("no title and no heading: the id", F.read_order(root, p5)["title"] == "FO-012")
    p6 = write_order(root, 13, "empty-title", text='---\ntitle: ""\n---\n# From body\n')
    check("empty title falls back to the heading", F.read_order(root, p6)["title"] == "From body")
    check("frontmatter title beats the heading", F.read_order(root, write_order(
        root, 14, "t", text="---\ntitle: Front\n---\n# Heading\n"))["title"] == "Front")
    p7 = write_order(root, 15, "", text="")
    o7 = F.read_order(root, p7)
    check("empty file", o7 is not None and o7["title"] == "FO-015" and o7["body"] == "")

    big = "---\nid: FO-020\ntitle: Huge order\npriority: p1\nstatus: spec\n---\n" + ("lorem ipsum dolor sit amet\n" * 20000)
    check("fixture is over 256 KiB", len(big) > 256 * 1024)
    pb = write_order(root, 20, "huge", text=big)
    ob = F.read_order(root, pb)
    check("oversize order: truncated, frontmatter still parsed",
          ob is not None and ob["title"] == "Huge order" and ob["priority"] == "p1" and ob["status"] == "spec"
          and len(ob["body"].encode("utf-8")) <= F.MAX_ORDER_BYTES - 40 and len(ob["body"]) > 200000,
          "%s %s" % (len(ob["body"]) if ob else None, ob and ob["title"]))
    pg = write_order(root, 21, "bin", text=b"---\ntitle: bytes \xff\xfe ok\n---\n\x00\x01body\xc3")
    og = F.read_order(root, pg)
    check("invalid UTF-8 is replaced, not fatal", og is not None and "bytes" in og["title"], str(og))

    # unsafe or foreign files
    odir = os.path.join(root, "factory", "orders")
    os.symlink(p, os.path.join(odir, "FO-030-link.md"))
    check("symlinked order -> None", F.read_order(root, os.path.join(odir, "FO-030-link.md")) is None)
    os.mkfifo(os.path.join(odir, "FO-031-fifo.md"))
    t0 = time.perf_counter()
    check("FIFO order -> None", F.read_order(root, os.path.join(odir, "FO-031-fifo.md")) is None)
    check("FIFO order does not hang", time.perf_counter() - t0 < 1.0)
    os.mkdir(os.path.join(odir, "FO-032-dir.md"))
    check("directory named like an order -> None", F.read_order(root, os.path.join(odir, "FO-032-dir.md")) is None)
    check("missing order -> None", F.read_order(root, os.path.join(odir, "FO-099.md")) is None)
    check("not named like an order -> None", F.read_order(root, put(os.path.join(odir, "notes.md"), "x")) is None)
    outside = put(os.path.join(base, "FO-040-out.md"), "---\ntitle: out\n---\n")
    check("order outside factory/orders -> None", F.read_order(root, outside) is None)
    check("non-string path -> None", F.read_order(root, None) is None and F.read_order(root, 5) is None)
    check("bad root -> None", F.read_order(None, p) is None and F.read_order("/nonexistent/x", p) is None)

    real = os.path.join(base, "real-factory")
    put(os.path.join(real, "orders", "FO-001-a.md"), "---\ntitle: via symlink\n---\n")
    root2 = fake_repo(base, "proj2")
    os.symlink(real, os.path.join(root2, "factory"))
    check("symlinked factory/ -> None", F.read_order(root2, os.path.join(root2, "factory", "orders", "FO-001-a.md")) is None)


# ── line_config ───────────────────────────────────────────────────────────────
def write_line(root, doc):
    return put(os.path.join(root, "factory", "line.json"), doc if isinstance(doc, (str, bytes)) else json.dumps(doc))


def stn(name, **kw):
    d = {"name": name}
    d.update(kw)
    return d


def test_line_config():
    base = mktemp("factory-line-")
    root = fake_repo(base, "proj")

    cfg = F.line_config(root)
    check("no line.json: DEFAULT_LINE", cfg == F.DEFAULT_LINE, str(cfg)[:100])
    cfg["lines"]["default"]["stations"].append("mutated")
    cfg["version"] = 99
    check("line_config returns a private copy", "mutated" not in F.DEFAULT_LINE["lines"]["default"]["stations"]
          and F.DEFAULT_LINE["version"] == 1 and F.line_config(root)["version"] == 1)
    d = F.DEFAULT_LINE["lines"]["default"]
    check("DEFAULT_LINE: five stations, models and gates",
          [(s["name"], s["model"]) for s in d["stations"]] == [("plan", "opus"), ("build", "sonnet"), ("test", "sonnet"),
                                                                ("verify", "opus"), ("review", "opus")]
          and d["gates"] == {"spec": "human", "merge": "human"} and d["retries"] == 1
          and d["stations"][1]["effort"] == "medium" and d["stations"][3]["holdout"] is True)
    design_doc = {"version": 1, "lines": {"default": {"stations": [
        {"name": "plan", "title": "Plan", "model": "opus"},
        {"name": "build", "title": "Build", "model": "sonnet", "effort": "medium"},
        {"name": "test", "title": "Test", "model": "sonnet"},
        {"name": "verify", "title": "Verify", "model": "opus", "holdout": True},
        {"name": "review", "title": "Review", "model": "opus"}],
        "gates": {"spec": "human", "merge": "human"}, "retries": 1}}}
    write_line(root, design_doc)
    check("the design doc's 5.2 line.json normalises to exactly DEFAULT_LINE", F.line_config(root) == F.DEFAULT_LINE)
    os.remove(os.path.join(root, "factory", "line.json"))

    write_line(root, {"version": 1, "lines": {"fast": {"stations": [
        stn("build", title="  Quick   build ", model="sonnet", effort="low"), stn("check", holdout=True)],
        "gates": {"spec": "auto"}, "retries": 0}}})
    cfg = F.line_config(root)
    fast = cfg["lines"].get("fast")
    check("custom line added next to default", set(cfg["lines"]) == {"default", "quick", "fast"} and cfg["lines"]["default"] == d, str(set(cfg["lines"])))
    check("custom line normalised", fast == {"stations": [
        {"name": "build", "title": "Quick build", "model": "sonnet", "holdout": False, "effort": "low"},
        {"name": "check", "title": "Check", "model": "", "holdout": True}],
        "gates": {"spec": "auto", "merge": "human"}, "retries": 0}, str(fast))
    check("line_for finds a named line", F.line_for(cfg, "fast") is fast)
    check("line_for unknown name -> default", F.line_for(cfg, "nope") is cfg["lines"]["default"])
    check("line_for degrades", F.line_for(None, "x") == d and F.line_for({}, None) == d and F.line_for({"lines": 5}, "a") == d
          and F.line_for(cfg, ["unhashable"]) is cfg["lines"]["default"])

    write_line(root, {"lines": {"default": {"stations": [stn("only")], "retries": 3}}})
    cfg = F.line_config(root)
    check("a same-named 'default' overrides", [s["name"] for s in cfg["lines"]["default"]["stations"]] == ["only"]
          and cfg["lines"]["default"]["retries"] == 3 and F.line_for(cfg, "zzz")["stations"][0]["name"] == "only")
    write_line(root, {"lines": {"default": {"stations": [{"name": "Bad Name"}, 5, None]}}})
    check("an invalid 'default' override leaves the built-in", F.line_config(root) == F.DEFAULT_LINE)

    for label, raw in [("not JSON", b"{not json"), ("empty file", b""), ("binary", bytes(range(256))), ("JSON null", "null"),
                       ("top level list", "[1,2]"), ("lines is a list", '{"lines": []}'), ("lines is a string", '{"lines": "x"}'),
                       ("line is a string", '{"lines": {"a": "x"}}'), ("stations not a list", '{"lines": {"a": {"stations": {}}}}'),
                       ("no stations key", '{"lines": {"a": {}}}'), ("NaN literal", '{"lines": {"a": {"retries": NaN}}}'),
                       ("deep nesting", "[" * 5000 + "]" * 5000), ("UTF-16 BOM", "﻿{}".encode("utf-16"))]:
        write_line(root, raw)
        check("line.json %s: default only" % label, F.line_config(root) == F.DEFAULT_LINE)

    def one(line, name="t"):
        write_line(root, {"lines": {name: line}})
        return F.line_config(root)["lines"].get(name)

    check("station entries of the wrong type are skipped", [s["name"] for s in one({"stations": [
        5, None, "x", [], stn("a"), {"name": 3}, {"name": None}, {}]})["stations"]] == ["a"])
    bad_names = ["Plan", "1x", "a b", "", "-a", "_a", "a" * 33, "a.b", "a/b", "é", "a\n"]
    check("invalid station names are skipped", [s["name"] for s in one({"stations": [stn(n) for n in bad_names] + [stn("ok")]})["stations"]] == ["ok"])
    check("a 32-char name is fine", one({"stations": [stn("a" * 32)]})["stations"][0]["name"] == "a" * 32)
    check("no valid station: line dropped", one({"stations": [stn("Bad")]}) is None and one({"stations": []}) is None)
    st15 = one({"stations": [stn("s%d" % i) for i in range(15)]})["stations"]
    check("more than 12 stations: the first 12 survive", [s["name"] for s in st15] == ["s%d" % i for i in range(12)], str(len(st15)))
    dup = one({"stations": [stn("a", title="first"), stn("b"), stn("a", title="second")]})["stations"]
    check("duplicate station names: the first wins", [(s["name"], s["title"]) for s in dup] == [("a", "first"), ("b", "B")], str(dup))
    check("title: default is name.capitalize()", one({"stations": [stn("my-step")]})["stations"][0]["title"] == "My-step")
    check("title: wrong type or blank falls back", [s["title"] for s in one({"stations": [stn("a", title=5), stn("b", title="  \n "), stn("c", title=None)]})["stations"]] == ["A", "B", "C"])
    check("title clipped to 40", len(one({"stations": [stn("a", title="x" * 90)]})["stations"][0]["title"]) == 40)
    check("title control chars removed", one({"stations": [stn("a", title="a\x00b\x1bc")]})["stations"][0]["title"] == "abc")

    models = {"opus": "opus", "claude-opus-4-5": "claude-opus-4-5", "claude-sonnet-5-5[1m]": "claude-sonnet-5-5[1m]",
              "a.b:c_d-e": "a.b:c_d-e", "us.anthropic.claude:v1": "us.anthropic.claude:v1", "has space": "", "-lead": "", "": "",
              "x" * 65: "", "x" * 64: "x" * 64, 123: "", None: "", "bad/slash": "", "new\nline": "", "é": ""}
    got = {}
    for m in models:
        st = one({"stations": [stn("a", model=m)]})["stations"][0]
        got[m] = st["model"]
    check("model strings validated, bad -> ''", got == models, str({k: v for k, v in got.items() if models[k] != v}))
    check("missing model -> ''", one({"stations": [stn("a")]})["stations"][0]["model"] == "")
    eff = [(e, one({"stations": [stn("a", effort=e)]})["stations"][0].get("effort")) for e in
           list(F.EFFORTS) + ["ludicrous", "HIGH", "", 5, None, ["low"], True]]
    check("effort kept only when valid", eff == [(e, e) for e in F.EFFORTS] + [("ludicrous", None), ("HIGH", None), ("", None),
                                                                              (5, None), (None, None), (["low"], None), (True, None)], str(eff))
    check("invalid effort is absent, not empty", "effort" not in one({"stations": [stn("a", effort="ludicrous")]})["stations"][0])
    check("holdout must be a real bool", [one({"stations": [stn("a", holdout=h)]})["stations"][0]["holdout"] for h in
                                          (True, False, 1, "yes", None, "true")] == [True, False, False, False, False, False])

    for gates, want in [({"spec": "auto", "merge": "auto"}, {"spec": "auto", "merge": "human"}),
                        ({"spec": "human", "merge": "human"}, {"spec": "human", "merge": "human"}),
                        ({"merge": "auto"}, {"spec": "human", "merge": "human"}), ({"spec": "AUTO"}, {"spec": "human", "merge": "human"}),
                        ("auto", {"spec": "human", "merge": "human"}), (None, {"spec": "human", "merge": "human"}),
                        ({"spec": 1}, {"spec": "human", "merge": "human"})]:
        check("gates %s -> %s" % (gates, want), one({"stations": [stn("a")], "gates": gates})["gates"] == want)
    check("missing gates -> human/human", one({"stations": [stn("a")]})["gates"] == {"spec": "human", "merge": "human"})

    def retries(v):
        return one({"stations": [stn("a")], "retries": v})["retries"]

    check("retries clamped to 0..3", [retries(v) for v in (0, 1, 2, 3, 4, 99, -1, -99)] == [0, 1, 2, 3, 3, 3, 0, 0])
    check("retries: bool rejected -> default 1", retries(True) == 1 and retries(False) == 1)
    check("retries: non-int rejected -> default 1", [retries(v) for v in ("2", 2.0, 1.5, None, [], {})] == [1] * 6)
    check("retries missing -> 1", one({"stations": [stn("a")]})["retries"] == 1)
    check("huge int retries clamp", retries(10 ** 30) == 3)

    def thr(v):
        return one({"stations": [stn("a")], "threshold": v})

    check("threshold: kept (as a float) when in (0, 1]", thr(0.5)["threshold"] == 0.5 and thr(1)["threshold"] == 1.0
          and isinstance(thr(1)["threshold"], float) and F.line_threshold(thr(0.75)) == 0.75)
    check("threshold: absent when invalid (0, negative, over 1, bool, string, null, NaN)",
          all("threshold" not in thr(v) for v in (0, -0.1, 1.5, True, "0.5", None, float("nan"))))
    check("threshold: absent when not set, so a line without one keeps the shape it had", "threshold" not in one({"stations": [stn("a")]}))
    # run / test: the only commands the verify station is handed, so only plain one-line strings a human wrote.
    cmd = lambda **kw: one(dict({"stations": [stn("a")]}, **kw))  # noqa: E731
    check("run/test: kept (stripped) when a plain one-line string", cmd(run="  python3 -m app  ", test="make test").get("run") == "python3 -m app"
          and cmd(test="make test")["test"] == "make test")
    check("run/test: absent when invalid (newline, escape, control, leading '-', over 300 characters, empty, not a string)",
          all("test" not in cmd(test=v) for v in ("make test\nrm -rf ~", "make\x1b[2J", "make\ttest", "-rf /", "x" * 301, "", "   ",
                                                  42, True, None, ["make"], {"cmd": "make"})))
    check("run/test: absent when not set, so a line without them keeps the shape it had", set(one({"stations": [stn("a")]})) == {"stations", "gates", "retries"})
    check("line_command: 300 characters is the limit", F.line_command("x" * 300) == "x" * 300 and F.line_command("x" * 301) == ""
          and F.COMMAND_KEYS == ("run", "test"))
    check("line_threshold: 1 when the line sets none (or is junk)", F.line_threshold(F.DEFAULT_LINE["lines"]["default"]) == 1.0
          and F.line_threshold({}) == 1.0 and F.line_threshold(None) == 1.0 and F.line_threshold({"threshold": True}) == 1.0)

    write_line(root, {"lines": {"Fast": {"stations": [stn("a")]}, "-x": {"stations": [stn("a")]}, "": {"stations": [stn("a")]},
                                "a" * 33: {"stations": [stn("a")]}, "good_1-x": {"stations": [stn("a")]}, "0lead": {"stations": [stn("a")]}}})
    check("invalid line names are ignored", set(F.line_config(root)["lines"]) == {"default", "quick", "good_1-x", "0lead"}, str(set(F.line_config(root)["lines"])))

    # safety: symlink, oversize, FIFO, symlinked factory/
    lj = os.path.join(root, "factory", "line.json")
    os.remove(lj)
    target = put(os.path.join(base, "elsewhere.json"), json.dumps({"lines": {"evil": {"stations": [stn("a")]}}}))
    os.symlink(target, lj)
    check("symlinked line.json is ignored", F.line_config(root) == F.DEFAULT_LINE)
    os.remove(lj)
    pad = "x" * (F.MAX_LINE_JSON_BYTES + 10)
    write_line(root, '{"lines": {"big": {"stations": [{"name": "a"}]}}, "pad": "%s"}' % pad)
    check("oversize line.json is ignored", F.line_config(root) == F.DEFAULT_LINE)
    ok_size = '{"lines": {"ok": {"stations": [{"name": "a"}]}}, "pad": "%s"}' % ("x" * 60000)
    write_line(root, ok_size)
    check("a 60 KB line.json is read", "ok" in F.line_config(root)["lines"])
    os.remove(lj)
    os.mkfifo(lj)
    t0 = time.perf_counter()
    check("FIFO line.json is ignored", F.line_config(root) == F.DEFAULT_LINE)
    check("FIFO line.json does not hang", time.perf_counter() - t0 < 1.0)
    os.remove(lj)
    real = os.path.join(base, "realfactory")
    put(os.path.join(real, "line.json"), json.dumps({"lines": {"evil": {"stations": [stn("a")]}}}))
    root3 = fake_repo(base, "proj3")
    os.symlink(real, os.path.join(root3, "factory"))
    check("symlinked factory/ -> line.json ignored", F.line_config(root3) == F.DEFAULT_LINE)
    check("no factory/ at all", F.line_config(fake_repo(base, "proj4")) == F.DEFAULT_LINE)
    for junk in [None, 5, "", "/nonexistent/dir\x00x", ["x"]]:
        check("line_config(%r) degrades to the default" % (junk,), F.line_config(junk) == F.DEFAULT_LINE)


# ── valid_event / read_ledger ─────────────────────────────────────────────────
def test_ledger():
    base = mktemp("factory-ledger-")
    root = fake_repo(base, "proj")
    ok = lambda **kw: dict({"t": 1700000000.5, "order": "FO-003", "event": "status"}, **kw)  # noqa: E731

    good = F.valid_event(ok(status="building", station="build", state="start", gate="spec", run_id="wf_ab12cd",
                            branch="factory/FO-003-x", worktree="/abs/wt", base="ABCDEF1234", note="hello", by="cc-factory"))
    check("valid_event keeps every contract field", good == {
        "t": 1700000000.5, "order": "FO-003", "event": "status", "status": "building", "state": "start", "gate": "spec",
        "station": "build", "by": "cc-factory", "run_id": "wf_ab12cd", "branch": "factory/FO-003-x", "worktree": "/abs/wt",
        "base": "abcdef1234", "note": "hello"}, str(good))
    check("valid_event minimal", F.valid_event(ok()) == {"t": 1700000000.5, "order": "FO-003", "event": "status"})
    rich = F.valid_event(ok(event="station", station="test", state="fail", attempt=2, cause="infra", recommendation="needs-scenarios",
                            flag="holdout-leak", satisfaction=0.5, scenarios=3, no_holdout=True, merge_sha="A" * 40, lane_head="b" * 40,
                            target="c" * 64, spec_hash="d" * 64, briefing="line 1\nline 2"))
    check("valid_event keeps the fields record and the gates write", rich == {
        "t": 1700000000.5, "order": "FO-003", "event": "station", "station": "test", "state": "fail", "attempt": 2, "cause": "infra",
        "recommendation": "needs-scenarios", "flag": "holdout-leak", "satisfaction": 0.5, "scenarios": 3, "no_holdout": True,
        "merge_sha": "a" * 40, "lane_head": "b" * 40, "target": "c" * 64, "spec_hash": "d" * 64, "briefing": "line 1\nline 2"}, str(rich))
    for key, val in [("attempt", 0), ("attempt", 1000), ("attempt", True), ("attempt", "2"), ("attempt", 1.0), ("cause", "flaky"),
                     ("cause", "INFRA"), ("recommendation", "ship"), ("flag", "leak"), ("satisfaction", 1.5), ("satisfaction", -0.1),
                     ("satisfaction", float("nan")), ("satisfaction", True), ("satisfaction", "1"), ("scenarios", -1), ("scenarios", 100001),
                     ("scenarios", False), ("no_holdout", "yes"), ("no_holdout", False), ("no_holdout", 1), ("merge_sha", "abc1234"),
                     ("merge_sha", "g" * 40), ("lane_head", "a" * 39), ("target", "a" * 65), ("spec_hash", "D" * 64), ("spec_hash", "d" * 63),
                     ("briefing", " \n "), ("briefing", 5)]:
        check("valid_event drops %s=%r" % (key, val), key not in F.valid_event(ok(**{key: val})))
    for r in F.RECOMMENDATIONS:
        check("valid_event keeps recommendation %s" % r, F.valid_event(ok(recommendation=r))["recommendation"] == r)
    b = F.valid_event(ok(briefing="a\x00b\n" + "x" * 20000))["briefing"]
    check("briefing: control characters removed, newlines kept, clipped to BRIEFING_MAX", len(b) == F.BRIEFING_MAX and b.startswith("ab\nx"), len(b))
    check("valid_event: int t is a float", isinstance(F.valid_event(ok(t=5))["t"], float))
    check("valid_event drops unknown keys", set(F.valid_event(ok(extra=1, zzz="x", **{"": 1}))) == {"t", "order", "event"})
    check("valid_event canonicalises FO-0003", F.valid_event(ok(order="FO-0003"))["order"] == "FO-003")
    for ev_name in F.EVENTS:
        check("event %s accepted" % ev_name, F.valid_event(ok(event=ev_name)) is not None)

    for label, rec in [("not a dict", [1]), ("None", None), ("string", "x"), ("int", 5), ("empty", {}),
                       ("t missing", {"order": "FO-003", "event": "note"}),
                       ("t NaN", ok(t=float("nan"))), ("t inf", ok(t=float("inf"))), ("t -inf", ok(t=float("-inf"))),
                       ("t negative", ok(t=-1)), ("t zero", ok(t=0)), ("t string", ok(t="5")), ("t bool", ok(t=True)),
                       ("t None", ok(t=None)), ("t list", ok(t=[1])), ("t huge int", ok(t=10 ** 400)), ("t 1e999", ok(t=1e999)),
                       ("order missing", {"t": 1, "event": "note"}), ("order lowercase", ok(order="fo-003")),
                       ("order short", ok(order="FO-3")), ("order 2 digits", ok(order="FO-03")), ("order 7 digits", ok(order="FO-1234567")),
                       ("order trailing newline", ok(order="FO-003\n")), ("order int", ok(order=3)), ("order None", ok(order=None)),
                       ("order padded", ok(order=" FO-003")), ("order unicode digits", ok(order="FO-٠٠٣")),
                       ("event unknown", ok(event="explode")), ("event missing", {"t": 1, "order": "FO-003"}),
                       ("event wrong case", ok(event="Status")), ("event list", ok(event=["status"]))]:
        check("valid_event rejects: " + label, F.valid_event(rec) is None, str(F.valid_event(rec)))

    bad_fields = ok(status="weird", station="Bad Station", state="go", gate="x", run_id=5, branch=7, worktree=["x"],
                    base="zzz", note=5, by=["x"])
    check("invalid optional fields are dropped, the line survives",
          F.valid_event(bad_fields) == {"t": 1700000000.5, "order": "FO-003", "event": "status"}, str(F.valid_event(bad_fields)))
    for key, val in [("status", "BUILDING"), ("status", ""), ("state", "PASS"), ("gate", "Merge"), ("station", "a b"),
                     ("station", "Upper"), ("station", "x" * 33), ("base", "abc"), ("base", "g" * 10), ("base", "a" * 65),
                     ("branch", "-delete-me"), ("run_id", "  "), ("note", " \n "), ("worktree", ""), ("branch", None)]:
        check("valid_event drops %s=%r" % (key, val), key not in F.valid_event(ok(**{key: val})))
    n = F.valid_event(ok(note="a\x00b" + "x" * 900))["note"]
    check("note: controls removed and clipped to 500", len(n) == F.NOTE_MAX and n.startswith("ab"), str(len(n)))
    check("note keeps newlines", F.valid_event(ok(note="l1\nl2"))["note"] == "l1\nl2")
    check("one-line fields are collapsed", F.valid_event(ok(branch="a\nb  c"))["branch"] == "a b c")
    check("run_id clipped to 80", len(F.valid_event(ok(run_id="r" * 200))["run_id"]) == 80)

    lines = [
        ok(t=1, status="queued", event="created"),
        "this is not json at all",
        '{"t": 2, "order": "FO-003", "event": "note", "note": "unterminated',
        "[1, 2, 3]", '"just a string"', "123", "null", "{}", "",
        '{"t": NaN, "order": "FO-003", "event": "note"}',
        '{"t": Infinity, "order": "FO-003", "event": "note"}',
        '{"t": -3, "order": "FO-003", "event": "note"}',
        '{"t": "5", "order": "FO-003", "event": "note"}',
        '{"t": 1e999, "order": "FO-003", "event": "note"}',
        ok(order="FO-3"), ok(event="unknown"),
        '{"t": 10, "order": "FO-003", "event": "note", "note": "' + "y" * 70000 + '"}',
        ok(t=3, event="note", note="kept", status="nonsense", station="Bad Name", extra={"a": 1}),
        b'\xff\xfe{"t": 4}',
        '{"t": 5, "order": "FO-004", "event": "note", "note": "café"}',
        '{"t": 6, "order": "FO-004", "event": "station", "station": "build", "state": "start"}   ',
        "  " + json.dumps(ok(t=7, event="note", note="indented")),
        '{"a":' * 3000,
        '{"t": 1' + "0" * 500 + ', "order": "FO-003", "event": "note"}',
    ]
    lp = put_ledger(root, lines)
    with open(lp, "ab") as fh:
        fh.write(b'{"t": 99, "order": "FO-003", "event": "note", "no')  # partial last line, no newline
    got = F.read_ledger(lp)
    want = [("FO-003", 1.0, "created"), ("FO-003", 3.0, "note"), ("FO-004", 5.0, "note"), ("FO-004", 6.0, "station"),
            ("FO-003", 7.0, "note")]
    check("read_ledger: bad lines skipped, good ones kept in file order",
          [(e["order"], e["t"], e["event"]) for e in got] == want, str([(e["order"], e["t"], e["event"]) for e in got]))
    e3 = next((e for e in got if e["t"] == 3.0), {})
    check("invalid optional fields dropped but the line survives", e3 == {"t": 3.0, "order": "FO-003", "event": "note", "note": "kept"}, str(e3))
    check("oversize ledger line (70 KB) skipped", all(e["t"] != 10.0 for e in got))
    check("partial last line skipped", all(e["t"] != 99.0 for e in got))
    check("unicode note survives", next(e for e in got if e["t"] == 5.0)["note"] == "café")
    check("an indented JSON line parses", got[-1]["note"] == "indented")

    # a complete last line without a trailing newline is kept
    lp2 = put(os.path.join(base, "l2", "ledger.jsonl"), json.dumps(ok(t=1)) + "\n" + json.dumps(ok(t=2)))
    check("complete last line without newline is kept", [e["t"] for e in F.read_ledger(lp2)] == [1.0, 2.0])

    # exact tail arithmetic
    mk = lambda i: json.dumps({"t": 1000 + i, "order": "FO-001", "event": "note", "note": "n%04d" % i})  # noqa: E731
    L = len(mk(0)) + 1
    tail = put(os.path.join(base, "tail", "ledger.jsonl"), "\n".join(mk(i) for i in range(10)) + "\n")
    serials = lambda mb: [int(e["t"]) - 1000 for e in F.read_ledger(tail, mb)]  # noqa: E731
    check("tail: exactly 3 lines when the cut falls on a boundary", serials(3 * L) == [7, 8, 9], str(serials(3 * L)))
    check("tail: a partial first line is dropped", serials(3 * L + 5) == [7, 8, 9] and serials(3 * L - 1) == [8, 9], str(serials(3 * L + 5)))
    check("tail: one byte short of a line", serials(L - 1) == [] and serials(1) == [])
    check("tail: max_bytes covering the file", serials(100 * L) == list(range(10)))
    check("tail: bad max_bytes means the default", serials(0) == list(range(10)) and serials(-5) == list(range(10))
          and serials(True) == list(range(10)) and serials("x") == list(range(10)) and serials(None) == list(range(10)))
    check("tail: max_bytes above the module cap is capped", len(F.read_ledger(tail, 10 ** 12)) == 10)

    # a ledger over 4 MiB reads only the tail
    N = 50000
    bigp = os.path.join(base, "big", "ledger.jsonl")
    os.makedirs(os.path.dirname(bigp))
    with open(bigp, "wb") as fh:
        for i in range(N):
            fh.write((json.dumps({"t": 1700000000 + i, "order": "FO-%03d" % (i % 200 + 1), "event": "note", "by": "cc-factory",
                                  "note": "n%06d" % i}) + "\n").encode())
    size = os.path.getsize(bigp)
    check("big ledger fixture is over 4 MiB", size > F.MAX_LEDGER_BYTES, str(size))
    t0 = time.perf_counter()
    big = F.read_ledger(bigp)
    took = time.perf_counter() - t0
    ser = [int(e["note"][1:]) for e in big]
    check("big ledger: only a tail is read", 0 < len(big) < N and ser[-1] == N - 1, "%d events" % len(big))
    check("big ledger: the tail is contiguous and starts on a whole line", ser == list(range(ser[0], N)) and ser[0] > 0)
    check("big ledger: no more than 4 MiB of lines", (N - ser[0]) * (size / N) <= F.MAX_LEDGER_BYTES + 200
          and (N - ser[0] + 2) * (size / N) >= F.MAX_LEDGER_BYTES - 200, "%s lines" % (N - ser[0]))
    check("big ledger parses in a sane time", took < 5.0, "%.2fs" % took)

    # unsafe ledgers
    real = put(os.path.join(base, "sym", "real.jsonl"), json.dumps(ok(t=1)) + "\n")
    os.symlink(real, os.path.join(base, "sym", "link.jsonl"))
    check("read_ledger on a regular file", len(F.read_ledger(real)) == 1)
    check("symlinked ledger -> []", F.read_ledger(os.path.join(base, "sym", "link.jsonl")) == [])
    fifo = os.path.join(base, "sym", "fifo.jsonl")
    os.mkfifo(fifo)
    t0 = time.perf_counter()
    r = F.read_ledger(fifo)
    check("FIFO ledger -> [] promptly", r == [] and time.perf_counter() - t0 < 1.0, "%.2fs" % (time.perf_counter() - t0))
    check("directory as ledger -> []", F.read_ledger(os.path.join(base, "sym")) == [])
    check("missing ledger -> []", F.read_ledger(os.path.join(base, "nothing.jsonl")) == [])
    check("bad path types -> []", F.read_ledger(None) == [] and F.read_ledger("") == [] and F.read_ledger(5) == []
          and F.read_ledger("x\x00y") == [])
    real_uid = os.getuid
    os.getuid = lambda: real_uid() + 1
    try:
        check("ledger owned by someone else -> []", F.read_ledger(real) == [])
    finally:
        os.getuid = real_uid
    check("ledger owned by us again", len(F.read_ledger(real)) == 1)

    # cache
    cp = put(os.path.join(base, "cache", "ledger.jsonl"), json.dumps(ok(t=1)) + "\n")
    a = F.read_ledger(cp)
    b = F.read_ledger(cp)
    check("cache: same result (the cached list)", a == b and a is b)
    time.sleep(0.01)
    append_ledger(cp, ok(t=2))
    c = F.read_ledger(cp)
    check("cache notices an append", [e["t"] for e in c] == [1.0, 2.0])
    check("cache: the earlier list is not mutated", [e["t"] for e in a] == [1.0])
    check("cache: unchanged file is a hit again", F.read_ledger(cp) is c)
    d = F.read_ledger(cp, 10 ** 6)
    check("cache: a different max_bytes is not served from a stale tail", [e["t"] for e in d] == [1.0, 2.0])
    small = len(json.dumps(ok(t=2))) + 2
    check("cache: a smaller tail differs", [e["t"] for e in F.read_ledger(cp, small)] == [2.0], str(small))
    os.replace(put(os.path.join(base, "cache", "new.jsonl"), json.dumps(ok(t=7)) + "\n"), cp)  # same path, new inode
    check("cache: a replaced file (new inode) is re-read", [e["t"] for e in F.read_ledger(cp)] == [7.0])
    os.remove(cp)
    check("cache: a deleted file -> []", F.read_ledger(cp) == [])
    for i in range(40):
        F.read_ledger(put(os.path.join(base, "many", "l%d.jsonl" % i), json.dumps(ok(t=i + 1)) + "\n"))
    check("cache holds at most 32 paths", len(F._CACHE) <= 32, str(len(F._CACHE)))
    r1 = F.read_ledger(os.path.join(base, "many", "l39.jsonl"))
    check("cache keeps the most recent paths", F.read_ledger(os.path.join(base, "many", "l39.jsonl")) is r1)


# ── fold ──────────────────────────────────────────────────────────────────────
def mkorder(oid="FO-003", **kw):
    o = {"id": oid, "title": "T " + oid, "priority": "p2", "line": "default", "status": None, "created_date": "",
         "path": "factory/orders/%s-x.md" % oid, "abs": "/x/%s" % oid, "body": "", "mtime": None}
    o.update(kw)
    return o


DEFAULT = F.DEFAULT_LINE["lines"]["default"]


def run_fold(raw, order=None, line=DEFAULT):
    evs = [F.valid_event(r) for r in raw]
    assert all(e is not None for e in evs), raw
    return F.fold(order or mkorder(), evs, line, 0.0)


def sts(o):
    return [s["status"] for s in o["stations"]]


def st(name, state, t, order="FO-003", **kw):
    return ev(order, "station", t, station=name, state=state, **kw)


def full_pass(t0=10):
    out, t = [], t0
    for name in ("plan", "build", "test", "verify", "review"):
        out += [st(name, "start", t), st(name, "pass", t + 1)]
        t += 2
    return out


def test_fold():
    o = run_fold([ev("FO-003", "created", 100.0, status="queued")])
    check("fold created: queued, no gate", o["status"] == "queued" and o["gate"] is None and o["station"] == ""
          and sts(o) == ["pending"] * 5 and o["created"] == 100.0 and o["updated"] == 100.0 and o["events"] == 1
          and o["rework"] == 0 and o["lane"] is None and o["run_id"] == "", str(o))
    check("fold: the dict has exactly the contract keys", set(o) == {
        "id", "title", "priority", "status", "path", "created", "updated", "station", "stations", "gate", "lane", "run_id",
        "rework", "events", "line"}, str(sorted(o)))
    check("fold: identity fields come from the order", o["id"] == "FO-003" and o["title"] == "T FO-003" and o["priority"] == "p2"
          and o["path"] == "factory/orders/FO-003-x.md" and o["line"] == "default")
    check("fold: stations are {name,title,status,t} in line order", o["stations"][0] == {"name": "plan", "title": "Plan", "status": "pending", "t": None}
          and [s["name"] for s in o["stations"]] == ["plan", "build", "test", "verify", "review"])

    base = [ev("FO-003", "created", 100.0, status="queued")]
    o = run_fold(base + [ev("FO-003", "status", 101.0, status="spec")])
    check("fold spec: waiting at the spec gate", o["status"] == "spec" and o["gate"] == {"name": "spec", "waiting": True})
    auto = copy.deepcopy(DEFAULT)
    auto["gates"]["spec"] = "auto"
    o = run_fold(base + [ev("FO-003", "status", 101.0, status="spec")], line=auto)
    check("fold spec with gates.spec auto: no gate", o["status"] == "spec" and o["gate"] is None)

    o = run_fold(base + [ev("FO-003", "status", 101.0, status="spec"),
                         ev("FO-003", "gate", 102.0, gate="spec", state="pass", status="approved")])
    check("fold approve: approved, gate None", o["status"] == "approved" and o["gate"] is None and o["events"] == 3)

    o = run_fold(base + [ev("FO-003", "status", 103.0, status="building", station="plan", state="start", run_id="wf_1",
                            branch="factory/FO-003-x", worktree="/wt")])
    check("fold building with plan start", o["status"] == "building" and o["station"] == "plan" and sts(o)[0] == "active"
          and sts(o)[1:] == ["pending"] * 4 and o["stations"][0]["t"] == 103.0 and o["run_id"] == "wf_1" and o["gate"] is None, str(o))
    check("fold: branch/worktree on a status event do not make a lane", o["lane"] is None)

    seq = [ev("FO-003", "status", 9.0, status="building")] + full_pass() + [ev("FO-003", "status", 30.0, status="review")]
    o = run_fold(seq)
    check("fold full pass sequence: every station done", sts(o) == ["done"] * 5 and o["status"] == "review", str(sts(o)))
    check("fold full pass: merge gate waiting, station is the last one", o["gate"] == {"name": "merge", "waiting": True}
          and o["station"] == "review")
    check("fold full pass: times are the pass events'", [s["t"] for s in o["stations"]] == [11.0, 13.0, 15.0, 17.0, 19.0])
    o = run_fold(seq[:-4])
    check("fold mid-sequence: the active station is current, no gate", o["station"] == "verify" and o["status"] == "building"
          and sts(o) == ["done", "done", "done", "active", "pending"] and o["gate"] is None, str(sts(o)))
    seq2 = [ev("FO-003", "status", 9.0, status="building")] + full_pass()[:-1] + [ev("FO-003", "status", 30.0, status="review")]
    o = run_fold(seq2)
    check("fold review with the review station still active: no merge gate yet", o["status"] == "review" and o["station"] == "review"
          and sts(o)[-1] == "active" and o["gate"] is None, str(o["gate"]))

    pre = [ev("FO-003", "status", 9.0, status="building"), st("plan", "start", 10), st("plan", "pass", 11), st("build", "start", 12),
           st("build", "pass", 13), st("test", "start", 14)]
    o = run_fold(pre + [st("test", "fail", 15)])
    check("fold test fail: failed, not yet a rework", sts(o) == ["done", "done", "failed", "pending", "pending"] and o["rework"] == 0)
    o = run_fold(pre + [st("test", "fail", 15), st("build", "start", 16)])
    check("fold fail then build start: rework 1, downstream reset to pending",
          o["rework"] == 1 and sts(o) == ["done", "active", "pending", "pending", "pending"] and o["stations"][2]["t"] is None
          and o["station"] == "build", str(sts(o)))
    cycle2 = pre + [st("test", "fail", 15), st("build", "start", 16), st("build", "pass", 17), st("test", "start", 18), st("test", "fail", 19),
                    st("build", "start", 20)]
    o = run_fold(cycle2)
    check("fold two fail/restart cycles: rework 2", o["rework"] == 2, str(o["rework"]))
    o = run_fold(cycle2 + [st("build", "pass", 21), st("test", "start", 22)])
    check("fold: a start after a handled failure is not another rework", o["rework"] == 2 and o["station"] == "test")
    o = run_fold(pre + [st("test", "fail", 15), st("test", "start", 16)])
    check("fold: retrying the same station after a fail counts as rework", o["rework"] == 1 and sts(o)[2] == "active")
    infra = pre + [st("test", "fail", 15, cause="infra")]
    o = run_fold(infra)
    check("fold: an infra fail still shows its station failed", sts(o)[2] == "failed" and o["rework"] == 0, str(sts(o)))
    o = run_fold(infra + [st("plan", "start", 16), st("plan", "pass", 17), st("build", "start", 18)])
    check("fold: the rerun after an infra fail (cause infra) is not a rework", o["rework"] == 0 and sts(o)[:2] == ["done", "active"], str(o["rework"]))
    o = run_fold(pre + [st("test", "fail", 15), st("build", "start", 16), st("build", "fail", 17, cause="infra"), st("plan", "start", 18)])
    check("fold: a real fail's rework counts; the infra fail after it adds none", o["rework"] == 1, str(o["rework"]))
    o = run_fold(pre + [st("test", "fail", 15, cause="bogus"), st("build", "start", 16)])
    check("fold: an unknown cause is dropped by the validator, so that fail counts", o["rework"] == 1, str(o["rework"]))
    o = run_fold(pre[:2] + [st("plan", "start", 14), st("build", "start", 15)])
    check("fold: a start closes the earlier active stations", sts(o)[:2] == ["done", "active"], str(sts(o)))
    o = run_fold(pre + [st("plan", "start", 30)])
    check("fold: restarting an early station resets everything after it", sts(o) == ["active"] + ["pending"] * 4)

    o = run_fold(base + [ev("FO-003", "status", 101.0, status="review")])
    check("fold review with no active station: merge gate", o["gate"] == {"name": "merge", "waiting": True} and o["station"] == "")

    o = run_fold([st("plan", "pass", 10), st("build", "start", 11), ev("FO-003", "status", 12.0, status="done")])
    check("fold done: active -> done, pending -> skipped", sts(o) == ["done", "done", "skipped", "skipped", "skipped"] and o["gate"] is None
          and o["station"] == "", str(sts(o)))
    o = run_fold([st("plan", "pass", 10), st("build", "start", 11), ev("FO-003", "status", 12.0, status="failed")])
    check("fold failed: active -> failed", sts(o) == ["done", "failed", "pending", "pending", "pending"] and o["station"] == "build"
          and o["gate"] is None, str(o["station"]))
    o = run_fold([st("plan", "pass", 10), ev("FO-003", "status", 12.0, status="failed")])
    check("fold failed with no active station: the last station touched", o["station"] == "plan")
    o = run_fold([st("plan", "start", 10), ev("FO-003", "status", 12.0, status="blocked")])
    check("fold blocked", o["status"] == "blocked" and o["station"] == "plan" and o["gate"] is None)
    o = run_fold(base + [ev("FO-003", "gate", 105.0, gate="spec", state="fail", status="rejected")])
    check("fold rejected: gate None", o["status"] == "rejected" and o["gate"] is None and o["station"] == "")
    o = run_fold([st("plan", "pass", 10), ev("FO-003", "status", 12.0, status="queued")])
    check("fold queued after activity: station empty", o["station"] == "")

    o = run_fold(base + [ev("FO-003", "gate", 105.0, gate="merge", state="waiting")])
    check("fold explicit gate waiting event", o["gate"] == {"name": "merge", "waiting": True} and o["status"] == "queued")
    o = run_fold(base + [ev("FO-003", "gate", 105.0, station="spec", state="waiting")])
    check("fold gate named through `station`", o["gate"] == {"name": "spec", "waiting": True})
    o = run_fold(base + [ev("FO-003", "gate", 105.0, gate="merge", state="waiting"), ev("FO-003", "gate", 106.0, gate="merge", state="pass")])
    check("fold: a later gate pass clears the waiting gate", o["gate"] is None)
    o = run_fold(base + [ev("FO-003", "gate", 105.0, gate="merge", state="waiting"), ev("FO-003", "status", 106.0, status="done")])
    check("fold: done always clears the gate", o["gate"] is None)
    o = run_fold(base + [ev("FO-003", "gate", 105.0, gate="merge", state="pass", status="done")])
    check("fold merge approval: done", o["status"] == "done" and o["gate"] is None)
    o = run_fold(base + [ev("FO-003", "gate", 105.0, gate="spec", state="waiting"), ev("FO-003", "gate", 106.0, gate="merge", state="waiting")])
    check("fold: the last gate event wins", o["gate"] == {"name": "merge", "waiting": True})
    o = run_fold(base + [ev("FO-003", "gate", 105.0, state="waiting")])
    check("fold: a gate event naming no gate is ignored", o["gate"] is None)
    o = run_fold(base + [ev("FO-003", "gate", 105.0, gate="spec", state="start")])
    check("fold: gate state 'start' is just not waiting", o["gate"] is None)
    check("fold: a gate event never touches stations", sts(o) == ["pending"] * 5)

    o = run_fold(base + [ev("FO-003", "lane", 110.0, branch="factory/FO-003-x", worktree="/wt/FO-003-x", base="abc1234")])
    check("fold lane", o["lane"] == {"branch": "factory/FO-003-x", "worktree": "/wt/FO-003-x"})
    o = run_fold(base + [ev("FO-003", "lane", 110.0, branch="b1"), ev("FO-003", "lane", 111.0, worktree="/w2")])
    check("fold lane: the last lane event wins, a missing half is ''", o["lane"] == {"branch": "", "worktree": "/w2"})
    o = run_fold(base + [ev("FO-003", "lane", 110.0, base="abc1234")])
    check("fold lane without branch or worktree is ignored", o["lane"] is None)
    o = run_fold(base + [ev("FO-003", "lane", 110.0, branch="b1"), ev("FO-003", "lane", 111.0, base="abc1234")])
    check("fold lane: an empty lane event keeps the previous lane", o["lane"] == {"branch": "b1", "worktree": ""})

    o = run_fold(base + [ev("FO-003", "status", 110.0, status="building", run_id="wf_1"), ev("FO-003", "note", 111.0, note="x"),
                         ev("FO-003", "note", 112.0, run_id="wf_2")])
    check("fold run_id: last wins (any event kind), kept across events without one", o["run_id"] == "wf_2")
    o = run_fold(base + [ev("FO-003", "status", 110.0, status="building", run_id="wf_1"), ev("FO-003", "note", 111.0, note="x")])
    check("fold run_id: an event without one does not clear it", o["run_id"] == "wf_1")
    o = run_fold(base + [ev("FO-003", "note", 111.0, status="blocked")])
    check("fold: a valid status on ANY event kind moves the status", o["status"] == "blocked")
    o = run_fold(base + [ev("FO-003", "note", 111.0, note="only a note")])
    check("fold note event: counted, no status change", o["status"] == "queued" and o["events"] == 2)

    mixed = [ev("FO-003", "created", 100.0), ev("FO-004", "status", 101.0, status="done"), ev("FO-003", "note", 102.0, note="x"),
             ev("FO-010", "created", 103.0)]
    o = run_fold(mixed)
    check("fold: only this order's events count", o["events"] == 2 and o["status"] == "queued" and o["updated"] == 102.0)
    o = run_fold(mixed, order=mkorder("FO-004"))
    check("fold: another order out of the same event list", o["status"] == "done" and o["events"] == 1)

    ts = time.mktime((2026, 10, 25, 0, 0, 0, 0, 0, -1))
    o = run_fold([ev("FO-003", "note", 200.0, note="x")], order=mkorder(created_date="2026-10-25", mtime=50.0))
    check("fold created: the frontmatter date is local midnight", o["created"] == ts and time.localtime(o["created"])[:6] == (2026, 10, 25, 0, 0, 0))
    o = run_fold([ev("FO-003", "created", 300.0), ev("FO-003", "created", 400.0)], order=mkorder(created_date="2026-10-25"))
    check("fold created: the first `created` event wins over the date", o["created"] == 300.0)
    o = F.fold(mkorder(), [], DEFAULT, 0.0)
    check("fold created: neither -> None", o["created"] is None)
    o = F.fold(mkorder(created_date="not-a-date"), [], DEFAULT, 0.0)
    check("fold created: a malformed date -> None", o["created"] is None)
    o = F.fold(mkorder(mtime=1234.5), [], DEFAULT, 0.0)
    check("fold updated: file mtime without events", o["updated"] == 1234.5 and o["events"] == 0)
    check("fold updated: None without events or mtime", F.fold(mkorder(mtime=None), [], DEFAULT, 0.0)["updated"] is None)
    o = run_fold([ev("FO-003", "created", 300.0), ev("FO-003", "note", 250.0, note="x")], order=mkorder(mtime=9999.0))
    check("fold updated: the last event's t (file order), not the file's mtime", o["updated"] == 250.0)

    o = F.fold(mkorder(status="spec"), [], DEFAULT, 0.0)
    check("fold: the frontmatter status is the initial one (spec)", o["status"] == "spec")
    check("fold: a frontmatter queued is kept", F.fold(mkorder(status="queued"), [], DEFAULT, 0.0)["status"] == "queued")
    for gate_status in ("approved", "building", "verifying", "review", "done", "blocked", "rejected", "failed"):
        o = F.fold(mkorder(status=gate_status), [], DEFAULT, 0.0)
        check("fold: a frontmatter status %s seeds queued (only the ledger moves an order past a gate)" % gate_status,
              o["status"] == "queued" and o["gate"] is None, (o["status"], o["gate"]))
    o = F.fold(mkorder(status="review"), [ev("FO-003", "note", 1.0, note="x")], DEFAULT, 0.0)
    check("fold: frontmatter review opens no merge gate", o["status"] == "queued" and o["gate"] is None, (o["status"], o["gate"]))
    o = F.fold(mkorder(status="approved"), [ev("FO-003", "gate", 1.0, gate="spec", state="pass", status="approved")], DEFAULT, 0.0)
    check("fold: approved comes from the gate event", o["status"] == "approved")
    o = F.fold(mkorder(status="bogus"), [], DEFAULT, 0.0)
    check("fold: an invalid frontmatter status -> queued", o["status"] == "queued")
    o = F.fold(mkorder(status="spec"), [], DEFAULT, 0.0)
    check("fold: frontmatter status spec waits at the spec gate", o["gate"] == {"name": "spec", "waiting": True})
    o = F.fold(mkorder(status="approved"), [ev("FO-003", "status", 1.0, status="spec")], DEFAULT, 0.0)
    check("fold: the ledger overrides the frontmatter status", o["status"] == "spec")

    custom = {"stations": [{"name": "draft", "title": "Draft"}, {"name": "ship", "title": "Ship"}], "gates": {"spec": "human", "merge": "human"}, "retries": 1}
    o = run_fold([st("draft", "start", 10), st("draft", "pass", 11), st("ship", "start", 12), st("plan", "start", 13)], order=mkorder(line="mine"), line=custom)
    check("fold custom line: its stations, in its order", [s["name"] for s in o["stations"]] == ["draft", "ship"]
          and sts(o) == ["done", "active"] and o["station"] == "ship" and o["line"] == "mine", str(o["stations"]))
    check("fold custom line: events for stations it lacks are ignored", o["events"] == 4 and all(s["name"] != "plan" for s in o["stations"]))
    cfg = F.line_config(None)
    o = F.fold(mkorder(line="nope"), [], F.line_for(cfg, "nope"), 0.0)
    check("fold unknown line -> default stations", [s["name"] for s in o["stations"]] == ["plan", "build", "test", "verify", "review"]
          and o["line"] == "nope")
    o = F.fold(mkorder(), [], None, 0.0)
    check("fold with no line degrades to the default stations", [s["name"] for s in o["stations"]] == ["plan", "build", "test", "verify", "review"])
    o = F.fold(mkorder(), [], {"stations": "junk"}, 0.0)
    check("fold with a malformed line degrades", len(o["stations"]) == 5)
    o = run_fold([st("plan", "start", 10)] + [ev("FO-003", "status", 11.0, status="building", station="plan", state="pass")])
    check("fold: a status event with station+state moves that station", sts(o)[0] == "done" and o["status"] == "building")
    o = run_fold([st("plan", "waiting", 10)])
    check("fold: station state 'waiting' is active", sts(o)[0] == "active")
    o = run_fold([st("build", "pass", 10)])
    check("fold: pass without start -> done", sts(o)[1] == "done" and o["stations"][1]["t"] == 10.0)
    o = run_fold([ev("FO-003", "station", 10.0, station="build")])
    check("fold: a station event without a state is ignored", sts(o) == ["pending"] * 5 and o["events"] == 1)

    raw = [F.valid_event(r) for r in base + [st("plan", "start", 1), ev("FO-003", "lane", 2.0, branch="b")]]
    snap = copy.deepcopy(raw)
    ordr = mkorder()
    ordr_snap = copy.deepcopy(ordr)
    line_snap = copy.deepcopy(DEFAULT)
    F.fold(ordr, raw, DEFAULT, 0.0)
    check("fold does not mutate its inputs", raw == snap and ordr == ordr_snap and DEFAULT == line_snap)
    a, b = F.fold(ordr, raw, DEFAULT, 0.0), F.fold(ordr, raw, DEFAULT, 99.0)
    check("fold is deterministic and ignores `now`", a == b)
    check("fold degrades on junk", isinstance(F.fold(None, None, None, None), dict) and isinstance(F.fold({}, [None, 5], DEFAULT, 0), dict)
          and F.fold({"id": "FO-001"}, [{"order": "FO-001", "station": ["x"], "state": "start", "event": "station", "t": 1}], DEFAULT, 0)["id"] == "FO-001")


# ── git: ledger_path / git_common_dir / main_checkout ────────────────────────
def test_git_layouts():
    base = mktemp("factory-gitl-")
    plain = os.path.join(base, "plain")
    os.makedirs(plain)
    check("no .git: git_common_dir None", F.git_common_dir(plain) is None)
    check("no .git: ledger_path None", F.ledger_path(plain) is None)
    check("no .git: main_checkout is realpath(root)", F.main_checkout(plain) == os.path.realpath(plain))
    for junk in [None, "", 5, "a\x00b", ["x"]]:
        check("git_common_dir(%r) is None" % (junk,), F.git_common_dir(junk) is None and F.ledger_path(junk) is None)
    check("main_checkout degrades", F.main_checkout(None) == "" and F.main_checkout("/nonexistent/x") == "/nonexistent/x")

    sym = os.path.join(base, "sym")
    os.makedirs(sym)
    real_git = os.path.join(base, "realgit")
    put(os.path.join(real_git, "HEAD"), "ref: refs/heads/main\n")
    os.symlink(real_git, os.path.join(sym, ".git"))
    check(".git symlink to a good dir -> None", F.git_common_dir(sym) is None and F.ledger_path(sym) is None)

    nohead = os.path.join(base, "nohead")
    os.makedirs(os.path.join(nohead, ".git"))
    check(".git dir without HEAD -> None", F.git_common_dir(nohead) is None)

    main = fake_repo(base, "main")
    common = os.path.realpath(os.path.join(main, ".git"))
    check(".git dir: common dir is its realpath", F.git_common_dir(main) == common)
    check("ledger_path under the common dir", F.ledger_path(main) == os.path.join(common, "cc_tool", "factory", "ledger.jsonl"))
    check("ledger_path creates nothing", not os.path.exists(os.path.join(common, "cc_tool")))
    check("main_checkout of a plain checkout", F.main_checkout(main) == os.path.realpath(main))
    link_to_main = os.path.join(base, "main-link")
    os.symlink(main, link_to_main)
    check("a symlinked root resolves to the same ledger", F.ledger_path(link_to_main) == F.ledger_path(main)
          and F.main_checkout(link_to_main) == os.path.realpath(main))

    def gitfile(name, content):
        d = os.path.join(base, name)
        put(os.path.join(d, ".git"), content)
        return d

    for label, content in [("garbage", "banana\n"), ("empty", ""), ("binary", bytes(range(256))), ("gitdir: with nothing", "gitdir:\n"),
                           ("gitdir: nonexistent", "gitdir: /nonexistent/place\n"), ("gitdir: relative nonexistent", "gitdir: ../nope\n"),
                           ("wrong key", "gitfile: /x\n"), ("oversize", "gitdir: /x" + " " * 5000 + "\n"), ("NUL byte", "gitdir: /x\x00y\n")]:
        d = gitfile("gf-" + label.replace(" ", "_").replace(":", ""), content)
        check(".git file %s -> None" % label, F.git_common_dir(d) is None and F.ledger_path(d) is None)
    d = gitfile("gf-nohead", "gitdir: %s\n" % os.path.join(base, "nohead", ".git"))
    check(".git file naming a dir without HEAD -> None", F.git_common_dir(d) is None)

    sub = os.path.join(base, "submod")
    put(os.path.join(sub, ".git"), "gitdir: ../modules/sub\n")
    put(os.path.join(base, "modules", "sub", "HEAD"), "ref: refs/heads/main\n")
    check(".git file, relative gitdir, no commondir -> the gitdir itself", F.git_common_dir(sub) == os.path.realpath(os.path.join(base, "modules", "sub")))
    check("main_checkout of a submodule-like layout is realpath(root)", F.main_checkout(sub) == os.path.realpath(sub))

    wt = os.path.join(base, "wt")
    adm = os.path.join(main, ".git", "worktrees", "wt")
    put(os.path.join(wt, ".git"), "gitdir: %s\n" % adm)
    put(os.path.join(adm, "HEAD"), "ref: refs/heads/wt\n")
    put(os.path.join(adm, "commondir"), "../..\n")
    check("fake linked worktree: common dir via commondir", F.git_common_dir(wt) == common and F.ledger_path(wt) == F.ledger_path(main))
    check("fake linked worktree: main_checkout", F.main_checkout(wt) == os.path.realpath(main))
    put(os.path.join(adm, "commondir"), "")
    check("empty commondir -> None", F.git_common_dir(wt) is None)
    put(os.path.join(adm, "commondir"), "/nonexistent\n")
    check("commondir naming nowhere -> None", F.git_common_dir(wt) is None)
    put(os.path.join(adm, "commondir"), "x" * 6000)
    check("oversize commondir -> None", F.git_common_dir(wt) is None)
    os.remove(os.path.join(adm, "commondir"))
    check("no commondir file -> the admin dir is the common dir", F.git_common_dir(wt) == os.path.realpath(adm))
    check("...and that is not a main checkout layout", F.main_checkout(wt) == os.path.realpath(wt))
    put(os.path.join(adm, "commondir"), os.path.join(main, ".git") + "\n")
    check("an absolute commondir works", F.git_common_dir(wt) == common)
    os.remove(os.path.join(adm, "commondir"))
    os.symlink(os.path.join(main, ".git"), os.path.join(adm, "commondir"))
    check("a symlinked commondir -> None", F.git_common_dir(wt) is None)

    fifo_root = os.path.join(base, "fiforoot")
    os.makedirs(fifo_root)
    os.mkfifo(os.path.join(fifo_root, ".git"))
    t0 = time.perf_counter()
    check(".git as a FIFO -> None, no hang", F.git_common_dir(fifo_root) is None and time.perf_counter() - t0 < 1.0)

    # .git/ with an unrelated basename: bare-like common dir is not a "main checkout"
    bare = os.path.join(base, "bare.git")
    put(os.path.join(bare, "HEAD"), "ref: refs/heads/main\n")
    bwt = os.path.join(base, "bwt")
    put(os.path.join(bwt, ".git"), "gitdir: %s\n" % os.path.join(bare, "worktrees", "bwt"))
    put(os.path.join(bare, "worktrees", "bwt", "HEAD"), "x\n")
    put(os.path.join(bare, "worktrees", "bwt", "commondir"), "../..\n")
    check("worktree of a bare repo: ledger under the bare dir", F.ledger_path(bwt) == os.path.join(os.path.realpath(bare), *F.LEDGER_REL))
    check("worktree of a bare repo: main_checkout is itself", F.main_checkout(bwt) == os.path.realpath(bwt))


def test_real_git():
    base = mktemp("factory-realgit-")
    main = os.path.join(base, "main")
    os.makedirs(main)
    git(main, "init", "-q")
    put(os.path.join(main, "README.md"), "hi\n")
    for n in (1, 2, 3):
        write_order(main, n, "committed", title="Committed %d" % n)
    git(main, "add", "-A")
    git(main, "commit", "-q", "-m", "init")
    git(main, "branch", "wtbranch")
    wt = os.path.join(base, "linked-wt")
    git(main, "worktree", "add", "-q", wt, "wtbranch")

    cd = git(main, "rev-parse", "--git-common-dir")
    expect = os.path.realpath(os.path.join(main, cd))
    check("real main checkout: git_common_dir matches `git rev-parse --git-common-dir`", F.git_common_dir(main) == expect,
          "%s vs %s" % (F.git_common_dir(main), expect))
    check("real main checkout: ledger_path", F.ledger_path(main) == os.path.join(expect, *F.LEDGER_REL))
    check("real main checkout: main_checkout", F.main_checkout(main) == os.path.realpath(main))
    cd_wt = git(wt, "rev-parse", "--git-common-dir")
    expect_wt = os.path.realpath(os.path.join(wt, cd_wt))
    check("real worktree: its common dir is git's", F.git_common_dir(wt) == expect_wt == expect, "%s vs %s" % (F.git_common_dir(wt), expect_wt))
    check("real worktree: the same ledger path as main", F.ledger_path(wt) == F.ledger_path(main))
    check("real worktree: main_checkout(worktree) == realpath(main)", F.main_checkout(wt) == os.path.realpath(main), F.main_checkout(wt))
    check("real worktree: .git is a file", os.path.isfile(os.path.join(wt, ".git")))
    sub = os.path.join(wt, "factory")
    check("a subdirectory has no .git of its own: None", F.git_common_dir(sub) is None)

    put_ledger(main, [ev("FO-002", "created", 100.0, status="queued"), ev("FO-002", "status", 101.0, status="spec")], via=wt)
    check("ledger written via the worktree path lands under the main .git",
          os.path.isfile(os.path.join(expect, *F.LEDGER_REL)))
    po_main = {o["id"]: o for o in F.project_orders(main, time.time())}
    po_wt = {o["id"]: o for o in F.project_orders(wt, time.time())}
    check("project_orders(main) sees events written through the worktree's ledger path",
          po_main["FO-002"]["status"] == "spec" and po_main["FO-002"]["events"] == 2, str(po_main.get("FO-002")))
    check("project_orders(worktree) sees the same orders and events (committed files)",
          sorted(po_wt) == ["FO-001", "FO-002", "FO-003"] and po_wt["FO-002"]["status"] == "spec")
    append_ledger(F.ledger_path(main), ev("FO-001", "status", 102.0, status="approved"))
    check("an append through main shows up from the worktree",
          {o["id"]: o["status"] for o in F.project_orders(wt, time.time())}["FO-001"] == "approved")
    check("max_order_num from the worktree", F.max_order_num(wt) == 3)

    # read-only: snapshot the whole tree, .git and the worktree included
    write_line(main, {"lines": {"fast": {"stations": [stn("a")]}}})
    put(os.path.join(main, "factory", "orders", "FO-004-nofront.md"), "# nothing\n")
    before = snapshot(base)
    for r in (main, wt, os.path.join(base, "nonexistent")):
        F.project_orders(r, 1.0)
        F.line_config(r)
        F.ledger_path(r)
        F.git_common_dir(r)
        F.main_checkout(r)
        F.order_files(r)
        F.max_order_num(r)
        F.find_order(r, "FO-002")
        for _, p in F.order_files(r):
            F.read_order(r, p)
    F.read_ledger(F.ledger_path(main))
    after = snapshot(base)
    changed = sorted(set(before) ^ set(after)) + sorted(k for k in before if k in after and before[k] != after[k])
    check("read-only: nothing under the tree (.git, worktree) was created or changed", before == after, str(changed[:5]))

    hold = os.path.join(base, "norepo")
    put(os.path.join(hold, "factory", "orders", "FO-001-a.md"), "---\ntitle: no git here\n---\n")
    before = snapshot(hold)
    got = F.project_orders(hold, 1.0)
    check("project without a git dir: orders listed, nothing written", [o["id"] for o in got] == ["FO-001"] and got[0]["events"] == 0
          and before == snapshot(hold))


# ── project_orders ────────────────────────────────────────────────────────────
def test_project_orders():
    base = mktemp("factory-po-")
    root = fake_repo(base, "proj")
    check("no factory/orders -> []", F.project_orders(root, 1.0) == [] and F.order_files(root) == [] and F.max_order_num(root) == 0
          and F.find_order(root, "FO-001") is None)
    os.makedirs(os.path.join(root, "factory"))
    check("factory/ without orders/ -> []", F.project_orders(root, 1.0) == [])
    os.makedirs(os.path.join(root, "factory", "orders"))
    check("empty orders/ -> []", F.project_orders(root, 1.0) == [])
    for junk in [None, "", 5, "/nonexistent", "a\x00b"]:
        check("project_orders(%r) -> []" % (junk,), F.project_orders(junk, 1.0) == [])

    # symlinks
    real = os.path.join(base, "elsewhere")
    put(os.path.join(real, "orders", "FO-001-a.md"), "---\ntitle: via symlink\n---\n")
    r1 = fake_repo(base, "symfactory")
    os.symlink(real, os.path.join(r1, "factory"))
    check("symlinked factory/ -> []", F.project_orders(r1, 1.0) == [] and F.order_files(r1) == [] and F.max_order_num(r1) == 0)
    r2 = fake_repo(base, "symorders")
    os.makedirs(os.path.join(r2, "factory"))
    os.symlink(os.path.join(real, "orders"), os.path.join(r2, "factory", "orders"))
    check("symlinked factory/orders -> []", F.project_orders(r2, 1.0) == [] and F.order_files(r2) == [])

    r3 = fake_repo(base, "symfile")
    write_order(r3, 1, "real", title="Real one")
    outside = put(os.path.join(base, "outside.md"), "---\ntitle: planted\n---\n")
    os.symlink(outside, os.path.join(r3, "factory", "orders", "FO-002-link.md"))
    os.symlink(os.path.join(r3, "factory", "orders", "FO-001-real.md"), os.path.join(r3, "factory", "orders", "FO-003-link2.md"))
    os.mkfifo(os.path.join(r3, "factory", "orders", "FO-004-fifo.md"))
    os.makedirs(os.path.join(r3, "factory", "orders", "FO-005-dir.md"))
    put(os.path.join(r3, "factory", "orders", "notes.md"), "x")
    put(os.path.join(r3, "factory", "orders", "FO-6.txt"), "x")
    put(os.path.join(r3, "factory", "orders", "fo-007.md"), "x")
    put(os.path.join(r3, "factory", "orders", "FO-1234567.md"), "x")
    t0 = time.perf_counter()
    got = F.project_orders(r3, 1.0)
    check("symlinks, FIFOs, dirs and misnamed files are skipped", [o["id"] for o in got] == ["FO-001"], str([o["id"] for o in got]))
    check("a FIFO among the orders does not hang", time.perf_counter() - t0 < 1.0)
    check("max_order_num counts skipped names too (no number is ever reused)", F.max_order_num(r3) == 5, str(F.max_order_num(r3)))
    check("find_order skips a symlinked order", F.find_order(r3, "FO-002") is None and F.find_order(r3, "FO-001").endswith("FO-001-real.md"))

    # the 200 cap
    r4 = fake_repo(base, "cap")
    for n in range(1, 206):
        write_order(r4, n, "o", text="---\ntitle: O%d\n---\n" % n)
    got = F.project_orders(r4, 1.0)
    ids = [o["id"] for o in got]
    check("205 order files: exactly the 200 highest", len(got) == 200 and ids[0] == "FO-006" and ids[-1] == "FO-205", "%d %s %s" % (len(ids), ids[:1], ids[-1:]))
    check("...sorted ascending", ids == sorted(ids) and [o["title"] for o in got][:2] == ["O6", "O7"])
    of = F.order_files(r4)
    check("order_files: the same 200, (int, abs path) pairs", len(of) == 200 and of[0][0] == 6 and of[-1][0] == 205
          and all(isinstance(n, int) and os.path.isabs(p) for n, p in of))
    check("max_order_num is not capped at MAX_ORDERS", F.max_order_num(r4) == 205)
    check("find_order finds an order beyond the cap", (F.find_order(r4, "FO-001") or "").endswith("FO-001-o.md") and F.find_order(r4, "1") == F.find_order(r4, "fo-001"))
    check("find_order: unknown / junk ids", F.find_order(r4, "FO-999") is None and F.find_order(r4, "banana") is None and F.find_order(r4, None) is None)

    # duplicates
    r5 = fake_repo(base, "dups")
    write_order(r5, 7, "zzz", title="Seven Z")
    write_order(r5, 7, "aaa", title="Seven A")
    put(os.path.join(r5, "factory", "orders", "FO-7.md"), "---\ntitle: Seven bare\n---\n")
    write_order(r5, 8, "ok", title="Eight")
    got = F.project_orders(r5, 1.0)
    check("duplicate numbers: the first file name wins", [(o["id"], o["title"]) for o in got] == [("FO-007", "Seven A"), ("FO-008", "Eight")],
          str([(o["id"], o["title"], o["path"]) for o in got]))
    check("order_files: one entry per number", [n for n, _ in F.order_files(r5)] == [7, 8])
    check("project_orders without a ledger creates no ledger dir", not os.path.exists(os.path.join(r5, ".git", "cc_tool")))
    check("find_order picks the first by name too", F.find_order(r5, "FO-007").endswith("FO-007-aaa.md"))

    # keys, ids from file names, custom line, ledger from a worktree-like path
    r6 = fake_repo(base, "full")
    write_order(r6, 3, "csv", text=ORDER_TEXT)
    write_order(r6, 4, "other", text="---\nid: FO-099\ntitle: Frontmatter id is informational\nline: fast\n---\nbody\n")
    write_order(r6, 5, "", text="# Plain markdown order\n")
    write_line(r6, {"lines": {"fast": {"stations": [stn("draft", title="Draft"), stn("ship")], "gates": {"spec": "auto"}}}})
    lp = put_ledger(r6, [
        ev("FO-003", "created", 1000.0, status="queued"), ev("FO-003", "status", 1001.0, status="spec"),
        ev("FO-003", "gate", 1002.0, gate="spec", state="pass", status="approved"),
        ev("FO-003", "lane", 1003.0, branch="factory/FO-003-csv", worktree="/wt/csv", base="abc1234"),
        ev("FO-003", "status", 1004.0, status="building", station="plan", state="start", run_id="wf_x"),
        ev("FO-004", "status", 1005.0, status="spec"),
        ev("FO-004", "station", 1006.0, station="draft", state="start"),
        ev("FO-004", "station", 1007.0, station="build", state="start"),
        ev("FO-050", "created", 1008.0, status="queued"),
    ])
    got = F.project_orders(r6, 2000.0)
    byid = {o["id"]: o for o in got}
    check("project_orders: ids come from the file names", [o["id"] for o in got] == ["FO-003", "FO-004", "FO-005"], str([o["id"] for o in got]))
    keys = {"id", "title", "priority", "status", "path", "created", "updated", "station", "stations", "gate", "lane", "run_id", "rework", "events", "line"}
    check("project_orders: exactly the contract keys (plus line)", all(set(o) == keys for o in got), str(sorted(got[0])))
    a = byid["FO-003"]
    check("project_orders: FO-003 folded from the ledger", a["status"] == "building" and a["station"] == "plan" and a["run_id"] == "wf_x"
          and a["lane"] == {"branch": "factory/FO-003-csv", "worktree": "/wt/csv"} and a["created"] == 1000.0 and a["updated"] == 1004.0
          and a["events"] == 5 and a["path"] == "factory/orders/FO-003-csv.md" and a["title"] == "Add CSV export to the reports page"
          and a["priority"] == "p2" and a["line"] == "default" and a["gate"] is None, str(a))
    b = byid["FO-004"]
    check("project_orders: custom line (stations, spec gate auto)", [s["name"] for s in b["stations"]] == ["draft", "ship"] and b["line"] == "fast"
          and b["status"] == "spec" and b["gate"] is None and b["stations"][0]["status"] == "active" and b["events"] == 3, str(b))
    c = byid["FO-005"]
    check("project_orders: a bare markdown order", c["title"] == "Plain markdown order" and c["status"] == "queued" and c["events"] == 0
          and c["priority"] == "p2" and c["created"] is None and isinstance(c["updated"], float))
    check("ledger events of an order without a file are ignored", "FO-050" not in byid)
    check("max_order_num includes ledger ids", F.max_order_num(r6) == 50, str(F.max_order_num(r6)))
    append_ledger(lp, ev("FO-005", "status", 1100.0, status="review"))
    check("project_orders notices a ledger append", {o["id"]: o for o in F.project_orders(r6, 2000.0)}["FO-005"]["gate"] == {"name": "merge", "waiting": True})
    check("project_orders does not mutate the cached ledger", len(F.read_ledger(lp)) == 10)
    again = F.project_orders(r6, 5.0)
    check("project_orders is repeatable", again == F.project_orders(r6, 99.0))
    again[0]["stations"].append("mutated")
    again[0]["events"] = -1
    check("callers can mutate a result freely", F.project_orders(r6, 5.0)[0]["events"] == 5 and len(F.project_orders(r6, 5.0)[0]["stations"]) == 5)

    # bad ledger, good orders
    r7 = fake_repo(base, "badledger")
    write_order(r7, 1, "a")
    put(F.ledger_path(r7), b"\x00\xff\xfe garbage\n" * 50)
    got = F.project_orders(r7, 1.0)
    check("garbage ledger: orders still listed", [o["id"] for o in got] == ["FO-001"] and got[0]["events"] == 0)
    os.remove(F.ledger_path(r7))
    os.symlink(outside, F.ledger_path(r7))
    check("symlinked ledger: orders listed without events", F.project_orders(r7, 1.0)[0]["events"] == 0)

    # line.json drives the fold
    r8 = fake_repo(base, "overridden")
    write_order(r8, 1, "a", title="A")
    write_line(r8, {"lines": {"default": {"stations": [stn("only", title="Only")], "gates": {"spec": "auto"}}}})
    got = F.project_orders(r8, 1.0)
    check("a project's own default line is used", [s["name"] for s in got[0]["stations"]] == ["only"])

    # bounded directory scan
    r9 = fake_repo(base, "scan")
    odir = os.path.join(r9, "factory", "orders")
    os.makedirs(odir)
    for i in range(1, 6201):
        with open(os.path.join(odir, "FO-%d.md" % i), "wb") as fh:
            fh.write(b"---\ntitle: t\n---\n")
    seen = []
    real_scandir = os.scandir

    class Counting:
        def __init__(self, d):
            self.cm = real_scandir(d)

        def __enter__(self):
            self.it = self.cm.__enter__()
            return self

        def __exit__(self, *a):
            return self.cm.__exit__(*a)

        def __iter__(self):
            return self

        def __next__(self):
            e = next(self.it)
            seen.append(e.name)
            return e

    os.scandir = Counting
    try:
        got = F.order_files(r9)
        nums = [n for n, _ in got]
    finally:
        os.scandir = real_scandir
    check("directory scan stops after 5000 entries", len(seen) == 5000, str(len(seen)))
    check("...and the cap still returns 200 sorted files", len(got) == 200 and nums == sorted(nums) and max(nums) <= 6200)


# ── fuzz and latency ──────────────────────────────────────────────────────────
def rand_json(rnd, depth=0):
    k = rnd.randrange(9 if depth < 3 else 6)
    if k == 0:
        return None
    if k == 1:
        return rnd.choice([True, False])
    if k == 2:
        return rnd.choice([0, 1, -1, 2 ** 70, 1.5, -2.5, 1e300, 1700000000.5])
    if k == 3:
        return rnd.choice(["", "FO-003", "build", "status", "x" * 70, "\x00\ud800", "pass", "queued", "spec", "merge", "abcd1234", "\n"])
    if k == 4:
        return rnd.choice(["note", "station", "gate", "lane", "created", "status", "weird"])
    if k == 5:
        return rnd.choice(["FO-%03d" % rnd.randrange(1000), "FO-1", "x"])
    if k == 6:
        return [rand_json(rnd, depth + 1) for _ in range(rnd.randrange(4))]
    return {rnd.choice(["t", "order", "event", "status", "station", "state", "gate", "note", "run_id", "branch", "worktree", "base", "by", "x"]):
            rand_json(rnd, depth + 1) for _ in range(rnd.randrange(8))}


def test_fuzz():
    rnd = random.Random(20261025)
    base = mktemp("factory-fuzz-")
    ok = True
    why = ""
    ev_keys = {"t", "order", "event", "by", "status", "state", "gate", "station", "run_id", "branch", "worktree", "base", "note"}
    for i in range(60):
        root = fake_repo(base, "p%d" % i)
        for n in range(rnd.randrange(1, 12)):
            blob = bytes(rnd.randrange(256) for _ in range(rnd.randrange(0, 3000)))
            if rnd.random() < 0.5:
                blob = b"---\n" + blob + (b"\n---\n" if rnd.random() < 0.7 else b"") + blob
            put(os.path.join(root, "factory", "orders", "FO-%d%s.md" % (rnd.randrange(1, 400), rnd.choice(["", "-x", "-" + "y" * 50]))), blob)
        if rnd.random() < 0.8:
            put(os.path.join(root, "factory", "line.json"),
                json.dumps({"lines": {"default": rand_json(rnd), "fast": rand_json(rnd), "x": {"stations": [rand_json(rnd) for _ in range(5)]}}})
                if rnd.random() < 0.7 else bytes(rnd.randrange(256) for _ in range(rnd.randrange(0, 500))))
        lines = []
        for _ in range(rnd.randrange(0, 300)):
            r = rnd.random()
            if r < 0.5:
                lines.append(json.dumps(rand_json(rnd), default=str))
            elif r < 0.7:
                lines.append(json.dumps({"t": 1.7e9 + rnd.random(), "order": "FO-%03d" % rnd.randrange(1, 50), "event": rnd.choice(F.EVENTS),
                                         "status": rnd.choice(list(F.ALL_STATUSES) + ["x"]), "station": rnd.choice(["plan", "build", "test", "verify", "review", "x"]),
                                         "state": rnd.choice(list(F.STATES) + ["x"]), "gate": rnd.choice(["spec", "merge", "x"])}))
            else:
                lines.append(bytes(rnd.randrange(256) for _ in range(rnd.randrange(0, 200))).replace(b"\n", b""))
        put(F.ledger_path(root), b"\n".join(l.encode() if isinstance(l, str) else l for l in lines))
        try:
            got = F.project_orders(root, 1.7e9)
            for o in got:
                if set(o) != {"id", "title", "priority", "status", "path", "created", "updated", "station", "stations", "gate", "lane",
                              "run_id", "rework", "events", "line"} or o["status"] not in F.ALL_STATUSES or o["priority"] not in F.PRIORITIES:
                    ok, why = False, "bad shape: %s" % o
                try:
                    json.dumps(o, ensure_ascii=False).encode("utf-8")
                except Exception as e:  # noqa: BLE001
                    ok, why = False, "not serialisable: %r" % e
            cfg = F.line_config(root)
            if "default" not in cfg["lines"]:
                ok, why = False, "default line vanished"
            for e in F.read_ledger(F.ledger_path(root)):
                if not set(e) <= ev_keys:
                    ok, why = False, "unknown key survived: %s" % e
            F.max_order_num(root)
        except BaseException as e:  # noqa: BLE001
            ok, why = False, "iteration %d raised %r" % (i, e)
            break
    check("fuzz: random bytes as orders, line.json and ledger never raise and keep the shape", ok, why)

    bad = ""
    for _ in range(4000):
        rec = rand_json(rnd)
        try:
            e = F.valid_event(rec)
            if e is not None:
                json.dumps(e, ensure_ascii=False).encode("utf-8")
        except BaseException as exc:  # noqa: BLE001
            bad = repr(exc)
            break
    check("fuzz: valid_event on random JSON values", not bad, bad)
    bad = ""
    for _ in range(300):
        try:
            F.norm_id(rand_json(rnd))
            F.slugify(rand_json(rnd), rand_json(rnd))
            F.clip(rand_json(rnd), rand_json(rnd))
            F.fold(rand_json(rnd), rand_json(rnd), rand_json(rnd), rand_json(rnd))
            F.line_for(rand_json(rnd), rand_json(rnd))
        except BaseException as exc:  # noqa: BLE001
            bad = repr(exc)
            break
    check("fuzz: the pure helpers accept any JSON value", not bad, bad)


def test_latency():
    base = mktemp("factory-lat-")
    root = fake_repo(base, "proj")
    for n in range(1, 201):
        write_order(root, n, "order-number-%d" % n, text="---\nid: FO-%03d\ntitle: Order %d with a realistic title\npriority: p2\n"
                    "created: 2026-10-20\nline: default\n---\n" % (n, n) + "## Intent\n" + "Some prose about the work. " * 40 + "\n")
    rnd = random.Random(5)
    lp = F.ledger_path(root)
    os.makedirs(os.path.dirname(lp))
    size, i = 0, 0
    with open(lp, "wb") as fh:
        while size < 4 * 1024 * 1024 + 50000:
            n = rnd.randrange(1, 201)
            line = json.dumps({"t": 1700000000 + i * 0.5, "order": "FO-%03d" % n, "event": "station", "by": "cc-factory",
                               "station": rnd.choice(["plan", "build", "test", "verify", "review"]), "state": rnd.choice(["start", "pass", "fail"]),
                               "run_id": "wf_%06d" % n, "note": "latency fixture line %d" % i}).encode() + b"\n"
            fh.write(line)
            size += len(line)
            i += 1
    t0 = time.perf_counter()
    first = F.project_orders(root, 1.0)
    t1 = time.perf_counter() - t0
    t0 = time.perf_counter()
    second = F.project_orders(root, 1.0)
    t2 = time.perf_counter() - t0
    check("latency fixture: 200 orders and a ledger over 4 MiB", len(first) == 200 and os.path.getsize(lp) > F.MAX_LEDGER_BYTES)
    check("latency: first project_orders under 3 s", t1 < 3.0, "%.2fs" % t1)
    check("latency: the cached second call is faster than the first", t2 < t1, "%.3fs vs %.3fs" % (t2, t1))
    check("latency: both calls agree", first == second)
    print("  (latency: first %.0f ms, cached %.0f ms, %d events)" % (t1 * 1000, t2 * 1000, i))


# ── review fixes: linear heading, path-shaped worktree, control-free order names ──
def test_heading_linear():
    import re as _re
    old = _re.compile(r"\s+#+\s*$")   # the regex _heading used to run: its semantics, minus the quadratic cost
    cases = {"Title ##": "Title", "C#": "C#", "C# #": "C#", "a # b #": "a # b", "###": "###", "x##": "x##",
             "Title\t#\t": "Title", "Title ## ##": "Title ##", "  ###": "", "Title": "Title", "Ti tle  ": "Ti tle  ",
             "#": "#", " #": "", "a#  ": "a#  ", "a #": "a"}
    for raw, want in cases.items():
        got = F._strip_closing_hashes(raw)
        check("closing hashes: %r -> %r" % (raw, want), got == want and old.sub("", raw) == want, (got, old.sub("", raw)))
    rnd = random.Random(11)
    diff = []
    for _ in range(3000):
        s = "".join(rnd.choice(" #a\t") for _ in range(rnd.randrange(0, 9)))
        if F._strip_closing_hashes(s).strip() != old.sub("", s).strip():
            diff.append(s)
    check("closing hashes: same result as the old regex on 3000 random short lines", not diff, diff[:5])
    base = mktemp("factory-redos-")
    root = fake_repo(base, "proj")
    write_order(root, 1, "spaces", text=("# a" + " " * (256 * 1024 - 10) + "x\n"))
    write_order(root, 2, "tabs", text=("# " + "\t " * (128 * 1024 - 64) + "#\n## more\n# Second\n"))  # just under 256 KiB
    write_order(root, 3, "hashes", text=("# T" + " #" * (128 * 1024 - 8) + "\n"))
    t0 = time.perf_counter()
    out = F.project_orders(root, 1.0)
    took = time.perf_counter() - t0
    check("a 256 KiB pathological heading goes through project_orders in under 0.2 s", took < 0.2 and len(out) == 3, "%.3fs" % took)
    titles = {o["id"]: o["title"] for o in out}
    check("pathological headings: a long line is read up to its scan limit", titles.get("FO-001") == "a", titles.get("FO-001"))
    check("pathological headings: an all-blank heading falls through to the next one", titles.get("FO-002") == "Second", titles.get("FO-002"))
    check("pathological headings: titles are clipped to 200", all(len(t) <= 200 for t in titles.values()), titles)
    o = F.read_order(root, write_order(root, 4, "csharp", text="# Port it to C#\n"))
    check("read_order: '# Port it to C#' keeps its hash", o["title"] == "Port it to C#", o["title"])


def test_worktree_and_names():
    ok = lambda **kw: dict({"t": 1700000000.5, "order": "FO-003", "event": "lane"}, **kw)  # noqa: E731
    check("valid_event: a worktree keeps two spaces in a row",
          F.valid_event(ok(worktree="/home/u/My  Projects/x"))["worktree"] == "/home/u/My  Projects/x")
    check("valid_event: a worktree keeps leading and trailing spaces (they are part of a path)",
          F.valid_event(ok(worktree="/a/b "))["worktree"] == "/a/b ")
    check("valid_event: a worktree loses control characters, tab and newline included",
          F.valid_event(ok(worktree="/a\t/b\n/c\x1b[2J\x9b"))["worktree"] == "/a/b/c[2J")
    check("valid_event: a worktree is clipped to 1024", len(F.valid_event(ok(worktree="/" + "w" * 2000))["worktree"]) == 1024)
    check("valid_event: an all-blank worktree is dropped", "worktree" not in F.valid_event(ok(worktree="   ")))
    check("valid_event: branch still collapses whitespace", F.valid_event(ok(branch="a  b"))["branch"] == "a b")
    base = mktemp("factory-names-")
    root = fake_repo(base, "proj")
    write_order(root, 1, "plain")
    for name in ("FO-002-\x1b]0;PWNED\x07.md", "FO-003-tab\there.md", "FO-004-nl\nx.md", "FO-005-c1\x9b31m.md", "FO-006-del\x7f.md"):
        put(os.path.join(root, "factory", "orders", name), "---\ntitle: hostile name\n---\n")
    nums = [n for n, _ in F.order_files(root)]
    check("order names with control characters are not orders", nums == [1], nums)
    check("project_orders lists only the clean name", [o["id"] for o in F.project_orders(root, 1.0)] == ["FO-001"])
    check("find_order does not find a control-character name", F.find_order(root, "FO-002") is None)
    write_order(root, 7, "café-✓")
    check("a non-ASCII slug is still an order", [n for n, _ in F.order_files(root)] == [1, 7])


# ── module hygiene ────────────────────────────────────────────────────────────
STDLIB_OK ={"copy", "datetime", "hashlib", "itertools", "json", "math", "os", "re", "stat", "threading", "time", "unicodedata"}


def test_module_hygiene():
    import ast
    path = os.path.join(REPO, "lib", "cc_dash", "factory.py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    mods, relative = set(), False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            relative = relative or bool(node.level)
            mods.add((node.module or "").split(".")[0])
    check("factory.py imports only the stdlib modules it declares", mods <= STDLIB_OK and not relative, str(sorted(mods - STDLIB_OK)))
    top = [type(n).__name__ for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.Assign, ast.Expr))]
    check("factory.py has only imports, defs, assignments and the docstring at top level", not top, str(top))

    work = mktemp("factory-import-")
    snippet = (
        "import importlib.util, sys\n"
        "sys.dont_write_bytecode = True\n"
        "spec = importlib.util.spec_from_file_location('cc_factory_lib', sys.argv[1])\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(m)\n"
        "print(sorted(k for k in sys.modules if 'cc_dash' in k))\n"
        "print(m.norm_id(' 7 '), m.line_config('/nonexistent') == m.DEFAULT_LINE)\n")
    before = set(os.listdir(work))
    r = subprocess.run([sys.executable, "-I", "-c", snippet, path], cwd=work, capture_output=True, text=True, timeout=30,
                       env={"PATH": os.environ.get("PATH", ""), "HOME": work})
    check("loads by path in an isolated interpreter without importing cc_dash", r.returncode == 0 and r.stdout.splitlines() == [
        "[]", "FO-007 True"], r.stdout + r.stderr)
    check("importing it writes nothing into the working directory", set(os.listdir(work)) == before)


# ── main ──────────────────────────────────────────────────────────────────────
def _timeout(signum, frame):  # a read that hangs on a FIFO must fail the run, not freeze it
    print("FAIL timeout  the suite did not finish in 300 s (a read hung?)")
    os._exit(1)


def run_library():
    if hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, _timeout)
        signal.alarm(300)
    try:
        for fn in (test_text_helpers, test_parse_order_text, test_read_order, test_line_config, test_ledger, test_fold,
                   test_git_layouts, test_real_git, test_project_orders, test_fuzz, test_latency, test_heading_linear,
                   test_worktree_and_names, test_module_hygiene):
            try:
                fn()
            except BaseException as e:  # noqa: BLE001 - one broken section must not hide the rest
                import traceback
                check("%s completed" % fn.__name__, False, "".join(traceback.format_exception(type(e), e, e.__traceback__))[-1500:])
        check("no em-dash in the module", chr(0x2014) not in open(os.path.join(REPO, "lib", "cc_dash", "factory.py"), encoding="utf-8").read())
    finally:
        if hasattr(signal, "SIGALRM"):
            signal.alarm(0)
        for d in TEMPS:
            shutil.rmtree(d, ignore_errors=True)
    return RESULTS


def run_part(fname):
    """Load tests/<fname> by path and run its main(); its (name, ok, detail) RESULTS.
    Each part keeps its own temp dirs and prints its own FAIL lines."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(fname[:-3], os.path.join(REPO, "tests", fname))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.main()
    return mod.RESULTS


PARTS = {"lib": None, "cli": "factory_cli_cases.py", "gates": "factory_gate_cases.py", "setup": "factory_setup_cases.py",
         "workflow": "factory_workflow_cases.py"}


def main(argv=None):
    names = list(argv if argv is not None else sys.argv[1:]) or list(PARTS)
    bad = [n for n in names if n not in PARTS]
    if bad:
        print("unknown part(s): %s (parts: %s)" % (", ".join(bad), ", ".join(PARTS)))
        return 2
    total = failed = 0
    for name in names:
        t0 = time.time()
        try:
            results = run_library() if PARTS[name] is None else run_part(PARTS[name])
        except BaseException as e:  # noqa: BLE001 - a part that cannot even load is one failure
            print("FAIL %s part did not run  %s: %s" % (name, type(e).__name__, e))
            results = [(name, False, "did not run")]
        bad_n = sum(1 for r in results if not r[1])
        total += len(results)
        failed += bad_n
        print("-- %-8s %d passed, %d failed (%.0f s)" % (name, len(results) - bad_n, bad_n, time.time() - t0))
    why = GUARD.problem()
    if why:
        print("FAIL state guard: the real cc_tool state dir is untouched  " + why)
        failed += 1
    total += 1
    print("%d passed, %d failed" % (total - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
