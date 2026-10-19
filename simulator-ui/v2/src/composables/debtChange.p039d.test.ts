/**
 * 039 slice D (T3940) - the total debt "was -> now" (spec 039, target 6, amendment P-5).
 *
 * The PRODUCTION pipeline (`applyAcceptedRealEvent`), patch applier and `useSystemBalance` are used; what is observed
 * is what the bottom row reads: `balance.totalUsed` (now) and `balance.debtBefore` (was).
 *
 * The scene separates the candidates for "was" (the scene of the browser reproducer R7):
 *   - settled total ......................................... 48.00 (cycle a>b>c>a of 10 each, and d>e of 18)
 *   - a payment raises d>e to 25: now 55.00, was 48.00
 *   - a clearing takes 10 off each line of the cycle: now 25.00, was 55.00 - NOT 25 + cleared_amount (10) = 35.00
 *
 * Walked: payment; clearing; the hold through a status frame; the next debt-changing event; a clearing (and a payment)
 * that changes nothing; an event of another equivalent; a replaced snapshot (equivalent switch, reload, incremental
 * refresh); a line whose debt is unknown; the debounce of the bar held by a fake clock (the "was" is the total just
 * before the patches, not the value the bar displayed); exact sums (no float, no rounding).
 *
 * NOT seen here: the DOM of the bar (`SystemBalanceBar.p039d.test.ts`), real network frames (the stand's
 * `e2e/qa/debt-before-after.qa.ts`), the topology path with structural changes (one case below, patches only).
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
  const status = () => send({ type: 'run_status', run_id: 'run_1', scenario_id: 's', state: 'paused', sim_time_ms: 1 })

  const scope = effectScope()
  // As the app wires it (`useSimulatorApp.ts`): the snapshot as a computed of the state, the pair read from the state.
  const balance = scope.run(() => useSystemBalance(computed(() => state.snapshot), () => state.debtChange).balance)!
  /** What the bottom row states: "was" (or null) and "now". */
  const shown = () => ({ was: balance.value.debtBefore, now: balance.value.totalUsed, eq: balance.value.equivalent })
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
  return { state, payment, clearing, status, shown, patch, replaceSnapshot, stop: () => scope.stop(), send }
}

const CYCLE_TO_ZERO = (s: ReturnType<typeof stand>) => [s.patch('a', 'b', '0.00'), s.patch('b', 'c', '0.00'), s.patch('c', 'a', '0.00')]

