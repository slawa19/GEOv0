/**
 * The money formatter `T1211` removed, kept alive on purpose as a NEGATIVE CONTROL.
 *
 * `formatDecimalFixed` quantized to exactly `precision` fraction digits with ROUND_HALF_UP,
 * so it changed the value rather than its spelling: `0.05` of the shipped HOUR
 * (`precision: 1`) rendered as `0.1`. Three separate test suites agreed with it, because
 * each of their samples happened to be one the wrong implementation also satisfies.
 *
 * A sample that both implementations answer identically proves nothing, and mutating the
 * production code cannot reveal that - the blind element is the measurer, not the code. So
 * every money-rendering suite here asserts its own sample against this function: at least
 * one case must come out differently, or the suite would pass without the fix.
 *
 * IT IS THE SECONDARY GUARD, NOT THE PRIMARY ONE, and T1211's repeat review is why that has to be
 * said here. One negative control proves only that the sample is not equivalent to ONE wrong
 * function; it says nothing about the class of plausible errors. The reviewer demonstrated it:
 * a mutation that loses the sign only at precision 0 passed both local suites untouched.
 *
 * The primary guard is `api/money-rendering-conformance.json`, which admin-ui's own
 * `decimal.conformance.test.ts` runs against this very formatter, and which is measured against
 * thirteen wrong implementations in
 * `tests/unit/test_p012_t1211_money_rendering_conformance.py`. That mutation reddens there.
 * Do not read a green local suite as coverage of the rule.
 *
 * It lives in one place because the two suites that needed it had already grown two slightly
 * different private copies - one handling negatives, one not - which is the same drift, at
 * small scale, that the three copies of the real rule produced at large scale
 * (`api/money-rendering-conformance.json`).
 *
 * IS IT REALLY THE REMOVED FUNCTION? Measured once, deliberately not pinned: swept against
 * `git show dd1218d:admin-ui/src/utils/decimal.ts`'s `formatDecimalFixed` over 3456
 * value/precision pairs (four magnitudes x fifteen fraction patterns x both signs x
 * precisions 0,1,2,3,4,8) - zero divergences.
 *
 * That equivalence is NOT load-bearing and is not re-checked by anything. What the suites
 * need from this function is not fidelity to one deleted commit but that it be A PLAUSIBLE
 * WRONG IMPLEMENTATION they must distinguish - precision as a maximum, rounding half-up. If
 * it drifts from the historic original while staying that, nothing is lost. Said out loud
 * because the opposite assumption would make a future reader treat a harmless edit here as a
 * broken guard, and because the wave's own lesson is that an unstated premise outlives the
 * person who held it.
 */
export function renderWithPrecisionAsMaximum(value: string, digits: number): string {
  const negative = value.startsWith('-')
  const body = negative || value.startsWith('+') ? value.slice(1) : value
  const [int = '0', frac = ''] = body.split('.')

  const scaled = BigInt(int + frac.padEnd(Math.max(frac.length, digits), '0'))
  const dropped = Math.max(0, frac.length - digits)
  const divisor = 10n ** BigInt(dropped)
  const quotient = scaled / divisor
  const rounded = dropped > 0 && (scaled % divisor) * 2n >= divisor ? quotient + 1n : quotient

  const text = rounded.toString().padStart(digits + 1, '0')
  const out =
    digits === 0 ? text : `${text.slice(0, text.length - digits)}.${text.slice(text.length - digits)}`

  // `-0.00` would read as a debt; the removed formatter did not print one either.
  return negative && /[1-9]/.test(rounded.toString()) ? `-${out}` : out
}
