/**
 * 038 S2 - the verdict of `run-stand.mjs` exercised on made-up runs: `node e2e/qa/run-verdict.selfcheck.mjs`.
 * Exits 0 and prints one line a case when every case gives the verdict written next to it; exits 1 otherwise.
 * Run by `tooling-tests/portable/test_p038_qa_stand_gate.py`. It does not run Playwright and does not show that the
 * JSON reporter of the installed Playwright still has the shape these cases assume - a real run of the gate does.
 */
import assert from 'node:assert/strict'

import { countExpectedFailures, debugArgProblems, destinations, playwrightArgs, verdict } from './run-verdict.mjs'

const stats = (o = {}) => ({ expected: 3, unexpected: 0, flaky: 0, skipped: 0, ...o })
const suites = (...expectedStatuses) => [{ specs: [], suites: [{ specs: expectedStatuses.map((expectedStatus) => ({ tests: [{ expectedStatus }] })) }] }]
const run = (o = {}) => ({ scope: 'gate', recorded: 3, repeat: 1, playwrightStatus: 0, report: { stats: stats(), suites: suites('passed', 'passed', 'passed') }, notInGate: [], ...o })
const entry = { title: 'R0 - a test that exists', why: 'a reason long enough to be one', since: '2026-10-11' }

/** [name, run, passes?, a word of the problem it must name] */
const CASES = [
  ['a clean run passes', run(), true, null],
  ['a clean run repeated three times passes', run({ repeat: 3, report: { stats: stats({ expected: 9 }), suites: [] } }), true, null],
  ['fewer tests than recorded', run({ report: { stats: stats({ expected: 2 }), suites: [] } }), false, 'are recorded'],
  ['more tests than recorded', run({ report: { stats: stats({ expected: 4 }), suites: [] } }), false, 'are recorded'],
  ['no test ran', run({ recorded: 0, report: { stats: stats({ expected: 0 }), suites: [] } }), false, 'no test ran'],
  ['a skipped test', run({ report: { stats: stats({ skipped: 1 }), suites: [] } }), false, 'skipped'],
  ['a flaky test, the count still right', run({ report: { stats: stats({ expected: 2, flaky: 1 }), suites: [] } }), false, 'retry'],
  ['an expected failure in the gate', run({ report: { stats: stats(), suites: suites('passed', 'failed', 'passed') } }), false, 'expected failures'],
  ['an expected failure in the full scope is allowed', run({ scope: 'full', report: { stats: stats(), suites: suites('passed', 'failed', 'passed') } }), true, null],
  ['a failed test', run({ playwrightStatus: 1, report: { stats: stats({ expected: 2, unexpected: 1 }), suites: [] } }), false, 'failed'],
  ['Playwright left no result', run({ playwrightStatus: 0, report: null }), false, 'no result'],
  ['Playwright crashed without a status', run({ playwrightStatus: null, report: null }), false, 'no result'],
  ['a well-formed exclusion', run({ notInGate: [entry] }), true, null],
  ['an exclusion without a reason', run({ notInGate: [{ ...entry, why: '' }] }), false, 'no reason'],
  ['an exclusion without a date', run({ notInGate: [{ ...entry, since: 'yesterday' }] }), false, 'no date'],
  ['an exclusion that is a bare title', run({ notInGate: ['R0 - a test that exists'] }), false, 'does not name a test'],
  // The debug scope (039 slice A): a slice, no recorded number - the count is not judged, everything else is.
  ['debug: a clean slice of any size passes', run({ scope: 'debug', recorded: null, report: { stats: stats({ expected: 1 }), suites: [] } }), true, null],
  ['debug: a failed test', run({ scope: 'debug', recorded: null, playwrightStatus: 1, report: { stats: stats({ expected: 1, unexpected: 1 }), suites: [] } }), false, 'failed'],
  ['debug: a filter that selected nothing', run({ scope: 'debug', recorded: null, report: { stats: stats({ expected: 0 }), suites: [] } }), false, 'no test ran'],
  ['debug: a skipped test', run({ scope: 'debug', recorded: null, report: { stats: stats({ skipped: 1 }), suites: [] } }), false, 'skipped'],
  ['debug: a flaky test', run({ scope: 'debug', recorded: null, report: { stats: stats({ expected: 2, flaky: 1 }), suites: [] } }), false, 'retry'],
  ['debug: Playwright left no result', run({ scope: 'debug', recorded: null, report: null }), false, 'no result'],
  ['debug: given a recorded number', run({ scope: 'debug' }), false, 'not compared with one'],
  // ... and the leniency of the debug scope does not leak into the counted ones.
  ['the gate without a recorded number', run({ recorded: null }), false, 'no recorded number'],
  ['the full scope with a recorded number of NaN', run({ scope: 'full', recorded: Number.NaN }), false, 'no recorded number'],
]

