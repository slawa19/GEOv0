/**
 * 039 T3901 - R8 (names instead of identifiers), COMPOSABLE tier.
 *
 * Corrected form (P-2; arbiter F6): the result of a clearing and the history line of a PAYMENT already carry names
 * (037 A2). What is left is the TRUST LINE history and its toasts: `useInteractMode.ts` prints `showPid(pid)` there,
 * which is the id without its scenario namespace, not the participant's `name` (the field is `name`, not `display_name`).
 * The assertion is positive: known names, a known number of entries - an empty history must not pass.
 *
 * LIMITS. The producer of the text is judged, not the component that renders it (`InteractHistoryLog.vue` prints the
 * text it is given). Three producers are walked: create, change of limit, close. A producer added later is not seen.
 *
 * SLICE D (names) must turn the three `red` tests green: then replace `red` by `it`. Done by 044 `T4405` (2026-10-17),
 * which made trust-line toasts and history print `participantName` (a run's ids are identities of the run).
 */
import { computed, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import { useInteractMode } from '../composables/useInteractMode'
import { historyEntryText } from '../composables/interact/useInteractHistory'
import type { GraphSnapshot } from '../types'
import { measured } from './p039'

type Actions = Parameters<typeof useInteractMode>[0]['actions']

/** Ids that cannot be mistaken for names, and names that cannot be mistaken for ids. */
const NODES = [
  { id: 'pid-0001', name: 'Alice Baker', type: 'person', status: 'active' },
  { id: 'pid-0002', name: 'Bob Carter', type: 'person', status: 'active' },
  { id: 'pid-0003', name: 'Carol Dean', type: 'person', status: 'active' },
]

function mode() {
  const snapshot = ref<GraphSnapshot | null>({
    equivalent: 'UAH', generated_at: '2026-01-01T00:00:00Z', nodes: NODES,
    // An existing line Bob -> Alice (so a payment Alice -> Bob has capacity) and Alice -> Carol (to change and to close).
    links: [
      { source: 'pid-0002', target: 'pid-0001', used: '0.00', available: '10.00', trust_limit: '10.00', status: 'active' },
      { source: 'pid-0001', target: 'pid-0003', used: '0.00', available: '10.00', trust_limit: '10.00', status: 'active' },
    ],
  })
  const actions = {
    actionsDisabled: ref(false),
    sendPayment: vi.fn(async () => ({ ok: true, payment_id: 'p1', from_pid: 'pid-0001', to_pid: 'pid-0002', equivalent: 'UAH', amount: '1.00', status: 'COMMITTED', routes: [] })),
    createTrustline: vi.fn(async () => ({ ok: true, trustline_id: 't1', from_pid: 'pid-0002', to_pid: 'pid-0003', equivalent: 'UAH', limit: '10.00' })),
    updateTrustline: vi.fn(async () => ({ ok: true, trustline_id: 't2', old_limit: '10.00', new_limit: '20.00' })),
    closeTrustline: vi.fn(async () => ({ ok: true, trustline_id: 't2', status: 'closed', close_requested_at: '2026-10-02T08:00:00Z' })),
    runClearing: vi.fn(async () => ({ ok: true, cycles: [] })),
    fetchParticipants: vi.fn(async () => []),
    fetchTrustlines: vi.fn(async () => []),
    fetchPaymentTargets: vi.fn(async () => []),
  } as unknown as Actions
  return useInteractMode({ actions, runId: computed(() => 'run_test'), equivalent: computed(() => 'UAH'), snapshot })
}

// 046 wave 3 (2026-10-19): a history line is kept as its key and data (BACKLOG 046-3); its text is `historyEntryText`.
const texts = (im: ReturnType<typeof mode>) => im.history.map((e) => historyEntryText(e))

describe('039 R8 - the Interact history names participants', () => {
  it('counter-check (already green, 037 A2): the history line of a PAYMENT carries both names - the harness resolves names', async () => {
    const im = mode()
    im.startPaymentFlow()
    im.selectNode('pid-0001')
    im.selectNode('pid-0002')
    await im.confirmPayment('1.00')
    measured('R8 payment history', texts(im))
    expect(texts(im)).toEqual(['Payment 1.00 UAH: Alice Baker → Bob Carter'])
  })

  it('a trust line CREATED: one history line with both names and no id (today: "pid-0002 → pid-0003")', async () => {
    const im = mode()
    im.startTrustlineFlow()
    im.selectNode('pid-0002')
    im.selectNode('pid-0003')
    expect(im.phase.value, 'counter-check: the flow reached the create step').toBe('confirm-trustline-create')
    await im.confirmTrustlineCreate('10.00')
    measured('R8 trustline created', { history: texts(im), toast: im.successMessage.value })
    expect(texts(im), 'exactly one history line').toHaveLength(1)
    expect(texts(im)[0], 'CREATED: the history line names Bob Carter').toContain('Bob Carter')
    expect(texts(im)[0], 'CREATED: the history line names Carol Dean').toContain('Carol Dean')
    expect(texts(im)[0]).not.toMatch(/pid-000\d/)
  })

  it('a trust line limit CHANGED: one history line with both names and no id (today: ids)', async () => {
    const im = mode()
    expect(im.selectEdge('pid-0001→pid-0003'), 'counter-check: the existing line is selected').toBe(true)
    await im.confirmTrustlineUpdate('20.00')
    measured('R8 trustline updated', { history: texts(im), toast: im.successMessage.value })
    expect(texts(im), 'exactly one history line').toHaveLength(1)
    expect(texts(im)[0], 'CHANGED: the history line names Alice Baker').toContain('Alice Baker')
    expect(texts(im)[0], 'CHANGED: the history line names Carol Dean').toContain('Carol Dean')
    expect(texts(im)[0]).not.toMatch(/pid-000\d/)
  })

  it('a trust line CLOSED: the history line and the toast carry both names and no id (today: ids)', async () => {
    const im = mode()
    expect(im.selectEdge('pid-0001→pid-0003')).toBe(true)
    await im.confirmTrustlineClose()
    measured('R8 trustline closed', { history: texts(im), toast: im.successMessage.value })
    expect(texts(im), 'exactly one history line').toHaveLength(1)
    for (const text of [texts(im)[0]!, String(im.successMessage.value)]) {
      expect(text, 'CLOSED: the text names Alice Baker').toContain('Alice Baker')
      expect(text, 'CLOSED: the text names Carol Dean').toContain('Carol Dean')
      expect(text).not.toMatch(/pid-000\d/)
    }
  })
})
