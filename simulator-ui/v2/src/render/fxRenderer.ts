import type { VizMapping } from '../vizMapping'
import type { LayoutNode } from './nodePainter'

export type FxSpark = {
  key: string
  source: string
  target: string
  startedAtMs: number
  ttlMs: number
  colorCore: string
  colorTrail: string
  thickness: number
  seed: number
}

export type FxState = {
  sparks: FxSpark[]
}

export function createFxState(): FxState {
  return { sparks: [] }
}

export function resetFxState(fxState: FxState) {
  fxState.sparks.length = 0
}

export function spawnSparks(
  fxState: FxState,
  opts: {
    edges: Array<{ from: string; to: string }>
    nowMs: number
    ttlMs: number
    colorCore: string
    colorTrail: string
    thickness: number
    seedPrefix: string
    countPerEdge: number
    keyEdge: (a: string, b: string) => string
    seedFn: (s: string) => number
    isTestMode: boolean
  },
) {
  if (opts.isTestMode) return

  const { edges, nowMs, ttlMs, colorCore, colorTrail, thickness, seedPrefix, countPerEdge, keyEdge, seedFn } = opts
  for (const e of edges) {
    const k = keyEdge(e.from, e.to)
    for (let i = 0; i < Math.max(1, countPerEdge); i++) {
      const seed = seedFn(`${seedPrefix}:${k}:${i}`)
      fxState.sparks.push({
        key: `${k}#${i}#${nowMs.toFixed(0)}`,
        source: e.from,
        target: e.to,
        startedAtMs: nowMs,
        ttlMs,
        colorCore,
        colorTrail,
        thickness,
        seed,
      })
    }
  }
}

export function renderFxFrame(opts: {
  nowMs: number
  ctx: CanvasRenderingContext2D
  pos: Map<string, LayoutNode>
  w: number
  h: number
  mapping: VizMapping
  fxState: FxState
  flash: number
  isTestMode: boolean
}): { flash: number } {
  const { nowMs, ctx, pos, w, h, mapping, fxState, isTestMode } = opts
  let flash = opts.flash

  if (isTestMode) return { flash }

  // Flash overlay (clearing)
  if (flash > 0) {
    const t = Math.max(0, Math.min(1, flash))
    ctx.save()
    ctx.globalAlpha = t
    const grad = ctx.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, Math.max(w, h) * 0.7)
    grad.addColorStop(0, mapping.fx.flash.clearing.from)
    grad.addColorStop(1, mapping.fx.flash.clearing.to)
    ctx.fillStyle = grad
    ctx.fillRect(0, 0, w, h)
    ctx.restore()

    flash = Math.max(0, flash - 0.03)
  }

  if (fxState.sparks.length === 0) return { flash }

  // Compact in-place filter.
  let write = 0
  for (let read = 0; read < fxState.sparks.length; read++) {
    const s = fxState.sparks[read]!
    const age = nowMs - s.startedAtMs
    if (age >= s.ttlMs) continue

    fxState.sparks[write++] = s

    const a = pos.get(s.source)
    const b = pos.get(s.target)
    if (!a || !b) continue

    const t0 = Math.max(0, Math.min(1, age / s.ttlMs))
    const phase = ((s.seed % 1000) / 1000) * 0.35
    const t = Math.max(0, Math.min(1, t0 + phase))

    const x = a.__x + (b.__x - a.__x) * t
    const y = a.__y + (b.__y - a.__y) * t

    const dx = b.__x - a.__x
    const dy = b.__y - a.__y
    const len = Math.max(1e-6, Math.hypot(dx, dy))
    const ux = dx / len
    const uy = dy / len

    ctx.save()
    ctx.globalCompositeOperation = 'lighter'

    for (let j = 1; j <= 4; j++) {
      const fade = (1 - j / 5) * 0.5
      ctx.globalAlpha = Math.max(0, 1 - t0) * fade
      ctx.fillStyle = s.colorTrail
      ctx.beginPath()
      ctx.arc(x - ux * j * 6, y - uy * j * 6, Math.max(0.8, s.thickness) * (1.4 - j * 0.15), 0, Math.PI * 2)
      ctx.fill()
    }

    ctx.globalAlpha = Math.max(0, 1 - t0)
    ctx.fillStyle = s.colorCore
    ctx.beginPath()
    ctx.arc(x, y, Math.max(1.0, s.thickness) * 1.6, 0, Math.PI * 2)
    ctx.fill()

    ctx.restore()
  }

  fxState.sparks.length = write
  return { flash }
}
