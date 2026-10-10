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
  fromPid: 'alice', toPid: 'bob', fromName: 'Alice', toName: 'Bob', runMismatch: false,
} as ManualPaymentOutcome

function mount(props: Record<string, unknown> = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const live = reactive({ phase: 'confirm-payment' })
  const state = reactive({
    phase: 'confirm-payment', fromPid: 'alice', toPid: 'bob', selectedEdgeKey: null as string | null,
    edgeAnchor: null as { x: number; y: number } | null, error: null as string | null, lastClearing: null,
  })
  const calls = { confirm: vi.fn(), retry: vi.fn(), discard: vi.fn(), cancel: vi.fn() }
  createApp({
    render: () => h(panel, {
      phase: live.phase, state, unit: 'UAH', availableCapacity: '100.00',
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
  return { host, calls, live }
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

const text = (host: HTMLElement, id: string) => (q(host, id)?.textContent ?? '').replace(/\s+/g, ' ').trim()

describe('the banner of an unresolved payment', () => {
  it('shows WHAT is unresolved (amount, equivalent, parties) and offers exactly Check / repeat and Discard', async () => {
    const { host, calls } = mount()
    await nextTick()

    expect(text(host, 'mp-outcome-unknown-intent')).toBe('Unresolved payment: 10.00 UAH, Alice → Bob.')
    expect(host.querySelector('#mp-from')).toBeNull()
    expect(host.querySelector('#mp-amount')).toBeNull()
    q(host, 'mp-retry')!.click()
    expect(calls.retry).toHaveBeenCalledTimes(1)
    expect(calls.discard).not.toHaveBeenCalled()
  })

  it('Discard is two steps: the first only shows the warning, the second discards, Keep goes back', async () => {
    const { host, calls } = mount()
    await nextTick()

    q(host, 'mp-discard')!.click()
    await nextTick()
    expect(calls.discard).not.toHaveBeenCalled()
    expect(text(host, 'mp-discard-warning')).toContain('may have been made')
    expect(q(host, 'mp-retry')).toBeNull()

    q(host, 'mp-discard-keep')!.click()
    await nextTick()
    expect(q(host, 'mp-retry')).not.toBeNull()
    expect(calls.discard).not.toHaveBeenCalled()

    q(host, 'mp-discard')!.click()
    await nextTick()
    q(host, 'mp-discard-confirm')!.click()
    expect(calls.discard).toHaveBeenCalledTimes(1)
  })

  it('Cancel closes the panel and does not discard', async () => {
    const { host, calls } = mount()
    await nextTick()
    q(host, 'manual-payment-cancel')!.click()
    expect(calls.cancel).toHaveBeenCalledTimes(1)
    expect(calls.discard).not.toHaveBeenCalled()
  })

  it('a payment of ANOTHER run: the repeat is disabled and the banner says why; discard stays available', async () => {
    const { host, calls } = mount({ paymentOutcome: { ...UNKNOWN, runMismatch: true } })
    await nextTick()

    expect((q(host, 'mp-retry') as HTMLButtonElement).disabled).toBe(true)
    expect(text(host, 'mp-outcome-other-run')).toContain('another run')
    expect((q(host, 'mp-discard') as HTMLButtonElement).disabled).toBe(false)
    q(host, 'mp-retry')!.click()
    expect(calls.retry).not.toHaveBeenCalled()
  })

  it('while a request is in flight neither action can be fired', async () => {
    const { host } = mount({ busy: true })
    await nextTick()
    expect((q(host, 'mp-retry') as HTMLButtonElement).disabled).toBe(true)
    expect((q(host, 'mp-discard') as HTMLButtonElement).disabled).toBe(true)
  })

  it('no unresolved payment: the ordinary panel (anti-vacuum for the hidden fields)', async () => {
    const { host } = mount({ paymentOutcome: null })
    await nextTick()
    expect(q(host, 'mp-outcome-unknown')).toBeNull()
    expect(host.querySelector('#mp-amount')).not.toBeNull()
  })
})

describe('the second step of the discard', () => {
  it('does not survive closing the panel: a reopened panel starts from the first step', async () => {
    const { host, live } = mount()
    await nextTick()
    q(host, 'mp-discard')!.click()
    await nextTick()
    expect(q(host, 'mp-discard-confirm'), 'premise: step two is showing').not.toBeNull()

    live.phase = 'idle' // the panel is closed (the component instance may live on, hidden)
    await nextTick()
    live.phase = 'confirm-payment' // and opened again
    await nextTick()

    expect(q(host, 'mp-discard-confirm'), 'a reopened panel offered "Discard it" at once').toBeNull()
    expect(q(host, 'mp-retry')).not.toBeNull()
  })
})
