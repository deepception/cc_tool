export const meta = {
  name: 'model-recalibration-audit',
  description: 'Audit cc_tool setup (calibrated for Opus 4.7) against a newer model (Opus 4.8) and produce prioritized, evidence-verified recalibration recommendations',
  whenToUse: 'Run once per Claude model release to re-audit hooks, permissions, CLAUDE.md guidance, and skills against the new model\'s capabilities. Parameterize via args.newModel / args.oldModel.',
  phases: [
    { title: 'Research', detail: 'Multi-angle web sweep for the new model\'s capabilities + 4.7→4.8 deltas' },
    { title: 'Profile', detail: 'Synthesize a single capability profile with confidence levels and research gaps' },
    { title: 'Analyze', detail: 'One agent per setup component: extract encoded model-behavior assumptions, classify vs profile' },
    { title: 'Verify', detail: 'Adversarially verify every proposed change; reject unverified-capability and guardrail-weakening claims' },
    { title: 'Synthesize', detail: 'Prioritized recalibration report: P0/P1/P2, keep-list, needs-confirmation list, CHANGELOG entry' },
  ],
}

// ---- config (reusable across model releases via args) -------------------
const cfg = (args && typeof args === 'object') ? args : {}
const NEW_MODEL = cfg.newModel || 'Claude Opus 4.8'
const NEW_MODEL_ID = cfg.newModelId || 'claude-opus-4-8'
const OLD_MODEL = cfg.oldModel || 'Claude Opus 4.7'
const ROOT = cfg.root || '/home/lukasz/Files/hobby/cc_tool'
// Facts authoritative for THIS environment (from the running harness/system prompt).
// These are the trustworthy floor: web research about a same-day release is often thin.
const CONFIRMED_FACTS = cfg.confirmedFacts || [
  `${NEW_MODEL} (model ID '${NEW_MODEL_ID}') was released 2026-05-28 and is the latest Claude model.`,
  `${NEW_MODEL} supports a 1,000,000-token (1M) context window (variant '${NEW_MODEL_ID}[1m]'). Earlier Opus context was commonly 200K. Whether ${OLD_MODEL} also offered 1M is uncertain — treat the AVAILABILITY of 1M context as confirmed for ${NEW_MODEL}.`,
  `Fast mode in Claude Code uses Claude Opus with faster output (NOT a smaller/downgraded model); available on Opus 4.8/4.7/4.6; toggled with /fast.`,
  `${NEW_MODEL} knowledge cutoff is January 2026.`,
  `The Claude Code harness offers deterministic multi-agent workflow orchestration (Workflow tool) and an 'ultracode' high-effort mode.`,
]

const FACTS_BLOCK = `MODELS: new = ${NEW_MODEL} (${NEW_MODEL_ID}); previous = ${OLD_MODEL}.
ENVIRONMENT-CONFIRMED FACTS (authoritative, do not contradict these):
${CONFIRMED_FACTS.map((f, i) => `  ${i + 1}. ${f}`).join('\n')}`

// ---- schemas ------------------------------------------------------------
const RESEARCH_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    angle: { type: 'string' },
    sourcesFound: { type: 'boolean', description: 'true only if authoritative info about the NEW model was actually located' },
    sources: { type: 'array', items: { type: 'object', additionalProperties: false,
      properties: { url: { type: 'string' }, title: { type: 'string' },
        credibility: { type: 'string', enum: ['official', 'reputable', 'community', 'unknown'] } },
      required: ['url', 'title', 'credibility'] } },
    facts: { type: 'array', items: { type: 'object', additionalProperties: false, properties: {
      claim: { type: 'string' },
      category: { type: 'string', enum: ['context-window', 'reasoning', 'agentic-autonomy', 'tool-use', 'instruction-following', 'speed-cost', 'safety-refusal', 'claude-code-feature', 'prompting-guidance', 'other'] },
      confidence: { type: 'string', enum: ['confirmed', 'likely', 'speculative'] },
      deltaFromOld: { type: 'string', description: 'how it differs from the previous model, or "unknown"' },
      source: { type: 'string' },
    }, required: ['claim', 'category', 'confidence', 'deltaFromOld', 'source'] } },
  },
  required: ['angle', 'sourcesFound', 'sources', 'facts'],
}

