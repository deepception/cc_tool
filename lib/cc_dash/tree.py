"""The recursive agent tree of one session: who spawned whom, at any depth.

Every subagent transcript of a session sits flat in <session>/subagents/,
whatever its depth; the tree comes from linkage, not from the folders:
  - an Agent/Task subagent links to the spawn call that made it through
    meta.toolUseId (or the agentId in that call's result). The call can sit
    in the main transcript or in any subagent's transcript, which is what
    makes the tree recursive. meta.parentAgentId and meta.spawnDepth (written
    for depth >= 2) are the fallback when no spawn call matches.
  - Workflow agents sit in subagents/workflows/<run>/ with no toolUseId;
    they hang under a workflow node, which links to the Workflow call whose
    result carries that run id, wherever that call was made.
  - A transcript that links to nothing is an orphan and hangs under the main
    thread, flagged. So does anything caught in a parent cycle. Nodes deeper
    than MAX_DEPTH are flattened under the deepest allowed ancestor.
  - An agent resumed with SendMessage appends to its own transcript: one
    node, its wall time spans the gap, its active time does not.

Effort, per agent, first match wins:
  recorded   the effort on its own api-request records (explicit)
  requested  the Agent call's `effort` input or meta.effort (explicit)
  inherited  its parent's effort (inferred: every subagent in the sample
             transcripts that recorded one ran at its parent's effort)
  default    Claude Code's default for its model (inferred)
  unknown    a model whose default we do not know
Thinking comes from the transcript only (parser.py docstring).
"""
from . import pricing
from .parser import clip

MAX_DEPTH = 24
MAX_NODES = 5000
EXPLICIT = ("recorded", "requested")


SUMMED = ("input", "output", "cache_read", "cache_write", "tokens", "cost", "unpriced_tokens", "output_estimated",
          "est_msgs", "msgs", "tool_calls", "errors", "active")


def _blank():
    """Own totals of a node with no transcript. "tokens" is new tokens; cache_read stays apart."""
    return {k: (0.0 if k in ("cost", "active") else 0) for k in SUMMED}


def _own(a):
    t = a.totals()
    return {"input": t["input"], "output": t["output"], "cache_read": t["cache_read"],
            "cache_write": t["cache_write"], "tokens": t["tokens"], "cost": t["cost"],
            "unpriced_tokens": t["unpriced_tokens"], "msgs": t["msgs"], "tool_calls": a.tool_calls,
            "errors": a.tool_errors + a.api_errors, "active": a.active_secs,
            "output_estimated": t["output_estimated"], "est_msgs": t["est_msgs"], "context": t["context"]}


def _thinking(a):
    measured = sum(r[0] for r in a.think_model.values())
    out = sum(r[1] for r in a.think_model.values())
    tok = sum(r[2] for r in a.think_model.values())
    return {"blocks": a.think_blocks, "text_blocks": a.think_text_blocks, "redacted": a.think_redacted,
            "chars": a.think_chars, "tokens": tok, "measured_output": out, "measured_msgs": measured,
            "msgs": a.api_msgs, "mode": a.thinking_mode}


def _recorded_effort(a):
    """(dominant effort on this transcript's requests, {effort: messages}) or ('', {})."""
    if a.effort_msgs:
        return max(a.effort_msgs.items(), key=lambda kv: kv[1])[0], dict(a.effort_msgs)
    if a.effort:                     # a request record with no reply after it yet
        return a.effort, {}
    return "", {}


