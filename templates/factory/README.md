# factory/

This project runs a small software factory (cc_tool): work orders go in, a line of
agent stations builds and checks them, and a human approves the spec and the merge.

## What is here

- `orders/FO-NNN-<slug>.md`: one work order each. The file is the spec (intent,
  acceptance criteria, notes). Create one with `cc-factory new "Title"`; the id is the
  next free number.
- `line.json`: the line, i.e. the stations an order passes through (plan, build, test,
  verify, review), the model and effort per station, the retry budget, and the gates.
  A line may set `"run"` and `"test"`, the commands the verify station uses (plain one-line
  strings, part of what the spec gate approves); without them verify reads the README,
  Makefile, package.json or pyproject.toml as they were at the lane's base commit, and it
  never takes run instructions from the build or test station.
  Several lines can live in one file; an order picks one with `line:` in its frontmatter.
  `default` has hidden scenarios (verify); `quick` has plan, build, test and review only:
  no holdout, the spec gate passes by itself (`"spec": "auto"`), the merge stays yours.
  An order keeps the line it was created on: `cc-factory lane`, `run-args` and `record` refuse
  when its `line:` was changed afterwards. To use another line, reject the order and make a new one.
  A missing or broken `line.json` falls back to the built-in lines.
- `order-template.md`: the skeleton `cc-factory new` fills in. Placeholders: `{{id}}`,
  `{{title}}`, `{{priority}}`, `{{created}}`, `{{line}}`. Edit it to suit the project.

Commit `factory/`: orders and the line are source. Nothing about an order's progress is
stored here.

## Where the lifecycle lives

An order's status (queued, spec, approved, building, verifying, review, done; or blocked,
rejected, failed), its station, its gate and its lane are an append-only ledger:
`<git common dir>/cc_tool/factory/ledger.jsonl`. It sits outside the working tree (so it is
never committed), is shared by the main checkout and every worktree, and is written only by
`cc-factory`. cc-dash reads it to draw the Factory floor.

The gates are decisions, not statuses: `approved` and `done` come only from
`cc-factory approve` (`mark` refuses them and `review`), and a `status:` in an order's
frontmatter counts only as `queued` or `spec`. On a line whose spec gate is `human`, `lane`
and `run-args` also need the spec gate's pass in the ledger, and `run-args` and `record`
refuse once the order body, the line or the threshold differ from what was approved (a hash
is stored at approval). `approve` and `reject` refuse when run under Claude Code (an agent's
Bash tool, or a `!` command): run them in your own terminal, where they show a random code
on /dev/tty and ask you to type it back there. The gates are Linux-only for now: without
/proc (macOS) they refuse outright. That stops an agent's tool calls; a process deliberately
evading it as your user (detached from Claude Code, driving a terminal of its own) could
still answer, append to the ledger or run a modified CLI. Only `record --workflow-run` (the run
Claude Code saved, tied to this order's lane and approved spec) opens the merge gate; a
result on stdin or in a file records only failed or blocked. `approve --gate merge` passes
exactly the reviewed lane head: the branch unmoved since the review, that commit merged into
your checkout's HEAD (or `--into REF`); it records the merge sha.

## Holdout scenarios

`.factory-holdout/FO-NNN/*.md` holds scenarios the build agents are told never to read:
how the finished feature should behave, written as observable trajectories. The directory is
git-ignored (`cc-factory init` adds it), so it is absent from every lane worktree; only the
verify station is given its absolute path. Nothing enforces the rest: no hook stops another
station from reading it. Verify's reasons never reach build (the script passes build only
scenario ids, the spec's own criterion text and a failure category), but Claude Code saves
the full workflow result under `~/.claude/projects/`. Create the directory with
`cc-factory holdout FO-NNN`. `cc-factory mark FO-NNN spec` wants at least 3 scenarios
(`--no-holdout` to skip); with none checked, the order still reaches the merge gate, but `record`
recommends `needs-scenarios` and the briefing says nothing hidden checked it. Verify runs
the lane's code only inside a bwrap sandbox the line builds (the holdout, `~/.claude` and the
temp dirs hidden, no network, only a throwaway copy writable); without bubblewrap (macOS, say)
it only reads the code, and `record` recommends `needs-human-check`, never `merge`.
A line with no verify station (`quick`) has no hidden scenarios by design: `record` recommends
`merge`, and the briefing says its tests and the review are all the checking there was.

## The plan document

The plan station's plan is also written into the lane as `docs/superpowers/plans/<created>-fo-nnn-<slug>.md`
(a `**Spec:**` line naming the order file, one `### Task N:` per step, one checkbox each), committed
as `FO-NNN: plan` and ticked as build finishes each step, so the Work view can show progress. If
git ignores that path it stays uncommitted. Progress is never read from the order file.

## Commands

```
cc-factory init [--force]                    scaffold this directory, the ignores and the ledger
cc-factory new "Title" [--priority p1|p2|p3] [--line NAME]
cc-factory list [--all] [--json]             the orders and where each one stands (alias: status)
cc-factory show FO-NNN [--events N]          one order in full, with recent ledger events and the merge-gate briefing
cc-factory approve FO-NNN [--gate spec|merge] [--into REF]    pass a human gate (not under Claude Code)
cc-factory reject FO-NNN [--gate spec|merge] [--rework]
cc-factory lane FO-NNN                       create the branch factory/FO-NNN-<slug> and its worktree
cc-factory holdout FO-NNN                    create and print the holdout directory
cc-factory run-args FO-NNN                   the Workflow arguments for the factory-line script
cc-factory record FO-NNN --workflow-run RUNID  write a finished run's result to the ledger and print the merge-gate briefing (--file F or JSON on stdin: failed or blocked only)
cc-factory mark FO-NNN [STATUS] [--station S --state start|pass|fail|waiting] [--note TEXT] [--no-holdout]
```

`cc-factory` never merges and never pushes. Merging is yours: after a run reaches review,
`git merge --no-ff <the reviewed sha record printed>`, then `cc-factory approve FO-NNN --gate merge`
in your own terminal. Station verdicts, "tests passed" included, are a model's report; no
code runs the tests. See `docs/guides/factory.md` in the cc_tool repository for what the
code enforces and what is only asked.

## The /factory skill

Inside Claude Code the `/factory` skill drives the same loop:

- intake: turn an idea into an order and a spec (`/factory intake ...`)
- run: build an approved order in its own lane (`/factory run FO-NNN`)
- status: show the floor (`/factory status`)
- gate: walk you through the waiting spec and merge decisions (`/factory gate`)

The full guide is `docs/guides/factory.md` in the cc_tool repository.
