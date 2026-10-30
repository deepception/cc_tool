#!/usr/bin/env python3
"""ratchet: score-gated optimization of one skill or CLAUDE.md file.

Runs headless `claude -p` sessions on fixed tasks, grades each workspace with a
check command, and keeps an edit only if it clears a noise floor. The script owns
the gates so an agent running the loop cannot skip them.

Usage (from the eval dir's repository, on a non-protected branch):
  ratchet.py baseline EVAL_DIR [--restart] split tasks, score the target and a null run
  ratchet.py round EVAL_DIR --hypothesis H score the edited target, keep or restore it
  ratchet.py test EVAL_DIR                 one held-out comparison, baseline vs winner
  ratchet.py status EVAL_DIR

EVAL_DIR holds eval.json, tasks.jsonl, fixtures and checks (all hashed, then read-only)
and writes to EVAL_DIR/runs/: state.json, results.tsv, last_run.json, transcripts.

Exit codes: 0 done, 1 refused or failed, 3 the loop must stop (see the message).
Python 3 standard library only.
"""

import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULTS = {
    "reps": 3,
    "test_reps": 5,
    "max_rounds": 6,
    "plateau": 3,
    "max_sessions": 320,
    "timeout_s": 900,
    "concurrency": 2,
    "seed": 42,
    "train_fraction": 0.5,
    "max_bytes": 0,
    "protected": [],
    "claude_args": ["--permission-mode", "acceptEdits"],
}
REQUIRED = ("target", "install_as", "model")
PROTECTED_BRANCHES = ("master", "main")
Z = 1.645  # one-sided 95%: the question is "did it improve", not "did it change"
MAX_ERROR_RATE = 0.2
MAX_RESTARTS = 2
MAX_TEST_TRIES = 2
CHECK_TIMEOUT_S = 120
# Sessions run as the user, so only what claude needs to start and log in reaches them.
SESSION_ENV = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "TERM",
    "TMPDIR",
    "CLAUDE_CONFIG_DIR",
    "ANTHROPIC_API_KEY",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "XDG_CACHE_HOME",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
)


class Refused(Exception):
    pass


class Stop(Exception):
    pass


def session_env() -> dict[str, str]:
    return {k: os.environ[k] for k in SESSION_ENV if k in os.environ}


def task_env(eval_dir) -> dict[str, str]:
    """For setup and check commands, which need no login."""
    env = session_env()
    env.pop("ANTHROPIC_API_KEY", None)
    return {**env, "EVAL_DIR": str(eval_dir)}


def git(root, *args):
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise Refused(f"git {args[0]} failed: {r.stderr.strip()}")
    return r.stdout.strip()


