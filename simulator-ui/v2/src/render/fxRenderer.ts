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

export type FxEdgePulse = {
  key: string
  from: string
  to: string
  startedAtMs: number
  durationMs: number
  color: string
  thickness: number
  seed: number
}

export type FxNodeBurst = {
  key: string
  nodeId: string
  startedAtMs: number
  durationMs: number
  color: string
  seed: number
}

export type FxState = {
  sparks: FxSpark[]
  edgePulses: FxEdgePulse[]
  nodeBursts: FxNodeBurst[]
}

export function createFxState(): FxState {
  return { sparks: [], edgePulses: [], nodeBursts: [] }
}

export function resetFxState(fxState: FxState) {
  fxState.sparks.length = 0
  fxState.edgePulses.length = 0
  fxState.nodeBursts.length = 0
}

export function spawnEdgePulses(
  fxState: FxState,
  opts: {
    edges: Array<{ from: string; to: string }>
    nowMs: number
    durationMs: number
    color: string
    thickness: number
    seedPrefix: string
    countPerEdge: number
    keyEdge: (a: string, b: string) => string
    seedFn: (s: string) => number
    isTestMode: boolean
  },
) {
  if (opts.isTestMode) return

  const { edges, nowMs, durationMs, color, thickness, seedPrefix, countPerEdge, keyEdge, seedFn } = opts
  for (const e of edges) {
    const k = keyEdge(e.from, e.to)
    for (let i = 0; i < Math.max(1, countPerEdge); i++) {
      const seed = seedFn(`${seedPrefix}:${k}:${i}`)
      fxState.edgePulses.push({
        key: `pulse:${k}#${i}#${nowMs.toFixed(0)}`,
        from: e.from,
        to: e.to,
        startedAtMs: nowMs,
        durationMs,
        color,
        thickness,
        seed,
      })
    }
  }
}

