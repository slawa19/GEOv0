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

type Store = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> | null

function mode(sendPayment: Send, runId = ref('run_1'), intentStorage?: Store) {
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
  const im = useInteractMode({
    actions, runId: computed(() => runId.value), equivalent: computed(() => 'UAH'), snapshot: ref(snapshot()),
    ...(intentStorage !== undefined ? { intentStorage } : {}),
  })
  return { im, runId }
}

async function settle() {
  for (let i = 0; i < 6; i += 1) await Promise.resolve()
  await nextTick()
}

async function atConfirm(send: Send, intentStorage?: Store) {
  const ctx = mode(send, ref('run_1'), intentStorage)
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

describe('the unresolved payment is held, shown and resolved only by a person (R1-R5)', () => {
  it('blocked: another payment is not sent, says why, and leaves the unresolved one on screen', async () => {
    const send = vi.fn().mockRejectedValueOnce(unknown()).mockResolvedValue(COMMITTED)
    const { im } = await atConfirm(send)
    await im.confirmPayment('10.00')

    await im.confirmPayment('20.00')

    expect(send).toHaveBeenCalledTimes(1)
    expect(im.state.error).toContain('checked or discarded')
    expect(im.paymentOutcome.value).toMatchObject({ kind: 'unknown', amount: '10.00', fromName: 'Alice', toName: 'Bob', runMismatch: false })
  })

  it('Esc / cancel / a closed panel do not resolve it: the banner is there again when the panel is opened', async () => {
    const send = vi.fn().mockRejectedValueOnce(unknown()).mockResolvedValue(COMMITTED)
    const { im } = await atConfirm(send)
    await im.confirmPayment('10.00')

    im.cancel()
    expect(im.phase.value).toBe('idle')
    expect(im.paymentOutcome.value?.kind).toBe('unknown')
    im.startPaymentFlow()
    expect(im.paymentOutcome.value?.kind).toBe('unknown')
    im.setPaymentFromPid('alice'); im.setPaymentToPid('carol')
    await settle()
    await im.confirmPayment('10.00')
    expect(send).toHaveBeenCalledTimes(1)
  })

  it('an explicit discard releases it - and only then another payment leaves, under a new key', async () => {
    const send = vi.fn().mockRejectedValueOnce(unknown()).mockResolvedValue(COMMITTED)
    const { im } = await atConfirm(send)
    await im.confirmPayment('10.00')
    await im.confirmPayment('20.00')
    expect(send).toHaveBeenCalledTimes(1)

    im.discardUnresolvedPayment()
    expect(im.paymentOutcome.value).toBeNull()
    await im.confirmPayment('20.00')

    expect(send).toHaveBeenCalledTimes(2)
    expect(keyOf(send, 1)).not.toBe(keyOf(send, 0))
    expect(amountOf(send, 1)).toBe('20.00')
  })

  it('the repeat that is answered COMMITTED unblocks: the next payment is free', async () => {
    const send = vi.fn().mockRejectedValueOnce(unknown()).mockResolvedValue(COMMITTED)
    const { im } = await atConfirm(send)
    await im.confirmPayment('10.00')

    await im.retryPayment()
    expect(im.paymentOutcome.value?.kind).toBe('success')
    await im.confirmPayment('20.00')

    expect(send).toHaveBeenCalledTimes(3)
    expect(keyOf(send, 2)).not.toBe(keyOf(send, 0))
  })

  it('the repeat that is answered "the key is spent" unblocks too', async () => {
    const send = vi.fn()
      .mockRejectedValueOnce(unknown())
      .mockRejectedValueOnce(refusal(503, 'ENGINE_TIMEOUT', { idempotency_key_spent: true }))
      .mockResolvedValue(COMMITTED)
    const { im } = await atConfirm(send)
    await im.confirmPayment('10.00')
    await im.retryPayment()
    expect(im.paymentOutcome.value).toBeNull()

    await im.confirmPayment('20.00')

    expect(send).toHaveBeenCalledTimes(3)
  })

  it('ANTI-VACUUM: a refusal with NOTHING unknown before it blocks nothing - another payment leaves at once', async () => {
    const send = vi.fn().mockRejectedValueOnce(refusal(409, 'NO_ROUTE', { reason: 'no_route' })).mockResolvedValue(COMMITTED)
    const { im } = await atConfirm(send)
    await im.confirmPayment('10.00')

    await im.confirmPayment('20.00')

    expect(send).toHaveBeenCalledTimes(2)
    expect(keyOf(send, 1)).not.toBe(keyOf(send, 0))
  })

  it('R5: a second Confirm while the first is in flight sends nothing and does not replace the held intent', async () => {
    let release!: () => void
    const send = vi.fn()
      .mockImplementationOnce(() => new Promise((_resolve, reject) => { release = () => reject(unknown()) }))
      .mockResolvedValue(COMMITTED)
    const { im } = await atConfirm(send)

    const first = im.confirmPayment('10.00')
    await settle()
    await im.confirmPayment('20.00')
    release()
    await first

    expect(send).toHaveBeenCalledTimes(1)
    expect(im.paymentOutcome.value).toMatchObject({ kind: 'unknown', amount: '10.00' })
    await im.retryPayment()
    expect(keyOf(send, 1)).toBe(keyOf(send, 0))
  })

  it('R3: a re-created composable restores it through the storage; its repeat is the same key and body', async () => {
    const send = vi.fn().mockRejectedValueOnce(unknown()).mockResolvedValue(COMMITTED)
    const first = await atConfirm(send)
    await first.im.confirmPayment('10.00')

    const again = mode(send)
    expect(again.im.paymentOutcome.value).toMatchObject({ kind: 'unknown', amount: '10.00', runMismatch: false })
    await again.im.retryPayment()

    expect(keyOf(send, 1)).toBe(keyOf(send, 0))
    expect(send.mock.calls[1]!.slice(0, 4)).toEqual(send.mock.calls[0]!.slice(0, 4))
    expect(window.sessionStorage.length, 'the stored record is removed by the verdict').toBe(0)
  })

  it('R3: a page reloaded while the request was in flight finds an unresolved payment', async () => {
    let started!: () => void
    const send = vi.fn().mockImplementationOnce(() => new Promise(() => { started() }))
    const first = await atConfirm(send)
    const sent = new Promise<void>((resolve) => { started = resolve })
    void first.im.confirmPayment('10.00')
    await sent

    const after = mode(vi.fn().mockResolvedValue(COMMITTED))
    expect(after.im.paymentOutcome.value).toMatchObject({ kind: 'unknown', amount: '10.00' })
  })

  it('R3: without a usable storage it still works in memory (and a re-created composable then starts clean - named)', async () => {
    const denied = {
      getItem: () => { throw new Error('denied') },
      setItem: () => { throw new Error('denied') },
      removeItem: () => { throw new Error('denied') },
    }
    const send = vi.fn().mockRejectedValueOnce(unknown()).mockResolvedValue(COMMITTED)
    window.history.replaceState({}, '', '/?mode=real&ui=interact')
    const actions = {
      actionsDisabled: ref(false), sendPayment: send, createTrustline: vi.fn(), updateTrustline: vi.fn(), closeTrustline: vi.fn(), runClearing: vi.fn(),
      fetchParticipants: vi.fn(async () => []), fetchTrustlines: vi.fn(async () => []), fetchPaymentTargets: vi.fn(async () => []),
    } as unknown as Actions
    const im = useInteractMode({
      actions, runId: computed(() => 'run_1'), equivalent: computed(() => 'UAH'), snapshot: ref(snapshot()), intentStorage: denied,
    })
    im.startPaymentFlow(); im.selectNode('alice'); im.selectNode('bob')
    await settle()

    await im.confirmPayment('10.00')
    await im.confirmPayment('10.0')
    im.cancel()

    expect(send).toHaveBeenCalledTimes(1)
    expect(im.paymentOutcome.value?.kind).toBe('unknown')
  })

  it('R4: another run - the banner says so, the repeat is NOT sent (it would go to the current run), only discard is possible', async () => {
    const send = vi.fn().mockRejectedValueOnce(unknown()).mockResolvedValue(COMMITTED)
    const { im, runId } = await atConfirm(send)
    await im.confirmPayment('10.00')

    runId.value = 'run_2'
    await settle()
    expect(im.paymentOutcome.value).toMatchObject({ kind: 'unknown', runMismatch: true })
    await im.retryPayment()
    await im.confirmPayment('10.00')
    expect(send).toHaveBeenCalledTimes(1)

    im.discardUnresolvedPayment()
    await im.confirmPayment('10.00')
    expect(send).toHaveBeenCalledTimes(2)
  })

  it('a COMMITTED payment confirmed after the discard is a normal one, and leaves nothing stored', async () => {
    const send = vi.fn().mockRejectedValueOnce(unknown()).mockResolvedValue(COMMITTED)
    const { im } = await atConfirm(send)
    await im.confirmPayment('10.00')
    im.discardUnresolvedPayment()

    await im.confirmPayment('10.00')

    expect(im.paymentOutcome.value?.kind).toBe('success')
    expect(window.sessionStorage.length).toBe(0)
  })
})

const ENTRY_KEY = 'geo.sim.v2.unresolvedPayment.run_1'
const FROZEN = { runId: 'run_1', from: 'alice', to: 'bob', equivalent: 'UAH', amount: '10.00' }

describe('a damaged stored record never sends a payment without a key (money-side consequence)', () => {
  it.each([
    ['an empty key', { v: 1, key: '', intent: FROZEN }],
    ['a key with a space', { v: 1, key: 'bad key', intent: FROZEN }],
    ['a key with a slash', { v: 1, key: 'bad/key', intent: FROZEN }],
    ['a key longer than the server accepts', { v: 1, key: 'k'.repeat(129), intent: FROZEN }],
    ['an amount that is not an amount', { v: 1, key: 'k-1', intent: { ...FROZEN, amount: 'abc' } }],
    ['an empty receiver', { v: 1, key: 'k-1', intent: { ...FROZEN, to: '' } }],
  ])('%s: nothing is restored, nothing is sent by Check / repeat, the entry is removed', async (_what, entry) => {
    window.sessionStorage.setItem(ENTRY_KEY, JSON.stringify(entry))
    const send = vi.fn().mockResolvedValue(COMMITTED)
    const { im } = mode(send)

    await im.retryPayment()

    expect(send, 'a request left on the strength of a damaged record').not.toHaveBeenCalled()
    expect(im.paymentOutcome.value).toBeNull()
    expect(window.sessionStorage.getItem(ENTRY_KEY)).toBeNull()
  })
})

describe('a storage whose removal fails does not lock the run', () => {
  /** getItem/setItem work; removeItem throws - the entry written stays where it is. */
  function stubbornStorage() {
    const map = new Map<string, string>()
    return {
      map,
      storage: {
        getItem: (k: string) => map.get(k) ?? null,
        setItem: (k: string, v: string) => void map.set(k, v),
        removeItem: () => { throw new Error('removal denied') },
      },
    }
  }

  it('after COMMITTED the banner does not come back, and the next payment goes under a new key', async () => {
    const { storage } = stubbornStorage()
    const send = vi.fn().mockRejectedValueOnce(unknown()).mockResolvedValue(COMMITTED)
    const { im } = await atConfirm(send, storage)
    await im.confirmPayment('10.00')

    await im.retryPayment()
    expect(im.paymentOutcome.value?.kind).toBe('success')
    im.dismissPaymentResult()
    expect(im.paymentOutcome.value, 'the closed record was lifted out of the storage again').toBeNull()
    im.setPaymentToPid('bob') // the result screen was left for the recipient step
    await settle()
    await im.confirmPayment('20.00')

    expect(send).toHaveBeenCalledTimes(3)
    expect(keyOf(send, 2)).not.toBe(keyOf(send, 0))
  })

  it('after an explicit discard the banner does not come back, and another payment is free', async () => {
    const { storage } = stubbornStorage()
    const send = vi.fn().mockRejectedValueOnce(unknown()).mockResolvedValue(COMMITTED)
    const { im } = await atConfirm(send, storage)
    await im.confirmPayment('10.00')

    im.discardUnresolvedPayment()
    expect(im.paymentOutcome.value).toBeNull()
    await im.confirmPayment('20.00')

    expect(send).toHaveBeenCalledTimes(2)
  })
})

describe('a refusal of the repeat does not take the unresolved payment out of the storage', () => {
  it('unknown -> the repeat is refused (403) -> the page is reloaded (composable re-created): the banner and the key are still there', async () => {
    const send = vi.fn().mockRejectedValueOnce(unknown()).mockRejectedValueOnce(refusal(403, 'ACCESS_DENIED')).mockResolvedValue(COMMITTED)
    const first = await atConfirm(send)
    await first.im.confirmPayment('10.00')
    await first.im.retryPayment()
    expect(first.im.paymentOutcome.value?.kind).toBe('unknown')

    const reloaded = mode(send)
    expect(reloaded.im.paymentOutcome.value).toMatchObject({ kind: 'unknown', amount: '10.00' })
    await reloaded.im.retryPayment()

    expect(keyOf(send, 2)).toBe(keyOf(send, 0))
  })
})

describe('defence in depth', () => {
  it('a request whose key is not valid is not sent - it would not be a check, it would be a new payment', async () => {
    vi.stubGlobal('crypto', { randomUUID: () => '' })
    try {
      const send = vi.fn().mockResolvedValue(COMMITTED)
      const { im } = await atConfirm(send)

      await im.confirmPayment('10.00')

      expect(send).not.toHaveBeenCalled()
      expect(im.state.error).toContain('no valid key')
    } finally {
      vi.unstubAllGlobals()
    }
  })
})

/** A storage on a Map whose failures can be switched on and off by the test. */
function flakyStorage() {
  const map = new Map<string, string>()
  const fail = { set: false, get: false, remove: false }
  const storage = {
    getItem: (k: string) => {
      if (fail.get) throw new Error('read denied')
      return map.get(k) ?? null
    },
    setItem: (k: string, v: string) => {
      if (fail.set) throw new Error('quota exceeded')
      map.set(k, v)
    },
    removeItem: (k: string) => {
      if (fail.remove) throw new Error('removal denied')
      map.delete(k)
    },
  }
  return { map, fail, storage }
}

describe('a payment is not sent unless the record that lets a reload check it could be saved (class 1)', () => {
  it('REPRODUCER: setItem throws; the first attempt ends unknown; the page is reloaded; the same intent goes under ANOTHER key', async () => {
    const { storage, fail } = flakyStorage()
    fail.set = true
    const send = vi.fn().mockRejectedValueOnce(unknown()).mockResolvedValue(COMMITTED)

    const before = await atConfirm(send, storage)
    await before.im.confirmPayment('10.00')
    const toldWhy = before.im.state.error

    const reloaded = await atConfirm(send, storage) // the composable is created again, as after a reload
    await reloaded.im.confirmPayment('10.00')

    expectNoOtherPaymentThanTheFirst(send)
    expect(toldWhy, 'the person must be told why the payment was not sent').toContain('was not sent')
  })

  it('getItem throws at the restore: whether a payment is unresolved is NOT known, so no new payment goes', async () => {
    const { storage, fail } = flakyStorage()
    const send = vi.fn().mockRejectedValueOnce(unknown()).mockResolvedValue(COMMITTED)
    const before = await atConfirm(send, storage)
    await before.im.confirmPayment('10.00') // an unresolved record is now stored

    fail.get = true
    const reloaded = await atConfirm(send, storage)
    await reloaded.im.confirmPayment('20.00')

    expect(send, 'a request left although the storage could not even be read').toHaveBeenCalledTimes(1)
    expect(reloaded.im.state.error).toContain('could not be read')
  })

  it('the quota is exhausted for the record of a NEW payment: it is not sent; when the storage is back it goes, and survives a re-creation', async () => {
    const { storage, fail } = flakyStorage()
    const send = vi.fn().mockResolvedValueOnce(COMMITTED).mockRejectedValueOnce(unknown()).mockResolvedValue(COMMITTED)
    const { im } = await atConfirm(send, storage)
    await im.confirmPayment('10.00')

    fail.set = true
    await im.confirmPayment('20.00')
    expect(send, 'a payment left without a stored record').toHaveBeenCalledTimes(1)

    fail.set = false
    await im.confirmPayment('20.00')
    expect(send).toHaveBeenCalledTimes(2)
    const reloaded = await atConfirm(send, storage)
    expect(reloaded.im.paymentOutcome.value).toMatchObject({ kind: 'unknown', amount: '20.00' })
  })

  it('the record of another run under the same storage is never overwritten (the storage key includes the run)', async () => {
    const { storage, map } = flakyStorage()
    const send = vi.fn().mockRejectedValue(unknown())
    const one = mode(send, ref('run_1'), storage)
    one.im.startPaymentFlow(); one.im.selectNode('alice'); one.im.selectNode('bob')
    await settle()
    await one.im.confirmPayment('10.00')
    const stored = map.get(ENTRY_KEY)

    const two = mode(vi.fn().mockRejectedValue(unknown()), ref('run_2'), storage)
    two.im.startPaymentFlow(); two.im.selectNode('alice'); two.im.selectNode('bob')
    await settle()
    await two.im.confirmPayment('30.00')

    expect(map.get(ENTRY_KEY)).toBe(stored)
    expect([...map.keys()].sort()).toEqual([ENTRY_KEY, 'geo.sim.v2.unresolvedPayment.run_2'])
  })
})

describe('an unknown outcome carries the correlation id of the request (AGENTS section 12)', () => {
  it('REPRODUCER: the error of an unknown outcome ends with (ref: <id>)', async () => {
    const send = vi.fn().mockRejectedValueOnce({ ...unknown(), requestId: 'req-7f3a' })
    const { im } = await atConfirm(send)

    await im.confirmPayment('10.00')

    expect(im.state.error).toContain('(ref: req-7f3a)')
  })
})
