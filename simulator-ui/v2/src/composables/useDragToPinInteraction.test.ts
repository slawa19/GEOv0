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

  // 038 S1: the drag delta under a camera. The pointer moves in client pixels, the node lives in world coordinates;
  // screen = world * zoom + pan, so a pointer delta (dx, dy) must move the node by (dx / zoom, dy / zoom), and the
  // point of the node under the pointer must stay under the pointer (no jump to the node centre at the first move).
  for (const cam of [{ zoom: 0.5, panX: -40, panY: 25 }, { zoom: 1, panX: 0, panY: 0 }, { zoom: 2.25, panX: 130, panY: -70 }]) {
    it(`moves the node by pointer delta / zoom at zoom ${cam.zoom}, pins it live on every move and commits the drop`, () => {
      const node = makeLayoutNode('node-1', 300, 200)
      const pinNodeLive = vi.fn()
      const commitPinnedPos = vi.fn()
      const api = useDragToPinInteraction({
        isEnabled: () => true,
        pickNodeAt: () => ({ id: 'node-1' }),
        getLayoutNodeById: () => node,
        setSelectedNodeId: vi.fn(),
        clearHoveredEdge: vi.fn(),
        clientToScreen: (clientX, clientY) => ({ x: clientX - 8, y: clientY - 60 }), // the canvas is not at the page origin
        screenToWorld: (x, y) => ({ x: (x - cam.panX) / cam.zoom, y: (y - cam.panY) / cam.zoom }),
        getCanvasEl: () => ({ setPointerCapture: vi.fn(), releasePointerCapture: vi.fn() } as unknown as HTMLCanvasElement),
        renderOnce: vi.fn(),
        pinNodeLive,
        commitPinnedPos,
        reheatPhysics: vi.fn(),
        showDragPreviewForNode: vi.fn(),
        scheduleDragPreview: vi.fn(),
        hideDragPreview: vi.fn(),
      })

      // Press 5 px right of and 3 px below the node centre, as a user who does not hit the exact centre.
      const centre = { x: 300 * cam.zoom + cam.panX + 8, y: 200 * cam.zoom + cam.panY + 60 }
      const press = { x: centre.x + 5, y: centre.y + 3 }
      api.onPointerDown(pointerEvent({ pointerId: 1, clientX: press.x, clientY: press.y }))

      // Under the 4 px threshold: not a drag, nothing moves and nothing is pinned.
      api.onPointerMove(pointerEvent({ pointerId: 1, clientX: press.x + 2, clientY: press.y + 2 }))
      expect({ x: node.__x, y: node.__y, dragging: api.dragState.dragging }).toEqual({ x: 300, y: 200, dragging: false })
      expect(pinNodeLive).not.toHaveBeenCalled()

      const steps = [{ dx: 40, dy: -10 }, { dx: 120, dy: -60 }]
      for (const { dx, dy } of steps) {
        api.onPointerMove(pointerEvent({ pointerId: 1, clientX: press.x + dx, clientY: press.y + dy }))
        expect(node.__x).toBeCloseTo(300 + dx / cam.zoom, 9)
        expect(node.__y).toBeCloseTo(200 + dy / cam.zoom, 9)
        expect(pinNodeLive).toHaveBeenLastCalledWith('node-1', node.__x, node.__y)
      }
      expect(pinNodeLive).toHaveBeenCalledTimes(steps.length)

      api.onPointerUp(pointerEvent({ pointerId: 1, clientX: press.x + 120, clientY: press.y - 60 }))
      expect(commitPinnedPos).toHaveBeenCalledWith('node-1', node.__x, node.__y)
      expect(pinNodeLive).toHaveBeenLastCalledWith('node-1', node.__x, node.__y)
      expect(node.__x).toBeCloseTo(300 + 120 / cam.zoom, 9)
      expect(node.__y).toBeCloseTo(200 - 60 / cam.zoom, 9)
    })
  }

  it('a press and release without movement is not a drag: nothing is pinned and nothing is committed', () => {
    const node = makeLayoutNode('node-1', 30, 40)
    const pinNodeLive = vi.fn()
    const commitPinnedPos = vi.fn()
    const api = useDragToPinInteraction({
      isEnabled: () => true,
      pickNodeAt: () => ({ id: 'node-1' }),
      getLayoutNodeById: () => node,
      setSelectedNodeId: vi.fn(),
      clearHoveredEdge: vi.fn(),
      clientToScreen: (clientX, clientY) => ({ x: clientX, y: clientY }),
      screenToWorld: (x, y) => ({ x, y }),
      getCanvasEl: () => ({ setPointerCapture: vi.fn(), releasePointerCapture: vi.fn() } as unknown as HTMLCanvasElement),
      renderOnce: vi.fn(),
      pinNodeLive,
      commitPinnedPos,
      reheatPhysics: vi.fn(),
      showDragPreviewForNode: vi.fn(),
      scheduleDragPreview: vi.fn(),
      hideDragPreview: vi.fn(),
    })
    api.onPointerDown(pointerEvent({ pointerId: 1, clientX: 30, clientY: 40 }))
    api.onPointerUp(pointerEvent({ pointerId: 1, clientX: 30, clientY: 40 }))
    expect({ x: node.__x, y: node.__y }).toEqual({ x: 30, y: 40 })
    expect(pinNodeLive).not.toHaveBeenCalled()
    expect(commitPinnedPos).not.toHaveBeenCalled()
  })
})
