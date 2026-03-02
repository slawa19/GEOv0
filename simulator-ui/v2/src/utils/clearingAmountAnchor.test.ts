import { describe, expect, it } from 'vitest'

import { computeClearingAmountAnchorFromEdgeMidpoints } from './clearingAmountAnchor'

describe('utils/clearingAmountAnchor — computeClearingAmountAnchorFromEdgeMidpoints()', () => {
  it('returns null when no midpoints can be computed', () => {
    const anchor = computeClearingAmountAnchorFromEdgeMidpoints(
      [{ from: 'A', to: 'B' }],
      () => null,
    )
    expect(anchor).toBeNull()
  })

  it('computes centroid of edge midpoints', () => {
    // Edge AB midpoint: (1, 0)
    // Edge BC midpoint: (2, 1)
    // Centroid: ((1+2)/2, (0+1)/2) = (1.5, 0.5)
    const pos: Record<string, { x: number; y: number }> = {
      A: { x: 0, y: 0 },
      B: { x: 2, y: 0 },
      C: { x: 2, y: 2 },
    }
    const anchor = computeClearingAmountAnchorFromEdgeMidpoints(
      [
        { from: 'A', to: 'B' },
        { from: 'B', to: 'C' },
      ],
      (id) => pos[id],
    )

    expect(anchor).toEqual({ x: 1.5, y: 0.5 })
  })

  it('ignores edges with missing endpoint coords', () => {
    const pos: Record<string, { x: number; y: number } | undefined> = {
      A: { x: 0, y: 0 },
      B: { x: 2, y: 0 },
      // C missing
    }

    const anchor = computeClearingAmountAnchorFromEdgeMidpoints(
      [
        { from: 'A', to: 'B' }, // ok -> (1, 0)
        { from: 'B', to: 'C' }, // ignored
      ],
      (id) => pos[id],
    )

    expect(anchor).toEqual({ x: 1, y: 0 })
  })
})

