export const meta = {
  name: 'ship-pipeline',
  description: 'Four-agent team that ships one feature end-to-end: Planner (Opus 5.5) → Coder (Sonnet 5.5) → Tester (Sonnet 5.5) → Reviewer (Opus 5.5), each handing structured output to the next.',
  whenToUse: 'When you want a single well-scoped change driven through plan → implement → test → review with separate agents and a read-only review gate. Parameterize via args.feature (or pass a plain string as args).',
  phases: [
    { title: 'Plan', detail: 'Planner turns the feature request into a concrete, file-level implementation spec' },
    { title: 'Code', detail: 'Coder implements the spec and reports a change summary + touched files' },
    { title: 'Test', detail: 'Tester writes/runs tests against the spec and reports pass/fail evidence' },
    { title: 'Review', detail: 'Reviewer (read-only gate) returns a pass/fail verdict + blocking issues' },
  ],
}

// ---- config (parameterized via the args global) -------------------------
// args may be an object ({ feature, root, ... }) or a bare string (the request).
const cfg = (args && typeof args === 'object') ? args : {}
const FEATURE = cfg.feature || (typeof args === 'string' ? args.trim() : '')
if (!FEATURE) return { error: 'No feature provided. Pass args.feature (or a plain request string as args) and re-invoke.' }
const ROOT = cfg.root || 'the current repository (your working directory)'
// Judgment stages run on Opus 5.5 and execution stages on Sonnet 5.5, per the
// managed block's Model routing. The planner's spec and acceptance criteria are
// what make code and test well-scoped enough for Sonnet, and the Opus reviewer
// is a gate from a different model than the one that wrote the code.
// 'opus' resolves to Opus 5.5 and 'sonnet' to Sonnet 5.5 (Claude Code >= 2.1.284,
// Claude API; on Bedrock/Vertex/Foundry 'sonnet' is still 4.5, so pass codeModel
// and testModel 'opus' there).
// Override per stage, e.g. args { feature, codeModel: 'opus' } for a change too
// tangled to spec. Effort unset means the session level. A Sonnet stage asked for
// 'low' effort runs at 'medium' instead, since Sonnet 5.5 at 'low' can report a
// change done without exercising it.
const MODEL = {
  plan: cfg.planModel || 'opus', code: cfg.codeModel || 'sonnet',
  test: cfg.testModel || 'sonnet', review: cfg.reviewModel || 'opus',
}
const EFFORT = { plan: cfg.planEffort, code: cfg.codeEffort, test: cfg.testEffort, review: cfg.reviewEffort }
const stageOpts = (stage, opts) => {
  const o = { ...opts, model: MODEL[stage] }
  if (!EFFORT[stage]) return o
  if (MODEL[stage] === 'sonnet' && EFFORT[stage] === 'low') {
    log(`${stage}: 'low' effort raised to 'medium' for Sonnet`)
    return { ...o, effort: 'medium' }
  }
  return { ...o, effort: EFFORT[stage] }
}

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
      showsFailure: { type: 'string', description: 'how to show it fails: a failing input, test, or command (empty for a nit)' },
      suggestion: { type: 'string' },
    }, required: ['severity', 'location', 'problem', 'showsFailure', 'suggestion'] } },
    meetsAcceptanceCriteria: { type: 'boolean' },
    nextSteps: { type: 'string', description: 'what to do given the verdict' },
  },
  required: ['verdict', 'summary', 'issues', 'meetsAcceptanceCriteria', 'nextSteps'],
}

// ---- Stage 1: Plan --------------------------------------------------
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
  stageOpts('plan', { label: 'plan:spec', schema: SPEC_SCHEMA })
)

// ---- Stages 2-4 as a streaming pipeline: code → test → review ----------
// Single-item pipeline so each stage receives the prior stage's structured
// output. (Batching would require running the planner per feature and
// threading each spec through the stages.)
const SPEC_JSON = JSON.stringify(spec, null, 2)
let codeReport, testReport

const result = await pipeline(
  [spec],
  // Stage 2: Code
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
      stageOpts('code', { label: 'code:implement', phase: 'Code', schema: CODE_SCHEMA })
    )
    return codeReport
  },
  // Stage 3: Test
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
      stageOpts('test', { label: 'test:verify', phase: 'Test', schema: TEST_SCHEMA })
    )
    return testReport
  },
  // Stage 4: Review (read-only gate)
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
- List issues by severity (blocking / should-fix / nit), each with its file and line, why it is wrong, how to show it fails (a failing input, test, or command the coder can run), and a suggestion. Mark something blocking only if you would stop the merge for it.
- nextSteps: if fail, what the coder must change; if pass, what remains before merge (the human still commits).
Return the structured review.`,
      stageOpts('review', { label: 'review:gate', phase: 'Review', schema: REVIEW_SCHEMA })
    )
  }
)

const review = result[0]
log(`Pipeline complete — review verdict: ${review && review.verdict}`)

return {
  feature: FEATURE,
  model: MODEL,
  effort: EFFORT,
  spec,
  code: codeReport,
  tests: testReport,
  review,
  shipped: !!(review && review.verdict === 'pass'),
}
