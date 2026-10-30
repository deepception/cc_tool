#!/usr/bin/env python3
"""Exercise ratchet.py end to end against a fake `claude` and a real git repo.

The fake session writes out.txt with the target file's text, and each check reads it,
so a task's result is decided by what the target says.

Run:  python3 tests/test_skill_optimize.py
"""

import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RATCHET = REPO / "templates" / "skills" / "skill-optimize" / "scripts" / "ratchet.py"

FAKE_CLAUDE = """#!/bin/sh
[ -n "$EVAL_DIR" ] && echo leaked >> "$EVAL_DIR/leak"
[ -n "$RATCHET_TEST_SECRET" ] && echo leaked > "$HOME/.ratchet-secret-leak"
if [ "$(cat)" = "fail" ]; then echo '{"type":"result","is_error":true,"total_cost_usd":0.5}'; exit 0; fi
if [ -f SKILL.md ]; then cp SKILL.md out.txt; else : > out.txt; fi
echo '{"type":"result"}'
"""

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
        print(f"FAIL {name} {detail}")


def sh(cwd, *cmd):
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)


class Rig:
    def __init__(self, tmp, words=("alpha", "beta", "gamma", "delta"), target="alpha\n", **cfg):
        self.root = Path(tmp) / "repo"
        self.root.mkdir()
        sh(self.root, "git", "init", "-q", "-b", "opt")
        sh(self.root, "git", "config", "user.email", "t@example.com")
        sh(self.root, "git", "config", "user.name", "t")
        self.target = self.root / "skill.md"
        self.target.write_text(target)
        bin_dir = Path(tmp) / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "claude"
        fake.write_text(FAKE_CLAUDE)
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "HOME": str(tmp),
            "RATCHET_TEST_SECRET": "x",
            "ANTHROPIC_API_KEY": "x",
        }
        self.home = Path(tmp)
        self.eval = self.root / "optimize" / "s"
        self.eval.mkdir(parents=True)
        (self.eval / "eval.json").write_text(
            json.dumps(
                {"target": "skill.md", "install_as": "SKILL.md", "model": "sonnet", "reps": 2, "test_reps": 2, **cfg}
            )
        )
        lines = [{"id": f"t{i}", "prompt": "go", "check": f"grep -q {w} out.txt"} for i, w in enumerate(words)]
        lines.append({"id": "g", "prompt": "go", "check": "grep -q guardrail out.txt", "guard": True})
        (self.eval / "tasks.jsonl").write_text("\n".join(json.dumps(x) for x in lines))
        sh(self.root, "git", "add", "skill.md")
        sh(self.root, "git", "commit", "-qm", "init")

    def run(self, *args):
        cmd = [sys.executable, "-I", str(RATCHET), args[0], str(self.eval), *args[1:]]
        return subprocess.run(cmd, env=self.env, capture_output=True, text=True, cwd=self.root)

    def state(self):
        return json.loads((self.eval / "runs" / "state.json").read_text())

    def log(self):
        return (self.eval / "runs" / "results.tsv").read_text()


def train_words(rig):
    """The words behind the train tasks, so an edit can target what the split chose."""
    tasks = {json.loads(x)["id"]: json.loads(x) for x in (rig.eval / "tasks.jsonl").read_text().splitlines()}
    return [tasks[i]["check"].split()[2] for i in rig.state()["train"] if i != "g"]


# Baseline refusals and stops
with tempfile.TemporaryDirectory() as tmp:
    rig = Rig(tmp, words=[f"word{i}" for i in range(16)], target="unrelated\n")
    r = rig.run("baseline")
    check(
        "target no better than null continues with a note",
        r.returncode == 0 and "note: the target beats no-target" in r.stdout,
        r.stdout + r.stderr,
    )
    rig.target.write_text("guardrail " + " ".join(train_words(rig)) + "\n")
    r = rig.run("round", "--hypothesis", "teach the missing behaviour")
    check("a thin target can then gain", "keep" in r.stdout, r.stdout + r.stderr)

with tempfile.TemporaryDirectory() as tmp:
    rig = Rig(tmp, target="unrelated\n")
    rig.run("baseline")
    (rig.eval / "runs" / "state.json").write_text(json.dumps({**rig.state(), "blocked": "x"}))
    check("blocked state refuses rounds", rig.run("round", "--hypothesis", "x").returncode == 3)

