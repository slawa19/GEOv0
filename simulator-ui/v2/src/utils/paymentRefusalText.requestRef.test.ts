/**
 * 034 S5a fix-delta, item 3: EVERY return branch of the refusal texts shows the request id.
 * The branches are enumerated from the source of `paymentRefusalText` / `clearingRefusalText`:
 *   payment:  (1) a known `details.reason`; (2) a known `code` without a reason (CODES map);
 *             (3) a generic code (INVALID_AMOUNT, PAYMENT_REJECTED); (4) anything else -> the server's message.
 *   clearing: (5) CLEARING_REFUSED + occurrence_amount_not_in_step; (6) anything else -> the server's message.
 */
import { describe, expect, it } from 'vitest'

import { clearingRefusalText, paymentRefusalText, type UiLocale } from './paymentRefusalText'

type Case = { branch: string; error: Record<string, unknown>; text: (e: unknown, l: UiLocale) => string }

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

describe('every branch of the refusal texts carries (ref: <id>)', () => {
  it.each(CASES.flatMap((c) => (['en', 'ru'] as UiLocale[]).map((l) => [c.branch, l, c] as const)))(
    '%s [%s]',
    (_branch, locale, c) => {
      const text = c.text(c.error, locale)
      expect(text).toContain('(ref: req-x)')
      expect(text.match(/\(ref:/g)).toHaveLength(1)
    },
  )

  it('anti-vacuum: the same branches without an id add no reference, and the texts differ per branch', () => {
    const texts = CASES.map((c) => c.text({ ...c.error, requestId: null }, 'en'))
    for (const t of texts) expect(t).not.toContain('(ref:')
    expect(new Set(texts).size).toBeGreaterThanOrEqual(4)
  })
})
