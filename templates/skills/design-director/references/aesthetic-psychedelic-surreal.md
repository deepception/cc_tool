# Aesthetic: Psychedelic / Surreal

Controlled chaos, vivid contrast, fluid forms. The visual lineage is 60s/70s concert-poster design (Wes Wilson, Victor Moscoso, Neon Rose-era Fillmore posters) and liquid-light-show projection art, translated into a working interface rather than a static image. This is a reference doc, not a skill: design-director reads it in full and follows it as the design method for briefs that land here.

## 1. What this is for

Route here for: music/festival/tour sites, concert or event pages, creative-tool and generative-art products, entertainment and streaming brands, youth-culture or counter-culture positioning, anything explicitly asking for "trippy", "groovy", "liquid", "retro-futurist 70s", or "maximalist and loud". The brief wants to feel alive, a little dangerous, and impossible to template.

Wrong for: enterprise SaaS, fintech, healthcare, B2B trust-first pages, dashboards, data-dense product UI, anything where a first-time visitor needs to feel safe before they feel delighted. If the brief says "professional" or "enterprise-grade" anywhere, this is the wrong reference; route to `high-end-visual-design` or `design-taste-frontend` instead.

## 2. Typography

Two registers, never blurred together.

- **Display (headline, wordmark, hero word):** a genuinely fluid or bulging face, used large and used with intent.
  - Primary: **Nabla** (Google Fonts variable font; `EDGE` and `CRAP` axes push it into a dripping, gooey liquid form) for hero words and standalone lockups.
  - Secondary poster face: **Fascinate** or **Fascinate Inline**, whose letterforms already read as Fillmore-era psychedelic type without needing filter tricks.
  - Rounded, bulging, Cooper-Black-adjacent alternative: **Fraunces** pushed to `font-variation-settings: "opsz" 144, "wght" 900, "SOFT" 100`; the variable axes fatten and round the serif into poster lettering.
- **Sub-headline / section headers (moderate warp):** the same display face at a smaller size, or a straight bold weight of the body face with letters set on a gentle SVG `<textPath>` arc instead of a straight baseline. Warp is dialed down here, not off.
- **Body copy, nav, forms, buttons, legal:** a clean, unwarped, highly legible humanist sans - **Switzer** or **General Sans**, weights 400-500, minimum `16px`, line-height `1.6`.
  - This face never bends, never sits on a curve, never gets a liquid filter. It is the calm register the eye rests in between chaotic passages, and it is where most of the actual reading happens.
  - If a body paragraph is hard to read, the direction has failed regardless of how good the hero looks.
- Never warp a paragraph. Warp is a headline privilege, spent on single words or short lines, not on sentences.

## 3. Color

Palette logic, not a fixed hex list: pick a base hue wheel position and commit to a **triadic or split-complementary** spread of at least three saturated hues in active tension, e.g. hot magenta (`~330°`), acid chartreuse (`~85°`), and electric cyan (`~185°`), or safety orange against violet against spring green. Saturation stays high (70-100%), but lightness is varied deliberately so each hue can also serve as legible text-on-ground somewhere in the system.

Ground options, pick one and stay consistent:
- **Poster-paper substrate:** aged cream or kraft (`#F3E6C8`-`#E8D9A8`) with saturated inks printed on top - the authentic 60s-poster ground.
- **Deep saturated ink ground:** warm or green-leaning, never violet-leaning - oxblood `#1A0508`, deep teal-black `#03120F`.
- Never a near-black-to-violet ground. That specific move (dark base, one hue bleeding to blue-violet, soft radial glow) is the AI-purple-glow tell, and it is banned here by name, not just by accident.

The structural difference that keeps this direction distinct from that tell: AI-purple-glow is one hue, radially faded, low-saturation at the edges, alone on the page. This palette is three-plus hues, at full saturation, in simultaneous contact.

Liquid-light color blending: layer translucent, differently-hued blob shapes and mix them with `mix-blend-mode: multiply` (on light/paper grounds) or `screen` / `hard-light` (on dark grounds) so overlapping shapes generate a third color at the seam, the way oil-and-water light-show slides physically blended. This is the mechanism, not a gradient - a smooth two-stop `linear-gradient` blob is the banned move; a blend of two opaque, differently-colored organic shapes is the correct one.

## 4. Fluid-form techniques (signature territory)

