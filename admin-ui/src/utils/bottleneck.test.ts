import { describe, expect, it } from 'vitest'

import { isTrustlineBottleneck } from './bottleneck'

const line = (over: Partial<{ status: string; limit: string; available: string }> = {}) => ({
  status: 'active', limit: '100', available: '5', ...over,
})

describe('isTrustlineBottleneck', () => {
  it('marks an active line whose available share of a positive limit is under the threshold', () => {
    expect(isTrustlineBottleneck(line(), '0.10')).toBe(true)
    expect(isTrustlineBottleneck(line({ available: '50' }), '0.10')).toBe(false)
    expect(isTrustlineBottleneck(line({ available: '10' }), '0.10')).toBe(false)
  })

  it('never marks a closed line, a line without a positive limit, or anything under an invalid threshold', () => {
    expect(isTrustlineBottleneck(line({ status: 'closed' }), '0.10')).toBe(false)
    expect(isTrustlineBottleneck(line({ limit: '0' }), '0.10')).toBe(false)
    expect(isTrustlineBottleneck(line({ limit: '0.000' }), '0.10')).toBe(false)
    expect(isTrustlineBottleneck(line({ limit: '-100', available: '-200' }), '0.10')).toBe(false)
    for (const bad of ['5', '-0.1', 'abc', '']) expect(isTrustlineBottleneck(line(), bad), bad).toBe(false)
  })
})
