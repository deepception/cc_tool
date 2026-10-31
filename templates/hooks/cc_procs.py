"""Who is this process? Checks shared by cc-dash (lib/cc_dash/procs.py loads
this file) and its control hook (dash-control.py, beside this file).

A helper module, not a hook. cc-setup copies it into the project's
.claude/hooks/ with the hooks, and the control hook imports it from there (a
protected path in Claude Code): the hook never runs code from the cc_tool
checkout, which lies outside the project and which an agent could edit.
Standard library only, no package imports.

Everything here reads /proc (Linux). Where there is no /proc (have_proc) the
process checks cannot run, and nothing can tell cc-dash or a hook from any other
program of yours, so control mode does not run there: `cc-dash --control`
refuses to start, cc-dash shows no request, the hook leaves every prompt in the
terminal, and cc-setup records no terminal. The rules that decide also fail
closed on their own when what they read cannot be read (python_problem,
claude_lineage, terminal_problem, hook_problem, peer_holder_problem,
code_after_start, recording_problem, control_mode_problem).

  have_proc()                   whether there is a /proc to read (tests patch
                                it to play a system without one)
  dashboard_problem(pid, root, python)
                                why pid is not the cc-dash of the checkout at
                                root, run by the recorded Python interpreter,
                                launched the way cc-dash launches itself and
                                from outside Claude Code ('' when it is)
  control_mode_problem(pid)     why that dashboard is not in control mode: no
                                --control flag, or no controlling terminal
  hook_problem(pid, root)       why pid is not a dash-control.py hook installed
                                by that checkout ('' when it is)
  peer_cred(sock), peer_pidfd   who is at the other end of the hook's Unix
                                socket (bind_private_socket, socket_file_problem)
  manifest_problem(root, m)     why cc-dash's code under root is not what the
                                manifest m recorded ('' when it is); build_manifest
                                makes one (cc-setup), with the interpreter
  code_after_start(pid, ...)    why the dashboard pid may not be running that code
                                (it changed after the process started)
  claude_lineage(pid)           why pid counts as started inside Claude Code (or
                                cannot be told apart from that: a leader that
                                cannot be inspected counts)
  driver_detached(pid)          a terminal driver (script, expect, ...) among its
                                parents or its leaders' parents, itself detached
  terminal_problem(pid, terminals)
                                a holder of the master side of its terminal that
                                is not one of your recorded terminals, or is, or
                                descends from, Claude Code (fail closed);
                                detect_terminals records them (cc-setup)
  fresh_session(pid)            a process that leads a session it started after
                                being re-parented (a typical double-fork escape)
  origin_problem(pid, ...)      all of the above, the one set of rules for
                                cc-dash --control, the hook's check of the
                                dashboard and cc-setup's terminal recording
                                (recording_problem)
  detached(pid)                 re-parented to init or a subreaper with no
                                controlling terminal left
"""
import os
import re
import stat

# What Claude Code puts in the environment of its Bash tool's processes
# (checked against `env` from a headless `claude -p` Bash call, CLI 2.1.30x).
CLAUDE_ENV_SIGNALS = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PID",
                      "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_CHILD_SESSION")
PIN_NAME = "cc_tool_root"


def have_proc():
    """True where /proc can tell processes apart (Linux). Without it the
    dashboard's lease and socket could be any program of yours (one an agent
    started), so control mode refuses rather than trusting them: callers treat
    False as "no", never as "nothing to check". dash-control.py has its own
    copy of this check."""
    return os.path.isdir("/proc/self")


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
    return chain(os.getppid() if pid is None else pid, n, proc)[0]


def chain(pid, n=64, proc="/proc"):
    """(ancestors(pid), complete): complete is False when a process on the way
    up could not be read (gone, or hidden), so the list may miss a link. Init,
    or a parent outside this pid namespace (ppid 0), ends a complete chain."""
    out = []
    for _ in range(n):
        if not isinstance(pid, int):
            return out, False
        if pid <= 1:
            return out, True
        st = proc_stat(pid, proc)
        if st is None:
            return out, False
        out.append((pid, st["comm"], _link(pid, "exe", proc) or "", cmdline(pid, proc) or []))
        pid = st["ppid"]
    return out, False


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


def _leaders(pid, st):
    """(label, leader pid) of the session and process-group leaders that are not pid itself."""
    return [(label, leader) for label, leader in (("session", st["sid"]), ("process-group", st["pgrp"])) if leader != pid]


def claude_lineage(pid, proc="/proc", parents=None):
    """Why pid counts as started inside Claude Code: the claude CLI is among its
    parents, or its session or process-group leader descends from it (they
    outlive a double fork). Fails closed: a leader that is gone, hidden or in
    another pid namespace, or a parent chain that cannot be read to the end,
    counts too, because then nobody can tell. '' otherwise. `parents`
    overrides the parent chain."""
    st = proc_stat(pid, proc)
    if st is None:
        return "process %d cannot be inspected" % pid
    if parents is None:
        parents, complete = chain(st["ppid"], proc=proc)
        if not complete:
            return "its parent chain cannot be read to the end (a process on the way is gone or hidden)"
    for p, comm, exe, argv in parents:
        if is_claude(comm, exe, argv):
            return "process %d, %s, is among its parents" % (p, comm)
    for label, leader in _leaders(pid, st):
        if leader <= 0:
            return "its %s leader is outside this pid namespace, so it cannot be inspected" % label
        up, complete = chain(leader, proc=proc)
        if not up:
            return "its %s leader (process %d) is gone or cannot be inspected" % (label, leader)
        for p, comm, exe, argv in up:
            if is_claude(comm, exe, argv):
                return "its %s leader descends from process %d, %s" % (label, p, comm)
        if not complete:
            return "the parent chain of its %s leader (process %d) cannot be read to the end" % (label, leader)
    return ""


# Programs that run a command on a terminal of their own making and read what it
# writes there. One of them that was itself detached (re-parented to init or a
# subreaper) is how something running as you gets a terminal, and with it the
# control code, without you: `setsid -f script -qc "cc-dash --control" /dev/null`.
# tmux and screen are not on the list: you run cc-dash in them yourself.
PTY_DRIVERS = {"script", "expect", "expectk", "unbuffer", "empty", "socat", "ttyrec", "dtach", "abduco", "pty-run"}
_SCRIPT_HOSTS = {"sh", "bash", "dash", "zsh", "ksh", "tclsh", "wish", "perl", "python"}


