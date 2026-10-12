/**
 * 039 T3901 - R11 (one comparison of equivalents), PIPELINE tier.
 *
 * Corrected form (P-2; arbiter F5): the input the spec first named - `'UAH '` in the EVENT - already passes, the event
 * side is trimmed. The defect is on the side of the SNAPSHOT: `applyTopologyEvent` (`realEventPipeline.ts`) upper-cases
 * the snapshot's equivalent without trimming it, so a snapshot `'UAH '` and a topology event `'UAH'` are "different
 * equivalents" and the event is dropped silently. Contract (P-8): the semantics of the backend's `canonical_code()`
 * (`strip().upper()`) on BOTH operands; two different valid codes never become equal, and no character is removed.
 *
 * What is asserted is the APPLIED STATE, through the PRODUCTION patch applier (`createPatchApplier`, `demo/patches.ts`):
 * the VALUES the patches carry are on the existing node and the existing line (`net_balance`, `used`, `available`), on
 * the snapshot and on the layout copies the canvas draws; the added node and line are in the snapshot; the loop is
 * woken. (Until the review of 2026-10-12 the applier was a stub that logged ids: a fix that normalised the equivalent
 * and then failed to apply the money would have passed.)
 *
 * SLICE A, its part after T3901 (`sameEquivalent`), must turn the two `red` tests green: then replace `red` by `it`.
 *
 * LIMITS. One of the 21 comparisons of the product is exercised: the one with the asymmetry. The other twenty are not
 * walked here; their migration to one function is slice A's, with its own enumeration.
 */
import { describe, expect, it } from 'vitest'

import type { AcceptedSimulatorEvent } from '../api/normalizeSimulatorEvent'
import { applyAcceptedRealEvent, type RealEventDraft } from '../composables/realEventPipeline'
import { createPatchApplier } from '../demo/patches'
import type { GraphSnapshot } from '../types'
import type { LayoutLink, LayoutNode } from '../types/layout'
import type { SimulatorAppState } from '../types/simulatorApp'
import { measured, red } from './p039'

const keyEdge = (a: string, b: string) => `${a}→${b}`

/** A snapshot with an EXISTING node A (balance 1), node B and line A -> B (used 3 of 10), and its layout copies. */
function apply(snapshotEq: string, eventEq: string, kind: 'structural' | 'patch-only') {
  const snapshot: GraphSnapshot = {
    equivalent: snapshotEq, generated_at: '2026-01-01T00:00:00Z',
    nodes: [{ id: 'A', net_balance: '1', net_sign: 1 }, { id: 'B', net_balance: '-1', net_sign: -1 }],
    links: [{ source: 'A', target: 'B', trust_limit: '10', used: '3', available: '7' }],
  }
  const layoutNodes: LayoutNode[] = snapshot.nodes.map((n, i) => ({ ...n, __x: i * 100, __y: 0 }))
  const layoutLinks: LayoutLink[] = snapshot.links.map((l) => ({ ...l, __key: keyEdge(l.source, l.target) }))
  const draft: RealEventDraft = {
    real: { runId: 'run_1', runStatus: null, lastError: '', runStats: { attempts: 0, committed: 0, rejected: 0, errors: 0, timeouts: 0, rejectedByCode: {}, errorsByCode: {} } },
    state: { loading: false, error: '', sourcePath: '', snapshot, selectedNodeId: null, flash: 0 } satisfies SimulatorAppState,
  }
  const event = {
    event_id: 'evt_run_1_000002', ts: '2026-01-01T00:00:01Z', type: 'topology.changed', equivalent: eventEq,
    payload: {
      added_nodes: kind === 'structural' ? [{ pid: 'C', name: 'Node C', type: 'person' }] : [],
      removed_nodes: [],
      added_edges: kind === 'structural' ? [{ from_pid: 'A', to_pid: 'C', equivalent_code: 'UAH', limit: '10' }] : [],
      removed_edges: [],
      node_patch: [{ id: 'A', net_balance: '5', net_sign: 1 }],
      edge_patch: [{ source: 'A', target: 'B', used: '7', available: '3' }],
    },
  } as AcceptedSimulatorEvent
  const intents = applyAcceptedRealEvent(event, 'run_1', draft, {
    patchApplier: createPatchApplier({ getSnapshot: () => draft.state.snapshot, getLayoutNodes: () => layoutNodes, getLayoutLinks: () => layoutLinks, keyEdge }),
    isUserFacingRunError: () => false,
    inc: () => undefined,
  }).map((i) => i.type)
  const snap = draft.state.snapshot!
  const a = snap.nodes.find((n) => n.id === 'A')
  const ab = snap.links.find((l) => l.source === 'A' && l.target === 'B')
  return {
    balanceOfA: a?.net_balance, usedAB: ab?.used, availableAB: ab?.available,
    layoutBalanceOfA: layoutNodes.find((n) => n.id === 'A')?.net_balance, layoutUsedAB: layoutLinks.find((l) => l.source === 'A' && l.target === 'B')?.used,
    nodes: snap.nodes.map((n) => n.id), links: snap.links.map((l) => `${l.source}>${l.target}`), intents, generatedAt: snap.generated_at,
  }
}

