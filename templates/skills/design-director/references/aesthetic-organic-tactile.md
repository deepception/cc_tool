# Aesthetic: Organic / Tactile

## 1. Direction Overview

A design language of paper, pigment, and the hand — for briefs where warmth and human touch are the point, executed with the same precision as any other direction here. Not sloppiness, not decoration: a considered system of irregular-but-intentional shapes, natural material color, and physical-feeling texture.

**Route here for:** artisan and craft goods, food/beverage and tea/coffee brands, apothecary and natural skincare, independent publishing, stationery and letterpress studios, botanical/garden and farm-to-table sites, bookbinding, museum/heritage and slow-living brands, wellness that wants "handmade" over "clinical."

**Do not route here for:** fintech, enterprise SaaS, dev tools, dashboards, or anything the brief describes as "futuristic," "innovative," or "cutting-edge" — those belong to `industrial-brutalist-ui`, `high-end-visual-design`, or `design-taste-frontend`. If the brief wants warmth but also wants "clean/minimal," prefer `minimalist-ui` and treat this doc as too heavy.

## 2. Typography

- **Display (headlines):** `Fraunces` — variable serif, set the optical-size and `soft`/`wonk` axes on (`font-variation-settings: "opsz" 72, "SOFT" 60, "WONK" 1`). Its ink-trap terminals and slight irregularity read as hand-cut type without sacrificing legibility. Weight 440–600. Tracking neutral to `+0.005em`, never negative — negative tracking reads cold and digital.
- **Body:** `Karla` — humanist sans with spurred, slightly irregular letterforms at close reading distance; avoids the sterility of Inter/Roboto/Helvetica while staying fully legible at small sizes. Line-height `1.65`–`1.75`, generous by design — warmth needs room to breathe. Body color: warm ink brown (`#2B2420`), never pure black.
- **Accent/marginalia only:** `Caveat` or `Gochi Hand` — genuine drawn-script fonts, used exclusively for small doses: captions, margin notes, hand-stamped labels, a single pull quote. Never for body copy, never for more than one short line at a time, never as the primary headline face. This is seasoning, not the meal.
- **Never:** Comic Sans, Kristen ITC, or any "friendly" system script — these read as clip-art, not craft.

## 3. Color Palette (Pigment Logic)

Derive the palette from natural dye and pigment sources rather than picking hues freehand — it keeps every accent feeling "sourced," not arbitrary.

- **Paper (background):** unbleached linen `#F7F3EA`, aged parchment `#F0E9D8`. Never `#FFFFFF`.
- **Ink (text/foreground):** walnut ink `#2B2420`, roasted-umber `#34281F`. Never `#000000`.
- **Primary accent — pick one per project, treat it like a single pigment:**
  - Madder red / terracotta: `#B85C3E`
  - Ochre / clay: `#C99A3D`
  - Moss: `#6B7353`
- **Secondary counterpoint (optional, one only):** indigo-dye blue `#465A63`, used sparingly against the warm field for contrast — never as a second dominant hue.
- **Saturation ceiling:** keep all accents under ~45% saturation. Anything brighter reads as brand-color-on-craft-paper, not natural pigment.
- **Rule:** one ink accent carries the whole composition. Multiple loud accents fighting for attention is the fastest way to look like a moodboard, not a system.

## 4. Texture and Grain Techniques (Signature Territory)

This is where the direction is won or lost. Every surface gets grain; every hard edge gets softened. Grain must stay **static** — an animated/shifting noise layer reads as digital scan-line effect, not paper.

- **Paper grain overlay:** an SVG `feTurbulence` noise layer as a data-URI background, blended in:
  ```css
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='3' stitchTiles='stitch'/%3E%3CfeColorMatrix type='saturate' values='0'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
  mix-blend-mode: multiply;
  opacity: 0.05; /* 0.04–0.08 range, never higher */
  ```
  Apply as a fixed full-viewport layer, not per-component — one grain source, consistent everywhere.
- **Deckle (torn-paper) edges** on cards and images, via displaced clip paths instead of straight rounded corners:
  ```svg
  <filter id="deckle">
    <feTurbulence type="fractalNoise" baseFrequency="0.015" numOctaves="2" result="noise"/>
    <feDisplacementMap in="SourceGraphic" in2="noise" scale="10" xChannelSelector="R" yChannelSelector="G"/>
  </filter>
  ```
  Apply `filter: url(#deckle)` to a card's background rect or image mask. Reserve for hero images and 1–2 feature cards, not every element.
