/**
 * 039 slice D (T3940) - names, not identifiers, in the Interact history ("Recent actions", `InteractHistoryLog.vue`)
 * and in the result of a clearing (`ClearingPanel.vue`), in both languages. Participant names are data: never
 * translated.
 *
 * The producer of the history text is `useInteractMode` (`participantName`); the panel prints what it is given. The
 * participant list it reads is the run's fetched list when there is one (`useInteractDataCache.ts`, cached 30 s),
 * else the snapshot's nodes. A participant the fetched list does not name - one added to the run by a topology event
 * after the list was read, or one the list names by its id - was printed as its raw id (`<run id>:p<n>`): the
 * `showPid` handed to `useInteractMode` by `useSimulatorApp.ts` strips only a SCENARIO namespace. The rule the rest of
 * the screen already uses (`SimulatorAppRoot.vue` `participantNameOf`: the snapshot node's `name`) is the fallback now.
 *
 * The clearing window already resolves through that rule (`useParticipantDisplayId` with the snapshot's names,
 * provided by `SimulatorAppRoot`); its case below is a counter-check, green before slice D.
 *
 * NOT seen here: the mounted app (the `showPid` of `useSimulatorApp.ts` is restated below, not imported); a producer
 * of history text added later.
 */
import { computed, createApp, h, nextTick, ref, type Component } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { SimulatorActionClearingRealResponse } from '../api/simulatorTypes'
import ClearingPanel from '../components/ClearingPanel.vue'
import { setUiLocale } from '../i18n/locale'
import type { GraphSnapshot } from '../types'
import { participantDisplayIdFor, provideParticipantNameOf, provideShownScenarioId } from './useParticipantDisplay'
import { useInteractMode } from './useInteractMode'
import { historyEntryText } from './interact/useInteractHistory'

type Actions = Parameters<typeof useInteractMode>[0]['actions']

const RUN = 'run_20261019_120000_abcd1234'
const ALICE = `${RUN}:p1`
const BOB = `${RUN}:p2`
const CAROL = `${RUN}:p3`
const NODES = [
  { id: ALICE, name: 'Alice Baker', type: 'person', status: 'active' },
  { id: BOB, name: 'Bob Carter', type: 'person', status: 'active' },
  // Joined the run after the participant list was read: the fetched list does not name her.
  { id: CAROL, name: 'Carol Dean', type: 'person', status: 'active' },
]

function mode() {
  const snapshot = ref<GraphSnapshot | null>({
    equivalent: 'UAH', generated_at: '2026-10-19T00:00:00Z', nodes: NODES,
    links: [
      { source: CAROL, target: ALICE, used: '0.00', available: '10.00', trust_limit: '10.00', status: 'active' },
      { source: BOB, target: ALICE, used: '0.00', available: '10.00', trust_limit: '10.00', status: 'active' },
    ],
  })
  const actions = {
    actionsDisabled: ref(false),
    sendPayment: vi.fn(async () => ({ ok: true, payment_id: 'p1', from_pid: ALICE, to_pid: CAROL, equivalent: 'UAH', amount: '1.00', status: 'COMMITTED', routes: [] })),
    createTrustline: vi.fn(async () => ({ ok: true, trustline_id: 't1', from_pid: CAROL, to_pid: BOB, equivalent: 'UAH', limit: '10.00' })),
    updateTrustline: vi.fn(async () => ({ ok: true })),
    closeTrustline: vi.fn(async () => ({ ok: true })),
    runClearing: vi.fn(async () => ({ ok: true, cycles: [] })),
    // The run's list: Alice by name, Bob by his id (the list's own fallback), Carol absent.
    fetchParticipants: vi.fn(async () => [
      { pid: ALICE, name: 'Alice Baker', type: 'person', status: 'active' },
      { pid: BOB, name: BOB, type: 'person', status: 'active' },
    ]),
    fetchTrustlines: vi.fn(async () => []),
    fetchPaymentTargets: vi.fn(async () => []),
  } as unknown as Actions
  // `showPid` exactly as `useSimulatorApp.ts` hands it: the scenario namespace rule.
  const im = useInteractMode({
    actions, runId: computed(() => RUN), equivalent: computed(() => 'UAH'), snapshot,
    showPid: participantDisplayIdFor(() => 'greenfield-village-100'),
  })
  return { im, actions }
}

const flush = async () => { for (let i = 0; i < 5; i++) await Promise.resolve() }

