# Aesthetic: Punk / Zine

A controlled-chaos design method for Xeroxed flyers, cut-and-paste ransom-note typography, DIY show posters, and riot-grrrl zines. Every rough edge here is a choice, not a shortcut: the system is precise about *which* rules bend and holds everything else rigid. This is a high `DESIGN_VARIANCE` (9-10) direction — read it in full before touching layout, it does not compose safely on top of a generic template underneath.

## 1. Route Here / Route Elsewhere

- **Route here for:** band and label sites, event/festival pages, streetwear and indie merch, underground art collectives, zines and fanzines, protest/activist campaigns, DIY-scene portfolios — anything where energy and handmade authenticity outrank polish.
- **Never route here for:** trust-first, regulated, or enterprise briefs — fintech, healthcare, legal, government, B2B SaaS, admin dashboards, checkout/payment flows, anything holding personal or medical data.
- **If the signals conflict** (e.g. "punk energy but it's a bank"), the trust constraint wins. Send it to `minimalist-ui`, `industrial-brutalist-ui`, or `high-end-visual-design` instead. Collage energy reads as untrustworthy the moment money or personal data is on the line.

## 2. Typography — Ransom-Note Display, Boring-on-Purpose Body

Two zones that never trade jobs: display fragments carry the chaos, reading copy carries none.

- **Riot headlines / eyebrows / pull-quotes:** cycle at most 3 display faces per composition — one heavy block face (`Anton`, `Archivo Black`, `Bebas Neue`), one scrawled marker face (`Permanent Marker`, `Caveat`) for annotations and corrections, one stencil or typewriter face (`Special Elite`, `Rubik Mono One`) for stamps and dates. Never a fourth face active at once.
- **Ransom-note technique:** wrap each *word* (never each letter — that breaks reading) in its own `<span>`, cycling `font-family` across the 2-3 display faces and varying `font-size` by roughly ±20%. Rotation and baseline offset must be deterministic, seeded from word index — never `Math.random()` on every render, which visibly jitters on re-render and breaks the "one hand cut all of this" cohesion:

  ```css
  .riot-word {
    display: inline-block;
    transform: rotate(calc((var(--i, 0) % 5 - 2) * 3deg))
               translateY(calc((var(--i, 0) % 3) * -4px));
  }
  ```

  Cap rotation at ±8deg regardless of the formula used.
- **Highlighter marker pass:** a semi-transparent skewed rect behind 1-2 key words per headline only — `background: #F5FF3D; mix-blend-mode: multiply; transform: skew(-3deg) rotate(-1deg);`. Never behind every word; that reads as a filter, not a choice.
- **Reading copy** (body, nav, forms, captions): one humanist sans, dead level, zero rotation — `Work Sans`, `IBM Plex Sans`, or `Public Sans`, regular weight, `line-height: 1.55-1.65`, `max-width: 62-68ch`. This block is the legibility anchor the rest of the page is free to go loud around.
- **Metadata/timestamps:** the typewriter face only, uppercase, small, static — never rotated, never mixed with the marker face.
- **Hard rule:** the ransom-note treatment never touches a run longer than ~8 words. Past that length it is a broken layout wearing a costume, not a design.

## 3. Color — Photocopy Contrast + Rationed Riso Ink

- **Base:** true black ink, `#0A0A0A`-`#000000`, on uncoated paper — `#F5F2E8` or `#EFEBDD` (cream/newsprint, never a cool pure white). Unlike the other directions in this family, near-pure black is correct here — it reads as toner, not as an accessibility miss.
- **Spot-ink budget: exactly two** Risograph-inspired colors per project, drawn from `#FF48B0` (fluoro pink), `#FF3D3D` (riso red), `#0078BF` (riso blue), `#FFE800` (riso yellow), `#FF6D3F` (riso orange), `#00A95C` (riso green). Never a third spot color — each ink is a separate print pass in real riso work, and that cost constraint is what gives it discipline instead of noise.
- **Overprint:** where two spot-ink layers overlap, let a third tone emerge from `mix-blend-mode: multiply` on translucent fills (pink over yellow reads warm coral) rather than hand-picking a third hex. Reserve this for deliberate overlap points — torn photo edges, sticker corners — not as a general palette move.
- **Text-on-paper:** black or exactly one spot ink at a time. Never set both spot inks as competing body text in the same view.
- **Photocopy variant** (flatter, if the brief wants less color): crush toward near-monochrome — `#141210` ink on `#EFEBDD` paper — with a single fluorescent accent reserved for marker-underlines and circle-callouts only.

## 4. Texture — the Signature Territory

