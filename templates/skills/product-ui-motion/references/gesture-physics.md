# Gesture Physics

Drag, swipe and sheet interactions, where motion stops being decoration and becomes the
interface. Derived from Apple's *Designing Fluid Interfaces* (WWDC 2018) translated to
the web. Everything here is for elements the user's finger or pointer is touching.

The through-line: an interface feels alive when motion starts from the current on-screen
value, inherits the user's velocity, projects momentum forward, and can be grabbed and
reversed at any instant.

## Tracking

**Respond on pointer-down, not on release.** Highlight the moment it is pressed. Waiting
for `click` feels dead.

**Stay glued to the finger, at the offset they grabbed.** Snapping the element's centre
to the pointer breaks the illusion immediately.

```js
el.addEventListener('pointerdown', (e) => {
  el.setPointerCapture(e.pointerId);          // tracking survives leaving the bounds
  const grabOffset = e.clientY - el.getBoundingClientRect().top;
  // keep a short history of (position, timestamp) — you need velocity at release
});
```

**Require ~10px of movement** before committing to a direction, then track 1:1. Detect
plausible gestures in parallel from the first move and cancel the losers once intent is
clear; recognisers that only report a final state throw away the tracking you need.

**Ignore extra touch points** once a drag has begun (`if (isDragging) return`), or
switching fingers mid-drag makes the element jump.

## Interruption

Every animation a user can touch must be grabbable mid-flight and reversible without
waiting for it to finish. Animate from the **presentation value** — the element's live
on-screen transform — never from the logical target, or the interrupt visibly jumps.

When a gesture reverses, blend velocity rather than hard-cutting it; a discontinuity at
the reversal reads as a brick wall. Decompose 2D motion into independent X and Y springs
— one spring over a 2D distance desyncs when the axes have different velocities.

## Velocity handoff

When the gesture ends, the animation continues at the finger's exact velocity. This is
the seam that separates "fluid" from "fine". Motion takes absolute px/s directly via its
`velocity` option; APIs wanting a relative figure normalise by remaining distance:

```
relativeVelocity = gestureVelocity / (targetValue − currentValue)
```

Element at `y=50`, target `y=150`, finger at 50px/s → `50 / 100 = 0.5`. Undefined when
already at the target — guard it.

## Momentum projection

Don't snap to the boundary nearest the *release point*. Project where the gesture was
going — the same deceleration model scrolling uses — and snap to the target nearest
*that*. This is what makes a flick feel like a throw.

```js
// decelerationRate ≈ 0.998 for normal scroll feel; 0.99 is snappier
function project(initialVelocity /* px/s */, decelerationRate = 0.998) {
  return (initialVelocity / 1000) * decelerationRate / (1 - decelerationRate);
}

const projected = currentPosition + project(releaseVelocity);
animateSpringTo(nearestSnapPoint(projected), { velocity: releaseVelocity });
```

The textbook `v²/(2·decel)` is not what ships here — the exponential-decay form above is.

For dismissal, don't gate on distance alone: compute `Math.abs(distance) / elapsedMs`
and dismiss above ~0.11 regardless of how far it travelled. A flick should be enough.

Decide reverse-vs-commit from the **sign of the velocity** at release, not from position.

## Boundaries

Resist progressively instead of stopping. A hard stop reads as frozen; rising resistance
reads as responsive-but-empty.

```js
// The further past the bound, the less the element follows
function rubberband(overshoot, dimension, constant = 0.55) {
  return (overshoot * dimension * constant) / (dimension + constant * Math.abs(overshoot));
}
```

## Spatial consistency

What slides in from the right dismisses to the right. Enter and exit share a path, and
reversible transitions mirror their easing (inverse control points for the two
directions). A surface that arrives one way and leaves another disorients.

Intermediate motion should point at the outcome — people predict the final state from
the trajectory, so make the in-between frames telegraph it rather than interpolate
blindly.

## Reduced motion

Gestures are the case where "reduced" cannot mean "removed" — the interaction is the
motion. Replace projected/spring settling with a short cross-fade to the committed
state, drop overshoot entirely, and keep the 1:1 tracking during the gesture itself.
Also honour `prefers-reduced-transparency` (raise background opacity, drop blur) and
`prefers-contrast` (near-solid backgrounds, defined borders) on any sheet or drawer
built as a translucent material.
