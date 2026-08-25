/**
 * Money as text. A string in, a string out — never a JS `number` (012 / `F-012-5`).
 *
 * A binary double holds ~15-16 significant decimal digits; the ledger column is
 * `Numeric(20, 8)`, i.e. up to 20. So the moment an amount is routed through a `number`
 * two limits the ledger stores separately can become one value, and no amount of
 * formatting afterwards can tell them apart again. Every function here works on decimal
 * strings and `BigInt`, and none of them constructs a `Number` from an amount.
 *
 * THE RENDERING RULE is `app/core/simulator/edge_patch_builder.py`'s `to_money_str`,
 * copied rather than invented (spec `## Optimal`): **`precision` is the MINIMUM number of
 * fraction digits, never the maximum.** A value exactly representable at `precision`
 * renders with exactly `precision` digits, byte for byte as `toFixed(precision)` would;
 * a finer value is shown in full instead of being floored away, because
 * `Equivalent.precision` is a display parameter and not the ledger's quantum — the door
 * accepts, and `Numeric(20, 8)` stores, amounts finer than `precision`.
 *
 * Exponent notation is impossible by construction, on input and on output.
 */

/** Fallback when the equivalent declares nothing. Matches `to_money_str`'s own fallback. */
export const DEFAULT_MONEY_PRECISION = 2

/** Plain decimal, optional sign, no exponent — the same grammar the backend door accepts. */
const PLAIN_DECIMAL_RE = /^[+-]?\d+(?:\.\d+)?$/

const PLAIN_INTEGER_RE = /^[+-]?\d+$/

/** An exact decimal: `value` is the unscaled integer, `scale` the number of fraction digits. */
type Unscaled = { value: bigint; scale: number }

function pow10(n: number): bigint {
  return 10n ** BigInt(n)
}

/**
 * Amount-like input -> plain decimal string, or null when it is not an amount.
 *
 * `number` is accepted only as a legacy carrier (older fixtures write `trust_limit: 10`)
 * and only when `String(v)` is already plain decimal: that conversion is lossless for what
 * the double still holds, and rejecting the exponent form keeps this function from
 * inventing digits it cannot justify.
 */
export function moneyText(value: unknown): string | null {
  if (value == null) return null
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return null
    const s = String(value)
    return PLAIN_DECIMAL_RE.test(s) ? s : null
  }
  if (typeof value !== 'string') return null
  const s = value.trim()
  return PLAIN_DECIMAL_RE.test(s) ? s : null
}

function parseUnscaled(text: string): Unscaled | null {
  if (!PLAIN_DECIMAL_RE.test(text)) return null
  const negative = text.startsWith('-')
  const body = negative || text.startsWith('+') ? text.slice(1) : text
  const dot = body.indexOf('.')
  const intDigits = dot < 0 ? body : body.slice(0, dot)
  const fracDigits = dot < 0 ? '' : body.slice(dot + 1)
  const magnitude = BigInt(intDigits + fracDigits)
  return { value: negative ? -magnitude : magnitude, scale: fracDigits.length }
}

/**
 * Renders an exact decimal with at least `minScale` fraction digits and never fewer than
 * the value itself needs. This is the whole rule of `to_money_str`, expressed as: drop
 * trailing zeros down to `minScale`, then pad up to `minScale`.
 */
function render(unscaled: Unscaled, minScale: number): string {
  let { value, scale } = unscaled
  while (scale > minScale && value % 10n === 0n) {
    value /= 10n
    scale -= 1
  }
  while (scale < minScale) {
    value *= 10n
    scale += 1
  }

  const negative = value < 0n
  const magnitude = (negative ? -value : value).toString()

  let text: string
  if (scale === 0) {
    text = magnitude
  } else {
    const padded = magnitude.padStart(scale + 1, '0')
    text = `${padded.slice(0, padded.length - scale)}.${padded.slice(padded.length - scale)}`
  }

  // No "-0.00": a zero amount is not a debt, and the sign would read as one.
  return negative && /[1-9]/.test(magnitude) ? `-${text}` : text
}

/** `precision` as the catalogue may deliver it: not an int, negative, absent. */
export function normalizePrecision(precision: unknown): number {
  const n = typeof precision === 'number' ? precision : Number(precision)
  if (!Number.isFinite(n)) return DEFAULT_MONEY_PRECISION
  return Math.max(0, Math.trunc(n))
}

/**
 * Renders an amount for display at the equivalent's `precision`.
 *
 * Not an amount -> zero at that precision, which is what every caller here used to do
 * with a non-finite `number`.
 */
export function formatMoney(value: unknown, precision: unknown): string {
  const minScale = normalizePrecision(precision)
  const text = moneyText(value)
  const parsed = text === null ? null : parseUnscaled(text)
  return render(parsed ?? { value: 0n, scale: 0 }, minScale)
}

/**
 * Exact sum of two amounts. An operand that is not an amount counts as zero — the
 * summation semantics the trust-limit totals had before, kept deliberately.
 *
 * The result carries every digit of both operands; it is an intermediate, so nothing is
 * dropped here and the display precision is applied once, at the end, by `formatMoney`.
 */
export function addMoney(a: unknown, b: unknown): string {
  const ta = moneyText(a)
  const tb = moneyText(b)
  const da = (ta === null ? null : parseUnscaled(ta)) ?? { value: 0n, scale: 0 }
  const db = (tb === null ? null : parseUnscaled(tb)) ?? { value: 0n, scale: 0 }

  const scale = Math.max(da.scale, db.scale)
  const sum = da.value * pow10(scale - da.scale) + db.value * pow10(scale - db.scale)
  return render({ value: sum, scale }, scale)
}

/**
 * Signed atoms -> major units, exactly, at `precision` fraction digits.
 *
 * Atoms are an integer count of 10^-precision units, so the conversion is a decimal-point
 * move and nothing else. Returns null when the input is not an integer, so the caller can
 * decide what to show rather than being handed an invented amount.
 */
export function atomsToMoney(atoms: unknown, precision: unknown): string | null {
  const minScale = normalizePrecision(precision)
  const raw = typeof atoms === 'string' ? atoms.trim() : String(atoms ?? '').trim()
  if (!PLAIN_INTEGER_RE.test(raw)) return null

  const negative = raw.startsWith('-')
  const digits = negative || raw.startsWith('+') ? raw.slice(1) : raw
  const magnitude = BigInt(digits)
  return render({ value: negative ? -magnitude : magnitude, scale: minScale }, minScale)
}
