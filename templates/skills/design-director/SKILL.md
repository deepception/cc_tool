---
name: design-director
description: 'Control layer for frontend visual design work: landing pages, hero sections, portfolios, marketing sites, redesigns, "make it look good / premium / not generic". Reads the brief, routes to the globally installed design-taste-frontend (taste-skill v2), a user-invoked classic style (/minimalist-ui, /industrial-brutalist-ui, /high-end-visual-design), scroll-world, or an in-repo aesthetic reference (organic/tactile, punk/zine, psychedelic/surreal), and composes a section-by-section master design prompt from the archetype templates in references/ when the full taste-skill run is not used. Not for screens inside an existing app''s component system (follow that system; motion goes to product-ui-motion); for API/module interface shape use design-an-interface.'
user-invocable: true
---

# Design Director

Two jobs, in order: (1) route the brief to the right aesthetic skill, (2) turn a vague brief into a **master design prompt**, a spec precise enough that the output cannot be generic. The taste-skill family (installed globally by `cc-install-tasteskill`) carries the design method; the archetypes in `references/` carry proven page choreography. This skill is the thin layer that decides which of each to pull.

## Step 1: Design read (one line, before anything)

State in one line: *"Reading this as: \<page kind> for \<audience>, with a \<vibe> language, leaning toward \<aesthetic family>."* Signals: page kind (landing / portfolio / editorial / redesign), vibe words the user used ("Linear-clean", "Awwwards", "brutalist", "expensive"), linked references, audience, existing brand assets, quiet constraints (accessibility, regulated industry), which override aesthetics.

If the read genuinely diverges two ways, ask exactly **one** clarifying question (e.g. "closer to Linear-clean or Awwwards-experimental?"). Otherwise declare the read and proceed.

If the read isn't obvious even after that (you know the page kind but not the visual treatment), pull concrete references before defaulting to a generic aesthetic: **devl.dev** (browsable gallery of real UI component patterns with source code) for component-level treatment, **getdesign.md** (analyzed design systems from real products: colors, type, spacing, reasoning) for a brand-level reference point, especially useful when redesigning toward a credible target instead of an invented one. Treat both as inspiration and calibration. The routed taste-skill variant still owns the anti-default rules.

## Step 2: Route to a taste-skill variant or aesthetic reference

Invoke `design-taste-frontend` via the Skill tool for any page build or redesign unless a row below fits better, and follow it as the design method. It runs its own process: a script deals the art direction, it writes the copy deck, builds, then gates the page with a real-browser inspector and a screenshot critique. A full page run takes roughly 30 to 45 minutes and a few hundred thousand tokens, so put that in the Step 1 design read before starting. For a single component or section it skips the draw and matches the surrounding design, so a small fix does not need the full run.

| Design read | Route |
|-------------|-------|
| Default: landing page, portfolio, marketing site, product page | `design-taste-frontend` |
| Existing page to upgrade, restyle or review | `design-taste-frontend` (its audit-first redesign mode: records the before, keeps the stack, proves the after) |
| New dashboard or app screen with no component system yet | `design-taste-frontend` (its app-screen route, `references/app-screens.md`) |
| Scroll-scrubbed 3D "fly through the world" landing built from generated video | `scroll-world` (project skill) |
| User asks for a classic look: clean editorial "Notion/Linear-style", raw Swiss/terminal/blueprint, or soft "expensive" agency | Name the opt-in classic so the user can run it: `/minimalist-ui`, `/industrial-brutalist-ui`, `/high-end-visual-design`. They set `disable-model-invocation`, so Claude cannot invoke them; don't Read their files to get around that. Until the user runs one, carry the style words into `design-taste-frontend` or Step 3. |

Aesthetic references (read directly, not invoked as skills):

| Design read | Reference |
|-------------|-----------|
| Artisan/craft, natural materials, handmade warmth: apothecary, stationery, farm-to-table, slow living | `references/aesthetic-organic-tactile.md` |
| DIY, collage, Xeroxed-flyer energy (bands, events, streetwear, underground/activist); never for trust-first or regulated briefs | `references/aesthetic-punk-zine.md` |
| Trippy, groovy, liquid, 60s/70s poster maximalism (music/festival, generative art, entertainment); never for enterprise/trust-first briefs | `references/aesthetic-psychedelic-surreal.md` |

If `design-taste-frontend` is not in your available-skills list, tell the user to run `cc-install-tasteskill` and continue with this skill's references alone (Step 3). They work standalone, just with less method depth.

If the official `frontend-design` skill (claude-plugins-official or anthropics/skills) is also installed, it is a second design method for the same requests. Use one method per page: the one this table routes to.

Not installed by default, worth naming when they fit: `imagegen-frontend-web` / `imagegen-frontend-mobile` / `brandkit` / `image-to-code` from `Leonxlnx/imagegen-skills` (reference-image pipelines that need an image-generation tool).

## Step 3: Compose the master prompt

