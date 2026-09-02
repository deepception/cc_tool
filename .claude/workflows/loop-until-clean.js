export const meta = {
  name: 'loop-until-clean',
  description: 'Loop-until-done sweep: repeatedly fan out finder agents over a target until TWO consecutive rounds surface nothing new, then adversarially verify the survivors and return only the confirmed set.',
  whenToUse: 'When the work is open-ended and you cannot know up front how many passes it takes (find all instances of X, hunt remaining issues, sweep until clean). The stop condition is "no new findings", not a fixed iteration count. Parameterize via args.target / args.find / args.maxRounds.',
  phases: [
    { title: 'Sweep', detail: 'Each round fans out finder agents over the target; dedup against everything seen so far' },
    { title: 'Verify', detail: 'Adversarially verify the de-duplicated survivors; keep only confirmed findings' },
  ],
}

// ---- config (parameterized via the args global) ------------------------
const cfg = (args && typeof args === 'object') ? args : {}
const TARGET = cfg.target || (typeof args === 'string' ? args.trim() : '')
if (!TARGET) return { error: 'No target provided. Pass args.target (or a plain target string as args, e.g. a directory, module, or codebase area) and re-invoke.' }
const FIND = cfg.find || 'bugs, dead code, and inconsistencies'
const ROOT = cfg.root || 'the current repository (your working directory)'
const FINDERS_PER_ROUND = cfg.findersPerRound || 3   // parallel finders each round
const MAX_ROUNDS = cfg.maxRounds || 6                // absolute safety cap on rounds
const DRY_ROUNDS_TO_STOP = cfg.dryRoundsToStop || 2  // stop after N consecutive empty rounds

// Token-budget guard: only consulted if the host populated `budget.total`.
// We keep looping only while a healthy fraction of the budget remains.
const BUDGET_FLOOR = cfg.budgetFloor || 0.2          // stop spawning below 20% remaining
const hasBudget = typeof budget !== 'undefined' && budget && budget.total
const budgetHealthy = () => {
  if (!hasBudget || typeof budget.remaining !== 'function') return true
  return (budget.remaining() / budget.total) > BUDGET_FLOOR
}

// ---- schemas -----------------------------------------------------------
const FIND_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    findings: { type: 'array', items: { type: 'object', additionalProperties: false, properties: {
      id: { type: 'string', description: 'canonical dedup slug "<repo-root-relative path>:<first affected line>:<kind>" where kind is a lowercase-kebab issue category, e.g. "bin/cc-new-project:42:unquoted-var". The same underlying issue produces the same id every round.' },
      title: { type: 'string' },
      location: { type: 'string', description: 'file + line range or symbol' },
      detail: { type: 'string' },
      severity: { type: 'string', enum: ['high', 'medium', 'low'] },
    }, required: ['id', 'title', 'location', 'detail', 'severity'] } },
  },
  required: ['findings'],
}

const VERIFY_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    id: { type: 'string' },
    confirmed: { type: 'boolean', description: 'true only if the finding is real and reproducible at the cited location' },
    reason: { type: 'string' },
    correctedLocation: { type: 'string', description: 'corrected file+line if the original was slightly off, else "n/a"' },
  },
  required: ['id', 'confirmed', 'reason', 'correctedLocation'],
}

// ---- Phase 1: sweep in rounds until two dry rounds (or caps) -----------
phase('Sweep')

const seen = new Set()      // dedup keys (finding ids) across all rounds
const survivors = []        // unique findings collected so far
let dryRounds = 0           // consecutive rounds that surfaced nothing new
let round = 0

const finderPrompt = (n) => `You are finder #${n} in a sweep. Find every instance of: ${FIND}.

REPO ROOT: ${ROOT}
TARGET TO SWEEP:
${TARGET}

ALREADY-FOUND (skip these; look for anything not in this list):
${survivors.length ? survivors.map(s => `  - ${s.id} — ${s.title}`).join('\n') : '  (nothing yet — this is the first pass)'}

METHOD:
- Use Read/Grep/Glob to inspect the target. Cover ground other finders may have missed; go for breadth this round.
- For each new issue, emit id "<repo-root-relative path>:<first affected line>:<kind>" (kind = lowercase-kebab issue category). If an ALREADY-FOUND entry clearly refers to the same underlying issue — even at a slightly different line or with different wording — skip it. Be precise with location.
- If you find nothing new, return an empty findings array; that is the correct answer, and each low-confidence guess costs a verification round.
Return the structured findings.`

