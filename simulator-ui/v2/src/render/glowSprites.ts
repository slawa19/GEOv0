import { withAlpha } from './color'
import { roundedRectPath } from './roundedRect'

import { quantize } from '../utils/math'
import { LruCache } from '../utils/lruCache'

type Shape = 'circle' | 'rounded-rect'

type NodeGlowSpriteOpts = {
  kind: 'bloom' | 'rim' | 'selection' | 'active'
  shape: Shape
  // World-space coordinates in our renderer are scaled such that these sizes are effectively in px.
  w: number
  h: number
  r: number
  rr: number
  color: string
  blurPx: number
  lineWidthPx: number
}

type FxGlowSpriteOpts =
  | {
      /**
       * Small radial glow used for spark/comet/edge-pulse heads.
       * Caller controls intensity via ctx.globalAlpha when drawing.
       */
      kind: 'fx-dot'
      color: string
      r: number
      blurPx: number
    }
  | {
      /**
       * Ring glow used for tx-impact.
       * The crisp white core stroke is drawn on-screen; the sprite provides only the glow.
       */
      kind: 'fx-ring'
      color: string
      r: number
      thicknessPx: number
      blurPx: number
    }
  | {
      /**
       * Radial bloom used for burst glow / clearing.
       * Caller controls intensity via ctx.globalAlpha when drawing.
       */
      kind: 'fx-bloom'
      color: string
      r: number
      blurPx: number
    }

type GlowSpriteOpts = NodeGlowSpriteOpts | FxGlowSpriteOpts

// Phase 1: node glow sprites are used widely (node bloom/rim + selection + active).
// Keep cache larger to avoid thrashing when zoom/size varies.
const MAX_CACHE = 500
const cache = new LruCache<string, HTMLCanvasElement>({ max: MAX_CACHE })

// Hard cap on offscreen sprite size to prevent pathological memory usage.
// ITEM-13: sanitize + clamp sprite canvas dimensions.
const MAX_SPRITE_PX = 4096

/**
 * Runtime cleanup hook for the module-level glow sprite cache.
 *
 * This does NOT change the existing cap/LRU semantics for normal operation.
 * It provides an explicit lifecycle-oriented way to drop all cached canvases,
 * e.g. on app unmount / scene teardown.
 */
export function resetGlowSpritesCache(): void {
  cache.clear()
}

function clampNum(v: unknown, min: number, max: number, fallback: number): number {
  const n = Number(v)
  if (!Number.isFinite(n)) return fallback
  if (n < min) return min
  if (n > max) return max
  return n
}

function sanitizeGlowOpts(opts: GlowSpriteOpts): GlowSpriteOpts {
  // Keep it allocation-friendly but safe: only called on cache miss.
  // We also sanitize BEFORE keying to avoid caching broken sprites.
  if (opts.kind === 'fx-dot' || opts.kind === 'fx-bloom') {
    return {
      ...opts,
      color: String(opts.color),
      r: clampNum(opts.r, 0, MAX_SPRITE_PX, 0),
      blurPx: clampNum(opts.blurPx, 0, MAX_SPRITE_PX, 0),
    }
  }
  if (opts.kind === 'fx-ring') {
    return {
      ...opts,
      color: String(opts.color),
      r: clampNum(opts.r, 0, MAX_SPRITE_PX, 0),
      thicknessPx: clampNum(opts.thicknessPx, 0, MAX_SPRITE_PX, 0),
      blurPx: clampNum(opts.blurPx, 0, MAX_SPRITE_PX, 0),
    }
  }

  // Node glow sprites.
  return {
    ...opts,
    color: String(opts.color),
    w: clampNum(opts.w, 0, MAX_SPRITE_PX, 0),
    h: clampNum(opts.h, 0, MAX_SPRITE_PX, 0),
    r: clampNum(opts.r, 0, MAX_SPRITE_PX, 0),
    rr: clampNum(opts.rr, 0, MAX_SPRITE_PX, 0),
    blurPx: clampNum(opts.blurPx, 0, MAX_SPRITE_PX, 0),
    lineWidthPx: clampNum(opts.lineWidthPx, 0, MAX_SPRITE_PX, 0),
  }
}

