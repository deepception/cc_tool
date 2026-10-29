"""Routing: who chose each subagent's model and effort, what it cost, and where that diverges
from the managed block's "Model routing" rules (templates/CLAUDE_snippet.md).

Served as /api/routing?project= and drawn as the Overview's "Routing" panel. Read-only; it
only re-reads what parser.Acc already holds.

What is measured, per subagent and workflow agent with a reply in the window:
  model        the model that ran most of its replies
  model source explicit   the Agent call named `model` (spawn input, or meta.model)
               workflow   the workflow's agent() opts named it (meta.model on a workflow agent)
               type       the agent type's definition sets `model:` (~/.claude/agents or the
                          project's .claude/agents) and the call named none
               default    nothing named a model: it ran on whatever the harness picked
  effort       the effort the replies were sent with (api-request records, parser.Acc.msg_eff);
               "named" when the Agent call or meta carried an `effort`, "inherited" when not
  cost         new tokens (input + cache writes + output) and cache reads, priced as everywhere
  rewrites     cache-expiry rewrites: a reply whose cache write is >= 50K tokens and >= 50% of its
               context, sent after an idle gap longer than the cache TTL (5 minutes for a
               subagent, 1 hour for a main session). The cost counted is that write. A big write
               after a shorter gap (compaction, an edited prefix) is kept apart as "other".
  context      the largest prompt (input + cache read + cache write); cost of replies over 300K

Work type is a heuristic (ported from the usage analysis; rules in order, first match wins):
  features  E  edited a file, or ran a Bash command that changes files or repo state
            T  ran a test, lint or type check
            J  the description (or the brief, for rule 5) names judgment work: review, verify, audit,
               gate, root cause, debug, plan, design, architect, research, assess, evaluate,
               critique, security, adversarial, decide, propose, investigate, analyse, compare,
               strategy, brainstorm, triage...
            M  the description or brief names a mechanical sweep: find, locate, search, list, extract,
               summarise, inventory, catalog, collect, scan, map, survey, fetch, classify, count,
               read, grep, enumerate, gather...
            X  it names a change: implement, fix, add, update, rewrite, migrate, rename, apply,
               refactor, port, replace, step or task N, per plan, bump, convert, build, create...
  0 it spawned agents itself, or its description says executor, orchestrator or lead -> judgment
  1 agent type Explore and not E                                         -> mechanical
  2 E and the description STARTS with a spec verb (Implement, Task N, Fix, Apply, an ID
    like "F2:")                                                          -> execution
  3 the description matches J                                            -> judgment
  4 E and X                                                              -> execution
  5 E                                                                    -> execution
  6 the brief matches J                                                  -> judgment
  7 not E and M and not T                                                -> mechanical
  8 not E and T                                                          -> execution (runs checks)
  9 otherwise                                                            -> judgment (ambiguous goes to Opus)
It was checked by hand on 15 agents: 12 right, 1 borderline, 2 wrong, errors in both directions.

Divergences ("flags"), against the managed block:
  no_model            nothing named a model (source default): the block says name it on every call
  execution_opus      spec'd execution on Opus: the block routes it to Sonnet
  mechanical_opus     read-only mechanical work on Opus: the block routes it to Haiku
  mechanical_sonnet   the same on Sonnet, with a context under 100K (above that Haiku 5.5 changes tier)
An estimate of what re-routing would save re-prices the same tokens at the target model.
"""
import os
import re
import threading

from . import pricing
from .parser import clip

CLASSES = ("judgment", "execution", "mechanical")
CLASS_LABEL = {"judgment": "Judgment", "execution": "Spec'd execution", "mechanical": "Read-only mechanical"}
SOURCES = ("explicit", "workflow", "type", "default")
TTL_MAIN = 3600
TTL_SUB = 300
REWRITE_MIN = 50_000
REWRITE_SHARE = 0.5
BIG_CTX = 300_000
HAIKU_CTX = 100_000
TOP = 5

JUDGE_RE = re.compile(r"\b(review|re-review|verif|audit|judge|gate|root[- ]?cause|debug|diagnos|plan(ner|ning)?\b|design|architect|"
                      r"research|assess|evaluat|critique|critic|security|adversar|decide|decision|propos|recalibrat|why\b|"
                      r"investigat|analy[sz]|compare|tradeoff|trade-off|strategy|brainstorm|red[- ]team|triage)", re.I)
