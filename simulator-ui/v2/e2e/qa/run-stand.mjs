/**
 * 038 S2 - the QA stand as a gate: one entry, two scopes.
 *
 *   node e2e/qa/run-stand.mjs gate   the required subset: project `qa-stand` without the tests tagged `@extended`
 *                                    (clock, pixels, the window matrix in the `hud` theme, axe). No wall-clock thresholds.
 *   node e2e/qa/run-stand.mjs full   the whole `qa-stand` project: the same plus the frames and axe of the other three themes.
 *
 * Both write the standard Playwright HTML report (frames of the matrix are its attachments) into `<output dir>-report`
 * and REFUSE, exit 1, when the number of tests that ran is not the recorded one - in either direction (AGENTS.md §5,
 * §6): a filter that selected nothing, a file that stopped being collected and an unrecorded new test fail alike.
 * Refused too: a skipped test, a flaky one (retries are off), and - in the gate - a test that is an expected failure.
 * The verdict is `verdict()` of `run-verdict.mjs`, a pure function exercised by `run-verdict.selfcheck.mjs`.
 *
 * The only extra argument accepted is `--repeat-each=N` (the expected number is multiplied). To run a slice, use
 * `npm run test:e2e:qa -- --grep ...`: a slice is not the gate and is not counted.
 *
 * What this does NOT see: the composition behind the number (one test replaced by another passes), and anything about
 * the perf project (`qa-perf`, slice S3) - it is not run here.
 */
import { spawnSync } from 'node:child_process'
import { existsSync, readFileSync, rmSync } from 'node:fs'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { exclusionProblems, verdict } from './run-verdict.mjs'

/**
 * Tests that run, by scope. Every movement is a dated line:
 * - 2026-10-11 (038 S2): gate 62, full 185. Of the gate, 31 are the tests of S0/S1 and 31 are `surfaces.qa.ts` (it held 7
 *   measurements before); 123 more of that file run under `@extended`.
 * - 2026-10-11 (038 S2): gate 62 -> 61, one test of S1 named in NOT_IN_GATE below; full unchanged.
 * - 2026-10-11 (038 S2): gate 61 -> 64, full 185 -> 187. The S1 test (R4) is back (62). The cause written here at the
 *   time, "a race of the helper", was WRONG: `pushEvent` now returns when the product has taken the frame (`stand.ts`),
 *   and that closed another race, not this one. Two tests of that, `delivery.qa.ts`. The real cause of the red R4 was the
 *   random phase of the fake clock on the 16 ms frame grid (R4 steps exactly the life of the beam, 75 x 16 = 1200 ms, and
 *   on phase 0 the last frame is empty). Fixed later the same day: the phase is pinned (`CLOCK_PHASE_MS` in `stand.ts`)
 *   and R4 judges a frame by the time it was drawn, not by its number.
 * - 2026-10-11 (038 S2, after the adversarial pass on a7f7e1a6): gate 64 -> 68, full 187 -> 191. Four tests: a card opened
 *   over an open menu, for two menus (D2); the four injected surfaces (D3); a frame written into nothing (D5).
 * - 2026-10-12 (039 slice A, R14): gate 68 -> 76, full 191 -> 199. Eight tests moved in from the perf project, none
 *   written: the six deterministic budgets (five of `budgets.qa.ts`, one of `no-gpu-effects.qa.ts`) and the two
 *   counter-checks that live beside them ("with a GPU believed present ..."), without which a budget of 0 filters
 *   would hold for a rule that removed the filter everywhere. All eight are counts on `page.clock`; the scheduled
 *   perf job was their only runner, so a pull request never ran them. They are in the GATE, not `@extended`: a budget
 *   the required step does not run is the state this line ends. Added time: 65 s locally (the eight tests, one worker; the gate 313 s);
 *   about 85 s on the CI runner by the timestamps of run 38200925497, whose gate step took 269 s - so the step is
 *   now past the five minutes the spec of 038 named as an orientation; `timeout-minutes` of the step is what holds.
 *   The wall-clock files of the perf project (frame time, heap, the FPS guard) did NOT move and must not.
 * - 2026-10-12 (039 slice A, delivery): gate 76 -> 78, full 199 -> 201. Two tests of `delivery.qa.ts`: a frame whose
 *   connection is lost before the product read it is refused and not counted as taken, with `pushFrames` holding
 *   `taken === pushed`; and a delivery during which the product's SSE handler threw is refused.
 * - 2026-10-12 (039 slice A, the chord): gate 78 -> 80, full 201 -> 203. Two tests of `chord.qa.ts`: the refusal of a
 *   route whose chord cannot be told from it, on made-up points; and, on an open six-leg route over seven nodes,
 *   property 3 accepting the beam on its legs and refusing the same payment drawn as one beam sender > receiver.
 */
