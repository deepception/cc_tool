# Archetype: Signature-Interaction Hero

A full-screen hero whose entire identity is ONE cursor-driven mechanic over layered media, plus staggered entrance choreography. Scope: a single section + nav — not a full site. Fill every `{{PLACEHOLDER}}`; delete option blocks you don't pick.

---

Build a full-screen hero section for {{BRAND}}, a {{ONE_LINE_POSITIONING}}. Stack: {{STACK — default: React 18 + TypeScript + Vite + Tailwind CSS, lucide-react for icons}}. The signature feature is {{SIGNATURE_MECHANIC — pick ONE from §3}}. Match every detail below exactly.

## 1. Fonts

- Body/UI font: {{SANS — e.g. Inter}}, weights 300–700.
- Display accent: {{DISPLAY — e.g. an italic serif like Playfair Display}} — used ONLY for the wordmark and one headline line, never body text.

Load via one `@import` at the top of the global CSS; set the sans as the universal default and expose the display font as a utility class.

## 2. Layout & structure

Section: `relative w-full overflow-hidden bg-{{BASE_BG}}`, height `100dvh` (inline style — Tailwind's `h-screen` alone clips under mobile browser chrome). Layers by z-index:

1. **Base media** (`z-10`): full-bleed `{{ASSET_BASE}}` as `bg-center bg-cover`.
2. **Signature layer** (`z-30`): the mechanic from §3.
3. **Heading** (`z-50`): positioned block (e.g. `top-[14%]`, centered), `pointer-events-none`. Two-line `<h1>`, `leading-[0.95]`:
   - Line 1 in the display font, italic, tight tracking (`-0.05em`): "{{HEADLINE_LINE_1}}"
   - Line 2 in the sans, tighter tracking (`-0.08em`), slight negative top margin: "{{HEADLINE_LINE_2}}"
4. **Supporting copy** (`z-50`): one or two small blocks anchored to bottom corners (`max-w-[260px]`, `text-sm`, 80% opacity), one containing the CTA button: accent bg `{{ACCENT_HEX}}`, `rounded-full`, `hover:scale-[1.03] active:scale-95`, soft accent-tinted hover shadow. Copy: "{{SUPPORTING_COPY}}", CTA label "{{CTA_LABEL}}".

## 3. Signature mechanic (pick exactly ONE)

**Option A — Cursor spotlight reveal** (reveals a second image through a soft circular mask):
- Track the mouse with lerp smoothing: raw `clientX/Y` in a ref; a rAF loop eases `smooth += (raw - smooth) * 0.1`; state updates from the loop. Init off-screen (`{x:-999,y:-999}`); clean up listener + rAF on unmount.
- Reveal layer: a div with `{{ASSET_REVEAL}}` as background, masked by a radial gradient drawn on a hidden canvas at the smoothed cursor position — radius `{{SPOTLIGHT_R — default 260}}`, stops `0→1, 0.4→1, 0.6→0.75, 0.75→0.4, 0.88→0.12, 1→0` — applied via `maskImage: canvas.toDataURL()`, `maskSize: '100% 100%'`.
- The two images must be pixel-aligned takes of the same scene (day/night, x-ray, alternate palette) so the spotlight reads as revelation, not collage.

**Option B — Cursor-following distortion**: a WebGL/canvas displacement ripple trailing the cursor over the base image (decaying radial wave from pointer, `uMouse` + `uWaveTime` uniforms). Use when there's no second image.

**Option C — Magnetic type**: headline glyphs repel/attract within a 180px radius of the cursor with spring-back (`elastic.out`-style). Use for type-led brands with no hero imagery.

## 4. Navigation (fixed over hero)

`fixed top-0 z-[100]`, transparent: left — inline-SVG logo mark + wordmark in the display font italic; center (`hidden md:flex`) — a glass pill (`bg-white/20 backdrop-blur-md border border-white/30 rounded-full`) with 4–5 text links, active link full-white, rest 80% white with hover states; right — solid white pill CTA ("{{NAV_CTA}}"). Mobile: hamburger only.

## 5. Entrance choreography

Three keyframe systems in global CSS, applied with `animation-fill-mode: forwards`, easing `cubic-bezier(0.16,1,0.3,1)`:
- `heroReveal` — opacity 0→1, `translateY(28px)→0`, `blur(12px)→0`, 1.1s — headline lines, staggered: line 1 at `0.25s`, line 2 at `0.42s`.
- `heroFadeUp` — opacity 0→1, `translateY(20px)→0`, 1s — corner copy at `0.7s` and `0.85s`.
- `heroZoom` — `scale(1.12)→1`, 1.8s — base media (slow Ken Burns settle).
- `@media (prefers-reduced-motion: reduce)` — all of the above become `animation: none; opacity: 1`.

## 6. Responsiveness

Headline steps `text-5xl → sm:text-7xl → md:text-8xl`. Center pill + desktop CTA hidden below `md`. Corner copy: one block hidden below `sm`, the other goes full-width (`left-5 right-5`). The signature mechanic degrades on touch devices: {{TOUCH_FALLBACK — default: spotlight follows a slow autonomous drift path instead of the cursor}}.

## 7. Acceptance checklist

1. Signature mechanic trails the cursor smoothly (lerped, no stepping) and is invisible before first mouse move.
2. Entrance reads as one choreographed sequence — staggers land in the specified order.
3. `100dvh` — no clipping under mobile browser chrome.
4. Reduced-motion: page is fully readable with zero animation.
5. Headline tracking, opacity tiers, and CTA hover states match the spec exactly.
6. No layout shift when fonts load (use `font-display: swap` + matching fallback metrics).

---

## Vary it (so two fills don't look like siblings)

Vary: which corner the supporting copy anchors to; light-on-dark vs dark-on-light; spotlight radius and gradient softness; headline split point and which line gets the display font; nav pill vs bare links; whether the CTA is accent-filled or outline. Keep: the layer order, the lerp smoothing, the stagger discipline, the reduced-motion fallback.
