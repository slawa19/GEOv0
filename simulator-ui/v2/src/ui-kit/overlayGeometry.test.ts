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
  vi.unstubAllGlobals()
  Reflect.deleteProperty(globalThis, '__GEO_TEST_ENABLE_OVERLAY_DIAGNOSTICS__')
})

describe('overlayGeometry', () => {
  it('falls back safely when CSS geometry tokens are missing or invalid and emits dev diagnostics', () => {
    Reflect.set(globalThis, '__GEO_TEST_ENABLE_OVERLAY_DIAGNOSTICS__', true)
    const host = {} as Element
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const getComputedStyle = vi.fn(() => ({
      getPropertyValue(name: string) {
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
          case '--ds-wm-group-z-interact-base':
            return '4'
          default:
            return ''
        }
      },
    }))
    vi.stubGlobal('window', { getComputedStyle })

    const geo = readOverlayGeometryPx(host)

    expect(geo.wmClampPadPx).toBe(DEFAULT_WM_CLAMP_PAD_PX)
    expect(geo.hudStackHeightPx).toBe(DEFAULT_HUD_STACK_HEIGHT_PX)
    expect(geo.hudBottomStackHeightPx).toBe(DEFAULT_HUD_BOTTOM_STACK_HEIGHT_PX)
    expect(geo.wmAnchorOffsetXPx).toBe(22)
    expect(geo.wmInteractMinWidthPx).toBe(DEFAULT_WM_INTERACT_MIN_WIDTH_PX)
    expect(geo.wmInteractMinHeightPx).toBe(DEFAULT_WM_INTERACT_MIN_HEIGHT_PX)
    expect(geo.wmNodeCardPreferredHeightPx).toBe(DEFAULT_WM_NODE_CARD_PREFERRED_HEIGHT_PX)
    expect(geo.wmGroupZInspectorBase).toBe(DEFAULT_WM_GROUP_Z_INSPECTOR_BASE)
    expect(warnSpy).toHaveBeenCalledWith(
      '[overlay] css-var-fallback: Using fallback overlay geometry token value.',
      expect.objectContaining({ token: '--ds-wm-clamp-pad', fallbackPx: DEFAULT_WM_CLAMP_PAD_PX }),
    )
    expect(warnSpy).toHaveBeenCalledWith(
      '[overlay] z-layer-mismatch: Invalid WM z-base ordering; reverting to documented defaults.',
      expect.objectContaining({ wmGroupZInspectorBase: 12, wmGroupZInteractBase: 4 }),
    )
  })

  it('normalizes invalid measured publishes to the fallback, ignores stale publishes, and emits dev diagnostics', () => {
    Reflect.set(globalThis, '__GEO_TEST_ENABLE_OVERLAY_DIAGNOSTICS__', true)
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const applied: number[] = []
    const publisher = createMeasuredPublishedGeometryValue(DEFAULT_HUD_STACK_HEIGHT_PX, (nextPx) => {
      applied.push(nextPx)
    })

    const epoch = publisher.nextEpoch()
    expect(publisher.publish(DEFAULT_HUD_STACK_HEIGHT_PX + 24, epoch)).toBe(true)
    expect(publisher.publish(DEFAULT_HUD_STACK_HEIGHT_PX + 12, epoch + 1)).toBe(false)
    expect(publisher.publish(Number.NaN, epoch)).toBe(true)
    expect(publisher.snapshot()).toBe(DEFAULT_HUD_STACK_HEIGHT_PX)
    expect(applied).toEqual([DEFAULT_HUD_STACK_HEIGHT_PX + 24, DEFAULT_HUD_STACK_HEIGHT_PX])
    expect(warnSpy).toHaveBeenCalledWith(
      '[overlay] stale-publish: Ignoring stale overlay geometry publish.',
      expect.objectContaining({ publishEpoch: epoch + 1, currentEpoch: epoch }),
    )
    expect(warnSpy).toHaveBeenCalledWith(
      '[overlay] invalid-measured-size: Invalid measured overlay geometry; falling back to documented default.',
      expect.objectContaining({ measuredPx: Number.NaN, fallbackPx: DEFAULT_HUD_STACK_HEIGHT_PX }),
    )
  })

  it('keeps documented defaults available when no explicit host is provided', () => {
    const geo = readOverlayGeometryPx()

    expect(geo.wmClampPadPx).toBe(DEFAULT_WM_CLAMP_PAD_PX)
    expect(geo.hudStackHeightPx).toBe(DEFAULT_HUD_STACK_HEIGHT_PX)
    expect(geo.hudBottomStackHeightPx).toBe(DEFAULT_HUD_BOTTOM_STACK_HEIGHT_PX)
    expect(geo.wmAnchorOffsetXPx).toBe(DEFAULT_WM_ANCHOR_OFFSET_X_PX)
    expect(geo.wmGroupZInspectorBase).toBe(DEFAULT_WM_GROUP_Z_INSPECTOR_BASE)
  })
})