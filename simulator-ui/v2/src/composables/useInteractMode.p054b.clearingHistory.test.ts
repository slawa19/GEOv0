/**
 * 054-B-6: the own history line of a clearing wrote «−total_cleared_amount», while the bar and the feed line state the drop
 * of the total debt (039 P-5, 049 :58) - one operation, two sums. A cycle of three debts cleared by 10 lowers the total by
 * 30: the answer's `total_cleared_amount` is 10, the drop is 30. The HTTP answer carries no identity of the stream's
 * `clearing.done` (no `plan_id`, no event id), so the drop of the pair cannot be bound to THIS clearing (an automatic
 * clearing of the run closes a pair too); the own line is the form without an amount, never `cleared_amount` - unknown is
 * not shown as a figure.
 * Real `useInteractMode`; only the HTTP actions are stand-ins.
 */
import { computed, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import { useInteractMode } from './useInteractMode'
import { historyEntryText } from './interact/useInteractHistory'
import type { GraphSnapshot } from '../types'

type Actions = Parameters<typeof useInteractMode>[0]['actions']
type Clearing = Awaited<ReturnType<Actions['runClearing']>>

const CYCLE = [{ from: 'alice', to: 'bob' }, { from: 'bob', to: 'carol' }, { from: 'carol', to: 'alice' }]
const oneCycleOfThree: Clearing = {
  ok: true, equivalent: 'UAH', cleared_cycles: 1, total_cleared_amount: '10.00', cycles: [{ cleared_amount: '10.00', edges: CYCLE }],
}
const none: Clearing = { ok: true, equivalent: 'UAH', cleared_cycles: 0, total_cleared_amount: '0.00', cycles: [] }

function mk(runClearing: Actions['runClearing']) {
  const snapshot = ref<GraphSnapshot | null>({
    equivalent: 'UAH', generated_at: '2026-01-01T00:00:00Z', data_revision: 0,
    nodes: ['alice', 'bob', 'carol'].map((id) => ({ id, name: id, type: 'person', status: 'active' })),
    links: CYCLE.map((e) => ({ source: e.from, target: e.to, used: '10.00', available: '90.00', status: 'active' })),
  } as GraphSnapshot)
  const actions = {
    actionsDisabled: ref(false), sendPayment: vi.fn(), createTrustline: vi.fn(), updateTrustline: vi.fn(), closeTrustline: vi.fn(),
    runClearing, fetchParticipants: vi.fn(async () => []), fetchTrustlines: vi.fn(async () => []), fetchPaymentTargets: vi.fn(async () => []),
  } as unknown as Actions
  return useInteractMode({ actions, runId: computed(() => 'run_test'), equivalent: computed(() => 'UAH'), snapshot })
}

const lines = (im: ReturnType<typeof mk>, locale: 'ru' | 'en') => im.history.map((e) => historyEntryText(e, locale))

describe('054-B-6: the own history line of a clearing', () => {
  it('does not state total_cleared_amount, which is not the drop of the total debt (10 cleared on a cycle of three = 30)', async () => {
    const im = mk(vi.fn(async () => oneCycleOfThree))
    im.startClearingFlow()
    await im.confirmClearing()
    for (const locale of ['ru', 'en'] as const) {
      const [line] = lines(im, locale)
      expect(line, `${locale}: one line`).toBeDefined()
      expect(line, `${locale}: the answer's total_cleared_amount is not the drop of the debt`).not.toContain('10.00')
      expect(line, `${locale}: no amount is stated without the pair of the debt`).not.toContain('−')
    }
    expect(lines(im, 'ru')).toEqual(['Клиринг: 1 круг'])
    expect(lines(im, 'en')).toEqual(['Clearing: 1 cycle'])
  })

  it('counter-check: a clearing that cleared nothing keeps its own line', async () => {
    const im = mk(vi.fn(async () => none))
    im.startClearingFlow()
    await im.confirmClearing()
    expect(lines(im, 'ru')).toEqual(['Клиринг: ничего не погашено'])
    expect(lines(im, 'en')).toEqual(['Clearing: nothing cleared'])
  })
})
