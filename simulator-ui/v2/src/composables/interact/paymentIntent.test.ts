import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  createPaymentIntentKeeper,
  isKeySpent,
  newIdempotencyKey,
  paymentOutcomeUnknown,
  type PaymentIntent,
} from './paymentIntent'

const KEY_GRAMMAR = /^[A-Za-z0-9._:-]{1,128}$/

const intent: PaymentIntent = { runId: 'run_1', from: 'alice', to: 'bob', equivalent: 'UAH', amount: '10' }

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the life of the idempotency key of a manual payment', () => {
  it('the same frozen intent is the same key, any number of times', () => {
    const keeper = createPaymentIntentKeeper()
    const first = keeper.begin(intent)
    expect(keeper.begin({ ...intent }).key).toBe(first.key)
    expect(keeper.begin({ ...intent }).key).toBe(first.key)
  })

  it.each([
    ['the amount', { amount: '11' }],
    ['the spelling of the amount ("10" and "10.00" are different requests)', { amount: '10.00' }],
    ['the sender', { from: 'carol' }],
    ['the receiver', { to: 'carol' }],
    ['the equivalent', { equivalent: 'HOUR' }],
    ['the run', { runId: 'run_2' }],
  ])('a change of %s is a new intent with a new key', (_what, change) => {
    const keeper = createPaymentIntentKeeper()
    const first = keeper.begin(intent)
    const second = keeper.begin({ ...intent, ...change })
    expect(second.key).not.toBe(first.key)
    expect(second.intent).toMatchObject(change)
  })

  it('an unknown outcome keeps the key; the next confirmation of the same intent reuses it', () => {
    const keeper = createPaymentIntentKeeper()
    const record = keeper.begin(intent)
    keeper.markUnknown(record)
    expect(keeper.peek()).toMatchObject({ key: record.key, unknown: true })
    expect(keeper.begin({ ...intent }).key).toBe(record.key)
  })

  it('a settled intent (made, or the key spent) gives the next confirmation of the same intent a new key', () => {
    const keeper = createPaymentIntentKeeper()
    const record = keeper.begin(intent)
    keeper.settle(record)
    expect(keeper.peek()).toBeNull()
    expect(keeper.begin({ ...intent }).key).not.toBe(record.key)
  })

  it('settling an old record does not erase a newer intent', () => {
    const keeper = createPaymentIntentKeeper()
    const old = keeper.begin(intent)
    const newer = keeper.begin({ ...intent, amount: '20' })
    keeper.settle(old)
    expect(keeper.peek()).toBe(newer)
  })
})

describe('what the server said about the key', () => {
  it('only an explicit true is "spent": false and an absent field are not', () => {
    expect(isKeySpent({ idempotency_key_spent: true })).toBe(true)
    expect(isKeySpent({ idempotency_key_spent: false })).toBe(false)
    expect(isKeySpent({})).toBe(false)
    expect(isKeySpent(null)).toBe(false)
    expect(isKeySpent({ idempotency_key_spent: 'true' })).toBe(false)
  })
})

describe('did a payment request that was sent end without a verdict', () => {
  it.each([
    ['no HTTP answer (network failure, cancel, timeout)', { status: 0 }, true],
    ['a 2xx answer that could not be accepted', { status: 200 }, true],
    ['408', { status: 408 }, true],
    ['500', { status: 500 }, true],
    ['503 ENGINE_TIMEOUT without a verdict on the key', { status: 503, details: {} }, true],
    ['503 with the key not spent', { status: 503, details: { idempotency_key_spent: false } }, true],
    ['503 with the key spent: a verdict', { status: 503, details: { idempotency_key_spent: true } }, false],
    ['500 with the key spent: a verdict', { status: 500, details: { idempotency_key_spent: true } }, false],
    ['409 NO_ROUTE: the payment was refused', { status: 409 }, false],
    ['400: the request was refused at the edge', { status: 400 }, false],
    ['403: refused', { status: 403 }, false],
    ['404: refused', { status: 404 }, false],
  ])('%s', (_what, error, expected) => {
    expect(paymentOutcomeUnknown(error, true)).toBe(expected)
  })

  it('a request that never left (no run id) is not unknown, whatever the error looks like', () => {
    expect(paymentOutcomeUnknown({ status: 0 }, false)).toBe(false)
  })
})

describe('the key itself', () => {
  it('is a UUID from crypto.randomUUID where there is one, and every key passes the server grammar', () => {
    const keys = new Set(Array.from({ length: 50 }, () => newIdempotencyKey()))
    expect(keys.size).toBe(50)
    for (const k of keys) expect(k).toMatch(KEY_GRAMMAR)
  })

  it('FALLBACK 1: without randomUUID it is 128 random bits from crypto.getRandomValues', () => {
    vi.stubGlobal('crypto', { getRandomValues: (a: Uint8Array) => a.fill(0xab) })
    const key = newIdempotencyKey()
    expect(key).toBe(`k-${'ab'.repeat(16)}`)
    expect(key).toMatch(KEY_GRAMMAR)
  })

  it('FALLBACK 2: with no crypto at all it is still inside the grammar (and is named weak)', () => {
    vi.stubGlobal('crypto', undefined)
    const key = newIdempotencyKey()
    expect(key).toMatch(KEY_GRAMMAR)
    expect(key.startsWith('k-weak-')).toBe(true)
  })
})
