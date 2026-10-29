[← README](../../README.md)

# Docs standard (one frontmatter, fixed homes, links that resolve)

One small format for every Markdown document a cc_tool project keeps: Superpowers specs and plans, task files, factory orders, decisions, guides, reports and vault notes. Each document says what it is in a short frontmatter block, lives in a known folder, and links to its neighbours with relative links. The standard fixes the outside of a document, not its contents: Superpowers keeps its plan format, the vault keeps its contract, and the factory keeps its orders. `cc-docs-migrate` brings existing docs in line without rewriting them.

```bash
cc-docs-migrate /path/to/project                 # dry run: the per-file plan, nothing written
cc-docs-migrate /path/to/project --apply         # write it; every change logged with before/after hashes
cc-docs-migrate /path/to/project --only plans    # one tree: specs | plans | vault | docs
cc-docs-migrate /path/to/project --undo docs/.migration-log.jsonl   # put the last run back
```

## The frontmatter

Every key in one place. Only `type` is required. Every other key appears only when it carries something no other part of the file or the repository already holds, because a second copy drifts.

```yaml
---
type: spec
title: Plan search
status: active
created: 2026-10-05
updated: 2026-10-20
source: docs/tasks/backlog/monitoring.md
links: [docs/reference/architecture.md]
---
```

| Key | Meaning | Who writes it |
|-----|---------|---------------|
| `type` | what kind of document this is (the table below) | the migration, or whoever creates the file |
| `title` | one line, only for a document with no `#` heading (otherwise the H1 is the title) | the migration, when there is no H1 |
| `status` | `draft`, `active`, `done` or `superseded`, only for specs, ADRs and PRDs | you; the migration only reports a `Status:` line it finds in the body |
| `created` | `YYYY-MM-DD`: the date in the file name, else the commit that added the file | the migration |
| `updated` | `YYYY-MM-DD` of the last meaningful edit | whoever makes it (never the migration) |
| `source` | what this came from, from the project root: an order's task, an ADR's spec | the migration (orders), you |
| `links` | related docs, from the project root | you |

