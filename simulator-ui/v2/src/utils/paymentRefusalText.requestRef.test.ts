/**
 * 034 S5a fix-delta, item 3: EVERY return branch of the refusal texts shows the request id.
 * The branches are enumerated from the source of `paymentRefusalText` / `clearingRefusalText`:
 *   payment:  (1) a known `details.reason`; (2) a known `code` without a reason (CODES map);
 *             (3) a generic code (INVALID_AMOUNT, PAYMENT_REJECTED); (4) anything else -> the text of its code, or the
 *             general text with the code (046 wave 2; was: the server's message).
 *   clearing: (5) CLEARING_REFUSED + occurrence_amount_not_in_step; (6) anything else -> the text of its code (same).
 *
 * 048 S3 (decision 3), rewritten - not removed: the id is no longer glued into the sentence as «(код запроса: …)»; every
 * branch carries it APART (`ShownError.ref`), and the sentence holds no id and no «ref»/«код запроса».
 */
import { describe, expect, it } from 'vitest'

import { clearingRefusalText, paymentRefusalText, type ShownError, type UiLocale } from './paymentRefusalText'

type Case = { branch: string; error: Record<string, unknown>; text: (e: unknown, l: UiLocale) => ShownError }

const withId = (e: Record<string, unknown>) => ({ message: 'server words', requestId: 'req-x', ...e })

const CASES: Case[] = [
  { branch: '1 known reason', error: withId({ code: 'PAYMENT_REJECTED', details: { reason: 'no_route' } }), text: (e, l) => paymentRefusalText(e, 'UAH', l) },
  { branch: '2 known code, no reason', error: withId({ code: 'NO_ROUTE' }), text: (e, l) => paymentRefusalText(e, 'UAH', l) },
  { branch: '3a generic code INVALID_AMOUNT', error: withId({ code: 'INVALID_AMOUNT' }), text: (e, l) => paymentRefusalText(e, 'UAH', l) },
  { branch: '3b generic code PAYMENT_REJECTED', error: withId({ code: 'PAYMENT_REJECTED' }), text: (e, l) => paymentRefusalText(e, 'UAH', l) },
  { branch: '4 unknown code', error: withId({ code: 'SOMETHING_NEW' }), text: (e, l) => paymentRefusalText(e, 'UAH', l) },
  {
    branch: '5 clearing step refusal',
    error: withId({ code: 'CLEARING_REFUSED', details: { reason: 'occurrence_amount_not_in_step' } }),
    text: (e, l) => clearingRefusalText(e, 'UAH', l),
  },
  { branch: '6 clearing other', error: withId({ code: 'CLEARING_FAILED' }), text: (e, l) => clearingRefusalText(e, 'UAH', l) },
]

describe('every branch of the refusal texts carries the request id - apart from the sentence (048 S3)', () => {
  it.each(CASES.flatMap((c) => (['en', 'ru'] as UiLocale[]).map((l) => [c.branch, l, c] as const)))(
    '%s [%s]',
    (_branch, locale, c) => {
      const shown = c.text(c.error, locale)
      expect(shown.ref).toBe('req-x')
      // The sentence is the sentence only: no id, no «ref» / «код запроса» (red before S3: "… (ref: req-x)").
      expect(shown.text).not.toContain('req-x')
      expect(shown.text).not.toMatch(/\(ref:|код запроса/)
      expect(shown.text.trim()).not.toBe('')
    },
  )

  it('anti-vacuum: the same branches without an id carry no id, and the texts differ per branch', () => {
    const shown = CASES.map((c) => c.text({ ...c.error, requestId: null }, 'en'))
    for (const s of shown) expect(s.ref).toBeNull()
    expect(new Set(shown.map((s) => s.text)).size).toBeGreaterThanOrEqual(4)
  })
})
