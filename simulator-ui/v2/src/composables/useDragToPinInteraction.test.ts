import { describe, expect, it, vi } from 'vitest'

import type { LayoutNode } from '../types/layout'
import { useDragToPinInteraction } from './useDragToPinInteraction'

function pointerEvent(init: { pointerId: number; clientX: number; clientY: number }): PointerEvent {
  return init as unknown as PointerEvent
}

function makeLayoutNode(id: string, x: number, y: number): LayoutNode {
  return {
    id,
    __x: x,
    __y: y,
  }
}

describe('useDragToPinInteraction', () => {
  it('cleans up pointer capture on pointerup after drag activation', () => {
    const setPointerCapture = vi.fn()
    const releasePointerCapture = vi.fn()
    const node = makeLayoutNode('node-1', 30, 40)

    const api = useDragToPinInteraction({
      isEnabled: () => true,
      pickNodeAt: () => ({ id: 'node-1' }),
      getLayoutNodeById: () => node,
      setSelectedNodeId: vi.fn(),
      clearHoveredEdge: vi.fn(),
      clientToScreen: (clientX, clientY) => ({ x: clientX, y: clientY }),
      screenToWorld: (x, y) => ({ x, y }),
      getCanvasEl: () => ({ setPointerCapture, releasePointerCapture } as unknown as HTMLCanvasElement),
      renderOnce: vi.fn(),
      pinNodeLive: vi.fn(),
      commitPinnedPos: vi.fn(),
      reheatPhysics: vi.fn(),
      showDragPreviewForNode: vi.fn(),
      scheduleDragPreview: vi.fn(),
      hideDragPreview: vi.fn(),
    })

    expect(api.onPointerDown(pointerEvent({ pointerId: 7, clientX: 100, clientY: 120 }))).toBe(true)
    expect(setPointerCapture).toHaveBeenCalledWith(7)

    expect(api.onPointerMove(pointerEvent({ pointerId: 7, clientX: 110, clientY: 120 }))).toBe(true)
    expect(api.dragState.dragging).toBe(true)

    expect(api.onPointerUp(pointerEvent({ pointerId: 7, clientX: 110, clientY: 120 }))).toBe(true)
    expect(releasePointerCapture).toHaveBeenCalledWith(7)
    expect(api.dragState.active).toBe(false)
    expect(api.dragState.pointerId).toBeNull()
  })
})