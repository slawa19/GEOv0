import { afterEach, describe, expect, it, vi } from 'vitest'

import { __testing, clearGradientCache, getLinearGradient2Stops, getLinearGradient3Stops } from './gradientCache'

type MockGradient = {
  _stops: Array<{ offset: number; color: string }>
  addColorStop: (offset: number, color: string) => void
}

type MockGradientCtx = Pick<CanvasRenderingContext2D, 'createLinearGradient'> & {
  _calls: Array<{ x0: number; y0: number; x1: number; y1: number }>
  createLinearGradient: ReturnType<typeof vi.fn>
}

function asCanvasContext(ctx: MockGradientCtx): CanvasRenderingContext2D {
  return ctx as unknown as CanvasRenderingContext2D
}

function createMockCtx(): MockGradientCtx {
  const calls: Array<{ x0: number; y0: number; x1: number; y1: number }> = []

  const ctx = {
    createLinearGradient: vi.fn((x0: number, y0: number, x1: number, y1: number) => {
      calls.push({ x0, y0, x1, y1 })
      const gradient: MockGradient = {
        _stops: [],
        addColorStop(offset: number, color: string) {
          this._stops.push({ offset, color })
        },
      }
      return gradient
    }),
    _calls: calls,
  }

  return ctx
}

describe('render/gradientCache — unit', () => {
  afterEach(() => {
    __testing._setMaxCacheForTests(__testing.DEFAULT_MAX_CACHE)
  })

  it('caches gradients per ctx and does not cross-contaminate between contexts', () => {
    const ctx1 = createMockCtx()
    const ctx2 = createMockCtx()

    // Same request — should create per-ctx once.
    getLinearGradient2Stops(asCanvasContext(ctx1), 0, 0, 10, 10, 0, 'a', 1, 'b')
    getLinearGradient2Stops(asCanvasContext(ctx1), 0, 0, 10, 10, 0, 'a', 1, 'b')
    expect(ctx1.createLinearGradient).toHaveBeenCalledTimes(1)

    getLinearGradient2Stops(asCanvasContext(ctx2), 0, 0, 10, 10, 0, 'a', 1, 'b')
    expect(ctx2.createLinearGradient).toHaveBeenCalledTimes(1)
  })

  it('evicts oldest entries when size exceeds MAX_CACHE (LRU)', () => {
    __testing._setMaxCacheForTests(3)
    const ctx = createMockCtx()

    // Insert 3 distinct gradients.
    getLinearGradient2Stops(asCanvasContext(ctx), 0, 0, 10, 10, 0, 'a', 1, 'b')
    getLinearGradient2Stops(asCanvasContext(ctx), 1, 0, 10, 10, 0, 'a', 1, 'b')
    getLinearGradient2Stops(asCanvasContext(ctx), 2, 0, 10, 10, 0, 'a', 1, 'b')

    const k0 = __testing._cacheKeys(asCanvasContext(ctx))[0]
    expect(__testing._cacheSize(asCanvasContext(ctx))).toBe(3)

    // Add 4th => evict oldest.
    getLinearGradient2Stops(asCanvasContext(ctx), 3, 0, 10, 10, 0, 'a', 1, 'b')
    expect(__testing._cacheSize(asCanvasContext(ctx))).toBe(3)
    expect(__testing._cacheKeys(asCanvasContext(ctx))).not.toContain(k0)
  })

  it('clearGradientCache(ctx) invalidates cache for that ctx', () => {
    const ctx = createMockCtx()

    getLinearGradient2Stops(asCanvasContext(ctx), 0, 0, 10, 10, 0, 'a', 1, 'b')
    expect(ctx.createLinearGradient).toHaveBeenCalledTimes(1)

    clearGradientCache(asCanvasContext(ctx))

    getLinearGradient2Stops(asCanvasContext(ctx), 0, 0, 10, 10, 0, 'a', 1, 'b')
    // Cache was cleared => re-created.
    expect(ctx.createLinearGradient).toHaveBeenCalledTimes(2)
  })

  it('caches 3-stop gradients per ctx', () => {
    const ctx = createMockCtx()

    getLinearGradient3Stops(asCanvasContext(ctx), 0, 0, 10, 10, 0, 'a', 0.5, 'b', 1, 'c')
    getLinearGradient3Stops(asCanvasContext(ctx), 0, 0, 10, 10, 0, 'a', 0.5, 'b', 1, 'c')
    expect(ctx.createLinearGradient).toHaveBeenCalledTimes(1)
  })

  it('does not mix 2-stop and 3-stop cache keys', () => {
    const ctx = createMockCtx()

    getLinearGradient2Stops(asCanvasContext(ctx), 0, 0, 10, 10, 0, 'a', 1, 'b')
    getLinearGradient3Stops(asCanvasContext(ctx), 0, 0, 10, 10, 0, 'a', 0.5, 'b', 1, 'c')

    // Different key families => two creations.
    expect(ctx.createLinearGradient).toHaveBeenCalledTimes(2)
  })
})

