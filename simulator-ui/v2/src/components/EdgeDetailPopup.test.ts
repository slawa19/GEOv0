import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createApp, h, nextTick, reactive, type Component } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import EdgeDetailPopup from './EdgeDetailPopup.vue'

const edgeDetailPopupSource = readFileSync(resolve(process.cwd(), 'src/components/EdgeDetailPopup.vue'), 'utf8')

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
    // `F-013-7`: основание для чисел — обязательный проп, и его отсутствие теперь запрещает мутацию.
    // Этот файл судит остальное поведение попапа, поэтому здесь источник ответил строкой;
    // сам гард судится в EdgeDetailPopup.sourceUnavailable.test.ts, где умолчания намеренно нет.
    figuresSource: { kind: 'row' } as const,
    used: '0.00',
    limit: '10.00',
    available: '10.00',
    status: 'active',
    busy: false,
    forceHidden: false,
    close: () => undefined,
  }

  const onSendPayment = vi.fn()

  const component: Component = EdgeDetailPopup
  const app = createApp({
    render: () => h(component, { ...defaultProps, ...overrides, onSendPayment }),
  })

  app.mount(host)
  return { app, host, onSendPayment }
}

describe('EdgeDetailPopup', () => {
  it('renders when forceHidden=false', async () => {
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

  // INTENTIONAL, 026 `T2603.2` (owner В1 2026-09-29): ED-1 and AC-ED-5 asserted that a debt either way disabled
  // Close with "Reduce debt to 0 first". The supported debt (`used`) now makes the close a request and says so; the
  // reverse debt is the other line's. A requested close is shown and not offered again.
  it('ED-1 (026): used>0 allows Close line and announces a request; reverse debt is silent', async () => {
    for (const [used, reverseUsed, note] of [['0.01', '0.00', true], ['0.00', '0.01', false]] as const) {
      const { app, host } = mountPopup({ used, reverseUsed })
      await nextTick()
      const btn = host.querySelector('[data-testid="edge-close-line-btn"]') as HTMLButtonElement
      expect(btn.disabled).toBe(false)
      expect(host.querySelector('[data-testid="edge-close-blocked"]')).toBeNull()
      const el = host.querySelector('[data-testid="edge-close-request-note"]')
      expect(el ? (el.textContent ?? '') : null).toEqual(note ? expect.stringContaining('0.01 UAH') : null)
      app.unmount()
      host.remove()
    }
  })

  it('ED-6 (026): a requested close is shown and Close line is not offered again', async () => {
    const { app, host } = mountPopup({ used: '7.00', limit: '0.00', closeRequestedAt: '2026-10-02T08:00:00Z' })
    await nextTick()
    expect(host.querySelector('[data-testid="edge-close-requested"]')?.textContent ?? '').toContain('Close requested')
    expect((host.querySelector('[data-testid="edge-close-line-btn"]') as HTMLButtonElement).disabled).toBe(true)
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

  it('keeps the edge-detail inspector on WM/DS contracts without introducing ds-inspector-row', () => {
    expect(edgeDetailPopupSource).toContain("position: 'static'")
    expect(edgeDetailPopupSource).toContain('class="popup ds-ov-item ds-ov-surface ds-ov-edge-detail"')
    expect(edgeDetailPopupSource).not.toContain('ds-inspector-row')
  })
})
