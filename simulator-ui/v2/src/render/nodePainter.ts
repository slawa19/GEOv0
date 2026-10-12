import type { LayoutNode } from '../types/layout'
import { classifyNode, fillOf, type NodeSign, type VizMapping } from '../vizMapping'
import { withAlpha } from './color'
import {
  NODE_BODY_FILL_ALPHA_HI_A,
  NODE_BODY_FILL_ALPHA_HI_B,
  NODE_BODY_FILL_ALPHA_LO,
  NODE_GLINT_BADGE_ALPHA,
  NODE_GLINT_COLOR_HEX,
  NODE_GLINT_INNER_STROKE_ALPHA,
  NODE_GLOW_K_BY_QUALITY,
} from './fxConfig'
import { getLinearGradient2Stops } from './gradientCache'
import { drawGlowSprite } from './glowSprites'
import { getNodeBaseGeometry } from './nodeGeometry'
import { roundedRectPath } from './roundedRect'

/* ------------------------------------------------------------------ */
/*  Sign glyph, badge                                                  */
/* ------------------------------------------------------------------ */

/** The sign glyph is about this tall and wide on screen, in CSS px, whatever the zoom and the quality. */
export const NODE_SIGN_SIZE_PX = 6
const NODE_SIGN_LINE_PX = 1.6
const NODE_SIGN_INK_ALPHA = 0.95
/** Frozen outline: dash and gap, and the line width, in CSS px. */
const NODE_FROZEN_DASH_PX = [3, 2.5] as const
const NODE_FROZEN_OUTLINE_PX = 1.6
/** Hub ring: its distance from the node's outline and its line width, in CSS px. */
const NODE_HUB_RING_GAP_PX = 3.5
const NODE_HUB_RING_LINE_PX = 1.4

/**
 * The sign of the node's net position, in the middle of the node: MINUS, PLUS, ZERO or a question mark (unknown).
 * 039 slice B: it stands where the person / building pictogram was, and it is the NON-COLOUR cue of the role - so it
 * is drawn whenever the node is, at every zoom, quality and label LOD. Short vector strokes (no text, no font):
 * one path, one `stroke()`. The ink is the glint white: the body under it is the role colour at reduced alpha over
 * a dark page, against which white holds 5:1 and more for every data colour (measured, `visual-language.md`).
 */
function drawNodeSign(
  ctx: CanvasRenderingContext2D,
  sign: NodeSign,
  cx: number,
  cy: number,
  sizePx: number,
  px: (v: number) => number,
) {
  const h = px(sizePx) / 2 // half of the glyph box
  ctx.save()
  ctx.globalCompositeOperation = 'source-over'
  ctx.strokeStyle = withAlpha(NODE_GLINT_COLOR_HEX, NODE_SIGN_INK_ALPHA)
  ctx.lineWidth = px(NODE_SIGN_LINE_PX * (sizePx / NODE_SIGN_SIZE_PX))
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  ctx.setLineDash([])
  ctx.beginPath()
  if (sign === '+') {
    ctx.moveTo(cx - h, cy)
    ctx.lineTo(cx + h, cy)
    ctx.moveTo(cx, cy - h)
    ctx.lineTo(cx, cy + h)
  } else if (sign === '0') {
    // A narrow oval: told from a plain ring (the node outline) by being taller than wide.
    const w = h * 0.68
    ctx.moveTo(cx, cy - h)
    ctx.quadraticCurveTo(cx + w, cy - h, cx + w, cy)
    ctx.quadraticCurveTo(cx + w, cy + h, cx, cy + h)
    ctx.quadraticCurveTo(cx - w, cy + h, cx - w, cy)
    ctx.quadraticCurveTo(cx - w, cy - h, cx, cy - h)
  } else if (sign === '?') {
    // Hook: three quarters of a small circle, a short stem, then the dot as a zero-length round-capped segment.
    const r = h * 0.5
    ctx.arc(cx, cy - h + r, r, Math.PI, Math.PI * 0.5)
    ctx.lineTo(cx, cy + h * 0.28)
    ctx.moveTo(cx, cy + h)
    ctx.lineTo(cx, cy + h)
  } else {
    // MINUS SIGN.
    ctx.moveTo(cx - h, cy)
    ctx.lineTo(cx + h, cy)
  }
  ctx.stroke()
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

  // 5) The sign of the net position, in place of the old pictogram. Never skipped: see `drawNodeSign`.
  //    About 6 CSS px; smaller only when the node itself is smaller than the glyph (the sizing floor is 6 px).
  const signPx = Math.min(NODE_SIGN_SIZE_PX, (Math.min(w, h) / invZ) * 0.6)
  drawNodeSign(ctx, look.sign, node.__x, node.__y, signPx, px)

  // Optional badge pip if viz_badge_key is present (no semantics, just presence).
  if (node.viz_badge_key !== undefined && node.viz_badge_key !== null) {
    drawNodeBadge(ctx, node.__x, node.__y, r, px, mapping.fx.glint)
  }

  ctx.restore()
}
