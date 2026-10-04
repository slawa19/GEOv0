import { equivalentPrecision } from '../config/equivalentPrecision'
import { extractErrorMessage } from './errorMessage'
import { formatMoney, moneyText } from './money'

/**
 * 028 `F-028-51` (owner В-6): the human text of a payment refusal is the client's, built from
 * `code + details.reason + details`; the server's `message` is an English hint and is shown only when
 * the answer names neither a known reason nor a known code. The set mirrors
 * `app/core/payments/service.py` `PAYMENT_REFUSAL_REASONS` (openapi `PaymentRefusalDetails.reason`).
 */
export type UiLocale = 'en' | 'ru'
type Vars = { eq: string; max: string | null; precision: string }
type Text = (v: Vars) => string

const REASONS: Record<string, Record<UiLocale, Text>> = {
  no_route: {
    en: (v) => `No payment route between these participants${v.max ? ` (available now: ${v.max} ${v.eq})` : ''}.`,
    ru: (v) => `Нет маршрута платежа между этими участниками${v.max ? ` (сейчас доступно: ${v.max} ${v.eq})` : ''}.`,
  },
  insufficient_capacity: {
    en: (v) => (v.max ? `Not enough capacity: at most ${v.max} ${v.eq} can be sent now.` : 'Not enough capacity on the route.'),
    ru: (v) => (v.max ? `Недостаточно ёмкости: сейчас можно отправить не больше ${v.max} ${v.eq}.` : 'Недостаточно ёмкости на маршруте.'),
  },
  policy: { en: () => 'The route breaks a trust line policy.', ru: () => 'Маршрут нарушает политику линии доверия.' },
  participant_suspended: { en: () => 'A participant of this payment is suspended.', ru: () => 'Участник платежа заморожен.' },
  amount_precision_exceeded: {
    en: (v) => `${v.eq} allows at most ${v.precision} decimal places.`,
    ru: (v) => `${v.eq} допускает не больше ${v.precision} знаков после запятой.`,
  },
  equivalent_integrity_hold: {
    en: (v) => `Payments in ${v.eq} are on hold after an integrity check.`,
    ru: (v) => `Платежи в ${v.eq} приостановлены проверкой целостности.`,
  },
  equivalent_inactive: { en: (v) => `${v.eq} is not active.`, ru: (v) => `Эквивалент ${v.eq} не активен.` },
  busy: { en: () => 'The system is busy; send the same payment again.', ru: () => 'Система занята; отправьте тот же платёж ещё раз.' },
  timeout: { en: () => 'The payment timed out.', ru: () => 'Время ожидания платежа истекло.' },
  tx_id_reused: { en: () => 'This payment id was already used.', ru: () => 'Этот идентификатор платежа уже использован.' },
  unverifiable_legacy_identity: {
    en: () => 'The earlier payment with this id cannot be verified.',
    ru: () => 'Прежний платёж с этим идентификатором нельзя проверить.',
  },
  invalid_signature: { en: () => 'The payment signature is invalid.', ru: () => 'Подпись платежа неверна.' },
  recipient_not_found: { en: () => 'The recipient was not found.', ru: () => 'Получатель не найден.' },
  amount_not_positive: { en: () => 'The amount must be positive.', ru: () => 'Сумма должна быть положительной.' },
  self_payment: { en: () => 'You cannot pay yourself.', ru: () => 'Нельзя заплатить самому себе.' },
  equivalent_not_found: { en: (v) => `Equivalent ${v.eq} was not found.`, ru: (v) => `Эквивалент ${v.eq} не найден.` },
  other: { en: () => 'The payment was refused.', ru: () => 'Платёж отклонён.' },
}

/** The generic text of a code, for a reason this client does not know (or none). */
const CODES: Record<string, string> = {
  NO_ROUTE: 'no_route', INSUFFICIENT_CAPACITY: 'insufficient_capacity', ENGINE_TIMEOUT: 'timeout',
  CONFLICT: 'busy',
}
const GENERIC: Record<UiLocale, string> = { en: 'The payment was not accepted.', ru: 'Платёж не принят.' }
const CODE_GENERIC = new Set(['PAYMENT_REJECTED', 'INVALID_AMOUNT'])

export const PAYMENT_REFUSAL_REASONS: readonly string[] = Object.freeze(Object.keys(REASONS))

export function uiLocale(): UiLocale {
  const lang = typeof document === 'undefined' ? '' : document.documentElement.lang
  return lang.toLowerCase().startsWith('ru') ? 'ru' : 'en'
}

export function paymentRefusalText(error: unknown, equivalent: string, locale: UiLocale = uiLocale()): string {
  const e = (error && typeof error === 'object' ? error : {}) as { code?: unknown; details?: unknown }
  const details = (e.details && typeof e.details === 'object' ? e.details : {}) as Record<string, unknown>
  const code = typeof e.code === 'string' ? e.code : ''
  const eq = String(details.equivalent ?? equivalent).toUpperCase()
  const max = moneyText(details.max_available)
  const vars: Vars = { eq, max: max === null ? null : formatMoney(max, equivalentPrecision(eq)),
    precision: String(details.precision ?? equivalentPrecision(eq)) }
  const reason = typeof details.reason === 'string' ? details.reason : ''
  const text = REASONS[reason] ?? REASONS[CODES[code] ?? '']
  if (text) return text[locale](vars)
  return CODE_GENERIC.has(code) ? GENERIC[locale] : extractErrorMessage(error)
}
