import { describe, it, expect, vi, afterEach } from 'vitest'

import { __testOnly_nanFallbackCoord } from './physicsD3'

describe('physicsD3: NaN fallback micro-jitter', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('does not place two NaN nodes into identical fallback point', () => {
    const centerX = 100
    const centerY = 50

    vi.spyOn(Math, 'random')
      .mockImplementationOnce(() => 0) // node #1: x => -1px
      .mockImplementationOnce(() => 0) // node #1: y => -1px
      .mockImplementationOnce(() => 1) // node #2: x => +1px
      .mockImplementationOnce(() => 1) // node #2: y => +1px

    const p1 = {
      x: __testOnly_nanFallbackCoord(Number.NaN, centerX),
      y: __testOnly_nanFallbackCoord(Number.NaN, centerY),
    }
    const p2 = {
      x: __testOnly_nanFallbackCoord(Number.NaN, centerX),
      y: __testOnly_nanFallbackCoord(Number.NaN, centerY),
    }

    expect(p1).not.toEqual(p2)
    expect(p1).toEqual({ x: centerX - 1, y: centerY - 1 })
    expect(p2).toEqual({ x: centerX + 1, y: centerY + 1 })
  })

  it('does not change behavior for valid finite coordinates', () => {
    const rand = vi.spyOn(Math, 'random')
    expect(__testOnly_nanFallbackCoord(42, 100)).toBe(42)
    expect(rand).not.toHaveBeenCalled()
  })
})

