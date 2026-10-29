"""The Factory floor's one live graph behind GET /api/graph: projects, sessions, agents, workflows, orders.

Joined from what the other views already compute, never re-derived:
  Store._board(now)           every agent on the Live board (main threads and subagents) with its live
                              state, the workflow headers and the activity feed
  Store._row(s, now)          the session detail (prompts, tool calls, errors, usage, guard)
  attention.alerts_payload()  open "needs you" alerts; each attaches to the node it is about
  plans payload               per-project plan progress and the session's plan chip (cached PLANS_TTL)
  factory.project_orders()    work orders per project root (cached ORDERS_TTL); a missing module or one
                              that raises means no orders, never a failed graph
  transcript hour buckets     project cost, tokens, model mix and errors over the window (Overview's rule)

Node ids are stable across polls (they key the client's layout):
  p:<project label>   s:<session>   a:<session>/<agent>   w:<session>/<run>   o:<project label>/<order>
Parents: a session hangs under its project, a subagent under the thread that spawned it, a Workflow
agent under its workflow node, a workflow under the thread that called Workflow, an order under its
project. Every parent link is an edge (member p->s/p->o, spawn s|a->a and s|a->w, step w->a), plus
`runs` from an order to the workflows it runs (the one with the ledger's run_id and every one whose agents
are labelled `FO-NNN:<station>`, design §5.6, so a rerun links while the recorded run lingers) and to a
session whose branch is the order's lane branch.
Links never cross projects: order ids repeat between repos.

What is on the floor: a session on the Live board (store LIVE_SECS, lingering finished agents, the
stalled show window), written in the last LIVE_SECS, or with an open alert; a project with one of
those or a shown non-terminal order. all_projects adds every project in the window as a dormant hub;
project=<label> keeps that project alone. A terminal order (done, rejected; a failed one can be re-laned) is shown for
ORDER_LINGER_SECS after its last ledger event, always with all_projects.

Session nodes carry the main thread's live fields (state, tool, last text) and the whole session's
cost, tokens, errors, burn and spark (summed over its board rows). kpis.cost_window, guard and errors
cover the window in scope (every project, or the filtered one) whatever all_projects says; the rest
count the floor. `sig` hashes everything but `now`, with each clock-derived age (node age, agent
stalled_for, alert age) replaced by the time it counts from: it holds still between polls while
nothing is written, and moves on any write (and when a burn rate, spark bucket or series bucket
changes, which depend on the clock through their windows). Strings are clipped as the board clips
them; paths only ever leave through tilde().

`series` is the last hour in SERIES_N clock-aligned buckets of SERIES_BUCKET seconds (oldest first):
end = (floor(now / SERIES_BUCKET) + 1) * SERIES_BUCKET, start = end - SERIES_N * SERIES_BUCKET, so the
last bucket holds now and is still filling, and a time on an edge is in the later bucket. Its scope is
every launch-window session of the floor's project (or all) written since start, each transcript
(main thread or subagent, one Acc) whose minute buckets (parser.py, Acc.mins) reach the window, plus
the board rows running now: not just what is on the floor (a subagent that finished 40 minutes ago
still counts), and neither all_projects nor `days` changes it. Per bucket: tokens (new tokens of the
replies whose first record is in it, split by family; the family keys are only the non-zero ones and
always sum to tokens), tool_calls (started in it), tool_errors (of those, an error or guard block),
agents (distinct transcripts that wrote a record in it, had a tool call running across it, or run on
the board now: from max(since, start) through now) and agents_total (distinct transcripts counted in
any bucket). Every value is a non-negative int and every list has SERIES_N entries, on an empty floor
too. It is in the sig, so the sig moves with any bucket (at the latest when start moves, every
SERIES_BUCKET seconds) and holds still between polls in one bucket while nothing is written.
"""
import hashlib
import json
import math
import re
import time

from . import attention
from . import plans as plans_mod
from . import pricing
from .parser import M_ACTIVE, M_ERRORS, M_TOOLS, SERIES_FAMILIES, clip, tilde
from .store import LINGER_SECS, LIVE_SECS, RUNNING, SPARK_MINS

PLANS_TTL = 5.0             # the plans join is re-evaluated at most this often (wall clock)
ORDERS_TTL = 2.0            # ...and each project's orders re-read at most this often
ORDER_LINGER_SECS = 86400   # a terminal order stays on the default floor this long after its last event
MAX_ORDERS = 200            # per project (factory.py reads at most as many)
MAX_STATIONS = 20
MAX_PLANS = 10              # plan summaries per project hub
MAX_MIX = 8                 # model mix rows per project hub
SERIES_BUCKET = 180         # series: seconds per bucket (a whole number of minutes: Acc.mins is per minute)
SERIES_N = 20               # ...and buckets: the last hour
ORDER_STATUSES = ("queued", "spec", "approved", "building", "verifying", "review", "done",
                  "blocked", "rejected", "failed")
