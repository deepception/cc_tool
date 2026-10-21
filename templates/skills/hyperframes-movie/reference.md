# hyperframes-movie reference

## Pacing model

- **Reading need** = 0.4 s to find a line + words ÷ 3 per second. A line is "shown" from its cue +
  0.15 s (the entrance settling) until it is swapped out, or the scene ends, minus 0.2 s when the next
  scene enters with a flood that covers it early. `reading_audit.py` applies exactly this.
- **Flurries are exempt**: a fast run of options (seasons, shapes, names) is read as motion, and only
  the line it lands on must pass. Mark it `{"flurry": True}`.
- **Calibration**: a 50 s first cut with 54 % frozen time and an event every 4.5 s read as boring; a
  38.75 s second cut at 6 % frozen and an event every 1.3 s left 22 of 34 lines unreadable; a 56.6 s
  third cut at 18 % frozen, an event every 1.9 s and zero short lines was accepted. Readability buys
  length: if the film runs long, cut content rather than speed.
- **Typical scene lengths at 128 BPM** (beat 0.469 s): hook 10–12 beats · brand reveal 10 · a
  feature scene 12–17 · a three-panel section 15 (6 + 4 + 5) · recap 2 beats a word + 1 · end 6 s.
- **Holds**: a reading hold longer than ~2 s needs a beat accent on an object (nod, squash, sheen,
  pile bob) or a live ground. Text that is being read never moves.

## Motion vocabulary (helpers live in templates/scene.html)

| Move | Use | Recipe |
|---|---|---|
| blur-up | default entrance | fromTo opacity 0, y 26, blur 10 → settle, 0.9–1.05 s `expo.out` |
| slam | a headline or one word on a beat | fromTo scale 1.4–1.9, blur 14–18, chromatic text-shadow → crisp, 0.22–0.35 s `expo.out` |
| chromatic split | energy on slams | `textShadow` cyan/magenta ±12–16 px → 0 (tween the whole string; same structure both ends) |
| shake | impacts (stamps, tally, drops) | three sequential `.to` on the stage, 0.035/0.045/0.16 s, ±10–22 px |
| stagger on 16ths | lists, cards, dots | `stagger: B / 4` |
| hard cut + flash | panel → panel, recap words | `tl.set` panel opacity on the beat; fx-top flash 0.25–0.45 |
| flood | section change | `enter: "flood"`: fx-top paints a glossy liquid wave across the cut |
| iris match cut | brand → next scene | an element of the logo (a dot) scales to fill the frame in the next scene's ground colour |
| nod | beat accent during holds | `nod(selector, at)`: y −18, yoyo, stagger 0.06 |
| dive | leaving a hero | the group scales 1.35 with blur toward a focal point, `power3.in`, 0.7 s |

House rules: `power3`/`expo` settles, no bounce or elastic; no breathing loops or slow pans in the
back half of a scene (prefer stillness plus beat accents); entrances are `fromTo`; exits only inside
a scene or on the final card (cuts and floods are the exits).

## Scene recipes (adapt the content; each gives the scene one visual idea)

- **Frenzy → consequence** (hook): items slam in one per beat with a counter that climbs; then a
  stamp per beat with shake and the counter falling; then a two-word punchline held ~2 s.
- **Logo slam**: the mark alone, huge and blurred → crisp on the drop; the wordmark wipes open via
  `clipPath: inset(0 68% 0 0) → inset(0 0 0 0)` while the lockup slides to centre; the tagline one
  word per beat; a bloom (radial gradient, blur 40 px) behind the mark's accent dot.
- **Flurry → landing** (any "many options, one answer"): a 3D object or card re-tints through every
  option with decelerating gaps that sum to whole beats; the label swaps on each; the landing gets a
  spin, a flash, a boom, and then its supporting lines on later beats.
- **Panel triad**: hard cuts between full-bleed panels in different grounds; each has one word
  (slam), a ≤ 4-word note, and an object (lipsticks rising, chrome rings, icons) that lands on
  sixteenths and nods during the hold.
- **Parametric morph**: an SVG path built from a few width parameters (Catmull-Rom through one side,
  mirrored); GSAP tweens the parameter object and the timeline's `onUpdate` redraws the path, so any
  shape morphs into any other with no plugin. A giant faint letter behind names each state.
- **Sorting game**: cards drop onto a stack; each beat-and-a-half the top card is stamped, held
  0.4 s, flung to its pile (raise its z-index first); the tally slams into the emptied centre only
  after the last card has landed.
- **Chat**: question bubble pops from its corner; typing dots; the answer leads with the verdict;
  follow-up items arrive as their own bubbles (a bubble with hidden children keeps their space and
  reads as an empty box).
