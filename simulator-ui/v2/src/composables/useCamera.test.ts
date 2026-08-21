import { describe, expect, it, vi } from 'vitest'
import type { LayoutNodeLike } from './useCamera'
import { useCamera } from './useCamera'

type PointerEventLike = Pick<PointerEvent, 'pointerId' | 'clientX' | 'clientY'>
type WheelEventLike = Pick<WheelEvent, 'clientX' | 'clientY' | 'deltaY'>

function pointerEvent(init: PointerEventLike): PointerEvent {
  return init as unknown as PointerEvent
}

function wheelEvent(init: WheelEventLike): WheelEvent {
  return init as unknown as WheelEvent
}

function createCanvasStub(): HTMLCanvasElement {
  return { setPointerCapture: () => undefined } as unknown as HTMLCanvasElement
}

describe('useCamera', () => {
  it('worldToScreen and screenToWorld are inverse', () => {
    const cameraSystem = useCamera({
      canvasEl: { value: null },
      hostEl: { value: null },
      getLayoutNodes: () => [],
      getLayoutW: () => 0,
      getLayoutH: () => 0,
      isTestMode: () => false,
    })

    cameraSystem.camera.panX = 10
    cameraSystem.camera.panY = 20
    cameraSystem.camera.zoom = 2

    const s = cameraSystem.worldToScreen(3, 4)
    expect(s).toEqual({ x: 16, y: 28 })

    const w = cameraSystem.screenToWorld(s.x, s.y)
    expect(w.x).toBeCloseTo(3)
    expect(w.y).toBeCloseTo(4)
  })

  it('clientToScreen uses host bounding rect', () => {
    const host = {
      getBoundingClientRect: () => ({ left: 100, top: 50 }),
    } as unknown as HTMLElement

    const cameraSystem = useCamera({
      canvasEl: { value: null },
      hostEl: { value: host },
      getLayoutNodes: () => [],
      getLayoutW: () => 0,
      getLayoutH: () => 0,
      isTestMode: () => false,
    })

    expect(cameraSystem.clientToScreen(130, 80)).toEqual({ x: 30, y: 30 })
  })

  it('clampCameraPan centers content when it fits', () => {
    const nodes: LayoutNodeLike[] = [
      { __x: 0, __y: 0 },
      { __x: 100, __y: 100 },
    ]

    const cameraSystem = useCamera({
      canvasEl: { value: null },
      hostEl: { value: null },
      getLayoutNodes: () => nodes,
      getLayoutW: () => 600,
      getLayoutH: () => 600,
      isTestMode: () => false,
    })

    cameraSystem.camera.zoom = 1
    cameraSystem.camera.panX = 0
    cameraSystem.camera.panY = 0

    cameraSystem.clampCameraPan()

    expect(cameraSystem.camera.panX).toBeCloseTo(250)
    expect(cameraSystem.camera.panY).toBeCloseTo(250)
  })

  it('handlers implement click-vs-pan threshold', () => {
    const canvas = createCanvasStub()

    const cameraSystem = useCamera({
      canvasEl: { value: canvas },
      hostEl: { value: null },
      getLayoutNodes: () => [],
      getLayoutW: () => 0,
      getLayoutH: () => 0,
      isTestMode: () => false,
    })

    cameraSystem.onPointerDown(pointerEvent({ pointerId: 1, clientX: 10, clientY: 10 }))
    cameraSystem.onPointerMove(pointerEvent({ pointerId: 1, clientX: 11, clientY: 11 })) // d2=2 < 9

    const wasClick = cameraSystem.onPointerUp(pointerEvent({ pointerId: 1, clientX: 11, clientY: 11 }))
    expect(wasClick).toBe(true)

    cameraSystem.onPointerDown(pointerEvent({ pointerId: 2, clientX: 10, clientY: 10 }))
    cameraSystem.onPointerMove(pointerEvent({ pointerId: 2, clientX: 13, clientY: 10 })) // d2=9

    const wasClick2 = cameraSystem.onPointerUp(pointerEvent({ pointerId: 2, clientX: 13, clientY: 10 }))
    expect(wasClick2).toBe(false)
  })

  it('does not change panX/panY for micro-moves below 3px threshold', () => {
    const canvas = createCanvasStub()

    const cameraSystem = useCamera({
      canvasEl: { value: canvas },
      hostEl: { value: null },
      getLayoutNodes: () => [],
      getLayoutW: () => 0,
      getLayoutH: () => 0,
      isTestMode: () => false,
    })

    cameraSystem.camera.panX = 123
    cameraSystem.camera.panY = -45

    cameraSystem.onPointerDown(pointerEvent({ pointerId: 1, clientX: 10, clientY: 10 }))
    cameraSystem.onPointerMove(pointerEvent({ pointerId: 1, clientX: 12, clientY: 11 })) // d2=5 < 9
    cameraSystem.onPointerUp(pointerEvent({ pointerId: 1, clientX: 12, clientY: 11 }))

    expect(cameraSystem.camera.panX).toBe(123)
    expect(cameraSystem.camera.panY).toBe(-45)
  })

  it('changes panX/panY once movement reaches 3px threshold', () => {
    const canvas = createCanvasStub()

    const cameraSystem = useCamera({
      canvasEl: { value: canvas },
      hostEl: { value: null },
      getLayoutNodes: () => [],
      getLayoutW: () => 0,
      getLayoutH: () => 0,
      isTestMode: () => false,
    })

    cameraSystem.camera.panX = 10
    cameraSystem.camera.panY = 20

    cameraSystem.onPointerDown(pointerEvent({ pointerId: 1, clientX: 10, clientY: 10 }))
    // First move crosses the threshold. Pan should START from this position,
    // so we don't apply the full delta from pointerdown (prevents "jump").
    cameraSystem.onPointerMove(pointerEvent({ pointerId: 1, clientX: 13, clientY: 10 })) // d2=9
    expect(cameraSystem.camera.panX).toBe(10)
    expect(cameraSystem.camera.panY).toBe(20)

    // Next move should actually pan.
    cameraSystem.onPointerMove(pointerEvent({ pointerId: 1, clientX: 16, clientY: 10 }))
    expect(cameraSystem.camera.panX).toBe(13)
    expect(cameraSystem.camera.panY).toBe(20)
  })

  it('does not pan when graph fully fits the viewport (locks to centered pan)', () => {
    const canvas = createCanvasStub()

    const nodes: LayoutNodeLike[] = [
      { __x: 0, __y: 0 },
      { __x: 100, __y: 100 },
    ]

    const cameraSystem = useCamera({
      canvasEl: { value: canvas },
      hostEl: { value: null },
      getLayoutNodes: () => nodes,
      getLayoutW: () => 600,
      getLayoutH: () => 600,
      isTestMode: () => false,
    })

    cameraSystem.camera.zoom = 1
    cameraSystem.camera.panX = 0
    cameraSystem.camera.panY = 0

    // Establish the centered baseline.
    cameraSystem.clampCameraPan()
    const centeredX = cameraSystem.camera.panX
    const centeredY = cameraSystem.camera.panY

    // Try to pan a lot.
    cameraSystem.onPointerDown(pointerEvent({ pointerId: 1, clientX: 10, clientY: 10 }))
    cameraSystem.onPointerMove(pointerEvent({ pointerId: 1, clientX: 20, clientY: 10 })) // crosses threshold
    cameraSystem.onPointerMove(pointerEvent({ pointerId: 1, clientX: 120, clientY: 10 })) // big move
    cameraSystem.onPointerUp(pointerEvent({ pointerId: 1, clientX: 120, clientY: 10 }))

    expect(cameraSystem.camera.panX).toBeCloseTo(centeredX)
    expect(cameraSystem.camera.panY).toBeCloseTo(centeredY)
  })

  it('locks panning for a gesture when all nodes are already within viewport', () => {
    const canvas = createCanvasStub()

    // Graph bounds are inside a large viewport.
    const nodes: LayoutNodeLike[] = [
      { __x: 100, __y: 100 },
      { __x: 200, __y: 200 },
    ]

    const cameraSystem = useCamera({
      canvasEl: { value: canvas },
      hostEl: { value: null },
      getLayoutNodes: () => nodes,
      getLayoutW: () => 1000,
      getLayoutH: () => 800,
      isTestMode: () => false,
    })

    // Choose a pan that keeps everything on screen.
    cameraSystem.camera.zoom = 1
    cameraSystem.camera.panX = 0
    cameraSystem.camera.panY = 0

    // Start a pan gesture and attempt to drag.
    cameraSystem.onPointerDown(pointerEvent({ pointerId: 1, clientX: 10, clientY: 10 }))
    cameraSystem.onPointerMove(pointerEvent({ pointerId: 1, clientX: 14, clientY: 10 })) // crosses threshold
    cameraSystem.onPointerMove(pointerEvent({ pointerId: 1, clientX: 200, clientY: 200 }))
    cameraSystem.onPointerUp(pointerEvent({ pointerId: 1, clientX: 200, clientY: 200 }))

    // Locked: should remain unchanged.
    expect(cameraSystem.camera.panX).toBe(0)
    expect(cameraSystem.camera.panY).toBe(0)
  })

  it('does not activate panState when graph fits viewport (no-op background drag)', () => {
    const canvas = createCanvasStub()

    const nodes: LayoutNodeLike[] = [
      { __x: 0, __y: 0 },
      { __x: 100, __y: 100 },
    ]

    const cameraSystem = useCamera({
      canvasEl: { value: canvas },
      hostEl: { value: null },
      getLayoutNodes: () => nodes,
      getLayoutW: () => 1000,
      getLayoutH: () => 800,
      isTestMode: () => false,
    })

    cameraSystem.camera.zoom = 1
    cameraSystem.camera.panX = 0
    cameraSystem.camera.panY = 0

    cameraSystem.onPointerDown(pointerEvent({ pointerId: 1, clientX: 10, clientY: 10 }))
    expect(cameraSystem.panState.active).toBe(false)
  })

  it('activates panState when graph is not fully visible', () => {
    const canvas = createCanvasStub()

    // Graph bounds exceed viewport.
    const nodes: LayoutNodeLike[] = [
      { __x: 0, __y: 0 },
      { __x: 2000, __y: 100 },
    ]

    const cameraSystem = useCamera({
      canvasEl: { value: canvas },
      hostEl: { value: null },
      getLayoutNodes: () => nodes,
      getLayoutW: () => 600,
      getLayoutH: () => 400,
      isTestMode: () => false,
    })

    cameraSystem.camera.zoom = 1
    cameraSystem.camera.panX = 0
    cameraSystem.camera.panY = 0

    cameraSystem.onPointerDown(pointerEvent({ pointerId: 1, clientX: 10, clientY: 10 }))
    expect(cameraSystem.panState.active).toBe(true)
  })

  it('calls onCameraChanged exactly once per RAF-batched wheel deltas', () => {
    vi.useFakeTimers()

    // useCamera falls back to setTimeout when requestAnimationFrame is not present;
    // with fake timers we can deterministically flush the batch.
    const onCameraChanged = vi.fn()

    const host = {
      getBoundingClientRect: () => ({ left: 0, top: 0 }),
    } as unknown as HTMLElement

    const cameraSystem = useCamera({
      canvasEl: { value: null },
      hostEl: { value: host },
      getLayoutNodes: () => [],
      getLayoutW: () => 1000,
      getLayoutH: () => 1000,
      isTestMode: () => false,
      onCameraChanged,
    })

    // Multiple wheel events in the same tick => must coalesce into one apply.
    cameraSystem.onWheel(wheelEvent({ clientX: 10, clientY: 20, deltaY: 100 }))
    cameraSystem.onWheel(wheelEvent({ clientX: 10, clientY: 20, deltaY: 50 }))
    cameraSystem.onWheel(wheelEvent({ clientX: 10, clientY: 20, deltaY: -25 }))

    expect(onCameraChanged).toHaveBeenCalledTimes(0)

    vi.runAllTimers()

    expect(onCameraChanged).toHaveBeenCalledTimes(1)

    vi.useRealTimers()
  })

  describe('focusOnEdge', () => {
    // 1000x600 viewport, edge from (100,100) to (300,300).
    // Padding is 80px per side => the segment must fit into 840x440 and end up centered.
    // The tight axis is Y: 440 / 200 = 2.2, which is inside the interactive zoom range.
    const from = { __x: 100, __y: 100 }
    const to = { __x: 300, __y: 300 }

    function makeCamera(overrides?: { onCameraChanged?: () => void }) {
      return useCamera({
        canvasEl: { value: null },
        hostEl: { value: null },
        getLayoutNodes: () => [from, to],
        getLayoutW: () => 1000,
        getLayoutH: () => 600,
        isTestMode: () => false,
        onCameraChanged: overrides?.onCameraChanged,
      })
    }

    it('frames the whole segment, not one of its ends', () => {
      const cameraSystem = makeCamera()

      expect(cameraSystem.focusOnEdge(from, to)).toBe(true)

      expect(cameraSystem.camera.zoom).toBeCloseTo(2.2)
      expect(cameraSystem.camera.panX).toBeCloseTo(60)
      expect(cameraSystem.camera.panY).toBeCloseTo(-140)

      // Both ends are on screen, inside the 80px padding, and the segment is centered:
      // the midpoint lands on the viewport center.
      const a = cameraSystem.worldToScreen(from.__x, from.__y)
      const b = cameraSystem.worldToScreen(to.__x, to.__y)

      expect(a.x).toBeCloseTo(280)
      expect(a.y).toBeCloseTo(80)
      expect(b.x).toBeCloseTo(720)
      expect(b.y).toBeCloseTo(520)

      const mid = cameraSystem.worldToScreen((from.__x + to.__x) / 2, (from.__y + to.__y) / 2)
      expect(mid.x).toBeCloseTo(500)
      expect(mid.y).toBeCloseTo(300)
    })

    it('lands on the same camera state regardless of where the camera was', () => {
      const first = makeCamera()
      first.focusOnEdge(from, to)

      const second = makeCamera()
      second.camera.panX = -4321
      second.camera.panY = 987
      second.camera.zoom = 0.42
      second.focusOnEdge(from, to)

      expect(second.camera.panX).toBeCloseTo(first.camera.panX)
      expect(second.camera.panY).toBeCloseTo(first.camera.panY)
      expect(second.camera.zoom).toBeCloseTo(first.camera.zoom)
    })

    it('notifies that the camera changed', () => {
      const onCameraChanged = vi.fn()
      const cameraSystem = makeCamera({ onCameraChanged })

      cameraSystem.focusOnEdge(from, to)

      expect(onCameraChanged).toHaveBeenCalledTimes(1)
    })

    it('does not move the camera when an endpoint is missing, and does not throw', () => {
      const onCameraChanged = vi.fn()
      const cameraSystem = makeCamera({ onCameraChanged })

      cameraSystem.camera.panX = 11
      cameraSystem.camera.panY = 22
      cameraSystem.camera.zoom = 1.5

      expect(cameraSystem.focusOnEdge(null, to)).toBe(false)
      expect(cameraSystem.focusOnEdge(from, null)).toBe(false)
      expect(cameraSystem.focusOnEdge(undefined, undefined)).toBe(false)

      expect(cameraSystem.camera.panX).toBe(11)
      expect(cameraSystem.camera.panY).toBe(22)
      expect(cameraSystem.camera.zoom).toBe(1.5)
      expect(onCameraChanged).not.toHaveBeenCalled()
    })

    it('does not move the camera when an endpoint has non-finite coordinates', () => {
      const cameraSystem = makeCamera()

      cameraSystem.camera.panX = 5
      cameraSystem.camera.panY = 6
      cameraSystem.camera.zoom = 1

      expect(cameraSystem.focusOnEdge({ __x: Number.NaN, __y: 0 }, to)).toBe(false)
      expect(cameraSystem.focusOnEdge(from, { __x: 0, __y: Number.POSITIVE_INFINITY })).toBe(false)

      expect(cameraSystem.camera.panX).toBe(5)
      expect(cameraSystem.camera.panY).toBe(6)
      expect(cameraSystem.camera.zoom).toBe(1)
    })

    /**
     * `focusOnEdge` ends with the same `clampCameraPan()` the wheel path uses, and until now
     * nothing could tell whether that call was there: every fixture in this file lays out exactly
     * the two endpoints of the edge being framed, so the world bounds ARE the framed segment and
     * the clamp is a no-op by construction. Deleting the call left the suite green.
     *
     * Here the graph is wider than the edge — a third node far to the right, which is the ordinary
     * case in a real snapshot. The centred pan then falls outside the legal range and the clamp
     * has to bite, so its absence is visible.
     */
    it('keeps the camera inside its legal pan range after framing an edge', () => {
      const left = { __x: 0, __y: 0 }
      const right = { __x: 100, __y: 0 }
      const faraway = { __x: 5000, __y: 0 }

      const cameraSystem = useCamera({
        canvasEl: { value: null },
        hostEl: { value: null },
        getLayoutNodes: () => [left, right, faraway],
        getLayoutW: () => 600,
        getLayoutH: () => 600,
        isTestMode: () => false,
      })

      expect(cameraSystem.focusOnEdge(left, right)).toBe(true)

      // Fit of a 100-wide segment into 440 usable px wants 4.4x; the interactive ceiling is 3.
      expect(cameraSystem.camera.zoom).toBe(3)

      // Centring the segment alone would put panX at 600/2 - 50*3 = 150. The clamp caps it at
      // `padPx - minX * z` = 80, because the content extends far to the right of this edge.
      expect(cameraSystem.camera.panX).toBeCloseTo(80)
      expect(cameraSystem.camera.panX).not.toBeCloseTo(150)

      // The Y axis fits entirely, and on this path the clamp only holds bounds: the framed
      // midpoint stays on the viewport's centre line instead of being re-centred to 298.5,
      // which is the whole graph's centre and not the thing that was asked for.
      expect(cameraSystem.camera.panY).toBeCloseTo(300)
      expect(cameraSystem.camera.panY).not.toBeCloseTo(298.5)

      // What the clamp is for: the left edge of the content is not dragged off screen.
      expect(cameraSystem.worldToScreen(left.__x, left.__y).x).toBeCloseTo(80)
    })

    /**
     * The other half of the clamp's job, and the half that used to be missing.
     *
     * The test above pins the case where the clamp MUST act: the graph is far wider than the
     * framed edge, and without the clamp the camera would show emptiness beyond the world. This
     * one is the case where the same clamp used to undo the whole operation: the graph fits the
     * viewport, so `fitX`/`fitY` held, and the fitting branch assigned `centeredPan*` — the centre
     * of the WHOLE GRAPH — over the pan that had just been computed to centre one edge. The edge
     * then sat wherever the graph's centre happened to leave it, while the panel that offered
     * "Focus" claimed otherwise.
     *
     * Both cases run through one clamp and one set of bounds; only the fitting branch differs.
     */
    it('does not re-centre the graph over the framing when the content fits', () => {
      const edgeFrom = { __x: 100, __y: 300 }
      const edgeTo = { __x: 200, __y: 300 }
      // Off to the side, and inside the viewport at the framing zoom: this is what makes the
      // graph's centre differ from the edge's centre while `fitX` still holds.
      const bystander = { __x: 400, __y: 300 }

      const cameraSystem = useCamera({
        canvasEl: { value: null },
        hostEl: { value: null },
        getLayoutNodes: () => [edgeFrom, edgeTo, bystander],
        getLayoutW: () => 1000,
        getLayoutH: () => 600,
        isTestMode: () => false,
      })

      expect(cameraSystem.focusOnEdge(edgeFrom, edgeTo)).toBe(true)

      const mid = cameraSystem.worldToScreen(
        (edgeFrom.__x + edgeTo.__x) / 2,
        (edgeFrom.__y + edgeTo.__y) / 2,
      )

      // The edge's midpoint is on the viewport's centre — the thing "Focus" promises.
      expect(mid.x).toBeCloseTo(500)
      expect(mid.y).toBeCloseTo(300)

      // Re-centring the graph instead would have put it here: 200px from the left, and 1.5px
      // off the vertical centre. Both are what the fitting branch produced before.
      expect(mid.x).not.toBeCloseTo(200)
      expect(mid.y).not.toBeCloseTo(298.5)

      // Both endpoints are on screen inside the padding, which is what framing means.
      const a = cameraSystem.worldToScreen(edgeFrom.__x, edgeFrom.__y)
      const b = cameraSystem.worldToScreen(edgeTo.__x, edgeTo.__y)
      expect(a.x).toBeGreaterThanOrEqual(80)
      expect(b.x).toBeLessThanOrEqual(1000 - 80)
    })

    /**
     * Spec 007 T705 mounts a dock on the right-hand side of the canvas, and "Focus" is pressed
     * inside it. The camera is told how much is covered; it is never told what covers it.
     */
    it('frames the edge into the middle of the visible region when part of it is covered', () => {
      const cameraSystem = makeCamera()
      const dockWidth = 560
      const visibleRight = 1000 - dockWidth

      expect(cameraSystem.focusOnEdge(from, to, { viewportInsetRight: dockWidth })).toBe(true)

      const mid = cameraSystem.worldToScreen((from.__x + to.__x) / 2, (from.__y + to.__y) / 2)
      const a = cameraSystem.worldToScreen(from.__x, from.__y)
      const b = cameraSystem.worldToScreen(to.__x, to.__y)

      // Centred in the strip the user can see (440px wide), not in the canvas (1000px wide).
      expect(mid.x).toBeCloseTo(visibleRight / 2)
      expect(mid.y).toBeCloseTo(300)

      // And the whole segment clears the covered strip, with the same padding as everywhere else.
      expect(a.x).toBeGreaterThanOrEqual(80)
      expect(b.x).toBeLessThanOrEqual(visibleRight - 80)

      // Without the inset this same edge is centred on x=500 — 60px INSIDE a 560px dock, i.e.
      // underneath the panel the "Focus" button was pressed in.
      const uninformed = makeCamera()
      uninformed.focusOnEdge(from, to)
      const hidden = uninformed.worldToScreen((from.__x + to.__x) / 2, (from.__y + to.__y) / 2)
      expect(hidden.x).toBeGreaterThan(visibleRight)
    })

    it('treats an absent, zero or nonsensical inset as "nothing is covered"', () => {
      const baseline = makeCamera()
      baseline.focusOnEdge(from, to)
      const expected = { ...baseline.camera }

      for (const inset of [undefined, 0, -400, Number.NaN, Number.POSITIVE_INFINITY]) {
        const cameraSystem = makeCamera()
        expect(cameraSystem.focusOnEdge(from, to, { viewportInsetRight: inset })).toBe(true)
        expect(cameraSystem.camera.panX).toBeCloseTo(expected.panX)
        expect(cameraSystem.camera.panY).toBeCloseTo(expected.panY)
        expect(cameraSystem.camera.zoom).toBeCloseTo(expected.zoom)
      }
    })

    it('keeps the zoom inside the interactive range for a very short edge', () => {
      const near = { __x: 400, __y: 400 }
      const alsoNear = { __x: 401, __y: 400 }

      const cameraSystem = useCamera({
        canvasEl: { value: null },
        hostEl: { value: null },
        getLayoutNodes: () => [near, alsoNear],
        getLayoutW: () => 1000,
        getLayoutH: () => 600,
        isTestMode: () => true,
      })

      expect(cameraSystem.focusOnEdge(near, alsoNear)).toBe(true)
      // 840 / 1 would be 840x; the wheel path can never exceed 3.0, and neither can this.
      expect(cameraSystem.camera.zoom).toBe(3)
    })
  })
})
