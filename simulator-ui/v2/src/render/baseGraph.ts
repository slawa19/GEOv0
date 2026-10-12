import type { GraphLink, GraphNode } from '../types'
import type { LayoutLink } from '../types/layout'
import { linkColorOf, type VizMapping } from '../vizMapping'
import { clamp01 } from '../utils/math'
import { createThrottledWarn } from '../utils/throttledWarn'
import { withAlpha } from './color'
import { drawGlowSprite } from './glowSprites'
import { newArrowhead, trustArrowhead, type Arrowhead } from './linkArrowhead'
import { getLinkTermination } from './linkGeometry'
import { getNodeBaseGeometry } from './nodeGeometry'
import type { LayoutNode } from '../types/layout'
import { fillForNode } from './nodeFill'
import { drawNodeShape } from './nodePainter'

const warnOrphanLink = createThrottledWarn(5000)
let orphanLinkSkippedCount = 0

const nodesRefByPos = new WeakMap<Map<string, LayoutNode>, LayoutNode[]>()

function isTestRuntime(): boolean {
  return import.meta.env.MODE === 'test' || String(import.meta.env.VITE_TEST_MODE ?? '0') === '1'
}

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

type Palette = Record<string, { color: string; label?: string }>

/** The one resolver of a line's colour, shared with the arrow of the legend (`vizMapping.ts`). */
const linkColor = linkColorOf

/**
 * The alpha of a direction marker: OPAQUE (039 slice B, PR 2). The marker must not inherit the alpha of its line - a
 * line with little or no debt is drawn at 0.06 and its marker would be invisible, although the direction of trust is
 * the same whatever is owed. At 1 the line colour `#64748b` has a contrast of 3.93 to the canvas of `saas` and 4.14
 * to that of `hud` (WCAG relative luminance, computed 2026-10-12, not measured on pixels; 3 is the floor for a
 * graphical object); at 0.8 it is 2.94 and 3.03 - at the floor and under it. Opaque also means that markers lying
 * one over another do not add up to another colour.
 */
export const DIRECTION_MARKER_ALPHA = 1

/** One reused object: `trustArrowhead` writes the corners of the current marker into it. */
const arrowhead: Arrowhead = newArrowhead()

/** A closed path of its own and one `fill`: the body of the line stays an open path with its own stroke. */
function fillArrowhead(ctx: CanvasRenderingContext2D, a: Arrowhead, style: string) {
  ctx.fillStyle = style
  ctx.beginPath()
  ctx.moveTo(a.tipX, a.tipY)
  ctx.lineTo(a.leftX, a.leftY)
  ctx.lineTo(a.rightX, a.rightY)
  ctx.closePath()
  ctx.fill()
}

