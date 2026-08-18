---
name: pick-ui-library
description: >-
  Task-to-library lookup for common UI/product needs — toasts, command palettes, drag
  and drop, virtualization, global state, class-name/variant composition, theming,
  forms, charts, date pickers, OTP inputs, animated numbers, syntax highlighting.
  Use before hand-rolling any of these, or when picking which npm package solves a
  UI task. For icons and overall design systems (Fluent/Material/Carbon/Radix/shadcn)
  see design-taste-frontend instead; for motion/interaction craft see product-ui-motion.
user-invocable: true
---

# Pick UI Library

Most "should I build this myself" questions for common UI mechanics already have a well-maintained answer. Check this table before hand-rolling; recommend one library, not a menu of options.

## Lookup table

| Task | Library | Why not hand-roll |
|---|---|---|
| Toasts / notifications | `sonner` | Stacking, swipe-to-dismiss, promise-based API |
| Command palette (⌘K) | `cmdk` | Fuzzy search + keyboard nav for free |
| Drag and drop | `@dnd-kit/core` | Accessible, modern, tree-shakeable |
| Virtualized lists/tables | `@tanstack/react-virtual` | Variable row height, overscan tuning |
| Global client state | `zustand` | Minimal boilerplate vs. Redux or deep prop-drilling |
| Class-name composition + variants | `clsx` + `tailwind-merge`, `class-variance-authority` | Prevents conflicting-Tailwind-class bugs, gives a variant API |
| Theme switching (light/dark/system) | `next-themes` (Next.js) | Handles SSR flash-of-wrong-theme and system-preference sync |
| Forms & validation | `react-hook-form` + `zod` | Avoids per-field `useState` soup; schema validation catches bugs early |
| Charts | `recharts` | Responsive resize, tooltip accessibility, composable primitives |
| Date picker | `react-day-picker` | Complex keyboard/a11y surface, locale handling |
| OTP / PIN code input | `input-otp` | Paste, auto-advance, backspace, screen-reader labeling |
| Animated numbers / counters | `number-flow` | Digit-roll animation, locale-aware formatting |
| Code syntax highlighting | `shiki` | VS Code-quality grammars, no runtime highlighting jank |

Not covered here — use these instead:
- **Icons** → `design-taste-frontend` §3.C (Phosphor / Hugeicons / Radix icons / Tabler, in priority order).
- **Accessible primitives** (dropdown, dialog, popover, tabs, tooltip) and the overall component/design system → `design-taste-frontend` §2.A's brief-to-system map.
- **Motion, gesture physics, spring configs** → `product-ui-motion`.

## Common mismatches

Signals that a hand-rolled implementation should be replaced with a library from the table above:

- A `<div>`-based toast/snackbar reimplementing timers, stacking, and dismissal → `sonner`.
- Manual `IntersectionObserver` infinite-scroll that re-solves what a virtualization library already does → `@tanstack/react-virtual`.
- A custom rich-text or markdown editor built from `contentEditable` → don't; reach for `react-markdown` (render-only) or `tiptap`/`lexical` (editable) instead.
- A hand-rolled color picker → `react-colorful`.
- Manually tracked `useState` per form field with ad hoc validation → `react-hook-form` + `zod`.

## Workflow

1. Identify the task from the brief or the code being written.
2. Check the lookup table, then the "not covered here" cross-references, before assuming nothing fits.
3. Check the project's manifest for an existing dependency that already does the job — including one not in this table — before adding a new one. `design-taste-frontend` §3.F covers this verification discipline in depth; this skill only adds the task→library mapping.
4. Recommend **one** library with the install command, not a menu of alternatives. If the project already uses a competing library for the same job, follow the existing one instead of introducing a second.
5. If nothing here fits and no existing dependency covers it, say so rather than forcing a match — some things are genuinely fine to hand-roll (a copy-to-clipboard button, a simple modal wrapper around a native primitive).
