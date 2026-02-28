import { createApp, h, nextTick, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import SuccessToast from './SuccessToast.vue'

describe('SuccessToast', () => {
  it('AC-A11Y-3: SuccessToast uses role="status" + aria-live="polite" when visible', async () => {
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
    expect(el?.getAttribute('aria-live')).toBe('polite')

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

  it('manual dismiss emits dismiss (owner clears message)', async () => {
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

    const btn = host.querySelector('button[aria-label="Dismiss"]') as HTMLButtonElement | null
    expect(btn).toBeTruthy()
    btn?.click()
    await nextTick()

    expect(onDismiss).toHaveBeenCalledTimes(1)
    expect(msg.value).toBeNull()
    // Vue Transition keeps the leaving element in DOM briefly.
    // We assert it started leaving (instead of asserting immediate DOM removal).
    const toast = host.querySelector('.success-toast') as HTMLElement | null
    expect(toast).toBeTruthy()
    expect(toast?.className ?? '').toContain('success-toast-leave')

    app.unmount()
    host.remove()
  })

  it('unmount clears auto-dismiss timer', async () => {
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

      app.unmount()

      await vi.advanceTimersByTimeAsync(10_000)
      expect(onDismiss).toHaveBeenCalledTimes(0)

      host.remove()
    } finally {
      vi.useRealTimers()
    }
  })

  it('manual dismiss clears timer (no later auto-dismiss)', async () => {
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

      const btn = host.querySelector('button[aria-label="Dismiss"]') as HTMLButtonElement | null
      expect(btn).toBeTruthy()

      // Dismiss early.
      await vi.advanceTimersByTimeAsync(1000)
      btn?.click()
      await nextTick()

      expect(onDismiss).toHaveBeenCalledTimes(1)

      // Even if we advance past the original deadline, no extra dismiss happens.
      await vi.advanceTimersByTimeAsync(10_000)
      expect(onDismiss).toHaveBeenCalledTimes(1)

      app.unmount()
      host.remove()
    } finally {
      vi.useRealTimers()
    }
  })
})
