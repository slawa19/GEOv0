/**
 * RT-013-2 — programme 013 (`specs/013-frontend-data-honesty/spec.md`), finding `F-013-2`.
 *
 * WHAT HAPPENS. A payment (`tx.updated`) or a clearing (`clearing.done`) arrives over SSE.
 * `applyAcceptedRealEvent` applies the node/edge patches into the live snapshot
 * (`realEventPipeline.ts:349-350` for `tx.updated`, `:414-415` for `clearing.done`) and does
 * **not** touch `snapshot.generated_at`. Compare with `topology.changed`, which does advance it
 * (`realEventPipeline.ts:236` and `:312`).
 *
 * WHAT A CORRECT SYSTEM WOULD DO. The two consumers keyed on the snapshot revision —
 * `interact/useInteractDataCache.ts:344` (drops the payment-targets cache at `:346-350`) and
 * `useInteractMode.ts:260` (re-prefetches payment targets for the active From) — would see that
 * the graph data changed and would stop answering with the pre-payment reachability set.
 *
 * WHAT THIS ONE DOES. Nothing invalidates. The To-dropdown keeps offering a target that the
 * backend no longer considers reachable, and `canSendPayment` keeps gating on that stale set.
 *
 * WHAT THESE ASSERTIONS CAN AND CANNOT TELL APART — required by the programme discipline.
 * The assertions below are on the OBSERVABLE CONSEQUENCE: what `paymentToTargetIds` answers and
 * whether the consumer re-asked the backend. Therefore:
 *   - a "fix" that bumps `generated_at` on patch events but leaves the cache answering the old
 *     value FAILS here (the consequence assertions), and also FAILS the separate
 *     `generated_at`-is-not-forged assertion in each test;
 *   - a fix that invalidates a local graph-data revision WITHOUT touching `generated_at` — the
 *     shape the spec mandates, aligned with `useLayoutCoordinator.ts:280` ("do not key layout on
 *     generated_at") and `008/evidence-index.md:686` — PASSES;
 *   - what they CANNOT tell apart is *which* revision counter a fix introduces, only that data
 *     changes must reach these two consumers while `generated_at` keeps meaning "the time the
 *     authoritative snapshot was generated".
 *
 * INTERLOCK. `interact/useInteractDataCache.paymentTargets.test.ts:100` asserts the opposite
 * direction — that changing `generated_at` DOES invalidate. It is green today and must stay green
 * after the fix; it is not evidence about this finding.
 */
import { describe, expect, it, vi } from 'vitest'
import { computed, effectScope, nextTick, reactive, ref } from 'vue'

import type { AcceptedSimulatorEvent } from '../api/normalizeSimulatorEvent'
import { createPatchApplier } from '../demo/patches'
import type { GraphSnapshot } from '../types'
import type { SimulatorAppState } from '../types/simulatorApp'
import { keyEdge } from '../utils/edgeKey'
import { applyAcceptedRealEvent, type RealEventDraft, type RealEventStateDeps } from './realEventPipeline'
import type { useInteractActions } from './useInteractActions'
import { useInteractMode } from './useInteractMode'

type CacheActions = ReturnType<typeof useInteractActions>
type PaymentTargetsResult = Awaited<ReturnType<CacheActions['fetchPaymentTargets']>>
type ParticipantsResult = Awaited<ReturnType<CacheActions['fetchParticipants']>>
type TrustlinesResult = Awaited<ReturnType<CacheActions['fetchTrustlines']>>
type MockedCacheActions = CacheActions & {
  fetchParticipants: ReturnType<typeof vi.fn<CacheActions['fetchParticipants']>>
  fetchTrustlines: ReturnType<typeof vi.fn<CacheActions['fetchTrustlines']>>
  fetchPaymentTargets: ReturnType<typeof vi.fn<CacheActions['fetchPaymentTargets']>>
}

const RUN_ID = 'run_1'
const EQ = 'EUR'
const SNAPSHOT_TS = '2026-01-01T00:00:00Z'
const EVENT_TS = '2026-01-01T00:00:05Z'

