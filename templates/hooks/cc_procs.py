"""Who is this process? Checks shared by cc-dash (lib/cc_dash/procs.py loads
this file) and its control hook (dash-control.py, beside this file).

A helper module, not a hook. cc-setup copies it into the project's
.claude/hooks/ with the hooks, and the control hook imports it from there (a
protected path in Claude Code): the hook never runs code from the cc_tool
checkout, which lies outside the project and which an agent could edit.
Standard library only, no package imports.

Everything here reads /proc (Linux). Where there is no /proc the process checks
cannot run; callers say what they do then.

  dashboard_problem(pid, root)  why pid is not the cc-dash of the checkout at
                                root, launched the way cc-dash launches itself
                                and from outside Claude Code ('' when it is)
  hook_problem(pid, root)       why pid is not a dash-control.py hook installed
                                by that checkout ('' when it is)
  manifest_problem(root, m)     why cc-dash's code under root is not what the
                                manifest m recorded ('' when it is); build_manifest
                                makes one (cc-setup)
  code_after_start(pid, ...)    why the dashboard pid may not be running that code
                                (it changed after the process started)
  claude_lineage(pid)           why pid counts as started inside Claude Code
  fresh_session(pid)            a process that leads a session it started after
                                being re-parented (a typical double-fork escape)
"""
import os
import re
import stat

# What Claude Code puts in the environment of its Bash tool's processes
# (checked against `env` from a headless `claude -p` Bash call, CLI 2.1.30x).
CLAUDE_ENV_SIGNALS = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PID",
                      "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_CHILD_SESSION")
PIN_NAME = "cc_tool_root"


def proc_stat(pid, proc="/proc"):
    """{comm, ppid, pgrp, sid, tty} from /proc/<pid>/stat, or None."""
    try:
        with open("%s/%d/stat" % (proc, pid), encoding="utf-8", errors="replace") as fh:
            st = fh.read()
        comm = st[st.index("(") + 1:st.rindex(")")]
        f = st[st.rindex(")") + 2:].split()
        return {"comm": comm, "ppid": int(f[1]), "pgrp": int(f[2]), "sid": int(f[3]), "tty": int(f[4])}
    except (OSError, ValueError, IndexError, TypeError):
        return None


def cmdline(pid, proc="/proc"):
    """argv of pid, or None."""
    try:
        with open("%s/%d/cmdline" % (proc, pid), "rb") as fh:
            return [a.decode("utf-8", "replace") for a in fh.read(65536).split(b"\0") if a]
    except (OSError, TypeError):
        return None


def environ(pid, proc="/proc"):
    """The environment pid was started with, as a dict, or None."""
    try:
        with open("%s/%d/environ" % (proc, pid), "rb") as fh:
            raw = fh.read(4 * 1024 * 1024)
    except (OSError, TypeError):
        return None
    out = {}
    for item in raw.split(b"\0"):
        k, sep, v = item.partition(b"=")
        if sep and k:
            out[k.decode("utf-8", "replace")] = v.decode("utf-8", "replace")
    return out


def _link(pid, name, proc="/proc"):
    try:
        return os.readlink("%s/%d/%s" % (proc, pid, name))
    except (OSError, TypeError):
        return None


def ancestors(pid=None, n=64, proc="/proc"):
    """(pid, comm, exe, argv) of pid (default: this process's parent) and each of
    its ancestors up to init, from /proc; [] elsewhere."""
    out, pid = [], os.getppid() if pid is None else pid
    for _ in range(n):
        if not isinstance(pid, int) or pid <= 1:
            break
        st = proc_stat(pid, proc)
        if st is None:
            break
        out.append((pid, st["comm"], _link(pid, "exe", proc) or "", cmdline(pid, proc) or []))
        pid = st["ppid"]
    return out


def is_claude(comm, exe, argv):
    """The claude CLI itself, or node running it."""
    names = {comm, os.path.basename(exe or "")} | ({os.path.basename(argv[0])} if argv else set())
    if "claude" in names:
        return True
    if names & {"node", "nodejs", "bun"}:
        return any(re.search(r"(?:^|/)(?:claude(?:\.js|\.mjs)?|@anthropic-ai/claude-code(?:/|$))", a) for a in argv[1:3])
    return False


def env_problem(env):
    """Why an environment does not belong to a cc-dash started by the human: a
    variable Claude Code sets for its tools, one that makes Python load other
    code (PYTHONPATH, PYTHONHOME, PYTHONSTARTUP, PYTHONINSPECT, ...), or one that
    makes the dynamic loader load other code (LD_PRELOAD, LD_LIBRARY_PATH,
    LD_AUDIT, ...). bin/cc-dash re-executes itself without the last two kinds."""
    seen = [k for k in CLAUDE_ENV_SIGNALS if env.get(k)]
    if seen:
        return "its environment carries %s (set by Claude Code)" % ", ".join(seen)
    py = sorted(k for k in env if k.startswith("PYTHON"))
    if py:
        return "its environment carries %s, which can make Python load other code" % ", ".join(py)
    ld = sorted(k for k in env if k.startswith("LD_"))
    if ld:
        return "its environment carries %s, which can make the loader load other code" % ", ".join(ld)
    return ""


