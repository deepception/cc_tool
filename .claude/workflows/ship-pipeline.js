export const meta = {
  name: 'ship-pipeline',
  description: 'Four-agent team that ships one feature end-to-end: Planner (Opus) → Coder (Sonnet) → Tester (Sonnet) → Reviewer (Opus), each handing structured output to the next.',
  whenToUse: 'When you want a single well-scoped change driven through plan → implement → test → review with model-tiered agents and a read-only review gate. Parameterize via args.feature (or pass a plain string as args).',
  phases: [
    { title: 'Plan', detail: 'Opus planner turns the feature request into a concrete, file-level implementation spec' },
    { title: 'Code', detail: 'Sonnet coder implements the spec and reports a change summary + touched files' },
    { title: 'Test', detail: 'Sonnet tester writes/runs tests against the spec and reports pass/fail evidence' },
    { title: 'Review', detail: 'Opus reviewer (read-only gate) returns a pass/fail verdict + blocking issues' },
  ],
}

// ---- config (parameterized via the args global) -------------------------
// args may be an object ({ feature, root, ... }) or a bare string (the request).
const cfg = (args && typeof args === 'object') ? args : {}
const FEATURE = cfg.feature || (typeof args === 'string' ? args.trim() : '')
if (!FEATURE) return { error: 'No feature provided. Pass args.feature (or a plain request string as args) and re-invoke.' }
const ROOT = cfg.root || 'the current repository (your working directory)'
// Planning + review want the stronger model. For the hardest features pass
// planModel:'fable' (Claude Fable 5: higher first-shot correctness + bug-finding
// recall) — ~2x opus cost, opt-in per feature, not the default. If the feature
// under review is security-sensitive (crypto, auth, exploit-adjacent), keep or
// fall back to opus (Fable's cyber classifier may refuse).
const PLAN_MODEL = cfg.planModel || 'opus'
const CODE_MODEL = cfg.codeModel || 'sonnet'  // implementation + testing are throughput-bound
const REVIEW_MODEL = cfg.reviewModel || PLAN_MODEL  // independent review tier (e.g. reviewModel:'fable')

// ---- schemas (the structured hand-offs between stages) ------------------
const SPEC_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    summary: { type: 'string', description: 'one-paragraph statement of what will be built and why' },
    approach: { type: 'string', description: 'the chosen implementation approach in prose' },
    steps: { type: 'array', items: { type: 'object', additionalProperties: false, properties: {
      order: { type: 'integer' },
      file: { type: 'string', description: 'file to create or edit (absolute path), or "n/a"' },
      change: { type: 'string', description: 'the concrete edit to make' },
    }, required: ['order', 'file', 'change'] } },
    acceptanceCriteria: { type: 'array', items: { type: 'string' }, description: 'observable conditions that mean "done"' },
    testPlan: { type: 'string', description: 'how the change should be tested (commands, cases)' },
    risks: { type: 'array', items: { type: 'string' } },
  },
  required: ['summary', 'approach', 'steps', 'acceptanceCriteria', 'testPlan', 'risks'],
}

const CODE_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    implemented: { type: 'boolean', description: 'true only if the spec was actually implemented in the working tree' },
    summary: { type: 'string', description: 'what was changed, in prose' },
    filesTouched: { type: 'array', items: { type: 'object', additionalProperties: false, properties: {
      file: { type: 'string' },
      change: { type: 'string', description: 'what changed in this file' },
    }, required: ['file', 'change'] } },
    deviationsFromSpec: { type: 'array', items: { type: 'string' }, description: 'anywhere the implementation diverged from the spec, and why' },
    howToTest: { type: 'string', description: 'exact command(s) to build/run/test the change' },
    openQuestions: { type: 'array', items: { type: 'string' } },
  },
  required: ['implemented', 'summary', 'filesTouched', 'deviationsFromSpec', 'howToTest', 'openQuestions'],
}

const TEST_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    ran: { type: 'boolean', description: 'true only if tests were actually executed (not just written)' },
    passed: { type: 'boolean', description: 'true only with observed passing output — no assumptions' },
    command: { type: 'string', description: 'the exact command run' },
    evidence: { type: 'string', description: 'the relevant tail of the test output (truncated)' },
    coverageOfAcceptance: { type: 'array', items: { type: 'object', additionalProperties: false, properties: {
      criterion: { type: 'string' },
      covered: { type: 'boolean' },
      note: { type: 'string' },
    }, required: ['criterion', 'covered', 'note'] } },
    failures: { type: 'array', items: { type: 'string' }, description: 'failing cases with the assertion that failed' },
  },
  required: ['ran', 'passed', 'command', 'evidence', 'coverageOfAcceptance', 'failures'],
}