MECH_RE = re.compile(r"\b(find|locat|search|list|extract|summari[sz]|inventor|catalog|collect|scan|map\b|survey|fetch|"
                     r"classif|count|read\b|where|grep|dump|enumerat|gather|lookup|look up|scrape|explore|check (for|whether))", re.I)
EXEC_RE = re.compile(r"\b(implement|fix|add|update|rewrite|migrat|renam|apply|refactor|port\b|replace|record|write tests|"
                     r"(step|task|batch|wave|phase) ?\d|per (the )?plan|bump|convert|build|create|remove|edit|patch|wire|polish|translat)", re.I)
SPEC_START = re.compile(r"(implement|task \d|fix\b|apply|migrat|renam|rewire|port\b|wire|add\b|update|rewrite|replace|convert|bump|translat|[A-Z]{1,3}\d+[a-z]?:)", re.I)
ORCH_RE = re.compile(r"\b(executor|orchestrat|lead\b)", re.I)
SPAWN_TOOLS = ("Agent", "Task", "Workflow")

# message tuple: (ts, family, cost, cread_cost, effort, ctx, rewrite_cost, other_big_write_cost, cost_over_300k,
#                 as_sonnet, as_haiku, new_tokens, cache_read_tokens)
_TS, _FAM, _COST, _CREAD, _EFF, _CTX, _RW, _BIG, _C300, _SON, _HAI, _NEW, _CRT = range(13)


def classify(desc, agent_type, brief, tools, edited, mutated, tested, spawned):
    """(work type, rule number) for one agent; the rules are in the module docstring."""
    desc = desc or ""
    brief = brief or ""
    text = desc + " \n " + brief
    e = bool(edited) or bool(mutated)
    t = bool(tested)
    jd = bool(JUDGE_RE.search(desc))
    j = jd or bool(JUDGE_RE.search(brief))
    m = bool(MECH_RE.search(text))
    x = bool(EXEC_RE.search(text))
    if spawned or any(n in tools for n in SPAWN_TOOLS) or ORCH_RE.search(desc):
        return "judgment", 0
    if agent_type == "Explore" and not e:
        return "mechanical", 1
    if e and SPEC_START.match(desc.strip()):
        return "execution", 2
    if jd:
        return "judgment", 3
    if e and x:
        return "execution", 4
    if e:
        return "execution", 5
    if j:
        return "judgment", 6
    if m and not t:
        return "mechanical", 7
    if t:
        return "execution", 8
    return "judgment", 9


_def_cache = {}
_def_lock = threading.Lock()


def _def_model(path):
    """The `model:` of an agent definition file ('' when absent or "inherit")."""
    try:
        mt = os.stat(path).st_mtime_ns
    except OSError:
        return ""
    with _def_lock:
        hit = _def_cache.get(path)
        if hit and hit[0] == mt:
            return hit[1]
    model = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            if fh.readline().strip() == "---":
                for _ in range(60):
                    line = fh.readline()
                    if not line or line.strip() == "---":
                        break
                    k, _, v = line.partition(":")
                    if k.strip().lower() == "model":
                        model = v.strip().strip("'\"")
                        break
    except OSError:
        model = ""
    if model.lower() in ("inherit", ""):
        model = ""
    with _def_lock:
        if len(_def_cache) > 200:
            _def_cache.clear()
        _def_cache[path] = (mt, model)
    return model


def type_model(config_root, project_root, agent_type):
    """The model an agent type's definition sets, or ''. Built-in types and unknown names have none."""
    if not agent_type or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,80}", agent_type):
        return ""
    dirs = []
    if project_root:
        dirs.append(os.path.join(project_root, ".claude", "agents"))
    dirs.append(os.path.join(config_root, "agents"))
    for d in dirs:
        m = _def_model(os.path.join(d, agent_type + ".md"))
        if m:
            return m
    return ""


def model_source(x, config_root, project_root):
    """(source, effort named?) for one Sub (store.Sub): see the module docstring."""
    meta = x.meta or {}
    sp = x.spawn or {}
    named_effort = bool(sp.get("effort_requested") or (isinstance(meta.get("effort"), str) and meta.get("effort")))
    if x.run_id:
        return ("workflow" if meta.get("model") else "default"), named_effort
    if sp.get("model_requested") or meta.get("model"):
        return "explicit", named_effort
    at = meta.get("agentType") or sp.get("subagent_type") or ""
    if type_model(config_root, project_root, at):
        return "type", named_effort
    return "default", named_effort


