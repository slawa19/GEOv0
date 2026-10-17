/**
 * 038 S2 - the QA stand as a gate: one entry, two counted scopes and one debug scope.
 *
 *   node e2e/qa/run-stand.mjs gate   the required subset: project `qa-stand` without the tests tagged `@extended`
 *                                    (clock, pixels, the window matrix in the `saas` and `hud` themes, axe, which theme is
 *                                    shown). No wall-clock thresholds.
 *   node e2e/qa/run-stand.mjs full   the whole `qa-stand` project: the same plus the gallery (frames and axe on a settled
 *                                    graph) and the other viewports, in both themes.
 *   node e2e/qa/run-stand.mjs debug [playwright arguments]
 *                                    a SLICE of the `qa-stand` project, for debugging (`npm run test:e2e:qa -- --grep ...`).
 *
 * `gate` and `full` write the standard Playwright HTML report (frames of the matrix are its attachments) into
 * `<output dir>-report` and REFUSE, exit 1, when the number of tests that ran is not the recorded one - in either
 * direction (AGENTS.md §5, §6): a filter that selected nothing, a file that stopped being collected and an unrecorded
 * new test fail alike. Refused too: a skipped test, a flaky one (retries are off), and - in the gate - a test that is
 * an expected failure. The verdict is `verdict()` of `run-verdict.mjs`, a pure function exercised by
 * `run-verdict.selfcheck.mjs`. The only extra argument they accept is `--repeat-each=N` (the expected number is multiplied).
 *
 * `debug` (039 slice A, 2026-10-12) is NOT a gate and says so in its first and its last line. Until that date
 * `npm run test:e2e:qa` was a bare `playwright test`: a run nobody judged, whose "N passed" read like a result of the
 * stand. It now comes here: the filter is the caller's, the number that ran is compared with nothing, and the line it
 * ends with carries no "recorded" and names the two counted entries. It still exits non-zero on a failed test, on a
 * filter that selected nothing, on a skip and on a flaky test, so a red slice is red; a green slice proves only itself.
 * It writes no HTML report and its files have their own names (`<out>-debug-result.json`; the HTML variables point at
 * `<out>-debug-report`): the result and the report of a counted run are never overwritten by it.
 * Its arguments are a CLOSED list (`debugArgProblems` in `run-verdict.mjs`): file filters, `--grep` and a few options
 * that pace a run. Everything else is refused - `--`, `--config`, `--project`, `--reporter`, `--output`, `--retries`
 * among them. The first form (refuse two options, pass the rest) was escaped at runtime by `-- <file>`: see there.
 *
 * What this does NOT see: the composition behind the number (one test replaced by another passes), and anything about
 * the perf project (`qa-perf`, slice S3) - it is not run here.
 */
