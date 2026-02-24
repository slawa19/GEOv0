import { describe, expect, it } from 'vitest'
import { computed, isProxy, isReactive, markRaw, ref } from 'vue'

import { createPhysicsEngine, createDefaultConfig } from '../layout/physicsD3'
import { useLayoutCoordinator } from './useLayoutCoordinator'

type N = { id: string; __x: number; __y: number; x?: number; y?: number }
type L = { __key: string; source: string; target: string }

describe('ITEM-9: layout nodes/links are raw + physics sync writes without Vue proxy overhead', () => {
  it('useLayoutCoordinator keeps layout.nodes/links and their elements non-reactive (raw)', () => {
    const coordinator = useLayoutCoordinator<N, L, 'm', { generated_at: string; nodes: unknown[]; links: unknown[] }>({
      canvasEl: { value: null } as any,
      fxCanvasEl: { value: null } as any,
      hostEl: { value: null } as any,
      snapshot: computed(() => null),
      layoutMode: ref('m'),
      dprClamp: computed(() => 1),
      isTestMode: computed(() => true),
      getSourcePath: () => 'mem',
      computeLayout: () => undefined,
    })

    const nodes = [{ id: 'A', __x: 1, __y: 2 }]
    const links = [{ __key: 'A→A', source: 'A', target: 'A' }]
    coordinator.setLayout(nodes, links)

    expect(isProxy(coordinator.layout.nodes)).toBe(false)
    expect(isReactive(coordinator.layout.nodes)).toBe(false)
    expect(isProxy(coordinator.layout.links)).toBe(false)
    expect(isReactive(coordinator.layout.links)).toBe(false)

    expect(isProxy(coordinator.layout.nodes[0]!)).toBe(false)
    expect(isReactive(coordinator.layout.nodes[0]!)).toBe(false)
    expect(isProxy(coordinator.layout.links[0]!)).toBe(false)
    expect(isReactive(coordinator.layout.links[0]!)).toBe(false)
  })

  it('syncToLayout updates raw node coords (__x/__y) after one tick', () => {
    // Ensure nodes are raw objects (as enforced by coordinator in production).
    const nodes: N[] = markRaw([
      { id: 'A', __x: 10, __y: 10 },
      { id: 'B', __x: 20, __y: 20 },
    ])

    const links: L[] = markRaw([{ __key: 'A→B', source: 'A', target: 'B' }])

    const config = createDefaultConfig({ width: 200, height: 100, nodeCount: nodes.length, quality: 'low' })
    const engine = createPhysicsEngine({ nodes: nodes as any, links: links as any, config })

    // Force a visible delta (engine reads `n.x/n.y` produced by d3-force).
    nodes[0]!.x = 111
    nodes[0]!.y = 77

    engine.syncToLayout()

    expect(nodes[0]!.__x).toBe(111)
    expect(nodes[0]!.__y).toBe(77)

    // Still raw.
    expect(isProxy(nodes[0]!)).toBe(false)
    expect(isReactive(nodes[0]!)).toBe(false)
  })
})

