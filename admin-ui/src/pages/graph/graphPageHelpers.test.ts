import { describe, expect, it } from 'vitest'

import {
  atomsToDecimal,
  buildFocusModeQuery,
  computeSeedLabel,
  extractPidFromText,
  labelPartsToMode,
  makeMetricsKey,
  modeToLabelParts,
  pct,
} from './graphPageHelpers'

describe('graphPageHelpers', () => {
  it('makeMetricsKey is stable and trims inputs', () => {
    expect(makeMetricsKey(' alice ', 'USD', ' 0.2 ')).toBe('alice|USD|thr=0.2')
    expect(makeMetricsKey('alice', null, '')).toBe('alice|ALL|thr=')
  })

  it('label mode helpers are consistent', () => {
    expect(labelPartsToMode([])).toBe('off')
    expect(labelPartsToMode(['name'])).toBe('name')
    expect(labelPartsToMode(['pid'])).toBe('pid')
    expect(labelPartsToMode(['name', 'pid'])).toBe('both')

    expect(modeToLabelParts('off')).toEqual([])
    expect(modeToLabelParts('name')).toEqual(['name'])
    expect(modeToLabelParts('pid')).toEqual(['pid'])
    expect(modeToLabelParts('both')).toEqual(['name', 'pid'])
  })

  it('computeSeedLabel handles known seeds and fallback', () => {
    expect(computeSeedLabel(undefined)).toBe('Seed: (not loaded)')
    expect(computeSeedLabel([{ display_name: 'Greenfield Village (Test)' }, { display_name: 'x' }])).toMatch(/^Seed: 2 participants, first:/)

    const greenfield = Array.from({ length: 100 }, (_, i) => ({ display_name: i === 0 ? 'Greenfield Village (Test)' : 'X' }))
    expect(computeSeedLabel(greenfield)).toBe('Seed: Greenfield (100)')
  })

  it('extractPidFromText finds PID tokens', () => {
    expect(extractPidFromText('hello PID_ABC_123 world')).toBe('PID_ABC_123')
    expect(extractPidFromText('no pid here')).toBeNull()
  })

  it('pct clamps and formats', () => {
    expect(pct(0)).toBe('0%')
    expect(pct(1)).toBe('100%')
    expect(pct(2)).toBe('100%')
    expect(pct(0.1234, 1)).toBe('12.3%')
  })

  it('atomsToDecimal formats with precision', () => {
    expect(atomsToDecimal(0n, 2)).toBe('0.00')
    expect(atomsToDecimal(12n, 0)).toBe('12')
    expect(atomsToDecimal(12n, 2)).toBe('0.12')
    expect(atomsToDecimal(-12n, 2)).toBe('-0.12')
    expect(atomsToDecimal(1234n, 2)).toBe('12.34')
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
