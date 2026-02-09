export type DirectedEdge = { from: string; to: string }

function normId(v: unknown): string {
  return String(v ?? '').trim()
}

function cleanEdges(edges: unknown): DirectedEdge[] {
  if (!Array.isArray(edges)) return []
  const out: DirectedEdge[] = []
  for (const e of edges) {
    const from = normId((e as any)?.from)
    const to = normId((e as any)?.to)
    if (!from || !to) continue
    out.push({ from, to })
  }
  return out
}

function swapEach(edges: DirectedEdge[]): DirectedEdge[] {
  return edges.map((e) => ({ from: e.to, to: e.from }))
}

function reverseOrder(edges: DirectedEdge[]): DirectedEdge[] {
  return [...edges].reverse()
}

function reverseAndSwap(edges: DirectedEdge[]): DirectedEdge[] {
  return reverseOrder(swapEach(edges))
}

function endpointsMatch(edges: DirectedEdge[], from: string, to: string): boolean {
  if (edges.length === 0) return false
  return edges[0]!.from === from && edges[edges.length - 1]!.to === to
}

/**
 * Resolves a consistent transaction direction for visualization.
 *
 * Goal: make every caller use the same (from,to) and an edge list oriented
 * from -> to whenever possible.
 */
export function resolveTxDirection(input: {
  from?: unknown
  to?: unknown
  edges?: unknown
}): { from: string; to: string; edges: DirectedEdge[] } {
  const edges = cleanEdges(input.edges)

  const fromRaw = normId(input.from)
  const toRaw = normId(input.to)

  const from = fromRaw || (edges.length > 0 ? edges[0]!.from : '')
  const to = toRaw || (edges.length > 0 ? edges[edges.length - 1]!.to : '')

  if (!from && !to) return { from: '', to: '', edges }
  if (edges.length === 0) return { from, to, edges }
  if (!from || !to) return { from, to, edges }

  if (endpointsMatch(edges, from, to)) return { from, to, edges }

  // Try common normalizations (solver/backends sometimes return path edges in
  // opposite direction or swapped per-edge).
  const c1 = swapEach(edges)
  if (endpointsMatch(c1, from, to)) return { from, to, edges: c1 }

  const c2 = reverseOrder(edges)
  if (endpointsMatch(c2, from, to)) return { from, to, edges: c2 }

  const c3 = reverseAndSwap(edges)
  if (endpointsMatch(c3, from, to)) return { from, to, edges: c3 }

  // Fallback: keep edges as-is if we can't align reliably.
  return { from, to, edges }
}
