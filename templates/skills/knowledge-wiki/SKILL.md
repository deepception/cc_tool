---
name: knowledge-wiki
description: Compile a large external source corpus (papers, docs, scraped pages, a big codebase) once into a project-local, cross-linked markdown wiki, then answer queries from the pre-synthesized wiki instead of re-reading raw files. Use when you will repeatedly query the same large corpus and want to cut token cost. Operations: ingest a source, query the wiki, lint for rot.
user-invocable: true
argument-hint: [ingest|query|lint] [path-or-question]
---

# Knowledge Wiki

Implements Andrej Karpathy's "LLM Wiki" / compile-once pattern. When you will repeatedly query a large external **source corpus**, re-reading the raw files on every query (RAG-style) is ruinously token-expensive, and the cost multiplies under sub-agent fan-out and loops. Instead, compile the corpus **once** into a structured, queryable markdown wiki. Subsequent queries read the pre-synthesized wiki, not the raw sources. The llm-wiki-compiler reference implementation (see Notes) reports ~84-90% token reduction.

Act as a **research librarian**: do the interlinking, deduplication, and index upkeep that makes human-maintained wikis rot. This skill is pure markdown plus the filesystem tools you already have — no new MCP server, no external dependency.

## When to Use

- You will query the same large corpus many times (research synthesis, doc Q&A, onboarding to a big codebase).
- The corpus is too large to keep re-reading in full, or is read under fan-out / loops where token cost compounds.
- The corpus is stable enough that a compiled digest stays useful between updates.

## When NOT to Use

- A handful of files, or a one-off read — the compile cost only pays back across many queries.
- A corpus that changes faster than you can re-ingest it.
- Emitting your own decisions / preferences / cross-project architecture notes — that is `basic-memory` (see below).

## Relationship to basic-memory

Complementary, not a replacement.

- **basic-memory** stores **emitted** knowledge: decisions, preferences, architecture — a cross-project graph living in `~/basic-memory/`.
- **knowledge-wiki** **digests** an external **source** corpus into a project-local wiki, tracked in the repo.

Different inputs (your output vs. an external corpus), different scope (cross-project graph vs. one project's source digest). Use both; do not conflate them.

## Layout

Project-local and git-trackable, default under `.wiki/`:

```
.wiki/
  raw/        # ground truth — immutable snapshots of external/ephemeral sources; read and cite, NEVER edit; in-repo sources are cited by path instead
  wiki/       # LLM-owned synthesized markdown (source summaries, entity/concept pages), cross-linked with [[Page Name]] wikilinks
  index.md    # routing table: every wiki page + one-line summary — read FIRST so a query loads only candidate pages
  log.md      # append-only, dated, grep-friendly record of each operation
```

## Operations

### Ingest (one new source -> wiki)

1. **Read** the source. Copy it into `raw/` first only if it is external or ephemeral (scraped page, downloaded paper, fetched doc) — then never edit the copy. If the source already lives in the repo (e.g., codebase files) or is too large to duplicate, read it in place and cite it by repo path; do not copy it into `raw/`.
2. **Summarize** the source into a source page under `wiki/` (key claims, entities, terms), with citations back to the `raw/` path.
3. **Surgically edit affected pages**: update existing entity/concept pages this source touches; create new ones only when genuinely new. Add `[[wikilinks]]` between related pages. Deduplicate overlapping claims.
4. **Refresh `index.md`**: add or update the one-line summary for every page changed or created.
5. **Append to `log.md`**: a dated line naming the source ingested and the pages touched.

Ingest one source at a time so edits stay surgical and reviewable.

### Query (answer from the wiki)

1. **Read `index.md` first.** Pick only the candidate pages whose summaries match the question.
2. **Load only those pages.** Follow `[[wikilinks]]` only when a page points at something needed.
3. **Answer with citations** to the wiki page(s), and through them to the underlying `raw/` source.
4. If the wiki cannot answer, say so — and either ingest the missing source or flag the gap. Do not silently fall back to re-reading the whole corpus.
5. **Optionally file the answer back** as a synthesis page under `wiki/`, add it to `index.md`, and log it — so the next identical query is even cheaper.

### Lint (find rot)

Read `index.md`, then scan `wiki/` for:

- **Contradictions**: pages making incompatible claims about the same thing.
- **Stale claims**: wiki statements no longer supported by their cited source (`raw/` snapshot or in-repo path).
- **Orphan pages**: pages in `wiki/` with no inbound `[[wikilinks]]` and no `index.md` entry.
- **Missing cross-refs**: pages that mention an entity/concept that has its own page but do not link to it.
- **Index drift**: pages missing from `index.md`, or `index.md` entries pointing at deleted pages.

Present findings as a prioritized list; ask before applying fixes. Append a dated lint summary to `log.md`.

## Notes

- Keep wiki pages focused — one entity or concept per page — so queries load less.
- Prefer Edit over rewrite on existing pages to keep diffs small and reviewable.
- Commit `.wiki/` with the project so the compiled knowledge is shared and versioned.
- For reference, see the open implementations `praneybehl/llm-wiki-plugin` and `ussumant/llm-wiki-compiler`; this skill is self-contained and needs neither.
