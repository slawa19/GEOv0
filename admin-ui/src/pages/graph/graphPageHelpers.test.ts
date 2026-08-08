import { describe, expect, it, vi } from 'vitest'

import {
  atomsToDecimal,
  buildFocusModeQuery,
  computeSeedLabel,
  createDebouncedGraphElementSearch,
  extractPidFromText,
  graphElementOptionsForSearch,
  labelPartsToMode,
  makeMetricsKey,
  modeToLabelParts,
  pct,
  reloadGraphView,
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

  it('does not build guarded keyboard options before a meaningful query', () => {
    const buildOptions = vi.fn(() => [{ key: 'node:PID_A', label: 'Node: Alice — PID_A' }])

    expect(graphElementOptionsForSearch({
      guarded: true,
      query: 'P',
      guardedQueryMin: 2,
      guardedLimit: 100,
      buildOptions,
    })).toEqual([])
    expect(buildOptions).not.toHaveBeenCalled()
    expect(graphElementOptionsForSearch({
      guarded: false,
      query: '',
      guardedQueryMin: 2,
      guardedLimit: 100,
      buildOptions,
    })).toHaveLength(1)
    expect(buildOptions).toHaveBeenCalledTimes(1)
  })

  it('filters and bounds guarded keyboard matches', () => {
    const buildOptions = vi.fn(() => Array.from({ length: 150 }, (_, index) => ({
      key: `node:PID_MATCH_${index}`,
      label: `Node: Match ${index} — PID_MATCH_${index}`,
    })))

    const options = graphElementOptionsForSearch({
      guarded: true,
      query: 'match',
      guardedQueryMin: 2,
      guardedLimit: 100,
      buildOptions,
    })

    expect(buildOptions).toHaveBeenCalledTimes(1)
    expect(options).toHaveLength(100)
    expect(options.every((option) => option.label.toLowerCase().includes('match'))).toBe(true)
  })

  it('debounces rapid guarded queries to one bounded builder scan and cancels pending work', () => {
    vi.useFakeTimers()
    try {
      let built = Array.from({ length: 150 }, (_, index) => ({
        key: `node:PID_A_${index}`,
        label: `Node: PID_A ${index}`,
      }))
      const buildOptions = vi.fn(() => built)
      const publish = vi.fn<(options: typeof built) => void>()
      const search = createDebouncedGraphElementSearch({
        delayMs: 200,
        guardedQueryMin: 2,
        guardedLimit: 100,
        buildOptions,
        publish,
      })

      search.search('P')
      search.search('PI')
      search.search('PID')
      search.search('PID_A')

      expect(buildOptions).not.toHaveBeenCalled()
      expect(publish).toHaveBeenLastCalledWith([])
      vi.advanceTimersByTime(199)
      expect(buildOptions).not.toHaveBeenCalled()
      vi.advanceTimersByTime(1)
      expect(buildOptions).toHaveBeenCalledTimes(1)
      expect(publish.mock.calls[publish.mock.calls.length - 1]?.[0]).toHaveLength(100)

      built = [{ key: 'node:PID_A_NEW', label: 'Node: PID_A new source' }]
      search.search('PID_A')
      expect(publish).toHaveBeenLastCalledWith([])
      expect(buildOptions).toHaveBeenCalledTimes(1)
      vi.advanceTimersByTime(200)
      expect(buildOptions).toHaveBeenCalledTimes(2)
      expect(publish).toHaveBeenLastCalledWith(built)

      search.search('PID_A_1')
      search.cancel()
      vi.runAllTimers()
      expect(buildOptions).toHaveBeenCalledTimes(2)
    } finally {
      vi.useRealTimers()
    }
  })

  it.each([
    ['normal reload', { fit: true }],
    ['drawer refresh', { fit: false, preserveViewport: true }],
  ] as const)('preserves fit semantics for %s', async (_label, rebuildOptions) => {
    const ensureInitialized = vi.fn()
    const rebuild = vi.fn()

    await expect(reloadGraphView({
      loadData: vi.fn().mockResolvedValue(undefined),
      isCurrent: () => true,
      afterLoad: vi.fn().mockResolvedValue(undefined),
      ensureInitialized,
      rebuild,
      rebuildOptions,
    })).resolves.toBe(true)

    expect(ensureInitialized).toHaveBeenCalledTimes(1)
    expect(rebuild).toHaveBeenCalledWith(rebuildOptions)
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
