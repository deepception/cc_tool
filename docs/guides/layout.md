[← README](../../README.md)

# Directory structure

```
cc_tool/
  install.sh                     one-time machine setup (PATH + Superpowers)
  bin/
    cc-setup                     first-time project setup (--vault / --devcontainer / --factory chain to cc-vault / cc-devcontainer / cc-factory init)
    cc-vault                     scaffold a self-writing vault (single-inbox autonomous note processing)
    cc-devcontainer              drop .devcontainer/ to sandbox Claude Code in Docker
    cc-token                     generate/refresh CLAUDE_CODE_OAUTH_TOKEN on host (for sandboxed containers)
    cc-dash                      local read-only dashboard of Claude Code sessions (Python stdlib server on 127.0.0.1)
    cc-factory                   software-factory CLI: work orders, spec and merge gates, lanes (worktree + branch), holdout dirs; the only writer of the factory ledger
    cc-selfheal                  self-heal from a terminal: the evidence bundle, one headless self-heal run, the proposals and their patch paths (--dry-run: the bundle only)
    cc-docs-migrate              bring a project's docs/ and vault/notes/ to the docs standard (docs/guides/docs-standard.md): frontmatter, relative links, opt-in factory orders via cc-factory new; dry run by default, every write logged with sha256 before/after and undoable from backups
  lib/cc_dash/                   cc-dash internals, standard library only
    pricing.py                   per-model prices and the per-message cost estimate
    parser.py                    incremental JSONL reader and per-transcript accumulator
    store.py                     transcript discovery, subagent and workflow linkage, guard-log attribution, JSON payloads
    server.py                    HTTP server: loopback bind, Host-header check, fixed asset map, CLI flags
    attention.py                 open alerts for /api/alerts: tails the attention hook's log, resolve rules, session registry, transcript fallback
    routing.py                   /api/routing: who picked each subagent's model and effort, work-type heuristic, cache-expiry rewrites, flags against the managed block's routing
    docs.py                      Docs view: per-project file tree, search, one-time HTML frame tickets; path allowlisting, secret refusal, redaction
    graph.py                     /api/graph: the Factory floor's one live graph (projects, sessions, agents, workflows, orders, their edges, alerts, feed, KPIs), joined from the board, session rows, plans, alerts and orders
    factory.py                   work orders, the line and the ledger: the one implementation of the formats, read-only (cc-dash reads it, cc-factory writes through it)
    selfheal.py                  self-heal: the evidence bundle (bounded, redacted), the run's state and window, the fixed-argv child with a scrubbed env, finalize, proposal and patch validation, /api/selfheal
    web/                         the page: index.html, app.css, app.js, alerts.js/.css (toasts, bell drawer, title badge), alerts-worker.js (background-tab poller), markdown.js (pure Markdown renderer), docs.js/.css (Docs view), routing.js/.css (the Overview's Routing panel), graph.js (dependency-free graph engine), factory.js/.css (the Factory floor), selfheal.js/.css (the header's Self-heal control and its results drawer); no CDN, no network
    cc-update-project            update an existing project (hooks + skills + permissions)
    cc-update                    update global plugins (Superpowers + security-guidance)
    cc-update-permissions        [internal] deny/ask merge helper, called by cc-update-project
    cc-install-superpowers       install Superpowers globally (called by install.sh)
    cc-install-security          install Anthropic security-guidance plugin (called by install.sh)
    cc-install-tasteskill        install taste-skill visual-design skills globally (called by install.sh)
  templates/
    settings.json                full settings for new projects; its hooks block is also
                                 what cc-setup merges into an existing settings.json
    CLAUDE_template.md           full CLAUDE.md for new projects (placeholders to fill in)
    CLAUDE_snippet.md            appended to existing CLAUDE.md (AI tools + model routing + reasoning + output discipline + verification + context mgmt + critical rules)
    vault/                       seed files dropped into project vault/ by cc-vault
      README.md                    the vault contract (5 rules + layout table)
      AUTOMATION.md                trigger wiring: cron / /schedule / /loop (path substituted)
      index.md, log.md             routing table + append-only run ledger seeds
    factory/                     scaffold dropped into project factory/ by cc-factory init
      README.md                    what the folder is, the order lifecycle in one table
      line.json                    the default line: plan, build, test, verify (holdout), review; spec and merge gates
      order-template.md            frontmatter + Intent / Acceptance criteria / Notes
    devcontainer/                files dropped into project .devcontainer/ by cc-devcontainer
      devcontainer.json            base config (cloud-specific mounts/env added at setup)
      Dockerfile                   node:24 + iptables/ipset + uv + optional cloud CLI
      init-firewall.sh             default-deny egress + ipset allowlist
      managed-settings.json        org-policy settings (highest precedence inside container)
    skills/                      project skills copied to .claude/skills/ on cc-setup
      reflect/SKILL.md                        session reflection and learning extraction
      skills-audit/SKILL.md                   audit installed skills for quality and overlap
      skill-engineer/SKILL.md                 create and update skills from workflow descriptions
      dynamic-workflows/SKILL.md              the 6 Workflow patterns + operational controls (full catalog)
      loop-engineering/SKILL.md               structural model of an autonomous loop: loop-type taxonomy, six-component anatomy, disk state, inner/outer layers
      gauntlet-loop/SKILL.md                  builder vs. independent critic iterated against a concrete reference bar (technique: somethingbig.ai/gauntlet-loop)
      knowledge-wiki/SKILL.md                 Karpathy compile-once wiki: distill a codebase/topic into a durable wiki
      vault/SKILL.md                          self-writing vault operations: process inbox → linked notes + digest, weekly synthesis, graph health
      factory/SKILL.md                        /factory: intake an order with holdout scenarios, run it down the line in its own lane, status, gates
      no-ai-slop/SKILL.md                     edit a draft you wrote into sharper prose, or name its AI patterns without rewriting (MIT, petergyang/no-ai-slop)
        eval.md, LICENSE                        upstream self-check + preserved MIT licence (vendored verbatim)
      design-an-interface/SKILL.md            generate divergent interface designs via parallel sub-agents, then compare (adapted from mattpocock/skills, MIT; retired upstream, successor codebase-design/DESIGN-IT-TWICE.md)
        LICENSE                                 upstream MIT licence; source and SHA in the SKILL.md header
      design-director/SKILL.md                route frontend design briefs to taste-skill variants + compose master design prompts
        references/                             prompt-anatomy guide + 3 archetype master-prompt templates + design-token scaffold + 3 aesthetic references (organic-tactile, punk-zine, psychedelic-surreal)
      product-ui-motion/SKILL.md              motion craft for product UI: frequency gate, easing/duration budgets, origin, interruptibility (derived from MIT, emilkowalski/skills)
        references/                             full motion catalog + review format; gesture physics (velocity handoff, momentum projection, rubber-banding)
      pick-ui-library/SKILL.md                task-to-library lookup for common UI mechanics: toasts, DnD, virtualization, forms (MIT, emilkowalski/skills)
      scroll-world/SKILL.md                   scroll-scrubbed AI-video "fly through the world" landing page pipeline (MIT, oso95/scroll-world)
        references/, LICENSE                  Higgsfield/Monid prompts + scrub engine + knockout script (vendored verbatim); needs paid external services, not bundled
      hyperframes-movie/SKILL.md              build a HyperFrames Studio launch/promo/explainer film and iterate it against 8 measured gates (explicit /hyperframes-movie only)
        reference.md, scripts/, templates/      pacing model + motion vocabulary; build, reading-audit, rhythm and playback gates; schedule, score and scene templates
      improve-codebase-architecture/SKILL.md  surface deep-module refactor opportunities as GitHub-issue RFCs (adapted from mattpocock/skills, MIT)
        REFERENCE.md, LICENSE                   RFC template; upstream MIT licence; source and SHA in the SKILL.md header
      triage/SKILL.md                         GitHub issue triage: bug/enhancement x 5-state machine, verify-before-label, agent-brief handoff (derived from mattpocock/skills)
      app-qa/SKILL.md                         full QA engagement orchestrator: e2e + UI/UX review + frontend review over shared discovery, up to three docs
      e2e-testing/SKILL.md                    plan + execute e2e tests of any app type; agent-run or paired mode; ✅/❌ walkthrough plan doc
        references/                             test-plan doc format + app-type driving-tools table
      ui-ux-review/SKILL.md                   live severity-tagged UX walkthrough (🔴🟡🔵) with beyond-happy-path sweep
        references/                             severity rubric, doc structure, app-type adaptation table
      frontend-review/SKILL.md                static interface-layer source review; no-duplication contract vs sibling docs
        references/                             the 9 review dimensions + coverage-mapping format
    hooks/
      cc_procs.py                process checks shared by dash-control.py and cc-dash (not a hook): which process is the real dashboard, which wrote a request, the sha256 record of cc-dash's code
      cc_hooklib.py              shared helpers (not a hook): repo root, BLOCKED: message format, activity log, per-session trajectory state, .claude/guard-rules.json loader
      session-context.py         SessionStart: git state, sensitive files, vault state, detected quality commands
      bash-guard.py              PreToolUse Bash: block commits/pushes to main/master, --no-verify, secret-file reads via grep/awk/xargs/inline interpreters, destructive commands (rm -rf on roots/.git/lockfiles/CI, sudo rm, pkill <name>, push --force, curl|sh, env|curl, registry redirection, mkfs/dd, chmod 777); ask only before installs, deletions, GitHub API writes and landing work on a protected branch, by allowlist (with HEAD on main/master/production/release, or moved to one or to an unknown branch earlier in the command, only git commands that cannot move it run: reads, add/mv/stash, fetch without a refspec, branch listing or a new branch, checkout/switch without -B/-C, config reads, `git pull --ff-only` from its own verified upstream; anything else asks, aliases, `-c` and GIT_DIR included; on any branch, a non-read git command that names a protected branch, or one it cannot read such as `-`, @{-1} or an expansion, asks, as do update-ref --stdin, symbolic-ref writes, remote add/set-url/set-branches and config writes to alias, remote, branch, url, pull, include or core.hooksPath; an unparseable git command asks; a text guard whose documented residual class is a `cd` into another repository and `.git/config` edited by hand through the shell); deleting anything that is not scratch or an untracked regenerable cache (rm / rmdir / find -delete / xargs rm / rsync --delete, git rm, checkout -- / restore <paths>, worktree remove --force, reset --hard, clean -f, branch -D, stash drop, docker prune, kubectl delete --all, terraform destroy, DROP-TRUNCATE-DELETE-without-WHERE), and `npx <pkg>` when the package is not on disk (installed dev dependencies and exact versions already in the npx cache run without a prompt), and a `gh api` call that may write (-X/--method other than GET, -f/-F fields without `-X GET`, --input, a method-override header, a GraphQL query with `mutation` or a `$`, or any word that is not literal: a `$`/backtick (no variable is resolved), an unquoted glob or brace, process substitution, IFS, or a command name bash builds such as `$G api`; reads such as `gh api repos/o/r --jq …` stay prompt-free under the `Bash(gh api *)` allow rule); warn on shell writes into source files (> / tee / sed -i / patch / git apply); applies project rules
      write-guard.py             PreToolUse Edit|Write|MultiEdit|NotebookEdit, on every path field: deny writes into cc-dash's control directory, log and project registry, self-heal's state directory, and anywhere in the pinned cc_tool checkout (unless the project is that checkout or one of its worktrees); deny writes to system paths, shell/credential dotfiles, .git/ (NotebookEdit gets these location checks only); deny real-looking secrets in content (keys, tokens, JWTs, credential assignments; placeholders and .env* pass); warn on writes outside the repo (tunable to ask; the repo's other git worktrees count as inside); warn on stale reads (file changed on disk since last read) and on a red check pending in another file; applies project rules
      route-nudge.py             PreToolUse Agent|Task: one line of additionalContext when the call names no `model` (and, above medium session effort, no `effort`); never asks, denies or prints for a call that names a model
      big-file-guard.py          PreToolUse Read: warn on files >200KB without offset/limit
      context-usage.py           Stop: warn when session context window passes 80% (suggest /compact)
      post-edit-typecheck.py     PostToolUse Edit|Write|MultiEdit: fast project check (tsc/cargo; ruff file-scoped for Python) after source edits, only for files whose real path is inside the session's project (a toolchain found elsewhere never runs), surface errors inline; tsc timeouts back off for 30 min via a marker in .git/ and report NOT CHECKED (once) instead of staying silent; missing tsc reported once per session; red/green recorded in session state
      activity-log.py            PostToolUse (every tool): one JSON line per tool call in .git/cc_tool/activity.jsonl, or for a git worktree in ${XDG_STATE_HOME:-~/.local/state}/cc_tool/repos/<tag>/ (0700, used only when it is a private directory of yours; files opened with O_NOFOLLOW) (guards append their deny/ask/warn there too; rotates at 5 MB); records file mtimes after Read/Edit/Write for the stale-read check
      attention.py               PermissionRequest, Notification, PostToolUse/Failure/Batch, UserPromptSubmit, Stop, SubagentStop, StopFailure, Elicitation(Result), SessionEnd (async except SessionEnd): one JSON line per event in ${XDG_STATE_HOME:-~/.local/state}/cc_tool/attention.jsonl (0700/0600, no symlinks followed, rotates at 5 MB) for cc-dash alerts; silent, never blocks
  tests/
    test_bash_guard.py           allow/ask/warn/deny matrix for bash-guard.py (stdlib only, self-contained fixtures)
    test_write_guard.py          matrix for write-guard.py: location, secrets-in-content, trajectory warnings, project rules
    test_cc_dash.py              cc-dash parser, cost and subagent linkage on fake transcripts, incremental reads, server Host/traversal checks
    test_route_nudge.py          route-nudge.py: the line on a missing model, silence with one, default resolution, effort suffix, garbage input, latency, settings registration, cc-setup and cc-update-project rollout
    test_cc_dash_routing.py      cc-dash routing: work-type rules, model source, effort, expiry rewrites, flags and savings, scope, endpoint auth, the panel in a browser (content, no churn on polls)
    test_attention_hook.py       attention.py: event lines per hook event, file modes, rotation, symlink/hard-link/FIFO refusal, silence, garbage input, latency, settings registration
    test_cc_dash_alerts.py       /api/alerts: open/resolve state machine, out-of-order lines, staleness, registry, transcript fallback and dedupe, rotation, shape, timing
    test_cc_dash_docs.py         Docs view: project allowlist, traversal/symlink/secret refusal, size caps, tree sections, search bounds, HTML sandbox CSP, renderer (node), hostile-doc browser check
    test_post_edit_typecheck.py  post-edit-typecheck.py scope: nothing runs for an edit outside the project (planted Cargo.toml, local tsc, symlinks); inside it, the right root
    test_cc_dash_graph.py        /api/graph: shape, stable ids and sig, edge integrity, workflow and alert attachment, the order join (stub and real cc-factory), the last-hour series (buckets, scope, finished agents, the minute-bucket bound), scope, auth, latency
    test_cc_dash_factory_ui.py   the Factory floor in headless Chrome: render, inspector and re-framing, in-place polls without relayout, alerts, the activity column (recent actions held under the pointer, the last-hour charts and their tooltips, how a short window splits it between rows and plots, the slot it shares with the inspector, the Activity toggle), zoom-button clearance, hub names, text contrast, themes, reduced motion, narrow and short viewports, malformed payloads
    test_factory.py              factory formats, ledger folding and file safety, worktree ledger path, cc-factory end to end, cc-setup --factory, factory-line.js sanity (parts: lib, cli, setup, workflow)
    test_selfheal.py             self-heal: window, redaction and bounds of the bundle, state and one run at a time, the control-scoped POST, the child's fixed argv and scrubbed env (fake claude), no self-approval, patch validation, workflow sanity, the header and drawer in headless Chrome
    test_docs_migrate.py         cc-docs-migrate on fixture projects: dry run writes nothing, classification and planned keys, skip reasons, link fixes, apply/log/backups, plans.py parse unchanged, idempotent re-apply, byte-identical undo, refusals (dirty tree, non-git, symlinked docs/, subdirectory), opt-in orders through cc-factory, --only
  .claude/
    workflows/                   saved Workflow definitions (run via the Workflow tool)
      model-recalibration-audit.js  re-audit this setup against a new Claude model
      ship-pipeline.js              Planner → Coder → Tester → Reviewer pipeline
      loop-until-clean.js           loop-until-done sweep: stop after two dry rounds, then verify survivors
      factory-line.js               the factory line: plan → build → test → holdout verify → review, rework loop, never merges (copied into projects by cc-factory init)
      self-heal.js                  self-heal: classify → root cause → propose → adversarial check → write, readers read-only (run by cc-dash and cc-selfheal, never by hand)
      self-heal.agents.json         its two agent types: selfheal-reader (Read, Grep, Glob) and selfheal-writer (Write)
```

