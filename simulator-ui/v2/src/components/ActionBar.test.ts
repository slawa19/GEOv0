import { createApp, h, nextTick } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import ActionBar from './ActionBar.vue'

function mountActionBar(overrides: Record<string, unknown> = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)

  const startPaymentFlow = vi.fn()
  const startTrustlineFlow = vi.fn()
  const startClearingFlow = vi.fn()

  const defaultProps: Record<string, unknown> = {
    phase: 'idle',
    busy: false,
    actionsDisabled: false,
    runTerminal: false,
    startPaymentFlow,
    startTrustlineFlow,
    startClearingFlow,
  }

  const app = createApp({
    render: () => h(ActionBar as any, { ...defaultProps, ...overrides }),
  })

  app.mount(host)

  return {
    host,
    app,
    startPaymentFlow,
    startTrustlineFlow,
    startClearingFlow,
  }
}

describe('ActionBar', () => {
  it('enables flow start buttons in idle phase', async () => {
    const { app, host } = mountActionBar({ phase: 'idle' })
    await nextTick()

    const btnPay = host.querySelector('[data-testid="actionbar-payment"]') as HTMLButtonElement
    const btnTl = host.querySelector('[data-testid="actionbar-trustline"]') as HTMLButtonElement
    const btnClr = host.querySelector('[data-testid="actionbar-clearing"]') as HTMLButtonElement

    expect(btnPay.disabled).toBe(false)
    expect(btnTl.disabled).toBe(false)
    expect(btnClr.disabled).toBe(false)

    expect(host.querySelector('[data-testid="actionbar-locked-hint"]')).toBeFalsy()

    app.unmount()
    host.remove()
  })

  it('when busy in idle phase: disables buttons and explains why (title + hint)', async () => {
    const { app, host, startPaymentFlow, startTrustlineFlow, startClearingFlow } = mountActionBar({ phase: 'idle', busy: true })
    await nextTick()

    const btnPay = host.querySelector('[data-testid="actionbar-payment"]') as HTMLButtonElement
    const btnTl = host.querySelector('[data-testid="actionbar-trustline"]') as HTMLButtonElement
    const btnClr = host.querySelector('[data-testid="actionbar-clearing"]') as HTMLButtonElement

    expect(btnPay.disabled).toBe(true)
    expect(btnTl.disabled).toBe(true)
    expect(btnClr.disabled).toBe(true)

    expect(btnPay.title).toContain('Operation in progress')
    expect(btnTl.title).toContain('Operation in progress')
    expect(btnClr.title).toContain('Operation in progress')

    const hint = host.querySelector('[data-testid="actionbar-busy-hint"]') as HTMLElement | null
    expect(hint).toBeTruthy()
    expect(hint?.textContent ?? '').toContain('please wait')

    // Safety: clicks must be blocked.
    btnPay.click()
    btnTl.click()
    btnClr.click()
    expect(startPaymentFlow).toHaveBeenCalledTimes(0)
    expect(startTrustlineFlow).toHaveBeenCalledTimes(0)
    expect(startClearingFlow).toHaveBeenCalledTimes(0)

    app.unmount()
    host.remove()
  })

  it('when cancelling in idle phase (busy=true, cancelling=true): shows cancelling hint + more specific title', async () => {
    const { app, host } = mountActionBar({ phase: 'idle', busy: true, cancelling: true })
    await nextTick()

    const btnPay = host.querySelector('[data-testid="actionbar-payment"]') as HTMLButtonElement
    expect(btnPay.disabled).toBe(true)
    expect(btnPay.title).toContain('Cancelling')

    const hint = host.querySelector('[data-testid="actionbar-busy-hint"]') as HTMLElement | null
    expect(hint).toBeTruthy()
    expect(hint?.textContent ?? '').toContain('Cancelling')

    app.unmount()
    host.remove()
  })

  it('locks ActionBar when phase is active (phase!=idle): disables buttons, shows reason, blocks clicks', async () => {
    const { app, host, startPaymentFlow, startTrustlineFlow, startClearingFlow } = mountActionBar({
      phase: 'picking-payment-from',
    })
    await nextTick()

    const btnPay = host.querySelector('[data-testid="actionbar-payment"]') as HTMLButtonElement
    const btnTl = host.querySelector('[data-testid="actionbar-trustline"]') as HTMLButtonElement
    const btnClr = host.querySelector('[data-testid="actionbar-clearing"]') as HTMLButtonElement

    expect(btnPay.disabled).toBe(true)
    expect(btnTl.disabled).toBe(true)
    expect(btnClr.disabled).toBe(true)

    // UX: explain why locked.
    expect(btnPay.title).toContain('Cancel current action first')
    expect(btnTl.title).toContain('Cancel current action first')
    expect(btnClr.title).toContain('Cancel current action first')
    expect(btnPay.title).toContain('ESC')
    expect(btnTl.title).toContain('ESC')
    expect(btnClr.title).toContain('ESC')

    const hint = host.querySelector('[data-testid="actionbar-locked-hint"]')
    expect(hint).toBeTruthy()
    expect(hint?.textContent ?? '').toContain('ESC')

    // Safety: even if a click somehow happens, handlers must not be invoked.
    btnPay.click()
    btnTl.click()
    btnClr.click()
    expect(startPaymentFlow).toHaveBeenCalledTimes(0)
    expect(startTrustlineFlow).toHaveBeenCalledTimes(0)
    expect(startClearingFlow).toHaveBeenCalledTimes(0)

    app.unmount()
    host.remove()
  })
})


