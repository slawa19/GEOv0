import { describe, expect, it, vi } from 'vitest'
import type { LayoutNode } from '../types/layout'
import { usePinning } from './usePinning'

function makeNode(id: string, x: number, y: number): LayoutNode {
  return { id, __x: x, __y: y }
}

describe('usePinning', () => {
  it('captureBaseline + reapplyPinnedToLayout restores pinned positions after relayout', () => {
    const pinnedPos = new Map<string, { x: number; y: number }>([['A', { x: 10, y: 20 }]])
    const baselineLayoutPos = new Map<string, { x: number; y: number }>()

    const nodes: LayoutNode[] = [makeNode('A', 1, 2), makeNode('B', 3, 4)]

    const pinning = usePinning({
      pinnedPos,
      baselineLayoutPos,
      getSelectedNodeId: () => null,
      getLayoutNodeById: (id) => nodes.find((n) => n.id === id) ?? null,
      physics: {
        pin: vi.fn(),
        unpin: vi.fn(),
        syncFromLayout: vi.fn(),
        reheat: vi.fn(),
      },
    })

    pinning.captureBaseline(nodes)

    // Simulate relayout moving nodes.
    nodes[0]!.__x = 999
    nodes[0]!.__y = 999

    pinning.reapplyPinnedToLayout()

    expect(nodes[0]).toMatchObject({ id: 'A', __x: 10, __y: 20 })
    expect(baselineLayoutPos.get('A')).toEqual({ x: 1, y: 2 })
  })

  it('pinSelectedNode and unpinSelectedNode interact with physics', () => {
    const pinnedPos = new Map<string, { x: number; y: number }>()
    const baselineLayoutPos = new Map<string, { x: number; y: number }>()

    const nodes: LayoutNode[] = [makeNode('A', 5, 6)]

    const physics = {
      pin: vi.fn(),
      unpin: vi.fn(),
      syncFromLayout: vi.fn(),
      reheat: vi.fn(),
    }

    const selectedId = 'A'

    const pinning = usePinning({
      pinnedPos,
      baselineLayoutPos,
      getSelectedNodeId: () => selectedId,
      getLayoutNodeById: (id) => nodes.find((n) => n.id === id) ?? null,
      physics,
    })

    pinning.captureBaseline(nodes)
    pinning.pinSelectedNode()

    expect(pinnedPos.get('A')).toEqual({ x: 5, y: 6 })
    expect(physics.pin).toHaveBeenCalledWith('A', 5, 6)

    // Move node away; unpin should restore baseline.
    nodes[0]!.__x = 100
    nodes[0]!.__y = 200

    pinning.unpinSelectedNode()

    expect(pinnedPos.has('A')).toBe(false)
    expect(nodes[0]).toMatchObject({ id: 'A', __x: 5, __y: 6 })
    expect(physics.unpin).toHaveBeenCalledWith('A')
    expect(physics.syncFromLayout).toHaveBeenCalledTimes(1)
  })
})
