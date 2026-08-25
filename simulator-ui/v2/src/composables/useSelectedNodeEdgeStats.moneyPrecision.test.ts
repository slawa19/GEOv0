import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import {
  equivalentPrecision,
  resetEquivalentPrecisions,
  setEquivalentPrecisions,
} from '../config/equivalentPrecision'
import type { GraphSnapshot } from '../types'
import { computeNodeEdgeStats } from './useSelectedNodeEdgeStats'

/**
 * RT-012-4 (F-012-5) — reproducer for `formatAmount2` on its real money caller.
 *
 * The trust limits shown on the node card go through
 * `useSelectedNodeEdgeStats.ts:39-40` -> `numberFormat.ts:73-75`:
 *
 *   asFiniteNumber(l.trust_limit)  // decimal string -> binary double
 *   formatAmount2(sum)             // -> sum.toFixed(2), digit count is a constant
 *
 * Two defects live there, and both are user-visible on a trust limit:
 *   1. the fraction-digit count is the constant 2, not the equivalent's `precision`;
 *   2. the amount travels as a JS `number`, so amounts the ledger can store exactly
 *      (`Numeric(20, 8)`) are already wrong before they reach the formatter.
 *
 * `Equivalent.precision` is an integer 0..18 (`app/schemas/equivalents.py:39`) and is
 * published to the frontend by `GET /api/v1/equivalents`; `simulator-ui/v2/src` never
 * asks for it (F-012-4). The catalogue below is therefore stated by the test, the way
 * the UI would have to state it once it does ask.
 */

/** Precision as the equivalents catalogue declares it. UAH/HOUR mirror `seeds/equivalents.json`. */
const PRECISION_BY_EQUIVALENT: Record<string, number> = {
  UAH: 2,
  HOUR: 1,
  GRAM: 4,
  MICRO: 8,
}

function fractionDigits(rendered: string): number {
  const dot = rendered.indexOf('.')
  return dot < 0 ? 0 : rendered.length - dot - 1
}

function snapshotWithOutgoingLimit(equivalent: string, trustLimit: string): GraphSnapshot {
  return {
    equivalent,
    generated_at: '2026-08-24T00:00:00Z',
    nodes: [
      { id: 'A', name: 'A' },
      { id: 'B', name: 'B' },
    ],
    links: [{ source: 'A', target: 'B', trust_limit: trustLimit }],
  }
}

function renderOutLimit(equivalent: string, trustLimit: string): string {
  return computeNodeEdgeStats(snapshotWithOutgoingLimit(equivalent, trustLimit), 'A').outLimitText
}