const PROFILE_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    summary: { type: 'string' },
    confirmedCapabilities: { type: 'array', items: { type: 'object', additionalProperties: false, properties: {
      capability: { type: 'string' },
      confidence: { type: 'string', enum: ['confirmed', 'likely', 'speculative'] },
      implicationForSetup: { type: 'string' },
    }, required: ['capability', 'confidence', 'implicationForSetup'] } },
    keyDeltas: { type: 'array', items: { type: 'string' }, description: 'most decision-relevant old→new deltas' },
    researchGaps: { type: 'array', items: { type: 'string' }, description: 'capability questions we could NOT verify from sources' },
  },
  required: ['summary', 'confirmedCapabilities', 'keyDeltas', 'researchGaps'],
}

const ANALYSIS_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    component: { type: 'string' },
    findings: { type: 'array', items: { type: 'object', additionalProperties: false, properties: {
      title: { type: 'string' },
      location: { type: 'string', description: 'file + line range or section heading' },
      encodedAssumption: { type: 'string', description: 'the model-behavior/capability/failure-mode assumption this component encodes' },
      classification: { type: 'string', enum: ['VALID', 'NOISE', 'GAP', 'OPPORTUNITY'] },
      tiedCapability: { type: 'string', description: 'the specific profile fact this maps to, or "none"' },
      rationale: { type: 'string' },
      recommendation: { type: 'string' },
      proposedChange: { type: 'string', description: 'concrete wording/diff, or "n/a — keep"' },
      confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
      severity: { type: 'string', enum: ['P0', 'P1', 'P2', 'keep'] },
    }, required: ['title', 'location', 'encodedAssumption', 'classification', 'tiedCapability', 'rationale', 'recommendation', 'proposedChange', 'confidence', 'severity'] } },
  },
  required: ['component', 'findings'],
}

const VERDICT_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    warranted: { type: 'boolean' },
    capabilityClaimVerified: { type: 'boolean', description: 'is the new-model capability backing this finding confirmed/likely in the profile (NOT speculative)?' },
    weakensModelIndependentGuardrail: { type: 'boolean', description: 'true if it weakens a security/safety guardrail that protects regardless of model intelligence (deny-lists, push-to-main block, secret reads)' },
    adjustedSeverity: { type: 'string', enum: ['P0', 'P1', 'P2', 'keep', 'reject'] },
    reason: { type: 'string' },
    caveats: { type: 'string' },
  },
  required: ['warranted', 'capabilityClaimVerified', 'weakensModelIndependentGuardrail', 'adjustedSeverity', 'reason', 'caveats'],
}

const REPORT_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    report: { type: 'string', description: 'the full markdown report' },
    p0: { type: 'integer' }, p1: { type: 'integer' }, p2: { type: 'integer' },
    keep: { type: 'integer' }, needsConfirmation: { type: 'integer' },
  },
  required: ['report', 'p0', 'p1', 'p2', 'keep', 'needsConfirmation'],
}

// ---- Phase 1: research sweep -------------------------------------------
phase('Research')
log(`Researching ${NEW_MODEL} capabilities and ${OLD_MODEL}→${NEW_MODEL} deltas across 5 angles`)

