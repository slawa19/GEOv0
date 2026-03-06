import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest'

import { __testing, resetGlowSpritesCache } from './glowSprites'

type GlowSpriteOpts = Parameters<typeof __testing.keyFor>[0]
type NodeGlowSpriteOpts = Extract<GlowSpriteOpts, { kind: 'bloom' | 'rim' | 'selection' | 'active' }>
type FxGlowSpriteOpts = Extract<GlowSpriteOpts, { kind: 'fx-dot' | 'fx-ring' | 'fx-bloom' }>
type TestCanvasContext = Pick<CanvasRenderingContext2D, 'save' | 'restore' | 'clearRect' | 'beginPath' | 'arc' | 'fill' | 'stroke'>
type TestCanvas = Pick<HTMLCanvasElement, 'width' | 'height' | 'getContext'>
type TestDocument = Pick<Document, 'createElement'>

function setMockDocument(value: Document | undefined): void {
  Object.defineProperty(globalThis, 'document', {
    value,
    configurable: true,
    writable: true,
  })
}

function makeNodeGlowOpts(
  kind: NodeGlowSpriteOpts['kind'] = 'bloom',
  overrides: Partial<NodeGlowSpriteOpts> = {},
): NodeGlowSpriteOpts {
  return {
    kind,
    shape: 'circle',
    color: '#ffffff',
    w: 10,
    h: 10,
    r: 10,
    rr: 0,
    blurPx: 2,
    lineWidthPx: 1,
    ...overrides,
  }
}

function makeFxGlowOpts(kind: FxGlowSpriteOpts['kind'], overrides: Partial<FxGlowSpriteOpts> = {}): FxGlowSpriteOpts {
  if (kind === 'fx-ring') {
    return {
      kind,
      color: '#ffffff',
      r: 10,
      blurPx: 2,
      thicknessPx: 3,
      ...overrides,
    } as unknown as FxGlowSpriteOpts
  }
  return {
    kind,
    color: '#ffffff',
    r: 10,
    blurPx: 2,
    ...overrides,
  } as unknown as FxGlowSpriteOpts
}

