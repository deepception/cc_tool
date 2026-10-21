---
name: hyperframes-movie
description: >-
  Builds a complete, playable HyperFrames Studio movie (product launch, promo or explainer film:
  kinetic type, Three.js shader layers, original synthesized score) and iterates it against measured
  gates for readability, rhythm, layout and real-time playback until it is excellent. Use only when
  the user explicitly invokes /hyperframes-movie.
disable-model-invocation: true
argument-hint: "[product, URL or brief] [length e.g. 50s] [aspect 16:9 | 9:16 | 1:1]"
---

# HyperFrames movie

Make a film a ten-year-old would watch to the end without knowing the product, in which every line
can still be read at normal speed. Both ways to miss are known and measured, so judge with the gates,
not by eye:

| Cut | Frozen ≥1 s | Event every | Unreadable lines | Verdict |
|---|---|---|---|---|
| first try | 54 % | 4.5 s | few | "slow, boring" |
| second | 6 % | 1.3 s | 22 of 34 | "impossible to read" |
| third | 18 % | 1.9 s | 0 | "better" |

The working method behind those numbers: one `scripts/schedule.py` owns every time in the film,
`build.py` writes those times into the HTML, and two scripts measure readability and rhythm.

## Gates: the film is done when all pass

| # | Gate | Command (from the project) | Pass |
|---|---|---|---|
| 1 | build | `python3 $SKILL/scripts/build.py .` | exit 0 |
| 2 | lint | `npx --yes hyperframes@<pin> lint` | 0 errors, 0 warnings |
| 3 | check | `npx --yes hyperframes@<pin> check` | "Check passed"; every warning explained |
| 4 | reading | `python3 $SKILL/scripts/reading_audit.py .` | 0 short lines |
| 5 | rhythm | `uv run --with numpy --with pillow python $SKILL/scripts/rhythm.py . <scratch>/r<N>` | RHYTHM: PASS |
| 6 | visual | read every `<scratch>/r<N>/sheet-*.jpg` | none of the defects in reference.md § Visual review |
| 7 | playback | `node <scratch>/pb/playback.mjs <studio url> <scratch>/p<N> <seconds>` | PLAYBACK: PASS (rate ≈ 1×, no stalls) |
| 8 | truth | compare on-screen claims with the brief's sources | every claim traceable |

Run the rhythm snapshots into a fresh directory each round: `rhythm.py` reuses frames it finds.
Gate 7 needs `puppeteer-core` next to the script (Node resolves imports from the script's own
folder): `mkdir -p <scratch>/pb && cp $SKILL/scripts/playback.mjs <scratch>/pb/ && (cd <scratch>/pb && npm init -y >/dev/null && npm i puppeteer-core@25)`.
On an unattended run, gate 6 goes to a fresh reviewer subagent given the sheets and the defect list,
because the worker is a lenient judge of its own frames.

## Setup

```bash
SKILL=.claude/skills/hyperframes-movie        # ~/.claude/skills/hyperframes-movie if installed globally
P=<project dir>                                # e.g. marketing/<product>-film
HYPERFRAMES_SKIP_SKILLS=1 npx --yes hyperframes@latest init "$P" --non-interactive --example=blank
grep -o 'hyperframes@[0-9.]*' "$P/package.json" | head -1      # the pin, written as <pin> below
mkdir -p "$P/scripts" "$P/compositions" "$P/assets/fonts" "$P/assets/img" "$P/assets/audio"
cp "$SKILL"/templates/{schedule,score}.py "$P/scripts/"
cp "$SKILL"/templates/{aura-bg,fx-top}.html "$P/compositions/"
```

Write the pin out literally in every later call (`npx --yes hyperframes@0.8.123 lint`), never as
`hyperframes@$PIN`: bash-guard asks before an `npx` whose version is a variable, and a pinned
cached package runs without a prompt.

`HYPERFRAMES_SKIP_SKILLS=1` stops `init` from rewriting the user's global skill set. Each scene is
`sed 's/__ID__/<id>/g' $SKILL/templates/scene.html > $P/compositions/<file>.html`. Fonts must be
local files (`@font-face` is required by lint): take the product's own, or OFL files from
`github.com/google/fonts/raw/main/ofl/<family>/`. Check `npx --yes hyperframes@<pin> doctor`: Studio
needs Chrome; an MP4 render also needs FFmpeg, which is the user's install to make, not yours.

