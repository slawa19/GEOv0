import { equivalentPrecision } from '../config/equivalentPrecision'
import { en } from '../i18n/en'
import { documentUiLocale, type UiLocale } from '../i18n/locale'
import { t, type MessageKey } from '../i18n/t'
import { plainErrorMessage, shownError, type ShownError } from './errorMessage'
import { formatMoney, moneyText } from './money'

/**
 * 028 `F-028-51` (owner В-6): the human text of a payment refusal is the client's, built from
 * `code + details.reason + details`. The set mirrors `app/core/payments/service.py` `PAYMENT_REFUSAL_REASONS`
 * (openapi `PaymentRefusalDetails.reason`).
 *
 * 046 wave 2 (T4603): the texts live in the dictionary (`refusal.*`); here only the closed sets that choose a key. A
 * code that names no known reason is an action error: its own text by the closed set `actionError.*`, or - for a code
 * this client does not know - the general text with the code (046 decision 3). The server's English `message` is no
 * longer shown for a coded answer; an answer WITHOUT a code (a client-side failure) keeps its message.
 */
export type { UiLocale }
export type { ShownError }
type Vars = { eq: string; max: string | null; precision: string; keyed: boolean }
type Choose = (v: Vars) => MessageKey

const REASONS: Record<string, Choose> = {
  no_route: (v) => (v.max ? 'refusal.noRouteMax' : 'refusal.noRoute'),
  insufficient_capacity: (v) => (v.max ? 'refusal.insufficientCapacityMax' : 'refusal.insufficientCapacity'),
  policy: () => 'refusal.policy',
  participant_suspended: () => 'refusal.participantSuspended',
  amount_precision_exceeded: () => 'refusal.amountPrecisionExceeded',
  equivalent_integrity_hold: () => 'refusal.equivalentIntegrityHold',
  equivalent_inactive: () => 'refusal.equivalentInactive',
  // 037 A2 (F-037-2): "send the same payment again" is advice that is safe ONLY under an idempotency key - without one a
  // repeat is a second payment. The request of a manual payment always has one; a caller that cannot say so gets no advice.
  busy: (v) => (v.keyed ? 'refusal.busyKeyed' : 'refusal.busy'),
  timeout: () => 'refusal.timeout',
  tx_id_reused: () => 'refusal.txIdReused',
  unverifiable_legacy_identity: () => 'refusal.unverifiableLegacyIdentity',
  invalid_signature: () => 'refusal.invalidSignature',
  recipient_not_found: () => 'refusal.recipientNotFound',
  amount_not_positive: () => 'refusal.amountNotPositive',
  self_payment: () => 'refusal.selfPayment',
  equivalent_not_found: () => 'refusal.equivalentNotFound',
  other: () => 'refusal.other',
}

/** The text of a code, for a reason this client does not know (or none). */
const CODES: Record<string, string> = {
  NO_ROUTE: 'no_route', INSUFFICIENT_CAPACITY: 'insufficient_capacity', ENGINE_TIMEOUT: 'timeout',
  CONFLICT: 'busy',
}
const CODE_GENERIC = new Set(['PAYMENT_REJECTED', 'INVALID_AMOUNT'])

export const PAYMENT_REFUSAL_REASONS: readonly string[] = Object.freeze(Object.keys(REASONS))

/** `details.reason` values that name the equivalent's state; told before the code's own text (Codex §15 round 1). */
const ACTION_REASONS = new Set(Object.keys(en.actionReason))

/** The closed set of the action error codes that have their own text (`actionError.*` minus the three general keys). */
export const ACTION_ERROR_CODES: readonly string[] = Object.freeze(
  Object.keys(en.actionError).filter((k) => k !== 'unknown' && k !== 'noRun' && k !== 'httpFailed'),
)
const ACTION_ERROR_CODE_SET = new Set(ACTION_ERROR_CODES)
/** The code `useInteractActions.ts` gives an answer that named no code of its own: `HTTP_<status>`. */
const HTTP_STATUS_CODE = /^HTTP_\d+$/

/**
 * 046 wave 2 (decision 3): the text of a failed action of the Interact windows (and of their data loads) in the language
 * of the interface. An answer with a `code` (an `InteractActionError`, an `ApiError`) is told by the code - its own
 * text from the closed set, else the general text WITH the code (so it can be correlated, AGENTS section 12) - never by
 * the server's English `message`. A `details.reason` naming the equivalent's state (`actionReason.*`) comes before the
 * code's text: a `CONFLICT` for a stopped equivalent is not "the system is busy". Kept as it is: a value without a code
 * (an `Error` the client composed itself, a plain object or string), and an error the client marked as its own
 * (`clientMessage`: the "no answer" of a mutation, a thrown value that was not an HTTP answer) - a server code, even
 * `UNKNOWN`, is never taken for the client's. A kept text that is empty gets the general text, never nothing.
 *
 * 048 S3 (decision 3): the result is the shown form - the sentence, and APART from it the request id and the technical
 * detail (`ShownError`): the id is no longer glued into the sentence. A code the client made from a bare HTTP status
 * (`HTTP_<n>`, an answer without a code of its own) is not printed: the sentence says the server did not carry the action
 * out, and the status is in the detail (the block's `title` and the console).
 */
