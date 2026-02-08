import { computed, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import { useLayoutCoordinator } from './useLayoutCoordinator'

type Listener = (ev?: any) => void

function createMockEventTarget() {
  const listeners = new Map<string, Set<Listener>>()
  return {
    addEventListener: (type: string, cb: Listener) => {
      if (!listeners.has(type)) listeners.set(type, new Set())
      listeners.get(type)!.add(cb)
    },
    removeEventListener: (type: string, cb: Listener) => {
      listeners.get(type)?.delete(cb)
    },
    dispatchEvent: (ev: { type: string }) => {
      for (const cb of listeners.get(ev.type) ?? []) cb(ev)
    },
  }
}

function createMockCanvas() {
  return {
    width: 0,
    height: 0,
    style: { width: '', height: '' },
  } as any
}

function createMockHost(w: number, h: number) {
  return {
    getBoundingClientRect: () => ({ width: w, height: h }),
  } as any
}

function withMockWindowAndDocument<T>(fn: (ctx: { win: any; doc: any }) => T): T {
  const prevWindow = (globalThis as any).window
  const prevDocument = (globalThis as any).document
  const prevResizeObserver = (globalThis as any).ResizeObserver

  const winTarget = createMockEventTarget()
  const docTarget = createMockEventTarget()

  const win = {
    ...winTarget,
    devicePixelRatio: 1,
    performance: { now: () => 0 },
    setTimeout: globalThis.setTimeout.bind(globalThis),
    clearTimeout: globalThis.clearTimeout.bind(globalThis),
    requestAnimationFrame: (cb: (t: number) => void) => {
      return globalThis.setTimeout(() => cb(0), 0) as any
    },
    cancelAnimationFrame: (id: any) => {
      globalThis.clearTimeout(id)
    },
    matchMedia: undefined as any,
  }

  const doc = {
    ...docTarget,
    visibilityState: 'visible' as 'visible' | 'hidden',
  }

  ;(globalThis as any).window = win
  ;(globalThis as any).document = doc

  try {
    return fn({ win, doc })
  } finally {
    ;(globalThis as any).window = prevWindow
    ;(globalThis as any).document = prevDocument
    ;(globalThis as any).ResizeObserver = prevResizeObserver
  }
}

describe('useLayoutCoordinator', () => {
  it('visibility/DPR relayout runs strictly after resize updates layout.w/h (ordering regression)', () => {
    vi.useFakeTimers()

    withMockWindowAndDocument(({ win, doc }) => {
      // Model real browser semantics: RAF callbacks run after setTimeout(0).
      // In the original bug, relayout was scheduled via setTimeout(0) and could run
      // before the resize RAF updated layout.w/h.
      win.requestAnimationFrame = (cb: (t: number) => void) => {
        return globalThis.setTimeout(() => cb(0), 16) as any
      }

      const wakeUp = vi.fn()
      const computeLayout = vi.fn()

      const coordinator = useLayoutCoordinator({
        canvasEl: ref(createMockCanvas()),
        fxCanvasEl: ref(createMockCanvas()),
        hostEl: ref(createMockHost(800, 600)),
        snapshot: computed(() => ({ generated_at: 't1', nodes: [], links: [] })),
        layoutMode: ref<'admin-force'>('admin-force'),
        dprClamp: computed(() => 10),
        isTestMode: computed(() => false),
        getSourcePath: () => 'src',
        computeLayout,
        clampCameraPan: vi.fn(),
        wakeUp,
      })

      doc.visibilityState = 'visible'
      coordinator.setupResizeListener()
      doc.dispatchEvent({ type: 'visibilitychange' })

      // Flush only setTimeout(0) tasks; RAF is still pending.
      vi.advanceTimersByTime(0)

      // Relayout must NOT run on stale 0x0 size before RAF resize.
      expect(computeLayout).toHaveBeenCalledTimes(0)

      // Now flush RAF.
      vi.advanceTimersByTime(16)

      expect(computeLayout).toHaveBeenCalledTimes(1)
      expect(computeLayout).toHaveBeenCalledWith(expect.anything(), 800, 600, 'admin-force')
      expect(wakeUp).toHaveBeenCalled()

      coordinator.teardownResizeListener()
    })

    vi.useRealTimers()
  })

  it('visibilitychange triggers wakeUp + resize/layout scheduling when tab becomes visible', () => {
    vi.useFakeTimers()

    withMockWindowAndDocument(({ doc }) => {
      const wakeUp = vi.fn()

      const coordinator = useLayoutCoordinator({
        canvasEl: ref(createMockCanvas()),
        fxCanvasEl: ref(createMockCanvas()),
        hostEl: ref(createMockHost(800, 600)),
        snapshot: computed(() => ({ generated_at: 't1', nodes: [], links: [] })),
        layoutMode: ref<'admin-force'>('admin-force'),
        dprClamp: computed(() => 10),
        isTestMode: computed(() => false),
        getSourcePath: () => 'src',
        computeLayout: vi.fn(),
        clampCameraPan: vi.fn(),
        wakeUp,
      })

      doc.visibilityState = 'visible'
      coordinator.setupResizeListener()
      doc.dispatchEvent({ type: 'visibilitychange' })
      vi.runAllTimers()
      expect(wakeUp).toHaveBeenCalled()
      coordinator.teardownResizeListener()
    })

    vi.useRealTimers()
  })

  it('ResizeObserver triggers requestResizeAndLayout (wakeUp) when host container resizes', () => {
    vi.useFakeTimers()

    withMockWindowAndDocument(() => {
      const wakeUp = vi.fn()
      let roCb: ((entries: any[]) => void) | null = null

      ;(globalThis as any).ResizeObserver = class {
        constructor(cb: (entries: any[]) => void) {
          roCb = cb
        }
        observe() {}
        disconnect() {}
      }

      const host = createMockHost(500, 400)

      const coordinator = useLayoutCoordinator({
        canvasEl: ref(createMockCanvas()),
        fxCanvasEl: ref(createMockCanvas()),
        hostEl: ref(host),
        snapshot: computed(() => ({ generated_at: 't1', nodes: [], links: [] })),
        layoutMode: ref<'admin-force'>('admin-force'),
        dprClamp: computed(() => 10),
        isTestMode: computed(() => false),
        getSourcePath: () => 'src',
        computeLayout: vi.fn(),
        clampCameraPan: vi.fn(),
        wakeUp,
      })

      coordinator.setupResizeListener()
      if (!roCb) throw new Error('ResizeObserver callback was not captured')
      ;(roCb as any)([{ target: host }])
      vi.runAllTimers()
      expect(wakeUp).toHaveBeenCalled()
      coordinator.teardownResizeListener()
    })

    vi.useRealTimers()
  })

	it('coalesces window.resize + ResizeObserver into one resize+relayout+wakeUp batch', () => {
		vi.useFakeTimers()

		withMockWindowAndDocument(({ win }) => {
			const wakeUp = vi.fn()
			const computeLayout = vi.fn()
			let roCb: ((entries: any[]) => void) | null = null

			;(globalThis as any).ResizeObserver = class {
				constructor(cb: (entries: any[]) => void) {
					roCb = cb
				}
				observe() {}
				disconnect() {}
			}

			const host = createMockHost(640, 480)

			const coordinator = useLayoutCoordinator({
				canvasEl: ref(createMockCanvas()),
				fxCanvasEl: ref(createMockCanvas()),
				hostEl: ref(host),
				snapshot: computed(() => ({ generated_at: 't1', nodes: [], links: [] })),
				layoutMode: ref<'admin-force'>('admin-force'),
				dprClamp: computed(() => 10),
				isTestMode: computed(() => false),
				getSourcePath: () => 'src',
				computeLayout,
				clampCameraPan: vi.fn(),
				wakeUp,
			})

			coordinator.setupResizeListener()
			if (!roCb) throw new Error('ResizeObserver callback was not captured')

			// Same "batch" / tick: both signals arrive before RAF flush.
			win.dispatchEvent({ type: 'resize' })
			;(roCb as any)([{ target: host }])

			vi.runAllTimers()

			expect(computeLayout).toHaveBeenCalledTimes(1)
			expect(wakeUp).toHaveBeenCalledTimes(1)

			coordinator.teardownResizeListener()
		})

		vi.useRealTimers()
	})

	it('does not wakeUp / recompute when resize signals arrive but size did not change', () => {
		vi.useFakeTimers()

		withMockWindowAndDocument(({ win }) => {
			const wakeUp = vi.fn()
			const computeLayout = vi.fn()
			let roCb: ((entries: any[]) => void) | null = null

			;(globalThis as any).ResizeObserver = class {
				constructor(cb: (entries: any[]) => void) {
					roCb = cb
				}
				observe() {}
				disconnect() {}
			}

			const host = createMockHost(320, 200)

			const coordinator = useLayoutCoordinator({
				canvasEl: ref(createMockCanvas()),
				fxCanvasEl: ref(createMockCanvas()),
				hostEl: ref(host),
				snapshot: computed(() => ({ generated_at: 't1', nodes: [], links: [] })),
				layoutMode: ref<'admin-force'>('admin-force'),
				dprClamp: computed(() => 10),
				isTestMode: computed(() => false),
				getSourcePath: () => 'src',
				computeLayout,
				clampCameraPan: vi.fn(),
				wakeUp,
			})

			coordinator.setupResizeListener()
			if (!roCb) throw new Error('ResizeObserver callback was not captured')

			// First batch: size changes from initial 0 -> 320x200, must compute + wake.
			win.dispatchEvent({ type: 'resize' })
			;(roCb as any)([{ target: host }])
			vi.runAllTimers()
			expect(computeLayout).toHaveBeenCalledTimes(1)
			expect(wakeUp).toHaveBeenCalledTimes(1)

			// Second batch: no size change, must be a no-op (no extra compute/wake).
			win.dispatchEvent({ type: 'resize' })
			;(roCb as any)([{ target: host }])
			vi.runAllTimers()

			expect(computeLayout).toHaveBeenCalledTimes(1)
			expect(wakeUp).toHaveBeenCalledTimes(1)

			coordinator.teardownResizeListener()
		})

		vi.useRealTimers()
	})

  it('DPR watcher callback re-registers media query listener and schedules resize/layout', () => {
    vi.useFakeTimers()

    withMockWindowAndDocument(({ win }) => {
      const wakeUp = vi.fn()

      const matchMediaCalls: string[] = []
      const mqlListeners: Listener[] = []

      win.devicePixelRatio = 1
      win.matchMedia = (query: string) => {
        matchMediaCalls.push(query)
        return {
          media: query,
          matches: true,
          addEventListener: (_: string, cb: Listener) => {
            mqlListeners.push(cb)
          },
          removeEventListener: (_: string, cb: Listener) => {
            const idx = mqlListeners.indexOf(cb)
            if (idx >= 0) mqlListeners.splice(idx, 1)
          },
        } as any
      }

      const coordinator = useLayoutCoordinator({
        canvasEl: ref(createMockCanvas()),
        fxCanvasEl: ref(createMockCanvas()),
        hostEl: ref(createMockHost(700, 500)),
        snapshot: computed(() => ({ generated_at: 't1', nodes: [], links: [] })),
        layoutMode: ref<'admin-force'>('admin-force'),
        dprClamp: computed(() => 10),
        isTestMode: computed(() => false),
        getSourcePath: () => 'src',
        computeLayout: vi.fn(),
        clampCameraPan: vi.fn(),
        wakeUp,
      })

      coordinator.setupResizeListener()
      expect(matchMediaCalls.length).toBe(1)
      expect(mqlListeners.length).toBe(1)

      // Simulate DPR change: update dpr and fire the media-query listener.
      win.devicePixelRatio = 2
      mqlListeners[0]?.({ matches: false } as any)
      vi.runAllTimers()

      // Should have re-registered with new `(resolution: <dpr>dppx)`.
      expect(matchMediaCalls.length).toBeGreaterThanOrEqual(2)
      expect(wakeUp).toHaveBeenCalled()

      coordinator.teardownResizeListener()
    })

    vi.useRealTimers()
  })

  it('debounced relayout respects lastLayoutKey cache', async () => {
    vi.useFakeTimers()

    const snapshotRef = ref({
      generated_at: 't1',
      nodes: [{ id: 'A' }],
      links: [],
    })

    const computeLayout = vi.fn()
    const clampCameraPan = vi.fn()

    const coordinator = useLayoutCoordinator({
      canvasEl: ref(null),
      fxCanvasEl: ref(null),
      hostEl: ref(null),
      snapshot: computed(() => snapshotRef.value),
      layoutMode: ref<'admin-force'>('admin-force'),
      dprClamp: computed(() => 1),
      isTestMode: computed(() => false),
      getSourcePath: () => 'src',
      computeLayout,
      clampCameraPan,
    })

    coordinator.layout.w = 800
    coordinator.layout.h = 600

    coordinator.requestRelayoutDebounced(10)
    vi.advanceTimersByTime(10)

    expect(computeLayout).toHaveBeenCalledTimes(1)
    expect(clampCameraPan).toHaveBeenCalledTimes(1)

    coordinator.requestRelayoutDebounced(10)
    vi.advanceTimersByTime(10)

    expect(computeLayout).toHaveBeenCalledTimes(1)
    expect(clampCameraPan).toHaveBeenCalledTimes(2)

    vi.useRealTimers()
  })

  it('requestResizeAndLayout coalesces to one scheduled run', () => {
    vi.useFakeTimers()

    const computeLayout = vi.fn()
    const clampCameraPan = vi.fn()
		const wakeUp = vi.fn()

    const coordinator = useLayoutCoordinator({
			canvasEl: ref(createMockCanvas()),
			fxCanvasEl: ref(createMockCanvas()),
			hostEl: ref(createMockHost(800, 600)),
      snapshot: computed(() => ({ generated_at: 't1', nodes: [], links: [] })),
      layoutMode: ref<'admin-force'>('admin-force'),
      dprClamp: computed(() => 1),
      isTestMode: computed(() => false),
      getSourcePath: () => 'src',
      computeLayout,
      clampCameraPan,
			wakeUp,
    })

    coordinator.requestResizeAndLayout()
    coordinator.requestResizeAndLayout()

		vi.runAllTimers()

		expect(clampCameraPan).toHaveBeenCalledTimes(1)
		expect(wakeUp).toHaveBeenCalledTimes(1)

		vi.useRealTimers()
	})

  it('changing layoutMode triggers relayout and wakeUp', () => {
    vi.useFakeTimers()

    const computeLayout = vi.fn()
    const clampCameraPan = vi.fn()
    const wakeUp = vi.fn()

    const layoutMode = ref<'admin-force' | 'type-split'>('admin-force')

    const coordinator = useLayoutCoordinator({
      canvasEl: ref(createMockCanvas()),
      fxCanvasEl: ref(createMockCanvas()),
      hostEl: ref(createMockHost(800, 600)),
      snapshot: computed(() => ({ generated_at: 't1', nodes: [], links: [] })),
      layoutMode,
      dprClamp: computed(() => 1),
      isTestMode: computed(() => false),
      getSourcePath: () => 'src',
      computeLayout,
      clampCameraPan,
      wakeUp,
    })

    // Initial sizing/layout.
    coordinator.resizeAndLayout()
    expect(computeLayout).toHaveBeenCalledTimes(1)

    // Mode change must relayout even if size is unchanged.
    layoutMode.value = 'type-split'
    vi.runAllTimers()

    expect(computeLayout).toHaveBeenCalledTimes(2)
    expect(wakeUp).toHaveBeenCalled()

    vi.useRealTimers()
  })

  it('wakeUp handler uses latest implementation even if changed after init', () => {
    // Regression test for wiring bug: callers capture deps.wakeUp at init-time.
    // If wakeUp is implemented as `let wakeUp = () => {}` and later reassigned,
    // the coordinator would keep calling the stale no-op reference.
    let wakeUpImpl = vi.fn()
    const wakeUp = () => wakeUpImpl()

    let w = 800
    const host = {
      getBoundingClientRect: () => ({ width: w, height: 600 }),
    } as any

    const canvas = createMockCanvas()
    const fxCanvas = createMockCanvas()

    const coordinator = useLayoutCoordinator({
      canvasEl: ref(canvas),
      fxCanvasEl: ref(fxCanvas),
      hostEl: ref(host),
      snapshot: computed(() => ({ generated_at: 't1', nodes: [], links: [] })),
      layoutMode: ref<'admin-force'>('admin-force'),
      dprClamp: computed(() => 1),
      isTestMode: computed(() => false),
      getSourcePath: () => 'src',
      computeLayout: vi.fn(),
      clampCameraPan: vi.fn(),
      wakeUp,
    })

    // First run: changed=true due to initial sizing -> wakeUp invoked.
    coordinator.resizeAndLayout()
    expect(wakeUpImpl).toHaveBeenCalledTimes(1)

    // Make a size change so resizeAndLayout will call wakeUp again.
    w = 801

    wakeUpImpl = vi.fn()
    coordinator.resizeAndLayout()
    expect(wakeUpImpl).toHaveBeenCalledTimes(1)
  })
})
