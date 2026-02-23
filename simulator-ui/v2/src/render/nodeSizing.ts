import type { GraphNode } from '../types'

const sizeCache = new WeakMap<GraphNode, { key: string; size: { w: number; h: number } }>()

export function sizeForNode(n: GraphNode): { w: number; h: number } {
  const s = n.viz_size
  const key = `${String(s?.w ?? '')}:${String(s?.h ?? '')}`
  const cached = sizeCache.get(n)
  if (cached && cached.key === key) return cached.size

  // Backend-first: do not derive visual size from domain fields like `type`.
  // If `viz_size` is missing, use a neutral default.
  const w = Math.max(6, Number(s?.w ?? 12))
  const h = Math.max(6, Number(s?.h ?? 12))
  const size = { w, h }
  sizeCache.set(n, { key, size })
  return size
}
