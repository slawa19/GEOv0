/**
 * Programme 037, `T3701` - UI half of F-037-2: a manual payment has no idempotency key, yet the refusal text
 * tells the user to "send the same payment again".
 *
 * The backend half (two `payment-real` -> two payments, double debt, second `tx.updated`) is
 * `tests/integration/test_p037_manual_payment_idempotency_postgres.py`.
 *
 * NAMES ASSUMED, stated so a reader can tell a wrong name from a wrong behaviour: the option is spelled
 * `idempotencyKey` (mirrors the existing `clientActionId` option of `sendPayment`) and the wire field is
 * `idempotency_key` (the spec, "Идемпотентность"). If the fix names the option otherwise, rename it here; the
 * behaviour asserted does not change. `client_action_id` keeps its meaning (a correlation id) and must NOT
 * be reused as the key - the spec's T3700 decision - which is what the control at the bottom holds.
 *
 * What these do NOT see: the wizard's "Проверьте" step (where the spec puts key creation) does not exist; the
 * lifecycle is asserted at `useInteractMode.confirmPayment`, the only place a retry can happen today.
 */
import { computed, ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useInteractMode } from './useInteractMode'
import { useInteractActions } from './useInteractActions'
import { paymentRefusalText } from '../utils/paymentRefusalText'
import type { GraphSnapshot } from '../types'

type InteractActions = Parameters<typeof useInteractMode>[0]['actions']

afterEach(() => {
  vi.unstubAllGlobals()
})

async function settle() {
  for (let i = 0; i < 6; i += 1) await Promise.resolve()
}

function mkMode(sendPayment: InteractActions['sendPayment']) {
  window.history.replaceState({}, '', '/?mode=real&ui=interact')
  const actions = {
    actionsDisabled: ref(false),
    sendPayment,
    createTrustline: vi.fn(),
    updateTrustline: vi.fn(),
    closeTrustline: vi.fn(),
    runClearing: vi.fn(),
    fetchParticipants: vi.fn(async () => []),
    fetchTrustlines: vi.fn(async () => []),
    fetchPaymentTargets: vi.fn(async () => []),
  } as unknown as InteractActions
  const im = useInteractMode({
    actions,
    runId: computed(() => 'run_p037'),
    equivalent: computed(() => 'UAH'),
    snapshot: ref<GraphSnapshot | null>(null),
  })
  im.startPaymentFlow()
  im.setPaymentFromPid('alice')
  im.setPaymentToPid('bob')
  return im
}

type SendOpts = { clientActionId?: string; signal?: AbortSignal; idempotencyKey?: string } | undefined
const optsOf = (fn: ReturnType<typeof vi.fn>, call: number): SendOpts => fn.mock.calls[call]?.[4] as SendOpts

const OK = {
  ok: true as const,
  payment_id: 'pay_1',
  from_pid: 'alice',
  to_pid: 'bob',
  equivalent: 'UAH',
  amount: '5.00',
  status: 'COMMITTED',
  routes: [],
}