import { spawnSync } from 'node:child_process'
import { existsSync, readFileSync, rmSync } from 'node:fs'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { debugArgProblems, destinations, exclusionProblems, playwrightArgs, verdict } from './run-verdict.mjs'

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
 * - 2026-10-12 (039 slice A, property 4 by intervals; BACKLOG 038-1 (a)): gate 80 -> 81, full 203 -> 204. One test,
 *   `leg-order.qa.ts`: the judge of leg order on made-up legs - the cases the count of common frames accepted
 *   (alternating frames, a leg inside a dark hole of the leg before it), the handoff allowance to the frame, an
 *   unpainted leg, and the same verdicts at a third of the tempo. No browser page; under a second.
 * - 2026-10-12 (039 slice B, PR 1 - palette, roles, legend): gate 81 -> 85, full 204 -> 208. Four tests, one new file,
 *   `data-colours.qa.ts`: the data tokens are one value under every theme; the legend names what the scene contains in
 *   the resolved colours; a live theme switch redraws by itself and leaves the base canvas byte-identical; canvas,
 *   legend and node card move together when a token changes. In the GATE, not `@extended`: they hold the three
 *   class-1 risks of the slice's design, and nothing else that a pull request runs sees a pixel of the new colours.
 *   Real clock (nothing in them is about time), about 45 s locally in one worker. No existing test left or changed scope.
 * - 2026-10-16 (039 slice B, PR 3 - themes and the stand): gate 85 -> 114, full 208 -> 201. The app offers two themes,
 *   `saas` (the default) and `hud` (owner's decision R1; slice B, decision 6), and the matrix walks exactly those. Exact
 *   accounting, gate: +25 the required per-entry test of `surfaces.qa.ts` in `saas` (geometry, targets, axe, declared
 *   policies - until now `hud` alone; the design of slice B asks for geometry and policies in both remaining themes,
 *   and `saas` is now what a user sees first); +1 `matrix [saas] page` moved from `@extended` into the gate (the
 *   default theme's page axe, as `hud`'s was); +3 a new file, `themes.qa.ts` (the first visit; a stored `shadcn` and
 *   `?theme=library` normalised; URL over storage, back/forward and the palette resolved again after such a switch).
 *   85 + 25 + 1 + 3 = 114. Full: the gate's +29, and under `@extended` -50 the gallery of `shadcn` and `library`
 *   (2 x 25 entries), -2 their `page` tests, -1 `matrix [saas] page` (now in the gate, counted above), +17 the other
 *   viewports (1920x1080, resized 1024x640) of the 17 entries placed by CSS, now in `saas` as well as `hud`.
 *   208 + 29 - 50 - 2 - 1 + 17 = 201. No other test left or changed scope.
 * - 2026-10-16 (043 S1 - the first view and the "Подробнее" toggle): gate 114 -> 122, full 201 -> 217. Exact accounting,
 *   gate: +4 a new file, `layout.qa.ts` (reproducer (d): service surfaces absent from the first view and present after
 *   a pointer click on the toggle; Tab through the first view; the canvas share at 1366x768 in `saas` and `hud`);
 *   +4 two new entries of the matrix in both themes, `top-bar-interact-details` and `bottom-bar-details` (the bars
 *   with Details on: the state the service controls now live in). 114 + 4 + 4 = 122. Full: the gate's +8, and under
 *   `@extended` +4 the gallery of the two entries in both themes and +4 their other viewports (both are placed by CSS).
 *   201 + 8 + 4 + 4 = 217. No test left or changed scope; seven entries (four menus, `top-bar-auto-run`,
 *   `graph-navigator`, `analytics-panel`) now first hold that their surface is NOT rendered on the first view
 *   (`bootEntry`, `details.hiddenByDefault`) and then turn "Подробнее" on.
 * - 2026-10-16 (043 S2 - one screen, autostart, the pause on a manual action): gate 122 -> 127, full 217 -> 222. One new
 *   file, `one-screen.qa.ts`, five tests in the gate (advisor's decision 2026-10-16: the new S2 tests go into the stand
 *   gate): (b) a first visit starts a live run at intensity 30 and its payment is drawn; (c) "Send Payment" pauses until
 *   "Поток", cancel does not resume; (c) a participant chosen pauses; (c) a run somebody paused is not resumed or
 *   doubled at opening; (d) zero navigations into the manual flow. 122 + 5 = 127; full 217 + 5 = 222. No test left or
 *   changed scope; the matrix lost its `ui` field (one screen: every entry opens the same address), not an entry.
 * - 2026-10-17 (043 S2, review round): gate 127 -> 128, full 222 -> 223. One test of `one-screen.qa.ts`: a start the server
 *   refuses by its global limit of runs shows the Russian line chosen by the page's own `lang` (`index.html`), and the
 *   three actions are disabled without a run (external review, Gemini B3/B4).
 * - 2026-10-17 (043 S2, the owner's decision on review item B2): gate 128 -> 129, full 223 -> 224. One test of
 *   `one-screen.qa.ts`: Send Payment opens its window in the right rail under the top bar, the three actions do not
 *   move when the flow locks them, focus goes into the window.
 */
export const EXPECTED = { gate: 129, full: 224 }

/**
 * Tests kept OUT of the required subset by name, although they carry no `@extended` tag: each with its title, reason
 * and date. The count above is the counter-check of the list - a title that matches nothing puts the test back. Empty
 * today (the one entry of 2026-10-11, the S1 test R4, left the list the same day; what made it red was the phase of the
 * fake clock, see the dated line of `EXPECTED` above).
 */
export const NOT_IN_GATE = []

/** The first and the last line of a debug run. A guard reads this text (`tooling-tests/portable/test_p038_qa_stand_gate.py`). */
export const DEBUG_BANNER = 'DEBUG-ONLY, NOT A GATE'

const scope = process.argv[2]
const extra = process.argv.slice(3)
const debug = scope === 'debug'
if (!debug && !Object.hasOwn(EXPECTED, scope ?? '')) {
  console.error(`run-stand: the scope is "gate", "full" or "debug", got "${scope ?? ''}"`)
  process.exit(1)
}
let repeat = 1
// A debug run accepts a CLOSED list of arguments (`debugArgProblems`): what is not named there is refused, the option
// terminator `--` first of all.
const refused = debug ? debugArgProblems(extra) : []
if (refused.length) {
  for (const problem of refused) console.error(`run-stand: ${problem}`)
  process.exit(1)
}
for (const a of debug ? [] : extra) {
  const m = /^--repeat-each=(\d+)$/.exec(a)
  if (!m) {
    console.error(`run-stand: "${a}" is not accepted - the gate runs whole. For a slice (debug-only, not counted): npm run test:e2e:qa -- ${a}`)
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
const { resultFile, reportDir } = destinations(scope, out)
rmSync(resultFile, { force: true })
if (debug) console.log(`run-stand [debug]: ${DEBUG_BANNER} - a slice of the qa-stand project chosen by: ${extra.join(' ') || '(no filter)'}`)

const cli = createRequire(import.meta.url).resolve('@playwright/test/cli')
// No retries: a test that passes on the second try failed on the first, and the verdict refuses a flaky run anyway.
// The stand's own options (config, project, no retries, the reporters whose JSON the verdict reads) come first in every
// scope: `playwrightArgs` of `run-verdict.mjs`, where the selfcheck holds the order.
const args = [cli, ...playwrightArgs(scope, extra, NOT_IN_GATE.map((t) => t.title))]
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
    PW_TEST_HTML_REPORT_OPEN: 'never',
    // Variables of the caller's shell that would add a reporter or move the JSON: set here, so the caller's are not read.
    PW_TEST_REPORTER: '',
    PLAYWRIGHT_JSON_OUTPUT_DIR: '',
  },
})
const seconds = Math.round((Date.now() - started) / 1000)

const report = existsSync(resultFile) ? JSON.parse(readFileSync(resultFile, 'utf8')) : null
const v = verdict({ scope, recorded: debug ? null : EXPECTED[scope], repeat, playwrightStatus: run.status, report, notInGate: debug ? [] : NOT_IN_GATE })
const s = report?.stats
const ran = `ran ${v.ran} (as expected ${s?.expected ?? 0}, failed ${s?.unexpected ?? 0}, flaky ${s?.flaky ?? 0}), skipped ${s?.skipped ?? 0}`
// The mark of a counted verdict is "recorded N" - the line a CI log is read by. A run that was not counted never prints it.
if (v.counted) console.log(`run-stand [${scope}]: ${ran}, recorded ${v.want}; ${seconds} s; HTML report: ${reportDir}`)
else {
  console.log(`run-stand [debug]: ${DEBUG_BANNER} - ${ran}; compared with no number; ${seconds} s. This is not the verdict of the stand: `
    + `the counted entries are "npm run test:e2e:qa:gate" (${EXPECTED.gate} tests) and "npm run test:e2e:qa:report" (${EXPECTED.full}).`)
}
for (const problem of v.problems) console.error(`run-stand: ${problem}`)
process.exit(v.code)