def profile(acc, role):
    """Per-reply facts of one transcript, in order. role 'main' uses the 1-hour cache TTL."""
    ttl = TTL_MAIN if role == "main" else TTL_SUB
    out = []
    prev_ts = None
    first = True
    for mid in acc.msg_order:
        mu = acc.msg_usage.get(mid)
        ts = acc.msg_ts.get(mid)
        if mu is None or not ts:
            continue
        model, (inp, o, cr, cw5, cw1h), cost, _hour = mu
        cost = cost or 0.0
        cw = cw5 + cw1h
        ctx = inp + cr + cw
        gap = (ts - prev_ts) if prev_ts else 0.0
        rw = big = 0.0
        if not first and cw >= REWRITE_MIN and cw >= REWRITE_SHARE * ctx:
            wc = pricing.cache_write_cost(model, inp, cr, cw5, cw1h) or 0.0
            if gap > ttl:
                rw = wc
            else:
                big = wc
        out.append((ts, pricing.family(model), cost, pricing.message_cost(model, 0, 0, cr, 0, 0) or 0.0,
                    acc.msg_eff.get(mid, ""), ctx, rw, big, cost if ctx > BIG_CTX else 0.0,
                    pricing.message_cost("claude-sonnet-5-5", inp, o, cr, cw5, cw1h) or 0.0,
                    pricing.message_cost("claude-haiku-5-5", inp, o, cr, cw5, cw1h) or 0.0,
                    inp + o + cw, cr))
        prev_ts = ts
        first = False
    return out


def _window(msgs, cutoff):
    """Sums of the replies inside the window."""
    r = {"msgs": 0, "cost": 0.0, "cread_cost": 0.0, "new_tokens": 0, "cache_read": 0, "max_ctx": 0,
         "rewrites": 0, "rewrite_cost": 0.0, "other_writes": 0, "other_write_cost": 0.0,
         "cost_300k": 0.0, "as_sonnet": 0.0, "as_haiku": 0.0, "by_effort": {}, "by_family": {}}
    for m in msgs:
        if m[_TS] < cutoff:
            continue
        r["msgs"] += 1
        r["cost"] += m[_COST]
        r["cread_cost"] += m[_CREAD]
        r["new_tokens"] += m[_NEW]
        r["cache_read"] += m[_CRT]
        if m[_CTX] > r["max_ctx"]:
            r["max_ctx"] = m[_CTX]
        if m[_RW]:
            r["rewrites"] += 1
            r["rewrite_cost"] += m[_RW]
        elif m[_BIG]:
            r["other_writes"] += 1
            r["other_write_cost"] += m[_BIG]
        r["cost_300k"] += m[_C300]
        r["as_sonnet"] += m[_SON]
        r["as_haiku"] += m[_HAI]
        e = m[_EFF] or ""
        be = r["by_effort"].setdefault(m[_FAM], {})
        be[e] = be.get(e, 0.0) + m[_COST]
        r["by_family"][m[_FAM]] = r["by_family"].get(m[_FAM], 0.0) + m[_COST]
    return r


def _flags(cls, fam, source, max_ctx):
    """(reasons, class-level divergence?) for one agent."""
    why = []
    div = False
    if source == "default":
        why.append("no_model")
    if cls == "execution" and fam == "opus":
        why.append("execution_opus")
        div = True
    elif cls == "mechanical" and fam == "opus":
        why.append("mechanical_opus")
        div = True
    elif cls == "mechanical" and fam == "sonnet" and max_ctx <= HAIKU_CTX:
        why.append("mechanical_sonnet")
        div = True
    return why, div


def _saving(cls, fam, w):
    """Estimated cost saved by running the same tokens on the model the block names, or 0."""
    if cls == "execution" and fam == "opus":
        return max(0.0, w["cost"] - w["as_sonnet"])
    if cls == "mechanical" and fam == "opus":
        return max(0.0, w["cost"] - (w["as_haiku"] if w["max_ctx"] <= HAIKU_CTX else w["as_sonnet"]))
    if cls == "mechanical" and fam == "sonnet" and w["max_ctx"] <= HAIKU_CTX:
        return max(0.0, w["cost"] - w["as_haiku"])
    return 0.0


def _share(a, b):
    return round(a / b, 4) if b else 0.0


