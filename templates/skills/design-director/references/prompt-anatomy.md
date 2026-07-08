# Anatomy of a Master Design Prompt

A master design prompt is a build spec so precise that a competent implementer (human or model) cannot produce a generic page from it. The structure below is distilled from prompts that reliably produce distinctive output (motionsites.ai-style briefs). Every part is present in a complete prompt; the order is the reading order.

## The 11 parts

1. **Concept line** — one sentence: invented brand name, page kind, art direction, emotional register. *"Build a single-page cinematic scroll experience called **Koisei** — a journey down a Japanese river from sakura morning to lantern night, ukiyo-e art direction."* The name and metaphor drive every later choice; without them the spec has no spine.

2. **Tech stack** — explicit and closed. Framework, build tool, styling, animation library, media libraries, icon set. "No UI frameworks, custom everything" is a stack decision too. The stack constrains what the motion language may ask for.

3. **Assets** — a table of exact URLs (or `{{ASSET_KEY}}` placeholders the user fills), each with a key name used throughout the spec (`HERO_PLATE`, `SCROLL_FILM_1`). State what gets preloaded. Never let the implementer pick stock photos.

4. **Design system** — exact values only:
   - *Color tokens:* CSS custom properties with hex/HSL values and a one-phrase role each (`--ink: #251C16; /* primary dark — text on light, night bg */`). Include usage rules ("accent appears only in tiny doses: logo mark, hover underlines, one CTA").
   - *Typography:* named fonts with weights, a clamp-based scale (`--display-xl: clamp(4rem, 11vw, 11.5rem)`), line-height and letter-spacing per tier, and the signature trick (e.g. "italicize the emotional word in each headline").
   - *Texture:* grain overlays, borders, vignettes, glass effects — with opacities and blend modes, not adjectives.

5. **Signature interaction** — ONE mechanic the visitor remembers, specified to implementation depth: the data flow (mouse → lerp smoothing at `0.1` → canvas mask), the parameters (radius, gradient stops), and what it must feel like ("buttery, never steppy"). Everything else on the page supports this; a second gimmick dilutes it.

6. **Section-by-section spec** — for each section top-to-bottom: layout (position, size, z-order), real copy (write the actual headlines — placeholder copy produces placeholder design), exact classes/values where they matter, and per-section behavior. Number the sections.

7. **Motion language** — a global vocabulary, then per-element application:
   - Easing vocabulary (reveals `power4.out` / `cubic-bezier(0.16,1,0.3,1)`, scrubs linear, micro-interactions `power2.out`).
   - Entrance choreography with explicit delays (`0.25s`, `0.42s`, `0.7s` — staggers are what make load feel intentional).
   - Scroll behavior: what pins, for how much scroll (`+=400%`), what scrubs.
   - **`prefers-reduced-motion`: reduce** fallback: kill pins/scrubs, videos become posters, reveals become fades. Mandatory.

8. **Chrome** — nav and footer speced like sections: heights, states on scroll (background fade-in at a threshold, light/dark swap over dark sections), logo as inline SVG path when feasible.

9. **Responsiveness** — the breakpoint story: type scale steps, what hides/collapses below which width, mobile replacements for expensive mechanics (horizontal pin → native swipe), `100dvh` over `100vh`.

10. **Performance guardrails** — DPR caps, shared renderers, preload staging (`metadata` until near viewport), lazy decode, DOM budgets, target ("60fps on an M1").

11. **Acceptance checklist** — 5–10 binary checks covering the signature interaction, the motion feel, the responsive story, and the guardrails. This is what makes the prompt self-verifying: the implementer greps their own work against it before calling it done.

## Writing rules

- **Exact values, not vibes.** Every adjective in the brief must be compiled into a number, a token, or a named technique before it reaches the spec.
- **Name things.** Assets get keys, sections get numbers, the brand gets a name. Named things can be referenced; referenced things stay consistent.
- **Write the copy.** Headlines, kickers, captions — real words in the spec. Copy is design material; "lorem ipsum" briefs produce lorem-ipsum-grade layouts.
- **One signature interaction** (see part 5). If the brief wants two, split into two pages or demote one to a micro-interaction.
- **Spec the boring parts.** Nav, footer, responsiveness, reduced-motion — the parts models skip are the parts that read as slop.
- **Scale the spec to the ask.** A hero section needs parts 1–5, 7, 9, 11 (~70 lines). A full experience needs all 11 (~250 lines). Don't pad a small ask into a big spec.
- **Hard requirements language.** "Everything below is a hard requirement — follow it exactly" outperforms "should ideally". Reserve softness for the explicitly-variable parts.
