/**
 * 037 F-037-4: a payment started from a line runs the other way (the line is creditor -> debtor; the DEBTOR pays the creditor).
 * The screen says so - in the line's popup and on the payment panel when it was opened from a line - and only then.
 */
import { createApp, h, nextTick, reactive, ref, type Component } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import EdgeDetailPopup from './EdgeDetailPopup.vue'
import ManualPaymentPanel from './ManualPaymentPanel.vue'
import { useInteractFSM } from '../composables/interact/useInteractFSM'
import { interactText } from '../i18n/interactStrings'

afterEach(() => { document.body.innerHTML = '' })
async function settle() { for (let i = 0; i < 5; i += 1) await nextTick() }

const PEOPLE = [{ pid: 'alice', name: 'Alice' }, { pid: 'bob', name: 'Bob' }, { pid: 'carol', name: 'Carol' }]
const q = (host: HTMLElement, id: string) => host.querySelector(`[data-testid="${id}"]`) as HTMLElement | null
const text = (host: HTMLElement, id: string) => (q(host, id)?.textContent ?? '').replace(/\s+/g, ' ').trim()
const NOTE = interactText('lineDirectionNote')

function mountPanel(start: (fsm: ReturnType<typeof useInteractFSM>) => void) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const fsm = useInteractFSM({ snapshot: ref(null), findActiveTrustline: () => null })
  start(fsm)
  const app = createApp({
    render: () => h(ManualPaymentPanel as Component, {
      phase: fsm.state.phase, state: fsm.state, unit: 'UAH', availableCapacity: '10.00', trustlinesLoading: false, paymentTargetsLoading: false,
      paymentTargetsLastError: null, paymentToTargetIds: new Set(['alice', 'bob', 'carol']), trustlines: [], participants: PEOPLE, busy: false,
      canSendPayment: true, confirmPayment: vi.fn(), cancel: vi.fn(), setFromPid: fsm.setPaymentFromPid, setToPid: fsm.setPaymentToPid,
    }),
  })
  app.mount(host)
  return { host, fsm }
}

describe('the payment panel', () => {
  it('opened from a line (both parties set, reversed): says why the sender and the recipient are the other way round', async () => {
    // What the line popup does: the debtor pays the creditor.
    const { host } = mountPanel((fsm) => { fsm.startPaymentFlowWithFrom('alice'); fsm.setPaymentToPid('bob') })
    await settle()
    expect(text(host, 'mp-line-direction-note')).toBe(NOTE)
  })

  it('opened the ordinary way (the sender, then the recipient chosen in the panel): no such sentence', async () => {
    const { host, fsm } = mountPanel((f) => { f.startPaymentFlow() })
    await settle()
    expect(q(host, 'mp-line-direction-note')).toBeNull()
    fsm.setPaymentFromPid('alice')
    await settle()
    fsm.setPaymentToPid('bob')
    await settle()
    expect(fsm.state.phase).toBe('confirm-payment')
    expect(q(host, 'mp-line-direction-note'), 'reaching the confirm step by choosing is not entering from a line').toBeNull()
  })

  it('opened from a node card (the sender only): no such sentence', async () => {
    const { host } = mountPanel((fsm) => { fsm.startPaymentFlowWithFrom('alice') })
    await settle()
    expect(q(host, 'mp-line-direction-note')).toBeNull()
  })
})

describe('the line popup', () => {
  it('says next to "Pay" that the payment goes against the arrow of the line', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const state = reactive({ phase: 'editing-trustline', fromPid: 'alice', toPid: 'bob', selectedEdgeKey: 'alice→bob', edgeAnchor: { x: 100, y: 200 }, error: null, lastClearing: null })
    const app = createApp({
      render: () => h(EdgeDetailPopup as Component, {
        phase: state.phase, state, unit: 'UAH', figuresSource: { kind: 'row' }, used: '0.00', limit: '10.00', available: '10.00', status: 'active',
        busy: false, forceHidden: false, close: () => undefined,
      }),
    })
    app.mount(host)
    await settle()

    expect(text(host, 'edge-payment-direction-note')).toBe(NOTE)
    app.unmount()
  })
})

