import type { RunError } from '../api/simulatorTypes'
import { toRaw } from 'vue'

import { errorDetail, errorLog, logFailureLine, requestIdOf } from './errorMessage'

import type { UiLocale } from '../i18n/locale'
import { t, tn, type MessageKey, type PluralKey } from '../i18n/t'
import { uiLocale } from './paymentRefusalText'

/**
 * 054-B-7b (the owner's decision of 2026-10-30: «на этапе разработки пользователь и есть администратор; все РЕАЛЬНЫЕ ошибки
 * показывать»): the codes of a REAL failure - infrastructure, integrity, a defect of the program - as opposed to a refusal
 * of a payment (`ROUTING_*`, `CONFLICT`, `INVALID_INPUT`, `PAYMENT_REJECTED`, `TRUSTLINE_*`, ...: an outcome, shown in the
 * history only). A run that goes on shows such a `last_error` on its line, its code and message in the text; a `tx.failed`
 * with such a code is counted as an error, not a rejection. Until 054 only `PAYMENT_TIMEOUT`, `INTERNAL_ERROR` and
 * `CLEARING_REFUSED` were (031 item 17). A code in none of the classes stays unshown on a running run (a run in `error`
 * shows whatever code stopped it).
 */
const REAL_RUN_ERROR_CODES = new Set([
  'PAYMENT_TIMEOUT',
  'INTERNAL_ERROR',
  'CLEARING_ERROR',
  'CLEARING_REFUSED',
  'INTEGRITY_VIOLATION',
  'HEARTBEAT_FAILED',
  'SENDER_NOT_FOUND',
])

export function isUserFacingRunErrorCode(code: string): boolean {
  const c = code.toUpperCase()
  if (!c) return false
  return REAL_RUN_ERROR_CODES.has(c) || c.startsWith('REAL_MODE_')
}

/**
 * 046 wave 1 (§15 Codex, fix-delta P3, 2026-10-18): THE ONE PLACE of the run error line's value - the line as a value
 * that carries its cause, not as the text of one language:
 *   - `dict`  - a dictionary key whose text needs no data;
 *   - `count` - a plural key and its count (the server's number of places);
 *   - `raw`   - a text kept as it came (the server's own `CODE: message`, a transport error, a scene-load error);
 *   - `code`  - a dictionary key whose text names a server code (`{code}`): the sentence is in the interface language,
 *               the code stays as it came, for correlation (046 wave 1, full: the line of a run ended by an error);
 *               `detail` is the server's own `CODE: message` - the line's `title` and one console line (048 decision 3,
 *               AGENTS.md section 12). 054-B-7b (the owner's decision of 2026-10-30, replaces 048 S3's «the technical text
 *               is the title only» for the run's own failure): `message` and `at` of `last_error` are DRAWN too
 *               (`{message}`, `{at}`), as the server gave them.
 * `renderRunErrorLine` gives the text in a language. The holder (`useSimulatorRealMode.ts`) keeps the VALUE in
 * `lastErrorLine` and the text of the current language in `lastError` (for every reader that wants a string: the top
 * bar, its title, tests), and renders again on a switch. A line put aside and brought back (a scene-load error over it)
 * is brought back as its value, so it comes back in the language of that moment. Scope: the top bar's run error line
 * only; the action windows keep their own texts (BACKLOG 046-3). 048 S3 («одна форма ошибки») builds on this value -
 * a new field (e.g. a request id) is added HERE, to the variants and to `renderRunErrorLine`, not as a new mechanism.
 *
 * 048 S3 (decision 3): `dict` and `code` carry the failure behind the line - `ref`, the server's correlation id (shown as
 * the line's own second line and `data-request-id`, AGENTS.md section 12), and `detail`, its `title` (for a failed
 * request: the identifiers «HTTP 500», «HTTP 409 CONFLICT»; for `code`: the server's `CODE: message`, as 046 wave 1 put
 * it). A failed request is a `dict` line of `failureRunErrorLine`: the sentence of the dictionary by the kind of the
 * failure, never the transport's English («HTTP 500 Internal Server Error for /…») - that is its `log`, one console line.
 */
export type RunErrorLine =
  | { readonly kind: 'dict'; readonly key: MessageKey; readonly ref?: string | null; readonly detail?: string; readonly log?: string }
  | { readonly kind: 'count'; readonly key: PluralKey; readonly count: number; readonly ref?: string | null; readonly log?: string }
  | { readonly kind: 'raw'; readonly text: string }
  | {
      readonly kind: 'code'
      readonly key: MessageKey
      readonly code: string
      readonly message?: string
      readonly at?: string
      readonly detail?: string
      readonly ref?: string | null
    }

