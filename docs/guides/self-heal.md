[← README](../../README.md)

# Self-heal: proposals from your agents' recent mistakes

Self-heal reads what went wrong in your recent sessions and proposes changes to the project harness so the same mistakes stop recurring. It looks at guard denials, repeated tool errors, permission stalls, API errors, failing tests and type checks, factory rework and verify failures, your corrections, stalled agents, and coordination friction between the agents of one session (an agent editing a file a sibling changed after it last read it, a merge or rebase stopping on a conflict while subagents worked, a subagent handing back more than once). The harness here means `CLAUDE.md`, guard rules, settings, skills, rule files, factory lines and cc_tool's own templates.

The run only proposes. A patch reaches a project only when you press **Apply** on it in the dashboard and confirm. Nothing it prints is run. It closes the loop the factory assessment calls the kaizen gap (#17): failures turn into reviewed rule, prompt and line changes.

## Running it

- **From the dashboard.** Start `ccdash --control` and type the control code. The **Self-heal** button next to the window picker shows a confirm step: the window it will cover, the projects, and the budget cap. **Run** starts the analysis. Beside the button: `last: 3d ago` (or `never`), and while a run goes, `running · 4m` with a link to its session on the Factory floor. When it finishes, the proposal count opens the results drawer.
- **Without control mode** the button is visible but disabled ("Self-heal needs ccdash --control"). Starting an agent run from the browser is privileged, like approving a prompt.
- **From a terminal.**

  ```bash
  cc-selfheal [--project P] [--since ISO] [--days N] [--budget USD] [--timeout MIN] [--dry-run]
  ```

  It builds the same evidence bundle, runs the same workflow, waits, and prints the proposals with their patch paths. `--dry-run` only builds the bundle (`evidence.json`, `evidence/`, `harness/`, `args.json`) so you can inspect it. A dry run starts no `claude` and does not move the window.

**Window.** A run covers the time from the end of the last completed run's window to now, across every project in the dashboard's scope. The first run uses the dashboard's current window. Failed runs do not move the window. A window older than 30 days is cut to 30.

**Temp directories.** Sessions whose project root is under a temp directory are left out: `/tmp`, `$TMPDIR` or Python's `tempfile.gettempdir()`. Paths are compared after resolving symlinks. Nothing else in the confirm step changes; those projects are just not in its list.

**One run at a time.** A second trigger gets "already going". `--selfheal-budget USD` sets the `--max-budget-usd` cap (default $5); the timeout is one hour. A bundle with no errors in it finishes at once, with no `claude` started and nothing spent.

## Reading the results

Proposals are grouped by project, then by severity. Each one gives:

- **target**: the file it changes:
  - `CLAUDE.md`, above the managed marker only;
  - `.claude/guard-rules.json`, following the `distill-rules` conventions;
  - `.claude/settings.json`;
  - a project skill;
  - a rule file, `.claude/rules/<name>.md` (a new one too);
  - `factory/line.json`;
  - or cc_tool's `templates/`, for a fix every project gets.
- **failure mode**: what it prevents.
- **evidence**: session ids, counts and short excerpts.
- **risk**: what it might over-restrict.
- **check**: the skeptic's verdict.
- **diff**: the unified diff.

A rule file holds one self-contained topic. It suits a project that already keeps rule files, or guidance that would crowd `CLAUDE.md`. A short, project-wide instruction goes in `CLAUDE.md`. Claude Code loads both into every session. A rule file sits directly in `.claude/rules/`, ends in `.md`, and its name has only lowercase letters, digits, `-` and `_`.

Each proposal has these helpers:

- **Apply** (control mode only). Applies the patch and commits it in the project. See [Applying a proposal](#applying-a-proposal).
- **Copy patch.** Apply it yourself with the command shown: `git -C <project> apply <patch>`.
- **File as factory order.** Shows a `cc-factory -C <project> new --line kaizen ...` command to run yourself. Add a `kaizen` line to `factory/line.json` first if the project has none.
- **The full write-up.** Every run's files are under `${XDG_STATE_HOME:-~/.local/state}/cc_tool/selfheal/runs/<id>/`. That covers `proposals.md` and the run's own copies of the patches. The apply command uses cc-dash's checked copy in `selfheal/patches/<id>/`.

Some patches are withheld:
- one that touches any file other than its stated target;
- one that renames, deletes or changes a file mode;
- one that carries invisible or direction-changing characters, or non-ASCII in a JSON file's changed lines;
- one that edits `CLAUDE.md` at or below the managed marker;
- one that no longer matches the live file exactly at the lines it names.

A proposal whose `proposals.json` changed after the run finished is not shown. A settings patch that does anything but add deny or ask rules or drop allow rules, a patch that weakens a guard rule, or may touch a skill's frontmatter (where `allowed-tools` pre-approves tools for the turn that invokes the skill, restricting nothing, and `hooks` run commands; judged by line numbers in the live file, and a new skill, an unreadable file or unclear `---` fences count too) carries a caution line. Sessions whose project root is your home directory are left out, so a proposal never targets your global settings. So are sessions rooted under a temp directory.

## Applying a proposal

**Apply** opens a confirm in the card. It names the file and the project. If the tab is still locked, it asks for the control code first. Then you type `apply`. A patch with a caution line also needs a tick: you read every line. **Apply and commit** sends it.

cc-dash checks everything again before it touches the file:
- the run and proposal ids have the right form;
- `proposals.json` still has the sha256 recorded when the run ended;
- the patch is cc-dash's checked copy, and it touches only its stated target;
- the target is still allowed for that project;
- the project root is the one the run recorded (for `_cc_tool`, cc_tool's checkout);
- the patch still matches the live file, and `git apply --check` passes.

It refuses, and says why, when:
- the target has uncommitted changes, staged or not, or git neither tracks nor ignores it;
- the repository is in the middle of a merge, rebase, cherry-pick, revert or bisect;
- the patch no longer applies;
- the target has a git filter attribute.

Then it applies the patch and commits that one file. The message is `self-heal: <title>`, with the run and proposal ids in the body. Other staged or unstaged work in the repository stays as it was. git runs hardened, like the factory's verify station: no system or global config, no hooks, no fsmonitor, no signing, no pager, no network. The author is your `user.name` and `user.email`. If the commit fails, the file goes back as it was.

The card then shows **Applied · <short sha>**, and Apply is gone. The run's record keeps the commit, so a second apply is refused.

**A file git ignores** is changed but not committed, since there is nothing to commit it to. cc_tool's deployed skills are often like this: a project that ignores `.claude/` keeps them out of git. The confirm says so, and the button reads **Apply**. cc-dash first copies the old file to `selfheal/backups/<id>/<proposal>.orig`. The card then shows **Applied · not committed** and names the backup. To undo it, copy the backup back. A new file in an ignored directory is created the same way, with no backup. Every apply and every refusal goes to `control/audit.jsonl`.

## How it is kept safe

Transcript text, tool errors and guard messages can carry prompt injection, so the run uses the quarantine pattern:

- **cc-dash builds the evidence** in deterministic Python. Each excerpt is clipped, the bundle is bounded, and secrets are redacted with write-guard's patterns. write-guard's patterns are parsed, never run. Bundles leave out sessions from self-heal's own runs. The harness files go in as a redacted snapshot under names Claude Code never loads, such as `CLAUDE.md.txt` and `_claude/`. The run also sets `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`, so no snapshot can act as instructions.
- **The run is one headless `claude -p`** with a fixed argv, as its own process group with no terminal. `claude` is found only through absolute `PATH` entries. Its working directory is the run directory, and it gets an allowlisted environment: no `CC_DASH_*`, no Claude Code session variables, no `PYTHON*`/`LD_*`, and nothing from the browser. Its two agent types are checked field by field before the run: tools, model and nothing else. No hooks, no MCP servers, no permission mode. It runs `--restricted`, so the only tools are Workflow, Read, Grep, Glob and Write, confined to the run directory, and no settings file or hook loads. `--permission-prompts none` means it never asks for anything, and `--strict-mcp-config` loads no MCP server. Its session is named `self-heal <id>` and shows on the Factory floor like any other.
- **Five stages:**
  1. classify (Sonnet);
  2. root cause (Opus);
  3. propose (Opus);
  4. adversarial check (a fresh Opus);
  5. write (Sonnet).

  The first four have only Read, Grep and Glob and answer in structured output. Only the writer can write, and it writes the files the workflow script composed, inside the run directory.
- **It cannot approve itself.** It has no prompt to approve, no hook runs in it, and it cannot reach the dashboard or the control directory. Its working directory is not a registered project, so cc-dash would refuse a request from it anyway. The control token lives only in your browser tab.
- **On the way back, everything it wrote is untrusted.** cc-dash validates `proposals.json` and every patch against the allowed targets, and records a sha256 when the run ends. The drawer renders all of it as text.

The trigger is a control-mode POST (`/api/control/selfheal`) with the same checks as approving a prompt: the control token, JSON, same-origin `Origin` and `Sec-Fetch-Site`, and the Host check. Apply is a second one (`/api/control/selfheal/apply`) with the same checks. Every start, apply, refusal and end goes to `control/audit.jsonl`. State lives in `${XDG_STATE_HOME:-~/.local/state}/cc_tool/selfheal/` (directory 0700, files 0600, no symlinks followed):
- `state.json`;
- `records/<id>.json`;
- `patches/<id>/`;
- `backups/<id>/`;
- `runs/<id>/`.

write-guard refuses the file tools there, as it does for `control/`. cc-dash stops a run only after checking it: the process must still be that run's `claude`, by its start time and by its session id and name. **Limits:** something running as you can still use Bash to write there or to edit the checkout. That is the same-user limit control mode documents. Review the patches like any change to your harness.
