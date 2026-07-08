# Self-writing vault

Your raw thinking about this project, filed and cross-linked by Claude on a
schedule. The rules below are the contract — you and Claude both follow them.

1. **One inlet.** New thoughts go ONLY into `inbox/` — voice transcripts, quick
   notes, half-ideas, no sorting at capture time. If filing a note takes a
   decision, it doesn't get filed; the inbox removes the decision.
2. **`raw/` is immutable.** Processing moves each inbox original into `raw/`
   with a date prefix. Nothing ever edits a file in `raw/` — cleaned-up,
   distilled versions live in `notes/`; the original stays as you said it.
3. **Backlinks over notes.** A note with no connections is just a file. Every
   note Claude files gets at least three `[[backlinks]]`, at least one of them
   to a note older than 60 days once the vault has that much history.
4. **Digest, then synthesis.** Each processing run appends a daily digest to
   `daily/`. Once a week, `synthesis/` gets the one file worth rereading:
   recurring themes, contradictions in your own thinking, promises you keep
   half-making.
5. **Graph pulse.** Health is link density climbing, not file count growing.
   Files accumulating without connections is a warehouse, not a second brain.
   Run `/vault health` about monthly.

| Path | What lives here | Who writes |
|------|-----------------|------------|
| `inbox/` | unprocessed capture | you (only you) |
| `raw/` | dated immutable originals | processing run (move only, never edit) |
| `notes/` | distilled, cross-linked notes | Claude |
| `daily/` | per-run digests (`YYYY-MM-DD.md`) | Claude |
| `synthesis/` | weekly synthesis (`YYYY-Www.md`) | Claude |
| `index.md` | one line per note — the routing table | Claude |
| `log.md` | append-only run ledger | Claude |

Operations: `/vault process` · `/vault synthesize` · `/vault health` (see the
`vault` skill). To run them without you, wire ONE of the triggers in
[AUTOMATION.md](AUTOMATION.md).
