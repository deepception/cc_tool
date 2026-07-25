export const meta = {
  name: 'model-recalibration-audit',
  description: 'Audit the cc_tool setup against a newer Claude model and produce prioritized, evidence-verified recalibration recommendations',
  whenToUse: 'Run once per Claude model release to re-audit hooks, permissions, the managed CLAUDE.md block, skills, and installer scripts against the new model\'s capabilities. Fully parameterized: pass args {newModel, oldModel, newModelId?, releaseDate?, confirmedFacts?, root?}.',
  phases: [
    { title: 'Census', detail: 'One agent inventories the repo (sections, hooks, skills, bin, config, docs) so coverage needs no hardcoded file list' },
    { title: 'Research', detail: 'claude-api skill first, then a multi-angle web sweep for the new model and old→new deltas' },
    { title: 'Profile', detail: 'Synthesize a single capability profile with confidence levels, surfaces, and research gaps' },
    { title: 'Analyze', detail: 'One agent per inventoried component: encoded model assumptions, anchor-located, root-cause swept' },
    { title: 'Verify', detail: 'Adversarially verify every proposed change: capability, surface, guardrail, and scope completeness' },
    { title: 'Synthesize', detail: 'Prioritized report written to docs/: P0/P1/P2, keep-list, needs-confirmation, coverage, CHANGELOG entry' },
  ],
}

// ---- config (fully parameterized per model release) ---------------------
// args may arrive as an object OR as a JSON-encoded string (some callers
// stringify, and a few double-encode) — parse defensively; a silent
// fall-through to defaults once caused a full audit to run against the wrong
// model pair. Do not regress this.
let cfg = {}
if (typeof args === 'string') {
  try { cfg = JSON.parse(args) } catch (e) { cfg = {} }
} else if (args && typeof args === 'object' && !Array.isArray(args)) {
  cfg = args
}
if (typeof cfg === 'string') {           // double-encoded string payload
  try { cfg = JSON.parse(cfg) } catch (e) { cfg = {} }
}
if (!cfg || typeof cfg !== 'object' || Array.isArray(cfg)) cfg = {}

const str = (v) => (typeof v === 'string' ? v.trim() : '')
const NEW_MODEL = str(cfg.newModel)
const OLD_MODEL = str(cfg.oldModel)

// No model facts are baked in. An audit run against the wrong or an assumed
// model pair is worse than no audit, so refuse rather than default.
if (!NEW_MODEL || !OLD_MODEL) {
  log('ABORT: this workflow carries NO built-in model facts by design. Pass args as a JSON object with at least newModel and oldModel — shape: {"newModel":"<display name of the model being migrated TO>","oldModel":"<display name of the model being migrated FROM>","newModelId":"<exact api model id, optional>","releaseDate":"<YYYY-MM-DD, optional>","confirmedFacts":["<environment-authoritative fact>", "..."],"root":"<repo path, optional>"}. Everything else is discovered at runtime: the repo inventory by the Census agent, the model facts by the claude-api skill.')
  return { error: 'Missing required args: newModel and oldModel. Nothing was spawned.', argsSeen: typeof args }
}

const NEW_MODEL_ID = str(cfg.newModelId)
const RELEASE_DATE = str(cfg.releaseDate)
// Pass args.root to audit a checkout elsewhere; the default assumes the
// workflow runs from inside the cc_tool repo it audits.
const ROOT = str(cfg.root) || '.'
const SKILLS_PER_AGENT = cfg.skillsPerAgent || 3
const BIN_PER_AGENT = cfg.binPerAgent || 4
const MAX_COMPONENTS = cfg.maxComponents || 40

// Optional caller-supplied facts, authoritative for THIS environment (e.g. read
// off the running harness / system prompt). Absent is fine — the agents then
// derive everything from the claude-api skill and research.
const rawFacts = cfg.confirmedFacts
const CONFIRMED_FACTS = Array.isArray(rawFacts)
  ? rawFacts.filter(f => str(f)).map(f => str(f))
  : (str(rawFacts) ? [str(rawFacts)] : [])

const FACTS_BLOCK = `MODELS UNDER AUDIT
  new      = ${NEW_MODEL}${NEW_MODEL_ID ? ` (model id '${NEW_MODEL_ID}')` : ' (exact model id NOT supplied — resolve it via the claude-api skill, do not invent one)'}
  previous = ${OLD_MODEL}
  release  = ${RELEASE_DATE || 'not supplied — establish it via the claude-api skill / official docs, do not guess'}

AUTHORITATIVE MODEL FACTS: invoke the 'claude-api' skill (Skill tool, skill name "claude-api") whenever you need a model id, context window, max output, pricing, effort/thinking semantics, refusal or stop_reason behavior, or a per-model migration guide. It is the non-stale source in this environment. Your own priors about ${NEW_MODEL} may predate it entirely — treat memory as a hypothesis, never as evidence. Web search supplements it for harness-level (Claude Code) facts it does not cover.
${CONFIRMED_FACTS.length ? `\nCALLER-CONFIRMED FACTS (authoritative for this environment; do not contradict):\n${CONFIRMED_FACTS.map((f, i) => `  ${i + 1}. ${f}`).join('\n')}` : '\nNo caller-confirmed facts were supplied: establish every model fact yourself and mark confidence honestly.'}`

