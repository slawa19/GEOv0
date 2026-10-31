import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest'

import { __testing } from './glowSprites'

/**
 * MM1-S1, second line (form only). The proof that the sprites are lit is the Chromium rasterisation in
 * `e2e/mm1-s1-glow-sprites.spec.ts`: a canvas shadow is built by the browser, and this mock cannot see it. What the mock
 * CAN see is the shape of the call that broke it: every fill made while a shadow blur is on must carry a non-zero alpha,
 * because a shadow is cast from the alpha of the shape (a fill with alpha 0 casts nothing).
 * `fx-dot` is not judged: its halo stays empty on purpose (see `fillShadowOnly` in `glowSprites.ts`).
 */

type Fill = { fillStyle: string; shadowBlur: number; shadowOffsetX: number }

const fills: Fill[] = []

function makeCtx() {
  const state = { fillStyle: '#000', shadowBlur: 0, shadowOffsetX: 0, shadowOffsetY: 0, shadowColor: '', strokeStyle: '#000', lineWidth: 1, globalCompositeOperation: 'source-over' }
  const stack: (typeof state)[] = []
  const noop = () => undefined
  return Object.assign(state, {
    save: () => { stack.push({ ...state }) },
    restore: () => { const s = stack.pop(); if (s) Object.assign(state, s) },
    clearRect: noop,
    beginPath: noop,
    arc: noop,
    moveTo: noop,
    lineTo: noop,
    arcTo: noop,
    quadraticCurveTo: noop,
    closePath: noop,
    roundRect: noop,
    rect: noop,
    stroke: noop,
    fill: () => { fills.push({ fillStyle: String(state.fillStyle), shadowBlur: state.shadowBlur, shadowOffsetX: state.shadowOffsetX }) },
  })
}

function alphaOf(style: string): number {
  const m = /^rgba\(.*,\s*([\d.]+)\s*\)$/.exec(style)
  return m ? Number(m[1]) : 1
}

describe('MM1-S1 glowSprites: a shadow-casting fill is not transparent', () => {
  const originalDocument = globalThis.document

  beforeAll(() => {
    const doc = {
      createElement: (tag: string) => {
        if (tag !== 'canvas') throw new Error(`Unexpected createElement tag: ${tag}`)
        const ctx = makeCtx()
        return { width: 0, height: 0, getContext: (id: string) => (id === '2d' ? ctx : null) }
      },
    }
    Object.defineProperty(globalThis, 'document', { value: doc, configurable: true, writable: true })
  })

  afterAll(() => {
    Object.defineProperty(globalThis, 'document', { value: originalDocument, configurable: true, writable: true })
  })

  beforeEach(() => {
    __testing._cacheClear()
    fills.length = 0
  })

  const node = { w: 20, h: 20, r: 10, rr: 4, color: '#22d3ee', blurPx: 16, lineWidthPx: 0 } as const

  it.each([
    ['bloom circle', { ...node, kind: 'bloom', shape: 'circle' }],
    ['bloom rounded-rect', { ...node, kind: 'bloom', shape: 'rounded-rect' }],
  ] as const)('%s: every fill under shadowBlur > 0 has alpha > 0', (_name, opts) => {
    __testing._getGlowSprite(opts)
    const blurred = fills.filter((f) => f.shadowBlur > 0)
    // Anti-vacuum: the sprite does cast a blurred shadow, so the filter below judges a real fill.
    expect(blurred.length).toBeGreaterThan(0)
    for (const f of blurred) expect(alphaOf(f.fillStyle), JSON.stringify(f)).toBeGreaterThan(0)
  })
})