const RESEARCH_ANGLES = [
  `Official Anthropic announcement & docs for ${NEW_MODEL}: search anthropic.com/news, docs.claude.com (changelog, model overview, pricing, models comparison). Find release date, context window, pricing, availability, headline capability claims.`,
  `${NEW_MODEL} model card / system card: agentic capabilities, autonomy & long-horizon task reliability, instruction-following, refusal/safety behavior changes, tool-use reliability, coding/agentic benchmarks (SWE-bench, Terminal-bench, etc.). How does it compare to ${OLD_MODEL}?`,
  `Claude Code release notes / feature changes tied to ${NEW_MODEL}: fast mode, context window & automatic compaction, hooks, subagents, model selection. Anything that changes how a PROJECT (CLAUDE.md, hooks, permissions) should be configured for this model.`,
  `${OLD_MODEL} → ${NEW_MODEL} behavioral deltas (community + official): changes in reasoning depth, verbosity, over-engineering tendency, hallucination rate, instruction adherence, long-context handling, "claims done without verifying" behavior, drive-by edits.`,
  `Anthropic prompt-engineering / Claude Code best-practices for the latest Opus (4.x): does Anthropic now recommend LESS explicit reasoning scaffolding, different CLAUDE.md patterns, or different context-management given larger context windows? Any guidance on guardrails that became unnecessary or newly necessary.`,
]

const researchPrompt = (angle) => `You are researching the capabilities of a NEWLY RELEASED Claude model to help recalibrate a Claude Code project-setup tool.

${FACTS_BLOCK}

RESEARCH ANGLE:
${angle}

INSTRUCTIONS:
- Use WebSearch and WebFetch (load their schemas via ToolSearch: "select:WebSearch,WebFetch" or keyword search). Prefer official Anthropic sources (anthropic.com, docs.claude.com); then reputable analysis.
- The model was released TODAY (2026-05-28). Coverage may be thin or absent. That is fine — DO NOT FABRICATE. If you cannot find authoritative info for a claim, either omit it or mark confidence 'speculative' and say so in the source field.
- For every fact, set confidence honestly: 'confirmed' only with an official/primary source; 'likely' for reputable secondary corroboration; 'speculative' for inference. The ENVIRONMENT-CONFIRMED FACTS above are already 'confirmed' — you may restate the ones relevant to your angle but focus on adding NEW findings.
- Each fact should note its delta from ${OLD_MODEL} (or "unknown").
- Set sourcesFound=false if you found no authoritative material about ${NEW_MODEL} specifically.
Return the structured object.`

const researchResults = (await parallel(
  RESEARCH_ANGLES.map((angle, i) => () => agent(researchPrompt(angle), { label: `research:angle-${i + 1}`, schema: RESEARCH_SCHEMA }))
)).filter(Boolean)

// ---- Phase 2: consolidate into one capability profile (barrier) --------
phase('Profile')
const allFacts = researchResults.flatMap(r => r.facts || [])
const allSources = researchResults.flatMap(r => r.sources || [])
const anyAuthoritative = researchResults.some(r => r.sourcesFound)
log(`Collected ${allFacts.length} candidate facts from ${allSources.length} sources (authoritative ${NEW_MODEL} material found: ${anyAuthoritative}). Synthesizing profile.`)

const profile = await agent(
`Synthesize a single CAPABILITY PROFILE for ${NEW_MODEL}, to be used for recalibrating a Claude Code setup tool.

${FACTS_BLOCK}

RAW RESEARCH FACTS (JSON):
${JSON.stringify(allFacts, null, 2)}

SOURCES (JSON):
${JSON.stringify(allSources, null, 2)}

INSTRUCTIONS:
- Merge and de-duplicate facts. Resolve conflicts by source credibility and confidence.
- The ENVIRONMENT-CONFIRMED FACTS are the trustworthy floor — always include the decision-relevant ones (esp. the 1M context window and fast mode) as 'confirmed', even if web research was thin.
- Be brutally honest about confidence. Same-day releases have little public coverage; most behavioral deltas will be 'speculative'. List those explicitly under researchGaps so the downstream audit treats them as unverified.
- confirmedCapabilities: each item needs an implicationForSetup (one line: what it means for hooks/CLAUDE.md/permissions/skills).
- keyDeltas: the few old→new differences most likely to change how a project should be configured.
Return the structured profile.`,
  { label: 'profile:synthesize', schema: PROFILE_SCHEMA }
)

