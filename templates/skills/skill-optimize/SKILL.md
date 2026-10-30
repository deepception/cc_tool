---
name: skill-optimize
description: >-
  Optimize one skill or CLAUDE.md file with a score-gated loop run entirely in
  Claude Code, using headless sessions on fixed tasks, end-state checks, and a
  noise gate that keeps an edit or restores it. Use when the user says "optimize
  this skill", "autoresearch", "hill-climb this skill", or wants measured rather
  than judged skill improvement. For trigger descriptions use skill-creator's
  run_loop instead.
user-invocable: true
disable-model-invocation: true
argument-hint: "[path to the skill or CLAUDE.md file]"
---

# Skill Optimize

Target: $ARGUMENTS

Improve one instruction file against fixed tasks. The method joins autoresearch's small loop (one file, git keep or restore, hard stops) to hill-climbing's validity rules (noise floor, train/test split, grading the end state).

You do the judgment: write the eval, read failures, edit the target, write learnings. `scripts/ratchet.py` does the measuring and every gate, so never decide keep or discard yourself and never run git on the target during rounds. Its verdict is final even when an edit looks right.

## Start here

If `optimize/<name>/runs/state.json` exists, this is a resumed run: run `ratchet.py status`, then continue from the step it implies. Do not rebuild anything or delete `runs/`, which holds the only record of what was tried and the session count.

For a new run, tell the user the two costs before starting, and get a yes. A full run is about 320 `claude -p` sessions on their login, so it spends plan usage. It commits to a feature branch.

Try the cheap path first if nobody has yet. Run the skill on a few real tasks, read the transcripts, and make one distilled edit a human reviews. Large effects need no gates, and at 10 train tasks a gain under about 20 points cannot be measured anyway. Use the loop to confirm a change, or when success can be checked by a script. Skip it when success is taste: a judge there gets flattered.

## Pick the file to edit

`target` must be tracked by git, so it has to be the source file:

| Optimizing | `target` (edited here) | `install_as` (placed in each workspace) |
|---|---|---|
| A skill you own in this project | `.claude/skills/<n>/SKILL.md` | the same path |
| A skill shipped by cc_tool | `templates/skills/<n>/SKILL.md`, working in the cc_tool repo | `.claude/skills/<n>/SKILL.md` |
| A project CLAUDE.md | `CLAUDE.md` | `CLAUDE.md` |
| The managed block | `templates/CLAUDE_snippet.md`, working in the cc_tool repo | `CLAUDE.md` |

A copy deployed by `cc-setup` is overwritten by `cc-update-project`, so an edit there is lost. Only one file is optimized and installed. Put a skill's `references/` and `scripts/` in the task's fixture at their installed paths.

## 1. Build the eval

Create a feature branch, then `optimize/<name>/` beside the target. Add `optimize/*/runs/` to `.gitignore`. Commit everything: `baseline` refuses tracked changes because rounds compare against HEAD.

```json
{
  "target": ".claude/skills/release-notes/SKILL.md",
  "install_as": ".claude/skills/release-notes/SKILL.md",
  "model": "sonnet",
  "concurrency": 4,
  "protected": ["Ask before publishing"],
  "claude_args": ["--permission-mode", "acceptEdits", "--allowedTools", "Bash(git log *)", "Bash(git diff *)"]
}
```

Use the model the target normally runs on. Other keys and their defaults are at the top of `ratchet.py`; set `max_bytes` when the file must not grow.

`tasks.jsonl` holds one task per line:

```json
{"id": "two-fixes", "prompt": "/release-notes v2.1", "fixture": "fixtures/two-fixes", "setup": "git init -q && git add -A && git -c user.name=t -c user.email=t@t commit -qm init", "check": "python3 \"$EVAL_DIR/checks/two_fixes.py\"", "engaged": "CHANGELOG"}
```

| Field | Meaning |
|---|---|
| `prompt` | What a user would type. A skill with `disable-model-invocation: true` never fires from plain words, so start with `/name args`. Otherwise use natural words, which also tests discovery. |
| `fixture` | Optional directory copied in as the workspace, otherwise it starts empty. Workspaces are not git repos unless `setup` makes one. |
| `setup` | Optional shell command run in the workspace, after the target is installed. |
| `check` | Shell command run in the workspace after the session; exit 0 is a pass. Keep check scripts in the eval dir, reached through `$EVAL_DIR`. Sessions are not given that variable, which hides the answers but does not lock them: a session with shell access can still find them, so read passing transcripts for it. `$TRANSCRIPT` is the session's stream-json file. |
| `engaged` | Optional regex on the transcript showing the target was used. A score that moves while this does not is noise. |
| `guard` | `true` for protected behaviour. Sits in both sets, and an edit that drops one more than one rep below its baseline rate is discarded. |