- **Hand-drawn dividers:** replace `<hr>` with an inline SVG single-stroke wavy path (multiple cubic-béziers with small, non-repeating y-jitter, 2–4px amplitude), `stroke-width: 1.5`, ink color, `stroke-linecap: round`.
- **Irregular corners on cards/buttons:** four different radius values per element, e.g. `border-radius: 12px 19px 14px 21px` — machine-uniform radii are the tell that breaks the whole illusion.
- **Warm-tinted shadow, not gray:** `box-shadow: 0 6px 18px rgba(80, 54, 30, 0.14)` — a neutral gray shadow reads as UI chrome, a warm brown one reads as paper lifted off a desk.
- **Micro-rotation:** stamps, tags, and photo-style elements get a fixed `rotate(-1.5deg)` to `rotate(2deg)` — small, deliberate, never randomized at runtime (fixed per element, so the layout stays reproducible).

## 5. Layout and Composition

- Work from a loose baseline grid, then deliberately break it: let one image or annotation overlap a card edge by 12–24px rather than sitting flush inside it.
- Use collage logic sparingly — a "washi-tape" accent (a short rotated, semi-opaque colored rectangle with a frayed short edge via `clip-path`) pinning a corner of a photo or card, at most once or twice per screen.
- Margin notes in the accent script font sit in generous whitespace beside body copy, not locked to the text column — functions like a pull-quote but placed by hand, not by grid.
- Whitespace is "page," not "canvas": favor slightly asymmetric margins (a wider top or gutter, like a bound book) over perfectly symmetric padding on all four sides.
- Cap the collage density: no more than 2–3 "placed" elements (rotated photo, tape accent, marginalia) visible per viewport. Beyond that it stops reading as a considered page and starts reading as a mood board.

## 6. Component Specifications

- **Cards:** irregular per-corner radius (§4), paper-grain background, warm shadow, optional deckle edge on feature cards only.
- **Buttons (primary):** solid ink-accent fill, irregular corner radii, grain texture at very low opacity baked into the fill. Hover/press: `translateY(1px)` + shadow contraction — mimics pressing into paper, not a digital lift.
- **Buttons (secondary):** hand-drawn outline — a single SVG rough-rect stroke (slightly uneven line weight) instead of a CSS border.
- **Imagery:** desaturated, warm-white-balanced photography or single-weight botanical line illustration. Frame photos like postcards — cream border, 8–14px, with a slight rotation — rather than full-bleed glossy crops.
- **Tags/labels:** washi-tape rectangles (see §5), never pill-shaped badges — pills read as SaaS UI, not craft.
- **Icons:** never generic thin-line tech sets (Lucide, Feather, Heroicons). Use single-weight pen-and-ink botanical/naturalist linework with intentionally variable stroke width, or skip icons in favor of small illustrations.

## 7. Motion Character

Motion is physical, not slick. Nothing should feel like it slides on rails.

- **Easing:** overshoot-then-settle, like a dropped card finding rest: `cubic-bezier(0.34, 1.56, 0.64, 1)`, or real spring physics if the stack supports it (stiffness ~120, damping ~14).
- **Entrance:** elements arrive with a small rotation correction (from ~2deg toward final resting angle) plus a short vertical drop — never a flat fade-and-slide.
- **Hover:** cards lift and rotate slightly further, as if being picked up off a desk, rather than a flat `scale()`.
- **Duration bounds:** 300–500ms. Under 150ms feels mechanical; over 800ms feels sluggish and digital — both break the physical illusion.
- **No parallax scroll-jacking, no glossy slide-wipe page transitions.** A simple cross-fade is enough.

## 8. Execution Protocol and Anti-Patterns

1. Set grain and paper background first, on the root layer — every other decision reads against it.
2. Lock the accent pigment (one hex) before laying out components; resist adding a second loud hue mid-build.
3. Apply irregular radii and one deckle edge to the hero visual before touching secondary components.
4. Place motion last, and only the physical-settle pattern from §7.
5. Universal AI-tell bans still apply on top of everything above: no em-dash, no AI-purple, no generic placeholder names/avatars, no fake-precise stats, no div-built fake screenshots.

**Banned — these produce generic "kraft-paper SaaS," not organic/tactile:**
- A literal tiled kraft-paper JPEG as a hero background. The single most on-the-nose tell for this direction.
- Washi-tape / stamp / marginalia accents on every single element — overuse collapses the collage logic into visual noise.
- Neumorphism or glossy skeuomorphic 3D mixed in. Stay flat and matte; this is paper, not plastic.
- Uniform `border-radius: 16px` (or any single value) applied everywhere — uniformity is the machine-made tell.
- Stock photography of twine, pressed flowers, or "rustic desk flatlays" used as filler. Commission-quality or skip.
- Animated/shifting grain, or grain opacity above ~0.08 — both read as a broken screen filter, not paper.
