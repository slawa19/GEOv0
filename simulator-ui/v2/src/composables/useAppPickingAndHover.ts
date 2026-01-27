import type { Ref } from 'vue'

import type { LayoutLink, LayoutNode } from '../types/layout'

import { clamp } from '../utils/math'

import { useEdgeHover } from './useEdgeHover'
import { useEdgeTooltip } from './useEdgeTooltip'
import { usePicking } from './usePicking'

type HoveredEdgeLike = ReturnType<typeof import('./useOverlayState').useOverlayState>['hoveredEdge']

export function useAppPickingAndHover(opts: {
  hostEl: Ref<HTMLDivElement | null>
  canvasEl: Ref<HTMLCanvasElement | null>

  // layout + camera
  getLayoutNodes: () => LayoutNode[]
  getLayoutLinks: () => LayoutLink[]
  getCameraZoom: () => number
  clientToScreen: (clientX: number, clientY: number) => { x: number; y: number }
  screenToWorld: (screenX: number, screenY: number) => { x: number; y: number }
  worldToScreen: (worldX: number, worldY: number) => { x: number; y: number }

  sizeForNode: (n: LayoutNode) => { w: number; h: number }

  // edge hover state (owned by fx overlays)
  hoveredEdge: HoveredEdgeLike
  clearHoveredEdge: () => void

  // selection + edges
  isWebDriver: () => boolean
  getSelectedNodeId: () => string | null
  hasSelectedIncidentEdges: () => boolean
  getLinkByKey: (key: string) => LayoutLink | undefined

  // tooltip unit
  getUnit: () => string
}) {
  const edgeTooltip = useEdgeTooltip({
    hostEl: opts.hostEl,
    hoveredEdge: opts.hoveredEdge,
    clamp,
    getUnit: opts.getUnit,
  })

  const picking = usePicking({
    getLayoutNodes: opts.getLayoutNodes,
    getLayoutLinks: opts.getLayoutLinks,
    getCameraZoom: opts.getCameraZoom,
    sizeForNode: opts.sizeForNode,
    clientToScreen: opts.clientToScreen,
    screenToWorld: opts.screenToWorld,
    isReady: () => !!opts.hostEl.value && !!opts.canvasEl.value,
  })

  const edgeHover = useEdgeHover({
    hoveredEdge: opts.hoveredEdge,
    clearHoveredEdge: opts.clearHoveredEdge,
    isWebDriver: opts.isWebDriver,
    getSelectedNodeId: opts.getSelectedNodeId,
    hasSelectedIncidentEdges: opts.hasSelectedIncidentEdges,
    pickNodeAt: picking.pickNodeAt,
    pickEdgeAt: picking.pickEdgeAt,
    getLinkByKey: opts.getLinkByKey,
    formatEdgeAmountText: edgeTooltip.formatEdgeAmountText,
    clientToScreen: opts.clientToScreen,
    screenToWorld: opts.screenToWorld,
    worldToScreen: opts.worldToScreen,
  })

  return {
    pickNodeAt: picking.pickNodeAt,
    pickEdgeAt: picking.pickEdgeAt,
    edgeTooltipStyle: edgeTooltip.edgeTooltipStyle,
    edgeHover,
  }
}