describe('render/glowSprites — unit', () => {
  const originalDocument = globalThis.document

  beforeAll(() => {
    // Node test environment has no DOM; glowSprites uses document.createElement('canvas').
    // Provide a minimal stub sufficient for cache/LRU logic.
    const mockDocument: TestDocument = {
      createElement: (tag: string) => {
        if (tag !== 'canvas') throw new Error(`Unexpected createElement tag: ${tag}`)
        const ctx: TestCanvasContext = {
          save: () => undefined,
          restore: () => undefined,
          clearRect: () => undefined,
          beginPath: () => undefined,
          arc: () => undefined,
          fill: () => undefined,
          stroke: () => undefined,
        }
        const canvas: TestCanvas = {
          width: 0,
          height: 0,
          getContext: ((contextId: string) =>
            contextId === '2d' ? (ctx as unknown as CanvasRenderingContext2D) : null) as HTMLCanvasElement['getContext'],
        }
        return canvas as unknown as HTMLElement
      },
    }
    setMockDocument(mockDocument as Document)
  })

  afterAll(() => {
    setMockDocument(originalDocument)
  })

  beforeEach(() => {
    __testing._cacheClear()
  })

  it('resetGlowSpritesCache() empties the cache; next request creates a new sprite', () => {
    const opts = makeNodeGlowOpts('bloom')

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
      const o = makeNodeGlowOpts('bloom', { h: 11, r: 12, color: '#ff00ff', blurPx: 3 })

      expect(__testing.keyFor(o)).toBe(__testing.keyFor({ ...o }))
    })

    it.each(['fx-dot', 'fx-ring', 'fx-bloom'] as const)('is deterministic for %s', (kind) => {
      const o = makeFxGlowOpts(kind, { color: '#aabbcc', r: 12.34, blurPx: 5.67, ...(kind === 'fx-ring' ? { thicknessPx: 2.5 } : {}) })
      expect(__testing.keyFor(o)).toBe(__testing.keyFor({ ...o }))
    })

    it('never emits NaN/Infinity/undefined in the key string (sanitizes via q())', () => {
      const o = makeNodeGlowOpts('rim', {
        shape: 'rounded-rect',
        w: Number.NaN,
        h: Number.POSITIVE_INFINITY,
        r: Number.NEGATIVE_INFINITY,
        rr: Number.NaN,
        color: '#00ff00',
        blurPx: Number.NaN,
        lineWidthPx: Number.POSITIVE_INFINITY,
      })

      const key = __testing.keyFor(o)
      expect(key).not.toContain('NaN')
      expect(key).not.toContain('Infinity')
      expect(key).not.toContain('undefined')
    })

    it.each(['fx-dot', 'fx-ring', 'fx-bloom'] as const)(
      'never emits NaN/Infinity/undefined in the key string for %s',
      (kind) => {
        const o = makeFxGlowOpts(kind, {
          color: '#00ff00',
          r: Number.NaN,
          blurPx: Number.POSITIVE_INFINITY,
          ...(kind === 'fx-ring' ? { thicknessPx: Number.NEGATIVE_INFINITY } : {}),
        })

        const key = __testing.keyFor(o)
        expect(key).not.toContain('NaN')
        expect(key).not.toContain('Infinity')
        expect(key).not.toContain('undefined')
      },
    )

    it('blurPx=0 produces a deterministic, finite key; sprite generation does not crash when 2D ctx exists', () => {
      const o = makeFxGlowOpts('fx-dot', { color: '#aabbcc', r: 12.34, blurPx: 0 })

      const key1 = __testing.keyFor(o)
      const key2 = __testing.keyFor({ ...o })

      expect(key2).toBe(key1)
      expect(key1).not.toContain('NaN')
      expect(key1).not.toContain('Infinity')
      expect(key1).not.toContain('undefined')

      // Runtime part: only execute if Canvas 2D is available in the test environment.
      // (In Node-only envs it may be missing; keyFor invariants must still be testable.)
      const c = globalThis.document?.createElement?.('canvas') as HTMLCanvasElement | undefined
      const ctx = c?.getContext?.('2d')
      if (!ctx) return

      expect(() => __testing._getGlowSprite(o)).not.toThrow()
    })

    it.each(['bloom', 'rim', 'selection', 'active'] as const)(
      'includes kind and differentiates kinds (%s)',
      (kind) => {
        const base = makeNodeGlowOpts('bloom')

        const k1 = __testing.keyFor(base)
        const k2 = __testing.keyFor(makeNodeGlowOpts(kind, { ...base, kind }))
        if (kind === 'bloom') {
          expect(k2).toBe(k1)
        } else {
          expect(k2).not.toBe(k1)
        }
      },
    )

    it('differentiates FX kinds when other parameters are the same', () => {
      const kDot = __testing.keyFor(makeFxGlowOpts('fx-dot', { color: '#ffffff', r: 10, blurPx: 2 }))
      const kBloom = __testing.keyFor(makeFxGlowOpts('fx-bloom', { color: '#ffffff', r: 10, blurPx: 2 }))
      const kRing = __testing.keyFor(makeFxGlowOpts('fx-ring', { color: '#ffffff', r: 10, blurPx: 2, thicknessPx: 3 }))

      expect(kDot).not.toBe(kBloom)
      expect(kDot).not.toBe(kRing)
      expect(kBloom).not.toBe(kRing)
    })

    it('differentiates fx-ring keys by thicknessPx', () => {
      const k1 = __testing.keyFor(makeFxGlowOpts('fx-ring', { thicknessPx: 1 }))
      const k2 = __testing.keyFor(makeFxGlowOpts('fx-ring', { thicknessPx: 2 }))
      expect(k2).not.toBe(k1)
    })
  })

  describe('getGlowSprite() input hardening (ITEM-13)', () => {
    it('sanitizes NaN/Infinity inputs into a finite, valid canvas size', () => {
      const o = makeFxGlowOpts('fx-dot', { color: '#aabbcc', r: Number.NaN, blurPx: Number.POSITIVE_INFINITY })

      const s = __testing._getGlowSprite(o)
      expect(Number.isFinite(s.width)).toBe(true)
      expect(Number.isFinite(s.height)).toBe(true)
      expect(s.width).toBeGreaterThanOrEqual(1)
      expect(s.height).toBeGreaterThanOrEqual(1)
      expect(s.width).toBeLessThanOrEqual(__testing.MAX_SPRITE_PX)
      expect(s.height).toBeLessThanOrEqual(__testing.MAX_SPRITE_PX)
    })

    it('clamps pathological sprite sizes to MAX_SPRITE_PX', () => {
      const o = makeFxGlowOpts('fx-bloom', { color: '#aabbcc', r: 1e9, blurPx: 1e9 })

      const s = __testing._getGlowSprite(o)
      expect(s.width).toBe(__testing.MAX_SPRITE_PX)
      expect(s.height).toBe(__testing.MAX_SPRITE_PX)
    })
  })

  describe('LRU eviction (MAX_CACHE)', () => {
    function optsFor(i: number): GlowSpriteOpts {
      return makeNodeGlowOpts('bloom', {
        // Make keys unique deterministically.
        w: i,
        h: 1,
        r: 1,
        rr: 0,
        blurPx: 0,
      })
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

