/**
 * Unified FX Configuration
 * ========================
 * Single source of truth for all visual effects parameters.
 *
 * Architecture:
 * - `shared`: Parameters identical for both modes
 * - `demo`: Overrides for demo mode (dense playlist events)
 * - `real`: Overrides for real mode (sporadic events; needs visibility)
 *
 * Use `getFxConfig(mode)` to get merged config for a specific mode.
 *
 * DO NOT duplicate these constants elsewhere.
 *
 * Responsibility split:
 * - `vizMapping.ts`: colors, semantic keys, and visual styles
 * - this module: FX timings, thresholds, and multipliers
 */

export const FX_CONFIG = {
  /**
   * Shared parameters (used by both Demo and Real modes)
   * These are identical regardless of mode.
   */
  shared: {
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
  },

  /**
   * Demo mode overrides
   *
   * IMPORTANT: Demo visuals must match real visuals 1:1.
   * Demo is a different *source* of events (fixtures/playlists), not a different visual language.
   */
  demo: {
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

  /**
   * Real mode overrides
   *
   * Rationale: Real mode has sporadic events (controlled by intensityPercent).
   * Longer durations ensure effects are noticeable before they fade.
   */
  real: {
    /** Duration of edge highlight glow (ms) — longer for sporadic events */
    highlightPulseMs: 5200,
    /** Time-to-live for beam spark particles (ms) */
    microTtlMs: 860,

    /** Edge highlight thickness multiplier (real-only) */
    highlightThickness: 2.9,
    /** Beam spark thickness multiplier (real-only) */
    microThickness: 1.25,
    /** Node burst duration (ms) (real-only) */
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

type SharedConfig = typeof FX_CONFIG.shared
type DemoConfig = typeof FX_CONFIG.demo
type RealConfig = typeof FX_CONFIG.real

export type DemoFxConfig = SharedConfig & DemoConfig
export type RealFxConfig = SharedConfig & RealConfig

export function getFxConfig(mode: 'demo'): DemoFxConfig
export function getFxConfig(mode: 'real'): RealFxConfig
export function getFxConfig(mode: 'demo' | 'real'): DemoFxConfig | RealFxConfig {
  return {
    ...FX_CONFIG.shared,
    ...(mode === 'demo' ? FX_CONFIG.demo : FX_CONFIG.real),
  }
}

/**
 * Unified intensity scaling function.
 *
 * Previously duplicated in useDemoPlayer.ts and useSimulatorApp.ts.
 */
export function intensityScale(intensityKey?: string): number {
  const k = String(intensityKey ?? '').trim().toLowerCase()
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
