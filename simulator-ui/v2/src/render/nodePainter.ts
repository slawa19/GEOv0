import type { GraphNode } from '../types'
import type { LayoutNode } from '../types/layout'
import type { VizMapping } from '../vizMapping'
import { withAlpha } from './color'
import { roundedRectPath } from './roundedRect'

export type { LayoutNode } from '../types/layout'

const sizeCache = new WeakMap<GraphNode, { key: string; size: { w: number; h: number } }>()

export function sizeForNode(n: GraphNode): { w: number; h: number } {
  const s = n.viz_size
  const key = `${String(n.type)}:${String(s?.w ?? '')}:${String(s?.h ?? '')}`
  const cached = sizeCache.get(n)
  if (cached && cached.key === key) return cached.size

  const w = Math.max(6, Number(s?.w ?? (n.type === 'business' ? 14 : 10)))
  const h = Math.max(6, Number(s?.h ?? (n.type === 'business' ? 14 : 10)))
  const size = { w, h }
  sizeCache.set(n, { key, size })
  return size
}

export function fillForNode(n: GraphNode, mapping: VizMapping): string {
  const key = String(n.viz_color_key ?? (n.type === 'business' ? 'business' : 'person'))
  const hit = mapping.node.color[key]
  if (hit) return hit.fill
  // Fallback for unknown keys (e.g. debt-0): use type-based color per vizMapping contract.
  const typeKey = n.type === 'business' ? 'business' : 'person'
  return mapping.node.color[typeKey]?.fill ?? mapping.node.color.person.fill
}

export function drawNodeShape(
  ctx: CanvasRenderingContext2D,
  node: LayoutNode,
  opts: { mapping: VizMapping; cameraZoom?: number; quality?: 'low' | 'med' | 'high'; dragMode?: boolean },
) {
  const { mapping } = opts
  const z = Math.max(0.01, Number(opts.cameraZoom ?? 1))
  const invZ = 1 / z
  const px = (v: number) => v * invZ
  const q = opts.quality ?? 'high'
  const blurK = q === 'high' ? 1 : q === 'med' ? 0.75 : 0.55
  const fill = fillForNode(node, mapping)
  const { w: w0, h: h0 } = sizeForNode(node)
  const w = w0 * invZ
  const h = h0 * invZ
  const isBusiness = String(node.type) === 'business'

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
    if (isBusiness) {
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
    ctx.restore()
    return
  }

  ctx.save()

  // 1) Soft bloom (underlay) - "Holographic" glow
  ctx.save()
  // Use screen blending for "light" effect against dark background
  ctx.globalCompositeOperation = 'screen'
  ctx.shadowColor = fill
  ctx.shadowBlur = r * 1.5 * blurK // Wide soft glow
  ctx.fillStyle = withAlpha(fill, 0.0) // Only shadow visible
  
  if (isBusiness) {
    roundedRectPath(ctx, x + px(2), y + px(2), w - px(4), h - px(4), rr)
    ctx.fill()
  } else {
    ctx.beginPath()
    ctx.arc(node.__x, node.__y, r * 0.8, 0, Math.PI * 2)
    ctx.fill()
  }
  ctx.restore()

  // 2) Body fill - Semi-transparent glass (Darker/More Solid now)
  ctx.save()
  ctx.globalCompositeOperation = 'source-over'
  // Linear gradient for "deep holographic" look
  // From moderately opaque at top-left to darker at bottom-right, but visible enough to hide stars behind it.
  const glassGrad = ctx.createLinearGradient(x, y, x + w, y + h)
  glassGrad.addColorStop(0, withAlpha(fill, 0.55)) 
  glassGrad.addColorStop(1, withAlpha(fill, 0.25))
  
  ctx.fillStyle = glassGrad
  if (isBusiness) {
    roundedRectPath(ctx, x, y, w, h, rr)
    ctx.fill()
  } else {
    ctx.beginPath()
    ctx.arc(node.__x, node.__y, r, 0, Math.PI * 2)
    ctx.fill()
  }
  ctx.restore()

  // 3) Neon Rim - The define feature
  ctx.save()
  ctx.globalCompositeOperation = 'screen'
  // Double stroke for "core + glow" effect
  
  // Outer glowy stroke
  ctx.strokeStyle = withAlpha(fill, 0.6)
  ctx.lineWidth = Math.max(px(2), r * 0.15)
  ctx.shadowColor = fill
  ctx.shadowBlur = Math.max(px(2), r * 0.3) * blurK
  
  if (isBusiness) {
    roundedRectPath(ctx, x, y, w, h, rr)
    ctx.stroke()
  } else {
    ctx.beginPath()
    ctx.arc(node.__x, node.__y, r, 0, Math.PI * 2)
    ctx.stroke()
  }

  // Inner bright core stroke (white-ish)
  ctx.strokeStyle = withAlpha('#ffffff', 0.9)
  ctx.lineWidth = Math.max(px(1), r * 0.05)
  ctx.shadowBlur = 0 // Sharp core
  
  if (isBusiness) {
    roundedRectPath(ctx, x, y, w, h, rr)
    ctx.stroke()
  } else {
    ctx.beginPath()
    ctx.arc(node.__x, node.__y, r, 0, Math.PI * 2)
    ctx.stroke()
  }
  ctx.restore()

  // 4) Icons (Holographic Projections inside)
  ctx.save()
  ctx.globalCompositeOperation = 'source-over'
  ctx.fillStyle = withAlpha(fill, 0.95) // Solid bright core for the icon

  if (!isBusiness) {
    // PERSON: "Pawn" / User silhouette
    const s = r * 0.045
    ctx.translate(node.__x, node.__y + r * 0.08)
    
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
    // Let's draw a stylized high-rise building
    const s = Math.min(w, h) * 0.024
    ctx.translate(node.__x, node.__y + h * 0.05)
    
    ctx.beginPath()
    // Main tower
    const bw = 12 * s
    const bh = 16 * s
    ctx.rect(-bw / 2, -bh / 2, bw, bh)
    
    // Roof detail (antenna or stepped)
    ctx.rect(-bw / 2 + 2 * s, -bh / 2 - 2 * s, bw - 4 * s, 2 * s) // Top block
    ctx.rect(-1 * s, -bh / 2 - 5 * s, 2 * s, 3 * s) // Antenna
    
    // Windows (cutout effect by drawing transparent/dark over fill? 
    // No, simpler is just solid shape acting as the "core")
    ctx.fill()
    
    // Window lines (cutouts)
    ctx.globalCompositeOperation = 'destination-out'
    ctx.fillStyle = '#000000'
    const winW = 2.5 * s
    const winH = 2.5 * s
    const gap = 1.5 * s
    // 2 columns of windows
    for (let row = -1; row <= 1; row++) {
      ctx.fillRect(-bw / 2 + gap + 0.5 * s, row * (winH + gap) - 1 * s, winW, winH)
      ctx.fillRect(0 + gap - 0.5 * s, row * (winH + gap) - 1 * s, winW, winH)
    }
  }
  ctx.restore()

  // Optional badge pip if viz_badge_key is present (no semantics, just presence).
  if (node.viz_badge_key !== undefined && node.viz_badge_key !== null) {
    const br = Math.max(px(1.6), r * 0.22)
    ctx.save()
    ctx.globalCompositeOperation = 'lighter'
    ctx.fillStyle = withAlpha('#ffffff', 0.85)
    ctx.beginPath()
    ctx.arc(node.__x + r * 0.72, node.__y - r * 0.72, br, 0, Math.PI * 2)
    ctx.fill()
    ctx.restore()
  }

  ctx.restore()
}
