/**
 * 034 S8, F-034-D6 (reproducer of T3404): a freeze reaches the objects the canvas draws.
 *
 * The canvas reads the LAYOUT copies of nodes and links (`forceLayout` spreads the snapshot objects), and the
 * backend sends a freeze with no `node_patch`/`edge_patch`. So a `topology.changed` that only freezes must itself
 * carry the change into the layout copies - through the same patch applier every other event uses.
 *
 * The applier here is the production one (`createPatchApplier`), with layout copies built the way the layout
 * builds them. Not judged here: the pixels (the renderer takes `viz_color_key` / `viz_alpha_key` from these objects).
 */
import { describe, expect, it } from 'vitest'

import type { AcceptedSimulatorEvent } from '../api/normalizeSimulatorEvent'
import { createPatchApplier } from '../demo/patches'
import type { GraphSnapshot } from '../types'
import type { LayoutLink, LayoutNode } from '../types/layout'
import type { SimulatorAppState } from '../types/simulatorApp'
import { keyEdge } from '../utils/edgeKey'
import { applyAcceptedRealEvent, type RealEventDraft, type RealEventIntent } from './realEventPipeline'

const TS = '2026-10-11T00:00:00Z'

function stand() {
  const snapshot: GraphSnapshot = {
    equivalent: 'UAH',
    generated_at: TS,
    nodes: [
      { id: 'A', type: 'person', status: 'active', viz_color_key: 'person' },
      { id: 'B', type: 'business', status: 'active', viz_color_key: 'business' },
    ],
    links: [
      { source: 'A', target: 'B', trust_limit: '100', used: '10', available: '90', status: 'active', viz_alpha_key: 'active' },
      { source: 'B', target: 'A', trust_limit: '50', used: '0', available: '50', status: 'active', viz_alpha_key: 'active' },
    ],
  }
  // Copies, as `forceLayout` makes them.
  const layoutNodes: LayoutNode[] = snapshot.nodes.map((n) => ({ ...n, __x: 0, __y: 0 }))
  const layoutLinks: LayoutLink[] = snapshot.links.map((l) => ({ ...l, __key: keyEdge(l.source, l.target) }))

  const draft: RealEventDraft = {
    real: {
      runId: 'run_1',
      runStatus: null,
      lastError: '',
      runStats: { attempts: 0, committed: 0, rejected: 0, errors: 0, timeouts: 0, rejectedByCode: {}, errorsByCode: {} },
    },
    state: { loading: false, error: '', sourcePath: '', snapshot, selectedNodeId: null } satisfies SimulatorAppState,
  }

  function apply(payload: Partial<Extract<AcceptedSimulatorEvent, { type: 'topology.changed' }>['payload']>): RealEventIntent[] {
    const event: AcceptedSimulatorEvent = {
      event_id: 'evt_run_1_1',
      ts: TS,
      type: 'topology.changed',
      equivalent: 'UAH',
      payload: { added_nodes: [], removed_nodes: [], added_edges: [], removed_edges: [], ...payload },
    }
    return applyAcceptedRealEvent(event, 'run_1', draft, {
      patchApplier: createPatchApplier({
        getSnapshot: () => draft.state.snapshot,
        getLayoutNodes: () => layoutNodes,
        getLayoutLinks: () => layoutLinks,
        keyEdge,
      }),
      isUserFacingRunError: () => false,
      inc: () => undefined,
    })
  }

  return { snapshot, layoutNodes, layoutLinks, apply }
}

describe('F-034-D6: a freeze sent without patches', () => {
  it('a frozen node is recoloured on the layout copy the canvas draws', () => {
    const s = stand()

    s.apply({ frozen_nodes: ['A'] })

    expect(s.snapshot.nodes[0]?.viz_color_key, 'precondition: the snapshot node is recoloured').toBe('suspended')
    expect(s.layoutNodes[0]?.viz_color_key, 'the canvas copy kept the colour from before the freeze').toBe('suspended')
    expect(s.layoutNodes[0]?.status).toBe('suspended')
  })

  it('a frozen line is muted on the layout copy the canvas draws', () => {
    const s = stand()

    s.apply({ frozen_edges: [{ from_pid: 'A', to_pid: 'B', equivalent_code: 'UAH' }] })

    expect(s.snapshot.links[0]?.viz_alpha_key, 'precondition: the snapshot line is muted').toBe('muted')
    expect(s.layoutLinks[0]?.viz_alpha_key, 'the canvas copy kept the alpha from before the freeze').toBe('muted')
    expect(s.layoutLinks[0]?.status).toBe('frozen')
  })

  // Equivalent TODAY for the caches that also watch `generated_at`: the same event moves it
  // (`realEventPipeline.ts`, end of the structural branch). The revision is bumped for uniformity with patches,
  // not because a stale cache was shown to exist (adversarial pass: the mutation "no bump" changed nothing on screen).
  it('a freeze counts as a data change for whoever caches by the snapshot revision', () => {
    const s = stand()
    const before = s.snapshot.data_revision ?? 0

    s.apply({ frozen_nodes: ['A'], frozen_edges: [{ from_pid: 'A', to_pid: 'B', equivalent_code: 'UAH' }] })

    expect(s.snapshot.data_revision ?? 0, 'the freeze did not bump data_revision').toBeGreaterThan(before)
  })

  it('what is not frozen is left alone, and a freeze of an unknown node or line changes nothing (stays so)', () => {
    const s = stand()

    const intents = s.apply({
      frozen_nodes: ['A', 'NOBODY'],
      frozen_edges: [
        { from_pid: 'A', to_pid: 'B', equivalent_code: 'UAH' },
        { from_pid: 'A', to_pid: 'NOBODY', equivalent_code: 'UAH' },
      ],
    })

    expect(s.snapshot.nodes[1]).toMatchObject({ id: 'B', status: 'active', viz_color_key: 'business' })
    expect(s.layoutNodes[1]).toMatchObject({ id: 'B', status: 'active', viz_color_key: 'business' })
    expect(s.snapshot.links[1]).toMatchObject({ source: 'B', target: 'A', status: 'active', viz_alpha_key: 'active' })
    expect(s.layoutLinks[1]).toMatchObject({ source: 'B', target: 'A', status: 'active', viz_alpha_key: 'active' })
    // The debt of the frozen line is not touched by the freeze (F-034-D1 counts it).
    expect(s.snapshot.links[0]?.used).toBe('10')
    expect(s.layoutLinks[0]?.used).toBe('10')
    expect(s.snapshot.nodes).toHaveLength(2)
    expect(s.snapshot.links).toHaveLength(2)
    expect(intents.map((i) => i.type)).toEqual(['accepted-event', 'wake'])
  })
})
