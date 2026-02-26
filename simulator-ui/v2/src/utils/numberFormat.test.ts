import { describe, expect, it } from 'vitest'
import { asFiniteNumber, formatAmount2, parseAmountNumber, parseAmountNumberOrZero, parseAmountStringOrNull } from './numberFormat'

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

    expect(parseAmountStringOrNull(' 10 ')).toBe('10')
    expect(parseAmountStringOrNull('10')).toBe('10')
    expect(parseAmountStringOrNull(' 10.5 ')).toBe('10.5')
    expect(parseAmountStringOrNull('1,5')).toBe('1.5')
    expect(parseAmountStringOrNull('1,23')).toBe('1.23')
    expect(parseAmountStringOrNull(' 1,23 ')).toBe('1.23')

    // Valid format but may be invalid for payment business rules (<= 0)
    expect(parseAmountStringOrNull('0')).toBe('0')

    // Reject exponent and malformed formats
    expect(parseAmountStringOrNull('abc')).toBeNull()
    expect(parseAmountStringOrNull('1e3')).toBeNull()
    expect(parseAmountStringOrNull('1E3')).toBeNull()
    expect(parseAmountStringOrNull('1.')).toBeNull()
    expect(parseAmountStringOrNull('.5')).toBeNull()
    expect(parseAmountStringOrNull('1.2.3')).toBeNull()
    expect(parseAmountStringOrNull('1 2')).toBeNull()
    expect(parseAmountStringOrNull('-1')).toBeNull()
  })

  it('parseAmountNumber is strict (invalid -> NaN)', () => {
    expect(parseAmountNumber(12)).toBe(12)
    expect(parseAmountNumber('3.5')).toBe(3.5)
    expect(Number.isNaN(parseAmountNumber(''))).toBe(true)
    expect(Number.isNaN(parseAmountNumber('   '))).toBe(true)
    expect(Number.isNaN(parseAmountNumber('nope'))).toBe(true)
    expect(Number.isNaN(parseAmountNumber(NaN))).toBe(true)
    expect(Number.isNaN(parseAmountNumber(Infinity))).toBe(true)
    expect(Number.isNaN(parseAmountNumber(null))).toBe(true)
    expect(Number.isNaN(parseAmountNumber(undefined))).toBe(true)
  })

  it('parseAmountNumberOrZero falls back to 0', () => {
    expect(parseAmountNumberOrZero(12)).toBe(12)
    expect(parseAmountNumberOrZero('3.5')).toBe(3.5)
    expect(parseAmountNumberOrZero('nope')).toBe(0)
    expect(parseAmountNumberOrZero(null)).toBe(0)
  })
})
