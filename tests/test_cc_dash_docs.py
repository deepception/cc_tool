#!/usr/bin/env python3
"""The cc-dash Docs view: lib/cc_dash/docs.py, its /api/docs/* endpoints, the
sandboxed HTML frame, and the Markdown renderer (lib/cc_dash/web/markdown.js).

Builds a temp project (a main checkout with a .claude/worktrees/ worktree
that has its own session, so the two must fold into one project) full of
docs, secrets, symlinks, a FIFO and oversized files, plus a fake
${CLAUDE_CONFIG_DIR}/projects tree, then checks:
  - project allowlisting (only resolved projects, never a client path)
  - path safety: ../, absolute, %2e%2e, double encoding, backslash, NUL, ~,
    symlink escapes (file and directory), hidden and skipped directories
  - extension, secret-file and FIFO refusal, size caps, redaction
  - tree sections, skipped folders, counts, cache invalidation
  - search bounds (min length, per-file and total caps, truncation)
  - the pairing token on every endpoint
  - the HTML frame: one-time ticket, CSP with `sandbox` and never
    allow-same-origin, no script unless asked, the dashboard CSP
  - backlinks
  - the renderer in node (skipped without node): raw <script>, javascript:
    and data: links, nested lists, tables, wikilinks, front matter, fences
    with backticks inside, and an allowlist of output tags and attributes
  - optional: headless Chrome (system google-chrome + playwright-core from
    color-analyzer/webapp, skipped when absent) loads a hostile HTML doc in
    the real Docs view and checks it can neither read the token nor call /api.

Run:  python3 tests/test_cc_dash_docs.py
"""
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import quote

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "lib"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cc_dash import docs as docsmod  # noqa: E402
from cc_dash.server import CSP, make_server  # noqa: E402
from cc_dash.store import Store  # noqa: E402
from dash_pairing import pair  # noqa: E402
from test_cc_dash_projects import write_session  # noqa: E402

RESULTS = []
PW = "/home/lukasz/Files/hobby/color-analyzer/webapp/node_modules/playwright-core"


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def w(root, rel, text="", mode="w"):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, mode) as fh:
        fh.write(text)
    return p


