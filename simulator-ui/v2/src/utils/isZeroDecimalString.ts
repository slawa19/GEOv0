/**
 * Предикат: строка является десятичным представлением нуля.
 *
 * Требования:
 * - учитывает `trim()`
 * - допускает знак `+`/`-`
 * - запрещает NaN/Infinity и экспоненциальную запись
 */
export function isZeroDecimalString(input: unknown): boolean {
  if (typeof input !== 'string') return false

  const s = input.trim()
  if (s === '') return false

  // Only decimal notation (no exponent). Require at least one digit.
  if (!/^[+-]?\d+(\.\d+)?$/.test(s)) return false

  const n = Number(s)
  if (!Number.isFinite(n)) return false

  return n === 0
}

