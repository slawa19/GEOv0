import { describe, expect, it } from 'vitest'

import { overlaps } from './geometry'
import type { WindowRect } from './types'

describe('geometry.overlaps()', () => {
  it('returns true when rects overlap by area', () => {
    const a: WindowRect = { left: 0, top: 0, width: 10, height: 10 }
    const b: WindowRect = { left: 9, top: 9, width: 10, height: 10 }
    expect(overlaps(a, b)).toBe(true)
  })

  it('returns false when rects only touch edges (no area overlap)', () => {
    const a: WindowRect = { left: 0, top: 0, width: 10, height: 10 }
    const b: WindowRect = { left: 10, top: 0, width: 10, height: 10 }
    expect(overlaps(a, b)).toBe(false)
  })

  it('returns false when rects are disjoint', () => {
    const a: WindowRect = { left: 0, top: 0, width: 10, height: 10 }
    const b: WindowRect = { left: 30, top: 30, width: 10, height: 10 }
    expect(overlaps(a, b)).toBe(false)
  })
})

