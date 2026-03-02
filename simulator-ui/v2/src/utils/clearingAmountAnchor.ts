export type ClearingEdge = { from: string; to: string }

export type WorldPoint = { x: number; y: number }

/**
 * Computes a world-space anchor for the clearing amount label.
 *
 * Spec: centroid of edge midpoints (NOT centroid of endpoint nodes).
 *
 * - For each edge: if both endpoints have world coords, take the midpoint.
 * - Anchor is the centroid (average) of all collected midpoints.
 * - If no edge midpoints can be computed (layout not ready), returns null.
 */
export function computeClearingAmountAnchorFromEdgeMidpoints(
  edges: ClearingEdge[],
  getNodeWorldPos: (nodeId: string) => WorldPoint | null | undefined,
): WorldPoint | null {
  if (!Array.isArray(edges) || edges.length === 0) return null

  let sumX = 0
  let sumY = 0
  let n = 0

  for (const e of edges) {
    const a = getNodeWorldPos(e.from)
    const b = getNodeWorldPos(e.to)
    if (!a || !b) continue

    const mx = (a.x + b.x) / 2
    const my = (a.y + b.y) / 2
    sumX += mx
    sumY += my
    n++
  }

  if (n === 0) return null
  return { x: sumX / n, y: sumY / n }
}

