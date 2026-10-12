/**
 * 039 slice A (R11): the SITES that compare two equivalents, after their move to `sameEquivalent`
 * (`config/equivalents.ts`). For each site walked here three things are asserted through the site's own entry point:
 *  - the same equivalent in another spelling (blanks, case) on either side is still the same equivalent;
 *  - another valid equivalent is still another one (the owner's rule: equivalents are never mixed);
 *  - the site's own guard of a MISSING equivalent is what it was before the move.
 *
 * Walked here: SSE admission, `tx.updated` against the snapshot on screen, the delayed receiver label, the interact
 * cache's snapshot fallback and its local limit patch, the strict recovery snapshot, the "same scene" decision.
 * The topology event is `p039-repro/r11-snapshot-equivalent-whitespace.p039-repro.test.ts`.
 * NOT walked here (read only; their existing suites stay green): `useSimulatorRealMode` (scene context, SSE message,
 * connection context), `useMetricsPolling`, `useInteractMode` (clearing FX), `loadStrictRunRecoverySnapshot`,
 * the trustlines fetch epoch guards of the interact cache.
 */
import { computed, effectScope, nextTick, reactive, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import type { AcceptedSimulatorEvent } from '../api/normalizeSimulatorEvent'
import type { GraphSnapshot, TxUpdatedEvent } from '../types'
import { parseAmountStringOrNull } from '../utils/numberFormat'
import { useInteractDataCache } from './interact/useInteractDataCache'
import {
  applyAcceptedRealEvent,
  createRealEventReplayOwner,
  executeRealEventIntents,
  type RealEventDraft,
} from './realEventPipeline'
import type { useInteractMode } from './useInteractMode'
import { useSceneState } from './useSceneState'

type Actions = Parameters<typeof useInteractMode>[0]['actions']

const snap = (eq: string): GraphSnapshot => ({
  equivalent: eq, generated_at: 'x',
  nodes: [{ id: 'alice' }, { id: 'bob' }],
  links: [{ source: 'alice', target: 'bob', used: '1.00', trust_limit: '10.00', available: '9.00', status: 'active' }],
})

/** The same equivalent, spelled differently on each side. */
const SAME = [['UAH', 'UAH'], ['UAH ', 'UAH'], [' UAH', 'UAH'], ['UAH', ' UAH '], ['uah', 'UAH'], ['UAH', 'uah'], [' uah ', '\tUAH']] as const
/** Two different valid codes; three of the pairs differ only by a character a sloppy normaliser would strip. */
const DIFFERENT = [['UAH', 'USD'], ['AA_B', 'AAB'], ['AAB', 'AA_B'], ['A1', 'A_1'], ['UAH', 'UAH1'], ['UAH1', 'UAH']] as const

describe('039 R11 sites - SSE admission (`createRealEventReplayOwner.admit`)', () => {
  const admit = (eventEq: string, connectionEq: string) => createRealEventReplayOwner().admit({
    event: { event_id: 'evt_run_1_1', ts: 'x', type: 'tx.updated', equivalent: eventEq, ttl_ms: 1, edges: [] } as AcceptedSimulatorEvent,
    frameId: 'evt_run_1_1', connectionRunId: 'run_1', activeRunId: 'run_1', connectionEquivalent: connectionEq, currentCursor: null,
  })

  it('admits an event of the connection\'s equivalent in any spelling', () => {
    for (const [evt, conn] of SAME) expect(admit(evt, conn).status, `event "${evt}", connection "${conn}"`).toBe('accepted')
  })

  it('rejects an event of another equivalent, and an event or a connection without one', () => {
    for (const [evt, conn] of [...DIFFERENT, ['', 'UAH'], ['UAH', ''], ['  ', 'UAH'], ['', ''], ['  ', '']] as const) {
      expect(admit(evt, conn), `event "${evt}", connection "${conn}"`).toEqual({ status: 'rejected', kind: 'context', diagnostic: 'equivalent_mismatch' })
    }
  })
})

describe('039 R11 sites - `tx.updated` against the snapshot on screen (`applyAcceptedRealEvent`)', () => {
  function apply(snapshotEq: string | null, eventEq: string) {
    const draft = { real: { runId: 'run_1', runStatus: null, lastError: '', runStats: { attempts: 0, committed: 0, rejected: 0, errors: 0, timeouts: 0, rejectedByCode: {}, errorsByCode: {} } },
      state: { snapshot: snapshotEq === null ? null : snap(snapshotEq) } } as unknown as RealEventDraft
    const applyEdgePatches = vi.fn()
    const event = { event_id: 'e', ts: 'x', type: 'tx.updated', equivalent: eventEq, ttl_ms: 1, edges: [], edge_patch: [{ source: 'alice', target: 'bob', used: '5' }] } as TxUpdatedEvent
    const intents = applyAcceptedRealEvent(event, 'run_1', draft, {
      patchApplier: { applyNodePatches: vi.fn(), applyEdgePatches, applyNodeStatus: vi.fn(), applyEdgeStatus: vi.fn() },
      isUserFacingRunError: () => false, inc: vi.fn(),
    }).map((i) => i.type)
    return { patched: applyEdgePatches.mock.calls.length, fx: intents.includes('tx-fx'), committed: draft.real.runStats.committed }
  }

  it('patches the snapshot of the same equivalent in any spelling', () => {
    for (const [s, e] of SAME) expect(apply(s, e), `snapshot "${s}", event "${e}"`).toEqual({ patched: 1, fx: true, committed: 1 })
  })

  it('never patches the snapshot of another equivalent (the commit is still counted)', () => {
    for (const [s, e] of DIFFERENT) expect(apply(s, e), `snapshot "${s}", event "${e}"`).toEqual({ patched: 0, fx: false, committed: 1 })
  })

  it('the site\'s own guard, unchanged: a snapshot WITHOUT an equivalent restricts nothing; an event without one never matches a snapshot that has one', () => {
    expect(apply('', 'UAH'), 'snapshot "", event "UAH"').toEqual({ patched: 1, fx: true, committed: 1 })
    expect(apply('  ', 'UAH'), 'snapshot "  ", event "UAH"').toEqual({ patched: 1, fx: true, committed: 1 })
    expect(apply(null, 'UAH'), 'no snapshot, event "UAH"').toEqual({ patched: 1, fx: true, committed: 1 })
    expect(apply('UAH', '  '), 'snapshot "UAH", event "  "').toEqual({ patched: 0, fx: false, committed: 1 })
  })
})

describe('039 R11 sites - the delayed receiver label (`executeRealEventIntents`)', () => {
  function run(sceneEq: string | null | 'no-getter', labelEq: string) {
    const trace: string[] = []
    executeRealEventIntents(
      [{ type: 'tx-label-delayed', role: 'receiver', nodeId: 'B', signedAmount: '+1', unit: labelEq, throttleMs: 0, delayMs: 10, runId: 'run_1', equivalent: labelEq }],
      {
        getActiveRunId: () => 'run_1',
        ...(sceneEq === 'no-getter' ? {} : { getSceneEquivalent: () => sceneEq }),
        refreshSnapshot: vi.fn(), runRealTxFx: vi.fn(), runRealClearingDoneFx: vi.fn(),
        pushTxAmountLabel: () => trace.push('label'), onReceiverGuardDropped: () => trace.push('dropped'),
        clampRealTxTtlMs: () => 10, scheduleTimeout: (fn) => fn(),
      },
    )
    return trace
  }

  it('is pushed when the scene is of the label\'s equivalent in any spelling', () => {
    for (const [scene, label] of SAME) expect(run(scene, label), `scene "${scene}", label "${label}"`).toEqual(['label'])
  })

  it('is dropped when the scene is of another equivalent', () => {
    for (const [scene, label] of DIFFERENT) expect(run(scene, label), `scene "${scene}", label "${label}"`).toEqual(['dropped'])
  })

  it('the site\'s own guards, unchanged: no getter or no snapshot (`null`) - pushed; a scene with an EMPTY equivalent - dropped', () => {
    expect(run('no-getter', 'UAH')).toEqual(['label'])
    expect(run(null, 'UAH')).toEqual(['label'])
    expect(run('', 'UAH')).toEqual(['dropped'])
    expect(run('  ', 'UAH')).toEqual(['dropped'])
  })
})

describe('039 R11 sites - the interact cache: snapshot fallback and local limit patch', () => {
  function cache(selectedEq: string, snapshotEq: string) {
    const fail = async () => { throw new Error('not used') }
    const actions = { actionsDisabled: ref(false), sendPayment: fail, createTrustline: fail, updateTrustline: fail, closeTrustline: fail,
      runClearing: fail, fetchParticipants: async () => [], fetchTrustlines: async () => null as never, fetchPaymentTargets: async () => [] } as Actions
    return effectScope().run(() => useInteractDataCache({ actions, runId: ref('run_1'), equivalent: ref(selectedEq), snapshot: ref(snap(snapshotEq)), parseAmountStringOrNull }))!
  }

  it('the fallback serves the snapshot of the selected equivalent in any spelling, and a limit patch named in any spelling reaches it', async () => {
    for (const [selected, snapshotEq] of SAME) {
      const c = cache(selected, snapshotEq)
      await nextTick()
      expect(c.trustlines.value.map((t) => t.limit), `selected "${selected}", snapshot "${snapshotEq}"`).toEqual(['10.00'])
      c.patchTrustlineLimitLocal('alice', 'bob', '50.00', ` ${selected.toLowerCase()} `)
      expect(c.trustlines.value.map((t) => t.limit), `patched: selected "${selected}", snapshot "${snapshotEq}"`).toEqual(['50.00'])
    }
  })

  it('the fallback never serves a snapshot of another equivalent, and a patch of another equivalent never reaches the list', async () => {
    for (const [selected, snapshotEq] of DIFFERENT) {
      const c = cache(selected, snapshotEq)
      await nextTick()
      expect(c.trustlines.value, `selected "${selected}", snapshot "${snapshotEq}"`).toEqual([])
      const own = cache(selected, selected)
      await nextTick()
      own.patchTrustlineLimitLocal('alice', 'bob', '50.00', snapshotEq)
      expect(own.trustlines.value.map((t) => t.limit), `list of "${selected}", patch of "${snapshotEq}"`).toEqual(['10.00'])
    }
  })

  it('a snapshot WITHOUT an equivalent serves nobody - neither a selected code (as before), nor an empty selection (stricter since 039)', async () => {
    for (const selected of ['UAH', '', '  ']) {
      const c = cache(selected, '')
      await nextTick()
      expect(c.trustlines.value, `selected "${selected}", snapshot ""`).toEqual([])
    }
  })
})

describe('039 R11 sites - scene state: the strict recovery snapshot and the "same scene" decision', () => {
  function scene(over: Partial<Parameters<typeof useSceneState>[0]>) {
    const eq = ref('UAH')
    const state = reactive({ loading: false, error: '', sourcePath: '', snapshot: null as GraphSnapshot | null, selectedNodeId: null as string | null })
    const resetOverlays = vi.fn()
    const noop = Object.fromEntries(['clearScheduledTimeouts', 'resetCamera', 'resetLayoutKeyCache', 'resizeAndLayout',
      'ensureRenderLoop', 'setupResizeListener', 'teardownResizeListener', 'stopRenderLoop'].map((k) => [k, vi.fn()]))
    const s = useSceneState({ ...noop, eq, scene: ref('A'), layoutMode: ref('admin-force'), allowEqDeepLink: () => true,
      isEqAllowed: () => true, effectiveEq: computed(() => eq.value), state, resetOverlays, loadSnapshot: vi.fn(), ...over } as unknown as Parameters<typeof useSceneState>[0])
    return { s, state, resetOverlays }
  }
  const RUN_SOURCE = 'GET http://sim.test/simulator/runs/run-1/graph/snapshot?equivalent=UAH'

  it('recovery accepts the snapshot of the requested equivalent in any spelling', async () => {
    for (const [requested, got] of SAME) {
      const { s, state } = scene({ loadRecoverySnapshot: async () => ({ snapshot: snap(got), sourcePath: RUN_SOURCE }) })
      await expect(s.loadRecoveryScene({ runId: 'run-1', equivalent: requested, isCurrent: () => true }), `requested "${requested}", got "${got}"`).resolves.toBe(true)
      expect(state.snapshot?.equivalent).toBe(got)
    }
  })

  it('recovery refuses a snapshot of another equivalent or of none, before applying it', async () => {
    for (const [requested, got] of [...DIFFERENT, ['UAH', ''], ['UAH', '  ']] as const) {
      const { s, state } = scene({ loadRecoverySnapshot: async () => ({ snapshot: snap(got), sourcePath: RUN_SOURCE }) })
      await expect(s.loadRecoveryScene({ runId: 'run-1', equivalent: requested, isCurrent: () => true }), `requested "${requested}", got "${got}"`).rejects.toThrow('does not match')
      expect(state.snapshot).toBeNull()
    }
  })

  it('recovery without an equivalent is refused before any load (the site\'s own guard, unchanged)', async () => {
    const loadRecoverySnapshot = vi.fn()
    const { s } = scene({ loadRecoverySnapshot })
    await expect(s.loadRecoveryScene({ runId: 'run-1', equivalent: '  ', isCurrent: () => true })).rejects.toThrow('requires a run id and equivalent')
    expect(loadRecoverySnapshot).not.toHaveBeenCalled()
  })

  /** Loads `first`, then `second` with the same participants from the same source; says whether the second was an increment. */
  async function secondLoadIsIncrement(first: string, second: string): Promise<boolean> {
    const queue = [first, second]
    const { s, resetOverlays } = scene({ loadSnapshot: async () => ({ snapshot: snap(queue.shift()!), sourcePath: 'x' }) })
    await s.loadScene()
    await s.loadScene()
    return resetOverlays.mock.calls.length === 1
  }

  it('the same participants in the same equivalent, spelled differently, are an increment (overlays kept)', async () => {
    for (const [first, second] of SAME) expect(await secondLoadIsIncrement(first, second), `"${first}" then "${second}"`).toBe(true)
  })

  it('the same participants in another equivalent are a new scene (overlays reset)', async () => {
    for (const [first, second] of DIFFERENT) expect(await secondLoadIsIncrement(first, second), `"${first}" then "${second}"`).toBe(false)
  })

  it('STRICTER since 039: two snapshots WITHOUT an equivalent are not "the same equivalent" - a new scene, not an increment', async () => {
    expect(await secondLoadIsIncrement('', '')).toBe(false)
    expect(await secondLoadIsIncrement('', 'UAH')).toBe(false)
  })
})
