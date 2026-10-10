import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  createPaymentIntentKeeper,
  isKeySpent,
  newIdempotencyKey,
  paymentOutcomeUnknown,
  type PaymentIntent,
  type PaymentIntentRecord,
} from './paymentIntent'

const KEY_GRAMMAR = /^[A-Za-z0-9._:-]{1,128}$/

const intent: PaymentIntent = { runId: 'run_1', from: 'alice', to: 'bob', equivalent: 'UAH', amount: '10' }

afterEach(() => {
  vi.unstubAllGlobals()
})

/** The record `begin` issued; fails the test if it was blocked. */
function begin(keeper: ReturnType<typeof createPaymentIntentKeeper>, i: PaymentIntent): PaymentIntentRecord {
  const r = keeper.begin(i)
  if (r.kind !== 'record') throw new Error('begin was blocked by an unresolved intent')
  return r.record
}

describe('the life of the idempotency key of a manual payment', () => {
  it('the same frozen intent is the same key, any number of times', () => {
    const keeper = createPaymentIntentKeeper({ storage: null })
    const first = begin(keeper, intent)
    expect(begin(keeper, { ...intent }).key).toBe(first.key)
    expect(begin(keeper, { ...intent }).key).toBe(first.key)
  })

  it.each([
    ['the amount', { amount: '11' }],
    ['the spelling of the amount ("10" and "10.00" are different requests)', { amount: '10.00' }],
    ['the sender', { from: 'carol' }],
    ['the receiver', { to: 'carol' }],
    ['the equivalent', { equivalent: 'HOUR' }],
    ['the run', { runId: 'run_2' }],
  ])('a change of %s is a new intent with a new key (when nothing is unresolved)', (_what, change) => {
    const keeper = createPaymentIntentKeeper({ storage: null })
    const first = begin(keeper, intent)
    const second = begin(keeper, { ...intent, ...change })
    expect(second.key).not.toBe(first.key)
    expect(second.intent).toMatchObject(change)
  })

  it('an unknown outcome keeps the key; the next confirmation of the same intent reuses it', () => {
    const keeper = createPaymentIntentKeeper({ storage: null })
    const record = begin(keeper, intent)
    keeper.markUnknown(record)
    expect(keeper.peek()).toMatchObject({ key: record.key, unknown: true })
    expect(begin(keeper, { ...intent }).key).toBe(record.key)
  })

  it('a settled intent (made, or the key spent) gives the next confirmation of the same intent a new key', () => {
    const keeper = createPaymentIntentKeeper({ storage: null })
    const record = begin(keeper, intent)
    keeper.settle(record)
    expect(keeper.peek()).toBeNull()
    expect(begin(keeper, { ...intent }).key).not.toBe(record.key)
  })

  it('settling an old record does not erase a newer intent', () => {
    const keeper = createPaymentIntentKeeper({ storage: null })
    const old = begin(keeper, intent)
    const newer = begin(keeper, { ...intent, amount: '20' })
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

describe('an unresolved intent is held (R1, R3)', () => {
  const memory = () => {
    const map = new Map<string, string>()
    return {
      map,
      storage: {
        getItem: (k: string) => map.get(k) ?? null,
        setItem: (k: string, v: string) => void map.set(k, v),
        removeItem: (k: string) => void map.delete(k),
      },
    }
  }
  const sameRun = () => 'run_1'

  it('another intent does not replace it and is issued no key', () => {
    const keeper = createPaymentIntentKeeper({ storage: null })
    const record = begin(keeper, intent)
    keeper.markUnknown(record)

    const other = keeper.begin({ ...intent, amount: '10.0' })

    expect(other).toEqual({ kind: 'blocked', unresolved: record })
    expect(keeper.peek()).toBe(record)
    expect(keeper.begin({ ...intent })).toEqual({ kind: 'record', record })
  })

  it('it ends only by a verdict or by an explicit discard - then, and only then, another intent gets a key', () => {
    const keeper = createPaymentIntentKeeper({ storage: null })
    const record = begin(keeper, intent)
    keeper.markUnknown(record)
    expect(keeper.begin({ ...intent, to: 'carol' }).kind).toBe('blocked')
    keeper.discard()
    const next = begin(keeper, { ...intent, to: 'carol' })
    expect(next.key).not.toBe(record.key)

    keeper.markUnknown(next)
    keeper.settle(next)
    expect(keeper.begin({ ...intent, amount: '1' }).kind).toBe('record')
  })

  it('a definitive refusal that was not preceded by an unknown attempt blocks nothing', () => {
    const keeper = createPaymentIntentKeeper({ storage: null })
    const record = begin(keeper, intent)
    keeper.markRefused(record)
    expect(keeper.begin({ ...intent, amount: '11' }).kind).toBe('record')
  })

  it('a refusal after an unknown attempt of the same intent does not clear it', () => {
    const keeper = createPaymentIntentKeeper({ storage: null })
    const record = begin(keeper, intent)
    keeper.markUnknown(record)
    keeper.markRefused(record)
    expect(keeper.peek()?.unknown).toBe(true)
  })

  it('the record is stored when a request leaves, and survives a re-created keeper as UNRESOLVED (a page left mid-flight)', () => {
    const { map, storage } = memory()
    const first = createPaymentIntentKeeper({ storage, runId: sameRun })
    const record = begin(first, intent)
    first.markSent(record)
    expect([...map.keys()]).toEqual(['geo.sim.v2.unresolvedPayment.run_1'])

    const second = createPaymentIntentKeeper({ storage, runId: sameRun })
    expect(second.peek()).toMatchObject({ key: record.key, unknown: true, intent })
    expect(second.begin({ ...intent, amount: '11' }).kind).toBe('blocked')
  })

  it('only the unresolved record is stored: a verdict, a refusal and a discard remove it', () => {
    const { map, storage } = memory()
    const keeper = createPaymentIntentKeeper({ storage, runId: sameRun })

    const a = begin(keeper, intent); keeper.markSent(a); keeper.settle(a)
    expect(map.size).toBe(0)
    const b = begin(keeper, intent); keeper.markSent(b); keeper.markRefused(b)
    expect(map.size).toBe(0)
    const c = begin(keeper, intent); keeper.markSent(c); keeper.markUnknown(c)
    expect(map.size).toBe(1)
    keeper.discard()
    expect(map.size).toBe(0)
  })

  it('a record is restored only for its own run', () => {
    const { storage } = memory()
    const first = createPaymentIntentKeeper({ storage, runId: () => 'run_1' })
    first.markSent(begin(first, intent))

    expect(createPaymentIntentKeeper({ storage, runId: () => 'run_2' }).peek()).toBeNull()
    expect(createPaymentIntentKeeper({ storage, runId: () => 'run_1' }).peek()).not.toBeNull()
  })

  it('works without a usable storage: every access that throws is swallowed, the record is held in memory', () => {
    const broken = {
      getItem: () => { throw new Error('denied') },
      setItem: () => { throw new Error('denied') },
      removeItem: () => { throw new Error('denied') },
    }
    const keeper = createPaymentIntentKeeper({ storage: broken, runId: sameRun })
    const record = begin(keeper, intent)
    keeper.markSent(record)
    keeper.markUnknown(record)
    expect(keeper.begin({ ...intent, amount: '11' }).kind).toBe('blocked')
    keeper.settle(record)
    expect(keeper.peek()).toBeNull()
  })

  it.each([
    ['garbage', '{nope'],
    ['another version', JSON.stringify({ v: 2, key: 'k', intent })],
    ['an intent with a missing field', JSON.stringify({ v: 1, key: 'k', intent: { runId: 'run_1', from: 'alice' } })],
    ['an intent of another run under this key', JSON.stringify({ v: 1, key: 'k', intent: { ...intent, runId: 'run_9' } })],
  ])('an unreadable stored entry (%s) restores nothing', (_what, raw) => {
    const { map, storage } = memory()
    map.set('geo.sim.v2.unresolvedPayment.run_1', raw)
    expect(createPaymentIntentKeeper({ storage, runId: sameRun }).peek()).toBeNull()
  })
})

describe('a stored record is validated like a request (the key by the server grammar, the intent by shape)', () => {
  const run = () => 'run_1'
  const entryKey = 'geo.sim.v2.unresolvedPayment.run_1'

  function storeWith(entry: unknown) {
    const map = new Map<string, string>([[entryKey, JSON.stringify(entry)]])
    return {
      map,
      storage: {
        getItem: (k: string) => map.get(k) ?? null,
        setItem: (k: string, v: string) => void map.set(k, v),
        removeItem: (k: string) => void map.delete(k),
      },
    }
  }

  it.each([
    ['an empty key', ''],
    ['a key with a space', 'a b'],
    ['a key with a slash', 'a/b'],
    ['a key with a non-ASCII letter', 'ключ'],
    ['a key of 129 characters', 'k'.repeat(129)],
  ])('%s is not restored, and the entry is removed', (_what, key) => {
    const { map, storage } = storeWith({ v: 1, key, intent })
    expect(createPaymentIntentKeeper({ storage, runId: run }).peek()).toBeNull()
    expect(map.has(entryKey)).toBe(false)
  })

  it.each([
    ['an amount that is not an amount', { amount: 'abc' }],
    ['a negative amount', { amount: '-1' }],
    ['an empty receiver', { to: '' }],
    ['an empty sender', { from: '' }],
    ['an empty equivalent', { equivalent: '' }],
  ])('an intent with %s is not restored, and the entry is removed', (_what, damage) => {
    const { map, storage } = storeWith({ v: 1, key: 'k-1', intent: { ...intent, runId: 'run_1', ...damage } })
    expect(createPaymentIntentKeeper({ storage, runId: run }).peek()).toBeNull()
    expect(map.has(entryKey)).toBe(false)
  })

  it('anti-vacuum: a well-formed record (key at the grammar edge, 128 characters) IS restored', () => {
    const { storage } = storeWith({ v: 1, key: 'A.b_c:d-'.repeat(16), intent: { ...intent, runId: 'run_1' } })
    expect(createPaymentIntentKeeper({ storage, runId: run }).peek()).toMatchObject({ unknown: true })
  })

  it('a closed record is remembered by the instance and is not lifted from a storage that could not remove it', () => {
    const map = new Map<string, string>()
    const storage = {
      getItem: (k: string) => map.get(k) ?? null,
      setItem: (k: string, v: string) => void map.set(k, v),
      removeItem: () => { throw new Error('denied') },
    }
    const keeper = createPaymentIntentKeeper({ storage, runId: run })
    const record = begin(keeper, { ...intent, runId: 'run_1' })
    keeper.markSent(record)
    keeper.markUnknown(record)
    keeper.settle(record)
    expect(keeper.peek()).toBeNull()

    const second = begin(keeper, { ...intent, runId: 'run_1' })
    keeper.markSent(second)
    keeper.discard()
    expect(keeper.peek()).toBeNull()
    expect(map.size, 'premise: the entry is still in the storage').toBe(1)

    // a NEW instance (a reload) honestly finds it again
    expect(createPaymentIntentKeeper({ storage, runId: run }).peek()).not.toBeNull()
  })
})