export const EXPECTED = { gate: 80, full: 203 }

/**
 * Tests kept OUT of the required subset by name, although they carry no `@extended` tag: each with its title, reason
 * and date. The count above is the counter-check of the list - a title that matches nothing puts the test back. Empty
 * today (the one entry of 2026-10-11, the S1 test R4, left the list the same day; what made it red was the phase of the
 * fake clock, see the dated line of `EXPECTED` above).
 */
export const NOT_IN_GATE = []

const scope = process.argv[2]
const extra = process.argv.slice(3)
if (!(scope in EXPECTED)) {
  console.error(`run-stand: the scope is "gate" or "full", got "${scope ?? ''}"`)
  process.exit(1)
}
let repeat = 1
for (const a of extra) {
  const m = /^--repeat-each=(\d+)$/.exec(a)
  if (!m) {
    console.error(`run-stand: "${a}" is not accepted - the gate runs whole. For a slice: npm run test:e2e:qa -- ${a}`)
    process.exit(1)
  }
  repeat = Number(m[1])
}

const malformed = exclusionProblems(NOT_IN_GATE)
if (malformed.length) {
  for (const problem of malformed) console.error(`run-stand: ${problem}`)
  process.exit(1)
}

const here = fileURLToPath(new URL('.', import.meta.url))
const root = resolve(here, '..', '..')
const out = resolve(process.env.GEO_SIMULATOR_PLAYWRIGHT_OUTPUT_DIR ?? resolve(root, '..', '..', '.local-run', 'playwright', 'simulator', 'qa'))
const resultFile = `${out}-result.json`
const reportDir = `${out}-report`
rmSync(resultFile, { force: true })

const cli = createRequire(import.meta.url).resolve('@playwright/test/cli')
// No retries: a test that passes on the second try failed on the first, and the verdict refuses a flaky run anyway.
const args = [cli, 'test', '--config=e2e/playwright.qa.config.ts', '--project=qa-stand', '--retries=0', '--reporter=list,json,html', ...extra]
const literal = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
if (scope === 'gate') args.push(`--grep-invert=${['@extended', ...NOT_IN_GATE.map((t) => literal(t.title))].join('|')}`)
const started = Date.now()
const run = spawnSync(process.execPath, args, {
  cwd: root,
  stdio: 'inherit',
  env: {
    ...process.env,
    GEO_SIMULATOR_PLAYWRIGHT_OUTPUT_DIR: out,
    PLAYWRIGHT_JSON_OUTPUT_FILE: resultFile,
    PLAYWRIGHT_JSON_OUTPUT_NAME: resultFile,
    PLAYWRIGHT_HTML_OUTPUT_DIR: reportDir,
    PLAYWRIGHT_HTML_REPORT: reportDir,
    PLAYWRIGHT_HTML_OPEN: 'never',
  },
})
const seconds = Math.round((Date.now() - started) / 1000)

const report = existsSync(resultFile) ? JSON.parse(readFileSync(resultFile, 'utf8')) : null
const v = verdict({ scope, recorded: EXPECTED[scope], repeat, playwrightStatus: run.status, report, notInGate: NOT_IN_GATE })
const s = report?.stats
console.log(`run-stand [${scope}]: ran ${v.ran} (as expected ${s?.expected ?? 0}, failed ${s?.unexpected ?? 0}, flaky ${s?.flaky ?? 0}), skipped ${s?.skipped ?? 0}, `
  + `recorded ${v.want}; ${seconds} s; HTML report: ${reportDir}`)
for (const problem of v.problems) console.error(`run-stand: ${problem}`)
process.exit(v.code)
