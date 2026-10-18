/**
 * 046 wave 2 (T4603): the windows of the actions - payment, trust line, clearing - and the analytics panel speak ONE
 * language. Red before the wave (the windows had English literals whatever the language): under `ru` the screen of each
 * state below carries no Latin word except data - participant names, the code of the equivalent, a payment id - and
 * the name of the key `Esc`. Visible text, and the `aria-label` / `title` / `placeholder` a keyboard or screen-reader
 * user meets, are read together. Counter-check: under `en` the same states show the English texts as before (byte for
 * byte, the strings the ~270 English assertions hold), with no Cyrillic outside the names.
 *
 * What this does NOT see: whether a translation is right or fits its control (the width probe
 * `e2e/p046-w2-windows-ru.spec.ts` measures that), states not mounted here (a busy window, the phone layout).
 */
import { createApp, h, nextTick, reactive, type Component } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ClearingPanel from './ClearingPanel.vue'
import ManualPaymentPanel from './ManualPaymentPanel.vue'
import RealMetricsPanel from './RealMetricsPanel.vue'
import TrustlineManagementPanel from './TrustlineManagementPanel.vue'
import type { BottlenecksResponse, MetricsResponse, SimulatorActionClearingRealResponse } from '../api/simulatorTypes'
import type { ManualPaymentOutcome } from '../composables/useInteractMode'
import { setUiLocale, type UiLocale } from '../i18n/locale'

/** Data: the same in both languages. Names are Cyrillic so that a Latin word on screen can only be interface text. */
const PEOPLE = [
  { pid: 'alice', name: 'Алиса', type: 'person', status: 'active' },
  { pid: 'bob', name: 'Борис', type: 'person', status: 'active' },
  { pid: 'carol', name: 'Вера', type: 'business', status: 'frozen' },
]
/**
 * Data on screen, not interface text: the equivalent, a payment id, participant ids (the trust line title and an unnamed
 * bottleneck target print ids), the bottleneck codes (in the `title` of the reason, for correlation), the unavailable
 * reason token and a failure text given as data (shown as the correlation handle) - and the key name `Esc`.
 */
const DATA_WORDS = new Set(['UAH', 'man', 'Esc', 'alice', 'bob', 'carol', 'storage_disabled', 'HTTP',
  'LOW_AVAILABLE', 'HIGH_USED', 'FREQUENT_ABORTS', 'TOO_MANY_TIMEOUTS', 'ROUTING_TOO_DEEP', 'CLEARING_PRESSURE'])

afterEach(() => {
  setUiLocale('en')
  document.body.innerHTML = ''
})

async function settle() {
  for (let i = 0; i < 4; i += 1) await nextTick()
}

/** Everything the window says: visible text and the accessible names, titles and placeholders of its elements. */
function said(): string {
  const parts = [document.body.textContent ?? '']
  for (const el of Array.from(document.body.querySelectorAll('*'))) {
    for (const a of ['aria-label', 'title', 'placeholder']) {
      const v = el.getAttribute(a)
      if (v) parts.push(v)
    }
  }
  return parts.join(' \n ').replace(/\s+/g, ' ')
}

/** The Latin words on screen that are not data. */
function latinWords(text: string): string[] {
  return (text.match(/[A-Za-z_]+/g) ?? []).filter((w) => !DATA_WORDS.has(w))
}

function mountWith(component: Component, props: Record<string, unknown>) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp({ render: () => h(component, props) })
  app.mount(host)
  return { host, app }
}

function state(phase: string, from: string | null, to: string | null, error: string | null = null, lastClearing: unknown = null) {
  return reactive({ phase, fromPid: from, toPid: to, selectedEdgeKey: null, edgeAnchor: null, error, lastClearing })
}

async function typeInto(selector: string, value: string) {
  const input = document.querySelector(selector) as HTMLInputElement
  input.value = value
  input.dispatchEvent(new Event('input'))
  await settle()
}

function paymentProps(phase: string, from: string | null, to: string | null, extra: Record<string, unknown> = {}) {
  return {
    phase, state: state(phase, from, to), unit: 'UAH', availableCapacity: '10.00',
    trustlinesLoading: false, paymentTargetsLoading: false, paymentTargetsLastError: null, paymentTargetsMaxHops: 6,
    paymentToTargetIds: new Set(['bob']), trustlines: [], participants: PEOPLE, busy: false, canSendPayment: true,
    trustlinesState: 'answered', confirmPayment: vi.fn(), cancel: vi.fn(), retryPayment: vi.fn(), dismissPaymentResult: vi.fn(),
    discardUnresolvedPayment: vi.fn(), setFromPid: vi.fn(), setToPid: vi.fn(),
    ...extra,
  }
}

