// FX config: a single source of truth for renderer tuning constants.
// IMPORTANT: keep values behavior-preserving unless explicitly changing visuals.

export const FX_ALPHA_EPS = 0.003

/** A clearing line's pulse is followed by an afterglow this share of its duration long (`renderFrame.ts`). */
export const EDGE_PULSE_AFTERGLOW_K = 0.3

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
// (039 slice C: the beam no longer fades inside a leg nor shrinks its trail at the end of one - the constants
// BEAM_ALPHA_DECAY_POW and BEAM_SHRINK_START_T went with that; the schedule is `composables/realFx/txRoutePlan.ts`.)
export const BEAM_MAX_TRAIL_FRAC = 0.85 // the trail never covers more than 85% of the edge
export const BEAM_TRAIL_MS = 240 // the trail is where the head was this long ago (so its length follows the speed)
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
// Low quality (039 slice C): one trail stroke (a two-stop gradient, tail to head) - readable without a GPU.
export const BEAM_LOW_TRAIL_ALPHA = 0.8
export const BEAM_LOW_TRAIL_MIN_SPX = 2.4 // screen-px
export const BEAM_LOW_TRAIL_TH_K = 2.4 // width = max(MIN_SPX, th * K)

// ── Head-dot constants (beam head) ────────────────────────────────────────
export const HEAD_DOT_MIN_SPX = 3.0 // beam head-dot min rendered radius (screen-px)
export const HEAD_DOT_TH_K = 4.2 // head-dot radius = max(MIN_SPX, th * K)
export const GLOW_BLUR_MIN_SPX = 16 // minimum glow-sprite blur (screen-px)
export const GLOW_BLUR_R_K = 5 // blur = max(GLOW_BLUR_MIN_SPX, r * K)
