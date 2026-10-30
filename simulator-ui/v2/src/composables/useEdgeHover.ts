import { watch } from 'vue'

import type { GraphLink } from '../types'
import type { Point } from '../types/layout'
import type { HoveredEdgeState } from './useOverlayState'
import { closestPointOnSegment, type EdgeSeg } from './usePicking'

type UseEdgeHoverDeps = {
  hoveredEdge: HoveredEdgeState
  clearHoveredEdge: () => void

  isWebDriver: () => boolean
  getSelectedNodeId: () => string | null
  hasSelectedIncidentEdges: () => boolean

  pickNodeAt: (clientX: number, clientY: number) => { id: string } | null
  pickEdgeAt: (clientX: number, clientY: number) => EdgeSeg | null

  getLinkByKey: (key: string) => GraphLink | null | undefined
  formatEdgeAmountText: (link: GraphLink | null | undefined) => string
  /** 043 S1: the equivalent of the scene the link's figures belong to - captured with them (review finding 1). */
  getEquivalent?: () => string | null
  /**
   * 054-B-7: the data revision of the shown snapshot (`data_revision`, bumped by every patch, `demo/patches.ts`). A patch
   * mutates the line in place under a still pointer; on a new revision the hovered line's figures are read again by its key.
   */
  getDataRevision?: () => unknown

  clientToScreen: (clientX: number, clientY: number) => Point
  screenToWorld: (screenX: number, screenY: number) => Point
  worldToScreen: (worldX: number, worldY: number) => Point
}

export function useEdgeHover(deps: UseEdgeHoverDeps) {
  /** The figures of `link` and their equivalent, written together (043 S1: figures and unit cannot come apart). */
  function writeFigures(link: GraphLink | null | undefined) {
    deps.hoveredEdge.amountText = deps.formatEdgeAmountText(link)
    // BUG-2: populate trustline metadata for Interact Mode EdgeTooltip
    deps.hoveredEdge.trustLimit = link?.trust_limit ?? null
    deps.hoveredEdge.used = link?.used ?? null
    deps.hoveredEdge.available = link?.available ?? null
    deps.hoveredEdge.edgeStatus = link?.status ?? null
    deps.hoveredEdge.equivalent = deps.getEquivalent?.() ?? null
  }

  // 054-B-7: the figures were a copy taken on pointermove - a payment under a still pointer left the old ones on screen.
  if (deps.getDataRevision) {
    watch(deps.getDataRevision, () => {
      const key = deps.hoveredEdge.key
      if (!key) return
      const link = deps.getLinkByKey(key)
      // The line is gone: its old figures are not shown.
      if (!link) {
        deps.clearHoveredEdge()
        return
      }
      writeFigures(link)
    })
  }

  function onPointerMove(ev: PointerEvent, opts: { panActive: boolean }) {
    // Hover only when a node is selected, so it's obvious which edges are relevant.
    // Also keep WebDriver runs stable (no transient tooltip).
    const selectedId = deps.getSelectedNodeId()
    const hoverEnabled = !opts.panActive && !deps.isWebDriver() && !!selectedId && deps.hasSelectedIncidentEdges()

    if (hoverEnabled) {
      // Do not show edge tooltips when hovering a node.
      const nodeHit = deps.pickNodeAt(ev.clientX, ev.clientY)
      if (nodeHit) {
        deps.clearHoveredEdge()
        return
      }

      const seg = deps.pickEdgeAt(ev.clientX, ev.clientY)
      if (seg && selectedId && (seg.fromId === selectedId || seg.toId === selectedId)) {
        const link = deps.getLinkByKey(seg.key)

        const s = deps.clientToScreen(ev.clientX, ev.clientY)
        const p = deps.screenToWorld(s.x, s.y)
        const cp = closestPointOnSegment(p.x, p.y, seg.ax, seg.ay, seg.bx, seg.by)
        const sp = deps.worldToScreen(cp.x, cp.y)

        deps.hoveredEdge.key = seg.key
        deps.hoveredEdge.fromId = seg.fromId
        deps.hoveredEdge.toId = seg.toId
        deps.hoveredEdge.screenX = sp.x
        deps.hoveredEdge.screenY = sp.y
        writeFigures(link)
      } else {
        deps.clearHoveredEdge()
      }

      return
    }

    if (!opts.panActive) deps.clearHoveredEdge()
  }

  return { onPointerMove }
}