describe('RT-012-4: node-card trust limits ignore the equivalent precision', () => {
  // 012 / T1210 finding 8. These cases used to declare GRAM and MICRO in the table above and
  // nowhere else: neither code exists anywhere in the repository, so `equivalentPrecision()` fell
  // through to the default of 2 - and because the rule is MINIMUM digits, a value carrying exactly
  // its own precision renders in full under any formatter that does not truncate. So the
  // digit-count assertions held against code entirely blind to the equivalent: the review mutated
  // `equivalentPrecision` to return a constant 1 and four of the five cases still passed.
  //
  // The catalogue is now pushed through the real registry, so the code has to READ these
  // precisions instead of coinciding with them. The first test below holds that line: a code in
  // the table that the registry does not answer for is a row measuring the default, and a row
  // measuring the default proves nothing here.
  beforeEach(() => {
    setEquivalentPrecisions(
      Object.entries(PRECISION_BY_EQUIVALENT).map(([code, precision]) => ({ code, precision })),
    )
  })

  afterEach(() => {
    resetEquivalentPrecisions()
  })

  it('every equivalent in the case table is really registered at that precision', () => {
    for (const [code, declared] of Object.entries(PRECISION_BY_EQUIVALENT)) {
      expect(
        equivalentPrecision(code),
        `${code} appears in this file's table at precision ${declared}, but the registry answers `
          + `${equivalentPrecision(code)}. A row driven by an unregistered code measures the `
          + 'default, and under the minimum-digit rule that passes even against a formatter that '
          + 'ignores the equivalent entirely.',
      ).toBe(declared)
    }
  })

  // Each amount carries FEWER digits than its equivalent declares, which is what makes these rows
  // load-bearing under a minimum-digit rule: a correct renderer pads up to `precision`, and one
  // that ignores the equivalent cannot. The first version used an amount with exactly `precision`
  // digits everywhere, so every row rendered in full whatever precision was believed, and the
  // assertion degenerated into a one-sided check (T1210 finding 8).
  it.each([
    { equivalent: 'GRAM', limit: '12.3' },
    { equivalent: 'MICRO', limit: '12.3' },
    { equivalent: 'HOUR', limit: '12' },
    { equivalent: 'UAH', limit: '12.3' },
  ])(
    'renders a $equivalent trust limit with exactly the digits that equivalent declares',
    ({ equivalent, limit }) => {
      const precision = PRECISION_BY_EQUIVALENT[equivalent]
      const rendered = renderOutLimit(equivalent, limit)

      expect(
        fractionDigits(rendered),
        `Node card shows "${rendered}" for a ${equivalent} trust limit. ${equivalent} declares `
          + `precision ${precision}, so the operator must see ${precision} fraction digits: more `
          + `digits claim a precision the equivalent does not have, fewer hide value the ledger holds.`,
      ).toBe(precision)

      // The VALUE must survive, not the spelling. Padding `12.3` to `12.3000` at precision 4 is
      // the rule working, not a defect; dropping a digit, or moving one, is the defect. Comparing
      // the string verbatim here would forbid the padding the assertion above requires - the two
      // halves would contradict each other, which is what the first version of this file did once
      // its inputs stopped carrying exactly `precision` digits.
      expect(
        Number(rendered),
        `A ${equivalent} trust limit of ${limit} must still be worth ${limit} after rendering at `
          + `precision ${precision}. The node card shows "${rendered}", which is a different amount - `
          + 'the operator reads a limit other than the one in force.',
      ).toBe(Number(limit))
    },
  )

  it('reacts to the equivalent: one amount must not render identically under precision 1 and precision 4', () => {
    const limit = '12.3'
    const asHour = renderOutLimit('HOUR', limit)
    const asGram = renderOutLimit('GRAM', limit)

    expect(
      PRECISION_BY_EQUIVALENT.HOUR === PRECISION_BY_EQUIVALENT.GRAM,
      'Counter-check premise: HOUR and GRAM must declare different precision, otherwise this case proves nothing.',
    ).toBe(false)

    expect(
      asHour,
      `The same amount ${limit} renders as "${asHour}" under HOUR (precision `
        + `${PRECISION_BY_EQUIVALENT.HOUR}) and as "${asGram}" under GRAM (precision `
        + `${PRECISION_BY_EQUIVALENT.GRAM}). Identical output means the node card is blind to the `
        + 'equivalent it is displaying, so the digits it shows carry no information about the money.',
    ).not.toBe(asGram)
  })

  it('does not collapse two distinct storable amounts into one rendering', () => {
    // Both are exactly storable in the ledger column `Numeric(20, 8)`
    // (12 integer digits + 8 fraction digits = 20 significant digits) and they differ
    // by one atom at MICRO's precision 8. A JS double holds ~15-16 significant digits,
    // so `asFiniteNumber` maps both onto the same value before any formatting happens.
    const lower = '10000000000.12345678'
    const upper = '10000000000.12345679'

    const renderedLower = renderOutLimit('MICRO', lower)
    const renderedUpper = renderOutLimit('MICRO', upper)

    expect(
      renderedLower,
      `Two trust limits that differ by one atom (${lower} vs ${upper}) both render as `
        + `"${renderedLower}". Money is carried through a binary float, so two different limits the `
        + 'ledger can store are indistinguishable on screen — the operator cannot tell which one is set.',
    ).not.toBe(renderedUpper)
  })
})
