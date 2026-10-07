import { describe, expect, it } from 'vitest'

import { formatTs } from './datetime'

describe('formatTs', () => {
  it('prints an ISO timestamp as UTC, whatever offset it came with', () => {
    expect(formatTs('2026-10-07T09:05:03Z')).toBe('2026-10-07 09:05:03')
    expect(formatTs('2026-10-07T12:05:03+03:00')).toBe('2026-10-07 09:05:03')
    expect(formatTs('2026-10-07T09:05:03.123456+00:00')).toBe('2026-10-07 09:05:03')
  })

  it('keeps midnight as 00 (not 24) at the day boundary', () => {
    expect(formatTs('2026-12-31T00:00:00Z')).toBe('2026-12-31 00:00:00')
  })

  it('returns an empty value as empty and an unreadable one as it is', () => {
    expect(formatTs('')).toBe('')
    expect(formatTs(null)).toBe('')
    expect(formatTs(undefined)).toBe('')
    expect(formatTs('not a date')).toBe('not a date')
  })
})