def load(eval_dir):
    path = eval_dir / "eval.json"
    if not path.is_file():
        raise Refused(f"no eval.json in {eval_dir}")
    cfg = {**DEFAULTS, **json.loads(path.read_text(encoding="utf-8"))}
    missing = [k for k in REQUIRED if not cfg.get(k)]
    if missing:
        raise Refused(f"eval.json is missing {', '.join(missing)}")
    tasks = [
        json.loads(line) for line in (eval_dir / "tasks.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    for t in tasks:
        if not t.get("id") or not t.get("prompt") or not t.get("check"):
            raise Refused(f"task needs id, prompt and check: {t}")
    if len({t["id"] for t in tasks}) != len(tasks):
        raise Refused("task ids must be unique")
    return cfg, {t["id"]: t for t in tasks}


def harness_hash(eval_dir):
    h = hashlib.sha256()
    for p in sorted(eval_dir.rglob("*")):
        rel = p.relative_to(eval_dir)
        if p.is_file() and rel.parts[0] != "runs" and "__pycache__" not in rel.parts:
            h.update(str(rel).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def split(cfg, tasks, old=None):
    """Guard tasks sit in both sets: they protect behaviour rather than measure a gain.

    On a restart old is the previous split: a task never crosses it, so the held-out set stays unread.
    """
    guards = sorted(i for i, t in tasks.items() if t.get("guard"))
    rest = sorted(i for i, t in tasks.items() if not t.get("guard"))
    if len(rest) < 2:
        raise Refused("need at least two non-guard tasks to split")
    old_train, old_test = old or ([], [])
    if set(guards) & (set(old_test) - set(old_train)):
        raise Refused("a held-out task cannot become a guard: guards are read in train")
    train = [i for i in rest if i in old_train]
    test = [i for i in rest if i in old_test and i not in train]
    new = [i for i in rest if i not in train and i not in test]
    random.Random(cfg["seed"]).shuffle(new)
    want = max(0, max(1, round(len(rest) * cfg["train_fraction"])) - len(train))
    train += new[:want]
    test += new[want:]
    if not train or not test:
        raise Refused("the train or held-out set is empty: add tasks")
    return train + guards, test + guards


def state_path(eval_dir):
    return eval_dir / "runs" / "state.json"


def read_state(eval_dir):
    path = state_path(eval_dir)
    if not path.is_file():
        raise Refused("no baseline yet: run `baseline` first")
    return json.loads(path.read_text(encoding="utf-8"))


def write_state(eval_dir, state):
    path = state_path(eval_dir)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(path)


def result_event(transcript):
    """The stream's last line: a session can exit 0 and still report is_error."""
    lines = transcript.read_text(encoding="utf-8").splitlines()
    try:
        last = json.loads(lines[-1]) if lines else {}
    except ValueError:
        last = {}
    return last if isinstance(last, dict) else {}


def session(cfg, eval_dir, task, text, transcript):
    """One headless session in a throwaway workspace. text None is the null run."""
    claude = shutil.which("claude")
    if not claude:
        raise Refused("claude is not on PATH")
    with tempfile.TemporaryDirectory(prefix="ratchet-") as tmp:
        work = Path(tmp) / "work"
        if task.get("fixture"):
            shutil.copytree(eval_dir / task["fixture"], work)
        else:
            work.mkdir()
        if text is not None:
            dest = work / cfg["install_as"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
        if task.get("setup"):
            s = subprocess.run(
                task["setup"],
                shell=True,
                cwd=work,
                capture_output=True,
                text=True,
                timeout=CHECK_TIMEOUT_S,
                env=task_env(eval_dir),
            )
            if s.returncode != 0:
                raise Refused(f"setup failed for {task['id']}: {s.stderr.strip()}")
        cmd = [
            claude,
            "-p",
            "--model",
            cfg["model"],
            "--output-format",
            "stream-json",
            "--verbose",
            "--setting-sources",
            "project",
            "--no-session-persistence",
            *cfg["claude_args"],
        ]
        transcript.parent.mkdir(parents=True, exist_ok=True)
        with transcript.open("w", encoding="utf-8") as out:
            try:
                env = session_env()
                r = subprocess.run(
                    cmd,
                    cwd=work,
                    input=task["prompt"],
                    text=True,
                    stdout=out,
                    env=env,
                    stderr=subprocess.DEVNULL,
                    timeout=cfg["timeout_s"],
                )
            except subprocess.TimeoutExpired:
                return {"error": True, "passed": False, "engaged": False, "cost": 0.0}
        event = result_event(transcript)
        cost = event.get("total_cost_usd") or 0.0
        if r.returncode != 0 or event.get("is_error"):
            return {"error": True, "passed": False, "engaged": False, "cost": cost}
        # EVAL_DIR reaches only the check. That hides the grading files, it does not lock them.
        # The session could have planted code in the workspace, so a check gets no more than it did.
        env = {**task_env(eval_dir), "TRANSCRIPT": str(transcript)}
        try:
            c = subprocess.run(
                task["check"], shell=True, cwd=work, env=env, capture_output=True, timeout=CHECK_TIMEOUT_S
            )
            passed = c.returncode == 0
        except subprocess.TimeoutExpired:
            return {"error": True, "passed": False, "engaged": False, "cost": cost}
    pattern = task.get("engaged")
    engaged = bool(pattern and re.search(pattern, transcript.read_text(encoding="utf-8")))
    return {"error": False, "passed": passed, "engaged": engaged, "cost": cost}


def run_pass(cfg, eval_dir, state, label, tasks, ids, text, reps):
    """Score ids at reps. Returns {id: {passed, n, errors, engaged}} from the sessions that ran."""
    state["sessions"] += len(ids) * reps
    write_state(eval_dir, state)  # counted first, so a crash cannot hide spent sessions

    def one(job):
        tid, rep = job
        path = eval_dir / "runs" / label / f"{tid}_r{rep}.jsonl"
        return tid, str(path), session(cfg, eval_dir, tasks[tid], text, path)

    jobs = [(i, r) for i in ids for r in range(reps)]
    out = {i: {"passed": 0, "n": 0, "errors": 0, "engaged": 0, "failed": []} for i in ids}
    with concurrent.futures.ThreadPoolExecutor(cfg["concurrency"]) as pool:
        for tid, path, res in pool.map(one, jobs):
            state["cost_usd"] += res["cost"]
            row = out[tid]
            if res["error"]:
                row["errors"] += 1
                continue
            row["n"] += 1
            row["passed"] += res["passed"]
            row["engaged"] += res["engaged"]
            if not res["passed"]:
                row["failed"].append(path)
    return out


def rate(row):
    return row["passed"] / row["n"] if row["n"] else 0.0


def mean(rows, ids):
    return sum(rate(rows[i]) for i in ids) / len(ids)


def noise_floor(a, b, ids):
    """Half-width on the paired difference of task pass rates, smoothed so 0/3 and 3/3 keep variance."""

    def var(row):
        n = max(row["n"], 1)
        p = (row["passed"] + 1) / (n + 2)
        return p * (1 - p) / n

    return Z * math.sqrt(sum(var(a[i]) + var(b[i]) for i in ids)) / len(ids)


def error_rate(rows):
    total = sum(r["n"] + r["errors"] for r in rows.values())
    return sum(r["errors"] for r in rows.values()) / total if total else 1.0


def guard_regressions(cfg, tasks, kept, cand, ids):
    """A guard may slip by one rep (sessions vary) but no further. kept is the baseline, so slips cannot add up."""
    slack = (1 / cfg["reps"] if cfg["reps"] > 1 else 0) + 1e-9
    return [i for i in ids if tasks[i].get("guard") and rate(cand[i]) < rate(kept[i]) - slack]


def check_harness(eval_dir, state):
    if harness_hash(eval_dir) != state["harness"]:
        raise Stop(
            "eval.json, tasks, fixtures or checks changed since baseline: scores are no longer "
            "comparable. Revert them, or commit the change and run `baseline --restart`."
        )


def check_target(cfg, root):
    text = (root / cfg["target"]).read_text(encoding="utf-8")
    lost = [p for p in cfg["protected"] if p not in text]
    if lost:
        raise Refused(f"protected text removed from the target: {lost}")
    if cfg["max_bytes"] and len(text.encode()) > cfg["max_bytes"]:
        raise Refused(f"target is {len(text.encode())} bytes, over max_bytes {cfg['max_bytes']}")
    return text


def blob(root, target):
    return git(root, "rev-parse", f"HEAD:{target}")


def finish_keep(cfg, root, state):
    """A kill between recording a keep and committing it leaves the kept text uncommitted."""
    target = cfg["target"]
    if blob(root, target) != state["head_blob"] and git(root, "hash-object", "--", target) == state["head_blob"]:
        git(root, "add", "--", target)
        git(root, "commit", "-m", f"ratchet round {state['round']}: finish killed keep", "--", target)
        print("note: finished a keep whose commit was killed")


def settle(cfg, root, state):
    finish_keep(cfg, root, state)
    target = cfg["target"]
    if blob(root, target) != state["head_blob"]:
        raise Refused(f"{target} at HEAD is not the last decided version: something else committed it")


def ready(cfg, root, state):
    if state["kept"] is None:
        raise Refused("the baseline did not finish: run `baseline` again")
    if git(root, "branch", "--show-current") != state["branch"]:
        raise Refused(f"switch back to {state['branch']}")
    settle(cfg, root, state)


def stop_reason(cfg, state, train):
    if state.get("blocked"):
        return state["blocked"]
    if state["test_result"]:
        return "the held-out test already ran"
    if state["round"] >= cfg["max_rounds"]:
        return f"max_rounds ({cfg['max_rounds']}) reached"
    if state["stale"] >= cfg["plateau"]:
        return f"plateau: {state['stale']} rounds without a keep"
    if state["sessions"] + len(train) * cfg["reps"] + state["test_cost"] > cfg["max_sessions"]:
        return f"max_sessions ({cfg['max_sessions']}) would be passed, counting the held-out test"
    return ""


def log(eval_dir, *fields):
    with (eval_dir / "runs" / "results.tsv").open("a", encoding="utf-8") as f:
        f.write("\t".join(str(x).replace("\t", " ").replace("\n", " ") for x in fields) + "\n")


def archive(eval_dir):
    """Keep a replaced attempt's record under runs/attempts/; learnings outlive it."""
    runs = eval_dir / "runs"
    (runs / "attempts").mkdir(parents=True, exist_ok=True)
    dest = runs / "attempts" / str(len(list((runs / "attempts").iterdir())))
    for p in list(runs.iterdir()):
        if p.name not in ("attempts", "learnings.md"):
            dest.mkdir(exist_ok=True)
            shutil.move(str(p), str(dest / p.name))
    learnings = runs / "learnings.md"
    if not learnings.exists():
        learnings.write_text("# Learnings\n\n", encoding="utf-8")


def cmd_baseline(eval_dir, root, args):
    cfg, tasks = load(eval_dir)
    old = read_state(eval_dir) if state_path(eval_dir).exists() else None
    spent = {"sessions": 0, "cost_usd": 0.0, "round": 0, "keeps": 0, "restarts": 0}
    if old:
        if old["kept"] is not None and not args.restart:
            raise Refused("a baseline exists: pass --restart to take a new one after changing the eval")
        if old["test_result"]:
            raise Refused("the held-out tasks have been read: write new tasks in a new eval dir")
        spent = {k: old[k] for k in spent}
        if old["kept"] is not None:
            if spent["restarts"] >= MAX_RESTARTS:
                raise Stop(f"{MAX_RESTARTS} restarts used: report that this target has no room a loop can measure")
            spent["restarts"] += 1
        if old["keeps"]:
            finish_keep(cfg, root, old)
    branch = git(root, "branch", "--show-current")
    if not branch or branch in PROTECTED_BRANCHES:
        raise Refused(f"run on a feature branch, not {branch or 'a detached HEAD'}")
    if (
        subprocess.run(
            ["git", "-C", str(root), "ls-files", "--error-unmatch", cfg["target"]], capture_output=True
        ).returncode
        != 0
    ):
        raise Refused(f"{cfg['target']} is not tracked by git: commit it, or target the file it is copied from")
    dirty = git(root, "diff", "--name-only", "HEAD")
    if dirty:
        raise Refused(f"commit or stash tracked changes first, rounds compare against HEAD: {dirty.split()}")
    text = check_target(cfg, root)
    if old and old["keeps"] and blob(root, cfg["target"]) != old["head_blob"]:
        raise Refused(f"{cfg['target']} changed after a keep: a restart keeps the kept work, so restore it")
    train, test = split(cfg, tasks, (old["train"], old["test"]) if old else None)
    test_cost = 2 * len(test) * cfg["test_reps"]
    if spent["sessions"] + len(train) * (cfg["reps"] + 1) + test_cost > cfg["max_sessions"]:
        raise Stop(f"max_sessions ({cfg['max_sessions']}) would be passed, counting earlier attempts and the test")
    archive(eval_dir)
    state = {
        "harness": harness_hash(eval_dir),
        "branch": branch,
        # After a keep the held-out test still has to compare against the true starting point.
        "base_sha": old["base_sha"] if old and old["keeps"] else git(root, "rev-parse", "HEAD"),
        "head_blob": blob(root, cfg["target"]),
        "train": train,
        "test": test,
        **spent,
        "stale": 0,
        "blocked": "",
        "test_cost": test_cost,
        "test_tries": old["test_tries"] if old else 0,
        "kept": None,
        "baseline_rows": None,
        "test_result": None,
    }
    log(eval_dir, "round", "sha", "train", "delta", "floor", "decision", "engaged", "hypothesis")
    null = run_pass(cfg, eval_dir, state, "null", tasks, train, None, 1)
    kept = run_pass(cfg, eval_dir, state, "baseline", tasks, train, text, cfg["reps"])
    state["kept"] = state["baseline_rows"] = kept
    score, null_score = mean(kept, train), mean(null, train)
    floor = noise_floor(kept, kept, train)
    log(
        eval_dir, 0, state["base_sha"][:8], f"{score:.3f}", "", f"{floor:.3f}", "baseline", "", f"null {null_score:.3f}"
    )
    print(f"baseline {score:.3f}  null {null_score:.3f}  noise floor {floor:.3f}  headroom {1 - score:.3f}")
    if error_rate(kept) > MAX_ERROR_RATE:
        state["blocked"] = "too many sessions errored at baseline: fix the harness"
    elif floor >= 1 - score:
        state["blocked"] = "noise floor is over the headroom: no edit can show. Add tasks or reps"
    elif score <= null_score:
        # A thin target with room to grow lands here too, so this cannot be a stop.
        print("note: the target beats no-target by nothing: check a failed transcript shows it was loaded")
    write_state(eval_dir, state)
    (eval_dir / "runs" / "last_run.json").write_text(json.dumps(kept, indent=2), encoding="utf-8")
    if state["blocked"]:
        raise Stop(state["blocked"])


def cmd_round(eval_dir, root, args):
    cfg, tasks = load(eval_dir)
    state = read_state(eval_dir)
    train = state["train"]
    ready(cfg, root, state)
    reason = stop_reason(cfg, state, train)
    if reason:
        raise Stop(reason)
    check_harness(eval_dir, state)
    changed = git(root, "diff", "--name-only", "HEAD").splitlines()
    if changed != [cfg["target"]]:
        raise Refused(f"exactly one file may differ from HEAD, the target; found {changed or 'none'}")
    try:
        text = check_target(cfg, root)
    except Refused:
        git(root, "checkout", "HEAD", "--", cfg["target"])
        raise
    kept = state["kept"]
    state["round"] += 1
    n = state["round"]
    cand = run_pass(cfg, eval_dir, state, f"r{n}", tasks, train, text, cfg["reps"])
    delta = mean(cand, train) - mean(kept, train)
    floor = noise_floor(kept, cand, train)
    guards = guard_regressions(cfg, tasks, state["baseline_rows"], cand, train)
    old_size = len(git(root, "show", f"HEAD:{cfg['target']}").encode())
    if error_rate(cand) > MAX_ERROR_RATE:
        decision = "void"
    elif guards:
        decision = "discard"
    elif delta > floor:
        decision = "keep"
    elif delta >= 0 and len(text.encode()) <= 0.9 * old_size:
        decision = "keep-shorter"  # no measured loss at 10% less text: fewer rules to overfit
    else:
        decision = "discard"
    sha = "-"
    if decision.startswith("keep"):
        state["kept"] = cand
        state["stale"] = 0
        state["keeps"] += 1
        state["head_blob"] = git(root, "hash-object", "--", cfg["target"])
        write_state(eval_dir, state)  # first, so settle() can finish a commit killed here
        git(root, "add", "--", cfg["target"])
        git(root, "commit", "-m", f"ratchet round {n}: {args.hypothesis}"[:72], "--", cfg["target"])
        state["head_blob"] = blob(root, cfg["target"])  # a pre-commit hook may have rewritten it
        sha = git(root, "rev-parse", "--short", "HEAD")
    else:
        git(root, "checkout", "HEAD", "--", cfg["target"])
        state["stale"] += decision == "discard"
    engaged = sum(r["engaged"] for r in cand.values())
    log(
        eval_dir,
        n,
        sha,
        f"{mean(cand, train):.3f}",
        f"{delta:+.3f}",
        f"{floor:.3f}",
        decision,
        engaged,
        args.hypothesis,
    )
    write_state(eval_dir, state)
    (eval_dir / "runs" / "last_run.json").write_text(json.dumps(cand, indent=2), encoding="utf-8")
    print(
        f"round {n}: {decision}  delta {delta:+.3f}  floor {floor:.3f}" + (f"  guards lost: {guards}" if guards else "")
    )
    reason = stop_reason(cfg, state, train)
    if reason:
        print(f"stop: {reason}")


def cmd_test(eval_dir, root, args):
    cfg, tasks = load(eval_dir)
    state = read_state(eval_dir)
    if state["test_result"]:
        raise Refused("the held-out test runs once: a second look would turn it into a tuning set")
    check_harness(eval_dir, state)
    ready(cfg, root, state)
    if not state["keeps"]:
        raise Refused("nothing was kept: there is no winner to test")
    # The reservation in stop_reason covers one test; a rerun after errors may pass max_sessions by one more.
    if state["test_tries"] >= MAX_TEST_TRIES:
        raise Stop(f"the held-out test gave no verdict in {MAX_TEST_TRIES} tries: report that")
    if git(root, "diff", "--name-only", "HEAD", "--", cfg["target"]):
        git(root, "checkout", "HEAD", "--", cfg["target"])
        print("note: restored an edit no round decided on; the winner is the last keep")
    state["test_tries"] += 1
    test = state["test"]
    base = git(root, "show", f"{state['base_sha']}:{cfg['target']}")
    a = run_pass(cfg, eval_dir, state, "test-baseline", tasks, test, base, cfg["test_reps"])
    b = run_pass(cfg, eval_dir, state, "test-winner", tasks, test, check_target(cfg, root), cfg["test_reps"])
    if max(error_rate(a), error_rate(b)) > MAX_ERROR_RATE:
        write_state(eval_dir, state)
        raise Stop("too many sessions errored in the held-out test: no verdict. Fix the cause and rerun `test`")
    delta = mean(b, test) - mean(a, test)
    floor = noise_floor(a, b, test)
    cfg["reps"] = cfg["test_reps"]
    guards = guard_regressions(cfg, tasks, a, b, test)
    verdict = "review-for-merge" if delta > floor and not guards else "do-not-merge"
    state["test_result"] = {
        "baseline": mean(a, test),
        "winner": mean(b, test),
        "delta": delta,
        "floor": floor,
        "guards_lost": guards,
        "verdict": verdict,
    }
    write_state(eval_dir, state)
    log(
        eval_dir,
        "test",
        git(root, "rev-parse", "--short", "HEAD"),
        f"{mean(b, test):.3f}",
        f"{delta:+.3f}",
        f"{floor:.3f}",
        verdict,
        "",
        "held-out, baseline vs winner",
    )
    print(f"test: baseline {mean(a, test):.3f}  winner {mean(b, test):.3f}  delta {delta:+.3f}  floor {floor:.3f}")
    print(verdict)


def cmd_status(eval_dir, root, args):
    cfg, _ = load(eval_dir)
    state = read_state(eval_dir)
    print(
        f"round {state['round']}/{cfg['max_rounds']}  sessions {state['sessions']}/{cfg['max_sessions']}  "
        f"stale {state['stale']}/{cfg['plateau']}  list price ${state['cost_usd']:.2f}"
    )
    if state["kept"] is None:
        print("the baseline did not finish: run `baseline` again")
        return
    print(f"train score {mean(state['kept'], state['train']):.3f}")
    print(f"stop: {stop_reason(cfg, state, state['train']) or 'not yet'}")
    t = state["test_result"]
    if t:
        print(
            f"held-out: baseline {t['baseline']:.3f}  winner {t['winner']:.3f}  delta {t['delta']:+.3f}  "
            f"floor {t['floor']:.3f}  {t['verdict']}"
        )


def main():
    p = argparse.ArgumentParser(description="Score-gated optimization of one skill or CLAUDE.md file.")
    p.add_argument("command", choices=["baseline", "round", "test", "status"])
    p.add_argument("eval_dir", type=Path)
    p.add_argument("--hypothesis", default="")
    p.add_argument("--restart", action="store_true", help="baseline: archive this attempt and take a new one")
    args = p.parse_args()
    if args.command == "round" and not args.hypothesis.strip():
        p.error("round needs --hypothesis")
    eval_dir = args.eval_dir.resolve()
    try:
        root = Path(git(eval_dir, "rev-parse", "--show-toplevel"))
        {"baseline": cmd_baseline, "round": cmd_round, "test": cmd_test, "status": cmd_status}[args.command](
            eval_dir, root, args
        )
    except Stop as e:
        print(f"stop: {e}", file=sys.stderr)
        return 3
    except (Refused, OSError, ValueError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
