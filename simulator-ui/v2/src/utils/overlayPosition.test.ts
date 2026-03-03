import { describe, expect, it } from 'vitest'

import { placeOverlayNearAnchor } from './overlayPosition'

describe('utils/overlayPosition — placeOverlayNearAnchor()', () => {
  it('returns px left/top values', () => {
    expect(
      placeOverlayNearAnchor({
        anchor: { x: 10, y: 20 },
        overlaySize: { w: 100, h: 50 },
        viewport: { w: 400, h: 300 },
        pad: 10,
        offset: { x: 12, y: 12 },
      }),
    ).toEqual({ left: '22px', top: '32px' })
  })

  it('clamps to viewport bounds (right/bottom)', () => {
    // vw - overlay.w - pad = 400 - 100 - 10 = 290
    // vh - overlay.h - pad = 300 - 50 - 10 = 240
    expect(
      placeOverlayNearAnchor({
        anchor: { x: 1000, y: 1000 },
        overlaySize: { w: 100, h: 50 },
        viewport: { w: 400, h: 300 },
        pad: 10,
        offset: { x: 12, y: 12 },
      }),
    ).toEqual({ left: '290px', top: '240px' })
  })

  it('clamps to viewport bounds (left/top)', () => {
    expect(
      placeOverlayNearAnchor({
        anchor: { x: -1000, y: -1000 },
        overlaySize: { w: 100, h: 50 },
        viewport: { w: 400, h: 300 },
        pad: 10,
        offset: { x: 12, y: 12 },
      }),
    ).toEqual({ left: '10px', top: '10px' })
  })
})

