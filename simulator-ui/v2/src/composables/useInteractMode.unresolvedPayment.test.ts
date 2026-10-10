/**
 * 037 A2 fix-delta: a payment whose outcome is UNKNOWN stays the one payment of the screen until a person resolves it.
 *
 * The invariant (arbiter S2): the key and the frozen intent are held while the outcome of the original payment is unknown;
 * an unresolved intent is never moved by anything automatic. Observed here as the REQUESTS the server would receive: after an
 * unknown first attempt, no request may leave with another key or another body - it either does not leave, or it is the same
 * key and the same body (a repeat the server answers from the stored payment).
 *
 * `sendPayment` is the only stand-in. The four entries below are the ones the adversarial pass executed against `80170b08`.
 */
import { computed, nextTick, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { GraphSnapshot } from '../types'
import { useInteractMode } from './useInteractMode'

type Actions = Parameters<typeof useInteractMode>[0]['actions']
type Send = ReturnType<typeof vi.fn>

const COMMITTED = {
  ok: true as const, payment_id: 'man:0123', from_pid: 'alice', to_pid: 'bob', equivalent: 'UAH', amount: '10.00',
  status: 'COMMITTED', routes: [] as Array<{ hops: Array<{ from: string; to: string; amount: string }> }>,
}
const unknown = () => ({ status: 0, code: 'UNKNOWN', message: 'Failed to fetch', details: null, outcomeUnknown: true })
const refusal = (status: number, code: string, details: Record<string, unknown> = {}) => ({
  status, code, message: code, details, outcomeUnknown: false,
})

function snapshot(): GraphSnapshot {
  return {
    equivalent: 'UAH', generated_at: '2026-01-01T00:00:00Z',
    nodes: ['alice', 'bob', 'carol'].map((id) => ({ id, name: id[0]!.toUpperCase() + id.slice(1), type: 'person', status: 'active' })),
    links: [{ source: 'bob', target: 'alice', used: '0.00', available: '100.00', status: 'active' }],
  }
}

function mode(sendPayment: Send, runId = ref('run_1')) {
  window.history.replaceState({}, '', '/?mode=real&ui=interact')
  const actions = {
    actionsDisabled: ref(false), sendPayment,
    createTrustline: vi.fn(), updateTrustline: vi.fn(), closeTrustline: vi.fn(), runClearing: vi.fn(),
    fetchParticipants: vi.fn(async () => ['alice', 'bob', 'carol'].map((pid) => ({
      pid, name: pid[0]!.toUpperCase() + pid.slice(1), type: 'person', status: 'active',
    }))),
    fetchTrustlines: vi.fn(async () => []),
    fetchPaymentTargets: vi.fn(async () => [{ to_pid: 'bob', hops: 1, max_available: '100.00' }]),
  } as unknown as Actions
  const im = useInteractMode({ actions, runId: computed(() => runId.value), equivalent: computed(() => 'UAH'), snapshot: ref(snapshot()) })
  return { im, runId }
}

async function settle() {
  for (let i = 0; i < 6; i += 1) await Promise.resolve()
  await nextTick()
}

async function atConfirm(send: Send) {
  const ctx = mode(send)
  ctx.im.startPaymentFlow()
  ctx.im.selectNode('alice')
  ctx.im.selectNode('bob')
  await settle()
  return ctx
}

const keyOf = (fn: Send, call: number): string | undefined => (fn.mock.calls[call]![4] as { idempotencyKey?: string }).idempotencyKey
const amountOf = (fn: Send, call: number): string => String(fn.mock.calls[call]![2])

/** The invariant: every request after the first one carries the first one's key AND body. */
function expectNoOtherPaymentThanTheFirst(fn: Send) {
  const sent = fn.mock.calls.map((_c, i) => ({ key: keyOf(fn, i), body: fn.mock.calls[i]!.slice(0, 4) }))
  for (const s of sent.slice(1)) expect(s, 'a request left with another key or another body than the unresolved payment').toEqual(sent[0])
}

beforeEach(() => {
  window.sessionStorage.clear()
})

describe('an unknown outcome is never replaced by another payment', () => {
  it('ENTRY 1: the amount is re-typed in another spelling ("10.00" -> "10.0") and Confirm is pressed', async () => {
    const send = vi.fn().mockRejectedValueOnce(unknown()).mockResolvedValue(COMMITTED)
    const { im } = await atConfirm(send)

    await im.confirmPayment('10.00')
    await im.confirmPayment('10.0')

    expectNoOtherPaymentThanTheFirst(send)
    expect(im.paymentOutcome.value?.kind, 'the unresolved payment must stay on screen').toBe('unknown')
  })

  it('ENTRY 2: another amount is confirmed (and refused), then the original amount again', async () => {
    const send = vi.fn()
      .mockRejectedValueOnce(unknown())
      .mockRejectedValueOnce(refusal(409, 'NO_ROUTE', { reason: 'no_route' }))
      .mockResolvedValue(COMMITTED)
    const { im } = await atConfirm(send)

    await im.confirmPayment('10.00')
    await im.confirmPayment('20.00')
    expect(im.paymentOutcome.value?.kind, 'the banner must not vanish because of a refusal of ANOTHER payment').toBe('unknown')
    await im.confirmPayment('10.00')

    expectNoOtherPaymentThanTheFirst(send)
  })

  it('ENTRY 3: the panel is closed while the request is in flight and the request ends unknown', async () => {
    const send = vi.fn()
      .mockImplementationOnce((_f, _t, _a, _e, o: { signal: AbortSignal }) => new Promise((_resolve, reject) => {
        o.signal.addEventListener('abort', () => reject(unknown()))
      }))
      .mockResolvedValue(COMMITTED)
    const { im } = await atConfirm(send)

    const pending = im.confirmPayment('10.00')
    im.cancel()
    await pending

    expect(im.paymentOutcome.value?.kind, 'a request closed in flight is an unresolved payment: it must be visible when the panel opens').toBe('unknown')
    im.startPaymentFlow(); im.selectNode('alice'); im.selectNode('bob')
    await settle()
    expect(im.paymentOutcome.value?.kind).toBe('unknown')
    await im.confirmPayment('10.0')
    expectNoOtherPaymentThanTheFirst(send)
  })

  it('ENTRY 4: the composable is created again (the page was left and came back) - the unresolved payment is still there', async () => {
    const send = vi.fn().mockRejectedValueOnce(unknown()).mockResolvedValue(COMMITTED)
    const first = await atConfirm(send)
    await first.im.confirmPayment('10.00')

    const again = await atConfirm(send)

    expect(again.im.paymentOutcome.value?.kind, 'the unresolved payment is restored after a re-creation').toBe('unknown')
    await again.im.confirmPayment('10.0')
    expectNoOtherPaymentThanTheFirst(send)
  })
})
