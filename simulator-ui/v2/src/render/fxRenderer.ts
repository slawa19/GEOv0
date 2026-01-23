import type { VizMapping } from '../vizMapping'
import type { LayoutNode } from './nodePainter'
import { sizeForNode } from './nodePainter'

const clamp01 = (v: number) => Math.max(0, Math.min(1, v))

type Rgb = { r: number; g: number; b: number }
const rgbCache = new Map<string, Rgb | null>()

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

function withAlpha(color: string, alpha: number) {
  const a = clamp01(alpha)
  const c = String(color || '').trim()
  if (c.startsWith('rgba(') || c.startsWith('hsla(')) return c
  if (!c.startsWith('#')) return c

  let rgb = rgbCache.get(c)
  if (rgb === undefined) {
    rgb = parseHexRgb(c)
    rgbCache.set(c, rgb)
  }
  if (!rgb) return c
  return `rgba(${rgb.r},${rgb.g},${rgb.b},${a})`
}

function roundedRectPath(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, rad: number) {
  const r = Math.max(0, Math.min(rad, Math.min(w, h) / 2))
  ctx.beginPath()
  if (r <= 0.01) {
    ctx.rect(x, y, w, h)
    return
  }
  ctx.moveTo(x + r, y)
  ctx.lineTo(x + w - r, y)
  ctx.quadraticCurveTo(x + w, y, x + w, y + r)
  ctx.lineTo(x + w, y + h - r)
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h)
  ctx.lineTo(x + r, y + h)
  ctx.quadraticCurveTo(x, y + h, x, y + h - r)
  ctx.lineTo(x, y + r)
  ctx.quadraticCurveTo(x, y, x + r, y)
  ctx.closePath()
}

function nodeOutlinePath(ctx: CanvasRenderingContext2D, n: LayoutNode, scale = 1) {
  const { w, h } = sizeForNode(n)
  const isBusiness = String(n.type) === 'business'
  const rr = Math.max(0, Math.min(4, Math.min(w, h) * 0.18))
  const ww = w * scale
  const hh = h * scale
  const x = n.__x - ww / 2
  const y = n.__y - hh / 2

  if (isBusiness) {
    roundedRectPath(ctx, x, y, ww, hh, rr * scale)
    return
  }

  const r = Math.max(ww, hh) / 2
  ctx.beginPath()
  ctx.arc(n.__x, n.__y, r, 0, Math.PI * 2)
}

function linkTermination(n: LayoutNode, target: LayoutNode) {
  const dx = target.__x - n.__x
  const dy = target.__y - n.__y
  if (Math.abs(dx) < 0.1 && Math.abs(dy) < 0.1) return { x: n.__x, y: n.__y }

  const angle = Math.atan2(dy, dx)
  const { w, h } = sizeForNode(n)

  // Person = circle
  if (String(n.type) !== 'business') {
    const r = Math.max(w, h) / 2
    return { x: n.__x + Math.cos(angle) * r, y: n.__y + Math.sin(angle) * r }
  }

  // Business = rounded-rect approximation via ray-box intersection
  const hw = w / 2
  const hh = h / 2
  const absCos = Math.abs(Math.cos(angle))
  const absSin = Math.abs(Math.sin(angle))
  const xDist = absCos > 0.001 ? hw / absCos : Infinity
  const yDist = absSin > 0.001 ? hh / absSin : Infinity
  const dist = Math.min(xDist, yDist)
  return { x: n.__x + Math.cos(angle) * dist, y: n.__y + Math.sin(angle) * dist }
}