// ---- Phase 3: per-component analysis + adversarial verification --------
// Pipeline: each component analyzes, then its PROPOSED CHANGES are verified — no barrier between.
const PROFILE_JSON = JSON.stringify(profile, null, 2)

const COMPONENTS = [
  { name: 'CLAUDE_snippet: AI-tools section (skills/Ruflo/memory tables + hard rules)',
    files: [`${ROOT}/templates/CLAUDE_snippet.md`],
    focus: 'Lines 1-53: the Superpowers skill-trigger table, "run brainstorming before code / verification before done / systematic-debugging after first failed fix" hard rules, the Ruflo MCP "4+ tasks" threshold and "never auto-invoke", and basic-memory usage table. These encode assumptions about when the model needs methodology scaffolding and parallelism.' },
  { name: 'CLAUDE_snippet: Reasoning protocol (5 phases)',
    files: [`${ROOT}/templates/CLAUDE_snippet.md`],
    focus: 'Lines 55-84: the 5-phase silent reasoning protocol (clarify/challenge/explore/anticipate/meta-check). This is heavy explicit reasoning scaffolding — assess whether a stronger-reasoning model needs this much, whether it is now noise/friction, or still earns its keep.' },
  { name: 'CLAUDE_snippet: Verification protocol',
    files: [`${ROOT}/templates/CLAUDE_snippet.md`],
    focus: 'Lines 88-96: "never mark complete without proving it works; run tests; surface uncertainty". Guards the classic "claims done without testing" failure mode. Is that failure mode reduced/eliminated in the new model, or model-independent and still essential?' },
  { name: 'CLAUDE_snippet: Context management (delegate-to-subagents rule)',
    files: [`${ROOT}/templates/CLAUDE_snippet.md`],
    focus: 'Lines 100-108: "Context is your most important resource. Delegate exploration/research to subagents; if reading 3+ files, delegate." This was calibrated for ~200K context. Re-examine hard against the 1M context window of the new model — is the >3-files-delegate threshold now overly aggressive / counterproductive?' },
  { name: 'CLAUDE_snippet: Critical rules (10)',
    files: [`${ROOT}/templates/CLAUDE_snippet.md`],
    focus: 'Lines 112-124: the 10 critical rules (read-before-write, plan-first 3+ steps, minimal-impact, no drive-by dead-code deletion, right-tool-for-the-job, etc.). Which are model-independent hygiene vs. which were patches for specific 4.7 failure modes that may now be noise?' },
  { name: 'CLAUDE_template.md (full new-project template)',
    files: [`${ROOT}/templates/CLAUDE_template.md`],
    focus: 'The full template: Codebase Map guidance, "keep root file lean / push to subdir CLAUDE.md", plan-mode for 3+ steps, prefer single test files. Re-examine the "keep root lean / hierarchical CLAUDE.md" advice against the larger context window, and any model-tuned thresholds.' },
  { name: 'settings.json — permissions (allow/deny/ask)',
    files: [`${ROOT}/templates/settings.json`],
    focus: 'The allow/deny/ask permission lists. CRITICAL: most of these are SECURITY guardrails (secret reads, lockfile edits, push-to-main, install-confirmation) that protect regardless of how capable the model is. Only flag a change if a NEW model capability genuinely changes the risk calculus — and never recommend weakening a security guardrail merely because "the model is smarter".' },
  { name: 'hook: prompt-linter.sh (UserPromptSubmit)',
    files: [`${ROOT}/templates/hooks/prompt-linter.sh`],
    focus: 'Warns on long/ambiguous prompts. Does a stronger instruction-following / disambiguating model make this noise, or is it still useful user-facing guidance?' },
  { name: 'hook: websearch-year.py (PreToolUse WebSearch)',
    files: [`${ROOT}/templates/hooks/websearch-year.py`],
    focus: 'Appends the current year to temporal web searches. Tie to the new model knowledge cutoff (Jan 2026). Still valid (recency always matters), and is the hardcoded/derived year handled correctly?' },
  { name: 'hook: session-context.py (SessionStart)',
    files: [`${ROOT}/templates/hooks/session-context.py`],
    focus: 'Injects git state, sensitive files, detected quality commands at session start. Mostly model-independent, but assess whether injected context volume should change given the larger context window, and whether anything here duplicates new built-in harness context.' },
  { name: 'hook: bash-guard.py (PreToolUse Bash)',
    files: [`${ROOT}/templates/hooks/bash-guard.py`],
    focus: 'Blocks commit/push to main/master/production/release and --no-verify bypass. Model-independent safety guardrail — verify carefully before recommending any relaxation.' },
  { name: 'hook: big-file-guard.py (PreToolUse Read)',
    files: [`${ROOT}/templates/hooks/big-file-guard.py`],
    focus: 'Warns on reading files >200KB without offset/limit. This threshold was calibrated for a ~200K context window. Re-examine HARD against the 1M context window — is 200KB now too conservative? Should the threshold scale, or is the guard about cost/noise rather than context capacity?' },
  { name: 'hook: context-usage.py (Stop)',
    files: [`${ROOT}/templates/hooks/context-usage.py`],
    focus: 'Warns when the session context window passes 80% (default limit, suggests /compact). The default CONTEXT_USAGE_LIMIT was almost certainly set for a ~200K window. Re-examine HARD against 1M context: is the default limit now wrong by 5x? Does it read the real window size or assume one?' },
  { name: 'Project skills (templates/skills/*)',
    files: [`${ROOT}/templates/skills/reflect/SKILL.md`, `${ROOT}/templates/skills/skills-audit/SKILL.md`, `${ROOT}/templates/skills/skill-engineer/SKILL.md`, `${ROOT}/templates/skills/design-an-interface/SKILL.md`, `${ROOT}/templates/skills/improve-codebase-architecture/SKILL.md`],
    focus: 'The 5 bundled skills. Do any reference a specific model, model-tuned thresholds, or assumptions (e.g. parallel-subagent counts, context limits) that should be recalibrated? skill-engineer/SKILL.md is the one file grep flagged for a model reference — check it specifically.' },
  { name: 'Devcontainer + security-guidance integration',
    files: [`${ROOT}/templates/devcontainer/init-firewall.sh`, `${ROOT}/templates/devcontainer/managed-settings.json`, `${ROOT}/templates/devcontainer/devcontainer.json`],
    focus: 'Sandboxing (firewall allowlist, managed-settings blocking --dangerously-skip-permissions). Almost entirely model-independent. Only flag if a new-model capability changes the trust boundary (e.g. more autonomous agentic behavior strengthening the case for sandboxing).' },
  { name: 'Positioning: README config-staleness note + design principles + MCP stance',
    files: [`${ROOT}/README.md`, `${ROOT}/CHANGELOG.md`],
    focus: 'The README "re-audit once per model release" note (line 69), the Superpowers-vs-Ruflo separation rationale, the "prefer dispatching-parallel-agents under 4 tasks" guidance, and overall framing. Should the positioning/wording mention the new model, update parallelism thresholds, or reflect new harness features (workflows, fast mode)?' },
]

