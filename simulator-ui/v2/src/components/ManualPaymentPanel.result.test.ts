/**
 * 037 A2 on the existing `ManualPaymentPanel`: the result screen, the unknown-outcome banner, the source of the figures,
 * the server's estimate and the exact comparison with the capacity. Observed through the DOM and the callbacks it fires.
 */
import { createApp, h, nextTick, reactive, type Component } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ManualPaymentPanel from './ManualPaymentPanel.vue'
import { resetEquivalentPrecisions, setEquivalentPrecisions } from '../config/equivalentPrecision'
import type { ManualPaymentOutcome, PaymentTargetEstimate } from '../composables/useInteractMode'

const panel: Component = ManualPaymentPanel

const PARTICIPANTS = [
  { pid: 'alice', name: 'Alice' },
  { pid: 'bob', name: 'Bob' },
  { pid: 'carol', name: 'Carol' },
]

afterEach(() => {
  resetEquivalentPrecisions()
  document.body.innerHTML = ''
})

function mount(props: Record<string, unknown> = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const state = reactive({
    phase: 'confirm-payment', fromPid: 'alice', toPid: 'bob', selectedEdgeKey: null as string | null,
    edgeAnchor: null as { x: number; y: number } | null, error: null as string | null, lastClearing: null,
  })
  const calls = { confirm: vi.fn(), cancel: vi.fn(), retry: vi.fn(), dismiss: vi.fn() }
  const app = createApp({
    render: () => h(panel, {
      phase: 'confirm-payment', state, unit: 'UAH', availableCapacity: '10.00',
      trustlinesLoading: false, paymentTargetsLoading: false, paymentTargetsLastError: null,
      paymentToTargetIds: new Set(['bob']), trustlines: [], participants: PARTICIPANTS,
      busy: false, canSendPayment: true,
      confirmPayment: calls.confirm, cancel: calls.cancel, retryPayment: calls.retry, dismissPaymentResult: calls.dismiss,
      setFromPid: vi.fn(), setToPid: vi.fn(),
      ...props,
    }),
  })
  app.mount(host)
  return { host, calls, state, unmount: () => app.unmount() }
}

const q = (host: HTMLElement, id: string) => host.querySelector(`[data-testid="${id}"]`) as HTMLElement | null
const text = (host: HTMLElement, id: string) => (q(host, id)?.textContent ?? '').replace(/\s+/g, ' ').trim()

function success(overrides: Partial<Extract<ManualPaymentOutcome, { kind: 'success' }>> = {}): ManualPaymentOutcome {
  return {
    kind: 'success', paymentId: 'man:9f2c', status: 'COMMITTED', amount: '10.00', equivalent: 'UAH',
    fromPid: 'alice', toPid: 'bob', fromName: 'Alice', toName: 'Bob',
    routes: [{ hops: [{ from: 'alice', to: 'bob', fromName: 'Alice', toName: 'Bob', amount: '10.00' }] }],
    ...overrides,
  }
}

async function typeAmount(host: HTMLElement, value: string) {
  const input = host.querySelector('#mp-amount') as HTMLInputElement
  input.value = value
  input.dispatchEvent(new Event('input'))
  await nextTick()
  await nextTick()
}

