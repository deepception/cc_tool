---
name: product-ui-motion
description: >-
  Motion and interaction craft for product UI — whether something should animate at all,
  and the exact duration, easing, transform-origin, spring config, and gesture physics
  when it should. Use when building or tuning a dropdown, modal, drawer, sheet, toast,
  tooltip, popover, command palette, tab, accordion, or drag/swipe interaction; when
  adding a transition to an app; or when motion feels sluggish, janky, or "off".
  Dashboards and admin panels included — this is the product surface `design-director`
  scopes out. Landing-page and marketing choreography goes to `design-director` instead.
user-invocable: true
---

<!-- Rule catalog derived from https://github.com/emilkowalski/skills (MIT, (c) 2026 Emil
     Kowalski) — chiefly `review-animations/STANDARDS.md` and `apple-design` — condensed
     and restructured for this repo, with several technical claims corrected against
     current browser and library behaviour; the corrections are this repo's. Detail in
     references/motion-standards.md. -->

# Product UI Motion

Motion is a cost paid every time the user sees it. The order below is the order to
decide in: whether, then how fast, then how. Skipping to "how" is what produces
interfaces that are animated but not good.

## 1. Should it animate at all?

Frequency decides. This is the question cc_tool's other design skills never ask.

| How often a user sees it | Decision |
|---|---|
| 100+/day — command palette, core nav, frequent toggles | No animation |
| Tens/day — hover, list navigation | Remove, or make it near-imperceptible |
| Occasional — modals, drawers, toasts, settings | Standard animation |
| Rare / first-run — onboarding, empty states, success | The delight budget lives here |

Name the purpose in one of these words or drop the animation: **feedback**, **spatial
consistency**, **state indication**, **preventing a jarring change**, **explanation**
(marketing/onboarding only), **delight** (rare tier only). "It looks cool" is not one.

The frequency rule is about frequency, not input device — a keyboard shortcut fired
hundreds of times a day gets nothing; a rarely-used one follows its element's budget.
Data the user is reading or acting on does not move for style.

## 2. How long?

| Element | Duration |
|---|---|
| Button press feedback | 100–160ms |
| Tooltips, small popovers | 125–200ms |
| Dropdowns, selects | 150–250ms |
| Modals, drawers, sheets | 200–500ms |

Everything except large travelling surfaces stays under 300ms. Modals, drawers and
sheets cross a whole surface, so 200–500ms is correct for them and only them. Over
500ms is a bug in product UI. Marketing pages are a different budget — `design-director`.

## 3. How?

**Easing.** Entering or exiting → `ease-out`. Moving or morphing on screen →
`ease-in-out`. Hover and colour → `ease`. Constant motion → `linear`. The CSS keywords
are weak; use the curves:

```css
--ease-out:    cubic-bezier(0.23, 1, 0.32, 1);     /* entrances, exits, most UI */
--ease-in-out: cubic-bezier(0.77, 0, 0.175, 1);    /* on-screen movement */
--ease-drawer: cubic-bezier(0.32, 0.72, 0, 1);     /* iOS-like drawer/sheet */
```

`ease-in` delays the moment the user is watching most — wrong for anything they are
waiting on. It is the conventional curve only for elements accelerating out of the
viewport entirely.

**Physicality.** Nothing appears from nothing: enter from `scale(0.95)` + `opacity: 0`,
not `scale(0)`. Trigger-anchored surfaces (popovers, dropdowns, tooltips, menus) scale
from their trigger — `transform-origin: var(--transform-origin)` in Base UI. Modals are
exempt; they are not anchored to anything and stay centred. Pressable elements get
`transform: scale(0.97)` on `:active` at 100–160ms.

**Interruptibility.** Anything triggered rapidly or driven by a gesture — toasts,
toggles, drags — needs to retarget from where it currently is. CSS transitions and
springs do; `@keyframes` restart from zero. `@starting-style` is Baseline; use it for
entry rather than a `useEffect` + `data-mounted` dance. When the element also enters or
leaves `display: none` or the top layer, add `transition-behavior: allow-discrete` and
list `display` in the transition, or the animation is skipped.

**Performance.** `transform` and `opacity` animate freely. `filter`, `backdrop-filter`
and `clip-path` also composite but cost enough to measure. Keep `width`, `height`,
`top`, `left`, `margin` and `padding` out of animation — they lay out every frame.
(`interpolate-size: allow-keywords` makes height-to-auto animatable but is Chromium-only
as of mid-2026, so keep a non-animating fallback.) **Accordions are the one sanctioned
exception** — there's no `transform` equivalent for expand/collapse. Measure the
content's real height in JS (or use a headless primitive that does) rather than
animating to `auto`, and keep the duration short since every frame costs layout.

**Accessibility.** `prefers-reduced-motion` means gentler, not zero — keep opacity and
colour transitions that aid comprehension, drop movement. Gate hover motion behind
`@media (hover: hover) and (pointer: fine)`; touch devices fire hover on tap.

## Precedence over the global taste skills

The taste-skill family (`design-taste-frontend`, `redesign-existing-projects`,
`high-end-visual-design`) carries landing-page motion advice that is wrong at product
scale. Those files are third-party and npx-managed — not editable here. On these three
points this skill wins:

| Taste skill says | For product UI |
|---|---|
| `transition: all 0.3s …` | Name the properties. `all` re-animates every inherited change. |
| "Add 200–300ms transitions to all interactive elements" | Gate by frequency first — see §1. |
| "Banned motion: `linear` or `ease-in-out`" | Correct instinct (keyword easings are weak), wrong as a ban. `ease-in-out` is right for on-screen movement, `linear` for marquees and progress. |

## Going deeper

- [references/motion-standards.md](references/motion-standards.md) — springs, stagger,
  asymmetric timing, `clip-path`, blur-masked crossfades, the `Before | After | Why`
  review format, and how to feel-check motion you can't judge from code.
- [references/gesture-physics.md](references/gesture-physics.md) — drag, swipe,
  velocity handoff, momentum projection, rubber-banding at boundaries.

## Not this skill

Layout, density, colour and type scale → the project's component system. Landing pages,
heroes, marketing choreography → `design-director`. Module or API shape →
`design-an-interface`. Motion findings during a code review → `frontend-review`
dimension 9, which reads this same catalog.
