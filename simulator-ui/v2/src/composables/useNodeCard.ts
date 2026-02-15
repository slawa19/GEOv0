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

    // Nodes have a sizeable glow/halo (bloom + rim). `getNodeScreenSize()` returns the
    // core geometry size, so we add a halo pad to keep the card visually separated.
    const haloPad = 20

    const { w: nw, h: nh } = deps.getNodeScreenSize(selectedNode.value)
    const nodeW = Math.max(10, nw) + haloPad * 2
    const nodeH = Math.max(10, nh) + haloPad * 2

    const nodeRect = {
      x: p.x - nodeW / 2,
      y: p.y - nodeH / 2,
      w: nodeW,
      h: nodeH,
    }

    // Require a minimum gap from the node on any side.
    // We approximate this by expanding the node rect and ensuring the card does not intersect it.
    const nodeRectWithGap = {
      x: nodeRect.x - gap,
      y: nodeRect.y - gap,
      w: nodeRect.w + gap * 2,
      h: nodeRect.h + gap * 2,
    }

    const clampCard = (x: number, y: number) => ({
      x: clamp(x, pad, rect.width - pad - cardW),
      y: clamp(y, pad, rect.height - pad - cardH),
    })

    const intersects = (
      a: { x: number; y: number; w: number; h: number },
      b: { x: number; y: number; w: number; h: number },
    ) => !(a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y)

    // Count edges in each direction
    const edgeCounts = countEdgeDirections(selectedNode.value.id, p)

    // Create candidates with their direction names and edge counts.
    // NOTE: `pos` is the desired position; it may be clamped later.
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

    const evalCandidate = (c: (typeof candidates)[number]) => {
      const t = clampCard(c.pos.x, c.pos.y)
      const cardRect = { x: t.x, y: t.y, w: cardW, h: cardH }

      const primaryAxisClamped =
        c.direction === 'left' || c.direction === 'right' ? t.x !== c.pos.x : t.y !== c.pos.y

      const violatesMinGap = intersects(cardRect, nodeRectWithGap)

      return { c, t, cardRect, primaryAxisClamped, violatesMinGap }
    }

    const evaluated = candidates.map(evalCandidate)

    const pickFirst = (pred: (e: (typeof evaluated)[number]) => boolean) => {
      const hit = evaluated.find(pred)
      if (!hit) return null
      return { left: `${hit.t.x}px`, top: `${hit.t.y}px` }
    }

    // Pass 1 (ideal): keep the intended gap AND do not clamp on the primary axis.
    const p1 = pickFirst((e) => !e.primaryAxisClamped && !e.violatesMinGap)
    if (p1) return p1

    // Pass 2: keep the intended gap (even if we must clamp). This avoids the "stuck to node" look.
    const p2 = pickFirst((e) => !e.violatesMinGap)
    if (p2) return p2

    // Pass 3: do not clamp on the primary axis (even if we cannot keep the full min gap).
    const p3 = pickFirst((e) => !e.primaryAxisClamped)
    if (p3) return p3

    // Fallback: use the best edge-count candidate (legacy behavior).
    const best = evaluated[0]!
    return { left: `${best.t.x}px`, top: `${best.t.y}px` }
  }

  return { selectedNode, nodeCardStyle }
}
