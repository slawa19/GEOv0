import type { RunError } from '../api/simulatorTypes'
import { localizedLine } from '../i18n/localizedLine'
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
  status: { state?: string | null; last_error?: RunError | null; stop_reason?: string | null },
  isUserFacing: (code: string) => boolean,
): string {
  // 044 stage 2 (decision 2): a run the server stopped because nobody watched it says so, in the interface language.
  if (String(status.state ?? '').trim().toLowerCase() === 'stopped' && status.stop_reason === IDLE_STOP_REASON) return idleStopText()
  const error = status.last_error
  if (!error) return ''
  const stopped = String(status.state ?? '').trim().toLowerCase() === 'error'
  return stopped || isUserFacing(error.code) ? runErrorText(error) : ''
}

/** The run-level error line: the step refusal is a human text in the interface language, the rest is `CODE: message`. */
export function runErrorText(error: RunError, locale: UiLocale = uiLocale()): string {
  // 046 wave 1 (§15 P2): a line kept in the error line is made through `localizedLine`, so a switch remakes it.
  if (error.code.toUpperCase() === 'CLEARING_REFUSED') return localizedLine((l) => clearingStepRefusalText(null, l), locale)
  return `${error.code}: ${error.message}`
}

/** 044 stage 2: `stop_reason` of a run the server stopped because it had no SSE subscriber (`run_lifecycle.py`). */
export const IDLE_STOP_REASON = 'idle_no_subscribers'

/**
 * 044 stage 2: the line of a run stopped by the idle stop. 046 wave 1 (merge of #299): from the dictionary
 * (`runLine.idleStopped`); the way back is the reload - the earlier text named Start under "Подробнее", which put a
 * Russian word in the English text and an English one in the Russian (the service row is not translated yet).
 */
export function idleStopText(locale: UiLocale = uiLocale()): string {
  return localizedLine((l) => t('runLine.idleStopped', undefined, l), locale)
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
 * 046 wave 1: does the error line offer "Try again"? Only while the line still IS the text of the refused start
 * (`retryableStartError`, recorded by `startRun`): any later error that replaces the line - a stream error, a pause
 * that failed - withdraws the offer with it, so "Try again" never stands beside an error it would not fix.
 */
export function startRetryOffered(lastError: string | null | undefined, retryableStartError: string | null | undefined): boolean {
  return !!retryableStartError && retryableStartError === lastError
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
    // 046 wave 1 (§15 P2): the line keeps its cause (the kind and the server's number), not one language's text.
    return localizedLine((l) => (n === null ? t('runStart.limitBusy', undefined, l) : n === 1 ? t('runStart.limitOne', undefined, l)
      : tn('runStart.limitAll', n, undefined, l)), locale)
  }
  return fallback(e)
}

/** 043 S2 (review B5): the error line when the server offers no scenario at all - the opening has nothing to show. */
export function noScenarioText(locale: UiLocale = uiLocale()): string {
  return localizedLine((l) => t('runStart.noScenario', undefined, l), locale)
}

/**
 * 044 stage 2 (BACKLOG 043-19): the error line when Start or Apply is asked with a tempo below 1 (the field allows 0).
 * A run at 0 makes no payment - a silent run that looks broken; it is refused with this hint instead, and the typed
 * value is not replaced by another one behind the person's back. Stopping the payments is the pause.
 */
export function zeroIntensityText(locale: UiLocale = uiLocale()): string {
  return localizedLine((l) => t('runLine.zeroIntensity', undefined, l), locale)
}
