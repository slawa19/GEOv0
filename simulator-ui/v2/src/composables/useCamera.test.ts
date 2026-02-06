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
