import { createApp, h, nextTick, reactive } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import ClearingPanel from './ClearingPanel.vue'

describe('ClearingPanel', () => {
  it('CL-1: in confirm step shows running feedback + disables buttons when busy (not stuck)', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'confirm-clearing',
      fromPid: null as string | null,
      toPid: null as string | null,
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: null as any,
    })

    const confirmClearing = vi.fn()

    const app = createApp({
      render: () =>
        h(ClearingPanel as any, {
          phase: 'confirm-clearing',
          state,
          busy: true,
          equivalent: 'EQ',
          confirmClearing,
          cancel: vi.fn(),
          anchor: null,
          hostEl: null,
        }),
    })

    app.mount(host)
    await nextTick()

    expect(host.textContent ?? '').toContain('ESC to close')

    const help = host.querySelector('[data-testid="clearing-confirm-help"]') as HTMLElement | null
    expect(help).toBeTruthy()
    expect(help?.textContent ?? '').toContain('Running clearing…')
    expect(help?.querySelector('.cp-spinner')).toBeTruthy()

    const btnConfirm = host.querySelector('button.ds-btn--primary') as HTMLButtonElement | null
    expect(btnConfirm).toBeTruthy()
    expect((btnConfirm!.textContent ?? '').trim()).toBe('Running…')
    expect(btnConfirm!.disabled).toBe(true)

    // guard against accidental submit while busy
    btnConfirm!.click()
    expect(confirmClearing).toHaveBeenCalledTimes(0)

    const btnCancel = Array.from(host.querySelectorAll('button')).find((b) => (b.textContent ?? '').trim() === 'Cancel') as
      | HTMLButtonElement
      | undefined
    expect(btnCancel).toBeTruthy()
    expect(btnCancel!.disabled).toBe(true)

    app.unmount()
    host.remove()
  })

  it('CL-1: in confirm step keeps normal help text when not busy', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'confirm-clearing',
      fromPid: null as string | null,
      toPid: null as string | null,
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: null as any,
    })

    const app = createApp({
      render: () =>
        h(ClearingPanel as any, {
          phase: 'confirm-clearing',
          state,
          busy: false,
          equivalent: 'EQ',
          confirmClearing: vi.fn(),
          cancel: vi.fn(),
          anchor: null,
          hostEl: null,
        }),
    })

    app.mount(host)
    await nextTick()

    const help = host.querySelector('[data-testid="clearing-confirm-help"]') as HTMLElement | null
    expect(help).toBeTruthy()
    expect(help?.textContent ?? '').toContain('This will run a clearing cycle in backend.')

    const btnConfirm = host.querySelector('button.ds-btn--primary') as HTMLButtonElement | null
    expect(btnConfirm).toBeTruthy()
    expect((btnConfirm!.textContent ?? '').trim()).toBe('Confirm')
    expect(btnConfirm!.disabled).toBe(false)

    const btnCancel = Array.from(host.querySelectorAll('button')).find((b) => (b.textContent ?? '').trim() === 'Cancel') as
      | HTMLButtonElement
      | undefined
    expect(btnCancel).toBeTruthy()
    expect(btnCancel!.disabled).toBe(false)

    app.unmount()
    host.remove()
  })
})

