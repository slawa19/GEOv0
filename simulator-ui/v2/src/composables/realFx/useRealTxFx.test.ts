import { describe, expect, it } from 'vitest'

import { REAL_TX_FX_DEFAULT, __clampRealTxTtlMs, __pickSparkEdges } from './useRealTxFx'

describe('useRealTxFx helpers', () => {
  it('__clampRealTxTtlMs: clamps to [min,max] and handles invalid inputs', () => {
    expect(__clampRealTxTtlMs(REAL_TX_FX_DEFAULT.ttlMinMs - 1)).toBe(REAL_TX_FX_DEFAULT.ttlMinMs)
    expect(__clampRealTxTtlMs(REAL_TX_FX_DEFAULT.ttlMaxMs + 1)).toBe(REAL_TX_FX_DEFAULT.ttlMaxMs)
    expect(__clampRealTxTtlMs('not-a-number')).toBe(REAL_TX_FX_DEFAULT.ttlMaxMs)
    expect(__clampRealTxTtlMs(null, REAL_TX_FX_DEFAULT, 777)).toBe(777)
  })

  it('__pickSparkEdges: keeps short routes, collapses long routes to endpoints', () => {
    const shortEdges = [{ from: 'A', to: 'B' }]
    expect(__pickSparkEdges(shortEdges as any, { from: 'A', to: 'B' })).toEqual(shortEdges)

    const longEdges = [
      { from: 'A', to: 'B' },
      { from: 'B', to: 'C' },
      { from: 'C', to: 'D' },
    ]

    expect(__pickSparkEdges(longEdges as any, { from: 'A', to: 'D' })).toEqual([{ from: 'A', to: 'D' }])
    expect(__pickSparkEdges(longEdges as any, { from: '', to: 'D' })).toEqual([])
    expect(__pickSparkEdges(longEdges as any, undefined)).toEqual([{ from: 'A', to: 'D' }])
  })
})
