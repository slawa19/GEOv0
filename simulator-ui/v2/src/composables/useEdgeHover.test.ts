import { describe, expect, it } from 'vitest'
import type { GraphLink } from '../types'
import type { HoveredEdgeState } from './useOverlayState'
import type { EdgeSeg } from './usePicking'
import { useEdgeHover } from './useEdgeHover'

function makeHoveredEdge(): HoveredEdgeState {
  return { key: null, fromId: '', toId: '', amountText: '', screenX: 0, screenY: 0 }
}

describe('useEdgeHover', () => {
  it('sets hoveredEdge when hovering a selected incident edge', () => {
    const hoveredEdge = makeHoveredEdge()

    const seg: EdgeSeg = { key: 'A→B', fromId: 'A', toId: 'B', ax: 0, ay: 0, bx: 10, by: 0 }
    const link: GraphLink = { source: 'A', target: 'B', used: '1', trust_limit: '2', available: '1' }

    const h = useEdgeHover({
      hoveredEdge,
      clearHoveredEdge: () => {
        hoveredEdge.key = null
      },
      isWebDriver: () => false,
      getSelectedNodeId: () => 'A',
      hasSelectedIncidentEdges: () => true,
      pickNodeAt: () => null,
      pickEdgeAt: () => seg,
      getLinkByKey: () => link,
      formatEdgeAmountText: () => 'amt',
      clientToScreen: (x, y) => ({ x, y }),
      screenToWorld: (x, y) => ({ x, y }),
      worldToScreen: (x, y) => ({ x, y }),
    })

    h.onPointerMove({ clientX: 5, clientY: 6 } as PointerEvent, { panActive: false })

    expect(hoveredEdge).toMatchObject({ key: 'A→B', fromId: 'A', toId: 'B', amountText: 'amt' })
  })

  it('clears hoveredEdge when pointer is over a node', () => {
    const hoveredEdge = makeHoveredEdge()
    hoveredEdge.key = 'prev'

    const h = useEdgeHover({
      hoveredEdge,
      clearHoveredEdge: () => {
        hoveredEdge.key = null
      },
      isWebDriver: () => false,
      getSelectedNodeId: () => 'A',
      hasSelectedIncidentEdges: () => true,
      pickNodeAt: () => ({ id: 'A' }),
      pickEdgeAt: () => null,
      getLinkByKey: () => null,
      formatEdgeAmountText: () => 'amt',
      clientToScreen: (x, y) => ({ x, y }),
      screenToWorld: (x, y) => ({ x, y }),
      worldToScreen: (x, y) => ({ x, y }),
    })

    h.onPointerMove({ clientX: 0, clientY: 0 } as PointerEvent, { panActive: false })
    expect(hoveredEdge.key).toBeNull()
  })

  it('does not clear hoveredEdge while panning', () => {
    const hoveredEdge = makeHoveredEdge()
    hoveredEdge.key = 'keep'

    const h = useEdgeHover({
      hoveredEdge,
      clearHoveredEdge: () => {
        hoveredEdge.key = null
      },
      isWebDriver: () => false,
      getSelectedNodeId: () => null,
      hasSelectedIncidentEdges: () => false,
      pickNodeAt: () => null,
      pickEdgeAt: () => null,
      getLinkByKey: () => null,
      formatEdgeAmountText: () => 'amt',
      clientToScreen: (x, y) => ({ x, y }),
      screenToWorld: (x, y) => ({ x, y }),
      worldToScreen: (x, y) => ({ x, y }),
    })

    h.onPointerMove({ clientX: 0, clientY: 0 } as PointerEvent, { panActive: true })
    expect(hoveredEdge.key).toBe('keep')
  })
})
