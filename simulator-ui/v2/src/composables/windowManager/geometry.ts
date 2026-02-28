import type { WindowRect, WindowSizeConstraints } from './types'

/** Clamp value between min and max. */
export function clamp(v: number, min: number, max: number): number {
  return Math.min(Math.max(v, min), max)
}

/** Check if two rects overlap. */
export function overlaps(a: WindowRect, b: WindowRect): boolean {
  return !(
    a.left + a.width <= b.left ||
    b.left + b.width <= a.left ||
    a.top + a.height <= b.top ||
    b.top + b.height <= a.top
  )
}

/** Estimate initial size from constraints (for first frame before ResizeObserver). */
export function estimateSizeFromConstraints(
  c: WindowSizeConstraints,
): { width: number; height: number } {
  const w = typeof c.preferredWidth === 'number' ? c.preferredWidth : c.minWidth
  const h = typeof c.preferredHeight === 'number' ? c.preferredHeight : c.minHeight
  return { width: w, height: h }
}

