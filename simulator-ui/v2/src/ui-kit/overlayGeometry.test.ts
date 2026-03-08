import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  DEFAULT_HUD_BOTTOM_STACK_HEIGHT_PX,
  DEFAULT_HUD_STACK_HEIGHT_PX,
  DEFAULT_WM_ANCHOR_OFFSET_X_PX,
  DEFAULT_WM_CLAMP_PAD_PX,
  DEFAULT_WM_GROUP_Z_INSPECTOR_BASE,
  DEFAULT_WM_INTERACT_MIN_HEIGHT_PX,
  DEFAULT_WM_INTERACT_MIN_WIDTH_PX,
  DEFAULT_WM_NODE_CARD_PREFERRED_HEIGHT_PX,
  createMeasuredPublishedGeometryValue,
  readOverlayGeometryPx,
} from './overlayGeometry'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('overlayGeometry', () => {
  it('falls back safely when CSS geometry tokens are missing or invalid', () => {
    const host = document.createElement('div')
    const computedStyle = document.createElement('div').style

    vi.spyOn(computedStyle, 'getPropertyValue').mockImplementation((name: string) => {
        switch (name) {
          case '--ds-wm-clamp-pad':
            return 'invalid'
          case '--ds-hud-stack-height':
            return '-8px'
          case '--ds-hud-bottom-stack-height':
            return ''
          case '--ds-wm-anchor-offset-x':
            return '22px'
          case '--ds-wm-interact-minw':
            return '0px'
          case '--ds-wm-interact-minh':
            return '-1'
          case '--ds-wm-node-card-prefh':
            return 'not-a-number'
          case '--ds-wm-group-z-inspector-base':
            return '12'
          default:
            return ''
        }
      })
    vi.spyOn(window, 'getComputedStyle').mockReturnValue(computedStyle)

    const geo = readOverlayGeometryPx(host)

    expect(geo.wmClampPadPx).toBe(DEFAULT_WM_CLAMP_PAD_PX)
    expect(geo.hudStackHeightPx).toBe(DEFAULT_HUD_STACK_HEIGHT_PX)
    expect(geo.hudBottomStackHeightPx).toBe(DEFAULT_HUD_BOTTOM_STACK_HEIGHT_PX)
    expect(geo.wmAnchorOffsetXPx).toBe(22)
    expect(geo.wmInteractMinWidthPx).toBe(DEFAULT_WM_INTERACT_MIN_WIDTH_PX)
    expect(geo.wmInteractMinHeightPx).toBe(DEFAULT_WM_INTERACT_MIN_HEIGHT_PX)
    expect(geo.wmNodeCardPreferredHeightPx).toBe(DEFAULT_WM_NODE_CARD_PREFERRED_HEIGHT_PX)
    expect(geo.wmGroupZInspectorBase).toBe(12)
  })

  it('normalizes invalid measured publishes to the fallback without crashing', () => {
    const applied: number[] = []
    const publisher = createMeasuredPublishedGeometryValue(DEFAULT_HUD_STACK_HEIGHT_PX, (nextPx) => {
      applied.push(nextPx)
    })

    const epoch = publisher.nextEpoch()
    expect(publisher.publish(DEFAULT_HUD_STACK_HEIGHT_PX + 24, epoch)).toBe(true)
    expect(publisher.publish(Number.NaN, epoch)).toBe(true)
    expect(publisher.snapshot()).toBe(DEFAULT_HUD_STACK_HEIGHT_PX)
    expect(applied).toEqual([DEFAULT_HUD_STACK_HEIGHT_PX + 24, DEFAULT_HUD_STACK_HEIGHT_PX])
  })

  it('keeps documented defaults available when no explicit host is provided', () => {
    const computedStyle = document.createElement('div').style
    vi.spyOn(computedStyle, 'getPropertyValue').mockReturnValue('')
    vi.spyOn(window, 'getComputedStyle').mockReturnValue(computedStyle)

    const geo = readOverlayGeometryPx()

    expect(geo.wmClampPadPx).toBe(DEFAULT_WM_CLAMP_PAD_PX)
    expect(geo.hudStackHeightPx).toBe(DEFAULT_HUD_STACK_HEIGHT_PX)
    expect(geo.hudBottomStackHeightPx).toBe(DEFAULT_HUD_BOTTOM_STACK_HEIGHT_PX)
    expect(geo.wmAnchorOffsetXPx).toBe(DEFAULT_WM_ANCHOR_OFFSET_X_PX)
    expect(geo.wmGroupZInspectorBase).toBe(DEFAULT_WM_GROUP_Z_INSPECTOR_BASE)
  })
})