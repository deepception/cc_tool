# Archetype: Dark Minimal Landing

A restrained, token-driven landing page for a SaaS / content / newsletter product: near-monochrome palette, one glass effect, one reusable animation helper, video-backed sections. The opposite pole from the immersive-scroll archetype — motion is quiet, the discipline is in the tokens. Fill every `{{PLACEHOLDER}}`.

---

Build a {{MODE — default: dark monochrome}} landing page called {{BRAND}} — {{ONE_LINE_POSITIONING}}. Stack: {{STACK — default: React + Vite + TypeScript + Tailwind CSS + shadcn/ui + Framer Motion}}. Fonts: {{SANS — e.g. Inter}} and {{SERIF — e.g. Instrument Serif}}, the serif used *only* for italic accent words inside headlines. The theme is {{BASE — pure black #000}} background with {{FG — white}} foreground — no colors beyond monochrome except at most one desaturated accent.

## 1. Design system (index.css)

CSS variables as raw HSL triples (no `hsl()` wrapper), consumed via `hsl(var(--token))`:

```
--background: {{0 0% 0%}}      --foreground: {{0 0% 100%}}
--card: {{0 0% 5%}}            --muted: {{0 0% 15%}}  --muted-foreground: {{0 0% 65%}}
--secondary: {{0 0% 12%}}      --border: {{0 0% 20%}}  --input: {{0 0% 18%}}  --ring: {{0 0% 40%}}
--accent: {{170 15% 45% — ONE desaturated hue, used almost never}}
--hero-subtitle: {{210 17% 95% — a barely-tinted near-white for hero subtitles}}
```

The discipline: hierarchy comes from *lightness steps* (0% / 5% / 12% / 15% / 20% / 65% / 100%), not hue. If a design decision needs a second hue, this is the wrong archetype.

## 2. The one material: liquid glass

A single global `.liquid-glass` class used for every elevated surface (nav icon buttons, email capture, secondary CTAs):

```css
.liquid-glass {
  background: rgba(255,255,255,0.01);
  background-blend-mode: luminosity;
  backdrop-filter: blur(4px); -webkit-backdrop-filter: blur(4px);
  border: none;
  box-shadow: inset 0 1px 1px rgba(255,255,255,0.1);
  position: relative; overflow: hidden;
}
.liquid-glass::before {         /* 1.4px gradient rim, bright at top & bottom */
  content: ''; position: absolute; inset: 0; border-radius: inherit; padding: 1.4px;
  background: linear-gradient(180deg,
    rgba(255,255,255,0.45) 0%, rgba(255,255,255,0.15) 20%,
    rgba(255,255,255,0) 40%, rgba(255,255,255,0) 60%,
    rgba(255,255,255,0.15) 80%, rgba(255,255,255,0.45) 100%);
  -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  -webkit-mask-composite: xor; mask-composite: exclude;
  pointer-events: none;
}
```

## 3. The one animation helper

Every section entrance uses a single reusable helper with staggered delays — no bespoke animations:

```ts
const fadeUp = (delay: number) => ({
  initial: { opacity: 0, y: 20 },
  whileInView: { opacity: 1, y: 0 },
  viewport: { once: true, margin: "-100px" },
  transition: { duration: 0.6, delay, ease: "easeOut" },
});
```

Buttons get `whileHover={{ scale: 1.03 }}` / `whileTap={{ scale: 0.98 }}`. Nothing loops infinitely. Wrap all motion in a `prefers-reduced-motion` check (Framer Motion's `useReducedMotion`) that zeroes the transforms.

## 4. Page structure (top to bottom)

1. **Navbar** — fixed, fully transparent, `z-50`. Left: {{LOGO — e.g. a concentric-circles mark drawn with borders, no image}} + bold wordmark. Center: 4 links separated by `•` dots, `text-muted-foreground hover:text-foreground`. Right: 3 social icons in `.liquid-glass` circular buttons.
2. **Hero** — full viewport; background: autoplaying looping muted video `{{ASSET_HERO_VIDEO}}` with a bottom `h-64` gradient fading to background. Content centered: a social-proof row (3 overlapping avatar circles + "{{PROOF_LINE — e.g. 7,000+ people already subscribed}}"); headline `text-5xl md:text-7xl lg:text-8xl font-medium tracking-[-2px]` — "{{HEADLINE}}" with "{{ACCENT_WORD}}" in serif italic; subtitle in `hsl(var(--hero-subtitle))`; a `.liquid-glass rounded-full` email-capture with a solid `bg-foreground text-background` SUBSCRIBE pill.
3. **Problem section** — huge top padding (`pt-52 md:pt-64`); headline with serif-italic accent word ("{{PROBLEM_HEADLINE — e.g. Search has changed. Have you?}}"); 3 icon cards in a `md:grid-cols-3` grid ({{CARD_TOPICS}}); a one-line kicker-sized closing tagline.
4. **Mission** — a large centered looping video `{{ASSET_MISSION_VIDEO}}`; then a scroll-driven **word-by-word opacity reveal** (`useScroll` + `useTransform`, each word 0.15→1 with scroll progress) over two paragraphs; 2–3 keywords stay full-foreground while the rest resolve to `--hero-subtitle`. This is the signature interaction — nothing else on the page may compete with it.
5. **Solution** — `border-t border-border/30`; uppercase tracked label ("SOLUTION"); headline with serif-italic accent; a wide `aspect-[3/1] rounded-2xl` product video `{{ASSET_SOLUTION_VIDEO}}`; a `md:grid-cols-4` feature grid ({{4_FEATURES}} — title + 2-line description each).
6. **CTA** — full-bleed background video ({{HLS via hls.js with `Hls.isSupported()` check and native-HLS Safari fallback, else mp4 loop}}) under a `bg-background/45` overlay; centered: logo mark, "{{CTA_HEADLINE}}" with serif italic, subtitle, two buttons — solid (`bg-foreground text-background`) and `.liquid-glass`.
7. **Footer** — single row: © line left; Privacy / Terms / Contact right; `text-muted-foreground text-sm`.

## 5. Dependencies & assets

`framer-motion`; `hls.js` (only if CTA uses HLS); `@fontsource/{{SANS}}` + `@fontsource/{{SERIF}}` (400 + 400-italic); `lucide-react`; `tailwindcss-animate`. Assets to supply: {{N}} avatar images, {{N}} card icons, video URLs above — never substitute stock.

## 6. Acceptance checklist

1. Zero hues beyond the monochrome ramp + the single `--accent` (grep the CSS for rogue hex values).
2. The serif appears only as italic accent words — never a full sentence.
3. All entrances go through `fadeUp` with visible stagger; nothing animates in a loop.
4. The word-by-word scroll reveal is the only scroll-bound mechanic.
5. Videos: muted, autoplaying, looping, with poster fallbacks; HLS degrades to native on Safari.
6. Reduced-motion: content readable with all motion zeroed.
7. Lighthouse: no layout shift from font/video load; videos lazy where offscreen.

---

## Vary it (so two fills don't look like siblings)

Vary: dark-on-black vs paper-white inversion (`MODE`); which section carries the signature scroll mechanic (word reveal ↔ number counters ↔ sticky image swap); serif vs mono as the accent voice; glass vs hairline-border as the one material; video-backed vs typographic hero. Keep: the lightness-step discipline, the single-helper animation system, the one-material rule, the checklist.