const SUCCESS: ManualPaymentOutcome = {
  kind: 'success', paymentId: 'man:1234', status: 'COMMITTED', amount: '10.00', equivalent: 'UAH',
  fromPid: 'alice', toPid: 'bob', fromName: 'Алиса', toName: 'Борис',
  routes: [
    { hops: [{ from: 'alice', to: 'bob', fromName: 'Алиса', toName: 'Борис', amount: '4.00' }] },
    { hops: [{ from: 'alice', to: 'carol', fromName: 'Алиса', toName: 'Вера', amount: '6.00' }, { from: 'carol', to: 'bob', fromName: 'Вера', toName: 'Борис', amount: '6.00' }] },
  ],
}

const UNKNOWN: ManualPaymentOutcome = {
  kind: 'unknown', message: '', amount: '5.00', equivalent: 'UAH', fromPid: 'alice', toPid: 'bob',
  fromName: 'Алиса', toName: 'Борис', runMismatch: false,
}

const TL_ROW = { from_pid: 'alice', to_pid: 'bob', from_name: 'Алиса', to_name: 'Борис', equivalent: 'UAH', limit: '100.00', used: '12.00', available: '88.00', status: 'active' }

function trustlineProps(phase: string, from: string | null, to: string | null, extra: Record<string, unknown> = {}) {
  return {
    phase, state: state(phase, from, to), unit: 'UAH', used: '12.00', currentLimit: '100.00', available: '88.00',
    figuresSource: { kind: 'row' }, participants: PEOPLE, trustlines: [TL_ROW], busy: false,
    confirmTrustlineCreate: vi.fn(), confirmTrustlineUpdate: vi.fn(), confirmTrustlineClose: vi.fn(), cancel: vi.fn(),
    setFromPid: vi.fn(), setToPid: vi.fn(), selectTrustline: vi.fn(),
    ...extra,
  }
}

const CLEARING: SimulatorActionClearingRealResponse = {
  ok: true, equivalent: 'UAH', cleared_cycles: 2, total_cleared_amount: '9.00',
  cycles: [
    { cleared_amount: '7.00', edges: [{ from: 'alice', to: 'bob' }, { from: 'bob', to: 'alice' }] },
    { cleared_amount: '2.00', edges: [{ from: 'bob', to: 'carol' }, { from: 'carol', to: 'bob' }] },
  ],
} as SimulatorActionClearingRealResponse

const METRICS = {
  api_version: 'v1', run_id: 'run-1', equivalent: 'UAH', from_ms: 0, to_ms: 2000, step_ms: 1000,
  series: [
    { key: 'success_rate', unit: '%', points: [{ t_ms: 0, v: '90' }, { t_ms: 1000, v: null }, { t_ms: 2000, v: '95' }] },
    { key: 'total_debt', unit: 'amount', points: [{ t_ms: 0, v: '100.00' }, { t_ms: 2000, v: '120.00' }] },
    { key: 'avg_route_length', unit: null, points: [{ t_ms: 0, v: '2' }] },
    { key: 'clearing_volume', unit: 'amount', points: [{ t_ms: 0, v: '1' }] },
    { key: 'bottlenecks_score', unit: null, points: [{ t_ms: 0, v: '0.3' }] },
    { key: 'active_participants', unit: null, points: [{ t_ms: 0, v: '3' }] },
    { key: 'active_trustlines', unit: null, points: [{ t_ms: 0, v: '4' }] },
  ],
} as unknown as MetricsResponse

const CODES = ['LOW_AVAILABLE', 'HIGH_USED', 'FREQUENT_ABORTS', 'TOO_MANY_TIMEOUTS', 'ROUTING_TOO_DEEP', 'CLEARING_PRESSURE']
const BOTTLENECKS = {
  api_version: 'v1', run_id: 'run-1', equivalent: 'UAH',
  items: CODES.map((reason_code, i) => ({
    target: { kind: 'edge', from: 'alice', to: 'bob' }, score: 0.1 * (i + 1), reason_code,
    label: 'Server English label', suggested_action: 'Server English advice',
  })),
} as unknown as BottlenecksResponse

