import { afterEach, describe, expect, it } from 'vitest'
import {
  SHIPPED_EQUIVALENT_PRECISION,
  equivalentPrecision,
  resetEquivalentPrecisions,
  setEquivalentPrecisions,
} from './equivalentPrecision'

/**
 * 012 / `F-012-4`. Before this module `simulator-ui/v2/src` contained the word `precision`
 * zero times outside tests, while `admin-ui/src` contained it 130 times: the same money,
 * one interface reading the equivalent's declared precision and the other unaware it exists.
 */

afterEach(() => {
  resetEquivalentPrecisions()
})

describe('equivalentPrecision', () => {
  it('prefers what the equivalents catalogue answered over the shipped default', () => {
    // A precision the shipped table does NOT hold, so a pass cannot come from the table.
    expect(SHIPPED_EQUIVALENT_PRECISION.HOUR).toBe(1)

    setEquivalentPrecisions([{ code: 'HOUR', precision: 6 }])
    expect(equivalentPrecision('HOUR')).toBe(6)

    resetEquivalentPrecisions()
    expect(equivalentPrecision('HOUR')).toBe(1)
  })

  it('reads the shipped fixture equivalents, which is the only source demo mode has', () => {
    // Demo/fast-mock mode never talks to a backend, and the fixtures under
    // public/simulator-fixtures/v1/ are atoms at these precisions.
    expect(equivalentPrecision('UAH')).toBe(2)
    expect(equivalentPrecision('HOUR')).toBe(1)
  })

  it('falls back to 2 for an equivalent nobody declared, matching to_money_str', () => {
    expect(equivalentPrecision('NOPE')).toBe(2)
    expect(equivalentPrecision('')).toBe(2)
    expect(equivalentPrecision(null)).toBe(2)
  })

  it('is case- and whitespace-insensitive about the code', () => {
    setEquivalentPrecisions([{ code: ' gram ', precision: 4 }])
    expect(equivalentPrecision('GRAM')).toBe(4)
    expect(equivalentPrecision('gram')).toBe(4)
  })

  it('ignores catalogue rows that carry no usable code or precision', () => {
    setEquivalentPrecisions([
      { code: '', precision: 9 },
      { code: 'X1', precision: null },
      { code: 'X2', precision: 3 },
    ])
    expect(equivalentPrecision('X1')).toBe(2)
    expect(equivalentPrecision('X2')).toBe(3)
  })

  it('keeps a legacy precision the API domain no longer admits', () => {
    // `Equivalent.precision` narrowed from 0..18 to 0..8 on 2026-08-25 (012 / S1), and the
    // narrowing was NOT applied to this reader on purpose: it reads what the catalogue
    // ANSWERS, and a database row written before that day still answers 12. Clamping here
    // would make this front end print an obligation at a resolution the row does not declare,
    // which is the defect the whole 012 programme is about, only pointed the other way.
    //
    // The neighbouring case above looks like it covers this and does not: its precision 9 sits
    // on a row with an empty code, so the row is discarded for the code and the precision is
    // never read. Found by external review (gpt-6-astra, medium, 2026-08-25).
    setEquivalentPrecisions([{ code: 'LEGACY12', precision: 12 }])
    expect(equivalentPrecision('LEGACY12')).toBe(12)
  })
})
