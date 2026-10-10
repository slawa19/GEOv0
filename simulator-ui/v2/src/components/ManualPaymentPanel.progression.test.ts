/**
 * 037 B2 on the existing `ManualPaymentPanel`: the progression From -> To -> Amount, the summary of the confirm step and the
 * two visible captions of the chosen recipient. Observed through the DOM: which list is open, where the focus is, what the
 * callbacks were called with. No test here reads the source text.
 */
import { createApp, h, nextTick, reactive, type Component } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ManualPaymentPanel from './ManualPaymentPanel.vue'
import type { ManualPaymentOutcome, PaymentTargetEstimate } from '../composables/useInteractMode'

const panel: Component = ManualPaymentPanel

const PARTICIPANTS = [
  { pid: 'alice', name: 'Alice' },
  { pid: 'bob', name: 'Bob' },
  { pid: 'carol', name: 'Carol' },
]

afterEach(() => {
  document.body.innerHTML = ''
})

async function settle() {
  for (let i = 0; i < 6; i += 1) await nextTick()
}

/** A panel wired to a tiny FSM like the real one (From picks move to the recipient step, a recipient to the confirm step). */
function mountFlow(over: { phase?: string; fromPid?: string | null; toPid?: string | null; props?: Record<string, unknown> } = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const state = reactive({
    phase: over.phase ?? 'picking-payment-from', fromPid: (over.fromPid ?? null) as string | null, toPid: (over.toPid ?? null) as string | null,
    selectedEdgeKey: null as string | null, edgeAnchor: null as { x: number; y: number } | null, error: null as string | null, lastClearing: null,
  })
  const props = reactive<Record<string, unknown>>({
    unit: 'UAH', availableCapacity: '10.00', trustlinesLoading: false, paymentTargetsLoading: false, paymentTargetsLastError: null,
    paymentToTargetIds: new Set(['alice', 'bob', 'carol']), trustlines: [], participants: PARTICIPANTS, busy: false, canSendPayment: true,
    targetEstimate: null, paymentOutcome: null, ...(over.props ?? {}),
  })
  const calls = {
    confirm: vi.fn(), cancel: vi.fn(),
    setFrom: vi.fn((pid: string | null) => { state.fromPid = pid; state.toPid = null; state.phase = pid ? 'picking-payment-to' : 'picking-payment-from' }),
    setTo: vi.fn((pid: string | null) => { state.toPid = pid; state.phase = pid ? 'confirm-payment' : 'picking-payment-to' }),
  }
  const app = createApp({
    render: () => h(panel, {
      ...props, phase: state.phase, state, confirmPayment: calls.confirm, cancel: calls.cancel,
      setFromPid: calls.setFrom, setToPid: calls.setTo, retryPayment: vi.fn(), dismissPaymentResult: vi.fn(),
    }),
  })
  app.mount(host)
  return { host, state, props, calls, unmount: () => app.unmount() }
}

const q = (host: HTMLElement, id: string) => host.querySelector(`[data-testid="${id}"]`) as HTMLElement | null
const text = (host: HTMLElement, id: string) => (q(host, id)?.textContent ?? '').replace(/\s+/g, ' ').trim()
const surface = (id: string) => document.getElementById(`${id}__surface`)
const option = (id: string, value: string) => document.querySelector(`#${id}__surface [data-option-value="${value}"]`) as HTMLElement

async function openByClick(id: string) {
  ;(document.getElementById(`${id}__trigger`) as HTMLElement).click()
  await settle()
}

