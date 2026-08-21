import { describe, expect, it } from 'vitest'

import { isUnitIntervalDecimalString } from './decimal'

describe('isUnitIntervalDecimalString', () => {
  it.each(['0', '0.0', '0.10000000000000001', '1', '1.000'])('accepts exact decimal %s', (value) => {
    expect(isUnitIntervalDecimalString(value)).toBe(true)
  })

  it.each(['', ' ', '.5', '1e-1', '-0.01', '1.00000000000000001', 'not-a-number'])(
    'rejects invalid or out-of-range value %j',
    (value) => {
      expect(isUnitIntervalDecimalString(value)).toBe(false)
    },
  )
})
