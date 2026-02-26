import { describe, expect, it } from 'vitest'
import { asFiniteNumber, formatAmount2, parseAmountStringOrNull } from './numberFormat'

describe('utils/numberFormat', () => {
  it('asFiniteNumber coerces numbers and strings, else 0', () => {
    expect(asFiniteNumber(12)).toBe(12)
    expect(asFiniteNumber('3.5')).toBe(3.5)
    expect(asFiniteNumber('nope')).toBe(0)
    expect(asFiniteNumber(NaN)).toBe(0)
    expect(asFiniteNumber(Infinity)).toBe(0)
    expect(asFiniteNumber(null)).toBe(0)
  })

  it('formatAmount2 formats finite numbers with 2 decimals', () => {
    expect(formatAmount2(0)).toBe('0.00')
    expect(formatAmount2(1.2)).toBe('1.20')
    expect(formatAmount2(1.234)).toBe('1.23')
    expect(formatAmount2(NaN)).toBe('0.00')
  })

  it('parseAmountStringOrNull normalizes and validates amount strings', () => {
    expect(parseAmountStringOrNull(null)).toBeNull()
    expect(parseAmountStringOrNull(undefined)).toBeNull()
    expect(parseAmountStringOrNull('')).toBeNull()
    expect(parseAmountStringOrNull('   ')).toBeNull()

    expect(parseAmountStringOrNull('10')).toBe('10')
    expect(parseAmountStringOrNull(' 10.5 ')).toBe('10.5')
    expect(parseAmountStringOrNull('1,23')).toBe('1.23')
    expect(parseAmountStringOrNull(' 1,23 ')).toBe('1.23')

    // Reject exponent and malformed formats
    expect(parseAmountStringOrNull('1e3')).toBeNull()
    expect(parseAmountStringOrNull('1E3')).toBeNull()
    expect(parseAmountStringOrNull('1.')).toBeNull()
    expect(parseAmountStringOrNull('.5')).toBeNull()
    expect(parseAmountStringOrNull('1.2.3')).toBeNull()
    expect(parseAmountStringOrNull('1 2')).toBeNull()
  })
})
