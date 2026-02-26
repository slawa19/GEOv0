import { createApp, h, nextTick, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import SuccessToast from './SuccessToast.vue'

describe('SuccessToast', () => {
  it('A11Y: uses role="status" when visible', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const msg = ref<string | null>('ok')
    const app = createApp({
      render: () => h(SuccessToast as any, { message: msg, onDismiss: vi.fn() }),
    })
    app.mount(host)
    await nextTick()

    const el = host.querySelector('.success-toast') as HTMLElement | null
    expect(el).toBeTruthy()
    expect(el?.getAttribute('role')).toBe('status')

    app.unmount()
    host.remove()
  })

  it('auto-dismisses in 2500ms for short messages', async () => {
    vi.useFakeTimers()
    try {
      const host = document.createElement('div')
      document.body.appendChild(host)

      const msg = ref<string | null>('ok')
      const onDismiss = vi.fn(() => {
        msg.value = null
      })

      const app = createApp({
        render: () => h(SuccessToast as any, { message: msg, onDismiss }),
      })
      app.mount(host)
      await nextTick()

      expect(host.textContent).toContain('ok')

      await vi.advanceTimersByTimeAsync(2499)
      expect(onDismiss).toHaveBeenCalledTimes(0)
      expect(host.textContent).toContain('ok')

      await vi.advanceTimersByTimeAsync(1)
      expect(onDismiss).toHaveBeenCalledTimes(1)

      // Contract: dismiss event is emitted, the owner may clear the message.
      expect(msg.value).toBeNull()

      app.unmount()
      host.remove()
    } finally {
      vi.useRealTimers()
    }
  })

  it('auto-dismisses in 3500ms for long messages (>50 chars)', async () => {
    vi.useFakeTimers()
    try {
      const host = document.createElement('div')
      document.body.appendChild(host)

      const longMsg = 'x'.repeat(51)
      const msg = ref<string | null>(longMsg)
      const onDismiss = vi.fn(() => {
        msg.value = null
      })

      const app = createApp({
        render: () => h(SuccessToast as any, { message: msg, onDismiss }),
      })
      app.mount(host)
      await nextTick()

      expect(host.textContent).toContain(longMsg)

      await vi.advanceTimersByTimeAsync(3499)
      expect(onDismiss).toHaveBeenCalledTimes(0)

      await vi.advanceTimersByTimeAsync(1)
      expect(onDismiss).toHaveBeenCalledTimes(1)

      app.unmount()
      host.remove()
    } finally {
      vi.useRealTimers()
    }
  })
})

