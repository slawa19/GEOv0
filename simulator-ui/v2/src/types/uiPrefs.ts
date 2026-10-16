import { toLowerTrim } from '../utils/stringHelpers'

export type LabelsLod = 'off' | 'selection' | 'neighbors'

export type Quality = 'low' | 'med' | 'high'

// ---------------------------------------------------------------------------
// UI theme prefs
// ---------------------------------------------------------------------------

/**
 * The themes the app offers (owner's decision R1, 2026-10-12; spec 039, slice B, decision 6): `saas`, the default,
 * and `hud`. `shadcn` and `library` are out of the switcher and of the stand's matrix; their token blocks stay in
 * `designSystem.tokens.css` (frozen, not deleted).
 */
export const UI_THEME_IDS = ['saas', 'hud'] as const
export type UiThemeId = (typeof UI_THEME_IDS)[number]
export const DEFAULT_UI_THEME: UiThemeId = 'saas'

/** `true` only for an id the app offers, exactly as it is written (no trimming, no case folding). */
export function isUiThemeId(v: unknown): v is UiThemeId {
  return typeof v === 'string' && (UI_THEME_IDS as readonly string[]).includes(v)
}

/**
 * Normalize any input (URL parameter, stored value) to a theme the app offers.
 *
 * - `hud` and `saas` (case and surrounding spaces ignored) are themselves;
 * - `shadcn`, `library`, anything else and nothing -> `saas` (the default).
 */
export function normalizeUiThemeId(v: unknown): UiThemeId {
  const s = toLowerTrim(v)
  return isUiThemeId(s) ? s : DEFAULT_UI_THEME
}
