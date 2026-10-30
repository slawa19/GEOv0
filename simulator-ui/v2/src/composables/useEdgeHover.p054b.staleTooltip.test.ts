/**
 * 054-B-7: the hovered line's figures were a copy taken on `pointermove` (`useEdgeHover.ts`): a payment's patch under a
 * still pointer changed the line, the tooltip kept the old `used` / `available` until the pointer moved. The stand is the
 * real path of a patch: the real patch applier (`demo/patches.ts`) mutates the snapshot's line and its RAW layout copy in
 * place and bumps `data_revision` of the reactive snapshot (as `useSimulatorApp.ts` wires it); the real `useOverlayState`
 * owns the hovered line; `getLinkByKey` reads the layout copy, as in the app.
 */
import { markRaw, nextTick, reactive } from 'vue'
import { describe, expect, it } from 'vitest'

import { createPatchApplier } from '../demo/patches'
import type { GraphSnapshot } from '../types'
import type { LayoutLink } from '../types/layout'
import { keyEdge } from '../utils/edgeKey'
import { useEdgeHover } from './useEdgeHover'
import { useOverlayState } from './useOverlayState'
import type { EdgeSeg } from './usePicking'

function stand() {
  const state = reactive<{ snapshot: GraphSnapshot }>({
    snapshot: {
      equivalent: 'UAH', generated_at: '2026-01-01T00:00:00Z', data_revision: 0,
      nodes: [{ id: 'A', name: 'A' }, { id: 'B', name: 'B' }],
      links: [{ source: 'A', target: 'B', trust_limit: '100.00', used: '10.00', available: '90.00', status: 'active' }],
    } as GraphSnapshot,
  })
  const layout = { links: markRaw([{ ...state.snapshot.links[0]!, __key: keyEdge('A', 'B') } as unknown as LayoutLink]) }
  const applier = createPatchApplier({ getSnapshot: () => state.snapshot, getLayoutNodes: () => [], getLayoutLinks: () => layout.links, keyEdge })
  const overlays = useOverlayState({ getLayoutNodeById: () => undefined, sizeForNode: () => ({ w: 0, h: 0 }), getCameraZoom: () => 1, resetFxState: () => {} })
  const seg: EdgeSeg = { key: keyEdge('A', 'B'), fromId: 'A', toId: 'B', ax: 0, ay: 0, bx: 10, by: 0 }
  let pointerOn: EdgeSeg | null = seg
  const hover = useEdgeHover({
    hoveredEdge: overlays.hoveredEdge,
    clearHoveredEdge: overlays.clearHoveredEdge,
    isWebDriver: () => false,
    getSelectedNodeId: () => 'A',
    hasSelectedIncidentEdges: () => true,
    pickNodeAt: () => null,
    pickEdgeAt: () => pointerOn,
    getLinkByKey: (k) => layout.links.find((l) => l.__key === k),
    formatEdgeAmountText: (l) => `used ${String(l?.used ?? '—')}`,
    getEquivalent: () => state.snapshot.equivalent,
    getDataRevision: () => state.snapshot.data_revision,
    clientToScreen: (x, y) => ({ x, y }),
    screenToWorld: (x, y) => ({ x, y }),
    worldToScreen: (x, y) => ({ x, y }),
  })
  const point = () => hover.onPointerMove({ clientX: 5, clientY: 0 } as PointerEvent, { panActive: false })
  return { applier, overlays, point, layout, leave: () => { pointerOn = null } }
}

describe('054-B-7: the edge tooltip under a still pointer', () => {
  it('shows the figures of the line after a payment patch, not the copy taken when the pointer arrived', async () => {
    const { applier, overlays, point } = stand()
    point()
    expect(overlays.hoveredEdge.used, 'stand: the figures are taken on hover').toBe('10.00')

    applier.applyEdgePatches([{ source: 'A', target: 'B', used: '40.00', available: '60.00' }])
    await nextTick()
    expect(overlays.hoveredEdge.key, 'the tooltip stays').toBe(keyEdge('A', 'B'))
    expect(overlays.hoveredEdge.used).toBe('40.00')
    expect(overlays.hoveredEdge.available).toBe('60.00')
    expect(overlays.hoveredEdge.trustLimit).toBe('100.00')
    expect(overlays.hoveredEdge.amountText).toBe('used 40.00')
    expect(overlays.hoveredEdge.equivalent, 'the unit stays with its figures').toBe('UAH')
  })

  it('counter-check: no hovered line - a patch shows nothing (no tooltip is opened by data)', async () => {
    const { applier, overlays, point, leave } = stand()
    point()
    leave(); point()
    expect(overlays.hoveredEdge.key, 'stand: the pointer left the line').toBeNull()
    applier.applyEdgePatches([{ source: 'A', target: 'B', used: '40.00', available: '60.00' }])
    await nextTick()
    expect(overlays.hoveredEdge.key).toBeNull()
    expect(overlays.hoveredEdge.used).toBeNull()
  })

  it('a hovered line gone from the layout is not shown with its old figures', async () => {
    const { applier, overlays, point, layout } = stand()
    point()
    layout.links.splice(0, layout.links.length)
    applier.applyEdgePatches([{ source: 'C', target: 'D', used: '1.00' }])   // another line's patch bumps the revision
    await nextTick()
    expect(overlays.hoveredEdge.key).toBeNull()
  })
})
