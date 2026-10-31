#!/usr/bin/env python3
"""install.sh on a fresh machine, per OS: the prerequisite check and the shell profile it picks.

Every case runs the real script with a throwaway HOME and stubs for claude, npx and uname, so
nothing is installed and this machine's profile is not touched.

Run:  python3 tests/test_install_sh.py
"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = tempfile.mkdtemp(prefix="cc-install-sh-")
TOOLS = ("bash", "git", "grep", "sed", "cat", "chmod", "mktemp", "rm", "sort", "head", "dirname", "tr", "awk")
failures = []


def stub(path, body):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\n" + body + "\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)


def run(name, uname="Linux", shell="/bin/bash", files=(), python="", drop=()):
    """The result of install.sh in a new HOME holding `files`, and that HOME."""
    home = os.path.join(BASE, name, "home")
    tools = os.path.join(BASE, name, "tools")
    os.makedirs(home)
    os.makedirs(tools)
    for f in files:
        open(os.path.join(home, f), "w").close()
    stub(os.path.join(tools, "uname"), "echo %s" % uname)
    for tool in ("claude", "npx"):
        if tool not in drop:
            stub(os.path.join(tools, tool), "exit 0")
    if python:
        stub(os.path.join(tools, "python3"), python)
    else:
        os.symlink(shutil.which("python3"), os.path.join(tools, "python3"))
    # PATH is this directory alone, so this machine's real claude and npx stay out of it.
    for tool in TOOLS:
        real = shutil.which(tool)
        if real:
            os.symlink(real, os.path.join(tools, tool))
    env = {"HOME": home, "PATH": tools, "SHELL": shell, "USER": "t"}
    r = subprocess.run([shutil.which("bash"), os.path.join(REPO, "install.sh")], env=env, capture_output=True,
                       text=True, stdin=subprocess.DEVNULL, timeout=60)
    return r, home


def check(label, ok, detail=""):
    print("%s %s" % ("ok  " if ok else "FAIL", label))
    if not ok:
        failures.append(label + (": " + detail[-400:] if detail else ""))


def profile_case(name, want, **kw):
    r, home = run(name, **kw)
    path = os.path.join(home, want)
    text = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    check("%s: PATH and ccdash land in %s" % (name, want), "cc_tool" in text and "ccdash()" in text, r.stdout + r.stderr)


profile_case("mac-zsh-new", ".zshrc", uname="Darwin", shell="/bin/zsh")
profile_case("mac-bash", ".bash_profile", uname="Darwin", shell="/bin/bash")
profile_case("linux-bash", ".bashrc", files=[".bashrc"])
profile_case("linux-zsh-file", ".zshrc", files=[".zshrc", ".bashrc"])
profile_case("linux-neither", ".profile")

r, home = run("git-bash", uname="MINGW64_NT-10.0-22631")
check("git-bash: refused, points to WSL", r.returncode == 1 and "wsl --install" in r.stderr, r.stderr)
check("git-bash: nothing written", os.listdir(home) == [])

r, home = run("old-python", uname="Darwin", shell="/bin/zsh", python="exit 1")
check("old python: refused with the brew command", r.returncode == 1 and "brew install python" in r.stderr, r.stderr)
check("old python: nothing written", os.listdir(home) == [])

r, home = run("no-claude", drop=("claude",))
check("no claude: refused with the installer", r.returncode == 1 and "claude.ai/install.sh" in r.stderr, r.stderr)

r, home = run("no-npx", drop=("npx",))
check("no npx: refused", r.returncode == 1 and "node/npx" in r.stderr, r.stderr)

shutil.rmtree(BASE, ignore_errors=True)
print("%d failed" % len(failures))
for f in failures:
    print("  " + f)
sys.exit(1 if failures else 0)
