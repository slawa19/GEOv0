import { compareMoney } from '../../utils/money'
import { parseAmountStringOrNull } from '../../utils/numberFormat'

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

/** The grammar of the server for `idempotency_key` (`SimulatorActionPaymentRealRequest`): 1-128 of `[A-Za-z0-9._:-]`. */
export const IDEMPOTENCY_KEY_GRAMMAR = /^[A-Za-z0-9._:-]{1,128}$/

export function isValidIdempotencyKey(key: unknown): key is string {
  return typeof key === 'string' && IDEMPOTENCY_KEY_GRAMMAR.test(key)
}

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
  /** The storage could not be READ: whether an unresolved payment exists for this run is unknown, so no intent may start. */
  | { kind: 'unreadable' }

type IntentStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

const STORAGE_PREFIX = 'geo.sim.v2.unresolvedPayment.'

/** `sessionStorage` where the platform has it and lets us touch it; otherwise null (and then NO payment is sent, see `markSent`). */
function defaultStorage(): IntentStorage | null {
  try {
    return typeof sessionStorage === 'undefined' ? null : sessionStorage
  } catch {
    return null
  }
}

/**
 * The SHAPE of a stored intent: non-empty strings, and an amount that is a positive plain decimal exactly as it would be sent
 * (the grammar of the amount field, no normalisation: the spelling is part of the request).
 */
function isIntent(v: unknown): v is PaymentIntent {
  if (!v || typeof v !== 'object') return false
  const o = v as Record<string, unknown>
  if (!['runId', 'from', 'to', 'equivalent', 'amount'].every((k) => typeof o[k] === 'string' && (o[k] as string).length > 0)) return false
  const amount = o.amount as string
  return parseAmountStringOrNull(amount) === amount && compareMoney(amount, '0') === 1
}

/**
 * The keeper of the ONE payment intent of a screen.
 *
 * LIMITS OF THE CONSTRUCTION, named: the record lives in the `sessionStorage` of ONE tab. A second tab, another browser or
 * another device knows nothing of an unresolved payment, and a new payment confirmed there gets a new key. A storage that
 * cannot REMOVE an entry cannot forget it either: within this instance a closed record (a verdict, a discard) is
 * remembered by its key and is not lifted from the storage again, but after a page reload the entry is found again - which
 * is honest: "Check / repeat" then returns the stored result and closes it.
 *
 * Persistence (R3): only an UNRESOLVED record is stored - at the moment a request is about to leave (`markSent`: a page left
 * mid-flight has an unknown outcome too) and while it is unknown - under a key that includes the run (a record of one run can
 * never overwrite another's), and removed on a verdict (`settle`), on a definitive refusal of a request that was not
 * unresolved before, and on `discard`. Not a log of payments: at most one record per run.
 *
 * A PAYMENT IS NOT SENT UNLESS ITS RECORD IS SAVED. `markSent` answers whether the record was written AND reads back as
 * written; when it is not (no storage, the browser refuses the write, the quota is full) the caller sends nothing. A request
 * that left without a record could not be checked after a reload: the same intent would get a NEW key and the server would
 * execute it a second time. Likewise, when the storage cannot be READ at the restore, `begin` answers `unreadable` and no
 * new intent starts - whether an unresolved payment exists is not known. (Narrowing, on purpose: with no usable storage the
 * page does not send manual payments at all. `sessionStorage` survives a reload of the tab; closing the tab is not promised
 * to keep it.) Reads and removals that fail elsewhere are swallowed.
 */
export function createPaymentIntentKeeper(
  o: { runId?: () => string; storage?: IntentStorage | null } = {},
) {
  const storage: IntentStorage | null = o.storage === undefined ? defaultStorage() : o.storage
  const runIdNow = o.runId ?? (() => '')
  let current: PaymentIntentRecord | null = null
  let loadedFor: string | null = null
  /** Keys of records this instance has closed (a verdict or a discard): never restored from the storage again. */
  const closedKeys = new Set<string>()

  const storageKey = (runId: string) => `${STORAGE_PREFIX}${runId}`

  /** Writes the record and reads it back; true only when the storage now holds exactly what was written. */
  function write(record: PaymentIntentRecord): boolean {
    if (!storage) return false
    const name = storageKey(record.intent.runId)
    const value = JSON.stringify({ v: 1, key: record.key, intent: record.intent })
    try {
      storage.setItem(name, value)
    } catch {
      /* refused (quota, privacy mode): what the storage holds is checked below - an identical entry already there is fine */
    }
    try {
      return storage.getItem(name) === value
    } catch {
      return false
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

  function dropEntry(runId: string): void {
    if (!storage) return
    try {
      storage.removeItem(storageKey(runId))
    } catch {
      /* no storage: memory only */
    }
  }

  /**
   * The unresolved record of the current run, once per run, when nothing is held in memory. `unreadable`: the storage threw on
   * the read - nothing is known about this run, and the next call tries again (the storage may come back).
   */
  function restore(): 'ok' | 'unreadable' {
    if (current) return 'ok'
    const runId = runIdNow()
    if (loadedFor === runId) return 'ok'
    if (!storage || !runId) {
      loadedFor = runId
      return 'ok'
    }
    let raw: string | null
    try {
      raw = storage.getItem(storageKey(runId))
    } catch {
      return 'unreadable'
    }
    loadedFor = runId
    try {
      if (!raw) return 'ok'
      const parsed = JSON.parse(raw) as { v?: unknown; key?: unknown; intent?: unknown }
      // A record is only as good as what a request needs: the key by the SERVER's grammar, the intent by shape. Anything
      // else is damage, and a damaged record must never be sent as a payment (a missing key would be a NEW payment).
      if (
        parsed.v !== 1 ||
        !isValidIdempotencyKey(parsed.key) ||
        !isIntent(parsed.intent) ||
        parsed.intent.runId !== runId ||
        closedKeys.has(parsed.key)
      ) {
        dropEntry(runId)
        return 'ok'
      }
      current = { fingerprint: intentFingerprint(parsed.intent), key: parsed.key, intent: parsed.intent, unknown: true }
    } catch {
      /* a damaged entry (not JSON): nothing to restore */
      dropEntry(runId)
    }
    return 'ok'
  }

  return {
    /** The record of `intent`: the held one when it is the same intent; a new one with a new key; or `blocked` by an unresolved one. */
    begin(intent: PaymentIntent): BeginResult {
      if (restore() === 'unreadable' && !current) return { kind: 'unreadable' }
      const fingerprint = intentFingerprint(intent)
      if (current && current.fingerprint === fingerprint) return { kind: 'record', record: current }
      if (current && current.unknown) return { kind: 'blocked', unresolved: current }
      current = { fingerprint, key: newIdempotencyKey(), intent: { ...intent }, unknown: false }
      return { kind: 'record', record: current }
    },
    /**
     * A request of `record` is about to leave: from now on it is unresolved until a verdict. Returns whether the record is
     * SAVED (written and read back); when it is not, the request must NOT leave.
     */
    markSent(record: PaymentIntentRecord): boolean {
      return current === record && write(record)
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
      closedKeys.add(record.key)
      current = null
      loadedFor = null
    },
    /** A person's explicit decision to give the unresolved intent up (the first payment may have been made). */
    discard(): void {
      restore()
      if (current) {
        erase(current)
        closedKeys.add(current.key)
      }
      current = null
      loadedFor = null
    },
    peek(): PaymentIntentRecord | null {
      restore()
      return current
    },
  }
}
