import { describe, expect, it } from 'vitest'
import type { GraphSnapshot } from '../types'
import { applyForceLayout, computeLayoutForMode } from './forceLayout'

function makeMockSnapshot(): GraphSnapshot {
  return {
    equivalent: 'UAH',
    generated_at: '2026-01-25T00:00:00Z',
    nodes: [
      { id: 'A', type: 'person', viz_size: { w: 10, h: 10 } },
      { id: 'B', type: 'person', viz_size: { w: 10, h: 10 } },
      { id: 'C', type: 'business', viz_size: { w: 14, h: 14 } },
      { id: 'D', type: 'person', viz_size: { w: 10, h: 10 } },
    ],
    links: [
      { source: 'A', target: 'B' },
      { source: 'B', target: 'C' },
      { source: 'C', target: 'D' },
      { source: 'A', target: 'D' },
    ],
  }
}

describe('forceLayout', () => {
  it('handles empty snapshot', () => {
    const snapshot: GraphSnapshot = {
      equivalent: 'UAH',
      generated_at: '2026-01-25T00:00:00Z',
      nodes: [],
      links: [],
    }

    const out = computeLayoutForMode(snapshot, 800, 600, 'admin-force', true)
    expect(out.nodes).toEqual([])
    expect(out.links).toEqual([])
  })

  it('applyForceLayout produces deterministic positions for same seed', () => {
    const snapshot = makeMockSnapshot()

    const r1 = applyForceLayout({ snapshot, w: 800, h: 600, seedKey: 'test', isTestMode: true })
    const r2 = applyForceLayout({ snapshot, w: 800, h: 600, seedKey: 'test', isTestMode: true })

    expect(r1.nodes).toEqual(r2.nodes)
    expect(r1.links).toEqual(r2.links)
  })

  it('computeLayoutForMode returns links with stable __key', () => {
    const snapshot = makeMockSnapshot()

    const out = computeLayoutForMode(snapshot, 800, 600, 'admin-force', true)

    expect(out.links.map((l) => l.__key)).toEqual(['A→B', 'B→C', 'C→D', 'A→D'])
    for (const n of out.nodes) {
      expect(Number.isFinite(n.__x)).toBe(true)
      expect(Number.isFinite(n.__y)).toBe(true)
    }
  })
})
