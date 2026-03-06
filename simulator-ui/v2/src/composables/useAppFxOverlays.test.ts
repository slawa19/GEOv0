import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, ref } from 'vue'

import { useAppFxOverlays } from './useAppFxOverlays'
import type { LayoutNode } from '../types/layout'

type TestWindowTimers = Pick<Window, 'setTimeout' | 'clearTimeout'>

function setMockWindow(windowValue: (Window & typeof globalThis) | undefined): void {
  Object.defineProperty(globalThis, 'window', {
    value: windowValue,
    configurable: true,
    writable: true,
  })
}

function makeLayoutNode(): LayoutNode {
  return { id: 'n', __x: 0, __y: 0 }
}

function getMockWindowTimers(): TestWindowTimers {
  return {
    setTimeout: globalThis.setTimeout.bind(globalThis),
    clearTimeout: globalThis.clearTimeout.bind(globalThis),
  }
}

describe('useAppFxOverlays.scheduleTimeout()', () => {
  const prevWindow = globalThis.window

  afterEach(() => {
    vi.useRealTimers()
    setMockWindow(prevWindow)
  })

  it('calls wakeUp() before firing timer callback', () => {
    vi.useFakeTimers()

    // timerRegistry uses window.setTimeout/window.clearTimeout.
    setMockWindow(getMockWindowTimers() as Window & typeof globalThis)

    const order: string[] = []
    const wakeUp = vi.fn(() => order.push('wakeUp'))

    const fx = useAppFxOverlays({
      getLayoutNodeById: () => makeLayoutNode(),
      sizeForNode: () => ({ w: 10, h: 10 }),
      getCameraZoom: () => 1,
      setFlash: () => undefined,
      isWebDriver: () => false,
      getLayoutNodes: () => [],
      worldToScreen: (x, y) => ({ x, y }),
      layoutVersion: ref(0),
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

    setMockWindow(getMockWindowTimers() as Window & typeof globalThis)

    const calls: string[] = []

    const fx = useAppFxOverlays({
      getLayoutNodeById: () => makeLayoutNode(),
      sizeForNode: () => ({ w: 10, h: 10 }),
      getCameraZoom: () => 1,
      setFlash: () => undefined,
      isWebDriver: () => false,
      getLayoutNodes: () => [],
      worldToScreen: (x, y) => ({ x, y }),
      layoutVersion: ref(0),
      // no wakeUp
    })

    fx.scheduleTimeout(() => calls.push('fn'), 50)
    vi.advanceTimersByTime(50)

    expect(calls).toEqual(['fn'])
  })

  it('cleans up scheduled timers on component unmount', () => {
    vi.useFakeTimers()

    const win = (globalThis.window ?? getMockWindowTimers()) as Window & TestWindowTimers
    const prevSetTimeout = win.setTimeout
    const prevClearTimeout = win.clearTimeout

    // timerRegistry uses window.setTimeout/window.clearTimeout.
    // Ensure those are the fake-timers functions so vi.advanceTimersByTime() drives them.
    win.setTimeout = globalThis.setTimeout.bind(globalThis)
    win.clearTimeout = globalThis.clearTimeout.bind(globalThis)

    const clearTimeoutSpy = vi.spyOn(win, 'clearTimeout')

    try {
      const calls: string[] = []

      const Comp = defineComponent({
        setup() {
          const fx = useAppFxOverlays({
            getLayoutNodeById: () => makeLayoutNode(),
            sizeForNode: () => ({ w: 10, h: 10 }),
            getCameraZoom: () => 1,
            setFlash: () => undefined,
            isWebDriver: () => false,
            getLayoutNodes: () => [],
            worldToScreen: (x, y) => ({ x, y }),
            layoutVersion: ref(0),
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
      win.setTimeout = prevSetTimeout
      win.clearTimeout = prevClearTimeout
    }
  })
})