with tempfile.TemporaryDirectory() as tmp:
    rig = Rig(tmp, target="guardrail\n")
    sh(rig.root, "git", "checkout", "-qb", "master")
    r = rig.run("baseline")
    check("protected branch refused", r.returncode == 1 and "feature branch" in r.stderr, r.stderr)

with tempfile.TemporaryDirectory() as tmp:
    rig = Rig(tmp, target="guardrail\n")
    rig.target.write_text("edited\n")
    r = rig.run("baseline")
    check("dirty tree refused before spending sessions", r.returncode == 1 and "commit or stash" in r.stderr, r.stderr)

with tempfile.TemporaryDirectory() as tmp:
    rig = Rig(tmp, target="guardrail\n")
    (rig.root / "copy.md").write_text("x\n")
    cfg = json.loads((rig.eval / "eval.json").read_text())
    (rig.eval / "eval.json").write_text(json.dumps({**cfg, "target": "copy.md"}))
    r = rig.run("baseline")
    check("untracked target refused", r.returncode == 1 and "not tracked" in r.stderr, r.stderr)

with tempfile.TemporaryDirectory() as tmp:
    rig = Rig(tmp, target="guardrail\n")
    cfg = json.loads((rig.eval / "eval.json").read_text())
    (rig.eval / "eval.json").write_text(json.dumps({**cfg, "target": "missing.md"}))
    r = rig.run("baseline")
    check("missing target refused without a traceback", r.returncode == 1 and "Traceback" not in r.stderr, r.stderr)

with tempfile.TemporaryDirectory() as tmp:
    rig = Rig(tmp, target="guardrail\n")
    tasks = [{"id": f"t{i}", "prompt": "fail", "check": "true"} for i in range(4)]
    (rig.eval / "tasks.jsonl").write_text("\n".join(json.dumps(t) for t in tasks))
    r = rig.run("baseline")
    check("is_error at exit 0 counts as an error", r.returncode == 3 and "errored" in r.stderr, r.stderr)
    check("cost of failed sessions is counted", rig.state()["cost_usd"] > 0)

with tempfile.TemporaryDirectory() as tmp:
    rig = Rig(tmp, target="guardrail alpha\n")
    tasks = [{"id": f"t{i}", "prompt": "go", "setup": "exit 7", "check": "true"} for i in range(4)]
    (rig.eval / "tasks.jsonl").write_text("\n".join(json.dumps(t) for t in tasks))
    r = rig.run("baseline")
    check("setup failure refuses", r.returncode == 1 and "setup failed" in r.stderr, r.stderr)

with tempfile.TemporaryDirectory() as tmp:
    rig = Rig(tmp, target="guardrail alpha beta gamma delta\n")
    r = rig.run("baseline")
    check("ceiling stops the first baseline", r.returncode == 3, r.stdout + r.stderr)
    first = rig.state()["sessions"]
    (rig.eval / "runs" / "learnings.md").write_text("kept note\n")
    check("second baseline needs --restart", "--restart" in rig.run("baseline").stderr)
    rig.run("baseline", "--restart")
    check("restart keeps counting sessions", rig.state()["sessions"] == 2 * first, str(rig.state()["sessions"]))
    check("restart archives the attempt", (rig.eval / "runs" / "attempts" / "0" / "results.tsv").is_file())
    check("restart keeps learnings", (rig.eval / "runs" / "learnings.md").read_text() == "kept note\n")

with tempfile.TemporaryDirectory() as tmp:
    rig = Rig(tmp, target="guardrail\n", max_sessions=first)
    rig.run("baseline")
    r = rig.run("baseline", "--restart")
    check("restart cannot pass the session cap", r.returncode == 3 and "earlier attempts" in r.stderr, r.stderr)