const SURFACE_RULES = `SURFACE DISCIPLINE (a claim is only true of the surface it was measured on):
  api         — Messages API / SDK: parameters, model ids, pricing, token limits, stop_reason/refusal behavior
  claude-code — the CLI harness: slash commands (/model, /effort, /fast), settings.json, hooks, subagents, workflows, harness version gates
  model       — intrinsic model behavior: reasoning depth, verbosity, instruction adherence, delegation habits, failure modes
  cc_tool     — this repo's own conventions and thresholds
Identify the surface of a claim BEFORE asserting it, and never transfer a claim across surfaces without evidence for the TARGET surface. An API parameter may have no harness equivalent, and the same-named feature can support different model sets on each surface. Worked example from a prior run: a finding applied the API-level 'speed: "fast"' support matrix to a harness-level '/fast' documentation line — different surfaces, different supported models, wrong finding.`

const SIBLING_RULE = `ROOT-CAUSE SWEEP (mandatory — do not skip, do not answer from memory):
Before you write down a finding, go and check whether the SAME root cause hits sibling cases. Grep/read for the pattern across the whole file, its sibling files, and any other place that repeats the construct — every model id in the same lookup table, every branch of the same conditional, every doc line stating the same rule. Enumerate EVERY affected case in siblingCases and say in siblingSweepMethod exactly how you checked. A fix that names one instance of an N-instance defect is an under-scoped fix and will be downgraded on review. Worked example from a prior run: an audit correctly found one model mis-mapped in a hook's model→context-window lookup and missed five other models mis-mapped by the identical code path.`

// ---- schemas ------------------------------------------------------------
const CENSUS_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    snippetSections: { type: 'array', items: { type: 'string' }, description: 'level-2 heading texts inside the managed block, in order, without the leading "## "' },
    hookFiles: { type: 'array', items: { type: 'string' } },
    skillDirs: { type: 'array', items: { type: 'string' } },
    binScripts: { type: 'array', items: { type: 'string' } },
    configFiles: { type: 'array', items: { type: 'string' } },
    templateDocs: { type: 'array', items: { type: 'string' } },
    positioningDocs: { type: 'array', items: { type: 'string' } },
    uncovered: { type: 'array', items: { type: 'string' }, description: 'anything a setup audit should look at that none of the other lists captured' },
    notes: { type: 'string' },
  },
  required: ['snippetSections', 'hookFiles', 'skillDirs', 'binScripts', 'configFiles', 'templateDocs', 'positioningDocs', 'uncovered', 'notes'],
}

const RESEARCH_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    angle: { type: 'string' },
    sourcesFound: { type: 'boolean', description: 'true only if authoritative info about the NEW model was actually located' },
    sources: { type: 'array', items: { type: 'object', additionalProperties: false,
      properties: { url: { type: 'string', description: 'URL, or "skill:claude-api" when the fact came from that skill' }, title: { type: 'string' },
        credibility: { type: 'string', enum: ['official', 'reputable', 'community', 'unknown'] } },
      required: ['url', 'title', 'credibility'] } },
    facts: { type: 'array', items: { type: 'object', additionalProperties: false, properties: {
      claim: { type: 'string' },
      surface: { type: 'string', enum: ['api', 'claude-code', 'model', 'cc_tool'] },
      category: { type: 'string', enum: ['context-window', 'reasoning', 'agentic-autonomy', 'tool-use', 'instruction-following', 'speed-cost', 'safety-refusal', 'claude-code-feature', 'prompting-guidance', 'other'] },
      confidence: { type: 'string', enum: ['confirmed', 'likely', 'speculative'] },
      deltaFromOld: { type: 'string', description: 'how it differs from the previous model, or "unknown"' },
      source: { type: 'string' },
    }, required: ['claim', 'surface', 'category', 'confidence', 'deltaFromOld', 'source'] } },
  },
  required: ['angle', 'sourcesFound', 'sources', 'facts'],
}

const PROFILE_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    summary: { type: 'string' },
    confirmedCapabilities: { type: 'array', items: { type: 'object', additionalProperties: false, properties: {
      capability: { type: 'string' },
      surface: { type: 'string', enum: ['api', 'claude-code', 'model', 'cc_tool'] },
      confidence: { type: 'string', enum: ['confirmed', 'likely', 'speculative'] },
      implicationForSetup: { type: 'string' },
    }, required: ['capability', 'surface', 'confidence', 'implicationForSetup'] } },
    keyDeltas: { type: 'array', items: { type: 'string' }, description: 'most decision-relevant old→new deltas' },
    researchGaps: { type: 'array', items: { type: 'string' }, description: 'capability questions we could NOT verify from sources' },
  },
  required: ['summary', 'confirmedCapabilities', 'keyDeltas', 'researchGaps'],
}

const ANALYSIS_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    component: { type: 'string' },
    targetsRead: { type: 'array', items: { type: 'string' }, description: 'every file you actually opened — coverage evidence' },
    findings: { type: 'array', items: { type: 'object', additionalProperties: false, properties: {
      title: { type: 'string' },
      location: { type: 'string', description: 'file + ANCHOR: heading text, marker comment, function name, or an exact quoted phrase. Line numbers are optional and secondary — never the only locator.' },
      encodedAssumption: { type: 'string', description: 'the model-behavior/capability/failure-mode assumption this component encodes' },
      surface: { type: 'string', enum: ['api', 'claude-code', 'model', 'cc_tool', 'mixed'] },
      surfaceEvidence: { type: 'string', description: 'what establishes this claim ON THAT surface (not on a neighbouring one)' },
      siblingCases: { type: 'array', items: { type: 'string' }, description: 'every OTHER location the same root cause affects, each as file + anchor. Empty only if you checked and there are none.' },
      siblingSweepMethod: { type: 'string', description: 'how you checked for siblings (the grep/glob/read you actually ran), or "n/a — prose-only finding with no repeatable construct"' },
      classification: { type: 'string', enum: ['VALID', 'NOISE', 'GAP', 'OPPORTUNITY'] },
      tiedCapability: { type: 'string', description: 'the specific profile fact this maps to, or "none"' },
      rationale: { type: 'string' },
      recommendation: { type: 'string' },
      proposedChange: { type: 'string', description: 'concrete wording/diff covering ALL sibling cases, or "n/a — keep"' },
      confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
      severity: { type: 'string', enum: ['P0', 'P1', 'P2', 'keep'] },
    }, required: ['title', 'location', 'encodedAssumption', 'surface', 'surfaceEvidence', 'siblingCases', 'siblingSweepMethod', 'classification', 'tiedCapability', 'rationale', 'recommendation', 'proposedChange', 'confidence', 'severity'] } },
  },
  required: ['component', 'targetsRead', 'findings'],
}

