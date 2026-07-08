---
name: vault
description: Self-writing vault — a single-inbox markdown vault for the USER'S raw project thinking (voice transcripts, quick notes, half-ideas), which Claude files, cross-links, and synthesizes on a schedule without the user. Operations: init (scaffold), process (inbox → linked notes + daily digest), synthesize (weekly themes/contradictions), health (graph pulse). Use when the user wants to set up a vault, capture a thought mid-session, process the inbox, or run note upkeep unattended. Distinct from knowledge-wiki (digests an EXTERNAL source corpus) and basic-memory (cross-project decision graph).
user-invocable: true
argument-hint: [init|process|synthesize|health]
---

# Self-Writing Vault

The vault holds the user's own raw thinking about this project and turns it into a
living, cross-linked graph without the user's involvement: they dump thoughts into one
folder; scheduled runs file, link, digest, and synthesize. The full contract (rules +
layout table) lives in `vault/README.md` — read it once per session before operating
on the vault. Invariants that hold across every operation:

- **Single inlet.** Only `vault/inbox/` receives new capture, and only the user (or a
  capture pipeline) writes there. When the user says "note this down / remember this
  thought" mid-session, append it as a new dated file in `vault/inbox/` — don't file
  it into `notes/` directly.
- **`raw/` is immutable.** Processing MOVES inbox originals to `raw/` (date-prefixed)
  and never edits anything already there. Distillations live in `notes/`.
- **Backlinks over notes.** Every filed note gets ≥3 `[[backlinks]]`, ≥1 to a note
  older than 60 days once the vault has that much history. A note with no connections
  is just a file.

This is a **proactive loop** (see `loop-engineering`): `vault/log.md` is its
disk-based state, `health` is its independent grader, and `vault/AUTOMATION.md` holds
the trigger wiring (cron / `/schedule` / `/loop`). Every operation needs only
Read/Write/Edit inside `vault/` — no ask-listed commands — so it survives headless
`claude -p` runs under `acceptEdits`.

## init

Prefer the deterministic scaffolder: run `cc-vault <project-dir>` if it's on PATH
(`--private` keeps the vault out of git — ask the user which they want). Without it,
create the same structure by hand: `vault/{inbox,raw,notes,daily,synthesis}/` plus
`index.md` (routing table), `log.md` (append-only ledger), and a `README.md` stating
the rules above; point the user at the cc_tool `templates/vault/` seeds for reference.

## process (the scheduled run)

1. If `vault/inbox/` has no files (ignore `.gitkeep`), say so and stop — this cheap
   no-op is what makes a daily trigger affordable.
2. Read the tail of `vault/log.md` (what the last runs did), then `vault/index.md`.
3. For each inbox file: read it; MOVE the original to `vault/raw/YYYY-MM-DD-<name>`
   unchanged; distill the content into `vault/notes/` — extend an existing note when
   the topic already has one (prefer Edit, keep diffs small), create a new note only
   for genuinely new topics; apply the backlink invariant.
4. Update `vault/index.md`: one line per touched note.
5. Write the daily digest `vault/daily/YYYY-MM-DD.md`: what came in, where it was
   filed, links added, open questions it raised.
6. Append one dated line to `vault/log.md`.

## synthesize (weekly)

Read only the last 7 days of `daily/` digests and the notes they touched, then write
`vault/synthesis/YYYY-Www.md` — the one file the user will actually reread: recurring
themes, contradictions in their own thinking, promises they keep half-making, and
what deserves attention next week. Link every claim to its `[[note]]`. Append a
`log.md` line.

## health (monthly-ish graph pulse)

Report, don't fix (offer fixes as a follow-up): unprocessed inbox backlog (count +
age of oldest), orphan notes (no inbound `[[backlinks]]`), link density (links per
note, compared against the last health entry in `log.md`), and index drift (notes
missing from `index.md`, index lines pointing at deleted notes). The metric that
matters is link density climbing — files accumulating without connections means the
vault is becoming a warehouse. Append a `log.md` line with the numbers so the next
run has a baseline.

## Boundaries

Your OWN raw thinking → vault. An EXTERNAL corpus to digest once and query →
`knowledge-wiki`. Cross-project decisions/preferences emitted while working →
basic-memory. A loop's work ledger → that loop's `LOG.md` (`vault/log.md` is this
loop's).