Skip this step when `design-taste-frontend` runs its full page or app-screen run: its direction sheet, copy deck and gate replace the archetypes, and it says not to blend its direction with another layout. Use this step when it isn't installed, for a prompt meant for another tool or agent, or alongside a classic style the user invoked.

When the task is building a full page/site (or producing a prompt for another tool or agent), don't design from adjectives. Write the spec first. Read `references/prompt-anatomy.md` for the 11-part structure, then start from the nearest archetype and fill its placeholders:

| Archetype | Reach for it when |
|-----------|-------------------|
| `references/archetype-signature-hero.md` | One full-screen hero / above-the-fold moment with a single memorable cursor-driven mechanic |
| `references/archetype-immersive-scroll.md` | Multi-chapter cinematic scroll experience (Awwwards-style): pinned sections, scroll-scrubbed video, a metamorphosis pivot |
| `references/archetype-dark-minimal-landing.md` | Dark monochrome SaaS / content / newsletter landing: token-driven, video-backed, restrained motion |

The archetypes are **structural skeletons, not looks**: they encode choreography, spec discipline, and acceptance checklists. The visual identity (palette, type, imagery, copy) must come from the design read, so two briefs filled into the same archetype must not look like siblings. Every archetype ends with a "Vary it" section; use it.

Define tokens before layout, using the scaffold in `references/design-tokens.md` (color, typography, spacing, radius/shadow, motion). It fixes names, not values; the taste-skill variant and `product-ui-motion` still own the actual decisions.

## Non-negotiables (whatever the route)

On a `design-taste-frontend` run its house rules and inspector are the ban list, and they win where this list differs (it allows fully round buttons, and bans glass and gradients outright). The list below covers routes without it, and fills gaps it leaves.

- **Exact values, not vibes**: tokens, font names, easing curves, durations, delays. "Premium feel" is not a spec; `cubic-bezier(0.16,1,0.3,1)` at `1.1s` staggered `0.12s` is.
- **Name the defaults to avoid**: "not generic" just swaps one default look for another. Unless the design read calls for them, rule out the current model's fallback styles by name: cream or off-white backgrounds, italic accent words in headlines, numbered "01/02/03" section labels, monospace labels, pill-shaped buttons. After the first pass, check which styles it reached for instead and extend the list.
- **One signature interaction** per page, a single mechanic the visitor remembers. Two competing gimmicks read as a template demo.
- **`prefers-reduced-motion` fallback** for every animation system, always.
- **Acceptance checklist** at the end of every master prompt, made of binary checks the implementer (you, later) can self-verify against.
- **Assets pinned or explicitly placeholder'd**: never invent URLs; mark `{{ASSET_*}}` slots the user fills.

## Materials & depth (only when the brief or brand asks for glass)

`design-taste-frontend` bans glass by default and its inspector fails blurred panels. When the brief or brand requires Liquid Glass / Glassmorphism, pass `--allow glass` to its inspector for that rule only, and treat materials as a layering system, not a one-off recipe:

- Never stack one translucent surface directly on another, because legibility collapses fast.
- Scale the treatment to the surface: a small chip gets a light blur and thin shadow; a large panel or sheet needs stronger blur plus a deeper shadow, or it reads as a sticker instead of a physical layer.
- Dim-to-focus vs. separate-to-keep-flow is a deliberate choice: a blocking modal gets a scrim, a non-blocking translucent panel (side panel, floating toolbar) does not, since a scrim on the latter implies it's blocking when it isn't.
- Vibrancy, not flat grey, for text over blur: bump contrast, weight, and tracking versus the same text on a solid background.
- Prefer a scroll-edge fade over a hard 1px divider under sticky headers, because the fade reads as one continuous surface and the hairline reads as two stacked layers.

## Accessibility mechanics (supplements taste-skill)

`design-taste-frontend`'s inspector measures contrast but not baseline interaction mechanics. Add these regardless of aesthetic:

- Semantic HTML structure; ARIA only where the semantic element doesn't already convey the role.
- Every interactive element reachable and operable by keyboard: a clickable `div`/`Box` needs a role, `tabIndex`, and a keyboard handler, or it should be a real `<button>`/`<a>`.
- Visible focus styling designed as part of the aesthetic, not a browser-default outline left in or stripped out.
- Form validation messaging inline, not just color.
- Touch targets at least 44px on mobile.
- At least 3 breakpoints for any non-trivial layout, narrative and hierarchy preserved across all of them.

## When NOT to use this

Dashboards, admin panels, data tables and multi-step flows inside an existing app: follow the project's component system, not a dealt style. `design-taste-frontend`'s app-screen route is for a new app screen with no system yet, or a restyle the user asked for. For how those components should *move*, and for any dropdown, modal, drawer, toast, palette or drag interaction anywhere, use `product-ui-motion`, whose duration and easing values supersede the taste skills' at product scale. Module/API shape questions → `design-an-interface`. Pure copywriting → the content is an input to this skill, not its output.