type Case = { name: string; render: () => Promise<void>; en: string[] }

const CASES: Case[] = [
  {
    name: 'payment: the sender step',
    render: async () => { mountWith(ManualPaymentPanel, paymentProps('picking-payment-from', null, null)); await settle() },
    en: ['Manual payment', 'From', 'To', 'Cancel', 'Confirm'],
  },
  {
    name: 'payment: no routes from a sender that is not active',
    render: async () => {
      mountWith(ManualPaymentPanel, paymentProps('picking-payment-to', 'carol', null, { paymentToTargetIds: new Set() }))
      await settle()
    },
    en: ['Backend reports no payment routes from selected sender (max hops: 6). Selected sender is not active (status: frozen); a participant that is not active takes no part in payments.'],
  },
  {
    name: 'payment: the confirm step, a comma and an amount above the estimate',
    render: async () => {
      mountWith(ManualPaymentPanel, paymentProps('confirm-payment', 'alice', 'bob', {
        targetEstimate: { state: 'received', hops: 3, maxAvailable: '1.00' },
      }))
      await settle()
      await typeInto('#mp-amount', '1,5')
    },
    en: ['Manual payment: Алиса → Борис', 'Amount', 'Direct', '(server)', 'Max', 'server estimate', '3 steps',
      "The amount is above the server's estimate of the maximum (1.00 UAH); the server decides."],
  },
  {
    name: 'payment: an amount the form cannot read',
    render: async () => {
      mountWith(ManualPaymentPanel, paymentProps('confirm-payment', 'alice', 'bob'))
      await settle()
      await typeInto('#mp-amount', '1.2.3')
    },
    en: ["Invalid amount format. Use digits and '.' for decimals."],
  },
  {
    name: 'payment: the result screen with two routes',
    render: async () => { mountWith(ManualPaymentPanel, paymentProps('confirm-payment', 'alice', 'bob', { paymentOutcome: SUCCESS })); await settle() },
    en: ['Payment sent', 'Payment ID', 'Status', 'COMMITTED', 'Route 1 of 2', 'Another payment', 'Close'],
  },
  {
    name: 'payment: an unknown result, discard asked',
    render: async () => {
      mountWith(ManualPaymentPanel, paymentProps('confirm-payment', 'alice', 'bob', { paymentOutcome: UNKNOWN }))
      await settle()
      ;(document.querySelector('[data-testid="mp-discard"]') as HTMLButtonElement).click()
      await settle()
    },
    en: ['Result unknown', 'Unresolved payment: 5.00 UAH, Алиса → Борис.', 'Discard it', 'Keep checking'],
  },
  {
    name: 'trust line: the create step with a comma',
    render: async () => {
      mountWith(TrustlineManagementPanel, trustlineProps('confirm-trustline-create', 'alice', 'carol', { figuresSource: { kind: 'no-row' } }))
      await settle()
      await typeInto('#tl-limit', '1,5')
    },
    en: ['Trustline: alice → carol', 'From', 'To', 'Existing', 'Limit', 'Create'],
  },
  {
    name: 'trust line: editing a line with debt',
    render: async () => { mountWith(TrustlineManagementPanel, trustlineProps('editing-trustline', 'alice', 'bob')); await settle() },
    en: ['Used', '/ Limit', 'avail 88.00 UAH', 'New limit', 'Close TL', 'Update',
      'Closing with debt (used: 12.00 UAH) is a request: limit becomes 0, the line closes when the debt is repaid.'],
  },
  {
    name: 'trust line: the source failed, close already requested',
    render: async () => {
      mountWith(TrustlineManagementPanel, trustlineProps('editing-trustline', 'alice', 'bob', {
        figuresSource: { kind: 'failed', message: 'Сервер не ответил вовремя.' },
        trustlines: [{ ...TL_ROW, close_requested_at: '2026-10-18T00:00:00Z' }],
      }))
      await settle()
    },
    en: ['Trustline data could not be loaded: Сервер не ответил вовремя.. The figures for this pair are not available, and what the graph shows may be out of date.'],
  },
  {
    name: 'trust line: the backend answered there is no line',
    render: async () => { mountWith(TrustlineManagementPanel, trustlineProps('editing-trustline', 'alice', 'bob', { figuresSource: { kind: 'no-row' } })); await settle() },
    en: ['The backend answered for this pair: there is no trustline.'],
  },
  {
    name: 'clearing: the confirm step',
    render: async () => {
      mountWith(ClearingPanel, { phase: 'confirm-clearing', state: state('confirm-clearing', null, null), busy: false, equivalent: 'UAH', participants: PEOPLE, confirmClearing: vi.fn(), cancel: vi.fn() })
      await settle()
    },
    en: ['Run clearing', 'This will run a clearing cycle in backend.', 'Cancel', 'Confirm'],
  },
  {
    name: 'clearing: the result of two cycles',
    render: async () => {
      mountWith(ClearingPanel, { phase: 'clearing-preview', state: state('clearing-preview', null, null, null, CLEARING), busy: false, equivalent: 'UAH', participants: PEOPLE, confirmClearing: vi.fn(), cancel: vi.fn() })
      await settle()
    },
    en: ['Clearing result', 'Cycles cleared: 2', 'Total over cycles', 'Cycle 1 of 2: 7.00 UAH', 'Борис’s debt to Алиса reduced by 7.00 UAH', 'Close'],
  },
  {
    name: 'clearing: nothing to clear',
    render: async () => {
      mountWith(ClearingPanel, { phase: 'clearing-preview', state: state('clearing-preview', null, null, null, { ...CLEARING, cleared_cycles: 0, cycles: [], total_cleared_amount: '0' }), busy: false, equivalent: 'UAH', participants: PEOPLE, confirmClearing: vi.fn(), cancel: vi.fn() })
      await settle()
    },
    en: ['Nothing was cleared: no cycle of debts was found.'],
  },
  {
    name: 'analytics: measurements and the six bottleneck reasons',
    render: async () => { mountWith(RealMetricsPanel, { metricsPhase: 'ready', bottlenecksPhase: 'ready', metrics: METRICS, bottlenecks: BOTTLENECKS }); await settle() },
    en: ['Analytics', 'Measurements', 'Success rate', 'Total debt', '1 of 3 points have no measurement', 'Bottlenecks',
      'Frequent failures', 'Increase trust limits, add alternative routes, or clear', 'Focus'],
  },
  {
    name: 'analytics: not recorded, and a failed request',
    render: async () => {
      mountWith(RealMetricsPanel, { metricsPhase: 'unavailable', bottlenecksPhase: 'error', metrics: null, bottlenecks: null, metricsUnavailableReason: 'storage_disabled', bottlenecksError: 'HTTP 500' })
      await settle()
    },
    en: ['No measurements recorded', 'Metric storage is off for this run, so no measurements were recorded.', 'Bottlenecks request failed'],
  },
]

