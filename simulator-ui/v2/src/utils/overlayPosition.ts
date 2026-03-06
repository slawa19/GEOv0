import type { Point } from '../types/layout'

import { clamp } from './math'

export type { Point } from '../types/layout'

/**
 * Clamp an overlay with known (or fallback) size to the viewport.
 * Coordinates must be in the same coordinate system as the viewport.
 *
 * - If `viewport` is omitted, the viewport is the browser window (`window.innerWidth/innerHeight`)
 *   and coordinates are expected to be viewport-based (clientX/clientY).
 * - If `viewport` is provided (e.g. host element bounds), coordinates are expected to be
 *   relative to that viewport (e.g. clientToScreen).
 */
export function placeOverlayNearAnchor(o: {
  anchor: Point
  overlaySize: { w: number; h: number }
  offset?: { x: number; y: number }
  pad?: number
  viewport?: { w: number; h: number }
}): { left: string; top: string } {
  const pad = o.pad ?? 10
  const offX = o.offset?.x ?? 12
  const offY = o.offset?.y ?? 12

  const w = typeof window !== 'undefined' ? window : undefined
  const vw = o.viewport?.w ?? (w?.innerWidth ?? 10_000)
  const vh = o.viewport?.h ?? (w?.innerHeight ?? 10_000)

  const left = clamp(o.anchor.x + offX, pad, vw - o.overlaySize.w - pad)
  const top = clamp(o.anchor.y + offY, pad, vh - o.overlaySize.h - pad)

  return { left: `${left}px`, top: `${top}px` }
}
