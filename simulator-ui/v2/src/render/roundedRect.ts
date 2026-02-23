type RoundedRectPathBuilder = Pick<
  CanvasRenderingContext2D,
  'rect' | 'moveTo' | 'lineTo' | 'quadraticCurveTo' | 'closePath'
>

function buildRoundedRectPath(p: RoundedRectPathBuilder, x: number, y: number, w: number, h: number, rad: number) {
  const r = Math.max(0, Math.min(rad, Math.min(w, h) / 2))
  if (r <= 0.01) {
    p.rect(x, y, w, h)
    return
  }
  p.moveTo(x + r, y)
  p.lineTo(x + w - r, y)
  p.quadraticCurveTo(x + w, y, x + w, y + r)
  p.lineTo(x + w, y + h - r)
  p.quadraticCurveTo(x + w, y + h, x + w - r, y + h)
  p.lineTo(x + r, y + h)
  p.quadraticCurveTo(x, y + h, x, y + h - r)
  p.lineTo(x, y + r)
  p.quadraticCurveTo(x, y, x + r, y)
  p.closePath()
}

export function roundedRectPath(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  rad: number,
) {
  ctx.beginPath()

  const r = Math.max(0, Math.min(rad, Math.min(w, h) / 2))
  const nativeRoundRect = (ctx as unknown as { roundRect?: (x: number, y: number, w: number, h: number, radii: number) => void })
    .roundRect

  if (typeof nativeRoundRect === 'function') {
    if (r <= 0.01) {
      ctx.rect(x, y, w, h)
      return
    }
    nativeRoundRect(x, y, w, h, r)
    return
  }

  buildRoundedRectPath(ctx, x, y, w, h, r)
}

export function roundedRectPath2D(x: number, y: number, w: number, h: number, rad: number) {
  const p = new Path2D()
  buildRoundedRectPath(p, x, y, w, h, rad)
  return p
}
