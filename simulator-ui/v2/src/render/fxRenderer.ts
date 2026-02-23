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
import { clamp01 } from '../utils/math'
import { getNodeShape } from '../types/nodeShape'
import { withAlpha } from './color'
import { drawGlowSprite } from './glowSprites'
import { getLinkTermination } from './linkGeometry'
import { roundedRectPath, roundedRectPath2D } from './roundedRect'
import { sizeForNode } from './nodePainter'

// Persistent Path2D cache: survives multiple frames to avoid rebuilding on every tick.
// Key includes rounded position (1px grid) so physics micro-movements don't thrash the cache.
// LRU with size cap; invalidated on snapshot change (see renderFxFrame opts.snapshotKey).
const MAX_NODE_OUTLINE_CACHE = 512
const nodeOutlinePath2DCache = new Map<string, Path2D>()
let _nodeOutlineCacheSnapshotKey: string | null | undefined = undefined

/**
 * Reset module-level caches used by the FX renderer.
 *
 * This is a runtime lifecycle hook for scene/app unmounts to prevent cache data
 * from living longer than the owning component.
 *
 * Idempotent by design.
 */
export function resetFxRendererCaches(): void {
  nodeOutlinePath2DCache.clear()
  _nodeOutlineCacheSnapshotKey = undefined
}

/**
 * Minimal internal hooks for unit tests.
 * Keep surface area small: sizes + a warmup entrypoint.
 */
export const __testing = {
  _nodeOutlinePath2DCacheSize(): number {
    return nodeOutlinePath2DCache.size
  },
  _nodeOutlineCacheSnapshotKey(): string | null | undefined {
    return _nodeOutlineCacheSnapshotKey
  },
  _warmNodeOutlinePath2DCache(n: LayoutNode, scale = 1, invZoom = 1): void {
    // Ensure at least one cache entry is created.
    void nodeOutlinePath2D(n, scale, invZoom)
  },
}

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
  const shapeKey = getNodeShape(n) ?? 'circle'
  const isRoundedRect = shapeKey === 'rounded-rect'
  const rr = Math.max(0, Math.min(4 * invZoom, Math.min(w, h) * 0.18))
  const ww = w * scale
  const hh = h * scale
  const x = n.__x - ww / 2
  const y = n.__y - hh / 2

  if (isRoundedRect) {
    roundedRectPath(ctx, x, y, ww, hh, rr * scale)
    return
  }

  const r = Math.max(ww, hh) / 2
  ctx.beginPath()
  ctx.arc(n.__x, n.__y, r, 0, Math.PI * 2)
}