def claude_lineage(pid, proc="/proc", parents=None):
    """Why pid counts as started inside Claude Code: the claude CLI is among its
    parents, or its session or process-group leader descends from it (they
    outlive a double fork). '' otherwise. `parents` overrides the parent chain."""
    st = proc_stat(pid, proc)
    if parents is None:
        parents = ancestors(st["ppid"], proc=proc) if st else []
    for p, comm, exe, argv in parents:
        if is_claude(comm, exe, argv):
            return "process %d, %s, is among its parents" % (p, comm)
    if st is None:
        return ""
    for label, leader in (("session", st["sid"]), ("process-group", st["pgrp"])):
        if leader in (0, pid):
            continue
        for p, comm, exe, argv in ancestors(leader, proc=proc):
            if is_claude(comm, exe, argv):
                return "its %s leader descends from process %d, %s" % (label, p, comm)
    return ""


def reparented(st, proc="/proc"):
    """True when a process (its proc_stat) was handed to init or the user manager."""
    parent = proc_stat(st["ppid"], proc) if st["ppid"] > 1 else None
    return st["ppid"] <= 1 or (parent or {}).get("comm") in ("systemd", "init")


def fresh_session(pid, proc="/proc"):
    """Why pid looks double-forked: it leads its own session and was re-parented."""
    st = proc_stat(pid, proc)
    if st is not None and st["sid"] == pid and reparented(st, proc):
        return "it leads a fresh session and was re-parented to pid %d" % st["ppid"]
    return ""


def _is_python(exe):
    return bool(exe) and os.path.basename(exe).startswith("python")


def dashboard_problem(pid, root, proc="/proc"):
    """'' when pid is cc-dash from the checkout at `root`, started the way
    bin/cc-dash starts itself and from outside Claude Code; else the reason.

    The kernel's view decides: /proc/<pid>/exe must be a Python interpreter, and
    its argv must be bin/cc-dash's own re-exec, `python -I <root>/bin/cc-dash`
    (resolved against the process's working directory). -I keeps PYTHON*
    variables, the user site directory and the current directory out of it.
    Then cc-dash's own launch rules, checked from outside so a modified copy
    cannot skip them: no Claude Code, PYTHON* or LD_* variable in its environment, no
    claude process among its parents or its session and process-group leaders'
    parents, and not a fresh session after a re-parent."""
    if not isinstance(pid, int) or pid <= 1:
        return "the lease names no process"
    exe = _link(pid, "exe", proc)
    if exe is None:
        return "process %d is gone or cannot be inspected" % pid
    if not _is_python(exe):
        return "process %d runs %s, not Python" % (pid, exe)
    argv = cmdline(pid, proc) or []
    if len(argv) < 3 or argv[1] != "-I":
        return "process %d was not started the way cc-dash starts itself (python -I .../bin/cc-dash)" % pid
    cwd = _link(pid, "cwd", proc)
    if cwd is None:
        return "cannot read the working directory of process %d" % pid
    script = os.path.realpath(os.path.join(cwd, argv[2]))
    want = os.path.join(os.path.realpath(root), "bin", "cc-dash")
    if script != want:
        return "process %d runs %s, not %s (the cc_tool checkout this project was set up from)" % (pid, script, want)
    env = environ(pid, proc)
    if env is None:
        return "cannot read the environment of process %d" % pid
    why = env_problem(env) or claude_lineage(pid, proc) or fresh_session(pid, proc)
    return ("process %d: %s" % (pid, why)) if why else ""


def read_pin(hooks_dir):
    """The cc_tool root cc-setup pinned in <hooks_dir>/cc_tool_root, or ''. Only a
    plain file we own that nobody else can write counts."""
    p = os.path.join(hooks_dir, PIN_NAME)
    try:
        fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        return ""
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o022 or st.st_size > 4096:
            return ""
        raw = os.read(fd, 4096).decode("utf-8", "replace").strip()
    except OSError:
        return ""
    finally:
        os.close(fd)
    return raw if os.path.isabs(raw) and "\n" not in raw else ""


def holds(pid, ident, proc="/proc"):
    """True when pid has the file whose (st_dev, st_ino) is ident open."""
    d = "%s/%d/fd" % (proc, pid)
    try:
        names = os.listdir(d)
    except (OSError, TypeError):
        return False
    for n in names:
        try:
            st = os.stat(os.path.join(d, n))
        except OSError:
            continue
        if (st.st_dev, st.st_ino) == tuple(ident):
            return True
    return False