- **About 20 tasks from real use.** Half stay held out. Write them before reading any failure, and cover what the target is for rather than what it does badly today.
- **Prefer end state**: files, tests, command results. Use `$TRANSCRIPT` only for behaviour that leaves no file, such as asking before a destructive step.
- **Measure what matters or lose it.** The optimizer deletes whatever is unmeasured. Give every rule that matters a `guard` task or a `protected` phrase.
- **A check must not reward the harness's limits.** A session with no human rewards dropping the approval step. Script the reply into the prompt, or guard it.
- **Tasks must be able to fail.** If the current target already passes everything, the loop has nothing to measure. Run two tasks by hand first, and use harder real cases, not artificial traps.

Sessions load project settings only, so user-level plugins, hooks and CLAUDE.md stay out of the score, and hooks that normally guard you are off. Sessions, `setup` and `check` get a reduced environment, so a check cannot read other variables you have exported. Sessions still run as the user with the user's files in reach: a temporary workspace is not a sandbox. Allow the narrowest tools the task needs, such as one script rather than `Bash(python3 *)`, which runs anything. When a task needs broad shell access, or `bypassPermissions`, run the whole loop in a devcontainer (`cc-devcontainer`). A tool the session is denied is a failing task, so try one task by hand with the same `claude_args` first.

Headless sessions refuse the Write and Edit tools under `.claude/` as a sensitive path, and `acceptEdits` does not lift that. When the target writes there, the score measures that refusal rather than the target. Say in the prompt to write those files through an allowed script, and read a baseline failure before believing it.

## 2. Run ratchet.py

```bash
python3 .claude/skills/skill-optimize/scripts/ratchet.py <command> optimize/<name>
```

`baseline` and `test` and each `round` take many minutes, past Bash's foreground limit. Run each one alone with `run_in_background: true` and `timeout: 7200000`, then wait for its notification. Chain nothing after it. If one is killed, run `status`. Repeat a killed `baseline` with `baseline`, or a killed round with the same edit, which is still in the file. The next command finishes a keep whose commit was killed.

| Exit | Meaning | Do |
|---|---|---|
| 0 | Done | Read the output line |
| 1 | Refused, nothing decided | Fix what it names and rerun. Change no eval file. |
| 3 | The loop must stop | Go to step 4 if a round kept an edit, otherwise report the reason |

## 3. Baseline, then rounds

Run `baseline`. It splits the tasks, runs a null pass with no target, and scores the target. Report its numbers to the user. A `note:` that the target beats no-target by nothing is normal for a thin target, so continue once a failed transcript shows the session loaded it. If none did, the target is not being reached: fix the prompts or the `install_as` path first. Exit 3 means fix the eval: sessions errored, or the noise floor is over the headroom (the target is near the ceiling, or there are too few tasks). Change the tasks, commit them, and run `baseline --restart`. That archives the attempt to `runs/attempts/` and keeps counting sessions against `max_sessions`. It keeps each task on its side of the split, and rounds and keeps carry over. `baseline` stops after two restarts: the skill may have no room a loop can measure, and that is a result to report. Before any keep, you may also commit a change to the target and restart. That file becomes the starting point, and nothing measures your change, so tell the user.

Each round:

1. Read `runs/last_run.json`, the failed transcripts it names, `runs/learnings.md` and `runs/results.tsv`. Never open `runs/test-*`.
2. Name the one behaviour costing the most, how many tasks show it, and what you expect to flip.
3. Edit the target to fix that at its root. One hypothesis, big enough to clear the floor: a rewritten section or a new capability, not a reworded line. Describe behaviour, and paste no nouns from the tasks. Removing text counts as an edit.
4. Run `round optimize/<name> --hypothesis "one line"`.
5. Append what was tried and why it did or did not work to `runs/learnings.md`, which `baseline` creates. Include discards.

| Decision | What happened | Next |
|---|---|---|
| `keep` | Above the floor, no guard lost; committed | Continue |
| `keep-shorter` | At least 10% less text with no measured loss; committed | Continue |
| `discard` | Target restored | Try a different hypothesis |
| `void` | Over 20% of sessions errored; target restored, no decision | Read a transcript, fix the cause, reapply the same edit |

A `stop:` line ends the rounds, and so does a train score of 1.000, since no failure is left to read. Either way go to step 4. After two discards in a row, sort the remaining failures before editing again: missing content, wrong check, harness failure, content present but not reached, or plain variance. Only the first is fixed by editing. A wrong check needs a new baseline, since the harness hash stops further rounds.

## 4. Held-out test and handover

Run `test`. It runs once, the starting file against the last keep on tasks nothing has read, and drops any edit no round decided on. If over 20% of its sessions error it gives no verdict, so fix the cause and rerun it, once. That rerun may pass `max_sessions` by one test. Otherwise it prints `review-for-merge` or `do-not-merge`. That number is the result. The train score was selected across rounds and reads high.

Hand the user the test delta, the diff and `learnings.md`. Tell them to review each hunk like a junior engineer's PR and cherry-pick, because a passing score does not make a deletion safe. Never merge the branch yourself.
