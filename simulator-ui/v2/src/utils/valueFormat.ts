import { uiLocaleRef, type UiLocale } from '../i18n/locale'
import { t } from '../i18n/t'
import { parseAmountStringOrNull } from './numberFormat'

export function emptyToNull(v: unknown): string | number | null {
  if (v == null) return null
  if (typeof v === 'number') return Number.isFinite(v) ? v : null
  if (typeof v === 'string') {
    const s = v.trim()
    return s ? s : null
  }
  return null
}

export function emptyToNullString(v: unknown): string | null {
  if (v == null) return null
  const s = String(v).trim()
  return s ? s : null
}

export function renderOrDash(v: unknown): string {
  if (v == null) return '—'
  const s = String(v).trim()
  return s ? s : '—'
}

/**
 * 026 `T2602`: `available` is signed; a negative one is the debt's excess over a lowered limit, not a payable amount.
 * 046 wave 2: the words of the excess are the dictionary's (`common.overLimit`). 046 wave 3: in the language of the
 * interface unless one is given (until wave 3 English by default - the participant card and the line window were English
 * as a whole then). The trust line window reads `isOverLimit` and says it in its own sentence.
 */
export function renderAvailable(v: unknown, locale: UiLocale = uiLocaleRef.value): string {
  const s = renderOrDash(v)
  return isOverLimit(s) ? t('common.overLimit', { amount: s.slice(1) }, locale) : s
}

/**
 * 046 (advisor's decision, round 1, 2026-10-18): an amount a PERSON typed, as the form sends it - or null. A comma is
 * refused (never read as the decimal point: "1,000" meant as a thousand must not become 1.000), one rule for both
 * languages and every equivalent. `parseAmountStringOrNull` itself still reads ',' as '.' for machine values (a snapshot
 * figure); the windows of the actions read what a person typed through this. Until that date the forms read "1,5" as 1.5.
 */
export function parseTypedAmount(raw: string): string | null {
  return raw.includes(',') ? null : parseAmountStringOrNull(raw)
}

/**
 * 046 (advisor, round 1): what to tell a person whose amount the form cannot read. A comma is refused with the amount as
 * it should be typed ("Use a dot: 1.5") when replacing it gives a number; anything else gets the general format hint.
 */
export function amountInputHint(raw: string, locale?: UiLocale): string {
  const s = raw.trim()
  const withDot = s.replaceAll(',', '.')
  if (s.includes(',') && /^\d+(?:\.\d+)?$/.test(withDot)) return t('common.commaRefused', { example: withDot }, locale)
  return t('common.invalidAmount', {}, locale)
}

/** A rendered `available` that is an excess over the limit (a leading minus), not an amount that can be sent. */
export function isOverLimit(rendered: string): boolean {
  return rendered.startsWith('-')
}