let failed = 0
for (const [name, input, passes, word] of CASES) {
  try {
    const v = verdict(input)
    assert.equal(v.code === 0, passes, `exit code ${v.code}; problems: ${JSON.stringify(v.problems)}`)
    assert.equal(v.problems.length === 0, passes, 'a non-zero exit names its reason, a zero exit has none')
    if (word) assert.ok(v.problems.some((p) => p.includes(word)), `no problem mentions "${word}": ${JSON.stringify(v.problems)}`)
    console.log(`ok   ${name}`)
  } catch (e) {
    failed += 1
    console.log(`FAIL ${name}: ${e.message}`)
  }
}
assert.equal(countExpectedFailures({ suites: suites('failed', 'passed', 'failed') }), 2)
// What a caller prints is decided by `counted`: a debug run never carries the mark of a counted verdict.
assert.deepEqual([verdict(run()).counted, verdict(run({ scope: 'full' })).counted, verdict(run({ scope: 'debug', recorded: null })).counted], [true, true, false])
assert.equal(verdict(run({ scope: 'debug', recorded: null })).want, null)
// THE ARGUMENTS AND THE FILES OF A DEBUG RUN (039 slice A, review of 0d0deae3). [arguments, accepted?, a word of the refusal]
const ARGS = [
  [['--grep', 'P4 order'], true, null],
  [['motion.qa', '--grep=R3', '-x', '--headed', '--repeat-each', '3', '--workers=1', '--trace', 'on'], true, null],
  [['--grep', '--reporter=html'], true, null], // the VALUE of --grep, whatever it looks like
  [['--pass-with-no-tests', '--list', '--last-failed'], true, null],
  [['--reporter=json,html', '--', 'console-guard'], false, '"--" is not accepted'], // the run that escaped the stand
  [['--'], false, '"--" is not accepted'],
  [['--grep', '--'], false, 'has no value'],
  [['--grep'], false, 'has no value'],
  [['--reporter=html'], false, '"--reporter=html" is not accepted'],
  [['--reporter', 'html'], false, '"--reporter" is not accepted'],
  [['--output=x'], false, 'not accepted'],
  [['--output', 'x'], false, 'not accepted'],
  [['--config=x'], false, 'not accepted'],
  [['-c', 'x'], false, 'not accepted'],
  [['-cplaywright.config.ts'], false, 'not accepted'],
  [['--project=qa-perf'], false, 'not accepted'],
  [['--project', 'qa-perf'], false, 'not accepted'],
  [['--retries=2'], false, 'not accepted'],
  [['--shard=1/2'], false, 'not accepted'],
  [['-u'], false, 'not accepted'],
  [['--update-snapshots'], false, 'not accepted'],
  [['--tsconfig=x'], false, 'not accepted'],
  [['--test-list=x'], false, 'not accepted'],
  [['--ui'], false, 'not accepted'],
  [['-xg', 'R3'], false, 'not accepted'],
  [['-j4'], false, 'not accepted'],
  [['--grepx=1'], false, 'not accepted'],
]
for (const [args, accepted, word] of ARGS) {
  const name = `debug arguments: ${args.join(' ')}`
  try {
    const problems = debugArgProblems(args)
    assert.equal(problems.length === 0, accepted, JSON.stringify(problems))
    if (word) assert.ok(problems.some((p) => p.includes(word)), `no refusal mentions ${word}: ${JSON.stringify(problems)}`)
    console.log(`ok   ${name}`)
  } catch (e) {
    failed += 1
    console.log(`FAIL ${name}: ${e.message}`)
  }
}
const STRUCTURE = [
  ['a debug run shares no file with a counted run', () => {
    const [d, g, f] = ['debug', 'gate', 'full'].map((s) => destinations(s, '/tmp/out'))
    assert.deepEqual(g, f)
    assert.equal(new Set([d.resultFile, d.reportDir, g.resultFile, g.reportDir]).size, 4)
    for (const p of [d.resultFile, d.reportDir]) assert.ok(!p.startsWith(g.reportDir) && !g.reportDir.startsWith(p), p)
  }],
  ["the stand's own options stand before every caller argument, in every scope", () => {
    for (const scope of ['debug', 'gate', 'full']) {
      const args = playwrightArgs(scope, ['CALLER-1', 'CALLER-2'], [])
      assert.deepEqual(args.slice(0, 4), ['test', '--config=e2e/playwright.qa.config.ts', '--project=qa-stand', '--retries=0'], scope)
      const first = args.indexOf('CALLER-1')
      for (const own of ['--config=', '--project=', '--retries=', '--reporter=']) assert.ok(args.findIndex((a) => a.startsWith(own)) < first, `${scope}: ${own}`)
      assert.equal(args.filter((a) => a.startsWith('--reporter')).length, 1, scope)
      assert.deepEqual(args.slice(-2), ['CALLER-1', 'CALLER-2'], scope)
    }
  }],
  ['a debug run has no HTML reporter; the counted runs have it', () => {
    assert.ok(playwrightArgs('debug', [], []).includes('--reporter=list,json'))
    for (const scope of ['gate', 'full']) assert.ok(playwrightArgs(scope, [], []).includes('--reporter=list,json,html'), scope)
  }],
  ['only the gate leaves out @extended and the named exclusions', () => {
    assert.ok(playwrightArgs('gate', [], ['R0 (x)']).includes('--grep-invert=@extended|R0 \\(x\\)'))
    for (const scope of ['debug', 'full']) assert.ok(!playwrightArgs(scope, [], ['R0 (x)']).some((a) => a.startsWith('--grep-invert')), scope)
  }],
]
for (const [name, check] of STRUCTURE) {
  try { check(); console.log(`ok   ${name}`) } catch (e) { failed += 1; console.log(`FAIL ${name}: ${e.message}`) }
}
const total = CASES.length + ARGS.length + STRUCTURE.length
console.log(`${total - failed} of ${total} cases`)
process.exit(failed ? 1 : 0)