TERMINAL = {"done", "rejected"}     # as cc-factory: a failed order can be re-laned, so it stays on the floor
ORDER_ACTIVE = {"building", "verifying", "review"}
STATION_STATUSES = {"pending", "active", "done", "failed", "skipped"}
_ORDER_ID = re.compile(r"^FO-\d{1,9}$")
_ORDER_LABEL = re.compile(r"^(FO-\d{1,9}):([A-Za-z0-9_.-]{1,40})")
_WF_DONE = {"completed", "complete", "done", "success", "succeeded"}
_WF_FAILED = {"failed", "error", "errored", "killed", "cancelled", "canceled", "aborted", "timeout"}
_BURN = ("cost_min", "tok_min", "new_min", "out_min", "unpriced_min")
_ALERT_RANK = {k: i for i, k in enumerate(attention.BLOCKING)}


# ── small coercions: factory and plan data is read from disk and never trusted ──
def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _int(v):
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else 0


def _text(v, n):
    return clip(v, n) if isinstance(v, str) else ""


def _cache(store):
    """Per-Store memo for the slow joins (plans, orders), kept on the store like attention.for_store."""
    c = getattr(store, "_graph_cache", None)
    if c is None:
        c = store._graph_cache = {"orders": {}}
    return c


# ── plans (cached) ───────────────────────────────────────────────────────────
def _plans(store, alerts, days=None):
    """{"by_project": {label: [summary]}, "chips": {session id: chip}} from the plans payload.

    One evaluation for every project per window (`days`), reused for PLANS_TTL seconds: it walks
    docs trees and may run git. A failure leaves the floor without plans rather than without a floor."""
    days = store.clamp_days(days)
    c = _cache(store)
    memo = c.setdefault("plans", {})          # days -> (monotonic time, view)
    t = time.monotonic()
    hit = memo.get(days)
    if hit is not None and t - hit[0] < PLANS_TTL:
        return hit[1]
    try:
        p = plans_mod.for_store(store).payload("", alerts, days=days)
    except Exception:  # noqa: BLE001 - the Plans view reports its own errors
        p = {}
    by_project = {}
    for pl in p.get("plans") or []:
        lst = by_project.setdefault(pl.get("project") or "", [])
        if len(lst) < MAX_PLANS:
            tasks = pl.get("tasks") or {}
            lst.append({"key": _text(pl.get("key"), 300), "title": _text(pl.get("title"), 200),
                        "status": _text(pl.get("status"), 20), "done": _int(tasks.get("done")),
                        "total": _int(tasks.get("total"))})
    chips = {}
    for key, ch in sorted((p.get("chips") or {}).items()):
        sid = key.split("/", 1)[0]
        if sid in chips and "/" in key:
            continue                     # the session's own chip wins over one of its subagents'
        chips[sid] = {"title": _text(ch.get("title"), 200), "done": _int(ch.get("done")),
                      "total": _int(ch.get("total")), "status": _text(ch.get("status"), 20)}
    view = {"by_project": by_project, "chips": chips}
    for k in [k for k, v in memo.items() if t - v[0] >= PLANS_TTL]:
        del memo[k]
    memo[days] = (t, view)
    return view


# ── orders (factory.py, cached) ──────────────────────────────────────────────
def _factory():
    """lib/cc_dash/factory.py when it is there (another workstream writes it), else None."""
    try:
        from . import factory
    except ImportError:
        return None
    return factory


def _order(o):
    """One §5.5 order dict, re-shaped with every field typed and clipped; None when it has no valid id."""
    if not isinstance(o, dict) or not isinstance(o.get("id"), str) or not _ORDER_ID.match(o["id"]):
        return None
    stations = []
    for st in (o.get("stations") if isinstance(o.get("stations"), list) else [])[:MAX_STATIONS]:
        if isinstance(st, dict) and isinstance(st.get("name"), str) and st["name"]:
            stations.append({"name": clip(st["name"], 40), "title": _text(st.get("title"), 80) or clip(st["name"], 80),
                             "status": st.get("status") if st.get("status") in STATION_STATUSES else "pending",
                             "t": _num(st.get("t"))})
    g, ln = o.get("gate"), o.get("lane")
    return {"id": o["id"], "title": _text(o.get("title"), 200), "priority": _text(o.get("priority"), 8),
            "status": o.get("status") if o.get("status") in ORDER_STATUSES else "queued",
            "path": _text(o.get("path"), 300), "created": _num(o.get("created")), "updated": _num(o.get("updated")),
            "station": _text(o.get("station"), 40), "stations": stations,
            "gate": {"name": _text(g.get("name"), 40), "waiting": bool(g.get("waiting"))} if isinstance(g, dict) else None,
            "lane": {"branch": _text(ln.get("branch"), 200),
                     "worktree": clip(tilde(ln.get("worktree")), 300) if isinstance(ln.get("worktree"), str) else ""}
            if isinstance(ln, dict) else None,
            "run_id": _text(o.get("run_id"), 100), "rework": _int(o.get("rework")), "events": _int(o.get("events")),
            "line": _text(o.get("line"), 40)}