## Workflow

**1. Research, then write the brief** at the top of `schedule.py`. Find what the product actually
does and who it is for from its docs, code, site and copy rules; pick the one value proposition the
film is built around, and record where each on-screen claim comes from. Pull brand tokens (colours,
fonts, logo files, imagery, the motion feel of the product's own UI) from source, not memory. No
real-looking people presented as customers, no invented numbers. Ask the user only for what the
repo cannot answer (usually: destination/aspect, or length if they care).

**2. Story and schedule.** Shape: hook (a relatable problem, moving from frame 0, resolved within
~6 s) → brand reveal → one idea per scene, each with a signature visual → a fast recap → end card
(≥ 6 s, CTA on screen ≥ 3 s). Budget copy: ≤ 7 words per line, about 3 lines per scene. Fill
`SCENES` (beats, ground, enter, energy, cues, copy, events) and run the reading audit *before*
building scenes: it prints the minimum beats every scene needs, which is cheaper than finding out
from frames.

**3. Scenes.** One composition per scene from `templates/scene.html`. Every tween position that
matters is a cue: `/*@cue:punch*/ 7 * B`, never a bare number, so the schedule can move it. A scene
of text on a background is a slide, not a film: give each scene one visual idea (3D object, morph,
sorting game, chat, flurry-then-land; recipes in reference.md) and keep the brand's design language.

**4. Build, score, gate.** `build.py`, then `uv run --with numpy --with scipy python scripts/score.py`,
then gates 2–8. Start Studio once with `npx --yes hyperframes@<pin> preview --background --json` (an
agent-owned foreground preview dies with the shell); it serves edits live.

**5. Iterate.** Fix the worst failing gate first, re-run from build, and log each round in the reply
as one row: round · length · frozen % · event cadence · short lines · defects fixed. Stop when all
gates pass, or after five rounds, and say which gate still fails and why.

**6. Deliver.** The Studio URL, the gate results with real output, the per-round table, and the
open items: you cannot hear the score (say so, ask the user to listen), MP4 render needs FFmpeg, and
any claim that depends on launch status.

## Speed and rhythm: which knob to turn

| Symptom | Turn |
|---|---|
| audit: line SHORT | cut words first; else move its cue earlier; else lengthen the scene to the printed minimum |
| audit: "(slack: could shorten)" | shorten that scene's beats; the reading time is already covered |
| rhythm: frozen > 25 % or a hold > 2.5 s | beat accents on objects in the hold (`nod`, squash, a sheen), a live ground (`ground: "aura"`), a rotating 3D object. Text being read never moves |
| rhythm: events sparser than every 2.2 s | a flurry before a landing, slams on beats, a flash or flood on the cut |
| whole film too long | drop the recap montage or a panel; never squeeze a line below its need |
| whole film too slow in feel, yet readable | raise `BPM` (128 → 132), then re-run the audit |
| music and picture disagree | change the schedule, rebuild, regenerate the score; never edit times in HTML |

Cadence that works: flurry items 0.18–0.45 s, decelerating into a landing on a beat; sorted or
stamped items 1.5 beats each, with the verdict at full size ≥ 0.4 s before it moves; recap words
2 beats each, 3 for the last; feature panels 4–6 beats; scene cuts on beats, big drops on bar lines.

## Traps that cost a round each

- Sub-composition assets are root-relative (`assets/…`), not `../assets/…`.
- Sub-comp CSS is scoped, GSAP selectors are not: prefix every id and class a script touches.
- `data-layout-allow-overlap` counts only on the text element itself; ghost type gets `data-layout-ignore`.
- Never tween `letterSpacing` (lint error); use `scaleX`.
- A fromTo starting at opacity 1 shows the element from frame 0; split opacity from the slam.
- Three.js: `THREE.ColorManagement.enabled = false` with raw-hex shaders, `preserveDrawingBuffer: true`,
  and drive the render from the scene's GSAP timeline (`onUpdate`), not `hf-seek` (global time).
- A full-screen canvas over the scenes counts as an opaque occluder in `check` (it forces
  `pointer-events` on); keep overlays hidden except while they paint, as `fx-top.html` does.
- `snapshot` sends frames to Gemini when a key is set; always pass `--describe false`.
- Studio stamps `data-hf-id` into files: match edits on content, not on exact tag text.

The full list, the motion vocabulary and scene recipes: [reference.md](reference.md).
