import type { VizMapping } from '../../vizMapping'
import type { LayoutNode } from '../nodePainter'
import { clamp01 } from '../../utils/math'
import { withAlpha } from '../color'
import { drawGlowSprite } from '../glowSprites'
import { getLinkTermination } from '../linkGeometry'
import { getNodeBaseGeometry } from '../nodeGeometry'
import { easeOutCubic } from './easing'
import {
  invalidateNodeOutlineCacheForSnapshotKey,
  nodeOutlinePath2D,
} from './outlineCache'
import type { FxState } from './state'
import { worldRectForCanvas } from './worldRect'

const SEED_BUCKET_1K = 1000
const SEED_BUCKET_4K = 4096
const FX_ALPHA_EPS = 0.003

// ── Beam FX constants ─────────────────────────────────────────────────────
const BEAM_ALPHA_DECAY_POW   = 1.2   // lifePos^k — slightly convex alpha fade
const BEAM_MAX_TRAIL_FRAC    = 0.85  // max trail = 85% of edge length
const BEAM_SHRINK_START_T    = 0.7   // trail starts shrinking at 70% of journey
const BEAM_TRAIL_ALPHA_K     = 0.55  // trail base-alpha multiplier
const BEAM_HALO_GLOBAL_ALPHA = 0.9   // globalAlpha for the halo stroke pass
const BEAM_HALO_MIN_SPX      = 1.8   // halo stroke min width (screen-px)
const BEAM_HALO_TH_K         = 4.6   // halo stroke = max(MIN_SPX, th * K)
const BEAM_CORE_MIN_SPX      = 0.9   // core stroke min width (screen-px)
const BEAM_CORE_TH_K         = 1.25  // core stroke = max(MIN_SPX, th * K)
const BEAM_SEG_LEN_FRAC      = 0.22  // bright-packet segment fraction of edge length
const BEAM_SEG_MIN_SPX       = 18    // bright-packet min length (screen-px)
const BEAM_SEG_MAX_SPX       = 54    // bright-packet max length (screen-px)
const BEAM_SEG_HALO_MIN_SPX  = 2.0   // bright-packet halo stroke min width
const BEAM_SEG_HALO_TH_K     = 5.2   // bright-packet halo = max(MIN, th * K)
const BEAM_SEG_CORE_MIN_SPX  = 1.2   // bright-packet core stroke min width
const BEAM_SEG_CORE_TH_K     = 3.0   // bright-packet core = max(MIN, th * K)
// ── Head-dot constants (beam head) ────────────────────────────────────────
const HEAD_DOT_MIN_SPX       = 3.0   // beam head-dot min rendered radius (screen-px)
const HEAD_DOT_TH_K          = 4.2   // head-dot radius = max(MIN_SPX, th * K)
const GLOW_BLUR_MIN_SPX      = 16    // minimum glow-sprite blur (screen-px)
const GLOW_BLUR_R_K          = 5     // blur = max(GLOW_BLUR_MIN_SPX, r * K)
// ── Spark FX constants ────────────────────────────────────────────────────
const SPARK_TRAIL_TIME_FRAC  = 0.35  // trail time = ttlMs * k
const SPARK_TRAIL_MIN_MS     = 150   // trail duration clamp min (ms)
const SPARK_TRAIL_MAX_MS     = 500   // trail duration clamp max (ms)
const SPARK_TRAIL_LEN_FRAC   = 0.75  // max trail len ≤ 75% of edge length
const SPARK_TRAIL_MIN_PX     = 16    // minimum trail length (world-px)
const SPARK_WOBBLE_FREQ_BASE  = 2.5  // base wobble frequency
const SPARK_WOBBLE_FREQ_RANGE = 4.0  // wobble frequency random range
const SPARK_WOBBLE_PHASE_OFF  = 11.3 // per-seed wobble phase randomizer
const SPARK_TRAIL_LIFE_K     = 0.75  // alphaTrail = life * K
const SPARK_CORE_LIFE_K      = 0.95  // alphaCore  = life * K
const SPARK_HEAD_MIN_SPX     = 1.6   // spark head-dot min rendered radius (screen-px)
const SPARK_HEAD_TH_K        = 2.4   // spark head-dot = max(MIN, th * K)
const SPARK_GLOW_BLUR_MIN_SPX = 10   // minimum glow blur for spark head (screen-px)
const SPARK_GLOW_BLUR_R_K    = 6     // spark blur = max(MIN, r * K)

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
  invalidateNodeOutlineCacheForSnapshotKey(sk)

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
    const SPARK_PHASE_K = 0.35
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
        const alpha = clamp01(Math.pow(lifePos, BEAM_ALPHA_DECAY_POW))

        const th = s.thickness * invZ

        const headX = start.x + dx * t
        const headY = start.y + dy * t

        // Trail length: limited so beam doesn't span the entire edge.
        // As head approaches target, trail shrinks proportionally.
        const maxTrailFraction = BEAM_MAX_TRAIL_FRAC // Max trail = 85% of edge length (longer for better visibility)
        const maxTrailLen = len * maxTrailFraction
        const distanceTraveled = len * t
        // Trail shrinks as we approach the end (last 30% of journey)
        const shrinkStart = BEAM_SHRINK_START_T
        const shrinkFactor = t > shrinkStart ? 1 - (t - shrinkStart) / (1 - shrinkStart) : 1
        const currentTrailLen = Math.min(maxTrailLen, distanceTraveled) * shrinkFactor

        // Trail start point (follows behind head)
        const trailStartT = Math.max(0, t - currentTrailLen / Math.max(1e-6, len))
        const trailStartX = start.x + dx * trailStartT
        const trailStartY = start.y + dy * trailStartT

        // Low quality: only draw the head dot — skip all trail gradient work.
        if (q === 'low') {
          const r = Math.max(spx(HEAD_DOT_MIN_SPX), th * HEAD_DOT_TH_K)
          ctx.save()
          ctx.globalCompositeOperation = 'lighter'
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

        // Med quality: gradient trail + bright packet segment (like HIGH), but no embers.
        // Per-frame gradient cost (2 objects per beam) is negligible for typical counts (≤28 beams).
        if (q === 'med') {
          ctx.save()
          ctx.globalCompositeOperation = 'lighter'
          ctx.lineCap = 'round'
          ctx.lineJoin = 'round'

          // Gradient trail (transparent tail → bright head)
          {
            const baseAlpha = Math.max(0, Math.min(1, alpha * BEAM_TRAIL_ALPHA_K))

            const trailStroke = (() => {
              const g = ctx.createLinearGradient(trailStartX, trailStartY, headX, headY)
              g.addColorStop(0, withAlpha(s.colorTrail, 0))
              g.addColorStop(0.5, withAlpha(s.colorTrail, baseAlpha * 0.35))
              g.addColorStop(1, withAlpha(s.colorTrail, baseAlpha))
              return g
            })()

            ctx.globalAlpha = BEAM_HALO_GLOBAL_ALPHA
            ctx.strokeStyle = trailStroke
            ctx.lineWidth = Math.max(spx(BEAM_HALO_MIN_SPX), th * BEAM_HALO_TH_K)
            ctx.beginPath()
            ctx.moveTo(trailStartX, trailStartY)
            ctx.lineTo(headX, headY)
            ctx.stroke()

            ctx.globalAlpha = 1
            ctx.strokeStyle = trailStroke
            ctx.lineWidth = Math.max(spx(BEAM_CORE_MIN_SPX), th * BEAM_CORE_TH_K)
            ctx.beginPath()
            ctx.moveTo(trailStartX, trailStartY)
            ctx.lineTo(headX, headY)
            ctx.stroke()
          }

          // Bright "packet" segment near the head
          {
            const segLen = Math.max(spx(BEAM_SEG_MIN_SPX), Math.min(spx(BEAM_SEG_MAX_SPX), len * BEAM_SEG_LEN_FRAC))
            const tailX = headX - ux * segLen
            const tailY = headY - uy * segLen
            ctx.globalAlpha = 1
            const grad = ctx.createLinearGradient(headX, headY, tailX, tailY)
            grad.addColorStop(0, withAlpha(s.colorCore, alpha * 1.0))
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

          // Head dot with glow
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
          const baseAlpha = Math.max(0, Math.min(1, alpha * BEAM_TRAIL_ALPHA_K))

          const trailStroke = (() => {
            const g = ctx.createLinearGradient(trailStartX, trailStartY, headX, headY)
            // Gradient from tail (transparent) to head (bright)
            g.addColorStop(0, withAlpha(s.colorTrail, 0))
            g.addColorStop(0.5, withAlpha(s.colorTrail, baseAlpha * 0.35))
            g.addColorStop(1, withAlpha(s.colorTrail, baseAlpha))
            return g
          })()

          // Halo approximation: thicker stroke + `lighter` (no on-screen shadowBlur).
          ctx.globalAlpha = BEAM_HALO_GLOBAL_ALPHA
          ctx.strokeStyle = trailStroke
          ctx.lineWidth = Math.max(spx(BEAM_HALO_MIN_SPX), th * BEAM_HALO_TH_K)
          ctx.beginPath()
          ctx.moveTo(trailStartX, trailStartY)
          ctx.lineTo(headX, headY)
          ctx.stroke()

          // Core pass (from trailStart to head)
          ctx.globalAlpha = 1
          ctx.strokeStyle = trailStroke
          ctx.lineWidth = Math.max(spx(BEAM_CORE_MIN_SPX), th * BEAM_CORE_TH_K)
          ctx.beginPath()
          ctx.moveTo(trailStartX, trailStartY)
          ctx.lineTo(headX, headY)
          ctx.stroke()
        }

        // Moving bright "packet" segment near the head
        {
          const segLen = Math.max(spx(BEAM_SEG_MIN_SPX), Math.min(spx(BEAM_SEG_MAX_SPX), len * BEAM_SEG_LEN_FRAC))
          const tailX = headX - ux * segLen
          const tailY = headY - uy * segLen
          ctx.globalAlpha = 1
          const grad = ctx.createLinearGradient(headX, headY, tailX, tailY)
          grad.addColorStop(0, withAlpha(s.colorCore, alpha * 1.0))
          grad.addColorStop(0.35, withAlpha(s.colorTrail, alpha * BEAM_TRAIL_ALPHA_K))
          grad.addColorStop(1, withAlpha(s.colorTrail, 0))
          ctx.strokeStyle = grad
          // Halo approximation: thicker segment stroke (no on-screen shadowBlur).
          ctx.lineWidth = Math.max(spx(BEAM_SEG_HALO_MIN_SPX), th * BEAM_SEG_HALO_TH_K)
          ctx.beginPath()
          ctx.moveTo(tailX, tailY)
          ctx.lineTo(headX, headY)
          ctx.stroke()

          // Core segment pass
          ctx.lineWidth = Math.max(spx(BEAM_SEG_CORE_MIN_SPX), th * BEAM_SEG_CORE_TH_K)
          ctx.beginPath()
          ctx.moveTo(tailX, tailY)
          ctx.lineTo(headX, headY)
          ctx.stroke()
        }

        // Head: soft glowing dot (no star/cross spikes)
        {
          const r = Math.max(spx(HEAD_DOT_MIN_SPX), th * HEAD_DOT_TH_K)

          // Replace arc+shadowBlur with pre-rendered FX dot sprite.
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
        continue
      }

      const phase = ((s.seed % SEED_BUCKET_1K) / SEED_BUCKET_1K) * SPARK_PHASE_K
      const t = clamp01(t0 + phase)

      // Comet-like spark: trail length depends on edge length and ttl.
      // Small ttl => faster head => longer trail.
      const speedPxPerMs = len / Math.max(1, s.ttlMs)
      const trailTimeMs = Math.max(SPARK_TRAIL_MIN_MS, Math.min(SPARK_TRAIL_MAX_MS, s.ttlMs * SPARK_TRAIL_TIME_FRAC))
      const trailLen = Math.max(SPARK_TRAIL_MIN_PX, Math.min(len * SPARK_TRAIL_LEN_FRAC, speedPxPerMs * trailTimeMs))

      const seed01 = (s.seed % SEED_BUCKET_4K) / SEED_BUCKET_4K
      const wobbleFreq = SPARK_WOBBLE_FREQ_BASE + seed01 * SPARK_WOBBLE_FREQ_RANGE
      const wobbleAmp = (spx(2.0) + (s.thickness * invZ) * 2.5) * (1 - t0)
      const perpX = -uy
      const perpY = ux
      const wobble = Math.sin((t * Math.PI * 2 * wobbleFreq) + seed01 * SPARK_WOBBLE_PHASE_OFF) * wobbleAmp

      const x = start.x + dx * t + perpX * wobble
      const y = start.y + dy * t + perpY * wobble

      const tailX = x - ux * trailLen
      const tailY = y - uy * trailLen

      const life = 1 - t0
      const alphaTrail = Math.max(0, Math.min(1, life * SPARK_TRAIL_LIFE_K))
      const alphaCore = Math.max(0, Math.min(1, life * SPARK_CORE_LIFE_K))

      // Low quality: only draw the head dot — skip all trail gradient work.
      if (q === 'low') {
        const th = s.thickness * invZ
        const r = Math.max(spx(SPARK_HEAD_MIN_SPX), th * SPARK_HEAD_TH_K)
        ctx.save()
        ctx.globalCompositeOperation = 'lighter'
        ctx.globalAlpha = alphaCore
        drawGlowSprite(ctx, {
          kind: 'fx-dot',
          x,
          y,
          color: s.colorCore,
          r,
          blurPx: Math.max(spx(SPARK_GLOW_BLUR_MIN_SPX), r * SPARK_GLOW_BLUR_R_K) * shadowBlurK,
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
        const r = Math.max(spx(SPARK_HEAD_MIN_SPX), th * SPARK_HEAD_TH_K)
        ctx.globalAlpha = alphaCore
        drawGlowSprite(ctx, {
          kind: 'fx-dot',
          x,
          y,
          color: s.colorCore,
          r,
          blurPx: Math.max(spx(SPARK_GLOW_BLUR_MIN_SPX), r * SPARK_GLOW_BLUR_R_K) * shadowBlurK,
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
        grad.addColorStop(0, withAlpha(s.colorTrail, alphaTrail * BEAM_HALO_GLOBAL_ALPHA))
        grad.addColorStop(0.25, withAlpha(s.colorTrail, alphaTrail * BEAM_TRAIL_ALPHA_K))
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
        const r = Math.max(spx(SPARK_HEAD_MIN_SPX), th * SPARK_HEAD_TH_K)

        ctx.globalAlpha = alphaCore
        drawGlowSprite(ctx, {
          kind: 'fx-dot',
          x,
          y,
          color: s.colorCore,
          r,
          blurPx: Math.max(spx(SPARK_GLOW_BLUR_MIN_SPX), r * SPARK_GLOW_BLUR_R_K) * shadowBlurK,
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
        if (afterAlpha >= FX_ALPHA_EPS) {
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

        ctx.strokeStyle = withAlpha('#ffffff', 0.7 * alpha)
        ctx.lineWidth = Math.max(spx(1), baseWidth * 0.4)
        ctx.stroke(outline)

        ctx.restore()
      } else if (b.kind === 'glow') {
        // Soft blurred circle glow (no rim, no hard ring).
        const { r: nodeR } = getNodeBaseGeometry(n, invZ)

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