export function renderRunErrorLine(line: RunErrorLine, locale: UiLocale = uiLocale()): string {
  switch (line.kind) {
    case 'dict':
      return t(line.key, undefined, locale)
    case 'count':
      return tn(line.key, line.count, undefined, locale)
    case 'raw':
      return line.text
    case 'code':
      return t(line.key, { code: line.code, message: line.message ?? '', at: line.at ?? '' }, locale)
  }
}

export const rawRunErrorLine = (text: string): RunErrorLine => ({ kind: 'raw', text })

/** The server's full text behind a line (the `title` of the error line), when the line keeps one; else null. */
export function runErrorLineTitle(line: RunErrorLine | null | undefined): string | null {
  return (line?.kind === 'code' || line?.kind === 'dict') && line.detail ? line.detail : null
}

/** 048 S3: the full technical text behind a line, for the console (a failed request's `log`, else its title), else null. */
export function runErrorLineLog(line: RunErrorLine | null | undefined): string | null {
  if ((line?.kind === 'dict' || line?.kind === 'count') && line.log) return line.log
  return runErrorLineTitle(line)
}

/** 048 S3: the server's correlation id behind a line (its own second line and `data-request-id`), else null. */
export function runErrorLineRef(line: RunErrorLine | null | undefined): string | null {
  return line && line.kind !== 'raw' && line.ref ? line.ref : null
}

/**
 * 048 S3 (decision 3): the line of a request that failed - one sentence «what happened → what to do» in the interface
 * language, chosen by the KIND of the failure (no answer, no connection, not this session's run, no such run, the server
 * failed), with the request id and the technical text kept apart (`ref`, `detail`). A value that is not an answer of the
 * server (a check of the client, a broken stream) is `view` - the picture of the network could not be brought up to date -
 * or `stream` when the caller knows the event stream broke. Nothing of the transport's English is drawn.
 */
export function failureRunErrorLine(e: unknown, o: { client?: 'view' | 'stream'; logPrefix?: string } = {}): RunErrorLine {
  const key = failureKey(e, o.client ?? 'view')
  const detail = errorDetail(e)
  const log = errorLog(e)
  const ref = requestIdOf(e)
  return {
    kind: 'dict',
    key,
    ...(ref ? { ref } : {}),
    ...(detail ? { detail } : {}),
    ...(log || o.logPrefix ? { log: `${o.logPrefix ?? ''}${log ?? ''}` } : {}),
  }
}

function failureKey(e: unknown, client: 'view' | 'stream'): MessageKey {
  const o = (e !== null && typeof e === 'object' ? e : {}) as { status?: unknown; code?: unknown; name?: unknown }
  const status = typeof o.status === 'number' ? o.status : null
  if (o.code === 'TIMEOUT') return 'failure.noAnswer'
  // `fetch` rejects with a `TypeError` when there is no connection; an answer with status 0 is the same.
  if (e instanceof TypeError || status === 0) return 'failure.noConnection'
  if (status === 401 || status === 403) return 'failure.forbidden'
  if (status === 404) return 'failure.notFound'
  if (status !== null && status > 0) return 'failure.serverFailed'
  return client === 'stream' ? 'failure.streamFailed' : 'failure.viewFailed'
}

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
  // 048 decision 3 (§12): the server's full text of a run ended by an error goes to the console ONCE - when this line
  // first replaces another, not on each status that repeats it, nor on a re-render after a switch of the language.
  // 048 S3: the same for the technical text of any line that keeps one (a failed request), with its request id.
  const previous = target.lastErrorLine
  const detail = runErrorLineLog(line)
  if (line && detail && (runErrorLineLog(previous) !== detail || runErrorLineRef(previous) !== runErrorLineRef(line))) {
    logFailureLine(line.kind === 'code' && line.key === 'runLine.errorStopped' ? '[run] stopped with an error:' : '[run] error:', detail, runErrorLineRef(line))
  }
  target.lastError = line ? renderRunErrorLine(line, locale) : ''
  target.lastErrorLine = line
}

