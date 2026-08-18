# Design token scaffold

Define these before writing any layout or component code — filling in values after the fact tends to produce one-off inline styles that drift from the design read. This is a starting shape, not a fixed schema: drop categories the build doesn't need, add ones it does (a data-viz palette, an elevation scale for a dashboard).

```css
:root {
  /* Color */
  --color-bg: ;
  --color-surface: ;
  --color-text: ;
  --color-muted: ;
  --color-accent: ;
  --color-focus: ;
  --color-success: ;
  --color-warning: ;
  --color-danger: ;

  /* Typography */
  --font-display: ;
  --font-body: ;
  --font-mono: ;
  --text-xs: ;   --leading-xs: ;
  --text-sm: ;   --leading-sm: ;
  --text-base: ; --leading-base: ;
  --text-lg: ;   --leading-lg: ;
  --text-xl: ;   --leading-xl: ;
  --text-2xl: ;  --leading-2xl: ;

  /* Spacing */
  --space-1: ; --space-2: ; --space-3: ;
  --space-4: ; --space-6: ; --space-8: ;

  /* Radius and shadow */
  --radius-sm: ; --radius-md: ; --radius-lg: ;
  --shadow-sm: ; --shadow-md: ; --shadow-lg: ;

  /* Motion */
  --duration-fast: ; --duration-base: ; --duration-slow: ;
  --ease-out: ; --ease-spring: ;
}
```

Fill values from the design read, not from habit — `design-taste-frontend` §4 (typography/color) and `product-ui-motion` (durations, easing curves) own the actual value decisions; this scaffold just fixes the token *names* so a build doesn't accumulate inline one-offs. One dark-mode swap strategy per project (Tailwind `dark:` variant or a second value set under `[data-theme="dark"]`/`prefers-color-scheme`) — see `design-taste-frontend` §8.