const PATCHED = { balanceOfA: '5', usedAB: '7', availableAB: '3', layoutBalanceOfA: '5', layoutUsedAB: '7', intents: ['accepted-event', 'wake'], generatedAt: '2026-01-01T00:00:01Z' }
const APPLIED_STRUCTURAL = { ...PATCHED, nodes: ['A', 'B', 'C'], links: ['A>B', 'A>C'] }
const APPLIED_PATCH_ONLY = { ...PATCHED, nodes: ['A', 'B'], links: ['A>B'] }
const UNTOUCHED = { balanceOfA: '1', usedAB: '3', availableAB: '7', layoutBalanceOfA: '1', layoutUsedAB: '3', nodes: ['A', 'B'], links: ['A>B'], generatedAt: '2026-01-01T00:00:00Z' }

describe('039 R11 - a topology event of the snapshot\'s own equivalent is applied whatever the whitespace or case of either side', () => {
  it('control: exact match - the values of the patches are on the existing node and line (snapshot and layout), the structural change is in, the loop is woken', () => {
    expect(apply('UAH', 'UAH', 'structural')).toEqual(APPLIED_STRUCTURAL)
    expect(apply('UAH', 'UAH', 'patch-only')).toEqual(APPLIED_PATCH_ONLY)
  })

  it('already green: whitespace or case on the EVENT side, case on the snapshot side (the form the spec first named)', () => {
    for (const [snap, evt] of [['UAH', 'UAH '], ['UAH', ' uah'], ['uah', 'UAH']] as const) {
      expect(apply(snap, evt, 'structural'), `snapshot "${snap}", event "${evt}"`).toEqual(APPLIED_STRUCTURAL)
    }
  })

  red('whitespace on the SNAPSHOT side: snapshot "UAH ", event "UAH" - a structural change and its patches are applied (today: dropped silently)', 'snapshot "UAH " + structural event "UAH": applied state', () => {
    const got = apply('UAH ', 'UAH', 'structural')
    measured('R11 snapshot "UAH " + structural event "UAH"', got)
    expect(got, 'snapshot "UAH " + structural event "UAH": applied state').toEqual(APPLIED_STRUCTURAL)
  })

  red('whitespace on the SNAPSHOT side: snapshot " UAH", event "UAH" - a patch-only event changes the balance and the debt (today: dropped silently)', 'snapshot " UAH" + patch-only event "UAH": applied state', () => {
    const got = apply(' UAH', 'UAH', 'patch-only')
    measured('R11 snapshot " UAH" + patch-only event "UAH"', got)
    expect(got, 'snapshot " UAH" + patch-only event "UAH": applied state').toEqual(APPLIED_PATCH_ONLY)
  })

  it('invariants that must SURVIVE the fix: an event of ANOTHER valid equivalent changes nothing - also when the two codes differ only by a character a sloppy normaliser would strip', () => {
    // `AA_B` and `AAB` are both valid codes (`^[A-Z0-9_]{1,16}$`); trim + upper-case keeps them apart, removing "_" would not.
    for (const [snap, evt] of [['UAH', 'USD'], ['AA_B', 'AAB'], ['AAB', 'AA_B'], ['A1', 'A_1'], ['UAH', 'UAH1']] as const) {
      const got = apply(snap, evt, 'structural')
      const { intents, ...state } = got
      expect(state, `snapshot "${snap}", event "${evt}": nothing is applied`).toEqual(UNTOUCHED)
      expect(intents, `snapshot "${snap}", event "${evt}": the loop is not woken`).toEqual(['accepted-event'])
    }
  })
})