/**
 * The error line of a run status (BACKLOG 037-5 (a)). A run in `error` was stopped by `last_error`: it is shown whatever
 * its code, because it is the only answer to "why did the run stop". For a run that goes on, the codes of a real failure
 * decide (`isUserFacingRunErrorCode`, 054-B-7b) - its line is a sentence with the code and the message (`runErrorLine`).
 * Null: no line.
 *
 * 046 wave 1, full (048 S2 hand-over P2-2): a visitor has no Start since 048 S2, so a run that ENDED says what to do -
 * reload the page (the opening then starts a new run, `useInteractAutoBootstrapRun.ts`): the idle stop (044), any other
 * stop that was not Stop of this screen's run row (`user_stop`, the administrator's own act - who has Start beside it):
 * the operator's stop-all, a stop through the API with any reason or none (internal adversarial pass, 2026-10-19: the
 * runbook's one-run stop sends no reason, and a list of the operator's reasons missed it) - and a run ended by an error
 * (its code, not the server's English prose: 046 decision 3). Who stopped it is the stop reason behind "Details".
 */
export function runStatusErrorLine(
  status: { state?: string | null; last_error?: RunError | null; stop_reason?: string | null; pause_reason?: string | null },
  isUserFacing: (code: string) => boolean,
): RunErrorLine | null {
  const state = String(status.state ?? '').trim().toLowerCase()
  // 054-B-7a (the owner's decision of 2026-10-30): a run the server paused because the scenario's automatic payments are
  // used up says so - the person pays by hand, resumes or restarts. Only this value: an unknown reason has no text.
  if (state === 'paused' && status.pause_reason === SCENARIO_EXHAUSTED_PAUSE_REASON) return SCENARIO_EXHAUSTED_LINE
  // 044 stage 2 (decision 2): a run the server stopped because nobody watched it says so, in the interface language.
  if (state === 'stopped' && status.stop_reason === IDLE_STOP_REASON) return IDLE_STOP_LINE
  if (state === 'stopped' && status.stop_reason !== OWN_STOP_REASON) return STOPPED_LINE
  const error = status.last_error
  if (!error) return null
  if (state === 'error') return runEndedByErrorLine(error)
  return isUserFacing(error.code) ? runErrorLine(error) : null
}

/** 054-B-7a: `pause_reason` of a run whose scenario's automatic payments are used up (stream A, 2026-10-30). */
export const SCENARIO_EXHAUSTED_PAUSE_REASON = 'scenario_exhausted'

const SCENARIO_EXHAUSTED_LINE: RunErrorLine = Object.freeze({ kind: 'dict', key: 'runLine.scenarioExhausted' })

/** The reason Stop of this screen's run row sends (`useSimulatorRealMode.ts` `stop`) - the one stop that writes no line. */
export const OWN_STOP_REASON = 'user_stop'

const STOPPED_LINE: RunErrorLine = Object.freeze({ kind: 'dict', key: 'runLine.stopped' })

/**
 * The line of a run the server ended with an error. 054-B-7b (the owner's decision of 2026-10-30): its code, message and
 * time are in the text, whatever the code (until 054 a general sentence, and the clearing step refusal its own human text,
 * `refusal.clearingStepNoEq`); the way back is a restart of the run.
 */
export function runEndedByErrorLine(error: RunError): RunErrorLine {
  return codeLine('runLine.errorStopped', error)
}

function codeLine(key: MessageKey, error: RunError): RunErrorLine {
  return { kind: 'code', key, code: error.code, message: error.message, at: error.at, detail: `${error.code}: ${error.message}` }
}

/**
 * The run-level error line of a run that goes on. 054-B-7b (the owner's decision of 2026-10-30): the code, the server's
 * message and its time are in the text, the clearing step refusal too (until 054 a human text of its own, and since
 * 048 S3 the message was the title only); the server's `CODE: message` stays the line's detail (`title`, console).
 */
export function runErrorLine(error: RunError): RunErrorLine {
  return codeLine('runLine.runningError', error)
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
  // 048 S3 (adversarial pass F3, AGENTS.md section 12): the refusal carries the request id of its answer, as every line.
  // 049 S2 (§15 Codex P2): and its technical text for the console line (`log`, not drawn, not the title) - without it
  // `writeRunErrorLine` wrote no line, and the id lived in `data-request-id` only.
  const id = requestIdOf(e)
  const log = errorLog(e)
  const ref = { ...(id ? { ref: id } : {}), ...(log ? { log } : {}) }
  if (n === null) return { kind: 'dict', key: 'runStart.limitBusy', ...ref }
  if (n === 1) return { kind: 'dict', key: 'runStart.limitOne', ...ref }
  return { kind: 'count', key: 'runStart.limitAll', count: n, ...ref }
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
