import { asFiniteNumber } from './numberFormat'

/**
 * Parses amount-like values from snapshot/API into a finite number.
 * Non-finite/invalid values return 0.
 */
export function parseAmountNumber(v: unknown): number {
  return asFiniteNumber(v)
}

