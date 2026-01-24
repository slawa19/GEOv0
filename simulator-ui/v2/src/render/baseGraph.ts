import type { GraphLink, GraphNode } from '../types'
import type { VizMapping } from '../vizMapping'
import { drawNodeShape, fillForNode, sizeForNode, type LayoutNode } from './nodePainter'

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

function clamp01(v: number) {
  return Math.max(0, Math.min(1, v))
}

type Rgb = { r: number; g: number; b: number }
const rgbCache = new Map<string, Rgb | null>()

function parseHexRgb(color: string): Rgb | null {
  const c = String(color || '').trim()
  if (!c.startsWith('#')) return null
  const hex = c.slice(1)
  const isShort = hex.length === 3
  const isLong = hex.length === 6
  if (!isShort && !isLong) return null
  const r = parseInt(isShort ? hex[0]! + hex[0]! : hex.slice(0, 2), 16)
  const g = parseInt(isShort ? hex[1]! + hex[1]! : hex.slice(2, 4), 16)
  const b = parseInt(isShort ? hex[2]! + hex[2]! : hex.slice(4, 6), 16)
  if (!Number.isFinite(r) || !Number.isFinite(g) || !Number.isFinite(b)) return null
  return { r, g, b }
}

function withAlpha(color: string, alpha: number) {
  const a = clamp01(alpha)
  const c = String(color || '').trim()
  if (c.startsWith('rgba(') || c.startsWith('hsla(')) return c
  if (!c.startsWith('#')) return c

  let rgb = rgbCache.get(c)
  if (rgb === undefined) {
    rgb = parseHexRgb(c)
    rgbCache.set(c, rgb)
  }
  if (!rgb) return c
  return `rgba(${rgb.r},${rgb.g},${rgb.b},${a})`
}

type Palette = Record<string, { color: string; label?: string }>

function linkColor(l: GraphLink, mapping: VizMapping, palette?: Palette) {
  const k = String(l.viz_color_key ?? '')
  const p = k && palette ? palette[k] : undefined
  return p?.color ?? mapping.link.color.default
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
  palette?: Palette
  selectedNodeId: string | null
  activeEdges: Set<string>
}) {
  const { w, h, nodes, links, mapping, palette, selectedNodeId, activeEdges } = opts

  // Background (deep space).
  ctx.fillStyle = '#020617'
  ctx.fillRect(0, 0, w, h)

  const pos = new Map(nodes.map((n) => [n.id, n]))

  // Links: base pass = strictly semantic viz_* (no focus/active overrides).
  for (const link of links) {
    const a = pos.get(link.source)!
    const b = pos.get(link.target)!

    const start = getLinkTermination(a, b)
    const end = getLinkTermination(b, a)

    const alpha = linkAlpha(link, mapping)
    const width = linkWidthPx(link, mapping)
    const color = linkColor(link, mapping, palette)

    ctx.strokeStyle = withAlpha(color, alpha)
    ctx.lineWidth = width
    ctx.beginPath()
    ctx.moveTo(start.x, start.y)
    ctx.lineTo(end.x, end.y)
    ctx.stroke()
  }

  // Links: overlay pass = UX highlight for focus/active without overwriting base style.
  if (selectedNodeId || activeEdges.size > 0) {
    for (const link of links) {
      const isActive = activeEdges.has(link.__key)
      const isFocusIncident = !!selectedNodeId && isIncident(link, selectedNodeId)
      if (!isActive && !isFocusIncident) continue

      const a = pos.get(link.source)!
      const b = pos.get(link.target)!
      const start = getLinkTermination(a, b)
      const end = getLinkTermination(b, a)

      const baseAlpha = linkAlpha(link, mapping)
      const baseWidth = linkWidthPx(link, mapping)
      const baseColor = linkColor(link, mapping, palette)

      // Focus overlay: boost visibility but keep semantic color.
      if (isFocusIncident && !isActive) {
        const alpha = clamp01(baseAlpha * 3.0)
        const width = Math.max(baseWidth, mapping.link.width_px.thin)
        ctx.strokeStyle = withAlpha(baseColor, alpha)
        ctx.lineWidth = width
        ctx.beginPath()
        ctx.moveTo(start.x, start.y)
        ctx.lineTo(end.x, end.y)
        ctx.stroke()
        continue
      }

      // Active overlay: explicit UX color policy (matches tx sparks) but stays as a separate layer.
      if (isActive) {
        const alpha = clamp01(Math.max(mapping.link.alpha.hi, baseAlpha * 4.0))
        const width = Math.max(baseWidth, mapping.link.width_px.highlight)
        ctx.strokeStyle = withAlpha('#22d3ee', alpha)
        ctx.lineWidth = width
        ctx.beginPath()
        ctx.moveTo(start.x, start.y)
        ctx.lineTo(end.x, end.y)
        ctx.stroke()
      }
    }
  }

  // Nodes
  for (const n of nodes) {
    const isSelected = selectedNodeId === n.id

    if (isSelected) {
      // Focus Glow: "Light glow around the node"
      // Stronger but diffuse (not a sharp second contour)
      
      const { w: nw, h: nh } = sizeForNode(n as GraphNode)
      const glow = fillForNode(n as GraphNode, mapping)
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
