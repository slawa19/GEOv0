/**
 * 039 slice D (T3940) - the total debt "was -> now" of the LAST CLEARING (spec 039 :160, target 6, amendment P-5;
 * advisor's decision 2026-10-19, option (a): only a clearing states it, payments and topology events neither create,
 * replace nor drop it - the label means "the clearing took this much off").
 *
 * The PRODUCTION pipeline (`applyAcceptedRealEvent`), patch applier and `useSystemBalance` are used; what is observed
 * is what the bottom row reads: `balance.clearingChange` (the clearing's own "was -> now") and `balance.totalUsed`
 * (the current total of the bar, which moves with payments).
 *
 * The scene separates the candidates for "was" (the scene of the browser stand test):
 *   - settled total ......................................... 48.00 (cycle a>b>c>a of 10 each, and d>e of 18)
 *   - a payment raises d>e to 25: the total is 55.00 - no "was" (no clearing yet)
 *   - a clearing takes 10 off each line of the cycle: was 55.00 -> now 25.00 - NOT 25 + cleared_amount (10) = 35.00
 *   - a payment after it raises d>e to 27: the bar says 27.00, the clearing's pair stays 55.00 -> 25.00 (its "now" is
 *     the clearing's own after-total, so the pair keeps meaning what the clearing did).
 *
 * Walked: payment without a clearing; clearing; payments, topology and status frames after it; the next clearing; a
 * clearing that changes nothing; an event of another equivalent; a replaced snapshot (equivalent switch, reload,
 * incremental refresh) - also INSIDE the bar's debounce window; a line whose debt is unknown; the debounce held by a
 * fake clock (the "was" is the total just before the patches, not the value the bar displayed); exact sums.
 *
 * NOT seen here: the DOM of the bar (`SystemBalanceBar.p039d.test.ts`), real network frames (the stand's
 * `e2e/qa/debt-before-after.qa.ts`), the topology path with structural changes.
 */
import { computed, effectScope, nextTick, reactive } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { AcceptedSimulatorEvent } from '../api/normalizeSimulatorEvent'
import { createPatchApplier } from '../demo/patches'
import type { EdgePatch, GraphSnapshot } from '../types'
import type { LayoutLink, LayoutNode } from '../types/layout'
import type { SimulatorAppState } from '../types/simulatorApp'
import { keyEdge } from '../utils/edgeKey'
import { applyAcceptedRealEvent, type RealEventDraft } from './realEventPipeline'
import { useSystemBalance } from './useSystemBalance'

const TS = '2026-10-19T00:00:00Z'
const USED: Record<string, string> = { 'a>b': '10.00', 'b>c': '10.00', 'c>a': '10.00', 'd>e': '18.00', 'e>f': '0.00' }

function sceneSnapshot(equivalent = 'UAH', used: Record<string, string | undefined> = USED): GraphSnapshot {
  return {
    equivalent,
    generated_at: TS,
    nodes: ['a', 'b', 'c', 'd', 'e', 'f'].map((id) => ({ id, name: id, status: 'active' })),
    links: Object.entries(used).map(([k, u]) => {
      const [source, target] = k.split('>') as [string, string]
      return { source, target, used: u, available: '90.00', trust_limit: '100.00', status: 'active' }
    }),
  }
}

