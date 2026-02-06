import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'

import { clearGradientCache } from '../render/gradientCache'

import {
  __cachedPosHygiene,
  __pruneCachedPosToSnapshotNodes,
  __shouldClearCachedPosOnSnapshotChange,
  useRenderLoop,
} from './useRenderLoop'

vi.mock('../render/gradientCache', () => ({
  clearGradientCache: vi.fn(),
}))

function makeCanvas(): HTMLCanvasElement {
  // Minimal canvas stub for renderLoop: getContext must exist.
  return {
    width: 1,
    height: 1,
    style: { width: '1px', height: '1px' } as any,
    getContext: () => ({
      setTransform: vi.fn(),
      clearRect: vi.fn(),
      fillRect: vi.fn(),
      translate: vi.fn(),
      scale: vi.fn(),
      save: vi.fn(),
      restore: vi.fn(),
      createRadialGradient: () => ({ addColorStop: vi.fn() }),
    }) as any,
  } as any
}

function makeCanvasWithCtx() {
  const ctx = {
    setTransform: vi.fn(),
    clearRect: vi.fn(),
    fillRect: vi.fn(),
    translate: vi.fn(),
    scale: vi.fn(),
    save: vi.fn(),
    restore: vi.fn(),
    createRadialGradient: () => ({ addColorStop: vi.fn() }),
  } as any

  const canvas: HTMLCanvasElement = {
    width: 1,
    height: 1,
    style: { width: '1px', height: '1px' } as any,
    getContext: () => ctx,
  } as any

  return { canvas, ctx }
}

function makeLoop() {
  const canvas = makeCanvas()
  const fxCanvas = makeCanvas()

  return useRenderLoop({
    canvasEl: { value: canvas } as any,
    fxCanvasEl: { value: fxCanvas } as any,
    getSnapshot: () => ({ generated_at: 't1', nodes: [], links: [], palette: {} } as any),
    getLayout: () => ({ w: 10, h: 10, nodes: [], links: [] }),
    getCamera: () => ({ panX: 0, panY: 0, zoom: 1 }),
    isTestMode: () => false,
    getQuality: () => 'low',
    getFlash: () => 0,
    setFlash: () => undefined,
    pruneFloatingLabels: () => undefined,
    drawBaseGraph: () => ({}),
    renderFxFrame: () => undefined,
    mapping: { fx: { flash: { clearing: { from: '#000', to: '#000' } } } },
    fxState: { sparks: [], edgePulses: [], nodeBursts: [] },
    getSelectedNodeId: () => null,
    activeEdges: new Set(),
    getLinkLod: () => 'full',
    getHiddenNodeId: () => null,
    beforeDraw: () => undefined,
    isAnimating: () => false,
  })
}