- **Blob shapes via animated `border-radius`:** `border-radius: 60% 40% 30% 70% / 60% 30% 70% 40%;` on a `div`, keyframed through 4-6 distinct value sets over `10s`-`18s` with `ease-in-out`, looping. Never a perfect circle or a single static blob shape - always mid-morph.
- **SVG path morphing:** author 3-5 closed blob paths with matching point counts, then animate between them with native SVG: `<path d="..."><animate attributeName="d" values="path1;path2;path3;path1" dur="14s" repeatCount="indefinite" calcMode="spline" keySplines="0.42 0 0.58 1; 0.42 0 0.58 1; 0.42 0 0.58 1" /></path>`. This is the reliable, dependency-free way to get true liquid morphing in the DOM.
- **Gooey merge/split (the classic "goo" filter):** stack an SVG filter of `feGaussianBlur` (`stdDeviation` 8-12) into `feColorMatrix` with a steep alpha contrast, applied to a container holding two overlapping blobs; as the blobs approach they visually fuse into one continuous liquid mass, then separate cleanly on the way back out. Use for hover states, section transitions, and cursor-follow effects.
- **Warped grid / heat-shimmer distortion:** an SVG `feTurbulence` (`baseFrequency` 0.01-0.03) piped into `feDisplacementMap` (`scale` 8-20), referenced via `filter: url(#warp)` on a section's grid lines or on photography edges. Keep scale modest - this should read as liquid shimmer, not glitch or noise.
- **Photography treatment:** duotone or tritone via `feColorMatrix`/CSS `filter` combinations that push photos into the active palette's hues, plus an optional halftone-dot overlay (repeating SVG dot pattern at `mix-blend-mode: multiply`). Raw, un-recolored stock photography breaks the world instantly.

## 5. Layout and composition

The chaos is deliberate and localized, never uniform. Underneath everything sits a strict 12-column or baseline grid; one or two focal elements per section (the headline, one hero blob-image, one pull quote) are allowed to rotate, overlap, or break the grid edge. Everything else - nav, body copy, forms, footer - stays gridded and calm. That contrast between one wild element and a disciplined field around it is what reads as "controlled" chaos instead of noise.

Section boundaries use organic dividers (an SVG wave or blob-edge shape between sections) instead of straight horizontal rules. Composition is asymmetric by default: off-center headlines, layered z-index collage of shape + type + image, diagonal internal alignment - but never let a decorative blob sit directly behind body text without a near-solid backing plate under the text. The wild layer and the reading layer occupy different depths, not the same one.

## 6. Components

- **Buttons:** blob-shaped or heavy-pill, never a perfect stadium ellipse - nudge the border-radius values asymmetrically.
  - Hard offset shadow in a second palette hue for a silkscreen-misregistration look: `box-shadow: 6px 6px 0 var(--accent-2)`, shifting on hover/press rather than fading.
  - Label type stays on the unwarped body face - a button is a functional element first.
- **Cards:** image frames clipped to an organic `clip-path: path(...)` blob or torn-edge polygon, not a rounded rectangle.
  - Photography inside gets the duotone/halftone treatment from Section 4; raw neutral photography is never left unstyled inside a blob frame.
- **Nav and forms:** deliberately the most restrained thing on the page - solid poster-paper or ink-ground bar, unwarped type, ordinary visible focus states. This is where trust and usability live, and where the direction should feel almost boring.
- **Dividers, badges, quote marks:** hand-feel over geometric perfection - slightly irregular blob badges, a wavy underline instead of a straight one, an SVG squiggle instead of a straight rule.

## 7. Motion

Motion carries a large share of this direction's identity, so it earns real weight, not a token hover state.

- Background blob layers morph continuously (Section 4 keyframes, `10s`-`20s` loops) and slow hue-rotate across the ambient gradient/blend layers.
- Section transitions use the gooey merge/split technique as a "melt" wipe over the viewport on scroll trigger.
- Buttons and cards respond with squash-stretch or gooey scale on hover/press (spring easing, e.g. `cubic-bezier(0.68, -0.55, 0.27, 1.55)`), not a flat `scale()`.
- Cursor-follow distortion (Section 4's displacement-map warp, tracked to pointer position) is optional set-dressing for hero sections only - never required, and never applied to a scrolling content column.
- Animate via `transform`, `filter`, and SVG attribute animation, not layout-triggering properties; the continuous loops especially need to stay off the main thread's layout pass.

`prefers-reduced-motion` is mandatory, not optional, precisely because so much identity lives in the ambient loops: freeze every continuous blob-morph and hue-rotate animation on a single deliberately-chosen frame (mid-morph, not the "resting" shape), disable scroll-triggered melt wipes and cursor-follow distortion, and keep only short, essential feedback transitions (button press, focus) at reduced duration. A frozen frame must still look like a designed choice, not a broken animation.

## 8. Execution and anti-patterns

What turns this into generic "AI gradient blob hero" instead of genuine psychedelic/surreal design: a single smooth two-stop gradient blob with heavy blur and no hard color boundary (that is the SaaS blob, not a poster); a perfectly symmetric, centered, static blob shape; type with a drop shadow slapped on a default sans instead of an actual warped/bulging face used with intent; chaos applied evenly across the whole page so nothing reads as an intentional focal break; and any near-black-to-violet glow ground, which collapses this direction back into the exact AI-purple tell it exists to avoid.

Accessibility floor, treated as non-negotiable rather than aspirational: contrast between two saturated hues is genuinely harder to hit than neutral-on-white, so check every text/background pairing against actual WCAG AA numbers (4.5:1 body text, 3:1 large text and UI components) rather than eyeballing it. When a vivid pairing fails, do not desaturate the identity palette to fix it - insert a solid neutral plate (poster-paper cream or ink-ground black) behind that specific text block, or flip that instance to pure white/black locally. Never encode state (link vs. plain text, error vs. normal) in hue alone; pair it with weight or underline. Keep ambient morphing layers on their own low-opacity z-index band, separated from any layer carrying live text, so a background animation never drags legibility down with it.
