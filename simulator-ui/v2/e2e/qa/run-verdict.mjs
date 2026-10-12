/**
 * 038 S2 - the verdict of the QA stand as a gate, as a pure function: `run-stand.mjs` feeds it what Playwright left,
 * `run-verdict.selfcheck.mjs` feeds it made-up runs (and is run by `tooling-tests/portable/test_p038_qa_stand_gate.py`).
 * No I/O here.
 *
 * A run passes only when ALL of this holds; every other outcome is a non-zero exit with the reason named:
 *  - Playwright itself exited 0 and left a result;
 *  - at least one test ran, and exactly the recorded number did (either direction is a refusal);
 *  - nothing was skipped: in a gate a skipped check is a check that did not happen;
 *  - nothing was flaky: a test that passed on a retry failed first (retries are 0 for the stand, so this is also the
 *    alarm for somebody turning them on);
 *  - in the `gate` scope, no test is an EXPECTED failure (`test.fail`): Playwright counts those as "expected", and a
 *    known defect parked in the required subset would be green there for ever;
 *  - every entry of NOT_IN_GATE names its test, its reason and its date.
 *
 * THE DEBUG SCOPE (039 slice A, 2026-10-12) is a slice somebody chose with a filter: there is no number to compare it
 * with, so the count is NOT judged and the result says `counted: false` - it is never the verdict of the stand. Every
 * other refusal stands in it (a failure, no test at all, a skip, a flaky test, no result). The leniency cannot leak:
 * outside the debug scope a run without a recorded positive number is itself a refusal.
 */

/** Tests whose expected status is "failed" (`test.fail`), anywhere in the JSON report of Playwright. */
export function countExpectedFailures(report) {
  let n = 0
  const walk = (suite) => {
    for (const spec of suite.specs ?? []) for (const t of spec.tests ?? []) if (t.expectedStatus === 'failed') n += 1
    for (const child of suite.suites ?? []) walk(child)
  }
  for (const suite of report?.suites ?? []) walk(suite)
  return n
}

/** Problems of the NOT_IN_GATE list itself. */
export function exclusionProblems(notInGate) {
  const out = []
  for (const [i, x] of (notInGate ?? []).entries()) {
    const title = typeof x?.title === 'string' ? x.title.trim() : ''
    if (title.length < 10) out.push(`NOT_IN_GATE[${i}] does not name a test (title)`)
    if (typeof x?.why !== 'string' || x.why.trim().length < 20) out.push(`NOT_IN_GATE[${i}] has no reason (why)`)
    if (typeof x?.since !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(x.since)) out.push(`NOT_IN_GATE[${i}] has no date (since: YYYY-MM-DD)`)
  }
  return out
}

/**
 * THE ARGUMENTS OF A DEBUG RUN (039 slice A, external review of 0d0deae3, 2026-10-12). The first form of the debug scope
 * refused `--project` and `--config` and passed everything else through, with the stand's own options appended LAST.
 * Run at runtime: `debug --reporter=json,html -- console-guard` - Playwright's option terminator `--` turned the
 * appended `--config/--project/--retries/--reporter` into positional filters, the default `playwright.config.ts` ran
 * `e2e/console-guard.spec.ts` (not a test of the stand), the run exited 0, and the caller's HTML reporter replaced the
 * report of the last gate run in `<out>-report`.
 *
 * So the list is closed the other way round: what is not named here is refused. Named are the options that choose or
 * pace a slice and nothing that says WHAT is run by which config, where results go, or how failures are counted:
 * no `--` (the terminator), `--config`/`-c`, `--project`, `--reporter`, `--output`, `--retries`, `--shard`,
 * `--update-snapshots`/`-u`, `--tsconfig`, `--test-list`, `--only-changed`, `--ui`, `--no-deps`, `--global-timeout`,
 * `--fail-on-flaky-tests`, `--ignore-snapshots`, combined or attached short forms (`-xg`, `-j4`).
 * `--pass-with-no-tests` and `--list` are let through and neutralised by the verdict (no test ran: exit 1).
 */
export const DEBUG_FLAGS = ['--headed', '--debug', '-x', '--list', '--pass-with-no-tests', '--last-failed']
export const DEBUG_VALUE_OPTIONS = ['--grep', '-g', '--grep-invert', '--repeat-each', '--workers', '-j', '--max-failures', '--timeout', '--trace']