- **Grain/photocopy noise:** one global SVG `feTurbulence` + `feColorMatrix` static filter on a `fixed inset-0 pointer-events-none` layer, opacity `0.05-0.08`, `mix-blend-mode: multiply`. Never on a scrolling container — same DOM-cost discipline the rest of this skill family follows.
- **Halftone/duotone photography:** every photograph is desaturated and contrast-punched (`filter: grayscale(1) contrast(1.4)`), tinted with one spot ink via blend mode, then overlaid with a halftone dot grid (`radial-gradient` dot pattern or an SVG `<pattern>` of circles) at `multiply`. A clean full-color photo anywhere on the page breaks the whole system — this treatment is mandatory, not optional flavor.
- **Torn/cut edges:** a small reused set of jagged `clip-path: polygon(...)` presets (`--torn-edge-1/2/3`) applied to **one edge only** per card or image — torn edges on all four sides reads as sloppy, not deliberate. An SVG `feTurbulence` + `feDisplacementMap` filter gives a softer, more organic tear as an alternative.
- **Tape and stickers:** absolutely-positioned strips simulating translucent masking tape (`linear-gradient`, rotated -4deg to 6deg, soft `box-shadow` for lift) at image/card corners; die-cut stickers as a clipped circle or blob with a thick offset "sticker border" and drop shadow, layered to overlap the element beneath by 10-20% of its bounds.
- **Rotation vocabulary:** pull every layer's rotation from one small fixed set — `-6deg, -4deg, -2deg, 3deg, 5deg, 6deg` — reused across the page instead of freely randomized per element. Repeating the same handful of angles is what reads as "cut by one person," not as noise.
- **Sell the physicality cheaply:** a tiny SVG staple (X-mark) or pushpin (circle + soft shadow) at the overlap corner of two layered elements finishes the illusion.
- **Placeholder imagery:** when no real asset exists, source from `https://picsum.photos/seed/{descriptive-string}/{w}/{h}` and run it through the duotone/halftone pipeline above before it ever appears — a placeholder photo in clean full color is as much a tell here as it is in every other direction.

## 5. Layout & Composition

One rule keeps this usable: **one strict axis, one loose one.** The content grid — primary reading order, a shared left baseline for body-copy blocks, section-to-section vertical flow — stays strictly aligned. The decoration grid — stickers, tape, torn photos, headline fragments, stamps — breaks free and overlaps around it. Never let the two swap roles.

- Never rotate a text block past ±6deg once it holds more than ~8 words.
- Overlap is deliberate, not accidental: a fixed negative margin (`-1rem` to `-2rem`) or an absolute position that lets one element's edge sit under another's by a chosen amount — not an emergent side effect of a stack running out of room.
- Density is high on purpose — this is not a whitespace-forward direction — but every interactive element keeps a clean padding buffer decoration never crosses. Collage may surround a button; it may not obscure or crowd its hit area.

## 6. Components

- **Buttons:** solid ink-block rectangle, hard corners (`border-radius: 0-2px`, never a pill), offset hard shadow with no blur — `box-shadow: 4px 4px 0 #000`. `:hover` tightens the shadow to `2px 2px 0` with a matching `2px, 2px` translate toward it; `:active` collapses the shadow to `0 0 0` and moves the button fully into place — a literal stamped-down press. Label text stays horizontal even when the button's container is tilted, so it reads as unambiguously clickable.
- **Cards:** duotone/halftone image, one torn edge, a rotated stamp badge (date/category) overlapping one corner.
- **Links:** underline as a hand-drawn marker stroke (SVG path with slight jitter, uneven via `stroke-dasharray`) rather than a clean 1px rule; hover fills it solid.
- **Forms/inputs stay plain:** no rotation, no torn edges, no ransom-note labels — a heavy solid border in the ink color and a typewriter-face label instead. A collaged input field reads as broken, not punk; this is the one zone that must look conventional.
- **Navigation:** treat like a flyer's fine print — tight, uppercase, monospace, calm. This deliberate restraint is what makes the chaos everywhere else read as intentional instead of exhausting.

## 7. Motion — Snap, Not Glide

Abrupt and mechanical, never the luxury `cubic-bezier(0.16,1,0.3,1)` ease the other directions in this family default to.

- Hover/press transforms land in 80-120ms with a hard `ease-out`, or `steps(2, end)` for a mechanical stutter that reads like a photocopier flash.
- Entrances "slam" into place — translate plus slight rotation settling under 200-250ms with overshoot (`cubic-bezier(.2,1.5,.4,1)`) — evoking a sticker slapped down, not a fade-up.
- One optional strobe (a brief `filter: contrast()` flash across the hero) is allowed once per page load, never looping, never re-triggered on scroll.
- Scroll reveals, if used at all, are binary — `opacity 0→1` under 150ms — no scroll-scrubbed smoothness anywhere in this direction.
- `prefers-reduced-motion`: collapse everything to instant state changes, same as every other direction in this family.

## 8. Anti-Patterns & the Accessibility Floor

**Reads as a lazy "grunge Photoshop filter," not genuine punk/zine, when:** a noise texture is slapped over an otherwise generic template (rounded cards, centered hero, default fonts); rotation/jitter is re-randomized on every render so elements visibly jump; the ransom-note treatment is applied to actual paragraphs; torn edges and tape are stacked on every element with no restraint, flattening hierarchy instead of marking 2-3 focal points; or washed-out low-contrast pairs get excused as "raw" — real zine culture is high-contrast *because* cheap photocopiers ate detail, not because legibility never mattered.

**Non-negotiable regardless of volume:** WCAG AA contrast (4.5:1 body, 3:1 large text/UI) holds for every ink-on-paper pairing actually used, not just the primary one. Interactive elements keep a real 44px tap target even when their visual crop looks smaller or rotated — hit area is never just the tilted bounding box. Focus states stay visible and in-system (a thick offset-shadow ring in the accent ink reads as punk; a suppressed outline does not). Rotated or skewed text never applies to body copy, labels, or anything a screen reader needs read straight. This direction still inherits cc_tool's baseline AI-tell bans — no Inter-as-default, no AI-purple gradients, no em-dash, no generic stock names or avatars, no fake-precise stats, no hand-rolled fake screenshots: punk/zine breaks visual convention on purpose, not the craft floor every other direction in this family holds.