const VERDICT_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    warranted: { type: 'boolean' },
    capabilityClaimVerified: { type: 'boolean', description: 'is the new-model capability backing this finding confirmed/likely in the profile (NOT speculative)?' },
    surfaceVerified: { type: 'boolean', description: 'does the evidence belong to the SAME surface (api / claude-code / model / cc_tool) as the thing being changed?' },
    weakensModelIndependentGuardrail: { type: 'boolean', description: 'true if it weakens a security/safety guardrail that protects regardless of model intelligence (deny-lists, push-to-main block, secret reads, sandboxing)' },
    scopeComplete: { type: 'boolean', description: 'false if you found sibling cases of the same root cause that the proposedChange does not cover' },
    missedSiblings: { type: 'string', description: 'the sibling cases you found that the finding missed (file + anchor each), or "none — swept and none found"' },
    scopeAdjustment: { type: 'string', description: 'what must be ADDED to the proposedChange to cover every sibling case, or "n/a"' },
    adjustedSeverity: { type: 'string', enum: ['P0', 'P1', 'P2', 'keep', 'reject'] },
    reason: { type: 'string' },
    caveats: { type: 'string' },
  },
  required: ['warranted', 'capabilityClaimVerified', 'surfaceVerified', 'weakensModelIndependentGuardrail', 'scopeComplete', 'missedSiblings', 'scopeAdjustment', 'adjustedSeverity', 'reason', 'caveats'],
}

const REPORT_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    report: { type: 'string', description: 'the full markdown report' },
    reportPath: { type: 'string', description: 'absolute path of the markdown file you wrote under docs/' },
    p0: { type: 'integer' }, p1: { type: 'integer' }, p2: { type: 'integer' },
    keep: { type: 'integer' }, needsConfirmation: { type: 'integer' },
  },
  required: ['report', 'reportPath', 'p0', 'p1', 'p2', 'keep', 'needsConfirmation'],
}

// ---- Phase 1: census — the only step allowed to enumerate the repo ------
// Workflow scripts have no filesystem API, so every file list this audit fans
// out over is produced HERE, by an agent that can ls/glob. Nothing downstream
// hardcodes a filename: add a skill or a hook next month and it gets audited
// without editing this script.
phase('Census')
log(`Inventorying ${ROOT} so audit coverage is derived from the repo, not from a hardcoded list`)

const census = await agent(
`You are taking an INVENTORY of the "cc_tool" repository (a Claude Code project-setup tool) so a recalibration audit can fan out over every component. You are the ONLY step in this workflow that can enumerate the filesystem — the workflow script itself has no filesystem access, so anything you miss is never audited. Be exhaustive.

REPO ROOT: ${ROOT}

Use Bash (ls, find) and Read. Every path you return must exist — verify, do not guess.

1. snippetSections — open ${ROOT}/templates/CLAUDE_snippet.md and list, IN ORDER, the exact text of every level-2 heading (a line starting with "## ") that sits between the "cc_tool:snippet:start" and "cc_tool:snippet:end" HTML marker comments. Strip the leading "## ". Do NOT return line numbers.
2. hookFiles — every hook script directly inside ${ROOT}/templates/hooks, whatever the extension. EXCLUDE __pycache__, *.pyc, and any other build artifact.
3. skillDirs — every skill directory directly inside ${ROOT}/templates/skills (the directory path itself, one entry per skill, not the SKILL.md inside it).
4. binScripts — every script directly inside ${ROOT}/bin.
5. configFiles — machine-readable config and sandboxing templates: the settings template, everything under ${ROOT}/templates/devcontainer, and the repo's install script. Return only paths that exist.
6. templateDocs — the remaining human-readable templates that are NOT CLAUDE_snippet.md (e.g. the new-project CLAUDE template, anything under ${ROOT}/templates/vault).
7. positioningDocs — the top-level docs that describe the setup to users (README, CHANGELOG, and any sibling).
8. uncovered — any other file or directory under ${ROOT}/templates, ${ROOT}/bin, or the repo root that a model-recalibration audit ought to look at but that lists 1-7 did not capture. This list is the audit's coverage alarm: if the repo grew a new KIND of component, name it here rather than dropping it.
9. notes — anything surprising: paths referenced by the repo but missing, build artifacts, a component category that changed shape, or a file that looks like a leftover of something deleted.

Return absolute paths. Return the structured object.`,
  { label: 'census:inventory', schema: CENSUS_SCHEMA }
)

// ---- build the audit surface from the census (no hardcoded file lists) --
const uniq = (xs) => Array.from(new Set((Array.isArray(xs) ? xs : []).filter(x => typeof x === 'string' && x.trim()).map(x => x.trim())))
const chunk = (xs, n) => { const out = []; for (let i = 0; i < xs.length; i += n) out.push(xs.slice(i, i + n)); return out }

