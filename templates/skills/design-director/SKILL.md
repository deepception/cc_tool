---
name: design-director
description: Control layer for frontend visual design work — landing pages, hero sections, portfolios, marketing sites, redesigns, "make it look good / premium / not generic". Reads the brief, routes to the right globally-installed taste-skill variant (design-taste-frontend, minimalist-ui, industrial-brutalist-ui, high-end-visual-design, redesign-existing-projects) or an in-repo aesthetic reference (organic/tactile, punk/zine, psychedelic/surreal), and composes a section-by-section master design prompt from the archetype templates in references/. For API/module interface shape use design-an-interface instead; for dashboards and data-heavy product UI this skill mostly does not apply.
user-invocable: true
---

# Design Director

Two jobs, in order: (1) route the brief to the right aesthetic skill, (2) turn a vague brief into a **master design prompt** — a spec precise enough that the output cannot be generic. The taste-skill family (installed globally by `cc-install-tasteskill`) carries the design method; the archetypes in `references/` carry proven page choreography. This skill is the thin layer that decides which of each to pull.

## Step 1 — Design read (one line, before anything)

State in one line: *"Reading this as: \<page kind> for \<audience>, with a \<vibe> language, leaning toward \<aesthetic family>."* Signals: page kind (landing / portfolio / editorial / redesign), vibe words the user used ("Linear-clean", "Awwwards", "brutalist", "expensive"), linked references, audience, existing brand assets, quiet constraints (accessibility, regulated industry — these override aesthetics).

If the read genuinely diverges two ways, ask exactly **one** clarifying question (e.g. "closer to Linear-clean or Awwwards-experimental?"). Otherwise declare the read and proceed.

If the read isn't obvious even after that — you know the page kind but not the visual treatment — pull concrete references before defaulting to a generic aesthetic: **devl.dev** (browsable gallery of real UI component patterns with source code) for component-level treatment, **getdesign.md** (analyzed design systems from real products — colors, type, spacing, reasoning) for a brand-level reference point, especially useful when redesigning toward a credible target instead of an invented one. Treat both as inspiration and calibration — the routed taste-skill variant still owns the anti-default rules.

## Step 2 — Route to a taste-skill variant or aesthetic reference

Invoke the matched skill via the Skill tool and follow it as the design method. For the aesthetic reference rows, there's no separate skill to invoke — read the file directly and follow it the same way. One direction at a time; `full-output-enforcement` may stack on top of any of them.

| Design read | Skill |
|-------------|-------|
| Default: landing page, portfolio, marketing site — no strong style signal | `design-taste-frontend` (set its three dials: VARIANCE / MOTION / DENSITY) |
| Clean, editorial, calm, "Notion/Linear-style", content-first | `minimalist-ui` |
| Raw, Swiss, terminal, blueprint, extreme type contrast; also data-heavy sites that need character | `industrial-brutalist-ui` |
| "Expensive", soft, premium consumer/agency, whitespace + depth | `high-end-visual-design` |
| Existing project to upgrade — audit first, don't break function | `redesign-existing-projects` |
| Long multi-section build where truncation/placeholder risk is real | stack `full-output-enforcement` |

Aesthetic references (read directly, not invoked as skills):

| Design read | Reference |
|-------------|-----------|
| Artisan/craft, natural materials, handmade warmth — apothecary, stationery, farm-to-table, slow living | `references/aesthetic-organic-tactile.md` |
| DIY, collage, Xeroxed-flyer energy — bands, events, streetwear, underground/activist; never for trust-first or regulated briefs | `references/aesthetic-punk-zine.md` |
| Trippy, groovy, liquid, 60s/70s poster maximalism — music/festival, generative art, entertainment; never for enterprise/trust-first briefs | `references/aesthetic-psychedelic-surreal.md` |

If a matched skill is not in your available-skills list, tell the user to run `cc-install-tasteskill` (or `npx -y skills add Leonxlnx/taste-skill -g -y`) and continue with this skill's references alone — they work standalone, just with less method depth.

Not installed by default but in the same repo (`Leonxlnx/taste-skill`), worth naming when they fit: `imagegen-frontend-web` / `imagegen-frontend-mobile` / `brandkit` (reference-image generation — needs an image-generation tool), `image-to-code` (image-first pipeline).