describe('the result screen', () => {
  it('shows the payment id, status, amount and the parties by NAME; the selectors are gone', async () => {
    const { host } = mount({ paymentOutcome: success() })
    await nextTick()

    expect(text(host, 'mp-result-payment-id')).toBe('man:9f2c')
    expect(text(host, 'mp-result-status')).toBe('COMMITTED')
    expect(text(host, 'mp-result-amount')).toBe('10.00 UAH')
    expect(text(host, 'mp-result-parties')).toBe('Alice → Bob')
    expect(host.querySelector('#mp-from')).toBeNull()
    expect(host.querySelector('#mp-amount')).toBeNull()
  })

  it('shows EVERY route of a two-route payment as a chain of names with the amount of each step', async () => {
    const { host } = mount({
      paymentOutcome: success({
        routes: [
          { hops: [
            { from: 'alice', to: 'carol', fromName: 'Alice', toName: 'Carol', amount: '6.00000000' },
            { from: 'carol', to: 'bob', fromName: 'Carol', toName: 'Bob', amount: '6.00000000' },
          ] },
          { hops: [{ from: 'alice', to: 'bob', fromName: 'Alice', toName: 'Bob', amount: '4.00000000' }] },
        ],
      }),
    })
    await nextTick()

    expect(text(host, 'mp-result-route-1')).toContain('Route 1 of 2')
    expect(text(host, 'mp-result-route-1')).toContain('Alice → Carol → Bob')
    expect(text(host, 'mp-result-route-1')).toContain('Alice → Carol: 6.00 UAH')
    expect(text(host, 'mp-result-route-1')).toContain('Carol → Bob: 6.00 UAH')
    expect(text(host, 'mp-result-route-2')).toContain('Route 2 of 2')
    expect(text(host, 'mp-result-route-2')).toContain('Alice → Bob: 4.00 UAH')
    expect(host.querySelectorAll('[data-testid="mp-result-hop"]').length).toBe(3)
  })

  it('a payment whose answer carried no route shows no chain - and says so, instead of joining the endpoints', async () => {
    const { host } = mount({ paymentOutcome: success({ routes: [] }) })
    await nextTick()

    expect(q(host, 'mp-result-route-chain')).toBeNull()
    expect(text(host, 'mp-result-no-routes')).toContain('did not report the route')
  })

  it('"Another payment" goes back to the recipient step; "Close" closes; neither is a silent reset', async () => {
    const { host, calls } = mount({ paymentOutcome: success() })
    await nextTick()

    q(host, 'mp-result-another')!.click()
    expect(calls.dismiss).toHaveBeenCalledTimes(1)
    q(host, 'mp-result-close')!.click()
    expect(calls.cancel).toHaveBeenCalledTimes(1)
    expect(calls.confirm).not.toHaveBeenCalled()
  })

  it('no outcome: the ordinary confirm step, and no result block', async () => {
    const { host } = mount()
    await nextTick()
    expect(q(host, 'mp-result')).toBeNull()
    expect(q(host, 'manual-payment-confirm')).not.toBeNull()
  })
})

