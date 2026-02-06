import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest'

import { __testing } from './glowSprites'

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

