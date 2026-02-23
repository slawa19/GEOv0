import type { LayoutNode } from './nodePainter'
import { getNodeShape } from '../types/nodeShape'

type PointLike = { x: number; y: number }

// Hot-path: avoid per-call allocations.
//
// `getLinkTermination()` is called for every rendered edge (often twice: start/end).
// The original implementation returned a fresh `{x,y}` object every time.
//
// Strategy: keep a small ring-buffer pool of mutable point objects.
// Callers that need to retain the point must copy `{ x, y }` out.
const LINK_POINT_POOL_SIZE = 1024 // power-of-2 for cheap wrap-around
const linkPointPool: PointLike[] = Array.from({ length: LINK_POINT_POOL_SIZE }, () => ({ x: 0, y: 0 }))
let linkPointPoolCursor = 0

function pooledPoint(x: number, y: number): PointLike {
  const p = linkPointPool[linkPointPoolCursor]
  linkPointPoolCursor = (linkPointPoolCursor + 1) & (LINK_POINT_POOL_SIZE - 1)
  p.x = x
  p.y = y
  return p
}

export function getLinkTermination(n: LayoutNode, target: { __x: number; __y: number }, invZoom: number) {
  const dx = target.__x - n.__x
  const dy = target.__y - n.__y
  if (Math.abs(dx) < 0.1 && Math.abs(dy) < 0.1) return pooledPoint(n.__x, n.__y)

  const len = Math.max(1e-6, Math.hypot(dx, dy))
  const ux = dx / len
  const uy = dy / len

  // Inline `sizeForNode()` to avoid its per-call string key allocation.
  // Must match behaviour 1-to-1: clamp to >= 6, default to 12, numeric coercion via `Number()`.
  const s = n.viz_size
  const w = Math.max(6, Number(s?.w ?? 12)) * invZoom
  const h = Math.max(6, Number(s?.h ?? 12)) * invZoom

  const shapeKey = getNodeShape(n) ?? 'circle'
  const isRoundedRect = shapeKey === 'rounded-rect'

  // Person = circle
  if (!isRoundedRect) {
    const r = Math.max(w, h) / 2
    return pooledPoint(n.__x + ux * r, n.__y + uy * r)
  }

  // Business = rounded-rect approximation via ray-box intersection
  const hw = w / 2
  const hh = h / 2
  const absUx = Math.abs(ux)
  const absUy = Math.abs(uy)
  const xDist = absUx > 1e-6 ? hw / absUx : Infinity
  const yDist = absUy > 1e-6 ? hh / absUy : Infinity
  const dist = Math.min(xDist, yDist)

  return pooledPoint(n.__x + ux * dist, n.__y + uy * dist)
}