describe('From -> To: the recipient list opens by itself, only after an EXPLICIT choice of the sender', () => {
  it('choosing a sender in its list opens the recipient list and moves the focus into it; the next choice is announced', async () => {
    const { host } = mountFlow()
    await settle()
    expect(surface('mp-to')).toBeNull()

    await openByClick('mp-from')
    option('mp-from', 'alice').click()
    await settle()

    expect(surface('mp-from')).toBeNull()
    expect(surface('mp-to')).not.toBeNull()
    expect(surface('mp-to')!.contains(document.activeElement), 'the focus is inside the recipient list').toBe(true)
    expect(text(host, 'mp-next-choice')).toBe('Choose the recipient.')
  })

  it('choosing the empty entry of the sender list opens nothing', async () => {
    const { calls } = mountFlow({ phase: 'picking-payment-to', fromPid: 'alice' })
    await settle()
    await openByClick('mp-from')
    option('mp-from', '').click()
    await settle()

    expect(calls.setFrom).toHaveBeenCalledWith(null)
    expect(surface('mp-to')).toBeNull()
  })

  it('a sender set by the program (not by choosing in the list) opens nothing', async () => {
    const { state, host } = mountFlow()
    await settle()
    state.fromPid = 'alice'
    state.phase = 'picking-payment-to'
    await settle()

    expect(surface('mp-to')).toBeNull()
    expect(text(host, 'mp-next-choice')).toBe('')
  })

  it('a refresh of the data (recipients allowed, participants, targets loading) opens nothing and does not take the focus', async () => {
    const { props } = mountFlow({ phase: 'picking-payment-to', fromPid: 'alice' })
    await settle()
    const trigger = document.getElementById('mp-from__trigger') as HTMLElement
    trigger.focus()

    props.paymentTargetsLoading = true
    await settle()
    props.paymentToTargetIds = new Set(['bob'])
    props.paymentTargetsLoading = false
    await settle()
    props.participants = [...PARTICIPANTS, { pid: 'dave', name: 'Dave' }]
    await settle()

    expect(surface('mp-to')).toBeNull()
    expect(document.activeElement).toBe(trigger)
  })

  it('a panel that starts with both parties set (from an edge or a node card) opens no list and does not take the focus', async () => {
    const { host } = mountFlow({ phase: 'confirm-payment', fromPid: 'bob', toPid: 'alice' })
    await settle()

    expect(surface('mp-to')).toBeNull()
    expect(surface('mp-from')).toBeNull()
    expect(document.activeElement).not.toBe(host.querySelector('#mp-amount'))
  })

  it('an unresolved payment (the banner replaces the selectors) opens no list', async () => {
    const outcome: ManualPaymentOutcome = {
      kind: 'unknown', message: 'no answer', amount: '1.00', equivalent: 'UAH', fromPid: 'alice', toPid: 'bob', fromName: 'Alice', toName: 'Bob',
      runMismatch: false,
    } as unknown as ManualPaymentOutcome
    const { host } = mountFlow({ phase: 'picking-payment-from', props: { paymentOutcome: outcome } })
    await settle()

    expect(q(host, 'mp-outcome-unknown')).not.toBeNull()
    expect(surface('mp-to')).toBeNull()
    expect(surface('mp-from')).toBeNull()
  })

  it('typing in the amount field opens nothing', async () => {
    const { host } = mountFlow({ phase: 'confirm-payment', fromPid: 'alice', toPid: 'bob' })
    await settle()
    const input = host.querySelector('#mp-amount') as HTMLInputElement
    input.value = '12.5'
    input.dispatchEvent(new Event('input'))
    await settle()

    expect(surface('mp-to')).toBeNull()
    expect(surface('mp-from')).toBeNull()
  })

  it('the first Escape closes the recipient list and leaves the panel alone; the focus goes back to its trigger', async () => {
    const { calls } = mountFlow()
    await settle()
    await openByClick('mp-from')
    option('mp-from', 'alice').click()
    await settle()
    expect(surface('mp-to')).not.toBeNull()

    surface('mp-to')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
    await settle()

    expect(surface('mp-to')).toBeNull()
    expect(calls.cancel).not.toHaveBeenCalled()
    expect(document.activeElement).toBe(document.getElementById('mp-to__trigger'))
  })

  it('Tab and Shift+Tab inside the open recipient list stay inside it', async () => {
    mountFlow()
    await settle()
    await openByClick('mp-from')
    option('mp-from', 'alice').click()
    await settle()
    const list = surface('mp-to')!
    const options = Array.from(list.querySelectorAll<HTMLElement>('[role="option"]'))
    options[options.length - 1].focus()

    list.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }))
    expect(list.contains(document.activeElement), 'Tab from the last option wraps to the first').toBe(true)
    expect(document.activeElement).toBe(options[0])
    list.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true }))
    expect(document.activeElement).toBe(options[options.length - 1])
  })
})

describe('To -> Amount: choosing the recipient focuses the amount and never sends', () => {
  it('moves the focus to the amount field, calls no confirm, and clears the announcement', async () => {
    const { host, calls } = mountFlow()
    await settle()
    await openByClick('mp-from')
    option('mp-from', 'alice').click()
    await settle()
    option('mp-to', 'bob').click()
    await settle()

    expect(calls.setTo).toHaveBeenCalledWith('bob')
    expect(surface('mp-to')).toBeNull()
    expect(document.activeElement).toBe(host.querySelector('#mp-amount'))
    expect(calls.confirm).not.toHaveBeenCalled()
    expect(text(host, 'mp-next-choice')).toBe('')
  })

  it('Enter in the still empty amount field sends nothing', async () => {
    const { host, calls } = mountFlow()
    await settle()
    await openByClick('mp-from')
    option('mp-from', 'alice').click()
    await settle()
    option('mp-to', 'bob').click()
    await settle()

    const input = host.querySelector('#mp-amount') as HTMLInputElement
    for (let i = 0; i < 3; i += 1) input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
    await settle()
    expect(calls.confirm).not.toHaveBeenCalled()
  })

  it('choosing the empty entry of the recipient list focuses nothing new', async () => {
    const { host } = mountFlow({ phase: 'picking-payment-to', fromPid: 'alice' })
    await settle()
    await openByClick('mp-to')
    option('mp-to', '').click()
    await settle()

    expect(host.querySelector('#mp-amount')).toBeNull()
  })
})

