---
name: gauntlet-loop
description: >-
  Use when the user says "gauntlet loop", or wants work refined "until it
  matches" a real example (a reference screenshot, competitor page, dataset,
  doc, or prior good output) rather than a vague quality target like "make it
  great". Explicitly user-triggered — states an objective, never fires on its
  own — and will not start iterating until the reference bar is concrete
  enough to actually grade against; negotiates that with the user first if
  it's weak.
user-invocable: true
disable-model-invocation: true
argument-hint: [objective]
---

# Gauntlet Loop

Named for the repeated-challenge structure: work must survive an independent grader's comparison against a real standard, then go back for another pass — no arbitrary stopping point, only "does it match the bar yet." The technique's own claim: the bar is the most important part of the whole loop — vague targets like "make it amazing" fail, concrete reference examples work.

This skill never fires itself. Only run it when the user explicitly invokes it with a stated objective.

## Step 1 — Get the objective and the bar

Ask for (or extract from what the user already gave you): the objective in one line, and what "great" looks like — a concrete reference: a screenshot, a competitor artifact, a dataset, a prior good output, a passing spec. Not adjectives.

## Step 2 — Validation gate (non-negotiable, do not skip)

Before decomposing or spawning anything, judge whether the stated bar is actually verifiable: could an independent critic compare a candidate against it and reach the same verdict you would?

- **Solid** — a concrete reference artifact, or measurable/checkable criteria (passes these tests, matches this screenshot, clears a stated threshold on a named rubric dimension). Proceed to Step 3.
- **Weak or missing** — adjectives only ("make it great", "as good as possible"), no external reference, or criteria that just restate the goal. Do not proceed, and do not invent criteria yourself to fill the gap. Say plainly that the loop needs a real bar to grade against, and work it out with the user together: ask for an example, a competitor, a rubric with named dimensions, or a concrete test. Keep refining across as many exchanges as it takes until you both agree the bar is solid. Only then move on.

This gate exists because an unattended loop graded against a soft bar converges on "looks done," not "is done" — the same failure `loop-engineering`'s adoption test names: "can you write the verification check?" — a no there is disqualifying.

## Step 3 — Decompose (skip for one atomic piece)

If the objective splits into independently improvable pieces (sections of a doc, features of a build, files in a migration), break it into that list up front — the lead-agent job the technique describes. A single indivisible piece skips straight to Step 4.

## Step 4 — Builder and critic, per piece

For each piece, run two separate fresh-context agents:

- **Builder** — produces or revises the piece.
- **Critic** — grades only by comparing the current output against the agreed reference bar from Step 2. Give it the bar and the candidate, never the builder's reasoning or prior iterations. This holds regardless of dispatch mechanism: a Workflow-tool script (see below) enforces it by schema, but for an ad hoc single piece dispatched straight from here, nothing enforces it structurally — build the critic's prompt fresh from just the bar and the candidate rather than continuing the builder's thread or the main conversation. Where the format allows it, withhold which side is the reference — a labeled "which one is AI" question invites leniency a blind comparison doesn't.

This is the `dynamic-workflows` adversarial-verification pattern with a concrete artifact standing in for the rubric: the builder never grades itself.

## Step 5 — One round

The critic returns the single biggest remaining gap versus the bar, not an exhaustive list. The builder addresses that gap. Repeat. Grading one gap at a time keeps each round focused instead of producing a scattershot revision.

## Step 6 — Stop conditions

Per piece, stop on whichever comes first:

- The critic confirms the candidate matches the bar.
- The iteration cap is hit (set one before starting — 5 is a reasonable default absent other guidance).
- Two consecutive rounds close no meaningful gap — reflect-or-kill: abandon the piece or escalate to the user rather than grinding identical attempts.

## Step 7 — Report

Per piece: rounds taken, final critic verdict, and — for anything that hit the cap without matching — flag it explicitly rather than folding it into an overall "done."

## Composing with native primitives

This skill is a recipe, not a new engine — pair it with what already exists rather than reinventing it:

- Pair the loop with `/goal`, using the Step 2 bar as its success criteria, for a hard completion condition instead of a soft one.
- For 3+ pieces, or any run big enough to want its own script, write a Workflow-tool script composing `fan-out-and-synthesize` (the decomposition) + `adversarial-verification` (the critic) + `loop-until-done` (the per-piece round-repeat) — see `dynamic-workflows` for the pattern APIs and `.claude/workflows/loop-until-clean.js` as a structural template: same shape, different stop condition — that script stops on two dry rounds, this stops on "matches the bar."
- For loop anatomy (trigger, isolation, state, verification) and the four loop types, see `loop-engineering`.
- A recurring cadence ("gauntlet this nightly against the latest reference") composes with `/schedule`; treat the Step 2 negotiation as one-time setup captured in the `/goal`, not re-litigated on every scheduled run.

## Not this skill

- Open-ended discovery with no known target count ("find every X," "sweep until clean") is `loop-until-clean`, not this — that stops on "nothing new found," this stops on "matches a reference."
- No reference artifact exists and the user won't commit to a proxy for one — force a fixed rubric instead, or fall back to a plain reviewed iteration (`superpowers:requesting-code-review`); don't run a gauntlet against a bar that doesn't exist.
