import type { VizMapping } from '../../vizMapping'
import type { LayoutNode } from '../../types/layout'
import { clamp01 } from '../../utils/math'
import { withAlpha } from '../color'
import { drawGlowSprite } from '../glowSprites'
import { getLinkTermination } from '../linkGeometry'
import { getNodeBaseGeometry } from '../nodeGeometry'
import { easeArrive } from './easing'
import {
  BEAM_CORE_MIN_SPX,
  BEAM_CORE_TH_K,
  BEAM_HALO_GLOBAL_ALPHA,
  BEAM_HALO_MIN_SPX,
  BEAM_HALO_TH_K,
  BEAM_LOW_TRAIL_ALPHA,
  BEAM_LOW_TRAIL_MIN_SPX,
  BEAM_LOW_TRAIL_TH_K,
  BEAM_MAX_TRAIL_FRAC,
  BEAM_SEG_CORE_MIN_SPX,
  BEAM_SEG_CORE_TH_K,
  BEAM_SEG_HALO_MIN_SPX,
  BEAM_SEG_HALO_TH_K,
  BEAM_SEG_LEN_FRAC,
  BEAM_SEG_MAX_SPX,
  BEAM_SEG_MIN_SPX,
  BEAM_TRAIL_ALPHA_K,
  BEAM_TRAIL_MS,
  EDGE_PULSE_AFTERGLOW_K,
  FX_ALPHA_EPS,
  FX_SHADOW_BLUR_K_BY_QUALITY,
  GLOW_BLUR_MIN_SPX,
  GLOW_BLUR_R_K,
  HEAD_DOT_MIN_SPX,
  HEAD_DOT_TH_K,
} from '../fxConfig'
import {
  invalidateNodeOutlineCacheForSnapshotKey,
  nodeOutlinePath2D,
} from './outlineCache'
import type { FxState } from './state'
import { worldRectForCanvas } from './worldRect'

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
  /** The product believes there is no GPU (`data-gpu=0`): accepted for symmetry with the base pass; no FX uses `screen` since 039 slice C. */
  noGpu?: boolean
}): void {
  const { nowMs, ctx, pos, w, h, mapping, fxState } = opts
  const z = Math.max(0.01, Number(opts.cameraZoom ?? 1))
  const invZ = 1 / z
  const spx = (v: number) => v * invZ
  const q = opts.quality ?? 'high'
  // Keep the same visuals as before for each quality preset,
  // but remove Interaction Quality dependencies from the FX stack.
  // Low quality should still keep a minimal blur so FX sprites don't degrade into hard geometry.
  const shadowBlurK = FX_SHADOW_BLUR_K_BY_QUALITY[q] ?? 1

  // Snapshot-based Path2D cache invalidation: clear only when snapshot identity changes,
  // NOT every frame (per-frame clear was negating the entire cache benefit).
  const sk = opts.snapshotKey
  invalidateNodeOutlineCacheForSnapshotKey(sk)

  // Lazily computed world-space view rect (getTransform().inverse is not cheap).
  let cachedWorldView: { x: number; y: number; w: number; h: number } | null = null
  const worldView = () => (cachedWorldView ??= worldRectForCanvas(ctx, w, h))

  // NOTE: Test mode primarily aims to make screenshot tests stable by not spawning FX.
  // Rendering stays enabled so manual interaction in test mode doesn't feel "dead".

  // NOTE: `withAlpha` and helpers are module-scoped with caching (perf).

  if (fxState.sparks.length === 0 && fxState.edgePulses.length === 0 && fxState.nodeBursts.length === 0) return

  // FX are always rendered (no early return on interaction).

  // Beams: payment legs and the clearing spark (039 slice C, motion contract P-3)
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

      const start = getLinkTermination(a, b, invZ)
      const end = getLinkTermination(b, a, invZ)

      const dx = end.x - start.x
      const dy = end.y - start.y
      const len = Math.max(1e-6, Math.hypot(dx, dy))
      const ux = dx / len
      const uy = dy / len

      // Where the head is: ONE speed along the route (`linear`), eased only into the receiver (`arrive`). Until 039
      // slice C every leg was eased (`easeOutCubic`) and faded inside itself, so the route went dark at every node.
      const moveMs = Math.max(1, Math.min(s.ttlMs, s.moveMs ?? s.ttlMs))
      const at = (ms: number) => {
        const u = clamp01(ms / moveMs)
        return s.motion === 'arrive' ? easeArrive(u) : u
      }
      const t = at(age)
      // Full brightness while the head moves; after it arrived (the last leg only: `ttlMs - moveMs` of fade) it
      // fades out at the receiver with a cosine, so the beam is never cut off.
      const fadeMs = s.ttlMs - moveMs
      const fadeT = fadeMs > 0 && age > moveMs ? clamp01((age - moveMs) / fadeMs) : 0
      const alpha = fadeT > 0 ? 0.5 * (1 + Math.cos(Math.PI * fadeT)) : 1
      if (alpha < FX_ALPHA_EPS) continue

      const th = s.thickness * invZ

      const headX = start.x + dx * t
      const headY = start.y + dy * t

      // The trail is where the head was `BEAM_TRAIL_MS` ago, on this edge, at most `BEAM_MAX_TRAIL_FRAC` of it: it is
      // as long as the speed makes it, it shortens by itself as the head slows into the receiver, and it drains into
      // the receiver during the fade.
      const trailStartT = Math.max(0, t - BEAM_MAX_TRAIL_FRAC, at(age - BEAM_TRAIL_MS))
      const trailStartX = start.x + dx * trailStartT
      const trailStartY = start.y + dy * trailStartT

      // Low quality (the machine without a GPU): ONE trail stroke and the head dot - a beam that is SEEN along its edge
      // (until 039 slice C low drew the head dot only: 0-26 lit pixels a frame). The stroke is a two-stop gradient,
      // transparent at the tail and full at the head, so the brightest point of the beam is always its head: with a
      // flat stroke the brightest pixels were the whole trail, and where the head was hidden (inside a node) the
      // brightest visible point jumped back along the trail - the motion oracle read that as the beam stepping back.
      if (q === 'low') {
        ctx.save()
        ctx.globalCompositeOperation = 'lighter'
        ctx.lineCap = 'round'
        if (trailStartT < t) {
          const g = ctx.createLinearGradient(trailStartX, trailStartY, headX, headY)
          g.addColorStop(0, withAlpha(s.colorTrail, 0))
          g.addColorStop(1, withAlpha(s.colorTrail, 1))
          ctx.globalAlpha = alpha * BEAM_LOW_TRAIL_ALPHA
          ctx.strokeStyle = g
          ctx.lineWidth = Math.max(spx(BEAM_LOW_TRAIL_MIN_SPX), th * BEAM_LOW_TRAIL_TH_K)
          ctx.beginPath()
          ctx.moveTo(trailStartX, trailStartY)
          ctx.lineTo(headX, headY)
          ctx.stroke()
        }
        const r = Math.max(spx(HEAD_DOT_MIN_SPX), th * HEAD_DOT_TH_K)
        ctx.globalAlpha = alpha
        drawGlowSprite(ctx, {
          kind: 'fx-dot',
          x: headX,
          y: headY,
          color: s.colorCore,
          r,
          blurPx: Math.max(spx(GLOW_BLUR_MIN_SPX), r * GLOW_BLUR_R_K) * shadowBlurK,
          composite: 'lighter',
        })
        ctx.restore()
        continue
      }

      // Med and high: gradient trail + bright packet segment + head dot (high and med drew the same before too).
      ctx.save()
      ctx.globalCompositeOperation = 'lighter'
      ctx.lineCap = 'round'
      ctx.lineJoin = 'round'

      // Trail beam (from trailStart to head) - soft halo + thin core.
      if (trailStartT < t) {
        const baseAlpha = Math.max(0, Math.min(1, alpha * BEAM_TRAIL_ALPHA_K))
        const g = ctx.createLinearGradient(trailStartX, trailStartY, headX, headY)
        g.addColorStop(0, withAlpha(s.colorTrail, 0))
        g.addColorStop(0.5, withAlpha(s.colorTrail, baseAlpha * 0.35))
        g.addColorStop(1, withAlpha(s.colorTrail, baseAlpha))

        // Halo approximation: thicker stroke + `lighter` (no on-screen shadowBlur).
        ctx.globalAlpha = BEAM_HALO_GLOBAL_ALPHA
        ctx.strokeStyle = g
        ctx.lineWidth = Math.max(spx(BEAM_HALO_MIN_SPX), th * BEAM_HALO_TH_K)
        ctx.beginPath()
        ctx.moveTo(trailStartX, trailStartY)
        ctx.lineTo(headX, headY)
        ctx.stroke()

        ctx.globalAlpha = 1
        ctx.lineWidth = Math.max(spx(BEAM_CORE_MIN_SPX), th * BEAM_CORE_TH_K)
        ctx.beginPath()
        ctx.moveTo(trailStartX, trailStartY)
        ctx.lineTo(headX, headY)
        ctx.stroke()
      }

      // Moving bright "packet" segment near the head, never longer than the trail.
      {
        const trailLen = len * (t - trailStartT)
        const segLen = Math.min(trailLen, Math.max(spx(BEAM_SEG_MIN_SPX), Math.min(spx(BEAM_SEG_MAX_SPX), len * BEAM_SEG_LEN_FRAC)))
        if (segLen > 0) {
          const tailX = headX - ux * segLen
          const tailY = headY - uy * segLen
          ctx.globalAlpha = 1
          const grad = ctx.createLinearGradient(headX, headY, tailX, tailY)
          grad.addColorStop(0, withAlpha(s.colorCore, alpha))
          grad.addColorStop(0.35, withAlpha(s.colorTrail, alpha * BEAM_TRAIL_ALPHA_K))
          grad.addColorStop(1, withAlpha(s.colorTrail, 0))
          ctx.strokeStyle = grad
          ctx.lineWidth = Math.max(spx(BEAM_SEG_HALO_MIN_SPX), th * BEAM_SEG_HALO_TH_K)
          ctx.beginPath()
          ctx.moveTo(tailX, tailY)
          ctx.lineTo(headX, headY)
          ctx.stroke()

          ctx.lineWidth = Math.max(spx(BEAM_SEG_CORE_MIN_SPX), th * BEAM_SEG_CORE_TH_K)
          ctx.beginPath()
          ctx.moveTo(tailX, tailY)
          ctx.lineTo(headX, headY)
          ctx.stroke()
        }
      }

      // Head: soft glowing dot (pre-rendered sprite, no on-screen shadowBlur).
      {
        const r = Math.max(spx(HEAD_DOT_MIN_SPX), th * HEAD_DOT_TH_K)
        ctx.globalAlpha = alpha
        drawGlowSprite(ctx, {
          kind: 'fx-dot',
          x: headX,
          y: headY,
          color: s.colorCore,
          r,
          blurPx: Math.max(spx(GLOW_BLUR_MIN_SPX), r * GLOW_BLUR_R_K) * shadowBlurK,
          composite: 'lighter',
        })
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
      const afterglowMs = p.durationMs * EDGE_PULSE_AFTERGLOW_K
      const totalMs = p.durationMs + afterglowMs
      if (age >= totalMs) continue
      fxState.edgePulses[write++] = p

      const a = pos.get(p.from)
      const b = pos.get(p.to)
      if (!a || !b) continue

      // From the border of one node to the border of the other, as the payment beam (MM1-M2, 2026-10-31): the FX canvas
      // is above the base canvas, so a centre-to-centre stroke was drawn over the bodies of both nodes. Copied out:
      // `getLinkTermination` returns pooled points.
      const startPt = getLinkTermination(a, b, invZ)
      const ax = startPt.x
      const ay = startPt.y
      const endPt = getLinkTermination(b, a, invZ)
      const bx = endPt.x
      const by = endPt.y

      const th = p.thickness * invZ
      const inOrangeDuration = age < p.durationMs

      ctx.save()
      ctx.globalCompositeOperation = 'lighter'
      ctx.lineCap = 'round'
      ctx.lineJoin = 'round'

      if (inOrangeDuration) {
        // --- Orange pulsing phase ---
        const t0 = clamp01(age / p.durationMs)

        // Asymmetric envelope: fast attack (first 12%), sustained middle, gentle release (last 30%).
        // (A symmetric raised-cosine variant was computed here and never used; removed 2026-10-12, 039 R13.)
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
          ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke()

          // Mid glow.
          ctx.globalAlpha = alpha * 0.28
          ctx.strokeStyle = p.color
          ctx.lineWidth = Math.max(spx(0.7), th * 1.3)
          ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke()

          // Core line.
          ctx.globalAlpha = alpha * 0.55
          ctx.strokeStyle = withAlpha(p.color, 1.0)
          ctx.lineWidth = Math.max(spx(0.35), th * 0.6)
          ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke()
        }

        // Afterglow seed: start fading in the base-blue line during the tail of the orange phase
        // so there's a seamless crossfade (no abrupt transition).
        if (t0 > 0.55) {
          const crossfade = clamp01((t0 - 0.55) / 0.45) // 0→1 over the last 45%
          const blueAlpha = crossfade * 0.12
          ctx.globalAlpha = blueAlpha
          ctx.strokeStyle = mapping.link.color.default
          ctx.lineWidth = Math.max(spx(0.6), th * 1.0)
          ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke()
        }
      } else {
        // --- Afterglow phase: soft blue-gray line fading out ---
        const afterAge = age - p.durationMs
        const afterT = clamp01(afterAge / afterglowMs)
        // Smooth exponential-ish decay via cosine.
        const afterAlpha = 0.12 * 0.5 * (1 + Math.cos(Math.PI * afterT)) // 0.12 → 0
        if (afterAlpha >= FX_ALPHA_EPS) {
          ctx.globalAlpha = afterAlpha
          ctx.strokeStyle = mapping.link.color.default
          ctx.lineWidth = Math.max(spx(0.6), th * 1.0)
          ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke()
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

      // 039 slice C: the one kind of burst - a node the beam leaves, passes or reaches, and a node of a clearing
      // cycle the spark passes. The 'glow' and 'clearing' kinds (bloom and shockwave with 'screen' composite over the
      // whole node area) had no spawner left and were removed.
      if (b.kind === 'tx-impact') {
        // Shape-aware contour glow: matches the node's actual outline (rounded-rect or circle).
        const { shape, w: nw, h: nh, r: nodeR, rr } = getNodeBaseGeometry(n, invZ)

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
          shape,
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

        // The glint is the mapping's resolved `--ds-glint` (039 slice B): no stylesheet read inside a frame.
        ctx.strokeStyle = withAlpha(mapping.fx.glint, 0.7 * alpha)
        ctx.lineWidth = Math.max(spx(1), baseWidth * 0.4)
        ctx.stroke(outline)

        ctx.restore()
      }
    }
    fxState.nodeBursts.length = write
  }
}