describe('039 D - "was -> now" of the total debt', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('counter-check: the settled scene states 48.00 and no "was"', () => {
    const s = stand()
    expect(s.shown()).toEqual({ was: null, now: '48.00', eq: 'UAH' })
    s.stop()
  })

  it('after a PAYMENT: was 48.00, now 55.00 - held through a status frame', () => {
    const s = stand()
    s.payment([s.patch('d', 'e', '25.00')])
    expect(s.shown()).toEqual({ was: '48.00', now: '55.00', eq: 'UAH' })
    s.status()
    expect(s.shown(), 'a status frame is not a debt-changing event').toEqual({ was: '48.00', now: '55.00', eq: 'UAH' })
    s.stop()
  })

  it('after a CLEARING: was 55.00 (the total just before its patches), now 25.00 - never now + cleared_amount (35.00)', () => {
    const s = stand()
    s.payment([s.patch('d', 'e', '25.00')])
    s.clearing(CYCLE_TO_ZERO(s))
    expect(s.shown()).toEqual({ was: '55.00', now: '25.00', eq: 'UAH' })
    s.status()
    expect(s.shown().was, 'held through a status frame').toBe('55.00')
    // The next debt-changing event replaces the pair with its own.
    s.payment([s.patch('d', 'e', '27.00')])
    expect(s.shown(), 'the next payment: was 25.00, now 27.00 - the clearing\'s 55.00 is gone').toEqual({ was: '25.00', now: '27.00', eq: 'UAH' })
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
    expect(s.shown(), 'was 55.00 (the new snapshot before the patches), not the displayed 48.00').toEqual({ was: '55.00', now: '25.00', eq: 'UAH' })
    s.stop()
  })

  it('a clearing that changes NOTHING states no "was" - also after a payment had stated one', () => {
    const s = stand()
    s.payment([s.patch('d', 'e', '25.00')])
    expect(s.shown().was, 'counter-check: the payment stated its "was"').toBe('48.00')
    s.clearing([], 'UAH', '0')
    expect(s.shown(), 'no patches: no "was"').toEqual({ was: null, now: '55.00', eq: 'UAH' })
    s.payment([s.patch('d', 'e', '25.00')])
    expect(s.shown(), 'a patch to the same value: no "was" either').toEqual({ was: null, now: '55.00', eq: 'UAH' })
    s.stop()
  })

  it('equivalents are never mixed: an event of another equivalent changes nothing, a switch of equivalent drops the "was"', async () => {
    const s = stand()
    s.payment([s.patch('d', 'e', '25.00')])
    s.payment([s.patch('d', 'e', '99.00')], 'USD')
    expect(s.shown(), 'a payment of USD on the UAH scene is not applied and states nothing').toEqual({ was: '48.00', now: '55.00', eq: 'UAH' })
    // The switch: the same lines in USD, with the SAME total as the UAH pair's "now" (55.00) - only the equivalent and
    // the object tell them apart.
    const within = await s.replaceSnapshot(sceneSnapshot('USD', { ...USED, 'd>e': '25.00' }))
    // §15 Codex 2026-10-19 (P2): the pair was compared with the DEBOUNCED snapshot, so for ~100 ms after the switch the
    // old "was" stayed beside the old total. Red before: { was: '48.00', now: '55.00', eq: 'UAH' }.
    expect(within, 'inside the debounce window (the totals still of the UAH object): no "was" already').toEqual({ was: null, now: '55.00', eq: 'UAH' })
    expect(s.shown(), 'the UAH "was" is not stated under USD').toEqual({ was: null, now: '55.00', eq: 'USD' })
    s.stop()
  })

  it('a reload of the same equivalent (a new snapshot object) drops the "was"', async () => {
    const s = stand()
    s.payment([s.patch('d', 'e', '25.00')])
    const within = await s.replaceSnapshot(sceneSnapshot('UAH', { ...USED, 'd>e': '25.00' }))
    expect(within.was, 'inside the debounce window: no "was" already (the refresh replaced the object)').toBeNull()
    expect(s.shown()).toEqual({ was: null, now: '55.00', eq: 'UAH' })
    s.payment([s.patch('d', 'e', '26.00')])
    expect(s.shown().was, 'counter-check: on the new object a payment states its "was" again').toBe('55.00')
    const gone = await s.replaceSnapshot(null as unknown as GraphSnapshot)
    expect(gone.was, 'no snapshot, inside the debounce window: nothing').toBeNull()
    expect(s.shown().was, 'no snapshot: nothing').toBeNull()
    s.stop()
  })

  it('unknown stays unknown: a line without an amount gives no "was" (it is not counted as zero)', () => {
    const s = stand(sceneSnapshot('UAH', { ...USED, 'e>f': undefined }))
    s.payment([s.patch('d', 'e', '25.00')])
    expect(s.shown().was).toBeNull()
    s.stop()
  })

  it('a pair whose "now" is no longer the total (a change by another path) is not stated', () => {
    const s = stand()
    s.payment([s.patch('d', 'e', '25.00')])
    // A debt change that did not go through an event's patches (a line edited in place).
    s.state.snapshot!.links.find((l) => l.source === 'e' && l.target === 'f')!.used = '1.00'
    expect(s.shown(), 'the bar states the total, without the stale pair').toEqual({ was: null, now: '56.00', eq: 'UAH' })
    s.stop()
  })

  it('a topology patch that changes a debt states its pair; one that changes none keeps the previous pair', () => {
    const s = stand()
    s.payment([s.patch('d', 'e', '25.00')])
    s.send({ type: 'topology.changed', equivalent: 'UAH', payload: { added_nodes: [], removed_nodes: [], added_edges: [], removed_edges: [], edge_patch: [{ source: 'e', target: 'f', status: 'active' }] } })
    expect(s.shown().was, 'no debt moved: the payment\'s pair stays').toBe('48.00')
    s.send({ type: 'topology.changed', equivalent: 'UAH', payload: { added_nodes: [], removed_nodes: [], added_edges: [], removed_edges: [], edge_patch: [s.patch('e', 'f', '5.00')] } })
    expect(s.shown()).toEqual({ was: '55.00', now: '60.00', eq: 'UAH' })
    s.stop()
  })

  it('exact sums: decimals a float would round, and more digits than the precision, are kept', () => {
    const s = stand(sceneSnapshot('UAH', { 'a>b': '0.1', 'b>c': '0.2', 'c>a': '9007199254740993.01' }))
    s.payment([s.patch('a', 'b', '0.105')])
    expect(s.shown()).toEqual({ was: '9007199254740993.31', now: '9007199254740993.315', eq: 'UAH' })
    s.stop()
  })
})