def _bare(name):
    """A program name without a version suffix: tclsh8.6 -> tclsh, python3.12 -> python."""
    name = os.path.basename(name or "")
    return re.sub(r"[\d.]+$", "", name) or name


def is_pty_driver(comm, exe, argv):
    """True for a terminal driver from PTY_DRIVERS, also when an interpreter runs
    it as a script (unbuffer is a Tcl script: `tclsh /usr/bin/unbuffer ...`)."""
    names = {_bare(comm), _bare(exe)} | ({_bare(argv[0])} if argv else set())
    if names & PTY_DRIVERS:
        return True
    if names & _SCRIPT_HOSTS and argv:
        script = next((a for a in argv[1:3] if a and not a.startswith("-")), "")
        return _bare(script) in PTY_DRIVERS
    return False


def driver_detached(pid, proc="/proc", parents=None):
    """Why pid runs under a terminal driver (PTY_DRIVERS) that was itself
    detached: one among its parents, or among its session or process-group
    leaders and their parents, re-parented to init or a subreaper. ''
    otherwise (also when the chains cannot be read: claude_lineage refuses
    that). `parents` overrides the parent chain."""
    st = proc_stat(pid, proc)
    if st is None:
        return ""
    chains = [parents if parents is not None else chain(st["ppid"], proc=proc)[0]]
    chains += [chain(leader, proc=proc)[0] for _label, leader in _leaders(pid, st) if leader > 0]
    for up in chains:
        for p, comm, exe, argv in up:
            if is_pty_driver(comm, exe, argv):
                pst = proc_stat(p, proc)
                if pst is None or reparented(pst, proc):
                    return ("process %d, %s, a program that runs commands on a terminal of its own, was detached "
                            "(re-parented to pid %s)" % (p, comm, pst["ppid"] if pst else "?"))
    return ""


# ── who holds the terminal ──────────────────────────────────────────────────
# The control code goes to the dashboard's controlling terminal, so whoever holds
# the master side of that pseudo-terminal can read it. That is the human's
# terminal emulator, sshd, or the human's own tmux/screen server, never the
# claude CLI or something it started. Found structurally, not by name: the
# dashboard's tty_nr names /dev/pts/<n>, and the process with a /dev/ptmx fd
# whose /proc/<p>/fdinfo says `tty-index: <n>` holds its master.
PTMX = ("/dev/ptmx", "/dev/pts/ptmx")


def pts_index(tty_nr):
    """The /dev/pts number of a tty_nr (from /proc/<pid>/stat), or None when it is
    not a pseudo-terminal (a virtual console or serial line has no master)."""
    if not isinstance(tty_nr, int) or tty_nr <= 0:
        return None
    major = (tty_nr >> 8) & 0xfff
    minor = (tty_nr & 0xff) | ((tty_nr >> 12) & 0xfff00)
    return (major - 136) * 256 + minor if 136 <= major <= 143 else None


def pty_master_holders(index, proc="/proc"):
    """The pids holding the master side of /dev/pts/<index> that can be seen
    (pty_master_scan, which also says who could not be looked at)."""
    return pty_master_scan(index, proc)[0]


def uids(pid, proc="/proc"):
    """The real, effective, saved and filesystem uids of pid (/proc/<pid>/status), or None."""
    try:
        with open("%s/%d/status" % (proc, pid), encoding="ascii", errors="replace") as fh:
            for line in fh:
                if line.startswith("Uid:"):
                    return [int(x) for x in line.split()[1:5]]
    except (OSError, ValueError):
        pass
    return None


def claude_descent(pid, proc="/proc"):
    """'' unless pid, its parents, or its session or process-group leaders and
    their parents include the claude CLI; then which one ("process N, claude").
    Reads only /proc/<pid>/stat, cmdline and exe (as far as readable), which stay
    readable for a process that hides its fd table. Unreadable links are not
    counted (claude_lineage is the fail-closed check for the process judged)."""
    st = proc_stat(pid, proc)
    if st is None:
        return ""
    chains = [chain(pid, proc=proc)[0]]
    chains += [chain(leader, proc=proc)[0] for _label, leader in _leaders(pid, st) if leader > 0]
    for up in chains:
        for p, comm, exe, argv in up:
            if is_claude(comm, exe, argv):
                return "process %d, %s" % (p, comm)
    return ""


def _hidden(pid, me, proc):
    """True for a process of ours (any uid in /proc/<pid>/status) that descends
    from Claude Code (claude_descent): one whose open files we cannot read then
    could hold what we look for."""
    return me in (uids(pid, proc) or []) and bool(claude_descent(pid, proc))


def pty_master_scan(index, proc="/proc"):
    """(holders, hidden) for /dev/pts/<index>: holders are the pids with a
    /dev/ptmx fd whose fdinfo says `tty-index: <index>`; hidden are processes
    running as you, descending from Claude Code (claude_descent), whose fd
    table or a ptmx fdinfo cannot be read (any program can make itself
    non-dumpable): such a process could hold that master, and the callers
    refuse (fail closed). A hidden process of yours outside Claude Code's
    lineage (an ssh-agent, an editor's sandbox helper) is not counted: hiding
    from Claude Code needs that and a double fork away from it, the documented
    same-user limit. Other users' processes cannot hold your terminal's master
    without your help."""
    out, hidden = [], []
    me = os.getuid()
    try:
        names = os.listdir(proc)
    except OSError:
        return out, hidden
    for name in names:
        if not name.isdigit():
            continue
        pid = int(name)
        d = "%s/%s/fd" % (proc, name)
        try:
            fds = os.listdir(d)
        except FileNotFoundError:
            continue                       # gone
        except OSError:
            if os.path.isdir("%s/%s" % (proc, name)) and _hidden(pid, me, proc):
                hidden.append(pid)
            continue
        for fd in fds:
            try:
                if os.readlink(os.path.join(d, fd)) not in PTMX:
                    continue
            except FileNotFoundError:
                continue                   # closed meanwhile
            except OSError:
                if _hidden(pid, me, proc):
                    hidden.append(pid)
                    break
                continue
            try:
                with open("%s/%s/fdinfo/%s" % (proc, name, fd), encoding="ascii", errors="replace") as fh:
                    m = re.search(r"^tty-index:\s*(\d+)\s*$", fh.read(4096), re.M)
            except FileNotFoundError:
                continue
            except OSError:
                if _hidden(pid, me, proc):
                    hidden.append(pid)
                    break
                continue
            if m and int(m.group(1)) == index:
                out.append(pid)
                break
    return sorted(out), sorted(set(hidden))


