import type { LayoutNode } from '../types/layout'
import { classifyNode, fillOf, type VizMapping } from '../vizMapping'
import { withAlpha } from './color'
import {
  NODE_BODY_FILL_ALPHA_HI_A,
  NODE_BODY_FILL_ALPHA_HI_B,
  NODE_BODY_FILL_ALPHA_LO,
  NODE_GLINT_BADGE_ALPHA,
  NODE_GLINT_COLOR_HEX,
  NODE_GLINT_INNER_STROKE_ALPHA,
  NODE_GLOW_K_BY_QUALITY,
  OPAQUE_BLACK_HEX,
} from './fxConfig'
import { getLinearGradient2Stops } from './gradientCache'
import { drawGlowSprite } from './glowSprites'
import { getNodeBaseGeometry } from './nodeGeometry'
import { roundedRectPath } from './roundedRect'

/* ------------------------------------------------------------------ */
/*  Lightweight icon / badge helpers                                  */
/* ------------------------------------------------------------------ */

/**
 * Draw the node's inner icon (person silhouette or building silhouette).
 * Cost ≈ a few arc/rect calls, no blur/gradient.
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
    ctx.fillStyle = OPAQUE_BLACK_HEX
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

/** Frozen outline: dash and gap, and the line width, in CSS px. */
const NODE_FROZEN_DASH_PX = [3, 2.5] as const
const NODE_FROZEN_OUTLINE_PX = 1.6
/** Hub ring: its distance from the node's outline and its line width, in CSS px. */
const NODE_HUB_RING_GAP_PX = 3.5
const NODE_HUB_RING_LINE_PX = 1.4

/**
 * Draw optional badge pip. Cost ≈ one arc + fill — negligible.
 */
function drawNodeBadge(
  ctx: CanvasRenderingContext2D,
  cx: number,
  cy: number,
  r: number,
  px: (v: number) => number,
  glint: string,
) {
  const br = Math.max(px(1.6), r * 0.22)
  ctx.save()
  ctx.globalCompositeOperation = 'lighter'
  ctx.fillStyle = withAlpha(glint, NODE_GLINT_BADGE_ALPHA)
  ctx.beginPath()
  ctx.arc(cx + r * 0.72, cy - r * 0.72, br, 0, Math.PI * 2)
  ctx.fill()
  ctx.restore()
}

export function drawNodeShape(
  ctx: CanvasRenderingContext2D,
  node: LayoutNode,
  opts: {
    mapping: VizMapping
    cameraZoom?: number
    quality?: 'low' | 'med' | 'high'
  },
) {
  const { mapping } = opts
  const z = Math.max(0.01, Number(opts.cameraZoom ?? 1))
  const invZ = 1 / z
  const px = (v: number) => v * invZ
  const q = opts.quality ?? 'high'
  // Sprite glow quality scaling.
  // (Even in low, we keep a modest glow for readability.)
  const glowK = NODE_GLOW_K_BY_QUALITY[q] ?? 1
  // 039 slice B: ONE classifier says what the node is; the fill is its role colour, or the freeze colour.
  const look = classifyNode(node)
  const fill = fillOf(look, mapping.node.palette)
  const geom = getNodeBaseGeometry(node, invZ)
  const w = geom.w
  const h = geom.h
  const isRoundedRect = geom.shape === 'rounded-rect'

  // The SHAPE and the SIZE are the wire's (`viz_shape_key`, `viz_size`); the COLOUR is not (`viz_color_key` is ignored).
  const r = Math.max(px(4), Math.min(w, h) / 2)

  const x = node.__x - w / 2
  const y = node.__y - h / 2
  const rr = geom.rr

  // Phase 6: node painting should use a single path (drag & non-drag).
  // TODO(phase-6.3): if perf becomes an issue, consider a *minimal* drag fallback
  // that only skips the bloom sprite (keep fill/rim/core/icon/badge) instead of
  // a separate "cheap shape" fast-path.

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
  if (q === 'high') {
    ctx.fillStyle = getLinearGradient2Stops(
      ctx,
      x,
      y,
      x + w,
      y + h,
      0,
      withAlpha(fill, NODE_BODY_FILL_ALPHA_HI_A),
      1,
      withAlpha(fill, NODE_BODY_FILL_ALPHA_HI_B),
    )
  } else {
    ctx.fillStyle = withAlpha(fill, NODE_BODY_FILL_ALPHA_LO)
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

  // 3) Glow sprite: rim (outer). A frozen node has NO continuous rim: its outline is the dashed stroke below, and a
  //    solid ring under the dashes would make them unreadable.
  ctx.save()
  ctx.globalCompositeOperation = 'screen'
  const rimLineW = Math.max(px(2), r * 0.15)
  if (!look.frozen) drawGlowSprite(ctx, {
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

  // Inner bright core stroke (white-ish). Frozen / suspended: the same stroke DASHED and a little heavier - the
  // non-colour cue of a freeze. The dash is set inside this save/restore pair, so no later stroke inherits it.
  ctx.strokeStyle = withAlpha(NODE_GLINT_COLOR_HEX, NODE_GLINT_INNER_STROKE_ALPHA)
  ctx.lineWidth = look.frozen ? Math.max(px(NODE_FROZEN_OUTLINE_PX), r * 0.08) : Math.max(px(1), r * 0.05)
  ctx.setLineDash(look.frozen ? [px(NODE_FROZEN_DASH_PX[0]), px(NODE_FROZEN_DASH_PX[1])] : [])

  if (isRoundedRect) {
    roundedRectPath(ctx, x, y, w, h, rr)
    ctx.stroke()
  } else {
    ctx.beginPath()
    ctx.arc(node.__x, node.__y, r, 0, Math.PI * 2)
    ctx.stroke()
  }
  ctx.restore()

  // 4) Hub mark: a thin extra ring OUTSIDE the outline, only for `type === 'hub'` (`classifyNode`). The size of
  //    the node is not changed; the ring is a stroke, never a fill.
  if (look.hub) {
    const gap = px(NODE_HUB_RING_GAP_PX)
    ctx.save()
    ctx.globalCompositeOperation = 'source-over'
    ctx.setLineDash([])
    ctx.strokeStyle = mapping.node.palette.hub
    ctx.lineWidth = px(NODE_HUB_RING_LINE_PX)
    if (isRoundedRect) {
      roundedRectPath(ctx, x - gap, y - gap, w + gap * 2, h + gap * 2, rr + gap)
    } else {
      ctx.beginPath()
      ctx.arc(node.__x, node.__y, r + gap, 0, Math.PI * 2)
    }
    ctx.stroke()
    ctx.restore()
  }

  // 5) Icons (Holographic Projections inside)
  drawNodeIcon(ctx, fill, node.__x, node.__y, w, h, r, isRoundedRect)

  // Optional badge pip if viz_badge_key is present (no semantics, just presence).
  if (node.viz_badge_key !== undefined && node.viz_badge_key !== null) {
    drawNodeBadge(ctx, node.__x, node.__y, r, px, mapping.fx.glint)
  }

  ctx.restore()
}
