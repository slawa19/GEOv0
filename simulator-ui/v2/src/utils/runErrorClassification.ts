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