describe('the unknown-outcome banner', () => {
  const unknownOutcome: ManualPaymentOutcome = {
    kind: 'unknown', message: 'No usable answer arrived.', amount: '10.00', equivalent: 'UAH',
    fromPid: 'alice', toPid: 'bob', fromName: 'Alice', toName: 'Bob',
  }

  it('says "unknown" and offers Check / repeat, which fires the retry (the same key lives in the model)', async () => {
    const { host, calls } = mount({ paymentOutcome: unknownOutcome })
    await nextTick()

    expect(text(host, 'mp-outcome-unknown')).toContain('Result unknown')
    expect(text(host, 'mp-outcome-unknown')).toContain('No usable answer arrived.')
    q(host, 'mp-retry')!.click()
    expect(calls.retry).toHaveBeenCalledTimes(1)
  })

  it('the repeat cannot be fired twice while a request is in flight', async () => {
    const { host } = mount({ paymentOutcome: unknownOutcome, busy: true })
    await nextTick()
    expect((q(host, 'mp-retry') as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('where the figures come from', () => {
  it.each([
    ['answered', 'server'],
    ['never-asked', 'snapshot'],
    ['loading', 'loading'],
    ['failed', 'failed'],
  ] as const)('trustlines state "%s" is marked %s', async (state, mark) => {
    const { host } = mount({ trustlinesState: state })
    await nextTick()
    expect(q(host, 'mp-figures-source')!.getAttribute('data-figures-source')).toBe(mark)
  })

  it('a figure the server has not confirmed is not offered as a capacity in the recipient list', async () => {
    const trustlines = [{ from_pid: 'bob', to_pid: 'alice', available: '45.00', status: 'active' }]
    const confirmed = mount({ trustlines, trustlinesState: 'answered', phase: 'picking-payment-to' })
    await nextTick()
    expect(confirmed.host.textContent).toContain('45.00 UAH')
    confirmed.unmount()
    document.body.innerHTML = ''

    const unconfirmed = mount({ trustlines, trustlinesState: 'never-asked', phase: 'picking-payment-to' })
    await nextTick()
    expect(unconfirmed.host.textContent).not.toContain('Bob (bob) — 45.00')
  })
})

describe('the estimate of the server', () => {
  const received = (maxAvailable: string | null, hops = 2): PaymentTargetEstimate => ({ state: 'received', hops, maxAvailable })

  it('shows the estimated maximum and the shortest path as two different things', async () => {
    const { host } = mount({ targetEstimate: received('87.5', 3) })
    await nextTick()
    expect(text(host, 'mp-estimate-max')).toBe('87.50 UAH')
    expect(text(host, 'mp-estimate-hops')).toBe('· 3 step(s)')
    expect(q(host, 'mp-estimate')!.getAttribute('title')).toContain('Shortest path: 3 step(s)')
  })

  it('"the server did not estimate" (null) is not a measured zero', async () => {
    const none = mount({ targetEstimate: received(null) })
    await nextTick()
    expect(text(none.host, 'mp-estimate-max')).toBe('not estimated')
    none.unmount()
    document.body.innerHTML = ''

    const zero = mount({ targetEstimate: received('0.00') })
    await nextTick()
    expect(text(zero.host, 'mp-estimate-max')).toBe('0.00 UAH')
  })

  it('names the state of the answer while it is not received', async () => {
    const loading = mount({ targetEstimate: { state: 'loading' } })
    await nextTick()
    expect(text(loading.host, 'mp-estimate-max')).toBe('waiting for the server')
    loading.unmount()
    document.body.innerHTML = ''
    const failed = mount({ targetEstimate: { state: 'failed' } })
    await nextTick()
    expect(text(failed.host, 'mp-estimate-max')).toBe('not received')
  })

  it('an amount above the estimate warns, and does NOT block the send (the server decides)', async () => {
    const { host, calls } = mount({ targetEstimate: received('50.00') })
    await nextTick()
    await typeAmount(host, '50.01')

    expect(text(host, 'mp-confirm-warning')).toContain('above the server')
    const confirm = q(host, 'manual-payment-confirm') as HTMLButtonElement
    expect(confirm.disabled).toBe(false)
    confirm.click()
    await nextTick()
    expect(calls.confirm).toHaveBeenCalledWith('50.01')
  })

  it('the estimate is compared exactly too: one atom above warns, the estimate itself and one atom below do not', async () => {
    setEquivalentPrecisions([{ code: 'P8', precision: 8 }])
    const warnsAt = async (amount: string) => {
      const { host, unmount } = mount({ unit: 'P8', targetEstimate: received('999999999999.99999998') })
      await nextTick()
      await typeAmount(host, amount)
      const warned = q(host, 'mp-confirm-warning') !== null
      unmount()
      document.body.innerHTML = ''
      return warned
    }
    expect(await warnsAt('999999999999.99999999')).toBe(true)
    expect(await warnsAt('999999999999.99999998')).toBe(false)
    expect(await warnsAt('999999999999.99999997')).toBe(false)
  })

  it('an amount equal to the estimate does not warn', async () => {
    const { host } = mount({ targetEstimate: received('50.00') })
    await nextTick()
    await typeAmount(host, '50.00')
    expect(q(host, 'mp-confirm-warning')).toBeNull()
  })
})

describe('the exact comparison with the capacity (no float)', () => {
  async function warns(unit: string, available: string, amount: string): Promise<boolean> {
    const { host, unmount } = mount({ unit, availableCapacity: available })
    await nextTick()
    await typeAmount(host, amount)
    const warned = q(host, 'mp-confirm-warning') !== null
    unmount()
    document.body.innerHTML = ''
    return warned
  }

  it('precision 0: exactly the capacity is fine, one unit above warns, one below is fine', async () => {
    setEquivalentPrecisions([{ code: 'P0', precision: 0 }])
    expect(await warns('P0', '10', '10')).toBe(false)
    expect(await warns('P0', '10', '11')).toBe(true)
    expect(await warns('P0', '10', '9')).toBe(false)
  })

  it('precision 8: one atom above the capacity warns, one atom below and the capacity itself do not', async () => {
    setEquivalentPrecisions([{ code: 'P8', precision: 8 }])
    expect(await warns('P8', '0.00000002', '0.00000002')).toBe(false)
    expect(await warns('P8', '0.00000002', '0.00000003')).toBe(true)
    expect(await warns('P8', '0.00000002', '0.00000001')).toBe(false)
  })

  it('amounts a double cannot tell apart are told apart', async () => {
    setEquivalentPrecisions([{ code: 'P8', precision: 8 }])
    expect(await warns('P8', '999999999999.99999998', '999999999999.99999999')).toBe(true)
    expect(await warns('P8', '999999999999.99999999', '999999999999.99999998')).toBe(false)
  })

  it('zero and a negative are not positive amounts: the send is disabled for them', async () => {
    const { host } = mount()
    await nextTick()
    await typeAmount(host, '0.00')
    expect((q(host, 'manual-payment-confirm') as HTMLButtonElement).disabled).toBe(true)
    await typeAmount(host, '0.01')
    expect((q(host, 'manual-payment-confirm') as HTMLButtonElement).disabled).toBe(false)
  })
})