describe('the summary of the confirm step', () => {
  it('names who pays whom, in the order of the payment, with the amount once it is entered', async () => {
    const { host } = mountFlow({ phase: 'confirm-payment', fromPid: 'alice', toPid: 'bob' })
    await settle()
    expect(text(host, 'mp-summary-parties')).toBe('Alice pays Bob')
    expect(text(host, 'mp-summary-amount')).toBe('amount not entered')

    const input = host.querySelector('#mp-amount') as HTMLInputElement
    input.value = '1'
    input.dispatchEvent(new Event('input'))
    await settle()
    expect(text(host, 'mp-summary-amount')).toBe('1.00 UAH')
  })

  it('is right for the reversed order too (a payment started from an edge runs the other way) and never shows an id', async () => {
    const { host } = mountFlow({ phase: 'confirm-payment', fromPid: 'bob', toPid: 'alice' })
    await settle()

    expect(text(host, 'mp-summary-parties')).toBe('Bob pays Alice')
    expect(text(host, 'mp-summary-parties')).not.toMatch(/\b(alice|bob)\b/)
    expect(host.textContent).toContain('Manual payment: Bob → Alice')
    expect(host.textContent).not.toContain('Manual payment: bob → alice')
  })
})

describe('the two visible captions of the chosen recipient', () => {
  const received = (maxAvailable: string | null, hops = 3): PaymentTargetEstimate => ({ state: 'received', hops, maxAvailable })
  const confirmWith = async (targetEstimate: PaymentTargetEstimate | null) => {
    const f = mountFlow({ phase: 'confirm-payment', fromPid: 'alice', toPid: 'bob', props: { targetEstimate } })
    await settle()
    return f.host
  }

  it('shows "Shortest path: N steps" and "Estimated maximum: X" as two rows, plainly visible (no tooltip needed)', async () => {
    const host = await confirmWith(received('87.5', 3))
    expect(text(host, 'mp-shortest')).toBe('Shortest path 3 steps')
    expect(text(host, 'mp-estimate')).toContain('Estimated maximum')
    expect(text(host, 'mp-estimate-max')).toBe('87.50 UAH')
    expect(text(host, 'mp-estimate-source')).toBe('server estimate')
    expect(text(host, 'mp-route-note')).toBe('The route is chosen when the payment is executed.')
  })

  it('says "1 step" for one step', async () => {
    const host = await confirmWith(received('5', 1))
    expect(text(host, 'mp-shortest')).toBe('Shortest path 1 step')
  })

  it('"not estimated" (null) is not a measured zero, and a measured zero is not "not estimated"', async () => {
    const none = await confirmWith(received(null))
    expect(text(none, 'mp-estimate-max')).toBe('not estimated')
    expect(text(none, 'mp-estimate-max')).not.toContain('0.00')
    expect(text(none, 'mp-estimate-source')).toBe('')
    document.body.innerHTML = ''
    const zero = await confirmWith(received('0.00'))
    expect(text(zero, 'mp-estimate-max')).toBe('0.00 UAH')
  })

  it('names the state of the answer while it is not received, for BOTH captions', async () => {
    const loading = await confirmWith({ state: 'loading' })
    expect(text(loading, 'mp-estimate-max')).toBe('waiting for the server')
    expect(text(loading, 'mp-shortest')).toBe('Shortest path waiting for the server')
    document.body.innerHTML = ''
    const failed = await confirmWith({ state: 'failed' })
    expect(text(failed, 'mp-estimate-max')).toBe('not received')
    expect(text(failed, 'mp-shortest')).toBe('Shortest path not received')
  })

  it('a recipient the answer does not list gets no captions at all', async () => {
    const host = await confirmWith(null)
    expect(q(host, 'mp-shortest')).toBeNull()
    expect(q(host, 'mp-estimate')).toBeNull()
  })
})
