import type { ComputedRef, Ref } from 'vue'
import { computed } from 'vue'
import type { GraphNode } from '../types'
import type { LayoutLinkLike as BaseLayoutLinkLike, LayoutNodeWithId as BaseLayoutNodeWithId } from '../types/layout'
import { clamp } from '../utils/math'

type LayoutNodeLike = BaseLayoutNodeWithId
type LayoutLinkLike = BaseLayoutLinkLike

type UseNodeCardDeps = {
  hostEl: Ref<HTMLElement | null>
  selectedNodeId: Ref<string | null>
  getNodeById: (id: string | null) => GraphNode | null
  getLayoutNodeById: (id: string) => LayoutNodeLike | null
  getNodeScreenSize: (node: GraphNode) => { w: number; h: number }
  worldToScreen: (x: number, y: number) => { x: number; y: number }
  // New: get incident edges for positioning
  getIncidentEdges?: (nodeId: string) => LayoutLinkLike[]
  getLayoutNodes?: () => LayoutNodeLike[]
}

type UseNodeCardReturn = {
  selectedNode: ComputedRef<GraphNode | null>
  nodeCardStyle: () => { left?: string; top?: string; display?: string }
}

export function useNodeCard(deps: UseNodeCardDeps): UseNodeCardReturn {
  const selectedNode = computed(() => deps.getNodeById(deps.selectedNodeId.value))

  /**
   * Determine edge direction quadrant relative to node center.
   * Returns: 'right' | 'left' | 'top' | 'bottom'
   */
  function getEdgeDirection(
    nodeCenterScreen: { x: number; y: number },
    neighborScreen: { x: number; y: number },
  ): 'right' | 'left' | 'top' | 'bottom' {
    const dx = neighborScreen.x - nodeCenterScreen.x
    const dy = neighborScreen.y - nodeCenterScreen.y

    // Determine primary direction based on the larger component
    if (Math.abs(dx) >= Math.abs(dy)) {
      return dx >= 0 ? 'right' : 'left'
    } else {
      return dy >= 0 ? 'bottom' : 'top'
    }
  }

  /**
   * Count how many edges go in each direction from the node.
   */
  function countEdgeDirections(
    nodeId: string,
    nodeScreen: { x: number; y: number },
  ): { right: number; left: number; top: number; bottom: number } {
    const counts = { right: 0, left: 0, top: 0, bottom: 0 }

    if (!deps.getIncidentEdges || !deps.getLayoutNodes) {
      return counts
    }

    const edges = deps.getIncidentEdges(nodeId)
    const nodes = deps.getLayoutNodes()
    const nodeMap = new Map(nodes.map((n) => [n.id, n]))

    for (const edge of edges) {
      const neighborId = edge.source === nodeId ? edge.target : edge.source
      const neighbor = nodeMap.get(neighborId)
      if (!neighbor) continue

      const neighborScreen = deps.worldToScreen(neighbor.__x, neighbor.__y)
      const dir = getEdgeDirection(nodeScreen, neighborScreen)
      counts[dir]++
    }

    return counts
  }

  function nodeCardStyle() {
    const host = deps.hostEl.value
    if (!host || !selectedNode.value) return { display: 'none' }

    const node = deps.getLayoutNodeById(selectedNode.value.id)
    if (!node) return { display: 'none' }

    const rect = host.getBoundingClientRect()
    const p = deps.worldToScreen(node.__x, node.__y)

    const pad = 12
    const cardW = 260
    const cardH = 146
    const gap = 18

    const { w: nw, h: nh } = deps.getNodeScreenSize(selectedNode.value)
    const nodeW = Math.max(10, nw) + 18
    const nodeH = Math.max(10, nh) + 18

    const nodeRect = {
      x: p.x - nodeW / 2,
      y: p.y - nodeH / 2,
      w: nodeW,
      h: nodeH,
    }

    const clampCard = (x: number, y: number) => ({
      x: clamp(x, pad, rect.width - pad - cardW),
      y: clamp(y, pad, rect.height - pad - cardH),
    })

    const intersects = (a: { x: number; y: number; w: number; h: number }, b: { x: number; y: number; w: number; h: number }) =>
      !(a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y)

    // Count edges in each direction
    const edgeCounts = countEdgeDirections(selectedNode.value.id, p)

    // Create candidates with their direction names and edge counts
    const candidates: Array<{
      pos: { x: number; y: number }
      direction: 'right' | 'left' | 'bottom' | 'top'
      edgeCount: number
    }> = [
      {
        pos: { x: p.x + nodeW / 2 + gap, y: p.y - cardH / 2 },
        direction: 'right',
        edgeCount: edgeCounts.right,
      },
      {
        pos: { x: p.x - nodeW / 2 - gap - cardW, y: p.y - cardH / 2 },
        direction: 'left',
        edgeCount: edgeCounts.left,
      },
      {
        pos: { x: p.x - cardW / 2, y: p.y + nodeH / 2 + gap },
        direction: 'bottom',
        edgeCount: edgeCounts.bottom,
      },
      {
        pos: { x: p.x - cardW / 2, y: p.y - nodeH / 2 - gap - cardH },
        direction: 'top',
        edgeCount: edgeCounts.top,
      },
    ]

    // Sort candidates by edge count (prefer positions with fewer edges)
    candidates.sort((a, b) => a.edgeCount - b.edgeCount)

    // Try candidates in order of preference (least edges first)
    for (const c of candidates) {
      const t = clampCard(c.pos.x, c.pos.y)
      const cardRect = { x: t.x, y: t.y, w: cardW, h: cardH }
      if (!intersects(cardRect, nodeRect)) {
        return { left: `${t.x}px`, top: `${t.y}px` }
      }
    }

    // Fallback: use the position with least edges, even if it intersects with node
    const best = candidates[0]!
    const t = clampCard(best.pos.x, best.pos.y)
    return { left: `${t.x}px`, top: `${t.y}px` }
  }

  return { selectedNode, nodeCardStyle }
}
