import { clamp01 } from '../utils/math'
import { LruCache } from '../utils/lruCache'

export type Rgb = { r: number; g: number; b: number }

const DEFAULT_MAX_RGB_CACHE = 512
const rgbCache = new LruCache<string, Rgb | null>({ max: DEFAULT_MAX_RGB_CACHE })
let rgbCacheMax = DEFAULT_MAX_RGB_CACHE

function parseHexRgb(color: string): Rgb | null {
  const c = String(color || '').trim()
  if (!c.startsWith('#')) return null
  const hex = c.slice(1)
  const isShort = hex.length === 3
  const isLong = hex.length === 6
  if (!isShort && !isLong) return null
  const r = parseInt(isShort ? hex[0]! + hex[0]! : hex.slice(0, 2), 16)
  const g = parseInt(isShort ? hex[1]! + hex[1]! : hex.slice(2, 4), 16)
  const b = parseInt(isShort ? hex[2]! + hex[2]! : hex.slice(4, 6), 16)
  if (!Number.isFinite(r) || !Number.isFinite(g) || !Number.isFinite(b)) return null
  return { r, g, b }
}

// LRU cache for final rgba(...) strings.
// Key = `${hex}|${roundedAlpha}` — alpha rounded to 2 decimal places to maximise hit rate.
// At 6720 calls/sec with ~10 unique hex+alpha combos, this cache eliminates
// virtually all string concatenation on the hot path.
const DEFAULT_MAX_RGBA_CACHE = 1024
const rgbaCache = new LruCache<string, string>({ max: DEFAULT_MAX_RGBA_CACHE })
let rgbaCacheMax = DEFAULT_MAX_RGBA_CACHE

/**
 * Converts a hex color (#rgb or #rrggbb) into rgba(...) with the given alpha.
 * Uses bounded LRU caches for both hex→rgb parsing and the final rgba string,
 * reducing per-call allocations in the FX hot path.
 */
export function withAlpha(color: string, alpha: number, opts?: { maxCacheEntries?: number }) {
  const a = clamp01(alpha)
  const c = String(color || '').trim()
  // If caller passes rgba()/hsla(), respect the requested alpha.
  // This is used in a few FX paths where base colors may already be alpha-blended.
  if (c.startsWith('rgba(')) {
    const m = /^rgba\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*\)$/.exec(c)
    if (m) {
      const r = Number(m[1])
      const g = Number(m[2])
      const b = Number(m[3])
      if (Number.isFinite(r) && Number.isFinite(g) && Number.isFinite(b)) {
        const roundedA = Math.round(a * 100) / 100
        return `rgba(${r},${g},${b},${roundedA})`
      }
    }
    return c
  }
  if (c.startsWith('hsla(')) {
    const m = /^hsla\(\s*([\d.]+)\s*,\s*([\d.]+)%\s*,\s*([\d.]+)%\s*,\s*([\d.]+)\s*\)$/.exec(c)
    if (m) {
      const h = Number(m[1])
      const s = Number(m[2])
      const l = Number(m[3])
      if (Number.isFinite(h) && Number.isFinite(s) && Number.isFinite(l)) {
        const roundedA = Math.round(a * 100) / 100
        return `hsla(${h},${s}%,${l}%,${roundedA})`
      }
    }
    return c
  }
  if (!c.startsWith('#')) return c

  // Keep compatibility with the previous per-call cache cap.
  const lim =
    typeof opts?.maxCacheEntries === 'number' && Number.isFinite(opts.maxCacheEntries)
      ? Math.max(16, Math.floor(opts.maxCacheEntries))
      : null
  const nextRgbMax = lim ?? DEFAULT_MAX_RGB_CACHE
  const nextRgbaMax = lim ?? DEFAULT_MAX_RGBA_CACHE
  if (nextRgbMax !== rgbCacheMax) {
    rgbCacheMax = nextRgbMax
    rgbCache.setMax(nextRgbMax)
  }
  if (nextRgbaMax !== rgbaCacheMax) {
    rgbaCacheMax = nextRgbaMax
    rgbaCache.setMax(nextRgbaMax)
  }

  // Round alpha to 2 decimal places to maximise rgba cache hit rate.
  const roundedA = Math.round(a * 100) / 100
  const rgbaKey = `${c}|${roundedA}`

  // Check rgba result cache first.
  const cachedRgba = rgbaCache.get(rgbaKey)
  if (cachedRgba !== undefined) {
    return cachedRgba
  }

  let rgb = rgbCache.get(c)
  if (rgb === undefined) {
    rgb = parseHexRgb(c)
    rgbCache.set(c, rgb)
  }

  if (!rgb) return c
  const result = `rgba(${rgb.r},${rgb.g},${rgb.b},${roundedA})`
  rgbaCache.set(rgbaKey, result)
  return result
}
