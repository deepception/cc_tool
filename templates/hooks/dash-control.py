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
    return d if pid_alive(d.get("pid")) else None


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
    """Every settings file Claude Code reads permissions from: user, project, local, managed."""
    import glob
    cfg = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    proj = os.environ.get("CLAUDE_PROJECT_DIR") or cwd or ""
    out = [os.path.join(cfg, "settings.json"), os.path.join(cfg, "settings.local.json"),
           "/etc/claude-code/managed-settings.json", "/Library/Application Support/ClaudeCode/managed-settings.json"]
    out += sorted(glob.glob("/etc/claude-code/managed-settings.d/*.json"))
    if proj:
        out += [os.path.join(proj, ".claude", "settings.json"), os.path.join(proj, ".claude", "settings.local.json")]
    return out


def ask_rules(cwd):
    """(rules, problem): every permissions.ask entry, and why that list may be incomplete."""
    rules, problem = [], ""
    for f in _settings_files(cwd):
        if not os.path.exists(f):
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                s = json.load(fh)
            ask = (s.get("permissions") or {}).get("ask") if isinstance(s, dict) else None
            rules += [r for r in ask or [] if isinstance(r, str)]
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


def ask_rule(d):
    """The permissions.ask rule this call matches, or a reason the check failed
    (fail closed: the dashboard then asks for a typed confirm), or ''."""
    tool = d.get("tool_name") or ""
    ti = d.get("tool_input") if isinstance(d.get("tool_input"), dict) else {}
    rules, problem = ask_rules(d.get("cwd") or "")
    if problem:
        return "(not checked: %s)" % problem
    mine = []
    for r in rules:
        m = re.match(r"^([A-Za-z0-9_:.-]+)(?:\((.*)\))?$", r.strip(), re.S)
        if m and m.group(1) == tool:
            mine.append((r, m.group(2)))
    if not mine:
        return ""
    for r, body in mine:
        if body is None or body.strip() in ("", "*"):
            return r
    if tool == "Bash":
        cmd = ti.get("command") if isinstance(ti.get("command"), str) else ""
        cmds, problem = bash_commands(cmd)
        texts = [" ".join(cmd.split())] + cmds
        for r, body in mine:
            if any(_rule_matches(body, t) for t in texts):
                return r
        if problem:
            return "(not checked against your permissions.ask rules: %s)" % problem
        return ""
    val = next((ti.get(k) for k in ("file_path", "notebook_path", "path", "url") if isinstance(ti.get(k), str)), "")
    for r, body in mine:
        if body.startswith("domain:"):
            if re.match(r"^https?://([^/:]+\.)?" + re.escape(body[7:]) + r"(?:[:/]|$)", val):
                return r
        elif val and _glob_rx(body.replace("**", "*")).match(val):
            return r
    return ""


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
                if dec.get("id") == rid and dec.get("hash") == req["hash"] and dec.get("action") == "release":
                    res["outcome"] = "terminal" if dec.get("why") == "terminal" else "released"
                    return
                o = decision_output(d, dec, req)
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


def stop_turn(d, ctl, ev):
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
def take_messages(ctl, sid, agent):
    """Claim every message queued for this session and agent; [(path, msg)]."""
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
        if m is not None and isinstance(m.get("text"), str):
            got.append((to, m))
    got.sort(key=lambda x: (x[1].get("ts") or 0, x[0]))     # oldest first
    return got


def deliver(d, ctl, ev):
    t = target_ok(d)
    if t is None:
        return
    sid, agent = t
    if (ev == "SubagentStop" and agent is None) or (ev == "Stop" and agent is not None):
        return
    got = take_messages(ctl, sid, agent)
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
        stop_turn(d, ctl, ev)
    elif ev == "SessionStart":
        record_socket(d, ctl)
    elif ev in ("PostToolUse", "PostToolUseFailure", "UserPromptSubmit", "Stop", "SubagentStop"):
        if ev in ("Stop", "SubagentStop"):
            stop_turn(d, ctl, ev)
        deliver(d, ctl, ev)


if __name__ == "__main__":
    try:
        main()
    except BaseException:  # noqa: BLE001 - never affect the session, whatever happens
        pass
    sys.exit(0)
