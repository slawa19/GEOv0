import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent } from 'vue'

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

  it('cleans up scheduled timers on component unmount', () => {
    vi.useFakeTimers()

    const win = (globalThis as any).window as Window
    const prevSetTimeout = (win as any).setTimeout
    const prevClearTimeout = (win as any).clearTimeout

    // timerRegistry uses window.setTimeout/window.clearTimeout.
    // Ensure those are the fake-timers functions so vi.advanceTimersByTime() drives them.
    ;(win as any).setTimeout = globalThis.setTimeout.bind(globalThis)
    ;(win as any).clearTimeout = globalThis.clearTimeout.bind(globalThis)

    const clearTimeoutSpy = vi.spyOn(win as any, 'clearTimeout')

    try {
      const calls: string[] = []

      const Comp = defineComponent({
        setup() {
          const fx = useAppFxOverlays({
            getLayoutNodeById: () => ({ __x: 0, __y: 0 } as any),
            sizeForNode: () => ({ w: 10, h: 10 }),
            getCameraZoom: () => 1,
            setFlash: () => undefined,
            isWebDriver: () => false,
            getLayoutNodes: () => [],
            worldToScreen: (x, y) => ({ x, y }),
          })

          fx.scheduleTimeout(() => calls.push('fn'), 100)

          return () => null
        },
      })

      const el = document.createElement('div')
      document.body.appendChild(el)

      const app = createApp(Comp)
      app.mount(el)
      app.unmount()

      // After unmount, the composable's onUnmounted cleanup must cancel pending timers.
      vi.advanceTimersByTime(200)

      expect(calls).toEqual([])
      expect(clearTimeoutSpy).toHaveBeenCalledTimes(1)
    } finally {
      clearTimeoutSpy.mockRestore()
      ;(win as any).setTimeout = prevSetTimeout
      ;(win as any).clearTimeout = prevClearTimeout
    }
  })
})

