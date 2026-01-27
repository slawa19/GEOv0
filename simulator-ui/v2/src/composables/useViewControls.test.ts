import { describe, expect, it, vi } from 'vitest'
import { useViewControls } from './useViewControls'

describe('useViewControls', () => {
  it('formats translate3d with screen coords (no scale)', () => {
    const h = useViewControls({
      worldToScreen: (x, y) => ({ x: x + 10, y: y + 20 }),
      resetCamera: vi.fn(),
      clampCameraPan: vi.fn(),
    })

    expect(h.worldToCssTranslateNoScale(1, 2)).toBe('translate3d(11px, 22px, 0)')
  })

  it('resetView calls resetCamera then clampCameraPan', () => {
    const calls: string[] = []

    const resetCamera = vi.fn(() => calls.push('reset'))
    const clampCameraPan = vi.fn(() => calls.push('clamp'))

    const h = useViewControls({
      worldToScreen: () => ({ x: 0, y: 0 }),
      resetCamera,
      clampCameraPan,
    })

    h.resetView()

    expect(resetCamera).toHaveBeenCalledTimes(1)
    expect(clampCameraPan).toHaveBeenCalledTimes(1)
    expect(calls).toEqual(['reset', 'clamp'])
  })
})
