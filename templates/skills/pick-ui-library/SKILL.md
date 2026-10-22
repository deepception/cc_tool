---
name: pick-ui-library
description: >-
  Task-to-library lookup for common UI/product needs, including toasts, command palettes, drag
  and drop, virtualization, global state, class-name/variant composition, theming,
  forms, charts, date pickers, OTP inputs, animated numbers, syntax highlighting,
  icons, and which design system (Fluent/Material/Carbon/Radix/shadcn) fits a new
  product. Use before hand-rolling any of these, or when picking which npm package
  solves a UI task. Visual direction goes to design-director; motion and interaction
  craft to product-ui-motion.
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
| Icons | `@phosphor-icons/react` (then `hugeicons-react`, `@radix-ui/react-icons`, `@tabler/icons-react`) | Consistent stroke and grid; never hand-draw SVG icon paths. One family per project, so an existing icon dependency wins |

Not covered here, so use these instead:
- **Accessible primitives** (dropdown, dialog, popover, tabs, tooltip) → the project's design system (below); Radix primitives when it has none.
- **Visual direction, type, colour** → `design-director`.
- **Motion, gesture physics, spring configs** → `product-ui-motion`.

## Design system for a new product

An existing app keeps the system it has: one system per project, never a second one mixed in. For a new product, match the brief and install the official package rather than recreating its CSS: enterprise or Microsoft-style SaaS → `@fluentui/react-components`; Material-flavored → `@material/web`; IBM-style B2B analytics → `@carbon/react`; Shopify admin → Polaris; Atlassian-style → `@atlaskit/*`; GitHub-style devtool → `@primer/react`; UK or US public sector → `govuk-frontend` / `uswds`; modern accessible React foundation → `@radix-ui/themes`; SaaS where you own the components → shadcn/ui, restyled, never shipped in its default state. (Condensed from taste-skill's pre-v2 brief-to-system map, MIT.)

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
3. Check the project's manifest for an existing dependency that already does the job (including one not in this table) before adding a new one.
4. Recommend **one** library with the install command, not a menu of alternatives. If the project already uses a competing library for the same job, follow the existing one instead of introducing a second.
5. If nothing here fits and no existing dependency covers it, say so rather than forcing a match. Some things are genuinely fine to hand-roll (a copy-to-clipboard button, a simple modal wrapper around a native primitive).
