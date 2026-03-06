import { describe, expect, it } from 'vitest'
import type { GraphNode } from '../types'

import { sizeForNode } from './nodeSizing'

describe('sizeForNode()', () => {
  it('falls back to default when viz_size contains NaN', () => {
    const n: GraphNode = { id: 'n', viz_size: { w: Number.NaN, h: Number.NaN } }
    expect(sizeForNode(n)).toEqual({ w: 12, h: 12 })
  })

  it('enforces minimum size even for zero/negative values', () => {
    const n: GraphNode = { id: 'n', viz_size: { w: 0, h: -5 } }
    expect(sizeForNode(n)).toEqual({ w: 6, h: 6 })
  })
})