/** The snapshot the production pipeline actually mutates: `state.snapshot` of the reactive app state. */
function makeSnapshot(): GraphSnapshot {
  return {
    equivalent: EQ,
    generated_at: SNAPSHOT_TS,
    nodes: [
      { id: 'alice', name: 'Alice', status: 'active', net_balance: '0' },
      { id: 'bob', name: 'Bob', status: 'active', net_balance: '0' },
      { id: 'carol', name: 'Carol', status: 'active', net_balance: '0' },
    ],
    links: [
      { source: 'alice', target: 'bob', trust_limit: '100', used: '0', available: '100', status: 'active' },
      { source: 'bob', target: 'carol', trust_limit: '100', used: '0', available: '100', status: 'active' },
    ],
  }
}

async function flush() {
  for (let i = 0; i < 8; i += 1) {
    await Promise.resolve()
    await nextTick()
  }
}

function mk() {
  // What `GET /runs/{run_id}/payment-targets` answers *right now*. Mutating `backend.targets`
  // models the backend changing its answer once balances move — which is the whole point: the UI
  // may keep an answer only while it is still the answer the backend would give.
  const backend = { targets: ['bob', 'carol'] }

  const actions: MockedCacheActions = {
    actionsDisabled: ref(false),
    sendPayment: vi.fn(async () => {
      throw new Error('not used in this test')
    }),
    createTrustline: vi.fn(async () => {
      throw new Error('not used in this test')
    }),
    updateTrustline: vi.fn(async () => {
      throw new Error('not used in this test')
    }),
    closeTrustline: vi.fn(async () => {
      throw new Error('not used in this test')
    }),
    runClearing: vi.fn(async () => {
      throw new Error('not used in this test')
    }),
    fetchParticipants: vi.fn<CacheActions['fetchParticipants']>(async () => [] as ParticipantsResult),
    fetchTrustlines: vi.fn<CacheActions['fetchTrustlines']>(async () => [] as TrustlinesResult),
    fetchPaymentTargets: vi.fn<CacheActions['fetchPaymentTargets']>(
      async () => backend.targets.map((pid) => ({ to_pid: pid, hops: 1 })) as PaymentTargetsResult,
    ),
  } as MockedCacheActions

  // Production wiring, not a parallel harness:
  //   useSimulatorApp.ts:534  `reactive<SimulatorAppState>` — the object the pipeline mutates
  //   useSimulatorApp.ts:767  `computed(() => state.snapshot)`
  //   useSimulatorApp.ts:815  that computed is what Interact Mode is given
  //   useSimulatorApp.ts:1209 the same `createPatchApplier` the real pipeline is fed
  const state = reactive<SimulatorAppState>({
    loading: false,
    error: '',
    sourcePath: '',
    snapshot: makeSnapshot(),
    selectedNodeId: null,
    flash: 0,
  })
  const snapshotRef = computed(() => state.snapshot)
  const runId = ref(RUN_ID)
  const equivalent = ref(EQ)

  const scope = effectScope()
  const mode = scope.run(() => useInteractMode({ actions, runId, equivalent, snapshot: snapshotRef }))!

  const patchApplier = createPatchApplier({
    getSnapshot: () => state.snapshot,
    getLayoutNodes: () => [],
    getLayoutLinks: () => [],
    keyEdge,
  })

  const draft: RealEventDraft = {
    real: {
      runId: RUN_ID,
      runStatus: null,
      lastError: '',
      runStats: {
        attempts: 0,
        committed: 0,
        rejected: 0,
        errors: 0,
        timeouts: 0,
        rejectedByCode: {},
        errorsByCode: {},
      },
    },
    state,
  }
  const deps: RealEventStateDeps = {
    patchApplier,
    isUserFacingRunError: (code) => code === 'INTERNAL_ERROR',
    inc: (map, key) => {
      map[key] = (map[key] ?? 0) + 1
    },
  }

  const dispatch = (event: AcceptedSimulatorEvent) => applyAcceptedRealEvent(event, RUN_ID, draft, deps)

  /** Bring the UI to the state in which the payment-targets cache is populated and consulted. */
  async function openPaymentFlowFromAlice() {
    mode.startPaymentFlowWithFrom('alice')
    await flush()
  }

  const link = (source: string, target: string) =>
    state.snapshot!.links.find((l) => l.source === source && l.target === target)!
  const node = (id: string) => state.snapshot!.nodes.find((n) => n.id === id)!

  return { actions, backend, state, mode, dispatch, draft, openPaymentFlowFromAlice, link, node, scope }
}

