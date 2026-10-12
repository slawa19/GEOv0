/**
 * 039 T3901 - R14, the RUNNER and MEMBERSHIP thirds (geometry: `r14-chord-geometry.p039-repro.ts`).
 *
 *   node e2e/qa/p039-repro/r14-membership.p039-repro.mjs        (from simulator-ui/v2; exit 1 on the base)
 *
 * Corrected form (spec 039, P-2, P-8; arbiter F9): runner behaviour, project membership and geometry are judged apart.
 *  1. RUNNER - already green: the verdict of the gate refuses ANY skipped test (`run-verdict.mjs`). The spec's
 *     "refuse a skip without a recorded reason" would be weaker than what stands; the rule is not to be loosened.
 *  2. MEMBERSHIP - the reproducer: the six deterministic budget tests, NAMED BELOW by their full titles (counts taken
 *     on `page.clock`), are members of the COUNTED project `qa-stand`. Today they are collected by `qa-perf` only, which
 *     the gate never runs. They are matched by identity, not by the `budget:` prefix: a moved-and-renamed test must be
 *     renamed in the list too.
 *
 * SLICE A, its stand part after T3901 (budgets moved into `qa-stand` with their counts), must turn check 2 green.
 * DONE 2026-10-12 (039 slice A): the two files are `budgets.qa.ts` and `no-gpu-effects.qa.ts`; this checker exits 0.
 * Run on the base a5e60d00 it printed "actual: 0 of 6 in qa-stand" and exited 1.
 *
 * What it reads: `playwright test --list` of the stand's own config (collection, no browser, no server) and the pure
 * function `verdict()`. What it does NOT see: whether a listed test would pass, and a skip decided at run time
 * (`test.skip(cond)` inside a test is listed like any other test).
 */
import { spawnSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { verdict } from '../run-verdict.mjs'

const root = resolve(fileURLToPath(new URL('.', import.meta.url)), '..', '..', '..')
const cli = createRequire(import.meta.url).resolve('@playwright/test/cli')

function list(project) {
  const run = spawnSync(process.execPath, [cli, 'test', '--config=e2e/playwright.qa.config.ts', `--project=${project}`, '--list', '--reporter=json'], { cwd: root, encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 })
  if (run.status !== 0) throw new Error(`playwright --list for ${project} exited ${run.status}: ${String(run.stderr).slice(0, 2000)}`)
  const out = []
  const walk = (suite, file) => {
    for (const spec of suite.specs ?? []) out.push({ file: file ?? suite.file ?? spec.file, title: spec.title })
    for (const child of suite.suites ?? []) walk(child, file ?? suite.file)
  }
  for (const suite of JSON.parse(run.stdout).suites ?? []) walk(suite, suite.file)
  return out
}

const stand = list('qa-stand')
const perf = list('qa-perf')
const byFile = (tests) => tests.reduce((m, t) => ({ ...m, [t.file]: (m[t.file] ?? 0) + 1 }), {})
/**
 * The deterministic budget tests, BY IDENTITY: the title of each, with the file it lives in today (2026-10-12, after
 * the move of 039 slice A). The file changes when a test moves (`qa-stand` collects `*.qa.ts`), so membership is judged by title. A
 * test that is renamed while it moves must be renamed HERE in the same commit - otherwise this check is red, which is
 * the point: a prefix match would let five budgets stay outside the count under new names.
 */
const BUDGET_TESTS = [
  ['budgets.qa.ts', 'budget: in deep sleep nothing is drawn and no frame is requested; an event is drawn within 48 ms; one payment costs a fixed number of draws'],
  ['budgets.qa.ts', 'budget: one period of the named load reaches the recorded peaks of sparks, edge pulses and node bursts, and leaves no FX object alive'],
  ['budgets.qa.ts', 'budget: one period of the named load is one pass per frame of a recorded drawing volume, with the recorded peak of labels, no blur effects, and nothing left'],
  ['budgets.qa.ts', 'budget: at a sustained 10 fps the FX budget falls, the particle cap follows and holds; at 60 fps both come back'],
  ['budgets.qa.ts', 'budget: a flood of amount labels stops at the cap'],
  ['no-gpu-effects.qa.ts', 'budget: without a GPU no element of the page carries a CSS filter or a backdrop-filter, in any state the stand can open'],
]
const titled = (tests, title) => tests.filter((t) => t.title === title)
const where = BUDGET_TESTS.map(([file, title]) => ({ file, title, inQaStand: titled(stand, title).length, inQaPerf: titled(perf, title).length }))
const row = {
  qaStand: { tests: stand.length, gateSubset: stand.filter((t) => !t.title.includes('@extended')).length, files: byFile(stand) },
  qaPerf: { tests: perf.length, files: byFile(perf) },
  budgetTests: where.map((w) => ({ from: w.file, title: w.title.slice(0, 70), inQaStand: w.inQaStand, inQaPerf: w.inQaPerf })),
}
console.log('[039 R14 membership]', JSON.stringify(row, null, 1))

const failures = []
const check = (name, ok, detail) => {
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}${ok ? '' : ` - ${detail}`}`)
  if (!ok) failures.push(name)
}

// Counter-checks: the listing saw both projects, and budget tests exist somewhere.
check('counter-check: qa-stand lists tests', stand.length > 0, `listed ${stand.length}`)
const lost = where.filter((w) => w.inQaStand + w.inQaPerf === 0)
check('counter-check: each of the six named budget tests is listed somewhere (the list is not stale)', lost.length === 0,
  `not listed in either project: ${lost.map((w) => w.title.slice(0, 60)).join(' | ')}`)

// 1. Runner, already green: a run with skipped tests is refused.
const full = { stats: { expected: 68, unexpected: 0, flaky: 0, skipped: 0 }, suites: [] }
const withSkips = { stats: { expected: 65, unexpected: 0, flaky: 0, skipped: 3 }, suites: [] }
const v0 = verdict({ scope: 'gate', recorded: 68, repeat: 1, playwrightStatus: 0, report: full, notInGate: [] })
const v1 = verdict({ scope: 'gate', recorded: 68, repeat: 1, playwrightStatus: 0, report: withSkips, notInGate: [] })
check('runner control: a whole run with no skip passes', v0.code === 0, JSON.stringify(v0))
check('runner (already green): a run with 3 skipped tests is refused, and the skip is named', v1.code !== 0 && v1.problems.some((p) => /skipped/.test(p)), JSON.stringify(v1))

// 2. Membership, the reproducer.
const outside = where.filter((w) => w.inQaStand !== 1)
check(`membership: each of the ${BUDGET_TESTS.length} named budget tests is collected by the counted project qa-stand, once`,
  outside.length === 0,
  `actual: ${where.filter((w) => w.inQaStand === 1).length} of ${BUDGET_TESTS.length} in qa-stand; outside the count: ${outside.map((w) => `"${w.title.slice(0, 50)}..." (qa-stand ${w.inQaStand}, qa-perf ${w.inQaPerf})`).join('; ')}`)

console.log(failures.length ? `r14-membership: ${failures.length} check(s) failed` : 'r14-membership: all checks passed')
process.exit(failures.length ? 1 : 0)