def hidden_problem(hidden, what, proc="/proc"):
    """The reason to refuse when a scan found hidden processes, or ''."""
    if not hidden:
        return ""
    h = hidden[0]
    st = proc_stat(h, proc)
    return ("a hidden process descending from Claude Code could hold %s: process %d (%s, under %s) runs as you but its "
            "open files cannot be read" % (what, h, (st or {}).get("comm", "?"), claude_descent(h, proc) or "Claude Code"))


def _inherited_from(h, hosts, proc="/proc"):
    """True when holder h descends from one of `hosts` (pids holding the same
    master): it inherited the fd from that terminal, as the shells some
    terminals start keep their siblings' masters (a leaked fd)."""
    up, complete = chain(h, proc=proc)
    return complete and any(p in hosts for p, _c, _e, _a in up[1:])


def terminal_problem(pid, proc="/proc", terminals=None, mountinfo="/proc/self/mountinfo", cache=None):
    """Why the terminal pid writes to (and the control code with it) may be read
    by something other than your own terminal. The processes holding the master
    side of its controlling pseudo-terminal (pty_master_holders) are judged:

      with `terminals` (the recorded terminal hosts, terminal_entry dicts; [] when
      none were recorded): each holder must run one of them (/proc/<p>/exe, see
      terminal_host_ok), or descend from a holder that does (an inherited fd);
      and, as extra signals, no holder may be the claude CLI, descend from it, or
      carry a variable Claude Code sets for its tools.

    Fails closed: nobody visible holding the master, a holder whose program,
    parents or environment cannot be read, or a process of yours descending
    from Claude Code whose open files cannot be read (pty_master_scan), counts. '' when the holders pass, and
    when pid has no controlling terminal or one that is not a pseudo-terminal
    (the detach rules judge the first; nothing as you can hold a console's
    master). terminals=None skips the recorded-host rule (only the signals)."""
    st = proc_stat(pid, proc)
    if st is None:
        return "process %d cannot be inspected" % pid
    index = pts_index(st["tty"])
    if index is None:
        return ""
    holders, hidden = pty_master_scan(index, proc)
    why = hidden_problem(hidden, "this terminal (/dev/pts/%d)" % index, proc)
    if why:
        return why
    if not holders:
        return ("nobody visible holds the master side of its terminal (/dev/pts/%d), so who reads it cannot be "
                "told" % index)
    if terminals is not None:
        exes = {h: _link(h, "exe", proc) for h in holders}
        hosts = [h for h in holders if exes[h] and terminal_host_ok(exes[h], terminals, "%s/%d/exe" % (proc, h), mountinfo, cache)]
        for h in holders:
            if h not in hosts and not _inherited_from(h, hosts, proc):
                return ("the terminal showing the control code (/dev/pts/%d) is held by %s (process %d), which is not one "
                        "of your recorded terminals; run cc-update-project from your terminal to record it"
                        % (index, exes[h] or "a program that cannot be read", h))
    for h in holders:
        # The terminal programs themselves (not a shell that inherited the fd from one, as the shells
        # of an IDE's terminals keep their siblings' masters) must not be interpreters by content.
        copy = interpreter_copy("%s/%d/exe" % (proc, h), cache) if not _inherited_from(h, holders, proc) else ""
        if copy:
            return ("process %d, which holds its terminal (/dev/pts/%d), runs a copy of %s, an interpreter or shell"
                    % (h, index, copy))
        env = environ(h, proc)
        if env is None:
            return "cannot read the environment of process %d, which holds its terminal (/dev/pts/%d)" % (h, index)
        seen = [k for k in CLAUDE_ENV_SIGNALS if env.get(k)]
        if seen:
            return ("process %d, which holds its terminal (/dev/pts/%d), carries %s (set by Claude Code)"
                    % (h, index, ", ".join(seen)))
        # The environment checks apply to every holder; the argv switches too, except for a shell (by
        # content) that inherited the fd from a terminal: an IDE terminal's zsh runs with -i, interactive.
        inherited_shell = _inherited_from(h, holders, proc) and is_shell("%s/%d/exe" % (proc, h), cache)
        why = debug_problem(h, proc, switches=not inherited_shell)
        if why:
            return "%s; it holds its terminal (/dev/pts/%d)" % (why, index)
        up, complete = chain(h, proc=proc)
        for p, comm, exe, argv in up:
            if is_claude(comm, exe, argv):
                return ("process %d, which holds its terminal (/dev/pts/%d), %s process %d, %s"
                        % (h, index, "is" if p == h else "descends from", p, comm))
        if not up or not complete:
            return "the parent chain of process %d, which holds its terminal (/dev/pts/%d), cannot be read" % (h, index)
    return ""


# ── your terminals, as cc-setup recorded them ───────────────────────────────
# An AppImage (Cursor's, say) runs from a FUSE mount at /tmp/.mount_<name><6
# random characters>/ that changes every launch, so its programs are recorded by
# image name, path inside the image, size and full sha256, never by mount path
# alone (a plain directory of that name could be made once the mount is gone).
APPIMAGE_RE = re.compile(r"^(/tmp/\.mount_(.+?)[A-Za-z0-9]{6})/(.+)$")


def _full_hash(fd):
    import hashlib
    d = hashlib.sha256()
    os.lseek(fd, 0, 0)
    while True:
        b = os.read(fd, 1 << 20)
        if not b:
            break
        d.update(b)
    return d.hexdigest()


