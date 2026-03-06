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
})
