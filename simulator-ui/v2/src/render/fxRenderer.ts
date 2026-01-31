/**
 * FX Renderer — Visual Effects Layer for Graph Animations
 * ========================================================
 *
 * This module provides a particle/effect system rendered on a separate canvas
 * layer above the base graph. It supports three types of effects:
 *
 * 1. **Sparks** (`FxSpark`, `spawnSparks`)
 *    - Moving particles that travel along edges from source to target
 *    - Two styles: 'comet' (wobbly trail) and 'beam' (straight line + glowing dot head)
 *    - Use for: transaction animations, clearing micro-transactions
 *    - NOTE: 'beam' style renders trail from source to head position
 *
 * 2. **Edge Pulses** (`FxEdgePulse`, `spawnEdgePulses`)
 *    - Soft glow traveling along an edge with fading trail
 *    - Use for: highlighting cycle paths, showing routes (without spark head)
 *    - NOTE: Do NOT combine with 'beam' sparks on same edge — causes double animation
 *
 * 3. **Node Bursts** (`FxNodeBurst`, `spawnNodeBursts`)
 *    - Expanding/fading effects centered on nodes
 *    - Styles: 'glow' (soft circle), 'tx-impact' (rim + shockwave), 'clearing' (bloom + ring)
 *    - Use for: impact bursts when spark arrives, highlighting nodes
 *    - NOTE: Global (screen-space) flash overlay is handled in App.vue, not here.
 *
 * Animation Pattern for Edge Transactions:
 * ----------------------------------------
 * For a single edge animation (tx or clearing micro-tx):
 *   1. spawnNodeBursts(source, 'glow') — optional source highlight
 *   2. spawnSparks(edge, 'beam') — spark flies, beam renders edge glow
 *   3. After ttlMs: spawnNodeBursts(target, 'glow'/'tx-impact') — impact flash
 *
 * DO NOT add spawnEdgePulses when using 'beam' sparks — the beam already
 * renders the edge glow internally. EdgePulses are for standalone edge
 * highlighting (e.g., showing a cycle path before animation starts).
 *
 * Color Conventions:
 * ------------------
 * - Cyan (#22d3ee): Single transactions (tx.updated)
 * - Gold (#fbbf24): Clearing operations
 * - Use colorCore for spark head, colorTrail for beam/trail
 */

import type { VizMapping } from '../vizMapping'
import type { LayoutNode } from './nodePainter'
import { withAlpha } from './color'
import { getLinkTermination } from './linkGeometry'
import { roundedRectPath, roundedRectPath2D } from './roundedRect'
import { sizeForNode } from './nodePainter'

const clamp01 = (v: number) => Math.max(0, Math.min(1, v))

function worldRectForCanvas(ctx: CanvasRenderingContext2D, w: number, h: number, padPx = 96) {
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
    return { x: -1e6, y: -1e6, w: 2e6, h: 2e6 }
  }

  return { x: minX, y: minY, w: maxX - minX, h: maxY - minY }
}

