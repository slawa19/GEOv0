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