export function spawnNodeBursts(
  fxState: FxState,
  opts: {
    nodeIds: string[]
    nowMs: number
    durationMs: number
    color: string
    seedPrefix: string
    seedFn: (s: string) => number
    isTestMode: boolean
  },
) {
  if (opts.isTestMode) return

  const { nodeIds, nowMs, durationMs, color, seedPrefix, seedFn } = opts
  for (const id of nodeIds) {
    const seed = seedFn(`${seedPrefix}:${id}`)
    fxState.nodeBursts.push({
      key: `burst:${id}#${nowMs.toFixed(0)}`,
      nodeId: id,
      startedAtMs: nowMs,
      durationMs,
      color,
      seed,
    })
  }
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

  const clamp01 = (v: number) => Math.max(0, Math.min(1, v))

  const withAlpha = (color: string, alpha: number) => {
    const a = clamp01(alpha)
    const c = String(color || '').trim()
    if (c.startsWith('rgba(') || c.startsWith('hsla(')) return c
    if (c.startsWith('#')) {
      const hex = c.slice(1)
      const isShort = hex.length === 3
      const isLong = hex.length === 6
      if (isShort || isLong) {
        const r = parseInt(isShort ? hex[0]! + hex[0]! : hex.slice(0, 2), 16)
        const g = parseInt(isShort ? hex[1]! + hex[1]! : hex.slice(2, 4), 16)
        const b = parseInt(isShort ? hex[2]! + hex[2]! : hex.slice(4, 6), 16)
        if (Number.isFinite(r) && Number.isFinite(g) && Number.isFinite(b)) {
          return `rgba(${r},${g},${b},${a})`
        }
      }
    }
    return c
  }

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

  if (fxState.sparks.length === 0 && fxState.edgePulses.length === 0 && fxState.nodeBursts.length === 0) return { flash }

  // Tx sparks / comets
  if (fxState.sparks.length > 0) {
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

      const t0 = clamp01(age / s.ttlMs)
      const phase = ((s.seed % 1000) / 1000) * 0.35
      const t = clamp01(t0 + phase)

      const dx = b.__x - a.__x
      const dy = b.__y - a.__y
      const len = Math.max(1e-6, Math.hypot(dx, dy))
      const ux = dx / len
      const uy = dy / len

      // Comet-like spark: trail length depends on edge length and ttl.
      // Small ttl => faster head => longer trail.
      const speedPxPerMs = len / Math.max(1, s.ttlMs)
      const trailTimeMs = Math.max(120, Math.min(420, s.ttlMs * 0.28))
      const trailLen = Math.max(12, Math.min(len * 0.55, speedPxPerMs * trailTimeMs))

      const seed01 = ((s.seed % 4096) / 4096)
      const wobbleFreq = 2.5 + (seed01 * 4.0)
      const wobbleAmp = (2.0 + s.thickness * 2.5) * (1 - t0)
      const px = -uy
      const py = ux
      const wobble = Math.sin((t * Math.PI * 2 * wobbleFreq) + seed01 * 11.3) * wobbleAmp

      const x = a.__x + (b.__x - a.__x) * t + px * wobble
      const y = a.__y + (b.__y - a.__y) * t + py * wobble

      const tailX = x - ux * trailLen
      const tailY = y - uy * trailLen

      ctx.save()
      ctx.globalCompositeOperation = 'lighter'

      const life = 1 - t0
      const alphaTrail = Math.max(0, Math.min(1, life * 0.75))
      const alphaCore = Math.max(0, Math.min(1, life * 0.95))

      // Trail (glow pass)
      {
        const grad = ctx.createLinearGradient(x, y, tailX, tailY)
        grad.addColorStop(0, withAlpha(s.colorTrail, alphaTrail * 0.9))
        grad.addColorStop(0.25, withAlpha(s.colorTrail, alphaTrail * 0.55))
        grad.addColorStop(1, withAlpha(s.colorTrail, 0))

        ctx.globalAlpha = 1
        ctx.strokeStyle = grad
        ctx.lineCap = 'round'
        ctx.lineJoin = 'round'
        ctx.lineWidth = Math.max(1.2, s.thickness * 3.0)
        ctx.shadowBlur = Math.max(6, s.thickness * 10)
        ctx.shadowColor = withAlpha(s.colorTrail, 0.85)
        ctx.beginPath()
        ctx.moveTo(tailX, tailY)
        ctx.lineTo(x, y)
        ctx.stroke()
      }

      // Trail (core pass)
      {
        const grad = ctx.createLinearGradient(x, y, tailX, tailY)
        grad.addColorStop(0, withAlpha(s.colorTrail, alphaTrail))
        grad.addColorStop(0.35, withAlpha(s.colorTrail, alphaTrail * 0.35))
        grad.addColorStop(1, withAlpha(s.colorTrail, 0))

        ctx.shadowBlur = 0
        ctx.globalAlpha = 1
        ctx.strokeStyle = grad
        ctx.lineWidth = Math.max(0.9, s.thickness * 1.9)
        ctx.beginPath()
        ctx.moveTo(tailX, tailY)
        ctx.lineTo(x, y)
        ctx.stroke()
      }

      // Head core with soft bloom
      {
        const r = Math.max(1.6, s.thickness * 2.4)
        ctx.globalAlpha = 1
        ctx.shadowBlur = Math.max(10, r * 6)
        ctx.shadowColor = withAlpha(s.colorCore, 0.9)
        ctx.fillStyle = withAlpha(s.colorCore, alphaCore)
        ctx.beginPath()
        ctx.arc(x, y, r, 0, Math.PI * 2)
        ctx.fill()
      }

      // Small embers behind the head
      {
        ctx.shadowBlur = 0
        ctx.fillStyle = withAlpha(s.colorTrail, alphaTrail * 0.55)
        for (let j = 1; j <= 3; j++) {
          const tt = j / 4
          const ex = x - ux * trailLen * tt
          const ey = y - uy * trailLen * tt
          const rr = Math.max(0.8, s.thickness * (1.25 - tt * 0.7))
          ctx.globalAlpha = Math.max(0, alphaTrail * (1 - tt) * 0.9)
          ctx.beginPath()
          ctx.arc(ex, ey, rr, 0, Math.PI * 2)
          ctx.fill()
        }
      }

      ctx.restore()
    }

    fxState.sparks.length = write
  }

  // Clearing edge pulses
  if (fxState.edgePulses.length > 0) {
    let write = 0
    for (let read = 0; read < fxState.edgePulses.length; read++) {
      const p = fxState.edgePulses[read]!
      const age = nowMs - p.startedAtMs
      if (age >= p.durationMs) continue
      fxState.edgePulses[write++] = p

      const a = pos.get(p.from)
      const b = pos.get(p.to)
      if (!a || !b) continue

      const t0 = clamp01(age / p.durationMs)
      const seed01 = ((p.seed % 4096) / 4096)
      const phase = seed01 * 0.12
      const t = clamp01(t0 + phase)

      const dx = b.__x - a.__x
      const dy = b.__y - a.__y
      const len = Math.max(1e-6, Math.hypot(dx, dy))
      const ux = dx / len
      const uy = dy / len

      const x = a.__x + dx * t
      const y = a.__y + dy * t

      const speedPxPerMs = len / Math.max(1, p.durationMs)
      const trailTimeMs = Math.max(90, Math.min(260, p.durationMs * 0.22))
      const trailLen = Math.max(10, Math.min(len * 0.45, speedPxPerMs * trailTimeMs))
      const tailX = x - ux * trailLen
      const tailY = y - uy * trailLen

      const life = 1 - t0
      const alpha = Math.max(0, Math.min(1, life * 0.8))

      ctx.save()
      ctx.globalCompositeOperation = 'lighter'
      ctx.lineCap = 'round'
      ctx.lineJoin = 'round'

      // Soft whole-edge presence so the route reads as a loop.
      ctx.globalAlpha = alpha * 0.10
      ctx.strokeStyle = p.color
      ctx.lineWidth = Math.max(1.0, p.thickness * 1.1)
      ctx.beginPath()
      ctx.moveTo(a.__x, a.__y)
      ctx.lineTo(b.__x, b.__y)
      ctx.stroke()

      // Moving pulse head + tail.
      const grad = ctx.createLinearGradient(x, y, tailX, tailY)
      grad.addColorStop(0, withAlpha(p.color, alpha * 0.95))
      grad.addColorStop(0.35, withAlpha(p.color, alpha * 0.35))
      grad.addColorStop(1, withAlpha(p.color, 0))

      ctx.globalAlpha = 1
      ctx.strokeStyle = grad
      ctx.lineWidth = Math.max(1.2, p.thickness * 2.8)
      ctx.shadowBlur = Math.max(10, p.thickness * 14)
      ctx.shadowColor = withAlpha(p.color, 0.9)
      ctx.beginPath()
      ctx.moveTo(tailX, tailY)
      ctx.lineTo(x, y)
      ctx.stroke()

      ctx.shadowBlur = 0
      ctx.globalAlpha = alpha
      ctx.fillStyle = p.color
      ctx.beginPath()
      ctx.arc(x, y, Math.max(1.5, p.thickness * 2.2), 0, Math.PI * 2)
      ctx.fill()

      ctx.restore()
    }
    fxState.edgePulses.length = write
  }

  // Clearing node bursts
  if (fxState.nodeBursts.length > 0) {
    let write = 0
    for (let read = 0; read < fxState.nodeBursts.length; read++) {
      const b = fxState.nodeBursts[read]!
      const age = nowMs - b.startedAtMs
      if (age >= b.durationMs) continue
      fxState.nodeBursts[write++] = b

      const n = pos.get(b.nodeId)
      if (!n) continue

      const t0 = clamp01(age / b.durationMs)
      const life = 1 - t0
      const seed01 = ((b.seed % 4096) / 4096)

      const base = 8 + seed01 * 5
      const r = base + t0 * (36 + seed01 * 10)
      const alpha = Math.max(0, Math.min(1, life * 0.65))

      ctx.save()
      ctx.globalCompositeOperation = 'lighter'
      ctx.globalAlpha = alpha
      ctx.strokeStyle = b.color
      ctx.lineWidth = Math.max(1.2, 2.4 - t0 * 1.6)
      ctx.shadowBlur = Math.max(6, 18 * life)
      ctx.shadowColor = withAlpha(b.color, 0.85)
      ctx.beginPath()
      ctx.arc(n.__x, n.__y, r, 0, Math.PI * 2)
      ctx.stroke()
      ctx.restore()
    }
    fxState.nodeBursts.length = write
  }

  return { flash }
}