## Step 3 — Compose the master prompt

When the task is building a full page/site (or producing a prompt for another tool or agent), don't design from adjectives — write the spec first. Read `references/prompt-anatomy.md` for the 11-part structure, then start from the nearest archetype and fill its placeholders:

| Archetype | Reach for it when |
|-----------|-------------------|
| `references/archetype-signature-hero.md` | One full-screen hero / above-the-fold moment with a single memorable cursor-driven mechanic |
| `references/archetype-immersive-scroll.md` | Multi-chapter cinematic scroll experience (Awwwards-style): pinned sections, scroll-scrubbed video, a metamorphosis pivot |
| `references/archetype-dark-minimal-landing.md` | Dark monochrome SaaS / content / newsletter landing: token-driven, video-backed, restrained motion |

The archetypes are **structural skeletons, not looks**: they encode choreography, spec discipline, and acceptance checklists. The visual identity (palette, type, imagery, copy) must come from the design read — two briefs filled into the same archetype must not look like siblings. Every archetype ends with a "Vary it" section; use it.

Define tokens before layout — see `references/design-tokens.md` for the scaffold (color, typography, spacing, radius/shadow, motion). It fixes names, not values; the taste-skill variant and `product-ui-motion` still own the actual decisions.

## Non-negotiables (whatever the route)

- **Exact values, not vibes** — tokens, font names, easing curves, durations, delays. "Premium feel" is not a spec; `cubic-bezier(0.16,1,0.3,1)` at `1.1s` staggered `0.12s` is.
- **One signature interaction** per page — a single mechanic the visitor remembers. Two competing gimmicks read as a template demo.
- **`prefers-reduced-motion` fallback** for every animation system, always.
- **Acceptance checklist** at the end of every master prompt — binary checks the implementer (you, later) can self-verify against.
- **Assets pinned or explicitly placeholder'd** — never invent URLs; mark `{{ASSET_*}}` slots the user fills.

## Materials & depth (supersedes taste-skill on this point)

When a route uses Liquid Glass / Glassmorphism, treat materials as a layering system, not a one-off recipe:

- Never stack one translucent surface directly on another — legibility collapses fast.
- Scale the treatment to the surface: a small chip gets a light blur and thin shadow; a large panel or sheet needs stronger blur plus a deeper shadow, or it reads as a sticker instead of a physical layer.
- Dim-to-focus vs. separate-to-keep-flow is a deliberate choice: a blocking modal gets a scrim, a non-blocking translucent panel (side panel, floating toolbar) does not — a scrim on the latter implies it's blocking when it isn't.
- Vibrancy, not flat grey, for text over blur — bump contrast, weight, and tracking versus the same text on a solid background.
- Prefer a scroll-edge fade over a hard 1px divider under sticky headers — the fade reads as one continuous surface, the hairline reads as two stacked layers.

## Accessibility mechanics (supplements taste-skill)

Taste-skill enforces WCAG AA contrast thoroughly (button/form contrast checks, dark-mode parity) but not baseline interaction mechanics. Add these regardless of aesthetic:

- Semantic HTML structure; ARIA only where the semantic element doesn't already convey the role.
- Every interactive element reachable and operable by keyboard — a clickable `div`/`Box` needs a role, `tabIndex`, and a keyboard handler, or it should be a real `<button>`/`<a>`.
- Visible focus styling designed as part of the aesthetic, not a browser-default outline left in or stripped out.
- Form validation messaging inline, not just color (see taste-skill §4.6).
- Touch targets at least 44px on mobile.
- At least 3 breakpoints for any non-trivial layout, narrative and hierarchy preserved across all of them.

## When NOT to use this

Dashboards, admin panels, data tables, multi-step product flows — taste-skill itself scopes those out; follow the project's existing component system instead (brutalist data-editorial being the one exception above). For how those components should *move* — and for any dropdown, modal, drawer, toast, palette or drag interaction anywhere — use `product-ui-motion`, whose duration and easing values supersede the taste skills' at product scale. Module/API shape questions → `design-an-interface`. Pure copywriting → the content is an input to this skill, not its output.
