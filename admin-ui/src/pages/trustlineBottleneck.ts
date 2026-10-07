import { isRatioBelowThreshold, isUnitIntervalDecimalString } from '../utils/decimal'

/**
 * The bottleneck mark of the Trustlines screen (032 S7, D-19): an ACTIVE line whose available share of a positive
 * limit is under the threshold.
 *
 * - A closed line has no capacity to be short of, whatever its numbers say.
 * - A line with no limit (limit 0, e.g. a requested close that waits for the debt to be repaid) is not "short": there
 *   is no share to compute.
 * - The threshold is a share, so a number from 0 to 1 (`isUnitIntervalDecimalString`); anything else marks nothing
 *   rather than everything (`5` was read as "every line is a bottleneck") and the screen says it is invalid.
 *
 * Decimal strings only; no `Number` ever holds an amount.
 */
export function isTrustlineBottleneck(
  row: { status: string; limit: string; available: string },
  threshold: string,
): boolean {
  if (row.status !== 'active') return false
  if (!isUnitIntervalDecimalString(threshold)) return false
  const limit = String(row.limit ?? '').trim()
  if (limit.startsWith('-') || !/[1-9]/.test(limit)) return false
  return isRatioBelowThreshold({ numerator: row.available, denominator: limit, threshold })
}
