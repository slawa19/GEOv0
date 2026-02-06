import type { GraphNode } from '../types'
import type { LayoutNode } from '../types/layout'
import type { VizMapping } from '../vizMapping'
import { withAlpha } from './color'
import { drawGlowSprite } from './glowSprites'
import { roundedRectPath } from './roundedRect'

export type { LayoutNode } from '../types/layout'

/* ------------------------------------------------------------------ */
/*  Lightweight icon / badge helpers — cheap enough for drag fast-path */
/* ------------------------------------------------------------------ */

/**
 * Draw the node's inner icon (person silhouette or building silhouette).
 * Cost ≈ a few arc/rect calls, no blur/gradient — safe for drag mode.
 */
function drawNodeIcon(
  ctx: CanvasRenderingContext2D,
  fill: string,
  cx: number,
  cy: number,
  w: number,
  h: number,
  r: number,
  isRoundedRect: boolean,
  alpha: number = 0.95,
) {
  ctx.save()
  ctx.globalCompositeOperation = 'source-over'
  ctx.fillStyle = withAlpha(fill, alpha)

  if (!isRoundedRect) {
    // PERSON: "Pawn" / User silhouette
    const s = r * 0.045
    ctx.translate(cx, cy + r * 0.08)

    ctx.beginPath()
    // Head
    ctx.arc(0, -9 * s, 5.5 * s, 0, Math.PI * 2)
    // Body (Shoulders)
    ctx.moveTo(-8 * s, 1 * s)
    ctx.quadraticCurveTo(0, -4 * s, 8 * s, 1 * s)
    ctx.quadraticCurveTo(9 * s, 11 * s, 0, 11 * s)
    ctx.quadraticCurveTo(-9 * s, 11 * s, -8 * s, 1 * s)
    ctx.closePath()
    ctx.fill()
  } else {
    // BUSINESS: "Building" / Briefcase silhouette
    const s = Math.min(w, h) * 0.024
    ctx.translate(cx, cy + h * 0.05)

    ctx.beginPath()
    // Main tower
    const bw = 12 * s
    const bh = 16 * s
    ctx.rect(-bw / 2, -bh / 2, bw, bh)

    // Roof detail (antenna or stepped)
    ctx.rect(-bw / 2 + 2 * s, -bh / 2 - 2 * s, bw - 4 * s, 2 * s)
    ctx.rect(-1 * s, -bh / 2 - 5 * s, 2 * s, 3 * s)

    ctx.fill()

    // Window lines (cutouts)
    ctx.globalCompositeOperation = 'destination-out'
    ctx.fillStyle = '#000000'
    const winW = 2.5 * s
    const winH = 2.5 * s
    const gap = 1.5 * s
    for (let row = -1; row <= 1; row++) {
      ctx.fillRect(-bw / 2 + gap + 0.5 * s, row * (winH + gap) - 1 * s, winW, winH)
      ctx.fillRect(0 + gap - 0.5 * s, row * (winH + gap) - 1 * s, winW, winH)
    }
  }
  ctx.restore()
}

/**
 * Draw optional badge pip. Cost ≈ one arc + fill — negligible.
 */
function drawNodeBadge(
  ctx: CanvasRenderingContext2D,
  cx: number,
  cy: number,
  r: number,
  px: (v: number) => number,
) {
  const br = Math.max(px(1.6), r * 0.22)
  ctx.save()
  ctx.globalCompositeOperation = 'lighter'
  ctx.fillStyle = withAlpha('#ffffff', 0.85)
  ctx.beginPath()
  ctx.arc(cx + r * 0.72, cy - r * 0.72, br, 0, Math.PI * 2)
  ctx.fill()
  ctx.restore()
}

const sizeCache = new WeakMap<GraphNode, { key: string; size: { w: number; h: number } }>()

export function sizeForNode(n: GraphNode): { w: number; h: number } {
  const s = n.viz_size
  const key = `${String(s?.w ?? '')}:${String(s?.h ?? '')}`
  const cached = sizeCache.get(n)
  if (cached && cached.key === key) return cached.size

  // Backend-first: do not derive visual size from domain fields like `type`.
  // If `viz_size` is missing, use a neutral default.
  const w = Math.max(6, Number(s?.w ?? 12))
  const h = Math.max(6, Number(s?.h ?? 12))
  const size = { w, h }
  sizeCache.set(n, { key, size })
  return size
}

export function fillForNode(n: GraphNode, mapping: VizMapping): string {
  // Backend-first: UI only interprets viz keys; never derives colors from `type`.
  const key = String(n.viz_color_key ?? 'unknown')
  return mapping.node.color[key]?.fill ?? mapping.node.color.unknown.fill
}

