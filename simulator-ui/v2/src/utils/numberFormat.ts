/**
 * Numeric helpers for amount-like input.
 *
 * WHAT IS NOT HERE ANY MORE, and why (012 / `F-012-5`, `T1208` / `C-B4-3-001`). This module
 * used to export four functions that turned money into a JS `number`:
 *
 *  - `formatAmount2(v: number)` -> `v.toFixed(2)`: the digit count was the constant 2 and
 *    the input was already a double. Replaced by `utils/money.ts` `formatMoney(value,
 *    precision)`, which takes a string and the equivalent's declared precision.
 *  - `fmtAmt`: `parseFloat` then drop a `.00` fraction. Its only caller was the node card's
 *    trustline rows, which now use `formatMoney` too.
 *  - `fmtInt`: locale-dependent `toLocaleString`, zero callers anywhere in the app.
 *  - `asFiniteNumber`: the coercion that fed `formatAmount2`, left without callers once the
 *    trust-limit totals became exact string arithmetic.
 *
 * `fmtAmt` and `fmtInt` were the two exports `C-B4-3-001` measured as untested; they are
 * gone rather than covered, because nothing calls them and a callerless money-through-float
 * formatter is an invitation, not an asset.
 *
 * What remains parses amount-like input for NON-display purposes: sorting, saturation
 * predicates, and normalising a form field before it is submitted. None of it formats money.
 */

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
