import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest'

import { __testing, resetGlowSpritesCache } from './glowSprites'

describe('render/glowSprites — unit', () => {
  const originalDocument = (globalThis as any).document

  beforeAll(() => {
    // Node test environment has no DOM; glowSprites uses document.createElement('canvas').
    // Provide a minimal stub sufficient for cache/LRU logic.
    ;(globalThis as any).document = {
      createElement: (tag: string) => {
        if (tag !== 'canvas') throw new Error(`Unexpected createElement tag: ${tag}`)
        return {
          width: 0,
          height: 0,
          getContext: () => {
            // Enough for the 'bloom' circle path used in LRU tests.
            return {
              save: () => undefined,
              restore: () => undefined,
              clearRect: () => undefined,
              beginPath: () => undefined,
              arc: () => undefined,
              fill: () => undefined,
              stroke: () => undefined,
            } as any
          },
        } as any
      },
    }
  })

  afterAll(() => {
    ;(globalThis as any).document = originalDocument
  })

  beforeEach(() => {
    __testing._cacheClear()
  })

  it('resetGlowSpritesCache() empties the cache; next request creates a new sprite', () => {
    const opts: any = {
      kind: 'bloom',
      shape: 'circle',
      color: '#ffffff',
      w: 10,
      h: 10,
      r: 10,
      rr: 0,
      blurPx: 2,
      lineWidthPx: 1,
    }

    const key = __testing.keyFor(opts)
    const s1 = __testing._getGlowSprite(opts)
    expect(__testing._cacheHas(key)).toBe(true)
    expect(__testing._cacheKeys().length).toBe(1)

    resetGlowSpritesCache()
    expect(__testing._cacheHas(key)).toBe(false)
    expect(__testing._cacheKeys().length).toBe(0)

    const s2 = __testing._getGlowSprite(opts)
    expect(__testing._cacheKeys().length).toBe(1)
    expect(s2).not.toBe(s1)
  })

  describe('q()', () => {
    it.each([
      [-1.01, -1],
      [-0.76, -1],
      [-0.74, -0.5],
      [-0.51, -0.5],
      [-0.49, -0.5],
      [-0.26, -0.5],
      [-0.24, 0],
      [0, 0],
      [0.24, 0],
      [0.26, 0.5],
      [0.49, 0.5],
      [0.51, 0.5],
      [0.74, 0.5],
      [0.76, 1],
      [1.01, 1],
    ])('quantizes %s with step=0.5 to %s', (input, expected) => {
      const got = __testing.q(input)
      expect(got).toBeCloseTo(expected, 10)
      // Default step should produce multiples of 0.5.
      expect(Number.isInteger(got / 0.5)).toBe(true)
    })

    it('maps non-finite values to a safe deterministic value', () => {
      expect(__testing.q(Number.NaN)).toBe(0)
      expect(__testing.q(Number.POSITIVE_INFINITY)).toBe(0)
      expect(__testing.q(Number.NEGATIVE_INFINITY)).toBe(0)
    })
  })

  describe('keyFor()', () => {
    it('is deterministic for identical inputs', () => {
      const o: any = {
        kind: 'bloom',
        shape: 'circle',
        w: 10,
        h: 11,
        r: 12,
        rr: 0,
        color: '#ff00ff',
        blurPx: 3,
        lineWidthPx: 1,
      }

      expect(__testing.keyFor(o)).toBe(__testing.keyFor({ ...o }))
    })

    it.each(['fx-dot', 'fx-ring', 'fx-bloom'] as const)('is deterministic for %s', (kind) => {
      const common: any = {
        color: '#aabbcc',
        r: 12.34,
        blurPx: 5.67,
      }

      const o: any = kind === 'fx-ring' ? { ...common, kind, thicknessPx: 2.5 } : { ...common, kind }
      expect(__testing.keyFor(o)).toBe(__testing.keyFor({ ...o }))
    })

    it('never emits NaN/Infinity/undefined in the key string (sanitizes via q())', () => {
      const o: any = {
        kind: 'rim',
        shape: 'rounded-rect',
        w: Number.NaN,
        h: Number.POSITIVE_INFINITY,
        r: Number.NEGATIVE_INFINITY,
        rr: Number.NaN,
        color: '#00ff00',
        blurPx: Number.NaN,
        lineWidthPx: Number.POSITIVE_INFINITY,
      }

      const key = __testing.keyFor(o)
      expect(key).not.toContain('NaN')
      expect(key).not.toContain('Infinity')
      expect(key).not.toContain('undefined')
    })

    it.each(['fx-dot', 'fx-ring', 'fx-bloom'] as const)(
      'never emits NaN/Infinity/undefined in the key string for %s',
      (kind) => {
        const common: any = {
          kind,
          color: '#00ff00',
          r: Number.NaN,
          blurPx: Number.POSITIVE_INFINITY,
        }
        const o: any = kind === 'fx-ring' ? { ...common, thicknessPx: Number.NEGATIVE_INFINITY } : common

        const key = __testing.keyFor(o)
        expect(key).not.toContain('NaN')
        expect(key).not.toContain('Infinity')
        expect(key).not.toContain('undefined')
      },
    )

    it('blurPx=0 produces a deterministic, finite key; sprite generation does not crash when 2D ctx exists', () => {
      const o: any = {
        kind: 'fx-dot',
        color: '#aabbcc',
        r: 12.34,
        blurPx: 0,
      }

      const key1 = __testing.keyFor(o)
      const key2 = __testing.keyFor({ ...o })

      expect(key2).toBe(key1)
      expect(key1).not.toContain('NaN')
      expect(key1).not.toContain('Infinity')
      expect(key1).not.toContain('undefined')

      // Runtime part: only execute if Canvas 2D is available in the test environment.
      // (In Node-only envs it may be missing; keyFor invariants must still be testable.)
      const c = (globalThis as any).document?.createElement?.('canvas')
      const ctx = c?.getContext?.('2d')
      if (!ctx) return

      expect(() => __testing._getGlowSprite(o)).not.toThrow()
    })

    it.each(['bloom', 'rim', 'selection', 'active'] as const)(
      'includes kind and differentiates kinds (%s)',
      (kind) => {
        const base: any = {
          kind: 'bloom',
          shape: 'circle',
          w: 10,
          h: 10,
          r: 10,
          rr: 0,
          color: '#ffffff',
          blurPx: 2,
          lineWidthPx: 1,
        }

        const k1 = __testing.keyFor(base)
        const k2 = __testing.keyFor({ ...base, kind })
        if (kind === 'bloom') {
          expect(k2).toBe(k1)
        } else {
          expect(k2).not.toBe(k1)
        }
      },
    )

    it('differentiates FX kinds when other parameters are the same', () => {
      const common: any = {
        color: '#ffffff',
        r: 10,
        blurPx: 2,
        thicknessPx: 3,
      }

      const kDot = __testing.keyFor({ kind: 'fx-dot', ...common })
      const kBloom = __testing.keyFor({ kind: 'fx-bloom', ...common })
      const kRing = __testing.keyFor({ kind: 'fx-ring', ...common })

      expect(kDot).not.toBe(kBloom)
      expect(kDot).not.toBe(kRing)
      expect(kBloom).not.toBe(kRing)
    })

    it('differentiates fx-ring keys by thicknessPx', () => {
      const common: any = {
        kind: 'fx-ring',
        color: '#ffffff',
        r: 10,
        blurPx: 2,
      }

      const k1 = __testing.keyFor({ ...common, thicknessPx: 1 })
      const k2 = __testing.keyFor({ ...common, thicknessPx: 2 })
      expect(k2).not.toBe(k1)
    })
  })

  describe('LRU eviction (MAX_CACHE)', () => {
    function optsFor(i: number): any {
      return {
        kind: 'bloom',
        shape: 'circle',
        color: '#ffffff',
        // Make keys unique deterministically.
        w: i,
        h: 1,
        r: 1,
        rr: 0,
        blurPx: 0,
        lineWidthPx: 1,
      }
    }

    it('evicts the oldest key when size grows beyond MAX_CACHE', () => {
      const firstKey = __testing.keyFor(optsFor(0))

      for (let i = 0; i < __testing.MAX_CACHE + 1; i++) {
        __testing._getGlowSprite(optsFor(i))
      }

      expect(__testing._cacheHas(firstKey)).toBe(false)
      expect(__testing._cacheKeys().length).toBe(__testing.MAX_CACHE)
    })

    it('refreshes recency on repeated access (poor-man LRU via Map insertion order)', () => {
      const k0 = __testing.keyFor(optsFor(0))
      const k1 = __testing.keyFor(optsFor(1))

      // Fill exactly to MAX_CACHE.
      for (let i = 0; i < __testing.MAX_CACHE; i++) {
        __testing._getGlowSprite(optsFor(i))
      }
      expect(__testing._cacheHas(k0)).toBe(true)
      expect(__testing._cacheHas(k1)).toBe(true)

      // Touch k0 to make it the freshest.
      __testing._getGlowSprite(optsFor(0))

      // Add one more entry: should evict k1 (oldest), not k0.
      __testing._getGlowSprite(optsFor(__testing.MAX_CACHE))
      expect(__testing._cacheHas(k0)).toBe(true)
      expect(__testing._cacheHas(k1)).toBe(false)
    })
  })
})

