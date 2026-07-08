# Archetype: Immersive Scroll Experience

An award-level (Awwwards/FWA-style) single-page scroll journey: pinned chapters, scroll-scrubbed film, a metamorphosis pivot, and a motion language stated once and obeyed everywhere. This is the largest archetype — only reach for it when the brief genuinely asks for an *experience*, not a landing page. Fill every `{{PLACEHOLDER}}`; cut chapters you don't need (5–7 sections is the sweet spot).

---

# MASTER PROMPT — "{{BRAND}}" {{THEME_ONE_LINER}}

Build a single-page scroll experience called **{{BRAND}}** — {{JOURNEY_METAPHOR — a concrete A→B arc, e.g. "a river journey from sakura morning to lantern night", "a descent from orbit to ocean floor"}}. {{ART_DIRECTION}} art direction. Everything below is a hard requirement — follow it exactly.

## 1. Tech stack

- {{BASE — default: vanilla HTML/CSS/JS or Vite}}; **GSAP 3 + ScrollTrigger** for all scroll choreography (pinning, scrubbing, timelines); **Lenis** smooth scroll (`lerp: 0.08`), synced via `lenis.on('scroll', ScrollTrigger.update)` + `gsap.ticker`.
- {{WEBGL — three.js r160+ if the spec uses particles/shaders/dissolves; otherwise delete every WebGL item below}}.
- Scroll-scrubbed `<video>` driven by `currentTime` (§6). No UI frameworks; custom everything.

## 2. Assets

| Key | Asset |
|---|---|
| `PLATE_1..N` | {{still images for each chapter — exact URLs or user-supplied}} |
| `SCROLL_FILM_1..M` | {{10–15s keyframe-dense mp4s for scrubbed chapters}} |
| `HERO_VIDEO` | {{optional ambient loop}} |

Preload the hero plate, film metadata, and fonts behind the loader (§4).

## 3. Design system

**Colors** — CSS custom properties, exact values, one-phrase role each. Required roles: paper/base bg, deep bg (the pivot target), primary dark (ink), text-over-imagery, 2 material accents, 1 signal accent used *only in tiny doses* (logo mark, hover underlines, tick marks, one CTA). Borders: 1px ink at 12–15% on light, cream at 15% on dark.

```css
:root {
  --paper: {{HEX}};  --paper-deep: {{HEX}};  --ink: {{HEX}};
  --night: {{HEX}};  --night-deep: {{HEX}};  --cream-text: {{HEX}};
  --accent-a: {{HEX}};  --accent-b: {{HEX}};  --signal: {{HEX}};
}
```

