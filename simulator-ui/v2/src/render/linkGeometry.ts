import { sizeForNode, type LayoutNode } from './nodePainter'

export function getLinkTermination(n: LayoutNode, target: { __x: number; __y: number }, invZoom: number) {
  const dx = target.__x - n.__x
  const dy = target.__y - n.__y
  if (Math.abs(dx) < 0.1 && Math.abs(dy) < 0.1) return { x: n.__x, y: n.__y }

  const angle = Math.atan2(dy, dx)
  const { w: w0, h: h0 } = sizeForNode(n)
  const w = w0 * invZoom
  const h = h0 * invZoom

  // Person = circle
  if (String(n.type) !== 'business') {
    const r = Math.max(w, h) / 2
    return { x: n.__x + Math.cos(angle) * r, y: n.__y + Math.sin(angle) * r }
  }

  // Business = rounded-rect approximation via ray-box intersection
  const hw = w / 2
  const hh = h / 2
  const absCos = Math.abs(Math.cos(angle))
  const absSin = Math.abs(Math.sin(angle))
  const xDist = absCos > 0.001 ? hw / absCos : Infinity
  const yDist = absSin > 0.001 ? hh / absSin : Infinity
  const dist = Math.min(xDist, yDist)

  return { x: n.__x + Math.cos(angle) * dist, y: n.__y + Math.sin(angle) * dist }
}