function stand(initial: GraphSnapshot = sceneSnapshot()) {
  const state = reactive<SimulatorAppState>({ loading: false, error: '', sourcePath: '', snapshot: initial, selectedNodeId: null, debtChange: null })
  const layoutNodes: LayoutNode[] = []
  const layoutLinks: LayoutLink[] = []
  const draft: RealEventDraft = {
    real: { runId: 'run_1', runStatus: null, lastError: '', runStats: { attempts: 0, committed: 0, rejected: 0, errors: 0, timeouts: 0, rejectedByCode: {}, errorsByCode: {} } },
    state,
  }
  const deps = {
    patchApplier: createPatchApplier({ getSnapshot: () => state.snapshot, getLayoutNodes: () => layoutNodes, getLayoutLinks: () => layoutLinks, keyEdge }),
    isUserFacingRunError: () => false,
    inc: () => undefined,
  }
  let seq = 0
  const send = (event: Record<string, unknown>) =>
    applyAcceptedRealEvent({ event_id: `evt_run_1_${String(++seq).padStart(6, '0')}`, ts: TS, ...event } as AcceptedSimulatorEvent, 'run_1', draft, deps)
  const patch = (source: string, target: string, used: string): EdgePatch => ({ source, target, used, available: '1.00' })
  const payment = (edge_patch: EdgePatch[], equivalent = 'UAH') =>
    send({ type: 'tx.updated', equivalent, from: 'e', to: 'd', amount: '7.00', ttl_ms: 10, edges: [{ from: 'e', to: 'd' }], edge_patch })
  const clearing = (edge_patch: EdgePatch[], equivalent = 'UAH', cleared_amount = '10.00') =>
    send({ type: 'clearing.done', equivalent, plan_id: 'p1', cleared_cycles: 1, cleared_amount, cycle_edges: [], edge_patch })
  const topology = (edge_patch: Array<Record<string, unknown>>) =>
    send({ type: 'topology.changed', equivalent: 'UAH', payload: { added_nodes: [], removed_nodes: [], added_edges: [], removed_edges: [], edge_patch } })
  const status = () => send({ type: 'run_status', run_id: 'run_1', scenario_id: 's', state: 'paused', sim_time_ms: 1 })

  const scope = effectScope()
  // As the app wires it (`useSimulatorApp.ts`): the snapshot as a computed of the state, the pair read from the state.
  const balance = scope.run(() => useSystemBalance(computed(() => state.snapshot), () => state.debtChange).balance)!
  /** What the bottom row states: the clearing's "was" and "now" (or null), and the bar's current total. */
  const shown = () => ({
    was: balance.value.clearingChange?.before ?? null,
    after: balance.value.clearingChange?.after ?? null,
    now: balance.value.totalUsed,
    eq: balance.value.equivalent,
  })
  /**
   * Replaces the snapshot as `useSceneState.applySceneLoad` does; returns what the row stated INSIDE the bar's debounce
   * window (100 ms: the totals still of the old object), then lets the debounce fire.
   */
  const replaceSnapshot = async (next: GraphSnapshot) => {
    state.snapshot = next
    await nextTick()
    const withinDebounce = shown()
    vi.advanceTimersByTime(150)
    await nextTick()
    return withinDebounce
  }
  return { state, payment, clearing, topology, status, shown, patch, replaceSnapshot, stop: () => scope.stop() }
}

const CYCLE_TO_ZERO = (s: ReturnType<typeof stand>) => [s.patch('a', 'b', '0.00'), s.patch('b', 'c', '0.00'), s.patch('c', 'a', '0.00')]
const NONE = { was: null, after: null }

