#!/usr/bin/env python3
"""Control mode for cc-dash: answer permission prompts, questions and plans,
deliver messages and stop a turn, from the dashboard.

Does nothing unless `cc-dash --control` is running. The dashboard keeps a lease
file fresh in

    ${XDG_STATE_HOME:-~/.local/state}/cc_tool/control/      (0700, made by cc-dash)

and every invocation exits at once, silently, when the lease is missing, not
ours, expired, or its process is gone. The settings.json registrations also put
a shell `test -e` in front of python (on `lease`, or on `pending`, which exists
only while a message or a stop is queued), so a session with no dashboard pays
one shell test per event, not an interpreter start.

PermissionRequest (synchronous; also the dialog behind AskUserQuestion and
ExitPlanMode). The terminal dialog opens at once and stays answerable; the first
answer wins. This hook writes req/<id>.json (session, agent, cwd, tool, input,
kind, a sha256 of tool + canonical input, why it asked: bash-guard's marker or
a matching permissions.ask rule) and polls dec/<id>.json every 200 ms. A
decision applies only when it names this request's hash.
  allow          {"behavior": "allow"}; AskUserQuestion: updatedInput = the
                 input plus "answers"; ExitPlanMode: updatedInput = the input
                 echoed (a bare allow leaves the plan dialog up). A Bash command
                 is never edited.
  allow_session  allow plus updatedPermissions: Claude Code's own addRules
                 suggestion with destination "session" only. Refused for a
                 guard-asked or ask-rule request.
  deny           {"behavior": "deny", "message": ...}; Claude reads it (a plan
                 is revised and resubmitted).
  release        exit silently.
It exits silently, without printing, once the prompt is answered in the terminal
(a late answer is ignored but the hook is not killed on "Yes"): attention.py's
log shows the call ran (PostToolUse/PostToolUseFailure, same tool and target),
its batch ended (PostToolBatch), or the turn moved on (UserPromptSubmit, Stop,
SubagentStop, SessionEnd); or the session registry
(~/.claude/sessions/<claude pid>.json, found through the parent process chain;
undocumented, best effort) leaves "waiting". Also when the lease goes, and after
the lease's wait (default 300 s). On exit it removes its request and writes
res/<id>.json (applied, terminal, released, lease, timeout).

PreToolUse (gated on `pending`): a stop queued for this session/agent
(stop/<session>.<agent|main>.json) denies the call with continue:false, so the
turn stops at this tool call. A running tool cannot be interrupted.

PostToolUse, PostToolUseFailure, UserPromptSubmit, Stop, SubagentStop (gated
on `pending`): delivers messages queued for exactly this session and agent
(msg/<session>.<agent|main>.<id>.json) as additionalContext. Claimed by an
atomic rename into dlv/, so parallel hooks never deliver one twice. A
SubagentStop delivers only to the subagent the message names (internal agents
fire SubagentStop too). Messages to a session's main thread normally go over
its inbox socket instead (cc-dash does that); this is the fallback.

SessionStart (gated on the lease): records the session's inbox socket path
(CLAUDE_CODE_MESSAGING_SOCKET) in sock/<session>.json, so cc-dash can message
an idle session. Never the token.

Files are a doorbell, never the authority. Your agents run as you and can
write into the control directory, so a decision, message or stop file alone does
nothing: before acting, the hook asks the dashboard (GET /api/control/verify on
the lease's port, with the lease's per-launch key, so only someone who can read
the 0600 lease may ask) and applies cc-dash's answer, which comes from its memory of
what was sent from a control-unlocked page in this launch. The answer counts
only when the socket that sent it belongs to the lease's pid (/proc/net/tcp
inode -> /proc/<pid>/fd; lsof where there is no /proc), so a server an agent
starts on another port cannot vouch for a planted file. A decision also names a
digest of what the dashboard card showed (tool, input, cwd, flags), so an
approval cannot be moved onto a request file rewritten after it was shown.
Limit: a program that rewrites the lease AND serves the answer itself runs as
you with your full rights; see docs/guides/commands.md (Control mode, Security).

Files: directory 0700 and ours, files 0600, opened with O_NOFOLLOW, written to a
temp name with O_EXCL and renamed; anything that is not a plain file we own that
nobody else can read is ignored. Every error is swallowed and the hook exits 0.
"""
import hashlib
import json
import os
import re
import stat
import sys
import time

POLL = 0.2
DEFAULT_WAIT = 300.0
MAX_WAIT = 590.0            # under the 600 s registered timeout
MAX_INPUT = 256 * 1024      # bytes of tool input copied into a request for display
MAX_MSG_FILE = 64 * 1024
GUARD_MARK_TTL = 120.0
CLIP = 400
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
QUESTION_TOOLS = {"AskUserQuestion": "question", "ExitPlanMode": "plan"}
SHOWN_KEYS = ("session", "agent_id", "agent_type", "cwd", "tool", "kind", "sig", "input", "input_truncated", "mode", "guard", "ask_rule")   # = lib/cc_dash/control.py
VERIFY_TIMEOUT = 2.0
TARGET_KEYS = ("file_path", "notebook_path", "path", "url", "pattern", "query", "skill",
               "description", "prompt", "command", "message")
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
CLOEXEC = getattr(os, "O_CLOEXEC", 0)


# ── paths and safe files ────────────────────────────────────────────────────
def state_dir():
    """Same rule as attention.py and lib/cc_dash: XDG_STATE_HOME when absolute."""
    base = os.environ.get("XDG_STATE_HOME") or ""
    if not os.path.isabs(base):
        home = os.path.expanduser("~")
        if not os.path.isabs(home):
            return ""
        base = os.path.join(home, ".local", "state")
    return os.path.join(base, "cc_tool")


def control_dir():
    sd = state_dir()
    return os.path.join(sd, "control") if sd else ""


def private_dir(d):
    """True when d is a real directory we own that nobody else can enter."""
    try:
        st = os.lstat(d)
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and not (st.st_mode & 0o077)