def build(store, s, now):
    """The tree payload for one session (store.Store, store.Session)."""
    nodes = {}
    order = []

    def add(n):
        if n["id"] in nodes or len(nodes) >= MAX_NODES:
            return None
        nodes[n["id"]] = n
        order.append(n["id"])
        return n

    a = s.acc
    main_state = store._state(s, now)
    add(_agent_node("main", "main", a, store._title(a), None, None, main_state or "ended", s))
    owner_of = {id(a): "main"}
    for x in s.subs:
        owner_of[id(x.acc)] = "a:" + x.agent_id
    spawn_owner = {}                 # spawn tool_use id -> owner node id
    run_owner = {}                   # workflow run id -> (owner node id, spawn)
    for acc in [a] + [x.acc for x in s.subs]:
        for sp in acc.spawns:
            spawn_owner[sp["id"]] = owner_of.get(id(acc))
            if sp.get("run_id"):
                run_owner[sp["run_id"]] = (owner_of.get(id(acc)), sp)

    # Subagent and workflow-agent nodes.
    parent = {}
    linked_spawns = set()
    runs = set(s.workflows) | set(s.journals)
    for x in s.subs:
        nid = "a:" + x.agent_id
        j = s.journals.get(x.run_id) if x.run_id else None
        label = x.meta.get("description") or (j.label.get(x.agent_id) if j else "") or x.acc.first_prompt or x.agent_id
        n = add(_agent_node(nid, "agent", x.acc, label, x, x.spawn, x.status, s, j))
        if n is None:
            continue
        if x.run_id:
            runs.add(x.run_id)
            parent[nid], n["link"] = "w:" + x.run_id, "workflow"
            continue
        p = owner_of.get(id(x.parent_acc)) if x.parent_acc is not None else None
        if p is not None and x.spawn is not None:
            linked_spawns.add(x.spawn["id"])
            n["link"] = "spawn"
        pid = x.meta.get("parentAgentId")
        if p is None and isinstance(pid, str) and pid and ("a:" + pid) in owner_of.values():
            p, n["link"] = "a:" + pid, "meta"
        if p is None or p == nid:
            p, n["link"] = "main", "orphan"
        parent[nid] = p

    # Workflow runs: a node each, under whoever made the Workflow call.
    for r in sorted(runs):
        info = s.workflows.get(r) or {}
        owner, sp = run_owner.get(r, (None, None))
        if sp is not None:
            linked_spawns.add(sp["id"])
        n = add(_workflow_node(r, info, s.journals.get(r), sp))
        if n is None:
            continue
        n["link"] = "spawn" if owner else "orphan"
        parent[n["id"]] = owner or "main"

    # Spawn calls that left no transcript (refused, failed at once, or not written yet).
    for acc in [a] + [x.acc for x in s.subs]:
        for sp in acc.spawns:
            if sp["id"] in linked_spawns or sp["kind"] != "agent" or sp.get("run_id"):
                continue
            if any(x.parent_tool == sp["id"] for x in s.subs):
                continue
            n = add(_missing_node(sp))
            if n is not None:
                parent[n["id"]] = owner_of.get(id(acc)) or "main"
                n["link"] = "spawn"

    # Children lists, then a bounded, cycle-safe walk from the main thread.
    kids = {nid: [] for nid in nodes}
    for nid, p in parent.items():
        if p not in nodes:
            p = parent[nid] = "main"
            nodes[nid]["link"] = "orphan"
        kids[p].append(nid)
    start = lambda nid: (nodes[nid]["start"] or 0, nid)   # noqa: E731
    for k in kids.values():
        k.sort(key=start)
    seen, out = set(), []

    def walk(root):
        stack = [(root, 0, 0)]
        while stack:
            nid, depth, level = stack.pop()
            if nid in seen:
                continue
            seen.add(nid)
            n = nodes[nid]
            n["depth"], n["level"] = depth, level
            out.append(nid)
            ch = [c for c in kids[nid] if c not in seen]
            if depth >= MAX_DEPTH - 1:
                # Flatten: every remaining descendant hangs directly under this node.
                flat, q = [], list(ch)
                while q:
                    c = q.pop(0)
                    if c in seen or c in flat:
                        continue
                    flat.append(c)
                    q.extend(kids[c])
                for c in flat:
                    nodes[c]["deep"] = True
                    parent[c] = nid
                kids[nid] = flat
                for c in flat:
                    kids[c] = []
                ch = flat
            step = 0 if n["kind"] == "workflow" else 1    # a workflow's agents sit at the workflow's level
            for c in reversed(ch):
                stack.append((c, depth + 1, level + step))
    walk("main")
    for nid in order:                # caught in a cycle: re-hang under the main thread
        if nid not in seen:
            nodes[nid]["link"] = "cycle"
            old = parent.get(nid)
            if old in kids and nid in kids[old]:
                kids[old].remove(nid)
            parent[nid] = "main"
            kids["main"].append(nid)
            walk(nid)
            nodes[nid]["depth"] = 1
            _redepth(nodes, kids, nid, 1, 1)

    # Subtree totals, children first.
    for nid in reversed(out):
        n = nodes[nid]
        sub = dict(n["own"])
        sub["agents"] = 1 if n["kind"] == "agent" and not n.get("missing") else 0
        sub["start"], sub["end"] = n["start"], n["end"]
        for c in kids[nid]:
            cs = nodes[c]["sub"]
            for k in SUMMED + ("agents",):
                sub[k] += cs[k]
            if cs["start"] and (not sub["start"] or cs["start"] < sub["start"]):
                sub["start"] = cs["start"]
            if cs["end"] and (not sub["end"] or cs["end"] > sub["end"]):
                sub["end"] = cs["end"]
        sub["cost"] = round(sub["cost"], 6)
        sub["active"] = round(sub["active"], 1)
        sub["wall"] = max(0.0, (sub["end"] or 0) - (sub["start"] or 0)) if sub["start"] else 0.0
        n["sub"] = sub
        n["children"] = list(kids[nid])
        n["parent"] = parent.get(nid)

    _efforts(nodes, out, parent)
    rows = [nodes[nid] for nid in out]
    for n in rows:
        n["own"]["cost"] = round(n["own"]["cost"], 6)
        n["own"]["active"] = round(n["own"]["active"], 1)
    return {"session": s.sid, "now": now, "nodes": rows, "summary": summarize(rows),
            "truncated": len(nodes) >= MAX_NODES, "max_depth_bound": MAX_DEPTH}


