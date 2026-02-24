import type { GraphNode } from '../types'

const sizeCache = new WeakMap<GraphNode, { key: string; size: { w: number; h: number } }>()

export function sizeForNode(n: GraphNode): { w: number; h: number } {
  const s = n.viz_size
  const key = `${String(s?.w ?? '')}:${String(s?.h ?? '')}`
  const cached = sizeCache.get(n)
  if (cached && cached.key === key) return cached.size

  // Backend-first: do not derive visual size from domain fields like `type`.
  // If `viz_size` is missing, use a neutral default.
  const DEFAULT = 12
  const MIN = 6
  const wRaw = Number(s?.w ?? DEFAULT)
  const hRaw = Number(s?.h ?? DEFAULT)
  const w = Math.max(MIN, Number.isFinite(wRaw) ? wRaw : DEFAULT)
  const h = Math.max(MIN, Number.isFinite(hRaw) ? hRaw : DEFAULT)
  const size = { w, h }
  sizeCache.set(n, { key, size })
  return size
}