const inv = census && typeof census === 'object' ? census : {}
const sections = uniq(inv.snippetSections)
const hookFiles = uniq(inv.hookFiles)
const skillDirs = uniq(inv.skillDirs)
const binScripts = uniq(inv.binScripts)
const configFiles = uniq(inv.configFiles)
const templateDocs = uniq(inv.templateDocs)
const positioningDocs = uniq(inv.positioningDocs)
const uncovered = uniq(inv.uncovered)

const SNIPPET_FILE = `${ROOT}/templates/CLAUDE_snippet.md`
const HOOKS_DIR = `${ROOT}/templates/hooks`
const SKILLS_DIR = `${ROOT}/templates/skills`
const BIN_DIR = `${ROOT}/bin`

const SNIPPET_FOCUS = `This is guidance injected verbatim into every project's CLAUDE.md (the cc_tool-managed block). Ask: what does this section assume about the model — context window, reasoning depth, verbosity, delegation behavior, effort semantics, refusal behavior, or a failure mode (claims-done-without-testing, over-engineering, fabrication, drive-by edits)? Does it name a model, model id, price, numeric threshold, harness version, or slash command that the new model or current harness changes? Is it now redundant with behavior the new model has natively, or missing a rule the new model newly needs? Check every model name/id/price/command against the claude-api skill AND against the correct surface.`
const HOOK_FOCUS = `A hook runs deterministically on every matching session or tool call, so a wrong assumption here fires constantly and silently. Check, in order: (1) Does it hardcode or pattern-match MODEL IDS, context-window sizes, token budgets, or prices? If so, enumerate EVERY model the code branches on and decide each one — allowlists silently mis-handle every model they were never taught, and exactly this defect has shipped in this repo before. Check that variant suffixes (e.g. a bracketed context variant) and cloud-provider-prefixed ids still match. (2) Does it guard a model-DEPENDENT failure mode (over-reading, sloppy prompts, stale-year searches) or a model-INDEPENDENT one (git safety, secret access)? Model-independent guards stay regardless of how capable the model is. (3) Does it duplicate something the harness now injects natively? (4) Do its thresholds still fit the new model's context window and pricing? Also confirm the hook is actually wired up — find its registration in the repo's settings template yourself (locate the file, do not assume a filename).`
const SKILL_FOCUS = `Project skills are instructions the model reads at invocation time. Ask: does the skill name a specific model, model id, price, effort level, context limit, subagent/parallelism count, or harness version that the new model or current harness changes? Is it over-scaffolded for the new model (prescriptive step-by-step where a brief instruction now suffices)? Only claim over-scaffolding from a CONFIRMED or LIKELY profile fact — never from "the model is smarter now". Does it encode a failure mode the new model no longer exhibits, or miss one it newly exhibits? Does its guidance still match features that actually exist today on the surface it names?`
const BIN_FOCUS = `These are the installer/scaffolder scripts: deterministic shell, mostly model-independent. Flag ONLY: model names/ids, prices, context sizes, or harness version gates written into the script or into text it writes into a user's project; guidance strings it injects that now duplicate or contradict the managed CLAUDE.md block; and anything it installs that a new model/harness feature made redundant or newly necessary. Do NOT propose stylistic shell refactors — out of scope for a model recalibration.`
const CONFIG_FOCUS = `Permission lists, sandboxing, and container config. CRITICAL: most of this is SECURITY guardrail (secret reads, lockfile edits, push-to-main, install confirmation, firewall allowlist, blocking permission-skip flags) that protects regardless of how capable the model is. Only flag a change if a NEW model capability genuinely changes the risk calculus — never recommend weakening a security guardrail because "the model is smarter". Do check the mechanical side: does the config still reference files that exist, is every hook it registers present on disk, and does any model id / setting name in it match the current harness?`
const TEMPLATE_DOCS_FOCUS = `Templates a user's project inherits. Re-examine model-tuned advice: hierarchical vs flat CLAUDE.md against the current context window, plan-mode and file-count thresholds, test-file conventions, and any loop/automation contract that names a model, cadence, or price. Distinguish guidance that is genuinely model-tuned from house style that is model-independent.`
const POSITIONING_FOCUS = `User-facing positioning: the config-staleness note, model-routing and pricing statements, design principles, orchestration guidance, and the directory/feature listings. Check every factual model claim (ids, prices, context, availability, fallback targets, slash commands, harness version gates) against the claude-api skill and against the right surface, and check whether the listings still match what the repo actually contains (the census result is in your prompt — compare it).`

let components = []

if (sections.length) {
  for (const s of sections) {
    components.push({
      name: `CLAUDE_snippet section: ${s}`,
      targets: [SNIPPET_FILE],
      anchor: `the section headed '## ${s}' inside the cc_tool-managed block. Find it by that heading text; the section runs to the next '## ' heading or the closing marker.`,
      focus: SNIPPET_FOCUS,
      maxFindings: 4,
    })
  }
} else {
  components.push({
    name: 'CLAUDE_snippet (all managed sections — census fallback)',
    targets: [SNIPPET_FILE],
    anchor: `EVERY '## ' heading between the cc_tool:snippet:start and cc_tool:snippet:end markers. Enumerate them yourself first, then audit each in turn — do not skip any.`,
    focus: SNIPPET_FOCUS,
    maxFindings: 10,
  })
}

