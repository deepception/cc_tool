[← README](../../README.md)

# Directory structure

```
cc_tool/
  install.sh                     one-time machine setup (PATH + Superpowers)
  bin/
    cc-setup                     first-time project setup (--vault / --devcontainer chain to cc-vault / cc-devcontainer)
    cc-vault                     scaffold a self-writing vault (single-inbox autonomous note processing)
    cc-devcontainer              drop .devcontainer/ to sandbox Claude Code in Docker
    cc-token                     generate/refresh CLAUDE_CODE_OAUTH_TOKEN on host (for sandboxed containers)
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
    devcontainer/                files dropped into project .devcontainer/ by cc-devcontainer
      devcontainer.json            base config (cloud-specific mounts/env added at setup)
      Dockerfile                   node:20 + iptables/ipset + uv + optional cloud CLI
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
      no-ai-slop/SKILL.md                     edit a draft you wrote into sharper prose, or name its AI patterns without rewriting (MIT, petergyang/no-ai-slop)
        eval.md, LICENSE                        upstream self-check + preserved MIT licence (vendored verbatim)
      design-an-interface/SKILL.md            generate divergent interface designs via parallel sub-agents, then compare (MIT, mattpocock/skills)
      design-director/SKILL.md                route frontend design briefs to taste-skill variants + compose master design prompts
        references/                             prompt-anatomy guide + 3 archetype master-prompt templates + design-token scaffold + 3 aesthetic references (organic-tactile, punk-zine, psychedelic-surreal)
      product-ui-motion/SKILL.md              motion craft for product UI: frequency gate, easing/duration budgets, origin, interruptibility (derived from MIT, emilkowalski/skills)
        references/                             full motion catalog + review format; gesture physics (velocity handoff, momentum projection, rubber-banding)
      pick-ui-library/SKILL.md                task-to-library lookup for common UI mechanics: toasts, DnD, virtualization, forms (MIT, emilkowalski/skills)
      scroll-world/SKILL.md                   scroll-scrubbed AI-video "fly through the world" landing page pipeline (MIT, oso95/scroll-world)
        references/, LICENSE                  Higgsfield/Monid prompts + scrub engine + knockout script (vendored verbatim); needs paid external services, not bundled
      improve-codebase-architecture/SKILL.md  surface deep-module refactor opportunities as GitHub-issue RFCs (MIT, mattpocock/skills)
      triage/SKILL.md                         GitHub issue triage: bug/enhancement x 5-state machine, verify-before-label, agent-brief handoff (derived from mattpocock/skills)
      app-qa/SKILL.md                         full QA engagement orchestrator: e2e + UI/UX review + frontend review over shared discovery, up to three docs
      e2e-testing/SKILL.md                    plan + execute e2e tests of any app type; agent-run or paired mode; ✅/❌ walkthrough plan doc
        references/                             test-plan doc format + app-type driving-tools table
      ui-ux-review/SKILL.md                   live severity-tagged UX walkthrough (🔴🟡🔵) with beyond-happy-path sweep
        references/                             severity rubric, doc structure, app-type adaptation table
      frontend-review/SKILL.md                static interface-layer source review; no-duplication contract vs sibling docs
        references/                             the 9 review dimensions + coverage-mapping format
    hooks/
      session-context.py         SessionStart: git state, sensitive files, vault state, detected quality commands
      bash-guard.py              PreToolUse Bash: block commits/pushes to main/master, block --no-verify, block secret-file reads (.env, keys, credential stores) via grep/awk/xargs/inline interpreters
      big-file-guard.py          PreToolUse Read: warn on files >200KB without offset/limit
      context-usage.py           Stop: warn when session context window passes 80% (suggest /compact)
      post-edit-typecheck.py     PostToolUse Edit|Write|MultiEdit: fast project check (tsc/cargo; ruff file-scoped for Python) after source edits, surface errors inline; tsc timeouts back off for 30 min via a marker in .git/
  tests/
    test_bash_guard.py           124-case allow/deny matrix for bash-guard.py (stdlib only, self-contained fixtures)
  .claude/
    workflows/                   saved Workflow definitions (run via the Workflow tool)
      model-recalibration-audit.js  re-audit this setup against a new Claude model
      ship-pipeline.js              Planner → Coder → Tester → Reviewer pipeline
      loop-until-clean.js           loop-until-done sweep: stop after two dry rounds, then verify survivors
```

## Verifying the Bash guard

`bash-guard.py` is the enforced boundary for protected-branch git writes during unattended and workflow runs, so it has a matrix rather than a promise:

```bash
python3 tests/test_bash_guard.py        # 124 cases, ~3s, exits non-zero on any deviation
```

Expectations encode *intended* behaviour, including the bypasses deliberately out of scope (shell expansion, `sh -c` wrappers, base64) — those assert ALLOW on purpose, so a change that appears to close one surfaces here as a diff to justify rather than a silent behavioural shift. Re-run after editing the guard, and whenever the Claude Code CLI changes its hook contract.
