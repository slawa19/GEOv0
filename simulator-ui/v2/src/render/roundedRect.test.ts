import { describe, expect, it, vi } from 'vitest'

import { roundedRectPath } from './roundedRect'

type RoundedRectCtx = Pick<CanvasRenderingContext2D,
  'beginPath' | 'rect' | 'moveTo' | 'lineTo' | 'quadraticCurveTo' | 'closePath'
> & {
  roundRect: (x: number, y: number, w: number, h: number, r: number) => void
}

describe('roundedRectPath()', () => {
  it('calls native ctx.roundRect with correct `this` binding (no Illegal invocation)', () => {
    const calls: Array<[number, number, number, number, number]> = []

    const ctx: RoundedRectCtx = {
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

    expect(() => roundedRectPath(ctx as unknown as CanvasRenderingContext2D, 10, 20, 30, 40, 6)).not.toThrow()
    expect(calls.length).toBe(1)
  })
})
