/**
 * FX configuration shared by the FX code
 * ======================================
 * What is here is what the runtime reads: the intensity scale and the colour of clearing labels.
 *
 * The timings of the payment and clearing animations are NOT here: they are literals of the code that owns the
 * behaviour (`composables/realFx/useRealTxFx.ts`, `useRealClearingFx.ts`). The `tx` and `clearing` groups this
 * object used to carry were read by nothing and were removed (034 S8, F-034-D15); the guard
 * `fxConfig.deadSettings.p034.test.ts` keeps an unread group from coming back.
 *
 * Responsibility split:
 * - `vizMapping.ts`: colors, semantic keys, and visual styles
 * - this module: the shared intensity multipliers
 */

import { toLowerTrim } from '../utils/stringHelpers'

export const FX_CONFIG = {
  /** Intensity scaling factors (shared) */
  intensity: {
    muted: 0.75,
    active: 1.0,
    hi: 1.35,
  },
} as const

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
