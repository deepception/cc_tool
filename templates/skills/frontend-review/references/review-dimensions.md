# Frontend Review Dimensions

What to look for, per dimension. Severity: 🔴 must fix, 🟡 should fix, 🔵 polish — user impact, not effort. A clean dimension is itself a finding worth one sentence. Defect findings follow the [finding format](#finding-format) at the end.

## 1. API client & auth plumbing
- Endpoints bypassing the shared request wrapper — duplicated auth logic missing the shared 401/error handling.
- Missing timeouts/AbortController: a hung call spins a loading state forever.
- Independent requests awaited one after another: a waterfall that multiplies load time, where `Promise.all` would run them together.
- Error bodies not parsed (raw JSON shown to users).
- WebSocket/stream clients: does reconnect distinguish transient drop from policy reject; is the handshake authenticated.
- Redirect handling that drops auth headers (e.g. a trailing-slash 307 whose re-request goes cross-origin or changes scheme).

## 2. State management
- Token/session validation on app load — or does a stale token render the full shell before the first 401?
- Events for unknown ids silently dropped; swallowed catch blocks with no diagnostic trail.
- Persistence inventory: everything landing in localStorage/config files, one pattern or several, any secrets or PII.
- Unbounded growth: arrays fed by streams/events without caps.

## 3. Component quality
- Vague generic labels where a direct, specific one exists ("Home" for a progress dashboard, "Library" for a collection of exactly one type of item).
- Interactions that look live but aren't: handlers that only console.log, edit affordances with no persistence path.
- Destructive actions without confirmation.
- Inverted threshold/comparison logic (a higher-is-better metric flagged as bad for exceeding a threshold).
- Rules-of-hooks violations, including suppressed ones (`eslint-disable` on early returns before hooks).
- Effects that subscribe (listeners, intervals, sockets, observers) with no cleanup; dependency arrays missing a value the effect reads, so it acts on stale data — suppressed `exhaustive-deps` included.
- Side effects in the render path (fetches, DOM writes, state setters). Components defined inside another component's body: each parent render creates a new type, so the child remounts and loses its state and focus.
- Dead interactive chrome (buttons without onClick); dead code (components with no importers).
- Silent failure paths: catch blocks that only log, no user feedback.

## 4. i18n architecture
- Key-set symmetry across locales — diff programmatically, report as fact.
- Placeholder symmetry per key, in the same pass: `{count}` in one locale and `{n}` in another renders a raw token or a blank.
- Plurals: a count-bearing string given one form in a locale whose rules need more (Polish and Russian distinguish one/few/many; Arabic has six categories). Numbers or units in a translation that disagree with the source string.
- Files with zero i18n usage hardcoding user-visible strings (grep for absence of the translation hook).
- Mixed hardcoded languages inside one component; error/loading paths skipped by localization; locale-bypassing date/number formatting.
- A language switcher exists, or is one locale tree dead infrastructure?

## 5. Type safety
- `any` in production code, `@ts-ignore`/`@ts-expect-error`, non-null `!` assertions, unjustified casts.
- Runtime shape validation at API boundaries, or noted as mirroring a genuinely dynamic backend schema.
- Contract drift: when the backend source or an OpenAPI/GraphQL schema is in the repo, compare the client's types with what the server actually sends — field names, optionality, nullability — rather than trusting the client's declaration.

## 6. Accessibility basics
- Clickable non-interactive elements: div/Box with onClick but no role, tabIndex, or keyboard handler.
- Icon-only buttons without labels — spot-check and report the sample size.
- Color-only meaning without a text pairing.
- Fixed `id`s inside components rendered more than once (list rows, reusable fields): every `htmlFor`/`aria-labelledby`/`aria-describedby` then resolves to the first instance. Form inputs with no associated label.
- Contrast, focus traps, tab order → route to "Needs live confirmation".

## 7. Test coverage mapping

| Covered directly | Covered indirectly | No coverage at all |
|---|---|---|
| has own test file | rendered un-mocked by another component's test | neither |

Name the highest-complexity uncovered components; tie gaps to bugs from other dimensions where the gap explains them ("this is why the fake drag-drop shipped unnoticed").

## 8. Build hygiene
- console.log leftovers in interaction paths (distinguish from deliberate, suppressed console.error).
- Import patterns that defeat tree-shaking where the framework documents a supported pattern.
- Env vars: client-appropriate prefixes only, no secrets client-side. `.env*` files (templates like `.env.example` aside) and `.npmrc` auth tokens: committed, or gitignored? Name the variable and file but never copy a secret's value into the doc, which gets committed.
- `package.json`: `latest` or `*` version ranges, one package in both `dependencies` and `devDependencies`, a script invoking a tool no manifest declares.

## 9. Motion & interaction feel

Applies to any app with transitions, animations, or drag/swipe interactions; skip it for CLIs and APIs and say so. The rule catalog and exact values live in [`product-ui-motion`](../../product-ui-motion/SKILL.md) and its `references/` — read them before writing findings, and pull curves, durations and spring configs from there rather than approximating. If that skill isn't installed alongside, review this dimension from the code's own motion tokens and flag the missing standard.

- Animation on something the user hits 100+ times a day — a command palette, core navigation, a frequent toggle. The fix is usually deletion, not tuning.
- `transition: all`, `ease-in` on anything the user waits on, UI durations over 300ms outside modals/drawers/sheets.
- Entrances from `scale(0)`; `transform-origin: center` on a trigger-anchored popover, dropdown or tooltip (modals are exempt).
- `@keyframes` on rapidly-triggered elements (toasts, toggles) where transitions or springs would retarget instead of restarting.
- Layout properties animated (`width`, `height`, `top`, `left`, `margin`, `padding`); a parent CSS variable driving child transforms.
- Missing `prefers-reduced-motion` handling on movement; `:hover` motion not gated behind `@media (hover: hover) and (pointer: fine)`.
- Symmetric timing on a press-and-release or hold interaction; a group entrance with no stagger where 30–80ms belongs.

Report these as the `Before | After | Why` table from `motion-standards.md` rather than prose bullets, and close with a short **considered and rejected** list — places that could animate and deliberately should not, each with the reason. A motion section that only adds motion is a wishlist. Feel judgements that code can't settle (does a crossfade read as one object, is a spring's bounce right) go to "Needs live confirmation" with the feel-check that would settle them.

