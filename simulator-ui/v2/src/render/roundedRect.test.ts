import { describe, expect, it, vi } from 'vitest'

import { roundedRectPath } from './roundedRect'

describe('roundedRectPath()', () => {
  it('calls native ctx.roundRect with correct `this` binding (no Illegal invocation)', () => {
    const calls: any[] = []

    const ctx: any = {
      beginPath: vi.fn(),
      rect: vi.fn(),
      moveTo: vi.fn(),
      lineTo: vi.fn(),
      quadraticCurveTo: vi.fn(),
      closePath: vi.fn(),
      roundRect: function (x: number, y: number, w: number, h: number, r: number) {
        // Simulate browser Canvas behavior: wrong binding throws.
        if (this !== ctx) throw new TypeError('Illegal invocation')
        calls.push([x, y, w, h, r])
      },
    }

    expect(() => roundedRectPath(ctx, 10, 20, 30, 40, 6)).not.toThrow()
    expect(calls.length).toBe(1)
  })
})
