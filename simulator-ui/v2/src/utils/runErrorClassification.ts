import type { RunError } from '../api/simulatorTypes'
import { t, tn } from '../i18n/t'
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

/** `error.details` of an HTTP 409 answer in the server's envelope (`{"error": {"code", "message", "details"}}`), else null. */
function detailsOf409(e: unknown): Record<string, unknown> | null {
  const err = (e && typeof e === 'object' ? e : {}) as { status?: unknown; bodyText?: unknown }
  if (err.status !== 409 || typeof err.bodyText !== 'string') return null
  try {
    const body = JSON.parse(err.bodyText) as { error?: { details?: unknown } }
    const details = body?.error?.details
    return details && typeof details === 'object' && !Array.isArray(details) ? (details as Record<string, unknown>) : null
  } catch {
    return null
  }
}

/**
 * 046 wave 1: was this start refused by the global limit of active runs? A 409 whose `details.conflict_kind` is
 * `global_active_limit` - the machine code, never the message. Such a refusal is worth repeating (a place frees when
 * another visitor leaves): the top bar offers "Try again" beside it (`useSimulatorRealMode.ts` records the text).
 */
export function isGlobalLimitRefusal(e: unknown): boolean {
  return detailsOf409(e)?.conflict_kind === GLOBAL_ACTIVE_LIMIT_CONFLICT
}

/**
 * 043 S2 (advisor's follow-up), 046 wave 1: the error line of a run start that failed. One case gets a human text in the
 * interface language: the server refused the create because its global limit of active runs is reached
 * (`isGlobalLimitRefusal`). On a shared server that is another visitor's live run, not this visitor's to watch. The
 * number of places is the server's (`details.max_active_runs`, a positive integer - `run_lifecycle.py` has sent it with
 * `active_runs` since 043 S2), never a constant here; without a usable number the text names none. The text no longer
 * points at Start under "Details": the line itself offers "Try again". Every other error - another 409 included - keeps
 * the text it had (`fallback`).
 */
export function startRunErrorText(e: unknown, fallback: (e: unknown) => string, locale: UiLocale = uiLocale()): string {
  if (isGlobalLimitRefusal(e)) {
    const raw = detailsOf409(e)?.max_active_runs
    const n = typeof raw === 'number' && Number.isInteger(raw) && raw > 0 ? raw : null
    if (n === null) return t('runStart.limitBusy', undefined, locale)
    if (n === 1) return t('runStart.limitOne', undefined, locale)
    return tn('runStart.limitAll', n, undefined, locale)
  }
  return fallback(e)
}

/** 043 S2 (review B5): the error line when the server offers no scenario at all - the opening has nothing to show. */
export function noScenarioText(locale: UiLocale = uiLocale()): string {
  return t('runStart.noScenario', undefined, locale)
}