function keyFor(o: GlowSpriteOpts) {
  if (o.kind === 'fx-dot' || o.kind === 'fx-bloom') {
    return [o.kind, o.color, quantize(o.r), quantize(o.blurPx)].join('|')
  }
  if (o.kind === 'fx-ring') {
    return [o.kind, o.color, quantize(o.r), quantize(o.thicknessPx), quantize(o.blurPx)].join('|')
  }

  return [
    o.kind,
    o.shape,
    o.color,
    quantize(o.w),
    quantize(o.h),
    quantize(o.r),
    quantize(o.rr),
    quantize(o.blurPx),
    quantize(o.lineWidthPx),
  ].join('|')
}

function createSpriteCanvas(pxW: number, pxH: number): HTMLCanvasElement {
  const c = document.createElement('canvas')
  c.width = Math.max(1, Math.floor(pxW))
  c.height = Math.max(1, Math.floor(pxH))
  return c
}

function getGlowSprite(opts: GlowSpriteOpts): HTMLCanvasElement {
  const o = sanitizeGlowOpts(opts)
  const key = keyFor(o)
  const cached = cache.get(key)
  if (cached) {
    return cached
  }

  const blur = Math.max(0, o.blurPx)
  let lw = 0
  if ('lineWidthPx' in o) {
    lw = Math.max(0, o.lineWidthPx)
  } else if (o.kind === 'fx-ring') {
    lw = Math.max(0, o.thicknessPx)
  }

  // Keep generous padding: shadow blur expands beyond geometry.
  const pad = Math.ceil(blur * 2.2 + lw * 1.5 + 6)

  const baseW =
    'shape' in o ? (o.shape === 'circle' ? Math.max(1, o.r * 2) : Math.max(1, o.w)) : Math.max(1, o.r * 2)
  const baseH =
    'shape' in o ? (o.shape === 'circle' ? Math.max(1, o.r * 2) : Math.max(1, o.h)) : Math.max(1, o.r * 2)

  const cw = Math.max(1, Math.min(MAX_SPRITE_PX, Math.ceil(baseW + pad * 2)))
  const ch = Math.max(1, Math.min(MAX_SPRITE_PX, Math.ceil(baseH + pad * 2)))

  const c = createSpriteCanvas(cw, ch)
  const ctx = c.getContext('2d')
  if (!ctx) return c

  const cx = cw / 2
  const cy = ch / 2

  ctx.save()
  ctx.clearRect(0, 0, cw, ch)
  ctx.globalCompositeOperation = 'source-over'
  ctx.shadowColor = o.color
  ctx.shadowBlur = blur

  // NOTE: Shadow blur is allowed only here (offscreen generation).
  // We intentionally draw geometry (sometimes as black) so the sprite can be composited with `screen`:
  // black pixels become neutral under screen blending, while the colored shadow remains visible.
  if (o.kind === 'fx-dot') {
    // Pass 1: glow halo (shadow-only).
    ctx.fillStyle = withAlpha(o.color, 0)
    ctx.beginPath()
    ctx.arc(cx, cy, Math.max(0.1, o.r * 0.9), 0, Math.PI * 2)
    ctx.fill()

    // Pass 2: crisp core (no blur) so the dot doesn't look washed-out.
    ctx.shadowBlur = 0
    ctx.fillStyle = withAlpha(o.color, 0.95)
    ctx.beginPath()
    ctx.arc(cx, cy, Math.max(0.1, o.r * 0.55), 0, Math.PI * 2)
    ctx.fill()
  }

  if (o.kind === 'fx-ring') {
    // Glow-only ring: draw neutral (black) geometry so only the shadow is visible under `screen`.
    ctx.strokeStyle = '#000000'
    ctx.lineWidth = Math.max(0.1, lw)
    ctx.beginPath()
    ctx.arc(cx, cy, Math.max(0.1, o.r), 0, Math.PI * 2)
    ctx.stroke()
  }

  if (o.kind === 'fx-bloom') {
    // Shadow-only bloom + faint core fill.
    ctx.fillStyle = withAlpha(o.color, 0)
    ctx.beginPath()
    ctx.arc(cx, cy, Math.max(0.1, o.r * 0.8), 0, Math.PI * 2)
    ctx.fill()

    ctx.shadowBlur = 0
    ctx.fillStyle = withAlpha(o.color, 0.22)
    ctx.beginPath()
    ctx.arc(cx, cy, Math.max(0.1, o.r * 0.45), 0, Math.PI * 2)
    ctx.fill()
  }

  if (o.kind === 'bloom') {
    // Only shadow: transparent fill.
    ctx.fillStyle = withAlpha(o.color, 0)

    if (o.shape === 'circle') {
      ctx.beginPath()
      ctx.arc(cx, cy, Math.max(0.1, o.r * 0.8), 0, Math.PI * 2)
      ctx.fill()
    } else {
      const w = Math.max(0.1, o.w - 4)
      const h = Math.max(0.1, o.h - 4)
      const rr = Math.max(0, Math.min(o.rr, Math.min(w, h) * 0.3))
      roundedRectPath(ctx, cx - w / 2, cy - h / 2, w, h, rr)
      ctx.fill()
    }
  }

  if (o.kind === 'rim') {
    // Rim shadow + faint colored stroke.
    ctx.strokeStyle = withAlpha(o.color, 0.6)
    ctx.lineWidth = Math.max(0.1, lw)

    if (o.shape === 'circle') {
      ctx.beginPath()
      ctx.arc(cx, cy, Math.max(0.1, o.r), 0, Math.PI * 2)
      ctx.stroke()
    } else {
      const w = Math.max(0.1, o.w)
      const h = Math.max(0.1, o.h)
      const rr = Math.max(0, Math.min(o.rr, Math.min(w, h) * 0.3))
      roundedRectPath(ctx, cx - w / 2, cy - h / 2, w, h, rr)
      ctx.stroke()
    }
  }

  if (o.kind === 'selection') {
    // BaseGraph historically used a 2-pass blur (outer + core) with black stroke in `screen`.
    // We bake both passes into one sprite for reuse.
    const stroke = () => {
      ctx.strokeStyle = '#000000'
      ctx.lineWidth = Math.max(0.1, lw)
      if (o.shape === 'circle') {
        ctx.beginPath()
        ctx.arc(cx, cy, Math.max(0.1, o.r), 0, Math.PI * 2)
        ctx.stroke()
      } else {
        const w = Math.max(0.1, o.w)
        const h = Math.max(0.1, o.h)
        const rr = Math.max(0, Math.min(o.rr, Math.min(w, h) * 0.3))
        roundedRectPath(ctx, cx - w / 2, cy - h / 2, w, h, rr)
        ctx.stroke()
      }
    }

    // Pass 1: outer diffuse glow
    ctx.shadowBlur = blur
    stroke()
    // Pass 2: core intensity closer to the node (ratio chosen to match previous on-screen values ~1.2r vs 0.4r)
    ctx.shadowBlur = blur * 0.33
    stroke()
  }

  if (o.kind === 'active') {
    // Active node glow: single-pass blur with black stroke in screen blending.
    ctx.strokeStyle = '#000000'
    ctx.lineWidth = Math.max(0.1, lw)
    if (o.shape === 'circle') {
      ctx.beginPath()
      ctx.arc(cx, cy, Math.max(0.1, o.r), 0, Math.PI * 2)
      ctx.stroke()
    } else {
      const w = Math.max(0.1, o.w)
      const h = Math.max(0.1, o.h)
      const rr = Math.max(0, Math.min(o.rr, Math.min(w, h) * 0.3))
      roundedRectPath(ctx, cx - w / 2, cy - h / 2, w, h, rr)
      ctx.stroke()
    }
  }

  ctx.restore()

  cache.set(key, c)

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

// Exposed for unit tests (cache keys / quantization invariants / LRU eviction).
export const __testing = {
  q: quantize,
  keyFor,
  MAX_CACHE,
  MAX_SPRITE_PX,
  _cacheClear: resetGlowSpritesCache,
  _cacheHas: (key: string) => cache.has(key),
  _cacheKeys: () => cache.keys(),
  _getGlowSprite: getGlowSprite,
}