afterEach(() => setUiLocale('en'))

describe('039 D - the Interact history names participants the run list does not name', () => {
  for (const lang of ['ru', 'en'] as const) {
    it(`${lang}: a payment to a participant absent from the fetched list - names, no run id`, async () => {
      setUiLocale(lang)
      const { im, actions } = mode()
      await flush()
      expect(actions.fetchParticipants, 'counter-check: the run list was read').toHaveBeenCalled()
      expect(im.participants.value.map((p) => p.pid), 'counter-check: the fetched list is in use (Carol absent)').toEqual([ALICE, BOB])
      im.startPaymentFlow()
      im.selectNode(ALICE)
      im.selectNode(CAROL)
      await im.confirmPayment('1.00')
      // 046 wave 3 (2026-10-19): a history line is kept as its key and data and drawn by `historyEntryText` (BACKLOG 046-3).
      const texts = im.history.map((e) => historyEntryText(e))
      expect(texts, 'exactly one history line').toHaveLength(1)
      expect(texts[0]).toContain('Alice Baker')
      expect(texts[0]).toContain('Carol Dean')
      expect(texts[0]).not.toContain(RUN)
      expect(texts[0]).toMatch(lang === 'ru' ? /^Платёж 1\.00 UAH: / : /^Payment 1\.00 UAH: /)
    })

    it(`${lang}: a trust line created by a participant the list names by id - names, no run id (history and toast)`, async () => {
      setUiLocale(lang)
      const { im } = mode()
      await flush()
      im.startTrustlineFlow()
      im.selectNode(CAROL)
      im.selectNode(BOB)
      expect(im.phase.value, 'counter-check: the flow reached the create step').toBe('confirm-trustline-create')
      await im.confirmTrustlineCreate('10.00')
      // 046 wave 3 (2026-10-19): a history line is kept as its key and data and drawn by `historyEntryText` (BACKLOG 046-3).
      const texts = im.history.map((e) => historyEntryText(e))
      expect(texts).toHaveLength(1)
      for (const text of [texts[0]!, String(im.successMessage.value)]) {
        expect(text).toContain('Carol Dean')
        expect(text).toContain('Bob Carter')
        expect(text).not.toContain(RUN)
      }
    })
  }
})

describe('039 D - the clearing result names participants (counter-check: green before slice D)', () => {
  const answer: SimulatorActionClearingRealResponse = {
    ok: true, equivalent: 'UAH', cleared_cycles: 1, total_cleared_amount: '2.00',
    cycles: [{ cleared_amount: '2.00', edges: [{ from: ALICE, to: CAROL }, { from: CAROL, to: BOB }, { from: BOB, to: ALICE }] }],
  } as unknown as SimulatorActionClearingRealResponse

  for (const lang of ['ru', 'en'] as const) {
    it(`${lang}: names from the list, else from the snapshot - never the run id`, async () => {
      setUiLocale(lang)
      const host = document.createElement('div')
      document.body.appendChild(host)
      const app = createApp({
        setup() {
          provideShownScenarioId(ref('greenfield-village-100'))
          provideParticipantNameOf((pid) => NODES.find((n) => n.id === pid)?.name ?? null)
          return () => h(ClearingPanel as Component, {
            phase: 'clearing-preview', busy: false, equivalent: 'UAH',
            state: { lastClearing: answer, clearingFailure: null, error: '' },
            participants: [{ pid: ALICE, name: 'Alice Baker', type: 'person', status: 'active' }, { pid: BOB, name: BOB, type: 'person', status: 'active' }],
            confirmClearing: () => undefined, cancel: () => undefined,
          })
        },
      })
      app.mount(host)
      await nextTick()
      const lines = [...host.querySelectorAll('[data-testid="clearing-edge-line"]')].map((el) => el.textContent!.trim())
      expect(lines).toHaveLength(3)
      expect(lines[0]).toMatch(/^Alice Baker → Carol Dean: /)
      expect(lines[1]).toMatch(/^Carol Dean → Bob Carter: /)
      expect(lines[2]).toMatch(/^Bob Carter → Alice Baker: /)
      expect(lines.join('\n')).not.toContain(RUN)
      expect(lines[0]).toContain(lang === 'ru' ? 'долг уменьшен на 2.00 UAH' : 'debt reduced by 2.00 UAH')
      app.unmount()
      host.remove()
    })
  }
})