while (round < MAX_ROUNDS && dryRounds < DRY_ROUNDS_TO_STOP && budgetHealthy()) {
  round++
  log(`Round ${round}: fanning out ${FINDERS_PER_ROUND} finders (seen so far: ${seen.size}, dry streak: ${dryRounds})`)

  const roundResults = (await parallel(
    Array.from({ length: FINDERS_PER_ROUND }, (_, i) => () =>
      agent(finderPrompt(i + 1), { label: `find:r${round}-f${i + 1}`, schema: FIND_SCHEMA }))
  )).filter(Boolean)

  const roundFindings = roundResults.flatMap(r => (r && r.findings) || [])
  let newThisRound = 0
  for (const f of roundFindings) {
    const key = f && f.id ? String(f.id).trim().toLowerCase() : ''
    if (!key || seen.has(key)) continue
    seen.add(key)
    survivors.push(f)
    newThisRound++
  }

  if (newThisRound === 0) {
    dryRounds++
    log(`Round ${round}: 0 new findings — dry streak now ${dryRounds}/${DRY_ROUNDS_TO_STOP}`)
  } else {
    dryRounds = 0
    log(`Round ${round}: +${newThisRound} new (total unique: ${survivors.length})`)
  }
}

const stopReason = dryRounds >= DRY_ROUNDS_TO_STOP ? `${DRY_ROUNDS_TO_STOP} consecutive dry rounds`
  : round >= MAX_ROUNDS ? `hit maxRounds (${MAX_ROUNDS})`
  : 'token budget floor reached'
log(`Sweep done after ${round} round(s): ${survivors.length} unique findings. Stop reason: ${stopReason}.`)

// ---- Phase 2: adversarially verify the survivors (barrier) -------------
phase('Verify')

if (survivors.length === 0) {
  return { target: TARGET, rounds: round, stopReason, confirmed: [], rejected: [], summary: 'Swept clean — no findings survived.' }
}

const verifyPrompt = (f) => `You are an adversarial verifier. Try to REFUTE this finding from a sweep. Default to NOT confirming unless you can reproduce it at the cited location.

REPO ROOT: ${ROOT}
FINDING (JSON):
${JSON.stringify(f, null, 2)}

METHOD:
- Read the cited location and surrounding context. Confirm the issue is real, present, and reproducible.
- Set confirmed=false if it is a false positive, already handled, out of scope, or you cannot locate it.
- If the issue is real but the location was slightly off, set confirmed=true and give the correctedLocation.
Return the structured verdict.`

log(`Verifying ${survivors.length} survivors adversarially`)
const verdicts = (await parallel(
  survivors.map(f => () =>
    agent(verifyPrompt(f), { label: `verify:${(f.id || '').slice(0, 36)}`, schema: VERIFY_SCHEMA })
      .then(v => ({ finding: f, verdict: v })))
)).filter(Boolean)

const confirmed = verdicts.filter(v => v.verdict && v.verdict.confirmed)
  .map(v => ({ ...v.finding, location: (v.verdict.correctedLocation && v.verdict.correctedLocation !== 'n/a') ? v.verdict.correctedLocation : v.finding.location, verifyReason: v.verdict.reason }))
const rejected = verdicts.filter(v => !v.verdict || !v.verdict.confirmed)
  .map(v => ({ id: v.finding.id, title: v.finding.title, reason: v.verdict ? v.verdict.reason : 'no verdict' }))

log(`Verified: ${confirmed.length} confirmed, ${rejected.length} rejected.`)

return {
  target: TARGET,
  rounds: round,
  stopReason,
  confirmed,
  rejected,
  summary: `${confirmed.length} confirmed finding(s) after ${round} round(s); ${rejected.length} rejected on verification.`,
}