def build(base):
    cfg = os.path.join(base, "claude")
    proj = os.path.join(base, "work", "proj")
    other = os.path.join(base, "work", "other")
    outside = os.path.join(base, "work", "outside")
    for d in (proj, other, outside):
        os.makedirs(os.path.join(d, ".git") if d != outside else d, exist_ok=True)
    w(outside, "secret.md", "OUTSIDE-SECRET")
    w(base, "work/outside.md", "OUTSIDE-FILE")
    files = {
        "README.md": "# Proj\n\nSee [guide](docs/guide.md).\n",
        "CHANGELOG.md": "# Changes\n",
        "CLAUDE.md": "# Claude\n",
        "AGENTS.md": "# Agents\n",
        "notes.txt": "plain needle text\n",
        "requirements.txt": "flask\n",
        "vault/README.md": "# Vault\n",
        "vault/AUTOMATION.md": "# Automation\n",
        "vault/index.md": "- [[alpha]]\n",
        "vault/notes/alpha.md": "---\ntitle: Alpha\ntags: [x, y]\n---\n# Alpha\n\nLinks to [[beta]] and [[missing]].\n",
        "vault/notes/beta.md": "# Beta\n\nBack to [[alpha|A]] and [[alpha#Alpha]].\n",
        "vault/daily/2026-10-20.md": "# Digest\n\nneedle in the digest\n",
        "vault/inbox/idea.md": "an idea\n",
        "docs/superpowers/plans/2026-10-01-plan.md": "# Plan\n\n- [ ] step one\n- [x] step two\n",
        "docs/superpowers/specs/spec.md": "# Spec\n",
        "docs/superpowers/research/r.md": "# Research\n",
        "docs/guide.md": "# Guide\n\n" + "needle line\n" * 20,
        "docs/unicode.md": "\u0130" * 300 + "\nunicodeneedle " + "y" * 800 + "\n",
        "docs/minified.html": "<div>" * 200000,
        "docs/page.html": "<!doctype html><title>p</title><p>hello html</p><script>fetch('/api/live')</script>",
        "docs/keys.md": "real sk-ant-" + "A1b2C3d4" * 5 + " and placeholder sk-ant-your-key-goes-here-example\n",
        "docs/img/pic.svg": "<svg xmlns='http://www.w3.org/2000/svg'/>",
        ".claude/skills/s1/SKILL.md": "# Skill\n",
        ".claude/skills/s1/references/r.md": "# Ref\n",
        ".claude/skills/.hidden/x.md": "# hidden\n",
        ".claude/agents/a.md": "# agent\n",
        ".claude/worktrees/wt1/README.md": "# worktree copy\n",
        "node_modules/pkg/README.md": "# pkg\n",
        ".git/x.md": "# git\n",
        ".env": "TOKEN=1\n",
        ".env.md": "# env md\n",
        "id_rsa.txt": "key\n",
        "credentials.txt": "creds\n",
        "key.pem": "pem\n",
        "sub/.hidden.md": "# hidden file\n",
        "src/lib/notes.md": "# Other notes\n",
        "src/app.py": "print(1)\n",
        "dist/out.md": "# built\n",
        "docs/plans/quiet-fox.md": "# Quiet fox\n",
        ".cc-board/run1/lab/checkpoint.md": "# Checkpoint\nsecret ghp_a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8\n",
        ".cc-board/run1/lab/report.md": "# Report\n",
        ".cc-board/run1/lab/data.json": "{}\n",
        ".cc-board/run1/lab/shot.png": "png\n",
        ".cc-board/run1/.hid/x.md": "# hidden board dir\n",
        ".cc-board/x.json": "{}\n",
        ".factory-holdout/FO-001/scenario.md": "# holdout scenario\n",
    }
    for rel, text in files.items():
        w(proj, rel, text)
    w(proj, "docs/swap/a.md", "inside")
    w(outside, "a.md", "OUTSIDE-SECRET swap")
    w(proj, "docs/img/pic.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 32, "wb")
    with open(w(proj, "docs/big.md", ""), "r+b") as fh:
        fh.truncate(docsmod.MAX_DOC + 1)
    with open(w(proj, "docs/img/huge.png", b"", "wb"), "r+b") as fh:
        fh.truncate(docsmod.MAX_IMG + 1)
    os.symlink(os.path.join(base, "work", "outside.md"), os.path.join(proj, "docs", "link-out.md"))
    os.symlink("../README.md", os.path.join(proj, "docs", "link-in.md"))
    os.symlink("../.env", os.path.join(proj, "docs", "link-env.md"))
    os.symlink(outside, os.path.join(proj, "docs", "dirlink"))
    os.symlink(os.path.join(base, "work", "outside.md"), os.path.join(proj, ".cc-board", "run1", "lab", "link.md"))
    os.mkfifo(os.path.join(proj, "docs", "fifo.md"))
    w(other, "README.md", "# Other project\n")
    now = time.time()
    write_session(cfg, proj, "11111111-0000-0000-0000-000000000001", now - 600)
    write_session(cfg, os.path.join(proj, ".claude", "worktrees", "wt1"), "11111111-0000-0000-0000-000000000002", now - 500)
    write_session(cfg, other, "22222222-0000-0000-0000-000000000001", now - 400)
    return cfg, proj, other


def req(port, path, token=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    hd = {"Host": f"127.0.0.1:{port}"}
    if token:
        hd["X-CC-Dash-Token"] = token
    hd.update(headers or {})
    c.request("GET", path, headers=hd)
    r = c.getresponse()
    body = r.read()
    out = (r.status, body, {k.lower(): v for k, v in r.getheaders()})
    c.close()
    return out


def run(base):
    cfg, proj, other = build(base)
    store = Store(root=cfg, days=7)
    store.refresh(force=True)
    srv = make_server(0, store, fallback=True)
    port = srv.port
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
    tok = pair(srv)
    P = "proj"

    def get(path, **kw):
        return req(port, path, tok, **kw)

    def jget(path):
        st, body, hd = get(path)
        try:
            return st, json.loads(body), hd
        except ValueError:
            return st, {"raw": body[:200]}, hd

    def file(rel):
        return jget("/api/docs/file?project=" + P + "&path=" + quote(rel, safe=""))

    try:
        # ── auth ──────────────────────────────────────────────────────────
        eps = ["/api/docs/projects", "/api/docs/tree?project=proj", "/api/docs/file?project=proj&path=README.md",
               "/api/docs/search?project=proj&q=needle", "/api/docs/asset?project=proj&path=docs/img/pic.png",
               "/api/docs/frame?project=proj&path=docs/page.html"]
        for ep in eps:
            st, _, _ = req(port, ep)
            st2, _, _ = req(port, ep, "not-the-token")
            check("auth: %s needs the token" % ep.split("?")[0], st == 401 and st2 == 401, f"{st} {st2}")
        st, _, _ = req(port, "/api/docs/projects", tok, headers={"Host": "evil.example:%d" % port})
        check("auth: a foreign Host header is refused", st == 421, str(st))

        # ── projects ─────────────────────────────────────────────────────
        st, d, _ = jget("/api/docs/projects")
        names = [p["project"] for p in d.get("projects", [])]
        check("projects: one entry per resolved project, the worktree folded in", st == 200 and sorted(names) == ["other", "proj"], str(names))
        pr = next((p for p in d.get("projects", []) if p["project"] == "proj"), {})
        check("projects: the worktree session counts toward its repo", pr.get("sessions") == 2, json.dumps(pr))
        check("projects: roots are shown with ~ or as the main checkout", pr.get("root", "").endswith("/work/proj"), pr.get("root"))
        for bad in ["nope", "../proj", proj, "/etc", "", "proj/../other"]:
            st, d, _ = jget("/api/docs/tree?project=" + quote(bad, safe=""))
            check("projects: %r is not a project" % bad, st in (400, 404), f"{st} {d}")
        st, d, _ = jget("/api/docs/file?project=" + quote(proj, safe="") + "&path=README.md")
        check("projects: an absolute root as project is refused", st == 404, str(st))

        # ── tree ─────────────────────────────────────────────────────────
        st, t, _ = jget("/api/docs/tree?project=proj")
        paths = {f["path"]: f["section"] for f in t.get("files", [])}
        expect = {
            "README.md": "root", "CHANGELOG.md": "root", "CLAUDE.md": "root", "AGENTS.md": "root", "notes.txt": "root",
            "vault/README.md": "vault", "vault/AUTOMATION.md": "vault", "vault/index.md": "vault", "vault/notes/alpha.md": "vault",
            "vault/daily/2026-10-20.md": "vault", "vault/inbox/idea.md": "vault",
            "docs/superpowers/plans/2026-10-01-plan.md": "plans", "docs/superpowers/specs/spec.md": "plans",
            "docs/superpowers/research/r.md": "plans", "docs/guide.md": "docs", "docs/page.html": "docs",
            ".claude/skills/s1/SKILL.md": "skills", ".claude/skills/s1/references/r.md": "skills", "src/lib/notes.md": "other",
            "docs/plans/quiet-fox.md": "plans", ".cc-board/run1/lab/checkpoint.md": "board", ".cc-board/run1/lab/report.md": "board",
        }
        wrong = {k: (paths.get(k), v) for k, v in expect.items() if paths.get(k) != v}
        check("tree: files land in their sections", st == 200 and not wrong, json.dumps(wrong))
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib", "cc_dash", "web", "docs.js")) as fh:
            check("tree: the page renders the board section", "{ k: 'board'," in fh.read())
        never = [".claude/skills/.hidden/x.md", ".claude/agents/a.md", ".claude/worktrees/wt1/README.md", "node_modules/pkg/README.md",
                 ".git/x.md", ".env", ".env.md", "id_rsa.txt", "credentials.txt", "sub/.hidden.md", "dist/out.md",
                 "docs/link-out.md", "docs/link-in.md", "docs/link-env.md", "docs/dirlink/secret.md", "docs/fifo.md", "requirements.txt",
                 ".cc-board/run1/lab/link.md", ".cc-board/x.json", ".cc-board/run1/lab/data.json", ".cc-board/run1/lab/shot.png",
                 ".cc-board/run1/.hid/x.md", ".factory-holdout/FO-001/scenario.md"]
        listed = [p for p in never if p in paths]
        check("tree: skipped folders, dotfiles, secrets, symlinks and FIFOs are not listed", not listed, str(listed))
        check("tree: section counts add up", sum(t.get("counts", {}).values()) == len(paths) and t["counts"].get("vault") == 7, json.dumps(t.get("counts")))
        check("tree: no file carries an absolute path", all(not f["path"].startswith("/") for f in t["files"]))
        docsmod_ttl = docsmod.TREE_TTL
        docsmod.TREE_TTL = 0
        w(proj, "docs/new-doc.md", "# New\n")
        st, t2, _ = jget("/api/docs/tree?project=proj")
        check("tree: a new file shows up once the directory's mtime changes", "docs/new-doc.md" in {f["path"] for f in t2["files"]})
        old = next(f["mtime"] for f in t2["files"] if f["path"] == "docs/guide.md")
        os.utime(os.path.join(proj, "docs", "guide.md"), (old + 50, old + 50))
        st, t3, _ = jget("/api/docs/tree?project=proj")
        new = next(f["mtime"] for f in t3["files"] if f["path"] == "docs/guide.md")
        check("tree: a changed file's mtime is refreshed (Recently changed)", abs(new - (old + 50)) < 1, f"{old} {new}")
        docsmod.TREE_TTL = docsmod_ttl

        # ── reading files ────────────────────────────────────────────────
        st, d, _ = file("vault/notes/alpha.md")
        check("file: a vault note is served with its text and version", st == 200 and "[[beta]]" in d.get("text", "") and d.get("version") and d.get("kind") == "md", str(st))
        st2, d2, _ = jget("/api/docs/file?project=proj&path=vault/notes/alpha.md&known=" + quote(d.get("version", "")))
        check("file: an unchanged file answers {unchanged} for the live-reload poll", st2 == 200 and d2.get("unchanged") is True and "text" not in d2, json.dumps(d2)[:200])
        st, d, _ = file(".claude/skills/s1/SKILL.md")
        check("file: .claude/skills/ is served", st == 200, str(st))
        st, d, _ = file("docs/link-in.md")
        check("file: a symlink to a file inside the project is followed", st == 200 and "# Proj" in d.get("text", ""), str(st))
        st, d, _ = file("vault/notes/beta.md")
        check("backlinks: a note lists the notes that link to it", st == 200 and "vault/notes/alpha.md" in d.get("backlinks", []) and "vault/index.md" not in d.get("backlinks", []), json.dumps(d.get("backlinks")))
        st, d, _ = file("vault/notes/alpha.md")
        check("backlinks: [[alpha|alias]] and [[alpha#heading]] count, a note never links itself",
              set(d.get("backlinks", [])) == {"vault/notes/beta.md", "vault/index.md"}, json.dumps(d.get("backlinks")))
        st, d, _ = file("docs/keys.md")
        txt = d.get("text", "")
        check("redaction: a live-looking API key is replaced", st == 200 and "[redacted: Anthropic API key]" in txt and "A1b2C3d4A1b2" not in txt, txt[:200])
        st, d, _ = file(".cc-board/run1/lab/checkpoint.md")
        check("board: a checkpoint is served and its secret redacted", st == 200 and "[redacted: GitHub token]" in d.get("text", "")
              and "ghp_a1B2" not in d.get("text", "") and d.get("section") == "board", str(st))
        check("redaction: an obvious placeholder stays", "sk-ant-your-key-goes-here-example" in txt, txt[:200])

        # ── traversal and refusals ──────────────────────────────────────
        cases = {
            "../outside.md": (400,), "../../outside.md": (400,), "docs/../../outside.md": (400,), "/etc/passwd": (400,),
            "..%2Foutside.md": (400,),                       # %2F decoded once by the server: still ../
            "%2e%2e/outside.md": (400,), "%252e%252e/outside.md": (404,),
            "..\\outside.md": (400,), "README.md\x00.png": (400,), "~/x.md": (400,), "./README.md": (400,),
            "docs/link-out.md": (403,), "docs/dirlink/secret.md": (403,), "docs/link-env.md": (403,),
            ".env": (403, 415), ".env.md": (403,), "id_rsa.txt": (403,), "credentials.txt": (403,), "key.pem": (415,),
            "src/app.py": (415,), "docs/img/pic.png": (415,), ".git/x.md": (403,), "node_modules/pkg/README.md": (403,),
            ".claude/agents/a.md": (403,), ".claude/skills/.hidden/x.md": (403,), ".claude/worktrees/wt1/README.md": (403,),
            ".cc-board/x.json": (403,), ".cc-board/run1/lab/data.json": (403,), ".cc-board/run1/.hid/x.md": (403,),
            ".cc-board/run1/lab/link.md": (403,), ".factory-holdout/FO-001/scenario.md": (403,), ".factory-holdout": (403,),
            "sub/.hidden.md": (403,), "docs/nope.md": (404,), "": (400,), "a" * 1100 + ".md": (400,),
        }
        for rel, want in cases.items():
            raw = rel if "%" in rel else quote(rel, safe="")
            if rel == "%2e%2e/outside.md":
                raw = "%2e%2e%2Foutside.md"
            if rel == "%252e%252e/outside.md":
                raw = "%252e%252e%2Foutside.md"
            if rel == "..%2Foutside.md":
                raw = "..%2Foutside.md"
            st, body, _ = get("/api/docs/file?project=proj&path=" + raw)
            check("refuse: %r -> %s" % (rel[:40], "/".join(map(str, want))), st in want and b"OUTSIDE" not in body and b"TOKEN=1" not in body, f"{st} {body[:120]}")
        t0 = time.time()
        st, body, _ = get("/api/docs/file?project=proj&path=docs/fifo.md")
        check("refuse: a FIFO is refused at once, never read", st == 403 and time.time() - t0 < 3, f"{st} {time.time() - t0:.1f}s")
        st, body, _ = get("/api/docs/file?project=proj&path=docs/big.md")
        check("size: a doc over 2 MB is refused", st == 413, str(st))
        st, body, _ = get("/api/docs/asset?project=proj&path=docs/img/huge.png")
        check("size: an image over 10 MB is refused", st == 413, str(st))
        st, body, hd = get("/api/docs/asset?project=proj&path=docs/img/pic.png")
        check("asset: an image is served with its type, sandboxed", st == 200 and hd.get("content-type") == "image/png" and body.startswith(b"\x89PNG")
              and "sandbox" in hd.get("content-security-policy", "") and hd.get("x-content-type-options") == "nosniff", json.dumps(hd))
        for rel in ("README.md", "../outside.md", "docs/dirlink/secret.md", ".env"):
            st, body, _ = get("/api/docs/asset?project=proj&path=" + quote(rel, safe=""))
            check("asset: %r is not an image we serve" % rel, st in (400, 403, 415) and b"OUTSIDE" not in body, str(st))

        # ── search ───────────────────────────────────────────────────────
        st, d, _ = jget("/api/docs/search?project=proj&q=needle")
        res = d.get("results", [])
        check("search: finds hits across docs with line numbers", st == 200 and {r["path"] for r in res if r["line"]} >= {"docs/guide.md", "notes.txt", "vault/daily/2026-10-20.md"}, json.dumps(res)[:300])
        check("search: hit ranges point at the query", all(r["text"][a:b].lower() == "needle" for r in res if r["line"] for a, b in r["hits"]), json.dumps(res)[:300])
        check("search: at most 5 hits per file", sum(1 for r in res if r["path"] == "docs/guide.md") == docsmod.SEARCH_PER_FILE)
        st, d, _ = jget("/api/docs/search?project=proj&q=n")
        check("search: one character searches nothing", st == 200 and d.get("results") == [], json.dumps(d))
        st, d, _ = jget("/api/docs/search?project=proj&q=sk-ant")
        check("search: snippets are redacted too", all("A1b2C3d4A1b2" not in r["text"] for r in d.get("results", [])) and d.get("results"), json.dumps(d)[:300])
        st, d, _ = jget("/api/docs/search?project=proj&q=OUTSIDE")
        check("search: never reaches files outside the project or behind symlinks", st == 200 and not [r for r in d["results"] if r["line"]], json.dumps(d)[:300])
        cap = docsmod.SEARCH_MAX_RESULTS
        docsmod.SEARCH_MAX_RESULTS = 3
        st, d, _ = jget("/api/docs/search?project=proj&q=needle")
        check("search: the result cap holds and says it was truncated", len(d["results"]) == 3 and d["truncated"] is True, json.dumps(d)[:200])
        docsmod.SEARCH_MAX_RESULTS = cap
        fcap = docsmod.SEARCH_MAX_FILES
        docsmod.SEARCH_MAX_FILES = 2
        st, d, _ = jget("/api/docs/search?project=proj&q=needle")
        check("search: the file cap holds", d["files"] <= 2 and d["truncated"] is True, json.dumps(d)[:200])
        docsmod.SEARCH_MAX_FILES = fcap
        st, d, _ = jget("/api/docs/search?project=proj&q=unicodeneedle")
        r0 = (d.get("results") or [{}])[0]
        check("search: offsets stay right when lower() changes lengths (dotted I)", st == 200 and r0.get("hits")
              and all(r0["text"][a:b].lower() == "unicodeneedle" for a, b in r0["hits"]), json.dumps(d)[:300])
        t0 = time.time()
        st, d, _ = jget("/api/docs/search?project=proj&q=div")
        check("search: a 1 MB one-line file with 200,000 hits answers fast", st == 200 and time.time() - t0 < 3
              and sum(1 for r in d["results"] if r["path"] == "docs/minified.html") == 1, f"{time.time() - t0:.2f}s")
        t0 = time.time()
        for s_ in ("-eyJ" * 500000, "eyJ" + "a" * 2000000, "-sk-" * 500000, "AKIA" * 500000, "xoxb-" * 400000):
            docsmod.redact(s_)
        check("redaction: linear on 2 MB of near-miss tokens (no quadratic regex)", time.time() - t0 < 5, f"{time.time() - t0:.2f}s")
        redaction_edges()
        # a directory swapped for a symlink between the check and the open is caught
        docs = srv.docs
        _, real = docs.resolve(proj, "docs/swap/a.md", ("doc",)) if os.path.isdir(os.path.join(proj, "docs", "swap")) else (None, None)
        os.rename(os.path.join(proj, "docs", "swap"), os.path.join(proj, "docs", "swap-moved"))
        os.symlink(os.path.join(base, "work", "outside"), os.path.join(proj, "docs", "swap"))
        try:
            docs._open(real, docsmod.MAX_DOC)
            raced = "read"
        except docsmod.DocsError as e:
            raced = e.status
        check("race: a directory swapped for an outside symlink after the check is refused at open", raced == 403, str(raced))
        st, d, _ = jget("/api/docs/search?project=proj&q=" + quote("x" * 500))
        check("search: a long query is clipped, not an error", st == 200 and len(d["q"]) == 200)

        # ── HTML: never in the page, only in the sandboxed frame ────────
        st, d, _ = file("docs/page.html")
        check("html: the file endpoint never returns HTML content", st == 200 and d.get("kind") == "html" and "text" not in d, json.dumps(d)[:200])
        st, body, hd = get("/")
        csp = hd.get("content-security-policy", "")
        check("csp: the dashboard allows same-origin frames and blob: images, nothing more",
              "frame-src 'self'" in csp and "img-src 'self' data: blob:" in csp and "script-src 'self';" in csp and "frame-ancestors 'none'" in csp
              and "unsafe" not in csp and csp == CSP, csp)
        st, d, _ = jget("/api/docs/frame?project=proj&path=docs/page.html")
        url = d.get("url", "")
        check("html: a frame ticket is a one-time URL, not a path", st == 200 and url.startswith("/docs-frame/") and "page.html" not in url, json.dumps(d))
        st, body, hd = req(port, url, headers={"Sec-Fetch-Dest": "iframe"})       # the iframe sends no token
        fcsp = hd.get("content-security-policy", "")
        check("html: the frame serves the document without the token", st == 200 and b"hello html" in body and tok.encode() not in body, str(st))
        check("html: frame CSP is sandboxed (opaque origin), never allow-same-origin",
              fcsp.endswith("sandbox") and "allow-same-origin" not in fcsp and "default-src 'none'" in fcsp, fcsp)
        check("html: no script without the toggle, and no connect/frame/form sources",
              "script-src" not in fcsp and "connect-src" not in fcsp and "form-action 'none'" in fcsp and "frame-ancestors 'self'" in fcsp, fcsp)
        check("html: images only from data:/blob:, inline styles allowed", "img-src data: blob:" in fcsp and "style-src 'unsafe-inline'" in fcsp, fcsp)
        st, body, _ = req(port, url, headers={"Sec-Fetch-Dest": "iframe"})
        check("html: a ticket works once", st == 404, str(st))
        st, d, _ = jget("/api/docs/frame?project=proj&path=docs/page.html&scripts=1")
        st, body, hd = req(port, d.get("url", "/x"), headers={"Sec-Fetch-Dest": "iframe"})
        fcsp = hd.get("content-security-policy", "")
        check("html: allow-scripts adds inline script only, still sandboxed, still no same-origin",
              st == 200 and "sandbox allow-scripts" in fcsp and "script-src 'unsafe-inline'" in fcsp and "allow-same-origin" not in fcsp
              and "connect-src" not in fcsp and "default-src 'none'" in fcsp, fcsp)
        st, d, _ = jget("/api/docs/frame?project=proj&path=docs/page.html")
        st, body, _ = req(port, d.get("url", "/x"), headers={"Sec-Fetch-Dest": "document"})
        check("html: the frame URL refuses a top-level navigation", st == 403, str(st))
        st, d, _ = jget("/api/docs/frame?project=proj&path=README.md")
        check("html: only HTML files get a frame", st == 415, str(st))
        st, body, _ = req(port, "/docs-frame/made-up-ticket")
        check("html: an unknown ticket is a 404", st == 404, str(st))
        st, body, _ = req(port, "/api/live")
        check("html: what the frame could reach, /api without the token, is 401", st == 401, str(st))
        st, body, hd = get("/markdown.js")
        check("assets: markdown.js, docs.js and docs.css are served", st == 200 and get("/docs.js")[0] == 200 and get("/docs.css")[0] == 200)
        guard_parity()
        renderer_tests()
        chrome_test(srv, port)
    finally:
        srv.shutdown()
        srv.server_close()


# Pattern-valid, made-up credentials (no placeholder words, so they must be redacted).
# Fake keys, split so no credential-shaped literal sits in the source (push protection).
KEYS = {
    "AWS access key id": "AKIA" + "Q3EGUZ7TKD5XHM2P",
    "GitHub token": "ghp_" + "Ab3dE6gH9jK2mN5pQ8sT1vW4yZ7bC0eF3hJ6",
    "GitHub fine-grained token": "github_" + "pat_11ABCDE2F0gHiJkLmNoPqR_sTuVwXyZ3aBcDeFgHiJ",
    "Slack token": "xox" + "b-2817364-QwErTyUiOp",
    "Anthropic API key": "sk-" + "ant-api03-Zq7Xk2Lm9Np4Rs8Tv1Wb",
    "OpenAI API key": "sk-" + "proj-Zq7Xk2Lm9Np4Rs8Tv1WbQ3eR6tY8uI0oP",
    "Google API key": "AIza" + "SyD3kF9mQ2xR7tV4bN8cL1pW6zH0jY5gUeA",
    "Stripe live key": "sk_" + "live_4eC39HqLyjWDarjtT1zdp7dc",
    "JWT": "eyJhbGciOiJIUzI1NiJ9." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0." + "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
}
EDGES = ["{}", "x-{}", "KEY_{}", "KEY={}", "KEY=\"{}\"", "'{}'", "`{}`", "({})", "{}-tail", "{}_x", "{}=", ".{}.",
         "line one\n{}\nline three", "\n{}", "{}\n", "a {} b"]


def redaction_edges():
    """Keys next to '-', '_', '=', quotes, dots and line edges are still redacted, and every
    pattern stays linear on 2 MB of adversarial near-misses built from those same edges."""
    missed = []
    for kind, key in KEYS.items():
        for e in EDGES:
            s = e.format(key)
            out = docsmod.redact(s)
            if key in out or "[redacted:" not in out:
                missed.append((kind, e, out[:80]))
    check("redaction: keys next to - _ = quotes, dots and line edges are still redacted", not missed, str(missed[:6]))
    check("redaction: the text around a redacted key stays", docsmod.redact("x-" + KEYS["AWS access key id"] + " end") == "x-[redacted: AWS access key id] end"
          and docsmod.redact("A=" + KEYS["JWT"] + ";") == "A=[redacted: JWT];", docsmod.redact("A=" + KEYS["JWT"] + ";"))
    hostile = ["-eyJ" * 500000, "x-eyJ" * 400000, ("eyJ" + "a" * 20 + ".") * 90000, "a." * 1000000, "_eyJ" * 500000,
               "_github_pat_" * 170000, "x-AKIA" * 330000, "_sk_live_" * 220000, "-sk-" * 500000, "=sk-ant-" * 250000,
               "-xoxb-" * 330000, "_AIza" * 400000, "-ghp_" * 400000, ('"eyJ' + "b" * 30 + ".eyJ" + "c" * 30 + '.d"') * 25000]
    slow = []
    t_all = time.time()
    for h in hostile:
        t0 = time.time()
        docsmod.redact(h)
        if time.time() - t0 > 1.5:
            slow.append((h[:12], round(time.time() - t0, 2)))
    check("redaction: 14 adversarial 2 MB inputs each finish in under 1.5 s (linear matching)", not slow,
          f"{slow} total {time.time() - t_all:.1f}s")


def guard_parity():
    """Every path bash-guard calls secret material is refused here too (when it has a doc extension)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("bash_guard_for_docs", os.path.join(REPO, "templates", "hooks", "bash-guard.py"))
    bg = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.join(REPO, "templates", "hooks"))
    try:
        spec.loader.exec_module(bg)
    except SystemExit:
        pass
    samples = [".env", ".env.local", ".env.production.txt", "config/.env.md", "id_rsa", "id_rsa.txt", "keys/id_rsa.pub.md", ".git-credentials",
               ".npmrc", ".pypirc", "certs/server.pem", "certs/server.key", "home/.ssh/config.md", ".aws/credentials", "x/.aws/credentials.txt"]
    gaps = []
    for s in samples:
        if not bg._is_secret_token(s):
            continue
        try:
            docsmod.check_rel(s, ("doc", "img"))
            gaps.append(s)
        except docsmod.DocsError:
            pass
    check("secrets: every bash-guard secret path is refused by the Docs endpoints", not gaps, str(gaps))
    check("secrets: .env.example is not secret to bash-guard, and is still refused here as a dotfile",
          not bg._is_secret_token(".env.example") and _refused(".env.example.md"))


def _refused(rel):
    try:
        docsmod.check_rel(rel, ("doc",))
        return False
    except docsmod.DocsError:
        return True


# ── the Markdown renderer, run in node ─────────────────────────────────────
FIXTURE = r"""---
title: "Alpha note"
tags: [one, two]
related:
  - "[[known]]"
---
# Head *one*

<script>alert('x')</script>
<img src=x onerror=alert(1)>

[js](javascript:alert(1)) [JS](JaVaScRiPt:alert(1)) [ent](java&#115;cript:alert(1)) [tab](java&#9;script:x)
[data](data:text/html;base64,PHNjcmlwdD4=) [vb](vbscript:msgbox) [file](file:///etc/passwd) <javascript:alert(1)>
[ok](https://example.com) [mail](mailto:a@b.c) [rel](../plans/p.md#step-2) [anchor](#head-one) [py](../../src/x.py)
![pic](img/a.png) ![bad](javascript:alert(1)) ![net](https://example.com/a.png)

- a
  - b
    - c
- d

1. one
2. two

| L | C | R |
|:--|:-:|--:|
| 1 | `a|b` | x \| y |

````md
```js
const s = `tick`;
```
````

[[known]] [[known|Alias]] [[known#Some Heading]] [[missing note]] ![[shot.png]]

> quote with **bold**
"""


def walk(n, out):
    if isinstance(n, str):
        return
    out.append(n)
    for c in n.get("c", []):
        walk(c, out)


def renderer_tests():
    node = shutil.which("node")
    if not node:
        check("renderer: node not installed, renderer checks skipped", True)
        return
    script = (
        "require(process.argv[1]); const M = globalThis.ccMarkdown;"
        "const r = M.render(process.argv[2], {path: 'vault/notes/a.md', docHref: (p, h) => '#/docs?p=x&f=' + p + (h ? '&h=' + h : ''),"
        " wiki: (t) => t === 'known' ? {path: 'vault/notes/known.md'} : null, isFile: (p) => p !== 'vault/plans/gone.md'});"
        "const hostile = ['b\\n'.repeat(500000), '# a' + ' '.repeat(1e6) + 'b', '- a\\n' + '\\n  b\\n'.repeat(200000), 'http://a' + ')'.repeat(1e6),"
        " '['.repeat(1e6), '[['.repeat(5e5), '` '.repeat(5e5), ('a_ '.repeat(1000) + '\\n\\n').repeat(650), '| a | b |\\n|---|---|\\n' + '| x | y |\\n'.repeat(1e5),"
        " '[a]: b \"c\\n'.repeat(1e5), '> '.repeat(5000) + 'x'];"
        "const times = hostile.map((h) => { const t = Date.now(); M.render(h); return Date.now() - t; });"
        "const big = { body: [] }; big.times = times;"
        "console.log(JSON.stringify({r, bigOk: Array.isArray(big.body), times}));"
    )
    t0 = time.time()
    p = subprocess.run([node, "-e", script, os.path.join(REPO, "lib", "cc_dash", "web", "markdown.js"), FIXTURE],
                       capture_output=True, text=True, timeout=60)
    try:
        out = json.loads(p.stdout)
    except ValueError:
        out = {}
    check("renderer: node ran web/markdown.js", p.returncode == 0 and out, p.stderr[-400:])
    if not out:
        return
    check("renderer: each of 11 hostile 1-2 MB inputs renders in under 3 s (no quadratic paths)", out["bigOk"] and max(out["times"]) < 3000, str(out["times"]))
    r = out["r"]
    nodes = []
    for n in r["body"]:
        walk(n, nodes)
    for k, v in r["front"]:
        for n in v:
            walk(n, nodes)
    tags = {n["t"] for n in nodes}
    allowed = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "a", "em", "strong", "del", "code", "pre", "blockquote", "ul", "ol", "li", "hr", "br",
               "table", "thead", "tbody", "tr", "th", "td", "img", "input", "div", "span"}
    check("renderer: only allowlisted tags come out (no script, iframe, style)", tags <= allowed, str(tags - allowed))
    text = json.dumps(r)
    check("renderer: raw <script> and <img onerror> stay visible text", "<script>alert('x')</script>" in text and "onerror=alert(1)>" in text
          and not any(n["t"] == "script" for n in nodes))
    attrs = [(k, str(v)) for n in nodes for k, v in n.get("a", {}).items() if v is not None]
    names = {k for k, _ in attrs}
    check("renderer: no event-handler or style attributes", not [k for k in names if k.startswith("on") or k == "style"], str(names))
    hrefs = [v for k, v in attrs if k in ("href", "src")]
    bad = [h for h in hrefs if not (h.startswith("#/docs?") or h.startswith("https://") or h.startswith("mailto:"))]
    check("renderer: every href is http(s), mailto or a viewer route (javascript:, data:, vbscript:, file: dropped)", not bad, str(bad))
    ext = [n for n in nodes if n["t"] == "a" and n["a"].get("href", "").startswith("https://")]
    check("renderer: external links open in a new tab with noopener noreferrer",
          ext and all(n["a"].get("target") == "_blank" and n["a"].get("rel") == "noopener noreferrer" for n in ext))
    links = {n["a"].get("href") for n in nodes if n["t"] == "a"}
    check("renderer: a relative .md link opens in the viewer at its heading", "#/docs?p=x&f=vault/plans/p.md&h=step-2" in links, str(links))
    check("renderer: a #fragment link stays in this document", "#/docs?p=x&f=vault/notes/a.md&h=head-one" in links, str(links))
    offs = [n for n in nodes if n["t"] == "span" and "md-off" in n["a"].get("class", "")]
    check("renderer: dropped links keep their text (js, JS, ent, tab, data, vb, file, autolink, py, image)", len(offs) >= 10, str(len(offs)))
    imgs = [n["a"] for n in nodes if n["t"] == "img"]
    check("renderer: relative images become data-asset (fetched with the token), never a src", any(a.get("data-asset") == "vault/notes/img/a.png" and "src" not in a for a in imgs), str(imgs))
    check("renderer: Obsidian ![[embed]] resolves next to the note", any(a.get("data-asset") == "vault/notes/shot.png" for a in imgs), str(imgs))
    check("renderer: network images are not loaded", not any(a.get("src", "").startswith("http") for a in imgs))
    hs = r["headings"]
    check("renderer: headings get slugs and the TOC list", hs and hs[0]["id"] == "head-one" and hs[0]["level"] == 1, str(hs))
    check("renderer: heading ids are prefixed so they never clash with the page", any(n["t"] == "h1" and n["a"].get("id") == "doc-head-one" for n in nodes))
    uls = [n for n in r["body"] if n["t"] == "ul"]
    nested = uls and uls[0]["c"][0]["c"][1]["t"] == "ul" and uls[0]["c"][0]["c"][1]["c"][0]["c"][1]["t"] == "ul"
    check("renderer: nested lists nest three deep", nested, json.dumps(uls[:1])[:400])
    ol = [n for n in r["body"] if n["t"] == "ol"]
    check("renderer: ordered lists", ol and len(ol[0]["c"]) == 2)
    tables = [n for n in nodes if n["t"] == "table"]
    if tables:
        th = tables[0]["c"][0]["c"][0]["c"]
        tds = tables[0]["c"][1]["c"][0]["c"]
        check("renderer: tables with alignment", [x["a"].get("class") for x in th] == ["al-left", "al-center", "al-right"], json.dumps(th)[:300])
        check("renderer: a pipe inside code or escaped stays in its cell", len(tds) == 3 and tds[1]["c"][0]["c"] == ["a|b"] and tds[2]["c"] == ["x | y"], json.dumps(tds)[:300])
    else:
        check("renderer: tables", False, "no table")
    codes = [n for n in nodes if n["t"] == "code" and "lang-md" in n["a"].get("class", "")]
    check("renderer: a 4-backtick fence keeps the inner ``` fence and backticks as code", codes and "```js\nconst s = `tick`;\n```" == codes[0]["c"][0], json.dumps(codes)[:300])
    check("renderer: the code block shows its language", any(n["t"] == "div" and n["a"].get("class") == "md-lang" and n["c"] == ["md"] for n in nodes))
    wl = [n for n in nodes if n["t"] == "a" and "md-wiki" in n["a"].get("class", "")]
    got = [(n["a"].get("href"), "md-missing" in n["a"]["class"], n["c"][0] if n["c"] else "") for n in wl]
    check("renderer: wikilinks resolve; alias and #heading work; unknown ones are marked missing",
          ("#/docs?p=x&f=vault/notes/known.md", False, "known") in got and ("#/docs?p=x&f=vault/notes/known.md", False, "Alias") in got
          and ("#/docs?p=x&f=vault/notes/known.md&h=some-heading", False, "known > Some Heading") in got
          and (None, True, "missing note") in got, str(got))
    fm = dict((k, v) for k, v in r["front"])
    check("renderer: front matter becomes key/value pairs", fm.get("title") == ["Alpha note"] and fm.get("tags") == ["one, two"], json.dumps(r["front"])[:300])
    check("renderer: a wikilink in front matter is a link", any(isinstance(x, dict) and x.get("t") == "a" for x in fm.get("related", [])), json.dumps(fm.get("related")))
    check("renderer: the front matter block is not rendered as text", "title: " not in json.dumps(r["body"]))
    bq = [n for n in r["body"] if n["t"] == "blockquote"]
    check("renderer: block quotes keep inline formatting", bq and bq[0]["c"][0]["c"][1]["t"] == "strong", json.dumps(bq)[:200])


def chrome_test(srv, port):
    """The real check: a hostile HTML doc in the Docs view cannot read the token or call /api."""
    chrome = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
    node = shutil.which("node")
    if not (chrome and node and os.path.isdir(PW)):
        check("chrome: google-chrome, node or playwright-core not found, browser sandbox check skipped", True)
        return
    proj = srv.docs.root_for("proj")
    w(proj, "docs/evil.html", """<!doctype html><title>evil</title><body><pre id=out>running</pre><script>
const out = []; const say = (k, v) => { out.push(k + '=' + v); document.title = out.join(' | '); };
try { say('ls', String(localStorage.getItem('cc-dash-token'))); } catch (e) { say('ls', 'blocked:' + e.name); }
try { say('cookie', document.cookie === '' ? 'empty' : 'set'); } catch (e) { say('cookie', 'blocked:' + e.name); }
try { say('parent', String(parent.document.title)); } catch (e) { say('parent', 'blocked:' + e.name); }
try { say('origin', String(self.origin)); } catch (e) { say('origin', 'err'); }
fetch('/api/live').then(r => say('fetch', 'status' + r.status)).catch(e => say('fetch', 'blocked:' + e.name));
try { parent.postMessage('hi', '*'); say('post', 'sent'); } catch (e) { say('post', 'err'); }
setTimeout(() => { try { top.location = 'https://example.com'; say('topnav', 'tried'); } catch (e) { say('topnav', 'blocked:' + e.name); } }, 100);
</script></body>""")
    live_md = w(proj, "docs/live.md", "# Live\n\n" + "".join("## Part %d\n\n%s\n\n" % (i, "Some text. " * 40) for i in range(12)))
    code = srv.auth.new_code()
    js = r"""
const { chromium } = require(process.argv[1]);
(async () => {
  const b = await chromium.launch({ executablePath: process.argv[2], headless: true });
  const p = await b.newPage();
  const base = 'http://127.0.0.1:' + process.argv[3];
  const errors = [];
  p.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  await p.goto(base + '/pair#code=' + process.argv[4]);
  await p.waitForFunction(() => !!localStorage.getItem('cc-dash-token'), null, { timeout: 10000 });
  await p.goto(base + '/#/docs?p=proj&f=docs/evil.html');
  await p.waitForSelector('.docs-head .seg button', { timeout: 15000 });
  await p.click('.docs-head .seg button');                         // allow scripts: the worst case
  await p.waitForSelector('iframe.docs-frame[sandbox="allow-scripts"]', { timeout: 10000 });
  const fr = await (await p.$('iframe.docs-frame')).contentFrame();
  await p.waitForTimeout(1500);
  const title = await fr.title().catch((e) => 'unreadable:' + e.message);
  const sandbox = await p.getAttribute('iframe.docs-frame', 'sandbox');
  const url = p.url();
  const tok = await p.evaluate(() => localStorage.getItem('cc-dash-token'));
  // live reload: a change on disk is patched in place, scroll position and nodes kept
  await p.setViewportSize({ width: 1280, height: 700 });
  await p.goto(base + '/#/docs?p=proj&f=docs/live.md');
  await p.waitForSelector('.docs-read .md h2');
  await p.evaluate(() => { window.scrollTo(0, 600); document.querySelector('.docs-read .md h2').__mark = 1; });
  const before = await p.evaluate(() => window.scrollY);
  require('fs').appendFileSync(process.argv[5], '\n\nLIVE-RELOAD-MARKER appended\n');
  await p.waitForFunction(() => document.querySelector('.docs-read .md').textContent.includes('LIVE-RELOAD-MARKER'), null, { timeout: 12000 }).catch(() => {});
  const live = await p.evaluate(() => ({ text: document.querySelector('.docs-read .md').textContent.includes('LIVE-RELOAD-MARKER'),
    kept: document.querySelector('.docs-read .md h2').__mark === 1, y: window.scrollY }));
  console.log(JSON.stringify({ title, sandbox, url, tokLen: (tok || '').length, errors, live, before }));
  await b.close();
})().catch((e) => { console.log(JSON.stringify({ fatal: String(e) })); process.exit(1); });
"""
    p = subprocess.run([node, "-e", js, PW, chrome, str(port), code, live_md], capture_output=True, text=True, timeout=120)
    try:
        out = json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        out = {"fatal": p.stdout[-300:] + p.stderr[-300:]}
    if out.get("fatal"):
        check("chrome: the browser check ran", False, out["fatal"])
        return
    t = out.get("title", "")
    check("chrome: the frame is sandboxed with scripts only", out.get("sandbox") == "allow-scripts", str(out.get("sandbox")))
    check("chrome: the hostile doc ran (scripts allowed)", "ls=" in t, t)
    check("chrome: it cannot read the dashboard's localStorage token", "ls=blocked:SecurityError" in t, t)
    check("chrome: its origin is opaque", "origin=null" in t, t)
    check("chrome: it cannot reach the parent document", "parent=blocked" in t, t)
    check("chrome: fetch('/api/live') is blocked (CSP connect none, no token)", "fetch=blocked" in t and "status200" not in t, t)
    check("chrome: it cannot navigate the dashboard", "/#/docs" in out.get("url", "") and out.get("tokLen", 0) > 20, out.get("url"))
    lv = out.get("live") or {}
    check("live reload: a changed file shows up in the open doc within the poll", lv.get("text") is True, json.dumps(out.get("live")))
    check("live reload: patched in place (same nodes) and the scroll position kept", lv.get("kept") is True and abs((lv.get("y") or 0) - out.get("before", -1)) < 2,
          json.dumps([out.get("before"), lv]))


def main():
    base = tempfile.mkdtemp(prefix="cc-dash-docs-")
    try:
        run(base)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    fails = [r for r in RESULTS if not r[1]]
    for name, ok, detail in RESULTS:
        print(("PASS " if ok else "FAIL ") + name + ("" if ok or not detail else "  -- " + detail))
    print(f"total={len(RESULTS)}  pass={len(RESULTS) - len(fails)}  fail={len(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