def build(store, project, now, cache, days=None):
    """The routing payload for one scope ('' = every project) over a `days` window (None: the
    store's launch window). `cache` is the Store's profile cache. Called under store.lock."""
    days = store.days if days is None else days
    cutoff = now - days * 86400
    sessions = store.scope_sessions(project, days, now)
    agents = []
    main = {"count": 0, "cost": 0.0, "spend": 0.0, "other_count": 0, "other_cost": 0.0}

    def prof(acc, role):
        key = (acc.path, role)
        sig = (acc.api_msgs, len(acc.msg_usage), acc.last_ts, acc.effort)
        hit = cache.get(key)
        if hit and hit[0] == sig:
            return hit[1]
        p = profile(acc, role)
        cache[key] = (sig, p)
        return p

    for s in sessions:
        w = _window(prof(s.acc, "main"), cutoff)
        main["spend"] += w["cost"]
        main["count"] += w["rewrites"]
        main["cost"] += w["rewrite_cost"]
        main["other_count"] += w["other_writes"]
        main["other_cost"] += w["other_write_cost"]
        for x in s.subs:
            w = _window(prof(x.acc, "sub"), cutoff)
            if not w["msgs"]:
                continue
            meta = x.meta or {}
            sp = x.spawn or {}
            at = meta.get("agentType") or sp.get("subagent_type") or ("workflow-subagent" if x.run_id else "general-purpose")
            j = s.journals.get(x.run_id) if x.run_id else None
            label = (meta.get("description") or (j.label.get(x.agent_id) if j else "") or x.acc.first_prompt or x.agent_id)
            desc = (meta.get("description") or "") + " " + (meta.get("workflowPhase") or "")
            if not desc.strip() and j:
                desc = j.label.get(x.agent_id, "") + " " + j.phase.get(x.agent_id, "")
            a = x.acc
            cls, rule = classify(desc, at, a.first_prompt, a.tools, a.edited, a.bash_mutate, a.bash_tests,
                                 bool(a.spawns))
            fam = max(w["by_family"].items(), key=lambda kv: kv[1])[0] if w["by_family"] else "other"
            mid = a.model_main()
            source, named_eff = model_source(x, store.root, s.root)
            why, div = _flags(cls, fam, source, w["max_ctx"])
            effs = {}
            for e, c in (w["by_effort"].get(fam) or {}).items():
                effs[e] = effs.get(e, 0.0) + c
            eff = max(effs.items(), key=lambda kv: kv[1])[0] if effs else ""
            agents.append({"sid": s.sid, "aid": x.agent_id, "project": s.label, "label": clip(label, 120), "type": at,
                           "cls": cls, "rule": rule, "family": fam, "model": pricing.display_name(mid) if mid else "",
                           "source": source, "effort": eff, "effort_named": named_eff, "flags": why, "diverges": div,
                           "save": round(_saving(cls, fam, w), 4), "w": w})

    def r4(v):
        return round(v, 4)

    # cost by work type x model family
    fams = sorted({a["family"] for a in agents}, key=lambda f: ("opus", "sonnet", "haiku", "fable", "other").index(f))
    classes = []
    for c in CLASSES:
        row = {"cls": c, "label": CLASS_LABEL[c], "agents": 0, "cost": 0.0, "cells": []}
        for f in fams:
            xs = [a for a in agents if a["cls"] == c and a["family"] == f]
            fl = [a for a in xs if a["diverges"]]
            row["cells"].append({"family": f, "agents": len(xs), "cost": r4(sum(a["w"]["cost"] for a in xs)),
                                 "flagged_agents": len(fl), "flagged_cost": r4(sum(a["w"]["cost"] for a in fl)),
                                 "save": r4(sum(a["save"] for a in fl))})
        row["agents"] = sum(x["agents"] for x in row["cells"])
        row["cost"] = r4(sum(x["cost"] for x in row["cells"]))
        classes.append(row)

    total_cost = sum(a["w"]["cost"] for a in agents)
    dflt = [a for a in agents if a["source"] == "default"]
    dflt_opus = [a for a in dflt if a["family"] == "opus"]
    sources = []
    for sname in SOURCES:
        xs = [a for a in agents if a["source"] == sname]
        sources.append({"source": sname, "agents": len(xs), "cost": r4(sum(a["w"]["cost"] for a in xs))})

    # Opus agent cost by effort, and how much of it the call asked for
    levels = {}
    for a in agents:
        for e, c in (a["w"]["by_effort"].get("opus") or {}).items():
            lv = levels.setdefault(e or "unrecorded", {"effort": e or "unrecorded", "cost": 0.0, "named_cost": 0.0})
            lv["cost"] += c
            if a["effort_named"]:
                lv["named_cost"] += c
    opus_cost = sum(v["cost"] for v in levels.values())
    order = {"low": 0, "medium": 1, "high": 2, "xhigh": 3, "max": 4}
    lv_rows = sorted(({"effort": v["effort"], "cost": r4(v["cost"]), "named_cost": r4(v["named_cost"]),
                       "share": _share(v["cost"], opus_cost)} for v in levels.values()),
                     key=lambda v: (order.get(v["effort"], 9), v["effort"]))
    opus_agents = [a for a in agents if a["family"] == "opus"]

    sub_rw = [a["w"] for a in agents]
    big = [a for a in agents if a["w"]["max_ctx"] > BIG_CTX]
    flagged = [a for a in agents if a["flags"]]
    flagged.sort(key=lambda a: (-a["save"], -a["w"]["cost"]))
    top = [{"sid": a["sid"], "aid": a["aid"], "project": a["project"], "label": a["label"], "cls": a["cls"],
            "family": a["family"], "model": a["model"], "source": a["source"], "effort": a["effort"],
            "effort_named": a["effort_named"], "flags": a["flags"], "cost": r4(a["w"]["cost"]), "save": a["save"],
            "max_ctx": a["w"]["max_ctx"]} for a in flagged[:TOP]]
    # Prune only the profiles of transcripts the store no longer holds at all: a profile outside this
    # request's window or scope may be another page's, on another window.
    keep = set()
    for s in store.held.values():
        keep.add((s.acc.path, "main"))
        keep.update((x.acc.path, "sub") for x in s.subs)
    for k in [k for k in cache if k not in keep]:
        del cache[k]
    return {
        "now": now, "days": days, "cutoff": cutoff, "scope": {"project": project},
        "agents": len(agents), "cost": r4(total_cost),
        "new_tokens": sum(a["w"]["new_tokens"] for a in agents), "cache_read": sum(a["w"]["cache_read"] for a in agents),
        "cache_read_cost": r4(sum(a["w"]["cread_cost"] for a in agents)),
        "families": fams, "classes": classes,
        "default": {"agents": len(dflt), "cost": r4(sum(a["w"]["cost"] for a in dflt)), "share_agents": _share(len(dflt), len(agents)),
                    "share_cost": _share(sum(a["w"]["cost"] for a in dflt), total_cost),
                    "opus_agents": len(dflt_opus), "opus_cost": r4(sum(a["w"]["cost"] for a in dflt_opus))},
        "sources": sources,
        "effort": {"opus_cost": r4(opus_cost), "opus_agents": len(opus_agents), "levels": lv_rows,
                   "named_agents": sum(1 for a in opus_agents if a["effort_named"]),
                   "named_cost": r4(sum(v["named_cost"] for v in levels.values()))},
        "rewrites": {"sub": {"count": sum(w["rewrites"] for w in sub_rw), "cost": r4(sum(w["rewrite_cost"] for w in sub_rw)),
                             "spend": r4(total_cost), "share": _share(sum(w["rewrite_cost"] for w in sub_rw), total_cost),
                             "other_count": sum(w["other_writes"] for w in sub_rw),
                             "other_cost": r4(sum(w["other_write_cost"] for w in sub_rw)), "ttl": TTL_SUB},
                     "main": {"count": main["count"], "cost": r4(main["cost"]), "spend": r4(main["spend"]),
                              "share": _share(main["cost"], main["spend"]), "other_count": main["other_count"],
                              "other_cost": r4(main["other_cost"]), "ttl": TTL_MAIN}},
        "ctx300": {"agents": len(big), "cost": r4(sum(a["w"]["cost"] for a in big)),
                   "share": _share(sum(a["w"]["cost"] for a in big), total_cost),
                   "msg_cost": r4(sum(a["w"]["cost_300k"] for a in agents)), "limit": BIG_CTX},
        "flagged": {"agents": len(flagged), "cost": r4(sum(a["w"]["cost"] for a in flagged)),
                    "save": r4(sum(a["save"] for a in flagged))},
        "top": top,
    }