function nodeOutlinePath2D(n: LayoutNode, scale = 1, invZoom = 1) {
  // Cache across frames: key uses rounded positions (1px grid) to maximise hit rate
  // during physics micro-movements while still detecting real positional changes.
  const shapeKeyForCache = getNodeShape(n) ?? ''
  const cacheKey = `${n.id}|${Math.round(n.__x)}|${Math.round(n.__y)}|${shapeKeyForCache}|${Math.round(scale * 100)}|${Math.round(invZoom * 1000)}`
  const cached = nodeOutlinePath2DCache.get(cacheKey)
  if (cached) {
    // Touch for LRU.
    nodeOutlinePath2DCache.delete(cacheKey)
    nodeOutlinePath2DCache.set(cacheKey, cached)
    return cached
  }

  const { w: w0, h: h0 } = sizeForNode(n)
  const w = w0 * invZoom
  const h = h0 * invZoom
  const shapeKey = getNodeShape(n) ?? 'circle'
  const isRoundedRect = shapeKey === 'rounded-rect'
  const rr = Math.max(0, Math.min(4 * invZoom, Math.min(w, h) * 0.18))
  const ww = w * scale
  const hh = h * scale
  const x = n.__x - ww / 2
  const y = n.__y - hh / 2

  let p: Path2D
  if (isRoundedRect) {
    p = roundedRectPath2D(x, y, ww, hh, rr * scale)
  } else {
    p = new Path2D()
    const r = Math.max(ww, hh) / 2
    p.arc(n.__x, n.__y, r, 0, Math.PI * 2)
  }
  nodeOutlinePath2DCache.set(cacheKey, p)
  // LRU trim: evict oldest entries when over capacity.
  while (nodeOutlinePath2DCache.size > MAX_NODE_OUTLINE_CACHE) {
    const first = nodeOutlinePath2DCache.keys().next().value as string | undefined
    if (first !== undefined) nodeOutlinePath2DCache.delete(first)
    else break
  }
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

  // Optional runtime-only cap to keep FX bounded (set by render loop).
  __maxParticles?: number

  // Optional runtime-only telemetry / knobs (set by render loop).
  __fxBudgetScale?: number
  __lastFps?: number

  // Optional runtime-only render knobs (set by render loop).
  __renderQuality?: 'low' | 'med' | 'high'
  __dprClamp?: number
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

  const max =
    typeof fxState.__maxParticles === 'number' && Number.isFinite(fxState.__maxParticles)
      ? Math.max(0, Math.floor(fxState.__maxParticles))
      : null
  let budget =
    max === null
      ? Number.POSITIVE_INFINITY
      : Math.max(0, max - (fxState.sparks.length + fxState.edgePulses.length + fxState.nodeBursts.length))
  if (budget <= 0) return

  const { edges, nowMs, durationMs, color, thickness, seedPrefix, countPerEdge, keyEdge, seedFn } = opts
  for (const e of edges) {
    const k = keyEdge(e.from, e.to)
    for (let i = 0; i < Math.max(1, countPerEdge); i++) {
      if (budget-- <= 0) return
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

  const max =
    typeof fxState.__maxParticles === 'number' && Number.isFinite(fxState.__maxParticles)
      ? Math.max(0, Math.floor(fxState.__maxParticles))
      : null
  let budget =
    max === null
      ? Number.POSITIVE_INFINITY
      : Math.max(0, max - (fxState.sparks.length + fxState.edgePulses.length + fxState.nodeBursts.length))
  if (budget <= 0) return

  const { nodeIds, nowMs, durationMs, color, seedPrefix, seedFn } = opts
  const kind = opts.kind ?? 'clearing'
  for (const id of nodeIds) {
    if (budget-- <= 0) return
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

  const max =
    typeof fxState.__maxParticles === 'number' && Number.isFinite(fxState.__maxParticles)
      ? Math.max(0, Math.floor(fxState.__maxParticles))
      : null
  let budget =
    max === null
      ? Number.POSITIVE_INFINITY
      : Math.max(0, max - (fxState.sparks.length + fxState.edgePulses.length + fxState.nodeBursts.length))
  if (budget <= 0) return

  const { edges, nowMs, ttlMs, colorCore, colorTrail, thickness, seedPrefix, countPerEdge, keyEdge, seedFn } = opts
  const kind = opts.kind ?? 'comet'
  for (const e of edges) {
    const k = keyEdge(e.from, e.to)
    for (let i = 0; i < Math.max(1, countPerEdge); i++) {
      if (budget-- <= 0) return
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
  /** Pass snapshot identity key so the Path2D cache can be invalidated on scene changes. */
  snapshotKey?: string | null
}): void {
  const { nowMs, ctx, pos, w, h, mapping, fxState, isTestMode } = opts
  const z = Math.max(0.01, Number(opts.cameraZoom ?? 1))
  const invZ = 1 / z
  const spx = (v: number) => v * invZ
  const q = opts.quality ?? 'high'
  // Keep the same visuals as before for each quality preset,
  // but remove Interaction Quality dependencies from the FX stack.
  // Low quality should still keep a minimal blur so FX sprites don't degrade into hard geometry.
  const shadowBlurK = q === 'high' ? 1 : q === 'med' ? 0.75 : 0.3

  // Snapshot-based Path2D cache invalidation: clear only when snapshot identity changes,
  // NOT every frame (per-frame clear was negating the entire cache benefit).
  const sk = opts.snapshotKey
  if (sk !== undefined && sk !== _nodeOutlineCacheSnapshotKey) {
    nodeOutlinePath2DCache.clear()
    _nodeOutlineCacheSnapshotKey = sk ?? null
  }

  // Lazily computed world-space view rect (getTransform().inverse is not cheap).
  let cachedWorldView: { x: number; y: number; w: number; h: number } | null = null
  const worldView = () => (cachedWorldView ??= worldRectForCanvas(ctx, w, h))

  // NOTE: Test mode primarily aims to make screenshot tests stable by not spawning FX.
  // Rendering stays enabled so manual interaction in test mode doesn't feel "dead".

  // NOTE: `withAlpha` and helpers are module-scoped with caching (perf).

  if (fxState.sparks.length === 0 && fxState.edgePulses.length === 0 && fxState.nodeBursts.length === 0) return

  // FX are always rendered (no early return on interaction).

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

        // Beam head uses easing (fast early, slow near the end).
        // Trail has a maximum length and shrinks as head approaches target (meteor effect).
        const lifePos = Math.max(0, 1 - t)
        const alpha = clamp01(Math.pow(lifePos, 1.2))

        const th = s.thickness * invZ

        const headX = start.x + dx * t
        const headY = start.y + dy * t

        // Trail length: limited so beam doesn't span the entire edge.
        // As head approaches target, trail shrinks proportionally.
        const maxTrailFraction = 0.85 // Max trail = 85% of edge length (longer for better visibility)
        const maxTrailLen = len * maxTrailFraction
        const distanceTraveled = len * t
        // Trail shrinks as we approach the end (last 30% of journey)
        const shrinkStart = 0.7
        const shrinkFactor = t > shrinkStart ? 1 - (t - shrinkStart) / (1 - shrinkStart) : 1
        const currentTrailLen = Math.min(maxTrailLen, distanceTraveled) * shrinkFactor

        // Trail start point (follows behind head)
        const trailStartT = Math.max(0, t - currentTrailLen / Math.max(1e-6, len))
        const trailStartX = start.x + dx * trailStartT
        const trailStartY = start.y + dy * trailStartT

        // Low quality: only draw the head dot — skip all trail gradient work.
        if (q === 'low') {
          const r = Math.max(spx(3.0), th * 4.2)
          ctx.save()
          ctx.globalCompositeOperation = 'lighter'
          ctx.globalAlpha = alpha
          drawGlowSprite(ctx, {
            kind: 'fx-dot',
            x: headX,
            y: headY,
            color: s.colorCore,
            r,
            blurPx: Math.max(spx(16), r * 5) * shadowBlurK,
            composite: 'lighter',
          })
          ctx.restore()
          continue
        }

        // Med quality: simplified solid-color trail (no gradient objects).
        if (q === 'med') {
          const baseAlpha = Math.max(0, Math.min(1, alpha * 0.55))
          ctx.save()
          ctx.globalCompositeOperation = 'lighter'
          ctx.lineCap = 'round'
          ctx.lineJoin = 'round'
          ctx.strokeStyle = s.colorTrail
          ctx.lineWidth = Math.max(spx(1.8), th * 4.6)
          ctx.globalAlpha = 0.9 * baseAlpha
          ctx.beginPath()
          ctx.moveTo(trailStartX, trailStartY)
          ctx.lineTo(headX, headY)
          ctx.stroke()
          ctx.lineWidth = Math.max(spx(0.9), th * 1.25)
          ctx.globalAlpha = baseAlpha
          ctx.beginPath()
          ctx.moveTo(trailStartX, trailStartY)
          ctx.lineTo(headX, headY)
          ctx.stroke()
          const r = Math.max(spx(3.0), th * 4.2)
          ctx.globalAlpha = alpha
          drawGlowSprite(ctx, {
            kind: 'fx-dot',
            x: headX,
            y: headY,
            color: s.colorCore,
            r,
            blurPx: Math.max(spx(16), r * 5) * shadowBlurK,
            composite: 'lighter',
          })
          ctx.restore()
          continue
        }

        // High quality: full gradient trail.
        ctx.save()
        ctx.globalCompositeOperation = 'lighter'
        ctx.lineCap = 'round'
        ctx.lineJoin = 'round'

        // Trail beam (from trailStart to head) — thin core + soft halo
        // Now the trail is limited in length and shrinks at the end.
        {
          const baseAlpha = Math.max(0, Math.min(1, alpha * 0.55))

          const trailStroke = (() => {
            const g = ctx.createLinearGradient(trailStartX, trailStartY, headX, headY)
            // Gradient from tail (transparent) to head (bright)
            g.addColorStop(0, withAlpha(s.colorTrail, 0))
            g.addColorStop(0.5, withAlpha(s.colorTrail, baseAlpha * 0.35))
            g.addColorStop(1, withAlpha(s.colorTrail, baseAlpha))
            return g
          })()

          // Halo approximation: thicker stroke + `lighter` (no on-screen shadowBlur).
          ctx.globalAlpha = 0.9
          ctx.strokeStyle = trailStroke
          ctx.lineWidth = Math.max(spx(1.8), th * 4.6)
          ctx.beginPath()
          ctx.moveTo(trailStartX, trailStartY)
          ctx.lineTo(headX, headY)
          ctx.stroke()

          // Core pass (from trailStart to head)
          ctx.globalAlpha = 1
          ctx.strokeStyle = trailStroke
          ctx.lineWidth = Math.max(spx(0.9), th * 1.25)
          ctx.beginPath()
          ctx.moveTo(trailStartX, trailStartY)
          ctx.lineTo(headX, headY)
          ctx.stroke()
        }

        // Moving bright "packet" segment near the head
        {
          const segLen = Math.max(spx(18), Math.min(spx(54), len * 0.22))
          const tailX = headX - ux * segLen
          const tailY = headY - uy * segLen
          ctx.globalAlpha = 1
          const grad = ctx.createLinearGradient(headX, headY, tailX, tailY)
          grad.addColorStop(0, withAlpha(s.colorCore, alpha * 1.0))
          grad.addColorStop(0.35, withAlpha(s.colorTrail, alpha * 0.55))
          grad.addColorStop(1, withAlpha(s.colorTrail, 0))
          ctx.strokeStyle = grad
          // Halo approximation: thicker segment stroke (no on-screen shadowBlur).
          ctx.lineWidth = Math.max(spx(2.0), th * 5.2)
          ctx.beginPath()
          ctx.moveTo(tailX, tailY)
          ctx.lineTo(headX, headY)
          ctx.stroke()

          // Core segment pass
          ctx.lineWidth = Math.max(spx(1.2), th * 3.0)
          ctx.beginPath()
          ctx.moveTo(tailX, tailY)
          ctx.lineTo(headX, headY)
          ctx.stroke()
        }

        // Head: soft glowing dot (no star/cross spikes)
        {
          const r = Math.max(spx(3.0), th * 4.2)

          // Replace arc+shadowBlur with pre-rendered FX dot sprite.
          ctx.globalAlpha = alpha
          drawGlowSprite(ctx, {
            kind: 'fx-dot',
            x: headX,
            y: headY,
            color: s.colorCore,
            r,
            blurPx: Math.max(spx(16), r * 5) * shadowBlurK,
            composite: 'lighter',
          })
        }

        ctx.restore()
        continue
      }

      const phase = ((s.seed % 1000) / 1000) * 0.35
      const t = clamp01(t0 + phase)

      // Comet-like spark: trail length depends on edge length and ttl.
      // Small ttl => faster head => longer trail.
      const speedPxPerMs = len / Math.max(1, s.ttlMs)
      const trailTimeMs = Math.max(150, Math.min(500, s.ttlMs * 0.35))
      const trailLen = Math.max(16, Math.min(len * 0.75, speedPxPerMs * trailTimeMs))

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

      const life = 1 - t0
      const alphaTrail = Math.max(0, Math.min(1, life * 0.75))
      const alphaCore = Math.max(0, Math.min(1, life * 0.95))

      // Low quality: only draw the head dot — skip all trail gradient work.
      if (q === 'low') {
        const th = s.thickness * invZ
        const r = Math.max(spx(1.6), th * 2.4)
        ctx.save()
        ctx.globalCompositeOperation = 'lighter'
        ctx.globalAlpha = alphaCore
        drawGlowSprite(ctx, {
          kind: 'fx-dot',
          x,
          y,
          color: s.colorCore,
          r,
          blurPx: Math.max(spx(10), r * 6) * shadowBlurK,
          composite: 'lighter',
        })
        ctx.restore()
        continue
      }

      // Med quality: simplified solid-color trail (no gradient objects).
      if (q === 'med') {
        const th = s.thickness * invZ
        ctx.save()
        ctx.globalCompositeOperation = 'lighter'
        ctx.lineCap = 'round'
        ctx.lineJoin = 'round'
        ctx.strokeStyle = s.colorTrail
        ctx.lineWidth = Math.max(spx(1.8), th * 4.2)
        ctx.globalAlpha = alphaTrail * 0.9
        ctx.beginPath()
        ctx.moveTo(tailX, tailY)
        ctx.lineTo(x, y)
        ctx.stroke()
        ctx.lineWidth = Math.max(spx(0.9), th * 1.9)
        ctx.globalAlpha = alphaTrail
        ctx.beginPath()
        ctx.moveTo(tailX, tailY)
        ctx.lineTo(x, y)
        ctx.stroke()
        const r = Math.max(spx(1.6), th * 2.4)
        ctx.globalAlpha = alphaCore
        drawGlowSprite(ctx, {
          kind: 'fx-dot',
          x,
          y,
          color: s.colorCore,
          r,
          blurPx: Math.max(spx(10), r * 6) * shadowBlurK,
          composite: 'lighter',
        })
        ctx.restore()
        continue
      }

      // High quality: full gradient trail.
      ctx.save()
      ctx.globalCompositeOperation = 'lighter'

      // Trail (glow pass)
      {
        const th = s.thickness * invZ
        ctx.globalAlpha = 1
        const grad = ctx.createLinearGradient(x, y, tailX, tailY)
        grad.addColorStop(0, withAlpha(s.colorTrail, alphaTrail * 0.9))
        grad.addColorStop(0.25, withAlpha(s.colorTrail, alphaTrail * 0.55))
        grad.addColorStop(1, withAlpha(s.colorTrail, 0))
        ctx.strokeStyle = grad
        ctx.lineCap = 'round'
        ctx.lineJoin = 'round'
        // Halo approximation: thicker stroke (no on-screen shadowBlur).
        ctx.lineWidth = Math.max(spx(1.8), th * 4.2)
        ctx.beginPath()
        ctx.moveTo(tailX, tailY)
        ctx.lineTo(x, y)
        ctx.stroke()
      }

      // Trail (core pass)
      {
        const th = s.thickness * invZ
        ctx.globalAlpha = 1
        const grad = ctx.createLinearGradient(x, y, tailX, tailY)
        grad.addColorStop(0, withAlpha(s.colorTrail, alphaTrail))
        grad.addColorStop(0.35, withAlpha(s.colorTrail, alphaTrail * 0.35))
        grad.addColorStop(1, withAlpha(s.colorTrail, 0))
        ctx.strokeStyle = grad
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

        ctx.globalAlpha = alphaCore
        drawGlowSprite(ctx, {
          kind: 'fx-dot',
          x,
          y,
          color: s.colorCore,
          r,
          blurPx: Math.max(spx(10), r * 6) * shadowBlurK,
          composite: 'lighter',
        })
      }

      // Small embers behind the head
      {
        const th = s.thickness * invZ
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

  // Clearing edge pulses — full-edge pulsing glow (synchronous 2-pulse + afterglow)
  if (fxState.edgePulses.length > 0) {
    let write = 0
    // Extended total lifetime = original durationMs + 30% afterglow tail.
    for (let read = 0; read < fxState.edgePulses.length; read++) {
      const p = fxState.edgePulses[read]!
      const age = nowMs - p.startedAtMs
      const afterglowMs = p.durationMs * 0.30
      const totalMs = p.durationMs + afterglowMs
      if (age >= totalMs) continue
      fxState.edgePulses[write++] = p

      const a = pos.get(p.from)
      const b = pos.get(p.to)
      if (!a || !b) continue

      const th = p.thickness * invZ
      const inOrangeDuration = age < p.durationMs

      ctx.save()
      ctx.globalCompositeOperation = 'lighter'
      ctx.lineCap = 'round'
      ctx.lineJoin = 'round'

      if (inOrangeDuration) {
        // --- Orange pulsing phase ---
        const t0 = clamp01(age / p.durationMs)

        // Raised cosine envelope (Hann window) — smooth from 0→1→0, no flat sustain.
        // No hard boundary breakpoints, perfectly smooth derivative everywhere.
        const envelope = 0.5 * (1 - Math.cos(Math.PI * 2 * Math.min(t0, 0.5)))
        // Peaks at t0=0.5, symmetric fade-in/fade-out over the full duration.
        // But we want the fade-out to be a bit slower, so use an asymmetric shape:
        // fast attack (first 15%), sustained middle, gentle release.
        const envAsym = t0 < 0.12
          ? 0.5 * (1 - Math.cos(Math.PI * (t0 / 0.12))) // smooth rise 0→1
          : t0 < 0.70
            ? 1.0 // sustained
            : 0.5 * (1 + Math.cos(Math.PI * ((t0 - 0.70) / 0.30))) // smooth fall 1→0

        // N=2 pulses: gentle sine modulation with a high floor (no zero-dips).
        const PULSE_COUNT = 2
        const pulse = 0.60 + 0.40 * Math.sin(t0 * PULSE_COUNT * Math.PI * 2)

        const alpha = clamp01(envAsym) * pulse
        if (alpha >= 0.005) {
          // Outer halo.
          ctx.globalAlpha = alpha * 0.12
          ctx.strokeStyle = p.color
          ctx.lineWidth = Math.max(spx(1.2), th * 2.4)
          ctx.beginPath(); ctx.moveTo(a.__x, a.__y); ctx.lineTo(b.__x, b.__y); ctx.stroke()

          // Mid glow.
          ctx.globalAlpha = alpha * 0.28
          ctx.strokeStyle = p.color
          ctx.lineWidth = Math.max(spx(0.7), th * 1.3)
          ctx.beginPath(); ctx.moveTo(a.__x, a.__y); ctx.lineTo(b.__x, b.__y); ctx.stroke()

          // Core line.
          ctx.globalAlpha = alpha * 0.55
          ctx.strokeStyle = withAlpha(p.color, 1.0)
          ctx.lineWidth = Math.max(spx(0.35), th * 0.6)
          ctx.beginPath(); ctx.moveTo(a.__x, a.__y); ctx.lineTo(b.__x, b.__y); ctx.stroke()
        }

        // Afterglow seed: start fading in the base-blue line during the tail of the orange phase
        // so there's a seamless crossfade (no abrupt transition).
        if (t0 > 0.55) {
          const crossfade = clamp01((t0 - 0.55) / 0.45) // 0→1 over the last 45%
          const blueAlpha = crossfade * 0.12
          ctx.globalAlpha = blueAlpha
          ctx.strokeStyle = '#64748b' // base edge color (slate)
          ctx.lineWidth = Math.max(spx(0.6), th * 1.0)
          ctx.beginPath(); ctx.moveTo(a.__x, a.__y); ctx.lineTo(b.__x, b.__y); ctx.stroke()
        }
      } else {
        // --- Afterglow phase: soft blue-gray line fading out ---
        const afterAge = age - p.durationMs
        const afterT = clamp01(afterAge / afterglowMs)
        // Smooth exponential-ish decay via cosine.
        const afterAlpha = 0.12 * 0.5 * (1 + Math.cos(Math.PI * afterT)) // 0.12 → 0
        if (afterAlpha >= 0.003) {
          ctx.globalAlpha = afterAlpha
          ctx.strokeStyle = '#64748b'
          ctx.lineWidth = Math.max(spx(0.6), th * 1.0)
          ctx.beginPath(); ctx.moveTo(a.__x, a.__y); ctx.lineTo(b.__x, b.__y); ctx.stroke()
        }
      }

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
        // Shape-aware contour glow: matches the node's actual outline (rounded-rect or circle).
        const { w: nw0, h: nh0 } = sizeForNode(n)
        const nw = nw0 * invZ
        const nh = nh0 * invZ
        const nodeR = Math.max(nw, nh) / 2
        const shapeKey = getNodeShape(n) ?? 'circle'
        const isRoundedRect = shapeKey === 'rounded-rect'
        const rr = Math.max(0, Math.min(4 * invZ, Math.min(nw, nh) * 0.18))

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

        // Shape-aware glow sprite (rounded-rect or circle, matching selection highlight).
        ctx.globalAlpha = alpha
        drawGlowSprite(ctx, {
          kind: 'active',
          shape: isRoundedRect ? 'rounded-rect' : 'circle',
          x: n.__x,
          y: n.__y,
          w: nw,
          h: nh,
          r: nodeR * 1.02,
          rr,
          color: b.color,
          blurPx: Math.max(spx(12), nodeR * 0.8) * shadowBlurK,
          lineWidthPx: baseWidth * 1.6,
          composite: 'lighter',
        })

        // Crisp contour strokes (no blur).
        ctx.globalAlpha = 1
        ctx.strokeStyle = withAlpha(b.color, 0.65 * alpha)
        ctx.lineWidth = baseWidth
        ctx.stroke(outline)

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
        ctx.globalAlpha = a

        drawGlowSprite(ctx, {
          kind: 'fx-bloom',
          x: n.__x,
          y: n.__y,
          color: b.color,
          r,
          blurPx: Math.max(spx(18), nodeR * 1.4) * shadowBlurK,
          composite: 'screen',
        })

        ctx.restore()
      } else {
        // Default (clearing) burst: bloom + ring
        const r = spx(10) + Math.pow(t0, 0.4) * spx(35)

        ctx.save()
        ctx.globalCompositeOperation = 'screen'

        // 1. Core bloom
        ctx.globalAlpha = alpha
        drawGlowSprite(ctx, {
          kind: 'fx-bloom',
          x: n.__x,
          y: n.__y,
          color: b.color,
          r: r * 0.5,
          blurPx: spx(30) * shadowBlurK,
          composite: 'screen',
        })

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
