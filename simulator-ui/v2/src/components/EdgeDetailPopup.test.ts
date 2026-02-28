import { createApp, h, nextTick, reactive } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import EdgeDetailPopup from './EdgeDetailPopup.vue'

function mountPopup(overrides: Record<string, unknown> = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)

  const state = reactive({
    phase: 'editing-trustline',
    fromPid: 'alice',
    toPid: 'bob',
    selectedEdgeKey: 'alice→bob',
    edgeAnchor: { x: 100, y: 200 },
    error: null,
    lastClearing: null,
  })

  const defaultProps: Record<string, unknown> = {
    phase: state.phase,
    state,
    unit: 'UAH',
    used: '0.00',
    limit: '10.00',
    available: '10.00',
    status: 'active',
    busy: false,
    forceHidden: false,
    close: () => undefined,
  }

  const onSendPayment = vi.fn()

  const app = createApp({
    render: () =>
      h(EdgeDetailPopup as any, { ...defaultProps, ...overrides, onSendPayment }),
  })

  app.mount(host)
  return { app, host, onSendPayment }
}

describe('EdgeDetailPopup', () => {
  it('renders when phase=editing-trustline, edgeAnchor set, forceHidden=false (edge click)', async () => {
    const { app, host } = mountPopup({ forceHidden: false })
    await nextTick()

    const el = host.querySelector('[data-testid="edge-detail-popup"]')
    expect(el).toBeTruthy()

    app.unmount()
    host.remove()
  })

  it('does NOT render when forceHidden=true (TrustlineManagementPanel is shown instead)', async () => {
    const { app, host } = mountPopup({ forceHidden: true })
    await nextTick()

    const el = host.querySelector('[data-testid="edge-detail-popup"]')
    expect(el).toBeFalsy()

    app.unmount()
    host.remove()
  })

  it('does NOT render when edgeAnchor is null (no edge click)', async () => {
    const state = reactive({
      phase: 'editing-trustline',
      fromPid: 'alice',
      toPid: 'bob',
      selectedEdgeKey: 'alice→bob',
      edgeAnchor: null,
      error: null,
      lastClearing: null,
    })
    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({
      render: () =>
        h(EdgeDetailPopup as any, {
          phase: state.phase,
          state,
          unit: 'UAH',
          forceHidden: false,
          close: () => undefined,
        }),
    })
    app.mount(host)
    await nextTick()

    const el = host.querySelector('[data-testid="edge-detail-popup"]')
    expect(el).toBeFalsy()

    app.unmount()
    host.remove()
  })

  it('ED-1: used>0 blocks Close line (disabled) and shows warning', async () => {
    const { app, host } = mountPopup({ used: '0.01' })
    await nextTick()

    const btn = host.querySelector('[data-testid="edge-close-line-btn"]') as HTMLButtonElement | null
    expect(btn).toBeTruthy()
    expect(btn?.disabled).toBe(true)

    const warn = host.querySelector('[data-testid="edge-close-blocked"]') as HTMLElement | null
    expect(warn).toBeTruthy()
    expect((warn?.textContent ?? '').trim()).toContain('Cannot close: trustline has outstanding debt')
    expect((warn?.textContent ?? '').trim()).toContain('0.01')
    expect((warn?.textContent ?? '').trim()).toContain('UAH')

    // Safety: even if a click is attempted, the text should not switch to confirmation.
    btn?.click()
    await nextTick()
    expect((btn?.textContent || '').includes('Confirm close')).toBe(false)

    app.unmount()
    host.remove()
  })

  it('AC-ED-5: reverse_used > 0, used = 0 => Close line disabled + inline warning', async () => {
    const { app, host } = mountPopup({ used: '0.00', reverseUsed: '0.01' })
    await nextTick()

    const btn = host.querySelector('[data-testid="edge-close-line-btn"]') as HTMLButtonElement | null
    expect(btn).toBeTruthy()
    expect(btn?.disabled).toBe(true)

    const warn = host.querySelector('[data-testid="edge-close-blocked"]') as HTMLElement | null
    expect(warn).toBeTruthy()
    expect((warn?.textContent ?? '').trim()).toContain('Cannot close: trustline has outstanding debt')
    expect((warn?.textContent ?? '').trim()).toContain('0.01')

    btn?.click()
    await nextTick()
    expect((btn?.textContent || '').includes('Confirm close')).toBe(false)

    app.unmount()
    host.remove()
  })

  it('ED-4: used=0 does NOT block Close line', async () => {
    const { app, host } = mountPopup({ used: '0.00' })
    await nextTick()

    const btn = host.querySelector('[data-testid="edge-close-line-btn"]') as HTMLButtonElement | null
    expect(btn).toBeTruthy()
    expect(btn?.disabled).toBe(false)

    expect(host.querySelector('[data-testid="edge-close-blocked"]')).toBeFalsy()

    app.unmount()
    host.remove()
  })

  it('ED-2: renders utilization bar label (used=50, limit=100 => 50%)', async () => {
    const { app, host } = mountPopup({ used: '50', limit: '100' })
    await nextTick()

    const pct = host.querySelector('[data-testid="edge-utilization-pct"]')
    expect(pct).toBeTruthy()
    expect((pct?.textContent || '').trim()).toBe('50%')

    // Bar should exist and have fill element.
    const bar = host.querySelector('[aria-label="Utilization bar"]') as HTMLElement | null
    expect(bar).toBeTruthy()
    expect(bar?.getAttribute('role')).toBe('progressbar')

    const fill = host.querySelector('.popup__util-fill') as HTMLElement | null
    expect(fill).toBeTruthy()

    app.unmount()
    host.remove()
  })

  it('ED-3: clicking Send Payment emits sendPayment', async () => {
    const { app, host, onSendPayment } = mountPopup()
    await nextTick()

    const btn = host.querySelector('[data-testid="edge-send-payment"]') as HTMLButtonElement | null
    expect(btn).toBeTruthy()
    // ED-3 polish: label must be contextual so direction is clear.
    expect((btn?.textContent ?? '').trim()).toContain('Pay alice')
    btn?.click()
    await nextTick()
    expect(onSendPayment).toHaveBeenCalledTimes(1)

    app.unmount()
    host.remove()
  })
})
