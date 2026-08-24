import { describe, expect, it } from 'vitest'
import { addMoney, atomsToMoney, formatMoney, moneyText, normalizePrecision } from './money'

/**
 * 012 / `F-012-5`, `T1208`.
 *
 * The spec forbids proving a money formatter with a test that contains the expected
 * two-digit string: such a test checks itself. So every digit-count assertion here is
 * parametrised by `precision`, and each carries a substitution counter-check — the same
 * amount under two precisions must not render identically, otherwise the assertion is
 * indistinguishable from its own absence.
 *
 * The rule under test is `to_money_str`'s (`app/core/simulator/edge_patch_builder.py`):
 * `precision` is the MINIMUM number of fraction digits, never the maximum.
 */

/** Every precision `Equivalent.precision` allows is 0..18; these span the shipped range and both ends. */
const PRECISIONS = [0, 1, 2, 4, 8, 18]

function fractionDigits(rendered: string): number {
  const dot = rendered.indexOf('.')
  return dot < 0 ? 0 : rendered.length - dot - 1
}

describe('formatMoney: precision sets the minimum number of fraction digits', () => {
  it.each(PRECISIONS)('an integer amount renders with exactly %i fraction digits', (precision) => {
    expect(fractionDigits(formatMoney('7', precision))).toBe(precision)
  })

  it.each(PRECISIONS)('an amount already at precision %i keeps exactly that many digits', (precision) => {
    const amount = precision === 0 ? '7' : `7.${'1'.repeat(precision)}`
    expect(formatMoney(amount, precision)).toBe(amount)
  })

  it('substitution counter-check: one amount must not render the same under two precisions', () => {
    const amount = '7'
    const rendered = PRECISIONS.map((p) => formatMoney(amount, p))
    expect(
      new Set(rendered).size,
      `The amount ${amount} rendered as ${JSON.stringify(rendered)} for precisions `
        + `${JSON.stringify(PRECISIONS)}. A formatter that answers the same for every precision `
        + 'is not reading the precision, and every digit-count assertion above would pass '
        + 'against a hard-coded constant.',
    ).toBe(PRECISIONS.length)
  })

  it.each(PRECISIONS)('never floors a value finer than precision %i', (precision) => {
    // One digit finer than the equivalent can express. `precision` is a display parameter,
    // not the ledger quantum: the door accepts and Numeric(20,8) stores such values, so
    // hiding the last digit would report an obligation that does not exist.
    const amount = `0.${'0'.repeat(precision)}5`
    expect(formatMoney(amount, precision)).toBe(amount)
  })

  it('is byte-identical to the old two-digit rendering wherever the old one was right', () => {
    // The spec's counter-check: at precision 2, values that were already correct must not
    // move by a single byte. Expected values are computed by `toFixed`, the previous
    // implementation, and only for amounts it represented exactly.
    for (const amount of ['0', '1.2', '13', '5', '1000.00', '999.99']) {
      expect(formatMoney(amount, 2)).toBe(Number(amount).toFixed(2))
    }
  })
})

