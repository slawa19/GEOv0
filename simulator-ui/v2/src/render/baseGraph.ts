import type { GraphLink, GraphNode } from '../types'
import type { LayoutLink } from '../types/layout'
import { classifyNode, linkColorOf, type VizMapping } from '../vizMapping'
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
 * the same whatever is owed. Its colour is the data token `--ds-data-direction` (`mapping.node.palette.direction`,
 * the same in every theme; revised 2026-10-16 from the line's own `#64748b`, which the external review found barely
 * legible): contrast 10.1 to the canvas of `saas` and 10.6 to that of `hud` (WCAG relative luminance, computed, not
 * measured on pixels). Opaque also means that markers lying one over another do not add up to another colour.
 */
export const DIRECTION_MARKER_ALPHA = 1

/** Corners of the markers of one pass, reused between passes: the hot path allocates only when the scene grows. */
const markerPool: Arrowhead[] = []
const markerLinks: LayoutLink[] = []
let markerCount = 0

/**
 * The painted extent of every node, once per pass (measured 2026-10-16: asking the node geometry and the classifier
 * for each marker x node pair - 348 x 100 per frame - took the frame from 54 to 117 ms at CPU x4). Plain arrays,
 * reused between passes.
 */
const extentX: number[] = []
const extentY: number[] = []
const extentHW: number[] = []
const extentHH: number[] = []
const extentCircle: boolean[] = []
const extentId: string[] = []
let extentCount = 0

function measureNodeExtents(nodes: LayoutNode[], hiddenNodeId: string | null, invZ: number) {
  extentCount = 0
  for (const n of nodes) {
    if (n.id === hiddenNodeId) continue
    const g = getNodeBaseGeometry(n, invZ)
    // The outline (1.5 px); a hub's ring stands 3.5 px out and is 1.4 px wide.
    const pad = (classifyNode(n).hub ? 1.5 + 3.5 + 1.4 : 1.5) * invZ
    const k = extentCount++
    extentX[k] = n.__x
    extentY[k] = n.__y
    extentCircle[k] = g.shape === 'circle'
    extentHW[k] = (g.shape === 'circle' ? g.r : g.w / 2) + pad
    extentHH[k] = (g.shape === 'circle' ? g.r : g.h / 2) + pad
    extentId[k] = n.id
  }
}

/**
 * Does a THIRD node (not the two ends of the line) lie over this triangle? Such a node covers the line, and it
 * covers the marker too: that marker is drawn before the nodes, the others after them. The node's painted extent is
 * its size plus its outline - plus the ring of a hub (`measureNodeExtents`). Rectangles are taken as their box (their
 * rounded corners make that a little generous: a marker near a corner goes under the node).
 */
function underAThirdNode(m: Arrowhead, sourceId: string, targetId: string): boolean {
  const minX = Math.min(m.tipX, m.leftX, m.rightX)
  const maxX = Math.max(m.tipX, m.leftX, m.rightX)
  const minY = Math.min(m.tipY, m.leftY, m.rightY)
  const maxY = Math.max(m.tipY, m.leftY, m.rightY)
  for (let k = 0; k < extentCount; k++) {
    const x = extentX[k]!
    const y = extentY[k]!
    const hw = extentHW[k]!
    const hh = extentHH[k]!
    // Box against box first: almost every node is far away.
    if (x + hw < minX || x - hw > maxX || y + hh < minY || y - hh > maxY) continue
    const id = extentId[k]!
    if (id === sourceId || id === targetId) continue
    if (!extentCircle[k]) return true
    if (circleTouchesTriangle(x, y, hw, m)) return true
  }
  return false
}

