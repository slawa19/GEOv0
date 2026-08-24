import { describe, expect, it, vi } from 'vitest'
import { useLatestRequest } from '../../composables/useLatestRequest'

import {
  atomsToDecimal,
  buildFocusModeQuery,
  computeSeedLabel,
  createDebouncedGraphElementSearch,
  extractPidFromText,
  graphElementOptionsForSearch,
  guardedGraphSearchCacheAction,
  labelPartsToMode,
  makeMetricsKey,
  modeToLabelParts,
  money,
  pct,
  reloadGraphView,
  syncGraphCoreForView,
  waitForLatestPendingGraphLoad,
} from './graphPageHelpers'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => {
    resolve = res
  })
  return { promise, resolve }
}

describe('graphPageHelpers', () => {
  it('waits through a replacement graph load until the latest owner settles', async () => {
    const older = deferred<void>()
    const newer = deferred<void>()
    let pending: Promise<unknown> | null = older.promise
    const waiting = waitForLatestPendingGraphLoad(() => pending)

    pending = newer.promise
    older.resolve()
    await older.promise
    let settled = false
    void waiting.then(() => { settled = true })
    await Promise.resolve()
    expect(settled).toBe(false)

    pending = null
    newer.resolve()
    await waiting
    expect(settled).toBe(true)
  })

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

  it('invalidates source or locale changes without a retained-query rescan', () => {
    vi.useFakeTimers()
    try {
      let built = [{ key: 'node:PID_A', label: 'Node: Alice — PID_A' }]
      const buildOptions = vi.fn(() => built)
      const publish = vi.fn<(options: typeof built) => void>()
      const search = createDebouncedGraphElementSearch({
        delayMs: 200,
        guardedQueryMin: 2,
        guardedLimit: 100,
        buildOptions,
        publish,
      })

      search.search('PID_A')
      built = [{ key: 'node:PID_A', label: 'Узел: Алиса — PID_A' }]
      search.invalidate()
      vi.runAllTimers()

      expect(publish).toHaveBeenLastCalledWith([])
      expect(buildOptions).not.toHaveBeenCalled()

      search.search('PID_A')
      vi.advanceTimersByTime(200)
      expect(buildOptions).toHaveBeenCalledTimes(1)
      expect(publish).toHaveBeenLastCalledWith(built)

      search.invalidate()
      vi.runAllTimers()
      expect(publish).toHaveBeenLastCalledWith([])
      expect(buildOptions).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it.each([
    ['coalesced guard activation wins over dependency changes', true, false, 'search'],
    ['guarded dependency change invalidates', true, true, 'invalidate'],
    ['guard deactivation invalidates', false, true, 'invalidate'],
    ['unguarded dependency change is a no-op', false, false, 'none'],
  ] as const)('%s', (_label, guarded, wasGuarded, action) => {
    expect(guardedGraphSearchCacheAction(guarded, wasGuarded)).toBe(action)
  })

  it.each([
    ['normal reload', { fit: true }],
    ['drawer refresh', { fit: false, preserveViewport: true }],
  ] as const)('preserves fit semantics for %s', async (_label, rebuildOptions) => {
    const applyView = vi.fn().mockReturnValue(true)

    await expect(reloadGraphView({
      loadData: vi.fn().mockResolvedValue(true),
      isCurrent: () => true,
      afterLoad: vi.fn().mockResolvedValue(undefined),
      applyView,
      rebuildOptions,
    })).resolves.toBe(true)

    expect(applyView).toHaveBeenCalledWith(rebuildOptions)
  })

  it('removes an existing Core when a small graph transitions into the render guard', () => {
    let hasCore = true
    const rebuild = vi.fn()
    const destroy = vi.fn(() => { hasCore = false })

    expect(syncGraphCoreForView({
      guarded: true,
      hasCore: () => hasCore,
      initialize: vi.fn(),
      destroy,
      rebuild,
      rebuildOptions: { fit: false },
    })).toBe(false)

    expect(destroy).toHaveBeenCalledTimes(1)
    expect(rebuild).not.toHaveBeenCalled()
    expect(hasCore).toBe(false)
  })

  it('initializes and rebuilds when a guarded graph transitions back to an allowed size', () => {
    let hasCore = false
    const initialize = vi.fn(() => { hasCore = true })
    const rebuild = vi.fn()

    expect(syncGraphCoreForView({
      guarded: false,
      hasCore: () => hasCore,
      initialize,
      destroy: vi.fn(),
      rebuild,
      rebuildOptions: { fit: true },
    })).toBe(true)

    expect(initialize).toHaveBeenCalledTimes(1)
    expect(rebuild).toHaveBeenCalledWith({ fit: true })
  })

  it('prevents an older manual refresh from applying effects after a newer watcher refresh', async () => {
    let resolveOlder!: (value: boolean) => void
    let resolveLatest!: (value: boolean) => void
    const olderLoad = new Promise<boolean>((resolve) => { resolveOlder = resolve })
    const latestLoad = new Promise<boolean>((resolve) => { resolveLatest = resolve })
    const owner = useLatestRequest()
    const applied: string[] = []

    const older = reloadGraphView({
      loadData: () => olderLoad,
      isCurrent: owner.begin().isCurrent,
      afterLoad: async () => {},
      applyView: () => { applied.push('manual'); return true },
      rebuildOptions: { fit: true },
    })
    const latest = reloadGraphView({
      loadData: () => latestLoad,
      isCurrent: owner.begin().isCurrent,
      afterLoad: async () => {},
      applyView: () => { applied.push('watcher'); return true },
      rebuildOptions: { fit: false },
    })

    resolveLatest(true)
    await expect(latest).resolves.toBe(true)
    resolveOlder(true)
    await expect(older).resolves.toBe(false)
    expect(applied).toEqual(['watcher'])
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

describe('graphPageHelpers.money (F-012-7)', () => {
  // Каталог, а не константа теста: точность объявляет эквивалент, и обе объявленные различны.
  const CATALOGUE = new Map<string, number>([['HOUR', 1], ['UAH', 2], ['SAT', 0]])
  const AMOUNT = '12.345'

  function fractionDigits(rendered: string): number {
    const dot = rendered.indexOf('.')
    return dot < 0 ? 0 : rendered.length - dot - 1
  }

  it.each([...CATALOGUE.entries()])(
    'prints %s with exactly the fraction digits its equivalent declares',
    (code, precision) => {
      const rendered = money(AMOUNT, code, CATALOGUE)
      expect(
        fractionDigits(rendered),
        `${code} declares precision ${precision}, but the cell reads "${rendered}".`,
      ).toBe(precision)
    },
  )

  it('reacts to the declared precision: substituting it must change the output', () => {
    const asHour = money(AMOUNT, 'HOUR', CATALOGUE)
    const asUah = money(AMOUNT, 'UAH', CATALOGUE)

    expect(
      CATALOGUE.get('HOUR') === CATALOGUE.get('UAH'),
      'Counter-check premise: the two equivalents must declare different precision.',
    ).toBe(false)
    expect(
      asHour,
      'One amount rendered identically for two precisions means the digits carry no information.',
    ).not.toBe(asUah)

    // Та же величина, тот же код — но каталог объявляет другую точность.
    const substituted = money(AMOUNT, 'HOUR', new Map([['HOUR', 3]]))
    expect(substituted).not.toBe(asHour)
    expect(fractionDigits(substituted)).toBe(3)
  })

  it('prints a dash instead of inventing digits when the equivalent is unknown', () => {
    expect(money(AMOUNT, 'NOPE', CATALOGUE)).toBe('—')
    expect(money(AMOUNT, '', CATALOGUE)).toBe('—')
    expect(money(AMOUNT, null, CATALOGUE)).toBe('—')
  })

  it('normalizes the equivalent code the way the rest of admin-ui does', () => {
    expect(money(AMOUNT, ' hour ', CATALOGUE)).toBe(money(AMOUNT, 'HOUR', CATALOGUE))
  })
})