describe('039 D - "was -> now" of the last clearing', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('counter-check: the settled scene states 48.00 and no "was"', () => {
    const s = stand()
    expect(s.shown()).toEqual({ ...NONE, now: '48.00', eq: 'UAH' })
    s.stop()
  })

  it('a PAYMENT with no clearing before it states no "was"', () => {
    const s = stand()
    s.payment([s.patch('d', 'e', '25.00')])
    expect(s.shown(), 'only a clearing states "was -> now" (advisor 2026-10-19, option (a))').toEqual({ ...NONE, now: '55.00', eq: 'UAH' })
    s.stop()
  })

  it('after a CLEARING: was 55.00 (the total just before its patches) -> now 25.00 - never now + cleared_amount (35.00)', () => {
    const s = stand()
    s.payment([s.patch('d', 'e', '25.00')])
    s.clearing(CYCLE_TO_ZERO(s))
    expect(s.shown()).toEqual({ was: '55.00', after: '25.00', now: '25.00', eq: 'UAH' })
    s.status()
    expect(s.shown(), 'a status frame keeps it').toEqual({ was: '55.00', after: '25.00', now: '25.00', eq: 'UAH' })
    s.stop()
  })

  it('a payment AFTER a clearing leaves the clearing\'s pair in place; the bar\'s total moves', () => {
    const s = stand()
    s.payment([s.patch('d', 'e', '25.00')])
    s.clearing(CYCLE_TO_ZERO(s))
    s.payment([s.patch('d', 'e', '27.00')])
    expect(s.shown(), 'the pair is still the clearing\'s own 55.00 -> 25.00; the bar says 27.00').toEqual({ was: '55.00', after: '25.00', now: '27.00', eq: 'UAH' })
    s.topology([s.patch('e', 'f', '5.00')])
    expect(s.shown(), 'a topology patch that moves a debt does not replace it either').toEqual({ was: '55.00', after: '25.00', now: '32.00', eq: 'UAH' })
    s.stop()
  })

  it('the next clearing replaces the pair with its own', () => {
    const s = stand()
    s.payment([s.patch('d', 'e', '25.00')])
    s.clearing(CYCLE_TO_ZERO(s))
    s.payment([s.patch('d', 'e', '27.00')])
    s.clearing([s.patch('d', 'e', '20.00')], 'UAH', '7.00')
    expect(s.shown()).toEqual({ was: '27.00', after: '20.00', now: '20.00', eq: 'UAH' })
    s.stop()
  })

  it('the "was" is the AUTHORITATIVE total before the patches, not the value the bar displayed (debounce held)', async () => {
    const s = stand()
    // An incremental refresh: a new snapshot object where d>e is 25 already. The bar is debounced (100 ms), so it
    // still displays the old object (48.00) while the clearing is applied to the new one.
    const fresh = sceneSnapshot('UAH', { ...USED, 'd>e': '25.00' })
    s.state.snapshot = fresh
    await nextTick()
    expect(s.shown().now, 'counter-check: the debounce is held - the bar still displays 48.00').toBe('48.00')
    s.clearing(CYCLE_TO_ZERO(s))
    expect(s.shown().was, 'nothing is stated while the bar displays another snapshot than the pair was taken on').toBeNull()
    vi.advanceTimersByTime(150)
    await nextTick()
    expect(s.shown(), 'was 55.00 (the new snapshot before the patches), not the displayed 48.00').toEqual({ was: '55.00', after: '25.00', now: '25.00', eq: 'UAH' })
    s.stop()
  })

  it('a clearing that changes NOTHING drops the pair (no false "was")', () => {
    const s = stand()
    s.clearing(CYCLE_TO_ZERO(s))
    expect(s.shown().was, 'counter-check: the clearing stated its "was"').toBe('48.00')
    s.clearing([], 'UAH', '0')
    expect(s.shown(), 'no patches: no "was"').toEqual({ ...NONE, now: '18.00', eq: 'UAH' })
    s.clearing(CYCLE_TO_ZERO(s))
    expect(s.shown(), 'patches to the same values: no "was" either').toEqual({ ...NONE, now: '18.00', eq: 'UAH' })
    s.stop()
  })

  it('equivalents are never mixed: an event of another equivalent changes nothing, a switch of equivalent drops the "was"', async () => {
    const s = stand()
    s.clearing(CYCLE_TO_ZERO(s))
    s.clearing([s.patch('d', 'e', '0.00')], 'USD')
    expect(s.shown(), 'a clearing of USD on the UAH scene is not applied and states nothing').toEqual({ was: '48.00', after: '18.00', now: '18.00', eq: 'UAH' })
    // The switch: the same lines in USD, with the SAME total as the UAH pair's "now" (18.00) - only the equivalent and
    // the object tell them apart.
    const within = await s.replaceSnapshot(sceneSnapshot('USD', { 'a>b': '0.00', 'b>c': '0.00', 'c>a': '0.00', 'd>e': '18.00', 'e>f': '0.00' }))
    // §15 Codex 2026-10-19 (P2): the pair was compared with the DEBOUNCED snapshot, so for ~100 ms after the switch the
    // old "was" stayed beside the old total.
    expect(within, 'inside the debounce window (the totals still of the UAH object): no "was" already').toEqual({ ...NONE, now: '18.00', eq: 'UAH' })
    expect(s.shown(), 'the UAH "was" is not stated under USD').toEqual({ ...NONE, now: '18.00', eq: 'USD' })
    s.stop()
  })

  it('a reload of the same equivalent (a new snapshot object) drops the "was" - inside the debounce window too', async () => {
    const s = stand()
    s.clearing(CYCLE_TO_ZERO(s))
    const within = await s.replaceSnapshot(sceneSnapshot('UAH', { ...USED, 'a>b': '0.00', 'b>c': '0.00', 'c>a': '0.00' }))
    expect(within.was, 'inside the debounce window: no "was" already (the refresh replaced the object)').toBeNull()
    expect(s.shown()).toEqual({ ...NONE, now: '18.00', eq: 'UAH' })
    s.clearing([s.patch('d', 'e', '8.00')], 'UAH', '10.00')
    expect(s.shown().was, 'counter-check: on the new object a clearing states its "was" again').toBe('18.00')
    const gone = await s.replaceSnapshot(null as unknown as GraphSnapshot)
    expect(gone.was, 'no snapshot, inside the debounce window: nothing').toBeNull()
    expect(s.shown().was, 'no snapshot: nothing').toBeNull()
    s.stop()
  })

  it('unknown stays unknown: a line without an amount gives no "was" (it is not counted as zero)', () => {
    const s = stand(sceneSnapshot('UAH', { ...USED, 'e>f': undefined }))
    s.clearing(CYCLE_TO_ZERO(s))
    expect(s.shown().was).toBeNull()
    s.stop()
  })

  it('exact sums: decimals a float would round, and more digits than the precision, are kept', () => {
    const s = stand(sceneSnapshot('UAH', { 'a>b': '0.1', 'b>c': '0.2', 'c>a': '9007199254740993.01' }))
    s.clearing([s.patch('a', 'b', '0.005')], 'UAH', '0.095')
    expect(s.shown()).toEqual({ was: '9007199254740993.31', after: '9007199254740993.215', now: '9007199254740993.215', eq: 'UAH' })
    s.stop()
  })
})