def _redepth(nodes, kids, nid, depth, level):
    stack, seen = [(nid, depth, level)], set()
    while stack:
        c, d, lv = stack.pop()
        if c in seen:
            continue
        seen.add(c)
        nodes[c]["depth"], nodes[c]["level"] = d, lv
        for k in kids.get(c, []):
            stack.append((k, d + 1, lv + (0 if nodes[c]["kind"] == "workflow" else 1)))


def _agent_node(nid, kind, a, label, x, sp, status, s, j=None):
    m = a.model_main()
    models = [pricing.display_name(k) for k, _ in sorted(a.models.items(), key=lambda kv: -kv[1]) if k and not k.startswith("<")]
    rec_eff, rec_msgs = _recorded_effort(a)
    meta = x.meta if x is not None else {}
    requested = (sp or {}).get("effort_requested") or (meta.get("effort") if isinstance(meta.get("effort"), str) else "")
    n = {
        "id": nid, "kind": kind, "label": clip(label, 200), "agent_id": x.agent_id if x is not None else None,
        "agent_type": (meta.get("agentType") or ("workflow-subagent" if x is not None and x.run_id else "subagent")) if x is not None else "main",
        "run_id": x.run_id if x is not None else "", "phase": (meta.get("workflowPhase") or (j.phase.get(x.agent_id) if j else "")) if x is not None else "",
        "status": status, "background": bool(meta.get("requestShape") == "background" or (sp or {}).get("background")),
        "model": pricing.display_name(m) if m else "", "family": pricing.family(m) if m else "other", "model_id": pricing.normalize(m),
        "models": models, "model_switches": len(a.model_log),
        "model_requested": (sp or {}).get("model_requested") or meta.get("model") or "",
        "effort_recorded": rec_eff, "effort_msgs": rec_msgs, "effort_requested": clip(requested, 20),
        "effort_log": a.effort_log[-20:],
        "thinking": _thinking(a), "fast_msgs": a.fast_msgs,
        "start": a.first_ts, "end": a.last_ts,
        "own": _own(a), "spawn": _spawn_brief(sp), "link": "root" if kind == "main" else "",
        "think_model": {pricing.display_name(k): v[:] for k, v in a.think_model.items() if k},
    }
    n["own"]["wall"] = max(0.0, (a.last_ts or 0) - (a.first_ts or 0)) if a.first_ts else 0.0
    return n


def _spawn_brief(sp):
    if not sp:
        return None
    return {"id": sp.get("id"), "tool": sp.get("tool"), "t": sp.get("t"), "description": sp.get("description"),
            "subagent_type": sp.get("subagent_type"), "model_requested": sp.get("model_requested") or "",
            "effort_requested": sp.get("effort_requested") or "", "background": bool(sp.get("background")),
            "status": sp.get("status")}


