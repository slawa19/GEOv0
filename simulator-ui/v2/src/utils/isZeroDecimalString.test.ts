import { describe, expect, it } from 'vitest'

import { isZeroDecimalString } from './isZeroDecimalString'

describe('isZeroDecimalString()', () => {
  it('считает нулём разные формы десятичной записи', () => {
    const ok = ['0', '0.0', '0.00', '0.000', '00', '  0.00  ', '-0.00', '+0']
    for (const s of ok) expect(isZeroDecimalString(s)).toBe(true)
  })

  it('не считает нулём ненулевые и невалидные строки', () => {
    const bad = ['0.01', '00.10', 'abc', '', '   ', 'NaN', '+NaN', '0e0', '-0.0e1', '0.']
    for (const s of bad) expect(isZeroDecimalString(s)).toBe(false)
  })
})

