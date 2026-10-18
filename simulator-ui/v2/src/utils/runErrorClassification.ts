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

/** `error.details` of an HTTP 409 answer in the server's envelope (`{"error": {"code", "message", "details"}}`), else null. */
function detailsOf409(e: unknown): Record<string, unknown> | null {
  const err = (e && typeof e === 'object' ? e : {}) as { status?: unknown; bodyText?: unknown }
  if (err.status !== 409 || typeof err.bodyText !== 'string') return null
  try {
    const body = JSON.parse(err.bodyText) as { error?: { details?: unknown } }
    const details = body?.error?.details
    return details && typeof details === 'object' ? (details as Record<string, unknown>) : null
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
  const details = detailsOf409(e)
  if (details?.conflict_kind === GLOBAL_ACTIVE_LIMIT_CONFLICT) {
    // 044 stage 2 (decision 3): the number of places comes from the answer (`details.max_active_runs`), never from here.
    const raw = details.max_active_runs
    const n = typeof raw === 'number' && Number.isInteger(raw) && raw > 0 ? raw : null
    if (n !== null) {
      if (n === 1) {
        return locale === 'ru'
          ? 'Сейчас занято единственное место: демо смотрит 1 человек. Попробуйте через минуту — Start в «Подробнее».'
          : 'The only place is taken right now: 1 person is watching the demo. Try again in a minute - Start under "Подробнее".'
      }
      return locale === 'ru'
        ? `Сейчас заняты все ${n} ${ruPlural(n, 'место', 'места', 'мест')}: демо ${ruPlural(n, 'смотрит', 'смотрят', 'смотрят')} ${n} ${ruPlural(n, 'человек', 'человека', 'человек')}. Попробуйте через минуту — Start в «Подробнее».`
        : `All ${n} places are taken right now: ${n} people are watching the demo. Try again in a minute - Start under "Подробнее".`
    }
    return locale === 'ru'
      ? 'Демо уже кто-то смотрит на этом сервере; попробуйте позже. Запустить снова — Start в «Подробнее».'
      : 'Someone is already watching the demo on this server; try again later. To start again, use Start under "Подробнее".'
  }
  return fallback(e)
}

/** 043 S2 (review B5): the error line when the server offers no scenario at all - the opening has nothing to show. */
export function noScenarioText(locale: UiLocale = uiLocale()): string {
  return locale === 'ru' ? 'На сервере нет сценариев: показать нечего.' : 'The server offers no scenario: there is nothing to show.'
}

/** Russian plural of a count: 1 место, 2 места, 5 мест (forms: one, few, many). */
function ruPlural(n: number, one: string, few: string, many: string): string {
  const m10 = n % 10
  const m100 = n % 100
  if (m10 === 1 && m100 !== 11) return one
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few
  return many
}

/**
 * 044 stage 2 (BACKLOG 043-19): the error line when Start or Apply is asked with a tempo below 1 (the field allows 0).
 * A run at 0 makes no payment - a silent run that looks broken; it is refused with this hint instead, and the typed
 * value is not replaced by another one behind the person's back. Stopping the payments is the pause.
 */
export function zeroIntensityText(locale: UiLocale = uiLocale()): string {
  return locale === 'ru'
    ? 'Темп 0 — прогон без платежей: укажите темп от 1 до 100. Остановить платежи — пауза.'
    : 'A tempo of 0 makes no payments: enter a tempo from 1 to 100. To stop the payments, pause.'
}
