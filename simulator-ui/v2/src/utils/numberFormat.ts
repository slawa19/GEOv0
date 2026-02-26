import { renderOrDash } from './valueFormat'

export function asFiniteNumber(v: unknown): number {
  if (typeof v === 'number') return Number.isFinite(v) ? v : 0
  if (typeof v === 'string') {
    const n = Number(v)
    return Number.isFinite(n) ? n : 0
  }
  return 0
}

/**
 * Parses amount-like values from snapshot/API into a number.
 *
 * Contract (strict):
 * - finite number => itself
 * - numeric string => Number(trimmed)
 * - anything else / invalid / non-finite => NaN
 */
export function parseAmountNumber(v: unknown): number {
  if (typeof v === 'number') return Number.isFinite(v) ? v : NaN
  if (typeof v === 'string') {
    const s = v.trim()
    if (!s) return NaN
    const n = Number(s)
    return Number.isFinite(n) ? n : NaN
  }
  return NaN
}

/**
 * Parses amount-like values into a finite number, falling back to 0 for invalid values.
 *
 * Use this helper for aggregations/summations where invalid/missing values should be treated as 0.
 */
export function parseAmountNumberOrZero(v: unknown): number {
  const n = parseAmountNumber(v)
  return Number.isFinite(n) ? n : 0
}

/**
 * Parses amount-like values (API/snapshot/UI) into a normalized string compatible with backend parsing.
 *
 * Rules:
 * - null/undefined -> null
 * - finite number -> String(number), but rejects exponent form (e/E)
 * - string -> trim, normalize ',' -> '.', then validate `^\d+(?:\.\d+)?$`
 * - other types -> null
 *
 * Notes:
 * - Backend `parse_amount_decimal` does NOT accept commas or exponent.
 * - This helper is intentionally strict to avoid submitting values the backend will reject.
 */
export function parseAmountStringOrNull(v: unknown): string | null {
  if (v == null) return null
  if (typeof v === 'number') {
    if (!Number.isFinite(v)) return null
    const s = String(v)
    if (/[eE]/.test(s)) return null
    return s
  }
  if (typeof v === 'string') {
    const s = v.trim()
    if (!s) return null
    const normalized = s.replaceAll(',', '.')
    if (/[eE\s]/.test(normalized)) return null
    if (!/^\d+(?:\.\d+)?$/.test(normalized)) return null
    return normalized
  }
  return null
}

export function formatAmount2(v: number): string {
  if (!Number.isFinite(v)) return '0.00'
  return v.toFixed(2)
}

/** Format integer with locale separators; invalid input -> '0'. */
export function fmtInt(n: unknown): string {
  const v = Number(n ?? 0)
  if (!Number.isFinite(v)) return '0'
  return v.toLocaleString(undefined, { maximumFractionDigits: 0 })
}

/** Format amount: drop redundant .00 fraction; null/undefined -> '—'. */
export function fmtAmt(v: unknown): string {
  if (v == null) return '—'
  const n = typeof v === 'number' ? v : parseFloat(String(v))
  if (!Number.isFinite(n)) return renderOrDash(v)
  return Number.isInteger(n) ? String(Math.round(n)) : String(n)
}
