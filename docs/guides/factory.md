[← README](../../README.md)

# Software factory (orders, a line of stations, two human gates, a ledger)

An optional per-project delivery line for coding agents. You write intent and approve the spec; a declared line of stations (plan, build, test, verify, review) does the production work in an isolated git lane; you read the result and do the merge. Every transition lands in an append-only ledger and shows up live on the **Factory floor** in cc-dash. This is the "software factory" pattern: intake, a specification kept apart from execution, an isolated build environment per task, verification as the binding constraint, two human gates, and an audit trail (Augment, *What is a software factory*). The hidden acceptance scenarios, kept away from the building agents, come from StrongDM's Software Factory (holdout sets, scored as "satisfaction"); how well they are kept away is in the next section.

```bash
cc-setup /path/to/project --factory   # normal setup + the factory scaffold   (or: cc-factory init /path)
                                      # init --force → re-copy README, line.json, template, workflow

# in a Claude Code session inside the project (the `factory` project skill):
/factory intake "Add CSV export to the reports page"   # order FO-001 + a written spec; stops at the spec gate
/factory run FO-001                                    # lane + the factory-line workflow + record the result
/factory status                                        # the board: every order, its station, its gate

# at the gates, in a shell (cc-factory is the only writer of the ledger):
cc-factory approve FO-001                     # spec gate: the spec is good, the line may run
cc-factory approve FO-001 --gate merge        # after YOU merged factory/FO-001-... into your branch
```