describe('F-037-2 (UI): the key of a confirmed intent survives an unknown outcome', () => {
  it('REPRODUCER (red now): after a timeout the retry of the SAME intent carries the SAME non-empty idempotency key', async () => {
    const send = vi
      .fn<InteractActions['sendPayment']>()
      .mockRejectedValueOnce({ status: 503, code: 'ENGINE_TIMEOUT', message: 'Engine timeout', details: { reason: 'timeout' } })
      .mockResolvedValueOnce(OK)
    const im = mkMode(send)
    await settle()

    await im.confirmPayment('5.00') // outcome unknown (timeout)
    expect(im.state.error, 'precondition: the first attempt ended in a refusal shown to the user').toBeTruthy()
    await im.confirmPayment('5.00') // the user retries the same intent
    expect(send, 'precondition: both attempts reached sendPayment').toHaveBeenCalledTimes(2)

    const first = optsOf(send, 0)?.idempotencyKey
    const second = optsOf(send, 1)?.idempotencyKey
    expect(first, 'F-037-2: the first attempt has no idempotency key').toBeTruthy()
    expect(second, 'F-037-2: the retry has no idempotency key - a second payment would be created').toBe(first)
  })

  it('REPRODUCER (red now): a changed intent (another amount) gets another key', async () => {
    const send = vi
      .fn<InteractActions['sendPayment']>()
      .mockRejectedValueOnce({ status: 503, code: 'ENGINE_TIMEOUT', message: 'Engine timeout', details: { reason: 'timeout' } })
      .mockResolvedValueOnce(OK)
    const im = mkMode(send)
    await settle()

    await im.confirmPayment('5.00')
    await im.confirmPayment('6.00')
    expect(send).toHaveBeenCalledTimes(2)
    const first = optsOf(send, 0)?.idempotencyKey
    const second = optsOf(send, 1)?.idempotencyKey
    expect(first, 'precondition: the first attempt has a key at all').toBeTruthy()
    expect(second, 'precondition: the second attempt has a key at all').toBeTruthy()
    expect(second).not.toBe(first)
  })

  it('REPRODUCER (red now): sendPayment puts idempotency_key on the wire body', async () => {
    const bodies: Array<Record<string, unknown>> = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: unknown, init?: RequestInit) => {
        bodies.push(JSON.parse(String(init?.body ?? '{}')))
        return new Response(JSON.stringify(OK), { status: 200, headers: { 'content-type': 'application/json' } })
      }),
    )
    const ia = useInteractActions({
      httpConfig: ref({ apiBase: 'http://example.test', accessToken: 'x' }),
      runId: ref('run_p037'),
    })

    await ia.sendPayment('alice', 'bob', '5.00', 'UAH', { clientActionId: 'c1', idempotencyKey: 'k1' } as never)

    expect(bodies, 'precondition: one request').toHaveLength(1)
    expect(bodies[0]!.client_action_id, 'precondition: correlation id still goes out').toBe('c1')
    expect(bodies[0]!.idempotency_key, 'F-037-2: the key never reaches the wire').toBe('k1')
  })

  it('CONTROL (green now): the retry path exists - a second confirm of the same intent reaches sendPayment again', async () => {
    const send = vi
      .fn<InteractActions['sendPayment']>()
      .mockRejectedValueOnce({ status: 503, code: 'ENGINE_TIMEOUT', message: 'Engine timeout', details: { reason: 'timeout' } })
      .mockResolvedValueOnce(OK)
    const im = mkMode(send)
    await settle()
    await im.confirmPayment('5.00')
    await im.confirmPayment('5.00')
    expect(send).toHaveBeenCalledTimes(2)
    expect(send.mock.calls[1]!.slice(0, 4)).toEqual(['alice', 'bob', '5.00', 'UAH'])
  })

  it('CONTROL (green now): client_action_id stays a correlation id - without a key it still differs per attempt', async () => {
    // Anti-vacuum for the decision "do not reuse client_action_id as the key": if the fix quietly turned it into
    // the key (same value on retry) this control would keep passing while the key tests above are what protect.
    const bodies: Array<Record<string, unknown>> = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: unknown, init?: RequestInit) => {
        bodies.push(JSON.parse(String(init?.body ?? '{}')))
        return new Response(JSON.stringify(OK), { status: 200, headers: { 'content-type': 'application/json' } })
      }),
    )
    const ia = useInteractActions({
      httpConfig: ref({ apiBase: 'http://example.test', accessToken: 'x' }),
      runId: ref('run_p037'),
    })
    await ia.sendPayment('alice', 'bob', '5.00', 'UAH')
    await ia.sendPayment('alice', 'bob', '5.00', 'UAH')
    expect(bodies[0]!.client_action_id).toBeTruthy()
    expect(bodies[1]!.client_action_id).not.toBe(bodies[0]!.client_action_id)
  })
})

describe('F-037-2 (UI): "send the same payment again" is advice that needs a key', () => {
  const busy = { status: 409, code: 'CONFLICT', message: 'busy', details: { reason: 'busy' } }

  it('REPRODUCER (red now): with no key in play the busy refusal does not tell the user to repeat the payment', () => {
    // Today the UI never sends a key, so this is the only situation there is: a repeat is a SECOND payment.
    const en = paymentRefusalText(busy, 'UAH', 'en')
    const ru = paymentRefusalText(busy, 'UAH', 'ru')
    expect(en, 'F-037-2 (en): advises a repeat that creates a second payment').not.toMatch(/again|retry|repeat/i)
    expect(ru, 'F-037-2 (ru): advises a repeat that creates a second payment').not.toMatch(/ещё раз|повтор/i)
  })

  it('CONTROL (green now): the busy refusal is still recognised and worded (the reader is not vacuous)', () => {
    const en = paymentRefusalText(busy, 'UAH', 'en')
    expect(en).toMatch(/busy/i)
    expect(paymentRefusalText({ ...busy, details: { reason: 'timeout' } }, 'UAH', 'en')).toMatch(/timed out/i)
  })
})
