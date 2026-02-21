export type Point = { x: number; y: number }

export function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v))
}

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

  const vw = o.viewport?.w ?? (typeof window !== 'undefined' ? window.innerWidth : 10_000)
  const vh = o.viewport?.h ?? (typeof window !== 'undefined' ? window.innerHeight : 10_000)

  const left = clamp(o.anchor.x + offX, pad, vw - o.overlaySize.w - pad)
  const top = clamp(o.anchor.y + offY, pad, vh - o.overlaySize.h - pad)

  return { left: `${left}px`, top: `${top}px` }
}
