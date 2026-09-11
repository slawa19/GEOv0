import { describe, it, expect } from 'vitest'

import { IntegrityStatusResponseSchema } from './adminContracts'
import fixture from '../../public/admin-fixtures/v1/datasets/integrity-status.json'

// T1402 of programme 014: the backend stopped publishing zero-sum as a passed check, because
// `check_zero_sum` sums the same debt rows grouped by creditor and by debtor and returns the
// difference - it telescopes to zero for any data and cannot fail on corruption.
//
// The reason these tests exist on the CLIENT is narrower than that. `InvariantResultSchema` is
// `.strict()`, so the server's new `{status, reason}` entry would have been REJECTED by the
// decoder and the whole Integrity page would have failed to load against a healthy server. That
// is the unowned P3 recorded in `specs/BACKLOG.md` by programme 013 - a strict decoder turning an
// additive server change into a page failure - arriving for the first time as a real change
// rather than as a hypothesis. These assertions hold the decoder to accepting BOTH shapes.

const base = {
  status: 'healthy' as const,
  last_check: '2026-09-11T10:00:00Z',
  alerts: [] as string[],
}

function withInvariants(invariants: Record<string, unknown>, extra: Record<string, unknown> = {}) {
  return {
    ...base,
    equivalents: {
      UAH: {
        status: 'healthy',
        checksum: '',
        invariants,
        ...extra,
      },
    },
  }
}

describe('integrity: a withdrawn check decodes, and cannot be read as a verdict', () => {
  it('accepts the withdrawn entry the server now sends', () => {
    const parsed = IntegrityStatusResponseSchema.safeParse(
      withInvariants(
        {
          zero_sum: { status: 'not_verified', reason: 'check_withdrawn' },
          trust_limits: { passed: true, violations: 0 },
          debt_symmetry: { passed: true, violations: 0 },
        },
        { unverified: ['zero_sum'] },
      ),
    )
    expect(parsed.success).toBe(true)
  })

  it('still accepts a verdict, so an older server is not broken by this change', () => {
    const parsed = IntegrityStatusResponseSchema.safeParse(
      withInvariants({
        zero_sum: { passed: true, value: '0' },
        trust_limits: { passed: true, violations: 0 },
        debt_symmetry: { passed: true, violations: 0 },
      }),
    )
    expect(parsed.success).toBe(true)
  })

  it('refuses an entry that is withdrawn AND carries a verdict', () => {
    // Not a hypothetical: the whole point of the separate variant is that "not verified" can
    // never be reported as a pass. If both shapes fused into one loose object, this would decode.
    const parsed = IntegrityStatusResponseSchema.safeParse(
      withInvariants({
        zero_sum: { status: 'not_verified', reason: 'check_withdrawn', passed: true },
        trust_limits: { passed: true, violations: 0 },
        debt_symmetry: { passed: true, violations: 0 },
      }),
    )
    expect(parsed.success).toBe(false)
  })

  it('refuses an unknown withdrawal reason', () => {
    const parsed = IntegrityStatusResponseSchema.safeParse(
      withInvariants({
        zero_sum: { status: 'not_verified', reason: 'because' },
        trust_limits: { passed: true, violations: 0 },
        debt_symmetry: { passed: true, violations: 0 },
      }),
    )
    expect(parsed.success).toBe(false)
  })
})

describe('integrity: the mock says what the server says', () => {
  it('the shipped fixture publishes the withdrawal, not a pass', () => {
    // A mock that still answered `passed: true` here would put the operator in front of a green
    // Zero-sum tag that the real service no longer claims - the mock/real divergence recorded as
    // `R-C4-002` and confirmed by T1400 on the very same day.
    const equivalents = (fixture as { equivalents: Record<string, Record<string, unknown>> })
      .equivalents
    const codes = Object.keys(equivalents)
    expect(codes.length).toBeGreaterThan(0)

    for (const code of codes) {
      const entry = equivalents[code]
      expect(entry, code).toBeDefined()
      const invariants = (entry as Record<string, unknown>).invariants as Record<
        string,
        Record<string, unknown> | undefined
      >
      const zeroSum = invariants.zero_sum
      expect(zeroSum, code).toEqual({ status: 'not_verified', reason: 'check_withdrawn' })
      expect(zeroSum?.passed, code).toBeUndefined()
      expect((entry as Record<string, unknown>).unverified, code).toEqual(['zero_sum'])
    }
  })

  it('the fixture decodes under the same schema the real client uses', () => {
    const parsed = IntegrityStatusResponseSchema.safeParse(fixture)
    expect(parsed.success).toBe(true)
  })
})