describe('formatMoney: money is never a double', () => {
  it('keeps two amounts that differ by one atom at scale 8 apart', () => {
    // Both fit `Numeric(20, 8)` exactly; a double holds ~15-16 significant digits.
    const lower = '10000000000.12345678'
    const upper = '10000000000.12345679'

    expect(Number(lower) === Number(upper), 'Premise: these two collapse into one double.').toBe(true)
    expect(formatMoney(lower, 8)).toBe(lower)
    expect(formatMoney(upper, 8)).toBe(upper)
  })

  it('never emits exponent notation, whatever the magnitude', () => {
    for (const amount of ['0.00000001', '0.000000000000000001', '100000000000000000000000']) {
      expect(formatMoney(amount, 2)).not.toMatch(/[eE]/)
    }
    // The value whose `String(Number(...))` does use an exponent, proving the point:
    expect(String(Number('0.000000001'))).toMatch(/e/)
    expect(formatMoney('0.000000001', 2)).toBe('0.000000001')
  })

  it('renders a zero amount without a minus sign', () => {
    expect(formatMoney('-0.00', 2)).toBe('0.00')
    expect(atomsToMoney('-0', 2)).toBe('0.00')
  })

  it('rejects input that is not a plain decimal amount', () => {
    for (const bad of [null, undefined, '', '  ', 'abc', '1e3', '1.2.3', '1 2', NaN, Infinity, {}]) {
      expect(moneyText(bad)).toBeNull()
      expect(formatMoney(bad, 2)).toBe('0.00')
    }
  })

  it('accepts a legacy numeric amount only while the conversion is lossless', () => {
    // Older fixtures write `trust_limit: 10`. That is still readable; an exponent-form
    // double is not, because the digits it stands for are no longer in the value.
    expect(moneyText(10)).toBe('10')
    expect(moneyText(1e21)).toBeNull()
  })
})

describe('addMoney: exact decimal arithmetic', () => {
  it('adds without visiting a double', () => {
    expect(addMoney('0.1', '0.2')).toBe('0.3')
    expect(Number('0.1') + Number('0.2')).not.toBe(0.3)
  })

  it('keeps every digit of both operands', () => {
    expect(addMoney('10000000000.12345678', '0.00000001')).toBe('10000000000.12345679')
  })

  it('treats a non-amount operand as zero, as the trust-limit totals always did', () => {
    expect(addMoney('5.5', undefined)).toBe('5.5')
    expect(addMoney(undefined, 'nope')).toBe('0')
  })

  it('is associative over a run of amounts the ledger can store', () => {
    const parts = ['0.00000001', '0.00000002', '0.00000003']
    const forwards = parts.reduce((acc, v) => addMoney(acc, v), '0')
    const backwards = [...parts].reverse().reduce((acc, v) => addMoney(acc, v), '0')
    expect(forwards).toBe(backwards)
    expect(forwards).toBe('0.00000006')
  })
})

describe('atomsToMoney: atoms are an integer count of 10^-precision units', () => {
  it.each(PRECISIONS)('moves the point exactly %i places', (precision) => {
    // More digits than the largest precision under test, so no zero-padding is involved
    // and the check below stays a statement about the point, not about the padding.
    const atoms = '1234567890123456789012'
    const rendered = atomsToMoney(atoms, precision) as string

    expect(fractionDigits(rendered)).toBe(precision)
    expect(
      rendered.replace('.', ''),
      `Converting ${atoms} atoms at precision ${precision} produced "${rendered}". Removing the `
        + 'decimal point must give back the atoms unchanged: anything else means digits were '
        + 'invented or lost on the way from the ledger to the screen.',
    ).toBe(atoms)
  })

  it('substitution counter-check: the same atoms are different money under different precisions', () => {
    const atoms = '4450'
    const rendered = PRECISIONS.map((p) => atomsToMoney(atoms, p))
    expect(new Set(rendered).size).toBe(PRECISIONS.length)
  })

  it('carries the sign, and reports a non-integer rather than inventing one', () => {
    expect(atomsToMoney('-4450', 2)).toBe('-44.50')
    expect(atomsToMoney('44.50', 2)).toBeNull()
    expect(atomsToMoney('', 2)).toBeNull()
    expect(atomsToMoney(null, 2)).toBeNull()
  })
})

describe('normalizePrecision', () => {
  it('falls back to 2 exactly where to_money_str does, and never goes negative', () => {
    expect(normalizePrecision(undefined)).toBe(2)
    expect(normalizePrecision('nope')).toBe(2)
    expect(normalizePrecision(-3)).toBe(0)
    expect(normalizePrecision('4')).toBe(4)
    expect(normalizePrecision(4.9)).toBe(4)
  })
})