# A full loop, with many tasks so one edit can clear the floor
words = [f"word{i}" for i in range(40)]
with tempfile.TemporaryDirectory() as tmp:
    rig = Rig(tmp, words=words, target="guardrail word0\n", protected=["guardrail"], max_rounds=4, plateau=2)
    r = rig.run("baseline")
    check("baseline runs", r.returncode == 0, r.stderr)
    state = rig.state()
    check("guard is in both sets", "g" in state["train"] and "g" in state["test"])
    check("split is disjoint apart from guards", set(state["train"]) & set(state["test"]) == {"g"})
    check("baseline counts null and reps", state["sessions"] == len(state["train"]) * 3, str(state["sessions"]))
    check("session never saw EVAL_DIR", not (rig.eval / "leak").exists())
    check("session env is reduced", not (rig.home / ".ratchet-secret-leak").exists())

    r = rig.run("round", "--hypothesis", "no edit")
    check("round with no edit refused", r.returncode == 1 and "none" in r.stderr, r.stderr)

    rig.target.write_text("word0\n")
    r = rig.run("round", "--hypothesis", "drop guard")
    check("removing protected text refused", r.returncode == 1 and "protected" in r.stderr, r.stderr)
    check("removing protected text restores the target", "guardrail" in rig.target.read_text())

    good = "guardrail word0 " + " ".join(train_words(rig)) + "\n"
    rig.target.write_text(good)
    r = rig.run("round", "--hypothesis", "cover train")
    check("real gain kept", r.returncode == 0 and "keep" in r.stdout, r.stdout + r.stderr)
    check(
        "keep committed",
        "ratchet round 1"
        in subprocess.run(["git", "log", "-1", "--format=%s"], cwd=rig.root, capture_output=True, text=True).stdout,
    )

    rig.target.write_text(good + "more text that changes nothing\n")
    r = rig.run("round", "--hypothesis", "noop growth")
    check("no gain discarded", "discard" in r.stdout, r.stdout)
    check("discard restored the target", rig.target.read_text() == good)

    (rig.eval / "tasks.jsonl").open("a").write("\n")
    rig.target.write_text(good + "x\n")
    r = rig.run("round", "--hypothesis", "after harness edit")
    check("harness edit stops the loop", r.returncode == 3 and "comparable" in r.stderr, r.stderr)
    (rig.eval / "tasks.jsonl").write_text((rig.eval / "tasks.jsonl").read_text().rstrip("\n"))
    rig.target.write_text(good)

    r = rig.run("test")
    check("held-out test runs", r.returncode == 0, r.stderr)
    check("overfit edit is not recommended", "do-not-merge" in r.stdout, r.stdout)
    r = rig.run("test")
    check("second test refused", r.returncode == 1 and "once" in r.stderr, r.stderr)
    rig.target.write_text(good + "y\n")
    check("rounds refused after test", rig.run("round", "--hypothesis", "more").returncode == 3)
    check("results log has every round", rig.log().count("\n") == 5, rig.log())

# Guard loss beats a score gain
with tempfile.TemporaryDirectory() as tmp:
    rig = Rig(tmp, words=words, target="guardrail word0\n")
    rig.run("baseline")
    rig.target.write_text("word0 " + " ".join(words) + "\n")
    r = rig.run("round", "--hypothesis", "gain without guard")
    check("guard loss discards a gain", "discard" in r.stdout and "guards lost" in r.stdout, r.stdout + r.stderr)

# Checks run in a workspace the session touched, so they get the reduced environment
with tempfile.TemporaryDirectory() as tmp:
    rig = Rig(tmp, target="guardrail alpha\n")
    lines = (rig.eval / "tasks.jsonl").read_text().splitlines()
    env_check = {
        "id": "env",
        "prompt": "go",
        "check": 'test -n "$EVAL_DIR" && test -z "$RATCHET_TEST_SECRET$ANTHROPIC_API_KEY"',
        "guard": True,
    }
    (rig.eval / "tasks.jsonl").write_text("\n".join([*lines, json.dumps(env_check)]))
    rig.run("baseline")
    results = json.loads((rig.eval / "runs" / "last_run.json").read_text())
    check("checks get the reduced environment", results["env"]["passed"] == results["env"]["n"] > 0, str(results))


# Review findings: what a held-out verdict may rest on, and recovery after a kill
def winner_rig(tmp, **cfg):
    rig = Rig(tmp, words=[f"word{i}" for i in range(16)], target="guardrail\n", **cfg)
    rig.run("baseline")
    return rig


def keep(rig):
    rig.target.write_text("guardrail " + " ".join(train_words(rig)) + "\n")
    return rig.run("round", "--hypothesis", "gain")


with tempfile.TemporaryDirectory() as tmp:
    rig = winner_rig(tmp)
    rig.target.write_text("guardrail edited\n")
    r = rig.run("test")
    check("test with no keep refused", r.returncode == 1 and "nothing was kept" in r.stderr, r.stderr)

with tempfile.TemporaryDirectory() as tmp:
    rig = winner_rig(tmp)
    keep(rig)
    won = rig.target.read_text()
    rig.target.write_text(won + "undecided\n")
    r = rig.run("test")
    check("test drops an undecided edit", "restored an edit" in r.stdout and rig.target.read_text() == won, r.stdout)