describe('useRenderLoop deep idle / wakeUp / ensureRenderLoop invariants', () => {
  const prevWindow = (globalThis as any).window

  beforeEach(() => {
    vi.useFakeTimers()

    const rafQueue: Array<(t: number) => void> = []

    const requestAnimationFrame = vi.fn((cb: (t: number) => void) => {
      rafQueue.push(cb)
      return rafQueue.length
    })
    const cancelAnimationFrame = vi.fn()

    const setTimeoutSpy = vi.fn(
      (fn: () => void, ms: number) => setTimeout(fn, ms) as unknown as number,
    )
    const clearTimeoutSpy = vi.fn((id: number) => clearTimeout(id as unknown as any))

    ;(globalThis as any).window = {
      performance: { now: () => 0 },
      requestAnimationFrame,
      cancelAnimationFrame,
      setTimeout: setTimeoutSpy,
      clearTimeout: clearTimeoutSpy,
      __rafQueue: rafQueue,
    }
  })

  afterEach(() => {
    vi.useRealTimers()
    ;(globalThis as any).window = prevWindow
  })

  it('wakeUp() is idempotent: repeated calls do not double-schedule RAF', () => {
    const loop = makeLoop()
    loop.ensureRenderLoop()
    const win = (globalThis as any).window

    expect(win.requestAnimationFrame).toHaveBeenCalledTimes(1)
    expect(win.__rafQueue.length).toBe(1)

    // RAF already queued => wakeUp should not schedule another frame.
    loop.wakeUp()
    loop.wakeUp()
    expect(win.requestAnimationFrame).toHaveBeenCalledTimes(1)
    expect(win.__rafQueue.length).toBe(1)
  })

  it('wakeUp() cancels an active idle-timeout and guarantees a frame is queued', () => {
    const loop = makeLoop()
    const win = (globalThis as any).window

    loop.ensureRenderLoop()

    // Run the first RAF sufficiently after start so we are outside "holdActive" window,
    // otherwise scheduleNext will queue RAF immediately.
    win.__rafQueue.shift()!(1000)

    // Not active + outside hold => should be in idle-throttle mode (timeout scheduled).
    expect(win.setTimeout).toHaveBeenCalledTimes(1)
    expect(win.__rafQueue.length).toBe(0)

    loop.wakeUp()

    // Wake-up should cancel the idle timeout and enqueue a RAF immediately.
    expect(win.clearTimeout).toHaveBeenCalledTimes(1)
    expect(win.requestAnimationFrame).toHaveBeenCalledTimes(2)
    expect(win.__rafQueue.length).toBe(1)

    // Idempotent while RAF is pending.
    loop.wakeUp()
    expect(win.requestAnimationFrame).toHaveBeenCalledTimes(2)
    expect(win.__rafQueue.length).toBe(1)
  })

  it('after reaching deep idle, no further scheduling happens until wakeUp()/ensureRenderLoop()', () => {
    const loop = makeLoop()
    const win = (globalThis as any).window

    loop.ensureRenderLoop()

    // First frame at t=1000 -> idle timeout (no RAF queued).
    expect(win.__rafQueue.length).toBe(1)
    win.__rafQueue.shift()!(1000)
    expect(win.__rafQueue.length).toBe(0)
    expect(win.setTimeout).toHaveBeenCalledTimes(1)

    // Timer fires => schedules a RAF.
    vi.runOnlyPendingTimers()
    expect(win.__rafQueue.length).toBe(1)

    // Run RAF at deep-idle time (>= 3000ms since lastActivityTime).
    win.__rafQueue.shift()!(4000)

    // Deep idle: loop stops completely (no RAF, no timeout).
    expect(win.__rafQueue.length).toBe(0)
    const rafCallsAtDeepIdle = win.requestAnimationFrame.mock.calls.length
    const timeoutCallsAtDeepIdle = win.setTimeout.mock.calls.length

    // Even if time passes, nothing should self-schedule.
    vi.advanceTimersByTime(10_000)
    expect(win.__rafQueue.length).toBe(0)
    expect(win.requestAnimationFrame.mock.calls.length).toBe(rafCallsAtDeepIdle)
    expect(win.setTimeout.mock.calls.length).toBe(timeoutCallsAtDeepIdle)

    // wakeUp() explicitly recovers by scheduling a RAF.
    loop.wakeUp()
    expect(win.__rafQueue.length).toBe(1)
  })

  it('ensureRenderLoop() restores scheduling after deep idle (no user input required)', () => {
    const loop = makeLoop()
    const win = (globalThis as any).window

    loop.ensureRenderLoop()
    win.__rafQueue.shift()!(1000)
    vi.runOnlyPendingTimers()
    win.__rafQueue.shift()!(4000)

    expect(win.__rafQueue.length).toBe(0)

    loop.ensureRenderLoop()
    expect(win.__rafQueue.length).toBe(1)
  })

  it('stopRenderLoop() disables the loop: wakeUp() must not resurrect scheduling', () => {
    const loop = makeLoop()
    const win = (globalThis as any).window

    loop.ensureRenderLoop()
    expect(win.__rafQueue.length).toBe(1)

    loop.stopRenderLoop()
    expect(win.cancelAnimationFrame).toHaveBeenCalledTimes(1)
    expect(win.__rafQueue.length).toBe(1) // queue is test-side; cancelAnimationFrame is the actual contract.

    const rafCallsBeforeWake = win.requestAnimationFrame.mock.calls.length

    loop.wakeUp()
    expect(win.requestAnimationFrame.mock.calls.length).toBe(rafCallsBeforeWake)
  })

  it('deep idle -> wakeUp schedules next RAF', () => {
    const loop = makeLoop()

    loop.ensureRenderLoop()
    const win = (globalThis as any).window

    // First scheduled frame.
    expect(win.__rafQueue.length).toBe(1)

    // Run first frame sufficiently after start so we are outside "holdActive" window,
    // otherwise scheduleNext will queue RAF immediately.
    win.__rafQueue.shift()!(1000)
    expect(win.__rafQueue.length).toBe(0)

    // Deep idle triggers after 3000ms of no activity.
    // Execute an idle-throttle tick at t=4000 that should stop scheduling.
    // 1) run timers to fire the idle timeout -> it will schedule RAF
    vi.runOnlyPendingTimers()
    expect(win.__rafQueue.length).toBe(1)
    // 2) run that RAF at deep-idle time
    win.__rafQueue.shift()!(4000)
    expect(win.__rafQueue.length).toBe(0)

    // Now loop is in deep idle (no scheduling pending). wakeUp must schedule next RAF.
    loop.wakeUp()
    expect(win.__rafQueue.length).toBe(1)
  })

  it('does not enter deep idle before the first successful renderFrame (no snapshot yet)', () => {
    const { canvas, ctx } = makeCanvasWithCtx()
    const { canvas: fxCanvas } = makeCanvasWithCtx()

    let snap: any = null

    const loop = useRenderLoop({
      canvasEl: { value: canvas } as any,
      fxCanvasEl: { value: fxCanvas } as any,
      getSnapshot: () => snap,
      getLayout: () => ({ w: 10, h: 10, nodes: [], links: [] }),
      getCamera: () => ({ panX: 0, panY: 0, zoom: 1 }),
      isTestMode: () => false,
      getQuality: () => 'low',
      getFlash: () => 0,
      setFlash: () => undefined,
      pruneFloatingLabels: () => undefined,
      drawBaseGraph: () => ({}),
      renderFxFrame: () => undefined,
      mapping: { fx: { flash: { clearing: { from: '#000', to: '#000' } } } },
      fxState: { sparks: [], edgePulses: [], nodeBursts: [] },
      getSelectedNodeId: () => null,
      activeEdges: new Set(),
      getLinkLod: () => 'full',
      getHiddenNodeId: () => null,
      beforeDraw: () => undefined,
      isAnimating: () => false,
    })

    const win = (globalThis as any).window

    loop.ensureRenderLoop()
    expect(win.__rafQueue.length).toBe(1)

    // No snapshot => renderFrame early-return.
    win.__rafQueue.shift()!(1000)
    vi.runOnlyPendingTimers()
    expect(win.__rafQueue.length).toBe(1)

    // Past deep-idle delay, still no snapshot => MUST keep scheduling (no full stop).
    win.__rafQueue.shift()!(4000)
    vi.runOnlyPendingTimers()
    expect(win.__rafQueue.length).toBe(1)

    // Snapshot appears: first frame should render without any external wakeUp.
    snap = { generated_at: 't1', nodes: [], links: [], palette: {} }
    win.__rafQueue.shift()!(5000)
    expect(ctx.clearRect).toHaveBeenCalled()
    expect(ctx.fillRect).toHaveBeenCalled()
  })

  it('snapshot+layout appear after long wait (5s+) -> first frame renders automatically without user input', () => {
    const { canvas, ctx } = makeCanvasWithCtx()
    const { canvas: fxCanvas } = makeCanvasWithCtx()

    let snap: any = null
    let layout = { w: 0, h: 0, nodes: [], links: [] }

    const loop = useRenderLoop({
      canvasEl: { value: canvas } as any,
      fxCanvasEl: { value: fxCanvas } as any,
      getSnapshot: () => snap,
      getLayout: () => layout,
      getCamera: () => ({ panX: 0, panY: 0, zoom: 1 }),
      isTestMode: () => false,
      getQuality: () => 'low',
      getFlash: () => 0,
      setFlash: () => undefined,
      pruneFloatingLabels: () => undefined,
      drawBaseGraph: () => ({}),
      renderFxFrame: () => undefined,
      mapping: { fx: { flash: { clearing: { from: '#000', to: '#000' } } } },
      fxState: { sparks: [], edgePulses: [], nodeBursts: [] },
      getSelectedNodeId: () => null,
      activeEdges: new Set(),
      getLinkLod: () => 'full',
      getHiddenNodeId: () => null,
      beforeDraw: () => undefined,
      isAnimating: () => false,
    })

    const win = (globalThis as any).window

    loop.ensureRenderLoop()
    expect(win.__rafQueue.length).toBe(1)

    // No snapshot/layout yet => renderFrame early-return, but loop must keep scheduling.
    win.__rafQueue.shift()!(1000)
    vi.runOnlyPendingTimers()
    expect(win.__rafQueue.length).toBe(1)

    // Wait well past deep-idle delay; still no snapshot/layout => MUST still schedule (idle cadence).
    win.__rafQueue.shift()!(6000)
    vi.runOnlyPendingTimers()
    expect(win.__rafQueue.length).toBe(1)

    // Snapshot + layout finally arrive (after 5s+). First real draw must happen automatically
    // on the already-scheduled frame (no wakeUp / ensureRenderLoop calls).
    snap = { generated_at: 't1', nodes: [], links: [], palette: {} }
    layout = { w: 10, h: 10, nodes: [], links: [] }
    win.__rafQueue.shift()!(6100)
    expect(ctx.clearRect).toHaveBeenCalled()
    expect(ctx.fillRect).toHaveBeenCalled()
  })

  it('after the first successful renderFrame, deep idle is allowed again (can stop scheduling)', () => {
    const { canvas, ctx } = makeCanvasWithCtx()
    const { canvas: fxCanvas } = makeCanvasWithCtx()

    let snap: any = null

    const loop = useRenderLoop({
      canvasEl: { value: canvas } as any,
      fxCanvasEl: { value: fxCanvas } as any,
      getSnapshot: () => snap,
      getLayout: () => ({ w: 10, h: 10, nodes: [], links: [] }),
      getCamera: () => ({ panX: 0, panY: 0, zoom: 1 }),
      isTestMode: () => false,
      getQuality: () => 'low',
      getFlash: () => 0,
      setFlash: () => undefined,
      pruneFloatingLabels: () => undefined,
      drawBaseGraph: () => ({}),
      renderFxFrame: () => undefined,
      mapping: { fx: { flash: { clearing: { from: '#000', to: '#000' } } } },
      fxState: { sparks: [], edgePulses: [], nodeBursts: [] },
      getSelectedNodeId: () => null,
      activeEdges: new Set(),
      getLinkLod: () => 'full',
      getHiddenNodeId: () => null,
      beforeDraw: () => undefined,
      isAnimating: () => false,
    })

    const win = (globalThis as any).window

    loop.ensureRenderLoop()

    // Early-return frames while waiting for snapshot.
    win.__rafQueue.shift()!(1000)
    vi.runOnlyPendingTimers()
    win.__rafQueue.shift()!(4000)
    vi.runOnlyPendingTimers()

    // Now provide snapshot: this frame is the first successful render.
    snap = { generated_at: 't1', nodes: [], links: [], palette: {} }
    const timeoutCallsBefore = win.setTimeout.mock.calls.length
    win.__rafQueue.shift()!(5000)
    expect(ctx.clearRect).toHaveBeenCalled()

    // Since it's already past deep-idle delay, loop is allowed to stop completely after rendering once.
    expect(win.__rafQueue.length).toBe(0)
    expect(win.setTimeout.mock.calls.length).toBe(timeoutCallsBefore)
    vi.advanceTimersByTime(10_000)
    expect(win.__rafQueue.length).toBe(0)
  })

  it('does not pass interaction-quality hints to renderers', () => {
    const { canvas, ctx } = makeCanvasWithCtx()
    const { canvas: fxCanvas } = makeCanvasWithCtx()

    const drawBaseGraph = vi.fn((_ctx: any, opts: any) => opts.pos)
    const renderFxFrame = vi.fn((_opts: any) => undefined)

    ;(globalThis as any).window.devicePixelRatio = 2

    const loop = useRenderLoop({
      canvasEl: { value: canvas } as any,
      fxCanvasEl: { value: fxCanvas } as any,
      getSnapshot: () => ({ generated_at: 't1', nodes: [], links: [], palette: {} } as any),
      getLayout: () => ({ w: 100, h: 80, nodes: [], links: [] }),
      getCamera: () => ({ panX: 0, panY: 0, zoom: 1 }),
      isTestMode: () => false,
      getQuality: () => 'high',
      getFlash: () => 0,
      setFlash: () => undefined,
      pruneFloatingLabels: () => undefined,
      drawBaseGraph,
      renderFxFrame,
      mapping: { fx: { flash: { clearing: { from: '#000', to: '#000' } } } },
      fxState: { sparks: [], edgePulses: [], nodeBursts: [] },
      getSelectedNodeId: () => null,
      activeEdges: new Set(),
      getLinkLod: () => 'full',
      getHiddenNodeId: () => null,
      beforeDraw: () => undefined,
      isAnimating: () => false,
    })

    loop.renderOnce(0)

    // DPR is no longer clamped during interaction — stays at full 2.0
    expect(canvas.width).toBe(Math.floor(100 * 2))
    expect(canvas.height).toBe(Math.floor(80 * 2))
    expect(fxCanvas.width).toBe(canvas.width)
    expect(fxCanvas.height).toBe(canvas.height)

    expect(ctx.clearRect).toHaveBeenCalled()

    const baseOpts = drawBaseGraph.mock.calls[0]?.[1] as any
    expect(baseOpts?.interaction).toBeUndefined()
    expect(baseOpts?.interactionIntensity).toBeUndefined()

    const fxOpts = renderFxFrame.mock.calls[0]?.[0] as any
    expect(fxOpts?.interaction).toBeUndefined()
    expect(fxOpts?.interactionIntensity).toBeUndefined()
  })

  it('resize invalidates gradient cache (clearGradientCache called only on real resize)', () => {
    ;(clearGradientCache as any).mockClear?.()

    const { canvas, ctx } = makeCanvasWithCtx()
    const { canvas: fxCanvas } = makeCanvasWithCtx()

    ;(globalThis as any).window.devicePixelRatio = 2

    const loop = useRenderLoop({
      canvasEl: { value: canvas } as any,
      fxCanvasEl: { value: fxCanvas } as any,
      getSnapshot: () => ({ generated_at: 't1', nodes: [], links: [], palette: {} } as any),
      getLayout: () => ({ w: 100, h: 80, nodes: [], links: [] }),
      getCamera: () => ({ panX: 0, panY: 0, zoom: 1 }),
      isTestMode: () => false,
      getQuality: () => 'high',
      getFlash: () => 0,
      setFlash: () => undefined,
      pruneFloatingLabels: () => undefined,
      drawBaseGraph: () => ({}),
      renderFxFrame: () => undefined,
      mapping: { fx: { flash: { clearing: { from: '#000', to: '#000' } } } },
      fxState: { sparks: [], edgePulses: [], nodeBursts: [] },
      getSelectedNodeId: () => null,
      activeEdges: new Set(),
      getLinkLod: () => 'full',
      getHiddenNodeId: () => null,
      beforeDraw: () => undefined,
      isAnimating: () => false,
    })

    // First render: canvas size changes => must invalidate.
    loop.renderOnce(0)
    expect(clearGradientCache).toHaveBeenCalledTimes(1)
    expect(clearGradientCache).toHaveBeenCalledWith(ctx)

    // Stable size: must NOT keep clearing.
    loop.renderOnce(16)
    expect(clearGradientCache).toHaveBeenCalledTimes(1)
  })
})

