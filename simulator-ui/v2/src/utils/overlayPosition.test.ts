import { describe, expect, it } from 'vitest'

import { normalizeAnchorToHostViewport } from './overlayPosition'

describe('utils/overlayPosition — normalizeAnchorToHostViewport()', () => {
  it('returns the anchor unchanged when hostRect is missing', () => {
    expect(normalizeAnchorToHostViewport({ x: 10, y: 20 }, null)).toEqual({ x: 10, y: 20 })
  })

  it('returns the anchor unchanged when it is already within host-relative bounds', () => {
    const rect = { left: 100, top: 200, right: 500, bottom: 600, width: 400, height: 400 } as DOMRect
    expect(normalizeAnchorToHostViewport({ x: 10, y: 20 }, rect)).toEqual({ x: 10, y: 20 })
  })

  it('converts viewport-based anchor (clientX/clientY) to host-relative when detectable', () => {
    const rect = { left: 100, top: 200, right: 500, bottom: 600, width: 400, height: 400 } as DOMRect
    // This is within the host in viewport coords, but NOT within [0..width]x[0..height] for X.
    expect(normalizeAnchorToHostViewport({ x: 450, y: 250 }, rect)).toEqual({ x: 350, y: 50 })
  })

  it('does not convert when hostRect has non-positive dimensions', () => {
    const rect = { left: 100, top: 200, right: 100, bottom: 200, width: 0, height: 0 } as DOMRect
    expect(normalizeAnchorToHostViewport({ x: 150, y: 250 }, rect)).toEqual({ x: 150, y: 250 })
  })
})

