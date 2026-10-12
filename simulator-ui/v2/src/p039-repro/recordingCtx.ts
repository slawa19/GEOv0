/**
 * Programme 039, T3901 - a 2D context that records what is drawn. Not a test file.
 *
 * It keeps the current path (in canvas coordinates: translate / rotate / scale / save / restore are applied) and, on
 * every `stroke` / `fill` / `fillRect` / `strokeRect` / `drawImage`, stores one record with the path and the style.
 * What it does NOT model: clipping, dashes, compositing, gradients, text. It records calls, it draws nothing.
 */
export type Pt = { x: number; y: number }
export type PathOp = { op: 'moveTo' | 'lineTo' | 'arc' | 'rect' | 'curve' | 'close'; at: Pt[] }
export type Draw = {
  kind: 'stroke' | 'fill' | 'fillRect' | 'strokeRect' | 'drawImage'
  path: PathOp[]
  strokeStyle: string
  fillStyle: string
  lineWidth: number
  globalAlpha: number
}

type M = [number, number, number, number, number, number] // a b c d e f

export function recordingCtx() {
  const draws: Draw[] = []
  let path: PathOp[] = []
  let m: M = [1, 0, 0, 1, 0, 0]
  const stack: M[] = []
  const tp = (x: number, y: number): Pt => ({ x: m[0] * x + m[2] * y + m[4], y: m[1] * x + m[3] * y + m[5] })
  const mul = (n: M): M => [
    m[0] * n[0] + m[2] * n[1], m[1] * n[0] + m[3] * n[1],
    m[0] * n[2] + m[2] * n[3], m[1] * n[2] + m[3] * n[3],
    m[0] * n[4] + m[2] * n[5] + m[4], m[1] * n[4] + m[3] * n[5] + m[5],
  ]
  const state = { strokeStyle: '', fillStyle: '', lineWidth: 1, globalAlpha: 1 }
  const emit = (kind: Draw['kind'], p: PathOp[]) => draws.push({ kind, path: p, ...state, strokeStyle: String(state.strokeStyle), fillStyle: String(state.fillStyle) })

  const api: Record<string, unknown> = {
    beginPath: () => { path = [] },
    moveTo: (x: number, y: number) => { path.push({ op: 'moveTo', at: [tp(x, y)] }) },
    lineTo: (x: number, y: number) => { path.push({ op: 'lineTo', at: [tp(x, y)] }) },
    arc: (x: number, y: number) => { path.push({ op: 'arc', at: [tp(x, y)] }) },
    rect: (x: number, y: number, w: number, h: number) => { path.push({ op: 'rect', at: [tp(x, y), tp(x + w, y + h)] }) },
    roundRect: (x: number, y: number, w: number, h: number) => { path.push({ op: 'rect', at: [tp(x, y), tp(x + w, y + h)] }) },
    quadraticCurveTo: (cx: number, cy: number, x: number, y: number) => { path.push({ op: 'curve', at: [tp(cx, cy), tp(x, y)] }) },
    bezierCurveTo: (a: number, b: number, c: number, d: number, x: number, y: number) => { path.push({ op: 'curve', at: [tp(a, b), tp(c, d), tp(x, y)] }) },
    arcTo: (a: number, b: number, x: number, y: number) => { path.push({ op: 'curve', at: [tp(a, b), tp(x, y)] }) },
    closePath: () => { path.push({ op: 'close', at: [] }) },
    stroke: () => emit('stroke', [...path]),
    fill: () => emit('fill', [...path]),
    fillRect: (x: number, y: number, w: number, h: number) => emit('fillRect', [{ op: 'rect', at: [tp(x, y), tp(x + w, y + h)] }]),
    strokeRect: (x: number, y: number, w: number, h: number) => emit('strokeRect', [{ op: 'rect', at: [tp(x, y), tp(x + w, y + h)] }]),
    drawImage: (_img: unknown, x: number, y: number, w = 0, h = 0) => emit('drawImage', [{ op: 'rect', at: [tp(x, y), tp(x + w, y + h)] }]),
    save: () => { stack.push([...m] as M) },
    restore: () => { m = stack.pop() ?? m },
    translate: (x: number, y: number) => { m = mul([1, 0, 0, 1, x, y]) },
    scale: (x: number, y: number) => { m = mul([x, 0, 0, y, 0, 0]) },
    rotate: (a: number) => { m = mul([Math.cos(a), Math.sin(a), -Math.sin(a), Math.cos(a), 0, 0]) },
    setTransform: (a = 1, b = 0, c = 0, d = 1, e = 0, f = 0) => { m = [a, b, c, d, e, f] },
    resetTransform: () => { m = [1, 0, 0, 1, 0, 0] },
    createLinearGradient: () => ({ addColorStop: () => undefined }),
    createRadialGradient: () => ({ addColorStop: () => undefined }),
    measureText: () => ({ width: 0 }),
    getTransform: () => ({ a: m[0], b: m[1], c: m[2], d: m[3], e: m[4], f: m[5] }),
  }
  const noop = () => undefined
  const ctx = new Proxy(api, {
    get(t, k: string) {
      if (k in state) return state[k as keyof typeof state]
      return k in t ? t[k] : noop
    },
    set(_t, k: string, v) {
      if (k in state) (state as Record<string, unknown>)[k] = v
      return true
    },
  }) as unknown as CanvasRenderingContext2D
  return { ctx, draws }
}

/** A stroke of exactly one straight segment: how the edge pass draws a trust line today. */
export function isPlainSegment(d: Draw): boolean {
  return d.kind === 'stroke' && d.path.length === 2 && d.path[0]!.op === 'moveTo' && d.path[1]!.op === 'lineTo'
}

/** Straight segments (moveTo/lineTo pairs) a stroke consists of - also when many are batched into one path. */
export function segmentsOf(d: Draw): Array<[Pt, Pt]> {
  const out: Array<[Pt, Pt]> = []
  let cur: Pt | null = null
  for (const p of d.path) {
    if (p.op === 'moveTo') cur = p.at[0]!
    else if (p.op === 'lineTo' && cur) { out.push([cur, p.at[0]!]); cur = p.at[0]! }
    else cur = null
  }
  return out
}

export const allPoints = (d: Draw): Pt[] => d.path.flatMap((p) => p.at)
export const dist = (a: Pt, b: Pt) => Math.hypot(a.x - b.x, a.y - b.y)
