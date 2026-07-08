# UI/UX Review Rubric

## Severity

| Tag | Meaning | Test |
|-----|---------|------|
| 🔴 must fix | Blocks or destroys user work, or traps the user | Would a user lose work, get logged out, or fail their task? |
| 🟡 should fix | Erodes trust or usability | Would a user hesitate, misread, or need a second attempt? |
| 🔵 polish | Noticeably imperfect | Would a careful reviewer notice, though no user is harmed? |

Severity measures user impact, not implementation effort.

## Doc structure

    # <App> — UI/UX Review (<date>)

    Based on a full walkthrough of <build/profile>, <role>, <viewport>, <theme>, <locale>.
    Severity: 🔴 must fix, 🟡 should fix, 🔵 polish.

    ## Global
    [Findings recurring across screens: navigation, auth-failure UX, i18n patterns, date formats]

    ## <Screen name>          (one section per screen, in nav order)
    [Per-screen findings; open with a ✅ line when the screen earns it]

    ## What already works well (keep)
    [Patterns to protect: theme consistency, empty-state quality, feedback conventions]

    ## Suggested fix order
    [Numbered: severity first, then cheap-and-visible]

## Finding style

- One finding per bullet: bold lead stating the problem, then the observed evidence, then the concrete suggestion. "(observed covering the first KPI card)" beats "sometimes overlaps".
- Copy findings name the exact strings ("Terminowosc dostaw" → "Terminowość dostaw"); a diacritics sweep names every instance found.
- Praise inline with ✅ where deserved — it calibrates the criticism.

## Beyond-happy-path checklist

- Both themes: grey-on-grey, unreadable, or unstyled regions?
- Viewport ≤1280px: clipping, missing horizontal-scroll affordances?
- i18n: leaks from other locales, missing diacritics, date/number formats per locale, native-widget placeholder formats.
- Empty states: every list/dashboard with no data — guidance, or a blank void?
- Loading states: skeletons/spinners on slow fetches?
- Error states: backend down, failed save — visible feedback or silence?

## Adaptation by app type

| App type | "Screen" means | Extra checks |
|----------|----------------|--------------|
| GUI | each route/page/dialog | the checklist above |
| CLI | each subcommand + its `--help` | error-message quality, exit codes, flag naming consistency, no-color/TTY output |
| API | each endpoint group + its error responses | error body shape and language, naming consistency, status-code discipline, docs/OpenAPI accuracy |