function easeOutCubic(t: number) {
  const x = clamp01(t)
  return 1 - Math.pow(1 - x, 3)
}

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
  kind: 'comet' | 'beam'
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
  kind: 'clearing' | 'tx-impact'
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
    kind?: FxNodeBurst['kind']
    seedPrefix: string
    seedFn: (s: string) => number
    isTestMode: boolean
  },
) {
  if (opts.isTestMode) return

  const { nodeIds, nowMs, durationMs, color, seedPrefix, seedFn } = opts
  const kind = opts.kind ?? 'clearing'
  for (const id of nodeIds) {
    const seed = seedFn(`${seedPrefix}:${id}`)
    fxState.nodeBursts.push({
      key: `burst:${id}#${nowMs.toFixed(0)}`,
      nodeId: id,
      startedAtMs: nowMs,
      durationMs,
      color,
      seed,
      kind,
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
    kind?: FxSpark['kind']
    seedPrefix: string
    countPerEdge: number
    keyEdge: (a: string, b: string) => string
    seedFn: (s: string) => number
    isTestMode: boolean
  },
) {
  if (opts.isTestMode) return

  const { edges, nowMs, ttlMs, colorCore, colorTrail, thickness, seedPrefix, countPerEdge, keyEdge, seedFn } = opts
  const kind = opts.kind ?? 'comet'
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
        kind,
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

  // NOTE: `withAlpha` and helpers are module-scoped with caching (perf).

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

      const start = linkTermination(a, b)
      const end = linkTermination(b, a)

      const dx = end.x - start.x
      const dy = end.y - start.y
      const len = Math.max(1e-6, Math.hypot(dx, dy))
      const ux = dx / len
      const uy = dy / len

      if (s.kind === 'beam') {
        const t = easeOutCubic(t0)
        const life = 1 - t0
        const alpha = clamp01(life)

        const headX = start.x + dx * t
        const headY = start.y + dy * t

        ctx.save()
        ctx.globalCompositeOperation = 'lighter'
        ctx.lineCap = 'round'
        ctx.lineJoin = 'round'

        // Base beam (full segment) — thin core + soft halo
        {
          const baseAlpha = Math.max(0, Math.min(1, alpha * 0.55))

          // Halo pass
          ctx.globalAlpha = baseAlpha * 0.55
          ctx.strokeStyle = s.colorTrail
          ctx.lineWidth = Math.max(1.2, s.thickness * 3.2)
          ctx.shadowBlur = Math.max(10, s.thickness * 18)
          ctx.shadowColor = withAlpha(s.colorTrail, 0.85)
          ctx.beginPath()
          ctx.moveTo(start.x, start.y)
          ctx.lineTo(end.x, end.y)
          ctx.stroke()

          // Core pass
          ctx.shadowBlur = 0
          ctx.globalAlpha = baseAlpha
          ctx.strokeStyle = withAlpha(s.colorTrail, 0.9)
          ctx.lineWidth = Math.max(0.9, s.thickness * 1.25)
          ctx.beginPath()
          ctx.moveTo(start.x, start.y)
          ctx.lineTo(end.x, end.y)
          ctx.stroke()
        }

        // Moving bright “packet” segment near the head
        {
          const segLen = Math.max(18, Math.min(54, len * 0.22))
          const tailX = headX - ux * segLen
          const tailY = headY - uy * segLen
          const grad = ctx.createLinearGradient(headX, headY, tailX, tailY)
          grad.addColorStop(0, withAlpha(s.colorCore, alpha * 1.0))
          grad.addColorStop(0.35, withAlpha(s.colorTrail, alpha * 0.55))
          grad.addColorStop(1, withAlpha(s.colorTrail, 0))

          ctx.globalAlpha = 1
          ctx.strokeStyle = grad
          ctx.lineWidth = Math.max(1.4, s.thickness * 4.2)
          ctx.shadowBlur = Math.max(12, s.thickness * 20)
          ctx.shadowColor = withAlpha(s.colorTrail, 0.9)
          ctx.beginPath()
          ctx.moveTo(tailX, tailY)
          ctx.lineTo(headX, headY)
          ctx.stroke()
        }

        // Head: star-like spark (tiny cross) + bloom
        {
          const r = Math.max(1.8, s.thickness * 2.8)
          const arm = Math.max(6, r * 3.2)
          const px = -uy
          const py = ux

          ctx.shadowBlur = Math.max(10, r * 6)
          ctx.shadowColor = withAlpha(s.colorCore, 0.95)
          ctx.globalAlpha = 1
          ctx.fillStyle = withAlpha(s.colorCore, alpha)
          ctx.beginPath()
          ctx.arc(headX, headY, r, 0, Math.PI * 2)
          ctx.fill()

          ctx.shadowBlur = Math.max(12, r * 7)
          ctx.shadowColor = withAlpha(s.colorTrail, 0.9)
          ctx.strokeStyle = withAlpha(s.colorCore, alpha)
          ctx.lineWidth = Math.max(1.0, s.thickness * 1.1)

          // Along direction
          ctx.beginPath()
          ctx.moveTo(headX - ux * arm, headY - uy * arm)
          ctx.lineTo(headX + ux * arm, headY + uy * arm)
          ctx.stroke()

          // Perpendicular
          ctx.beginPath()
          ctx.moveTo(headX - px * arm * 0.65, headY - py * arm * 0.65)
          ctx.lineTo(headX + px * arm * 0.65, headY + py * arm * 0.65)
          ctx.stroke()
        }

        ctx.restore()
        continue
      }

      const phase = ((s.seed % 1000) / 1000) * 0.35
      const t = clamp01(t0 + phase)

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

      const x = start.x + dx * t + px * wobble
      const y = start.y + dy * t + py * wobble

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
      const alpha = Math.max(0, 1 - t0)

      if (b.kind === 'tx-impact') {
        const { w: nw, h: nh } = sizeForNode(n)
        const nodeR = Math.max(nw, nh) / 2

        const ringR = nodeR * (1.05 + Math.pow(t0, 0.55) * 1.65)
        const ringW = Math.max(1, (1 - t0) * 3.0)

        ctx.save()
        ctx.globalCompositeOperation = 'lighter'

        // Rim flash (outline glow)
        {
          ctx.globalAlpha = alpha
          ctx.shadowBlur = Math.max(12, nodeR * 1.1) * alpha
          ctx.shadowColor = withAlpha(b.color, 0.95)
          ctx.strokeStyle = withAlpha(b.color, 0.95)
          ctx.lineWidth = Math.max(1.2, nodeR * 0.12)
          nodeOutlinePath(ctx, n, 1)
          ctx.stroke()
        }

        // Shockwave ring
        {
          ctx.shadowBlur = 0
          ctx.globalAlpha = alpha * 0.85
          ctx.strokeStyle = withAlpha(b.color, 0.9)
          ctx.lineWidth = ringW
          ctx.beginPath()
          ctx.arc(n.__x, n.__y, ringR, 0, Math.PI * 2)
          ctx.stroke()
        }

        // Small center bloom (subtle)
        {
          ctx.globalAlpha = alpha * 0.45
          ctx.shadowBlur = 24 * alpha
          ctx.shadowColor = withAlpha(b.color, 0.9)
          ctx.fillStyle = withAlpha(b.color, 0.25)
          ctx.beginPath()
          ctx.arc(n.__x, n.__y, nodeR * 0.55, 0, Math.PI * 2)
          ctx.fill()
        }

        ctx.restore()
      } else {
        // Default (clearing) burst: bloom + ring
        const r = 10 + Math.pow(t0, 0.4) * 35

        ctx.save()
        ctx.globalCompositeOperation = 'screen'

        // 1. Core bloom
        ctx.globalAlpha = alpha
        ctx.fillStyle = withAlpha(b.color, 0.5)
        ctx.shadowBlur = 30 * alpha
        ctx.shadowColor = b.color
        ctx.beginPath()
        ctx.arc(n.__x, n.__y, r * 0.5, 0, Math.PI * 2)
        ctx.fill()

        // 2. Shockwave
        ctx.globalAlpha = alpha * 0.7
        ctx.strokeStyle = b.color
        ctx.lineWidth = 3 * (1 - t0)
        ctx.beginPath()
        ctx.arc(n.__x, n.__y, r, 0, Math.PI * 2)
        ctx.stroke()

        ctx.restore()
      }
    }
    fxState.nodeBursts.length = write
  }

  return { flash }
}
