import { describe, it, expect } from 'vitest'

import { IntegrityStatusResponseSchema, IntegrityVerifyResponseSchema } from './adminContracts'

// 026 `T2601` (owner, В3): the server's `trust_limits` entry is now `TrustLimitsResult` - it lists a
// debt above a lowered limit as `over_limit_allowed` and says growth is not verified by a snapshot.
// A strict decoder without that variant turned every Integrity load and verify into INVALID_RESPONSE
// (§15 review of 1c3809e, P2). These hold the decoder to the canon: accept it, refuse a fused shape.

const trustLimits = {
  passed: true,
  violations: 0,
  details: null,
  over_limit_allowed: [
    {
      debtor_id: 'b0000000-0000-0000-0000-000000000002',
      creditor_id: 'a0000000-0000-0000-0000-000000000001',
      equivalent_id: 'e0000000-0000-0000-0000-000000000003',
      debt_amount: '50.00000000',
      trust_limit: '10.00000000',
      excess: '40.00000000',
    },
  ],
  growth: { status: 'not_verified', reason: 'requires_operation_prestate' },
}

function equivalents(trust: Record<string, unknown>) {
  return {
    UAH: {
      status: 'healthy',
      checksum: '',
      last_verified: null,
      invariants: {
        zero_sum: { status: 'not_verified', reason: 'check_withdrawn' },
        trust_limits: trust,
        debt_symmetry: { passed: true, value: null, violations: 0, details: null },
      },
      unverified: ['zero_sum'],
    },
  }
}

const status = (trust: Record<string, unknown>) => ({
  status: 'healthy',
  last_check: '2026-09-29T10:00:00Z',
  equivalents: equivalents(trust),
  alerts: [],
})
const verify = (trust: Record<string, unknown>) => ({
  status: 'healthy',
  checked_at: '2026-09-29T10:00:00Z',
  equivalents: equivalents(trust),
  alerts: [],
})

describe('integrity: trust_limits reports allowed excess and unverified growth (026 T2601)', () => {
  it('decodes the status and verify responses the server now sends', () => {
    expect(IntegrityStatusResponseSchema.safeParse(status(trustLimits)).success).toBe(true)
    expect(IntegrityVerifyResponseSchema.safeParse(verify(trustLimits)).success).toBe(true)
  })

  it('refuses an unknown growth reason and an entry missing the excess', () => {
    const badGrowth = { ...trustLimits, growth: { status: 'not_verified', reason: 'because' } }
    const [entry] = trustLimits.over_limit_allowed
    const noExcess = { ...trustLimits, over_limit_allowed: [{ ...entry, excess: undefined }] }
    expect(IntegrityStatusResponseSchema.safeParse(status(badGrowth)).success).toBe(false)
    expect(IntegrityStatusResponseSchema.safeParse(status(noExcess)).success).toBe(false)
  })
})
