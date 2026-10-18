// 028 E8 (`T2884`, F-028-51; `T2882`, F-028-48): the refusal text is the client's, money at the equivalent's step.
import { computed, createApp, h, ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import SystemBalanceBar from '../components/SystemBalanceBar.vue'
import { amountStepHint, equivalentPrecision } from '../config/equivalentPrecision'
import { useEdgeTooltip } from '../composables/useEdgeTooltip'
import { useInteractMode } from '../composables/useInteractMode'
import { t } from '../i18n/t'
import { ACTION_ERROR_CODES, PAYMENT_REFUSAL_REASONS, actionErrorText, clearingRefusalText, paymentRefusalText } from './paymentRefusalText'

const refusal = (code: string, details: Record<string, unknown>) => ({ status: 409, code, message: 'server text', details })

afterEach(() => { document.documentElement.lang = 'en' })

describe('F-028-51: the human text of a payment refusal', () => {
  it('repro: insufficient_capacity with max_available reaches the payer in the interface language', async () => {
    document.documentElement.lang = 'ru'
    const sendPayment = vi.fn(async () => {
      throw refusal('INSUFFICIENT_CAPACITY', { reason: 'insufficient_capacity', max_available: '0.5', equivalent: 'UAH' })
    })
    const im = useInteractMode({ actions: { actionsDisabled: ref(false), sendPayment, fetchParticipants: async () => [],
      fetchTrustlines: async () => [], fetchPaymentTargets: async () => [] } as never,
    runId: computed(() => 'run_1'), equivalent: computed(() => 'UAH'), snapshot: ref(null) })
    im.startPaymentFlow(); im.selectNode('alice'); im.selectNode('bob')
    await im.confirmPayment('5.00')
    // 046 wave 2: the Russian text is the dictionary's (not hand-built here); the amount keeps its dot.
    expect(im.state.error).toBe(t('refusal.insufficientCapacityMax', { max: '0.50', eq: 'UAH' }, 'ru'))
    expect(im.state.error).toBe('Недостаточно доступного лимита: сейчас можно отправить не больше 0.50 UAH.')
  })
  it('every reason of the server set has its own text in both languages (anti-vacuum: not the generic one)', () => {
    // The set of `app/core/payments/service.py` PAYMENT_REFUSAL_REASONS / openapi PaymentRefusalDetails.reason.
    expect([...PAYMENT_REFUSAL_REASONS].sort()).toEqual(['amount_not_positive', 'amount_precision_exceeded', 'busy',
      'equivalent_inactive', 'equivalent_integrity_hold', 'equivalent_not_found', 'insufficient_capacity',
      'invalid_signature', 'no_route', 'other', 'participant_suspended', 'policy', 'recipient_not_found',
      'self_payment', 'timeout', 'tx_id_reused', 'unverifiable_legacy_identity'])
    for (const locale of ['en', 'ru'] as const) {
      const generic = paymentRefusalText(refusal('PAYMENT_REJECTED', {}), 'UAH', locale)
      const texts = PAYMENT_REFUSAL_REASONS.map((reason) => paymentRefusalText(refusal('X', { reason }), 'UAH', locale))
      expect(new Set(texts).size).toBe(PAYMENT_REFUSAL_REASONS.length)
      for (const t of texts) expect(t).not.toBe(generic)
    }
  })
  it('names the details: precision, no-route maximum; unknown reason -> the code text; a coded answer -> its code', () => {
    expect(paymentRefusalText(refusal('INVALID_AMOUNT', { reason: 'amount_precision_exceeded', precision: 2,
      equivalent: 'HOUR' }), 'UAH', 'en')).toBe('HOUR allows at most 2 decimal places.')
    expect(paymentRefusalText(refusal('NO_ROUTE', { reason: 'no_route', max_available: '0' }), 'UAH', 'en'))
      .toBe('No payment route between these participants (available now: 0.00 UAH).')
    expect(paymentRefusalText(refusal('NO_ROUTE', { reason: 'future_reason' }), 'UAH', 'en'))
      .toBe(paymentRefusalText(refusal('NO_ROUTE', {}), 'UAH', 'en'))
    // 046 wave 2: an action code is told by the dictionary (its English is the server's fixed message), not by `message`.
    expect(paymentRefusalText({ status: 404, code: 'RUN_NOT_FOUND', message: 'server words' }, 'UAH', 'en'))
      .toBe('Run not found')
    expect(paymentRefusalText({ status: 404, code: 'RUN_NOT_FOUND', message: 'server words' }, 'UAH', 'ru'))
      .toBe(t('actionError.RUN_NOT_FOUND', {}, 'ru'))
  })
})

describe('046 wave 2 (decision 3): an action error is told by its code, by a closed set', () => {
  it('every code of the set has its own text in both languages, none of them the general one or a Latin word in ru', () => {
    expect([...ACTION_ERROR_CODES].sort()).toEqual(['ACCESS_DENIED', 'ACTIONS_DISABLED', 'CLEARING_FAILED',
      'CLEARING_INTERRUPTED', 'CLEARING_REFUSED', 'CONFLICT', 'ENGINE_TIMEOUT', 'EQUIVALENT_NOT_FOUND', 'INSUFFICIENT_CAPACITY',
      'INVALID_AMOUNT', 'INVALID_JSON', 'INVALID_REQUEST', 'NETWORK', 'NO_ROUTE', 'PARTICIPANT_NOT_FOUND', 'PAYMENT_REJECTED',
      'RUN_NOT_FOUND', 'RUN_PERIMETER_UNAVAILABLE', 'RUN_TERMINAL', 'SCENARIO_TRUSTLINE_REFUSED', 'SEEDING_FAILED',
      'SIMULATOR_PID_TAKEN', 'TIMEOUT', 'TRUSTLINE_CLOSED', 'TRUSTLINE_CLOSE_REQUESTED', 'TRUSTLINE_EXISTS', 'TRUSTLINE_NOT_FOUND',
      'TRUSTLINE_USED_UNAVAILABLE', 'USED_EXCEEDS_NEW_LIMIT'])
    for (const locale of ['en', 'ru'] as const) {
      const general = actionErrorText({ status: 409, code: 'X_NEW', message: 'server words' }, locale)
      const texts = ACTION_ERROR_CODES.map((code) => actionErrorText({ status: 409, code, message: 'server words' }, locale))
      expect(new Set(texts).size).toBe(ACTION_ERROR_CODES.length)
      for (const text of texts) {
        expect(text).not.toBe(general)
        expect(text).not.toContain('server words')
        if (locale === 'ru') expect(text).not.toMatch(/[A-Za-z]/)
      }
    }
  })
  it('an unknown code: the general text WITH the code, in both languages; the server message is not shown', () => {
    expect(actionErrorText({ status: 500, code: 'HTTP_500', message: 'Internal Server Error' }, 'en')).toBe('The action failed (HTTP_500).')
    expect(actionErrorText({ status: 500, code: 'HTTP_500', message: 'Internal Server Error' }, 'ru'))
      .toBe(t('actionError.unknown', { code: 'HTTP_500' }, 'ru'))
  })
  it('kept as they are: no code, and an error the client marked as its own (clientMessage)', () => {
    expect(actionErrorText(new Error('boom'), 'ru')).toBe('boom')
    expect(actionErrorText({ status: 500, message: 'Trustlines are down' }, 'ru')).toBe('Trustlines are down')
    expect(actionErrorText({ status: 0, code: 'UNKNOWN', message: 'client words', clientMessage: true }, 'ru')).toBe('client words')
    expect(actionErrorText({ status: 408, code: 'TIMEOUT', message: 'Ответ не получен', clientMessage: true }, 'ru')).toBe('Ответ не получен')
    // Counter-check: the same TIMEOUT without the mark is told by its code.
    expect(actionErrorText({ status: 408, code: 'TIMEOUT', message: 'Request timed out' }, 'ru')).toBe(t('actionError.TIMEOUT', {}, 'ru'))
  })
  // Codex §15 round 1 (P2): a SERVER answer with the code UNKNOWN was taken for the client's own message - its English
  // prose was shown, and an empty message showed nothing at all (`v-if="state.error"` hid the window's error).
  it('a server code UNKNOWN is told by the general text with the code - with prose, and with an empty message', () => {
    expect(actionErrorText({ status: 500, code: 'UNKNOWN', message: 'Something broke on the server' }, 'ru'))
      .toBe(t('actionError.unknown', { code: 'UNKNOWN' }, 'ru'))
    expect(actionErrorText({ status: 500, code: 'UNKNOWN', message: '' }, 'en')).toBe('The action failed (UNKNOWN).')
    // A kept text that is empty is never an empty error either.
    expect(actionErrorText({ status: 0, code: 'UNKNOWN', message: '', clientMessage: true }, 'en')).toBe('The action failed (UNKNOWN).')
    expect(actionErrorText(new Error(''), 'en')).toBe('The action failed (UNKNOWN).')
  })
  // Codex §15 round 1 (P3): the reference after the client's own text follows its language too.
  it('the own text of the client carries the request reference in its language', () => {
    expect(actionErrorText({ status: 408, code: 'TIMEOUT', message: 'Ответ не получен', clientMessage: true, requestId: 'req-123' }, 'ru'))
      .toBe('Ответ не получен (код запроса: req-123)')
    expect(actionErrorText({ status: 408, code: 'TIMEOUT', message: 'No answer', clientMessage: true, requestId: 'req-123' }, 'en'))
      .toBe('No answer (ref: req-123)')
  })
  // Codex §15 round 1 (P2): the operator's stop and the integrity hold answer CONFLICT with `details.reason`; before the
  // fix a clearing refused that way read "the system is busy; try again".
  it('a CONFLICT naming the state of the equivalent is told by that state - on the clearing path and on any other', () => {
    const stopped = { status: 409, code: 'CONFLICT', message: 'Equivalent UAH is not active', details: { reason: 'equivalent_inactive' } }
    const held = { status: 409, code: 'CONFLICT', message: 'hold', details: { reason: 'equivalent_integrity_hold' } }
    expect(clearingRefusalText(stopped, 'UAH', 'ru')).toBe('Эквивалент остановлен — клиринг недоступен.')
    expect(clearingRefusalText(held, 'UAH', 'ru')).toBe('Эквивалент на проверке целостности — операции приостановлены.')
    expect(clearingRefusalText(stopped, 'UAH', 'en')).toBe('The equivalent is stopped — clearing is unavailable.')
    expect(actionErrorText(stopped, 'ru')).toBe(t('actionReason.equivalent_inactive', {}, 'ru'))
    expect(actionErrorText(held, 'en')).toBe('The equivalent is under an integrity check — operations are on hold.')
    // The payment path already told these reasons by its own set (`refusal.*`).
    expect(paymentRefusalText(stopped, 'UAH', 'en')).toBe('UAH is not active.')
    // Counter-check: a CONFLICT without such a reason is still "busy".
    expect(clearingRefusalText({ status: 409, code: 'CONFLICT', message: 'x', details: { reason: 'retryable' } }, 'UAH', 'en'))
      .toBe('The system is busy; try again.')
  })
})

describe('F-028-48: display and input at the equivalent precision', () => {
  it('the edge tooltip keeps every digit at the declared precision', () => {
    const t = useEdgeTooltip({ hostEl: ref(null), hoveredEdge: { key: null, screenX: 0, screenY: 0 },
      clamp: (v) => v, getUnit: () => 'UAH' })
    expect(t.formatEdgeAmountText({ source: 'a', target: 'b', used: '0.005', trust_limit: '12345678901234567.10' }))
      .toBe('from: 0.005 / to: 12345678901234567.10 UAH')
  })
  it('the system balance bar shows fractions, in the equivalent of its numbers', () => {
    const host = document.createElement('div')
    createApp({ render: () => h(SystemBalanceBar, { balance: { totalUsed: '0.30', totalAvailable: '1234.5',
      equivalent: 'HOUR', activeTrustlines: 2, activeParticipants: 2, utilization: 0.1, isClean: false } }) }).mount(host)
    expect(host.textContent).toContain('0.30 HOUR')
    expect(host.textContent).toContain('1234.50 HOUR')
  })
  it('HOUR is 2 decimals (owner В-4), and an amount finer than the step is hinted, not rounded', () => {
    expect(equivalentPrecision('HOUR')).toBe(2)
    expect(amountStepHint('1.500', 'UAH')).toBeNull()
    expect(amountStepHint('1.505', 'UAH')).toBe('UAH allows at most 2 decimal places; the server will refuse this amount.')
  })
})

// 031 slice C (item 17): `POST .../clearing-real` answers 409 CLEARING_REFUSED with details.reason
// `occurrence_amount_not_in_step` (030 S2); the Interact panel showed the server's English hint raw.
describe('031 item 17: the human text of a clearing refusal', () => {
  const stepRefusal = refusal('CLEARING_REFUSED', { reason: 'occurrence_amount_not_in_step' })
  const runClearingRefused = (error: unknown) => {
    const im = useInteractMode({ actions: { actionsDisabled: ref(false), runClearing: vi.fn(async () => { throw error }),
      fetchParticipants: async () => [], fetchTrustlines: async () => [], fetchPaymentTargets: async () => [] } as never,
    runId: computed(() => 'run_1'), equivalent: computed(() => 'UAH'), snapshot: ref(null) })
    im.startClearingFlow()
    return im
  }

  it('repro: Interact shows the step refusal in the interface language, not the server text', async () => {
    document.documentElement.lang = 'ru'
    const im = runClearingRefused(stepRefusal)
    await im.confirmClearing()
    expect(im.state.error).toBe('Клиринг отклонён: в базе есть долги мельче шага учёта эквивалента UAH. Нужен пересев базы.')
    document.documentElement.lang = 'en'
    const en = runClearingRefused(stepRefusal)
    await en.confirmClearing()
    expect(en.state.error).toBe(
      'Clearing refused: the database holds debts finer than the accounting step of UAH. Reseed the database.')
  })
  it('anti-vacuum: another clearing answer is told by its own code, not as the step refusal', async () => {
    // 046 wave 2 (decision 3): was "keeps its own message" - the server's English is no longer shown for a coded answer.
    const im = runClearingRefused({ status: 409, code: 'CLEARING_INTERRUPTED', message: 'interrupted', details: { reason: 'retry_budget' } })
    await im.confirmClearing()
    expect(im.state.error).toBe('Clearing pass interrupted before the plan was exhausted')
    expect(clearingRefusalText(refusal('CLEARING_REFUSED', { reason: 'future_reason' }), 'UAH', 'en')).toBe('Clearing refused')
    expect(clearingRefusalText(refusal('CLEARING_REFUSED', { reason: 'future_reason' }), 'UAH', 'ru')).toBe(t('actionError.CLEARING_REFUSED', {}, 'ru'))
  })
})
