import { describe, expect, it } from 'vitest'
import { closestPointOnSegment, dist2PointToSegment, usePicking } from './usePicking'

describe('usePicking geometry', () => {
  it('dist2PointToSegment returns 0 on-segment', () => {
    const d2 = dist2PointToSegment(5, 0, 0, 0, 10, 0)
    expect(d2).toBe(0)
  })

  it('closestPointOnSegment clamps to endpoints', () => {
    const left = closestPointOnSegment(-5, 0, 0, 0, 10, 0)
    expect(left).toEqual({ x: 0, y: 0, t: 0 })

    const right = closestPointOnSegment(25, 0, 0, 0, 10, 0)
    expect(right).toEqual({ x: 10, y: 0, t: 1 })
  })
})

describe('usePicking', () => {
  it('pickNodeAt selects nearest node within radius', () => {
    const nodes = [
      { id: 'A', __x: 0, __y: 0 },
      { id: 'B', __x: 300, __y: 0 },
    ]

    const picking = usePicking({
      getLayoutNodes: () => nodes,
      getLayoutLinks: () => [],
      getCameraZoom: () => 1,
      sizeForNode: () => ({ w: 40, h: 40 }),
      clientToScreen: (x, y) => ({ x, y }),
      screenToWorld: (x, y) => ({ x, y }),
      isReady: () => true,
    })

    const hitA = picking.pickNodeAt(2, 1)
    expect(hitA?.id).toBe('A')

    const miss = picking.pickNodeAt(200, 200)
    expect(miss).toBeNull()
  })

  it('pickEdgeAt selects edge near segment', () => {
    const nodes = [
      { id: 'A', __x: 0, __y: 0 },
      { id: 'B', __x: 100, __y: 0 },
    ]

    const links = [{ __key: 'A→B', source: 'A', target: 'B' }]

    const picking = usePicking({
      getLayoutNodes: () => nodes,
      getLayoutLinks: () => links,
      getCameraZoom: () => 1,
      sizeForNode: () => ({ w: 40, h: 40 }),
      clientToScreen: (x, y) => ({ x, y }),
      screenToWorld: (x, y) => ({ x, y }),
      isReady: () => true,
    })

    const hit = picking.pickEdgeAt(50, 5) // 5px from the segment
    expect(hit?.key).toBe('A→B')

    const miss = picking.pickEdgeAt(50, 50)
    expect(miss).toBeNull()
  })
})
