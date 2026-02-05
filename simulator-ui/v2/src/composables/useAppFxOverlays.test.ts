import { afterEach, describe, expect, it, vi } from 'vitest'

import { useAppFxOverlays } from './useAppFxOverlays'

describe('useAppFxOverlays.scheduleTimeout()', () => {
  const prevWindow = (globalThis as any).window

  afterEach(() => {
    vi.useRealTimers()
    ;(globalThis as any).window = prevWindow
  })

  it('calls wakeUp() before firing timer callback', () => {
    vi.useFakeTimers()

    // timerRegistry uses window.setTimeout/window.clearTimeout.
    ;(globalThis as any).window = {
      setTimeout: globalThis.setTimeout.bind(globalThis),
      clearTimeout: globalThis.clearTimeout.bind(globalThis),
    }

    const order: string[] = []
    const wakeUp = vi.fn(() => order.push('wakeUp'))

    const fx = useAppFxOverlays({
      getLayoutNodeById: () => ({ __x: 0, __y: 0 } as any),
      sizeForNode: () => ({ w: 10, h: 10 }),
      getCameraZoom: () => 1,
      setFlash: () => undefined,
      isWebDriver: () => false,
      getLayoutNodes: () => [],
      worldToScreen: (x, y) => ({ x, y }),
      wakeUp,
    })

    fx.scheduleTimeout(() => order.push('fn'), 123)

    expect(wakeUp).toHaveBeenCalledTimes(0)
    expect(order).toEqual([])

    vi.advanceTimersByTime(123)

    expect(wakeUp).toHaveBeenCalledTimes(1)
    expect(order).toEqual(['wakeUp', 'fn'])
  })

  it('works as before when wakeUp is not provided', () => {
    vi.useFakeTimers()

    ;(globalThis as any).window = {
      setTimeout: globalThis.setTimeout.bind(globalThis),
      clearTimeout: globalThis.clearTimeout.bind(globalThis),
    }

    const calls: string[] = []

    const fx = useAppFxOverlays({
      getLayoutNodeById: () => ({ __x: 0, __y: 0 } as any),
      sizeForNode: () => ({ w: 10, h: 10 }),
      getCameraZoom: () => 1,
      setFlash: () => undefined,
      isWebDriver: () => false,
      getLayoutNodes: () => [],
      worldToScreen: (x, y) => ({ x, y }),
      // no wakeUp
    })

    fx.scheduleTimeout(() => calls.push('fn'), 50)
    vi.advanceTimersByTime(50)

    expect(calls).toEqual(['fn'])
  })
})

