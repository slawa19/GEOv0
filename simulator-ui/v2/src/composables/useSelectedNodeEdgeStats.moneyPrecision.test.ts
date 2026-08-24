import { describe, expect, it } from 'vitest'
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
  it.each([
    { equivalent: 'GRAM', limit: '12.3456' },
    { equivalent: 'HOUR', limit: '12.3' },
    { equivalent: 'UAH', limit: '12.34' },
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

      expect(
        rendered,
        `A ${equivalent} trust limit of ${limit} is exactly representable at precision ${precision}, `
          + `so the node card must show it unchanged. It shows "${rendered}" instead, i.e. the operator `
          + 'reads a different limit than the one that is in force.',
      ).toBe(limit)
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
