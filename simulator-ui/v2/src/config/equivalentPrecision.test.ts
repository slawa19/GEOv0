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
})
