import type { GraphLink, GraphNode } from '../types'
import type { VizMapping } from '../vizMapping'
import { withAlpha } from './color'
import { getLinkTermination } from './linkGeometry'
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

type Palette = Record<string, { color: string; label?: string }>

function linkColor(l: GraphLink, mapping: VizMapping, palette?: Palette) {
  const k = String(l.viz_color_key ?? '')
  const p = k && palette ? palette[k] : undefined
  return p?.color ?? mapping.link.color.default
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
  cameraZoom?: number
  quality?: 'low' | 'med' | 'high'
  linkLod?: 'full' | 'focus'
  dragMode?: boolean
  hiddenNodeId?: string | null
}) {
  const { w, h, nodes, links, mapping, palette, selectedNodeId, activeEdges } = opts
  const linkLod = opts.linkLod ?? 'full'
  const dragMode = opts.dragMode ?? false
  const hiddenNodeId = opts.hiddenNodeId ?? null
  const z = Math.max(0.01, Number(opts.cameraZoom ?? 1))
  const invZ = 1 / z
  const q = opts.quality ?? 'high'
  const blurK = q === 'high' ? 1 : q === 'med' ? 0.75 : 0.55

  const pos = new Map(nodes.map((n) => [n.id, n]))

  // Links: base pass = strictly semantic viz_* (no focus/active overrides).
  for (const link of links) {
    if (linkLod === 'focus') {
      const isActive = activeEdges.has(link.__key)
      const isFocusIncident = !!selectedNodeId && isIncident(link, selectedNodeId)
      if (!isActive && !isFocusIncident) continue
    }

    const a = pos.get(link.source)!
    const b = pos.get(link.target)!

    const start = getLinkTermination(a, b, invZ)
    const end = getLinkTermination(b, a, invZ)

    const baseAlpha = linkAlpha(link, mapping)
    const baseWidth = linkWidthPx(link, mapping)
    const color = linkColor(link, mapping, palette)

    // During drag we render a reduced set of edges (focus LOD).
    // Boost visibility here to avoid needing a second overlay pass.
    const alpha = dragMode ? clamp01(Math.max(0.22, baseAlpha * 2.4)) : baseAlpha
    const width = dragMode ? Math.max(baseWidth, mapping.link.width_px.thin) : baseWidth

    ctx.strokeStyle = withAlpha(color, alpha)
    ctx.lineWidth = width * invZ
    ctx.beginPath()
    ctx.moveTo(start.x, start.y)
    ctx.lineTo(end.x, end.y)
    ctx.stroke()
  }

  // Links: overlay pass = UX highlight for focus/active without overwriting base style.
  if (!dragMode && (selectedNodeId || activeEdges.size > 0)) {
    for (const link of links) {
      if (linkLod === 'focus') {
        const isActive = activeEdges.has(link.__key)
        const isFocusIncident = !!selectedNodeId && isIncident(link, selectedNodeId)
        if (!isActive && !isFocusIncident) continue
      }

      const isActive = activeEdges.has(link.__key)
      const isFocusIncident = !!selectedNodeId && isIncident(link, selectedNodeId)
      if (!isActive && !isFocusIncident) continue

      const a = pos.get(link.source)!
      const b = pos.get(link.target)!
      const start = getLinkTermination(a, b, invZ)
      const end = getLinkTermination(b, a, invZ)

      const baseAlpha = linkAlpha(link, mapping)
      const baseWidth = linkWidthPx(link, mapping)
      const baseColor = linkColor(link, mapping, palette)

      // Focus overlay: boost visibility but keep semantic color.
      if (isFocusIncident && !isActive) {
        const alpha = clamp01(baseAlpha * 3.0)
        const width = Math.max(baseWidth, mapping.link.width_px.thin)
        ctx.strokeStyle = withAlpha(baseColor, alpha)
        ctx.lineWidth = width * invZ
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
        ctx.strokeStyle = withAlpha(mapping.fx.tx_spark.trail, alpha)
        ctx.lineWidth = width * invZ
        ctx.beginPath()
        ctx.moveTo(start.x, start.y)
        ctx.lineTo(end.x, end.y)
        ctx.stroke()
      }
    }
  }

  // Nodes
  for (const n of nodes) {
    if (hiddenNodeId && n.id === hiddenNodeId) continue
    const isSelected = selectedNodeId === n.id

    if (isSelected && !dragMode) {
      // Focus Glow: "Light glow around the node"
      // Stronger but diffuse (not a sharp second contour)
      
      const { w: nw0, h: nh0 } = sizeForNode(n as GraphNode)
      const nw = nw0 * invZ
      const nh = nh0 * invZ
      const glow = fillForNode(n as GraphNode, mapping)
      const isBusiness = n.type === 'business'
      
      const r = Math.max(nw, nh) / 2
      const rr = Math.max(0, Math.min(4 * invZ, Math.min(nw, nh) * 0.18))
      
      ctx.save()
      ctx.globalCompositeOperation = 'screen' // Black stroke will disappear, only colored shadow remains
      
      ctx.shadowColor = glow
      ctx.shadowBlur = r * 1.2 * blurK
      // Trick: Stroke is black (invisible in Screen mode), so we don't see a "hard" contour.
      // But the shadow (glow) is drawn.
      ctx.strokeStyle = '#000000' 
      ctx.lineWidth = Math.max(4 * invZ, r * 0.25) // keep minimum in screen-space
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
      ctx.shadowBlur = r * 0.4 * blurK
      ctx.stroke()

      ctx.restore()
    }

    drawNodeShape(ctx, n, { mapping, cameraZoom: z, quality: q, dragMode })
  }

  return pos
}