const analysisPrompt = (comp) => `You are auditing ONE component of "cc_tool", a Claude Code project-setup tool, to recalibrate it from ${OLD_MODEL} to ${NEW_MODEL}.

${FACTS_BLOCK}

CAPABILITY PROFILE for ${NEW_MODEL} (the ONLY evidence you may use for new-model behavior — do not invent capabilities beyond this):
${PROFILE_JSON}

COMPONENT TO AUDIT: ${comp.name}
FILES TO READ (read them in full with the Read tool before analyzing):
${comp.files.map(f => `  - ${f}`).join('\n')}
FOCUS: ${comp.focus}

METHOD:
1. Read the file(s). Quote exact locations (file + line range or section).
2. For each meaningful piece of guidance/config/code in this component, identify the ENCODED ASSUMPTION about model behavior, capability, or failure mode (e.g. "assumes ~200K context", "assumes the model claims done without testing", "assumes the model needs explicit reasoning steps", "assumes the model over-reads large files").
3. Classify each against the profile:
   - VALID  = keep as-is. Either model-independent (security/hygiene/user-facing) OR the failure mode it guards still exists in ${NEW_MODEL}.
   - NOISE  = now unnecessary friction/overhead because a CONFIRMED-or-LIKELY ${NEW_MODEL} capability removed the failure mode it guarded.
   - GAP    = ${NEW_MODEL} introduces a new capability or failure mode this component does NOT address.
   - OPPORTUNITY = ${NEW_MODEL} enables a meaningfully better approach.
4. CRITICAL ANTI-HALLUCINATION RULE: default to VALID. Only assign NOISE/GAP/OPPORTUNITY when a SPECIFIC profile fact with confidence 'confirmed' or 'likely' supports it — name that fact in tiedCapability. If the only support is 'speculative' or "the model is generally smarter now", you MUST classify VALID and may note the speculation in rationale, but NOT propose a change. NEVER recommend weakening a security/safety guardrail because the model is more capable.
5. For each non-VALID finding, give a concrete proposedChange (exact replacement wording or a precise diff description). Set severity P0 (clearly wrong/harmful for ${NEW_MODEL}), P1 (meaningful improvement), P2 (minor/polish). VALID findings get severity 'keep'.

Return up to ~6 of the most significant findings. Quality over quantity.`

