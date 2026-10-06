// 028 E8 (`T2884`, F-028-51; `T2882`, F-028-48): the refusal text is the client's, money at the equivalent's step.
import { computed, createApp, h, ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import SystemBalanceBar from '../components/SystemBalanceBar.vue'
import { amountStepHint, equivalentPrecision } from '../config/equivalentPrecision'
import { useEdgeTooltip } from '../composables/useEdgeTooltip'
import { useInteractMode } from '../composables/useInteractMode'
import { PAYMENT_REFUSAL_REASONS, clearingRefusalText, paymentRefusalText } from './paymentRefusalText'

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
    expect(im.state.error).toBe('Недостаточно ёмкости: сейчас можно отправить не больше 0.50 UAH.')
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
  it('names the details: precision, no-route maximum; unknown reason -> the code text; no code -> the message', () => {
    expect(paymentRefusalText(refusal('INVALID_AMOUNT', { reason: 'amount_precision_exceeded', precision: 2,
      equivalent: 'HOUR' }), 'UAH', 'en')).toBe('HOUR allows at most 2 decimal places.')
    expect(paymentRefusalText(refusal('NO_ROUTE', { reason: 'no_route', max_available: '0' }), 'UAH', 'en'))
      .toBe('No payment route between these participants (available now: 0.00 UAH).')
    expect(paymentRefusalText(refusal('NO_ROUTE', { reason: 'future_reason' }), 'UAH', 'en'))
      .toBe(paymentRefusalText(refusal('NO_ROUTE', {}), 'UAH', 'en'))
    expect(paymentRefusalText({ status: 404, code: 'RUN_NOT_FOUND', message: 'Run not found' }, 'UAH', 'en'))
      .toBe('Run not found')
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
  it('anti-vacuum: another clearing answer keeps its own message', async () => {
    const im = runClearingRefused({ status: 409, code: 'CLEARING_INTERRUPTED', message: 'interrupted', details: { reason: 'retry_budget' } })
    await im.confirmClearing()
    expect(im.state.error).toBe('interrupted')
    expect(clearingRefusalText(refusal('CLEARING_REFUSED', { reason: 'future_reason' }), 'UAH', 'en')).toBe('server text')
  })
})
