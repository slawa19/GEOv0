/**
 * 037 C on the existing `ClearingPanel`: the finished result of a clearing - who repaid whom - until "Close". Observed through
 * the DOM. The direction is the contract: an edge of the answer runs creditor -> debtor, so `{from: Alice, to: Bob}` reads
 * "Bob's debt to Alice reduced by X" and is never worded as a payment from Alice to Bob.
 */
import { createApp, h, nextTick, reactive, type Component } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ClearingPanel from './ClearingPanel.vue'
import type { SimulatorActionClearingRealResponse } from '../api/simulatorTypes'

const panel: Component = ClearingPanel

const PEOPLE = [
  { pid: 'alice', name: 'Alice', type: 'person', status: 'active' },
  { pid: 'bob', name: 'Bob', type: 'person', status: 'active' },
  { pid: 'carol', name: 'Carol', type: 'person', status: 'active' },
]

afterEach(() => { document.body.innerHTML = '' })

async function settle() { for (let i = 0; i < 4; i += 1) await nextTick() }

function result(over: Partial<SimulatorActionClearingRealResponse> = {}): SimulatorActionClearingRealResponse {
  return {
    ok: true, equivalent: 'UAH', cleared_cycles: 1, total_cleared_amount: '7.00',
    cycles: [{ cleared_amount: '7.00', edges: [{ from: 'alice', to: 'bob' }, { from: 'bob', to: 'carol' }, { from: 'carol', to: 'alice' }] }],
    ...over,
  }
}

function mount(o: { phase?: string; last?: SimulatorActionClearingRealResponse | null; error?: string | null; busy?: boolean; participants?: unknown[] | undefined; equivalent?: string } = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const state = reactive({
    phase: o.phase ?? 'clearing-preview', fromPid: null as string | null, toPid: null as string | null, initiatedWithPrefilledFrom: false,
    selectedEdgeKey: null as string | null, edgeAnchor: null as { x: number; y: number } | null, error: (o.error ?? null) as string | null,
    lastClearing: (o.last === undefined ? result() : o.last) as SimulatorActionClearingRealResponse | null,
  })
  const calls = { cancel: vi.fn(), confirm: vi.fn() }
  const props = reactive<Record<string, unknown>>({ busy: o.busy ?? false, equivalent: o.equivalent ?? 'UAH', participants: 'participants' in o ? o.participants : PEOPLE })
  const app = createApp({
    render: () => h(panel, { phase: state.phase, state, ...props, confirmClearing: calls.confirm, cancel: calls.cancel }),
  })
  app.mount(host)
  return { host, state, props, calls }
}

const q = (host: HTMLElement, id: string) => host.querySelector(`[data-testid="${id}"]`) as HTMLElement | null
const text = (host: HTMLElement, id: string) => (q(host, id)?.textContent ?? '').replace(/\s+/g, ' ').trim()
const lines = (host: HTMLElement) => Array.from(host.querySelectorAll('[data-testid="clearing-edge-line"]')).map((e) => (e.textContent ?? '').replace(/\s+/g, ' ').trim())

describe('the finished result', () => {
  it('names the debtor and the creditor in the right direction: the edge of the answer is creditor -> debtor', async () => {
    const { host } = mount()
    await settle()

    expect(lines(host)).toEqual([
      'Bob’s debt to Alice reduced by 7.00 UAH',
      'Carol’s debt to Bob reduced by 7.00 UAH',
      'Alice’s debt to Carol reduced by 7.00 UAH',
    ])
    const all = host.textContent ?? ''
    expect(all).not.toMatch(/Alice (paid|pays|→|->) Bob/)
    expect(all).not.toMatch(/(payment|paid)\b.*\b(Alice|Bob|Carol)/i)
  })

  it('uses the sum of the cycle and the equivalent of the ANSWER (not the one the panel was given)', async () => {
    const { host } = mount({
      equivalent: 'UAH',
      last: result({ equivalent: 'USD', total_cleared_amount: '5.50', cycles: [{ cleared_amount: '5.50', edges: [{ from: 'alice', to: 'bob' }, { from: 'bob', to: 'alice' }] }] }),
    })
    await settle()

    expect(lines(host)[0]).toBe('Bob’s debt to Alice reduced by 5.50 USD')
    expect(text(host, 'clearing-total')).toContain('5.50 USD')
    expect(host.textContent).not.toContain('UAH 5.50')
  })

  it('one block per cycle with its own sum, in the order of the answer', async () => {
    const { host } = mount({
      last: result({
        cleared_cycles: 2, total_cleared_amount: '10.00',
        cycles: [
          { cleared_amount: '7.00', edges: [{ from: 'alice', to: 'bob' }, { from: 'bob', to: 'alice' }] },
          { cleared_amount: '3.00', edges: [{ from: 'carol', to: 'alice' }, { from: 'alice', to: 'carol' }] },
        ],
      }),
    })
    await settle()

    expect(host.querySelectorAll('[data-testid="clearing-cycle"]').length).toBe(2)
    expect(text(host, 'clearing-cycles')).toContain('2')
    expect(Array.from(host.querySelectorAll('[data-testid="clearing-cycle-title"]')).map((e) => e.textContent!.replace(/\s+/g, ' ').trim()))
      .toEqual(['Cycle 1 of 2: 7.00 UAH', 'Cycle 2 of 2: 3.00 UAH'])
    expect(lines(host)).toEqual([
      'Bob’s debt to Alice reduced by 7.00 UAH', 'Alice’s debt to Bob reduced by 7.00 UAH',
      'Alice’s debt to Carol reduced by 3.00 UAH', 'Carol’s debt to Alice reduced by 3.00 UAH',
    ])
  })

  it('names come from the participants given; a participant the list does not know is shown by id, never guessed', async () => {
    const known = mount()
    await settle()
    expect(lines(known.host)[0]).toContain('Bob’s debt to Alice')
    document.body.innerHTML = ''

    const unknown = mount({ participants: [{ pid: 'alice', name: 'Alice', type: 'person', status: 'active' }] })
    await settle()
    expect(lines(unknown.host)[0]).toBe('bob’s debt to Alice reduced by 7.00 UAH')

    const none = mount({ participants: undefined })
    await settle()
    expect(lines(none.host)[0]).toBe('bob’s debt to alice reduced by 7.00 UAH')
  })

  it('shows no before -> after: the answer carries none', async () => {
    const { host } = mount()
    await settle()
    expect(host.textContent).not.toMatch(/→\s*\d|before|after|\d+\.\d+\s*→/i)
  })

  it('Close is there at once and is not blocked by a busy flag; it calls cancel', async () => {
    const { host, calls } = mount({ busy: true })
    await settle()
    const close = Array.from(host.querySelectorAll('button')).find((b) => (b.textContent ?? '').trim() === 'Close') as HTMLButtonElement
    expect(close).toBeTruthy()
    expect(close.disabled).toBe(false)
    close.click()
    expect(calls.cancel).toHaveBeenCalledTimes(1)
  })
})

