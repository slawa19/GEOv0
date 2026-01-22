import type { GraphLink, GraphNode } from '../types'
import type { VizMapping } from '../vizMapping'
import { drawNodeShape, sizeForNode, type LayoutNode } from './nodePainter'

export type LayoutLink = GraphLink & { __key: string }

function linkWidthPx(l: GraphLink, mapping: VizMapping): number {
  const k = String(l.viz_width_key ?? 'hairline')
  return mapping.link.width_px[k] ?? mapping.link.width_px.hairline
}

function linkAlpha(l: GraphLink, mapping: VizMapping): number {
  const k = String(l.viz_alpha_key ?? 'bg')
  return mapping.link.alpha[k] ?? mapping.link.alpha.bg
}

function isIncident(link: LayoutLink, nodeId: string) {
  return link.source === nodeId || link.target === nodeId
}

export function drawBaseGraph(ctx: CanvasRenderingContext2D, opts: {
  w: number
  h: number
  nodes: LayoutNode[]
  links: LayoutLink[]
  mapping: VizMapping
  selectedNodeId: string | null
  activeEdges: Set<string>
}) {
  const { w, h, nodes, links, mapping, selectedNodeId, activeEdges } = opts

  // Background (deep space).
  ctx.fillStyle = '#020617'
  ctx.fillRect(0, 0, w, h)

  const pos = new Map(nodes.map((n) => [n.id, n]))

  // Links (very faint in idle; brighter on focus/active edges).
  for (const link of links) {
    const a = pos.get(link.source)!
    const b = pos.get(link.target)!

    let alpha = linkAlpha(link, mapping)
    let width = linkWidthPx(link, mapping)

    const active = activeEdges.has(link.__key)

    if (active) {
      alpha = mapping.link.alpha.hi
      width = mapping.link.width_px.highlight
    } else if (selectedNodeId) {
      // Focus mode: show incident links, dim the rest.
      if (isIncident(link, selectedNodeId)) {
        alpha = Math.max(alpha, mapping.link.alpha.active)
        width = Math.max(width, mapping.link.width_px.thin)
      } else {
        alpha = Math.min(alpha, mapping.link.alpha.bg)
        width = mapping.link.width_px.hairline
      }
    }

    ctx.strokeStyle = `rgba(100,116,139,${alpha})`
    ctx.lineWidth = width
    ctx.beginPath()
    ctx.moveTo(a.__x, a.__y)
    ctx.lineTo(b.__x, b.__y)
    ctx.stroke()
  }

  // Nodes
  for (const n of nodes) {
    const isSelected = selectedNodeId === n.id

    if (isSelected) {
      // Conservative glow ring (focus) per screen-prototypes.
      const { w: nw, h: nh } = sizeForNode(n as GraphNode)
      ctx.save()
      ctx.globalAlpha = 0.35
      ctx.fillStyle = '#22d3ee'
      ctx.beginPath()
      ctx.arc(n.__x, n.__y, Math.max(nw, nh) * 1.2, 0, Math.PI * 2)
      ctx.fill()
      ctx.restore()
    }

    drawNodeShape(ctx, n, { mapping })
  }

  return pos
}