const verifyPrompt = (f) => `You are an adversarial reviewer. Try to REFUTE a proposed change to the "cc_tool" Claude Code setup. Default to rejecting unless the change is clearly justified.

${FACTS_BLOCK}

CAPABILITY PROFILE for ${NEW_MODEL}:
${PROFILE_JSON}

PROPOSED FINDING (JSON):
${JSON.stringify(f, null, 2)}

Check, in order:
1. capabilityClaimVerified: Is the ${NEW_MODEL} capability this finding relies on actually 'confirmed' or 'likely' in the profile (NOT 'speculative', NOT in researchGaps, NOT a vibe like "smarter now")? If the supporting capability is unverified, this is FALSE.
2. weakensModelIndependentGuardrail: Would applying this weaken a guardrail that protects regardless of model intelligence — security deny-lists, secret-read blocks, push-to-main/--no-verify blocks, install-confirmation, sandboxing? If so, this is TRUE and the change should almost always be rejected.
3. Would the change introduce a NEW failure mode, or remove a guardrail still useful for the human user (not just the model)?
4. Is the proposedChange concrete and correct?

DECISION: warranted=true only if capabilityClaimVerified=true AND weakensModelIndependentGuardrail=false AND no new failure mode. Otherwise warranted=false and adjustedSeverity='reject'. If warranted but mis-prioritized, adjust severity. Be specific in reason and caveats.`

log(`Analyzing ${COMPONENTS.length} setup components and adversarially verifying every proposed change`)
const perComponent = await pipeline(
  COMPONENTS,
  (comp) => agent(analysisPrompt(comp), { label: `analyze:${comp.name.slice(0, 40)}`, phase: 'Analyze', schema: ANALYSIS_SCHEMA }),
  (analysis, comp) => {
    const findings = (analysis && analysis.findings) || []
    const changes = findings.filter(f => f.classification !== 'VALID')
    const keeps = findings.filter(f => f.classification === 'VALID').map(f => ({
      ...f, verdict: { warranted: true, capabilityClaimVerified: true, weakensModelIndependentGuardrail: false, adjustedSeverity: 'keep', reason: 'Leave as-is; no change proposed.', caveats: '' },
    }))
    return parallel(changes.map(f => () =>
      agent(verifyPrompt(f), { label: `verify:${(f.title || '').slice(0, 36)}`, phase: 'Verify', schema: VERDICT_SCHEMA })
        .then(v => ({ ...f, verdict: v }))
    )).then(verified => ({ component: comp.name, findings: [...verified.filter(Boolean), ...keeps] }))
  }
)

