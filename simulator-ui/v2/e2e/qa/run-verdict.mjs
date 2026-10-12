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
