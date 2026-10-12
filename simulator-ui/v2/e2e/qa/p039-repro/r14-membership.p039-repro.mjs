/**
 * 039 T3901 - R14, the RUNNER and MEMBERSHIP thirds (geometry: `r14-chord-geometry.p039-repro.ts`).
 *
 *   node e2e/qa/p039-repro/r14-membership.p039-repro.mjs        (from simulator-ui/v2; exit 1 on the base)
 *
 * Corrected form (spec 039, P-2, P-8; arbiter F9): runner behaviour, project membership and geometry are judged apart.
 *  1. RUNNER - already green: the verdict of the gate refuses ANY skipped test (`run-verdict.mjs`). The spec's
 *     "refuse a skip without a recorded reason" would be weaker than what stands; the rule is not to be loosened.
 *  2. MEMBERSHIP - the reproducer: the deterministic budgets (tests titled "budget: ...", counts taken on `page.clock`)
 *     are members of the COUNTED project `qa-stand`. Today they are collected by `qa-perf` only, which the gate never runs.
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
const isBudget = (t) => /^budget:/.test(t.title)
const budgetsInStand = stand.filter(isBudget)
const budgetsInPerf = perf.filter(isBudget)
const row = {
  qaStand: { tests: stand.length, gateSubset: stand.filter((t) => !t.title.includes('@extended')).length, files: byFile(stand) },
  qaPerf: { tests: perf.length, files: byFile(perf) },
  budgetTestsInQaStand: budgetsInStand.length,
  budgetTestsInQaPerfOnly: budgetsInPerf.map((t) => `${t.file}: ${t.title.slice(0, 100)}`),
}
console.log('[039 R14 membership]', JSON.stringify(row, null, 1))

const failures = []
const check = (name, ok, detail) => {
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}${ok ? '' : ` - ${detail}`}`)
  if (!ok) failures.push(name)
}

// Counter-checks: the listing saw both projects, and budget tests exist somewhere.
check('counter-check: qa-stand lists tests', stand.length > 0, `listed ${stand.length}`)
check('counter-check: budget tests exist in the stand', budgetsInStand.length + budgetsInPerf.length > 0, 'no test titled "budget: ..." is listed in either project')

// 1. Runner, already green: a run with skipped tests is refused.
const full = { stats: { expected: 68, unexpected: 0, flaky: 0, skipped: 0 }, suites: [] }
const withSkips = { stats: { expected: 65, unexpected: 0, flaky: 0, skipped: 3 }, suites: [] }
const v0 = verdict({ scope: 'gate', recorded: 68, repeat: 1, playwrightStatus: 0, report: full, notInGate: [] })
const v1 = verdict({ scope: 'gate', recorded: 68, repeat: 1, playwrightStatus: 0, report: withSkips, notInGate: [] })
check('runner control: a whole run with no skip passes', v0.code === 0, JSON.stringify(v0))
check('runner (already green): a run with 3 skipped tests is refused, and the skip is named', v1.code !== 0 && v1.problems.some((p) => /skipped/.test(p)), JSON.stringify(v1))

// 2. Membership, the reproducer.
check('membership: every deterministic budget test is in the counted project qa-stand',
  budgetsInStand.length > 0 && budgetsInPerf.length === 0,
  `actual: ${budgetsInStand.length} in qa-stand, ${budgetsInPerf.length} in qa-perf only; expected: all in qa-stand, 0 outside the count`)

console.log(failures.length ? `r14-membership: ${failures.length} check(s) failed` : 'r14-membership: all checks passed')
process.exit(failures.length ? 1 : 0)
