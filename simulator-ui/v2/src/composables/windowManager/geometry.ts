import type { WindowRect, WindowSizeConstraints } from './types'

/** Clamp value between min and max. */
export function clamp(v: number, min: number, max: number): number {
  return Math.min(Math.max(v, min), max)
}

/** Estimate initial size from constraints (for first frame before ResizeObserver). */
export function estimateSizeFromConstraints(
  c: WindowSizeConstraints,
): { width: number; height: number } {
  const w = typeof c.preferredWidth === 'number' ? c.preferredWidth : c.minWidth
  const h = typeof c.preferredHeight === 'number' ? c.preferredHeight : c.minHeight
  return { width: w, height: h }
}

/**
 * True when two rects overlap by area.
 *
 * Semantics: touching edges/corners is NOT considered an overlap.
 */
export function overlaps(a: WindowRect, b: WindowRect): boolean {
  const ar = a.left + a.width
  const ab = a.top + a.height
  const br = b.left + b.width
  const bb = b.top + b.height

  return a.left < br && ar > b.left && a.top < bb && ab > b.top
}

