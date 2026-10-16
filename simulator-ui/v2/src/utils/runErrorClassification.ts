import type { RunError } from '../api/simulatorTypes'
import { clearingStepRefusalText, uiLocale, type UiLocale } from './paymentRefusalText'

export function isUserFacingRunErrorCode(code: string): boolean {
  const c = code.toUpperCase()
  if (!c) return false
  if (c === 'PAYMENT_TIMEOUT') return true
  if (c === 'INTERNAL_ERROR') return true
  // 031 item 17: the tick's clearing step refusal is named by the server (030 S2); every other clearing failure
  // stays the sanitised CLEARING_ERROR and is not shown.
  if (c === 'CLEARING_REFUSED') return true
  return false
}

/**
 * The error line of a run status (BACKLOG 037-5 (a)). A run in `error` was stopped by `last_error`: it is shown whatever
 * its code, because it is the only answer to "why did the run stop". For a run that goes on, the list of user-facing
 * codes decides as before. Not judged here: whether the server's message is fit for a person - it is printed as sent.
 */
export function runStatusErrorText(
  status: { state?: string | null; last_error?: RunError | null },
  isUserFacing: (code: string) => boolean,
): string {
  const error = status.last_error
  if (!error) return ''
  const stopped = String(status.state ?? '').trim().toLowerCase() === 'error'
  return stopped || isUserFacing(error.code) ? runErrorText(error) : ''
}

/** The run-level error line: the step refusal is a human text in the interface language, the rest is `CODE: message`. */
export function runErrorText(error: RunError, locale: UiLocale = uiLocale()): string {
  if (error.code.toUpperCase() === 'CLEARING_REFUSED') return clearingStepRefusalText(null, locale)
  return `${error.code}: ${error.message}`
}

/** 043 S2: the machine code the server gives a create refused by the global limit of active runs (`run_lifecycle.py`). */
export const GLOBAL_ACTIVE_LIMIT_CONFLICT = 'global_active_limit'

/** `error.details.conflict_kind` of an HTTP 409 answer in the server's envelope (`{"error": {"code", "message", "details"}}`), else null. */
function conflictKindOf409(e: unknown): string | null {
  const err = (e && typeof e === 'object' ? e : {}) as { status?: unknown; bodyText?: unknown }
  if (err.status !== 409 || typeof err.bodyText !== 'string') return null
  try {
    const body = JSON.parse(err.bodyText) as { error?: { details?: { conflict_kind?: unknown } } }
    const kind = body?.error?.details?.conflict_kind
    return typeof kind === 'string' ? kind : null
  } catch {
    return null
  }
}

/**
 * 043 S2 (advisor's follow-up): the error line of a run start that failed. One case gets a human text in the interface
 * language: the server refused the create because its global limit of active runs is reached (a 409 whose
 * `conflict_kind` is `global_active_limit` - matched on the machine code, never on the message). On a shared server
 * that is a second visitor while somebody else's run is live; that run is not this visitor's to watch. Every other
 * error - another 409 included - keeps the text it had (`fallback`).
 */
export function startRunErrorText(e: unknown, fallback: (e: unknown) => string, locale: UiLocale = uiLocale()): string {
  if (conflictKindOf409(e) === GLOBAL_ACTIVE_LIMIT_CONFLICT) {
    return locale === 'ru'
      ? 'Демо уже кто-то смотрит на этом сервере; попробуйте позже. Запустить снова — Start в «Подробнее».'
      : 'Someone is already watching the demo on this server; try again later. To start again, use Start under "Подробнее".'
  }
  return fallback(e)
}
