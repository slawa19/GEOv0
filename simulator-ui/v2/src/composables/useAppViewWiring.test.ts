import { describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

import type { LayoutLinkLike, LayoutNode } from '../types/layout'
import { useAppViewWiring } from './useAppViewWiring'

// 1000x600 viewport, one directed edge A → B laid out from (100,100) to (300,300).
// With 80px padding per side the segment fits into 840x440; the tight axis is Y,
// so the framing zoom is 440 / 200 = 2.2 and the segment ends up centered.
const NODE_A: LayoutNode = { id: 'A', __x: 100, __y: 100 }
const NODE_B: LayoutNode = { id: 'B', __x: 300, __y: 300 }
const NODE_C: LayoutNode = { id: 'C', __x: 500, __y: 100 }

const EDGE_A_B: LayoutLinkLike = { __key: 'A→B', source: 'A', target: 'B' }

function makeWiring(opts?: {
  links?: LayoutLinkLike[] | null
  onCameraChanged?: () => void
}) {
  const nodes = [NODE_A, NODE_B, NODE_C]
  const links = opts?.links === undefined ? [EDGE_A_B] : opts.links

  return useAppViewWiring({
    canvasEl: ref(null),
    hostEl: ref(null),

    getLayoutNodes: () => [NODE_A, NODE_B],
    getLayoutW: () => 1000,
    getLayoutH: () => 600,
    isTestMode: () => false,

    onCameraChanged: opts?.onCameraChanged,

    setClampCameraPan: () => undefined,

    selectedNodeId: ref<string | null>(null),
    setSelectedNodeId: () => undefined,

    getNodeById: (id) => nodes.find((n) => n.id === id) ?? null,
    getLayoutNodeById: (id) => nodes.find((n) => n.id === id) ?? null,
    getLayoutLinks: links === null ? undefined : () => links,
  })
}

describe('useAppViewWiring focusOnEdge', () => {
  it('moves the camera onto an edge of the current snapshot', () => {
    const onCameraChanged = vi.fn()
    const wiring = makeWiring({ onCameraChanged })

    expect(wiring.focusOnEdge('A', 'B')).toBe(true)

    expect(wiring.camera.zoom).toBeCloseTo(2.2)
    expect(wiring.camera.panX).toBeCloseTo(60)
    expect(wiring.camera.panY).toBeCloseTo(-140)

    // Both ends of the edge are on screen, not just one of them.
    const a = wiring.worldToScreen(NODE_A.__x, NODE_A.__y)
    const b = wiring.worldToScreen(NODE_B.__x, NODE_B.__y)
    expect(a.x).toBeCloseTo(280)
    expect(a.y).toBeCloseTo(80)
    expect(b.x).toBeCloseTo(720)
    expect(b.y).toBeCloseTo(520)

    expect(onCameraChanged).toHaveBeenCalledTimes(1)
  })

  it('leaves the camera alone when the edge is gone but both endpoints remain', () => {
    const onCameraChanged = vi.fn()
    // A and C are both in the snapshot; the edge A → C is not.
    const wiring = makeWiring({ onCameraChanged })

    wiring.camera.panX = 7
    wiring.camera.panY = 8
    wiring.camera.zoom = 1.25

    expect(wiring.focusOnEdge('A', 'C')).toBe(false)

    expect(wiring.camera.panX).toBe(7)
    expect(wiring.camera.panY).toBe(8)
    expect(wiring.camera.zoom).toBe(1.25)
    expect(onCameraChanged).not.toHaveBeenCalled()
  })

  it('leaves the camera alone when the snapshot has no such nodes at all', () => {
    const wiring = makeWiring()

    wiring.camera.panX = 7
    wiring.camera.panY = 8
    wiring.camera.zoom = 1.25

    expect(wiring.focusOnEdge('ghost', 'phantom')).toBe(false)

    expect(wiring.camera.panX).toBe(7)
    expect(wiring.camera.panY).toBe(8)
    expect(wiring.camera.zoom).toBe(1.25)
  })

  it('treats the reversed edge as a different edge', () => {
    const wiring = makeWiring()

    wiring.camera.panX = 7
    wiring.camera.panY = 8
    wiring.camera.zoom = 1.25

    // Only A → B is in the snapshot. Edge identity is directed everywhere in this app.
    expect(wiring.focusOnEdge('B', 'A')).toBe(false)

    expect(wiring.camera.panX).toBe(7)
    expect(wiring.camera.panY).toBe(8)
    expect(wiring.camera.zoom).toBe(1.25)
  })

  it('does not guess when links were never wired in', () => {
    const wiring = makeWiring({ links: null })

    wiring.camera.panX = 7
    wiring.camera.panY = 8
    wiring.camera.zoom = 1.25

    expect(wiring.focusOnEdge('A', 'B')).toBe(false)

    expect(wiring.camera.panX).toBe(7)
    expect(wiring.camera.panY).toBe(8)
    expect(wiring.camera.zoom).toBe(1.25)
  })

  it('is repeatable: focusing the same edge twice lands on the same camera state', () => {
    const wiring = makeWiring()

    wiring.focusOnEdge('A', 'B')
    const first = { panX: wiring.camera.panX, panY: wiring.camera.panY, zoom: wiring.camera.zoom }

    wiring.camera.panX = -900
    wiring.camera.panY = 1200
    wiring.camera.zoom = 0.5

    wiring.focusOnEdge('A', 'B')

    expect(wiring.camera.panX).toBeCloseTo(first.panX)
    expect(wiring.camera.panY).toBeCloseTo(first.panY)
    expect(wiring.camera.zoom).toBeCloseTo(first.zoom)
  })
})