/**
 * 039 D-fix (2026-10-19): an AUTOMATIC clearing with trust growth, as the backend tick publishes it. Growth raises the
 * limits of the lines the clearing touched and broadcasts `topology.changed` (`reason: trust_drift_growth`) with an
 * edge patch of EVERY edge of the equivalent, read after the clearing committed - so it carries the post-clearing
 * debts. Until the D-fix the tick broadcast it BEFORE `clearing.done` (`app/core/simulator/tick.py`
 * `RealTick._run_clearing`): the debts were already lowered when `clearing.done` arrived, and its pair came out empty.
 * The order the backend now holds (`tests/integration/test_p039_dfix_clearing_done_precedes_trust_growth_postgres.py`)
 * is `clearing.done` first, as of the clearing, then the growth.
 */
describe('039 D-fix - an automatic clearing with trust growth', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  /** The growth's edge patch: every edge, post-clearing `used`, grown limit (1000 -> 1050 on the cleared lines). */
  const growthPatch = (s: ReturnType<typeof stand>) => [
    ...CYCLE_TO_ZERO(s).map((p) => ({ ...p, available: '1050.00', trust_limit: '1050.00' })),
    { source: 'd', target: 'e', used: '25.00', available: '75.00', trust_limit: '100.00' },
    { source: 'e', target: 'f', used: '0.00', available: '100.00', trust_limit: '100.00' },
  ]
  const growth = (s: ReturnType<typeof stand>) =>
    s.topology(growthPatch(s).map((p) => ({ ...p })))

  it('the order the backend publishes - clearing.done, then the growth: the pair is stated, and the growth leaves it', () => {
    const s = stand()
    s.payment([s.patch('d', 'e', '25.00')])
    s.clearing(CYCLE_TO_ZERO(s))
    expect(s.shown(), 'the clearing states its own "was -> now"').toEqual({ was: '55.00', after: '25.00', now: '25.00', eq: 'UAH' })
    growth(s)
    expect(s.shown(), 'the growth that follows neither creates, replaces nor drops the pair (decision (a))').toEqual({ was: '55.00', after: '25.00', now: '25.00', eq: 'UAH' })
    expect(s.state.snapshot?.links.find((l) => l.source === 'a' && l.target === 'b')?.trust_limit, 'counter-check: the growth WAS applied').toBe('1050.00')
    s.stop()
  })

  it('premise - why the order matters: the growth BEFORE clearing.done (the tick until the D-fix) leaves no pair', () => {
    const s = stand()
    s.payment([s.patch('d', 'e', '25.00')])
    growth(s)
    expect(s.shown().now, 'the growth event alone already lowered the total').toBe('25.00')
    s.clearing(CYCLE_TO_ZERO(s))
    expect(s.shown(), 'clearing.done found nothing left to change: no pair').toEqual({ ...NONE, now: '25.00', eq: 'UAH' })
    s.stop()
  })
})