export function actionErrorText(error: unknown, locale: UiLocale = uiLocale()): ShownError {
  const e = (error && typeof error === 'object' ? error : {}) as { code?: unknown; details?: unknown; clientMessage?: unknown; clientText?: unknown }
  const code = typeof e.code === 'string' && e.code.trim() ? e.code.trim() : ''
  if (!code || e.clientMessage === true) {
    // 046 wave 3: the client's own text in the asked language when it carries what makes it (`clientText`), else as it is.
    const own = (e.clientMessage === true && typeof e.clientText === 'function'
      ? String((e.clientText as (l: UiLocale) => string)(locale))
      : plainErrorMessage(error)).trim()
    return shownError(own || t('actionError.unknown', { code: code || 'UNKNOWN' }, locale), error)
  }
  const details = (e.details && typeof e.details === 'object' ? e.details : {}) as Record<string, unknown>
  const reason = typeof details.reason === 'string' ? details.reason : ''
  const text = ACTION_REASONS.has(reason)
    ? t(`actionReason.${reason}` as MessageKey, {}, locale)
    : ACTION_ERROR_CODE_SET.has(code)
      ? t(`actionError.${code}` as MessageKey, {}, locale)
      : HTTP_STATUS_CODE.test(code)
        ? t('actionError.httpFailed', {}, locale)
        : t('actionError.unknown', { code }, locale)
  return shownError(text, error)
}

/**
 * 034 S5a (review round 1, item 3): the text of a mutating action that got NO ANSWER in time. It is not a refusal -
 * the server may have carried the action out - so the operator is told to look at the state before repeating it.
 * (A second Confirm would pay twice if the first one was committed; the idempotency key for that belongs to 037.)
 */
export function actionOutcomeUnknownText(timeoutMs: number | undefined, locale: UiLocale = uiLocale()): string {
  const seconds = timeoutMs && timeoutMs > 0 ? Math.round(timeoutMs / 1000) : null
  return seconds ? t('refusal.outcomeUnknownSeconds', { seconds }, locale) : t('refusal.outcomeUnknown', {}, locale)
}

/** 046 wave 0: the reader of `<html lang>`, which only `i18n/locale.ts` writes (it chooses the language). */
export function uiLocale(): UiLocale {
  return documentUiLocale()
}

export function paymentRefusalText(
  error: unknown,
  equivalent: string,
  locale: UiLocale = uiLocale(),
  o: { keyed?: boolean } = {},
): ShownError {
  const e = (error && typeof error === 'object' ? error : {}) as { code?: unknown; details?: unknown }
  const details = (e.details && typeof e.details === 'object' ? e.details : {}) as Record<string, unknown>
  const code = typeof e.code === 'string' ? e.code : ''
  const eq = String(details.equivalent ?? equivalent).toUpperCase()
  const max = moneyText(details.max_available)
  const vars: Vars = { eq, max: max === null ? null : formatMoney(max, equivalentPrecision(eq)),
    precision: String(details.precision ?? equivalentPrecision(eq)), keyed: o.keyed === true }
  const reason = typeof details.reason === 'string' ? details.reason : ''
  const choose = REASONS[reason] ?? REASONS[CODES[code] ?? '']
  if (choose) return shownError(t(choose(vars), { eq: vars.eq, max: vars.max ?? '', precision: vars.precision }, locale), error)
  return CODE_GENERIC.has(code) ? shownError(t('refusal.generic', {}, locale), error) : actionErrorText(error, locale)
}

/**
 * 031 slice C (item 17): the human text of a clearing refusal, in the same place and pattern as a payment's.
 * `POST .../clearing-real` answers 409 CLEARING_REFUSED with `details.reason` `occurrence_amount_not_in_step`
 * (030 S2: the database holds debts finer than the equivalent's step), and the tick names the same refusal as
 * `last_error.code` CLEARING_REFUSED (which carries no reason, so no equivalent either: `equivalent` is optional).
 */
export function clearingStepRefusalText(equivalent: string | null, locale: UiLocale = uiLocale()): string {
  return equivalent
    ? t('refusal.clearingStep', { eq: equivalent.toUpperCase() }, locale)
    : t('refusal.clearingStepNoEq', {}, locale)
}

/**
 * The text of a failed clearing action; any other answer is told by its code (`actionErrorText`). Codex §15 round 1: the
 * operator's stop and the integrity hold answer 409 `CONFLICT` with `details.reason` (`app/api/v1/simulator.py`
 * `MONEY_STOP_REASONS`) - said as the clearing's own refusal, before the generic `CONFLICT` ("the system is busy").
 */
export function clearingRefusalText(error: unknown, equivalent: string, locale: UiLocale = uiLocale()): ShownError {
  const e = (error && typeof error === 'object' ? error : {}) as { code?: unknown; details?: unknown }
  const details = (e.details && typeof e.details === 'object' ? e.details : {}) as Record<string, unknown>
  if (e.code === 'CLEARING_REFUSED' && details.reason === 'occurrence_amount_not_in_step') {
    return shownError(clearingStepRefusalText(String(details.equivalent ?? equivalent), locale), error)
  }
  if (details.reason === 'equivalent_inactive') return shownError(t('clearing.equivalentInactive', {}, locale), error)
  if (details.reason === 'equivalent_integrity_hold') return shownError(t('clearing.equivalentIntegrityHold', {}, locale), error)
  return actionErrorText(error, locale)
}
