import { describe, expect, it } from 'vitest'
import type { GraphSnapshot } from '../types'
import type { LayoutLink, LayoutNode } from '../types/layout'
import { createPatchApplier } from './patches'

const keyEdge = (a: string, b: string) => `${a}→${b}`

function makeSnapshot(): GraphSnapshot {
  return {
    equivalent: 'UAH',
    generated_at: '2026-01-25T00:00:00Z',
    nodes: [{ id: 'A', net_sign: 0, net_balance_atoms: '0' }, { id: 'B' }],
    links: [{ source: 'A', target: 'B', used: '0', available: '10', viz_alpha_key: 'bg' }],
  }
}

function makeLayoutNodes(): LayoutNode[] {
  return [
    { id: 'A', __x: 10, __y: 20, net_sign: 0, net_balance_atoms: '0' },
    { id: 'B', __x: 30, __y: 40 },
  ]
}

function makeLayoutLinks(): LayoutLink[] {
  return [{ source: 'A', target: 'B', __key: keyEdge('A', 'B'), used: '0', available: '10', viz_alpha_key: 'bg' }]
}

describe('demo/patches', () => {
  it('applyNodePatches updates snapshot + layout nodes', () => {
    const snapshot = makeSnapshot()
    const layoutNodes = makeLayoutNodes()
    const layoutLinks = makeLayoutLinks()

    const applier = createPatchApplier({
      getSnapshot: () => snapshot,
      getLayoutNodes: () => layoutNodes,
      getLayoutLinks: () => layoutLinks,
      keyEdge,
    })

    applier.applyNodePatches([
      { id: 'A', net_sign: 1, net_balance_atoms: '123', viz_color_key: 'pos', viz_size: { w: 9, h: 7 } },
      { id: 'missing', net_sign: -1 },
    ])

    expect(snapshot.nodes.find((n) => n.id === 'A')).toMatchObject({
      id: 'A',
      net_sign: 1,
      net_balance_atoms: '123',
      viz_color_key: 'pos',
      viz_size: { w: 9, h: 7 },
    })

    expect(layoutNodes.find((n) => n.id === 'A')).toMatchObject({
      id: 'A',
      net_sign: 1,
      net_balance_atoms: '123',
      viz_color_key: 'pos',
      viz_size: { w: 9, h: 7 },
    })

    expect(snapshot.nodes.find((n) => n.id === 'B')).toMatchObject({ id: 'B' })
    expect(layoutNodes.find((n) => n.id === 'B')).toMatchObject({ id: 'B' })
  })

  it('applyEdgePatches updates snapshot + layout links', () => {
    const snapshot = makeSnapshot()
    const layoutNodes = makeLayoutNodes()
    const layoutLinks = makeLayoutLinks()

    const applier = createPatchApplier({
      getSnapshot: () => snapshot,
      getLayoutNodes: () => layoutNodes,
      getLayoutLinks: () => layoutLinks,
      keyEdge,
    })

    applier.applyEdgePatches([
      {
        source: 'A',
        target: 'B',
        used: '4',
        available: '6',
        viz_alpha_key: 'active',
        viz_width_key: 'thin',
      },
      { source: 'missing', target: 'B', used: '999' },
    ])

    expect(snapshot.links[0]).toMatchObject({
      source: 'A',
      target: 'B',
      used: '4',
      available: '6',
      viz_alpha_key: 'active',
      viz_width_key: 'thin',
    })

    expect(layoutLinks[0]).toMatchObject({
      source: 'A',
      target: 'B',
      used: '4',
      available: '6',
      viz_alpha_key: 'active',
      viz_width_key: 'thin',
    })
  })

  it('no-ops safely when snapshot is null or patches empty', () => {
    const layoutNodes = makeLayoutNodes()
    const layoutLinks = makeLayoutLinks()

    const applier = createPatchApplier({
      getSnapshot: () => null,
      getLayoutNodes: () => layoutNodes,
      getLayoutLinks: () => layoutLinks,
      keyEdge,
    })

    applier.applyNodePatches(undefined)
    applier.applyNodePatches([])
    applier.applyEdgePatches(undefined)
    applier.applyEdgePatches([])

    expect(layoutNodes).toHaveLength(2)
    expect(layoutLinks).toHaveLength(1)
  })
})
