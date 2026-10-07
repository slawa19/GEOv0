import { beforeEach, describe, expect, it, vi } from 'vitest'
import { effectScope, nextTick, ref } from 'vue'

const apiMock = vi.hoisted(() => ({
  graphSnapshot: vi.fn(),
  graphEgo: vi.fn(),
}))

vi.mock('../api', () => ({ api: apiMock }))

import {
  computePrimaryEquivalent,
  filterTrustlinesByEqAndStatus,
  useGraphData,
} from './useGraphData'
import type { Equivalent, Trustline } from '../pages/graph/graphTypes'

type Deferred<T> = {
  promise: Promise<T>
  resolve: (value: T) => void
  reject: (reason: unknown) => void
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

function snapshotEnvelope(pid: string) {
  return {
      participants: [{ pid }],
      trustlines: [],
      equivalents: [{ code: 'EUR', precision: 2, description: '', is_active: true }],
      debts: [],
      audit_log: [],
      transactions: [],
    }
}

describe('useGraphData', () => {
  beforeEach(() => vi.resetAllMocks())


  it('filterTrustlinesByEqAndStatus filters by eq and status', () => {
    const trustlines: Trustline[] = [
      { equivalent: 'EUR', from: 'A', to: 'B', limit: '1', used: '0', available: '1', status: 'active', created_at: 't' },
      { equivalent: 'EUR', from: 'A', to: 'C', limit: '1', used: '0', available: '1', status: 'closed', created_at: 't' },
      { equivalent: 'USD', from: 'A', to: 'D', limit: '1', used: '0', available: '1', status: 'active', created_at: 't' },
    ]

    expect(
      filterTrustlinesByEqAndStatus({ trustlines, equivalent: 'EUR', statusFilter: ['active', 'closed'] }).map(
        (t) => t.to
      )
    ).toEqual(['B', 'C'])

    expect(
      filterTrustlinesByEqAndStatus({ trustlines, equivalent: 'EUR', statusFilter: ['active'] }).map((t) => t.to)
    ).toEqual(['B'])

    expect(
      filterTrustlinesByEqAndStatus({ trustlines, equivalent: '', statusFilter: ['active'] }).map((t) => t.to)
    ).toEqual(['B', 'D'])
  })

  it('computePrimaryEquivalent picks equivalent with most active trustlines', () => {
    const trustlines = [
      { equivalent: 'UAH', status: 'active' },
      { equivalent: 'UAH', status: 'active' },
      { equivalent: 'EUR', status: 'active' },
      { equivalent: 'EUR', status: 'closed' },
      { equivalent: ' usd ', status: 'active' },
    ]

    const equivalents = [{ code: 'EUR' }, { code: 'UAH' }, { code: 'USD' }]

    expect(computePrimaryEquivalent(trustlines, equivalents)).toBe('UAH')
  })

  it('computePrimaryEquivalent falls back to first equivalent when no active trustlines', () => {
    const trustlines = [
      { equivalent: 'UAH', status: 'closed' },
      { equivalent: 'UAH', status: 'frozen' },
    ]
    const equivalents = [{ code: 'EUR' }, { code: 'UAH' }]

    expect(computePrimaryEquivalent(trustlines, equivalents)).toBe('EUR')
  })

  it('computePrimaryEquivalent returns empty string when no data', () => {
    expect(computePrimaryEquivalent([], [])).toBe('')
  })

  it('availableEquivalents merges dataset + trustlines (no ALL option)', () => {
    const eq = ref('')
    const focusMode = ref(false)
    const focusRootPid = ref('')
    const focusDepth = ref(1)
    const statusFilter = ref<string[]>([])

    const g = useGraphData({ eq, focusMode, focusRootPid, focusDepth, statusFilter })
    g.equivalents.value = [{ code: 'eur', precision: 2, description: '', is_active: true } satisfies Equivalent]
    const tlBase = {
      from: 'A',
      to: 'B',
      limit: '0',
      used: '0',
      available: '0',
      status: 'active',
      created_at: 't',
    } satisfies Omit<Trustline, 'equivalent'>
    g.trustlines.value = [
      { ...tlBase, equivalent: ' usd ' },
      { ...tlBase, equivalent: 'EUR' },
    ]

    expect(g.availableEquivalents.value).toEqual(['EUR', 'USD'])
  })

  it('028 F-028-49 (C2): an equivalent chosen for the operator is marked as chosen, until the operator picks', async () => {
    apiMock.graphSnapshot.mockResolvedValueOnce(snapshotEnvelope('A'))
    const eq = ref('')
    const g = useGraphData({ eq, focusMode: ref(false), focusRootPid: ref(''),
      focusDepth: ref(1), statusFilter: ref<string[]>([]) })
    await g.loadData()
    expect(eq.value).toBe('EUR')
    expect(g.eqAutoSelected.value).toBe(true)
    eq.value = 'UAH'
    await nextTick()
    expect(g.eqAutoSelected.value).toBe(false)
  })

  it('keeps the newest graph load when an older load rejects last', async () => {
    const olderSnapshot = deferred<ReturnType<typeof snapshotEnvelope>>()
    const latestSnapshot = deferred<ReturnType<typeof snapshotEnvelope>>()
    apiMock.graphSnapshot.mockReturnValueOnce(olderSnapshot.promise).mockReturnValueOnce(latestSnapshot.promise)

    const g = useGraphData({
      eq: ref('EUR'),
      focusMode: ref(false),
      focusRootPid: ref(''),
      focusDepth: ref(1),
      statusFilter: ref<string[]>([]),
    })

    const olderLoad = g.loadData()
    const latestLoad = g.loadData()
    latestSnapshot.resolve(snapshotEnvelope('LATEST'))
    await latestLoad

    expect(g.participants.value.map((participant) => participant.pid)).toEqual(['LATEST'])
    expect(g.error.value).toBeNull()
    expect(g.loading.value).toBe(false)

    olderSnapshot.reject(new Error('stale graph failure'))
    await olderLoad

    expect(g.participants.value.map((participant) => participant.pid)).toEqual(['LATEST'])
    expect(g.error.value).toBeNull()
    expect(g.loading.value).toBe(false)
  })

  it('guards snapshot state at its application owner', async () => {
    const olderSnapshot = deferred<ReturnType<typeof snapshotEnvelope>>()
    const latestSnapshot = deferred<ReturnType<typeof snapshotEnvelope>>()
    apiMock.graphSnapshot.mockReturnValueOnce(olderSnapshot.promise).mockReturnValueOnce(latestSnapshot.promise)

    const eq = ref('EUR')
    const g = useGraphData({
      eq,
      focusMode: ref(false),
      focusRootPid: ref(''),
      focusDepth: ref(1),
      statusFilter: ref<string[]>([]),
    })

    const olderRefresh = g.refreshSnapshotForEq()
    eq.value = 'USD'
    const latestRefresh = g.refreshSnapshotForEq()
    latestSnapshot.resolve(snapshotEnvelope('LATEST'))
    await latestRefresh
    olderSnapshot.resolve(snapshotEnvelope('STALE'))
    await olderRefresh
    expect(g.participants.value.map((participant) => participant.pid)).toEqual(['LATEST'])
  })

  // 032 S6 (E-12): the graph kept its own precision map with a different predicate (any finite number); the one map
  // is `buildPrecisionByEquivalent`, so a precision the contract does not allow is unknown here exactly as on the lists.
  it('precisionByEq is the one precision map: normalized codes, only whole non-negative precisions', async () => {
    apiMock.graphSnapshot.mockResolvedValueOnce({
      ...snapshotEnvelope('A'),
      equivalents: [
        { code: ' uah ', precision: 2, description: '', is_active: true },
        { code: 'HOUR', precision: 0, description: '', is_active: true },
        { code: 'BAD1', precision: -1, description: '', is_active: true },
        { code: 'BAD2', precision: 1.5, description: '', is_active: true },
      ],
    })
    const g = useGraphData({
      eq: ref('UAH'),
      focusMode: ref(false),
      focusRootPid: ref(''),
      focusDepth: ref(1),
      statusFilter: ref<string[]>([]),
    })

    await g.loadData()

    expect([...g.precisionByEq.value]).toEqual([
      ['UAH', 2],
      ['HOUR', 0],
    ])
  })

  it('asks the snapshot by equivalent only: no optional collection is requested (032 S5, F-1)', async () => {
    const eq = ref('')
    apiMock.graphSnapshot
      .mockResolvedValueOnce(snapshotEnvelope('INITIAL'))
      .mockResolvedValueOnce(snapshotEnvelope('PRIMARY_EQ'))
    const g = useGraphData({
      eq,
      focusMode: ref(false),
      focusRootPid: ref(''),
      focusDepth: ref(1),
      statusFilter: ref<string[]>([]),
    })

    await expect(g.loadData()).resolves.toBe(true)
    expect(eq.value).toBe('EUR')
    await expect(g.refreshSnapshotForEq()).resolves.toBe(true)

    expect(apiMock.graphSnapshot.mock.calls).toEqual([
      [{ equivalent: undefined }],
      [{ equivalent: 'EUR' }],
    ])
    expect(g.participants.value.map((participant) => participant.pid)).toEqual(['PRIMARY_EQ'])
    expect(g.error.value).toBeNull()
    expect(g.loading.value).toBe(false)
  })

  it('keeps a failed equivalent refresh visible when an older full load resolves last', async () => {
    const olderSnapshot = deferred<ReturnType<typeof snapshotEnvelope>>()
    const latestSnapshot = deferred<ReturnType<typeof snapshotEnvelope>>()
    apiMock.graphSnapshot
      .mockResolvedValueOnce(snapshotEnvelope('BASE'))
      .mockReturnValueOnce(olderSnapshot.promise)
      .mockReturnValueOnce(latestSnapshot.promise)

    const eq = ref('EUR')
    const g = useGraphData({
      eq,
      focusMode: ref(false),
      focusRootPid: ref(''),
      focusDepth: ref(1),
      statusFilter: ref<string[]>([]),
    })
    await g.loadData()

    const olderLoad = g.loadData()
    eq.value = 'USD'
    const latestRefresh = g.refreshSnapshotForEq()
    latestSnapshot.reject(new Error('latest equivalent failed'))
    await latestRefresh

    expect(g.participants.value.map((participant) => participant.pid)).toEqual(['BASE'])
    expect(g.error.value).toBe('latest equivalent failed')
    expect(g.loading.value).toBe(false)

    olderSnapshot.resolve(snapshotEnvelope('STALE'))
    await olderLoad

    expect(g.participants.value.map((participant) => participant.pid)).toEqual(['BASE'])
    expect(g.error.value).toBe('latest equivalent failed')
    expect(g.loading.value).toBe(false)
  })

  it('keeps a failed focus refresh visible when an older full load resolves last', async () => {
    const olderSnapshot = deferred<ReturnType<typeof snapshotEnvelope>>()
    const latestEgo = deferred<ReturnType<typeof snapshotEnvelope>>()
    apiMock.graphSnapshot
      .mockResolvedValueOnce(snapshotEnvelope('BASE'))
      .mockReturnValueOnce(olderSnapshot.promise)
    apiMock.graphEgo.mockReturnValueOnce(latestEgo.promise)

    const focusMode = ref(false)
    const g = useGraphData({
      eq: ref('EUR'),
      focusMode,
      focusRootPid: ref('PID_A'),
      focusDepth: ref(1),
      statusFilter: ref<string[]>(['active']),
    })
    await g.loadData()

    const olderLoad = g.loadData()
    focusMode.value = true
    const latestRefresh = g.refreshForFocusMode()
    latestEgo.reject(new Error('latest focus failed'))
    await latestRefresh

    expect(g.participants.value.map((participant) => participant.pid)).toEqual(['BASE'])
    expect(g.error.value).toBe('latest focus failed')
    expect(g.loading.value).toBe(false)

    olderSnapshot.resolve(snapshotEnvelope('STALE'))
    await olderLoad

    expect(g.participants.value.map((participant) => participant.pid)).toEqual(['BASE'])
    expect(g.error.value).toBe('latest focus failed')
    expect(g.loading.value).toBe(false)
  })

  it('restores the latest cached full snapshot after a focus view exits', async () => {
    const focusMode = ref(false)
    const focusRootPid = ref('')
    apiMock.graphSnapshot
      .mockResolvedValueOnce(snapshotEnvelope('BASE'))
      .mockResolvedValueOnce(snapshotEnvelope('LATEST_FULL'))
    apiMock.graphEgo.mockResolvedValueOnce(snapshotEnvelope('FOCUS'))
    const g = useGraphData({
      eq: ref('EUR'),
      focusMode,
      focusRootPid,
      focusDepth: ref(1),
      statusFilter: ref<string[]>([]),
    })

    await expect(g.loadData()).resolves.toBe(true)
    await expect(g.loadData()).resolves.toBe(true)

    focusMode.value = true
    focusRootPid.value = 'PID_FOCUS'
    await expect(g.refreshForFocusMode()).resolves.toBe(true)
    expect(g.participants.value.map((participant) => participant.pid)).toEqual(['FOCUS'])

    focusMode.value = false
    focusRootPid.value = ''
    await expect(g.refreshForFocusMode()).resolves.toBe(true)
    expect(g.participants.value.map((participant) => participant.pid)).toEqual(['LATEST_FULL'])
    expect(apiMock.graphSnapshot).toHaveBeenCalledTimes(2)
    expect(g.error.value).toBeNull()
  })

  it('does not let a superseded global load replace the cached full snapshot across focus', async () => {
    const staleSnapshot = deferred<ReturnType<typeof snapshotEnvelope>>()
    const focusMode = ref(false)
    const focusRootPid = ref('')
    apiMock.graphSnapshot
      .mockResolvedValueOnce(snapshotEnvelope('FULL'))
      .mockReturnValueOnce(staleSnapshot.promise)
    apiMock.graphEgo.mockResolvedValueOnce(snapshotEnvelope('FOCUS'))
    const g = useGraphData({
      eq: ref('EUR'),
      focusMode,
      focusRootPid,
      focusDepth: ref(1),
      statusFilter: ref<string[]>([]),
    })
    await g.loadData()

    const staleFullLoad = g.loadData()

    focusMode.value = true
    focusRootPid.value = 'PID_FOCUS'
    await expect(g.refreshForFocusMode()).resolves.toBe(true)

    staleSnapshot.resolve(snapshotEnvelope('STALE_FULL'))
    await expect(staleFullLoad).resolves.toBe(false)

    focusMode.value = false
    focusRootPid.value = ''
    await expect(g.refreshForFocusMode()).resolves.toBe(true)
    expect(g.participants.value.map((participant) => participant.pid)).toEqual(['FULL'])
  })

  it.each(['success', 'failure'] as const)(
    'loads a global view on focus exit without a cached full snapshot (%s)',
    async (outcome) => {
      const globalSnapshot = deferred<ReturnType<typeof snapshotEnvelope>>()
      const focusMode = ref(true)
      apiMock.graphEgo.mockResolvedValueOnce(snapshotEnvelope('FOCUS'))
      apiMock.graphSnapshot.mockReturnValueOnce(globalSnapshot.promise)
      const g = useGraphData({
        eq: ref('EUR'),
        focusMode,
        focusRootPid: ref('PID_FOCUS'),
        focusDepth: ref(1),
        statusFilter: ref<string[]>([]),
      })
      await expect(g.refreshForFocusMode()).resolves.toBe(true)
      expect(g.participants.value.map((participant) => participant.pid)).toEqual(['FOCUS'])

      focusMode.value = false
      const exitFocus = g.refreshForFocusMode()

      expect(apiMock.graphSnapshot).toHaveBeenCalledWith({ equivalent: 'EUR' })
      expect(apiMock.graphSnapshot).toHaveBeenCalledTimes(1)
      expect(g.participants.value).toEqual([])

      if (outcome === 'success') {
        globalSnapshot.resolve(snapshotEnvelope('GLOBAL'))
        await expect(exitFocus).resolves.toBe(true)
        expect(g.participants.value.map((participant) => participant.pid)).toEqual(['GLOBAL'])
        expect(g.error.value).toBeNull()
      } else {
        globalSnapshot.reject(new Error('global view unavailable'))
        await expect(exitFocus).resolves.toBe(false)
        expect(g.participants.value).toEqual([])
        expect(g.error.value).toBe('global view unavailable')
      }
    },
  )

  it('invalidates a pending focus load before it can commit', async () => {
    const focusSnapshot = deferred<ReturnType<typeof snapshotEnvelope>>()
    const focusMode = ref(false)
    apiMock.graphSnapshot.mockResolvedValueOnce(snapshotEnvelope('FULL'))
    apiMock.graphEgo.mockReturnValueOnce(focusSnapshot.promise)
    const g = useGraphData({
      eq: ref('EUR'),
      focusMode,
      focusRootPid: ref('PID_FOCUS'),
      focusDepth: ref(1),
      statusFilter: ref<string[]>([]),
    })
    await g.loadData()

    focusMode.value = true
    const pendingFocus = g.refreshForFocusMode()
    g.invalidateDataOwnership()
    focusSnapshot.resolve(snapshotEnvelope('STALE_FOCUS'))

    await expect(pendingFocus).resolves.toBe(false)
    expect(g.participants.value.map((participant) => participant.pid)).toEqual(['FULL'])
    expect(g.loading.value).toBe(false)
  })

  it('reloads the active focus view and only uses the global loader after focus is cleared', async () => {
    const focusMode = ref(true)
    apiMock.graphEgo.mockResolvedValueOnce(snapshotEnvelope('FOCUS'))
    apiMock.graphSnapshot.mockResolvedValueOnce(snapshotEnvelope('GLOBAL'))
    const g = useGraphData({
      eq: ref('EUR'),
      focusMode,
      focusRootPid: ref('PID_A'),
      focusDepth: ref(1),
      statusFilter: ref<string[]>(['active']),
    })

    await expect(g.reloadCurrentView()).resolves.toBe(true)

    expect(apiMock.graphEgo).toHaveBeenCalledWith({
      pid: 'PID_A',
      depth: 1,
      equivalent: 'EUR',
      status: ['active'],
    })
    expect(apiMock.graphSnapshot).not.toHaveBeenCalled()
    expect(g.participants.value.map((participant) => participant.pid)).toEqual(['FOCUS'])

    focusMode.value = false
    await expect(g.reloadCurrentView()).resolves.toBe(true)

    expect(apiMock.graphSnapshot).toHaveBeenCalledWith({ equivalent: 'EUR' })
    expect(g.participants.value.map((participant) => participant.pid)).toEqual(['GLOBAL'])
  })

  it('keeps the latest focus equivalent and status when an older focus load resolves last', async () => {
    const olderEgo = deferred<ReturnType<typeof snapshotEnvelope>>()
    const latestEgo = deferred<ReturnType<typeof snapshotEnvelope>>()
    apiMock.graphEgo.mockReturnValueOnce(olderEgo.promise).mockReturnValueOnce(latestEgo.promise)

    const eq = ref('EUR')
    const statusFilter = ref<string[]>(['active'])
    const g = useGraphData({
      eq,
      focusMode: ref(true),
      focusRootPid: ref('PID_A'),
      focusDepth: ref(1),
      statusFilter,
    })

    const olderLoad = g.refreshForFocusMode()
    eq.value = 'USD'
    statusFilter.value = ['closed']
    const latestLoad = g.refreshForFocusMode()
    latestEgo.resolve(snapshotEnvelope('LATEST'))
    await latestLoad

    olderEgo.resolve(snapshotEnvelope('STALE'))
    await olderLoad

    expect(apiMock.graphEgo).toHaveBeenNthCalledWith(1, {
      pid: 'PID_A',
      depth: 1,
      equivalent: 'EUR',
      status: ['active'],
    })
    expect(apiMock.graphEgo).toHaveBeenNthCalledWith(2, {
      pid: 'PID_A',
      depth: 1,
      equivalent: 'USD',
      status: ['closed'],
    })
    expect(g.participants.value.map((participant) => participant.pid)).toEqual(['LATEST'])
    expect(g.error.value).toBeNull()
    expect(g.loading.value).toBe(false)
  })

  it('does not apply a pending full snapshot after scope disposal', async () => {
    const snapshot = deferred<ReturnType<typeof snapshotEnvelope>>()
    apiMock.graphSnapshot.mockReturnValueOnce(snapshot.promise)
    const scope = effectScope()
    const graph = scope.run(() => useGraphData({
      eq: ref('EUR'),
      focusMode: ref(false),
      focusRootPid: ref(''),
      focusDepth: ref(1),
      statusFilter: ref<string[]>([]),
    }))
    if (!graph) throw new Error('Expected graph data owner')

    const pending = graph.loadData()
    scope.stop()
    snapshot.resolve(snapshotEnvelope('LATE'))
    await pending

    expect(graph.participants.value).toEqual([])
    expect(graph.error.value).toBeNull()
  })

  it('does not apply a pending focus result after scope disposal', async () => {
    const ego = deferred<ReturnType<typeof snapshotEnvelope>>()
    apiMock.graphEgo.mockReturnValueOnce(ego.promise)
    const scope = effectScope()
    const graph = scope.run(() => useGraphData({
      eq: ref('EUR'),
      focusMode: ref(true),
      focusRootPid: ref('PID_A'),
      focusDepth: ref(1),
      statusFilter: ref<string[]>(['active']),
    }))
    if (!graph) throw new Error('Expected graph data owner')

    const pendingFocus = graph.refreshForFocusMode()
    scope.stop()
    ego.resolve(snapshotEnvelope('LATE_FOCUS'))
    await pendingFocus

    expect(graph.participants.value).toEqual([])
    expect(graph.error.value).toBeNull()
  })
})
