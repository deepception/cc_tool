"""Incremental JSONL reading and the per-transcript accumulator.

A transcript is append-only while its session runs, so each file is read once
and afterwards only from the last consumed byte. The cache key is
(size, mtime_ns, inode); a file that shrank or was replaced is re-read from 0.
A trailing line without its newline is left for the next pass unless it is
already valid JSON. Malformed lines are counted and skipped.

One Claude Code API message is spread over several `assistant` records (one
per content block) that repeat the same `message.id` and `usage`, so usage is
counted once per message id.

"Tokens" everywhere means NEW tokens: input + cache writes + output. Cache
reads are re-read context, priced at a fraction of input; they are kept and
shown apart, never added into the headline.

Subagent transcripts mostly save only the usage from the start of a reply's
stream (no record with a stop_reason), so their output_tokens stay near 10
while the reply holds thousands. For a message with no final record the
output counted (and priced) is the larger of the recorded value and its
visible content at ~4 characters a token (text, tool input JSON, thinking
text). Hidden thinking cannot be seen, so this is a floor; msg_est keeps the
messages and tokens that are estimates. A final record, when one arrives,
replaces the estimate.

Effort and thinking (see tree.py for how they roll up):
  - `api-request` records (Claude Code 2.1.29x+) carry the request params:
    params.model, params.output_config.effort and params.thinking.
  - From 2.1.30x every assistant record also carries its own `effort` field,
    subagents included. A reply is credited with that field when present,
    else with the effort of the latest api-request record before it.
  - usage.output_tokens_details.thinking_tokens counts thinking inside the
    output tokens, on records written after the reply finished. Subagent
    transcripts mostly save usage at the start of the stream, without it.
  - thinking / redacted_thinking content blocks: usually a signature only
    (thinking display "updates" or "omitted"), sometimes with text.
  - usage.speed == "fast" marks fast mode.

Minute buckets (`Acc.mins`, the Factory floor's last-hour charts, graph.py):
epoch minute -> [tok_opus, tok_sonnet, tok_haiku, tok_fable, tok_other, tools,
errors, active], flat ints (every transcript keeps one).
  - tokens: a message's new tokens, in the minute of its first record (msg_ts,
    as the burn rate counts it) under its model's family; a later record that
    rewrites the usage takes the old amount out and puts the new one in, so a
    message counts once at its latest usage. No msg_ts, no minute.
  - tools: tool_use blocks at their record's minute; errors: those whose result
    was an error or a guard block, in the tool_use's minute.
  - active: 1 when the transcript wrote a timestamped assistant / user / system
    record in that minute, or a tool call was running across it (tool_use to
    tool_result, filled when the result arrives).
Only the SERIES_KEEP_MINS minutes up to the newest one seen are kept: older
ones are refused (never re-created after a prune, so a rewrite never takes from
the wrong minute) and pruned once the dict passes KEEP + SERIES_SLACK_MINS.
"""
import json
import os
import re
from datetime import datetime

from . import pricing

CHUNK = 8 * 1024 * 1024
MAX_EVENTS = 8000          # per transcript; past this the oldest timeline events are dropped
TEXT_PROMPT = 1200
TEXT_ASSIST = 600
TEXT_TARGET = 200
TEXT_ERROR = 400
ACTIVE_GAP = 1800          # a pause longer than this between records is idle time, not work
SERIES_KEEP_MINS = 64      # minute buckets kept before the newest (graph.py's hour: 60 + alignment + slack)
SERIES_SLACK_MINS = 16     # ...and pruned once the dict holds this many more (amortised)
SERIES_FAMILIES = ("opus", "sonnet", "haiku", "fable", "other")   # Acc.mins token columns, in order
_FAM_COL = {f: i for i, f in enumerate(SERIES_FAMILIES)}
M_TOOLS, M_ERRORS, M_ACTIVE = 5, 6, 7   # the other Acc.mins columns

# Record types that carry nothing the dashboard shows. Checked against the raw
# line prefix so json.loads never runs on them (some are large).
_SKIP_PREFIXES = tuple(b'{"type":"%s"' % t for t in (
    b"bridge-session", b"atis-latch", b"queue-operation", b"file-history-snapshot",
    b"file-history-delta", b"api-request-blob", b"api-request", b"api-request-shape",
    b"mode", b"permission-mode", b"last-prompt",
))

_REQUEST_PREFIX = b'{"type":"api-request",'   # small; the -blob and -shape kinds stay skipped
_SPAWN_TOOLS = {"Agent", "Task"}
_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
MAX_EDITED = 200           # distinct edited paths kept per transcript (routing.py needs "any" and a rough count)
# What a Bash command says about the work (routing.py's work-type heuristic): a check run, or a change made.
_TEST_RE = re.compile(r"\b(pytest|vitest|jest|npm (run )?test|pnpm (run )?test|yarn test|go test|cargo test|"
                      r"make test|playwright|unittest|tox|npx tsc|tsc --noEmit|ruff|eslint|mypy|flutter test|gradlew|maestro|bats|shellcheck|run_tests|test\.sh|tests?/)")
