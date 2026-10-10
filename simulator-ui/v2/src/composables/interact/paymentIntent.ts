/**
 * The life of the idempotency key of a manual payment (037 slice A2). No Vue, no network: rules only.
 *
 * ONE RULE. The same key and the same body belong to the same INTENT - run, sender, receiver, equivalent and the amount
 * exactly as it is sent (`"10"` and `"10.00"` are different requests to the server) - ALWAYS, except when the server
 * answered `details.idempotency_key_spent: true` (an `ABORTED` row stands under the key: it is refused for good). A known
 * success also ends the intent. `idempotency_key_spent: false`, or no such field, never means "the key may be thrown
 * away": a commit that did not land leaves no row, one that did leaves the payment, and a repeat under the same key
 * returns either the payment or pays once.
 *
 * Any change of the intent is a new intent with a new key - EXCEPT while an intent is UNRESOLVED (an attempt of it ended
 * without a verdict). That intent is held with its key and its frozen body, and nothing automatic moves it: another intent
 * does not replace it and gets no key (`begin` is `blocked`), the page being left or reloaded does not lose it (it is kept in
 * `sessionStorage`), and only a person's explicit `discard` ends it without a verdict.
 */

export type PaymentIntent = {
  runId: string
  from: string
  to: string
  equivalent: string
  /** The amount in the exact spelling that goes into the request. */
  amount: string
}

export function intentFingerprint(intent: PaymentIntent): string {
  return JSON.stringify([intent.runId, intent.from, intent.to, intent.equivalent.toUpperCase(), intent.amount])
}

