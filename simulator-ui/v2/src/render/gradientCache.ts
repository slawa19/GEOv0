/**
 * Phase 5 — Gradient caching.
 *
 * Canvas gradients are tied to a specific CanvasRenderingContext2D.
 * This module keeps a per-ctx LRU cache (WeakMap) to avoid per-node per-frame
 * allocations of createLinearGradient() in high-quality rendering.
 */

import { quantize } from '../utils/math'
import { LruCache } from '../utils/lruCache'

type CacheKey = string

const DEFAULT_MAX_CACHE = 300

function q01(v: number) {
  // Color stop offsets are in [0..1]; quantize more finely.
  return quantize(v, 0.01)
}

function keyForLinear2Stops(
  x0: number,
  y0: number,
  x1: number,
  y1: number,
  s0Offset: number,
  s0Color: string,
  s1Offset: number,
  s1Color: string,
) {
  // IMPORTANT: keep this key deterministic and relatively small.
  // Geometry is quantized to increase cache hit rate while keeping visuals stable.
  return [
    'lg2',
    quantize(x0),
    quantize(y0),
    quantize(x1),
    quantize(y1),
    q01(s0Offset),
    s0Color,
    q01(s1Offset),
    s1Color,
  ].join('|')
}

function keyForLinear3Stops(
  x0: number,
  y0: number,
  x1: number,
  y1: number,
  s0Offset: number,
  s0Color: string,
  s1Offset: number,
  s1Color: string,
  s2Offset: number,
  s2Color: string,
) {
  return [
    'lg3',
    quantize(x0),
    quantize(y0),
    quantize(x1),
    quantize(y1),
    q01(s0Offset),
    s0Color,
    q01(s1Offset),
    s1Color,
    q01(s2Offset),
    s2Color,
  ].join('|')
}

const config = {
  maxCache: DEFAULT_MAX_CACHE,
}

const cachesByCtx = new WeakMap<CanvasRenderingContext2D, LruCache<CacheKey, CanvasGradient>>()

function cacheFor(ctx: CanvasRenderingContext2D): LruCache<CacheKey, CanvasGradient> {
  const existing = cachesByCtx.get(ctx)
  if (existing) return existing
  const created = new LruCache<CacheKey, CanvasGradient>({ max: config.maxCache })
  cachesByCtx.set(ctx, created)
  return created
}

/**
 * Returns a cached linear gradient (2 stops) bound to the given ctx.
 *
 * Prefer this overload in hot paths to avoid per-call allocations.
 */
export function getLinearGradient2Stops(
  ctx: CanvasRenderingContext2D,
  x0: number,
  y0: number,
  x1: number,
  y1: number,
  s0Offset: number,
  s0Color: string,
  s1Offset: number,
  s1Color: string,
): CanvasGradient {
  const c = cacheFor(ctx)
  const key = keyForLinear2Stops(x0, y0, x1, y1, s0Offset, s0Color, s1Offset, s1Color)
  const cached = c.get(key)
  if (cached) return cached

  const g = ctx.createLinearGradient(x0, y0, x1, y1)
  g.addColorStop(s0Offset, s0Color)
  g.addColorStop(s1Offset, s1Color)
  c.set(key, g)
  return g
}

/**
 * Returns a cached linear gradient (3 stops) bound to the given ctx.
 *
 * Backwards-compatible with existing 2-stop usage: this is an additional overload
 * used in FX paths that require a mid stop.
 */
export function getLinearGradient3Stops(
  ctx: CanvasRenderingContext2D,
  x0: number,
  y0: number,
  x1: number,
  y1: number,
  s0Offset: number,
  s0Color: string,
  s1Offset: number,
  s1Color: string,
  s2Offset: number,
  s2Color: string,
): CanvasGradient {
  const c = cacheFor(ctx)
  const key = keyForLinear3Stops(
    x0,
    y0,
    x1,
    y1,
    s0Offset,
    s0Color,
    s1Offset,
    s1Color,
    s2Offset,
    s2Color,
  )
  const cached = c.get(key)
  if (cached) return cached

  const g = ctx.createLinearGradient(x0, y0, x1, y1)
  g.addColorStop(s0Offset, s0Color)
  g.addColorStop(s1Offset, s1Color)
  g.addColorStop(s2Offset, s2Color)
  c.set(key, g)
  return g
}

/**
 * Explicitly clears cache for a specific ctx.
 * Useful when the underlying canvas/ctx is recreated.
 */
export function clearGradientCache(ctx: CanvasRenderingContext2D) {
  cachesByCtx.delete(ctx)
}

export const __testing = {
  q: quantize,
  keyForLinear2Stops,
  keyForLinear3Stops,
  _setMaxCacheForTests(max: number) {
    config.maxCache = Math.max(0, Math.floor(max))
  },
  _cacheClear(ctx: CanvasRenderingContext2D) {
    cachesByCtx.delete(ctx)
  },
  _cacheSize(ctx: CanvasRenderingContext2D) {
    return cacheFor(ctx).size()
  },
  _cacheKeys(ctx: CanvasRenderingContext2D) {
    return cacheFor(ctx).keys()
  },
  DEFAULT_MAX_CACHE,
}

