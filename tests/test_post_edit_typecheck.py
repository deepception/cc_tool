#!/usr/bin/env python3
"""post-edit-typecheck.py scope checks: a checker runs only inside the session's project.

Builds, in a temp dir, a project (CLAUDE_PROJECT_DIR), a directory outside it
with its own .git, Cargo.toml, tsconfig.json and node_modules/.bin/tsc, a
symlink from the project to that directory, a project that is a subdirectory
of a bigger repo, and a nested repo inside the project. Fake `cargo` and `tsc`
on PATH (and the planted local tsc) log the directory they ran in, so each case
checks whether a checker ran and where. Nothing outside the temp dir is touched.

Run:  python3 tests/test_post_edit_typecheck.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "templates", "hooks", "post-edit-typecheck.py")

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(ok)
    print(("PASS " if ok else "FAIL ") + name + ("" if ok or not detail else "  (" + detail + ")"))


def write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)


def fake_tool(path, log):
    write(path, '#!/bin/sh\necho "$(basename "$0") $(pwd -P)" >> "%s"\nexit 0\n' % log)
    os.chmod(path, 0o755)


def main():
    t = os.path.realpath(tempfile.mkdtemp(prefix="cc-typecheck-scope-"))
    try:
        log = os.path.join(t, "ran.log")
        bindir = os.path.join(t, "bin")
        fake_tool(os.path.join(bindir, "cargo"), log)
        fake_tool(os.path.join(bindir, "tsc"), log)

        proj = os.path.join(t, "proj")
        os.makedirs(os.path.join(proj, ".git"))
        write(os.path.join(proj, "Cargo.toml"), "[package]\nname='p'\n")
        write(os.path.join(proj, "tsconfig.json"), "{}")
        write(os.path.join(proj, "src", "main.rs"))
        write(os.path.join(proj, "web", "a.ts"))

        out = os.path.join(t, "outside")
        os.makedirs(os.path.join(out, ".git"))
        write(os.path.join(out, "Cargo.toml"), "[package]\nname='evil'\n")
        write(os.path.join(out, "build.rs"), "fn main() {}\n")
        write(os.path.join(out, "tsconfig.json"), "{}")
        fake_tool(os.path.join(out, "node_modules", ".bin", "tsc"), log)
        write(os.path.join(out, "src", "main.rs"))
        write(os.path.join(out, "a.ts"))
        os.symlink(out, os.path.join(proj, "link"))

        big = os.path.join(t, "big")
        os.makedirs(os.path.join(big, ".git"))
        sub = os.path.join(big, "sub")
        write(os.path.join(sub, "Cargo.toml"), "[package]\nname='s'\n")
        write(os.path.join(sub, "src", "x.rs"))
        write(os.path.join(big, "Cargo.toml"), "[package]\nname='big'\n")

        nested = os.path.join(proj, "nested")
        os.makedirs(os.path.join(nested, ".git"))
        write(os.path.join(nested, "Cargo.toml"), "[package]\nname='n'\n")
        write(os.path.join(nested, "src", "y.rs"))

        state = os.path.join(t, "state")
        os.makedirs(state)

        def run(file_path, project, cwd=None):
            if os.path.exists(log):
                os.remove(log)
            env = dict(os.environ, PATH=bindir + os.pathsep + os.environ.get("PATH", ""),
                       CLAUDE_PROJECT_DIR=project, XDG_STATE_HOME=state)
            payload = json.dumps({"tool_input": {"file_path": file_path}, "session_id": "scope-test"})
            subprocess.run([sys.executable, HOOK], input=payload, text=True, env=env, cwd=cwd or project,
                           capture_output=True, timeout=60)
            if not os.path.exists(log):
                return []
            with open(log) as fh:
                return [ln.split() for ln in fh.read().splitlines() if ln.strip()]

        r = run(os.path.join(out, "src", "main.rs"), proj)
        check("outside the project: cargo does not run", r == [], str(r))
        r = run(os.path.join(out, "a.ts"), proj)
        check("outside the project: neither the planted local tsc nor PATH tsc runs", r == [], str(r))
        r = run(os.path.join(proj, "link", "src", "main.rs"), proj)
        check("a symlink in the project that resolves outside it: nothing runs", r == [], str(r))
        r = run(os.path.join(proj, "link", "a.ts"), proj)
        check("a symlinked .ts that resolves outside: nothing runs", r == [], str(r))

        r = run(os.path.join(proj, "src", "main.rs"), proj)
        check("inside the project: cargo runs once", len(r) == 1 and r[0][0] == "cargo", str(r))
        check("inside the project: cargo runs in the project root", bool(r) and r[0][1] == proj, str(r))
        r = run(os.path.join(proj, "web", "a.ts"), proj)
        check("inside the project: tsc runs in the project root", r == [["tsc", proj]], str(r))

        r = run(os.path.join(sub, "src", "x.rs"), sub)
        check("project inside a bigger repo: cargo runs in the project, not the repo above it",
              r == [["cargo", sub]], str(r))
        r = run(os.path.join(nested, "src", "y.rs"), proj)
        check("a nested repo inside the project: cargo runs in the nested repo", r == [["cargo", nested]], str(r))

        r = run(os.path.join("src", "main.rs"), proj, cwd=proj)
        check("a relative path inside the project still runs", r == [["cargo", proj]], str(r))
    finally:
        shutil.rmtree(t, ignore_errors=True)

    fails = RESULTS.count(False)
    print("total=%d  pass=%d  fail=%d" % (len(RESULTS), len(RESULTS) - fails, fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