if (hookFiles.length) {
  for (const h of hookFiles) {
    components.push({ name: `hook: ${h.split('/').pop()}`, targets: [h], anchor: 'the whole file, plus its registration in the settings template', focus: HOOK_FOCUS, maxFindings: 4 })
  }
} else {
  components.push({ name: 'hooks (whole directory — census fallback)', targets: [HOOKS_DIR], anchor: 'list this directory yourself (ignore __pycache__ and *.pyc) and audit EVERY hook script in it', focus: HOOK_FOCUS, maxFindings: 10 })
}

if (skillDirs.length) {
  for (const group of chunk(skillDirs, SKILLS_PER_AGENT)) {
    components.push({
      name: `skills: ${group.map(d => d.split('/').pop()).join(', ')}`,
      targets: group,
      anchor: 'for each directory: SKILL.md in full, plus every file under its references/ if present',
      focus: SKILL_FOCUS,
      maxFindings: 4,
    })
  }
} else {
  components.push({ name: 'project skills (whole directory — census fallback)', targets: [SKILLS_DIR], anchor: 'list this directory yourself and audit EVERY skill in it (SKILL.md plus references/)', focus: SKILL_FOCUS, maxFindings: 10 })
}

if (binScripts.length) {
  for (const group of chunk(binScripts, BIN_PER_AGENT)) {
    components.push({ name: `bin scripts: ${group.map(f => f.split('/').pop()).join(', ')}`, targets: group, anchor: 'each script in full', focus: BIN_FOCUS, maxFindings: 3 })
  }
} else {
  components.push({ name: 'bin scripts (whole directory — census fallback)', targets: [BIN_DIR], anchor: 'list this directory yourself and audit EVERY script in it', focus: BIN_FOCUS, maxFindings: 6 })
}

if (configFiles.length) components.push({ name: 'config: settings + devcontainer + installer', targets: configFiles, anchor: 'permission allow/deny/ask lists, hook registrations, firewall allowlist, managed-settings restrictions', focus: CONFIG_FOCUS, maxFindings: 5 })
if (templateDocs.length) components.push({ name: 'project templates (CLAUDE template, vault seeds)', targets: templateDocs, anchor: 'each file by its headings', focus: TEMPLATE_DOCS_FOCUS, maxFindings: 4 })
if (positioningDocs.length) components.push({ name: 'positioning: README + CHANGELOG', targets: positioningDocs, anchor: 'locate each claim by its heading or an exact quoted phrase; CHANGELOG only for the latest entries and the version number', focus: POSITIONING_FOCUS, maxFindings: 5 })
if (uncovered.length) components.push({ name: 'census-flagged uncovered components', targets: uncovered, anchor: 'each path in full; if a path is a directory, list it and read what is inside', focus: `The census flagged these as components no other audit arm covers. Work out what each one is, then apply the same question to it: what does it assume about the model, and does the new model change that? ${SKILL_FOCUS}`, maxFindings: 4 })

let droppedComponents = []
if (components.length > MAX_COMPONENTS) {
  droppedComponents = components.slice(MAX_COMPONENTS).map(c => c.name)
  components = components.slice(0, MAX_COMPONENTS)
  log(`WARNING: census produced more components than maxComponents (${MAX_COMPONENTS}). NOT audited this run: ${droppedComponents.join(' | ')}. Raise args.maxComponents or args.skillsPerAgent/binPerAgent to cover them.`)
}
log(`Census: ${sections.length} snippet sections, ${hookFiles.length} hooks, ${skillDirs.length} skills, ${binScripts.length} bin scripts, ${configFiles.length} config files, ${uncovered.length} flagged-uncovered → ${components.length} audit components`)
if (str(inv.notes)) log(`Census notes: ${str(inv.notes)}`)

// ---- Phase 2: research sweep -------------------------------------------
phase('Research')
log(`Researching ${NEW_MODEL} capabilities and ${OLD_MODEL}→${NEW_MODEL} deltas across 5 angles (claude-api skill first)`)

const RESEARCH_ANGLES = [
  `Ground truth from the 'claude-api' skill FIRST: exact model id(s) and any variant suffixes for ${NEW_MODEL}, context window, max output, pricing, effort/thinking semantics, refusal and stop_reason behavior, deprecation status of ${OLD_MODEL}, and the migration guide for this model. Then confirm/extend against official docs. Tag each fact's surface.`,
  `Official announcement, model card, and system card for ${NEW_MODEL}: agentic capability, long-horizon autonomy, instruction-following, tool-use reliability, refusal/safety behavior, coding benchmarks. How does each compare to ${OLD_MODEL}? Where does ${NEW_MODEL} sit in the current lineup — is there a tier above it, and which model is the documented fallback when it refuses?`,
  `Claude Code HARNESS changes tied to ${NEW_MODEL} (surface = claude-code, do not mix with API facts): model selection and fallback chains, slash commands (/model, /effort, /fast), context compaction, hooks, subagents, workflows, version gates. Which harness features support which models — state the support matrix per surface explicitly.`,
  `${OLD_MODEL} → ${NEW_MODEL} behavioral deltas (surface = model): reasoning depth, verbosity and output length, over-engineering, hallucination/fabrication rate, instruction adherence, long-context handling, delegation and subagent habits, "claims done without verifying", unrequested drive-by edits, early stopping.`,
  `Current Anthropic prompt-engineering and Claude Code best practice for ${NEW_MODEL}: is less explicit reasoning scaffolding now recommended, are skills/CLAUDE.md written for older models now too prescriptive, and what does official guidance say about context management, verification, and guardrails that became unnecessary or newly necessary?`,
]