// ---- Phase 4: synthesis (barrier — needs all findings together) --------
phase('Synthesize')
const allFindings = perComponent.filter(Boolean).flatMap(c => (c.findings || []).map(f => ({ component: c.component, ...f })))
const survivingChanges = allFindings.filter(f => f.classification !== 'VALID' && f.verdict && f.verdict.warranted && f.verdict.adjustedSeverity !== 'reject')
const rejected = allFindings.filter(f => f.classification !== 'VALID' && (!f.verdict || !f.verdict.warranted || f.verdict.adjustedSeverity === 'reject'))
const keeps = allFindings.filter(f => f.classification === 'VALID')
log(`Findings: ${survivingChanges.length} verified changes, ${rejected.length} rejected (unverified/guardrail), ${keeps.length} keep-as-is. Writing report.`)

const result = await agent(
`Write the final recalibration report (GitHub-flavored markdown) for migrating the "cc_tool" Claude Code setup from ${OLD_MODEL} to ${NEW_MODEL}.

${FACTS_BLOCK}

CAPABILITY PROFILE:
${PROFILE_JSON}

VERIFIED CHANGES (passed adversarial review — JSON):
${JSON.stringify(survivingChanges, null, 2)}

REJECTED PROPOSALS (failed review: unverified capability or would weaken a guardrail — JSON):
${JSON.stringify(rejected, null, 2)}

KEEP-AS-IS (model-independent or failure mode still present — JSON):
${JSON.stringify(keeps, null, 2)}

Write the report with these sections:
1. "# cc_tool recalibration: ${OLD_MODEL} → ${NEW_MODEL}" + a 1-paragraph executive summary.
2. "## What actually changed in ${NEW_MODEL} that matters here" — bullet the decision-relevant capabilities with confidence tags. Be explicit that this model released 2026-05-28 (after the auditing model's Jan-2026 cutoff), so behavioral deltas rest on the profile, not memory.
3. "## Recommended changes" — grouped P0 → P1 → P2. Each: component, file+location, the change (with concrete proposed wording/diff), the confirmed/likely capability it rests on, and the risk of NOT doing it. The 1M-context-window recalibration of context-usage.py default, big-file-guard threshold, and the delegate-to-subagents rule should feature prominently IF they survived verification.
4. "## Leave as-is (deliberately not changing)" — the keep-list, with one-line reasons. Emphasize security/safety guardrails are model-independent.
5. "## Needs confirmation before acting" — anything resting on speculative facts / researchGaps. Phrase as questions to resolve against ${NEW_MODEL} release notes once available.
6. "## Suggested CHANGELOG entry" — a ready-to-paste v0.0.5 entry in the existing CHANGELOG.md style summarizing the recalibration.
7. "## How to re-run this audit" — note it was produced by the 'model-recalibration-audit' workflow, re-runnable per model release with args {newModel, oldModel}.

Be precise and honest. Do not overstate confidence. If a section is empty, say so explicitly rather than padding.
Return: report (full markdown), and counts p0/p1/p2 (verified changes by adjusted severity), keep (keep-list size), needsConfirmation (count of items in the Needs-confirmation section).`,
  { label: 'synthesize:report', schema: REPORT_SCHEMA }
)

return {
  profile,
  counts: { verifiedChanges: survivingChanges.length, rejected: rejected.length, keeps: keeps.length, ...{ p0: result.p0, p1: result.p1, p2: result.p2, needsConfirmation: result.needsConfirmation } },
  anyAuthoritativeResearch: anyAuthoritative,
  report: result.report,
}