/**
 * BACKLOG 049-8 (049 decision 5, «смена прогона или эквивалента очищает строки»): a late own line. A manual action begun in
 * one context (run, equivalent) and answered after the context changed - the history of the old context is cleared by the
 * change - must not write its line into the new context's history. What the action itself shows and accounts for (the
 * window's result, the payment's record) is not the history's and stays as it was.
 *
 * Red on `main` (`178ca192`): the answer of the UAH payment wrote «Payment 1.00 UAH» over the EUR history; `isCurrent()`
 * checked only the operation's epoch.
 */
import { computed, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { GraphSnapshot } from '../types'
import { historyEntryText } from './interact/useInteractHistory'
import { useInteractMode } from './useInteractMode'

type Actions = Parameters<typeof useInteractMode>[0]['actions']

const snap = (eq: string): GraphSnapshot => ({ equivalent: eq, generated_at: 'x',
  nodes: [{ id: 'alice', name: 'Alice' }, { id: 'bob', name: 'Bob' }],
  links: [{ source: 'alice', target: 'bob', used: '1.00', trust_limit: '10.00', available: '9.00', status: 'active' }] })

const COMMITTED = { ok: true, status: 'COMMITTED', payment_id: 'p1', routes: [] }

function actions(over: Partial<Actions> = {}): Actions {
  const fail = async () => { throw new Error('not used') }
  return {
    actionsDisabled: ref(false), sendPayment: fail, createTrustline: fail, updateTrustline: fail,
    closeTrustline: fail, runClearing: fail, fetchParticipants: async () => [],
    fetchTrustlines: async () => null as never, fetchPaymentTargets: async () => [], ...over,
  } as Actions
}

/** A request whose answer the test releases. */
function held<T>() {
  let release!: (v: T) => void
  const fn = vi.fn(() => new Promise<T>((r) => { release = r }))
  return { fn, release: (v: T) => release(v) }
}

function mode(over: Partial<Actions>) {
  const eq = ref('UAH')
  const runId = ref('run_1')
  const im = useInteractMode({ actions: actions(over), runId: computed(() => runId.value), equivalent: eq, snapshot: ref(snap('UAH')) })
  const lines = () => im.history.map((e) => historyEntryText(e, 'en'))
  return { im, eq, runId, lines }
}

function pay(im: ReturnType<typeof useInteractMode>, amount = '1.00') {
  im.startPaymentFlow(); im.selectNode('alice'); im.selectNode('bob')
  return im.confirmPayment(amount)
}

beforeEach(() => {
  window.sessionStorage.clear()
})

describe('049-8: an answer that comes after a context change writes no line into the new context', () => {
  it('a payment in UAH, EUR chosen before the answer: no line over EUR; the window still shows the UAH result', async () => {
    const send = held<never>()
    const { im, eq, lines } = mode({ sendPayment: send.fn as unknown as Actions['sendPayment'] })
    const done = pay(im)
    eq.value = 'EUR'
    send.release(COMMITTED as never); await done

    expect(send.fn, 'precondition: the payment left in the equivalent of the action').toHaveBeenCalledWith('alice', 'bob', '1.00', 'UAH', expect.anything())
    expect(lines(), 'the UAH answer writes nothing over the EUR history').toEqual([])
    expect(im.paymentOutcome.value, 'counter-check: the window shows the result, in the equivalent of the action')
      .toMatchObject({ kind: 'success', amount: '1.00', equivalent: 'UAH', fromPid: 'alice', toPid: 'bob' })
  })

  it('counter-check: the answer in the SAME context writes its one line', async () => {
    const send = held<never>()
    const { im, lines } = mode({ sendPayment: send.fn as unknown as Actions['sendPayment'] })
    const done = pay(im)
    send.release(COMMITTED as never); await done
    expect(lines()).toEqual(['Payment 1.00 UAH: Alice → Bob'])
    expect(im.paymentOutcome.value).toMatchObject({ kind: 'success', equivalent: 'UAH' })
  })

  it('another run before the answer: no line in the new run; the window still shows the result', async () => {
    const send = held<never>()
    const { im, runId, lines } = mode({ sendPayment: send.fn as unknown as Actions['sendPayment'] })
    const done = pay(im)
    runId.value = 'run_2'
    send.release(COMMITTED as never); await done
    expect(lines(), 'the old run\'s answer writes nothing into the new run').toEqual([])
    expect(im.paymentOutcome.value, 'counter-check: the window keeps the result').toMatchObject({ kind: 'success', amount: '1.00' })
  })

  it('another picture before the answer (a stopped run, another scenario; the run id and the equivalent stay): no line over it', async () => {
    const send = held<never>()
    const source = ref('run:run_1')
    const im = useInteractMode({ actions: actions({ sendPayment: send.fn as unknown as Actions['sendPayment'] }),
      runId: computed(() => 'run_1'), equivalent: ref('UAH'), snapshot: ref(snap('UAH')), sceneSource: () => source.value })
    const done = pay(im)
    source.value = 'preview:sc2'
    send.release(COMMITTED as never); await done
    expect(im.history.map((e) => historyEntryText(e, 'en'))).toEqual([])
    expect(im.paymentOutcome.value, 'counter-check: the window keeps the result').toMatchObject({ kind: 'success' })
  })

  it('EUR and back to UAH before the answer: the history was cleared since the payment began - the old line does not come back', async () => {
    const send = held<never>()
    const { im, eq, lines } = mode({ sendPayment: send.fn as unknown as Actions['sendPayment'] })
    const done = pay(im)
    eq.value = 'EUR'
    eq.value = 'UAH'
    send.release(COMMITTED as never); await done
    expect(lines()).toEqual([])
  })

  it('the repeat of an unresolved UAH payment while EUR is on screen: the result is shown, the line is not written over EUR', async () => {
    const send = vi.fn<Actions['sendPayment']>()
      .mockRejectedValueOnce({ status: 503, code: 'ENGINE_TIMEOUT', message: 'Engine timeout', details: { reason: 'timeout' }, outcomeUnknown: true })
      .mockResolvedValueOnce(COMMITTED as never)
    const { im, eq, lines } = mode({ sendPayment: send })
    await pay(im)
    expect(im.paymentOutcome.value, 'precondition: the first attempt has no verdict').toMatchObject({ kind: 'unknown', equivalent: 'UAH' })
    eq.value = 'EUR'
    await im.retryPayment()
    expect(send, 'precondition: the repeat went, in its own equivalent').toHaveBeenLastCalledWith('alice', 'bob', '1.00', 'UAH', expect.anything())
    expect(lines(), 'a UAH line is not written under EUR').toEqual([])
    expect(im.paymentOutcome.value, 'counter-check: the window shows the result').toMatchObject({ kind: 'success', equivalent: 'UAH' })
  })

  it('counter-check: the repeat of an unresolved payment in its own equivalent writes its line', async () => {
    const send = vi.fn<Actions['sendPayment']>()
      .mockRejectedValueOnce({ status: 503, code: 'ENGINE_TIMEOUT', message: 'Engine timeout', details: { reason: 'timeout' }, outcomeUnknown: true })
      .mockResolvedValueOnce(COMMITTED as never)
    const { im, lines } = mode({ sendPayment: send })
    await pay(im)
    await im.retryPayment()
    expect(lines()).toEqual(['Payment 1.00 UAH: Alice → Bob'])
  })

  it('the same for the other actions: a clearing and a new trust line answered after an equivalent switch write no line', async () => {
    const clearing = held<never>()
    const created = held<never>()
    const { im, eq, lines } = mode({
      runClearing: clearing.fn as unknown as Actions['runClearing'],
      createTrustline: created.fn as unknown as Actions['createTrustline'],
    })
    im.startClearingFlow()
    const cleared = im.confirmClearing()
    eq.value = 'EUR'
    clearing.release({ ok: true, cleared_cycles: 1, total_cleared_amount: '3.00', cycles: [] } as never); await cleared
    expect(lines(), 'clearing').toEqual([])
    expect(im.state.lastClearing, 'counter-check: the clearing window has its result').toMatchObject({ cleared_cycles: 1 })

    im.cancel()
    im.startTrustlineFlow(); im.selectNode('alice'); im.selectNode('bob')
    const made = im.confirmTrustlineCreate('5.00')
    eq.value = 'UAH'
    created.release({ ok: true } as never); await made
    expect(lines(), 'trust line').toEqual([])
  })
})
