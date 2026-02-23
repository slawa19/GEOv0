import { describe, expect, it } from 'vitest'

import { isUserFacingRunErrorCode } from './runErrorClassification'

describe('utils/runErrorClassification', () => {
  it('treats PAYMENT_TIMEOUT and INTERNAL_ERROR as user-facing', () => {
    expect(isUserFacingRunErrorCode('PAYMENT_TIMEOUT')).toBe(true)
    expect(isUserFacingRunErrorCode('payment_timeout')).toBe(true)
    expect(isUserFacingRunErrorCode('INTERNAL_ERROR')).toBe(true)
  })

  it('treats other codes as non-user-facing', () => {
    expect(isUserFacingRunErrorCode('ROUTING_CAPACITY')).toBe(false)
    expect(isUserFacingRunErrorCode('')).toBe(false)
  })
})
