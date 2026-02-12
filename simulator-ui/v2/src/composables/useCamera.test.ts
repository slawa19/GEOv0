import { describe, expect, it, vi } from 'vitest'
import { useCamera } from './useCamera'

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
    const nodes = [
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
    const canvas = { setPointerCapture: () => undefined } as unknown as HTMLCanvasElement

    const cameraSystem = useCamera({
      canvasEl: { value: canvas },
      hostEl: { value: null },
      getLayoutNodes: () => [],
      getLayoutW: () => 0,
      getLayoutH: () => 0,
      isTestMode: () => false,
    })

    cameraSystem.onPointerDown({ pointerId: 1, clientX: 10, clientY: 10 } as any)
    cameraSystem.onPointerMove({ pointerId: 1, clientX: 11, clientY: 11 } as any) // d2=2 < 9

    const wasClick = cameraSystem.onPointerUp({ pointerId: 1 } as any)
    expect(wasClick).toBe(true)

    cameraSystem.onPointerDown({ pointerId: 2, clientX: 10, clientY: 10 } as any)
    cameraSystem.onPointerMove({ pointerId: 2, clientX: 13, clientY: 10 } as any) // d2=9

    const wasClick2 = cameraSystem.onPointerUp({ pointerId: 2 } as any)
    expect(wasClick2).toBe(false)
  })

  it('does not change panX/panY for micro-moves below 3px threshold', () => {
    const canvas = { setPointerCapture: () => undefined } as unknown as HTMLCanvasElement

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

    cameraSystem.onPointerDown({ pointerId: 1, clientX: 10, clientY: 10 } as any)
    cameraSystem.onPointerMove({ pointerId: 1, clientX: 12, clientY: 11 } as any) // d2=5 < 9
    cameraSystem.onPointerUp({ pointerId: 1 } as any)

    expect(cameraSystem.camera.panX).toBe(123)
    expect(cameraSystem.camera.panY).toBe(-45)
  })

  it('changes panX/panY once movement reaches 3px threshold', () => {
    const canvas = { setPointerCapture: () => undefined } as unknown as HTMLCanvasElement

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

    cameraSystem.onPointerDown({ pointerId: 1, clientX: 10, clientY: 10 } as any)
    // First move crosses the threshold. Pan should START from this position,
    // so we don't apply the full delta from pointerdown (prevents "jump").
    cameraSystem.onPointerMove({ pointerId: 1, clientX: 13, clientY: 10 } as any) // d2=9
    expect(cameraSystem.camera.panX).toBe(10)
    expect(cameraSystem.camera.panY).toBe(20)

    // Next move should actually pan.
    cameraSystem.onPointerMove({ pointerId: 1, clientX: 16, clientY: 10 } as any)
    expect(cameraSystem.camera.panX).toBe(13)
    expect(cameraSystem.camera.panY).toBe(20)
  })

  it('does not pan when graph fully fits the viewport (locks to centered pan)', () => {
    const canvas = { setPointerCapture: () => undefined } as unknown as HTMLCanvasElement

    const nodes = [
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
    cameraSystem.onPointerDown({ pointerId: 1, clientX: 10, clientY: 10 } as any)
    cameraSystem.onPointerMove({ pointerId: 1, clientX: 20, clientY: 10 } as any) // crosses threshold
    cameraSystem.onPointerMove({ pointerId: 1, clientX: 120, clientY: 10 } as any) // big move
    cameraSystem.onPointerUp({ pointerId: 1 } as any)

    expect(cameraSystem.camera.panX).toBeCloseTo(centeredX)
    expect(cameraSystem.camera.panY).toBeCloseTo(centeredY)
  })

  it('locks panning for a gesture when all nodes are already within viewport', () => {
    const canvas = { setPointerCapture: () => undefined } as unknown as HTMLCanvasElement

    // Graph bounds are inside a large viewport.
    const nodes = [
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
    cameraSystem.onPointerDown({ pointerId: 1, clientX: 10, clientY: 10 } as any)
    cameraSystem.onPointerMove({ pointerId: 1, clientX: 14, clientY: 10 } as any) // crosses threshold
    cameraSystem.onPointerMove({ pointerId: 1, clientX: 200, clientY: 200 } as any)
    cameraSystem.onPointerUp({ pointerId: 1 } as any)

    // Locked: should remain unchanged.
    expect(cameraSystem.camera.panX).toBe(0)
    expect(cameraSystem.camera.panY).toBe(0)
  })

  it('does not activate panState when graph fits viewport (no-op background drag)', () => {
    const canvas = { setPointerCapture: () => undefined } as unknown as HTMLCanvasElement

    const nodes = [
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

    cameraSystem.onPointerDown({ pointerId: 1, clientX: 10, clientY: 10 } as any)
    expect(cameraSystem.panState.active).toBe(false)
  })

  it('activates panState when graph is not fully visible', () => {
    const canvas = { setPointerCapture: () => undefined } as unknown as HTMLCanvasElement

    // Graph bounds exceed viewport.
    const nodes = [
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

    cameraSystem.onPointerDown({ pointerId: 1, clientX: 10, clientY: 10 } as any)
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
    cameraSystem.onWheel({ clientX: 10, clientY: 20, deltaY: 100 } as any)
    cameraSystem.onWheel({ clientX: 10, clientY: 20, deltaY: 50 } as any)
    cameraSystem.onWheel({ clientX: 10, clientY: 20, deltaY: -25 } as any)

    expect(onCameraChanged).toHaveBeenCalledTimes(0)

    vi.runAllTimers()

    expect(onCameraChanged).toHaveBeenCalledTimes(1)

    vi.useRealTimers()
  })
})
