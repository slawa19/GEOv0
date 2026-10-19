/**
 * 039 slice B, PR 1 - roles under live events (the third class-1 risk of the slice's design: the data of an old
 * snapshot under the title of a new equivalent, or another equivalent's patch on the shown one).
 *
 * The pipeline and the patch applier are the PRODUCTION ones; the layout copies are built as `forceLayout` builds
 * them. What is observed is what the canvas and the legend read: the `net_sign` of the layout copies (the canvas
 * draws these), the fill the node painter's resolver gives them, and the legend's entries for the snapshot.
 *
 *  - a `net_sign` patch changes the role WITHOUT a relayout: same node objects, same positions;
 *  - an incomplete patch keeps the previous field; an explicit `null` gives "unknown" (not zero, not the old role);
 *  - an event of ANOTHER equivalent than the shown snapshot's changes nothing on screen, on every path that carries
 *    node patches (payment, clearing, topology) - so a late event after a switch cannot paint old roles under the
 *    new title, nor the reverse. The legend's title and its entries are derived from ONE snapshot object.
 *
 * NOT seen here: pixels; the order of real network frames (the stand's `delivery.qa.ts`); the equivalent switch of
 * the scene loader itself (`useSceneState`), which replaces the snapshot as a whole.
 */
import { describe, expect, it } from 'vitest'

import type { AcceptedSimulatorEvent } from '../api/normalizeSimulatorEvent'
import { canonicalEquivalent } from '../config/equivalents'
import { createPatchApplier } from '../demo/patches'
import { fillForNode } from '../render/nodeFill'
import type { GraphSnapshot, NodePatch } from '../types'
import type { LayoutLink, LayoutNode } from '../types/layout'
import type { SimulatorAppState } from '../types/simulatorApp'
import { keyEdge } from '../utils/edgeKey'
import { classifyNode, createVizMapping, DATA_TOKEN, legendEntriesFor, type DataColorName, type VizMapping } from '../vizMapping'
import { applyAcceptedRealEvent, type RealEventDraft } from './realEventPipeline'

const TS = '2026-10-12T00:00:00Z'
const PALETTE = { debtor: '#010101', creditor: '#020202', neutral: '#030303', frozen: '#040404', hub: '#050505' } as const
const MAPPING: VizMapping = { ...createVizMapping(), node: { palette: PALETTE } }
const tokenOf = (fill: string) => DATA_TOKEN[(Object.keys(PALETTE) as DataColorName[]).find((k) => PALETTE[k] === fill)!]

function stand(equivalent = 'EUR') {
  const snapshot: GraphSnapshot = {
    equivalent,
    generated_at: TS,
    nodes: [
      { id: 'A', type: 'person', status: 'active', net_sign: 1, net_balance_atoms: '500', viz_color_key: 'person', viz_shape_key: 'circle' },
      { id: 'B', type: 'person', status: 'active', net_sign: -1, net_balance_atoms: '500', viz_color_key: 'debt-4', viz_shape_key: 'circle' },
    ],
    links: [{ source: 'A', target: 'B', trust_limit: '100', used: '5', available: '95', status: 'active' }],
  }
  const layoutNodes: LayoutNode[] = snapshot.nodes.map((n, i) => ({ ...n, __x: 100 + i * 50, __y: 200 }))
  const layoutLinks: LayoutLink[] = snapshot.links.map((l) => ({ ...l, __key: keyEdge(l.source, l.target) }))
  const identity = [...layoutNodes]
  const draft: RealEventDraft = {
    real: { runId: 'run_1', runStatus: null, lastError: '', runStats: { attempts: 0, committed: 0, rejected: 0, errors: 0, timeouts: 0, rejectedByCode: {}, errorsByCode: {} } },
    state: { loading: false, error: '', sourcePath: '', snapshot, selectedNodeId: null } satisfies SimulatorAppState,
  }
  const deps = {
    patchApplier: createPatchApplier({ getSnapshot: () => draft.state.snapshot, getLayoutNodes: () => layoutNodes, getLayoutLinks: () => layoutLinks, keyEdge }),
    isUserFacingRunError: () => false,
    inc: () => undefined,
  }
  let seq = 0
  const send = (event: Record<string, unknown>) => applyAcceptedRealEvent({ event_id: `evt_run_1_${++seq}`, ts: TS, ...event } as AcceptedSimulatorEvent, 'run_1', draft, deps)

  /** What the canvas draws (layout copies) and what the legend says (the snapshot) - read after an event. */
  const seen = () => ({
    canvas: layoutNodes.map((n) => `${n.id}:${tokenOf(fillForNode(n, MAPPING))}:${classifyNode(n).sign}`),
    legendTitle: `Node colour — balance · ${canonicalEquivalent(snapshot.equivalent)}`,
    legend: legendEntriesFor(snapshot.nodes),
    sameObjects: layoutNodes.every((n, i) => n === identity[i]),
    positions: layoutNodes.map((n) => [n.__x, n.__y]),
  })

  const payment = (eq: string, node_patch: NodePatch[]) => send({ type: 'tx.updated', equivalent: eq, from: 'A', to: 'B', amount: '1', ttl_ms: 10, edges: [{ from: 'A', to: 'B' }], node_patch })
  const clearing = (eq: string, node_patch: NodePatch[]) => send({ type: 'clearing.done', equivalent: eq, plan_id: 'p1', cleared_cycles: 1, cleared_amount: '1', node_patch })
  const topology = (eq: string, node_patch: NodePatch[]) => send({ type: 'topology.changed', equivalent: eq, payload: { added_nodes: [], removed_nodes: [], added_edges: [], removed_edges: [], node_patch } })

  return { snapshot, layoutNodes, seen, payment, clearing, topology }
}