**Why a factory and not one long chat.** One agent in an editor checks its own work and loses the thread when the context rolls. The factory separates the jobs: the agent that builds is not the agent that verifies or reviews (review runs on a different model from build, except on late rework attempts, see [The line](#the-line)), the acceptance scenarios live where the builder is told not to look (nothing enforces that yet, see [What code enforces](#what-code-enforces-and-what-is-only-asked)), each order gets its own branch and worktree so lanes cannot trample each other, and the whole history of an order is one file plus one ledger you can read afterwards. People keep the two decisions that matter: *what* to build (the spec) and *whether it lands* (the merge). Review capacity, not generation, is the bottleneck of agent-written code, so the line spends its effort making what reaches that gate worth reading.

**Boundaries.** `cc-factory` is a stdlib script, not a daemon; there is no MCP server and no cloud dependency; it never merges, pushes or deletes anything; cc-dash shows the factory read-only. A work order is a markdown file in the project, so it is versioned with the code it describes. The ledger is not: it lives in the git directory.

## What code enforces and what is only asked

Enforced by `cc-factory` (code, not a prompt):

- `approve` and `reject` (both gates, `--rework` included) refuse when run under Claude Code: an agent's Bash tool, or a `!` command typed into Claude Code. The message is `BLOCKED: ... this cc-factory was not started from your own terminal (<why>). Suggestion: run it from your own terminal: ...`. With `/proc` (Linux) it applies `recording_problem()` from `templates/hooks/cc_procs.py`, the rules control mode starts by (Claude Code's variables, a claude process among the parents or the session and process-group leaders' parents, a detached terminal driver, a fresh session after a re-parent), and it also needs a controlling pseudo-terminal of its own, so cron, CI or a pipe cannot run it either. **The gates are Linux-only for now:** without `/proc` (macOS) nothing can tell your terminal from an agent's, so `approve` and `reject` refuse outright ("factory gates need Linux's /proc ... in this release they work on Linux only"), as control mode does. Past the process rules, the decision needs a random 6-character code that `cc-factory` writes only to `/dev/tty` (never to stdout, stderr or the ledger), typed back there: it opens `/dev/tty` itself (never stdin, which a pipe can fill), discards anything typed ahead of the question, compares in constant time, and refuses when there is no controlling terminal, nothing is typed within 300 seconds, or the text is not the code. If `cc_procs.py` cannot be loaded it refuses. There is no bypass flag.
- `mark` refuses `approved`, `done` and `review`. `mark ID spec` refuses with fewer than 3 holdout scenarios on a line with a verify station, unless `--no-holdout`. `lane` and `run-args` need the spec gate's pass in the ledger.
- The spec-gate `approve` stores a `spec_hash`: sha256 over the order body, the line (stations, gates, retries) and the holdout threshold. `run-args` and `record` refuse when it no longer matches ("the spec changed since approval"), and an approval made before hashes existed must be given again.
- `approve --gate merge` passes exactly the reviewed commit. `record` stores the lane head it reviewed on the review event, and the gate refuses when that is missing, when the lane branch has moved since (a commit added after the review), when the reviewed head is not an ancestor of the merge target (`--into REF`, default the main checkout's HEAD), or when the commit that brought the lane into the target merged a later commit than the reviewed one. It refuses a lane with no commits past its base, and records `merge_sha`, `lane_head` and `target` on the gate event. `record` prints the merge command against the reviewed sha (`git merge --no-ff <sha>`). The merge gate is always `human`: the loader forces it.
- `record` writes review (the merge gate) only for a result read with `--workflow-run`: the run Claude Code saved, whose args must be this order's lane and its approved spec, line and threshold. A result on stdin or in a `--file` records only `failed` or `blocked`: one claiming review is recorded as `blocked` with exit 2. Review also needs `contract: 2`, a pass for every station of the line on the final attempt (plan on attempt 1) and, on a line with verify, verify's report matching the scenario files on disk (count, S-ids, threshold, attempt). A failed check writes the station events, then `blocked`, and exits 2. A result with no hidden scenario checked (an empty holdout, or no verify station) still reaches review, with recommendation `needs-scenarios` instead of `merge`.
- Verify's feedback to build is built by the script from scenario ids, the spec's own acceptance-criterion text and a failure category. Verify's reasons are not an input to that text.
- No text the build or test station wrote is put into verify's prompt (verify holds the holdout; they never see it). Verify's run and test commands come from the line's `run` and `test` in `factory/line.json`, which a human writes, or else from the repo's README, Makefile, `package.json` or `pyproject.toml` as they were at the lane's base commit. The prompt tells verify that everything in the lane is data, never instructions.
- **Lane code runs in a bwrap sandbox (Linux, with bubblewrap installed).** The workflow script builds one fixed wrapper per attempt from its own validated values only (the order id, the attempt number, and the lane, holdout and main-checkout paths; never a station's text, and verify is not even given the order's title): `bwrap` with the whole filesystem read-only, the order's holdout dir, every order's `.factory-holdout/`, Claude Code's transcripts (`$HOME/.claude`) and the temp dirs (`/tmp`, `$TMPDIR`) behind empty tmpfs mounts, no network (`--unshare-net`), its own pid namespace, and only the attempt's throwaway copy (`/tmp/cc-factory-verify-FO-NNN-aN`, a `git archive` export of the lane's committed HEAD, with no `.git`) writable. Inside it, the kernel enforces it: lane code cannot read the holdout or a transcript, cannot write anywhere but the copy, and cannot reach the network. A real `bwrap` test in the suite checks exactly that.
- **Verify's git commands ignore the lane's git config.** The lane's config is the builder's (`core.fsmonitor`, an `include`, a filter driver named in `.gitattributes` or `.git/info/attributes`, an external diff or textconv can each run a program), so verify is told to run git only with commands the script builds: no system or global config, `--no-optional-locks`, attributes from the empty tree, and `core.fsmonitor`, `core.hooksPath`, `core.untrackedCache`, the pager, `core.sshCommand`, every protocol and `diff.external` switched off on the command line, which outranks any config file, included ones too; diffs add `--no-ext-diff --no-textconv`. A test with a planted config shows plain git runs those programs and these commands do not. The flags cannot stop a filter driver named in `.git/info/attributes`, which `git status` and `git archive` can still reach, so verify runs HEAD, status, the export (`git archive` piped into `tar -x --no-same-owner --no-overwrite-dir`), the diff and the base-commit file reads only inside the sandbox.
- **No transport, no lazy fetch.** Repo config can re-enable a transport (`protocol.<name>.allow=always` outranks `protocol.allow`), and a partial-clone promisor turns a missing object into a fetch, so a planted `ext::` remote would run a program, outside the sandbox and in static mode too. Every verify git command therefore runs with `GIT_ALLOW_PROTOCOL=` (the empty list: it overrides every `protocol.*.allow` setting and allows no transport at all), `GIT_PROTOCOL_FROM_USER=0`, `GIT_NO_LAZY_FETCH=1` (honoured from git 2.44) and `-c protocol.<name>.allow=never` for ext, file, ssh, git, http and https. A missing object makes the command fail; it is never fetched, and verify reports `git: objects-missing`, which makes its verdicts static (`needs-human-check`, never `merge`). `-c extensions.partialClone=` is accepted by git but does not stop the lazy fetch (checked on 2.43), so it is not relied on. A test plants exactly that lane (an `ext::` promisor, the transport re-enabled, a blob deleted): plain git runs the program, none of verify's commands do, and those that need the blob fail closed.
- Without a sandbox (static mode), verify runs no git command outside one except the guarded `rev-parse` and a commit-to-commit diff from the objects already present. That verify uses these exact commands is a prompt instruction.
- Verify reports the lane worktree's HEAD and `git status --porcelain` before it made the copy and after it deleted it (without a sandbox, only HEAD: its status reads `not-run`). The script compares them: a change, a missing report, or an undeleted copy ends the run `blocked` with reason `lane-tampered` and recommendation `reject`, and no further build attempt starts.
- Verify also reports whether it ran lane code through the wrapper (`executed`) and how the wrapper's probe went (`sandbox`: `ok`, `missing` when there is no `bwrap`, `failed` when the probe did not exit 0). Without a working sandbox it runs no lane code and judges by reading it; such static verdicts can still fail an attempt, but the line and `record` never recommend `merge` on them: the merge gate gets `needs-human-check`. A result without these fields counts as static.
- A station whose agent returns nothing (an API error after retries, or a skip) ends the run `blocked` with reason infra and recommendation `rerun`, and the attempt is not counted.
- `record` notes (`flag: "holdout-leak"`) any 5-word overlap between the rework text build got and a scenario file, naming the attempt, station and S-id counts, never the text. It flags; it does not stop anything.

Only asked of the model, not enforced: that build, test, review and plan never read `.factory-holdout/` (no hook stops them); that stations are read-only where they should be; that no station merges or pushes; that a station stays in its lane (write-guard treats every worktree as the project). Every station verdict, "tests passed" included, is a model's report: no code runs the tests or reads an exit code. Review's own test re-run is also a model's report.

**What the sandbox does not cover.** It is OS-level only for lane code that verify runs through the wrapper; *that verify uses the wrapper* (and only the unchanged wrapper) is a prompt instruction, as is everything verify itself does with its own Bash tool. A hook that binds Bash calls from agents labelled `FO-NNN:verify` to the wrapper is on the roadmap with #10. On macOS, or anywhere without bubblewrap, there is no sandbox: verify runs no lane code and the merge gate gets `needs-human-check`. Inside the sandbox, lane code still sees the scenario inputs verify hands it and can shape its output to them within one run; it cannot store them past the run (the copy is deleted, nothing else is writable, there is no network), but scenario-specific rework feedback is still the main way a hidden check becomes a visible one. The structural fix for that is the two-tier holdout on the roadmap (#10d below): a final set of scenarios judged only on the last attempt, which never produces feedback.

**The same-user limit.** The process rules and the code stop an agent's tool calls: a Bash command runs under Claude Code, and piping an answer in, or typing one blind, does not match a code shown only on the terminal. They cannot stop a deliberately evasive process running as you that first gets past the process rules (it detaches from Claude Code, double-forks and scrubs its environment, as control mode's limit describes) and then drives a pseudo-terminal of its own, where it can read the code and type it back. Such a process can also append to the ledger with a shell (write-guard exempts `.git/cc_tool`), write a saved run file of its own under `~/.claude/projects/`, or run a modified copy of the CLI. This is the same limit control mode documents in [commands](commands.md); for an agent you do not trust, use the devcontainer. Claude Code also saves the full workflow result, per-scenario reasons included, under `~/.claude/projects/.../workflows/` and in `tasks/*.output`, where a later run's build agent could read it.

## Setup

```bash
cc-setup /path/to/project --factory   # or, on a project that is already set up:
cc-factory init /path/to/project      # idempotent; present files are kept unless --force
```

`cc-factory` is a `bin/` command like the others, on your PATH after cc_tool's `install.sh`. `init` scaffolds `factory/README.md`, `factory/line.json` (the default line), `factory/order-template.md` and `factory/orders/`; appends `.factory-holdout/` and `.claude/worktrees/` to `.gitignore` (once, under one comment line, even when the file has no final newline); creates the ledger directory; and copies `factory-line.js` into the project's `.claude/workflows/`. Commit `factory/` and `.gitignore`: orders and the line are source.

The workflow is copied because `cc-setup` and `cc-update-project` do not distribute saved workflows (cc_tool's own live only in its checkout); `init` is the one place that installs it. Re-running `init` reports it as up to date, refreshes a copy that matches an older shipped version, and leaves a locally edited copy alone unless `--force`. The `/factory` skill ships under `templates/skills/factory/`, so a plain `cc-setup` or `cc-update-project` deploys it to every project like any other skill; `cc-update-project` never passes `--factory`, so it never re-runs `init`. A project with no copy of the workflow still works: `/factory run` falls back to `Workflow({scriptPath})` using the path in `cc-factory run-args`.

## Orders

A work order is one file, `factory/orders/FO-NNN-<slug>.md`. **The file is the spec; the ledger is the lifecycle.** Ids are `FO-` plus a zero-padded sequence (three digits minimum), unique per repo.

```markdown
---
id: FO-003
title: Add CSV export to the reports page
priority: p2            # p1 | p2 | p3
created: 2026-10-25
line: default           # name of a line in factory/line.json
status: queued          # optional: queued or spec only; the ledger overrides it
---
## Intent
Why this exists and for whom.
## Acceptance criteria
- [ ] observable condition
## Notes
Constraints, links, prior art.
```

`cc-factory new "Add CSV export"` writes this file from `factory/order-template.md` (next free number, slug from the title; edit the template to suit the project) and a `created` ledger line. The frontmatter `status` is only where the fold starts, and only `queued` or `spec` count there: any other value (a gate status such as `approved` or `done` included) starts the order at `queued`, so an order file cannot pass a gate by saying so. The first ledger event that carries a status overrides it. The file name's slug may not contain control characters; a name that does is not an order. Reads are bounded: at most 200 orders (the highest-numbered ones) and 256 KiB per file.

**Lifecycle.** Statuses in order, then the three off the path:

| Status | Meaning |
|---|---|
| `queued` | The order exists; the spec is not finished. |
| `spec` | Spec written, waiting at the human **spec gate**. |
| `approved` | The spec gate passed; ready to run. |
| `building` | The line is running (a workflow run is in flight). |
| `verifying` | The verify station is running against the holdout scenarios. Phase 0 does not write it live: a run stays `building` until `cc-factory record` replays its stations and writes the final status, and the floor shows the live station from the workflow's agent labels. |
| `review` | After a run: every station passed on the final attempt and the order waits at the human **merge gate**, with recommendation `merge`, or `needs-scenarios` when no hidden scenario checked it. Written only by `record`; `mark` refuses it. |
| `done` | A human merged. `cc-factory` never merges; `done` is recorded by `approve --gate merge`. |
| `blocked` | Needs a human or a rerun: bad arguments, an infeasible spec, a missing environment piece, an **infra** failure (a station's agent returned nothing after an API error such as a 429, a session limit or overload, or a skipped station; this does not use up an attempt and the recommendation is `rerun`, which continues from the lane's commits, or, from the same Claude Code session only, resume the workflow run with `resumeFromRunId` to reuse finished stations), a **lane-tampered** stop (the lane worktree changed while verify ran lane code in its throwaway copy: inspect the lane and rewrite the scenarios first), or a `record` check that failed. Re-runnable once fixed. |
| `rejected` | A human said no, at either gate. Terminal. |
| `failed` | The line ran out of retries. Re-runnable: sharpen the spec, then `/factory run` again. |

`cc-factory list` shows `ID PRI STATUS STATION GATE LANE TITLE` and hides `done` and `rejected` unless you pass `--all`; `--json` is the same data for tooling. `cc-factory show FO-003` prints one order: status, line, rework count, gate, lane, last run id, how many holdout scenarios exist, each station's state, and the last 10 ledger events (`--events N`).

## The line

`factory/line.json` declares the stations an order passes through. A missing or broken file falls back to the built-in default; a valid line in the file replaces the same-named default line, so a project can add a second line (a `docs` line without the verify station, say) and point an order at it with `line: docs` or `cc-factory new ... --line docs`.

```json
{"version": 1, "lines": {"default": {"stations": [
  {"name": "plan",   "title": "Plan",   "model": "opus"},
  {"name": "build",  "title": "Build",  "model": "sonnet", "effort": "medium"},
  {"name": "test",   "title": "Test",   "model": "sonnet"},
  {"name": "verify", "title": "Verify", "model": "opus", "holdout": true},
  {"name": "review", "title": "Review", "model": "opus"}
], "gates": {"spec": "human", "merge": "human"}, "retries": 1}}}
```

A line may also set `"threshold"`, the holdout pass mark, a number above 0 and up to 1 (default 1: every scenario). It is part of what the spec gate hashes.

A line may set `"run"` and `"test"`: the commands the verify station uses to run the program and its tests (for example `"test": "make test"`). They are kept only as plain one-line strings of at most 300 characters that do not start with `-`; anything else is dropped. They are the only run instructions verify is handed, since text from the build or test station never reaches it, and they are part of what the spec gate hashes: changing one after approval needs a new approval. Without them, verify reads the repo's own README, Makefile, `package.json` or `pyproject.toml` as they were at the lane's base commit.

- **Station** = `name` (lowercase, unique, at most 12 per line), `title` (shown on the floor), `model` (an alias such as `opus` / `sonnet` / `haiku` or a full id; empty inherits the session's), optional `effort` (`low` | `medium` | `high` | `xhigh` | `max`), and `holdout: true` on the station that is handed the scenario directory. The workflow runs `plan`, `build`, `test`, `verify` and `review`; any other name is skipped with a log line. A Sonnet station asked for `low` effort runs at `medium`.
- **Order of work**: plan runs once, first, and can rule the spec infeasible (the order becomes `blocked` with the reasons and a recommendation to reject or revise). Then build, test, verify and review run in line order; the first failure stops that attempt and its feedback goes to the next attempt's build. A station whose agent returns no result (an API error after retries, or a skip) is not a failure of the work: the run is `blocked` for infra, no attempt is used, and the summary says rerun.
- **Models follow the repo's routing**: Opus plans, verifies and reviews (judgment); Sonnet builds and tests (it carries out a written spec). Review re-runs the tests itself, reports the output it observed, and fails if they do not pass, so the binding pass/fail judgement runs on Opus on the default line (the review passes only if it ran the tests and they passed). If a line gives build and review the same model family, the workflow switches review to the other one and logs it. From attempt 3 (so only with `retries` of 2 or more) a build station that is not on Opus runs on Opus, and review stays on Opus too, so on those attempts build and review share a family. The test station's prompt carries test anti-patterns (tautological and implementation-coupled tests) adapted from mattpocock/skills `tdd`. See `## Model routing` in the managed CLAUDE.md block.
- **Retries** (0 to 3, default 1): attempts = 1 + retries. A failing build, test, verify or review sends the order back to build with that station's feedback (test and review feedback is those stations' own report; verify's is the script-built text described under Holdout scenarios); the lane keeps the earlier attempts' commits and build continues from them. When attempts run out the order is `failed`, and the summary names the station that gave up. Each fail-then-restart is one **rework** on the floor and in `show`.
- **Gates**: `spec` is `human` (default) or `auto`; with `auto`, `lane` and a run do not wait for an approval. `merge` is **always** `human`: the loader forces it, whatever the file says.

## Lanes

Each order builds in its own lane, a branch plus a git worktree, so parallel orders cannot trample each other and the main checkout stays yours.

```bash
cc-factory lane FO-003            # branch factory/FO-003-csv-export, worktree .claude/worktrees/FO-003-csv-export
cc-factory lane FO-003 --base v1  # branch from another ref (default: the main checkout's HEAD)
```

The branch is `factory/FO-NNN-<slug>`; the worktree is `<main checkout>/.claude/worktrees/FO-NNN-<slug>` (git-ignored by `init`, the same directory Claude Code uses for its own worktrees). Run it from the main checkout or from any worktree: `cc-factory` resolves to the main checkout either way. The base commit, branch and absolute worktree path are written to the ledger as a `lane` event, and the last stdout line is the worktree path. Running `lane` again reuses the lane, so a rework run builds on the same branch.

`lane` **refuses** (exit 2) until the spec gate has passed: when the line's spec gate is `human`, the order must be `approved` or later (`building`, `verifying`, `review`, `failed`, `blocked`) **and** the ledger must hold the spec gate's pass from `cc-factory approve` (a status set with `mark`, or written in the order file, does not count), and `done` and `rejected` orders always refuse. It also refuses when the commit the lane would check out tracks files under `.factory-holdout/`, and it checks that the ledger can record the lane (the exact worktree path) before git creates anything. Lane agents are told to work only inside the worktree, to commit on the lane branch with messages starting `FO-NNN:`, and never to push, merge, rebase or touch the main checkout. That is a request, not a fence: write-guard treats every worktree as the project, so nothing stops a station from writing into another lane. The project's `bash-guard.py` is the enforced boundary for protected branches (see the Permissions note in [workflows](workflows.md)).

## Holdout scenarios

The acceptance criteria in the order are visible to the builder; the *scenarios* are not.

```bash
cc-factory holdout FO-003         # creates .factory-holdout/FO-003/ (mode 0700), prints its path
$EDITOR .factory-holdout/FO-003/S01-comma-in-a-cell.md   # one scenario per file: a user trajectory and its observable outcome
```

`.factory-holdout/` is git-ignored, so it is absent from every lane worktree by construction. `holdout` refuses (exit 2) when files under `.factory-holdout/` are already tracked (say so, with the `git rm -r --cached` that untracks them) or when `git check-ignore` says the path is not ignored; `lane` refuses when the lane's commit tracks any of them, `run-args` refuses when the lane's `HEAD` does, and the workflow refuses to start if the holdout directory sits inside the lane. The run hands the absolute path to the **verify** station only (the station marked `holdout: true`); plan, build, test and review are not given it, and are told never to read, list or ask about a `.factory-holdout` directory. That instruction is the only fence: no hook stops them.

**Why hide them.** An agent that can read its own acceptance tests grades its own homework: it builds to the test instead of the intent ("teaching to the test"), and a green run then says little about the software. Scenarios the builder has never seen are the closest thing to an independent check. Three rules keep that true:

- verify judges each scenario (satisfied or not, with its evidence) but does **not** compute the score; the line does, so the model cannot round its own grade up;
- **verify's feedback to build is built by the script, not written by verify**: build gets only the failing scenario ids, the spec's own acceptance-criterion text (looked up by index) and a failure category. Verify's per-scenario reasons stay in the workflow result for the human at the merge gate, and cannot reach build because the script never passes them on. Limit: feedback specific enough to act on still spends some of the holdout's independence, and the two-tier holdout (a feedback set and a final set) is not built. The other side of the trade: when the spec is silent on, or contradicts, what a scenario expects, build cannot learn the answer from this feedback, so the rework does not converge and the run fails; then you sharpen the spec. A 2026-10-26 re-run of a trap order went exactly this way: verify caught the hidden case on both attempts, and nothing wrong reached the merge gate;
- the scenario directory is git-ignored, so it is not in a lane's checkout.

`record` also flags, as a ledger note, any 5-word overlap between the rework text build got and the scenario files (words the spec itself contains do not count). The note names the attempt, station and S-ids with counts, never the text. It is a flag for you to read, not a stop.

**Satisfaction** is the fraction of scenarios verify judges satisfied. The default pass mark is all of them (the line's `threshold`); a scenario file with no verdict counts as unsatisfied; below the mark, verify fails and the order reworks. With no scenarios (no directory, or an empty one), or with 0 scenarios actually checked, verify passes **vacuously**, satisfaction is `null`, and `record` recommends `needs-scenarios`, never `merge`. On a line with verify, `cc-factory mark ID spec` refuses with fewer than 3 scenarios unless you pass `--no-holdout` (recorded on the event, and the briefing then says no hidden scenario checked the work). `record` refuses a result whose scenario count, S-ids, threshold or attempt differ from the files on disk. Satisfaction is a field on the review status event; the per-scenario reasons are only in the workflow result.

Phase 0 enforces the hiding by location (git-ignored, outside the lane, path given only to verify) and by instruction, not by a guard: a build agent determined to read `../../.factory-holdout/` could. See [Not done yet](#not-done-yet).

## Gates

Two human control points, both recorded in the ledger. `approve` and `reject` (both gates, `--rework` included) refuse with `BLOCKED: ... run it from your own terminal` when run under Claude Code: an agent's Bash tool, or a `!` command typed into Claude Code. From your terminal (Linux only for now) they show a random code and ask you to type it back. That stops an agent's tool calls; it is not a barrier against a process running as you (see [the same-user limit](#what-code-enforces-and-what-is-only-asked)).

- **Spec gate** (`spec` to `approved`). You read the spec the interview produced and either pass it or send it away. The spec becomes every station's prompt, and the line's agents edit and commit in `acceptEdits`, so this read is also the check against instructions planted in a GitHub issue the order came from. Approving stores a hash of the order body, the line and the threshold: edit any of them afterwards and `run-args` and `record` refuse until you approve again.

  ```bash
  cc-factory approve FO-003 --note "scope is right"      # status approved; the line may run
  cc-factory reject  FO-003 --note "too broad, split"    # status rejected
  ```
- **Merge gate** (`review` to `done`). `record` prints the briefing from facts: `git diff --stat base...branch` and `git log --oneline base..branch` from the lane; satisfaction and the S-ids; review's verdict, summary, every should-fix item and its full next steps; and review's own test re-run. Review's summary, should-fix items and next steps print whole; the commit and diffstat lists stop at 30 and 40 lines ("... N more"). The terminal gets the whole briefing; the ledger keeps it redacted and capped at 12000 characters on the review event, and `cc-factory show ID` prints it again. At the merge gate the recommendation is `merge`, `needs-scenarios` (no hidden scenario was checked) or `needs-human-check` (verify could not run the lane's code in the sandbox and judged by reading it); a run that did not reach it recommends `rework` (failed), `reject` (the plan judged the spec infeasible) or `rerun` (infra). A recommendation is advice, and the station verdicts in it are model reports. **You** merge the reviewed commit with whatever process the project uses (a local merge, a pull request); `cc-factory` never touches your branches. Merge the sha `record` printed, not the branch name: a branch that moved after the review is refused at the gate.

  ```bash
  git merge --no-ff 3f9c2e1d…                           # the reviewed lane head record printed; your call, your tooling
  cc-factory approve FO-003 --gate merge                 # asks you to type back a code it shows; checks exactly the reviewed head is merged into HEAD, records the merge sha: status done
  cc-factory reject  FO-003 --gate merge --rework --note "export ignores filters"   # back to approved; the lane is reused
  cc-factory reject  FO-003 --gate merge                 # not merging: status rejected
  ```

Without `--gate`, the gate is the merge gate when the order is in `review` and the spec gate otherwise; `--rework` applies to the merge gate only; a finished (`done` or `rejected`) order takes neither. `approved` and `done` are written only by `approve`: `cc-factory mark` refuses them (and `review`, which only `record` writes), an order file's frontmatter cannot set them, and `lane` and `run-args` look for the spec gate's pass event itself, not just the status. The recommendation is advice: nothing in the line merges, pushes or opens a pull request.

## The ledger

`<git common dir>/cc_tool/factory/ledger.jsonl`, for a normal checkout `.git/cc_tool/factory/ledger.jsonl`. The git common dir is shared by the main checkout and every linked worktree, so a lane and the main checkout write and read **one** ledger. It is append-only and written only by `cc-factory`; cc-dash and the skill only read it. One JSON object per line:

```json
{"t": 1761400000.12, "order": "FO-003", "event": "status", "status": "building",
 "station": "build", "state": "start", "run_id": "wf_ab12cd",
 "branch": "factory/FO-003-csv-export", "worktree": "/abs/path", "note": "", "by": "cc-factory"}
```

`event` is `created`, `status`, `station`, `gate`, `lane` or `note`; `state` is `start`, `pass`, `fail` or `waiting`; gate events carry `gate: spec|merge`; `lane` events carry the lane's `base` commit. `run_id` and `branch` are single lines; `worktree` is a path, kept with its spaces, without control characters, at most 1024 characters. Readers ignore unknown keys and skip lines that do not parse, so the format can grow. An order's current status, station, gate and rework count are not stored anywhere: they are **folded** from these events (plus the order file) every time, so there is no second source of truth to drift.

**Safety.** The ledger directory is created `0700` and the file `0600`; `cc-factory` refuses a ledger that is a symlink, has more than one link, or is not owned by you, and readers apply the same ownership rule. Notes are clipped to 500 characters and redacted for secret shapes (API keys, tokens, private keys, `password=...` pairs) before they are written, and order text is never copied into the ledger. **Never put secrets in a note, an order or a scenario.** Reads are bounded (the last 4 MiB of the ledger).

## The /factory skill

The `factory` project skill is the conversational front for `cc-factory`: it asks, writes and runs; the script records. Four operations:

| Operation | What it does |
|---|---|
| `/factory intake` | Takes your idea or a GitHub issue (`gh issue view`), asks three to five questions (who it is for, what observable result means done, what is out of scope), creates the order with `cc-factory new`, fills Intent / Acceptance criteria / Notes, writes 3 to 8 holdout scenarios as `S01-<slug>.md`, marks the order `spec`, and shows you the order file (not the scenarios). It tells you to run `cc-factory approve` in your own terminal; it cannot (the command refuses under Claude Code), and it is told not to try a workaround. `mark spec` needs 3 scenarios unless `--no-holdout`. |
| `/factory run ID` | Needs status `approved` (or `failed` / `blocked` for a rerun). Makes or reuses the lane, reads `cc-factory run-args`, marks `building`, launches the `factory-line` workflow (`Workflow({name})`, or `Workflow({scriptPath})` when the project has no copy; it runs in the background and the session waits for its notification), checks the run picked up its args, then runs `cc-factory record ID --workflow-run <runId>`, which reads the result Claude Code saved (no heredoc, no piping, and never through a file in the scratchpad, which the line's agents share), failed and blocked runs included. It ends with the briefing `record` printed and the commands for you to run; on `blocked` for infra it says rerun, or resume with `resumeFromRunId` from the same session, on `needs-scenarios` that no hidden scenario checked the change. |
| `/factory status` | `cc-factory list`, or `show ID` for one order: stations, gate, lane, run id, recent events. |
| `/factory gate` | Finds the orders whose gate is waiting. At the spec gate it shows the order and asks approve or reject; at the merge gate it gives the review summary, the lane's `git diff --stat` and the merge commands. It records exactly the decision you make, never one for you. |

Four invariants hold in every operation. They are instructions to the model, except where the CLI refuses on its own (the gate commands under Claude Code, `mark`, `lane`, `run-args`): the CLI owns the lifecycle (the skill never edits the ledger or writes a status into an order file); the holdout stays hidden (scenario text never goes into the order file, a plan, an agent prompt, a commit message or a chat summary, and scenarios are referred to by count and S-number only); humans merge (the skill never merges, pushes, rebases or deletes a lane unless you ask in that turn); and a `BLOCKED:` refusal is relayed with its suggestion, never worked around.

### A worked session

```text
$ cc-setup ~/work/shop --factory
...
── Factory ──────────────────────────────────────────────────────────
Setting up the software factory in: /home/me/work/shop
  ✓ factory/README.md written
  ✓ factory/line.json written
  ✓ factory/order-template.md written
  ✓ factory/orders/ created
  ✓ .gitignore: added .factory-holdout/, .claude/worktrees/
  ✓ ledger directory /home/me/work/shop/.git/cc_tool/factory (0700)
  ✓ .claude/workflows/factory-line.js copied

> /factory intake Add CSV export to the reports page
  FO-001  factory/orders/FO-001-add-csv-export-to-the-reports-page.md
  Questions: which reports? quoting of commas and newlines? a row limit?
  ... spec written into the order file; 3 holdout scenarios (S01 to S03) in .factory-holdout/FO-001/
  FO-001 is at the spec gate: read the order, then run cc-factory approve FO-001.

$ cc-factory approve FO-001
FO-001  spec gate passed: approved (spec sha256 ...)

> /factory run FO-001
  FO-001  lane created: branch factory/FO-001-add-csv-export-to-the-reports-page
  workflow factory-line (run wf_8f3a21): Plan > Build > Test > Verify > Review
  cc-factory record FO-001 --workflow-run wf_8f3a21
  FO-001  recorded 10 station events, then status review
    recommendation: merge   holdout satisfaction: 100%   attempts: 1   rework: 0
    git diff --stat, git log --oneline, review verdict, should-fix items, next steps ...
  Holdout: S01 satisfied, S02 satisfied, S03 satisfied.
  git merge --no-ff 5d86bd7…   (the reviewed head of factory/FO-001-add-csv-export-to-the-reports-page)
  cc-factory approve FO-001 --gate merge   (it asks you to type back a code it shows, and passes only that reviewed commit)

$ git merge --no-ff 5d86bd7…
$ cc-factory approve FO-001 --gate merge
cc-factory: pass FO-001's merge gate (the reviewed lane head 5d86bd7…, merged into HEAD by 9e41a0c…).
Type the code K7MQ4X and press Enter to confirm (anything else cancels): K7MQ4X
FO-001  merge gate passed: done
```

If a station fails an attempt, the order reworks inside the same run (once, by default) before it ever reaches you; on a second failure it is `failed`, and `show` has the station and the reason.

## The floor (cc-dash)

cc-dash shows the factory on its **Factory** tab (`#/factory`, key `f`), the landing view: one live graph of projects, sessions, agents, workflows and **orders**, wired by who spawned whom. A project is a disc ringed by its model mix; every other node's face carries its model, state, current tool, how long it has been in that state (from a minute in), cost and burn, and an order's also its station stepper and, once it has been sent back, its rework count; particles flow down the edges at the rate of token burn. Zoomed out to where a busy floor fits, a failed node keeps a red ring, a stalled one a dashed amber ring and anything that needs you a wider amber halo, so they can be found among hundreds. An order node sits beside its project and is linked to its workflow runs by the ledger `run_id` and by the agent labels the workflow sets (`FO-003:build`, with the station title as the phase), and to any session working on its lane branch. An order waiting at a gate is marked as needing you in amber, like an agent waiting on a prompt.

- **Instrument panel**, across the top of the stage: the totals, what needs you, your subscription's 5-hour and weekly **usage limits** (use, and time to reset; recorded by `cc-statusline`, see [commands.md](commands.md)), and the **Activity** toggle.
- **Activity column**, on the right: **Recent actions**, the 8 newest tool calls on the floor (when, which agent in its model's colour, the tool and its target, running, ok, failed or blocked), newest first; a row shows its node, and the list holds still while your pointer or focus is on it. Under it, **Last hour**: new tokens (by model family), tool calls (the failed ones in red) and working agents, each in 20 bars of 3 minutes headed by the hour's total (for agents, the peak), always the last 60 minutes whatever the window picker says; the lighter last bar is the 3 minutes still running. Hover a bar, or focus a chart and use the arrow keys, for its exact figures. On a short window the list shows fewer rows (the next one peeks out, the rest scroll) so the charts keep their height. It is shown by default on a window wider than 900 px; narrower, the Activity toggle opens it (a bottom sheet on a phone), and the toggle hides it anywhere.
- **Inspector**: selecting a node (click, or the arrow keys and Enter) opens it in the column's place (a bottom sheet on a phone), and closing it brings the column back; the two share one slot of one width, so a pick never re-fits the floor. An order shows its stations, gate, lane and rework count; a session or agent shows its activity, tokens and guard catches; a session or agent links to its session or agent page, and a project or order to its project's Overview and Plans. The floor re-frames so the node, its parent and its children stay clear of the panel. Double-click opens a node's page.
- **Camera and scope**: `+`, `-` and `0` zoom and fit (the fit keeps nodes out from under the column, the inspector and the zoom buttons). Alert toasts sit to the left of the zoom buttons, and of the column or an open inspector, and never move the camera; on a phone they sit above the zoom row and step aside while the inspector's bottom sheet is up (the alert strip still lists what needs you). The project picker narrows the floor to one project, and **Dormant projects** adds the projects with no live session or open order.
- **List** shows the same tree as a list of buttons: the keyboard and screen-reader path, and the fallback when the graph engine cannot start. Reduced motion stops the flow and the breathing.

It passes no gates: in phase 0 the floor shows them but cannot pass them, so approvals go through the CLI (or the skill). With `cc-dash --control`, a session or agent waiting on a decision (a permission, a question or a plan) gets a **Review** button (**Answer** for a question, **Review plan** for a plan), in the alert strip and in its inspector. It opens the full decision card in a dialog over the floor, the same card as the toasts and the session page; the strip and the inspector only summarize the request in one line, and a decision is made on the card alone. Escape closes the dialog. Without control mode (a plain read-only pairing) there is no Review; until you type the control code, the card asks for it, as everywhere. Projects (Overview, Sessions, Plans, Docs) and the session and agent pages are the drill-downs from the floor; the old Live tab is gone, and `#/live` redirects here.

## CLI reference

| Command | What it does |
|---|---|
| `cc-factory init [PROJECT] [--force]` | Scaffold `factory/`, the `.gitignore` lines, the ledger dir and `factory-line.js`. |
| `cc-factory new TITLE... [--priority p1\|p2\|p3] [--line NAME]` | Create an order file and its `created` event. |
| `cc-factory list [--all] [--json]` | The board (alias `status`); hides `done` / `rejected` without `--all`. |
| `cc-factory show ID [--json] [--events N]` | One order, folded from the ledger, with the last merge-gate briefing. |
| `cc-factory mark ID [STATUS] [--station S --state start\|pass\|fail\|waiting] [--run-id R] [--branch B] [--worktree W] [--note TEXT] [--no-holdout]` | Append a status, station or note event. Not `approved`, `done` or `review`: gate decisions are made with `approve`, and `review` is written by `record`. `mark ID spec` refuses with fewer than 3 scenarios unless `--no-holdout`. |
| `cc-factory approve ID [--gate spec\|merge] [--note TEXT] [--into REF]` | Pass a gate: spec makes `approved` and stores the spec hash; merge makes `done` (exactly the reviewed lane head must be merged into `--into REF`, default the main checkout's HEAD, the branch unmoved since the review; records the merge sha). Refuses under Claude Code and without `/proc` (Linux only for now): run it in your own terminal, where it shows a random code to type back. |
| `cc-factory reject ID [--gate spec\|merge] [--rework] [--note TEXT]` | Fail a gate: `rejected`, or at the merge gate with `--rework`, back to `approved`. Refuses under Claude Code and without `/proc`, and asks for the code, like `approve`. |
| `cc-factory lane ID [--base REF] [--json]` | Create (or reuse) the branch and worktree; the last stdout line is the worktree path. |
| `cc-factory holdout ID` | Create the git-ignored scenario directory; the last stdout line is its path. |
| `cc-factory run-args ID` | JSON for the `Workflow` tool: `{name, scriptPath, args}`. Includes the line's `threshold`. Refuses without a recorded lane, without the spec gate's pass (on a `human` spec gate), when the spec changed since approval, and when the lane's `HEAD` tracks files under `.factory-holdout/`. |
| `cc-factory record ID [--workflow-run RUNID] [--run-id R] [--file F]` | Write a workflow result into the ledger: one station event per history entry, then the final status, and print the merge-gate briefing. `--workflow-run` reads the result Claude Code saved under `~/.claude/projects/<project>/<session>/workflows/<runId>.json`; its args must match the order and line. `--file` and stdin also work, but only for `failed` or `blocked`. Writes `review` only for a `--workflow-run` result with `contract: 2` in which every station passed on the final attempt and the verify result matches the scenario files on disk; otherwise records `blocked` and exits 2. |

`-C DIR` before or after the subcommand points it at another project (default: the current directory; it resolves to the nearest `.git`, then to the main checkout). IDs are forgiving: `3`, `fo-3` and `FO-003` are the same order. Machine output (`list --json`, `show --json`, `lane --json`, `run-args`) is ASCII-only JSON, and paths in refusals are printed without control characters. Exit codes: `0` ok, `1` usage or unknown id, `2` refused, with `BLOCKED: <reason>. Suggestion: <what to do>.` on stderr.

## Not done yet

The factory is an experiment: the core, the line and the floor work, and the gaps below are known and open. Numbers follow the prioritized gap list of an independent assessment made on 2026-10-25 (not part of this repository). Gaps 0 to 7 are addressed in this release, except one part of #4: a rerun still starts without the last run's failure feedback.

- **macOS factory gates** via a sysctl/`ps` lineage check: today `approve` and `reject` refuse without `/proc`.
- **#8 Prompts fight the permission system**: lane rules use `cd <dir> && cmd`, which a project allowlist can refuse, and a headless run on an untrusted workspace ignores the project allowlist.
- **#9 No deterministic checks**: a `cc-factory check` (run the tests at lane HEAD, fail-first on new tests, no deleted tests or new skips) so a pass does not rest on a model's report.
- **#10 The holdout is not enforced**: scenarios out of the project tree, a hook that denies the path to every station but verify, a hook that binds verify's Bash calls to the sandbox wrapper, hashing at the spec gate, and (#10d) the two-tier holdout, whose final tier never produces feedback.
- **#11 No docs station**: nothing updates docs or the changelog for a user-visible change.
- **#12 Thin ledger, no metrics**: station events carry the record time, not their own, and there are no model, effort, cost or approver fields (satisfaction and the merge sha are recorded), and no lead time, first-pass yield or rework rate.
- **#13 Untrusted intake**: a GitHub issue still reaches the main session as raw text.
- **#14 The merge path and lanes are only partly guarded**: bash-guard asks before a merge, rebase, pull or reset into a protected branch and before a non-read git command that names one, but it is a text guard with a documented residual class (a `cd` into another repository, `.git/config` edited by hand through the shell). The structural fix is a git `reference-transaction` hook that refuses agent-originated updates to protected refs, whatever command made them. Nothing stops a push of `factory/*`, a write into another lane, or an append to the ledger.
- **#15 No cost cap** per order.
- **#16 No dispatcher** between orders (WIP limit, merge queue).
- **#17 No kaizen loop** from failures back into scenarios, rules and line config.
- **#18 The verifier is never calibrated** against the human's merge decisions.
- **#19 No realistic environments** beyond CLIs and libraries.
- **Gates from the floor**: approve the spec and merge gates from cc-dash in control mode, behind the same typed confirmation as other decisions.
- **Non-goals**: a daemon, an MCP server, auto-merge to protected branches, a cloud dependency.

A line run is an ordinary `Workflow`, so it has the same constraints as any unattended workflow: no one answers a permission prompt mid-run. Read [Dynamic workflows & unattended runs](workflows.md#unattended--autonomous-runs) and [Sandboxing](sandboxing.md) before you schedule one.
