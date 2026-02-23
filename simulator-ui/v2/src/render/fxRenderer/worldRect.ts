const WORLD_RECT_PAD_PX_DEFAULT = 96
const WORLD_RECT_FALLBACK_EXTENT = 1e6

export function worldRectForCanvas(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  padPx = WORLD_RECT_PAD_PX_DEFAULT,
): { x: number; y: number; w: number; h: number } {
  // Current transform includes camera pan/zoom. Invert it to convert screen-space canvas bounds
  // into world-space coordinates for stable clip paths.
  const m = ctx.getTransform()
  const inv = m.inverse()

  const corners = [
    new DOMPoint(-padPx, -padPx),
    new DOMPoint(w + padPx, -padPx),
    new DOMPoint(-padPx, h + padPx),
    new DOMPoint(w + padPx, h + padPx),
  ].map((p) => p.matrixTransform(inv))

  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (const p of corners) {
    if (p.x < minX) minX = p.x
    if (p.y < minY) minY = p.y
    if (p.x > maxX) maxX = p.x
    if (p.y > maxY) maxY = p.y
  }

  if (!Number.isFinite(minX) || !Number.isFinite(minY) || !Number.isFinite(maxX) || !Number.isFinite(maxY)) {
    return {
      x: -WORLD_RECT_FALLBACK_EXTENT,
      y: -WORLD_RECT_FALLBACK_EXTENT,
      w: WORLD_RECT_FALLBACK_EXTENT * 2,
      h: WORLD_RECT_FALLBACK_EXTENT * 2,
    }
  }

  return { x: minX, y: minY, w: maxX - minX, h: maxY - minY }
}