- **Syntax.** The first line is `---` and the closing `---` comes within 100 lines (the limit `lib/cc_dash/factory.py` reads to). One `key: value` per line, lower-case keys, no nesting. A value with `:`, `#` or a leading YAML indicator is double-quoted JSON-style (`title: "Ladle: stage 2"`), the way `cc-factory new` writes titles. Lists are one-line flow lists, `[a, b]`. Keys the standard does not name are kept as they are.
- **Paths in frontmatter are from the project root**, like the `**Spec:**` line Superpowers puts in a plan header. Paths in the body are relative to the file (see [Links](#links)).
- **`status` lives in exactly one place.** A plan's progress is its checkboxes (cc-dash's Plans view counts them). A task's state is its folder (`active/`, `backlog/`, `completed/`), or its order once it has one. An order's state is the factory ledger. A doc under `archive/` is archived. None of these get a `status` key. Specs, ADRs and PRDs have no other home for it, so they carry one.
- **Relations are read, not copied.** A plan names its spec in its `**Spec:**` line, and that line is the link. Nothing writes the same path again into `source`, and no spec keeps a list of its plans.
- **Per-type extras:** an order keeps the factory's `id`, `priority` and `line`, and its `status` is only the `queued` or `spec` seed the ledger starts from. A task that became an order gets `order: FO-NNN`. Vault notes keep the vault's own `summary`, `type`, `status`, `date` and `tags`, with `date` counting as `created`.
- **Frontmatter comes after the fact.** Skills that write documents (writing-plans, brainstorming, `start-project`, `cc-factory new`) don't have to add it. Rerunning the migration types new files, and does nothing to files already in line.

## Where each type lives

The folder is the contract the tools already rely on. The migration never moves a file to match it. New documents go here.

| `type` | Lives in | Created by | Example |
|--------|----------|------------|---------|
| `spec` | `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md` | Superpowers brainstorming | `type: spec`, `status: active` |
| `plan` | `docs/superpowers/plans/YYYY-MM-DD-<feature>.md` | Superpowers writing-plans | `type: plan`, `created: 2026-10-06` (no status: the checkboxes are the progress) |
| `task` | `docs/tasks/{active,backlog,completed}/<name>.md` | `start-project`, `next-task`, `update-documentation` | `type: task`, `order: FO-004` once it became an order |
| `tracker` | `docs/TASKS.md`, `docs/OPEN-ITEMS.md`: running multi-item lists | you, long runs | `type: tracker` |
| `order` | `factory/orders/FO-NNN-<slug>.md` | `cc-factory new` only | the factory's own keys, plus `source: docs/tasks/backlog/monitoring.md` for a migrated task (the folder gives the type) |
| `adr` | `docs/adr/NNNN-<slug>.md` | you, or a planner, past the bar below | `type: adr`, `status: active`, `source: docs/superpowers/specs/...` |
| `glossary` | `docs/GLOSSARY.md`, one per project | created with its first term | `type: glossary` |
| `start-here` | `docs/start-here.md` | `start-project` (read by `next-task`) | `type: start-here` |
| `guide` | `docs/guides/<topic>.md` | you | `type: guide` |
| `reference` | `docs/reference/<topic>.md` (architecture, API, data model) | you | `type: reference` |
| `report` | `docs/E2E-TEST-PLAN.md`, `docs/UI-UX-REVIEW.md`, `docs/FRONTEND-REVIEW.md` (app-qa), `docs/reports/` | skills and audits | `type: report`, `created: 2026-10-12` |
| `prd` | `docs/prd/<product>.md` | you | `type: prd`, `status: draft` |
| `log` | next to the loop that appends to it (`LOG.md`) | a loop, append-only | `type: log` |
| `note` | `docs/notes/`, or anything that fits nothing above | anyone | `type: note` |
| vault | `vault/` per [its contract](vault.md): `inbox/`, `raw/`, `notes/`, `daily/`, `synthesis/`, `index.md`, `log.md` | you (inbox), the vault skill (the rest) | a note in `vault/notes/` keeps the vault's keys as they are |

Not documents for this standard: `README.md` and `CHANGELOG.md` (forges render them and tools parse changelogs), `CLAUDE.md` and `AGENTS.md` (agent instructions), and anything under a dot-directory (`.superpowers/sdd/` is a git-ignored execution ledger). The vault's notes are the vault's: its `type` is its own kind of note (`research`, `status`, ...), and only a note with no frontmatter at all gets one (`type: note`).

## Open work, decisions and the glossary

**When open work becomes a factory order.** Only in a project with a factory (`factory/orders/`, from `cc-factory init`), and only when you pass `--with-orders`: an order is a permanent event in the factory ledger, and retiring one takes `cc-factory reject` from your own terminal. Then each `task` in `docs/tasks/active/` or `docs/tasks/backlog/` with at least one open checkbox becomes one order through `cc-factory new`, unless an order already names it. The title comes from its H1, the priority is `p1` for `active/` and `p3` for `backlog/`, and it goes on the default line. The order's body links back to the task and copies the task's summary paragraph, with its open checkboxes as acceptance criteria. The task gets `order: FO-NNN`, and from then on the order holds its state. Migrated orders arrive `queued`, without holdout scenarios: carry each one on from `/factory intake` step 5 (`cc-factory holdout FO-NNN`). Without `--with-orders` the dry run lists the candidates. Plans, specs, trackers and vault notes never become orders: Superpowers executes plans, a tracker is a list of many things, and the vault's open questions are thinking, not work.

**When a decision becomes an ADR.** Rarely. All three must hold:
- it is hard to reverse;
- it would surprise someone reading the code without the history;
- it came out of a real trade-off between options that each had a case.

A choice that is easy to undo, obvious, or forced by a constraint is not an ADR. At most it is a sentence in the spec. An ADR is `docs/adr/NNNN-<slug>.md` with **Context**, **Decision** and **Consequences** sections. Its `status` is `draft` (proposed), `active` (accepted) or `superseded`, and its `source` names the spec or plan where the decision was made. The bar is a judgement, so the migration never writes an ADR. It types the files under `docs/adr/` and lists docs that already have Context and Decision sections, for you to decide.

**The glossary is lazy.** `docs/GLOSSARY.md` appears the first time a word needs pinning down: the code and docs use it in a project-specific sense, or two words are in use for one thing. One line per term: `**Order**: a work item in factory/orders/, the spec of one lane. Not: ticket, job.` Nobody writes an empty glossary, and the migration never creates one.

## Links

- **Relative, resolving, Markdown.** `[the spec](../specs/2026-10-05-plan-search-design.md)`, relative to the file that holds it. No absolute paths, no `file://`, no links that only work from the project root. A `#heading` fragment is fine.
- **Wikilinks stay in the vault.** `[[note]]` is the vault's link form (its backlink rule counts them), and nothing converts it either way.
- **No orphans.** Every document is linked from at least one other: an entry point (`README.md`, `docs/start-here.md`, a folder's `README.md` or `index.md`, `vault/index.md`) or the document it belongs to. A plan reaches its spec through `**Spec:**`. An order and its task reach each other through `source` and `order`. The migration reports orphans; it does not write indexes for them.

## What the migration does

`cc-docs-migrate` walks `docs/` (with `docs/superpowers/`) and `vault/`. It decides a `type` for each Markdown file from its folder, then its shape (a file cc-dash would track as a plan is a `plan`), then its name. Per file it plans one of:

- **add** a frontmatter block, or **merge** missing keys into an existing one: `type`, `title` when there is no H1, `created`, and `order` (with `--with-orders`). An existing key or value is never changed or reordered.
- **links**: a Markdown link that does not resolve is rewritten only when exactly one existing file fits. That is either an absolute path or `file://` URL inside the project, or a path that resolves from the project root (`docs/x.md`, `/docs/x.md`, or `../docs/x.md` from too deep). It is never rewritten on a checkbox or heading line, because cc-dash matches those lines' text against what transcripts did. Anything else is reported as broken, with a hint when a file of that name exists elsewhere, and left alone.
- **order**: as above, through `cc-factory new`, only with `--with-orders`.
- **skip**, with a reason:
  - a symlink;
  - malformed frontmatter;
  - a file over 4 MB, or not UTF-8;
  - a generated file (a `generated` or `DO NOT EDIT` marker near its top);
  - a file the project's tracked code names (a script that reads or hashes it);
  - a doc whose plan parse would change;
  - everything the vault owns.

It also reports, without acting: broken links, orphans, `Status:` lines in specs and other docs (for you to turn into `status` or not), ADR candidates and order candidates.

**What it never does.**
- Delete, rename or move a file.
- Touch code, or anything outside `docs/`, `vault/notes/` and, for new orders, `factory/orders/`.
- Change a frontmatter value it did not add, a heading, a checkbox, or any body text other than a link target.
- Write to a vault note that already has frontmatter, rewrite a link under `vault/`, or convert a wikilink.
- Touch `vault/raw/`, `vault/inbox/`, `vault/daily/`, `vault/synthesis/`, `vault/index.md`, `vault/log.md`, `vault/README.md` or `vault/AUTOMATION.md`.
- Write the factory ledger (only `cc-factory` does).
- Write a file whose parse in `lib/cc_dash/plans.py` would differ. Every file is parsed before and after, and it is skipped unless its title, goal, spec, tasks, step texts and ticks all match.

**Running it.**

```
cc-docs-migrate PROJECT [--apply] [--with-orders] [--only specs|plans|vault|docs] [--json] [--allow-dirty]
cc-docs-migrate PROJECT --undo LOG [--run RUN_ID] [--json]
```

- **PROJECT is the top of a git work tree.** A linked worktree's own root counts. A subdirectory is refused.
- **The dry run is the default and writes nothing.** It also runs outside git, with a warning.
- **`--apply` refuses** outside git, and on a dirty tree (any `git status` entry outside the migration's own log and backups) unless `--allow-dirty`. It also refuses when the code check could not read every tracked file within its caps (50,000 files, 512 MB), because that check is what protects a project's own scripts.
- **How `--apply` writes.** Each file is written once, atomically (a fresh sibling file renamed over it, never following a symlink), after checking the file still has the bytes it planned from. The original is kept as `docs/.migration-backup/<run>/<path>.orig`, in a folder that ignores itself in git.
- **The log.** Each write appends one line to `docs/.migration-log.jsonl`: `{"v": 1, "run", "event": "write", "path", "actions", "before", "after", "backup"}`. `before` and `after` are sha256 hex; `before` and `backup` are null for a created order. A `start` line and an `end` line frame each run. The log holds paths and hashes, no content; commit it or ignore it as you like.
- **Re-running.** A run with nothing to do writes nothing, not even to the log, so applying twice changes nothing the second time.
- **`--undo LOG`** (relative to PROJECT) puts back the last run in that log that changed something and was not undone; `--run` picks another. A file whose sha256 still equals `after` gets its backup back, after the backup's own hash is checked against `before`. A file edited since is kept and reported.
- **Orders survive an undo**, because the ledger is append-only: retire one with `cc-factory reject FO-NNN` from your terminal. A later `--with-orders` run finds the order by its `source` and links it again instead of making a second one.
- **Exit codes:** 0 done (including nothing to do), 1 usage error, 2 refused (`BLOCKED: <reason>. Suggestion: <what to do>.` on stderr, as every cc_tool guard prints).

**Before you apply on a real project**, read the dry run's `skip`, `broken` and `status?` lines. The code check sees a doc's name in a script, but not a path the script builds from pieces (`root / "docs" / "seo" / "reviews"`). If a script of yours reads docs that way and parses them from line 1, leave that tree out with `--only`, or run `git restore <file>` on those files after `--apply` and before you commit.