def _project_orders(store, fac, root, now):
    """Sanitised orders of one project root, re-read at most every ORDERS_TTL seconds."""
    c = _cache(store)["orders"]
    t = time.monotonic()
    hit = c.get(root)
    if hit is not None and hit[2] is fac and t - hit[0] < ORDERS_TTL:
        return hit[1]
    try:
        # Orders are committed in the main checkout; a linked worktree's tree may not have them yet.
        main = getattr(fac, "main_checkout", None)
        raw = fac.project_orders(main(root) if main else root, now)
    except Exception:  # noqa: BLE001 - a broken factory module must not take the floor down
        raw = []
    out, seen = [], set()
    for o in (raw if isinstance(raw, list) else [])[:MAX_ORDERS]:
        o = _order(o)
        if o is not None and o["id"] not in seen:
            seen.add(o["id"])
            out.append(o)
    if len(c) > 256:
        c.clear()
    c[root] = (t, out, fac)
    return out


# ── project aggregates over the window ───────────────────────────────────────
def _project_stats(sessions, now, days, guards=None):
    """{label: window totals} from every transcript's hour buckets, counted the way Overview counts them.
    `guards` maps a session id to its guard events in the window (default: the session's own list)."""
    hc = int((now - days * 86400) // 3600)
    out = {}
    for s in sessions:
        p = out.get(s.label)
        if p is None:
            p = out[s.label] = {"root": s.root, "sessions_live": 0, "sessions_window": 0, "cost": 0.0, "tokens": 0,
                                "errors": 0, "guard": {"deny": 0, "ask": 0, "warn": 0}, "mix": {}}
        p["root"] = p["root"] or s.root
        p["sessions_window"] += 1
        p["sessions_live"] += now - s.last_write < LIVE_SECS
        for ev in (guards[s.sid] if guards is not None else s.guard):
            if ev.get("kind") in p["guard"]:
                p["guard"][ev["kind"]] += 1
        for a in [s.acc] + [x.acc for x in s.subs]:
            for h, b in a.hours.items():
                if h < hc:
                    continue
                p["tokens"] += b["tok"]
                p["errors"] += b["err"]
                p["cost"] += sum(b["c"].values())
                for m, (tok, cost) in b.get("m", {}).items():
                    name = pricing.display_name(m)
                    r = p["mix"].get(name)
                    if r is None:
                        r = p["mix"][name] = {"family": pricing.family(m), "model": clip(name, 80), "tokens": 0,
                                              "cost": None}
                    r["tokens"] += tok
                    if pricing.has_price(m):
                        r["cost"] = (r["cost"] or 0.0) + cost
    return out


# ── the last hour (series) ───────────────────────────────────────────────────
def _series(sessions, rows, project, now):
    """The last hour's buckets as plain data (module docstring), from the minute buckets of every
    transcript in scope and the board rows running now. Read under the store's lock."""
    n, per = SERIES_N, SERIES_BUCKET // 60
    end = (int(now // SERIES_BUCKET) + 1) * SERIES_BUCKET
    start = end - n * SERIES_BUCKET
    m0, m1 = start // 60, end // 60
    fam = [[0] * n for _ in SERIES_FAMILIES]
    f0, f1, f2, f3, f4 = fam
    tools, errs = [0] * n, [0] * n
    masks = {}                      # Acc -> bitmask of the buckets it worked in
    for s in sessions.values():
        if (project and s.label != project) or s.last_write < start:
            continue
        for a in [s.acc] + [x.acc for x in s.subs]:
            if a.min_top < m0:
                continue
            bits = 0
            for m, r in a.mins.items():
                if m < m0 or m >= m1:
                    continue
                i = (m - m0) // per
                if r[M_ACTIVE]:
                    bits |= 1 << i
                if r[M_TOOLS]:
                    tools[i] += r[M_TOOLS]
                    errs[i] += r[M_ERRORS]
                t0, t1, t2, t3, t4 = r[0], r[1], r[2], r[3], r[4]
                if t0 or t1 or t2 or t3 or t4:
                    f0[i] += t0
                    f1[i] += t1
                    f2[i] += t2
                    f3[i] += t3
                    f4[i] += t4
            if bits:
                masks[a] = bits
    subs_of = {}
    for row in rows:                # running now: from max(since, start) through the current bucket
        if row["state"] not in RUNNING:
            continue
        s = sessions.get(row["session"])
        if s is None or (project and s.label != project):
            continue
        if row["agent_id"]:
            by_id = subs_of.get(s.sid)
            if by_id is None:
                by_id = subs_of[s.sid] = {x.agent_id: x.acc for x in reversed(s.subs)}
            a = by_id.get(row["agent_id"])
        else:
            a = s.acc
        if a is None:
            continue
        since = _num(row.get("since"))
        i0 = n - 1 if since is None else min(n - 1, max(0, int((since - start) // SERIES_BUCKET)))
        masks[a] = masks.get(a, 0) | (((1 << n) - 1) >> i0 << i0)
    agents = [0] * n
    for bits in masks.values():
        while bits:
            low = bits & -bits
            agents[low.bit_length() - 1] += 1
            bits ^= low
    return {"start": start, "fam": dict(zip(SERIES_FAMILIES, fam)), "tools": tools, "errors": errs,
            "agents": agents, "total": len(masks)}


def _series_view(raw, now):
    """`series` for the payload: every count a non-negative int, every list SERIES_N long, only the
    whitelisted families with a non-zero hour (tokens is their sum), tool_errors never above tool_calls.
    With no raw series (or a start that does not hold now) it is the empty hour ending after now."""
    n = SERIES_N
    end = (int(now // SERIES_BUCKET) + 1) * SERIES_BUCKET
    raw = raw if isinstance(raw, dict) and _int(raw.get("start")) == end - n * SERIES_BUCKET else {}

    def ints(v):
        out = [max(0, _int(x)) for x in (v if isinstance(v, list) else [])[:n]]
        return out + [0] * (n - len(out))
    fam = raw.get("fam") if isinstance(raw.get("fam"), dict) else {}
    by_fam = {}
    for f in SERIES_FAMILIES:
        col = ints(fam.get(f))
        if any(col):
            by_fam[f] = col
    calls = ints(raw.get("tools"))
    return {"bucket_secs": SERIES_BUCKET, "start": end - n * SERIES_BUCKET,
            "tokens": [sum(c[i] for c in by_fam.values()) for i in range(n)], "tokens_by_family": by_fam,
            "tool_calls": calls, "tool_errors": [min(e, c) for e, c in zip(ints(raw.get("errors")), calls)],
            "agents": ints(raw.get("agents")), "agents_total": max(0, _int(raw.get("total")))}


# ── node builders ────────────────────────────────────────────────────────────
def _key_id(sid, key):
    """A board key (<session> or <session>/<agent>) as a graph id."""
    return "s:" + sid if not key or key == sid else "a:" + key


def _live(row):
    """The live fields a session or agent node shares, from one board row."""
    tool = row.get("tool")
    return {"model": clip(row["model"], 80), "family": row["family"], "effort": row["effort"],
            "effort_source": row["effort_source"], "cost": row["cost"], "tokens": row["tokens"],
            "burn": {k: row["burn"].get(k, 0) for k in _BURN}, "errors": row["errors"],
            "tool": clip(tool["name"], 80) if tool else None, "target": _text(tool.get("target"), 200) if tool else "",
            "last_text": _text(row.get("last_text"), 600), "since": row["since"], "start": row["start"],
            "age": round(row["age"], 1), "spark": [b["n"] for b in row["spark"]]}


def _session_node(s, srow, rows, chip, now):
    sid = s.sid
    main = next((r for r in rows if r["key"] == sid), None)
    if main is not None:
        n = _live(main)
        state = main["state"]
        n["burn"] = {k: (round(sum(r["burn"].get(k, 0) for r in rows), 4) if k == "cost_min"
                         else sum(r["burn"].get(k, 0) for r in rows)) for k in _BURN}
        n["spark"] = [sum(col) for col in zip(*[[b["n"] for b in r["spark"]] for r in rows])]
        needs = (main.get("needs") or {}).get("kind")
    else:                                  # in the graph for an alert or a fresh write, but off the board
        a = s.acc
        n = {"model": clip(srow["model"], 80), "family": srow["family"], "effort": a.effort or "",
             "effort_source": "recorded" if a.effort else "", "cost": 0.0, "tokens": 0,
             "burn": {k: 0 for k in _BURN}, "errors": 0, "tool": None, "target": "",
             "last_text": _text(a.last_text, 600), "since": s.last_write, "start": srow["start"],
             "age": round(now - s.last_write, 1), "spark": [0] * SPARK_MINS}
        state, needs = "idle", None
    n.update(cost=srow["cost"], tokens=srow["tokens"], errors=srow["errors"])
    node = {"id": "s:" + sid, "kind": "session", "label": srow["title"], "parent": "p:" + s.label,
            "state": state, "needs": needs, **n,
            "detail": {"session": sid, "project": s.label, "worktree": clip(srow["worktree"], 200),
                       "branch": clip(srow["branch"], 200), "cwd": clip(srow["cwd"], 300), "prompts": srow["prompts"],
                       "tool_calls": srow["tool_calls_all"], "tool_errors": srow["tool_errors"],
                       "api_errors": srow["api_errors"],
                       "usage": {"input": srow["input"], "output": srow["output"], "cache_read": srow["cache_read"],
                                 "cache_write": srow["cache_write"]},
                       "guard": dict(srow["guard_kinds"]), "subagents": srow["subagents"],
                       "running": sum(1 for r in rows if r["agent_id"] and r["state"] in RUNNING),
                       "plan": chip}}
    return node, (main["last_write"] if main is not None else s.last_write)


def _agent_node(row, parent, ref):
    return {"id": "a:" + row["key"], "kind": "agent", "label": clip(row["label"], 200), "parent": parent,
            "state": row["state"], "needs": (row.get("needs") or {}).get("kind"), **_live(row),
            "detail": {"session": row["session"], "agent_id": row["agent_id"], "type": clip(row["type"], 80),
                       "kind": row["kind"], "phase": clip(row["phase"], 80), "run_id": clip(row["run_id"], 100),
                       "background": bool(row["background"]), "waiting_on": row["waiting_on"],
                       "stalled_for": row["stalled_for"], "tool_calls": row["tool_calls"], "order": ref[0] if ref else ""}}


def _workflow_state(status, running, finished, total):
    st = (status or "").lower()
    if st in _WF_FAILED:
        return "failed"
    if st in _WF_DONE:
        return "done"
    # No run summary yet (Claude Code writes it when the run ends): running while agents remain.
    if running or st == "running" or (not st and finished < total):
        return "running"
    return "done"


def _workflow_node(head):
    sid = head["session"]
    d = {"run_id": clip(head["run_id"], 100), "name": _text(head["name"], 200), "status": _text(head["status"], 40),
         "phases": [_text(p, 80) for p in head["phases"][:MAX_STATIONS]], "total": _int(head["total"]),
         "finished": _int(head["finished"]), "running": _int(head["running"]), "failed": _int(head["failed"]),
         "order": ""}
    return {"id": "w:%s/%s" % (sid, head["run_id"]), "kind": "workflow", "label": d["name"] or d["run_id"],
            "parent": _key_id(sid, head["parent"]),
            "state": _workflow_state(head["status"], d["running"], d["finished"], d["total"]), "needs": None, "detail": d}


def _match(label):
    m = _ORDER_LABEL.match(label) if isinstance(label, str) else None
    return (m.group(1), m.group(2)) if m else None


# ── the payload ──────────────────────────────────────────────────────────────
def payload(store, project="", all_projects=False, days=None):
    """The floor. `days` is the window of the project aggregates and plans (Store.clamp_days);
    what is live comes from the launch window's board, whatever the window."""
    project = (project or "")[:200]
    days = store.clamp_days(days)
    store.ensure_window(days)     # first: alerts and plans below take their own locks before the store's
    store.refresh()
    # Both take their own lock and then the store's: called before store.lock, never inside it.
    alerts = attention.alerts_payload(store)["alerts"]
    plan_view = _plans(store, alerts, days)
    with store.lock:
        now = store.clock()
        cutoff = now - days * 86400
        board = store._board(now)
        sessions = store.sessions
        series = _series(sessions, board["agents"], project, now)   # the last hour, whatever days says
        scoped = store.scope_sessions(project, days, now)
        stats = _project_stats(scoped, now, days, store._guards(scoped, cutoff))
        rows_of = {}
        for r in board["agents"]:
            rows_of.setdefault(r["session"], []).append(r)
        alerted = {a["session"] for a in alerts}
        picked = sorted((s for sid, s in sessions.items()
                         if (not project or s.label == project)
                         and (sid in rows_of or now - s.last_write < LIVE_SECS or sid in alerted)),
                        key=lambda s: (s.label.lower(), s.label, min([x.first_ts for x in [s.acc] + [y.acc for y in s.subs]
                                                                      if x.first_ts] or [s.mtime]), s.sid))
        # Session nodes are built here: Session and Acc objects are only read under the lock.
        pguard = store._guards(picked, cutoff)
        snodes = {s.sid: _session_node(s, store._row(s, now, pguard[s.sid]), rows_of.get(s.sid, []),
                                       plan_view["chips"].get(s.sid), now)
                  for s in picked}
        label_of = {sid: s.label for sid, s in sessions.items()}
        seen_at = {s.sid: s.last_write for s in picked}
        # Order references (§5.6): a Workflow agent's label is its meta description, which is the
        # journal's `label` for agents the workflow started; either may carry FO-NNN:<station>.
        agent_ref, run_refs = {}, {}
        for s in picked:
            for r in rows_of.get(s.sid, []):
                if r["agent_id"]:
                    j = s.journals.get(r["run_id"]) if r["run_id"] else None
                    ref = _match(r["label"]) or _match(j.label.get(r["agent_id"]) if j else None)
                    if ref:
                        agent_ref[r["key"]] = ref
            for run, j in s.journals.items():
                refs = run_refs.setdefault((s.sid, run), set())
                refs.update(m[0] for m in map(_match, j.label.values()) if m)
            for x in s.subs:
                ref = _match(x.meta.get("description")) if x.run_id else None
                if ref:
                    run_refs.setdefault((s.sid, x.run_id), set()).add(ref[0])
        heads = [w for w in board["workflows"] if w["session"] in snodes]
    # Orders: factory.py reads files only, outside the store's lock.
    fac = _factory()
    orders = {}
    if fac is not None:
        for label, st in sorted(stats.items()):
            if st["root"] and label != "unknown":
                orders[label] = _project_orders(store, fac, st["root"], now)
    return _assemble(days, project, all_projects, now, board, stats, snodes, label_of, seen_at, rows_of,
                     agent_ref, run_refs, heads, orders, alerts, plan_view, series)


def _rank(kind):
    """Which needs wins on one node: a blocking kind (permission, question, plan, other) before the rest."""
    return _ALERT_RANK.get(kind, len(_ALERT_RANK)) if kind else len(_ALERT_RANK) + 1


def _copy_order(o):
    """A per-poll copy: the live overlay must never write into the ORDERS_TTL cache."""
    return dict(o, stations=[dict(st) for st in o["stations"]], gate=dict(o["gate"]) if o["gate"] else None,
                lane=dict(o["lane"]) if o["lane"] else None)


def _assemble(days, project, all_projects, now, board, stats, snodes, label_of, seen_at, rows_of,
              agent_ref, run_refs, heads, orders, alerts, plan_view, series=None):
    """The payload from plain data gathered under the store's lock (no Session or Acc is touched here)."""
    nodes, edges, stamps, proj = {}, {}, {}, {}      # proj: node id -> project label

    def add(n, label):
        nodes[n["id"]] = n
        proj[n["id"]] = label
        return n

    def edge(src, dst, kind):
        e = "%s>%s" % (src, dst)
        if e not in edges:
            edges[e] = {"id": e, "source": src, "target": dst, "kind": kind, "active": False, "rate": 0}

    # Orders shown per project: every non-terminal one, and terminal ones that ended recently.
    shown = {}
    for label, lst in orders.items():
        keep = [_copy_order(o) for o in lst if all_projects or o["status"] not in TERMINAL
                or (o["updated"] or o["created"] or 0) >= now - ORDER_LINGER_SECS]
        if keep:
            shown[label] = sorted(keep, key=lambda o: o["id"])
    labels = {label_of[sid] for sid in snodes} | {a.get("project") or "unknown" for a in alerts} \
        | {lb for lb, lst in shown.items() if any(o["status"] not in TERMINAL for o in lst)}
    if all_projects or project:
        labels |= set(stats)
    if project:
        labels &= {project}

    hubs = {}
    blank = {"root": "", "sessions_live": 0, "sessions_window": 0, "cost": 0.0, "tokens": 0, "errors": 0,
             "guard": {"deny": 0, "ask": 0, "warn": 0}, "mix": {}}
    for label in sorted(labels, key=lambda x: (x.lower(), x)):
        st = stats.get(label) or blank
        counts = {}
        for o in shown.get(label, []):
            counts[o["status"]] = counts.get(o["status"], 0) + 1
        mix = sorted(st["mix"].values(), key=lambda r: (-r["tokens"], r["model"]))[:MAX_MIX]
        hubs[label] = add({
            "id": "p:" + label, "kind": "project", "label": clip(label, 200), "parent": None, "state": "idle",
            "needs": None, "cost": round(st["cost"], 4), "tokens": st["tokens"],
            "detail": {"root": clip(tilde(st["root"]), 300), "sessions_live": st["sessions_live"],
                       "sessions_window": st["sessions_window"], "cost_window": round(st["cost"], 4),
                       "tokens_window": st["tokens"],
                       "model_mix": [dict(r, cost=round(r["cost"], 4) if r["cost"] is not None else None) for r in mix],
                       "guard": dict(st["guard"]), "errors": st["errors"],
                       "plans": list(plan_view["by_project"].get(label, [])), "orders": counts}}, label)
        for o in shown.get(label, []):
            on = add({"id": "o:%s/%s" % (label, o["id"]), "kind": "order", "label": o["title"] or o["id"],
                      "parent": "p:" + label, "state": o["status"], "needs": None, "detail": o}, label)
            edge(on["parent"], on["id"], "member")

    # Sessions, each followed by its agents and workflows in the board's tree order.
    wf_of = {}
    for h in heads:
        wf_of.setdefault(h["session"], {})[h["run_id"]] = h
    for sid, (sn, stamp) in snodes.items():
        label = label_of[sid]
        if label not in hubs:
            continue
        add(sn, label)
        stamps[sn["id"]] = stamp
        runs = wf_of.get(sid, {})
        for r in rows_of.get(sid, []):
            if not r["agent_id"]:
                continue
            wid = "w:%s/%s" % (sid, r["run_id"]) if r["run_id"] in runs else None
            if wid and wid not in nodes:
                add(_workflow_node(runs[r["run_id"]]), label)
            an = add(_agent_node(r, wid or _key_id(sid, r["parent"]), agent_ref.get(r["key"])), label)
            stamps[an["id"]] = r["last_write"]
    for n in nodes.values():
        if n["kind"] == "session":
            edge(n["parent"], n["id"], "member")
        elif n["kind"] in ("agent", "workflow"):
            if n["parent"] not in nodes:     # a parent the board did not place: under the session
                n["parent"] = "s:" + n["id"][2:].split("/", 1)[0]
            edge(n["parent"], n["id"], "step" if n["kind"] == "agent" and n["parent"].startswith("w:") else "spawn")

    # Order links (§5.6), never across projects; then the live station overlay.
    for label, lst in shown.items():
        if label not in hubs:
            continue
        mine = [n for nid, n in nodes.items() if proj[nid] == label]
        wfs = [n for n in mine if n["kind"] == "workflow"]
        for o in lst:
            oid = "o:%s/%s" % (label, o["id"])
            # Both ways, deduped in floor order: the ledger's run_id is the last RECORDED run, so a rerun still
            # in flight is found only by its labels while the recorded one may linger on the floor.
            hit = [w for w in wfs if (o["run_id"] and w["detail"]["run_id"] == o["run_id"])
                   or o["id"] in run_refs.get((w["id"][2:].split("/", 1)[0], w["detail"]["run_id"]), ())]
            for w in hit:
                edge(oid, w["id"], "runs")
                w["detail"]["order"] = w["detail"]["order"] or o["id"]
            branch = (o["lane"] or {}).get("branch")
            for n in mine:
                if branch and n["kind"] == "session" and n["detail"]["branch"] == branch:
                    edge(oid, n["id"], "runs")
            live = sorted((n["start"], agent_ref[n["id"][2:]][1]) for n in mine
                          if n["kind"] == "agent" and n["state"] in RUNNING and agent_ref.get(n["id"][2:], ("",))[0] == o["id"])
            if live:
                o["station"] = live[-1][1]   # the latest-started running agent's station
                for st in o["stations"]:
                    if st["name"] == o["station"]:
                        st["status"] = "active"
    for n in nodes.values():                # a workflow agent carries its workflow's order
        if n["kind"] == "agent" and not n["detail"]["order"] and n["parent"].startswith("w:"):
            n["detail"]["order"] = nodes[n["parent"]]["detail"]["order"]

    # Alerts: each attaches to its agent, else its session, else its project's hub.
    out_alerts = []
    for al in sorted(alerts, key=lambda a: (_rank(a["kind"]), -a["since_ts"], a["id"])):
        sid, aid = al["session"], al.get("agent_id")
        label = label_of.get(sid) or al.get("project") or "unknown"
        if label not in hubs:
            continue
        nid = next(x for x in ("a:%s/%s" % (sid, aid) if aid else "", "s:" + sid, "p:" + label) if x in nodes)
        if _rank(al["kind"]) < _rank(nodes[nid]["needs"]):
            nodes[nid]["needs"] = al["kind"]
        out_alerts.append({"id": clip(al["id"], 40), "node": nid, "kind": al["kind"],
                           "title": _text(al.get("title"), 200), "message": _text(al.get("message"), 400),
                           "tool": clip(al["tool"], 80) if isinstance(al.get("tool"), str) and al["tool"] else None,
                           "age": round(al.get("age") or 0.0, 1), "_t": al["since_ts"]})

    # Edge activity and rates: s/a burn; a workflow sums its agents, an order the targets it runs.
    rate, active = {}, {}
    for nid, n in nodes.items():
        if n["kind"] in ("session", "agent"):
            rate[nid], active[nid] = n["burn"]["tok_min"], n["state"] in RUNNING
    for nid, n in nodes.items():
        if n["kind"] == "workflow":
            rate[nid] = sum(rate[e["target"]] for e in edges.values() if e["source"] == nid and e["kind"] == "step")
            active[nid] = n["state"] == "running"
    for nid, n in nodes.items():
        if n["kind"] == "order":
            rate[nid] = sum(rate[e["target"]] for e in edges.values() if e["source"] == nid and e["kind"] == "runs")
            active[nid] = n["state"] in ORDER_ACTIVE or any(st["status"] == "active" for st in n["detail"]["stations"])
    for e in edges.values():
        e["active"], e["rate"] = active[e["target"]], rate[e["target"]]

    # Hub state: needs > working > idle over everything under it.
    working = set()
    for nid, n in nodes.items():
        hub = hubs[proj[nid]]
        if n is not hub and _rank(n["needs"]) < _rank(hub["needs"]):
            hub["needs"] = n["needs"]
        if n["kind"] in ("session", "agent", "workflow") and active[nid]:
            working.add(proj[nid])
    for label, hub in hubs.items():
        hub["state"] = "needs" if hub["needs"] else "working" if label in working else "idle"

    # KPIs: the floor's nodes; cost_window, guard and errors over the window in scope.
    kp = {"sessions_live": 0, "agents": 0, "working": 0, "needs": 0, "stalled": 0, "failed": 0, "done": 0,
          "burn_cost_min": 0.0, "burn_tok_min": 0, "cost_live": 0.0, "cost_window": 0.0,
          "guard": {"deny": 0, "ask": 0, "warn": 0}, "errors": 0, "orders": {}}
    for nid, n in nodes.items():
        if n["kind"] in ("session", "agent"):
            kp["agents"] += 1
            b = "needs" if n["needs"] else "working" if n["state"] in RUNNING else n["state"]
            if b in ("working", "needs", "stalled", "failed", "done"):
                kp[b] += 1
        if n["kind"] == "session":
            kp["sessions_live"] += now - seen_at[n["detail"]["session"]] < LIVE_SECS
            kp["burn_cost_min"] += n["burn"]["cost_min"]
            kp["burn_tok_min"] += n["burn"]["tok_min"]
            kp["cost_live"] += n["cost"]
        elif n["kind"] == "order":
            kp["orders"][n["state"]] = kp["orders"].get(n["state"], 0) + 1
    for st in stats.values():
        kp["cost_window"] += st["cost"]
        kp["errors"] += st["errors"]
        for k in kp["guard"]:
            kp["guard"][k] += st["guard"][k]
    for k in ("burn_cost_min", "cost_live", "cost_window"):
        kp[k] = round(kp[k], 4)

    feed = []
    for f in board["feed"]:
        nid = _key_id(f["key"].split("/", 1)[0], f["key"])
        if nid in nodes:
            feed.append({"t": f["t"], "key": f["key"], "id": clip(f["id"], 100), "label": clip(f["label"], 200),
                         "kind": f["kind"], "project": clip(f["project"], 200), "family": f["family"],
                         "name": clip(f["name"], 80), "target": _text(f["target"], 200), "status": f["status"],
                         "dur": f["dur"], "node": nid})

    out = {"now": now, "window_days": days, "scope": {"project": project}, "sig": "", "kpis": kp,
           "nodes": list(nodes.values()), "edges": list(edges.values()), "alerts": out_alerts, "feed": feed,
           "series": _series_view(series, now), "limits": {"live_secs": LIVE_SECS, "linger": LINGER_SECS}}
    out["sig"] = _sig(out, stamps)
    for a in out_alerts:
        del a["_t"]
    return out


def _sig(out, stamps):
    """Digest of what the floor shows, clock ticks aside (see the module docstring)."""
    nodes = []
    for n in out["nodes"]:
        if "age" in n:
            n = dict(n, age=stamps.get(n["id"]))
            if n["kind"] == "agent":
                n["detail"] = dict(n["detail"], stalled_for=n["detail"]["stalled_for"] is not None)
        nodes.append(n)
    view = {"window_days": out["window_days"], "scope": out["scope"], "kpis": out["kpis"], "nodes": nodes,
            "edges": out["edges"], "feed": out["feed"], "series": out["series"], "limits": out["limits"],
            "alerts": [dict(a, age=a["_t"]) for a in out["alerts"]]}
    raw = json.dumps(view, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:20]