## Verifying the Bash guard

`bash-guard.py` is the enforced boundary for protected-branch git writes during unattended and workflow runs, so it has a matrix rather than a promise:

```bash
python3 tests/test_bash_guard.py        # ~200 cases, a few seconds, exits non-zero on any deviation
python3 tests/test_write_guard.py       # write-guard.py matrix
python3 tests/test_cc_dash.py           # cc-dash: parser, aggregation, server security
python3 tests/test_attention_hook.py    # attention hook: event log, modes, refusals
python3 tests/test_cc_dash_alerts.py    # cc-dash alerts: state machine, endpoint, timing
python3 tests/test_cc_dash_docs.py      # cc-dash Docs view: path safety, sandbox, renderer
python3 tests/test_cc_dash_routing.py   # cc-dash Routing panel: classifier, flags, rewrites, endpoint, browser
python3 tests/test_selfheal.py          # self-heal: bundle, state, control-scoped trigger, fixed child argv and env, patches, UI
python3 tests/test_route_nudge.py       # route-nudge hook: nudge, silence, effort suffix, rollout
```

## Project guard rules

`.claude/guard-rules.json` adds per-project deny/ask/warn regexes that `bash-guard.py` (on the command string) and `write-guard.py` (on `file_path` or `content`) apply after their built-in checks. The `distill-rules` skill writes it from the imperatives in `CLAUDE.md` and the project's skills, with the lexical ladder never/must not → deny, should not/avoid → ask, should/prefer → warn. Schema and an example live at the top of `cc_hooklib.py`. A malformed file is reported to the model as "project rules are NOT enforced" rather than silently ignored.

Expectations encode *intended* behaviour, including the bypasses deliberately out of scope (shell expansion, `sh -c` wrappers, base64) — those assert ALLOW on purpose, so a change that appears to close one surfaces here as a diff to justify rather than a silent behavioural shift. Re-run after editing the guard, and whenever the Claude Code CLI changes its hook contract.
