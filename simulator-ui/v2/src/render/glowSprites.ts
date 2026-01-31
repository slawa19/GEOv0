import { withAlpha } from './color'
import { roundedRectPath } from './roundedRect'

type Shape = 'circle' | 'rounded-rect'

type GlowSpriteOpts = {
  shape: Shape
  // World-space coordinates in our renderer are scaled such that these sizes are effectively in px.
  w: number
  h: number
  r: number
  rr: number
  color: string
  kind: 'bloom' | 'rim'
  blurPx: number
  lineWidthPx: number
}

const cache = new Map<string, HTMLCanvasElement>()
const MAX_CACHE = 260

function q(v: number, step = 0.5) {
  if (!Number.isFinite(v)) return 0
  return Math.round(v / step) * step
}

function keyFor(o: GlowSpriteOpts) {
  return [
    o.kind,
    o.shape,
    o.color,
    q(o.w),
    q(o.h),
    q(o.r),
    q(o.rr),
    q(o.blurPx),
    q(o.lineWidthPx),
  ].join('|')
}

function createSpriteCanvas(pxW: number, pxH: number): HTMLCanvasElement {
  const c = document.createElement('canvas')
  c.width = Math.max(1, Math.floor(pxW))
  c.height = Math.max(1, Math.floor(pxH))
  return c
}

function getGlowSprite(opts: GlowSpriteOpts): HTMLCanvasElement {
  const key = keyFor(opts)
  const cached = cache.get(key)
  if (cached) {
    // Poor-man LRU: refresh insertion order.
    cache.delete(key)
    cache.set(key, cached)
    return cached
  }

  const blur = Math.max(0, opts.blurPx)
  const lw = Math.max(0, opts.lineWidthPx)

  // Keep generous padding: shadow blur expands beyond geometry.
  const pad = Math.ceil(blur * 2.2 + lw * 1.5 + 6)
  const baseW = opts.shape === 'circle' ? Math.max(1, opts.r * 2) : Math.max(1, opts.w)
  const baseH = opts.shape === 'circle' ? Math.max(1, opts.r * 2) : Math.max(1, opts.h)

  const cw = Math.ceil(baseW + pad * 2)
  const ch = Math.ceil(baseH + pad * 2)

  const c = createSpriteCanvas(cw, ch)
  const ctx = c.getContext('2d')
  if (!ctx) return c

  const cx = cw / 2
  const cy = ch / 2

  ctx.save()
  ctx.clearRect(0, 0, cw, ch)
  ctx.globalCompositeOperation = 'source-over'
  ctx.shadowColor = opts.color
  ctx.shadowBlur = blur

  // Use transparent fill/stroke so only shadow is drawn (cheap to reuse, expensive to compute each frame).
  if (opts.kind === 'bloom') {
    ctx.fillStyle = withAlpha(opts.color, 0)

    if (opts.shape === 'circle') {
      ctx.beginPath()
      ctx.arc(cx, cy, Math.max(0.1, opts.r * 0.8), 0, Math.PI * 2)
      ctx.fill()
    } else {
      const w = Math.max(0.1, opts.w - 4)
      const h = Math.max(0.1, opts.h - 4)
      const rr = Math.max(0, Math.min(opts.rr, Math.min(w, h) * 0.3))
      roundedRectPath(ctx, cx - w / 2, cy - h / 2, w, h, rr)
      ctx.fill()
    }
  } else {
    // rim
    ctx.strokeStyle = withAlpha(opts.color, 0.6)
    ctx.lineWidth = Math.max(0.1, lw)

    if (opts.shape === 'circle') {
      ctx.beginPath()
      ctx.arc(cx, cy, Math.max(0.1, opts.r), 0, Math.PI * 2)
      ctx.stroke()
    } else {
      const w = Math.max(0.1, opts.w)
      const h = Math.max(0.1, opts.h)
      const rr = Math.max(0, Math.min(opts.rr, Math.min(w, h) * 0.3))
      roundedRectPath(ctx, cx - w / 2, cy - h / 2, w, h, rr)
      ctx.stroke()
    }
  }

  ctx.restore()

  cache.set(key, c)
  if (cache.size > MAX_CACHE) {
    const first = cache.keys().next().value
    if (first) cache.delete(first)
  }

  return c
}

export function drawGlowSprite(
  ctx: CanvasRenderingContext2D,
  opts: GlowSpriteOpts & { x: number; y: number; composite?: GlobalCompositeOperation },
) {
  const sprite = getGlowSprite(opts)
  ctx.save()
  ctx.globalCompositeOperation = opts.composite ?? 'screen'

  // World-space in our renderer is effectively in px after invZoom sizing.
  const x = opts.x - sprite.width / 2
  const y = opts.y - sprite.height / 2
  ctx.drawImage(sprite, x, y)

  ctx.restore()
}
