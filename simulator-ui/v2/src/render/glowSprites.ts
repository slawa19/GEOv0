import { withAlpha } from './color'
import { roundedRectPath } from './roundedRect'

import { quantize } from '../utils/math'

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

const cache = new Map<string, HTMLCanvasElement>()
// Phase 1: node glow sprites are used widely (node bloom/rim + selection + active).
// Keep cache larger to avoid thrashing when zoom/size varies.
const MAX_CACHE = 500

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
  const key = keyFor(opts)
  const cached = cache.get(key)
  if (cached) {
    // Poor-man LRU: refresh insertion order.
    cache.delete(key)
    cache.set(key, cached)
    return cached
  }

  const blur = Math.max(0, opts.blurPx)
  let lw = 0
  if ('lineWidthPx' in opts) {
    lw = Math.max(0, opts.lineWidthPx)
  } else if (opts.kind === 'fx-ring') {
    lw = Math.max(0, opts.thicknessPx)
  }

  // Keep generous padding: shadow blur expands beyond geometry.
  const pad = Math.ceil(blur * 2.2 + lw * 1.5 + 6)

  const baseW =
    'shape' in opts
      ? opts.shape === 'circle'
        ? Math.max(1, opts.r * 2)
        : Math.max(1, opts.w)
      : Math.max(1, opts.r * 2)
  const baseH =
    'shape' in opts
      ? opts.shape === 'circle'
        ? Math.max(1, opts.r * 2)
        : Math.max(1, opts.h)
      : Math.max(1, opts.r * 2)

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

  // NOTE: Shadow blur is allowed only here (offscreen generation).
  // We intentionally draw geometry (sometimes as black) so the sprite can be composited with `screen`:
  // black pixels become neutral under screen blending, while the colored shadow remains visible.
  if (opts.kind === 'fx-dot') {
    // Pass 1: glow halo (shadow-only).
    ctx.fillStyle = withAlpha(opts.color, 0)
    ctx.beginPath()
    ctx.arc(cx, cy, Math.max(0.1, opts.r * 0.9), 0, Math.PI * 2)
    ctx.fill()

    // Pass 2: crisp core (no blur) so the dot doesn't look washed-out.
    ctx.shadowBlur = 0
    ctx.fillStyle = withAlpha(opts.color, 0.95)
    ctx.beginPath()
    ctx.arc(cx, cy, Math.max(0.1, opts.r * 0.55), 0, Math.PI * 2)
    ctx.fill()
  }

  if (opts.kind === 'fx-ring') {
    // Glow-only ring: draw neutral (black) geometry so only the shadow is visible under `screen`.
    ctx.strokeStyle = '#000000'
    ctx.lineWidth = Math.max(0.1, lw)
    ctx.beginPath()
    ctx.arc(cx, cy, Math.max(0.1, opts.r), 0, Math.PI * 2)
    ctx.stroke()
  }

  if (opts.kind === 'fx-bloom') {
    // Shadow-only bloom + faint core fill.
    ctx.fillStyle = withAlpha(opts.color, 0)
    ctx.beginPath()
    ctx.arc(cx, cy, Math.max(0.1, opts.r * 0.8), 0, Math.PI * 2)
    ctx.fill()

    ctx.shadowBlur = 0
    ctx.fillStyle = withAlpha(opts.color, 0.22)
    ctx.beginPath()
    ctx.arc(cx, cy, Math.max(0.1, opts.r * 0.45), 0, Math.PI * 2)
    ctx.fill()
  }

  if (opts.kind === 'bloom') {
    // Only shadow: transparent fill.
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
  }

  if (opts.kind === 'rim') {
    // Rim shadow + faint colored stroke.
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

  if (opts.kind === 'selection') {
    // BaseGraph historically used a 2-pass blur (outer + core) with black stroke in `screen`.
    // We bake both passes into one sprite for reuse.
    const stroke = () => {
      ctx.strokeStyle = '#000000'
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

    // Pass 1: outer diffuse glow
    ctx.shadowBlur = blur
    stroke()
    // Pass 2: core intensity closer to the node (ratio chosen to match previous on-screen values ~1.2r vs 0.4r)
    ctx.shadowBlur = blur * 0.33
    stroke()
  }

  if (opts.kind === 'active') {
    // Active node glow: single-pass blur with black stroke in screen blending.
    ctx.strokeStyle = '#000000'
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

// Exposed for unit tests (cache keys / quantization invariants / LRU eviction).
export const __testing = {
  q: quantize,
  keyFor,
  MAX_CACHE,
  _cacheClear: resetGlowSpritesCache,
  _cacheHas: (key: string) => cache.has(key),
  _cacheKeys: () => Array.from(cache.keys()),
  _getGlowSprite: getGlowSprite,
}
