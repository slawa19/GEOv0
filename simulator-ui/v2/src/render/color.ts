export type Rgb = { r: number; g: number; b: number }

function clamp01(v: number) {
  return Math.max(0, Math.min(1, v))
}

const DEFAULT_MAX_RGB_CACHE = 512
const rgbCache = new Map<string, Rgb | null>()

function trimCache(max: number) {
  const lim = Math.max(16, Math.floor(max))
  while (rgbCache.size > lim) {
    const first = rgbCache.keys().next().value as string | undefined
    if (!first) return
    rgbCache.delete(first)
  }
}

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

/**
 * Converts a hex color (#rgb or #rrggbb) into rgba(...) with the given alpha.
 * Uses a bounded LRU cache to avoid unbounded memory growth.
 */
export function withAlpha(color: string, alpha: number, opts?: { maxCacheEntries?: number }) {
  const a = clamp01(alpha)
  const c = String(color || '').trim()
  if (c.startsWith('rgba(') || c.startsWith('hsla(')) return c
  if (!c.startsWith('#')) return c

  let rgb = rgbCache.get(c)
  if (rgb !== undefined) {
    // Touch for LRU.
    rgbCache.delete(c)
    rgbCache.set(c, rgb)
  } else {
    rgb = parseHexRgb(c)
    rgbCache.set(c, rgb)
    trimCache(opts?.maxCacheEntries ?? DEFAULT_MAX_RGB_CACHE)
  }

  if (!rgb) return c
  return `rgba(${rgb.r},${rgb.g},${rgb.b},${a})`
}
