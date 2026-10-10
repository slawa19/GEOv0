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
 * Any change of the intent is a new intent with a new key.
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
  /** An attempt of this intent ended without a verdict, and nothing since has settled it. */
  unknown: boolean
}

export function createPaymentIntentKeeper() {
  let current: PaymentIntentRecord | null = null

  return {
    /** The record of `intent`: the stored one when it is the same intent, else a new one with a new key. */
    begin(intent: PaymentIntent): PaymentIntentRecord {
      const fingerprint = intentFingerprint(intent)
      if (current && current.fingerprint === fingerprint) return current
      current = { fingerprint, key: newIdempotencyKey(), intent: { ...intent }, unknown: false }
      return current
    },
    /** The last attempt had no verdict: the key stays, and the intent stays unsettled. */
    markUnknown(record: PaymentIntentRecord): void {
      if (current === record) record.unknown = true
    },
    /** A verdict: the payment was made, or the key is spent. The next confirmation of the same intent is a new payment. */
    settle(record: PaymentIntentRecord): void {
      if (current === record) current = null
    },
    peek(): PaymentIntentRecord | null {
      return current
    },
  }
}
