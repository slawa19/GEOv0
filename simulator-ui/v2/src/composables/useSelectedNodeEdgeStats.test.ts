import { describe, expect, it } from 'vitest'
import type { GraphSnapshot } from '../types'
import { computeNodeEdgeStats, useSelectedNodeEdgeStats } from './useSelectedNodeEdgeStats'

/**
 * 012 / `T1208`, `C-B4-3-009`. This suite used to pin the money `trust_limit` in its old
 * form: numeric limits summed through a double and printed with a constant two digits. That
 * pinned `F-012-5` as the expected contract, so the fix could not be observed here.
 *
 * The precision-2 case is kept deliberately and unchanged, byte for byte — it is the
 * spec's counter-check that amounts which were already correct must not move. What is added
 * is a substitution counter-check: the same links under a different equivalent must not
 * render identically, or the digits carry no information about the money.
 */

function makeSnapshot(equivalent = 'UAH'): GraphSnapshot {
  return {
    equivalent,
    generated_at: '2026-01-25T00:00:00Z',
    nodes: [
      { id: 'A', name: 'A' },
      { id: 'B', name: 'B' },
      { id: 'C', name: 'C' },
    ],
    links: [
      // Legacy numeric form, still produced by older fixtures — it must keep working.
      { source: 'A', target: 'B', trust_limit: 10 },
      { source: 'C', target: 'A', trust_limit: 5 },
      { source: 'A', target: 'C', trust_limit: 3 },
    ],
  }
}

describe('useSelectedNodeEdgeStats', () => {
  it('computes in/out limits and degree for selected node', () => {
    const snapshot = makeSnapshot()
    const { selectedNodeEdgeStats } = useSelectedNodeEdgeStats({
      getSnapshot: () => snapshot,
      getSelectedNodeId: () => 'A',
    })

    expect(selectedNodeEdgeStats.value).toEqual({
      inLimitText: '5.00',
      outLimitText: '13.00',
      degree: 3,
    })
  })

  it('reacts to the equivalent: the same links must not render identically under UAH and HOUR', () => {
    const asUah = computeNodeEdgeStats(makeSnapshot('UAH'), 'A')
    const asHour = computeNodeEdgeStats(makeSnapshot('HOUR'), 'A')

    expect(
      asHour.outLimitText,
      `The same three links total "${asUah.outLimitText}" under UAH (precision 2) and `
        + `"${asHour.outLimitText}" under HOUR (precision 1). Identical output would mean the `
        + 'digit count is a constant again, and the precision-2 case above would prove nothing.',
    ).not.toBe(asUah.outLimitText)
    expect(asHour.outLimitText).toBe('13.0')
    expect(asHour.inLimitText).toBe('5.0')
  })

  it('sums trust limits exactly, without routing them through a double', () => {
    // Eight fraction digits is exactly what `Numeric(20, 8)` stores, and a double holds
    // ~15-16 significant ones. Added as doubles these two limits give 10000000000.123457,
    // i.e. the one-atom limit vanishes; the operator must see the limit that is in force.
    const snapshot: GraphSnapshot = {
      equivalent: 'UAH',
      generated_at: '2026-08-24T00:00:00Z',
      nodes: [{ id: 'A', name: 'A' }, { id: 'B', name: 'B' }],
      links: [
        { source: 'A', target: 'B', trust_limit: '10000000000.12345678' },
        { source: 'A', target: 'B', trust_limit: '0.00000001' },
      ],
    }

    expect(
      Number('10000000000.12345678') === Number('10000000000.12345679'),
      'Premise: the sum and its neighbour are one and the same double, so a float path cannot '
        + 'render this total correctly even by accident.',
    ).toBe(true)
    expect(computeNodeEdgeStats(snapshot, 'A').outLimitText).toBe('10000000000.12345679')
  })

  it('counts a link with an unusable trust limit as zero, and still counts its degree', () => {
    const snapshot: GraphSnapshot = {
      equivalent: 'UAH',
      generated_at: '2026-08-24T00:00:00Z',
      nodes: [{ id: 'A', name: 'A' }, { id: 'B', name: 'B' }],
      links: [
        { source: 'A', target: 'B', trust_limit: '2.50' },
        { source: 'A', target: 'B' },
        { source: 'A', target: 'B', trust_limit: 'nope' },
      ],
    }

    expect(computeNodeEdgeStats(snapshot, 'A')).toEqual({
      inLimitText: '0.00',
      outLimitText: '2.50',
      degree: 3,
    })
  })

  it('returns null when snapshot or selected id missing', () => {
    const { selectedNodeEdgeStats } = useSelectedNodeEdgeStats({
      getSnapshot: () => null,
      getSelectedNodeId: () => 'A',
    })
    expect(selectedNodeEdgeStats.value).toBeNull()
  })
})