def _workflow_node(run, info, j, sp):
    st = info.get("status") or ""
    outcomes = list(j.outcome.values()) if j else []
    if st in ("completed", "done"):
        status = "done"
    elif st == "failed" or (outcomes and all(o in ("done", "failed") for o in outcomes) and "failed" in outcomes):
        status = "failed"
    elif outcomes and any(o == "running" for o in outcomes):
        status = "running"
    elif outcomes:
        status = "done"
    else:
        status = st or ((sp or {}).get("status") or "")
    phases = [p.get("title") for p in (info.get("phases") or []) if isinstance(p, dict) and p.get("title")]
    n = {
        "id": "w:" + run, "kind": "workflow", "label": clip(info.get("workflowName") or (sp or {}).get("description") or run, 200),
        "agent_id": None, "agent_type": "workflow", "run_id": run, "phase": "", "status": status, "background": True,
        "model": pricing.display_name(info["defaultModel"]) if info.get("defaultModel") else "",
        "family": pricing.family(info["defaultModel"]) if info.get("defaultModel") else "other", "model_id": "",
        "models": [], "model_switches": 0, "model_requested": "", "effort_recorded": "", "effort_msgs": {},
        "effort_requested": "", "effort_log": [], "thinking": None, "fast_msgs": 0,
        "start": (sp or {}).get("t"), "end": None, "own": _blank(), "spawn": _spawn_brief(sp), "link": "",
        "phases": phases, "agent_count": info.get("agentCount") or (len(outcomes) if outcomes else None),
        "duration_ms": info.get("durationMs"), "think_model": {},
    }
    n["own"]["wall"] = 0.0
    return n


def _missing_node(sp):
    st = sp.get("status") or ""
    n = _workflow_node("", {}, None, sp)
    n.update(id="m:" + (sp.get("id") or ""), kind="agent", missing=True, label=clip(sp.get("description") or "(no description)", 200),
             agent_type=sp.get("subagent_type") or "subagent", run_id="", background=bool(sp.get("background")),
             status="failed" if st == "error" else ("done" if st == "completed" else "missing"),
             model="", family="other", model_requested=sp.get("model_requested") or "",
             effort_requested=sp.get("effort_requested") or "", start=sp.get("t"), end=sp.get("t"), error=sp.get("error") or "")
    return n


def _efforts(nodes, order, parent):
    """Resolve each agent's effort top-down (order is a pre-order walk, parents first)."""
    for nid in order:
        n = nodes[nid]
        p = nodes.get(parent.get(nid)) if parent.get(nid) else None
        while p is not None and p["kind"] == "workflow":      # a workflow passes its caller's effort through
            p = nodes.get(parent.get(p["id"])) if parent.get(p["id"]) else None
        pe = p.get("effort") if p is not None else None
        if n["kind"] == "workflow":
            n["effort"] = None
            continue
        if n["effort_recorded"]:
            e = {"value": n["effort_recorded"], "source": "recorded"}
            if n["effort_requested"] and n["effort_requested"] != n["effort_recorded"]:
                e["requested"] = n["effort_requested"]
        elif n["effort_requested"]:
            e = {"value": n["effort_requested"], "source": "requested"}
        elif pe and pe.get("value") and pe["source"] in ("recorded", "requested", "inherited") and n["kind"] != "main":
            # Only an effort that traces back to a recorded or requested one passes down; under a
            # parent at its model default the child is at its own model's default.
            e = {"value": pe["value"], "source": "inherited"}
        else:
            d = pricing.default_effort(n.get("model_id") or "")
            e = {"value": d, "source": "default"} if d else {"value": "", "source": "unknown"}
        e["explicit"] = e["source"] in EXPLICIT
        n["effort"] = e


