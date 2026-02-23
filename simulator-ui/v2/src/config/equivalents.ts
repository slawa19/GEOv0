/**
 * Equivalent currency/unit codes shown in the EQ selector (BottomBar).
 * Single source of truth — avoids hardcoding in template HTML.
 */
export const EQUIVALENT_CODES = ['UAH', 'HOUR', 'EUR'] as const

export type EquivalentCode = (typeof EQUIVALENT_CODES)[number]
