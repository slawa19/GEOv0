import type { GraphNode } from '../types'
import type { VizMapping } from '../vizMapping'

export type LayoutNode = GraphNode & { __x: number; __y: number }

function clamp01(v: number) {
  return Math.max(0, Math.min(1, v))
}

function hash32(s: string) {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
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

  ctx.save()

  // Conservative “soft bloom” only (no invented skins). Fully deterministic.
  ctx.globalCompositeOperation = 'source-over'
  ctx.globalAlpha = 0.35
  ctx.fillStyle = 'rgba(0,0,0,0.55)'
  if (isBusiness) {
    ctx.fillRect(node.__x - w / 2 + r * 0.10, node.__y - h / 2 + r * 0.16, w, h)
  } else {
    ctx.beginPath()
    ctx.arc(node.__x + r * 0.10, node.__y + r * 0.16, r * 1.05, 0, Math.PI * 2)
    ctx.fill()
  }
  ctx.globalAlpha = 1

  ctx.fillStyle = fill
  if (isBusiness) {
    // Square (optionally with tiny rounding to avoid pixel shimmer).
    const x = node.__x - w / 2
    const y = node.__y - h / 2
    const rr = Math.max(0, Math.min(3, Math.min(w, h) * 0.18))
    if (rr <= 0.01) {
      ctx.fillRect(x, y, w, h)
    } else {
      const r2 = rr
      ctx.beginPath()
      ctx.moveTo(x + r2, y)
      ctx.lineTo(x + w - r2, y)
      ctx.quadraticCurveTo(x + w, y, x + w, y + r2)
      ctx.lineTo(x + w, y + h - r2)
      ctx.quadraticCurveTo(x + w, y + h, x + w - r2, y + h)
      ctx.lineTo(x + r2, y + h)
      ctx.quadraticCurveTo(x, y + h, x, y + h - r2)
      ctx.lineTo(x, y + r2)
      ctx.quadraticCurveTo(x, y, x + r2, y)
      ctx.closePath()
      ctx.fill()
    }
  } else {
    // Circle.
    ctx.beginPath()
    ctx.arc(node.__x, node.__y, r, 0, Math.PI * 2)
    ctx.fill()
  }

  // Gentle edge highlight.
  ctx.strokeStyle = withAlpha('#ffffff', 0.18)
  ctx.lineWidth = Math.max(0.8, r * 0.10)
  if (isBusiness) {
    ctx.strokeRect(node.__x - w / 2, node.__y - h / 2, w, h)
  } else {
    ctx.beginPath()
    ctx.arc(node.__x, node.__y, r * 0.98, 0, Math.PI * 2)
    ctx.stroke()
  }

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
