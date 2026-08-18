# Motion Standards

The long tail behind [SKILL.md](../SKILL.md): springs, sequencing, the techniques worth
knowing, and how to review motion. Cite exact values from here rather than approximating.

## Springs

Springs have no duration — they settle from their parameters, and they keep velocity
when interrupted, which is why gesture-driven motion wants them and `@keyframes` never.

Two parameterisations, and they are **not interchangeable**:

```js
// Designer-facing (Motion). `duration` here is perceptual settle time.
{ type: 'spring', bounce: 0, duration: 0.4 }

// Physics. ζ = damping / (2·√(stiffness · mass))
{ type: 'spring', mass: 1, stiffness: 100, damping: 20 }   // ζ = 1.0, no overshoot
```

Default to critically damped (`bounce: 0` / ζ = 1.0). Add bounce only when the gesture
itself carried momentum — a flick, a throw, a drag release. Overshoot on a menu that
faded in reads as a mistake; overshoot on a card you threw reads as physics. Subtle
means `bounce: 0.1–0.3`, or ζ ≈ 0.8 (`damping: 16` at `stiffness: 100, mass: 1`).

Apple publishes damping/response pairs for its own interactions — move/reposition
`1.0 / 0.4`, rotation `0.8 / 0.4`, drawer `0.8 / 0.3`. **Do not paste those response
values into Motion's `duration`.** Response is the oscillator period; `duration` is
perceptual settle time. Map `bounce ≈ 1 − damping`, then tune duration by eye — it
lands above the response figure, not equal to it.

The package is `motion`; `framer-motion` is the legacy name:

```js
import { useSpring } from 'motion/react';
```

For decorative mouse-tracking, interpolate through a spring rather than binding the
value straight to pointer position — direct binding has no momentum and reads as
artificial. Only do this where the motion is decorative; on a graph someone is reading,
none is better.

## Sequencing

**Stagger** group entrances by 30–80ms per item. Longer reads as slow. Stagger is
decorative — never block interaction while it plays.

**Asymmetric timing.** Slow where the user is deciding, fast where the system responds.
A hold-to-confirm fills over 2s linear and snaps back in 200ms `ease-out`. Symmetric
timing on a press-and-release or hold interaction is a defect.

**Exit faster than enter** for dismissals. The user has already decided; the interface
is confirming, not presenting.

## Techniques

**`clip-path: inset(t r b l)`** — each value eats in from that side. Reveal-on-scroll
(`inset(0 0 100% 0)` → `inset(0 0 0 0)`), hold-to-delete fills, before/after sliders,
and seamless tab colour transitions (duplicate the tab list, style the copy active, clip
it) — all without extra DOM.

**Percentage translates** are relative to the element's own size, so `translateY(100%)`
moves an element by its own height whatever that is. Prefer them over pixel values for
toasts and drawers.

**Blur to mask a crossfade.** When two states visibly overlap during a swap despite
tuned easing, a `filter: blur(2px)` during the transition blends them into one perceived
transformation. Keep blur under 20px — it is expensive, especially in Safari.

**`scale()` scales children** — font, icons, content. That is the feature that makes
press feedback feel physical.

**WAAPI** gives JS control at CSS performance for programmatic motion:

```js
el.animate([{ clipPath: 'inset(0 0 100% 0)' }, { clipPath: 'inset(0 0 0 0)' }],
  { duration: 1000, fill: 'forwards', easing: 'cubic-bezier(0.77, 0, 0.175, 1)' });
```

**Skip the delay on adjacent tooltips.** In a toolbar, the first tooltip should wait out
the normal activation delay, but once the user is already moving between triggers,
follow-up tooltips should appear instantly — the delay's job (avoid firing on transient
mouse-overs) is already satisfied. Track a shared "recently active" flag and switch
adjacent triggers to `transition-duration: 0ms` (e.g. via a `[data-instant]` attribute)
for both the delay and the animation while it's set.

## Where JS motion loses to CSS

Motion's `x` / `y` / `scale` shorthands compose into a `transform` recomputed per frame
on the main thread, so they stutter while the page is loading or scripting. The library
hands work to the compositor only in a narrow case — a single, uninterrupted,
non-layout animation of a supported property with no `onUpdate` — and `motion/react`
narrows it further. The rule that survives regardless of version: **CSS or WAAPI for
predetermined motion, JS springs for dynamic, interruptible, gesture-driven motion.**
Re-check motion.dev/docs/performance before citing a mechanism; this is one library's
internals, not a platform guarantee.

Related: driving a child's transform by setting a CSS variable on its parent recalculates
styles for every child. Set `transform` on the element directly.

## Reviewing motion

Report findings as one table — paired before/after is denser than prose and shows its
own reasoning:

| Before | After | Why |
|---|---|---|
| `transition: all 300ms` | `transition: transform 200ms var(--ease-out)` | `all` animates unintended properties off-GPU |
| `transform: scale(0)` | `transform: scale(0.95); opacity: 0` | Nothing appears from nothing |
| `ease-in` on a dropdown | `var(--ease-out)` | Delays the moment the user is watching most |
| `transform-origin: center` on a popover | `var(--transform-origin)` | Popovers grow from their trigger (modals are exempt) |

When proposing fixes, prefer earlier moves to later ones: **delete** the animation →
**reduce** it → fix the **easing** → fix the **origin/physicality** → make it
**interruptible** → move it to the **GPU** → make timing **asymmetric** → **polish**
(stagger, blur, `@starting-style`) → **accessibility and cohesion**.

Include a short **"considered and rejected"** list alongside any set of motion
suggestions — places that could animate and deliberately should not, each with the
reason. A motion review that only adds motion is a wishlist.

## Feel-checking what code can't tell you

Whether a crossfade reads as one object, whether a spring's bounce is right, whether two
coordinated properties drift — none of this is visible in a diff. Say so rather than
guessing, and check it:

- Play it at 2–5× duration, or use the DevTools animation inspector.
- Step frame-by-frame to catch drift between coordinated properties.
- Test gestures on real hardware, not the simulator.
- Look again the next day. Imperfections invisible during development surface later.

## Cohesion

Match motion to the product's personality: a playful component can be bouncier, a dense
dashboard stays crisp and fast. The easing, the duration, the visual design and the copy
should read as one decision. Opacity paired with height in an entering list has no
formula — adjust until it settles.

## Multimodal feedback (motion + sound + haptics)

When a channel beyond motion is available, three rules keep it from feeling bolted on:

- **Causality.** Fire feedback on the actual causal event (the drop lands, the toggle
  commits) — not on the animation that represents it.
- **Harmony.** Visual, sound, and haptic must land on the same frame. A haptic pulse that
  fires before a lagging CSS transition finishes breaks the illusion of one physical
  event.
- **Utility.** Reserve extra channels for moments that matter. Buzzing or chiming on
  every minor interaction trains users to ignore it.

Treat haptics as progressive enhancement, not a universal rule: the Vibration API has
real support only on Android Chrome — no iOS Safari, no desktop.
