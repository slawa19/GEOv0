import { describe, expect, it } from 'vitest'

import { buildFocusModeQuery, makeMetricsKey } from './graphPageHelpers'

describe('graphPageHelpers', () => {
  it('makeMetricsKey is stable and trims inputs', () => {
    expect(makeMetricsKey(' alice ', 'USD', ' 0.2 ')).toBe('alice|USD|thr=0.2')
    expect(makeMetricsKey('alice', null, '')).toBe('alice|ALL|thr=')
  })

  it('buildFocusModeQuery returns null when disabled or missing pid', () => {
    expect(buildFocusModeQuery({ enabled: false, rootPid: 'alice', depth: 1, equivalent: 'USD', statusFilter: [] })).toBeNull()
    expect(buildFocusModeQuery({ enabled: true, rootPid: '  ', depth: 1, equivalent: 'USD', statusFilter: [] })).toBeNull()
  })

  it('buildFocusModeQuery normalizes depth, equivalent, and statusFilter', () => {
    const q = buildFocusModeQuery({
      enabled: true,
      rootPid: ' alice ',
      depth: 2,
      equivalent: 'ALL',
      statusFilter: [' active ', '', 'frozen'],
    })

    expect(q).toEqual({
      pid: 'alice',
      depth: 2,
      status: ['active', 'frozen'],
      participant_pid: 'alice',
    })

    const q2 = buildFocusModeQuery({
      enabled: true,
      rootPid: 'bob',
      depth: 1,
      equivalent: 'USD',
      statusFilter: undefined,
    })

    expect(q2).toEqual({ pid: 'bob', depth: 1, equivalent: 'USD', participant_pid: 'bob' })
  })
})