function nodeOutlinePath(ctx: CanvasRenderingContext2D, n: LayoutNode, scale = 1, invZoom = 1) {
  const { w: w0, h: h0 } = sizeForNode(n)
  const w = w0 * invZoom
  const h = h0 * invZoom
  const isBusiness = String(n.type) === 'business'
  const rr = Math.max(0, Math.min(4 * invZoom, Math.min(w, h) * 0.18))
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

function nodeOutlinePath2D(n: LayoutNode, scale = 1, invZoom = 1) {
  const { w: w0, h: h0 } = sizeForNode(n)
  const w = w0 * invZoom
  const h = h0 * invZoom
  const isBusiness = String(n.type) === 'business'
  const rr = Math.max(0, Math.min(4 * invZoom, Math.min(w, h) * 0.18))
  const ww = w * scale
  const hh = h * scale
  const x = n.__x - ww / 2
  const y = n.__y - hh / 2

  if (isBusiness) return roundedRectPath2D(x, y, ww, hh, rr * scale)

  const p = new Path2D()
  const r = Math.max(ww, hh) / 2
  p.arc(n.__x, n.__y, r, 0, Math.PI * 2)
  return p
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
  kind: 'clearing' | 'tx-impact' | 'glow'
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
  isTestMode: boolean
  cameraZoom?: number
  quality?: 'low' | 'med' | 'high'
}): void {
  const { nowMs, ctx, pos, w, h, mapping, fxState, isTestMode } = opts
  const z = Math.max(0.01, Number(opts.cameraZoom ?? 1))
  const invZ = 1 / z
  const spx = (v: number) => v * invZ
  const q = opts.quality ?? 'high'
  const blurK = q === 'high' ? 1 : q === 'med' ? 0.75 : 0
  const allowGradients = q === 'high'

  // Lazily computed world-space view rect (getTransform().inverse is not cheap).
  let cachedWorldView: { x: number; y: number; w: number; h: number } | null = null
  const worldView = () => (cachedWorldView ??= worldRectForCanvas(ctx, w, h))

  // NOTE: Test mode primarily aims to make screenshot tests stable by not spawning FX.
  // Rendering stays enabled so manual interaction in test mode doesn't feel "dead".

  // NOTE: `withAlpha` and helpers are module-scoped with caching (perf).

  if (fxState.sparks.length === 0 && fxState.edgePulses.length === 0 && fxState.nodeBursts.length === 0) return

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

      const start = getLinkTermination(a, b, invZ)
      const end = getLinkTermination(b, a, invZ)

      const dx = end.x - start.x
      const dy = end.y - start.y
      const len = Math.max(1e-6, Math.hypot(dx, dy))
      const ux = dx / len
      const uy = dy / len

      if (s.kind === 'beam') {
        const t = easeOutCubic(t0)
        const life = 1 - t0
        const alpha = clamp01(life)

        const th = s.thickness * invZ

        const headX = start.x + dx * t
        const headY = start.y + dy * t

        ctx.save()
        ctx.globalCompositeOperation = 'lighter'
        ctx.lineCap = 'round'
        ctx.lineJoin = 'round'

        // Trail beam (from start to head) — thin core + soft halo
        {
          const baseAlpha = Math.max(0, Math.min(1, alpha * 0.55))
          const th = s.thickness * invZ

          // Halo pass (only from start to head, not full edge)
          ctx.globalAlpha = baseAlpha * 0.55
          ctx.strokeStyle = s.colorTrail
          ctx.lineWidth = Math.max(spx(1.2), th * 3.2)
          ctx.shadowBlur = Math.max(spx(10), th * 18) * blurK
          ctx.shadowColor = withAlpha(s.colorTrail, 0.85)
          ctx.beginPath()
          ctx.moveTo(start.x, start.y)
          ctx.lineTo(headX, headY)
          ctx.stroke()

          // Core pass (only from start to head)
          ctx.shadowBlur = 0
          ctx.globalAlpha = baseAlpha
          ctx.strokeStyle = withAlpha(s.colorTrail, 0.9)
          ctx.lineWidth = Math.max(spx(0.9), th * 1.25)
          ctx.beginPath()
          ctx.moveTo(start.x, start.y)
          ctx.lineTo(headX, headY)
          ctx.stroke()
        }

        // Moving bright “packet” segment near the head
        {
          const segLen = Math.max(spx(18), Math.min(spx(54), len * 0.22))
          const tailX = headX - ux * segLen
          const tailY = headY - uy * segLen
          ctx.globalAlpha = 1
          if (allowGradients) {
            const grad = ctx.createLinearGradient(headX, headY, tailX, tailY)
            grad.addColorStop(0, withAlpha(s.colorCore, alpha * 1.0))
            grad.addColorStop(0.35, withAlpha(s.colorTrail, alpha * 0.55))
            grad.addColorStop(1, withAlpha(s.colorTrail, 0))
            ctx.strokeStyle = grad
          } else {
            ctx.strokeStyle = withAlpha(s.colorCore, alpha * 0.65)
          }
          ctx.lineWidth = Math.max(spx(1.4), th * 4.2)
          ctx.shadowBlur = Math.max(spx(12), th * 20) * blurK
          ctx.shadowColor = withAlpha(s.colorTrail, 0.9)
          ctx.beginPath()
          ctx.moveTo(tailX, tailY)
          ctx.lineTo(headX, headY)
          ctx.stroke()
        }

        // Head: soft glowing dot (no star/cross spikes)
        {
          const r = Math.max(spx(2.2), th * 3.2)

          ctx.shadowBlur = Math.max(spx(14), r * 5) * blurK
          ctx.shadowColor = withAlpha(s.colorCore, 0.95)
          ctx.globalAlpha = 1
          ctx.fillStyle = withAlpha(s.colorCore, alpha)
          ctx.beginPath()
          ctx.arc(headX, headY, r, 0, Math.PI * 2)
          ctx.fill()
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
      const wobbleAmp = (spx(2.0) + (s.thickness * invZ) * 2.5) * (1 - t0)
      const perpX = -uy
      const perpY = ux
      const wobble = Math.sin((t * Math.PI * 2 * wobbleFreq) + seed01 * 11.3) * wobbleAmp

      const x = start.x + dx * t + perpX * wobble
      const y = start.y + dy * t + perpY * wobble

      const tailX = x - ux * trailLen
      const tailY = y - uy * trailLen

      ctx.save()
      ctx.globalCompositeOperation = 'lighter'

      const life = 1 - t0
      const alphaTrail = Math.max(0, Math.min(1, life * 0.75))
      const alphaCore = Math.max(0, Math.min(1, life * 0.95))

      // Trail (glow pass)
      {
        const th = s.thickness * invZ
        ctx.globalAlpha = 1
        if (allowGradients) {
          const grad = ctx.createLinearGradient(x, y, tailX, tailY)
          grad.addColorStop(0, withAlpha(s.colorTrail, alphaTrail * 0.9))
          grad.addColorStop(0.25, withAlpha(s.colorTrail, alphaTrail * 0.55))
          grad.addColorStop(1, withAlpha(s.colorTrail, 0))
          ctx.strokeStyle = grad
        } else {
          ctx.strokeStyle = withAlpha(s.colorTrail, alphaTrail * 0.75)
        }
        ctx.lineCap = 'round'
        ctx.lineJoin = 'round'
        ctx.lineWidth = Math.max(spx(1.2), th * 3.0)
        ctx.shadowBlur = Math.max(spx(6), th * 10) * blurK
        ctx.shadowColor = withAlpha(s.colorTrail, 0.85)
        ctx.beginPath()
        ctx.moveTo(tailX, tailY)
        ctx.lineTo(x, y)
        ctx.stroke()
      }

      // Trail (core pass)
      {
        const th = s.thickness * invZ
        ctx.shadowBlur = 0
        ctx.globalAlpha = 1
        if (allowGradients) {
          const grad = ctx.createLinearGradient(x, y, tailX, tailY)
          grad.addColorStop(0, withAlpha(s.colorTrail, alphaTrail))
          grad.addColorStop(0.35, withAlpha(s.colorTrail, alphaTrail * 0.35))
          grad.addColorStop(1, withAlpha(s.colorTrail, 0))
          ctx.strokeStyle = grad
        } else {
          ctx.strokeStyle = withAlpha(s.colorTrail, alphaTrail * 0.85)
        }
        ctx.lineWidth = Math.max(spx(0.9), th * 1.9)
        ctx.beginPath()
        ctx.moveTo(tailX, tailY)
        ctx.lineTo(x, y)
        ctx.stroke()
      }

      // Head core with soft bloom
      {
        const th = s.thickness * invZ
        const r = Math.max(spx(1.6), th * 2.4)
        ctx.globalAlpha = 1
        ctx.shadowBlur = Math.max(spx(10), r * 6) * blurK
        ctx.shadowColor = withAlpha(s.colorCore, 0.9)
        ctx.fillStyle = withAlpha(s.colorCore, alphaCore)
        ctx.beginPath()
        ctx.arc(x, y, r, 0, Math.PI * 2)
        ctx.fill()
      }

      // Small embers behind the head
      {
        const th = s.thickness * invZ
        ctx.shadowBlur = 0
        ctx.fillStyle = withAlpha(s.colorTrail, alphaTrail * 0.55)
        for (let j = 1; j <= 3; j++) {
          const tt = j / 4
          const ex = x - ux * trailLen * tt
          const ey = y - uy * trailLen * tt
          const rr = Math.max(spx(0.8), th * (1.25 - tt * 0.7))
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
      ctx.lineWidth = Math.max(spx(1.0), (p.thickness * invZ) * 1.1)
      ctx.beginPath()
      ctx.moveTo(a.__x, a.__y)
      ctx.lineTo(b.__x, b.__y)
      ctx.stroke()

      // Moving pulse head + tail.
      ctx.globalAlpha = 1
      if (allowGradients) {
        const grad = ctx.createLinearGradient(x, y, tailX, tailY)
        grad.addColorStop(0, withAlpha(p.color, alpha * 0.95))
        grad.addColorStop(0.35, withAlpha(p.color, alpha * 0.35))
        grad.addColorStop(1, withAlpha(p.color, 0))
        ctx.strokeStyle = grad
      } else {
        ctx.strokeStyle = withAlpha(p.color, alpha * 0.85)
      }
      ctx.lineWidth = Math.max(spx(1.2), (p.thickness * invZ) * 2.8)
      ctx.shadowBlur = Math.max(spx(10), (p.thickness * invZ) * 14) * blurK
      ctx.shadowColor = withAlpha(p.color, 0.9)
      ctx.beginPath()
      ctx.moveTo(tailX, tailY)
      ctx.lineTo(x, y)
      ctx.stroke()

      ctx.shadowBlur = 0
      ctx.globalAlpha = alpha
      ctx.fillStyle = p.color
      ctx.beginPath()
      ctx.arc(x, y, Math.max(spx(1.5), (p.thickness * invZ) * 2.2), 0, Math.PI * 2)
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
      const alpha = Math.pow(1 - t0, 1.2) // Smooth decay

      if (b.kind === 'tx-impact') {
        // Uniform contour glow: the outline itself glows evenly around the perimeter.
        // We draw multiple strokes with increasing blur/thickness to create a soft aura.
        const { w: nw0, h: nh0 } = sizeForNode(n)
        const nw = nw0 * invZ
        const nh = nh0 * invZ
        const nodeR = Math.max(nw, nh) / 2

        ctx.save()
        ctx.globalCompositeOperation = 'lighter'

        // Clip to outside of node so interior stays dark.
        const outside = new Path2D()
        const view = worldView()
        outside.rect(view.x, view.y, view.w, view.h)
        outside.addPath(nodeOutlinePath2D(n, 1.0, invZ))
        ctx.clip(outside, 'evenodd')

        const outline = nodeOutlinePath2D(n, 1.0, invZ)
        const baseWidth = Math.max(spx(2), nodeR * 0.15)

        // Layer 1: Wide outer glow (largest blur, lowest alpha)
        ctx.shadowColor = b.color
        ctx.shadowBlur = Math.max(spx(12), nodeR * 0.8) * alpha * blurK
        ctx.strokeStyle = withAlpha(b.color, 0.4 * alpha)
        ctx.lineWidth = baseWidth * 3
        ctx.stroke(outline)

        // Layer 2: Medium glow
        ctx.shadowBlur = Math.max(spx(8), nodeR * 0.5) * alpha * blurK
        ctx.strokeStyle = withAlpha(b.color, 0.6 * alpha)
        ctx.lineWidth = baseWidth * 1.8
        ctx.stroke(outline)

        // Layer 3: Bright inner stroke (crisp edge)
        ctx.shadowBlur = Math.max(spx(4), nodeR * 0.25) * alpha * blurK
        ctx.strokeStyle = withAlpha(b.color, 0.9 * alpha)
        ctx.lineWidth = baseWidth
        ctx.stroke(outline)

        // Layer 4: Hot white core (very thin, bright)
        ctx.shadowBlur = 0
        ctx.strokeStyle = withAlpha('#ffffff', 0.7 * alpha)
        ctx.lineWidth = Math.max(spx(1), baseWidth * 0.4)
        ctx.stroke(outline)

        ctx.restore()
      } else if (b.kind === 'glow') {
        // Soft blurred circle glow (no rim, no hard ring).
        const { w: nw0, h: nh0 } = sizeForNode(n)
        const nw = nw0 * invZ
        const nh = nh0 * invZ
        const nodeR = Math.max(nw, nh) / 2

        const life = Math.max(0, 1 - t0)
        const a = Math.max(0, Math.min(1, life * life))
        const r = nodeR * (0.75 + Math.pow(t0, 0.6) * 2.0)

        ctx.save()
        ctx.globalCompositeOperation = 'screen'
        ctx.globalAlpha = 1

        const grad = ctx.createRadialGradient(n.__x, n.__y, 0, n.__x, n.__y, r)
        grad.addColorStop(0, withAlpha(b.color, 0.55 * a))
        grad.addColorStop(0.35, withAlpha(b.color, 0.22 * a))
        grad.addColorStop(1, withAlpha(b.color, 0))

        ctx.fillStyle = grad
        ctx.shadowBlur = Math.max(spx(18), nodeR * 1.4) * a * blurK
        ctx.shadowColor = withAlpha(b.color, 0.9)
        ctx.beginPath()
        ctx.arc(n.__x, n.__y, r, 0, Math.PI * 2)
        ctx.fill()

        ctx.restore()
      } else {
        // Default (clearing) burst: bloom + ring
        const r = spx(10) + Math.pow(t0, 0.4) * spx(35)

        ctx.save()
        ctx.globalCompositeOperation = 'screen'

        // 1. Core bloom
        ctx.globalAlpha = alpha
        ctx.fillStyle = withAlpha(b.color, 0.5)
        ctx.shadowBlur = spx(30) * alpha * blurK
        ctx.shadowColor = b.color
        ctx.beginPath()
        ctx.arc(n.__x, n.__y, r * 0.5, 0, Math.PI * 2)
        ctx.fill()

        // 2. Shockwave
        ctx.globalAlpha = alpha * 0.7
        ctx.strokeStyle = b.color
        ctx.lineWidth = spx(3) * (1 - t0)
        ctx.beginPath()
        ctx.arc(n.__x, n.__y, r, 0, Math.PI * 2)
        ctx.stroke()

        ctx.restore()
      }
    }
    fxState.nodeBursts.length = write
  }

}