class TerminalCache:
    """Full sha256s of terminal-host programs (an AppImage's binary is about 200
    MB, half a second to hash), in this process's memory only: a hook process
    lives for one event, so it hashes such a program at most once per
    permission request, and cc-dash once at startup. Never kept in a file: one
    a Bash write could forge would let another program pass for your terminal.
    An entry counts only for exactly the (st_dev, st_ino, size, mtime_ns,
    ctime_ns) and mount it was made for (any change rehashes). `hashed` counts
    the full hashes this object computed."""

    def __init__(self):
        self.entries, self.hashed = {}, 0

    def sha256(self, open_path, mount=""):
        """Full sha256 of the file at open_path (e.g. /proc/<pid>/exe, the very
        file running), from memory when its key still matches."""
        fd = os.open(open_path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        try:
            st = os.fstat(fd)
            key = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns, mount)
            h = self.entries.get(key)
            if h is None:
                h = self.entries[key] = _full_hash(fd)
                self.hashed += 1
            return h
        finally:
            os.close(fd)


TERMINAL_HASHES = TerminalCache()      # this process's, shared by every check in it


def terminal_entry(path):
    """What cc-setup records for a terminal host program: {"path": real path},
    plus {"appimage": {"name", "rel", "size", "sha256"}} when it runs from an
    AppImage mount (the full sha256). None when the path is not an executable file."""
    real = os.path.realpath(path)
    if not (os.path.isfile(real) and os.access(real, os.X_OK)):
        return None
    e = {"path": real}
    m = APPIMAGE_RE.match(real)
    if m:
        e["appimage"] = {"name": m.group(2), "rel": m.group(3), "size": os.stat(real).st_size,
                         "sha256": TerminalCache().sha256(real)}
    return e


