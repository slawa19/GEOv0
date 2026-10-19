import type { RunError } from '../api/simulatorTypes'
import { toRaw } from 'vue'

import type { UiLocale } from '../i18n/locale'
import { t, tn, type MessageKey, type PluralKey } from '../i18n/t'
import { uiLocale } from './paymentRefusalText'

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
 * 046 wave 1 (§15 Codex, fix-delta P3, 2026-10-18): THE ONE PLACE of the run error line's value - the line as a value
 * that carries its cause, not as the text of one language:
 *   - `dict`  - a dictionary key whose text needs no data;
 *   - `count` - a plural key and its count (the server's number of places);
 *   - `raw`   - a text kept as it came (the server's own `CODE: message`, a transport error, a scene-load error);
 *   - `code`  - a dictionary key whose text names a server code (`{code}`): the sentence is in the interface language,
 *               the code stays as it came, for correlation (046 wave 1, full: the line of a run ended by an error).
 * `renderRunErrorLine` gives the text in a language. The holder (`useSimulatorRealMode.ts`) keeps the VALUE in
 * `lastErrorLine` and the text of the current language in `lastError` (for every reader that wants a string: the top
 * bar, its title, tests), and renders again on a switch. A line put aside and brought back (a scene-load error over it)
 * is brought back as its value, so it comes back in the language of that moment. Scope: the top bar's run error line
 * only; the action windows keep their own texts (BACKLOG 046-3). 048 S3 («одна форма ошибки») builds on this value -
 * a new field (e.g. a request id) is added HERE, to the variants and to `renderRunErrorLine`, not as a new mechanism.
 */
export type RunErrorLine =
  | { readonly kind: 'dict'; readonly key: MessageKey }
  | { readonly kind: 'count'; readonly key: PluralKey; readonly count: number }
  | { readonly kind: 'raw'; readonly text: string }
  | { readonly kind: 'code'; readonly key: MessageKey; readonly code: string }

export function renderRunErrorLine(line: RunErrorLine, locale: UiLocale = uiLocale()): string {
  switch (line.kind) {
    case 'dict':
      return t(line.key, undefined, locale)
    case 'count':
      return tn(line.key, line.count, undefined, locale)
    case 'raw':
      return line.text
    case 'code':
      return t(line.key, { code: line.code }, locale)
  }
}

export const rawRunErrorLine = (text: string): RunErrorLine => ({ kind: 'raw', text })

/**
 * Write a line into a holder: the text (current language) FIRST, then the value. The holder's own watcher of `lastError`
 * forgets the value of a text written without one (`useSimulatorRealMode.ts`), so the order matters: a write of the
 * text alone - by any other code - leaves no stale value behind it.
 */
export function writeRunErrorLine(
  target: { lastError: string; lastErrorLine?: RunErrorLine | null },
  line: RunErrorLine | null,
  locale: UiLocale = uiLocale(),
): void {
  target.lastError = line ? renderRunErrorLine(line, locale) : ''
  target.lastErrorLine = line
}

/**
 * The error line of a run status (BACKLOG 037-5 (a)). A run in `error` was stopped by `last_error`: it is shown whatever
 * its code, because it is the only answer to "why did the run stop". For a run that goes on, the list of user-facing
 * codes decides as before - its line is printed as sent (`CODE: message`). Null: no line.
 *
 * 046 wave 1, full (048 S2 hand-over P2-2): a visitor has no Start since 048 S2, so a run that ENDED says what to do -
 * reload the page (the opening then starts a new run, `useInteractAutoBootstrapRun.ts`): the idle stop (044), any other
 * stop that was not Stop of this screen's run row (`user_stop`, the administrator's own act - who has Start beside it):
 * the operator's stop-all, a stop through the API with any reason or none (internal adversarial pass, 2026-10-19: the
 * runbook's one-run stop sends no reason, and a list of the operator's reasons missed it) - and a run ended by an error
 * (its code, not the server's English prose: 046 decision 3). Who stopped it is the stop reason behind "Details".
 */
export function runStatusErrorLine(
  status: { state?: string | null; last_error?: RunError | null; stop_reason?: string | null },
  isUserFacing: (code: string) => boolean,
): RunErrorLine | null {
  const state = String(status.state ?? '').trim().toLowerCase()
  // 044 stage 2 (decision 2): a run the server stopped because nobody watched it says so, in the interface language.
  if (state === 'stopped' && status.stop_reason === IDLE_STOP_REASON) return IDLE_STOP_LINE
  if (state === 'stopped' && status.stop_reason !== OWN_STOP_REASON) return STOPPED_LINE
  const error = status.last_error
  if (!error) return null
  if (state === 'error') return runEndedByErrorLine(error)
  return isUserFacing(error.code) ? runErrorLine(error) : null
}

/** The reason Stop of this screen's run row sends (`useSimulatorRealMode.ts` `stop`) - the one stop that writes no line. */
export const OWN_STOP_REASON = 'user_stop'

const STOPPED_LINE: RunErrorLine = Object.freeze({ kind: 'dict', key: 'runLine.stopped' })

/**
 * The line of a run the server ended with an error: the clearing step refusal keeps its own human text (it names the
 * cause and the operator's action); any other code is told in the interface language with the code itself.
 */
export function runEndedByErrorLine(error: RunError): RunErrorLine {
  if (error.code.toUpperCase() === 'CLEARING_REFUSED') return runErrorLine(error)
  return { kind: 'code', key: 'runLine.errorStopped', code: error.code }
}

export function runStatusErrorText(
  status: { state?: string | null; last_error?: RunError | null; stop_reason?: string | null },
  isUserFacing: (code: string) => boolean,
  locale: UiLocale = uiLocale(),
): string {
  const line = runStatusErrorLine(status, isUserFacing)
  return line ? renderRunErrorLine(line, locale) : ''
}

/** The run-level error line: the step refusal is a human text in the interface language, the rest is `CODE: message`. */
export function runErrorLine(error: RunError): RunErrorLine {
  if (error.code.toUpperCase() === 'CLEARING_REFUSED') return { kind: 'dict', key: 'refusal.clearingStepNoEq' }
  return rawRunErrorLine(`${error.code}: ${error.message}`)
}

export function runErrorText(error: RunError, locale: UiLocale = uiLocale()): string {
  return renderRunErrorLine(runErrorLine(error), locale)
}

/** 044 stage 2: `stop_reason` of a run the server stopped because it had no SSE subscriber (`run_lifecycle.py`). */
export const IDLE_STOP_REASON = 'idle_no_subscribers'

/**
 * 044 stage 2: the line of a run stopped by the idle stop. 046 wave 1 (merge of #299): from the dictionary
 * (`runLine.idleStopped`); the way back is the reload - the earlier text named Start under "Подробнее", which put a
 * Russian word in the English text and an English one in the Russian (the service row is not translated yet).
 */
const IDLE_STOP_LINE: RunErrorLine = Object.freeze({ kind: 'dict', key: 'runLine.idleStopped' })

export function idleStopText(locale: UiLocale = uiLocale()): string {
  return renderRunErrorLine(IDLE_STOP_LINE, locale)
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
 * another visitor leaves): the top bar offers "Try again" beside it (`useSimulatorRealMode.ts` records the line).
 */
export function isGlobalLimitRefusal(e: unknown): boolean {
  return detailsOf409(e)?.conflict_kind === GLOBAL_ACTIVE_LIMIT_CONFLICT
}

/**
 * 046 wave 1: does the error line offer "Try again"? Only while the line on screen IS the refused start's line - the
 * same value (`retryableStartLine`, recorded by `startRun`), compared by identity, never by its text: a later error that
 * replaces the line withdraws the offer, and the refusal brought back after a scene-load error brings it back.
 */
export function startRetryOffered(lastErrorLine: RunErrorLine | null | undefined, retryableStartLine: RunErrorLine | null | undefined): boolean {
  return !!retryableStartLine && !!lastErrorLine && toRaw(retryableStartLine) === toRaw(lastErrorLine)
}

/**
 * 043 S2 (advisor's follow-up), 046 wave 1: the line of a run start that failed because the server's global limit of
 * active runs is reached (`isGlobalLimitRefusal`), else null. On a shared server that is another visitor's live run,
 * not this visitor's to watch. The number of places is the server's (`details.max_active_runs`, a positive integer -
 * `run_lifecycle.py` has sent it with `active_runs` since 043 S2), never a constant here; without a usable number the
 * text names none. The text does not point at Start under "Details": the line itself offers "Try again".
 */
export function startRunErrorLine(e: unknown): RunErrorLine | null {
  if (!isGlobalLimitRefusal(e)) return null
  const raw = detailsOf409(e)?.max_active_runs
  const n = typeof raw === 'number' && Number.isInteger(raw) && raw > 0 ? raw : null
  if (n === null) return { kind: 'dict', key: 'runStart.limitBusy' }
  if (n === 1) return { kind: 'dict', key: 'runStart.limitOne' }
  return { kind: 'count', key: 'runStart.limitAll', count: n }
}

/** The text of a failed start: the limit refusal in the interface language; every other error keeps `fallback`. */
export function startRunErrorText(e: unknown, fallback: (e: unknown) => string, locale: UiLocale = uiLocale()): string {
  const line = startRunErrorLine(e)
  return line ? renderRunErrorLine(line, locale) : fallback(e)
}

/** 043 S2 (review B5): the error line when the server offers no scenario at all - the opening has nothing to show. */
export const NO_SCENARIO_LINE: RunErrorLine = Object.freeze({ kind: 'dict', key: 'runStart.noScenario' })

export function noScenarioText(locale: UiLocale = uiLocale()): string {
  return renderRunErrorLine(NO_SCENARIO_LINE, locale)
}

/**
 * 044 stage 2 (BACKLOG 043-19): the error line when Start or Apply is asked with a tempo below 1 (the field allows 0).
 * A run at 0 makes no payment - a silent run that looks broken; it is refused with this hint instead, and the typed
 * value is not replaced by another one behind the person's back. Stopping the payments is the pause.
 */
export const ZERO_INTENSITY_LINE: RunErrorLine = Object.freeze({ kind: 'dict', key: 'runLine.zeroIntensity' })

export function zeroIntensityText(locale: UiLocale = uiLocale()): string {
  return renderRunErrorLine(ZERO_INTENSITY_LINE, locale)
}