describe('the sentence follows the pair the panel was opened with, however the pair is edited', () => {
  it('a recipient picked on the canvas ends it; going back to the same pair brings it back', async () => {
    const { host, fsm } = mountPanel((f) => { f.startPaymentFlowWithFrom('alice'); f.setPaymentToPid('bob') })
    await settle()
    expect(q(host, 'mp-line-direction-note')).not.toBeNull()

    fsm.selectNode('carol') // the canvas path: toPid changes inside the confirm step
    await settle()
    expect(fsm.state.toPid).toBe('carol')
    expect(q(host, 'mp-line-direction-note'), 'the pair is no longer the line pair').toBeNull()

    fsm.selectNode('bob')
    await settle()
    expect(q(host, 'mp-line-direction-note')).not.toBeNull()
  })

  it('the sentence does not promise that the payment goes through this line, nor that it repays the debt', () => {
    expect(NOTE).toContain('The server may route some or all of the payment through other participants.')
    expect(NOTE).toContain('it does not repay that debt')
    expect(NOTE).not.toMatch(/adds to the debtor.s debt on this line/)
  })
})

describe('a panel opened from a line: the amount has the focus, and the sentence is folded away', () => {
  it('the amount is focused (it is empty, nothing is sent), and the sentence sits in a CLOSED details that is always in the DOM', async () => {
    const { host } = mountPanel((fsm) => { fsm.startPaymentFlowWithFrom('alice'); fsm.setPaymentToPid('bob') })
    await settle()

    expect(document.activeElement).toBe(host.querySelector('#mp-amount'))
    const details = q(host, 'mp-line-direction') as HTMLDetailsElement
    expect(details.tagName).toBe('DETAILS')
    expect(details.open, 'closed by default: the confirm step is the tallest one on a phone').toBe(false)
    expect(text(host, 'mp-line-direction-note')).toBe(NOTE)
    expect(details.querySelector('summary')!.textContent).toBe('Why is the direction reversed?')
  })

  it('nothing is sent by that focus: Enter in the empty amount, held or not, confirms nothing', async () => {
    const calls = { confirm: vi.fn() }
    const host = document.createElement('div')
    document.body.appendChild(host)
    const fsm = useInteractFSM({ snapshot: ref(null), findActiveTrustline: () => null })
    fsm.startPaymentFlowWithFrom('alice')
    fsm.setPaymentToPid('bob')
    createApp({
      render: () => h(ManualPaymentPanel as Component, {
        phase: fsm.state.phase, state: fsm.state, unit: 'UAH', availableCapacity: '10.00', trustlinesLoading: false, paymentTargetsLoading: false,
        paymentTargetsLastError: null, paymentToTargetIds: new Set(['alice', 'bob', 'carol']), trustlines: [], participants: PEOPLE, busy: false,
        canSendPayment: true, confirmPayment: calls.confirm, cancel: vi.fn(), setFromPid: fsm.setPaymentFromPid, setToPid: fsm.setPaymentToPid,
      }),
    }).mount(host)
    await settle()
    const input = host.querySelector('#mp-amount') as HTMLInputElement
    expect(document.activeElement).toBe(input)
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', repeat: true, bubbles: true, cancelable: true }))
    await settle()
    expect(calls.confirm).not.toHaveBeenCalled()
  })

  it('the node card (the sender only) and the ordinary way do NOT take the focus into an amount', async () => {
    const card = mountPanel((fsm) => { fsm.startPaymentFlowWithFrom('alice') })
    await settle()
    expect(card.host.querySelector('#mp-amount')).toBeNull()
    document.body.innerHTML = ''

    const ordinary = mountPanel((f) => { f.startPaymentFlow() })
    await settle()
    expect(ordinary.host.querySelector('#mp-amount')).toBeNull()
    expect(q(ordinary.host, 'mp-line-direction')).toBeNull()
  })
})
