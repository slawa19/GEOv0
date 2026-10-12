/**
 * 039 T3901 - R11 (one comparison of equivalents), PIPELINE tier.
 *
 * Corrected form (P-2; arbiter F5): the input the spec first named - `'UAH '` in the EVENT - already passes, the event
 * side is trimmed. The defect is on the side of the SNAPSHOT: `applyTopologyEvent` (`realEventPipeline.ts`) upper-cases
 * the snapshot's equivalent without trimming it, so a snapshot `'UAH '` and a topology event `'UAH'` are "different
 * equivalents" and the event is dropped silently. Contract (P-8): the semantics of the backend's `canonical_code()`
 * (`strip().upper()`) on BOTH operands; two different valid codes never become equal.
 *
 * What is asserted is the APPLIED STATE (the node and the line are in the snapshot, the patch reached the applier, the
 * loop is woken), with the exact-match case next to it as the control.
 *
 * LIMITS. One of the 21 comparisons of the product is exercised: the one with the asymmetry. The other twenty are not
 * walked here; their migration to one function is slice A's, with its own enumeration.
 */
import { describe, expect, it } from 'vitest'

import type { AcceptedSimulatorEvent } from '../api/normalizeSimulatorEvent'
import { applyAcceptedRealEvent, type RealEventDraft, type RealEventStateDeps } from '../composables/realEventPipeline'
import type { GraphSnapshot } from '../types'
import type { SimulatorAppState } from '../types/simulatorApp'
import { measured, red } from './p039'

function draftWith(equivalent: string): RealEventDraft {
  const snapshot: GraphSnapshot = { equivalent, generated_at: '2026-01-01T00:00:00Z', nodes: [{ id: 'A' }], links: [] }
  return {
    real: { runId: 'run_1', runStatus: null, lastError: '', runStats: { attempts: 0, committed: 0, rejected: 0, errors: 0, timeouts: 0, rejectedByCode: {}, errorsByCode: {} } },
    state: { loading: false, error: '', sourcePath: '', snapshot, selectedNodeId: null, flash: 0 } satisfies SimulatorAppState,
  }
}

function apply(snapshotEq: string, eventEq: string, kind: 'structural' | 'patch-only') {
  const draft = draftWith(snapshotEq)
  const applied: string[] = []
  const deps: RealEventStateDeps = {
    patchApplier: {
      applyNodePatches: (p) => { if (p?.length) applied.push(`node-patch:${p[0]!.id}`) },
      applyEdgePatches: (p) => { if (p?.length) applied.push(`edge-patch:${p[0]!.source}>${p[0]!.target}`) },
      applyNodeStatus: () => undefined,
      applyEdgeStatus: () => undefined,
    },
    isUserFacingRunError: () => false,
    inc: () => undefined,
  }
  const event = {
    event_id: 'evt_run_1_000002', ts: '2026-01-01T00:00:01Z', type: 'topology.changed', equivalent: eventEq,
    payload: {
      added_nodes: kind === 'structural' ? [{ pid: 'B', name: 'Node B', type: 'person' }] : [],
      removed_nodes: [],
      added_edges: kind === 'structural' ? [{ from_pid: 'A', to_pid: 'B', equivalent_code: 'UAH', limit: '10' }] : [],
      removed_edges: [],
      node_patch: [{ id: 'A', net_balance: '5' }],
      edge_patch: [{ source: 'A', target: 'B', used: '7' }],
    },
  } as AcceptedSimulatorEvent
  const intents = applyAcceptedRealEvent(event, 'run_1', draft, deps).map((i) => i.type)
  const snap = draft.state.snapshot!
  return { nodes: snap.nodes.map((n) => n.id), links: snap.links.map((l) => `${l.source}>${l.target}`), applied, intents, generatedAt: snap.generated_at }
}

const APPLIED_STRUCTURAL = {
  nodes: ['A', 'B'], links: ['A>B'], applied: ['node-patch:A', 'edge-patch:A>B'], intents: ['accepted-event', 'wake'], generatedAt: '2026-01-01T00:00:01Z',
}
const APPLIED_PATCH_ONLY = { nodes: ['A'], links: [], applied: ['node-patch:A', 'edge-patch:A>B'], intents: ['accepted-event', 'wake'], generatedAt: '2026-01-01T00:00:01Z' }

describe('039 R11 - a topology event of the snapshot\'s own equivalent is applied whatever the whitespace or case of either side', () => {
  it('control: exact match - the structural change and the patches are applied, the loop is woken', () => {
    expect(apply('UAH', 'UAH', 'structural')).toEqual(APPLIED_STRUCTURAL)
    expect(apply('UAH', 'UAH', 'patch-only')).toEqual(APPLIED_PATCH_ONLY)
  })

  it('already green: whitespace or case on the EVENT side, case on the snapshot side (the form the spec first named)', () => {
    for (const [snap, evt] of [['UAH', 'UAH '], ['UAH', ' uah'], ['uah', 'UAH']] as const) {
      expect(apply(snap, evt, 'structural'), `snapshot "${snap}", event "${evt}"`).toEqual(APPLIED_STRUCTURAL)
    }
  })

  red('whitespace on the SNAPSHOT side: snapshot "UAH ", event "UAH" - a structural change is applied (today: dropped silently)', () => {
    const got = apply('UAH ', 'UAH', 'structural')
    measured('R11 snapshot "UAH " + structural event "UAH"', got)
    expect(got).toEqual(APPLIED_STRUCTURAL)
  })

  red('whitespace on the SNAPSHOT side: snapshot " UAH", event "UAH" - a patch-only event is applied (today: dropped silently)', () => {
    const got = apply(' UAH', 'UAH', 'patch-only')
    measured('R11 snapshot " UAH" + patch-only event "UAH"', got)
    expect(got).toEqual(APPLIED_PATCH_ONLY)
  })

  it('invariant that must SURVIVE the fix: an event of ANOTHER valid equivalent is not applied to the snapshot', () => {
    const got = apply('UAH', 'USD', 'structural')
    expect({ nodes: got.nodes, links: got.links, applied: got.applied }).toEqual({ nodes: ['A'], links: [], applied: [] })
    expect(got.intents, 'and the loop is not woken for it').toEqual(['accepted-event'])
  })
})
