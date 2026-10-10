/**
 * 037 A2: the life of a manual payment in `useInteractMode` - the idempotency key of an intent, what an unknown outcome
 * keeps, what a refusal or a success ends, and what is on screen. `sendPayment` is the only stand-in: the observable is
 * the request it receives (sender, receiver, amount spelling, key) and the state the panel reads.
 */
import { computed, nextTick, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { GraphSnapshot } from '../types'
import { useInteractMode } from './useInteractMode'

// An unresolved payment is kept in `sessionStorage` across a re-created composable; each test starts from none.
beforeEach(() => {
  window.sessionStorage.clear()
})

type Actions = Parameters<typeof useInteractMode>[0]['actions']
type SendArgs = Parameters<Actions['sendPayment']>

const COMMITTED = {
  ok: true as const,
  payment_id: 'man:0123',
  from_pid: 'alice',
  to_pid: 'bob',
  equivalent: 'UAH',
  amount: '10.00',
  status: 'COMMITTED',
  routes: [] as Array<{ hops: Array<{ from: string; to: string; amount: string }> }>,
}

const unknown = () => ({ status: 0, code: 'UNKNOWN', message: 'Failed to fetch', details: null, outcomeUnknown: true })
const refusal = (status: number, code: string, details: Record<string, unknown> = {}) => ({
  status, code, message: code, details, outcomeUnknown: false,
})

function snapshot(): GraphSnapshot {
  return {
    equivalent: 'UAH',
    generated_at: '2026-01-01T00:00:00Z',
    nodes: ['alice', 'bob', 'carol'].map((id) => ({ id, name: id[0]!.toUpperCase() + id.slice(1), type: 'person', status: 'active' })),
    links: [{ source: 'bob', target: 'alice', used: '0.00', available: '100.00', status: 'active' }],
  }
}

function setup(sendPayment: ReturnType<typeof vi.fn>) {
  window.history.replaceState({}, '', '/?mode=real&ui=interact')
  const actions = {
    actionsDisabled: ref(false),
    sendPayment,
    createTrustline: vi.fn(), updateTrustline: vi.fn(), closeTrustline: vi.fn(), runClearing: vi.fn(),
    fetchParticipants: vi.fn(async () => ['alice', 'bob', 'carol'].map((pid) => ({
      pid, name: pid[0]!.toUpperCase() + pid.slice(1), type: 'person', status: 'active',
    }))),
    fetchTrustlines: vi.fn(async () => []),
    fetchPaymentTargets: vi.fn(async () => [{ to_pid: 'bob', hops: 1, max_available: '100.00' }, { to_pid: 'carol', hops: 2, max_available: null }]),
  } as unknown as Actions
  const im = useInteractMode({
    actions, runId: computed(() => 'run_1'), equivalent: computed(() => 'UAH'), snapshot: ref(snapshot()),
  })
  return { im, sendPayment }
}

async function settle() {
  for (let i = 0; i < 6; i += 1) await Promise.resolve()
  await nextTick()
}

async function atConfirm(sendPayment: ReturnType<typeof vi.fn>) {
  const ctx = setup(sendPayment)
  ctx.im.startPaymentFlow()
  ctx.im.selectNode('alice')
  ctx.im.selectNode('bob')
  await settle()
  return ctx
}

const keyOf = (fn: ReturnType<typeof vi.fn>, call: number): string | undefined =>
  (fn.mock.calls[call]![4] as { idempotencyKey?: string }).idempotencyKey
const bodyOf = (fn: ReturnType<typeof vi.fn>, call: number) => (fn.mock.calls[call] as SendArgs).slice(0, 4)

describe('the key of a manual payment', () => {
  it('an unknown outcome keeps the key: the repeat of the same intent sends the same key and the same body', async () => {
    const send = vi.fn().mockRejectedValueOnce(unknown()).mockResolvedValueOnce(COMMITTED)
    const { im } = await atConfirm(send)

    await im.confirmPayment('10.00')
    expect(keyOf(send, 0)).toMatch(/^[A-Za-z0-9._:-]{1,128}$/)
    expect(im.paymentOutcome.value).toMatchObject({ kind: 'unknown' })
    await im.confirmPayment('10.00')

    expect(keyOf(send, 1)).toBe(keyOf(send, 0))
    expect(bodyOf(send, 1)).toEqual(bodyOf(send, 0))
  })

  it.each([
    ['spent: false', { idempotency_key_spent: false }],
    ['no such field', {}],
  ])('a refusal that says "%s" does not release the key', async (_what, details) => {
    const send = vi.fn().mockRejectedValueOnce(refusal(503, 'ENGINE_TIMEOUT', details)).mockResolvedValueOnce(COMMITTED)
    const { im } = await atConfirm(send)

    await im.confirmPayment('10.00')
    await im.confirmPayment('10.00')

    expect(keyOf(send, 1)).toBe(keyOf(send, 0))
  })

  it('a refusal before admission (NO_ROUTE) keeps the key too: the server stored nothing and the repeat is safe', async () => {
    const send = vi.fn().mockRejectedValueOnce(refusal(409, 'NO_ROUTE', { reason: 'no_route' })).mockResolvedValueOnce(COMMITTED)
    const { im } = await atConfirm(send)
    await im.confirmPayment('10.00')
    await im.confirmPayment('10.00')
    expect(keyOf(send, 1)).toBe(keyOf(send, 0))
  })

  it('the key is spent (idempotency_key_spent: true): the next confirmation of the same intent gets a new key', async () => {
    const send = vi.fn()
      .mockRejectedValueOnce(refusal(503, 'ENGINE_TIMEOUT', { idempotency_key_spent: true }))
      .mockResolvedValueOnce(COMMITTED)
    const { im } = await atConfirm(send)

    await im.confirmPayment('10.00')
    expect(im.paymentOutcome.value).toBeNull()
    await im.confirmPayment('10.00')

    expect(keyOf(send, 1)).not.toBe(keyOf(send, 0))
  })

  it('a known success ends the intent: paying the same again is a new payment with a new key', async () => {
    const send = vi.fn().mockResolvedValue(COMMITTED)
    const { im } = await atConfirm(send)

    await im.confirmPayment('10.00')
    await im.confirmPayment('10.00')

    expect(keyOf(send, 1)).not.toBe(keyOf(send, 0))
  })

  it.each([
    ['the amount', async (im: ReturnType<typeof setup>['im']) => im.confirmPayment('11.00')],
    ['the spelling of the amount', async (im: ReturnType<typeof setup>['im']) => im.confirmPayment('10')],
    ['the receiver', async (im: ReturnType<typeof setup>['im']) => { im.setPaymentToPid('carol'); await settle(); return im.confirmPayment('10.00') }],
    ['the sender', async (im: ReturnType<typeof setup>['im']) => { im.setPaymentFromPid('carol'); await settle(); im.setPaymentToPid('bob'); await settle(); return im.confirmPayment('10.00') }],
  ])('a change of %s while a payment is UNRESOLVED sends nothing (it is not a new intent with a new key)', async (_what, change) => {
    const send = vi.fn().mockRejectedValueOnce(unknown()).mockResolvedValueOnce(COMMITTED)
    const { im } = await atConfirm(send)

    await im.confirmPayment('10.00')
    await change(im)

    expect(send).toHaveBeenCalledTimes(1)
    expect(im.paymentOutcome.value).toMatchObject({ kind: 'unknown', amount: '10.00' })
  })

  it.each([
    ['the amount', async (im: ReturnType<typeof setup>['im']) => im.confirmPayment('11.00')],
    ['the spelling of the amount', async (im: ReturnType<typeof setup>['im']) => im.confirmPayment('10')],
    ['the receiver', async (im: ReturnType<typeof setup>['im']) => { im.setPaymentToPid('carol'); await settle(); return im.confirmPayment('10.00') }],
    ['the sender', async (im: ReturnType<typeof setup>['im']) => { im.setPaymentFromPid('carol'); await settle(); im.setPaymentToPid('bob'); await settle(); return im.confirmPayment('10.00') }],
  ])('a change of %s after a REFUSAL that left nothing unresolved is a new intent with a new key (the block is only for unknown outcomes)', async (_what, change) => {
    const send = vi.fn().mockRejectedValueOnce(refusal(409, 'NO_ROUTE', { reason: 'no_route' })).mockResolvedValueOnce(COMMITTED)
    const { im } = await atConfirm(send)

    await im.confirmPayment('10.00')
    await change(im)

    expect(send).toHaveBeenCalledTimes(2)
    expect(keyOf(send, 1)).not.toBe(keyOf(send, 0))
  })

  it.each([
    ['RUN_TERMINAL', refusal(409, 'RUN_TERMINAL')],
    ['403', refusal(403, 'ACCESS_DENIED')],
    ['401', refusal(401, 'UNAUTHORIZED')],
    ['a retryable 409', refusal(409, 'CONFLICT', { reason: 'busy' })],
  ])('%s after an UNKNOWN attempt does not settle it: the key stays and the screen still says unknown', async (_what, later) => {
    const send = vi.fn().mockRejectedValueOnce(unknown()).mockRejectedValueOnce(later).mockResolvedValueOnce(COMMITTED)
    const { im } = await atConfirm(send)

    await im.confirmPayment('10.00')
    await im.confirmPayment('10.00')
    expect(im.paymentOutcome.value).toMatchObject({ kind: 'unknown' })
    await im.retryPayment()

    expect(keyOf(send, 2)).toBe(keyOf(send, 0))
    expect(im.paymentOutcome.value).toMatchObject({ kind: 'success' })
  })

  it('the same refusal WITHOUT an earlier unknown attempt leaves nothing unknown on screen', async () => {
    const send = vi.fn().mockRejectedValueOnce(refusal(409, 'RUN_TERMINAL'))
    const { im } = await atConfirm(send)
    await im.confirmPayment('10.00')
    expect(im.paymentOutcome.value).toBeNull()
  })

  it('"Check / repeat" sends the FROZEN intent under the same key, whatever the panel field says now', async () => {
    const send = vi.fn().mockRejectedValueOnce(unknown()).mockResolvedValueOnce(COMMITTED)
    const { im } = await atConfirm(send)

    await im.confirmPayment('10.00')
    await im.retryPayment()

    expect(bodyOf(send, 1)).toEqual(['alice', 'bob', '10.00', 'UAH'])
    expect(keyOf(send, 1)).toBe(keyOf(send, 0))
  })

  it('"Check / repeat" sends the payment that was attempted even if the recipient was changed in the panel meanwhile', async () => {
    const send = vi.fn().mockRejectedValueOnce(unknown()).mockResolvedValueOnce(COMMITTED)
    const { im } = await atConfirm(send)

    await im.confirmPayment('10.00')
    im.setPaymentToPid('carol') // another recipient chosen in the dropdown; nothing has been sent for it
    await settle()
    await im.retryPayment()

    expect(bodyOf(send, 1)).toEqual(['alice', 'bob', '10.00', 'UAH'])
    expect(keyOf(send, 1)).toBe(keyOf(send, 0))
  })

  it('a busy refusal advises a repeat - safe, because every manual payment is sent under a key', async () => {
    const send = vi.fn().mockRejectedValueOnce(refusal(409, 'CONFLICT', { reason: 'busy' }))
    const { im } = await atConfirm(send)
    await im.confirmPayment('10.00')
    expect(im.state.error).toMatch(/send the same payment again/i)
  })

  it('retryPayment does nothing when nothing is unknown', async () => {
    const send = vi.fn().mockResolvedValue(COMMITTED)
    const { im } = await atConfirm(send)
    await im.retryPayment()
    expect(send).not.toHaveBeenCalled()
  })

  it('closing the panel during the request is an unknown outcome: the same intent confirmed later reuses the key', async () => {
    const send = vi.fn()
      .mockImplementationOnce((_f, _t, _a, _e, o: { signal: AbortSignal }) => new Promise((_resolve, reject) => {
        o.signal.addEventListener('abort', () => reject(unknown()))
      }))
      .mockResolvedValueOnce(COMMITTED)
    const { im } = await atConfirm(send)

    const pending = im.confirmPayment('10.00')
    im.cancel()
    await pending
    expect(im.phase.value).toBe('idle')

    im.startPaymentFlow(); im.selectNode('alice'); im.selectNode('bob')
    await settle()
    await im.confirmPayment('10.00')

    expect(keyOf(send, 1)).toBe(keyOf(send, 0))
  })
})

describe('what a success-shaped answer is allowed to say', () => {
  it('COMMITTED is the only status shown as a payment: the result is on screen, with the names of the parties', async () => {
    const send = vi.fn().mockResolvedValue({
      ...COMMITTED,
      routes: [{ hops: [{ from: 'alice', to: 'carol', amount: '6.00' }, { from: 'carol', to: 'bob', amount: '6.00' }] },
        { hops: [{ from: 'alice', to: 'bob', amount: '4.00' }] }],
    })
    const { im } = await atConfirm(send)

    await im.confirmPayment('10.00')

    expect(im.successMessage.value).toBe('Payment sent: 10.00 UAH')
    expect(im.phase.value).toBe('confirm-payment')
    const outcome = im.paymentOutcome.value
    expect(outcome).toMatchObject({ kind: 'success', paymentId: 'man:0123', status: 'COMMITTED', fromName: 'Alice', toName: 'Bob' })
    expect(outcome?.kind === 'success' && outcome.routes.map((r) => r.hops.map((h) => `${h.fromName}>${h.toName}:${h.amount}`))).toEqual([
      ['Alice>Carol:6.00', 'Carol>Bob:6.00'],
      ['Alice>Bob:4.00'],
    ])
  })

  it('a stored refusal (ABORTED) in a 2xx answer is not shown as a payment, and its key is spent', async () => {
    const send = vi.fn().mockResolvedValueOnce({ ...COMMITTED, status: 'ABORTED' }).mockResolvedValueOnce(COMMITTED)
    const { im } = await atConfirm(send)

    await im.confirmPayment('10.00')

    expect(im.successMessage.value).toBeNull()
    expect(im.paymentOutcome.value).toBeNull()
    expect(im.state.error).toContain('did not confirm')
    expect(im.history.length).toBe(0)
    await im.confirmPayment('10.00')
    expect(keyOf(send, 1)).not.toBe(keyOf(send, 0))
  })

  it('an unrecognised status is not shown as a payment either, and the outcome is unknown (the key stays)', async () => {
    const send = vi.fn().mockResolvedValueOnce({ ...COMMITTED, status: 'PENDING' }).mockResolvedValueOnce(COMMITTED)
    const { im } = await atConfirm(send)

    await im.confirmPayment('10.00')

    expect(im.successMessage.value).toBeNull()
    expect(im.paymentOutcome.value).toMatchObject({ kind: 'unknown' })
    await im.confirmPayment('10.00')
    expect(keyOf(send, 1)).toBe(keyOf(send, 0))
  })

  it('the history line of a payment names the parties, not their pids', async () => {
    const send = vi.fn().mockResolvedValue(COMMITTED)
    const { im } = await atConfirm(send)

    await im.confirmPayment('10.00')

    expect(im.history.map((h) => h.text)).toEqual(['Payment 10.00 UAH: Alice → Bob'])
  })

  it('the result is dismissed to the recipient step, and closing clears it', async () => {
    const send = vi.fn().mockResolvedValue(COMMITTED)
    const { im } = await atConfirm(send)
    await im.confirmPayment('10.00')

    im.dismissPaymentResult()
    expect(im.paymentOutcome.value).toBeNull()
    expect(im.phase.value).toBe('picking-payment-to')
    expect(im.state.fromPid).toBe('alice')
  })

  it('the error of an unknown outcome says so in words, not as a refusal', async () => {
    const send = vi.fn().mockRejectedValueOnce(unknown())
    const { im } = await atConfirm(send)
    await im.confirmPayment('10.00')
    expect(im.state.error).toContain('may have been made')
    expect(im.paymentOutcome.value).toMatchObject({ kind: 'unknown', amount: '10.00', fromName: 'Alice', toName: 'Bob' })
  })
})

describe('the estimate of the chosen recipient', () => {
  it('carries hops and the server estimate, and keeps "did not estimate" (null) apart from a measured zero', async () => {
    const send = vi.fn()
    const { im } = await atConfirm(send)
    await vi.waitFor(() => expect(im.paymentTargetEstimate.value).toMatchObject({ state: 'received' }))
    expect(im.paymentTargetEstimate.value).toEqual({ state: 'received', hops: 1, maxAvailable: '100.00' })

    im.setPaymentToPid('carol')
    await settle()
    expect(im.paymentTargetEstimate.value).toEqual({ state: 'received', hops: 2, maxAvailable: null })
  })
})
