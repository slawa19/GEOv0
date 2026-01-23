import type { GraphNode } from '../types'
import type { VizMapping } from '../vizMapping'

export type LayoutNode = GraphNode & { __x: number; __y: number }

function clamp01(v: number) {
  return Math.max(0, Math.min(1, v))
}

function withAlpha(color: string, alpha: number) {
  const a = clamp01(alpha)
  const c = String(color || '').trim()
  if (c.startsWith('rgba(') || c.startsWith('hsla(')) return c
  if (c.startsWith('#')) {
    const hex = c.slice(1)
    const isShort = hex.length === 3
    const isLong = hex.length === 6
    if (isShort || isLong) {
      const r = parseInt(isShort ? hex[0]! + hex[0]! : hex.slice(0, 2), 16)
      const g = parseInt(isShort ? hex[1]! + hex[1]! : hex.slice(2, 4), 16)
      const b = parseInt(isShort ? hex[2]! + hex[2]! : hex.slice(4, 6), 16)
      if (Number.isFinite(r) && Number.isFinite(g) && Number.isFinite(b)) {
        return `rgba(${r},${g},${b},${a})`
      }
    }
  }
  return c
}

export function sizeForNode(n: GraphNode): { w: number; h: number } {
  const s = n.viz_size
  const w = Math.max(6, Number(s?.w ?? (n.type === 'business' ? 14 : 10)))
  const h = Math.max(6, Number(s?.h ?? (n.type === 'business' ? 14 : 10)))
  return { w, h }
}

export function fillForNode(n: GraphNode, mapping: VizMapping): string {
  const key = String(n.viz_color_key ?? (n.type === 'business' ? 'business' : 'person'))
  const hit = mapping.node.color[key]
  return hit ? hit.fill : mapping.node.color.person.fill
}

export function drawNodeShape(ctx: CanvasRenderingContext2D, node: LayoutNode, opts: { mapping: VizMapping }) {
  const { mapping } = opts
  const fill = fillForNode(node, mapping)
  const { w, h } = sizeForNode(node)
  const isBusiness = String(node.type) === 'business'

  // IMPORTANT: node appearance follows the prototypes:
  // - business: emerald square
  // - person: blue circle
  // Additional semantics (debt bins, statuses) are still fixtures/backend-driven via viz_color_key.
  const r = Math.max(4, Math.min(w, h) / 2)

  const x = node.__x - w / 2
  const y = node.__y - h / 2
  const rr = Math.max(0, Math.min(4, Math.min(w, h) * 0.18))

  const roundedRectPath = (ctx2: CanvasRenderingContext2D, rx: number, ry: number, rw: number, rh: number, rad: number) => {
    const r2 = Math.max(0, Math.min(rad, Math.min(rw, rh) / 2))
    ctx2.beginPath()
    if (r2 <= 0.01) {
      ctx2.rect(rx, ry, rw, rh)
      return
    }
    ctx2.moveTo(rx + r2, ry)
    ctx2.lineTo(rx + rw - r2, ry)
    ctx2.quadraticCurveTo(rx + rw, ry, rx + rw, ry + r2)
    ctx2.lineTo(rx + rw, ry + rh - r2)
    ctx2.quadraticCurveTo(rx + rw, ry + rh, rx + rw - r2, ry + rh)
    ctx2.lineTo(rx + r2, ry + rh)
    ctx2.quadraticCurveTo(rx, ry + rh, rx, ry + rh - r2)
    ctx2.lineTo(rx, ry + r2)
    ctx2.quadraticCurveTo(rx, ry, rx + r2, ry)
    ctx2.closePath()
  }

  ctx.save()

  // 1) Soft bloom (underlay) - "Holographic" glow
  ctx.save()
  // Use screen blending for "light" effect against dark background
  ctx.globalCompositeOperation = 'screen'
  ctx.shadowColor = fill
  ctx.shadowBlur = r * 1.5 // Wide soft glow
  ctx.fillStyle = withAlpha(fill, 0.0) // Only shadow visible
  
  if (isBusiness) {
    roundedRectPath(ctx, x + 2, y + 2, w - 4, h - 4, rr)
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
  ctx.lineWidth = Math.max(2, r * 0.15)
  ctx.shadowColor = fill
  ctx.shadowBlur = Math.max(2, r * 0.3)
  
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
  ctx.lineWidth = Math.max(1, r * 0.05)
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
    const br = Math.max(1.6, r * 0.22)
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