/** alice pays bob the whole trust limit: balances move, and bob stops being reachable. */
const paymentThatExhaustsAliceToBob: AcceptedSimulatorEvent = {
  event_id: 'evt_run_1_100',
  ts: EVENT_TS,
  type: 'tx.updated',
  equivalent: EQ,
  from: 'alice',
  to: 'bob',
  amount: '100',
  ttl_ms: 1200,
  edges: [{ from: 'alice', to: 'bob' }],
  node_patch: [
    { id: 'alice', net_balance: '-100' },
    { id: 'bob', net_balance: '100' },
  ],
  edge_patch: [{ source: 'alice', target: 'bob', used: '100', available: '0' }],
}

/** Clearing moves the same balances, carried by the other patch-bearing event. */
const clearingThatMovesTheSameEdge: AcceptedSimulatorEvent = {
  event_id: 'evt_run_1_101',
  ts: EVENT_TS,
  type: 'clearing.done',
  equivalent: EQ,
  plan_id: 'plan_1',
  cycle_edges: [{ from: 'alice', to: 'bob' }],
  node_patch: [
    { id: 'alice', net_balance: '-100' },
    { id: 'bob', net_balance: '100' },
  ],
  edge_patch: [{ source: 'alice', target: 'bob', used: '100', available: '0' }],
}

