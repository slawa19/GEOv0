import { sizeForNode, type LayoutNode } from './nodePainter'

export function getLinkTermination(n: LayoutNode, target: { __x: number; __y: number }, invZoom: number) {
  const dx = target.__x - n.__x
  const dy = target.__y - n.__y
  if (Math.abs(dx) < 0.1 && Math.abs(dy) < 0.1) return { x: n.__x, y: n.__y }

  const len = Math.max(1e-6, Math.hypot(dx, dy))
  const ux = dx / len
  const uy = dy / len
  const { w: w0, h: h0 } = sizeForNode(n)
  const w = w0 * invZoom
  const h = h0 * invZoom

  const shapeKey = String((n as any).viz_shape_key ?? 'circle')
  const isRoundedRect = shapeKey === 'rounded-rect'

  // Person = circle
  if (!isRoundedRect) {
    const r = Math.max(w, h) / 2
    return { x: n.__x + ux * r, y: n.__y + uy * r }
  }

  // Business = rounded-rect approximation via ray-box intersection
  const hw = w / 2
  const hh = h / 2
  const absUx = Math.abs(ux)
  const absUy = Math.abs(uy)
  const xDist = absUx > 1e-6 ? hw / absUx : Infinity
  const yDist = absUy > 1e-6 ? hh / absUy : Infinity
  const dist = Math.min(xDist, yDist)

  return { x: n.__x + ux * dist, y: n.__y + uy * dist }
}