**Typography** — display serif {{DISPLAY_FONT}} (the voice of the site; mixed roman/*italic* within one headline — italicize the emotional word), UI sans {{UI_FONT}} for kickers/captions/buttons, {{OPTIONAL_SCRIPT_ACCENT}}. Clamp scale: `--display-xl: clamp(4rem, 11vw, 11.5rem)` (hero + footer wordmark), `--display-lg: clamp(3rem, 7vw, 7rem)` (section heads), `--display-md`, `--body`, `--kicker: 0.6875rem` at `0.32em` tracking, uppercase. Kickers always render as: tiny signal-colored tick + tracked uppercase label, `— THE {{SECTION_NAME}}`.

**Texture** — full-viewport animated film grain (shader or SVG `feTurbulence`, opacity 0.05, `mix-blend-mode: overlay`); every image in a 1px inset mat border offset ~10px outside its edge; 3% corner vignette on light sections.

## 4. Preloader

Base-paper bg; brand mark + wordmark tracking in from `letter-spacing: 0.5em → 0.02em` (1.2s `power3.out`); 1px progress line tied to *real* asset loading with a percentage counter; on complete, wipe upward via `clip-path: inset(0 0 100% 0)` 0.9s `power4.inOut`, revealing the hero already mid-settle. Minimum display 1.6s.

## 5. Navigation

Fixed, 88px, transparent initially; on scroll >120px, bg fades in as paper at 82% + `backdrop-filter: blur(12px)`, height compresses to 68px; over dark sections toggle a `.nav--dark` class via ScrollTrigger `onToggle`. Left: mark + tracked wordmark. Right: 4 links (hover: 1px signal underline draws left→right, `scaleX` 0.35s) + pill-outline CTA. Left screen edge: a vertical 160px progress rail with a fill tracking total scroll, dots per section, and the current index `01 / {{N}}` rotated 90°.

## 6. Chapter patterns (compose 5–7 from these)

**Hero (pinned exit)** — 100svh full-bleed plate {{as a three.js textured plane with a subtle displacement/ripple shader on {{WATER_OR_SKY_REGION}}, else a plain cover image}}. Centered kicker + `--display-xl` two-line headline (masked line-rise from `y:110%` with 2°→0 rotation settle, stagger 0.12s, 1.1s `power4.out`); plate starts `scale 1.12` easing to 1.0 over 2.4s. Optional signature particle system: instanced {{PARTICLE — petals/embers/dust}} (300–400 desktop / ~120 mobile), per-instance size/phase/depth, curl-noise drift, radial mouse-repulsion (180px, quadratic falloff), rendered over the plate but under the text. Pin for `+=120%`: plate scales to 1.18 and darkens 0→0.25, headline exits at 1.4× scroll speed, next section slides over like a paper sheet (top corners `border-radius: 32px 32px 0 0` flattening to 0).

**Manifesto** — paper bg, 16vh padding. Kicker; a `--display-lg` serif statement across ~11 of 12 columns with mixed italics and **inline image tokens** (small rounded-rect crops, ~140×90, sitting on the text baseline between words, scaling in from 0 width as their line reveals); masked line-rise stagger 0.09s at 70% viewport. Right-aligned body paragraph + draw-on-underline text link; a row of 2–3 large counter-animating stats.

**Scroll-scrubbed film (pinned)** — `<video preload="auto" muted playsinline>` covering 100svh; pin `+=400%`; `onUpdate`: target `currentTime = progress * duration`, smoothed through a rAF lerp (`current += (target-current)*0.1`) so scrubbing is buttery, never steppy (file must be keyframe-dense). 2–3 text chapters fade in/out over fixed progress windows (e.g. 0.05–0.3, 0.38–0.63, 0.7–0.95): kicker + one `--display-md` line each. Chrome: vertical chapter numerals (active in signal color), bottom 1px scrub-progress bar with tabular timecode. 6% dark vignette.

**Horizontal gallery (pinned)** — static header (kicker, `--display-lg` heading, "( scroll )" hint); a track of {{3–4}} oversized cards (`min(66vw, 900px)` × 68vh) translating to `-(trackWidth - viewport)`, `scrub: 1`, pin `+=250%`. Inner parallax: card images scaled 1.15 counter-translating ±7%. Giant outlined numbers (`-webkit-text-stroke`, transparent fill) that fill solid as their card crosses center. Custom cursor becomes a circular DRAG badge (`mix-blend-difference`).

**Metamorphosis pivot (the emotional turn)** — pinned `+=200%`; bg color scrubbed `--paper → --night` (nav flips, particle system swaps character — e.g. pink petals falling → amber embers rising). A centered portrait frame scales to full-bleed (`clip-path: inset(11vh 29vw) → inset(0)`) while {{a WebGL noise-threshold dissolve (`smoothstep(uProgress±0.08, noise(uv*4.0))`) crossfades PLATE_A → PLATE_B — day literally dissolving into night, else a masked crossfade}}. Overlay line in `--display-lg`, ink→cream in sync.

**Footer (dawn)** — snap back to paper (0.8s). Kicker + giant CTA line + circular signal-colored button (120px, magnetic hover: translates toward cursor within 24px, springs back `elastic.out(1,0.4)`). Link columns + vertical accent strip. The wordmark at `--display-xl` fitted nearly full-width via JS, rising with a masked reveal {{particles settling on its cap line, if particles exist}}. Bottom rule: © line left, journey endpoints right.

## 7. Global motion language

Reveals `power4.out`; pinned scrubs linear (`scrub: 1–1.5`); color shifts scrubbed with no easing; micro-interactions `power2.out`; magnetic/elastic `elastic.out(1,0.4)`. Every heading = masked line rise (110%→0, 1–2° settle). Every image reveal = `clip-path: inset(100% 0 0 0) → inset(0)` 1.2s `power4.inOut` with inner counter-scale 1.25→1. Decorative parallax via `data-speed` (0.85×–1.2×) through a single ScrollTrigger. Custom cursor: small ink dot + trailing ring (lerp 0.15), morphing over links/cards/films. **`prefers-reduced-motion`: kill pins/scrubs, videos become posters, particles static, reveals become fades.**

## 8. Performance & responsive

One shared `WebGLRenderer` (antialias off, DPR capped 1.75), scenes paused offscreen via IntersectionObserver. Videos `preload="metadata"` until within 150% viewport. Mobile (<768px): particle count ~⅓, horizontal gallery becomes native swipe (no pin), film pins shorten, display-xl clamps down, inline headline images hidden, custom cursor disabled. Lazy-decode all plates. Target: 60fps on an M1 laptop.

## 9. Acceptance checklist

1. Loader → hero entrance feels like one continuous shot.
2. The signature system (particles/shader) reacts to the mouse and changes character across the pivot.
3. Films scrub perfectly smoothly with lerped `currentTime`.
4. The pivot uses the specified dissolve, not a plain crossfade.
5. Gallery has inner-parallax images and outlined numbers that fill on center.
6. Nav adapts light/dark automatically; progress rail tracks all sections.
7. Footer wordmark nearly spans the viewport.
8. Grain, mat borders, vignettes present everywhere; reduced-motion path fully works.

---

## Vary it (so two fills don't look like siblings)

The metaphor does the differentiating: a new `{{JOURNEY_METAPHOR}}` should change palette roles, particle character, film content, the pivot's direction (light→dark, macro→micro, order→chaos), and the typography's voice. Also vary: chapter count and order, which chapter is pinned longest, horizontal vs vertical gallery, where the manifesto sits, serif vs sans as the display voice. Keep: the motion vocabulary, the lerped scrubbing, the pin budgets, the checklist discipline.