const researchPrompt = (angle) => `You are researching a Claude model to help recalibrate "cc_tool", a Claude Code project-setup tool.

${FACTS_BLOCK}

${SURFACE_RULES}

RESEARCH ANGLE:
${angle}

INSTRUCTIONS:
- Start with the 'claude-api' skill (Skill tool) for anything it covers — model ids, context windows, output limits, pricing, effort/thinking, refusals, migration guides. Cite it as source "skill:claude-api", credibility 'official'. Do NOT answer those from memory and do NOT prefer a web result over it.
- Then use WebSearch and WebFetch (load their schemas via ToolSearch: "select:WebSearch,WebFetch") for what the skill does not cover — harness behavior, community-observed deltas. Prefer official Anthropic sources, then reputable analysis.
- ${NEW_MODEL} may postdate your training data entirely. Coverage may be thin. DO NOT FABRICATE: omit a claim, or mark it 'speculative' and say so in the source field.
- Set confidence honestly: 'confirmed' only from an official/primary source (the claude-api skill counts); 'likely' for reputable secondary corroboration; 'speculative' for inference.
- Tag every fact with its surface. A fact about an API parameter is not a fact about a slash command.
- Each fact should note its delta from ${OLD_MODEL} (or "unknown").
- Set sourcesFound=false if you found no authoritative material about ${NEW_MODEL} specifically.
Return the structured object.`

const researchResults = (await parallel(
  RESEARCH_ANGLES.map((angle, i) => () => agent(researchPrompt(angle), { label: `research:angle-${i + 1}`, schema: RESEARCH_SCHEMA }))
)).filter(Boolean)

// ---- Phase 3: consolidate into one capability profile (barrier) --------
phase('Profile')
const allFacts = researchResults.flatMap(r => r.facts || [])
const allSources = researchResults.flatMap(r => r.sources || [])
const anyAuthoritative = researchResults.some(r => r.sourcesFound)
log(`Collected ${allFacts.length} candidate facts from ${allSources.length} sources (authoritative ${NEW_MODEL} material found: ${anyAuthoritative}). Synthesizing profile.`)

const profile = await agent(
`Synthesize a single CAPABILITY PROFILE for ${NEW_MODEL}, to be used for recalibrating a Claude Code setup tool.

${FACTS_BLOCK}

${SURFACE_RULES}

RAW RESEARCH FACTS (JSON):
${JSON.stringify(allFacts, null, 2)}

SOURCES (JSON):
${JSON.stringify(allSources, null, 2)}

INSTRUCTIONS:
- Merge and de-duplicate. Resolve conflicts by source credibility and confidence; the claude-api skill outranks web sources for anything it covers — re-invoke it yourself to settle a conflict or fill a hole.
- Keep the surface tag on every capability. If two facts disagree only because they describe different surfaces, keep BOTH and say so — that is a support-matrix difference, not a conflict.
- Be brutally honest about confidence. A recent release has little public coverage; most behavioral deltas will be 'speculative'. List those under researchGaps so the downstream audit treats them as unverified.
- confirmedCapabilities: each item needs an implicationForSetup (one line: what it means for hooks / the managed CLAUDE.md block / permissions / skills / installers).
- keyDeltas: the few old→new differences most likely to change how a project should be configured.
Return the structured profile.`,
  { label: 'profile:synthesize', schema: PROFILE_SCHEMA }
)

// ---- Phase 4: per-component analysis + adversarial verification --------
// Pipeline: each component analyzes, then its PROPOSED CHANGES are verified — no barrier between.
const PROFILE_JSON = JSON.stringify(profile, null, 2)

const analysisPrompt = (comp) => `You are auditing ONE component of "cc_tool", a Claude Code project-setup tool, to recalibrate it from ${OLD_MODEL} to ${NEW_MODEL}.

${FACTS_BLOCK}

${SURFACE_RULES}

${SIBLING_RULE}

CAPABILITY PROFILE for ${NEW_MODEL} (the primary evidence for new-model behavior — do not invent capabilities beyond it; if you need a hard fact it lacks, invoke the 'claude-api' skill rather than guessing):
${PROFILE_JSON}

COMPONENT TO AUDIT: ${comp.name}
TARGETS (read each one in full before analyzing; if a target is a directory, list it and read what is inside):
${comp.targets.map(f => `  - ${f}`).join('\n')}
WHERE TO LOOK: ${comp.anchor}
FOCUS: ${comp.focus}

METHOD:
1. Read the targets. Never analyze a file you have not opened this run.
2. Locate every finding by ANCHOR — heading text, marker comment, function name, or an exact quoted phrase. NOT by line number: line numbers in this repo shift every release and a finding located only by line number is unusable a month later. A line number may be added as a secondary hint only if you read it in the file this run.
3. For each meaningful piece of guidance/config/code, name the ENCODED ASSUMPTION about model behavior, capability, or failure mode (e.g. "assumes a 200K context", "assumes the model claims done without testing", "assumes the model under-delegates", "assumes this model id is the only one with a large window").
4. Identify the SURFACE of the thing you are changing and of the evidence you are using. If they differ, you have no finding yet — go get evidence for the target surface or drop it.
5. Run the ROOT-CAUSE SWEEP. Fill siblingCases and siblingSweepMethod for every finding. Your proposedChange must cover every sibling case you found, not just the one you noticed first.
6. Classify against the profile:
   - VALID  = keep as-is. Either model-independent (security/hygiene/user-facing) OR the failure mode it guards still exists in ${NEW_MODEL}.
   - NOISE  = now unnecessary friction because a CONFIRMED-or-LIKELY ${NEW_MODEL} capability removed the failure mode it guarded.
   - GAP    = ${NEW_MODEL} (or the current harness) introduces a capability, model id, or failure mode this component does NOT address.
   - OPPORTUNITY = ${NEW_MODEL} enables a meaningfully better approach.
7. ANTI-HALLUCINATION RULE: default to VALID. Assign NOISE/GAP/OPPORTUNITY only when a SPECIFIC profile fact with confidence 'confirmed' or 'likely' supports it — name it in tiedCapability. If the only support is 'speculative' or "the model is generally smarter now", classify VALID, note the speculation in rationale, and propose no change. NEVER recommend weakening a security/safety guardrail because the model is more capable. A factual staleness bug (a wrong model id, price, or support matrix, verified against the claude-api skill) does not need a capability claim — it needs the correct value and a full sibling sweep.
8. Give each non-VALID finding a concrete proposedChange (exact replacement wording or a precise diff) and a severity: P0 (clearly wrong/harmful for ${NEW_MODEL}), P1 (meaningful improvement), P2 (minor/polish). VALID findings get severity 'keep'.

BUDGET: read what you need and stop. Return at most ${comp.maxFindings} findings, best first. Quality over quantity — an under-swept or surface-confused finding costs the audit more than a missing one.
List every file you opened in targetsRead.`

