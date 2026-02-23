import { renderOrDash } from './valueFormat'

export function asFiniteNumber(v: unknown): number {
  if (typeof v === 'number') return Number.isFinite(v) ? v : 0
  if (typeof v === 'string') {
    const n = Number(v)
    return Number.isFinite(n) ? n : 0
  }
  return 0
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