## 10. Untrusted content & client-side security

The interface layer's share of security: places where data the repo didn't author reaches a sink. Applies to web front ends and server-rendered templates; skip it for CLIs and JSON-only APIs and say so. Server-side authorization and dependency CVEs are out of scope; `/security-review` covers a branch's diff.

- Raw-HTML escape hatches: `dangerouslySetInnerHTML`, `v-html`, `innerHTML`/`insertAdjacentHTML`, Astro's `set:html`, a template engine's unescaped output (`{{{ }}}`, `!{}`, `|safe`). Trace where the string comes from and whether a sanitizer runs after the last concatenation. Plain JSX or `{{ }}` text is escaped by the framework already; don't flag it.
- User- or API-supplied URLs in `href`, `src`, `action`, `window.location`, or a post-login redirect (`?next=`, `returnUrl`) with no scheme or destination allowlist: `javascript:` links, open redirects. Frameworks differ by version on blocking `javascript:` URLs, so check the installed version before rating severity.
- `eval`, `new Function`, string arguments to `setTimeout`/`setInterval`, `document.write`.
- Server data crossing to the client whole: SSR props, hydrated-island props or serialized page state (`__NEXT_DATA__`, `window.__INITIAL_STATE__`) carrying fields the UI never shows — tokens, internal ids, other users' records. State serialized into an inline `<script>` without escaping `</script>`.

## Finding format

One bullet per finding: the severity tag and a bold lead naming the problem; `file:line` plus a short fragment of the offending code copied verbatim from the file; what the user experiences as a result; the fix direction. The fragment keeps a finding locatable once fixing starts, since the first edit in a file shifts every later line number in it, and it turns the re-read at the citation into a string match. Motion findings use dimension 9's table instead.

One root cause at many sites is one finding: give the count and representative citations, or all of them when there are only a handful. Merge only findings that make the same claim; two different problems in the same file stay two findings.

## Doc skeleton

    # <App> — Frontend Code Review (<date>)

    Static source review of `<scope>`. Complements <sibling docs> — findings here
    are visible only by reading the code, and duplicate none of those reports'
    line items except where a new, distinct root cause was found.

    Coverage: <N> files in scope — <n> read in full, <n> sampled (<how>),
    <n> skipped (<why>). Excluded as generated/vendored: <paths>.
    Severity: 🔴 must fix · 🟡 should fix · 🔵 polish.

    ## 1..10 [the dimensions that apply; state which were skipped and why]
    ## Coverage gaps (summary)
    ## Suggested fix order
    [Numbered, one line per root cause: severity first, then cheap-and-visible]
    ## Needs live confirmation
    [Each item with the live check that would settle it]
