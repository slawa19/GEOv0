import { describe, expect, it } from 'vitest'
import * as numberFormat from './numberFormat'
import { parseAmountNumber, parseAmountNumberOrZero, parseAmountStringOrNull } from './numberFormat'

/**
 * 012 / `T1208`, `MISSED-B4-NUMFMT-FIXED2`. This suite used to open with
 *
 *   expect(formatAmount2(1.234)).toBe('1.23')
 *
 * which pinned the two defects of `F-012-5` as the expected contract: money arriving as a
 * JS `number`, and a fraction-digit count that was the constant 2 whatever the equivalent
 * declared. The formatter is gone (see the module docstring); its replacement lives in
 * `utils/money.ts` and is tested in `money.test.ts`, parametrised by `precision` with a
 * substitution counter-check, never against a hard-coded two-digit string.
 *
 * What is left here formats nothing. It parses amount-like input for sorting, for the
 * saturation predicate on trustline rows, and for normalising a form field before submit.
 */

describe('utils/numberFormat', () => {
  it('exports exactly the helpers this suite exercises, and no money formatter', () => {
    // `C-B4-3-001`: the module carried 7 exports and the suite imported 5, leaving the two
    // float money formatters untested. Asserting the export list makes that gap impossible
    // to reopen silently — a new export must be named here, and therefore tested.
    expect(Object.keys(numberFormat).sort()).toEqual([
      'parseAmountNumber',
      'parseAmountNumberOrZero',
      'parseAmountStringOrNull',
    ])
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

  it('parseAmountStringOrNull does not round-trip an amount through a double', () => {
    // It normalizes text and hands the text on; the scale the caller submitted survives.
    // A `Number()` round-trip would turn the first into '10.1' and shorten the second.
    expect(parseAmountStringOrNull('10.10')).toBe('10.10')
    expect(parseAmountStringOrNull('10000000000.12345679')).toBe('10000000000.12345679')
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
