# Frontend Review Dimensions

What to look for, per dimension. Cite `file:line` for every finding. Severity: 🔴 must fix, 🟡 should fix, 🔵 polish — user impact, not effort. A clean dimension is itself a finding worth one sentence.

## 1. API client & auth plumbing
- Endpoints bypassing the shared request wrapper — duplicated auth logic missing the shared 401/error handling.
- Missing timeouts/AbortController: a hung call spins a loading state forever.
- Error bodies not parsed (raw JSON shown to users).
- WebSocket/stream clients: does reconnect distinguish transient drop from policy reject; is the handshake authenticated.
- Redirect handling that drops auth headers (e.g. a trailing-slash 307 whose re-request goes cross-origin or changes scheme).

## 2. State management
- Token/session validation on app load — or does a stale token render the full shell before the first 401?
- Events for unknown ids silently dropped; swallowed catch blocks with no diagnostic trail.
- Persistence inventory: everything landing in localStorage/config files, one pattern or several, any secrets or PII.
- Unbounded growth: arrays fed by streams/events without caps.

## 3. Component quality
- Interactions that look live but aren't: handlers that only console.log, edit affordances with no persistence path.
- Destructive actions without confirmation.
- Inverted threshold/comparison logic (a higher-is-better metric flagged as bad for exceeding a threshold).
- Rules-of-hooks violations, including suppressed ones (`eslint-disable` on early returns before hooks).
- Dead interactive chrome (buttons without onClick); dead code (components with no importers).
- Silent failure paths: catch blocks that only log, no user feedback.

## 4. i18n architecture
- Key-set symmetry across locales — diff programmatically, report as fact.
- Files with zero i18n usage hardcoding user-visible strings (grep for absence of the translation hook).
- Mixed hardcoded languages inside one component; error/loading paths skipped by localization; locale-bypassing date/number formatting.
- A language switcher exists, or is one locale tree dead infrastructure?

## 5. Type safety
- `any` in production code, `@ts-ignore`/`@ts-expect-error`, non-null `!` assertions, unjustified casts.
- Runtime shape validation at API boundaries, or noted as mirroring a genuinely dynamic backend schema.

## 6. Accessibility basics
- Clickable non-interactive elements: div/Box with onClick but no role, tabIndex, or keyboard handler.
- Icon-only buttons without labels — spot-check and report the sample size.
- Color-only meaning without a text pairing.
- Contrast, focus traps, tab order → route to "Needs live confirmation".

## 7. Test coverage mapping

| Covered directly | Covered indirectly | No coverage at all |
|---|---|---|
| has own test file | rendered un-mocked by another component's test | neither |

Name the highest-complexity uncovered components; tie gaps to bugs from other dimensions where the gap explains them ("this is why the fake drag-drop shipped unnoticed").

## 8. Build hygiene
- console.log leftovers in interaction paths (distinguish from deliberate, suppressed console.error).
- Import patterns that defeat tree-shaking where the framework documents a supported pattern.
- Env vars: client-appropriate prefixes only, no secrets client-side; .env presence vs .gitignore.

## Doc skeleton

    # <App> — Frontend Code Review (<date>)

    Static source review of `<scope>`. Complements <sibling docs> — findings here
    are visible only by reading the code, and duplicate none of those reports'
    line items except where a new, distinct root cause was found.

    Severity: 🔴 must fix · 🟡 should fix · 🔵 polish.

    ## 1..8 [the dimensions that apply; state which were skipped and why]
    ## Coverage gaps (summary)
    ## Needs live confirmation