def hook_problem(pid, root, proc="/proc", ident=None, projects=None):
    """'' when pid is Python running exactly <project>/.claude/hooks/dash-control.py,
    where <project> is the CLAUDE_PROJECT_DIR that Claude Code put in that
    process's environment, set up from the checkout at `root` (the cc_tool_root
    beside it names it), and (with `ident`, the request file's (st_dev, st_ino))
    holding that very file open, as the hook does while it waits; else why not.
    A copy of the hook anywhere else is not a hook, and naming a real hook's pid
    in a planted file is not enough: the hook holds only the file it wrote.
    With `projects` (the real paths cc-setup registered), the hook's project
    must be one of them: a copy of the hook in a made-up <dir>/.claude/hooks
    run with CLAUDE_PROJECT_DIR=<dir> is not a project anyone set up."""
    if not isinstance(pid, int) or pid <= 1:
        return "the request names no hook process"
    exe = _link(pid, "exe", proc)
    if exe is None:
        return "its hook process %d is gone" % pid
    if not _is_python(exe):
        return "process %d runs %s, not Python" % (pid, exe)
    argv = cmdline(pid, proc) or []
    cwd = _link(pid, "cwd", proc)
    if len(argv) < 2 or os.path.basename(argv[1]) != "dash-control.py" or cwd is None:
        return "process %d is not running dash-control.py" % pid
    script = os.path.realpath(os.path.join(cwd, argv[1]))
    project = (environ(pid, proc) or {}).get("CLAUDE_PROJECT_DIR") or ""
    hooks_dir = os.path.realpath(os.path.join(project, ".claude", "hooks")) if os.path.isabs(project) else ""
    if not hooks_dir or script != os.path.join(hooks_dir, "dash-control.py"):
        return "process %d runs %s, not <its project>/.claude/hooks/dash-control.py" % (pid, script)
    if projects is not None and os.path.realpath(project) not in projects:
        return "process %d runs the hook of %s, a project cc-setup did not register" % (pid, project)
    pin = read_pin(os.path.dirname(script))
    if not pin or os.path.realpath(pin) != os.path.realpath(root):
        return "process %d runs %s, which no cc-setup from %s installed" % (pid, script, root)
    if ident is not None and not holds(pid, ident, proc):
        return "hook process %d did not write this file (it does not hold it open)" % pid
    return ""


# ── the dashboard's code, as cc-setup recorded it ───────────────────────────
MANIFEST_NAME = "cc_dash_manifest.json"
CODE_CHANGED = "cc-dash's code changed since cc-setup ran; run cc-update-project to re-record it if you changed it yourself"
CODE_AFTER_START = "cc-dash's code changed after the dashboard started; restart it"
_HASHES = {}
# Outside lib/: the two files cc-dash runs from elsewhere in the checkout, and the
# directories they sit in (a swapped directory changes their ctime).
_SCOPE_FILES = ("bin/cc-dash", "templates/hooks/cc_procs.py")
_SCOPE_PARENTS = ("bin", "templates", "templates/hooks")


def scan_code(root):
    """({file: lstat}, {dir: lstat}, [problem entries]) for cc-dash's code under root:
    bin/cc-dash, templates/hooks/cc_procs.py, and every entry under lib/ (lib/ is
    where cc-dash's package lives; anything there could be imported), relative
    paths. __pycache__ directories are skipped: bin/cc-dash never reads them (its
    pycache_prefix points nowhere) and writes none. A symlink or anything but a
    plain file or directory, anywhere in scope, is a problem entry (lstat)."""
    files, dirs, bad = {}, {}, []

    def look(rel, want_dir):
        try:
            st = os.lstat(os.path.join(root, rel))
        except OSError:
            return None
        ok = stat.S_ISDIR(st.st_mode) if want_dir else stat.S_ISREG(st.st_mode)
        if not ok:
            bad.append(rel)
            return None
        return st

    for rel in _SCOPE_PARENTS:
        st = look(rel, True)
        if st is not None:
            dirs[rel] = st
    for rel in _SCOPE_FILES:
        st = look(rel, False)
        if st is not None:
            files[rel] = st
    st = look("lib", True)
    if st is not None:
        dirs["lib"] = st
        stack = ["lib"]
        while stack:
            d = stack.pop()
            try:
                names = sorted(os.listdir(os.path.join(root, d)))
            except OSError:
                bad.append(d)
                continue
            for n in names:
                rel = d + "/" + n
                try:
                    est = os.lstat(os.path.join(root, rel))
                except OSError:
                    continue
                if stat.S_ISDIR(est.st_mode):
                    if n == "__pycache__":
                        continue
                    dirs[rel] = est
                    stack.append(rel)
                elif stat.S_ISREG(est.st_mode):
                    files[rel] = est
                else:
                    bad.append(rel)          # a symlink, socket, fifo or device
    return files, dirs, bad


