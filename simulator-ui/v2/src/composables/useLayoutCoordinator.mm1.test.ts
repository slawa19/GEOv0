/**
 * MM1-M7 (pilot finding, owner decision 2026-10-31): in the split layouts (`balance-split`, `status-split`,
 * `type-split`) a participant whose attribute moves it into ANOTHER group is laid out again; a change inside its
 * group moves nothing, and the non-split layouts do not react to these attributes at all (spec 039, decision 9:
 * "a `net_sign` patch changes the picture without relayout" - the ordinary layouts).
 *
 * The stand is the app's own path: the production layout wiring (`useAppLayoutWiring` -> `useLayoutCoordinator`)
 * over a reactive app state, the real `computeLayoutForMode` (counted through a spy), and the patch arriving through
 * the real doors - the production patch applier (`demo/patches.ts`) and a raw SSE event through
 * `normalizeSimulatorEvent` -> `applyAcceptedRealEvent`, as `useSimulatorRealMode` / the playlist workbench feed it.
 *
 * Not judged here: the pixels and the animation (the relayout takes the same path as a layout-mode switch).
 */
import { computed, nextTick, reactive, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { normalizeSimulatorEvent } from '../api/normalizeSimulatorEvent'
import { createPatchApplier } from '../demo/patches'
import { computeLayoutForMode, type LayoutMode } from '../layout/forceLayout'
import type { GraphSnapshot, NodePatch } from '../types'
import type { SimulatorAppState } from '../types/simulatorApp'
import { keyEdge } from '../utils/edgeKey'
import { applyAcceptedRealEvent, type RealEventDraft } from './realEventPipeline'
import { useAppLayoutWiring } from './useAppLayoutWiring'

const TS = '2026-10-31T00:00:00Z'

function snapshotOf(overrides: Partial<Record<'A' | 'B' | 'C', Partial<GraphSnapshot['nodes'][number]>>> = {}): GraphSnapshot {
  return {
    equivalent: 'UAH',
    generated_at: TS,
    nodes: [
      { id: 'A', type: 'person', status: 'active', net_sign: -1, net_balance: '-10', ...overrides.A },
      { id: 'B', type: 'person', status: 'active', net_sign: 0, net_balance: '0', ...overrides.B },
      { id: 'C', type: 'business', status: 'active', net_sign: 1, net_balance: '10', ...overrides.C },
    ],
    links: [
      { source: 'A', target: 'B', trust_limit: '100', used: '10', available: '90', status: 'active' },
      { source: 'C', target: 'A', trust_limit: '100', used: '10', available: '90', status: 'active' },
    ],
  }
}

function stand(mode: LayoutMode) {
  const state = reactive<SimulatorAppState>({
    loading: false,
    error: '',
    sourcePath: 'fixtures:mm1',
    snapshot: snapshotOf(),
    selectedNodeId: null,
  })
  const layoutMode = ref<LayoutMode>(mode)
  const computeSpy = vi.fn(computeLayoutForMode)

  const wiring = useAppLayoutWiring({
    canvasEl: ref(null),
    fxCanvasEl: ref(null),
    hostEl: ref(null),
    snapshot: computed(() => state.snapshot),
    layoutMode,
    dprClamp: computed(() => 1),
    isTestMode: computed(() => true),
    getSourcePath: () => state.sourcePath,
    computeLayoutForMode: computeSpy,
  })
  wiring.initComputeLayout({
    pinning: { captureBaseline: () => undefined, reapplyPinnedToLayout: () => undefined },
    physics: { recreateForCurrentLayout: () => undefined },
  })
  const coordinator = wiring.layoutCoordinator
  coordinator.layout.w = 800
  coordinator.layout.h = 600

  const applier = createPatchApplier({
    getSnapshot: () => state.snapshot,
    getLayoutNodes: () => coordinator.layout.nodes,
    getLayoutLinks: () => coordinator.layout.links,
    keyEdge,
  })

  const draft: RealEventDraft = {
    real: {
      runId: 'run_1',
      runStatus: null,
      lastError: '',
      runStats: { attempts: 0, committed: 0, rejected: 0, errors: 0, timeouts: 0, rejectedByCode: {}, errorsByCode: {} },
    },
    state,
  }

  let seq = 0
  /** A raw SSE event through the real normalizer and the real event pipeline. */
  function sse(raw: Record<string, unknown>) {
    const n = normalizeSimulatorEvent({ event_id: `evt_run_1_${++seq}`, ts: TS, equivalent: 'UAH', ...raw })
    if (n.status !== 'accepted') throw new Error(`stand: event not accepted: ${n.diagnostic}`)
    applyAcceptedRealEvent(n.event, 'run_1', draft, {
      patchApplier: applier,
      isUserFacingRunError: () => false,
      inc: () => undefined,
    })
  }

  async function settle() {
    await nextTick()
    vi.runAllTimers()
    await nextTick()
    vi.runAllTimers()
  }

  async function start() {
    coordinator.requestRelayoutDebounced(0)
    await settle()
    expect(computeSpy, 'precondition: the first layout').toHaveBeenCalledTimes(1)
  }

  const patch = async (p: NodePatch) => {
    applier.applyNodePatches([p])
    await settle()
  }

  const lastLaidOutNode = (id: string) => {
    const lastSnapshot = computeSpy.mock.calls.at(-1)?.[0] as GraphSnapshot | undefined
    return lastSnapshot?.nodes.find((n) => n.id === id)
  }

  return { state, layoutMode, computeSpy, coordinator, applier, sse, settle, start, patch, lastLaidOutNode }
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('MM1-M7: balance-split re-lays out a participant that changed its group', () => {
  it('(a) net_sign 0 -> 1 (neutral -> creditors) through the patch applier lays out again, from the patched value', async () => {
    const s = stand('balance-split')
    await s.start()

    await s.patch({ id: 'B', net_sign: 1, net_balance: '5' })

    expect(s.computeSpy, `computeLayout calls after net_sign 0->1: ${s.computeSpy.mock.calls.length}`).toHaveBeenCalledTimes(2)
    expect(s.lastLaidOutNode('B')?.net_sign, 'the relayout read a stale copy').toBe(1)
    expect(s.computeSpy.mock.calls.at(-1)?.[3]).toBe('balance-split')
  })

  it('(a, SSE) the same transition sent as a raw tx.updated node_patch lays out again', async () => {
    const s = stand('balance-split')
    await s.start()

    s.sse({ type: 'tx.updated', node_patch: [{ id: 'B', net_sign: -1, net_balance: '-5' }] })
    await s.settle()

    expect(s.computeSpy, `computeLayout calls after an SSE net_sign 0->-1: ${s.computeSpy.mock.calls.length}`).toHaveBeenCalledTimes(2)
    expect(s.lastLaidOutNode('B')?.net_sign).toBe(-1)
  })

  it('(b) a change of the amount inside the group moves nothing, also many in a row', async () => {
    const s = stand('balance-split')
    await s.start()

    for (let i = 1; i <= 10; i++) await s.patch({ id: 'C', net_balance: String(10 + i), net_balance_atoms: String(1000 + i) })
    await s.patch({ id: 'C', net_sign: 1 }) // the same sign again

    expect(s.computeSpy).toHaveBeenCalledTimes(1)
  })

  it('(b) a patch without net_sign keeps the group (the field is preserved, no jump to "?")', async () => {
    const s = stand('balance-split')
    await s.start()

    await s.patch({ id: 'A', viz_color_key: 'x', net_balance: '-12' })

    expect(s.state.snapshot?.nodes[0]?.net_sign, 'precondition: the incomplete patch kept the field').toBe(-1)
    expect(s.computeSpy).toHaveBeenCalledTimes(1)
  })

  it('(b) explicit null groups with neutral in this layout: 0 -> null moves nothing, null -> 1 does', async () => {
    const s = stand('balance-split')
    await s.start()

    await s.patch({ id: 'B', net_sign: null })
    expect(s.computeSpy, 'neutral -> null is the same layout group').toHaveBeenCalledTimes(1)

    await s.patch({ id: 'B', net_sign: 1 })
    expect(s.computeSpy, 'null -> 1 leaves the neutral group').toHaveBeenCalledTimes(2)
  })

  it('every transition is laid out, and nothing between transitions', async () => {
    const s = stand('balance-split')
    await s.start()

    await s.patch({ id: 'B', net_sign: 1 })
    await s.patch({ id: 'B', net_balance: '7' })
    await s.patch({ id: 'B', net_sign: -1 })
    await s.patch({ id: 'B', net_balance: '-7' })

    expect(s.computeSpy).toHaveBeenCalledTimes(3)
  })
})

describe('MM1-M7: the ordinary layouts do not react to these attributes (spec 039, decision 9)', () => {
  for (const mode of ['admin-force', 'community-clusters'] as const) {
    it(`(c) ${mode}: a net_sign transition, a freeze and a type change lay out nothing`, async () => {
      const s = stand(mode)
      await s.start()

      await s.patch({ id: 'B', net_sign: 1 })
      s.sse({ type: 'tx.updated', node_patch: [{ id: 'A', net_sign: 1 }] })
      s.sse({ type: 'topology.changed', payload: { frozen_nodes: ['C'] } })
      await s.settle()
      s.state.snapshot = snapshotOf({ B: { type: 'business', net_sign: -1 } })
      await s.settle()

      expect(s.state.snapshot?.nodes[1]?.type, 'precondition').toBe('business')
      expect(s.computeSpy).toHaveBeenCalledTimes(1)
    })
  }
})

describe('MM1-M7: status-split and type-split', () => {
  it('(d) status-split: a freeze from SSE (active -> suspended) lays out again; a net_sign patch does not', async () => {
    const s = stand('status-split')
    await s.start()

    await s.patch({ id: 'B', net_sign: 1 })
    expect(s.computeSpy, 'net_sign is not the status-split attribute').toHaveBeenCalledTimes(1)

    s.sse({ type: 'topology.changed', payload: { frozen_nodes: ['B'] } })
    await s.settle()

    expect(s.state.snapshot?.nodes[1]?.status, 'precondition: the freeze reached the snapshot').toBe('suspended')
    expect(s.computeSpy).toHaveBeenCalledTimes(2)
    expect(s.lastLaidOutNode('B')?.status).toBe('suspended')
  })

  it('(d) type-split: a snapshot with a changed type lays out again; a net_sign patch and a type change inside "other" do not', async () => {
    const s = stand('type-split')
    await s.start()

    await s.patch({ id: 'B', net_sign: 1 })
    s.state.snapshot = snapshotOf({ B: { type: 'hub' } }) // person -> hub: both are "other" in this layout
    await s.settle()
    expect(s.computeSpy).toHaveBeenCalledTimes(1)

    s.state.snapshot = snapshotOf({ B: { type: 'business' } })
    await s.settle()

    expect(s.computeSpy).toHaveBeenCalledTimes(2)
    expect(s.lastLaidOutNode('B')?.type).toBe('business')
  })
})

describe('MM1-M7: mode switches and scene changes', () => {
  it('split -> ordinary -> split: a transition is laid out only while a split layout is shown', async () => {
    const s = stand('balance-split')
    await s.start()

    s.layoutMode.value = 'admin-force'
    await s.settle()
    expect(s.computeSpy, 'the mode switch itself').toHaveBeenCalledTimes(2)

    await s.patch({ id: 'B', net_sign: 1 })
    expect(s.computeSpy, 'admin-force ignores the sign').toHaveBeenCalledTimes(2)

    s.layoutMode.value = 'balance-split'
    await s.settle()
    expect(s.computeSpy, 'the switch back lays out once, with the current groups').toHaveBeenCalledTimes(3)
    expect(s.lastLaidOutNode('B')?.net_sign).toBe(1)

    await s.patch({ id: 'B', net_sign: -1 })
    expect(s.computeSpy).toHaveBeenCalledTimes(4)
  })

  it('a new snapshot with the same groups (another equivalent / a refresh) lays out nothing extra; reset of the cache still lays out', async () => {
    const s = stand('balance-split')
    await s.start()

    s.state.snapshot = { ...snapshotOf(), equivalent: 'EUR', generated_at: '2026-10-31T00:00:01Z' }
    await s.settle()
    expect(s.computeSpy).toHaveBeenCalledTimes(1)

    s.coordinator.resetLayoutKeyCache()
    s.coordinator.resizeAndLayout()
    expect(s.computeSpy).toHaveBeenCalledTimes(2)
  })
})