describe('046 wave 2 - the action windows in Russian carry no English word', () => {
  it.each(CASES.map((c) => [c.name, c] as const))('%s', async (_name, c) => {
    setUiLocale('ru')
    await c.render()
    const text = said()
    expect(text.length, 'anti-vacuum: the window rendered').toBeGreaterThan(20)
    expect(latinWords(text), text).toEqual([])
  })

  it('anti-vacuum: the scanner sees an English word, in the text and in an attribute', () => {
    expect(latinWords('Отмена Cancel')).toEqual(['Cancel'])
    document.body.innerHTML = '<button aria-label="Close panel">Закрыть</button>'
    expect(latinWords(said())).toEqual(['Close', 'panel'])
    expect(latinWords('Алиса → Борис: 10.00 UAH, man:1234, Esc, alice')).toEqual([])
  })

  it('the server\'s English bottleneck prose is not shown in either language', async () => {
    for (const locale of ['ru', 'en'] as UiLocale[]) {
      setUiLocale(locale)
      document.body.innerHTML = ''
      await CASES.find((c) => c.name.startsWith('analytics: measurements'))!.render()
      expect(said()).not.toContain('Server English')
    }
  })
})

describe('046 wave 2 - counter-check: the same windows in English are as before', () => {
  it.each(CASES.map((c) => [c.name, c] as const))('%s', async (_name, c) => {
    setUiLocale('en')
    await c.render()
    const text = said()
    for (const s of c.en) expect(text, s).toContain(s)
    const cyrillicOutsideNames = text.replace(/Алиса|Борис|Вера|Сервер не ответил вовремя\./g, '')
    expect(cyrillicOutsideNames).not.toMatch(/[Ѐ-ӿ]/)
  })
})