_MUTATE_RE = re.compile(r"(\bgit (commit|add|apply|checkout -b|merge|rebase|push|worktree add)|\bsed -i|\bmv |\brm |\bcp |"
                        r"> ?[\w./~-]+\.(py|ts|tsx|js|md|json|sh|yaml|yml)\b|\btee )")
_TAG_STRIP = re.compile(r"<(ide_opened_file|ide_selection|system-reminder|ide_diagnostics)>.*?</\1>", re.S)
_CMD_NAME = re.compile(r"<command-name>\s*(.*?)\s*</command-name>", re.S)
_CMD_ARGS = re.compile(r"<command-args>\s*(.*?)\s*</command-args>", re.S)
_TN_SUMMARY = re.compile(r"<summary>\s*(.*?)\s*</summary>", re.S)
_TN_STATUS = re.compile(r"<status>\s*(.*?)\s*</status>", re.S)
_WF_NAME = re.compile(r"""\bname\s*:\s*['"]([^'"]{1,80})['"]""")
HOME = os.path.expanduser("~")


def parse_ts(s):
    if not isinstance(s, str) or not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def clip(s, n):
    s = s if isinstance(s, str) else ("" if s is None else str(s))
    s = s.strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def tilde(p):
    if isinstance(p, str) and HOME and p.startswith(HOME + "/"):
        return "~" + p[len(HOME):]
    return p


def tool_target(name, ti):
    """A one-line summary of what a tool call acted on."""
    if not isinstance(ti, dict):
        return ""
    if name == "Bash":
        return clip(ti.get("command"), TEXT_TARGET)
    if name in _SPAWN_TOOLS:
        return clip(ti.get("description") or ti.get("prompt"), TEXT_TARGET)
    if name == "Workflow":
        return clip(workflow_name(ti), TEXT_TARGET)
    if name == "AskUserQuestion":  # the first question (alerts show it; the timeline too)
        qs = ti.get("questions")
        q = qs[0] if isinstance(qs, list) and qs and isinstance(qs[0], dict) else {}
        return clip(q.get("question"), TEXT_TARGET)
    for key in ("file_path", "notebook_path", "path", "pattern", "url", "query", "skill",
                "description", "prompt", "command", "message"):
        v = ti.get(key)
        if isinstance(v, str) and v:
            return clip(tilde(v), TEXT_TARGET)
    for v in ti.values():
        if isinstance(v, str) and v:
            return clip(v, TEXT_TARGET)
    return ""


def workflow_name(ti):
    if not isinstance(ti, dict):
        return ""
    for key in ("name", "workflow", "workflowName"):
        if isinstance(ti.get(key), str) and ti[key]:
            return ti[key]
    m = _WF_NAME.search(ti.get("script") or "")
    if m:
        return m.group(1)
    sp = ti.get("scriptPath") or ti.get("path") or ""
    if isinstance(sp, str) and sp:
        return os.path.basename(sp).split(".")[0]
    return "workflow"


def _request_params(line):
    """(timestamp, model, effort, thinking) from one `api-request` record line, or None."""
    try:
        rec = json.loads(line)
    except ValueError:
        return None
    p = rec.get("params") if isinstance(rec, dict) else None
    if not isinstance(p, dict):
        return None
    oc = p.get("output_config") if isinstance(p.get("output_config"), dict) else {}
    th = p.get("thinking") if isinstance(p.get("thinking"), dict) else {}
    eff = oc.get("effort") if isinstance(oc.get("effort"), str) else ""
    think = "/".join(x for x in (th.get("type"), th.get("display")) if isinstance(x, str) and x)
    model = p.get("model") if isinstance(p.get("model"), str) else ""
    return parse_ts(rec.get("timestamp")), model, clip(eff, 20), clip(think, 40)


def _visible_chars(content):
    """Characters of output a model wrote into these content blocks (text, tool input, thinking text)."""
    n = 0
    for b in content:
        if not isinstance(b, dict):
            continue
        bt = b.get("type")
        if bt == "text" and isinstance(b.get("text"), str):
            n += len(b["text"])
        elif bt == "tool_use":
            try:
                n += len(json.dumps(b.get("input"), ensure_ascii=False, separators=(",", ":"))) + len(b.get("name") or "")
            except (TypeError, ValueError):
                pass
        elif bt == "thinking" and isinstance(b.get("thinking"), str):
            n += len(b["thinking"])
    return n


def _content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                out.append(b.get("text") or "")
            elif isinstance(b, str):
                out.append(b)
        return "\n".join(out)
    return ""