const REVIEW_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    verdict: { type: 'string', enum: ['pass', 'fail'] },
    summary: { type: 'string', description: 'one-paragraph assessment' },
    issues: { type: 'array', items: { type: 'object', additionalProperties: false, properties: {
      severity: { type: 'string', enum: ['blocking', 'should-fix', 'nit'] },
      location: { type: 'string', description: 'file + line range or symbol' },
      problem: { type: 'string' },
      suggestion: { type: 'string' },
    }, required: ['severity', 'location', 'problem', 'suggestion'] } },
    meetsAcceptanceCriteria: { type: 'boolean' },
    nextSteps: { type: 'string', description: 'what to do given the verdict' },
  },
  required: ['verdict', 'summary', 'issues', 'meetsAcceptanceCriteria', 'nextSteps'],
}

// ---- Stage 1: Plan (Opus) ----------------------------------------------
phase('Plan')
log(`Planning feature: ${FEATURE.slice(0, 120)}`)

const spec = await agent(
`You are the PLANNER on a 4-agent team shipping ONE feature. Produce a concrete, file-level implementation spec — do NOT write code yet.

REPO ROOT: ${ROOT}
FEATURE REQUEST:
${FEATURE}

METHOD:
- Explore only as much of the codebase as you need (Read/Grep/Glob) to ground the plan in how this project is actually structured. Quote real file paths.
- Decompose into ordered, minimal steps. Each step names the file (absolute path) and the concrete change.
- Define acceptance criteria as observable conditions, and a test plan with concrete commands/cases.
- Prefer the smallest change that satisfies the request; call out risks and anything ambiguous.
Return the structured spec.`,
  { label: 'plan:spec', model: PLAN_MODEL, schema: SPEC_SCHEMA }
)

// ---- Stages 2-4 as a streaming pipeline: code → test → review ----------
// Single-item pipeline so each stage receives the prior stage's structured
// output. (Batching would require running the planner per feature and
// threading each spec through the stages.)
const SPEC_JSON = JSON.stringify(spec, null, 2)
let codeReport, testReport

const result = await pipeline(
  [spec],
  // Stage 2: Code (Sonnet)
  async (s) => {
    log('Implementing the spec')
    codeReport = await agent(
`You are the CODER on a 4-agent team. Implement the following spec in the working tree at ${ROOT}. Make the edits — do not just describe them.

SPEC (JSON):
${JSON.stringify(s, null, 2)}

METHOD:
- Read the files named in the spec before editing them. Make the minimal edits that satisfy the spec and its acceptance criteria.
- Do NOT run git commit/push. Do NOT change unrelated code.
- If you must deviate from the spec, do it deliberately and record it in deviationsFromSpec.
- Provide the exact command(s) the tester should run in howToTest.
Set implemented=true only if you actually changed the working tree. Return the structured summary.`,
      { label: 'code:implement', phase: 'Code', model: CODE_MODEL, schema: CODE_SCHEMA }
    )
    return codeReport
  },
  // Stage 3: Test (Sonnet)
  async (code) => {
    log(`Testing the change (implemented=${code && code.implemented})`)
    testReport = await agent(
`You are the TESTER on a 4-agent team. Verify the change just implemented against the spec's acceptance criteria. Write tests where useful, then RUN them.

SPEC (JSON):
${SPEC_JSON}

CODER REPORT (JSON):
${JSON.stringify(code, null, 2)}

METHOD:
- Run the coder's howToTest command (and the spec testPlan). Use the project's existing test runner/build where one exists.
- Report ONLY observed results. Set ran=true / passed=true only with real output in evidence — never assume green.
- Map each acceptance criterion to covered true/false. List concrete failures with the assertion that failed.
Return the structured test report.`
      ,
      { label: 'test:verify', phase: 'Test', model: CODE_MODEL, schema: TEST_SCHEMA }
    )
    return testReport
  },
  // Stage 4: Review (Opus, read-only gate)
  (tests) => {
    log(`Reviewing (tests passed=${tests && tests.passed})`)
    return agent(
`You are the REVIEWER on a 4-agent team and the final GATE. You are READ-ONLY: do not edit files, do not run mutating commands, do not commit. Inspect the diff and the evidence, then return a pass/fail verdict.

SPEC (JSON):
${SPEC_JSON}

CODER REPORT (JSON):
${JSON.stringify(codeReport, null, 2)}

TEST REPORT (JSON):
${JSON.stringify(tests, null, 2)}

METHOD:
- Inspect the working-tree change (e.g. read the touched files and 'git diff' read-only) against the spec and acceptance criteria.
- verdict='pass' ONLY if: acceptance criteria are met, tests actually ran and passed (tests.ran && tests.passed), and there are no blocking correctness/security issues. Otherwise verdict='fail'.
- List issues by severity (blocking / should-fix / nit) with concrete location and suggestion.
- nextSteps: if fail, what the coder must change; if pass, what remains before merge (the human still commits).
Return the structured review.`,
      { label: 'review:gate', phase: 'Review', model: REVIEW_MODEL, schema: REVIEW_SCHEMA }
    )
  }
)

const review = result[0]
log(`Pipeline complete — review verdict: ${review && review.verdict}`)

return {
  feature: FEATURE,
  models: { plan: PLAN_MODEL, code: CODE_MODEL, test: CODE_MODEL, review: REVIEW_MODEL },
  spec,
  code: codeReport,
  tests: testReport,
  review,
  shipped: !!(review && review.verdict === 'pass'),
}
