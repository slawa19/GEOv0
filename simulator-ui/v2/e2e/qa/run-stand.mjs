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
 * A skipped test is refused too: in a gate it is a check that did not happen.
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

/**
 * Tests that run, by scope. Every movement is a dated line:
 * - 2026-10-11 (038 S2): gate 62, full 185. Of the gate, 31 are the tests of S0/S1 and 31 are `surfaces.qa.ts` (it held 7
 *   measurements before); 123 more of that file run under `@extended`.
 * - 2026-10-11 (038 S2): gate 62 -> 61, one test of S1 named in NOT_IN_GATE below; full unchanged.
 */
export const EXPECTED = { gate: 61, full: 185 }

/**
 * Tests kept OUT of the required subset by name, although they carry no `@extended` tag. Each line has its reason
 * and date; the test itself is not touched and still runs in the `full` scope. The count above is the counter-check:
 * a title that stops matching anything puts the test back and the gate reads 62 against 61.
 */
export const NOT_IN_GATE = [
  {
    title: 'R4 - a 3-edge beam started from deep sleep is drawn on every frame of its flight, and the loop sleeps again afterwards',
    since: '2026-10-11',
    why: 'Failed once in the required step on the CI runner (run 38152711087, ubuntu): "there is a spark in the FX state on every one '
      + 'of them - Expected: 0, Received: 1", and passed in five other executions on the same runner and in every local one. The event '
      + 'reaches the product over the SSE stream in real time while the test steps the fake clock at once, so on a loaded machine the '
      + 'first frame can be stepped before the event is taken. A race of the test, not a measured defect of the product; a required '
      + 'gate cannot carry it. Recipient: the owner of the motion oracle, BACKLOG 038-1.',
  },
]

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

const here = fileURLToPath(new URL('.', import.meta.url))
const root = resolve(here, '..', '..')
const out = resolve(process.env.GEO_SIMULATOR_PLAYWRIGHT_OUTPUT_DIR ?? resolve(root, '..', '..', '.local-run', 'playwright', 'simulator', 'qa'))
const resultFile = `${out}-result.json`
const reportDir = `${out}-report`
rmSync(resultFile, { force: true })

const cli = createRequire(import.meta.url).resolve('@playwright/test/cli')
const args = [cli, 'test', '--config=e2e/playwright.qa.config.ts', '--project=qa-stand', '--reporter=list,json,html', ...extra]
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

if (!existsSync(resultFile)) {
  console.error(`run-stand: Playwright left no result file (${resultFile}), exit ${run.status}: the run did not happen, which is not a pass`)
  process.exit(1)
}
const stats = JSON.parse(readFileSync(resultFile, 'utf8')).stats
const ran = stats.expected + stats.unexpected + stats.flaky
const want = EXPECTED[scope] * repeat
console.log(`run-stand [${scope}]: ran ${ran} (as expected ${stats.expected}, failed ${stats.unexpected}, flaky ${stats.flaky}), skipped ${stats.skipped}, `
  + `recorded ${want}; ${seconds} s; HTML report: ${reportDir}`)

let code = run.status ?? 1
if (stats.skipped !== 0) {
  console.error(`run-stand: ${stats.skipped} test(s) were skipped - in a gate a skipped check is a check that did not happen`)
  code = code || 1
}
if (ran !== want) {
  console.error(`run-stand: ${ran} tests ran, ${want} are recorded in EXPECTED.${scope} (e2e/qa/run-stand.mjs). `
    + 'Fewer: a test is no longer collected or a filter took it out. More: a new test - record it, with a dated line.')
  code = code || 1
}
process.exit(code)