const verifyPrompt = (f) => `You are an adversarial reviewer. Try to REFUTE a proposed change to the "cc_tool" Claude Code setup. Default to rejecting unless the change is clearly justified. You have file access: OPEN the cited location and check it yourself — do not review the finding's prose in the abstract.

${FACTS_BLOCK}

${SURFACE_RULES}

CAPABILITY PROFILE for ${NEW_MODEL}:
${PROFILE_JSON}

PROPOSED FINDING (JSON):
${JSON.stringify(f, null, 2)}

Check, in order:
1. Reproduce it. Open the file at the cited anchor. Does the quoted text/code actually exist and say what the finding claims? If the anchor does not resolve, the finding fails here.
2. capabilityClaimVerified: Is the ${NEW_MODEL} capability this relies on 'confirmed' or 'likely' in the profile (NOT 'speculative', NOT in researchGaps, NOT a vibe like "smarter now")? A pure factual-staleness fix (wrong model id / price / support matrix) instead needs the correct value — verify it via the 'claude-api' skill and set this true only if you confirmed it there or in the profile.
3. surfaceVerified: Does the evidence belong to the SAME surface as the thing being changed — API evidence for an API change, harness evidence for a harness/slash-command/settings change, model-behavior evidence for guidance? A support matrix on one surface says NOTHING about the other. Set false on any cross-surface transfer; a false here means adjustedSeverity='reject'.
4. weakensModelIndependentGuardrail: Would applying this weaken a guardrail that protects regardless of model intelligence — security deny-lists, secret-read blocks, push-to-main / verify-bypass blocks, install confirmation, sandboxing? If so, true, and reject almost always.
5. scopeComplete: Go looking for sibling cases YOURSELF — grep the file and its siblings for the same construct (every entry in the same lookup table, every branch of the same conditional, every doc line stating the same rule). If the same root cause hits cases the proposedChange does not cover, set scopeComplete=false, list them in missedSiblings, and write the scopeAdjustment needed to cover them all. An under-scoped fix is NOT a rejection — it is a finding that must be widened, and an N-instance defect usually deserves a HIGHER severity than the 1-instance version it was reported as.
6. Would the change introduce a NEW failure mode, or remove a guardrail still useful to the human user (not just the model)? Is the proposedChange concrete and correct?

DECISION: warranted=true only if it reproduces AND capabilityClaimVerified=true AND surfaceVerified=true AND weakensModelIndependentGuardrail=false AND no new failure mode. Otherwise warranted=false and adjustedSeverity='reject'. scopeComplete=false does not reject — it widens the change and may raise severity. If warranted but mis-prioritized, adjust severity. Be specific in reason and caveats.`

log(`Analyzing ${components.length} setup components and adversarially verifying every proposed change`)
const perComponent = await pipeline(
  components,
  (comp) => agent(analysisPrompt(comp), { label: `analyze:${comp.name.slice(0, 40)}`, phase: 'Analyze', schema: ANALYSIS_SCHEMA }),
  (analysis, comp) => {
    const findings = (analysis && analysis.findings) || []
    const changes = findings.filter(f => f.classification !== 'VALID')
    const keeps = findings.filter(f => f.classification === 'VALID').map(f => ({
      ...f, verdict: { warranted: true, capabilityClaimVerified: true, surfaceVerified: true, weakensModelIndependentGuardrail: false, scopeComplete: true, missedSiblings: 'n/a — keep', scopeAdjustment: 'n/a', adjustedSeverity: 'keep', reason: 'Leave as-is; no change proposed.', caveats: '' },
    }))
    return parallel(changes.map(f => () =>
      agent(verifyPrompt(f), { label: `verify:${(f.title || '').slice(0, 36)}`, phase: 'Verify', schema: VERDICT_SCHEMA })
        .then(v => ({ ...f, verdict: v }))
    )).then(verified => ({ component: comp.name, targetsRead: (analysis && analysis.targetsRead) || [], findings: [...verified.filter(Boolean), ...keeps] }))
  }
)

