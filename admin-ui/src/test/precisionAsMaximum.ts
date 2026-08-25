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
 * It lives in one place because the two suites that needed it had already grown two slightly
 * different private copies - one handling negatives, one not - which is the same drift, at
 * small scale, that the three copies of the real rule produced at large scale
 * (`api/money-rendering-conformance.json`).
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
