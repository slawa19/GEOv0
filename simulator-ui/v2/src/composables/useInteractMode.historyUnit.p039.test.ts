/**
 * 039 closing review (Codex on f330c537, finding 1): a history line of a trust line - created, limit changed - shows its
 * amount WITH the equivalent of the operation (owner: equivalents are never mixed and always shown with their amounts).
 * Red before: `history.trustlineCreated` / `history.trustlineUpdated` had no `{unit}` and the producer passed none -
 * «Линия доверия изменена: … лимит 20.00», without the equivalent; the toast of the same change already had it.
 *
 * The producer is judged through its entry point (`useInteractMode`), and the expected texts are LITERALS in both
 * languages, not `t()` of the same vars - so a missing placeholder cannot agree with itself. The equivalent is `HOUR`,
 * not the default `UAH`, so a hard-coded unit does not pass; the amount is printed exactly as entered (no reformatting).
 *
 * Walked: create, change of limit. Not here: payment and clearing lines (they already carried the unit; `r08` and
 * `inspectors.p046w3` walk them), close (no amount in its text).
 */
import { computed, ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useInteractMode } from './useInteractMode'
import { historyEntryText } from './interact/useInteractHistory'
import { setUiLocale } from '../i18n/locale'
import type { GraphSnapshot } from '../types'

type Actions = Parameters<typeof useInteractMode>[0]['actions']

const EQ = 'HOUR'

function mode() {
  const snapshot = ref<GraphSnapshot | null>({
    equivalent: EQ, generated_at: '2026-01-01T00:00:00Z',
    nodes: [
      { id: 'pid-0001', name: 'Alice Baker', type: 'person', status: 'active' },
      { id: 'pid-0002', name: 'Bob Carter', type: 'person', status: 'active' },
      { id: 'pid-0003', name: 'Carol Dean', type: 'person', status: 'active' },
    ],
    links: [
      { source: 'pid-0001', target: 'pid-0003', used: '0.00', available: '10.00', trust_limit: '10.00', status: 'active' },
    ],
  })
  const actions = {
    actionsDisabled: ref(false),
    createTrustline: vi.fn(async () => ({ ok: true, trustline_id: 't1', from_pid: 'pid-0002', to_pid: 'pid-0003', equivalent: EQ, limit: '12345.67' })),
    updateTrustline: vi.fn(async () => ({ ok: true, trustline_id: 't2', old_limit: '10.00', new_limit: '20.50' })),
    fetchParticipants: vi.fn(async () => []),
    fetchTrustlines: vi.fn(async () => []),
    fetchPaymentTargets: vi.fn(async () => []),
  } as unknown as Actions
  return { im: useInteractMode({ actions, runId: computed(() => 'run_test'), equivalent: computed(() => EQ), snapshot }), actions }
}

afterEach(() => setUiLocale('en'))

describe('039 closing, finding 1 - a trust line history line carries the equivalent of its amount', () => {
  it('a trust line CREATED: the limit with its equivalent, in both languages', async () => {
    const { im, actions } = mode()
    im.startTrustlineFlow()
    im.selectNode('pid-0002')
    im.selectNode('pid-0003')
    expect(im.phase.value, 'counter-check: the flow reached the create step').toBe('confirm-trustline-create')
    await im.confirmTrustlineCreate('12345.67')
    expect(actions.createTrustline, 'counter-check: the request was made in the operation\'s equivalent').toHaveBeenCalledWith('pid-0002', 'pid-0003', '12345.67', EQ, expect.anything())
    expect(im.history, 'exactly one history line').toHaveLength(1)
    const entry = im.history[0]!
    expect(historyEntryText(entry, 'en')).toBe('New line: Bob Carter → Carol Dean, limit 12345.67 HOUR')
    expect(historyEntryText(entry, 'ru')).toBe('Новая линия: Bob Carter → Carol Dean, лимит 12345.67 HOUR')
  })

  it('a trust line limit CHANGED: the new limit with its equivalent, in both languages', async () => {
    const { im } = mode()
    expect(im.selectEdge('pid-0001→pid-0003'), 'counter-check: the existing line is selected').toBe(true)
    await im.confirmTrustlineUpdate('20.50')
    expect(im.history, 'exactly one history line').toHaveLength(1)
    const entry = im.history[0]!
    expect(historyEntryText(entry, 'en')).toBe('New line limit: Alice Baker → Carol Dean, 20.50 HOUR')
    expect(historyEntryText(entry, 'ru')).toBe('Новый лимит линии: Alice Baker → Carol Dean, 20.50 HOUR')
  })
})
