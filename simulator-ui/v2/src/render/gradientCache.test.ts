import { afterEach, describe, expect, it, vi } from 'vitest'

import { __testing, clearGradientCache, getLinearGradient2Stops } from './gradientCache'

function createMockCtx() {
  const calls: Array<{ x0: number; y0: number; x1: number; y1: number }> = []

  const ctx: any = {
    createLinearGradient: vi.fn((x0: number, y0: number, x1: number, y1: number) => {
      calls.push({ x0, y0, x1, y1 })
      return {
        _stops: [] as Array<{ offset: number; color: string }>,
        addColorStop(offset: number, color: string) {
          this._stops.push({ offset, color })
        },
      }
    }),
    _calls: calls,
  }

  return ctx as CanvasRenderingContext2D & {
    _calls: typeof calls
    createLinearGradient: ReturnType<typeof vi.fn>
  }
}

describe('render/gradientCache — unit', () => {
  afterEach(() => {
    __testing._setMaxCacheForTests(__testing.DEFAULT_MAX_CACHE)
  })

  it('caches gradients per ctx and does not cross-contaminate between contexts', () => {
    const ctx1 = createMockCtx()
    const ctx2 = createMockCtx()

    // Same request — should create per-ctx once.
    getLinearGradient2Stops(ctx1, 0, 0, 10, 10, 0, 'a', 1, 'b')
    getLinearGradient2Stops(ctx1, 0, 0, 10, 10, 0, 'a', 1, 'b')
    expect(ctx1.createLinearGradient).toHaveBeenCalledTimes(1)

    getLinearGradient2Stops(ctx2, 0, 0, 10, 10, 0, 'a', 1, 'b')
    expect(ctx2.createLinearGradient).toHaveBeenCalledTimes(1)
  })

  it('evicts oldest entries when size exceeds MAX_CACHE (LRU)', () => {
    __testing._setMaxCacheForTests(3)
    const ctx = createMockCtx()

    // Insert 3 distinct gradients.
    getLinearGradient2Stops(ctx, 0, 0, 10, 10, 0, 'a', 1, 'b')
    getLinearGradient2Stops(ctx, 1, 0, 10, 10, 0, 'a', 1, 'b')
    getLinearGradient2Stops(ctx, 2, 0, 10, 10, 0, 'a', 1, 'b')

    const k0 = __testing._cacheKeys(ctx)[0]
    expect(__testing._cacheSize(ctx)).toBe(3)

    // Add 4th => evict oldest.
    getLinearGradient2Stops(ctx, 3, 0, 10, 10, 0, 'a', 1, 'b')
    expect(__testing._cacheSize(ctx)).toBe(3)
    expect(__testing._cacheKeys(ctx)).not.toContain(k0)
  })

  it('clearGradientCache(ctx) invalidates cache for that ctx', () => {
    const ctx = createMockCtx()

    getLinearGradient2Stops(ctx, 0, 0, 10, 10, 0, 'a', 1, 'b')
    expect(ctx.createLinearGradient).toHaveBeenCalledTimes(1)

    clearGradientCache(ctx)

    getLinearGradient2Stops(ctx, 0, 0, 10, 10, 0, 'a', 1, 'b')
    // Cache was cleared => re-created.
    expect(ctx.createLinearGradient).toHaveBeenCalledTimes(2)
  })
})

