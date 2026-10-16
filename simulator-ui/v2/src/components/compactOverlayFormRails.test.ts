import { createApp, h, nextTick, reactive, type Component } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import ManualPaymentPanel from './ManualPaymentPanel.vue'
import TrustlineManagementPanel from './TrustlineManagementPanel.vue'

const manualPaymentPanelComponent: Component = ManualPaymentPanel
const trustlineManagementPanelComponent: Component = TrustlineManagementPanel

describe('shared compact overlay form rails', () => {
  it('keeps ManualPaymentPanel on shared compact-row primitives without local width clamps', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({
      render: () =>
        h(manualPaymentPanelComponent, {
          phase: 'confirm-payment',
          state: reactive({ fromPid: 'alice', toPid: 'bob' }),
          unit: 'EQ',
          availableCapacity: '10',
          trustlinesLoading: false,
          paymentTargetsLoading: false,
          paymentToTargetIds: new Set(['bob']),
          participants: [
            { pid: 'alice', name: 'Alice' },
            { pid: 'bob', name: 'Bob with a very long label for compact rail checks' },
          ],
          busy: false,
          canSendPayment: true,
          confirmPayment: vi.fn(),
          cancel: vi.fn(),
        }),
    })

    app.mount(host)
    await nextTick()

    const panel = host.querySelector('[data-testid="manual-payment-panel"]') as HTMLElement | null
    const row = host.querySelector('.ds-controls__row.ds-controls__row--compact') as HTMLElement | null
    const suffix = host.querySelector('.mp-amount-row') as HTMLElement | null

    expect(panel).toBeTruthy()
    expect(panel?.style.width).toBe('')
    expect(panel?.style.maxWidth).toBe('')
    expect(row).toBeTruthy()
    expect(suffix?.classList.contains('ds-controls__suffix')).toBe(true)

    app.unmount()
    host.remove()
  })

  it('keeps TrustlineManagementPanel on shared compact-row primitives without local width clamps', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'editing-trustline',
      fromPid: 'alice',
      toPid: 'bob',
      selectedEdgeKey: null,
      edgeAnchor: null,
      error: null,
      lastClearing: null,
    })

    const app = createApp({
      render: () =>
        h(trustlineManagementPanelComponent, {
          phase: 'editing-trustline',
          state,
          unit: 'EQ',
          used: '1',
          currentLimit: '10',
          available: '9',
          participants: [
            { pid: 'alice', name: 'Alice' },
            { pid: 'bob', name: 'Bob with a very long trustline label for compact rail checks' },
          ],
          // Внешнее ревью 013 (P2): панель здесь в фазе правки существующей линии и показывает её
          // числа, значит основание — `row`. При `no-row` числа честно погашены, и тест про
          // раскладку судил бы уже не ту раскладку.
          figuresSource: { kind: 'row' } as const,
          trustlines: [],
          busy: false,
          confirmTrustlineCreate: vi.fn(),
          confirmTrustlineUpdate: vi.fn(),
          confirmTrustlineClose: vi.fn(),
          cancel: vi.fn(),
        }),
    })

    app.mount(host)
    await nextTick()

    const panel = host.querySelector('[data-testid="trustline-panel"]') as HTMLElement | null
    const row = host.querySelector('.ds-controls__row.ds-controls__row--compact') as HTMLElement | null
    const suffix = host.querySelector('.tl-input-row') as HTMLElement | null

    expect(panel).toBeTruthy()
    expect(panel?.style.width).toBe('')
    expect(panel?.style.maxWidth).toBe('')
    expect(row).toBeTruthy()
    expect(suffix?.classList.contains('ds-controls__suffix')).toBe(true)

    app.unmount()
    host.remove()
  })
})