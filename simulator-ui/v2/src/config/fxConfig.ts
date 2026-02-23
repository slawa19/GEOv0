/**
 * Unified FX Configuration
 * ========================
 * Single source of truth for all visual effects parameters.
 *
 * NOTE:
 * Previously this module had a split between `demo` and `real` configs.
 * They were identical, which added false complexity and risk of drift.
 * The config is now canonical and mode-agnostic.
 *
 * Responsibility split:
 * - `vizMapping.ts`: colors, semantic keys, and visual styles
 * - this module: FX timings, thresholds, and multipliers
 */

import { toLowerTrim } from '../utils/stringHelpers'

export { EQUIVALENT_CODES } from './equivalents'

export const FX_CONFIG = {
  /**
   * Clearing/Tx FX parameters (canonical, mode-agnostic).
   */
  clearing: {
    /** Gap between consecutive spark spawns (ms) */
    microGapMs: 110,
    /** Duration of floating debt label (ms) */
    labelLifeMs: 2200,
    /** Duration of source node burst effect (ms) */
    sourceBurstMs: 360,
    /** Duration of target node burst effect (ms) */
    targetBurstMs: 520,
    /** Cleanup padding after animation completes (ms) */
    cleanupPadMs: 220,
    /** Throttle interval for label spawning (ms) */
    labelThrottleMs: 80,

    /** Duration of edge highlight glow (ms) */
    highlightPulseMs: 5200,
    /** Time-to-live for beam spark particles (ms) */
    microTtlMs: 860,

    /** Edge highlight thickness multiplier */
    highlightThickness: 2.9,
    /** Beam spark thickness multiplier */
    microThickness: 1.25,
    /** Node burst duration (ms) */
    nodeBurstMs: 1100,
  },

  /** Intensity scaling factors (shared) */
  intensity: {
    muted: 0.75,
    active: 1.0,
    hi: 1.35,
  },

  /** Single transaction animation (shared) */
  tx: {
    /** Default spark time-to-live (ms) */
    sparkTtlMs: 1200,
    /** Default spark trail length multiplier */
    trailLengthK: 1.0,
  },
} as const

export type FxClearingConfig = typeof FX_CONFIG.clearing

/**
 * Equivalent currency/unit codes shown in the EQ selector (BottomBar).
 * Single source of truth — avoids hardcoding in template HTML.
 */
/**
 * Colour for clearing floating labels.
 * Corresponds to DS token --ds-warn (warning yellow).
 * Expressed as a CSS custom property so the browser resolves the
 * correct per-theme value at render time.
 */
export const CLEARING_LABEL_COLOR = 'var(--ds-warn)'

/**
 * Unified intensity scaling function.
 *
 * Previously duplicated in the offline demo player and useSimulatorApp.ts.
 */
export function intensityScale(intensityKey?: string): number {
  const k = toLowerTrim(intensityKey)
  if (!k) return 1

  // Keep this conservative to avoid overblown visuals and perf regressions.
  switch (k) {
    case 'muted':
    case 'low':
      return FX_CONFIG.intensity.muted
    case 'active':
    case 'mid':
    case 'med':
      return FX_CONFIG.intensity.active
    case 'hi':
    case 'high':
      return FX_CONFIG.intensity.hi
    default:
      return 1
  }
}
