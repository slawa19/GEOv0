/**
 * 037 A2 fix-delta, panel half: next to the banner of an unresolved payment the panel offers no way to send ANOTHER one.
 */
import { createApp, h, nextTick, reactive, type Component } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ManualPaymentPanel from './ManualPaymentPanel.vue'
import type { ManualPaymentOutcome } from '../composables/useInteractMode'

const panel: Component = ManualPaymentPanel

afterEach(() => {
  document.body.innerHTML = ''
})

const UNKNOWN = {
  kind: 'unknown', message: 'No usable answer arrived.', amount: '10.00', equivalent: 'UAH',
  fromPid: 'alice', toPid: 'bob', fromName: 'Alice', toName: 'Bob',
} as ManualPaymentOutcome

function mount(props: Record<string, unknown> = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const state = reactive({
    phase: 'confirm-payment', fromPid: 'alice', toPid: 'bob', selectedEdgeKey: null as string | null,
    edgeAnchor: null as { x: number; y: number } | null, error: null as string | null, lastClearing: null,
  })
  const calls = { confirm: vi.fn(), retry: vi.fn(), discard: vi.fn(), cancel: vi.fn() }
  createApp({
    render: () => h(panel, {
      phase: 'confirm-payment', state, unit: 'UAH', availableCapacity: '100.00',
      trustlinesLoading: false, paymentTargetsLoading: false, paymentTargetsLastError: null,
      paymentToTargetIds: new Set(['bob']), trustlines: [],
      participants: [{ pid: 'alice', name: 'Alice' }, { pid: 'bob', name: 'Bob' }],
      busy: false, canSendPayment: true,
      confirmPayment: calls.confirm, cancel: calls.cancel, retryPayment: calls.retry, discardUnresolvedPayment: calls.discard,
      setFromPid: vi.fn(), setToPid: vi.fn(),
      paymentOutcome: UNKNOWN,
      ...props,
    }),
  }).mount(host)
  return { host, calls }
}

const q = (host: HTMLElement, id: string) => host.querySelector(`[data-testid="${id}"]`) as HTMLElement | null

describe('the panel next to an unresolved payment', () => {
  it('REPRODUCER: Confirm of another payment is not offered (a disabled or absent button, and Enter in the field sends nothing)', async () => {
    const { host, calls } = mount()
    await nextTick()

    const confirm = q(host, 'manual-payment-confirm') as HTMLButtonElement | null
    const input = host.querySelector('#mp-amount') as HTMLInputElement | null
    expect(q(host, 'mp-outcome-unknown'), 'precondition: the banner is there').not.toBeNull()
    if (confirm) {
      expect(confirm.disabled).toBe(true)
      confirm.click()
    }
    if (input) {
      input.value = '10.0'
      input.dispatchEvent(new Event('input'))
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }))
    }
    await nextTick()
    expect(calls.confirm).not.toHaveBeenCalled()
  })
})