/** Why the arguments of a debug run are refused; empty when they are accepted. */
export function debugArgProblems(extra) {
  const out = []
  const list = 'accepted: file filters, ' + [...DEBUG_VALUE_OPTIONS.map((o) => `${o} <value>`), ...DEBUG_FLAGS].join(', ')
  for (let i = 0; i < extra.length; i += 1) {
    const a = String(extra[i])
    if (a === '--') {
      out.push('"--" is not accepted in a debug run: after the option terminator the stand\'s own config and project would be read as file filters')
      continue
    }
    if (!a.startsWith('-')) continue // a file filter
    if (DEBUG_FLAGS.includes(a)) continue
    const eq = a.indexOf('=')
    if (a.startsWith('--') && eq > 0 && DEBUG_VALUE_OPTIONS.includes(a.slice(0, eq))) continue
    if (DEBUG_VALUE_OPTIONS.includes(a)) {
      const value = extra[i + 1]
      if (value === undefined || value === '--') out.push(`"${a}" has no value`)
      i += 1 // the value, whatever it starts with
      continue
    }
    out.push(`"${a}" is not accepted in a debug run - it is a slice of the qa-stand project, by the stand's config, into the debug run's own files (${list})`)
  }
  return out
}

/** Where a run writes. A debug run has files of its own: nothing it does can replace the result or the report of a counted run. */
export function destinations(scope, out) {
  return scope === 'debug'
    ? { resultFile: `${out}-debug-result.json`, reportDir: `${out}-debug-report` }
    : { resultFile: `${out}-result.json`, reportDir: `${out}-report` }
}

/**
 * The arguments of `playwright test`. The stand's own options come FIRST in every scope and the caller's after them:
 * no caller argument can demote them to file filters, and none of them can be given twice - a counted scope accepts
 * `--repeat-each=N` only, a debug scope what `debugArgProblems` lets through. A debug run has no HTML reporter.
 */
export function playwrightArgs(scope, extra, notInGateTitles = []) {
  const literal = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const own = ['--config=e2e/playwright.qa.config.ts', '--project=qa-stand', '--retries=0', scope === 'debug' ? '--reporter=list,json' : '--reporter=list,json,html']
  if (scope === 'gate') own.push(`--grep-invert=${['@extended', ...notInGateTitles.map(literal)].join('|')}`)
  return ['test', ...own, ...extra]
}

/**
 * @param {{ scope: 'gate' | 'full' | 'debug', recorded: number | null, repeat: number, playwrightStatus: number | null,
 *           report: { stats?: { expected: number, unexpected: number, flaky: number, skipped: number }, suites?: unknown[] } | null,
 *           notInGate: unknown[] }} run
 * @returns {{ code: number, ran: number, want: number | null, counted: boolean, problems: string[] }}
 */
export function verdict(run) {
  const problems = [...exclusionProblems(run.notInGate)]
  const counted = run.scope !== 'debug'
  if (counted && !(Number.isInteger(run.recorded) && run.recorded > 0)) {
    problems.push(`the scope "${run.scope}" has no recorded number of tests (got ${JSON.stringify(run.recorded ?? null)}): only the debug scope runs uncounted`)
  }
  if (!counted && run.recorded != null) problems.push('the debug scope was given a recorded number: a slice is not compared with one')
  const want = counted ? run.recorded * run.repeat : null
  const stats = run.report?.stats
  if (!stats) {
    problems.push(`Playwright left no result (exit ${run.playwrightStatus}): the run did not happen, which is not a pass`)
    return { code: run.playwrightStatus || 1, ran: 0, want, counted, problems }
  }
  const ran = stats.expected + stats.unexpected + stats.flaky
  if (run.playwrightStatus !== 0) problems.push(`Playwright exited ${run.playwrightStatus}: ${stats.unexpected} test(s) failed`)
  if (ran === 0) problems.push('no test ran: a filter that selected nothing is not a pass')
  if (counted && ran !== want) {
    problems.push(`${ran} tests ran, ${want} are recorded in EXPECTED.${run.scope} (e2e/qa/run-stand.mjs). `
      + 'Fewer: a test is no longer collected or a filter took it out. More: a new test - record it, with a dated line.')
  }
  if (stats.skipped !== 0) problems.push(`${stats.skipped} test(s) were skipped - in a gate a skipped check is a check that did not happen`)
  if (stats.flaky !== 0) problems.push(`${stats.flaky} test(s) passed only on a retry - a flaky test failed first; the stand runs without retries`)
  const expectedFailures = countExpectedFailures(run.report)
  if (run.scope === 'gate' && expectedFailures !== 0) {
    problems.push(`${expectedFailures} test(s) of the required subset are expected failures (test.fail): a known defect does not belong `
      + 'in the required subset - tag its reproducer @extended')
  }
  return { code: run.playwrightStatus || (problems.length ? 1 : 0), ran, want, counted, problems }
}
