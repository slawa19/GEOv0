// FX config: a single source of truth for renderer tuning constants.
// IMPORTANT: keep values behavior-preserving unless explicitly changing visuals.

export const SEED_BUCKET_1K = 1000
export const SEED_BUCKET_4K = 4096
export const FX_ALPHA_EPS = 0.003

export type QualityPreset = 'low' | 'med' | 'high'

export const FX_SHADOW_BLUR_K_BY_QUALITY = {
  high: 1,
  med: 0.75,
  low: 0.3,
} as const satisfies Record<QualityPreset, number>

// Node glow sprite quality scaling.
// IMPORTANT: keep these values behavior-preserving (nodePainter.ts used 0.55 in low).
export const NODE_GLOW_K_BY_QUALITY = {
  high: 1,
  med: 0.75,
  low: 0.55,
} as const satisfies Record<QualityPreset, number>

// Node painter appearance (non-FX, but render-layer constants).
// Keep these values behavior-preserving.
export const NODE_GLINT_COLOR_HEX = '#ffffff'
export const OPAQUE_BLACK_HEX = '#000000'
export const NODE_GLINT_BADGE_ALPHA = 0.85
export const NODE_GLINT_INNER_STROKE_ALPHA = 0.9

export const NODE_BODY_FILL_ALPHA_HI_A = 0.55
export const NODE_BODY_FILL_ALPHA_HI_B = 0.25
export const NODE_BODY_FILL_ALPHA_LO = 0.42

// ── Beam FX constants ─────────────────────────────────────────────────────
export const BEAM_ALPHA_DECAY_POW = 1.2 // lifePos^k — slightly convex alpha fade
export const BEAM_MAX_TRAIL_FRAC = 0.85 // max trail = 85% of edge length
export const BEAM_SHRINK_START_T = 0.7 // trail starts shrinking at 70% of journey
export const BEAM_TRAIL_ALPHA_K = 0.55 // trail base-alpha multiplier
export const BEAM_HALO_GLOBAL_ALPHA = 0.9 // globalAlpha for the halo stroke pass
export const BEAM_HALO_MIN_SPX = 1.8 // halo stroke min width (screen-px)
export const BEAM_HALO_TH_K = 4.6 // halo stroke = max(MIN_SPX, th * K)
export const BEAM_CORE_MIN_SPX = 0.9 // core stroke min width (screen-px)
export const BEAM_CORE_TH_K = 1.25 // core stroke = max(MIN_SPX, th * K)
export const BEAM_SEG_LEN_FRAC = 0.22 // bright-packet segment fraction of edge length
export const BEAM_SEG_MIN_SPX = 18 // bright-packet min length (screen-px)
export const BEAM_SEG_MAX_SPX = 54 // bright-packet max length (screen-px)
export const BEAM_SEG_HALO_MIN_SPX = 2.0 // bright-packet halo stroke min width
export const BEAM_SEG_HALO_TH_K = 5.2 // bright-packet halo = max(MIN, th * K)
export const BEAM_SEG_CORE_MIN_SPX = 1.2 // bright-packet core stroke min width
export const BEAM_SEG_CORE_TH_K = 3.0 // bright-packet core = max(MIN, th * K)

// ── Head-dot constants (beam head) ────────────────────────────────────────
export const HEAD_DOT_MIN_SPX = 3.0 // beam head-dot min rendered radius (screen-px)
export const HEAD_DOT_TH_K = 4.2 // head-dot radius = max(MIN_SPX, th * K)
export const GLOW_BLUR_MIN_SPX = 16 // minimum glow-sprite blur (screen-px)
export const GLOW_BLUR_R_K = 5 // blur = max(GLOW_BLUR_MIN_SPX, r * K)

// ── Spark FX constants ────────────────────────────────────────────────────
export const SPARK_TRAIL_TIME_FRAC = 0.35 // trail time = ttlMs * k
export const SPARK_TRAIL_MIN_MS = 150 // trail duration clamp min (ms)
export const SPARK_TRAIL_MAX_MS = 500 // trail duration clamp max (ms)
export const SPARK_TRAIL_LEN_FRAC = 0.75 // max trail len ≤ 75% of edge length
export const SPARK_TRAIL_MIN_PX = 16 // minimum trail length (world-px)
export const SPARK_WOBBLE_FREQ_BASE = 2.5 // base wobble frequency
export const SPARK_WOBBLE_FREQ_RANGE = 4.0 // wobble frequency random range
export const SPARK_WOBBLE_PHASE_OFF = 11.3 // per-seed wobble phase randomizer
export const SPARK_PHASE_K = 0.35
export const SPARK_TRAIL_LIFE_K = 0.75 // alphaTrail = life * K
export const SPARK_CORE_LIFE_K = 0.95 // alphaCore  = life * K
export const SPARK_HEAD_MIN_SPX = 1.6 // spark head-dot min rendered radius (screen-px)
export const SPARK_HEAD_TH_K = 2.4 // spark head-dot = max(MIN, th * K)
export const SPARK_GLOW_BLUR_MIN_SPX = 10 // minimum glow blur for spark head (screen-px)
export const SPARK_GLOW_BLUR_R_K = 6 // spark blur = max(MIN, r * K)