def classify_user(rec, accept_sidechain):
    """('prompt'|'command'|'notify'|'peer'|'interrupt'|'compact', text) or None."""
    if rec.get("isMeta"):
        return None
    if rec.get("isSidechain") and not accept_sidechain:
        return None
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return None
    content = msg.get("content")
    if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return None
    text = _content_text(content)
    has_image = isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "image" for b in content)
    origin = rec.get("origin") if isinstance(rec.get("origin"), dict) else {}
    okind = origin.get("kind")
    head = text.lstrip()[:200]
    if okind == "task-notification" or head.startswith("<task-notification"):
        m = _TN_SUMMARY.search(text) or _TN_STATUS.search(text)
        return ("notify", clip(m.group(1) if m else "Background task update", TEXT_ASSIST))
    if okind in ("peer", "coordinator") or head.startswith("Another Claude session sent"):
        return ("peer", clip(text, TEXT_ASSIST))
    if "<command-name>" in head:
        m = _CMD_NAME.search(text)
        a = _CMD_ARGS.search(text)
        name = m.group(1) if m else "/command"
        if not name.startswith("/"):
            name = "/" + name
        return ("command", clip(name + (" " + a.group(1) if a and a.group(1) else ""), TEXT_ASSIST))
    if head.startswith("<local-command") or head.startswith("Caveat:"):
        return None
    if head.startswith("[Request interrupted"):
        return ("interrupt", "")
    if head.startswith("This session is being continued"):
        return ("compact", "")
    text = _TAG_STRIP.sub("", text).strip()
    if not text and not has_image:
        return None
    if not text:
        text = "[image]"
    return ("prompt", clip(text, TEXT_PROMPT))


def _result_text(block):
    c = block.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    return ""


