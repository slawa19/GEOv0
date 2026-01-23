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

function focusGlowColor(n: GraphNode) {
  const s = Number(n.net_sign ?? 0)
  if (s > 0) return '#22d3ee' // credit
  if (s < 0) return '#f97316' // debt
  return '#e2e8f0' // near-zero (neutral white/slate)
}

function getLinkTermination(n: LayoutNode, target: { __x: number; __y: number }) {
  const dx = target.__x - n.__x
  const dy = target.__y - n.__y
  if (Math.abs(dx) < 0.1 && Math.abs(dy) < 0.1) return { x: n.__x, y: n.__y }

  const angle = Math.atan2(dy, dx)
  const { w, h } = sizeForNode(n as GraphNode)
  
  // Person = Circle
  if (n.type !== 'business') {
    const r = Math.max(w, h) / 2
    // Terminate slightly inside the rim to ensure connection, or exactly at rim.
    // Given the request "end at contours", we use r. 
    return {
      x: n.__x + Math.cos(angle) * r,
      y: n.__y + Math.sin(angle) * r
    }
  }

  // Business = Rectangle (Approximation for square)
  // Ray-box intersection
  const hw = w / 2
  const hh = h / 2
  
  const absCos = Math.abs(Math.cos(angle))
  const absSin = Math.abs(Math.sin(angle))
  
  const xDist = (absCos > 0.001) ? hw / absCos : Infinity
  const yDist = (absSin > 0.001) ? hh / absSin : Infinity
  
  const dist = Math.min(xDist, yDist)
  
  return {
    x: n.__x + Math.cos(angle) * dist,
    y: n.__y + Math.sin(angle) * dist
  }
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

    // Calculate termination points at contours
    const start = getLinkTermination(a, b)
    const end = getLinkTermination(b, a)

    let alpha = linkAlpha(link, mapping)
    let width = linkWidthPx(link, mapping)

    const active = activeEdges.has(link.__key)

    if (active) {
      alpha = 1.0 // Active = Fully visible
      width = mapping.link.width_px.highlight
      // Active edges are Electric Cyan (to match sparks)
      ctx.strokeStyle = `rgba(34, 211, 238, ${alpha})` 
    } else if (selectedNodeId) {
      // Focus mode
      if (isIncident(link, selectedNodeId)) {
        alpha = 0.5 // Clearly visible but not blinding
        width = mapping.link.width_px.thin
        // Incident edges are Lighter Slate
        ctx.strokeStyle = `rgba(148, 163, 184, ${alpha})`
      } else {
        alpha = 0.03 // Almost invisible
        width = mapping.link.width_px.hairline
        // Non-incident are dark
        ctx.strokeStyle = `rgba(71, 85, 105, ${alpha})`
      }
    } else {
      // Idle mode
      ctx.strokeStyle = `rgba(100,116,139,${alpha})`
    }

    ctx.lineWidth = width
    ctx.beginPath()
    ctx.moveTo(start.x, start.y)
    ctx.lineTo(end.x, end.y)
    ctx.stroke()
  }

  // Nodes
  for (const n of nodes) {
    const isSelected = selectedNodeId === n.id

    if (isSelected) {
      // Focus Glow: "Light glow around the node"
      // Stronger but diffuse (not a sharp second contour)
      
      const { w: nw, h: nh } = sizeForNode(n as GraphNode)
      const glow = focusGlowColor(n as GraphNode)
      const isBusiness = n.type === 'business'
      
      const r = Math.max(nw, nh) / 2
      const rr = Math.max(0, Math.min(4, Math.min(nw, nh) * 0.18))
      
      ctx.save()
      ctx.globalCompositeOperation = 'screen' // Black stroke will disappear, only colored shadow remains
      
      ctx.shadowColor = glow
      ctx.shadowBlur = r * 1.2 
      // Trick: Stroke is black (invisible in Screen mode), so we don't see a "hard" contour.
      // But the shadow (glow) is drawn.
      ctx.strokeStyle = '#000000' 
      ctx.lineWidth = Math.max(4, r * 0.25) // Thicker stroke generates more glow intensity
      ctx.globalAlpha = 1.0
      
      ctx.beginPath()
      if (isBusiness) {
         const offset = 0
         ctx.roundRect(n.__x - nw/2 - offset, n.__y - nh/2 - offset, nw + offset*2, nh + offset*2, rr)
      } else {
         const offset = 0
         ctx.arc(n.__x, n.__y, r + offset, 0, Math.PI * 2)
      }
      ctx.stroke()
      
      // Optional: Second pass for "core" intensity closer to the node
      ctx.shadowBlur = r * 0.4
      ctx.stroke()

      ctx.restore()
    }

    drawNodeShape(ctx, n, { mapping })
  }

  return pos
}
