#!/usr/bin/env python3
"""Allow/deny matrix for templates/hooks/bash-guard.py.

124 cases covering protected-branch commit/push, --no-verify variants,
secret-read probes, and malformed payloads. Expectations encode the guard's
INTENDED behaviour, including the bypasses that are deliberately out of scope
(shell expansion, sh -c wrappers, base64) — those assert ALLOW on purpose, so a
future change that appears to "fix" one will show up here as a diff to justify
rather than a silent behaviour change.

Fixtures are created in a temp dir and removed afterwards; nothing outside it is
touched and no command in the matrix is ever executed — each is only fed to the
guard as a PreToolUse payload.

Run:  python3 tests/test_bash_guard.py
      python3 tests/test_bash_guard.py --json out.json
Exits non-zero if any case deviates. Re-run after editing bash-guard.py, and
whenever the Claude Code CLI changes its hook contract.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARD = os.environ.get("BASH_GUARD") or os.path.join(REPO, "templates", "hooks", "bash-guard.py")

D, A = "DENY", "ALLOW"


def _git(cwd, *args):
    subprocess.run(("git",) + args, cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _repo(path, branch):
    """A git repo with one commit, checked out on `branch`."""
    os.makedirs(path, exist_ok=True)
    _git(path, "init", "-q", "-b", branch)
    _git(path, "config", "user.email", "t@example.invalid")
    _git(path, "config", "user.name", "t")
    open(os.path.join(path, "README.md"), "w").write("fixture\n")
    _git(path, "add", "README.md")
    _git(path, "commit", "-qm", "init")
    return path


def make_fixtures(base):
    master = _repo(os.path.join(base, "repo_master"), "master")
    feature = _repo(os.path.join(base, "repo_feature"), "feature/x")
    other = _repo(os.path.join(base, "repo_other"), "master")
    notgit = os.path.join(base, "notgit")
    os.makedirs(notgit, exist_ok=True)
    # Decoy secret-ish files so probes look realistic. Contents are dummies;
    # the guard is static-analysis only and never opens them.
    for d in (notgit, master, feature):
        for name in (".env", ".env.example", ".env.production", ".npmrc",
                     "id_rsa", "slides.key", "data.txt", "audit.md"):
            open(os.path.join(d, name), "w").write("DUMMY=not-a-real-secret\n")
        os.makedirs(os.path.join(d, "certs"), exist_ok=True)
        open(os.path.join(d, "certs", "server.pem"), "w").write("DUMMY\n")
    return master, feature, other, notgit


BASE = tempfile.mkdtemp(prefix="bashguard-matrix-")
MASTER, FEATURE, OTHER, NOTGIT = make_fixtures(BASE)

CASES = [
    # ── Protected-branch commit ────────────────────────────────────────────
    ("C01", "commit", MASTER, "git commit -m 'x'", D),
    ("C02", "commit", MASTER, "git commit --amend --no-edit", A),
    ("C03", "commit", FEATURE, "git commit -m 'x'", A),
    ("C04", "commit", MASTER, "git -C . commit -m 'x'", D),                      # BG-2
    ("C05", "commit", MASTER, "git --git-dir=.git --work-tree=. commit -m 'x'", D),  # BG-2
    ("C06", "commit", MASTER, "git -c user.name=x commit -m 'x'", D),            # BG-2
    ("C07", "commit", MASTER, "git commit -m 'docs: explain --amend'", D),       # BG-3
    ("C08", "commit", FEATURE, "git commit -n -m 'x'", D),                       # BG-4
    ("C09", "commit", FEATURE, "git commit -m 'ci: drop --no-verify'", A),       # BG-3 inverse
    ("C09b", "commit", FEATURE, 'git commit -m "ci: drop --no-verify from CI"', A),  # BG-3 inverse (real form)
    ("C10", "commit", FEATURE, "git -c core.hooksPath=/dev/null commit -m 'x'", D),  # BG-4b
    ("C11", "commit", MASTER, "echo hi\ngit commit -m 'x'", D),
    ("C12", "commit", MASTER, "npm test && git commit -m 'x'", D),
    ("C13", "commit", NOTGIT, "git commit -m 'x'", A),
    ("C14", "commit", MASTER, "echo 'git commit -m x'", A),
    ("C15", "commit", MASTER, "sh -c 'git commit -m x'", A),                     # inherent gap
    ("C16", "commit", MASTER, "env GIT_AUTHOR_NAME=x git commit -m 'x'", D),
    ("C17", "commit", MASTER, "git\tcommit -m 'x'", D),
    ("C18", "commit", MASTER, "git${IFS}commit -m 'x'", A),                      # inherent gap
    # ── --no-verify ────────────────────────────────────────────────────────
    ("N01", "noverify", FEATURE, "git push --no-verify origin feature/x", D),
    ("N02", "noverify", FEATURE, "git commit --no-verify -m 'x'", D),
    ("N03", "noverify", FEATURE, "npm publish --no-verify", A),
    # ── Push ───────────────────────────────────────────────────────────────
    ("P01", "push", MASTER, "git push", D),
    ("P02", "push", FEATURE, "git push", A),
    ("P03", "push", FEATURE, "git push origin master", D),
    ("P04", "push", FEATURE, "git push origin main", D),
    ("P05", "push", FEATURE, "git push -u origin master", D),
    ("P06", "push", MASTER, "git push origin HEAD", D),                          # BG-1
    ("P07", "push", MASTER, "git push origin @", D),                             # BG-1
    ("P08", "push", MASTER, "git push -f origin HEAD", D),                       # BG-1
    ("P09", "push", FEATURE, "git push origin HEAD:main", D),
    ("P10", "push", FEATURE, "git push origin refs/heads/main", D),
    ("P11", "push", FEATURE, "git push origin +master", D),
    ("P12", "push", FEATURE, "git push --all origin", D),
    ("P13", "push", FEATURE, "git push --mirror origin", D),
    ("P14", "push", FEATURE, "git push origin feature/x", A),
    ("P15", "push", FEATURE, "git -C %s push origin master" % OTHER, D),
    ("P16", "push", FEATURE, "git push origin 'master'", D),
    ("P17", "push", FEATURE, "git push origin ma'ster'", D),
    ("P18", "push", FEATURE, "B=master; git push origin $B", A),                 # inherent gap
    ("P19", "push", FEATURE, "sh -c 'git push origin master'", A),               # inherent gap
    ("P20", "push", FEATURE, "echo 'git push origin master'", A),
    ("P21", "push", FEATURE, "git push origin master # ship it", D),
    ("P22", "push", FEATURE, "git add -A && git push origin master", D),
    ("P23", "push", FEATURE, "git push origin master &", D),
    ("P24", "push", FEATURE, "bash <<'EOF'\ngit push origin master\nEOF", D),
    ("P25", "push", FEATURE, 'git push origin master "', D),                     # regex fallback
    ("P26", "push", FEATURE, "git push --receive-pack=x origin master", D),
    ("P27", "push", FEATURE, "git push -o ci.skip origin master", D),
    ("P28", "push", FEATURE, "git push origin $'mas\\x74er'", A),                # inherent gap
    ("P29", "push", FEATURE, "git push origin ma`echo s`ter", A),                # inherent gap
    ("P30", "push", FEATURE, 'git push origin "$(echo master)"', A),             # inherent gap
    ("P31", "push", FEATURE, "git push origin мaster", A),
    ("P32", "push", FEATURE, "git push origin master​", A),
    ("P33", "push", FEATURE, "gh pr merge --squash", A),
    ("P34", "push", MASTER, "git status --porcelain", A),
    ("P35", "push", MASTER, "git log --grep='push origin master'", A),
    ("P36", "push", MASTER, "git commit -m x && git push", D),
    # ── Secret reads — expect DENY ─────────────────────────────────────────
    ("S01", "secret-deny", NOTGIT, "grep KEY .env", D),
    ("S02", "secret-deny", NOTGIT, "grep -i secret config/.env.production", D),
    ("S03", "secret-deny", NOTGIT, "grep -i x ~/.ssh/id_rsa", D),
    ("S04", "secret-deny", NOTGIT, "awk '{print}' .env", D),
    ("S05", "secret-deny", NOTGIT, "source .env", D),
    ("S06", "secret-deny", NOTGIT, ". .env", D),
    ("S07", "secret-deny", NOTGIT, "python3 -c \"print(open('.env').read())\"", D),
    ("S08", "secret-deny", NOTGIT, "node -e \"console.log(require('fs').readFileSync('.env','utf8'))\"", D),
    ("S09", "secret-deny", NOTGIT, "base64 ~/.ssh/id_rsa", D),
    ("S10", "secret-deny", NOTGIT, "strings ~/.aws/credentials", D),
    ("S11", "secret-deny", NOTGIT, "cut -d= -f2 .env", D),
    ("S12", "secret-deny", NOTGIT, "sort certs/server.pem", D),
    ("S13", "secret-deny", NOTGIT, "rg SECRET .env", D),
    ("S14", "secret-deny", NOTGIT, "grep github ~/.git-credentials", D),
    ("S15", "secret-deny", NOTGIT, "grep token ~/.npmrc", D),
    ("S16", "secret-deny", NOTGIT, "xargs cat .env", D),
    ("S17", "secret-deny", NOTGIT, "command grep KEY .env", D),
    ("S18", "secret-deny", NOTGIT, "sudo grep KEY .env", D),
    ("S19", "secret-deny", NOTGIT, "\\grep KEY .env", D),
    ("S20", "secret-deny", NOTGIT, "env -i grep KEY .env", D),
    ("S21", "secret-deny", NOTGIT, "grep KEY '.en'v", D),
    # ── Secret reads — expect ALLOW (false-positive probes) ────────────────
    ("F01", "secret-allow", NOTGIT, "grep -r TODO src/", A),
    ("F02", "secret-allow", NOTGIT, "grep -rn '\\.env' src/", A),
    ("F03", "secret-allow", NOTGIT, "grep -r 'id_rsa' .", A),
    ("F04", "secret-allow", NOTGIT, "find . -name '*.pem'", A),
    ("F05", "secret-allow", NOTGIT, "rsync -a ./src .", A),
    ("F06", "secret-allow", NOTGIT, "ls -la ~/.ssh", A),
    ("F07", "secret-allow", NOTGIT, "echo 'remember to set .env'", A),
    ("F08", "secret-allow", NOTGIT, "sort data.txt", A),
    ("F09", "secret-allow", NOTGIT, "cp .env /tmp/x", A),
    ("F10", "secret-allow", NOTGIT, "grep KEY env.example", A),
    ("F11", "secret-allow", NOTGIT, "grep KEY sample.env", A),
    ("F12", "secret-allow", NOTGIT, "grep KEY .env.example", D),   # BG-9 known FP (out of scope)
    ("F13", "secret-allow", NOTGIT, "cut -f1 slides.key", D),      # BG-9 known FP (out of scope)
    ("F14", "secret-allow", NOTGIT, "cat README.md", A),
    ("F15", "secret-allow", NOTGIT, "grep -rn 'push origin master' docs/", A),
    ("F16", "secret-allow", NOTGIT, "npm run build", A),
    ("F17", "secret-allow", NOTGIT, "grep -rn '\\.env' src/ | xargs wc -l", A),
    # ── Secret-read bypass probes ─────────────────────────────────────────
    ("B01", "bypass", NOTGIT, "grep KEY < .env", D),                             # BG: fix -> DENY
    ("B02", "bypass", NOTGIT, "tr -d x < .env", D),                              # BG: fix -> DENY
    ("B03", "bypass", NOTGIT, "while read l; do echo $l; done < .env", D),       # BG: fix -> DENY
    ("B04", "bypass", NOTGIT, "cat .env | grep KEY", A),                         # by design
    ("B05", "bypass", NOTGIT, "sed -n 1p .env", A),                              # by design
    ("B06", "bypass", NOTGIT, "head .env", A),                                   # by design
    ("B07", "bypass", NOTGIT, "tail .env", A),                                   # by design
    ("B08", "bypass", NOTGIT, "less .env", A),                                   # by design
    ("B09", "bypass", NOTGIT, "find . -name '.env' | xargs cat", D),             # BG-10: fix -> DENY
    ("B10", "bypass", NOTGIT, "echo .env | xargs -I{} cat {}", D),               # BG-10: fix -> DENY
    ("B11", "bypass", NOTGIT, "/bin/grep KEY .env", D),                          # BG: fix -> DENY
    ("B12", "bypass", NOTGIT, "F=.env; grep KEY $F", A),                         # inherent
    ("B13", "bypass", NOTGIT, "grep KEY .en*", A),                               # inherent
    ("B14", "bypass", NOTGIT, "grep KEY .{e,}nv", A),                            # inherent
    ("B15", "bypass", NOTGIT, "bash -c 'grep KEY .env'", A),                     # inherent
    ("B16", "bypass", NOTGIT, "sh -c 'grep KEY .env'", A),                       # inherent
    ("B17", "bypass", NOTGIT, "python3 <<'EOF'\nprint(open('.env').read())\nEOF", A),  # inherent
    ("B18", "bypass", NOTGIT, "python3 -c \"print(open(chr(46)+'env').read())\"", A),  # inherent
    ("B19", "bypass", NOTGIT, "echo Z3JlcCBLRVkgLmVudgo= | base64 -d | sh", A),  # inherent
    # ── Malformed payloads (BG-5 fail-open) ───────────────────────────────
    ("M01", "malformed", NOTGIT, "@@RAW@@not json at all", D),
    ("M02", "malformed", NOTGIT, "@@RAW@@", D),
    ("M03", "malformed", NOTGIT, '@@RAW@@{"tool_input": null}', D),
    ("M04", "malformed", NOTGIT, '@@RAW@@{"tool_input":{"command":["git","push"]}}', D),
    ("M05", "malformed", NOTGIT, '@@RAW@@{"tool_input":{"command":42}}', D),
    ("M06", "malformed", NOTGIT, '@@RAW@@{"tool_input":{}}', A),
    ("M07", "malformed", NOTGIT, '@@RAW@@{"tool_name":"BashOutput","tool_input":{"bash_id":"1"}}', A),
    ("M08", "malformed", NOTGIT, '@@RAW@@{"tool_input":{"command":""}}', A),
    ("M09", "malformed", NOTGIT, '@@RAW@@{"session_id":"s","tool_name":"Bash","tool_input":{"command":"ls -la"}}', A),
]


def run_case(cwd, command):
    if command.startswith("@@RAW@@"):
        payload = command[len("@@RAW@@"):]
    else:
        payload = json.dumps({"session_id": "s", "hook_event_name": "PreToolUse",
                              "tool_name": "Bash", "tool_input": {"command": command}})
    p = subprocess.run([sys.executable, GUARD], input=payload, capture_output=True,
                       text=True, cwd=cwd, timeout=60)
    if p.returncode != 0:
        tail = p.stderr.strip().splitlines()[-1] if p.stderr.strip() else ""
        return "ERR%d" % p.returncode, tail
    out = p.stdout
    if '"permissionDecision": "deny"' in out or '"permissionDecision":"deny"' in out:
        return D, out.strip()
    return A, out.strip()


def main():
    if not os.path.exists(GUARD):
        print("guard not found: %s" % GUARD)
        return 2
    results, fails = {}, []
    for cid, section, cwd, cmd, expected in CASES:
        got, detail = run_case(cwd, cmd)
        results[cid] = {"section": section, "cmd": cmd, "expected": expected,
                        "got": got, "detail": detail[:200]}
        if got != expected:
            fails.append((cid, section, cmd, expected, got))
    print("total=%d  pass=%d  fail=%d" % (len(CASES), len(CASES) - len(fails), len(fails)))
    for cid, section, cmd, exp, got in fails:
        print("  %-5s %-10s expected %-6s got %-8s  %r" % (cid, section, exp, got, cmd))
    if "--json" in sys.argv:
        path = sys.argv[sys.argv.index("--json") + 1]
        with open(path, "w") as fh:
            json.dump(results, fh, indent=1)
        print("wrote", path)
    return 1 if fails else 0


try:
    sys.exit(main())
finally:
    shutil.rmtree(BASE, ignore_errors=True)
