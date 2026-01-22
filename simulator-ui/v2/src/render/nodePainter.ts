import type { GraphNode } from '../types'
import type { VizMapping } from '../vizMapping'

export type LayoutNode = GraphNode & { __x: number; __y: number }

export function sizeForNode(n: GraphNode): { w: number; h: number } {
  const s = n.viz_size
  const w = Math.max(6, Number(s?.w ?? (n.type === 'business' ? 14 : 10)))
  const h = Math.max(6, Number(s?.h ?? (n.type === 'business' ? 14 : 10)))
  return { w, h }
}

export function fillForNode(n: GraphNode, mapping: VizMapping): string {
  const key = String(n.viz_color_key ?? (n.type === 'business' ? 'business' : 'person'))
  const hit = mapping.node.color[key]
  return hit ? hit.fill : mapping.node.color.person.fill
}

export function drawNodeShape(ctx: CanvasRenderingContext2D, node: LayoutNode, opts: { mapping: VizMapping }) {
  const { mapping } = opts
  const fill = fillForNode(node, mapping)
  const { w, h } = sizeForNode(node)
  const isBusiness = String(node.type) === 'business'

  ctx.fillStyle = fill
  if (isBusiness) {
    ctx.fillRect(node.__x - w / 2, node.__y - h / 2, w, h)
  } else {
    ctx.beginPath()
    ctx.arc(node.__x, node.__y, Math.min(w, h) / 2, 0, Math.PI * 2)
    ctx.fill()
  }
}
