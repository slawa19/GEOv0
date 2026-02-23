import type { LayoutNode } from '../types/layout'
import { getNodeShape } from '../types/nodeShape'
import { sizeForNode } from './nodeSizing'

export type NodeShapeKind = 'circle' | 'rounded-rect'

export type NodeBaseGeometry = {
  shape: NodeShapeKind
  w: number
  h: number
  r: number
  rr: number
}

export type NodeScaledGeometry = {
  shape: NodeShapeKind
  w: number
  h: number
  r: number
  rr: number
  x: number
  y: number
}

function shapeKindForNode(n: LayoutNode): NodeShapeKind {
  const shapeKey = getNodeShape(n) ?? 'circle'
  return shapeKey === 'rounded-rect' ? 'rounded-rect' : 'circle'
}

export function getNodeBaseGeometry(n: LayoutNode, invZoom = 1): NodeBaseGeometry {
  const { w: w0, h: h0 } = sizeForNode(n)
  const w = w0 * invZoom
  const h = h0 * invZoom
  const r = Math.max(w, h) / 2
  const rr = Math.max(0, Math.min(4 * invZoom, Math.min(w, h) * 0.18))
  return { shape: shapeKindForNode(n), w, h, r, rr }
}

export function getNodeScaledGeometry(n: LayoutNode, scale = 1, invZoom = 1): NodeScaledGeometry {
  const base = getNodeBaseGeometry(n, invZoom)
  const w = base.w * scale
  const h = base.h * scale
  const r = Math.max(w, h) / 2
  const rr = base.rr * scale
  const x = n.__x - w / 2
  const y = n.__y - h / 2
  return { shape: base.shape, w, h, r, rr, x, y }
}