class Acc:
    """Everything the dashboard needs from one transcript file, built record by record."""

    def __init__(self, path, kind="main"):
        self.path = path
        self.kind = kind                      # "main" or "sub"
        self.session_id = ""
        self.agent_id = ""
        self.cwd = ""
        self.branch = ""
        self.version = ""
        self.entrypoint = ""
        self.first_ts = None
        self.last_ts = None
        self.active_secs = 0.0                # sum of gaps under ACTIVE_GAP between records
        self.ai_title = ""
        self.custom_title = ""
        self.agent_name = ""
        self.summary = ""
        self.first_prompt = ""
        self.cost_state = None
        self.bad_lines = 0
        self.prompts = 0
        self.api_msgs = 0
        self.tool_calls = 0
        self.tool_errors = 0
        self.blocked = 0
        self.api_errors = 0
        self.interrupts = 0
        self.compactions = 0
        self.fallbacks = 0
        self.last_stop = ""
        self.last_role = ""                   # "assistant" or "user": who wrote the latest message record
        self.last_kind = ""                   # what the latest turn-relevant record was: "assistant", "tool_result",
                                              # or a classify_user kind ("prompt", "notify", "interrupt", ...)
        self.last_msg_stop = ""               # stop_reason on the latest assistant record ("" while streaming blocks)
        self.msg_order = []                   # message ids in first-seen order (burn rate over the last minutes)
        self.msg_ts = {}                      # message id -> timestamp of its first record
        self.last_text = ""
        self.last_tool = None                 # {"name","target","t"}
        self.handed_back = False
        self.last_model = ""
        self.models = {}                      # model -> msg count
        self.usage = {}                       # model -> [msgs, in, out, cread, cw5, cw1h, cost]
        self.msg_usage = {}                   # message id -> (model, (in,out,cr,cw5,cw1h), cost, hour)
        self.hours = {}                       # epoch hour -> {"c": {family: cost}, "tok", "tools", "err", "msgs"}
        self.mins = {}                        # epoch minute -> [tok x5 families, tools, errors, active] (module docstring)
        self.min_top = 0                      # the newest minute mins has seen; minutes <= min_top - KEEP are refused
        self.tools = {}                       # tool -> [calls, errors, dur_sum, dur_n]
        self.pending = {}                     # tool_use id -> its timeline event (dict)
        self.spawns = []                      # Agent/Task/Workflow calls
        self.spawn_by_id = {}
        self.tool_ts = []                     # timestamps of tool calls (guard-event attribution)
        self.events = []
        self.dropped_events = 0
        self._last_text_mid = None
        self.plan_sigs = []                   # plans.py hook: tool calls that can tie this transcript to a plan
        # effort and thinking (module docstring)
        self.effort = ""                      # effort on the latest api-request record ("" before any)
        self.effort_msgs = {}                 # effort -> assistant messages sent with it
        self.effort_log = []                  # [{"t", "effort", "model"}] at the first request and each change
        self.thinking_mode = ""               # params.thinking of the latest request, e.g. "adaptive/updates"
        self.think_blocks = 0                 # thinking + redacted_thinking content blocks
        self.think_redacted = 0
        self.think_text_blocks = 0            # blocks that carry thinking text (not just a signature)
        self.think_chars = 0
        self.think_model = {}                 # model -> [messages with a thinking count, their output tokens, thinking tokens]
        self.msg_think = {}                   # message id -> (model, output tokens, thinking tokens)
        self.fast_msgs = 0                    # messages billed at fast-mode speed
        self.model_log = []                   # [{"t", "from", "to"}] each time the model changes mid-transcript
        # Output estimates (module docstring): messages with no final usage record
        self.msg_chars = {}                   # message id -> characters of its visible content (text, tool input, thinking text)
        self.msg_final = set()                # message ids that have a record with stop_reason (final usage)
        self.msg_est = {}                     # message id -> (model, output tokens added by the estimate)
        self.context = 0                      # the latest call's prompt: input + cache read + cache write
        # What the transcript did, for routing.py (work type, effort by cost)
        self.msg_eff = {}                     # message id -> effort it was sent with (when a request record named one)
        self.edited = set()                   # file paths touched by Edit/Write/MultiEdit/NotebookEdit (capped)
        self.bash_tests = 0                   # Bash calls that ran a test, lint or type check
        self.bash_mutate = 0                  # Bash calls that changed files or repo state
        self._ctx_mid = None

    # ── helpers ────────────────────────────────────────────────────────────
    def _hour(self, t):
        h = int(t // 3600)
        b = self.hours.get(h)
        if b is None:
            b = self.hours[h] = {"c": {}, "m": {}, "tok": 0, "tools": 0, "err": 0, "msgs": 0}
        return b

    def _min(self, t, create=True):
        """The minute row of time t (module docstring), made when missing; None when t is older than the
        kept span, or (create=False) when the row is not there. Rows are pruned here, amortised: the
        dict never holds more than SERIES_KEEP_MINS + SERIES_SLACK_MINS of them."""
        m = int(t // 60)
        mins = self.mins
        if not create:
            return None if m <= self.min_top - SERIES_KEEP_MINS else mins.get(m)
        if m > self.min_top:
            self.min_top = m
        elif m <= self.min_top - SERIES_KEEP_MINS:
            return None
        r = mins.get(m)
        if r is None:
            if len(mins) >= SERIES_KEEP_MINS + SERIES_SLACK_MINS:
                cut = self.min_top - SERIES_KEEP_MINS
                for k in [k for k in mins if k <= cut]:
                    del mins[k]
            r = mins[m] = [0, 0, 0, 0, 0, 0, 0, 0]
        return r

    def _min_tokens(self, mid, model, tup, sign):
        """Add (sign=1) or take out (sign=-1) one message's new tokens in the minute of its first record."""
        mt = self.msg_ts.get(mid)
        n = tup[0] + tup[1] + tup[3] + tup[4]
        if not mt or n <= 0:
            return
        r = self._min(mt, sign > 0)
        if r is not None:
            c = _FAM_COL.get(pricing.family(model), 4)
            r[c] = max(0, r[c] + sign * n)

    def _min_span(self, t0, t1):
        """Mark the minutes strictly between a tool call's start (t0) and its result (t1) active; the two
        ends are marked by their own records. At most SERIES_KEEP_MINS minutes, never a refused one."""
        m1 = int(t1 // 60)
        m = max(int(t0 // 60) + 1, m1 - SERIES_KEEP_MINS + 1, self.min_top - SERIES_KEEP_MINS + 1)
        while m < m1:
            r = self._min(m * 60)
            if r is not None:
                r[M_ACTIVE] = 1
            m += 1

    def _event(self, ev):
        self.events.append(ev)
        if len(self.events) > MAX_EVENTS + MAX_EVENTS // 4:
            cut = len(self.events) - MAX_EVENTS
            del self.events[:cut]
            self.dropped_events += cut
        return ev

    def model_main(self):
        if not self.models:
            return ""
        return max(self.models.items(), key=lambda kv: kv[1])[0]

    def totals(self):
        """Usage sums. "tokens" is NEW tokens (input + cache writes + output); cache reads stay separate."""
        inp = out = cr = cw = msgs = 0
        cost = 0.0
        unpriced = 0
        for m, u in self.usage.items():
            msgs += u[0]
            inp += u[1]
            out += u[2]
            cr += u[3]
            cw += u[4] + u[5]
            if pricing.has_price(m):
                cost += u[6]
            else:
                unpriced += u[1] + u[2] + u[4] + u[5]
        return {"input": inp, "output": out, "cache_read": cr, "cache_write": cw,
                "tokens": inp + out + cw, "cost": cost, "unpriced_tokens": unpriced, "msgs": msgs,
                "output_estimated": sum(e[1] for e in self.msg_est.values()), "est_msgs": len(self.msg_est),
                "context": self.context}

    def est_by_model(self):
        """model -> [messages with estimated output, output tokens the estimate added]."""
        out = {}
        for m, extra in self.msg_est.values():
            r = out.setdefault(m, [0, 0])
            r[0] += 1
            r[1] += extra
        return out

    # ── the record handler ─────────────────────────────────────────────────
    def feed(self, rec):
        if not isinstance(rec, dict):
            return
        t = rec.get("type")
        if t == "assistant":
            self._assistant(rec)
        elif t == "user":
            self._user(rec)
        elif t == "system":
            self._system(rec)
        elif t == "ai-title":
            self.ai_title = clip(rec.get("aiTitle"), 200)
        elif t == "custom-title":
            self.custom_title = clip(rec.get("customTitle"), 200)
        elif t == "agent-name":
            self.agent_name = clip(rec.get("agentName"), 120)
        elif t == "summary":
            self.summary = clip(rec.get("summary"), 200)
        elif t == "cost-state":
            if isinstance(rec.get("totalCostUSD"), (int, float)):
                self.cost_state = {"total": rec["totalCostUSD"],
                                   "models": {m: (u or {}).get("costUSD") for m, u in (rec.get("modelUsage") or {}).items()
                                              if isinstance(u, dict)}}
        else:
            return
        if t in ("assistant", "user", "system"):
            ts = parse_ts(rec.get("timestamp"))
            if ts:
                r = self._min(ts)
                if r is not None:
                    r[M_ACTIVE] = 1
                if self.first_ts is None or ts < self.first_ts:
                    self.first_ts = ts
                if self.last_ts is None or ts > self.last_ts:
                    if self.last_ts is not None and ts - self.last_ts < ACTIVE_GAP:
                        self.active_secs += ts - self.last_ts
                    self.last_ts = ts
            if not self.session_id and rec.get("sessionId"):
                self.session_id = rec["sessionId"]
            if not self.agent_id and rec.get("agentId"):
                self.agent_id = rec["agentId"]
            if not self.cwd and isinstance(rec.get("cwd"), str):
                self.cwd = rec["cwd"]
            if rec.get("gitBranch"):
                self.branch = rec["gitBranch"]
            if rec.get("version"):
                self.version = rec["version"]
            if rec.get("entrypoint") and not self.entrypoint:
                self.entrypoint = rec["entrypoint"]

    def request(self, line):
        """One `api-request` record: the effort and thinking mode the next messages are sent with."""
        got = _request_params(line)
        if got is None:
            return
        ts, model, eff, think = got
        if think:
            self.thinking_mode = think
        self._set_effort(ts, model, eff)

    def _set_effort(self, ts, model, eff):
        if not eff or eff == self.effort:
            return
        first = not self.effort
        self.effort = eff
        self.effort_log.append({"t": ts, "effort": eff, "model": model})
        if len(self.effort_log) > 200:
            del self.effort_log[1:-199]
        self._event({"k": "effort", "t": ts, "text": ("Effort " if first else "Effort changed to ") + eff,
                     "effort": eff, "first": first})

    def _assistant(self, rec):
        msg = rec.get("message")
        if not isinstance(msg, dict):
            return
        ts = parse_ts(rec.get("timestamp"))
        model = msg.get("model") or ""
        mid = msg.get("id") or rec.get("requestId") or rec.get("uuid") or ""
        synthetic = model.startswith("<")
        if model and not synthetic:
            if self.last_model and model != self.last_model and mid not in self.msg_usage:
                self.model_log.append({"t": ts, "from": self.last_model, "to": model})
                if len(self.model_log) > 200:
                    del self.model_log[1:-199]
                if not (self.events and self.events[-1].get("k") == "fallback"):   # a refusal fallback already says so
                    self._event({"k": "model", "t": ts, "text": "Model changed from " + pricing.display_name(self.last_model)
                                 + " to " + pricing.display_name(model)})
                    self._last_text_mid = None
            self.last_model = model
        u = msg.get("usage")
        if mid and not synthetic:
            content0 = msg.get("content")
            if isinstance(content0, list):
                self.msg_chars[mid] = self.msg_chars.get(mid, 0) + _visible_chars(content0)
            if msg.get("stop_reason"):
                self.msg_final.add(mid)
        if not synthetic and isinstance(u, dict) and mid:
            cc = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else None
            cw_total = int(u.get("cache_creation_input_tokens") or 0)
            if cc is not None:
                cw1h = int(cc.get("ephemeral_1h_input_tokens") or 0)
                cw5 = int(cc.get("ephemeral_5m_input_tokens") or 0)
                if cw1h + cw5 < cw_total:
                    cw5 += cw_total - cw1h - cw5
            else:
                cw5, cw1h = cw_total, 0
            out_rec = int(u.get("output_tokens") or 0)
            out_eff = out_rec
            if mid not in self.msg_final:
                # Usage saved at the start of the stream: the reply's real output is at least
                # what its visible content holds, about 4 characters a token.
                est = (self.msg_chars.get(mid, 0) + 3) // 4
                if est > out_rec:
                    out_eff = est
                    self.msg_est[mid] = (model, est - out_rec)
                else:
                    self.msg_est.pop(mid, None)
            else:
                self.msg_est.pop(mid, None)
            tup = (int(u.get("input_tokens") or 0), out_eff,
                   int(u.get("cache_read_input_tokens") or 0), cw5, cw1h)
            if self._ctx_mid is None or mid == self._ctx_mid or mid not in self.msg_usage:
                self._ctx_mid = mid
                self.context = tup[0] + tup[2] + tup[3] + tup[4]
            prev = self.msg_usage.get(mid)
            if prev is None or prev[1] != tup:
                cost = pricing.message_cost(model, *tup, speed=u.get("speed") or "")
                hour = int(ts // 3600) if ts else (prev[3] if prev else 0)
                if prev is not None:
                    self._apply_usage(prev[0], prev[1], prev[2], prev[3], -1)
                    self._min_tokens(mid, prev[0], prev[1], -1)
                else:
                    self.api_msgs += 1
                    self.models[model] = self.models.get(model, 0) + 1
                    if ts:
                        self.msg_order.append(mid)
                        self.msg_ts[mid] = ts
                    if u.get("speed") == "fast":
                        self.fast_msgs += 1
                    # routing.py's effort: the assistant record's own `effort` field (written by 2.1.30x for
                    # every reply, subagents included), else the latest api-request record's.
                    reff = rec.get("effort")
                    if isinstance(reff, str) and reff:
                        self._set_effort(ts, model, clip(reff, 20))
                    eff = self.effort
                    if eff:
                        self.msg_eff[mid] = eff
                        self.effort_msgs[eff] = self.effort_msgs.get(eff, 0) + 1
                        if hour:
                            em = self._hour(ts).setdefault("e", {}).setdefault(model, {})
                            em[eff] = em.get(eff, 0) + 1
                self.msg_usage[mid] = (model, tup, cost, hour)
                self._apply_usage(model, tup, cost, hour, 1)
                self._min_tokens(mid, model, tup, 1)
            det = u.get("output_tokens_details")
            if isinstance(det, dict) and isinstance(det.get("thinking_tokens"), int):
                self._think_usage(mid, model, tup[1], det["thinking_tokens"])
        content = msg.get("content")
        if msg.get("stop_reason"):
            self.last_stop = msg["stop_reason"]
        self.last_role = "assistant"
        self.last_kind = "assistant"
        self.last_msg_stop = msg.get("stop_reason") or ""
        if not isinstance(content, list):
            return
        for b in content:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text":
                txt = (b.get("text") or "").strip()
                if not txt:
                    continue
                self.last_text = clip(txt, TEXT_ASSIST)
                if self._last_text_mid == mid and self.events and self.events[-1].get("k") == "text":
                    ev = self.events[-1]
                    ev["text"] = clip(ev["text"] + "\n\n" + txt, TEXT_ASSIST)
                else:
                    self._event({"k": "text", "t": ts, "text": clip(txt, TEXT_ASSIST),
                                 "model": pricing.display_name(model) if model else ""})
                    self._last_text_mid = mid
            elif bt == "tool_use":
                self._tool_use(b, ts)
            elif bt in ("thinking", "redacted_thinking"):
                self.think_blocks += 1
                if bt == "redacted_thinking":
                    self.think_redacted += 1
                txt = b.get("thinking")
                if isinstance(txt, str) and txt:
                    self.think_text_blocks += 1
                    self.think_chars += len(txt)

    def _think_usage(self, mid, model, out, think):
        """A message's thinking count; a later record of the same message replaces the earlier one."""
        prev = self.msg_think.get(mid)
        if prev == (model, out, think):
            return
        for m, o, t, sign in ((prev[0], prev[1], prev[2], -1) if prev else (None, 0, 0, 0), (model, out, think, 1)):
            if not sign:
                continue
            r = self.think_model.get(m)
            if r is None:
                r = self.think_model[m] = [0, 0, 0]
            r[0] += sign
            r[1] += sign * o
            r[2] += sign * t
        self.msg_think[mid] = (model, out, think)

    def _apply_usage(self, model, tup, cost, hour, sign):
        """Add (sign=1) or remove (sign=-1) one message's usage."""
        u = self.usage.get(model)
        if u is None:
            u = self.usage[model] = [0, 0, 0, 0, 0, 0, 0.0]
        u[0] += sign
        for i in range(5):
            u[i + 1] += sign * tup[i]
        if cost is not None:
            u[6] += sign * cost
        if not hour:
            return
        b = self.hours.get(hour)
        if b is None:
            b = self.hours[hour] = {"c": {}, "m": {}, "tok": 0, "tools": 0, "err": 0, "msgs": 0}
        new = tup[0] + tup[1] + tup[3] + tup[4]       # new tokens; cache reads counted apart
        b["tok"] += sign * new
        b["cr"] = b.get("cr", 0) + sign * tup[2]
        b["msgs"] += sign
        mu = b["m"].get(model)
        if mu is None:
            mu = b["m"][model] = [0, 0.0]
        mu[0] += sign * new
        if cost is not None:
            mu[1] += sign * cost
            fam = pricing.family(model)
            b["c"][fam] = b["c"].get(fam, 0.0) + sign * cost

    def _tool_use(self, b, ts):
        name = b.get("name") or "?"
        ti = b.get("input") if isinstance(b.get("input"), dict) else {}
        tid = b.get("id") or ""
        self.tool_calls += 1
        sig = _plans.tool_signal(name, ti, ts)   # plans.py hook: plan reads, checkbox ticks, todos, Skill args
        if sig is not None:
            self.plan_sigs.append(sig)
        if name in _EDIT_TOOLS:
            if len(self.edited) < MAX_EDITED:
                self.edited.add(str(ti.get("file_path") or ti.get("notebook_path") or "?"))
        elif name == "Bash":
            cmd = ti.get("command")
            if isinstance(cmd, str):
                if _TEST_RE.search(cmd):
                    self.bash_tests += 1
                if _MUTATE_RE.search(cmd):
                    self.bash_mutate += 1
        st = self.tools.get(name)
        if st is None:
            st = self.tools[name] = [0, 0, 0.0, 0]
        st[0] += 1
        if ts:
            self.tool_ts.append(ts)
            self._hour(ts)["tools"] += 1
            r = self._min(ts)
            if r is not None:
                r[M_TOOLS] += 1
        target = tool_target(name, ti)
        ev = {"k": "tool", "t": ts, "name": name, "target": target, "id": tid, "status": "pending"}
        if name in _SPAWN_TOOLS or name == "Workflow":
            sp = {"id": tid, "t": ts, "tool": name,
                  "kind": "workflow" if name == "Workflow" else "agent",
                  "description": clip(ti.get("description") if name != "Workflow" else workflow_name(ti), 200),
                  "subagent_type": ti.get("subagent_type") or ("workflow" if name == "Workflow" else "general-purpose"),
                  "model_requested": ti.get("model") or "",
                  "effort_requested": clip(ti.get("effort"), 20) if isinstance(ti.get("effort"), str) else "",
                  "background": bool(ti.get("run_in_background")),
                  "status": "pending", "agent_id": "", "run_id": "", "resolved_model": "",
                  "total_tokens": None, "duration_ms": None, "tool_uses": None, "error": ""}
            self.spawns.append(sp)
            self.spawn_by_id[tid] = sp
            ev["spawn"] = True
        if name == "SubagentHandback":
            self.handed_back = True
            msg = ti.get("message")
            if isinstance(msg, str) and msg.strip():
                # The subagent's final report travels in the tool input; show it as its last word.
                self.last_text = clip(msg, TEXT_ASSIST)
                self._event({"k": "text", "t": ts, "text": clip(msg, TEXT_PROMPT), "report": True,
                             "model": pricing.display_name(self.last_model) if self.last_model else ""})
                self._last_text_mid = None
                return
        self._event(ev)
        if tid:
            self.pending[tid] = ev
        self.last_tool = {"name": name, "target": target, "t": ts}

    def _user(self, rec):
        msg = rec.get("message")
        if not isinstance(msg, dict):
            return
        ts = parse_ts(rec.get("timestamp"))
        content = msg.get("content")
        self.last_role = "user"
        if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
            tur = rec.get("toolUseResult") if len(results) == 1 else None
            for b in results:
                self._tool_result(b, ts, tur)
            self.last_kind = "tool_result"
            return
        c = classify_user(rec, accept_sidechain=(self.kind == "sub"))
        if c is None:
            return
        kind, text = c
        self.last_kind = kind
        if kind == "prompt":
            self.prompts += 1
            if not self.first_prompt:
                self.first_prompt = clip(text, 300)
        elif kind == "interrupt":
            self.interrupts += 1
        elif kind == "compact":
            self.compactions += 1
        self._last_text_mid = None
        self._event({"k": kind, "t": ts, "text": text})

    def _tool_result(self, b, ts, tur):
        tid = b.get("tool_use_id") or ""
        ev = self.pending.pop(tid, None)
        is_err = b.get("is_error") is True or b.get("is_error") == "true"
        text = _result_text(b)
        blocked = "BLOCKED:" in text[:400] and ("hook error" in text[:200] or text.lstrip().startswith("BLOCKED:"))
        name = ev["name"] if ev else ""
        if ev is not None:
            ev["status"] = "error" if is_err else "ok"
            if blocked:
                ev["status"] = "blocked"
            if is_err:
                ev["err"] = clip(text, TEXT_ERROR)
            if ts and ev.get("t"):
                ev["dur"] = round(max(0.0, ts - ev["t"]), 2)
                st = self.tools.get(name)
                if st:
                    st[2] += ev["dur"]
                    st[3] += 1
            t0 = ev.get("t")
            if t0:
                if ev["status"] != "ok":        # error or blocked: in the tool_use's minute, if it was counted there
                    r = self._min(t0, False)
                    if r is not None:
                        r[M_ERRORS] += 1
                if ts and ts > t0:
                    self._min_span(t0, ts)
        if is_err:
            self.tool_errors += 1
            if blocked:
                self.blocked += 1
            st = self.tools.get(name)
            if st:
                st[1] += 1
            if ts:
                self._hour(ts)["err"] += 1
        sp = self.spawn_by_id.get(tid)
        if sp is not None:
            if is_err:
                sp["status"] = "error"
                sp["error"] = clip(text, TEXT_ERROR)
            if isinstance(tur, dict):
                st = tur.get("status")
                if st:
                    sp["status"] = {"async_launched": "running"}.get(st, st)
                sp["agent_id"] = tur.get("agentId") or sp["agent_id"]
                sp["run_id"] = tur.get("runId") or sp["run_id"]
                sp["resolved_model"] = tur.get("resolvedModel") or sp["resolved_model"]
                if isinstance(tur.get("totalTokens"), int):
                    sp["total_tokens"] = tur["totalTokens"]
                if isinstance(tur.get("totalDurationMs"), (int, float)):
                    sp["duration_ms"] = tur["totalDurationMs"]
                if isinstance(tur.get("totalToolUseCount"), int):
                    sp["tool_uses"] = tur["totalToolUseCount"]
                if tur.get("workflowName"):
                    sp["description"] = clip(tur["workflowName"], 200)
            elif sp["status"] == "pending" and not is_err:
                sp["status"] = "completed"

    def _system(self, rec):
        st = rec.get("subtype")
        ts = parse_ts(rec.get("timestamp"))
        if st == "api_error":
            self.api_errors += 1
            err = rec.get("error") if isinstance(rec.get("error"), dict) else {}
            text = err.get("formatted") or err.get("message") or "API error"
            attempt = rec.get("retryAttempt")
            if attempt:
                text += f" (retry {attempt}/{rec.get('maxRetries', '?')})"
            if ts:
                self._hour(ts)["err"] += 1
            self._event({"k": "error", "t": ts, "text": clip(text, TEXT_ERROR)})
        elif st == "compact_boundary":
            self.compactions += 1
            meta = rec.get("compactMetadata") if isinstance(rec.get("compactMetadata"), dict) else {}
            pre = meta.get("preTokens")
            self._event({"k": "compact", "t": ts,
                         "text": f"Context compacted ({meta.get('trigger', 'auto')}"
                                 + (f", {pre:,} tokens before" if isinstance(pre, int) else "") + ")"})
        elif st == "model_refusal_fallback":
            self.fallbacks += 1
            self._event({"k": "fallback", "t": ts,
                         "text": f"Flagged on {pricing.display_name(rec.get('originalModel') or '')}, "
                                 f"switched to {pricing.display_name(rec.get('fallbackModel') or '')}"})


class FileCache:
    """path -> (signature, offset, acc). Re-reads only appended bytes."""

    def __init__(self):
        self.entries = {}
        self.bytes_read = 0

    def get(self, path, make_acc, st=None):
        """Return (acc, changed). `make_acc()` builds a fresh accumulator."""
        try:
            st = st or os.stat(path)
        except OSError:
            self.entries.pop(path, None)
            return None, False
        sig = (st.st_size, st.st_mtime_ns, st.st_ino)
        e = self.entries.get(path)
        if e is not None and e["sig"] == sig:
            return e["acc"], False
        if e is None or st.st_ino != e["sig"][2] or st.st_size < e["offset"]:
            e = {"sig": None, "offset": 0, "acc": make_acc()}
            self.entries[path] = e
        self._consume(path, e, st.st_size)
        e["sig"] = sig
        return e["acc"], True

    def _consume(self, path, e, size):
        acc = e["acc"]
        try:
            fh = open(path, "rb")
        except OSError:
            return
        with fh:
            fh.seek(e["offset"])
            carry = b""
            remaining = size - e["offset"]
            while remaining > 0:
                chunk = fh.read(min(CHUNK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                self.bytes_read += len(chunk)
                buf = carry + chunk
                cut = buf.rfind(b"\n")
                if cut < 0:
                    carry = buf
                    continue
                # buf starts exactly at e["offset"]: carry was never counted.
                self._lines(acc, buf[:cut])
                e["offset"] += cut + 1
                carry = buf[cut + 1:]
            # e["offset"] counts bytes up to the last newline; a complete JSON
            # tail without a newline is consumed too, a partial one waits.
            if carry.strip():
                try:
                    rec = json.loads(carry)
                except ValueError:
                    return
                acc.feed(rec)
                e["offset"] += len(carry)

    @staticmethod
    def _lines(acc, data):
        for line in data.split(b"\n"):
            if line.startswith(_REQUEST_PREFIX):
                req = getattr(acc, "request", None)
                if req is not None:
                    req(line)
                continue
            if not line or line.startswith(_SKIP_PREFIXES):
                continue
            if b'"attachment":{' in line[:160] and line.startswith(b'{"parentUuid"'):
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                acc.bad_lines += 1
                continue
            acc.feed(rec)


class LineLog:
    """Accumulator for a cc_tool activity.jsonl: keeps the guard decisions only."""

    def __init__(self):
        self.events = []
        self.tool_lines = 0
        self.bad_lines = 0

    def feed(self, rec):
        if not isinstance(rec, dict):
            return
        kind = rec.get("kind")
        if kind == "tool":
            self.tool_lines += 1
            return
        if kind not in ("deny", "ask", "warn"):
            return
        ts = rec.get("ts")
        if not isinstance(ts, (int, float)):
            return
        self.events.append({"t": float(ts), "kind": kind, "hook": rec.get("hook") or "",
                            "tool": rec.get("tool") or "",
                            "target": clip(tilde(rec.get("command") or rec.get("path") or ""), TEXT_TARGET),
                            "message": clip(rec.get("message"), TEXT_ERROR)})


from . import plans as _plans   # noqa: E402  plans.py hook; imported last because plans.py imports this module
