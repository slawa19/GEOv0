/**
 * 049 S2 (decision 5), reproducer (г): the line of the network's events, through the WHOLE client path of an event - the
 * SSE frame of `connectSse`, its normalization, the replay owner's admission (the context check and the deduplication by
 * `event_id`), the pipeline (`applyAcceptedRealEvent` and its intents) and the history of the interact mode
 * (`useInteractMode.pushFeedItem`) - not a helper called by hand. The only stubs are the transport (the frames are pushed
 * by the test) and the REST calls of the run's start.
 *
 * Red on `main` (`d1289867`): the history had no network lines at all (`useInteractMode` has no `pushFeedItem`, the
 * pipeline no feed intent) - the module does not even resolve.
 */
import { computed, reactive, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { connectSse, type SseConnectOpts } from '../api/sse'
import { setUiLocale } from '../i18n/locale'
import type { GraphSnapshot } from '../types'
import type { RealModeState } from './useSimulatorRealMode'
import { useSimulatorRealMode } from './useSimulatorRealMode'
import { useInteractMode } from './useInteractMode'
import { historyEntryParts, historyEntryText, type InteractHistoryEntry } from './interact/useInteractHistory'

vi.mock('../api/simulatorApi', () => ({
  artifactDownloadUrl: () => 'http://artifact',
  createRun: vi.fn(async () => ({ run_id: 'r1' })),
  getActiveRun: vi.fn(async () => ({ run_id: 'r1' })),
  getRun: vi.fn(async () => ({ run_id: 'r1', scenario_id: 'sc1', state: 'running', sim_time_ms: 0, intensity_percent: 5, ops_sec: 0, queue_depth: 0, last_event_type: null, current_phase: null, last_error: null })),
  listArtifacts: vi.fn(async () => ({ items: [] })),
  listScenarios: vi.fn(async () => ({ items: [] })),
  pauseRun: vi.fn(async () => undefined),
  resumeRun: vi.fn(async () => undefined),
  setIntensity: vi.fn(async () => undefined),
  stopRun: vi.fn(async () => undefined),
}))
vi.mock('../api/equivalentsApi', () => ({ fetchEquivalentPrecisions: vi.fn(async () => []) }))
vi.mock('../api/sse', () => ({ connectSse: vi.fn() }))

const NBSP = ' '
const TS = '2026-10-20T12:00:00Z'

/** The live connection the client opened: the test pushes frames through its own `onMessage`, as the transport does. */
let sse: SseConnectOpts | null = null
function emit(payload: { event_id: string } & Record<string, unknown>) {
  if (!sse) throw new Error('no connection: the run did not open its stream')
  sse.onMessage({ id: payload.event_id, data: JSON.stringify(payload) })
}

const payment = (id: string, from = 'A', to = 'B', amount = '120.00', equivalent = 'UAH') => ({
  event_id: id, ts: TS, type: 'tx.updated', equivalent, from, to, amount, ttl_ms: 1200, edges: [{ from, to }],
})
const failure = (id: string, code: string, from = 'A', to = 'B') => ({
  event_id: id, ts: TS, type: 'tx.failed', equivalent: 'UAH', from, to, error: { code, message: 'server words', at: TS },
})

function snapshot(): GraphSnapshot {
  return {
    equivalent: 'UAH',
    generated_at: TS,
    nodes: [
      { id: 'A', name: 'Анна', status: 'active' },
      { id: 'B', name: 'Пекарня', status: 'active' },
      { id: 'C', name: 'Вера', status: 'active' },
    ],
    // A cycle of three debts of 10 each: a clearing of it lowers the total by 30, while its `cleared_amount` is 10.
    links: [
      { source: 'A', target: 'B', used: '10.00', trust_limit: '100.00', available: '90.00', status: 'active' },
      { source: 'B', target: 'C', used: '10.00', trust_limit: '100.00', available: '90.00', status: 'active' },
      { source: 'C', target: 'A', used: '10.00', trust_limit: '100.00', available: '90.00', status: 'active' },
    ],
  }
}

async function flush() {
  for (let i = 0; i < 5; i += 1) await Promise.resolve()
}

async function harness(o: { feedOn?: boolean } = {}) {
  const feedOn = ref(o.feedOn ?? true)
  const equivalent = ref('UAH')
  const snap = ref<GraphSnapshot | null>(snapshot())
  const state = reactive({ loading: false, error: '', sourcePath: '', snapshot: snap.value, selectedNodeId: null as string | null, debtChange: null })
  const real = reactive<RealModeState>({
    apiBase: 'http://x', accessToken: 't', loadingScenarios: false, scenarios: [], selectedScenarioId: 'sc1', intensityPercent: 5,
    runId: null, runStatus: null, sseState: 'idle', lastEventId: null, lastError: '', artifacts: [], artifactsLoading: false,
    runStats: { startedAtMs: 0, attempts: 0, committed: 0, rejected: 0, errors: 0, timeouts: 0, rejectedByCode: {}, errorsByCode: {} },
  })
  const sendPayment = vi.fn(async (from: string, to: string, amount: string) => ({
    ok: true, payment_id: 'man:1', from_pid: from, to_pid: to, equivalent: 'UAH', amount, status: 'COMMITTED', routes: [],
  }))
  const im = useInteractMode({
    actions: {
      actionsDisabled: ref(false), sendPayment, fetchParticipants: async () => [], fetchTrustlines: async () => [],
      fetchPaymentTargets: async () => [{ to_pid: 'B', hops: 1, max_available: '100.00' }],
    } as never,
    runId: computed(() => real.runId ?? ''),
    equivalent,
    snapshot: snap,
    eventsFeedOn: () => feedOn.value,
  })
  const rm = useSimulatorRealMode({
    isRealMode: computed(() => true),
    isLocalhost: true,
    effectiveEq: computed(() => equivalent.value),
    state: state as never,
    real,
    ensureScenarioSelectionValid: () => undefined,
    resetRunStats: () => undefined,
    cleanupRealRunFxAndTimers: () => undefined,
    isUserFacingRunError: () => false,
    inc: () => undefined,
    loadScene: vi.fn(async () => undefined),
    realPatchApplier: {
      applyNodePatches: () => undefined,
      // The lines of the snapshot on screen take the patch's debts - what the debt pair is taken around.
      applyEdgePatches: (patches) => {
        for (const p of patches ?? []) {
          const link = state.snapshot!.links.find((l) => l.source === p.source && l.target === p.target)
          if (link && p.used !== undefined) link.used = p.used
        }
      },
      applyNodeStatus: () => undefined,
      applyEdgeStatus: () => undefined,
    },
    pushTxAmountLabel: () => undefined,
    routeArrivalMs: () => 1200,
    scheduleTimeout: () => undefined,
    runRealTxFx: () => undefined,
    runRealClearingDoneFx: () => undefined,
    wakeUp: () => undefined,
    onFeedItem: (item) => im.pushFeedItem(item),
  })
  await rm.startRun({ intensityPercent: 5 })
  await flush()
  const lines = () => im.shownHistory.value.map((e) => historyEntryText(e, 'ru'))
  return { im, rm, real, feedOn, equivalent, lines, network: () => im.feed.map((e) => historyEntryText(e, 'ru')) }
}

let clock = Date.parse(TS)
beforeEach(() => {
  setUiLocale('ru')
  vi.useFakeTimers({ toFake: ['Date'] })
  clock = Date.parse(TS)
  vi.setSystemTime(clock)
  sse = null
  vi.mocked(connectSse).mockImplementation(async (opts: SseConnectOpts) => {
    sse = opts
    await new Promise<void>((resolve) => opts.signal?.addEventListener('abort', () => resolve(), { once: true }))
  })
})
afterEach(() => {
  vi.useRealTimers()
  setUiLocale('en')
})
/** The arrival time of the next frames: past the 2-second window of a series. */
function later(ms = 2500) {
  clock += ms
  vi.setSystemTime(clock)
}

describe('049 S2 (г): the network events in the history, from accepted SSE events', () => {
  it('switch on: a payment is «A → B: amount unit» by the names of the network (no «вы»)', async () => {
    const h = await harness()
    emit(payment('evt_r1_1'))
    expect(h.lines()).toEqual([`Анна → Пекарня: 120.00${NBSP}UAH`])
    expect(h.lines()[0]).not.toMatch(/^вы/)
    h.rm.stopSse()
  })

  it('switch off: no network line at all (as before 049: the own lines only)', async () => {
    const h = await harness({ feedOn: false })
    emit(payment('evt_r1_1'))
    emit(failure('evt_r1_2', 'ROUTING_NO_CAPACITY'))
    emit({ event_id: 'evt_r1_3', ts: TS, type: 'clearing.done', equivalent: 'UAH', plan_id: 'p1', cleared_cycles: 2 })
    expect(h.real.runStats.attempts, 'anti-vacuum: the events were accepted by the pipeline').toBe(2)
    expect(h.lines()).toEqual([])
    expect(h.network()).toEqual([])
    h.rm.stopSse()
  })

  it('a failure has no amount; its reason is the dictionary\'s words; an unknown reason gets no diagnosis', async () => {
    const h = await harness()
    emit(failure('evt_r1_1', 'ROUTING_NO_CAPACITY'))
    later()
    emit(failure('evt_r1_2', 'PAYMENT_REJECTED'))
    expect(h.lines()).toEqual([
      'Анна → Пекарня: не прошёл — мало лимита на маршруте',
      'Анна → Пекарня: платёж не прошёл',
    ])
    for (const line of h.lines()) {
      expect(line, 'no amount on a failure').not.toMatch(/\d/)
      expect(line, 'the server\'s words are not shown').not.toContain('server words')
    }
    h.rm.stopSse()
  })

  it('a clearing: the drop of the total is the pair difference (30), not `cleared_amount` (10); without a pair - the cycles', async () => {
    const h = await harness()
    emit({
      event_id: 'evt_r1_1', ts: TS, type: 'clearing.done', equivalent: 'UAH', plan_id: 'p1', cleared_cycles: 1, cleared_amount: '10.00',
      edge_patch: [
        { source: 'A', target: 'B', used: '0.00' }, { source: 'B', target: 'C', used: '0.00' }, { source: 'C', target: 'A', used: '0.00' },
      ],
    })
    expect(h.lines()).toEqual([`Клиринг: общий долг уменьшился на 30.00${NBSP}UAH; 1 круг`])
    // A clearing that moved no debt of the snapshot (no patches): no pair - the cycles alone; nothing is made up.
    emit({ event_id: 'evt_r1_2', ts: TS, type: 'clearing.done', equivalent: 'UAH', plan_id: 'p2', cleared_cycles: 2, cleared_amount: '7.00' })
    expect(h.lines().at(-1)).toBe('Клиринг: 2 круга')
    expect(h.lines().join(' | ')).not.toContain('7.00')
    h.rm.stopSse()
  })

  it('the growth of trust after a clearing is a line; another topology change is not', async () => {
    const h = await harness()
    const topology = (id: string, reason: string) => ({
      event_id: id, ts: TS, type: 'topology.changed', equivalent: 'UAH', reason,
      payload: { added_nodes: [], removed_nodes: [], added_edges: [], removed_edges: [], edge_patch: [{ source: 'A', target: 'B', trust_limit: '110.00' }] },
    })
    emit(topology('evt_r1_1', 'trust_drift_growth'))
    emit(topology('evt_r1_2', 'trustline_created'))
    expect(h.lines()).toEqual(['После клиринга выросли лимиты доверия'])
    h.rm.stopSse()
  })

  it('a series of 6 payments within 1 s is ONE line: «За 2 с: 6 платежей; последний — …»', async () => {
    const h = await harness()
    for (let i = 1; i <= 6; i += 1) {
      emit(payment(`evt_r1_${i}`, 'A', i % 2 ? 'B' : 'C', `${i}.00`))
      later(150)
    }
    expect(h.lines()).toEqual([`За 2 с: 6 платежей; последний — Анна → Вера: 6.00${NBSP}UAH`])
    // Failures are a series of their own, and a payment after the window opens a new line.
    emit(failure('evt_r1_7', 'ROUTING_NO_ROUTE'))
    later()
    emit(payment('evt_r1_8'))
    expect(h.lines()).toEqual([
      `За 2 с: 6 платежей; последний — Анна → Вера: 6.00${NBSP}UAH`,
      'Анна → Пекарня: не прошёл — нет маршрута',
      `Анна → Пекарня: 120.00${NBSP}UAH`,
    ])
    h.rm.stopSse()
  })

  it('a repeated event_id (a replay after a reconnect) is not written twice', async () => {
    const h = await harness()
    emit(payment('evt_r1_1'))
    later()
    emit(payment('evt_r1_1'))
    expect(h.lines()).toEqual([`Анна → Пекарня: 120.00${NBSP}UAH`])
    expect(h.real.runStats.attempts, 'counter-check: the duplicate was dropped by the replay owner, not by the history').toBe(1)
    h.rm.stopSse()
  })

  it('a foreign context is not written: another equivalent on the connection, another frame id', async () => {
    const h = await harness()
    emit(payment('evt_r1_1', 'A', 'B', '5.00', 'EUR'))
    sse!.onMessage({ id: 'other_frame', data: JSON.stringify(payment('evt_r1_2')) })
    expect(h.lines()).toEqual([])
    later()
    emit(payment('evt_r1_3'))
    expect(h.lines(), 'counter-check: the same connection still writes its own events').toHaveLength(1)
    h.rm.stopSse()
  })

  it('a manual payment is ONE line: with the switch on the line of its tx.updated (no «вы»); off - its own line, as before', async () => {
    const h = await harness()
    h.im.startPaymentFlow()
    h.im.selectNode('A')
    h.im.selectNode('B')
    await flush()
    expect(h.im.phase.value, 'counter-check: the payment reached its confirm step').toBe('confirm-payment')
    await h.im.confirmPayment('5.00')
    expect(h.im.paymentOutcome.value, 'counter-check: the payment is committed').toMatchObject({ kind: 'success' })
    expect(h.im.history, 'the own line is kept - for the switch off').toHaveLength(1)
    expect(h.lines(), 'on: the own line of a payment is not shown beside the network\'s').toEqual([])
    emit(payment('evt_r1_1', 'A', 'B', '5.00'))
    expect(h.lines()).toEqual([`Анна → Пекарня: 5.00${NBSP}UAH`])
    h.feedOn.value = false
    expect(h.lines(), 'off: exactly the own lines, as before 049').toEqual(['Платёж 5.00 UAH: Анна → Пекарня'])
    // On again (internal adversarial pass, P2): the network's line is gone with the switch - the own line comes back,
    // an own line (marked «вы» by the log); the payment never leaves the history.
    h.feedOn.value = true
    expect(h.lines()).toEqual(['Платёж 5.00 UAH: Анна → Пекарня'])
    expect(h.im.shownHistory.value.map((e) => e.source)).toEqual(['own'])
    h.rm.stopSse()
  })

  /**
   * Closing Codex review of 049 (2026-10-20), class 2: one `clearFeed` served the switch AND a change of run or equivalent,
   * so the own payment of the old run - hidden behind its stream line - came back under «События сети» in the new context.
   * Decision 5: a change of run or equivalent clears the lines - all of them; the switch alone clears the network's.
   */
  async function payByHand(h: Awaited<ReturnType<typeof harness>>, amount = '5.00') {
    h.im.startPaymentFlow()
    h.im.selectNode('A')
    h.im.selectNode('B')
    await flush()
    expect(h.im.phase.value, 'counter-check: the payment reached its confirm step').toBe('confirm-payment')
    await h.im.confirmPayment(amount)
    expect(h.im.paymentOutcome.value, 'counter-check: the payment is committed').toMatchObject({ kind: 'success' })
    h.im.dismissPaymentResult()
    h.im.cancel()
  }

  it('a change of run clears every line of the old run: its own payment does not come back (switch on)', async () => {
    const h = await harness()
    await payByHand(h)
    emit(payment('evt_r1_1', 'A', 'B', '5.00'))
    expect(h.lines(), 'precondition: the own payment is shown as its stream line').toEqual([`Анна → Пекарня: 5.00${NBSP}UAH`])
    expect(h.im.history, 'precondition: the own line is kept, hidden').toHaveLength(1)
    h.real.runId = 'r2'
    await flush()
    expect(h.lines(), 'another run: no line of the old one, own or network').toEqual([])
    h.feedOn.value = false
    expect(h.lines(), 'and the switch turned off does not bring the old run\'s own line back').toEqual([])
    h.rm.stopSse()
  })

  it('a change of equivalent clears every line: the own payment in UAH does not come back under EUR, nor back in UAH', async () => {
    const h = await harness()
    await payByHand(h)
    emit(payment('evt_r1_1', 'A', 'B', '5.00'))
    expect(h.lines(), 'precondition: the own payment is shown as its stream line').toEqual([`Анна → Пекарня: 5.00${NBSP}UAH`])
    expect(h.im.history, 'precondition: the own line is kept, hidden').toHaveLength(1)
    h.equivalent.value = 'EUR'
    expect(h.lines(), 'another equivalent: no line of the old one, own or network').toEqual([])
    h.equivalent.value = 'UAH'
    expect(h.lines(), 'back to UAH: the cleared lines stay cleared').toEqual([])
    h.rm.stopSse()
  })

  it('switch off: a toggle keeps the own lines of the current run; a change of run clears them', async () => {
    const h = await harness({ feedOn: false })
    await payByHand(h)
    expect(h.lines()).toEqual(['Платёж 5.00 UAH: Анна → Пекарня'])
    h.feedOn.value = true
    h.feedOn.value = false
    expect(h.lines(), 'a toggle keeps the own line of the current run').toEqual(['Платёж 5.00 UAH: Анна → Пекарня'])
    h.real.runId = 'r2'
    await flush()
    expect(h.lines(), 'another run: the own line of the old one is gone').toEqual([])
    h.rm.stopSse()
  })

  it('a change of run clears the network\'s lines', async () => {
    const h = await harness()
    emit(payment('evt_r1_1'))
    expect(h.network()).toHaveLength(1)
    h.real.runId = 'r2'
    await flush()
    expect(h.network(), 'another run: the lines of the old one are gone').toEqual([])
    h.rm.stopSse()
  })

  it('a change of equivalent clears the lines; the switch turned off clears them, and on again starts empty', async () => {
    const h = await harness()
    emit(payment('evt_r1_1'))
    expect(h.lines()).toHaveLength(1)
    h.equivalent.value = 'EUR'
    expect(h.network(), 'another equivalent: the lines of the old one are gone').toEqual([])
    h.equivalent.value = 'UAH'
    h.rm.stopSse()

    const g = await harness()
    emit(payment('evt_r1_1'))
    g.feedOn.value = false
    expect(g.network()).toEqual([])
    g.feedOn.value = true
    expect(g.lines()).toEqual([])
    later()
    emit(payment('evt_r1_2'))
    expect(g.lines()).toHaveLength(1)
    g.rm.stopSse()
  })
})

describe('049 decision 9 (owner review 2026-10-20): the parts of a line - the names give way, the values do not', () => {
  const entry = (line: InteractHistoryEntry['line'], lead?: InteractHistoryEntry['lead']) => ({ line, ...(lead ? { lead } : {}) })
  const roles = (e: ReturnType<typeof entry>) => historyEntryParts(e, 'ru').map((p) => `${p.role}:${p.text}`)

  it('a payment: the two names, then the amount with its unit as one value; the parts are the line', () => {
    const e = entry({ key: 'feed.payment', vars: { from: 'Анна', to: 'Пекарня', amount: '120.00', unit: 'UAH' } })
    expect(roles(e)).toEqual(['name:Анна', 'value: → ', 'name:Пекарня', `value:: 120.00${NBSP}UAH`])
    expect(historyEntryParts(e, 'ru').map((p) => p.text).join('')).toBe(historyEntryText(e, 'ru'))
  })

  it('a failure: the reason is a value; a series: its count is the lead; a clearing (no names) is one value', () => {
    expect(roles(entry({ key: 'feed.failedLimit', vars: { from: 'A', to: 'B' } }))).toEqual([
      'name:A', 'value: → ', 'name:B', 'value:: не прошёл — превысил бы лимит линии',
    ])
    const series = entry({ key: 'feed.payment', vars: { from: 'A', to: 'B', amount: '6.00', unit: 'UAH' } }, { plural: 'feed.paymentSeries', count: 6 })
    expect(roles(series)[0]).toBe('lead:За 2 с: 6 платежей; последний — ')
    expect(roles(entry({ plural: 'feed.clearing', count: 1, vars: { amount: '30.00', unit: 'UAH' } }))).toEqual([
      `value:Клиринг: общий долг уменьшился на 30.00${NBSP}UAH; 1 круг`,
    ])
  })

  it('an own payment line: the amount before the names is a value too', () => {
    expect(roles(entry({ key: 'history.payment', vars: { amount: '5.00', unit: 'UAH', from: 'A', to: 'B' } }))).toEqual([
      'value:Платёж 5.00 UAH: ', 'name:A', 'value: → ', 'name:B',
    ])
  })
})
