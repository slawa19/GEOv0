import { describe, expect, it } from 'vitest'

import { normalizeTxAmountLabelInput } from './txAmountLabel'

describe('utils/txAmountLabel', () => {
  it('returns null when inputs are blank after trim', () => {
    expect(normalizeTxAmountLabelInput('   ', '+1.00')).toBeNull()
    expect(normalizeTxAmountLabelInput('A', '   ')).toBeNull()
  })

  it('normalizes + amounts and trims nodeId', () => {
    const v = normalizeTxAmountLabelInput('  A  ', '  +5.00  ')
    expect(v).not.toBeNull()
    expect(v!.nodeId).toBe('A')
    expect(v!.raw).toBe('+5.00')
    expect(v!.sign).toBe('+')
    expect(v!.amountText).toBe('5.00')
  })

  it('preserves - sign and returns sign=-', () => {
    const v = normalizeTxAmountLabelInput('A', ' -3.00 ')
    expect(v).not.toBeNull()
    expect(v!.sign).toBe('-')
    expect(v!.amountText).toBe('-3.00')
  })
})
