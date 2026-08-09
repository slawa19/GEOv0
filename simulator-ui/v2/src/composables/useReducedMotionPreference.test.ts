import { createApp, h, nextTick } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import { useReducedMotionPreference } from './useReducedMotionPreference'

describe('useReducedMotionPreference', () => {
  it('tracks the media preference and removes its listener on unmount', async () => {
    const changeListeners: Array<(event: MediaQueryListEvent) => void> = []
    const addEventListener = vi.fn((_type: string, listener: (event: MediaQueryListEvent) => void) => {
      changeListeners.push(listener)
    })
    const removeEventListener = vi.fn()
    const mediaQuery = {
      matches: true,
      media: '(prefers-reduced-motion: reduce)',
      onchange: null,
      addEventListener,
      removeEventListener,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(() => true),
    } as unknown as MediaQueryList
    vi.stubGlobal('matchMedia', vi.fn(() => mediaQuery))

    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = createApp({
      setup() {
        const reduced = useReducedMotionPreference()
        return () => h('div', { 'data-motion': reduced.value ? 'reduced' : 'full' })
      },
    })

    app.mount(host)
    await nextTick()
    expect(host.firstElementChild?.getAttribute('data-motion')).toBe('reduced')

    changeListeners[0]?.({ matches: false } as MediaQueryListEvent)
    await nextTick()
    expect(host.firstElementChild?.getAttribute('data-motion')).toBe('full')

    app.unmount()
    expect(removeEventListener).toHaveBeenCalledWith('change', changeListeners[0])
    host.remove()
    vi.unstubAllGlobals()
  })
})