- **Recap**: one word per panel on full-bleed brand colours with a counter, a progress bar and an
  outline "echo" (stroked copy scaling out); ornaments under words are graphics, never sentences.
- **End card**: lockup slam on the final hit, tagline words on half-beats, a row of brand dots that
  ripple on the beat, CTA pill + URL, platforms; ≥ 6 s.

## Three.js and shaders

- Import in a module script inside the template: `import * as THREE from "https://cdn.jsdelivr.net/npm/three@0.181.2/+esm"`.
- Renderer: `preserveDrawingBuffer: true`, `setPixelRatio(1)`, fixed size; `alpha: true` +
  `setClearColor(0, 0)` for overlays.
- Drive it from the scene's own paused GSAP timeline: a state object tweened over the scene's length
  plus `onUpdate: render`. The adapter's `hf-seek` carries global time, which is wrong inside a
  scene that starts later.
- Raw-hex `ShaderMaterial` colours need `THREE.ColorManagement.enabled = false`, or every colour comes
  out darker. Standard materials (metals) keep colour management and look best with
  `ACESFilmicToneMapping` and an environment from `PMREMGenerator.fromScene()` of a hand-built
  softbox room (a dark BackSide box plus a few white boxes): no addon imports needed.
- Fluttering stacked planes intersect if their waves are out of phase: share the phase and separate
  them in z (≥ 0.07). Round their corners with an SDF in the fragment shader and `discard`.
- Keep WebGL contexts to about five (aura, fx, and one per 3D scene). Studio, snapshot and render
  all ran fine on hardware GL; software fallback also played at 1×.

## HyperFrames contract and traps

- Sub-composition: everything (style, markup, script) inside `<template>`; root `#root` with
  `data-composition-id` matching the host and `window.__timelines["<id>"]`; three-only roots need
  `data-duration`.
- Asset paths in sub-comps are root-relative: `assets/img/x.webp`.
- Scoped CSS, global GSAP selectors → prefix ids/classes per scene.
- A host element with nested children belongs in its own sub-composition (lint: `nested_structure_needs_subcomposition`).
- Audio: every `<audio>` needs an `id`; overlapping clips need different `data-track-index` lanes
  (`build.py` assigns them).
- Lint reads tween positions statically: literal numbers (what the cue markers produce) are safe; a
  variable computed in a loop is evaluated at its initial value and causes false overlap warnings.
- `overlapping_gsap_tweens`: two tweens on one property must not touch; leave ≥ 0.01 s.
- Layout audit: `data-layout-allow-overflow` on glows and off-canvas washes; `data-layout-allow-overlap`
  on each deliberately layered text element; `data-layout-ignore` on ghost typography (also skips contrast).
- Occlusion audit: it re-enables `pointer-events` on everything, then treats any `<canvas>`, `<img>` or
  `<video>` on top as opaque. A full-screen shader overlay therefore "hides" every line of text unless
  it is `visibility: hidden` between its events (the fx-top template toggles it per flood/flash window).
  The only other opt-out is per text element (`data-layout-allow-occlusion`), which blinds the audit.
- Contrast audit measures a rotated element's axis-aligned box, so a tilted label over a photo can
  fail while its pixels pass; confirm with a zoomed snapshot (`snapshot --at t --zoom "x,y,w,h"`) and report it.
- `snapshot`: `--describe false` (else Gemini), `--timeout 30000` with WebGL, a new `-o` directory
  each time (deleting directories may be blocked).
- Studio waveforms 500 without FFmpeg; playback is unaffected.
- `preview --background --json` prints the Studio URL; it survives the shell.

## Visual review: defects to look for in every sheet

- Text overlapping text during a transition (a big title still flying while its small copy appears).
- An element peeking into frame before its entrance (start positions far enough off-canvas).
- A box reserving space for hidden children (empty bubble, empty card band).
- Labels clipped by their container (a rotated stamp wider than its card).
- An ornament the same colour as its ground (a blue dot on a blue panel).
- A result that appears before the action that produces it finishes (a tally over the last card).
- 3D glitches: interpenetration seams, an object cut off by the frame edge.
- Anything that reads as a static slide for more than ~2 s with nothing on the beat.
- Brand drift: fonts, colours or tone that are not the product's.

## Audio

`score.py` is seeded and licence-free; it follows the schedule, so re-run it after every timing
change. It cannot be listened to by the agent: check its per-scene RMS (the drop and the recap
loudest, a break quieter, the outro fading) and ask the user to listen. Sample SFX bundled with
HyperFrames (Pixabay licence, `skills/media-use/audio/assets/sfx/` inside the installed package)
can be added through `SAMPLE_SFX`; synthesized `events` usually cover it.