def read_private(path, limit=MAX_INPUT * 2):
    """Bytes of a plain 0600 file we own (not a symlink), else None."""
    try:
        fd = os.open(path, os.O_RDONLY | NOFOLLOW | CLOEXEC | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077 or st.st_size > limit:
            return None
        chunks, got = [], 0
        while got <= limit:
            b = os.read(fd, 65536)
            if not b:
                break
            chunks.append(b)
            got += len(b)
        return b"".join(chunks)
    except OSError:
        return None
    finally:
        os.close(fd)


def read_json(path, limit=MAX_INPUT * 2):
    raw = read_private(path, limit)
    if raw is None:
        return None
    try:
        d = json.loads(raw)
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def write_private(path, obj):
    """Write obj as JSON to path atomically: temp file (O_EXCL, 0600), then rename."""
    tmp = "%s.%d.%s.tmp" % (path, os.getpid(), os.urandom(4).hex())
    data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8", "replace")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW | CLOEXEC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


# ── the lease ───────────────────────────────────────────────────────────────
def pid_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def lease(ctl, now=None):
    """The lease dict when cc-dash --control is alive, else None."""
    if not ctl or not private_dir(ctl):
        return None
    d = read_json(os.path.join(ctl, "lease"), 4096)
    if d is None:
        return None
    exp = d.get("expires")
    if not isinstance(exp, (int, float)) or exp < (now or time.time()):
        return None
    return d if pid_alive(d.get("pid")) and is_cc_dash(d.get("pid")) else None


def is_cc_dash(pid):
    """True when the lease's process is cc-dash: a Python interpreter running a
    `bin/cc-dash` that sits in a cc_tool checkout (lib/cc_dash/server.py beside
    it), or `python -m cc_dash.server`. A lease pointing at any other process is
    not trusted; an argument merely mentioning cc-dash does not count.
    Where /proc is missing the check can't run and the dashboard's own answer
    (verify, keyed and socket-owner checked) carries the trust.
    CC_DASH_LEASE_ANY_PROGRAM=1 is for the hook's tests, whose fake dashboard is
    the test process; hooks inherit Claude Code's environment, not an agent's."""
    if os.environ.get("CC_DASH_LEASE_ANY_PROGRAM") == "1":
        return True
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            argv = [a.decode("utf-8", "replace") for a in f.read(8192).split(b"\0") if a]
    except FileNotFoundError:
        return not os.path.isdir("/proc/self")
    except (OSError, TypeError):
        return False
    if len(argv) >= 3 and argv[1] == "-m" and argv[2] == "cc_dash.server":
        return os.path.basename(argv[0]).startswith("python")
    if len(argv) < 2 or not os.path.basename(argv[0]).startswith("python"):
        return False
    script = argv[1]
    if os.path.basename(script) != "cc-dash":
        return False
    try:                                   # relative to the dashboard's own working directory
        script = os.path.realpath(os.path.join(os.readlink("/proc/%d/cwd" % pid), script))
    except OSError:
        return False
    # a real cc_tool checkout: bin/cc-dash with lib/cc_dash/server.py beside it
    return os.path.isfile(os.path.join(os.path.dirname(os.path.dirname(script)), "lib", "cc_dash", "server.py"))


# ── asking the dashboard before acting on a file ─────────────────────────────
def shown_digest(req):
    """sha256 of what the dashboard card shows (lib/cc_dash/control.py computes the same)."""
    canon = json.dumps({k: req.get(k) for k in SHOWN_KEYS}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8", "replace")).hexdigest()


def _tcp_inode(local_port, remote_port, tables=("/proc/net/tcp",)):
    """Inode of the 127.0.0.1:local_port <-> 127.0.0.1:remote_port socket, or None."""
    want_l, want_r = "0100007F:%04X" % local_port, "0100007F:%04X" % remote_port
    for table in tables:
        try:
            with open(table, encoding="ascii", errors="replace") as fh:
                next(fh, None)
                for line in fh:
                    cols = line.split()
                    if len(cols) >= 10 and cols[1].upper() == want_l and cols[2].upper() == want_r:
                        return cols[9]
        except OSError:
            continue
    return None


def _pid_owns_socket(pid, inode):
    target = "socket:[%s]" % inode
    d = "/proc/%d/fd" % pid
    try:
        names = os.listdir(d)
    except OSError:
        return False
    for n in names:
        try:
            if os.readlink(os.path.join(d, n)) == target:
                return True
        except OSError:
            continue
    return False


def answered_by(pid, port, my_port):
    """True when the server end of our connection (127.0.0.1:port <-> our port)
    belongs to pid. Linux: /proc. Elsewhere: lsof's listener on the port."""
    if os.path.exists("/proc/net/tcp"):
        inode = _tcp_inode(port, my_port)
        return bool(inode) and inode != "0" and _pid_owns_socket(pid, inode)
    import subprocess
    try:
        out = subprocess.run(["lsof", "-nP", "-a", "-iTCP:%d" % port, "-sTCP:LISTEN", "-t"], capture_output=True,
                             text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return str(pid) in out.split()


def verify(ls, **query):
    """cc-dash's own record for a file we are about to act on, or None.

    Asks GET /api/control/verify on the lease's port, and believes the answer
    only when the socket that sent it belongs to the lease's pid."""
    import http.client
    from urllib.parse import urlencode
    port, pid, key = ls.get("port"), ls.get("pid"), ls.get("key")
    if not isinstance(port, int) or not 0 < port < 65536 or not isinstance(pid, int) or not isinstance(key, str) or not key:
        return None
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=VERIFY_TIMEOUT)
    try:
        c.request("GET", "/api/control/verify?" + urlencode({k: v for k, v in query.items() if v is not None}),
                  headers={"Host": "127.0.0.1:%d" % port, "X-CC-Dash-Lease": key})
        r = c.getresponse()
        body = r.read(65536)
        # Checked while the connection is still open, so the server's end is in its fd table.
        if r.status != 200 or not answered_by(pid, port, c.sock.getsockname()[1]):
            return None
        d = json.loads(body)
    except (OSError, ValueError, http.client.HTTPException, AttributeError):
        return None
    finally:
        c.close()
    return d if isinstance(d, dict) and d.get("ok") is True else None


# ── what a call is (kept in step with attention.py and lib/cc_dash/control.py) ─
def clip(s, n=CLIP):
    s = s if isinstance(s, str) else ("" if s is None else str(s))
    s = " ".join(s.split()) if len(s) < 4 * n else " ".join(s[: 4 * n].split())
    return s if len(s) <= n else s[: n - 1] + "…"


def target(tool, ti):
    """attention.py's target(): what the call acts on, the key that pairs a prompt with its run."""
    if not isinstance(ti, dict):
        return ""
    if tool == "Bash":
        return clip(ti.get("command"), 300)
    if tool == "AskUserQuestion":
        qs = ti.get("questions")
        if isinstance(qs, list) and qs and isinstance(qs[0], dict):
            return clip(qs[0].get("question"), 300)
        return ""
    if tool == "ExitPlanMode":
        return ""
    for k in TARGET_KEYS:
        v = ti.get(k)
        if isinstance(v, str) and v:
            return clip(v, 300)
    return ""


def call_hash(tool, ti):
    """sha256 of tool + canonical input: a decision applies only to the call it was made for."""
    canon = json.dumps(ti if isinstance(ti, dict) else {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256((str(tool) + "\0" + canon).encode("utf-8", "replace")).hexdigest()


def guard_key(session, command):
    """Name of bash-guard's "asked" marker (cc_hooklib.guard_ask_key computes the same)."""
    return hashlib.sha256(("%s\0Bash\0%s" % (session, command)).encode("utf-8", "replace")).hexdigest()[:40]


def display_input(ti):
    """The tool input for the dashboard card, capped at MAX_INPUT bytes."""
    try:
        raw = json.dumps(ti, ensure_ascii=False)
    except (TypeError, ValueError):
        return {}, True
    if len(raw.encode("utf-8", "replace")) <= MAX_INPUT:
        return ti, False
    out = {}
    for k, v in (ti or {}).items():
        out[k] = v[:4000] + "…" if isinstance(v, str) and len(v) > 4000 else v
    return out, True


# ── why it asked: bash-guard's marker, or a permissions.ask rule ───────────
def guard_reason(ctl, d):
    ti = d.get("tool_input") if isinstance(d.get("tool_input"), dict) else {}
    cmd = ti.get("command")
    if d.get("tool_name") != "Bash" or not isinstance(cmd, str):
        return ""
    # Not deleted on read: a parallel identical call must be flagged too. Markers
    # expire by age (GUARD_MARK_TTL here; cc-dash prunes them after 5 minutes).
    p = os.path.join(ctl, "asked", guard_key(d.get("session_id") or "", cmd) + ".json")
    m = read_json(p, 16384)
    if m is None:
        return ""
    ts = m.get("ts")
    if not isinstance(ts, (int, float)) or time.time() - ts > GUARD_MARK_TTL:
        return ""
    return clip(m.get("reason") or "bash-guard asked about this command", 1200)


def _settings_files(cwd):
    """(file, anchor) for every settings file Claude Code reads permissions from:
    user, project, local, managed. `anchor` is where that file's `/path` rules
    point (None: not documented for that source, so such a rule is not checked)."""
    import glob
    cfg = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    proj = os.environ.get("CLAUDE_PROJECT_DIR") or cwd or ""
    out = [(os.path.join(cfg, "settings.json"), cfg), (os.path.join(cfg, "settings.local.json"), cfg),
           ("/etc/claude-code/managed-settings.json", None),
           ("/Library/Application Support/ClaudeCode/managed-settings.json", None)]
    out += [(f, None) for f in sorted(glob.glob("/etc/claude-code/managed-settings.d/*.json"))]
    if proj:
        out += [(os.path.join(proj, ".claude", "settings.json"), proj),
                (os.path.join(proj, ".claude", "settings.local.json"), proj)]
    return out


def ask_rules(cwd):
    """([(rule, anchor)], problem): every permissions.ask entry with its file's
    `/path` anchor, and why that list may be incomplete."""
    rules, problem = [], ""
    for f, anchor in _settings_files(cwd):
        if not os.path.exists(f):
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                s = json.load(fh)
            ask = (s.get("permissions") or {}).get("ask") if isinstance(s, dict) else None
            rules += [(r, anchor) for r in ask or [] if isinstance(r, str)]
        except (OSError, ValueError, AttributeError):
            problem = "could not read %s" % f
    return rules, problem


def _glob_rx(pattern):
    return re.compile("^" + ".*".join(re.escape(x) for x in pattern.split("*")) + "$", re.S)


_BG = []


def _bash_guard():
    """bash-guard.py loaded as a module, for its shell parser (cached; None when missing)."""
    if not _BG:
        mod = None
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bash-guard.py")
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("cc_bash_guard_parser", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception:  # noqa: BLE001
            mod = None
        _BG.append(mod)
    return _BG[0]


def bash_commands(cmd, depth=0):
    """(simple commands as normalised strings, problem) using bash-guard's own
    parser: env prefixes, wrappers (env, sudo, timeout, nice ...), subshells,
    operators, newlines, `sh -c` code and xargs/find handoffs all become their
    own simple commands. A non-empty problem means the list may be incomplete."""
    bg = _bash_guard()
    if bg is None:
        return [], "bash-guard.py is not next to this hook, so the command could not be parsed"
    if depth > 4:
        return [], "commands nested too deep to check"
    try:
        tokens = bg.tokenize(bg.shell_prepass(cmd))
    except Exception:  # noqa: BLE001
        tokens = None
    if tokens is None:
        return [], "the command could not be parsed"
    out, problem = [], ""
    try:
        for _rng, words in bg._simple_commands_at(tokens):
            name = bg._cmd_name(words[0])
            if name in ("eval", "source", ".") or "$" in words[0] or "`" in words[0]:
                problem = problem or "part of it is built at run time (%s)" % words[0]
            out.append(" ".join(words))
            if name in bg._SHELLS:
                code = bg._shell_code(words)
                if code is not None:
                    more, p2 = bash_commands(code, depth + 1)
                    out += more
                    problem = problem or p2
            for handed in bg._handed_commands(words):
                inner = bg._strip_assignments(bg._strip_wrappers(bg._strip_assignments(handed))[0])
                if inner:
                    out.append(" ".join(inner))
    except Exception:  # noqa: BLE001
        return out, "the command could not be parsed"
    return out, problem


def _rule_matches(body, text):
    body = " ".join(body.split())
    if body.endswith(":*"):
        pre = body[:-2].rstrip()
        return text == pre or text.startswith(pre + " ")
    return bool(_glob_rx(body).match(text))


# Which tools a rule's tool name covers (https://code.claude.com/docs/en/permissions.md):
# Edit rules apply to every built-in tool that edits files, Read rules (best effort)
# to the ones that read; Agent and Task are one tool.
_RULE_TOOLS = {"Read": ("Read", "Grep", "Glob"), "Edit": ("Edit", "Write", "MultiEdit", "NotebookEdit"),
               "Agent": ("Agent", "Task"), "Task": ("Agent", "Task")}
# Path rules written for these are accepted by Claude Code but never consulted.
_UNCONSULTED_PATH_TOOLS = {"Write", "NotebookEdit", "MultiEdit", "Glob"}
# A tool's primary field cannot be matched as `Tool(param:value)` (Claude Code ignores such a rule).
_PRIMARY = {"Bash": "command", "PowerShell": "command", "Read": "file_path", "Edit": "file_path", "Write": "file_path",
            "MultiEdit": "file_path", "Grep": "path", "Glob": "path", "NotebookEdit": "notebook_path", "WebFetch": "url"}
_BASH_PARAMS = {"run_in_background", "timeout", "description", "dangerouslyDisableSandbox"}
_PARAM_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", re.S)


class NotChecked(Exception):
    """A rule names this tool but its pattern form is not understood: fail closed."""


def _rule_tool_matches(rule_tool, tool, has_body):
    """True when a rule's tool name covers this tool."""
    if rule_tool.startswith("mcp__"):
        if has_body:
            return False                   # Claude Code skips an mcp__ rule with parentheses
        if "*" in rule_tool:
            import fnmatch
            return fnmatch.fnmatchcase(tool, rule_tool)
        # `mcp__server` and `mcp__server__*` both cover the whole server; as an ask rule
        # `mcp__reports` also covers a server named `reports__v2`.
        return tool == rule_tool or tool.startswith(rule_tool + "__")
    if "*" in rule_tool or "?" in rule_tool or "[" in rule_tool:
        import fnmatch
        return fnmatch.fnmatchcase(tool, rule_tool)     # tool-name globs: the full name must match
    if rule_tool in _UNCONSULTED_PATH_TOOLS and has_body:
        return False
    return tool in _RULE_TOOLS.get(rule_tool, (rule_tool,))


def _value_matches(pattern, value):
    """`Tool(param:value)`: the literal input value, `*` matching any text."""
    if isinstance(value, bool):
        value = "true" if value else "false"
    elif isinstance(value, (int, float)):
        value = repr(value)
    elif not isinstance(value, str):
        return False                       # nested objects and arrays are not matchable
    return bool(_glob_rx(pattern.strip()).match(value))


def _gitignore_rx(pat):
    """Regex for a gitignore pattern relative to its anchor: `*` within a segment,
    `**` across, `?`, [classes], backslash escapes. Raises NotChecked on what it
    cannot translate."""
    out, i, n = [], 0, len(pat)
    while i < n:
        c = pat[i]
        if pat.startswith("**/", i) and (i == 0 or pat[i - 1] == "/"):
            out.append("(?:.*/)?")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            j = pat.find("]", i + 2)
            if j < 0:
                raise NotChecked("an unclosed [ in the path pattern")
            body = pat[i + 1:j]
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append("[" + body.replace("\\", "\\\\") + "]")
            i = j + 1
        elif c == "\\" and i + 1 < n:
            out.append(re.escape(pat[i + 1]))
            i += 2
        else:
            out.append(re.escape(c))
            i += 1
    return "".join(out)


def _path_rule(body, anchor_src, cwd, home):
    """(anchor directory, compiled regex for the path below it), per the four
    documented forms: //abs, ~/home, /settings-source, path or ./path."""
    p = body.strip()
    if p.startswith("!"):
        return None                        # a carve-out only narrows other rules: ignoring it fails closed
    if p.startswith("//"):
        anchor, pat, relative = "/", p[2:], False
    elif p.startswith("~/"):
        if not home:
            raise NotChecked("no home directory to resolve ~/")
        anchor, pat, relative = home, p[2:], False
    elif p.startswith("~"):
        raise NotChecked("the ~user form is not documented")
    elif p.startswith("/"):
        if not anchor_src:
            raise NotChecked("a /path rule from a settings file whose anchor is not documented")
        anchor, pat, relative = anchor_src, p[1:], False
    else:
        if not cwd:
            raise NotChecked("no working directory to resolve a relative path rule")
        anchor, pat, relative = cwd, p[2:] if p.startswith("./") else p, True
    pat = pat.rstrip("/")
    if not pat or pat in ("**", "*"):
        return anchor, re.compile(r"^.*$", re.S)
    core = _gitignore_rx(pat)
    if "/" not in pat:
        rx = "(?:.*/)?" + core             # a bare name matches at any depth
    elif relative and re.match(r"^[^/*?\[\\]+/\*\*$", pat):
        # `dir/**` (also written ./dir/**) in an ask rule: that directory name at any depth
        rx = "(?:.*/)?" + core
    else:
        rx = core
    return anchor, re.compile("^" + rx + "(?:/.*)?$", re.S)


def _under(path, anchor):
    anchor = anchor.rstrip("/") or "/"
    if anchor == "/":
        return path.lstrip("/")
    if path == anchor:
        return ""
    if path.startswith(anchor + "/"):
        return path[len(anchor) + 1:]
    return None


def _literal_prefix(rx_body):
    """The leading path segments of a pattern that contain no glob."""
    segs = []
    for seg in rx_body.split("/"):
        if any(ch in seg for ch in "*?[\\"):
            break
        segs.append(seg)
    return "/".join(segs)


def _path_matches(body, anchor_src, ti, tool, cwd):
    """True / False for a Read or Edit path rule against this call's path(s)."""
    home = os.path.expanduser("~")
    home = home if os.path.isabs(home) else ""
    parsed = _path_rule(body, anchor_src, cwd, home)
    if parsed is None:
        return False
    anchor, rx = parsed
    key = {"NotebookEdit": "notebook_path", "Grep": "path", "Glob": "path"}.get(tool, "file_path")
    raw = ti.get(key)
    if raw is None and tool in ("Grep", "Glob"):
        raw = cwd                          # no path: the search runs in the working directory
    if not isinstance(raw, str) or not raw:
        raise NotChecked("the call names no %s" % key)
    if raw.startswith("~/") and home:
        raw = os.path.join(home, raw[2:])
    full = os.path.normpath(raw if os.path.isabs(raw) else os.path.join(cwd or "/", raw))
    spellings = {full, os.path.realpath(full)}
    for anc in {anchor, os.path.realpath(anchor)}:
        for path in spellings:
            rel = _under(path, anc)
            if rel is not None and rx.match(rel):
                return True
    if tool in ("Grep", "Glob"):
        # A search over a directory reaches every file below it: if the rule could
        # match anything down there, do not call the search clean.
        p = body.strip()
        relative = not p.startswith(("/", "~"))
        pat = (p[2:] if p.startswith(("//", "~/", "./")) else p[1:] if p.startswith("/") else p).rstrip("/")
        any_depth = "/" not in pat or (relative and re.match(r"^[^/*?\[\\]+/\*\*$", pat))
        pre = _literal_prefix(pat)
        for path in spellings:
            if _under(anchor, path) is not None:
                raise NotChecked("the search covers %s, where this path rule applies" % anchor)
            rel = _under(path, anchor)
            if rel is not None and (any_depth or not pre or pre.startswith(rel + "/") or rel.startswith(pre)):
                raise NotChecked("the search may reach files this path rule covers")
    return False


def _domain_matches(spec, url):
    from urllib.parse import urlsplit
    try:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        host = ""
    if not host:
        raise NotChecked("the URL has no host to compare")
    spec = spec.strip().lower().rstrip(".")
    if spec == "*":
        return True
    if spec.startswith("*."):
        return host.endswith(spec[1:]) and host != spec[2:]
    want, have = spec.split("."), host.split(".")
    if len(want) != len(have):
        return False
    return all(re.match("^" + "[^.]*".join(re.escape(x) for x in w.split("*")) + "$", h) for w, h in zip(want, have))


def _body_matches(rule_tool, body, anchor_src, tool, ti, cwd):
    """True / False for one rule body against this call; NotChecked when the form
    is not understood. Bash is matched separately (bash-guard's parser)."""
    m = _PARAM_RE.match(body)
    if m and m.group(1) not in ti and m.group(2).startswith("//"):
        m = None                           # a URL such as https://…, not `param:value`
    if m and not (rule_tool == "WebFetch" and m.group(1) == "domain"):
        name, want = m.group(1), m.group(2)
        is_path_tool = rule_tool in ("Read", "Edit")
        if name in ti or not is_path_tool:
            if _PRIMARY.get(tool) == name or _PRIMARY.get(rule_tool) == name:
                return False               # ignored by Claude Code (bypassable), never consulted
            if name not in ti:
                return False               # a parameter the call leaves out is never matched
            return _value_matches(want, ti[name])
    if rule_tool in ("Read", "Edit"):
        return _path_matches(body, anchor_src, ti, tool, cwd)
    if rule_tool == "WebFetch":
        if not body.strip().lower().startswith("domain:"):
            raise NotChecked("a WebFetch rule without domain:")
        url = ti.get("url")
        if not isinstance(url, str):
            raise NotChecked("the call names no url")
        return _domain_matches(body.strip()[7:], url)
    if rule_tool in ("Agent", "Task"):
        st = ti.get("subagent_type")
        return isinstance(st, str) and bool(_glob_rx(body.strip()).match(st))
    if rule_tool == "Skill":
        sk = ti.get("skill")
        b = body.strip()
        if b.endswith(":*"):
            return isinstance(sk, str) and (sk == b[:-2] or sk.startswith(b[:-2] + ":"))
        return isinstance(sk, str) and bool(_glob_rx(b).match(sk))
    raise NotChecked("the %s(…) pattern form is not one cc-dash can check" % rule_tool)


def ask_rule(d):
    """The permissions.ask rule this call matches, or a reason the check failed
    (fail closed: the dashboard then asks for a typed confirm), or ''."""
    tool = d.get("tool_name") or ""
    ti = d.get("tool_input") if isinstance(d.get("tool_input"), dict) else {}
    cwd = d.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or ""
    rules, problem = ask_rules(d.get("cwd") or "")
    if problem:
        return "(not checked: %s)" % problem
    mine = []
    for r, anchor in rules:
        m = re.match(r"^([^()\s]+)(?:\((.*)\))?$", r.strip(), re.S)
        if not m:
            mine_problem = "the rule %s could not be parsed" % r
            if tool and tool in r:
                return "(not checked: %s)" % mine_problem
            continue
        rule_tool, body = m.group(1), m.group(2)
        has_body = body is not None and body.strip() not in ("", "*")
        if _rule_tool_matches(rule_tool, tool, has_body):
            mine.append((r, rule_tool, body, anchor))
    if not mine:
        return ""
    for r, _rt, body, _a in mine:
        if body is None or body.strip() in ("", "*"):
            return r
    if tool == "Bash":
        cmd = ti.get("command") if isinstance(ti.get("command"), str) else ""
        cmds, problem = bash_commands(cmd)
        texts = [" ".join(cmd.split())] + cmds
        for r, _rt, body, _a in mine:
            pm = _PARAM_RE.match(body)
            if pm and pm.group(1) in _BASH_PARAMS:
                if pm.group(1) in ti and _value_matches(pm.group(2), ti[pm.group(1)]):
                    return r
                continue
            if pm and pm.group(1) == "command":
                continue                   # ignored by Claude Code
            if any(_rule_matches(body, t) for t in texts):
                return r
        if problem:
            return "(not checked against your permissions.ask rules: %s)" % problem
        return ""
    unsure = ""
    for r, rule_tool, body, anchor in mine:
        try:
            if _body_matches(rule_tool, body, anchor, tool, ti, cwd):
                return r
        except NotChecked as e:
            unsure = unsure or "%s: %s" % (r, e)
        except (re.error, ValueError, TypeError) as e:
            unsure = unsure or "%s: %s" % (r, type(e).__name__)
    return "(not checked: %s)" % unsure if unsure else ""


def session_rules(d):
    """Claude Code's own addRules/allow suggestions, re-scoped to this session only."""
    out = []
    for s in d.get("permission_suggestions") or []:
        if isinstance(s, dict) and s.get("type") == "addRules" and s.get("behavior") == "allow" and isinstance(s.get("rules"), list):
            rules = [r for r in s["rules"] if isinstance(r, dict) and isinstance(r.get("toolName"), str)]
            if rules:
                out.append({"type": "addRules", "rules": rules, "behavior": "allow", "destination": "session"})
    return out


def rule_text(entries):
    return [r["toolName"] + ("(%s)" % r["ruleContent"] if r.get("ruleContent") else "") for e in entries for r in e["rules"]]


# ── was the prompt answered in the terminal? ────────────────────────────────
def parent_pids(n=6):
    """This process's ancestors (hook -> sh -> claude ...), from /proc; [] elsewhere."""
    out, pid = [], os.getppid()
    for _ in range(n):
        if pid <= 1:
            break
        out.append(pid)
        try:
            with open("/proc/%d/stat" % pid, encoding="ascii", errors="replace") as fh:
                pid = int(fh.read().rsplit(")", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            break
    return out


class Resolution:
    """Watches attention.py's log and the session registry for this prompt."""

    def __init__(self, d, started):
        self.sid = d.get("session_id") or ""
        self.agent = d.get("agent_id") or None
        self.tool = d.get("tool_name") or ""
        self.sig = target(self.tool, d.get("tool_input"))
        self.started = started
        self.log = os.path.join(state_dir(), "attention.jsonl")
        self.off = self._size()
        self.carry = b""
        cfg = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
        self.reg_dir = os.path.join(cfg, "sessions")
        self.reg_path = None
        self.reg_scan = 0.0
        self.armed = False
        for pid in parent_pids():                  # fast path: the claude process that runs us
            p = os.path.join(self.reg_dir, "%d.json" % pid)
            r = self._reg_read(p)
            if r is not None and r.get("sessionId") == self.sid:
                self.reg_path = p
                break

    def _size(self):
        try:
            st = os.lstat(self.log)
            return st.st_size if stat.S_ISREG(st.st_mode) else 0
        except OSError:
            return 0

    def _match(self, r):
        if r.get("session") != self.sid:
            return False
        ev = r.get("event")
        ag = r.get("agent") or None
        ts = r.get("ts")
        if not isinstance(ts, (int, float)) or ts < self.started - 0.5:
            return False
        if ev in ("PostToolUse", "PostToolUseFailure"):
            return ag == self.agent and r.get("tool") == self.tool and (r.get("sig") or "") == self.sig
        if ev == "PostToolBatch":
            return ag == self.agent
        if ev in ("UserPromptSubmit", "Stop"):
            return self.agent is None
        if ev == "SubagentStop":
            return ag is not None and ag == self.agent
        return ev == "SessionEnd"

    def events(self):
        size = self._size()
        if size < self.off:
            self.off, self.carry = 0, b""      # rotated
        if size == self.off:
            return False
        raw = read_tail(self.log, self.off, size)
        if raw is None:
            return False
        self.off = size
        buf = self.carry + raw
        cut = buf.rfind(b"\n")
        self.carry = buf[cut + 1:] if cut >= 0 else buf
        for line in (buf[:cut].split(b"\n") if cut >= 0 else []):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict) and self._match(r):
                return True
        return False

    def registry(self, now):
        if self.reg_path is None:
            if now - self.reg_scan < 1.0:
                return False
            self.reg_scan = now
            try:
                names = os.listdir(self.reg_dir)
            except OSError:
                return False
            for n in names:
                if n.endswith(".json") and n[:-5].isdigit():
                    d = self._reg_read(os.path.join(self.reg_dir, n))
                    if d is not None and d.get("sessionId") == self.sid:
                        self.reg_path = os.path.join(self.reg_dir, n)
                        break
            if self.reg_path is None:
                return False
        d = self._reg_read(self.reg_path)
        if d is None or d.get("sessionId") != self.sid:
            self.reg_path = None
            return False
        status = d.get("status") if isinstance(d.get("status"), str) else ""
        upd = d.get("statusUpdatedAt") or d.get("updatedAt") or 0
        upd = upd / 1000.0 if isinstance(upd, (int, float)) else 0.0
        if status == "waiting" and upd >= self.started - 2:
            self.armed = True
            return False
        return bool(self.armed and status and status != "waiting" and upd > self.started)

    @staticmethod
    def _reg_read(path):
        try:
            st = os.lstat(path)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_size > 65536:
                return None
            with open(path, encoding="utf-8") as fh:
                d = json.load(fh)
            return d if isinstance(d, dict) else None
        except (OSError, ValueError):
            return None

    def answered(self, now):
        return self.events() or self.registry(now)


def read_tail(path, off, size):
    """Bytes off..size of attention.jsonl (a file we own that others cannot write)."""
    try:
        fd = os.open(path, os.O_RDONLY | NOFOLLOW | CLOEXEC | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o022:
            return None
        os.lseek(fd, off, 0)
        want = min(size - off, 4 * 1024 * 1024)
        out = b""
        while len(out) < want:
            b = os.read(fd, want - len(out))
            if not b:
                break
            out += b
        return out
    except OSError:
        return None
    finally:
        os.close(fd)


# ── PermissionRequest ───────────────────────────────────────────────────────
def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.flush()


def decision_output(d, dec, req):
    """The hook output for a dashboard decision, or None when it is not valid for this request."""
    if dec.get("id") != req["id"] or dec.get("hash") != req["hash"]:
        return None
    action = dec.get("action")
    ti = d.get("tool_input") if isinstance(d.get("tool_input"), dict) else {}
    kind = req["kind"]
    out = lambda decision: {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}  # noqa: E731
    if action == "deny":
        msg = dec.get("message") if isinstance(dec.get("message"), str) else ""
        msg = msg.strip()[:4000]
        if kind == "plan":
            text = "The user rejected the plan from the cc-dash dashboard." + (" Feedback: " + msg if msg else "")
        elif kind == "question":
            text = "The user declined to answer from the cc-dash dashboard." + (" Note: " + msg if msg else "")
        else:
            text = "Denied by the user from the cc-dash dashboard." + (" Reason: " + msg if msg else "")
        return out({"behavior": "deny", "message": text})
    if action == "allow" and kind == "permission":
        return out({"behavior": "allow"})
    if action == "allow" and kind == "plan":
        return out({"behavior": "allow", "updatedInput": ti})      # a bare allow leaves the plan dialog up
    if action == "allow_session" and kind == "permission" and not req["guard"] and not req["ask_rule"]:
        rules = session_rules(d)
        return out({"behavior": "allow", "updatedPermissions": rules}) if rules else None
    if action == "answer" and kind == "question":
        answers = dec.get("answers")
        qs = ti.get("questions") if isinstance(ti.get("questions"), list) else []
        texts = {q.get("question") for q in qs if isinstance(q, dict) and isinstance(q.get("question"), str)}
        if not isinstance(answers, dict) or not answers:
            return None
        clean = {k: v.strip()[:2000] for k, v in answers.items() if k in texts and isinstance(v, str) and v.strip()}
        if not clean:
            return None
        upd = dict(ti)
        upd["answers"] = clean
        return out({"behavior": "allow", "updatedInput": upd})
    return None


def permission_request(d, ctl, ls):
    sid = d.get("session_id")
    if not isinstance(sid, str) or not sid:
        return
    for sub in ("req", "dec", "res"):
        if not private_dir(os.path.join(ctl, sub)):
            return
    started = time.time()
    tool = d.get("tool_name") or ""
    ti = d.get("tool_input") if isinstance(d.get("tool_input"), dict) else {}
    kind = QUESTION_TOOLS.get(tool, "permission")
    wait = ls.get("wait") if isinstance(ls.get("wait"), (int, float)) else DEFAULT_WAIT
    wait = max(5.0, min(float(wait), MAX_WAIT))
    rid = "%x-%s" % (int(started * 1000), os.urandom(6).hex())
    shown, truncated = display_input(ti)
    guard = guard_reason(ctl, d) if private_dir(os.path.join(ctl, "asked")) else ""
    rule = ask_rule(d) if kind == "permission" else ""
    req = {
        "v": 1, "id": rid, "ts": round(started, 3), "pid": os.getpid(), "session": sid[:100],
        "agent_id": d.get("agent_id") or None, "agent_type": d.get("agent_type") or None,
        "cwd": d.get("cwd") or "", "transcript": d.get("transcript_path") or "",
        "tool": tool, "kind": kind, "sig": target(tool, ti), "hash": call_hash(tool, ti),
        "input": shown, "input_truncated": truncated, "mode": d.get("permission_mode") or "",
        "deadline": round(started + wait, 3), "guard": guard, "ask_rule": rule,
        "session_rules": [] if guard or rule or kind != "permission" else rule_text(session_rules(d)),
    }
    if isinstance(d.get("mcp_server"), dict):
        req["server"] = clip(d["mcp_server"].get("name"), 80)
    req_path = os.path.join(ctl, "req", rid + ".json")
    dec_path = os.path.join(ctl, "dec", rid + ".json")
    import signal
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, lambda *_: sys.exit(0))   # killed (terminal "No", or the timeout): still clean up
    write_private(req_path, req)
    res = {"v": 1, "id": rid, "session": req["session"], "agent_id": req["agent_id"], "outcome": "terminal"}
    watch = Resolution(d, started)
    try:
        last_lease = started
        while True:
            now = time.time()
            dec = read_json(dec_path, MAX_MSG_FILE)
            if dec is not None:
                unlink(dec_path)
                # The file only rings the bell: what applies is cc-dash's own record of
                # a decision sent from a control-unlocked page, for this request as shown.
                dec = verify(ls, kind="dec", id=rid, hash=req["hash"], shown=shown_digest(req)) \
                    if dec.get("id") == rid and dec.get("hash") == req["hash"] else None
                if dec is not None and dec.get("id") == rid and dec.get("hash") == req["hash"] and dec.get("action") == "release":
                    res["outcome"] = "terminal" if dec.get("why") == "terminal" else "released"
                    return
                o = decision_output(d, dec, req) if dec is not None else None
                if o is not None:
                    emit(o)
                    res.update(outcome="applied", action=dec.get("action"))
                    return
                res["rejected_decisions"] = res.get("rejected_decisions", 0) + 1
            if watch.answered(now):
                res["outcome"] = "terminal"
                return
            if now - last_lease >= 1.0:
                last_lease = now
                if lease(ctl, now) is None:
                    res["outcome"] = "lease"
                    return
            if now >= started + wait:
                res["outcome"] = "timeout"
                return
            time.sleep(POLL)
    finally:
        unlink(req_path)
        unlink(dec_path)
        res["ts"] = round(time.time(), 3)
        res["waited"] = round(res["ts"] - started, 3)
        try:
            write_private(os.path.join(ctl, "res", rid + ".json"), res)
        except OSError:
            pass


# ── stop at the next tool call ──────────────────────────────────────────────
def target_ok(d):
    sid = d.get("session_id")
    agent = d.get("agent_id") or None
    if not isinstance(sid, str) or not SAFE_ID.match(sid) or (agent is not None and not SAFE_ID.match(str(agent))):
        return None
    return sid, agent


def claim(ctl, src, dst, name, sid, agent):
    """Rename src/name into dst/ (atomic: one claimer wins) and return its dict."""
    to = os.path.join(ctl, dst, name)
    try:
        os.rename(os.path.join(ctl, src, name), to)
    except OSError:
        return None, to
    m = read_json(to, MAX_MSG_FILE)
    if m is None or m.get("session") != sid or (m.get("agent_id") or None) != agent:
        unlink(to)
        return None, to
    return m, to


def stop_turn(d, ctl, ev, ls):
    t = target_ok(d)
    if t is None or not (private_dir(os.path.join(ctl, "stop")) and private_dir(os.path.join(ctl, "stopped"))):
        return False
    sid, agent = t
    name = "%s.%s.json" % (sid, agent or "main")
    if not os.path.lexists(os.path.join(ctl, "stop", name)):
        return False
    m, to = claim(ctl, "stop", "stopped", "%s" % name, sid, agent)
    if m is None:
        return False
    if verify(ls, kind="stop", id=name[:-5], session=sid) is None:
        unlink(to)                  # not sent from the dashboard: dropped, the turn goes on
        return False
    now = time.time()
    if ev == "PreToolUse":
        m.update(applied_ts=round(now, 3), outcome="stopped", tool=d.get("tool_name") or None)
        emit({"continue": False, "stopReason": "Stopped from the cc-dash dashboard.",
              "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                     "permissionDecisionReason": "The user stopped this turn from the cc-dash dashboard."}})
    else:
        m.update(applied_ts=round(now, 3), outcome="turn ended")     # the turn ended before another tool call
    try:
        write_private(to, m)
    except OSError:
        pass
    return ev == "PreToolUse"


# ── message delivery ────────────────────────────────────────────────────────
def take_messages(ctl, sid, agent, ls):
    """Claim every message queued for this session and agent that cc-dash confirms
    it sent; [(path, msg)] with cc-dash's text, never the file's."""
    msg_dir = os.path.join(ctl, "msg")
    if not (private_dir(msg_dir) and private_dir(os.path.join(ctl, "dlv"))):
        return []
    prefix = "%s.%s." % (sid, agent or "main")
    try:
        names = sorted(n for n in os.listdir(msg_dir) if n.startswith(prefix) and n.endswith(".json"))
    except OSError:
        return []
    got = []
    for n in names:
        m, to = claim(ctl, "msg", "dlv", n, sid, agent)
        if m is None:
            continue
        ok = verify(ls, kind="msg", id=m.get("id") if isinstance(m.get("id"), str) else "", session=sid, agent=agent or "")
        if ok is None or not isinstance(ok.get("text"), str) or ok.get("session") != sid or (ok.get("agent_id") or None) != agent:
            unlink(to)              # not sent from the dashboard: dropped unread
            continue
        m["text"] = ok["text"]
        got.append((to, m))
    got.sort(key=lambda x: (x[1].get("ts") or 0, x[0]))     # oldest first
    return got


def deliver(d, ctl, ev, ls):
    t = target_ok(d)
    if t is None:
        return
    sid, agent = t
    if (ev == "SubagentStop" and agent is None) or (ev == "Stop" and agent is not None):
        return
    got = take_messages(ctl, sid, agent, ls)
    if not got:
        return
    now = time.time()
    parts = []
    for path, m in got:
        when = time.strftime("%H:%M:%S", time.localtime(m.get("ts") or now))
        parts.append("Message from the user, sent from the cc-dash dashboard at %s:\n%s" % (when, m["text"].strip()))
        m.update(delivered_ts=round(now, 3), via=ev, tool=d.get("tool_name") or None)
        try:
            write_private(path, m)
        except OSError:
            pass
    # additionalContext everywhere: at a stop it continues the turn as "Stop hook
    # feedback" without the hook-error styling and blocking text of decision:block.
    emit({"hookSpecificOutput": {"hookEventName": ev, "additionalContext": "\n\n".join(parts)}})


def record_socket(d, ctl):
    sock = os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET") or ""
    t = target_ok(d)
    sdir = os.path.join(ctl, "sock")
    if not sock or t is None or d.get("agent_id") or not private_dir(sdir):
        return
    pids = parent_pids()
    write_private(os.path.join(sdir, t[0] + ".json"),
                  {"session": t[0], "socket": sock, "pid": pids[1] if len(pids) > 1 else (pids[0] if pids else None),
                   "ts": round(time.time(), 3)})


def main():
    ctl = control_dir()
    now = time.time()
    ls = lease(ctl, now)
    if ls is None:
        # No dashboard in control mode: drop a leftover flag so the shell guard
        # in settings.json stops starting python for every tool call.
        if ctl and private_dir(ctl):
            unlink(os.path.join(ctl, "pending"))
        return
    d = json.loads(sys.stdin.read() or "null")
    if not isinstance(d, dict):
        return
    ev = d.get("hook_event_name") or ""
    if ev == "PermissionRequest":
        permission_request(d, ctl, ls)
    elif ev == "PreToolUse":
        stop_turn(d, ctl, ev, ls)
    elif ev == "SessionStart":
        record_socket(d, ctl)
    elif ev in ("PostToolUse", "PostToolUseFailure", "UserPromptSubmit", "Stop", "SubagentStop"):
        if ev in ("Stop", "SubagentStop"):
            stop_turn(d, ctl, ev, ls)
        deliver(d, ctl, ev, ls)


if __name__ == "__main__":
    try:
        main()
    except BaseException:  # noqa: BLE001 - never affect the session, whatever happens
        pass
    sys.exit(0)
