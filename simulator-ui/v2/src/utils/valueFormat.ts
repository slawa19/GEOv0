import type { UiLocale } from '../i18n/locale'
import { t } from '../i18n/t'

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
 * 046 wave 2: the words of the excess are the dictionary's (`common.overLimit`). English unless a language is given:
 * the callers left (the participant card and the line window, wave 3) are English as a whole until wave 3 moves them,
 * and a Russian phrase inside their English text would be the mixed string spec 046 forbids. The trust line window
 * reads `isOverLimit` and says it in its own sentence.
 */
export function renderAvailable(v: unknown, locale: UiLocale = 'en'): string {
  const s = renderOrDash(v)
  return isOverLimit(s) ? t('common.overLimit', { amount: s.slice(1) }, locale) : s
}

/** A rendered `available` that is an excess over the limit (a leading minus), not an amount that can be sent. */
export function isOverLimit(rendered: string): boolean {
  return rendered.startsWith('-')
}