describe('RT-013-2: patch-bearing SSE events change graph data without invalidating what is keyed on the snapshot revision', () => {
  it('tx.updated: a balance-changing payment leaves the payment-targets cache answering its pre-payment value', async () => {
    const h = mk()
    await h.openPaymentFlowFromAlice()

    // Pre-state: the To-dropdown holds the backend answer for alice.
    expect(h.actions.fetchPaymentTargets, 'the payment flow prefetches reachable targets once').toHaveBeenCalledTimes(1)
    expect(h.mode.paymentToTargetIds.value, 'before the payment, bob and carol are reachable').toEqual(
      new Set(['bob', 'carol']),
    )

    const revisionBefore = h.state.snapshot!.generated_at
    // The payment consumes the only trustline to bob, so the backend would no longer list bob.
    h.backend.targets = ['carol']

    h.dispatch(paymentThatExhaustsAliceToBob)
    await flush()

    // 1. The event really changed the data — this test is not vacuous.
    //    Anchor: realEventPipeline.ts:349-350 (applyNodePatches / applyEdgePatches).
    expect(h.node('alice').net_balance, 'tx.updated applied the node patch').toBe('-100')
    expect(h.link('alice', 'bob').available, 'tx.updated applied the edge patch').toBe('0')

    // 2. …and did not advance the snapshot revision. `topology.changed` does exactly that at
    //    realEventPipeline.ts:236 and :312; the tx.updated branch has no such line.
    //    Kept as an assertion, not a note: moving `generated_at` on a patch event forges the
    //    generation time of an authoritative snapshot, which useLayoutCoordinator.ts:280 already
    //    rules out. A fix that moves it instead of introducing a data revision must fail here.
    expect(h.state.snapshot!.generated_at, 'a patch event must not forge the snapshot generation time').toBe(
      revisionBefore,
    )

    // 3. THE DEFECT — the consequence, not the mechanism. Both consumers keyed on the revision
    //    stayed asleep: useInteractDataCache.ts:344 never cleared the cache (:346-350) and
    //    useInteractMode.ts:260 never re-prefetched. The dropdown still offers bob.
    expect(
      h.actions.fetchPaymentTargets,
      'graph data changed, so the active payment flow must re-ask the backend (useInteractMode.ts:260)',
    ).toHaveBeenCalledTimes(2)
    expect(
      h.mode.paymentToTargetIds.value,
      'the To-dropdown must not keep offering a target the backend dropped (useInteractDataCache.ts:344)',
    ).toEqual(new Set(['carol']))
  })

  it('tx.updated: the stale target set keeps gating confirm, so the UI enables a payment the backend now refuses', async () => {
    const h = mk()
    await h.openPaymentFlowFromAlice()

    h.mode.setPaymentToPid('bob')
    expect(h.mode.phase.value, 'picking To moves the flow to confirm-payment').toBe('confirm-payment')

    h.backend.targets = ['carol']
    h.dispatch(paymentThatExhaustsAliceToBob)
    await flush()

    // canSendPayment (useInteractMode.ts:286) answers from `paymentToTargetIds`, which answers
    // from the never-invalidated cache. A correct system, having re-read reachability, would not
    // present alice → bob as sendable; today it does, and the backend would answer NO_ROUTE.
    expect(
      h.mode.canSendPayment.value,
      'confirm must not stay enabled for a route the post-payment reachability no longer contains',
    ).toBe(false)
  })

  it('clearing.done: the same mechanism, the same stale cache — one event was not enough for this finding', async () => {
    const h = mk()
    await h.openPaymentFlowFromAlice()

    expect(h.actions.fetchPaymentTargets).toHaveBeenCalledTimes(1)
    expect(h.mode.paymentToTargetIds.value).toEqual(new Set(['bob', 'carol']))

    const revisionBefore = h.state.snapshot!.generated_at
    h.backend.targets = ['carol']

    h.dispatch(clearingThatMovesTheSameEdge)
    await flush()

    // Anchor: realEventPipeline.ts:414-415 — patches applied, `generated_at` untouched.
    expect(h.node('alice').net_balance, 'clearing.done applied the node patch').toBe('-100')
    expect(h.link('alice', 'bob').available, 'clearing.done applied the edge patch').toBe('0')
    expect(h.state.snapshot!.generated_at, 'a patch event must not forge the snapshot generation time').toBe(
      revisionBefore,
    )

    expect(
      h.actions.fetchPaymentTargets,
      'clearing changed balances too, so the active payment flow must re-ask the backend',
    ).toHaveBeenCalledTimes(2)
    expect(
      h.mode.paymentToTargetIds.value,
      'the To-dropdown must not keep offering a target the backend dropped after clearing',
    ).toEqual(new Set(['carol']))
  })

  it('COUNTER-EXAMPLE: run_status must not invalidate the graph caches, even though it is a fully processed event', async () => {
    const h = mk()
    await h.openPaymentFlowFromAlice()

    expect(h.actions.fetchPaymentTargets).toHaveBeenCalledTimes(1)
    // The backend answer would change if anyone asked — so a wrongly invalidating run_status
    // would move the observable set below, not only the call count.
    h.backend.targets = ['carol']

    const runStatus: AcceptedSimulatorEvent = {
      event_id: 'evt_run_1_102',
      ts: EVENT_TS,
      type: 'run_status',
      run_id: RUN_ID,
      scenario_id: 'scenario_1',
      state: 'running',
      attempts_total: 41,
      committed_total: 40,
      rejected_total: 1,
      errors_total: 0,
      timeouts_total: 0,
      last_error: null,
    }
    h.dispatch(runStatus)
    await flush()

    // The event was really processed — otherwise this counter-example would prove nothing.
    // Anchor: realEventPipeline.ts:326-343 writes run stats and touches no node or edge.
    expect(h.draft.real.runStats.committed, 'run_status updated the run counters').toBe(40)
    expect(h.node('alice').net_balance, 'run_status carries no graph data').toBe('0')
    expect(h.link('alice', 'bob').available, 'run_status carries no graph data').toBe('100')

    // Therefore nothing keyed on graph data may be dropped. A "fix" that invalidates on every
    // accepted event passes the three tests above and fails here — which is why this case exists.
    expect(
      h.actions.fetchPaymentTargets,
      'run_status changes no graph data, so it must not force a payment-targets re-fetch',
    ).toHaveBeenCalledTimes(1)
    expect(h.mode.paymentToTargetIds.value, 'run_status must leave a valid payment-targets answer in place').toEqual(
      new Set(['bob', 'carol']),
    )
  })
})