export function drawNodeShape(
  ctx: CanvasRenderingContext2D,
  node: LayoutNode,
  opts: {
    mapping: VizMapping
    cameraZoom?: number
    quality?: 'low' | 'med' | 'high'
    dragMode?: boolean
    /** Interaction Quality hint (legacy boolean). */
    interaction?: boolean
    /** Smooth interaction intensity 0.0–1.0. Takes precedence over boolean `interaction`. */
    interactionIntensity?: number
  },
) {
  const { mapping } = opts
  const z = Math.max(0.01, Number(opts.cameraZoom ?? 1))
  const invZ = 1 / z
  const px = (v: number) => v * invZ
  const q = opts.quality ?? 'high'
  // Sprite glow quality scaling.
  // (Even in low, we keep a modest glow for readability.)
  const glowK = q === 'high' ? 1 : q === 'med' ? 0.75 : 0.55
  const fill = fillForNode(node, mapping)
  const { w: w0, h: h0 } = sizeForNode(node)
  const w = w0 * invZ
  const h = h0 * invZ
  const shapeKey = String(node.viz_shape_key ?? 'circle')
  const isRoundedRect = shapeKey === 'rounded-rect'

  // IMPORTANT: node appearance follows the prototypes:
  // - business: emerald square
  // - person: blue circle
  // Additional semantics (debt bins, statuses) are still fixtures/backend-driven via viz_color_key.
  const r = Math.max(px(4), Math.min(w, h) / 2)

  const x = node.__x - w / 2
  const y = node.__y - h / 2
  const rr = Math.max(0, Math.min(px(4), Math.min(w, h) * 0.18))

  // Drag fast-path: keep visuals recognizable but avoid expensive shadows/gradients.
  if (opts.dragMode) {
    ctx.save()
    ctx.globalCompositeOperation = 'source-over'
    ctx.fillStyle = withAlpha(fill, 0.45)
    if (isRoundedRect) {
      roundedRectPath(ctx, x, y, w, h, rr)
      ctx.fill()
      ctx.strokeStyle = withAlpha('#ffffff', 0.7)
      ctx.lineWidth = Math.max(px(1), r * 0.07)
      roundedRectPath(ctx, x, y, w, h, rr)
      ctx.stroke()
    } else {
      ctx.beginPath()
      ctx.arc(node.__x, node.__y, r, 0, Math.PI * 2)
      ctx.fill()
      ctx.strokeStyle = withAlpha('#ffffff', 0.7)
      ctx.lineWidth = Math.max(px(1), r * 0.07)
      ctx.beginPath()
      ctx.arc(node.__x, node.__y, r, 0, Math.PI * 2)
      ctx.stroke()
    }
    // Icon & badge are cheap (a few arcs/rects) — keep them visible during drag.
    drawNodeIcon(ctx, fill, node.__x, node.__y, w, h, r, isRoundedRect, 0.7)
    if (node.viz_badge_key !== undefined && node.viz_badge_key !== null) {
      drawNodeBadge(ctx, node.__x, node.__y, r, px)
    }
    ctx.restore()
    return
  }

  ctx.save()

  // 1) Glow sprite: bloom (underlay)
  drawGlowSprite(ctx, {
    kind: 'bloom',
    shape: isRoundedRect ? 'rounded-rect' : 'circle',
    x: node.__x,
    y: node.__y,
    w,
    h,
    r,
    rr,
    color: fill,
    blurPx: r * 1.5 * glowK,
    lineWidthPx: 0,
    composite: 'screen',
  })

  // 2) Body fill - Semi-transparent glass (Darker/More Solid now)
  ctx.save()
  ctx.globalCompositeOperation = 'source-over'
  // Linear gradients are moderately expensive; keep them for high only.
  // Gradients stay even during interaction — their visual absence is very noticeable.
  if (q === 'high') {
    const glassGrad = ctx.createLinearGradient(x, y, x + w, y + h)
    glassGrad.addColorStop(0, withAlpha(fill, 0.55))
    glassGrad.addColorStop(1, withAlpha(fill, 0.25))
    ctx.fillStyle = glassGrad
  } else {
    ctx.fillStyle = withAlpha(fill, 0.42)
  }
  if (isRoundedRect) {
    roundedRectPath(ctx, x, y, w, h, rr)
    ctx.fill()
  } else {
    ctx.beginPath()
    ctx.arc(node.__x, node.__y, r, 0, Math.PI * 2)
    ctx.fill()
  }
  ctx.restore()

  // 3) Glow sprite: rim (outer)
  ctx.save()
  ctx.globalCompositeOperation = 'screen'
  const rimLineW = Math.max(px(2), r * 0.15)
  drawGlowSprite(ctx, {
    kind: 'rim',
    shape: isRoundedRect ? 'rounded-rect' : 'circle',
    x: node.__x,
    y: node.__y,
    w,
    h,
    r,
    rr,
    color: fill,
    blurPx: Math.max(px(2), r * 0.3) * glowK,
    lineWidthPx: rimLineW,
    composite: 'screen',
  })

  // Inner bright core stroke (white-ish)
  ctx.strokeStyle = withAlpha('#ffffff', 0.9)
  ctx.lineWidth = Math.max(px(1), r * 0.05)
  
  if (isRoundedRect) {
    roundedRectPath(ctx, x, y, w, h, rr)
    ctx.stroke()
  } else {
    ctx.beginPath()
    ctx.arc(node.__x, node.__y, r, 0, Math.PI * 2)
    ctx.stroke()
  }
  ctx.restore()

  // 4) Icons (Holographic Projections inside)
  drawNodeIcon(ctx, fill, node.__x, node.__y, w, h, r, isRoundedRect)

  // Optional badge pip if viz_badge_key is present (no semantics, just presence).
  if (node.viz_badge_key !== undefined && node.viz_badge_key !== null) {
    drawNodeBadge(ctx, node.__x, node.__y, r, px)
  }

  ctx.restore()
}
