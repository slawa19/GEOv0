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

  // 1) Soft bloom (very subtle, deterministic; no pulsing)
  ctx.save()
  ctx.globalCompositeOperation = 'lighter'
  ctx.globalAlpha = 0.10
  ctx.fillStyle = fill
  if (isBusiness) {
    roundedRectPath(ctx, x - r * 0.55, y - r * 0.55, w + r * 1.1, h + r * 1.1, rr + r * 0.35)
    ctx.fill()
  } else {
    ctx.beginPath()
    ctx.arc(node.__x, node.__y, r * 1.85, 0, Math.PI * 2)
    ctx.fill()
  }
  ctx.restore()

  // 2) Drop shadow (kept tight to avoid looking like planets)
  ctx.save()
  ctx.globalCompositeOperation = 'source-over'
  ctx.shadowColor = 'rgba(0,0,0,0.55)'
  ctx.shadowBlur = Math.max(4, r * 0.95)
  ctx.shadowOffsetX = Math.max(1, r * 0.10)
  ctx.shadowOffsetY = Math.max(1, r * 0.14)
  ctx.fillStyle = withAlpha('#000000', 0.18)
  if (isBusiness) {
    roundedRectPath(ctx, x, y, w, h, rr)
    ctx.fill()
  } else {
    ctx.beginPath()
    ctx.arc(node.__x, node.__y, r, 0, Math.PI * 2)
    ctx.fill()
  }
  ctx.restore()

  // 3) Body with gentle shading
  if (isBusiness) {
    const grad = ctx.createLinearGradient(x, y, x + w, y + h)
    grad.addColorStop(0, withAlpha('#ffffff', 0.22))
    grad.addColorStop(0.22, fill)
    grad.addColorStop(1, withAlpha('#000000', 0.22))
    ctx.fillStyle = grad
    roundedRectPath(ctx, x, y, w, h, rr)
    ctx.fill()
  } else {
    const gx = node.__x - r * 0.25
    const gy = node.__y - r * 0.30
    const grad = ctx.createRadialGradient(gx, gy, Math.max(1, r * 0.10), node.__x, node.__y, r * 1.10)
    grad.addColorStop(0, withAlpha('#ffffff', 0.28))
    grad.addColorStop(0.25, fill)
    grad.addColorStop(1, withAlpha('#000000', 0.18))
    ctx.fillStyle = grad
    ctx.beginPath()
    ctx.arc(node.__x, node.__y, r, 0, Math.PI * 2)
    ctx.fill()
  }

  // 4) Crisp rim
  ctx.save()
  ctx.globalCompositeOperation = 'lighter'
  ctx.strokeStyle = withAlpha('#ffffff', 0.22)
  ctx.lineWidth = Math.max(0.9, r * 0.11)
  if (isBusiness) {
    roundedRectPath(ctx, x + 0.5, y + 0.5, w - 1, h - 1, rr)
    ctx.stroke()
  } else {
    ctx.beginPath()
    ctx.arc(node.__x, node.__y, r * 0.98, 0, Math.PI * 2)
    ctx.stroke()
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