describe('the states that are not a success', () => {
  it('zero cycles: says nothing was cleared, lists no cycle, and does not look like a success', async () => {
    const { host } = mount({ last: result({ cleared_cycles: 0, total_cleared_amount: '0.00', cycles: [] }) })
    await settle()

    expect(text(host, 'clearing-nothing')).toBe('Nothing was cleared: no cycle of debts was found.')
    expect(q(host, 'clearing-cycle')).toBeNull()
    expect(q(host, 'clearing-total')).toBeNull()
    expect(host.textContent).not.toMatch(/reduced by/)
  })

  it('a refusal (an error, no result): the error is shown, no "preparing" spinner and no result, and Close works', async () => {
    const { host, calls } = mount({ last: null, error: 'Clearing refused: the step is too small.' })
    await settle()

    expect(text(host, 'clearing-error')).toContain('Clearing refused')
    expect(q(host, 'clearing-cycle')).toBeNull()
    const close = Array.from(host.querySelectorAll('button')).find((b) => (b.textContent ?? '').trim() === 'Close') as HTMLButtonElement
    expect(close.disabled).toBe(false)
    close.click()
    expect(calls.cancel).toHaveBeenCalled()
  })

  it('while the answer is awaited: says it is running, shows no result, and Close waits', async () => {
    const { host } = mount({ last: null, busy: true })
    await settle()

    expect(text(host, 'clearing-running')).toContain('Running clearing')
    expect(q(host, 'clearing-cycle')).toBeNull()
    const close = Array.from(host.querySelectorAll('button')).find((b) => (b.textContent ?? '').trim() === 'Close') as HTMLButtonElement
    expect(close.disabled).toBe(true)
  })
})

describe('037 C fix-delta: honest states that do not depend on a toast', () => {
  it('a finished refusal keeps its text and a working Close after the transient error is gone', async () => {
    const { host, state, calls } = mount({ last: null, error: null })
    ;(state as unknown as { clearingFailure: string | null }).clearingFailure = 'Clearing refused: the step is too small. (request abc)'
    await settle()
    expect(text(host, 'clearing-error')).toContain('request abc')
    expect(q(host, 'clearing-running')).toBeNull()

    state.error = 'Clearing refused: the step is too small. (request abc)'
    await settle()
    state.error = null // the error toast expired and the application cleared it
    await settle()
    expect(text(host, 'clearing-error')).toContain('request abc')
    const close = Array.from(host.querySelectorAll('button')).find((b) => (b.textContent ?? '').trim() === 'Close') as HTMLButtonElement
    expect(close.disabled).toBe(false)
    close.click()
    expect(calls.cancel).toHaveBeenCalled()
  })

  it('"running" is shown only while a request is really in flight', async () => {
    const idle = mount({ last: null, busy: false })
    await settle()
    expect(q(idle.host, 'clearing-running')).toBeNull()
    document.body.innerHTML = ''
    const inFlight = mount({ last: null, busy: true })
    await settle()
    expect(q(inFlight.host, 'clearing-running')).not.toBeNull()
  })

  it('the equivalent of a held result is the equivalent of ITS answer, not the one selected now', async () => {
    const { host } = mount({ equivalent: 'USD', last: result({ equivalent: 'UAH' }) })
    await settle()
    expect(text(host, 'clearing-equivalent')).toContain('UAH')
    expect(text(host, 'clearing-equivalent')).not.toContain('USD')
    document.body.innerHTML = ''
    const confirm = mount({ phase: 'confirm-clearing', equivalent: 'USD', last: null })
    await settle()
    expect(text(confirm.host, 'clearing-equivalent'), 'before the run, the selected equivalent is what will be run').toContain('USD')
  })

  it('the total is the sum of the cycle amounts and says so: it is not the total of the debts written off', async () => {
    const { host } = mount()
    await settle()
    expect(text(host, 'clearing-total')).toContain('Total over cycles')
    expect(text(host, 'clearing-total')).not.toContain('Total cleared')
    expect(text(host, 'clearing-total-note')).toBe('Each debt in a cycle was reduced by the amount of that cycle; this total adds the cycle amounts, not the debts.')
  })
})
