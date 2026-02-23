import { toLowerTrim } from '../utils/stringHelpers'

export type LabelsLod = 'off' | 'selection' | 'neighbors'

export type Quality = 'low' | 'med' | 'high'

// ---------------------------------------------------------------------------
// UI theme prefs
// ---------------------------------------------------------------------------

export type UiThemeId = 'hud' | 'shadcn' | 'saas' | 'library'

/**
 * Normalize unknown input to a valid theme id.
 *
 * - Accepts string-ish values (e.g. from URLSearchParams/localStorage)
 * - Defaults to 'hud'
 */
export function normalizeUiThemeId(v: unknown): UiThemeId {
  const s = toLowerTrim(v)
  if (s === 'shadcn') return 'shadcn'
  if (s === 'saas') return 'saas'
  if (s === 'library') return 'library'
  return 'hud'
}
