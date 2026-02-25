import { computed, nextTick, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import { computeSnapshotStructuralKey, topologyFingerprint, useLayoutCoordinator } from './useLayoutCoordinator'

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

// ---------------------------------------------------------------------------
// topologyFingerprint — pure function tests (ITEM-2+7, NOTE C-1)
// ---------------------------------------------------------------------------
describe('topologyFingerprint', () => {
  it('returns different fingerprints for same count but different last node ID', () => {
    const fp1 = topologyFingerprint({
      nodes: [{ id: 'A' }, { id: 'B' }, { id: 'C' }],
      links: [],
    })
    const fp2 = topologyFingerprint({
      nodes: [{ id: 'A' }, { id: 'B' }, { id: 'Z' }],
      links: [],
    })
    expect(fp1).not.toEqual(fp2)
  })

  it('returns different fingerprints for same node IDs but edge swap (A→B vs A→C)', () => {
    const fp1 = topologyFingerprint({
      nodes: [{ id: 'A' }, { id: 'B' }, { id: 'C' }],
      links: [{ source: 'A', target: 'B' }],
    })
    const fp2 = topologyFingerprint({
      nodes: [{ id: 'A' }, { id: 'B' }, { id: 'C' }],
      links: [{ source: 'A', target: 'C' }],
    })
    expect(fp1).not.toEqual(fp2)
  })

  it('returns the same fingerprint for identical snapshots', () => {
    const snap = { nodes: [{ id: 'X' }], links: [{ source: 'X', target: 'Y' }] }
    expect(topologyFingerprint(snap)).toEqual(topologyFingerprint(snap))
  })

  it('returns different fingerprints for empty edges vs one edge (same nodes)', () => {
    const fp1 = topologyFingerprint({ nodes: [{ id: 'A' }], links: [] })
    const fp2 = topologyFingerprint({ nodes: [{ id: 'A' }], links: [{ source: 'A', target: 'B' }] })
    expect(fp1).not.toEqual(fp2)
  })

  it('is order-independent for nodes and links arrays (reorder-only patches)', () => {
    const fp1 = topologyFingerprint({
      nodes: [{ id: 'A' }, { id: 'B' }, { id: 'C' }],
      links: [
        { source: 'A', target: 'B' },
        { source: 'B', target: 'C' },
      ],
    })

    const fp2 = topologyFingerprint({
      nodes: [{ id: 'C' }, { id: 'A' }, { id: 'B' }],
      links: [
        { source: 'B', target: 'C' },
        { source: 'A', target: 'B' },
      ],
    })

    expect(fp2).toEqual(fp1)
  })
})

// ---------------------------------------------------------------------------
// useLayoutCoordinator — integration tests
// ---------------------------------------------------------------------------
describe('useLayoutCoordinator', () => {
  it('computeSnapshotStructuralKey changes when node IDs set changes (even if lengths are equal)', () => {
    const k1 = computeSnapshotStructuralKey({
      nodes: [{ id: 'A' }, { id: 'B' }],
      links: [{}, {}],
    })
    const k2 = computeSnapshotStructuralKey({
      nodes: [{ id: 'A' }, { id: 'C' }],
      links: [{}, {}],
    })

    expect(k1).not.toEqual(k2)
    expect(k1.split('|').slice(0, 2)).toEqual(['2', '2'])
  })

  it('computeSnapshotStructuralKey changes when edge changes (same node IDs, same counts)', () => {
    const k1 = computeSnapshotStructuralKey({
      nodes: [{ id: 'A' }, { id: 'B' }],
      links: [{ source: 'A', target: 'B' }],
    })
    const k2 = computeSnapshotStructuralKey({
      nodes: [{ id: 'A' }, { id: 'B' }],
      links: [{ source: 'A', target: 'C' }], // edge swap: A→B → A→C
    })
    expect(k1).not.toEqual(k2)
    // counts are the same, so the leading segments must match
    expect(k1.split('|').slice(0, 2)).toEqual(['2', '1'])
    expect(k2.split('|').slice(0, 2)).toEqual(['2', '1'])
  })

  it('snapshot structural watcher triggers relayout on edge swap (same node IDs)', async () => {
    vi.useFakeTimers()

    const snapshotRef = ref({
      generated_at: 't1',
      nodes: [{ id: 'A' }, { id: 'B' }],
      links: [{ source: 'A', target: 'B' }],
    })

    const computeLayout = vi.fn()

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
      clampCameraPan: vi.fn(),
    })

    coordinator.layout.w = 800
    coordinator.layout.h = 600

    // Baseline: one compute.
    coordinator.requestRelayoutDebounced(0)
    vi.runAllTimers()
    expect(computeLayout).toHaveBeenCalledTimes(1)

    // Edge swap in-place (SSE topology patch style): same node IDs, same counts.
    snapshotRef.value.links[0]!.target = 'C' // was A→B, now A→C

    await nextTick()
    vi.runAllTimers()

    expect(computeLayout).toHaveBeenCalledTimes(2)

    vi.useRealTimers()
  })

  it('edge swap with same node IDs and counts invalidates snapKey → triggers relayout', () => {
    vi.useFakeTimers()

    const snapshotRef = ref({
      generated_at: 't1',
      nodes: [{ id: 'A' }, { id: 'B' }],
      links: [{ source: 'A', target: 'B' }],
    })

    const computeLayout = vi.fn()

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
      clampCameraPan: vi.fn(),
    })

    coordinator.layout.w = 800
    coordinator.layout.h = 600

    coordinator.requestRelayoutDebounced(0)
    vi.runAllTimers()
    expect(computeLayout).toHaveBeenCalledTimes(1)

    // Direct ref swap: same node IDs and same counts, only edge target changes.
    snapshotRef.value = {
      ...snapshotRef.value,
      links: [{ source: 'A', target: 'C' }], // was A→B, now A→C
    }

    coordinator.requestRelayoutDebounced(0)
    vi.runAllTimers()

    // snapKey must differ → computeLayout called again.
    expect(computeLayout).toHaveBeenCalledTimes(2)

    vi.useRealTimers()
  })

  it('snapshot structural watcher triggers relayout on in-place node ID replacement (same counts)', async () => {
    vi.useFakeTimers()

    const snapshotRef = ref({
      generated_at: 't1',
      nodes: [{ id: 'A' }, { id: 'B' }],
      links: [],
    })

    const computeLayout = vi.fn()

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
      clampCameraPan: vi.fn(),
    })

    coordinator.layout.w = 800
    coordinator.layout.h = 600

    // Baseline: one compute.
    coordinator.requestRelayoutDebounced(0)
    vi.runAllTimers()
    expect(computeLayout).toHaveBeenCalledTimes(1)

    // Mutate snapshot in-place (SSE topology patch style): replace one node ID.
    snapshotRef.value.nodes[0]!.id = 'Z'

    // Watcher is flush:'post' so wait for Vue post-flush queue.
    await nextTick()

    // Relayout is scheduled via requestRelayoutDebounced(0) which uses RAF/setTimeout.
    vi.runAllTimers()

    expect(computeLayout).toHaveBeenCalledTimes(2)

    vi.useRealTimers()
  })

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

  it('coalesces relayout RAF + resize-relayout RAF into a single compute (ITEM-18)', () => {
    vi.useFakeTimers()

    withMockWindowAndDocument(({ win }) => {
      const wakeUp = vi.fn()
      const computeLayout = vi.fn()

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

      // Same animation frame: schedule relayout and then resize-relayout.
      coordinator.requestRelayoutDebounced(0)
      win.dispatchEvent({ type: 'resize' })

      vi.runAllTimers()

      expect(computeLayout).toHaveBeenCalledTimes(1)
      expect(computeLayout).toHaveBeenCalledWith(expect.anything(), 640, 480, 'admin-force')
      expect(wakeUp).toHaveBeenCalledTimes(1)

      coordinator.teardownResizeListener()
    })

    withMockWindowAndDocument(({ win }) => {
      const wakeUp = vi.fn()
      const computeLayout = vi.fn()

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

      // Same animation frame: schedule resize-relayout and then relayout.
      win.dispatchEvent({ type: 'resize' })
      coordinator.requestRelayoutDebounced(0)

      vi.runAllTimers()

      expect(computeLayout).toHaveBeenCalledTimes(1)
      expect(computeLayout).toHaveBeenCalledWith(expect.anything(), 640, 480, 'admin-force')
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

  it('DPR listener setup does not throw when matchMedia throws (sandbox SecurityError)', () => {
    withMockWindowAndDocument(({ win }) => {
      win.matchMedia = () => {
        throw new Error('SecurityError')
      }

      const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => undefined)

      const coordinator = useLayoutCoordinator({
        canvasEl: ref(createMockCanvas()),
        fxCanvasEl: ref(createMockCanvas()),
        hostEl: ref(null),
        snapshot: computed(() => ({ generated_at: 't1', nodes: [], links: [] })),
        layoutMode: ref<'admin-force'>('admin-force'),
        dprClamp: computed(() => 10),
        isTestMode: computed(() => false),
        getSourcePath: () => 'src',
        computeLayout: vi.fn(),
        clampCameraPan: vi.fn(),
      })

      expect(() => coordinator.setupResizeListener()).not.toThrow()
      expect(() => coordinator.teardownResizeListener()).not.toThrow()

      warnSpy.mockRestore()
    })
  })

  it('DPR listener setup supports legacy MediaQueryList.addListener API (no addEventListener)', () => {
    withMockWindowAndDocument(({ win }) => {
      const listeners: Listener[] = []
      win.matchMedia = (query: string) => {
        return {
          media: query,
          matches: true,
          addListener: (cb: Listener) => listeners.push(cb),
          removeListener: (cb: Listener) => {
            const idx = listeners.indexOf(cb)
            if (idx >= 0) listeners.splice(idx, 1)
          },
        } as any
      }

      const coordinator = useLayoutCoordinator({
        canvasEl: ref(createMockCanvas()),
        fxCanvasEl: ref(createMockCanvas()),
        hostEl: ref(null),
        snapshot: computed(() => ({ generated_at: 't1', nodes: [], links: [] })),
        layoutMode: ref<'admin-force'>('admin-force'),
        dprClamp: computed(() => 10),
        isTestMode: computed(() => false),
        getSourcePath: () => 'src',
        computeLayout: vi.fn(),
        clampCameraPan: vi.fn(),
      })

      expect(() => coordinator.setupResizeListener()).not.toThrow()
      expect(listeners.length).toBe(1)
      expect(() => coordinator.teardownResizeListener()).not.toThrow()
    })
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

  it('generated_at changes do not invalidate layout cache', async () => {
    vi.useFakeTimers()

    const snapshotRef = ref({
      generated_at: 't1',
      nodes: [{ id: 'A' }],
      links: [],
    })

    const computeLayout = vi.fn()

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
      clampCameraPan: vi.fn(),
    })

    coordinator.layout.w = 800
    coordinator.layout.h = 600

    coordinator.requestRelayoutDebounced(0)
    vi.runAllTimers()
    expect(computeLayout).toHaveBeenCalledTimes(1)

    // Only timestamp changes: should not cause another computeLayout.
    snapshotRef.value = { ...snapshotRef.value, generated_at: 't2' }
    coordinator.requestRelayoutDebounced(0)
    vi.runAllTimers()
    expect(computeLayout).toHaveBeenCalledTimes(1)

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
