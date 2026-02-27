import { createApp, h, nextTick } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import ErrorToast from './ErrorToast.vue'

describe('ErrorToast', () => {
  it('uses props.dismissMs when message length <= 80', async () => {
    vi.useFakeTimers()
    try {
      const host = document.createElement('div')
      document.body.appendChild(host)

      const onDismiss = vi.fn()
      const app = createApp({
        render: () =>
          h(ErrorToast as any, {
            message: 'short',
            dismissMs: 1234,
            onDismiss,
          }),
      })
      app.mount(host)
      await nextTick()

      await vi.advanceTimersByTimeAsync(1233)
      expect(onDismiss).toHaveBeenCalledTimes(0)
      await vi.advanceTimersByTimeAsync(1)
      expect(onDismiss).toHaveBeenCalledTimes(1)

      app.unmount()
      host.remove()
    } finally {
      vi.useRealTimers()
    }
  })

  it('uses 6000ms when message length > 80', async () => {
    vi.useFakeTimers()
    try {
      const host = document.createElement('div')
      document.body.appendChild(host)

      const onDismiss = vi.fn()
      const msg = 'x'.repeat(81)
      const app = createApp({
        render: () => h(ErrorToast as any, { message: msg, dismissMs: 4000, onDismiss }),
      })
      app.mount(host)
      await nextTick()

      await vi.advanceTimersByTimeAsync(5999)
      expect(onDismiss).toHaveBeenCalledTimes(0)
      await vi.advanceTimersByTimeAsync(1)
      expect(onDismiss).toHaveBeenCalledTimes(1)

      app.unmount()
      host.remove()
    } finally {
      vi.useRealTimers()
    }
  })

  it('uses 8000ms when message length > 150', async () => {
    vi.useFakeTimers()
    try {
      const host = document.createElement('div')
      document.body.appendChild(host)

      const onDismiss = vi.fn()
      const msg = 'x'.repeat(151)
      const app = createApp({
        render: () => h(ErrorToast as any, { message: msg, dismissMs: 4000, onDismiss }),
      })
      app.mount(host)
      await nextTick()

      await vi.advanceTimersByTimeAsync(7999)
      expect(onDismiss).toHaveBeenCalledTimes(0)
      await vi.advanceTimersByTimeAsync(1)
      expect(onDismiss).toHaveBeenCalledTimes(1)

      app.unmount()
      host.remove()
    } finally {
      vi.useRealTimers()
    }
  })

  it('unmount clears auto-dismiss timer', async () => {
    vi.useFakeTimers()
    try {
      const host = document.createElement('div')
      document.body.appendChild(host)

      const onDismiss = vi.fn()
      const app = createApp({
        render: () => h(ErrorToast as any, { message: 'err', dismissMs: 4000, onDismiss }),
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

      const onDismiss = vi.fn()
      const app = createApp({
        render: () => h(ErrorToast as any, { message: 'err', dismissMs: 4000, onDismiss }),
      })

      app.mount(host)
      await nextTick()

      const btn = host.querySelector('button[aria-label="Dismiss"]') as HTMLButtonElement | null
      expect(btn).toBeTruthy()

      await vi.advanceTimersByTimeAsync(1000)
      btn?.click()
      await nextTick()

      expect(onDismiss).toHaveBeenCalledTimes(1)

      await vi.advanceTimersByTimeAsync(10_000)
      expect(onDismiss).toHaveBeenCalledTimes(1)

      app.unmount()
      host.remove()
    } finally {
      vi.useRealTimers()
    }
  })
})

