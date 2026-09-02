---
name: distill-rules
description: Turn the hard imperatives in CLAUDE.md, AGENTS.md and the project's skills ("never run X", "always use pnpm", "don't edit files under db/migrations") into deterministic hook rules in .claude/guard-rules.json, enforced by bash-guard.py and write-guard.py. Use when the user says "make my CLAUDE.md enforced", "add a guard rule", "this repo uses X not Y, stop the agent from using Y", or when a rule you wrote keeps being ignored. Manual invocation only; never fires on its own.
---

# distill-rules

Prose in `CLAUDE.md` is hopeful; a hook rule is enforced. This skill reads the project's instruction files, extracts every concrete imperative that has an observable trigger (a command shape, a file path, a content pattern), and writes it as a rule the cc_tool hooks apply before the tool call runs. The built-in guards (protected branches, secrets, destructive commands) stay in the hook code; this file is for what is specific to *this* repo.

## Where rules live

`.claude/guard-rules.json`, read by `bash-guard.py` (tool `Bash`, field `command`) and `write-guard.py` (tools `Write`/`Edit`/`MultiEdit`, field `file_path` or `content`). No reload step: hooks read the file on every call.

```json
{
  "write_outside_repo": "ask",
  "rules": [
    {
      "id": "use-pnpm",
      "source": "CLAUDE.md: 'Always use pnpm, never npm or yarn'",
      "tool": "Bash",
      "field": "command",
      "regex": "(^|[;&|]\\s*)(npm|yarn)\\s+(install|i|add|remove|run|exec)\\b",
      "negate": ["\\bnpm\\s+(view|info|search)\\b"],
      "action": "deny",
      "reason": "This repo uses pnpm; npm/yarn would create a second lockfile.",
      "suggestion": "pnpm install / pnpm add <pkg> / pnpm run <script>"
    },
    {
      "id": "no-migration-edits",
      "source": "CLAUDE.md: 'Never edit an applied migration'",
      "tool": ["Edit", "Write", "MultiEdit"],
      "field": "file_path",
      "regex": "/db/migrations/\\d{4,}.*\\.sql$",
      "action": "ask",
      "reason": "Applied migrations are immutable in this repo.",
      "suggestion": "add a new migration instead"
    },
    {
      "id": "no-console-log",
      "source": "docs/style.md: 'Prefer the logger over console.log'",
      "tool": ["Edit", "Write", "MultiEdit"],
      "field": "content",
      "regex": "\\bconsole\\.log\\(",
      "action": "warn",
      "reason": "Use the project logger.",
      "suggestion": "import { log } from '@/lib/log'"
    }
  ]
}
```

Fields: `id` (unique, kebab-case), `regex` (Python `re`), `reason` are required. `tool` defaults to `Bash`; `field` defaults to `command` for Bash and `file_path` for write tools. `flags` (`i`, `m`, `s`) default to none. `negate` lists exception regexes; any match suppresses the rule. `enabled: false` keeps a rule on disk without enforcing it. `source` is for humans: quote the sentence the rule came from. `write_outside_repo` (`ask` | `warn` | `off`) tunes write-guard's confinement check.

## The lexical ladder (binding)

The strength of the words picks the action. Do not upgrade or downgrade on your own judgment.

| Wording in the source | action |
|---|---|
| never, must not, do not, forbidden, under no circumstances | `deny` |
| should not, avoid, don't … unless, only with approval | `ask` |
| should, prefer, use X over Y, always (as a style preference) | `warn` |
| consider, try, usually, when possible, it's fine to | skip (not a rule) |

"Always X" is `deny` only when its negation is observable ("always use pnpm" → deny `npm install`); "always write tests" has no trigger and is skipped.

## Procedure

1. **Collect sources.** `CLAUDE.md` (the project part above the cc_tool marker only; the managed block is cc_tool's own), `AGENTS.md`, `.claude/skills/*/SKILL.md`, `docs/**/*.md` that read as conventions. Read each once. If the user named a file or pasted a sentence, use only that.
2. **Extract imperatives.** One sentence each. Keep the quote verbatim for `source`.
3. **Find the trigger.** For each imperative decide which observable it has: a command shape (Bash `command`), a path (`file_path`), or content (`content`). If none, skip it and list it under "not enforceable" in the summary. Sequencing rules ("run tests before committing") have no per-call trigger; they stay prose.
4. **Write the regex.** Anchor commands to a command position (`(^|[;&|]\s*)`), use `\b` boundaries (bare `git` matches `gitlab`), never match the empty string, no nested unbounded quantifiers (`(.*)*`). Convert globs to anchored regexes: `db/migrations/**` → `/db/migrations/.*`. Add `negate` for the obvious legitimate cases (the tool's read-only subcommands, `.example` files).
5. **Check for overlap** with the built-in guards and `.claude/settings.json` deny/ask lists. If a built-in already covers it, do not add a rule; mention it in the summary.
6. **Dry-run every rule** against three commands or paths: one that must fire, one that must not, one edge case. Run them with Python's `re` in a scratch script, not by invoking the hooks on real commands.
7. **Merge, don't overwrite.** Read the existing file. Keep rules whose `id` is present and whose `source` is unchanged; update rules whose source sentence changed; add the new ones. Never delete a rule the user added by hand (no `source`, or `source` starting with `manual:`) unless asked.
8. **Validate and write.** The file must load with `json.load`; every regex must compile. Write it, then print the summary.

## Summary format

```
distilled 4 rules → .claude/guard-rules.json
  deny  use-pnpm            CLAUDE.md L12
  ask   no-migration-edits  CLAUDE.md L31
  warn  no-console-log      docs/style.md L8
  kept  manual: no-curl     (hand-written, untouched)
not enforceable (no per-call trigger): "run tests before committing", "keep PRs small"
already covered by built-ins: "never force-push" (bash-guard), "don't read .env" (settings deny)
```

## Lifecycle

- **List**: read the file and print the table above.
- **Disable one**: set `enabled: false`; do not delete.
- **Change strength**: edit `action`; note in `source` that the user overrode the ladder.
- **Remove a source**: delete every rule whose `source` starts with that file name.
- **Re-distill**: re-run the procedure; it is idempotent by `id`.

## Self-checks before writing

- No rule with `action: deny` and a regex that could match ordinary read-only work (`ls`, `cat`, `git status`, `grep`).
- No rule whose regex matches its own `reason` or `suggestion` text (a rule that fires on the message it prints loops the model).
- Every `deny`/`ask` rule has a `suggestion`; the model needs a way forward, not just a wall.
- The file is under 200 lines. Past that, the repo has a convention problem, not a rules problem.
