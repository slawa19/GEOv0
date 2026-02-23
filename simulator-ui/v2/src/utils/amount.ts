import { asFiniteNumber } from './numberFormat'

/**
 * Parses amount-like values from snapshot/API into a finite number.
 * Non-finite/invalid values return 0.
 */
export function parseAmountNumber(v: unknown): number {
  return asFiniteNumber(v)
}

/**
 * Parses amount-like values (API/snapshot/UI) into a trimmed non-empty string.
 *
 * - null/undefined -> null
 * - finite number -> String(number)
 * - string -> trimmed string if non-empty, else null
 * - other types -> null
 */
export function parseAmountStringOrNull(v: unknown): string | null {
  if (v == null) return null
  if (typeof v === 'number') return Number.isFinite(v) ? String(v) : null
  if (typeof v === 'string') {
    const s = v.trim()
    return s ? s : null
  }
  return null
}

