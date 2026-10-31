import { afterEach, describe, expect, it, vi } from 'vitest'

import { useCamera } from './useCamera'

/**
 * MM1-M10: the wheel coalesces its deltas into one animation frame. A camera reset (the "reset view" button,
 * `useViewControls.resetView`, and the scene change, `useSceneState.clearScene`, both call `resetCamera`) inside that
 * frame must not be undone by the frame: the pending zoom belongs to the camera the user was looking at, not to the
 * reset one.
 */
function wheelEvent(deltaY: number): WheelEvent {
  return { clientX: 100, clientY: 50, deltaY } as unknown as WheelEvent
}

function makeCamera() {
  const onCameraChanged = vi.fn()
  const cameraSystem = useCamera({
    canvasEl: { value: null },
    hostEl: { value: { getBoundingClientRect: () => ({ left: 0, top: 0 }) } as unknown as HTMLElement },
    getLayoutNodes: () => [],
    getLayoutW: () => 1000,
    getLayoutH: () => 600,
    isTestMode: () => false,
    onCameraChanged,
  })
  return { cameraSystem, onCameraChanged }
}

/** A hand-driven requestAnimationFrame / cancelAnimationFrame pair. */
function stubFrames() {
  let nextId = 1
  const queue = new Map<number, (t: number) => void>()
  vi.stubGlobal('requestAnimationFrame', (cb: (t: number) => void) => {
    const id = nextId++
    queue.set(id, cb)
    return id
  })
  vi.stubGlobal('cancelAnimationFrame', (id: number) => { queue.delete(id) })
  return {
    pending: () => queue.size,
    flush: () => {
      const due = [...queue.entries()]
      queue.clear()
      for (const [, cb] of due) cb(0)
    },
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('MM1-M10: resetCamera cancels the pending wheel frame', () => {
  it('a wheel, then a reset in the same frame: the frame does not zoom the reset camera', () => {
    const frames = stubFrames()
    const { cameraSystem } = makeCamera()
    cameraSystem.camera.zoom = 2
    cameraSystem.camera.panX = 40
    cameraSystem.camera.panY = -30

    cameraSystem.onWheel(wheelEvent(-500))
    cameraSystem.resetCamera()
    frames.flush()

    expect({ ...cameraSystem.camera }).toEqual({ panX: 0, panY: 0, zoom: 1 })
    expect(frames.pending()).toBe(0)
  })

  it('after the reset the wheel works again, with only the deltas that came after it', () => {
    const frames = stubFrames()
    const { cameraSystem, onCameraChanged } = makeCamera()

    cameraSystem.onWheel(wheelEvent(-500))
    cameraSystem.resetCamera()
    cameraSystem.onWheel(wheelEvent(100))
    expect(frames.pending(), 'a new frame is scheduled after the reset').toBe(1)
    frames.flush()

    expect(cameraSystem.camera.zoom).toBeCloseTo(Math.exp(-0.1), 12)
    expect(onCameraChanged).toHaveBeenCalledTimes(1)
  })

  it('a reset with no wheel pending changes nothing about the next wheel (counter-check: the frame still applies)', () => {
    const frames = stubFrames()
    const { cameraSystem } = makeCamera()

    cameraSystem.resetCamera()
    cameraSystem.onWheel(wheelEvent(-200))
    frames.flush()

    expect(cameraSystem.camera.zoom).toBeCloseTo(Math.exp(0.2), 12)
  })

  it('the same on the setTimeout fallback (no requestAnimationFrame)', () => {
    vi.stubGlobal('requestAnimationFrame', undefined)
    vi.stubGlobal('cancelAnimationFrame', undefined)
    vi.useFakeTimers()
    const { cameraSystem } = makeCamera()
    cameraSystem.camera.zoom = 2

    cameraSystem.onWheel(wheelEvent(-500))
    cameraSystem.resetCamera()
    vi.runAllTimers()

    expect({ ...cameraSystem.camera }).toEqual({ panX: 0, panY: 0, zoom: 1 })
  })

  it('focusOnEdge inside the frame: the framing is not zoomed by the earlier wheel (sibling of the reset path)', () => {
    const frames = stubFrames()
    const { cameraSystem } = makeCamera()
    const a = { __x: 100, __y: 100 }
    const b = { __x: 300, __y: 200 }

    // Zoom OUT: the framing of a short edge sits at or near ZOOM_MAX, where a zoom-in would be clamped away and the
    // test would pass without the fix.
    cameraSystem.onWheel(wheelEvent(500))
    expect(cameraSystem.focusOnEdge(a, b)).toBe(true)
    const framed = { ...cameraSystem.camera }
    frames.flush()

    expect({ ...cameraSystem.camera }).toEqual(framed)
    expect(frames.pending()).toBe(0)
  })

  it('focusOnEdge that cannot place the edge leaves the pending wheel alone (counter-check)', () => {
    const frames = stubFrames()
    const { cameraSystem } = makeCamera()

    cameraSystem.onWheel(wheelEvent(-200))
    expect(cameraSystem.focusOnEdge(null, { __x: 1, __y: 1 })).toBe(false)
    frames.flush()

    expect(cameraSystem.camera.zoom).toBeCloseTo(Math.exp(0.2), 12)
  })
})
