import { describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

import { useRealClearingFx } from './useRealClearingFx'

describe('useRealClearingFx', () => {
  it('deferred clearing amount overlay: pushes only after layout coords become available', () => {
    vi.useFakeTimers()
    vi.setSystemTime(0)
    try {
      const isTestMode = ref(false)

      let layoutReady = false
      const getLayoutNodeById = (id: string) => {
        if (!layoutReady) return null
        if (id === 'A') return { __x: 0, __y: 0 }
        if (id === 'B') return { __x: 10, __y: 0 }
        return null
      }

      const overlayCalls: any[] = []
      const pushClearingAmountOverlay = (overlay: any, opts: any) => {
        overlayCalls.push({ overlay, opts })
      }

      const fx = useRealClearingFx({
        fxState: {},
        isTestMode,
        isWebDriver: false,
        keyEdge: (from, to) => `${from}>${to}`,
        seedFn: () => 0,
        clearingColor: '#000',
        addActiveNode: () => undefined,
        addActiveEdge: () => undefined,
        pushClearingAmountOverlay,
        scheduleTimeout: (fn, ms) => {
          setTimeout(fn, ms)
        },
        getLayoutNodeById,
        setFlash: () => undefined,
        nowMs: () => Date.now(),
        nowEpochMs: () => Date.now(),
        spawnEdgePulses: () => undefined,
        spawnNodeBursts: () => undefined,
      })

      setTimeout(() => {
        layoutReady = true
      }, 120)

      fx.runClearingFx({
        edges: [{ from: 'A', to: 'B' }],
        totalAmount: '10.00',
        equivalent: 'UAH',
        planId: 'p1',
      })

      vi.advanceTimersByTime(200)

      expect(overlayCalls.length).toBe(1)
      expect(overlayCalls[0].overlay.text).toBe('−10.00 UAH')
      expect(overlayCalls[0].overlay.worldX).toBe(5)
      expect(overlayCalls[0].overlay.worldY).toBe(0)
      expect(overlayCalls[0].opts.color).toBeDefined()
    } finally {
      vi.useRealTimers()
    }
  })

  it('deferred clearing amount overlay: times out and does not push when coords never appear', () => {
    vi.useFakeTimers()
    vi.setSystemTime(0)
    try {
      const isTestMode = ref(false)

      const overlayCalls: any[] = []

      const fx = useRealClearingFx({
        fxState: {},
        isTestMode,
        isWebDriver: false,
        keyEdge: (from, to) => `${from}>${to}`,
        seedFn: () => 0,
        clearingColor: '#000',
        addActiveNode: () => undefined,
        addActiveEdge: () => undefined,
        pushClearingAmountOverlay: (overlay, opts) => {
          overlayCalls.push({ overlay, opts })
        },
        scheduleTimeout: (fn, ms) => {
          setTimeout(fn, ms)
        },
        getLayoutNodeById: () => null,
        setFlash: () => undefined,
        nowMs: () => Date.now(),
        nowEpochMs: () => Date.now(),
        spawnEdgePulses: () => undefined,
        spawnNodeBursts: () => undefined,
      })

      fx.runClearingFx({
        edges: [{ from: 'A', to: 'B' }],
        totalAmount: '10.00',
        equivalent: 'UAH',
        planId: 'p1',
      })

      vi.advanceTimersByTime(1200)
      expect(overlayCalls.length).toBe(0)
    } finally {
      vi.useRealTimers()
    }
  })

  it('dedup reset: HTTP path (no planId) is suppressed until resetDedup()', () => {
    vi.useFakeTimers()
    vi.setSystemTime(0)
    try {
      const isTestMode = ref(false)

      const spawnEdgePulses = vi.fn()

      const fx = useRealClearingFx({
        fxState: {},
        isTestMode,
        isWebDriver: false,
        keyEdge: (from, to) => `${from}>${to}`,
        seedFn: () => 0,
        clearingColor: '#000',
        addActiveNode: () => undefined,
        addActiveEdge: () => undefined,
        scheduleTimeout: (fn, ms) => {
          setTimeout(fn, ms)
        },
        getLayoutNodeById: () => null,
        setFlash: () => undefined,
        nowMs: () => Date.now(),
        nowEpochMs: () => Date.now(),
        spawnEdgePulses: spawnEdgePulses as any,
        spawnNodeBursts: () => undefined,
      })

      fx.runClearingFx({ edges: [{ from: 'A', to: 'B' }], totalAmount: '0', equivalent: 'UAH' })
      fx.runClearingFx({ edges: [{ from: 'A', to: 'B' }], totalAmount: '0', equivalent: 'UAH' })
      expect(spawnEdgePulses).toHaveBeenCalledTimes(1)

      fx.resetDedup()
      fx.runClearingFx({ edges: [{ from: 'A', to: 'B' }], totalAmount: '0', equivalent: 'UAH' })
      expect(spawnEdgePulses).toHaveBeenCalledTimes(2)
    } finally {
      vi.useRealTimers()
    }
  })
})
