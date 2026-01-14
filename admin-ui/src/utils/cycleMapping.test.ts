import { describe, expect, it } from 'vitest'

import { cycleDebtEdgeToTrustlineDirection } from './cycleMapping'

describe('cycleDebtEdgeToTrustlineDirection', () => {
  it('reverses debtor->creditor into trustline from=creditor,to=debtor', () => {
    const m = cycleDebtEdgeToTrustlineDirection({ debtor: 'A', creditor: 'B', equivalent: 'usd' })
    expect(m).toEqual({ from: 'B', to: 'A', equivalent: 'USD' })
  })

  it('returns null for invalid edges', () => {
    expect(cycleDebtEdgeToTrustlineDirection({ debtor: '', creditor: 'B', equivalent: 'USD' })).toBeNull()
    expect(cycleDebtEdgeToTrustlineDirection({ debtor: 'A', creditor: '', equivalent: 'USD' })).toBeNull()
    expect(cycleDebtEdgeToTrustlineDirection({ debtor: 'A', creditor: 'B', equivalent: '' })).toBeNull()
  })
})