def _fuse_mount(mountpoint, mountinfo="/proc/self/mountinfo"):
    """The mount ID of mountpoint when it is a FUSE mount (an AppImage's), from
    mountinfo, else ''."""
    try:
        with open(mountinfo, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                left, _, right = line.partition(" - ")
                f = left.split()
                if len(f) >= 5 and f[4] == mountpoint and right.split()[:1] and right.split()[0].startswith("fuse"):
                    return f[0]
    except OSError:
        pass
    return ""


def terminal_host_ok(exe, terminals, open_path=None, mountinfo="/proc/self/mountinfo", cache=None):
    """True when the program at real path `exe` is one of the recorded terminal
    hosts: the same real path (a program installed normally), or, for an
    AppImage, the same image name and path inside it, on a FUSE mount, with the
    same size and full sha256 (read through open_path, e.g. /proc/<pid>/exe,
    the very file running; `cache`, a TerminalCache, default this process's
    TERMINAL_HASHES, spares rehashing within the process)."""
    terminals = [t for t in terminals or [] if isinstance(t, dict)]
    if any(t.get("path") == exe and not isinstance(t.get("appimage"), dict) for t in terminals):
        return True
    m = APPIMAGE_RE.match(exe or "")
    if not m:
        return False
    cache = cache if cache is not None else TERMINAL_HASHES
    for t in terminals:
        a = t.get("appimage")
        if isinstance(a, dict) and a.get("name") == m.group(2) and a.get("rel") == m.group(3) \
                and isinstance(a.get("sha256"), str):
            try:
                st = os.stat(open_path or exe)
                mount = _fuse_mount(m.group(1), mountinfo)
                if st.st_size == a.get("size") and mount and \
                        cache.sha256(open_path or exe, "%s %s" % (mount, m.group(1))) == a["sha256"]:
                    return True
            except OSError:
                continue
    return False


# Programs never recorded as a terminal host, whatever holds the terminal: a generic
# interpreter or pty driver, judged by its program's name family (python3.12 ->
# python), can run anything, so recording it would let any copy of it host the code.
# tmux and screen stay recordable: you use them, an agent-started one carries Claude
# Code's variables, and one that does not is the documented same-user limit.
NOT_RECORDABLE = {"python", "node", "nodejs", "deno", "bun", "perl", "ruby", "sh", "bash", "zsh", "fish", "dash",
                  "ksh", "mksh", "tcsh", "csh", "busybox", "tclsh", "wish", "lua", "luajit", "php", "pwsh", "java",
                  "script", "expect", "expectk", "unbuffer", "socat", "empty", "ttyrec", "dtach", "abduco", "pty-run"}
# A program that would run injected code: Electron or Node told to act as plain Node,
# preload options, or debugger and eval switches.
DEBUG_ENV = ("ELECTRON_RUN_AS_NODE", "NODE_OPTIONS")


# Switches that make a program run code it was handed, bare or as --switch=value.
DEBUG_ARGS = {"-e", "--eval", "-p", "--print", "-r", "--require", "--import", "--loader", "--experimental-loader",
              "--js-flags", "-i", "--interactive"}
DEBUG_ARG_PREFIXES = ("--inspect", "--remote-debugging")


def debug_arg(a):
    """True for an argv entry that makes a program run code it was handed (DEBUG_ARGS
    bare or with =value, --inspect*, --remote-debugging*, any --*-preload*, and
    --enable-logging= with a file target)."""
    name, eq, value = a.partition("=")
    if name in DEBUG_ARGS or name.startswith(DEBUG_ARG_PREFIXES):
        return True
    if name.startswith("--") and "-preload" in name:
        return True
    return name == "--enable-logging" and bool(eq) and value not in ("", "stderr")


def debug_problem(pid, proc="/proc", switches=True):
    """Why pid (a terminal host) may run code it was handed rather than its own:
    ELECTRON_RUN_AS_NODE, NODE_OPTIONS or an ELECTRON_* debug or inspect
    variable in its environment, or (with `switches`) a debug_arg switch in its
    argv. '' otherwise; an unreadable environment counts."""
    env = environ(pid, proc)
    if env is None:
        return "cannot read the environment of process %d" % pid
    bad = sorted(k for k in env if k in DEBUG_ENV or (k.startswith("ELECTRON_") and ("INSPECT" in k or "DEBUG" in k)))
    if bad:
        return "process %d carries %s, which can make it run other code" % (pid, ", ".join(bad))
    for a in (cmdline(pid, proc) or [])[1:] if switches else []:
        if debug_arg(a):
            return "process %d was started with %s, which can make it run other code" % (pid, a)
    return ""


# Interpreters and shells, also under another name: a copy of python3 renamed
# `myterm` is still python3. Judged by content: the sha256 of every program of
# these names on PATH and in the usual directories, hashed only when the size
# matches (node is 120 MB), once per process, in memory only.
INTERPRETER_NAMES = ("node", "nodejs", "deno", "bun", "perl", "ruby", "bash", "sh", "dash", "zsh", "fish", "ksh", "tclsh",
                     "expect", "script", "socat", "unbuffer")
INTERPRETER_GLOBS = ("python3*", "python*")
_INTERPRETERS = []


def interpreters():
    """{real path: size} of the interpreters and shells on this system (cached)."""
    if not _INTERPRETERS:
        import glob
        dirs = [d for d in (os.environ.get("PATH") or "").split(os.pathsep) if os.path.isabs(d)]
        dirs += ["/usr/bin", "/bin", "/usr/local/bin"]
        found = {}
        for d in dict.fromkeys(dirs):
            cands = [os.path.join(d, n) for n in INTERPRETER_NAMES]
            for g in INTERPRETER_GLOBS:
                cands += glob.glob(os.path.join(glob.escape(d), g))
            for c in cands:
                real = os.path.realpath(c)
                try:
                    st = os.stat(real)
                except OSError:
                    continue
                if stat.S_ISREG(st.st_mode) and os.access(real, os.X_OK):
                    found[real] = st.st_size
        _INTERPRETERS.append(found)
    return _INTERPRETERS[0]


SHELLS = {"bash", "sh", "dash", "zsh", "fish", "ksh"}


def is_shell(open_path, cache=None):
    """True when the program at open_path has, by content, the sha256 of one of the
    shells (SHELLS) found on this system (sh is often dash under another name)."""
    copy = interpreter_copy(open_path, cache)
    if not copy:
        return False
    cache = cache if cache is not None else TERMINAL_HASHES
    mine = cache.sha256(copy)
    for p, n in interpreters().items():
        if os.path.basename(p) in SHELLS and n == interpreters()[copy]:
            try:
                if cache.sha256(p) == mine:
                    return True
            except OSError:
                continue
    return False


def interpreter_copy(open_path, cache=None):
    """The real path of the interpreter or shell whose content the program at
    open_path (e.g. /proc/<pid>/exe) has, or ''. Only same-size candidates are
    hashed; hashes are kept in `cache` (default this process's TERMINAL_HASHES)."""
    cache = cache if cache is not None else TERMINAL_HASHES
    try:
        size = os.stat(open_path).st_size
    except OSError:
        return ""
    same = [p for p, n in interpreters().items() if n == size]
    if not same:
        return ""
    try:
        mine = cache.sha256(open_path)
    except OSError:
        return ""
    for p in same:
        try:
            if cache.sha256(p) == mine:
                return p
        except OSError:
            continue
    return ""


def recording_problem(pid=None, proc="/proc", env=None):
    """Why cc-setup running as pid (default: this process) must not record any
    terminal: exactly the rules `cc-dash --control` starts by (origin_problem:
    Claude Code's variables in `env`, default os.environ; the claude CLI among
    its parents or its session and process-group leaders' parents, failing
    closed on anything unreadable; a detached terminal driver; a fresh session
    after a re-parent; re-parented with no terminal), and it must run on a
    pseudo-terminal of its own (pty_problem). An agent could otherwise run
    cc-update-project on a terminal of its own making and have that program
    recorded. '' when it may record."""
    env = os.environ if env is None else env
    pid = os.getpid() if pid is None else pid
    st = proc_stat(pid, proc)
    if st is None:
        return "its own process cannot be inspected"
    return origin_problem(pid, proc, env=env, terminal=lambda: pty_problem(st))[1]


def pty_problem(st):
    """Why a process (its proc_stat) has no controlling pseudo-terminal, or ''."""
    if pts_index(st["tty"]) is None:
        return "it is not running on a terminal (it has no controlling pseudo-terminal)"
    return ""


def claude_env(env):
    """Which of Claude Code's tool variables `env` carries, as a reason, or ''."""
    seen = [k for k in CLAUDE_ENV_SIGNALS if (env or {}).get(k)]
    return ("its environment carries %s (set by Claude Code)" % ", ".join(seen)) if seen else ""


def origin_problem(pid, proc="/proc", env=None, parents=None, terminal=None):
    """(inside, why): why pid was not started by the human from their own
    terminal, the rules shared by `cc-dash --control` (server.control_refusal),
    the control hook's check of the dashboard (dashboard_problem) and cc-setup's
    terminal recording (recording_problem), in this order:

      Claude Code's tool variables in `env` (None skips this rule)
      claude_lineage    the claude CLI among its parents or its session and
                        process-group leaders' parents; unreadable counts
      driver_detached   a detached terminal driver (script, expect ...) on the way
      terminal()        the caller's terminal rule (a callable returning a
                        reason or ''; None skips it)
      fresh_session     it, or its session leader, leads a session it started
                        after being re-parented (a double fork)
      detached          re-parented with no controlling terminal left

    `inside` is True for the first two (started inside Claude Code, or cannot be
    told apart from that). ('', False) when every rule passes."""
    why = claude_env(env) if env is not None else ""
    if why:
        return True, why
    why = claude_lineage(pid, proc, parents=parents)
    if why:
        return True, why
    why = driver_detached(pid, proc, parents=parents) or (terminal() if terminal is not None else "")
    if why:
        return False, why
    why = fresh_session(pid, proc)
    st = proc_stat(pid, proc)
    if not why and st is not None and 0 < st["sid"] != pid:
        lead = fresh_session(st["sid"], proc)
        why = ("its session leader (process %d) %s" % (st["sid"], lead.replace("it leads", "leads", 1))) if lead else ""
    return False, why or detached(pid, proc)


def detect_terminals(pid=None, proc="/proc", skipped=None):
    """The real paths of the programs holding the master side of pid's
    controlling terminal (default: this process's): the terminal it runs in
    (gnome-terminal-server, konsole, kitty, Cursor's or VS Code's pty host, a
    tmux or screen server). Left out: holders that only inherited the fd from
    another holder (a shell that keeps its siblings' masters), the holders'
    parents (your shell, the user manager), and any holder that is, descends
    from or carries the variables of Claude Code, whose parent chain cannot be
    read, that runs with debug or eval switches (debug_problem), or whose
    program is a generic interpreter or pty driver (NOT_RECORDABLE). Nothing at
    all when a hidden process of yours descending from Claude Code could hold
    it (pty_master_scan). `skipped`
    (a list) receives (path, why) for each holder left out for those reasons.
    None when pid has no pseudo-terminal at all."""
    pid = os.getpid() if pid is None else pid
    st = proc_stat(pid, proc)
    index = pts_index(st["tty"]) if st else None
    if index is None:
        return None
    holders, hidden = pty_master_scan(index, proc)
    if hidden:
        if skipped is not None:
            skipped.append(("this terminal", hidden_problem(hidden, "this terminal (/dev/pts/%d)" % index, proc)))
        return []
    roots = [h for h in holders if not _inherited_from(h, holders, proc)]
    out = set()
    for h in roots:
        exe = _link(h, "exe", proc)
        if not exe or exe.endswith(" (deleted)"):
            continue
        path = os.path.realpath(exe) if proc == "/proc" else exe
        env = environ(h, proc)
        up, complete = chain(h, proc=proc)
        why = ""
        if _bare(path) in NOT_RECORDABLE:
            why = "a generic interpreter or terminal driver is never recorded"
        elif interpreter_copy("%s/%d/exe" % (proc, h)):
            why = "it is a copy of %s; a generic interpreter or terminal driver is never recorded" % (
                interpreter_copy("%s/%d/exe" % (proc, h)))
        elif env is None or any(env.get(k) for k in CLAUDE_ENV_SIGNALS):
            why = "it carries Claude Code's variables" if env is not None else "its environment cannot be read"
        elif any(is_claude(c, e, a) for _p, c, e, a in up) or not complete:
            why = "it is, or descends from, Claude Code" if complete else "its parent chain cannot be read"
        else:
            why = debug_problem(h, proc)
        if why:
            if skipped is not None:
                skipped.append((path, why))
            continue
        out.add(path)
    return sorted(out)


def merge_terminals(old, paths):
    """Recorded terminal entries: `old` (kept, they are the user's) plus an entry
    for each of `paths`, without duplicates."""
    out, seen = [], set()
    for e in list(old or []) + [terminal_entry(p) for p in paths or []]:
        if not isinstance(e, dict) or not isinstance(e.get("path"), str):
            continue
        a = e.get("appimage") if "appimage" in e else None
        if a is not None and not (isinstance(a, dict) and isinstance(a.get("sha256"), str)):
            continue                       # an AppImage record without its full sha256 matches nothing
        key = ("img", a.get("name"), a.get("rel"), a.get("size"), a.get("sha256")) if a else ("path", e["path"])
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


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


def detached(pid, proc="/proc"):
    """Why pid looks detached from any terminal: re-parented to init or a
    user-manager subreaper, and no controlling terminal left."""
    st = proc_stat(pid, proc)
    if st is not None and st["tty"] == 0 and reparented(st, proc):
        return "it was detached (re-parented to pid %d) and has no controlling terminal" % st["ppid"]
    return ""


def _is_python(exe):
    return bool(exe) and os.path.basename(exe).startswith("python")


# ── the interpreter cc-dash runs on ─────────────────────────────────────────
# cc-setup records it (python_record) in the manifest beside the hooks. bin/cc-dash
# re-executes the interpreter its shebang resolved (/proc/self/exe) and nothing
# else: no file anything running as you can write picks what it executes.


def python_record(path=None):
    """{"path": real path, "sha256": ...} of an interpreter: by default the one
    running this code (the kernel's /proc/self/exe; sys.executable can name
    something else, e.g. with zsh's exported ARGV0)."""
    import sys
    if path is None:
        path = "/proc/self/exe" if os.path.exists("/proc/self/exe") else sys.executable
    real = os.path.realpath(path)
    return {"path": real, "sha256": file_hash(real)}


def exe_identity(pid, proc="/proc"):
    """(path, sha256) of the program pid runs. The hash is of the very file the
    kernel executed (/proc/<pid>/exe opened, not its path: a binary replaced
    since does not count), cached by inode, size, mtime and ctime. (path, None)
    when it cannot be read, (None, None) when the process is gone."""
    link = _link(pid, "exe", proc)
    if link is None:
        return None, None
    import hashlib
    try:
        fd = os.open("%s/%d/exe" % (proc, pid), os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        return link, None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return link, None
        key = ("exe", st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        h = _HASHES.get(key)
        if h is None:
            d = hashlib.sha256()
            while True:
                b = os.read(fd, 1 << 20)
                if not b:
                    break
                d.update(b)
            h = _HASHES[key] = d.hexdigest()
        return link, h
    except OSError:
        return link, None
    finally:
        os.close(fd)


def python_problem(pid, python, proc="/proc"):
    """'' when pid runs exactly the interpreter `python` records (its real path,
    and a file with its sha256); else why not. A binary merely named python is
    not enough."""
    if not (isinstance(python, dict) and isinstance(python.get("path"), str) and os.path.isabs(python["path"])
            and isinstance(python.get("sha256"), str) and re.match(r"^[0-9a-f]{64}$", python["sha256"])):
        return "there is no record of the Python interpreter cc-dash runs on (run cc-update-project)"
    link, h = exe_identity(pid, proc)
    if link is None:
        return "process %d is gone or cannot be inspected" % pid
    if link != python["path"]:
        return ("process %d runs %s, not %s, the Python interpreter cc-setup recorded (run cc-update-project if "
                "you changed python3)" % (pid, link, python["path"]))
    if h is None:
        return "cannot read the program process %d runs" % pid
    if h != python["sha256"]:
        return ("process %d runs a %s that is not the one cc-setup recorded (its sha256 differs; run cc-update-project "
                "if python3 was upgraded, then restart cc-dash)" % (pid, link))
    return ""


def dashboard_problem(pid, root, proc="/proc", python=None, terminals=None, cache=None):
    """'' when pid is cc-dash from the checkout at `root`, run by the interpreter
    cc-setup recorded, started the way bin/cc-dash starts itself and from
    outside Claude Code; else the reason.

    The kernel's view decides: /proc/<pid>/exe must be the recorded interpreter
    (`python`, {"path", "sha256"} from the manifest; None: the interpreter
    running this code), the same real path and the same sha256, and its argv
    must be bin/cc-dash's own re-exec, exactly `<that path> -I <root>/bin/cc-dash`
    (flags for cc-dash may follow). -I keeps PYTHON* variables, the user site
    directory and the current directory out of it. Then cc-dash's own launch
    rules, checked from outside so a modified copy cannot skip them: no Claude
    Code, PYTHON* or LD_* variable in its environment, no claude process among
    its parents or its session and process-group leaders' parents (a leader that
    cannot be inspected counts), no detached terminal driver (script, expect ...)
    on the way, every holder of its terminal's master side one of your recorded
    terminals (`terminals`, from the manifest; None skips that rule) and none
    of them Claude Code (terminal_problem), not a fresh session after a re-parent,
    and not re-parented with no controlling terminal left."""
    if not isinstance(pid, int) or pid <= 1:
        return "the lease names no process"
    if python is None:
        python = python_record()
    why = python_problem(pid, python, proc)
    if why:
        return why
    argv = cmdline(pid, proc) or []
    want = os.path.join(os.path.realpath(root), "bin", "cc-dash")
    if len(argv) < 3 or argv[0] != python["path"] or argv[1] != "-I":
        return "process %d was not started the way cc-dash starts itself (%s -I %s)" % (pid, python["path"], want)
    if argv[2] != want:
        cwd = _link(pid, "cwd", proc) or "/"
        script = os.path.realpath(os.path.join(cwd, argv[2]))
        return "process %d runs %s, not %s (the cc_tool checkout this project was set up from)" % (pid, script, want)
    env = environ(pid, proc)
    if env is None:
        return "cannot read the environment of process %d" % pid
    why = env_problem(env) or origin_problem(
        pid, proc, terminal=lambda: terminal_problem(pid, proc, terminals=terminals, cache=cache))[1]
    return ("process %d: %s" % (pid, why)) if why else ""


def control_mode_problem(pid, proc="/proc"):
    """'' when pid, a dashboard by dashboard_problem, is in control mode; else why
    not. dashboard_problem cannot tell: any flags may follow the script, and a
    process with no terminal passes unless it was also re-parented. Control mode
    has `--control` after `<python> -I <script>` (cc-dash takes no other spelling,
    allow_abbrev=False) and a controlling terminal of any kind: cc-dash goes no
    further without opening /dev/tty for the control code, and /dev/tty is the
    terminal tty_nr names. `ccdash` keeps it (nohup, setpgid and the redirections
    leave the session alone). It goes only when the session's leader or its
    terminal does, and a dashboard whose leader is gone fails claude_lineage
    anyway. Fails closed: a process that cannot be read counts."""
    st = proc_stat(pid, proc)
    argv = cmdline(pid, proc)
    if st is None or argv is None:
        return "process %d cannot be inspected" % pid
    if "--control" not in argv[3:]:
        return "process %d was not started with --control" % pid
    if not st["tty"]:
        return "process %d has no controlling terminal, and control mode needs one" % pid
    return ""


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


# ── the hook's channel to cc-dash: a Unix socket in the control directory ─────
HOOK_SOCK = "hook.sock"
SO_PEERPIDFD = 77           # Linux >= 6.5 (asm-generic/socket.h)


def peer_cred(sock):
    """(pid, uid) of the process at the other end of a connected Unix socket, as
    the kernel recorded it when the connection was made (SO_PEERCRED on Linux;
    LOCAL_PEERPID and LOCAL_PEERCRED on macOS), or (None, None). A pid alone
    says nothing about which program it is: control mode also needs /proc
    (have_proc) and refuses without it."""
    import socket
    import struct
    try:
        if hasattr(socket, "SO_PEERCRED"):
            pid, uid, _gid = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
            return (pid if pid > 0 else None), uid
        import sys
        if sys.platform == "darwin":
            pid = sock.getsockopt(0, 0x002)                       # SOL_LOCAL, LOCAL_PEERPID
            raw = sock.getsockopt(0, 0x001, 76)                   # LOCAL_PEERCRED: struct xucred
            _ver, uid = struct.unpack("Ii", raw[:8])
            return (pid if pid > 0 else None), uid
    except (OSError, struct.error):
        pass
    return None, None


def peer_pidfd(sock):
    """A pidfd for the process at the other end of a connected Unix socket
    (SO_PEERPIDFD, Linux >= 6.5), or None where the kernel has none. It names
    that very process, so a pid reused after it exits does not match it."""
    import socket
    try:
        fd = sock.getsockopt(socket.SOL_SOCKET, SO_PEERPIDFD)
    except OSError:
        return None
    return fd if isinstance(fd, int) and fd >= 0 else None


def pidfd_pid(fd):
    """The pid a pidfd names while that process lives; -1 once it has exited
    (/proc/self/fdinfo/<fd> `Pid:`); None when it cannot be read."""
    try:
        with open("/proc/self/fdinfo/%d" % fd, encoding="ascii", errors="replace") as fh:
            m = re.search(r"^Pid:\s*(-?\d+)\s*$", fh.read(4096), re.M)
    except OSError:
        return None
    return int(m.group(1)) if m else None


def unix_path(path, fn):
    """fn(a name for the Unix socket path that fits sun_path): path itself, or,
    when it is too long (108 bytes on Linux; a deep XDG_STATE_HOME), the same
    file reached through an fd of its directory (/proc/self/fd/<n>/<name>)."""
    if len(path.encode()) < 100 or not os.path.isdir("/proc/self/fd"):
        return fn(path)
    fd = os.open(os.path.dirname(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
    try:
        return fn("/proc/self/fd/%d/%s" % (fd, os.path.basename(path)))
    finally:
        os.close(fd)


def unix_peer_inode(sock):
    """The inode of the other end of a connected Unix socket, from the kernel
    (sock_diag: NETLINK_SOCK_DIAG, UDIAG_SHOW_PEER), or None where it cannot
    say (no netlink, no unix_diag)."""
    import socket
    import struct
    try:
        ino = os.fstat(sock.fileno()).st_ino
        nl = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, 4)        # NETLINK_SOCK_DIAG
    except (OSError, AttributeError):
        return None
    try:
        nl.settimeout(2)
        # struct unix_diag_req: family, protocol, pad, states, ino, show (UDIAG_SHOW_PEER), cookie (none)
        req = struct.pack("=BBHIIIII", socket.AF_UNIX, 0, 0, 0xffffffff, ino, 4, 0xffffffff, 0xffffffff)
        nl.send(struct.pack("=IHHII", 16 + len(req), 20, 1, 1, 0) + req)   # SOCK_DIAG_BY_FAMILY, NLM_F_REQUEST
        data = nl.recv(65536)
        length, kind = struct.unpack("=IH", data[:6])
        if kind != 20:
            return None
        body = data[16:length]
        if struct.unpack("=I", body[4:8])[0] != ino:
            return None
        off = 16                                                          # past struct unix_diag_msg
        while off + 4 <= len(body):
            alen, atype = struct.unpack("=HH", body[off:off + 4])
            if alen < 4:
                break
            if atype == 2:                                                # UNIX_DIAG_PEER
                peer = struct.unpack("=I", body[off + 4:off + 8])[0]
                return peer or None
            off += (alen + 3) & ~3
    except (OSError, struct.error, ValueError):
        return None
    finally:
        nl.close()
    return None


def socket_holders(inode, proc="/proc"):
    """(holders, hidden) for the socket with this inode, from one scan of
    /proc/*/fd: holders have it open; hidden are processes of yours descending
    from Claude Code whose fd table cannot be read (they could hold it too)."""
    target = "socket:[%s]" % inode
    out, hidden = [], []
    me = os.getuid()
    try:
        names = os.listdir(proc)
    except OSError:
        return out, hidden
    for name in names:
        if not name.isdigit():
            continue
        pid = int(name)
        d = "%s/%s/fd" % (proc, name)
        try:
            fds = os.listdir(d)
        except FileNotFoundError:
            continue
        except OSError:
            if os.path.isdir("%s/%s" % (proc, name)) and _hidden(pid, me, proc):
                hidden.append(pid)
            continue
        for fd in fds:
            try:
                if os.readlink(os.path.join(d, fd)) == target:
                    out.append(pid)
                    break
            except FileNotFoundError:
                continue
            except OSError:
                if _hidden(pid, me, proc):
                    hidden.append(pid)
                    break
    return sorted(out), sorted(set(hidden))


def peer_holder_problem(sock, pid, proc="/proc"):
    """'' when the other end of the connected Unix socket `sock` is held by
    process `pid` and nothing else (unix_peer_inode, socket_holders); else why
    not. Fails closed: no answer from the kernel, or a hidden process of yours
    descending from Claude Code, counts."""
    inode = unix_peer_inode(sock)
    if not inode:
        return "the kernel cannot say which socket is at the other end (sock_diag)"
    holders, hidden = socket_holders(inode, proc)
    why = hidden_problem(hidden, "the connecting socket", proc)
    if why:
        return why
    if holders != [pid]:
        return ("the connecting socket is held by %s, not by process %d alone"
                % (", ".join("process %d" % h for h in holders) or "no process that can be seen", pid))
    return ""


def bind_private_socket(directory, name=HOOK_SOCK):
    """A listening Unix socket at <directory>/<name>, made by this process: bound
    under a fresh random name (umask 077, then chmod 0600) in the private
    directory and renamed over <name>, so whatever was planted there before is
    replaced, never reused. Returns (socket, (st_dev, st_ino)) of the file."""
    import socket
    tmp = os.path.join(directory, ".hs.%s" % os.urandom(6).hex())
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o077)
    try:
        unix_path(tmp, s.bind)
    except OSError:
        s.close()
        raise
    finally:
        os.umask(old)
    try:
        os.chmod(tmp, 0o600)
        s.listen(16)
        os.rename(tmp, os.path.join(directory, name))
        st = os.lstat(os.path.join(directory, name))
    except OSError:
        s.close()
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return s, (st.st_dev, st.st_ino)


def socket_file_problem(path):
    """Why the hook must not talk to the Unix socket at path: it is not a socket,
    not ours, open to others (mode beyond 0600), or its directory is not a
    private directory of ours (0700). '' when it may connect; the answering
    process is then checked with peer_cred."""
    try:
        st = os.lstat(path)
        dst = os.lstat(os.path.dirname(path))
    except OSError:
        return "there is no socket at %s" % path
    if not stat.S_ISDIR(dst.st_mode) or dst.st_uid != os.getuid() or dst.st_mode & 0o077:
        return "%s is not a private directory of yours" % os.path.dirname(path)
    if not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        return "%s is not a socket of yours that only you can use" % path
    return ""


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


MANIFEST_V = 3


def build_manifest(root, python=None, terminals=None):
    """What cc-setup records beside the hooks: {"v": 3, "root", "files": {path:
    sha256}, "dirs": [path], "python": {"path", "sha256"}} for scan_code's scope
    and the interpreter cc-dash runs on (`python`, default python_record(): the
    one running this, which for cc-setup is the python3 on PATH, the one
    bin/cc-dash's `#!/usr/bin/env python3` finds), and the terminal hosts
    (`terminals`, terminal_entry dicts; default: the terminal this runs in,
    detect_terminals, when recording_problem allows it, else none). Raises ValueError when the
    scope holds a symlink or a special file (the hook would refuse it anyway)."""
    root = os.path.realpath(root)
    files, dirs, bad = scan_code(root)
    if bad:
        raise ValueError("not a plain file or directory: %s" % ", ".join(bad))
    return {"v": MANIFEST_V, "root": root,
            "files": {rel: file_hash(os.path.join(root, rel), st) for rel, st in sorted(files.items())},
            "dirs": sorted(dirs), "python": python or python_record(),
            "terminal_hosts": terminals if terminals is not None else
            [] if recording_problem() else merge_terminals([], detect_terminals() or [])}


def manifest_problem(root, manifest):
    """'' when cc-dash's code under root is exactly what the manifest recorded:
    the same files with the same sha256, the same directories, nothing unlisted
    of any name or kind, no symlink or special file. Else CODE_CHANGED and what
    differs."""
    root = os.path.realpath(root)
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if (not isinstance(files, dict) or manifest.get("v") != MANIFEST_V or manifest.get("root") != root
            or not isinstance(manifest.get("dirs"), list) or not isinstance(manifest.get("python"), dict)):
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