const BEFORE = { canvas: ['A:--ds-data-creditor:+', 'B:--ds-data-debtor:−'], legend: ['debtor', 'creditor', 'person'] }

describe('039 B1 - a patch of net_sign changes the picture without a relayout', () => {
  it('counter-check: the stand starts with a creditor and a debtor', () => {
    const s = stand()
    expect(s.seen()).toMatchObject({ ...BEFORE, legendTitle: 'Node colour — balance · EUR' })
  })

  for (const path of ['payment', 'clearing', 'topology'] as const) {
    it(`${path}: the roles follow the patch; the node objects and their positions are the same`, () => {
      const s = stand()
      const positions = s.seen().positions
      s[path]('EUR', [{ id: 'A', net_sign: -1 }, { id: 'B', net_sign: 1 }])
      const after = s.seen()
      expect(after.canvas).toEqual(['A:--ds-data-debtor:−', 'B:--ds-data-creditor:+'])
      expect(after.sameObjects, 'no relayout: the layout copies are the same objects').toBe(true)
      expect(after.positions).toEqual(positions)
    })
  }

  it('an incomplete patch keeps the previous sign: a patch of the balance only does not touch the role', () => {
    const s = stand()
    s.payment('EUR', [{ id: 'A', net_balance_atoms: '700' }, { id: 'B', viz_color_key: 'debt-8', viz_size: { w: 30, h: 30 } }])
    expect(s.seen().canvas).toEqual(BEFORE.canvas)
    expect(s.layoutNodes[0]!.net_balance_atoms, 'counter-check: the patch was applied').toBe('700')
    expect(s.layoutNodes[1]!.viz_size, 'counter-check: the size still follows the wire').toEqual({ w: 30, h: 30 })
  })

  it('an explicit null gives UNKNOWN - not zero and not the role from before', () => {
    const s = stand()
    s.payment('EUR', [{ id: 'A', net_sign: null }])
    const after = s.seen()
    expect(after.canvas).toEqual(['A:--ds-data-neutral:?', 'B:--ds-data-debtor:−'])
    expect(after.legend).toEqual(['debtor', 'unknown', 'person'])
    expect(after.legend, 'unknown is not explained as "even"').not.toContain('neutral')
  })

  it('zero is zero: the legend then says "even", not "unknown"', () => {
    const s = stand()
    s.payment('EUR', [{ id: 'A', net_sign: 0 }])
    expect(s.seen().canvas[0]).toBe('A:--ds-data-neutral:0')
    expect(s.seen().legend).toEqual(['debtor', 'neutral', 'person'])
  })
})

describe('039 B1 - a late event of another equivalent paints nothing under the shown one', () => {
  for (const path of ['payment', 'clearing', 'topology'] as const) {
    it(`${path}: an event of UAH does not change the roles of a shown EUR snapshot; the same event in EUR does`, () => {
      const s = stand('EUR')
      const flip: NodePatch[] = [{ id: 'A', net_sign: -1 }, { id: 'B', net_sign: 1 }]
      s[path]('UAH', flip)
      expect(s.seen(), 'the old equivalent\'s event arrived after the switch').toMatchObject({ ...BEFORE, legendTitle: 'Node colour — balance · EUR' })
      // Anti-vacuum: the very same patch is applied when it is of the shown equivalent (written with a space and in lower case).
      s[path](' eur', flip)
      expect(s.seen().canvas).toEqual(['A:--ds-data-debtor:−', 'B:--ds-data-creditor:+'])
    })
  }

  it('the title and the roles come from one snapshot object: replacing the snapshot moves both together', () => {
    const eur = stand('EUR')
    const uah = stand('UAH')
    uah.payment('UAH', [{ id: 'A', net_sign: 0 }])
    expect(eur.seen()).toMatchObject({ legendTitle: 'Node colour — balance · EUR', legend: ['debtor', 'creditor', 'person'] })
    expect(uah.seen()).toMatchObject({ legendTitle: 'Node colour — balance · UAH', legend: ['debtor', 'neutral', 'person'] })
  })
})