describe('useRenderLoop cachedPos hygiene on snapshot changes', () => {
  it('does not request clear when there is overlap with cached ids (same scene)', () => {
    const cachedPos = new Map<string, any>([
      ['A', { x: 1, y: 1 }],
      ['B', { x: 2, y: 2 }],
      ['C', { x: 3, y: 3 }],
    ])

    const snapshotNodes = [{ id: 'A' }, { id: 'B' }, { id: 'C' }]

    expect(
      __shouldClearCachedPosOnSnapshotChange({
        cachedPos,
        snapshotNodes,
      }),
    ).toBe(false)
  })

  it('requests clear when snapshot ids do not intersect with cached ids (new scene)', () => {
    const cachedPos = new Map<string, any>([
      ['A', { x: 1, y: 1 }],
      ['B', { x: 2, y: 2 }],
    ])

    const snapshotNodes = [{ id: 'X' }, { id: 'Y' }, { id: 'Z' }]

    expect(
      __shouldClearCachedPosOnSnapshotChange({
        cachedPos,
        snapshotNodes,
      }),
    ).toBe(true)
  })

  it('prunes cachedPos when node composition changes but generated_at (snapshotKey) does not', () => {
    const cachedPos = new Map<string, any>([
      ['A', { x: 1, y: 1 }],
      ['B', { x: 2, y: 2 }],
      ['C', { x: 3, y: 3 }],
    ])

    // First snapshot: [A,B,C]
    const snap1: any = { equivalent: 'UAH', generated_at: 't1', nodes: [{ id: 'A' }, { id: 'B' }, { id: 'C' }], links: [] }
    // Patch update within the same scene: [A,B] but `generated_at` stays the same.
    const snap2: any = { equivalent: 'UAH', generated_at: 't1', nodes: [{ id: 'A' }, { id: 'B' }], links: [] }

    // snapshotKey stays stable
    const loop = makeLoop()
    // Force two renders with different snapshots in deps.getSnapshot
    let current = snap1
    ;(loop as any).__deps = undefined

    // Use a new loop instance with overridable getSnapshot
    const canvas = makeCanvas()
    const fxCanvas = makeCanvas()
    const loop2 = useRenderLoop({
      canvasEl: { value: canvas } as any,
      fxCanvasEl: { value: fxCanvas } as any,
      getSnapshot: () => current as any,
      getLayout: () => ({ w: 10, h: 10, nodes: [], links: [] }),
      getCamera: () => ({ panX: 0, panY: 0, zoom: 1 }),
      isTestMode: () => false,
      getQuality: () => 'low',
      getFlash: () => 0,
      setFlash: () => undefined,
      pruneFloatingLabels: () => undefined,
      drawBaseGraph: (_ctx: any, opts: any) => {
        // Inject our cachedPos map into the render path.
        // The render loop passes it as opts.pos.
        expect(opts.pos).toBeInstanceOf(Map)
        ;(opts.pos as Map<string, any>).clear()
        for (const [k, v] of cachedPos) (opts.pos as Map<string, any>).set(k, v)
        return opts.pos
      },
      renderFxFrame: () => undefined,
      mapping: { fx: { flash: { clearing: { from: '#000', to: '#000' } } } },
      fxState: { sparks: [], edgePulses: [], nodeBursts: [] },
      getSelectedNodeId: () => null,
      activeEdges: new Set(),
      getLinkLod: () => 'full',
      getHiddenNodeId: () => null,
      beforeDraw: () => undefined,
      isAnimating: () => false,
    })

    // First render seeds internal cachedPos with [A,B,C]
    loop2.renderOnce(0)
    current = snap2
    loop2.renderOnce(16)

    // Since cachedPos is internal, we validate pruning helper directly:
    // when snapshot nodes become [A,B], stale C must be dropped.
    __pruneCachedPosToSnapshotNodes({ cachedPos, snapshotNodes: snap2.nodes })
    expect([...cachedPos.keys()].sort()).toEqual(['A', 'B'])
  })

  it('does not prune every frame when composition is stable (prune called once per change)', () => {
    const pruneSpy = vi.spyOn(__cachedPosHygiene, 'pruneToSnapshotNodes')

    const cachedPos = new Map<string, any>([
      ['A', { x: 1, y: 1 }],
      ['B', { x: 2, y: 2 }],
      ['C', { x: 3, y: 3 }],
    ])

    const canvas = makeCanvas()
    const fxCanvas = makeCanvas()
    let current: any = { equivalent: 'UAH', generated_at: 't1', nodes: [{ id: 'A' }, { id: 'B' }, { id: 'C' }], links: [] }

    // Wrap drawBaseGraph to keep internal pos growing like real code would.
    const loop = useRenderLoop({
      canvasEl: { value: canvas } as any,
      fxCanvasEl: { value: fxCanvas } as any,
      getSnapshot: () => current as any,
      getLayout: () => ({ w: 10, h: 10, nodes: [], links: [] }),
      getCamera: () => ({ panX: 0, panY: 0, zoom: 1 }),
      isTestMode: () => false,
      getQuality: () => 'low',
      getFlash: () => 0,
      setFlash: () => undefined,
      pruneFloatingLabels: () => undefined,
      drawBaseGraph: (_ctx: any, opts: any) => {
        // Seed with cachedPos only once; subsequent frames use same map.
        if ((opts.pos as Map<string, any>).size === 0) {
          for (const [k, v] of cachedPos) (opts.pos as Map<string, any>).set(k, v)
        }
        return opts.pos
      },
      renderFxFrame: () => undefined,
      mapping: { fx: { flash: { clearing: { from: '#000', to: '#000' } } } },
      fxState: { sparks: [], edgePulses: [], nodeBursts: [] },
      getSelectedNodeId: () => null,
      activeEdges: new Set(),
      getLinkLod: () => 'full',
      getHiddenNodeId: () => null,
      beforeDraw: () => undefined,
      isAnimating: () => false,
    })

    // First frame always establishes snapshot identity and may run hygiene once.
    loop.renderOnce(0)
    pruneSpy.mockClear()

    // Stable frames: must not trigger additional prune work.
    loop.renderOnce(16)
    loop.renderOnce(32)
    expect(pruneSpy).toHaveBeenCalledTimes(0)

    // Composition change without generated_at change: should trigger exactly once.
    current = { ...current, nodes: [{ id: 'A' }, { id: 'B' }], links: [] }
    loop.renderOnce(48)
    loop.renderOnce(64)
    expect(pruneSpy).toHaveBeenCalledTimes(1)
  })
})

