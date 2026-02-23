import { describe, expect, it } from 'vitest'

import type { LayoutNode } from './nodePainter'
import { getLinkTermination } from './linkGeometry'

function node(
  opts: {
    x: number
    y: number
    shape?: 'circle' | 'rounded-rect'
    w?: number
    h?: number
  } = { x: 0, y: 0 },
): LayoutNode {
  const { x, y, shape = 'circle', w, h } = opts
  return {
    id: 'n',
    __x: x,
    __y: y,
    viz_shape_key: shape,
    viz_size: w == null || h == null ? null : { w, h },
  } as LayoutNode
}

describe('getLinkTermination()', () => {
  it('computes circle termination point deterministically', () => {
    const n = node({ x: 0, y: 0, shape: 'circle', w: 10, h: 14 })
    const target = { __x: 10, __y: 0 }

    const p = getLinkTermination(n, target, 1)
    // r = max(10,14)/2 = 7, direction (1,0)
    expect(p.x).toBeCloseTo(7)
    expect(p.y).toBeCloseTo(0)
  })

  it('computes rounded-rect termination via ray-box intersection', () => {
    const n = node({ x: 0, y: 0, shape: 'rounded-rect', w: 10, h: 14 })
    const target = { __x: 10, __y: 0 }

    const p = getLinkTermination(n, target, 1)
    // For horizontal ray, it hits the right side at x = w/2.
    expect(p.x).toBeCloseTo(5)
    expect(p.y).toBeCloseTo(0)
  })

  it('returns node center for near-zero direction vector', () => {
    const n = node({ x: 3, y: -4, shape: 'circle', w: 10, h: 14 })
    const target = { __x: 3.05, __y: -4.05 }

    const p = getLinkTermination(n, target, 1)
    expect(p.x).toBeCloseTo(3)
    expect(p.y).toBeCloseTo(-4)
  })

  it('reuses point objects across calls (hot-path allocation guard)', () => {
    const n = node({ x: 0, y: 0, shape: 'circle', w: 10, h: 14 })
    const target = { __x: 10, __y: 0 }

    const refs = new Set<unknown>()
    const N = 20_000
    for (let i = 0; i < N; i++) {
      refs.add(getLinkTermination(n, target, 1))
    }

    // If `getLinkTermination()` allocated a fresh object per call, unique refs === N.
    // Pooling should keep the number of unique refs bounded.
    expect(refs.size).toBeLessThan(N)
  })
})