def file_hash(path, st=None):
    """sha256 of a plain file (no symlink followed), cached in this process by
    (size, mtime, ctime, inode, device): ctime cannot be set back by a normal
    user, so a rewrite always misses the cache."""
    import hashlib
    st = st or os.lstat(path)
    key = (path, st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino, st.st_dev)
    h = _HASHES.get(key)
    if h is None:
        d = hashlib.sha256()
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
        with os.fdopen(fd, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                d.update(chunk)
        h = _HASHES[key] = d.hexdigest()
    return h


def build_manifest(root):
    """What cc-setup records beside the hooks: {"v": 2, "root", "files": {path:
    sha256}, "dirs": [path]} for scan_code's scope. Raises ValueError when the
    scope holds a symlink or a special file (the hook would refuse it anyway)."""
    root = os.path.realpath(root)
    files, dirs, bad = scan_code(root)
    if bad:
        raise ValueError("not a plain file or directory: %s" % ", ".join(bad))
    return {"v": 2, "root": root, "files": {rel: file_hash(os.path.join(root, rel), st) for rel, st in sorted(files.items())},
            "dirs": sorted(dirs)}


def manifest_problem(root, manifest):
    """'' when cc-dash's code under root is exactly what the manifest recorded:
    the same files with the same sha256, the same directories, nothing unlisted
    of any name or kind, no symlink or special file. Else CODE_CHANGED and what
    differs."""
    root = os.path.realpath(root)
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if (not isinstance(files, dict) or manifest.get("v") != 2 or manifest.get("root") != root
            or not isinstance(manifest.get("dirs"), list)):
        return CODE_CHANGED + " (the record is missing, old, or for another checkout)"
    have_files, have_dirs, bad = scan_code(root)
    if bad:
        return CODE_CHANGED + " (%s is a symlink or not a plain file)" % bad[0]
    for kind, have, want in (("file", set(have_files), set(files)), ("directory", set(have_dirs), set(manifest["dirs"]))):
        if have - want:
            return CODE_CHANGED + " (new %s %s)" % (kind, sorted(have - want)[0])
        if want - have:
            return CODE_CHANGED + " (%s is missing)" % sorted(want - have)[0]
    for rel, want in sorted(files.items()):
        try:
            if file_hash(os.path.join(root, rel), have_files[rel]) != want:
                return CODE_CHANGED + " (%s differs)" % rel
        except OSError:
            return CODE_CHANGED + " (%s cannot be read)" % rel
    return ""


START_MARGIN = 1.0      # seconds: ctime granularity, tick truncation and clock jitter


def process_start(pid, proc="/proc"):
    """When pid started, in seconds since the epoch: /proc/stat btime (whole
    seconds, rounded down) plus /proc/<pid>/stat field 22 (clock ticks after
    boot, rounded down). No wall-clock read is involved, and both roundings make
    the estimate early, never late: the safe direction for code_after_start."""
    try:
        with open("%s/%d/stat" % (proc, pid), encoding="ascii", errors="replace") as fh:
            ticks = int(fh.read().rsplit(")", 1)[1].split()[19])
        with open("%s/stat" % proc, encoding="ascii", errors="replace") as fh:
            btime = next(int(ln.split()[1]) for ln in fh if ln.startswith("btime "))
        return btime + ticks / float(os.sysconf("SC_CLK_TCK"))
    except (OSError, ValueError, IndexError, StopIteration, TypeError):
        return None


def code_after_start(pid, root, manifest_path, proc="/proc", start=None):
    """'' when every file and directory of cc-dash's code (scan_code's scope and
    its parent directories), and the manifest itself, last changed (ctime: any
    write, rename or restore sets it, and a normal user cannot set it back)
    before process pid started; else CODE_AFTER_START. Matching hashes alone do
    not prove the running process loaded that code: it could have been started
    from changed files that were then put back. Fails closed: a change counts
    unless its ctime is at least START_MARGIN before the (early) start estimate,
    so a dashboard started within about a second of a change must be restarted.
    `start` overrides the estimate (tests)."""
    start = process_start(pid, proc) if start is None else start
    if start is None:
        return CODE_AFTER_START + " (the start time of process %s is unknown)" % pid
    files, dirs, _bad = scan_code(os.path.realpath(root))
    entries = [(rel, st) for rel, st in list(files.items()) + list(dirs.items())]
    try:
        entries.append((manifest_path, os.lstat(manifest_path)))
    except OSError:
        return CODE_AFTER_START + " (no record of the code)"
    for rel, st in entries:
        if st.st_ctime_ns / 1e9 >= start - START_MARGIN:
            return CODE_AFTER_START + " (%s changed after process %d started)" % (rel, pid)
    return ""
