import { beforeEach, describe, expect, it, vi } from 'vitest'
import { effectScope, ref } from 'vue'

const apiMock = vi.hoisted(() => ({
  graphSnapshot: vi.fn(),
  graphEgo: vi.fn(),
  clearingCycles: vi.fn(),
}))

vi.mock('../api', () => ({ api: apiMock }))

import {
  computeIncidentRatioByPid,
  computePrimaryEquivalent,
  filterTrustlinesByEqAndStatus,
  normalizeEqCode,
  useGraphData,
} from './useGraphData'
import type { Equivalent, Incident, Trustline } from '../pages/graph/graphTypes'

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
    success: true as const,
    data: {
      participants: [{ pid }],
      trustlines: [],
      incidents: [],
      equivalents: [{ code: 'EUR', precision: 2, description: '', is_active: true }],
      debts: [],
      audit_log: [],
      transactions: [],
    },
  }
}

function cyclesEnvelope(code: string) {
  return {
    success: true as const,
    data: {
      equivalents: {
        [code]: { cycles: [] },
      },
    },
  }
}

describe('useGraphData', () => {
  beforeEach(() => vi.resetAllMocks())

  it('normalizeEqCode trims and uppercases', () => {
    expect(normalizeEqCode(' eur ')).toBe('EUR')
    expect(normalizeEqCode('')).toBe('')
  })

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

  it('computeIncidentRatioByPid filters by eq and keeps max ratio per pid', () => {
    const incidents: Incident[] = [
      { tx_id: '1', state: 'open', initiator_pid: 'PID_A', equivalent: 'EUR', age_seconds: 10, sla_seconds: 10 }, // 1.0
      { tx_id: '2', state: 'open', initiator_pid: 'PID_A', equivalent: 'EUR', age_seconds: 30, sla_seconds: 10 }, // 3.0
      { tx_id: '3', state: 'open', initiator_pid: 'PID_B', equivalent: 'EUR', age_seconds: 10, sla_seconds: 0 }, // 0
      { tx_id: '4', state: 'open', initiator_pid: 'PID_A', equivalent: 'USD', age_seconds: 999, sla_seconds: 1 },
    ]

    const m = computeIncidentRatioByPid({ incidents, equivalent: 'EUR' })
    expect(m.get('PID_A')).toBe(3)
    expect(m.get('PID_B')).toBeUndefined()
    expect(m.has('PID_C')).toBe(false)
  })

  it('availableEquivalents merges dataset + trustlines (no ALL option)', () => {
    const eq = ref('')
    const isRealMode = ref(false)
    const focusMode = ref(false)
    const focusRootPid = ref('')
    const focusDepth = ref(1)
    const statusFilter = ref<string[]>([])

    const g = useGraphData({ eq, isRealMode, focusMode, focusRootPid, focusDepth, statusFilter })
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

  it('does not claim mock focus data is ready while the full snapshot is loading', async () => {
    const snapshot = deferred<ReturnType<typeof snapshotEnvelope>>()
    const cycles = deferred<ReturnType<typeof cyclesEnvelope>>()
    apiMock.graphSnapshot.mockReturnValueOnce(snapshot.promise)
    apiMock.clearingCycles.mockReturnValueOnce(cycles.promise)
    const graph = useGraphData({
      eq: ref('EUR'),
      isRealMode: ref(false),
      focusMode: ref(true),
      focusRootPid: ref('PID_A'),
      focusDepth: ref(1),
      statusFilter: ref<string[]>([]),
    })

    const pending = graph.loadData()
    expect(graph.loading.value).toBe(true)
    await expect(graph.refreshForFocusMode()).resolves.toBe(false)

    snapshot.resolve(snapshotEnvelope('READY'))
    cycles.resolve(cyclesEnvelope('READY'))
    await pending

    await expect(graph.refreshForFocusMode()).resolves.toBe(true)
  })

  it('keeps the newest graph load when an older load rejects last', async () => {
    const olderSnapshot = deferred<ReturnType<typeof snapshotEnvelope>>()
    const latestSnapshot = deferred<ReturnType<typeof snapshotEnvelope>>()
    const olderCycles = deferred<ReturnType<typeof cyclesEnvelope>>()
    const latestCycles = deferred<ReturnType<typeof cyclesEnvelope>>()
    apiMock.graphSnapshot.mockReturnValueOnce(olderSnapshot.promise).mockReturnValueOnce(latestSnapshot.promise)
    apiMock.clearingCycles.mockReturnValueOnce(olderCycles.promise).mockReturnValueOnce(latestCycles.promise)

    const g = useGraphData({
      eq: ref('EUR'),
      isRealMode: ref(true),
      focusMode: ref(false),
      focusRootPid: ref(''),
      focusDepth: ref(1),
      statusFilter: ref<string[]>([]),
    })

    const olderLoad = g.loadData()
    const latestLoad = g.loadData()
    latestSnapshot.resolve(snapshotEnvelope('LATEST'))
    latestCycles.resolve(cyclesEnvelope('LATEST'))
    await latestLoad

    expect(g.participants.value.map((participant) => participant.pid)).toEqual(['LATEST'])
    expect(g.clearingCycles.value).toEqual(cyclesEnvelope('LATEST').data)
    expect(g.error.value).toBeNull()
    expect(g.loading.value).toBe(false)

    olderSnapshot.reject(new Error('stale graph failure'))
    await olderLoad

    expect(g.participants.value.map((participant) => participant.pid)).toEqual(['LATEST'])
    expect(g.clearingCycles.value).toEqual(cyclesEnvelope('LATEST').data)
    expect(g.error.value).toBeNull()
    expect(g.loading.value).toBe(false)
  })

  it('keeps a successful graph snapshot visible when clearing cycles fail', async () => {
    apiMock.graphSnapshot.mockResolvedValueOnce(snapshotEnvelope('SNAPSHOT'))
    apiMock.clearingCycles.mockResolvedValueOnce({
      success: false,
      error: { code: 'cycles_unavailable', message: 'clearing cycles unavailable' },
    })
    const g = useGraphData({
      eq: ref('EUR'),
      isRealMode: ref(true),
      focusMode: ref(false),
      focusRootPid: ref(''),
      focusDepth: ref(1),
      statusFilter: ref<string[]>([]),
    })

    await expect(g.loadData()).resolves.toBe(true)

    expect(g.participants.value.map((participant) => participant.pid)).toEqual(['SNAPSHOT'])
    expect(g.clearingCycles.value).toBeNull()
    expect(g.error.value).toBe('clearing cycles unavailable')
    expect(g.loading.value).toBe(false)
  })

  it('guards snapshot and clearing-cycle state at their application owners', async () => {
    const olderSnapshot = deferred<ReturnType<typeof snapshotEnvelope>>()
    const latestSnapshot = deferred<ReturnType<typeof snapshotEnvelope>>()
    apiMock.graphSnapshot.mockReturnValueOnce(olderSnapshot.promise).mockReturnValueOnce(latestSnapshot.promise)

    const eq = ref('EUR')
    const g = useGraphData({
      eq,
      isRealMode: ref(true),
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

    const olderCycles = deferred<ReturnType<typeof cyclesEnvelope>>()
    const latestCycles = deferred<ReturnType<typeof cyclesEnvelope>>()
    apiMock.clearingCycles.mockReturnValueOnce(olderCycles.promise).mockReturnValueOnce(latestCycles.promise)
    const olderCycleRefresh = g.refreshClearingCyclesForParticipant('OLD')
    const latestCycleRefresh = g.refreshClearingCyclesForParticipant('LATEST')
    latestCycles.resolve(cyclesEnvelope('LATEST'))
    await latestCycleRefresh
    olderCycles.resolve(cyclesEnvelope('STALE'))
    await olderCycleRefresh
    expect(g.clearingCycles.value).toEqual(cyclesEnvelope('LATEST').data)
  })

  it.each(['participant-first', 'load-first'] as const)(
    'invalidates a participant cycle refresh when a full load resolves %s',
    async (resolutionOrder) => {
      const participantCycles = deferred<ReturnType<typeof cyclesEnvelope>>()
      const loadSnapshot = deferred<ReturnType<typeof snapshotEnvelope>>()
      const loadCycles = deferred<ReturnType<typeof cyclesEnvelope>>()
      apiMock.graphSnapshot.mockReturnValueOnce(loadSnapshot.promise)
      apiMock.clearingCycles
        .mockReturnValueOnce(participantCycles.promise)
        .mockReturnValueOnce(loadCycles.promise)

      const g = useGraphData({
        eq: ref('EUR'),
        isRealMode: ref(true),
        focusMode: ref(false),
        focusRootPid: ref(''),
        focusDepth: ref(1),
        statusFilter: ref<string[]>([]),
      })

      const participantRefresh = g.refreshClearingCyclesForParticipant('PID_A')
      const fullLoad = g.loadData()

      if (resolutionOrder === 'participant-first') {
        participantCycles.resolve(cyclesEnvelope('PARTICIPANT'))
        expect(await participantRefresh).toBe(false)
        loadSnapshot.resolve(snapshotEnvelope('LOAD'))
        loadCycles.resolve(cyclesEnvelope('LOAD'))
        await fullLoad
      } else {
        loadSnapshot.resolve(snapshotEnvelope('LOAD'))
        loadCycles.resolve(cyclesEnvelope('LOAD'))
        await fullLoad
        participantCycles.resolve(cyclesEnvelope('PARTICIPANT'))
        expect(await participantRefresh).toBe(false)
      }

      expect(g.clearingCycles.value).toEqual(cyclesEnvelope('LOAD').data)
    },
  )

  it('does not let a no-op participant deselection supersede pending load cycles', async () => {
    const snapshot = deferred<ReturnType<typeof snapshotEnvelope>>()
    const cycles = deferred<ReturnType<typeof cyclesEnvelope>>()
    apiMock.graphSnapshot.mockReturnValueOnce(snapshot.promise)
    apiMock.clearingCycles.mockReturnValueOnce(cycles.promise)
    const g = useGraphData({
      eq: ref('EUR'),
      isRealMode: ref(true),
      focusMode: ref(false),
      focusRootPid: ref(''),
      focusDepth: ref(1),
      statusFilter: ref<string[]>([]),
    })

    const fullLoad = g.loadData()
    await expect(g.refreshClearingCyclesForParticipant('')).resolves.toBe(true)
    expect(apiMock.clearingCycles.mock.calls).toEqual([[]])

    snapshot.resolve(snapshotEnvelope('LOAD'))
    cycles.resolve(cyclesEnvelope('LOAD'))
    await expect(fullLoad).resolves.toBe(true)

    expect(g.participants.value.map((participant) => participant.pid)).toEqual(['LOAD'])
    expect(g.clearingCycles.value).toEqual(cyclesEnvelope('LOAD').data)
    expect(g.error.value).toBeNull()
  })

  it('invalidates pending participant cycles when the participant is deselected', async () => {
    const participantCycles = deferred<ReturnType<typeof cyclesEnvelope>>()
    apiMock.graphSnapshot.mockResolvedValueOnce(snapshotEnvelope('BASE'))
    apiMock.clearingCycles
      .mockResolvedValueOnce(cyclesEnvelope('FULL'))
      .mockReturnValueOnce(participantCycles.promise)
    const g = useGraphData({
      eq: ref('EUR'),
      isRealMode: ref(true),
      focusMode: ref(false),
      focusRootPid: ref(''),
      focusDepth: ref(1),
      statusFilter: ref<string[]>([]),
    })
    await g.loadData()

    const pendingParticipant = g.refreshClearingCyclesForParticipant('PID_A')
    await expect(g.refreshClearingCyclesForParticipant('')).resolves.toBe(true)
    participantCycles.resolve(cyclesEnvelope('PARTICIPANT'))

    await expect(pendingParticipant).resolves.toBe(false)
    expect(g.clearingCycles.value).toEqual(cyclesEnvelope('FULL').data)
    expect(apiMock.clearingCycles.mock.calls).toEqual([[], [{ participant_pid: 'PID_A' }]])
  })

  it('keeps a cycle-owned error across an initial primary-equivalent snapshot refresh', async () => {
    const eq = ref('')
    apiMock.graphSnapshot
      .mockResolvedValueOnce(snapshotEnvelope('INITIAL'))
      .mockResolvedValueOnce(snapshotEnvelope('PRIMARY_EQ'))
    apiMock.clearingCycles.mockResolvedValueOnce({
      success: false,
      error: { code: 'cycles_unavailable', message: 'clearing cycles unavailable' },
    })
    const g = useGraphData({
      eq,
      isRealMode: ref(true),
      focusMode: ref(false),
      focusRootPid: ref(''),
      focusDepth: ref(1),
      statusFilter: ref<string[]>([]),
    })

    await expect(g.loadData()).resolves.toBe(true)
    expect(eq.value).toBe('EUR')
    expect(g.error.value).toBe('clearing cycles unavailable')

    await expect(g.refreshSnapshotForEq()).resolves.toBe(true)

    expect(apiMock.graphSnapshot.mock.calls).toEqual([[{ equivalent: undefined }], [{ equivalent: 'EUR' }]])
    expect(g.participants.value.map((participant) => participant.pid)).toEqual(['PRIMARY_EQ'])
    expect(g.clearingCycles.value).toBeNull()
    expect(g.error.value).toBe('clearing cycles unavailable')
    expect(g.loading.value).toBe(false)
  })

  it('keeps a failed equivalent refresh visible when an older full load resolves last', async () => {
    const olderSnapshot = deferred<ReturnType<typeof snapshotEnvelope>>()
    const olderCycles = deferred<ReturnType<typeof cyclesEnvelope>>()
    const latestSnapshot = deferred<ReturnType<typeof snapshotEnvelope>>()
    apiMock.graphSnapshot
      .mockResolvedValueOnce(snapshotEnvelope('BASE'))
      .mockReturnValueOnce(olderSnapshot.promise)
      .mockReturnValueOnce(latestSnapshot.promise)
    apiMock.clearingCycles
      .mockResolvedValueOnce(cyclesEnvelope('BASE'))
      .mockReturnValueOnce(olderCycles.promise)

    const eq = ref('EUR')
    const g = useGraphData({
      eq,
      isRealMode: ref(true),
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
    olderCycles.resolve(cyclesEnvelope('STALE'))
    await olderLoad

    expect(g.participants.value.map((participant) => participant.pid)).toEqual(['BASE'])
    expect(g.error.value).toBe('latest equivalent failed')
    expect(g.loading.value).toBe(false)
  })

  it('keeps a failed focus refresh visible when an older full load resolves last', async () => {
    const olderSnapshot = deferred<ReturnType<typeof snapshotEnvelope>>()
    const olderCycles = deferred<ReturnType<typeof cyclesEnvelope>>()
    const latestEgo = deferred<ReturnType<typeof snapshotEnvelope>>()
    const latestCycles = deferred<ReturnType<typeof cyclesEnvelope>>()
    apiMock.graphSnapshot
      .mockResolvedValueOnce(snapshotEnvelope('BASE'))
      .mockReturnValueOnce(olderSnapshot.promise)
    apiMock.graphEgo.mockReturnValueOnce(latestEgo.promise)
    apiMock.clearingCycles
      .mockResolvedValueOnce(cyclesEnvelope('BASE'))
      .mockReturnValueOnce(olderCycles.promise)
      .mockReturnValueOnce(latestCycles.promise)

    const focusMode = ref(false)
    const g = useGraphData({
      eq: ref('EUR'),
      isRealMode: ref(true),
      focusMode,
      focusRootPid: ref('PID_A'),
      focusDepth: ref(1),
      statusFilter: ref<string[]>(['active']),
    })
    await g.loadData()

    const olderLoad = g.loadData()
    focusMode.value = true
    const latestRefresh = g.refreshForFocusMode()
    latestCycles.resolve(cyclesEnvelope('LATEST'))
    latestEgo.reject(new Error('latest focus failed'))
    await latestRefresh

    expect(g.participants.value.map((participant) => participant.pid)).toEqual(['BASE'])
    expect(g.error.value).toBe('latest focus failed')
    expect(g.loading.value).toBe(false)

    olderSnapshot.resolve(snapshotEnvelope('STALE'))
    olderCycles.resolve(cyclesEnvelope('STALE'))
    await olderLoad

    expect(g.participants.value.map((participant) => participant.pid)).toEqual(['BASE'])
    expect(g.error.value).toBe('latest focus failed')
    expect(g.loading.value).toBe(false)
  })

  it('keeps a successful focus snapshot visible when clearing cycles fail', async () => {
    apiMock.graphEgo.mockResolvedValueOnce(snapshotEnvelope('FOCUS'))
    apiMock.clearingCycles.mockResolvedValueOnce({
      success: false,
      error: { code: 'cycles_unavailable', message: 'focus cycles unavailable' },
    })
    const g = useGraphData({
      eq: ref('EUR'),
      isRealMode: ref(true),
      focusMode: ref(true),
      focusRootPid: ref('PID_A'),
      focusDepth: ref(1),
      statusFilter: ref<string[]>(['active']),
    })

    await expect(g.refreshForFocusMode()).resolves.toBe(true)

    expect(g.participants.value.map((participant) => participant.pid)).toEqual(['FOCUS'])
    expect(g.clearingCycles.value).toBeNull()
    expect(g.error.value).toBe('focus cycles unavailable')
    expect(g.loading.value).toBe(false)
  })

  it('keeps the latest focus equivalent and status when an older focus load resolves last', async () => {
    const olderEgo = deferred<ReturnType<typeof snapshotEnvelope>>()
    const latestEgo = deferred<ReturnType<typeof snapshotEnvelope>>()
    const olderCycles = deferred<ReturnType<typeof cyclesEnvelope>>()
    const latestCycles = deferred<ReturnType<typeof cyclesEnvelope>>()
    apiMock.graphEgo.mockReturnValueOnce(olderEgo.promise).mockReturnValueOnce(latestEgo.promise)
    apiMock.clearingCycles.mockReturnValueOnce(olderCycles.promise).mockReturnValueOnce(latestCycles.promise)

    const eq = ref('EUR')
    const statusFilter = ref<string[]>(['active'])
    const g = useGraphData({
      eq,
      isRealMode: ref(true),
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
    latestCycles.resolve(cyclesEnvelope('LATEST'))
    await latestLoad

    olderEgo.resolve(snapshotEnvelope('STALE'))
    olderCycles.resolve(cyclesEnvelope('STALE'))
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
    expect(g.clearingCycles.value).toEqual(cyclesEnvelope('LATEST').data)
    expect(g.error.value).toBeNull()
    expect(g.loading.value).toBe(false)
  })

  it('does not apply a pending full snapshot or cycles after scope disposal', async () => {
    const snapshot = deferred<ReturnType<typeof snapshotEnvelope>>()
    const cycles = deferred<ReturnType<typeof cyclesEnvelope>>()
    apiMock.graphSnapshot.mockReturnValueOnce(snapshot.promise)
    apiMock.clearingCycles.mockReturnValueOnce(cycles.promise)
    const scope = effectScope()
    const graph = scope.run(() => useGraphData({
      eq: ref('EUR'),
      isRealMode: ref(true),
      focusMode: ref(false),
      focusRootPid: ref(''),
      focusDepth: ref(1),
      statusFilter: ref<string[]>([]),
    }))
    if (!graph) throw new Error('Expected graph data owner')

    const pending = graph.loadData()
    scope.stop()
    snapshot.resolve(snapshotEnvelope('LATE'))
    cycles.resolve(cyclesEnvelope('LATE'))
    await pending

    expect(graph.participants.value).toEqual([])
    expect(graph.clearingCycles.value).toBeNull()
    expect(graph.error.value).toBeNull()
  })

  it('does not apply pending focus or participant-cycle results after scope disposal', async () => {
    const ego = deferred<ReturnType<typeof snapshotEnvelope>>()
    const focusCycles = deferred<ReturnType<typeof cyclesEnvelope>>()
    const participantCycles = deferred<ReturnType<typeof cyclesEnvelope>>()
    apiMock.graphEgo.mockReturnValueOnce(ego.promise)
    apiMock.clearingCycles
      .mockReturnValueOnce(focusCycles.promise)
      .mockReturnValueOnce(participantCycles.promise)
    const scope = effectScope()
    const graph = scope.run(() => useGraphData({
      eq: ref('EUR'),
      isRealMode: ref(true),
      focusMode: ref(true),
      focusRootPid: ref('PID_A'),
      focusDepth: ref(1),
      statusFilter: ref<string[]>(['active']),
    }))
    if (!graph) throw new Error('Expected graph data owner')

    const pendingFocus = graph.refreshForFocusMode()
    const pendingCycles = graph.refreshClearingCyclesForParticipant('PID_A')
    scope.stop()
    ego.resolve(snapshotEnvelope('LATE_FOCUS'))
    focusCycles.resolve(cyclesEnvelope('LATE_FOCUS'))
    participantCycles.resolve(cyclesEnvelope('LATE_PARTICIPANT'))
    await Promise.all([pendingFocus, pendingCycles])

    expect(graph.participants.value).toEqual([])
    expect(graph.clearingCycles.value).toBeNull()
    expect(graph.error.value).toBeNull()
  })
})