export function drawBaseGraph(ctx: CanvasRenderingContext2D, opts: {
  w: number
  h: number
  nodes: LayoutNode[]
  links: LayoutLink[]
  mapping: VizMapping
  palette?: Palette
  selectedNodeId: string | null
  activeEdges: Map<string, number>
  activeNodes?: Set<string>
  /** When non-empty: nodes NOT in this set are rendered with reduced opacity (picking dim). */
  dimmedNodeIds?: Set<string> | null
  cameraZoom?: number
  quality?: 'low' | 'med' | 'high'
  linkLod?: 'full' | 'focus'
  dragMode?: boolean
  hiddenNodeId?: string | null
  pos?: Map<string, LayoutNode>
}) {
  const { nodes, links, mapping, palette, selectedNodeId, activeEdges } = opts
  const activeNodes = opts.activeNodes
  const dimmedNodeIds = opts.dimmedNodeIds
  // A node is dimmed when dimmedNodeIds is non-null/non-empty AND the node is not in it.
  const hasDim = !!dimmedNodeIds && dimmedNodeIds.size > 0
  const linkLod = opts.linkLod ?? 'full'
  const dragMode = opts.dragMode ?? false
  const hiddenNodeId = opts.hiddenNodeId ?? null
  const z = Math.max(0.01, Number(opts.cameraZoom ?? 1))
  const invZ = 1 / z
  const q = opts.quality ?? 'high'
  const allowDevWarn = import.meta.env.DEV && !isTestRuntime()

  function drawNodeOverlayGlow(
    n: LayoutNode,
    glow: string,
    params: { kind: 'selection' | 'active'; blurK: number; lineWidthMinPx: number; lineWidthK: number },
  ) {
    const { shape, w, h, r, rr } = getNodeBaseGeometry(n, invZ)
    drawGlowSprite(ctx, {
      kind: params.kind,
      shape,
      x: n.__x,
      y: n.__y,
      w,
      h,
      r,
      rr,
      color: glow,
      blurPx: r * params.blurK,
      lineWidthPx: Math.max(params.lineWidthMinPx * invZ, r * params.lineWidthK),
      composite: 'screen',
    })
  }

  const pos = opts.pos ?? new Map<string, LayoutNode>()
  // Optimization: rebuild pos Map only when the nodes array reference changes.
  // When physics updates __x/__y in-place (same array, same object refs), the Map
  // entries are already current — no need to clear + rebuild O(n) every frame.
  // Also rebuild when the Map was cleared externally (e.g., on snapshot change in useRenderLoop).
  const prevNodesRef = nodesRefByPos.get(pos)
  if (prevNodesRef !== nodes || pos.size === 0) {
    pos.clear()
    for (const n of nodes) pos.set(n.id, n)
    nodesRefByPos.set(pos, nodes)
  }

  // Links: base pass = strictly semantic viz_* (no focus/active overrides).
  for (const link of links) {
    if (linkLod === 'focus') {
      const isActive = activeEdges.has(link.__key)
      const isFocusIncident = !!selectedNodeId && isIncident(link, selectedNodeId)
      if (!isActive && !isFocusIncident) continue
    }

    const a = pos.get(link.source)
    const b = pos.get(link.target)
    if (!a || !b) {
      orphanLinkSkippedCount++
      warnOrphanLink(
        allowDevWarn,
        `[baseGraph] orphan link skipped (total ${orphanLinkSkippedCount}):`,
        link.source,
        '→',
        link.target,
      )
      continue
    }

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

  // Links: direction markers. `source -> target` is creditor -> debtor (AGENTS.md section 8): the arrowhead stands
  // at the TARGET. A pass of its own after every body, so that no body is stroked over a marker, in the resolved
  // colour of the line and opaque - the same triangle whatever `used` is (it shows trust, not a debt). The lines are
  // the ones the pass above drew: all of them in `full` LOD, the remaining ones in `focus`. No batching: one fill each.
  for (const link of links) {
    if (linkLod === 'focus') {
      const isActive = activeEdges.has(link.__key)
      const isFocusIncident = !!selectedNodeId && isIncident(link, selectedNodeId)
      if (!isActive && !isFocusIncident) continue
    }
    const a = pos.get(link.source)
    const b = pos.get(link.target)
    // An orphan link was counted and reported by the pass above.
    if (!a || !b) continue
    if (!trustArrowhead(a, b, invZ, arrowhead)) continue
    fillArrowhead(ctx, arrowhead, withAlpha(linkColor(link, mapping, palette), DIRECTION_MARKER_ALPHA))
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

      const a = pos.get(link.source)
      const b = pos.get(link.target)
      if (!a || !b) {
        orphanLinkSkippedCount++
        warnOrphanLink(
          allowDevWarn,
          `[baseGraph] orphan link skipped (total ${orphanLinkSkippedCount}):`,
          link.source,
          '→',
          link.target,
        )
        continue
      }
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
      // Alpha fades smoothly via activeEdges map value (1.0 → 0.0 over last ~1.2s of TTL).
      if (isActive) {
        const edgeAlpha = activeEdges.get(link.__key) ?? 1.0
        const alpha = clamp01(Math.max(mapping.link.alpha.hi, baseAlpha * 4.0)) * edgeAlpha
        if (alpha > 0.003) {
          const width = Math.max(baseWidth, mapping.link.width_px.highlight)
          ctx.strokeStyle = withAlpha(mapping.fx.tx_spark.trail, alpha)
          ctx.lineWidth = width * invZ
          ctx.beginPath()
          ctx.moveTo(start.x, start.y)
          ctx.lineTo(end.x, end.y)
          ctx.stroke()
          // The highlight stroke runs over the marker of this line: the marker is drawn again in the highlight
          // colour, at the same TARGET end, and fades with the highlight back to the marker underneath. A focus
          // overlay (above) keeps the line's own colour, so the marker of the pass before stays as it is.
          if (trustArrowhead(a, b, invZ, arrowhead)) {
            fillArrowhead(ctx, arrowhead, withAlpha(mapping.fx.tx_spark.trail, DIRECTION_MARKER_ALPHA * edgeAlpha))
          }
        }
      }
    }
  }

  // Nodes
  for (const n of nodes) {
    if (hiddenNodeId && n.id === hiddenNodeId) continue
    const isSelected = selectedNodeId === n.id
    const isActiveNode = !!activeNodes && activeNodes.has(n.id)
    const isDimmed = hasDim && !dimmedNodeIds!.has(n.id) && !isSelected

    if (isDimmed) ctx.globalAlpha = 0.25

    if (isSelected && !dragMode) {
      // Focus Glow: "Light glow around the node"
      // Stronger but diffuse (not a sharp second contour)
      
      const glow = fillForNode(n as GraphNode, mapping)
      
      // Phase 1: selection glow via pre-rendered sprite (no on-screen shadowBlur).
      // Keep minimum lineWidth similar to the previous high-quality stroke.
      drawNodeOverlayGlow(n, glow, { kind: 'selection', blurK: 1.2, lineWidthMinPx: 4, lineWidthK: 0.25 })
    }

    if (isActiveNode && !isSelected && !dragMode) {
      const glow = mapping.fx.clearing_debt

      // Phase 1: active glow via pre-rendered sprite (no on-screen shadowBlur).
      drawNodeOverlayGlow(n, glow, { kind: 'active', blurK: 0.55, lineWidthMinPx: 3.0, lineWidthK: 0.18 })
    }

    drawNodeShape(ctx, n, {
      mapping,
      cameraZoom: z,
      quality: q,
    })

    if (isDimmed) ctx.globalAlpha = 1
  }

  return pos
}
