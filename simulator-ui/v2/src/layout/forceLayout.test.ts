import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { GraphSnapshot } from '../types'

type ForceLayoutModule = typeof import('./forceLayout')

async function loadForceLayout(): Promise<ForceLayoutModule> {
  // Some other test files mock [`render.nodePainter`](simulator-ui/v2/src/render/nodePainter.ts:1)
  // without exporting `sizeForNode()`. Since [`layout.forceLayout`](simulator-ui/v2/src/layout/forceLayout.ts:1)
  // depends on it, that mock can leak via the shared module cache between test files.
  //
  // Ensure each test loads a fresh, unmocked module instance.
  vi.resetModules()
  vi.doUnmock('../render/nodePainter')
  return await import('./forceLayout')
}

beforeEach(() => {
  vi.restoreAllMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})

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

function makeRepulsionOnlySnapshot(n: number): GraphSnapshot {
  return {
    equivalent: 'UAH',
    generated_at: '2026-01-25T00:00:00Z',
    nodes: Array.from({ length: n }, (_, i) => ({
      id: `N${String(i).padStart(4, '0')}`,
      type: 'person',
      viz_size: { w: 10, h: 10 },
    })),
    links: [],
  }
}

describe('forceLayout', () => {
  it('handles empty snapshot', async () => {
    const { computeLayoutForMode } = await loadForceLayout()
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

  it('applyForceLayout produces deterministic positions for same seed', async () => {
    const { applyForceLayout } = await loadForceLayout()
    const snapshot = makeMockSnapshot()

    const r1 = applyForceLayout({ snapshot, w: 800, h: 600, seedKey: 'test', isTestMode: true })
    const r2 = applyForceLayout({ snapshot, w: 800, h: 600, seedKey: 'test', isTestMode: true })

    expect(r1.nodes).toEqual(r2.nodes)
    expect(r1.links).toEqual(r2.links)
  })

  it('computeLayoutForMode returns links with stable __key', async () => {
    const { computeLayoutForMode } = await loadForceLayout()
    const snapshot = makeMockSnapshot()

    const out = computeLayoutForMode(snapshot, 800, 600, 'admin-force', true)

    expect(out.links.map((l) => l.__key)).toEqual(['A→B', 'B→C', 'C→D', 'A→D'])
    for (const n of out.nodes) {
      expect(Number.isFinite(n.__x)).toBe(true)
      expect(Number.isFinite(n.__y)).toBe(true)
    }
  })

  it('applyForceLayout with w=h=1 produces coordinates in [0, 1] (ITEM-12 smoke)', async () => {
    const { applyForceLayout } = await loadForceLayout()
    const snapshot = makeMockSnapshot()
    const out = applyForceLayout({ snapshot, w: 1, h: 1, seedKey: 'tiny', isTestMode: true })
    for (const n of out.nodes) {
      expect(n.__x).toBeGreaterThanOrEqual(0)
      expect(n.__x).toBeLessThanOrEqual(1)
      expect(n.__y).toBeGreaterThanOrEqual(0)
      expect(n.__y).toBeLessThanOrEqual(1)
    }
  })

  // NOTE C-4 (ITEM-17): dangling links must be filtered – no throw, clean output
  it('applyForceLayout does NOT throw when a link references a missing node (dangling link)', async () => {
    const { applyForceLayout } = await loadForceLayout()
    const snapshot: GraphSnapshot = {
      equivalent: 'UAH',
      generated_at: '2026-01-25T00:00:00Z',
      nodes: [
        { id: 'A', type: 'person', viz_size: { w: 10, h: 10 } },
        { id: 'B', type: 'person', viz_size: { w: 10, h: 10 } },
      ],
      // C is missing from nodes – this is the dangling link
      links: [{ source: 'A', target: 'C' }],
    }

    // Must not throw
    let out: ReturnType<typeof applyForceLayout> | undefined
    expect(() => {
      out = applyForceLayout({ snapshot, w: 800, h: 600, seedKey: 'dangling', isTestMode: true })
    }).not.toThrow()

    // Dangling link must be absent from output
    expect(out!.links.length).toBe(0)
    expect(out!.links.find((l) => l.source === 'A' && l.target === 'C')).toBeUndefined()

    // Both surviving nodes must have finite coordinates within viewport
    expect(out!.nodes.length).toBe(2)
    for (const n of out!.nodes) {
      expect(Number.isFinite(n.__x)).toBe(true)
      expect(Number.isFinite(n.__y)).toBe(true)
      expect(n.__x).toBeGreaterThanOrEqual(0)
      expect(n.__x).toBeLessThanOrEqual(800)
      expect(n.__y).toBeGreaterThanOrEqual(0)
      expect(n.__y).toBeLessThanOrEqual(600)
    }
  })

  it('applyForceLayout preserves all links in output when all links are valid (NOTE C-4)', async () => {
    const { applyForceLayout } = await loadForceLayout()
    const snapshot = makeMockSnapshot()
    const out = applyForceLayout({ snapshot, w: 800, h: 600, seedKey: 'valid-links', isTestMode: true })
    // All 4 links in makeMockSnapshot are valid → output must contain all of them
    expect(out.links.length).toBe(snapshot.links.length)
  })

  // ITEM-6: Barnes–Hut repulsion via d3-quadtree + configurable theta.
  it('applyForceLayout chargeTheta is configurable and affects output (Barnes–Hut)', async () => {
    const { applyForceLayout } = await loadForceLayout()
    const snapshot = makeRepulsionOnlySnapshot(120)

    const a1 = applyForceLayout({ snapshot, w: 900, h: 700, seedKey: 'theta', isTestMode: true, chargeTheta: 0.35 })
    const a2 = applyForceLayout({ snapshot, w: 900, h: 700, seedKey: 'theta', isTestMode: true, chargeTheta: 0.35 })
    expect(a1.nodes).toEqual(a2.nodes)

    const b = applyForceLayout({ snapshot, w: 900, h: 700, seedKey: 'theta', isTestMode: true, chargeTheta: 1.25 })
    // Theta is speed/accuracy tradeoff; for a repulsion-only graph with many nodes,
    // different theta should yield different positions.
    expect(a1.nodes).not.toEqual(b.nodes)

    for (const n of b.nodes) {
      expect(Number.isFinite(n.__x)).toBe(true)
      expect(Number.isFinite(n.__y)).toBe(true)
      expect(n.__x).toBeGreaterThanOrEqual(0)
      expect(n.__x).toBeLessThanOrEqual(900)
      expect(n.__y).toBeGreaterThanOrEqual(0)
      expect(n.__y).toBeLessThanOrEqual(700)
    }
  })
})