/** Hex of 16 random bytes, with the key grammar of the server (`[A-Za-z0-9._:-]`, 1-128). */
function randomHex(): string | null {
  const c = (globalThis as { crypto?: Crypto }).crypto
  if (!c || typeof c.getRandomValues !== 'function') return null
  const bytes = new Uint8Array(16)
  c.getRandomValues(bytes)
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

/**
 * A fresh key. `crypto.randomUUID()` where the platform has it. FALLBACKS, in order, named so nobody mistakes them for
 * the default: `crypto.getRandomValues` (32 hex digits), and - only on a platform with no `crypto` at all, which no
 * supported browser is - a clock-and-`Math.random` string, which is NOT collision-proof across tabs (the server scopes a
 * key by run, so a collision needs the same run and the same body to matter).
 */
export function newIdempotencyKey(): string {
  const c = (globalThis as { crypto?: Crypto }).crypto
  if (c && typeof c.randomUUID === 'function') return c.randomUUID()
  const hex = randomHex()
  if (hex) return `k-${hex}`
  return `k-weak-${Date.now().toString(36)}-${Math.random().toString(16).slice(2)}`
}

/** `details.idempotency_key_spent === true` and nothing else: `false` and an absent field are not "spent". */
export function isKeySpent(details: unknown): boolean {
  return (
    !!details &&
    typeof details === 'object' &&
    (details as Record<string, unknown>).idempotency_key_spent === true
  )
}

/**
 * Did a payment request that WAS SENT fail without a usable verdict on the payment? Yes when there was no HTTP answer
 * (network failure, timeout, a cancel after sending), when a 2xx answer could not be accepted (unreadable or broken body),
 * and for 408 and 5xx - EXCEPT an answer that says the key is spent, which is a verdict (refused for good).
 * A request that was never sent (no run id) is not unknown.
 */
export function paymentOutcomeUnknown(
  error: { status?: unknown; details?: unknown },
  sent: boolean,
): boolean {
  if (!sent) return false
  if (isKeySpent(error.details)) return false
  const status = typeof error.status === 'number' ? error.status : 0
  return status === 0 || (status >= 200 && status < 300) || status === 408 || status >= 500
}

export type PaymentIntentRecord = {
  fingerprint: string
  key: string
  intent: PaymentIntent
  /** An attempt of this intent ended without a verdict (or is in flight), and nothing since has settled it. */
  unknown: boolean
}

export type BeginResult =
  | { kind: 'record'; record: PaymentIntentRecord }
  /** An unresolved intent stands and `intent` is another one: no key is issued. */
  | { kind: 'blocked'; unresolved: PaymentIntentRecord }

type IntentStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

const STORAGE_PREFIX = 'geo.sim.v2.unresolvedPayment.'

/** `sessionStorage` where the platform has it and lets us touch it; otherwise null (the page then works without it). */
function defaultStorage(): IntentStorage | null {
  try {
    return typeof sessionStorage === 'undefined' ? null : sessionStorage
  } catch {
    return null
  }
}

function isIntent(v: unknown): v is PaymentIntent {
  if (!v || typeof v !== 'object') return false
  const o = v as Record<string, unknown>
  return ['runId', 'from', 'to', 'equivalent', 'amount'].every((k) => typeof o[k] === 'string' && (o[k] as string).length > 0)
}

/**
 * The keeper of the ONE payment intent of a screen.
 *
 * Persistence (R3): only an UNRESOLVED record is stored - at the moment a request is about to leave (`markSent`: a page left
 * mid-flight has an unknown outcome too) and while it is unknown - under a key that includes the run, and removed on a verdict
 * (`settle`), on a definitive refusal of a request that was not unresolved before, and on `discard`. Every access to the
 * storage is in try/catch: without a usable storage the keeper still holds the record in memory (it then survives a closed
 * panel but not a reload). Not a log of payments: at most one record per run.
 */
export function createPaymentIntentKeeper(
  o: { runId?: () => string; storage?: IntentStorage | null } = {},
) {
  const storage: IntentStorage | null = o.storage === undefined ? defaultStorage() : o.storage
  const runIdNow = o.runId ?? (() => '')
  let current: PaymentIntentRecord | null = null
  let loadedFor: string | null = null

  const storageKey = (runId: string) => `${STORAGE_PREFIX}${runId}`

  function write(record: PaymentIntentRecord): void {
    if (!storage) return
    try {
      storage.setItem(storageKey(record.intent.runId), JSON.stringify({ v: 1, key: record.key, intent: record.intent }))
    } catch {
      /* no storage: memory only */
    }
  }

  function erase(record: PaymentIntentRecord): void {
    if (!storage) return
    try {
      storage.removeItem(storageKey(record.intent.runId))
    } catch {
      /* no storage: memory only */
    }
  }

  /** The unresolved record of the current run, once per run, when nothing is held in memory. */
  function restore(): void {
    if (current) return
    const runId = runIdNow()
    if (loadedFor === runId) return
    loadedFor = runId
    if (!storage || !runId) return
    try {
      const raw = storage.getItem(storageKey(runId))
      if (!raw) return
      const parsed = JSON.parse(raw) as { v?: unknown; key?: unknown; intent?: unknown }
      if (parsed.v !== 1 || typeof parsed.key !== 'string' || !isIntent(parsed.intent) || parsed.intent.runId !== runId) return
      current = { fingerprint: intentFingerprint(parsed.intent), key: parsed.key, intent: parsed.intent, unknown: true }
    } catch {
      /* unreadable entry or no storage: nothing to restore */
    }
  }

  return {
    /** The record of `intent`: the held one when it is the same intent; a new one with a new key; or `blocked` by an unresolved one. */
    begin(intent: PaymentIntent): BeginResult {
      restore()
      const fingerprint = intentFingerprint(intent)
      if (current && current.fingerprint === fingerprint) return { kind: 'record', record: current }
      if (current && current.unknown) return { kind: 'blocked', unresolved: current }
      current = { fingerprint, key: newIdempotencyKey(), intent: { ...intent }, unknown: false }
      return { kind: 'record', record: current }
    },
    /** A request of `record` is about to leave: from now on it is unresolved until a verdict. */
    markSent(record: PaymentIntentRecord): void {
      if (current === record) write(record)
    },
    /** The attempt had no verdict: the key stays, the intent stays unresolved (and stored). */
    markUnknown(record: PaymentIntentRecord): void {
      if (current !== record) return
      record.unknown = true
      write(record)
    },
    /** A definitive refusal of an attempt: nothing is unresolved unless an EARLIER attempt of the same intent was. */
    markRefused(record: PaymentIntentRecord): void {
      if (current === record && !record.unknown) erase(record)
    },
    /** A verdict: the payment was made, or the key is spent. The next confirmation of the same intent is a new payment. */
    settle(record: PaymentIntentRecord): void {
      if (current !== record) return
      erase(record)
      current = null
      loadedFor = null
    },
    /** A person's explicit decision to give the unresolved intent up (the first payment may have been made). */
    discard(): void {
      restore()
      if (current) erase(current)
      current = null
      loadedFor = null
    },
    peek(): PaymentIntentRecord | null {
      restore()
      return current
    },
  }
}