function circleTouchesTriangle(cx: number, cy: number, r: number, m: Arrowhead): boolean {
  const r2 = r * r
  const edge = (ax: number, ay: number, bx: number, by: number) => {
    const dx = bx - ax
    const dy = by - ay
    const l2 = dx * dx + dy * dy
    const t = l2 > 0 ? Math.max(0, Math.min(1, ((cx - ax) * dx + (cy - ay) * dy) / l2)) : 0
    const ex = ax + t * dx - cx
    const ey = ay + t * dy - cy
    return ex * ex + ey * ey <= r2
  }
  if (edge(m.tipX, m.tipY, m.leftX, m.leftY) || edge(m.leftX, m.leftY, m.rightX, m.rightY) || edge(m.rightX, m.rightY, m.tipX, m.tipY)) return true
  // The centre inside the triangle (a node smaller than the marker): same side of all three edges.
  const side = (ax: number, ay: number, bx: number, by: number) => (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
  const s1 = side(m.tipX, m.tipY, m.leftX, m.leftY)
  const s2 = side(m.leftX, m.leftY, m.rightX, m.rightY)
  const s3 = side(m.rightX, m.rightY, m.tipX, m.tipY)
  return (s1 >= 0 && s2 >= 0 && s3 >= 0) || (s1 <= 0 && s2 <= 0 && s3 <= 0)
}

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

  // Direction markers (039 slice B, PR 2). `source -> target` is creditor -> debtor (AGENTS.md section 8): the
  // arrowhead stands at the TARGET, the same triangle whatever `used` is (it shows trust, not a debt). Collected here
  // for the lines the pass above drew - all of them in `full` LOD, the remaining ones in `focus` - and DRAWN after
  // everything that could be painted over them: the highlight strokes and the nodes with their rings (revised
  // 2026-10-16 after the external review: a hub ring cut the neck of a marker at a hub-debtor, and the highlight
  // stroke of A -> B crossed the marker of B -> A at A). Exception: a marker under a THIRD node is drawn before the
  // nodes, so that node covers it as it covers the line. One fill per marker, no batching.
  const markerHighlight = (link: LayoutLink): number => {
    if (dragMode) return 0
    const edgeAlpha = activeEdges.get(link.__key)
    if (edgeAlpha === undefined) return 0
    // The same gate as the highlight stroke below: no stroke, no highlight marker.
    const alpha = clamp01(Math.max(mapping.link.alpha.hi, linkAlpha(link, mapping) * 4.0)) * edgeAlpha
    return alpha > 0.003 ? edgeAlpha : 0
  }
  const markerBase = withAlpha(mapping.node.palette.direction, DIRECTION_MARKER_ALPHA)
  const fillMarker = (m: Arrowhead, link: LayoutLink) => {
    fillArrowhead(ctx, m, markerBase)
    // A highlighted line: its marker again in the highlight colour, at the same TARGET end, fading with the highlight
    // back to the marker underneath.
    const h = markerHighlight(link)
    if (h > 0) fillArrowhead(ctx, m, withAlpha(mapping.fx.tx_spark.trail, DIRECTION_MARKER_ALPHA * h))
  }
  markerCount = 0
  const deferred: number[] = []
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
    const m = (markerPool[markerCount] ??= newArrowhead())
    if (!trustArrowhead(a, b, invZ, m)) continue
    markerLinks[markerCount] = link
    deferred.push(markerCount)
    markerCount += 1
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
        }
      }
    }
  }

  // Markers that a third node lies over: drawn now, under the nodes (see the collection above).
  let after = 0
  if (deferred.length > 0) measureNodeExtents(nodes, hiddenNodeId, invZ)
  for (const k of deferred) {
    const link = markerLinks[k]!
    if (underAThirdNode(markerPool[k]!, link.source, link.target)) fillMarker(markerPool[k]!, link)
    else deferred[after++] = k
  }
  deferred.length = after

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

  // The markers, last: nothing of the base canvas is painted over them.
  for (const k of deferred) fillMarker(markerPool[k]!, markerLinks[k]!)
  markerLinks.fill(undefined as unknown as LayoutLink, 0, markerCount)

  return pos
}