with tempfile.TemporaryDirectory() as tmp:
    rig = winner_rig(tmp)
    keep(rig)
    rig.target.write_text("guardrail hand\n")
    sh(rig.root, "git", "commit", "-qam", "hand edit")
    r = rig.run("test")
    check("test refuses a hand commit", r.returncode == 1 and "last decided" in r.stderr, r.stderr)

with tempfile.TemporaryDirectory() as tmp:
    rig = winner_rig(tmp)
    keep(rig)
    (rig.home / "bin" / "claude").write_text(FAKE_CLAUDE.replace('cat)" = "fail"', 'cat)" = "go"'))
    r = rig.run("test")
    check("errored held-out test gives no verdict", r.returncode == 3 and "no verdict" in r.stderr, r.stderr)
    check("errored held-out test records nothing", rig.state()["test_result"] is None)

with tempfile.TemporaryDirectory() as tmp:
    rig = winner_rig(tmp)
    (rig.eval / "runs" / "state.json").write_text(json.dumps({**rig.state(), "kept": None}))
    check("status survives a killed baseline", "did not finish" in rig.run("status").stdout)
    check("round refused after a killed baseline", "did not finish" in rig.run("round", "--hypothesis", "x").stderr)
    r = rig.run("baseline")
    check("killed baseline reruns without --restart", r.returncode == 0 and rig.state()["restarts"] == 0, r.stderr)

with tempfile.TemporaryDirectory() as tmp:
    rig = winner_rig(tmp)
    state = rig.state()
    slipped = {**state["kept"]["g"], "passed": 0}
    (rig.eval / "runs" / "state.json").write_text(json.dumps({**state, "kept": {**state["kept"], "g": slipped}}))
    rig.target.write_text(" ".join(train_words(rig)) + "\n")
    r = rig.run("round", "--hypothesis", "gain, guard already slipped")
    check("guard slips cannot add up across keeps", "discard" in r.stdout and "guards lost" in r.stdout, r.stdout)

with tempfile.TemporaryDirectory() as tmp:
    rig = Rig(tmp, words=[f"word{i}" for i in range(16)], target="guardrail\n", reps=1)
    rig.run("baseline")
    rig.target.write_text(" ".join(train_words(rig)) + "\n")
    r = rig.run("round", "--hypothesis", "gain without guard")
    check("a guard can be lost at reps 1", "guards lost" in r.stdout, r.stdout + r.stderr)

with tempfile.TemporaryDirectory() as tmp:
    rig = winner_rig(tmp)
    checks = rig.eval / "checks" / "__pycache__"
    checks.mkdir(parents=True)
    (checks / "c.pyc").write_text("x")
    rig.target.write_text("guardrail filler\n")
    check("a check's __pycache__ is not a harness change", rig.run("round", "--hypothesis", "x").returncode == 0)

with tempfile.TemporaryDirectory() as tmp:
    rig = winner_rig(tmp)
    before = rig.state()
    with (rig.eval / "tasks.jsonl").open("a") as f:
        f.write("\n" + json.dumps({"id": "extra", "prompt": "go", "check": "true"}))
    rig.run("baseline", "--restart")
    after = rig.state()
    moved = set(before["test"]) & set(after["train"]) - {"g"}
    check("a restart moves no held-out task into train", not moved, str(moved))
    check("a restart is counted", after["restarts"] == 1)
    rig.run("baseline", "--restart")
    r = rig.run("baseline", "--restart")
    check("third restart stops", r.returncode == 3 and "2 restarts" in r.stderr, r.stderr)

with tempfile.TemporaryDirectory() as tmp:
    rig = winner_rig(tmp)
    keep(rig)
    base, rounds = rig.state()["base_sha"], rig.state()["round"]
    rig.run("baseline", "--restart")
    state = rig.state()
    check(
        "a restart after a keep holds the start point and rounds",
        state["base_sha"] == base and state["round"] == rounds,
    )

with tempfile.TemporaryDirectory() as tmp:
    rig = winner_rig(tmp)
    keep(rig)
    won = rig.target.read_text()
    sh(rig.root, "git", "reset", "-q", "--soft", "HEAD~1")
    sh(rig.root, "git", "reset", "-q")
    rig.target.write_text(won)
    r = rig.run("test")
    check("a killed keep commit is finished", "finished a keep" in r.stdout, r.stdout + r.stderr)