// ---- Phase 5: synthesis (barrier — needs all findings together) --------
phase('Synthesize')
const allFindings = perComponent.filter(Boolean).flatMap(c => (c.findings || []).map(f => ({ component: c.component, ...f })))
const survivingChanges = allFindings.filter(f => f.classification !== 'VALID' && f.verdict && f.verdict.warranted && f.verdict.adjustedSeverity !== 'reject')
const rejected = allFindings.filter(f => f.classification !== 'VALID' && (!f.verdict || !f.verdict.warranted || f.verdict.adjustedSeverity === 'reject'))
const keeps = allFindings.filter(f => f.classification === 'VALID')
const widened = survivingChanges.filter(f => f.verdict && f.verdict.scopeComplete === false)
log(`Findings: ${survivingChanges.length} verified changes (${widened.length} widened by the sibling sweep), ${rejected.length} rejected (unverified capability / wrong surface / guardrail), ${keeps.length} keep-as-is. Writing report.`)

const coverageBlock = JSON.stringify({
  componentsAudited: perComponent.filter(Boolean).map(c => c.component),
  filesActuallyRead: perComponent.filter(Boolean).flatMap(c => c.targetsRead || []),
  censusFlaggedUncovered: uncovered,
  censusNotes: str(inv.notes),
  notAuditedThisRun: droppedComponents,
}, null, 2)

const result = await agent(
`Write the final recalibration report (GitHub-flavored markdown) for migrating the "cc_tool" Claude Code setup from ${OLD_MODEL} to ${NEW_MODEL}, and WRITE IT TO DISK.

${FACTS_BLOCK}

${SURFACE_RULES}

CAPABILITY PROFILE:
${PROFILE_JSON}

VERIFIED CHANGES (passed adversarial review — JSON. Where a verdict has scopeComplete=false, the change was UNDER-SCOPED as reported: fold verdict.scopeAdjustment and verdict.missedSiblings into the change you publish, and use verdict.adjustedSeverity):
${JSON.stringify(survivingChanges, null, 2)}

REJECTED PROPOSALS (failed review: unreproducible, unverified capability, cross-surface claim, or would weaken a guardrail — JSON):
${JSON.stringify(rejected, null, 2)}

KEEP-AS-IS (model-independent or failure mode still present — JSON):
${JSON.stringify(keeps, null, 2)}

COVERAGE (what this run actually inventoried and read — JSON):
${coverageBlock}

Write the report with these sections:
1. "# cc_tool recalibration: ${OLD_MODEL} → ${NEW_MODEL}" + a 1-paragraph executive summary with the P0/P1/P2/keep counts.
2. "## What actually changed in ${NEW_MODEL} that matters here" — the decision-relevant capabilities with confidence tags AND surface tags. State the model's release date and how it relates to your own knowledge cutoff (get both from the profile or the claude-api skill — do not assume either), so the reader knows which claims rest on the profile rather than on memory.
3. "## Recommended changes" — grouped P0 → P1 → P2. Each: component, file + anchor (heading / marker / quoted phrase — NOT a bare line number), the change with concrete proposed wording or diff covering EVERY sibling case, the confirmed/likely capability or verified fact it rests on, its surface, and the risk of not doing it. Where the sibling sweep widened a change, say so explicitly.
4. "## Leave as-is (deliberately not changing)" — the keep-list with one-line reasons. Emphasize that security/safety guardrails are model-independent.
5. "## Refuted during verification" — the rejected proposals and WHY each failed (unreproducible / unverified capability / cross-surface / guardrail). This section is the audit's evidence that it filtered itself; never omit it.
6. "## Needs confirmation before acting" — anything resting on speculative facts or researchGaps, phrased as questions to resolve against official sources.
7. "## Coverage" — what was inventoried and audited this run, plus anything the census flagged as uncovered and anything listed under notAuditedThisRun. Be explicit about blind spots; do not pad.
8. "## Suggested CHANGELOG entry" — a ready-to-paste entry for the NEXT version after the latest one in CHANGELOG.md (read the file to get the number and the house style).
9. "## How to re-run this audit" — produced by the 'model-recalibration-audit' workflow; re-runnable per model release with args {newModel, oldModel, newModelId?, releaseDate?, confirmedFacts?}. Note that it carries no built-in model facts and enumerates the repo at runtime, so new skills/hooks are picked up automatically.

THEN WRITE IT: save the full markdown under the repo's docs/ directory as '${ROOT}/docs/<slug>-recalibration-<YYYY-MM-DD>.md', where <slug> is ${NEW_MODEL} lowercased and hyphenated and <YYYY-MM-DD> is TODAY'S real date — obtain it from the environment (e.g. run 'date +%F'); never guess or reuse a date from an example. Look at the existing files in that directory first and match their naming style. Create the directory if it does not exist. Return that absolute path as reportPath.

Be precise and honest. Do not overstate confidence. If a section is empty, say so explicitly rather than padding.
Return: report (full markdown), reportPath, and counts p0/p1/p2 (verified changes by adjusted severity), keep (keep-list size), needsConfirmation (items in the Needs-confirmation section).`,
  { label: 'synthesize:report', schema: REPORT_SCHEMA }
)

return {
  models: { newModel: NEW_MODEL, newModelId: NEW_MODEL_ID || 'not supplied', oldModel: OLD_MODEL },
  coverage: {
    snippetSections: sections.length,
    hooks: hookFiles.length,
    skills: skillDirs.length,
    binScripts: binScripts.length,
    componentsAudited: components.length,
    censusFlaggedUncovered: uncovered,
    notAuditedThisRun: droppedComponents,
  },
  counts: {
    verifiedChanges: survivingChanges.length,
    widenedBySiblingSweep: widened.length,
    rejected: rejected.length,
    keeps: keeps.length,
    p0: result.p0, p1: result.p1, p2: result.p2, needsConfirmation: result.needsConfirmation,
  },
  anyAuthoritativeResearch: anyAuthoritative,
  reportPath: result.reportPath,
  profile,
  report: result.report,
}