def summarize(rows):
    """Session-level roll-ups for the KPI strip and the charts."""
    agents = [n for n in rows if n["kind"] in ("agent", "main") and not n.get("missing")]
    root = rows[0] if rows else None
    total_cost = root["sub"]["cost"] if root else 0.0
    sub_cost = total_cost - (root["own"]["cost"] if root else 0.0)
    levels = {}
    matrix = {}
    think = {}
    efforts = {}
    for n in agents:
        lv = levels.setdefault(n["level"], {"level": n["level"], "agents": 0, "cost": 0.0, "tokens": 0, "cache_read": 0,
                                            "fam_cost": {}, "fam_tokens": {}, "fam_cache": {}})
        lv["agents"] += 1
        lv["cost"] += n["own"]["cost"]
        lv["tokens"] += n["own"]["tokens"]
        f = n["family"]
        lv["fam_cost"][f] = lv["fam_cost"].get(f, 0.0) + n["own"]["cost"]
        lv["fam_tokens"][f] = lv["fam_tokens"].get(f, 0) + n["own"]["tokens"]
        lv["cache_read"] += n["own"]["cache_read"]
        lv["fam_cache"][f] = lv["fam_cache"].get(f, 0) + n["own"]["cache_read"]
        e = n.get("effort") or {"value": "", "source": "unknown", "explicit": False}
        ev = e["value"] or "unknown"
        key = (n["model"] or "No API call", ev)
        cell = matrix.setdefault(key, {"model": key[0], "family": f, "effort": ev,
                                        "explicit": {"agents": 0, "cost": 0.0}, "inferred": {"agents": 0, "cost": 0.0}})
        side = cell["explicit" if e["explicit"] else "inferred"]
        side["agents"] += 1
        side["cost"] += n["own"]["cost"]
        efforts.setdefault(ev, {"effort": ev, "recorded": 0, "requested": 0, "inherited": 0, "default": 0, "unknown": 0})[e["source"]] += 1
        th = n.get("thinking") or {}
        for model, (msgs, out, tok) in (n.get("think_model") or {}).items():
            t = think.setdefault((model, ev), {"model": model, "family": pricing.family(model), "effort": ev, "explicit": 0,
                                               "agents": 0, "measured_msgs": 0, "output": 0, "thinking": 0})
            t["measured_msgs"] += msgs
            t["output"] += out
            t["thinking"] += tok
            t["agents"] += 1
            t["explicit"] += 1 if e["explicit"] else 0
        tb = think.setdefault(("", ""), {"blocks": 0, "text_blocks": 0, "chars": 0, "msgs": 0, "measured_msgs": 0})
        tb["blocks"] += th.get("blocks", 0)
        tb["text_blocks"] += th.get("text_blocks", 0)
        tb["chars"] += th.get("chars", 0)
        tb["msgs"] += th.get("msgs", 0)
        tb["measured_msgs"] += th.get("measured_msgs", 0)
    for lv in levels.values():
        lv["cost"] = round(lv["cost"], 6)
    for c in matrix.values():
        for side in ("explicit", "inferred"):
            c[side]["cost"] = round(c[side]["cost"], 6)
    blocks = think.pop(("", ""), {"blocks": 0, "text_blocks": 0, "chars": 0, "msgs": 0, "measured_msgs": 0})
    return {
        "agents": sum(1 for n in agents if n["kind"] == "agent"),
        "agents_all": len(agents),
        "max_depth": max((n["level"] for n in agents), default=0),
        "workflows": sum(1 for n in rows if n["kind"] == "workflow"),
        "missing": sum(1 for n in rows if n.get("missing")),
        "orphans": sum(1 for n in rows if n.get("link") in ("orphan", "cycle")),
        "cost": round(total_cost, 6), "sub_cost": round(sub_cost, 6),
        "output_estimated": root["sub"]["output_estimated"] if root else 0, "est_msgs": root["sub"]["est_msgs"] if root else 0,
        "sub_share": round(sub_cost / total_cost, 4) if total_cost > 0 else 0.0,
        "levels": [levels[k] for k in sorted(levels)],
        "matrix": sorted(matrix.values(), key=lambda c: (c["model"], c["effort"])),
        "efforts": sorted(efforts.values(), key=lambda r: r["effort"]),
        "thinking": sorted(think.values(), key=lambda r: (-r["output"], r["model"], r["effort"])),
        "thinking_blocks": blocks,
    }


def session_effort(s, tree_root=None):
    """What the session header shows about effort, thinking and speed (main thread only)."""
    a = s.acc
    e = (tree_root or {}).get("effort") or {}
    cmds = [{"t": ev.get("t"), "text": ev.get("text")} for ev in a.events
            if ev.get("k") == "command" and isinstance(ev.get("text"), str) and ev["text"].split(" ")[0] in ("/effort", "/fast", "/model")]
    return {"value": e.get("value") or "", "source": e.get("source") or "unknown", "explicit": bool(e.get("explicit")),
            "log": a.effort_log[-50:], "thinking_mode": a.thinking_mode, "fast_msgs": a.fast_msgs,
            "msgs": a.api_msgs, "model_log": [{"t": m["t"], "from": pricing.display_name(m["from"]), "to": pricing.display_name(m["to"])}
                                               for m in a.model_log[-50:]],
            "commands": cmds[-50:]}
