[← README](../../README.md)

# Self-writing vault (your thinking, processed without you)

An optional per-project, single-inbox markdown vault where Claude maintains your own raw thinking. You only capture; scheduled runs do everything else — file, cross-link, digest, synthesize. Distilled from the "Self-Writing Vault" pattern, and the shipped worked example of a *proactive loop* (see the `loop-engineering` skill).

```bash
cc-vault /path/to/project      # scaffold vault/           (or: cc-setup /path --vault)
                               # --private → git-ignore it; --force → re-copy seed files

# capture — the inbox is the ONLY folder you write; no sorting at capture time
echo "half-idea about X" > vault/inbox/$(date +%F)-x.md   # or voice-transcription drops files here

# operations (the `vault` project skill), in a Claude Code session:
/vault process      # inbox → originals preserved untouched in dated raw/, distilled into
                    # notes/ with ≥3 [[backlinks]] each, index updated, daily digest written
/vault synthesize   # weekly: the one file worth rereading — recurring themes,
                    # contradictions in your own thinking, half-made promises
/vault health       # monthly graph pulse: link density, orphans, inbox backlog
```

**Run it without you** — wire ONE trigger from the generated `vault/AUTOMATION.md` (your project path pre-substituted): a system cron line running headless `claude -p --model claude-sonnet-5 --permission-mode acceptEdits "/vault process"` each weekday morning, a `/schedule` cloud routine (no laptop involved), or `/loop` in a long-lived session. Two properties make this cheap and safe unattended: processing needs only file edits inside `vault/` (nothing on the `ask` list, so headless runs never stall), and an empty inbox exits after one check — a daily trigger on a quiet vault costs ~nothing.

**The contract** (full version in the scaffolded `vault/README.md`): `inbox/` is the single inlet; `raw/` originals are immutable; every note gets ≥3 backlinks, at least one to an old note; digests daily, synthesis weekly; health means link density climbing, not file count growing. `session-context.py` surfaces vault state (inbox backlog + latest digest/synthesis) at every session start, so each session opens already knowing where your thinking stands.

**Boundaries**: your own raw thinking → vault; an external source corpus to digest once → `knowledge-wiki`; cross-session decisions/preferences → Claude Code's native memory.