# Second review: budget at the end of a run, hooks, and restart edges
with tempfile.TemporaryDirectory() as tmp:
    rig = winner_rig(tmp)
    (rig.home / "bin" / "claude").write_text(FAKE_CLAUDE.replace('cat)" = "fail"', 'cat)" = "go"'))
    (rig.eval / "runs" / "state.json").write_text(json.dumps({**rig.state(), "keeps": 1}))
    first, second, third = rig.run("test"), rig.run("test"), rig.run("test")
    check("errored test gets one rerun", first.returncode == 3 and "no verdict" in second.stderr, second.stderr)
    check("third test try stops", third.returncode == 3 and "2 tries" in third.stderr, third.stderr)

with tempfile.TemporaryDirectory() as tmp:
    rig = winner_rig(tmp)
    hook = rig.root / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho hooked >> skill.md\ngit add skill.md\n")
    hook.chmod(0o755)
    keep(rig)
    rig.target.write_text(rig.target.read_text() + "more\n")
    r = rig.run("round", "--hypothesis", "after a rewriting hook")
    check("a rewriting pre-commit hook does not jam rounds", r.returncode == 0, r.stderr)

with tempfile.TemporaryDirectory() as tmp:
    rig = winner_rig(tmp)
    held = rig.state()["test"][0]
    tasks = [json.loads(x) for x in (rig.eval / "tasks.jsonl").read_text().splitlines()]
    marked = [{**t, "guard": True} if t["id"] == held else t for t in tasks]
    (rig.eval / "tasks.jsonl").write_text("\n".join(json.dumps(t) for t in marked))
    r = rig.run("baseline", "--restart")
    check("a held-out task cannot become a guard", r.returncode == 1 and "cannot become" in r.stderr, r.stderr)
    train = set(rig.state()["train"]) - {"g"}
    (rig.eval / "tasks.jsonl").write_text("\n".join(json.dumps(t) for t in tasks if t["id"] not in train))
    r = rig.run("baseline", "--restart")
    check("restart with no train tasks refused", r.returncode == 1 and "is empty" in r.stderr, r.stderr)

with tempfile.TemporaryDirectory() as tmp:
    rig = winner_rig(tmp)
    keep(rig)
    won = rig.target.read_text()
    sh(rig.root, "git", "reset", "-q", "--soft", "HEAD~1")
    sh(rig.root, "git", "reset", "-q")
    rig.target.write_text(won)
    r = rig.run("baseline", "--restart")
    check("a restart finishes a killed keep", "finished a keep" in r.stdout, r.stdout + r.stderr)

with tempfile.TemporaryDirectory() as tmp:
    rig = winner_rig(tmp)
    used = rig.state()["sessions"] + rig.state()["test_cost"]
    cfg = json.loads((rig.eval / "eval.json").read_text())
    (rig.eval / "eval.json").write_text(json.dumps({**cfg, "max_sessions": used + 1}))
    r = rig.run("baseline", "--restart")
    check("a restart reserves the held-out test", r.returncode == 3 and "max_sessions" in r.stderr, r.stderr)

# Plateau and session cap
with tempfile.TemporaryDirectory() as tmp:
    rig = Rig(tmp, words=words, target="guardrail word0\n", plateau=1)
    rig.run("baseline")
    rig.target.write_text("guardrail word0 filler\n")
    r = rig.run("round", "--hypothesis", "flat")
    check("plateau announced", "stop: plateau" in r.stdout, r.stdout)
    rig.target.write_text("guardrail word0 filler two\n")
    check("plateau stops the next round", rig.run("round", "--hypothesis", "again").returncode == 3)

with tempfile.TemporaryDirectory() as tmp:
    rig = Rig(tmp, words=words, target="guardrail word0\n", max_sessions=150)
    rig.run("baseline")
    rig.target.write_text("guardrail word0 filler\n")
    r = rig.run("round", "--hypothesis", "over cap")
    check("session cap reserves the held-out test", r.returncode == 3 and "max_sessions" in r.stderr, r.stderr)

# A shorter file with no measured loss is kept
with tempfile.TemporaryDirectory() as tmp:
    rig = Rig(tmp, words=words, target="guardrail word0 " + "padding " * 20 + "\n")
    rig.run("baseline")
    rig.target.write_text("guardrail word0\n")
    r = rig.run("round", "--hypothesis", "trim")
    check("shorter tie kept", "keep-shorter" in r.stdout, r.stdout + r.stderr)

print(f"{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
