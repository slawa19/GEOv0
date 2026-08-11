import { beforeEach, describe, expect, it, vi } from 'vitest'
import { computed, effectScope, nextTick, ref } from 'vue'

const apiMock = vi.hoisted(() => ({
  participantMetrics: vi.fn(),
}))

vi.mock('../api', () => ({ api: apiMock }))

import { useGraphAnalytics } from './useGraphAnalytics'
import type { SelectedInfo } from './useGraphVisualization'
import type { AuditLogEntry, ClearingCycles, Debt, Incident, Participant, Transaction, Trustline } from '../pages/graph/graphTypes'
import type { ParticipantMetrics } from '../types/domain'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

function metricsEnvelope(net: string) {
  const metrics: ParticipantMetrics = {
    pid: 'PID_A',
    equivalent: 'EUR',
    balance_rows: [
      {
        equivalent: 'EUR',
        outgoing_limit: '0.00',
        outgoing_used: '0.00',
        incoming_limit: '0.00',
        incoming_used: '0.00',
        total_debt: '0.00',
        total_credit: '0.00',
        net,
      },
    ],
  }
  return { success: true as const, data: metrics }
}

describe('useGraphAnalytics (fixtures-first)', () => {
  beforeEach(() => vi.resetAllMocks())

  it('computes selectedBalanceRows from trustlines + debts', () => {
    const selected = ref<SelectedInfo | null>({ kind: 'node', pid: 'PID_A', degree: 0, inDegree: 0, outDegree: 0 })

    const participants = ref<Participant[]>([
      { pid: 'PID_A', display_name: 'Alice' },
      { pid: 'PID_B', display_name: 'Bob' },
      { pid: 'PID_C', display_name: 'Carol' },
    ])

    const trustlines = ref<Trustline[]>([
      // PID_A is creditor (outgoing limit)
      { from: 'PID_A', to: 'PID_B', equivalent: 'EUR', limit: '10.00', used: '3.00', available: '7.00', status: 'active', created_at: 't' },
      // PID_A is debtor (incoming limit)
      { from: 'PID_C', to: 'PID_A', equivalent: 'EUR', limit: '5.00', used: '1.00', available: '4.00', status: 'active', created_at: 't' },
    ])

    const debts = ref<Debt[]>([
      { debtor: 'PID_A', creditor: 'PID_B', equivalent: 'EUR', amount: '3.00' },
      { debtor: 'PID_C', creditor: 'PID_A', equivalent: 'EUR', amount: '1.50' },
    ])

    const participantByPid = computed(() => {
      const m = new Map<string, Participant>()
      for (const p of participants.value) m.set(p.pid, p)
      return m
    })

    const g = useGraphAnalytics({
      isRealMode: computed(() => false),
      threshold: ref('0.10'),
      analyticsEq: computed(() => 'EUR'),

      precisionByEq: computed(() => new Map([['EUR', 2]])),
      availableEquivalents: computed(() => ['ALL', 'EUR']),
      participantByPid,

      participants,
      trustlines,
      debts,
      incidents: ref<Incident[]>([]),
      auditLog: ref<AuditLogEntry[]>([]),
      transactions: ref<Transaction[]>([]),
      clearingCycles: ref<ClearingCycles | null>(null),

      selected,
    })

    expect(g.selectedBalanceRows.value).toEqual([
      {
        equivalent: 'EUR',
        outgoing_limit: '10.00',
        outgoing_used: '3.00',
        incoming_limit: '5.00',
        incoming_used: '1.00',
        total_debt: '3.00',
        total_credit: '1.50',
        net: '-1.50',
      },
    ])
  })

  it('computes concentration (top shares + HHI) from debts', () => {
    const selected = ref<SelectedInfo | null>({ kind: 'node', pid: 'PID_A', degree: 0, inDegree: 0, outDegree: 0 })

    const participants = ref<Participant[]>([
      { pid: 'PID_A', display_name: 'Alice' },
      { pid: 'PID_B', display_name: 'Bob' },
      { pid: 'PID_C', display_name: 'Carol' },
      { pid: 'PID_D', display_name: 'Dan' },
      { pid: 'PID_E', display_name: 'Eve' },
    ])

    const debts = ref<Debt[]>([
      // outgoing (you owe): total 4.00 => shares 0.75 + 0.25, HHI = 0.625
      { debtor: 'PID_A', creditor: 'PID_B', equivalent: 'EUR', amount: '3.00' },
      { debtor: 'PID_A', creditor: 'PID_C', equivalent: 'EUR', amount: '1.00' },
      // incoming (owed to you): total 4.00 => shares 0.5 + 0.5, HHI = 0.5
      { debtor: 'PID_D', creditor: 'PID_A', equivalent: 'EUR', amount: '2.00' },
      { debtor: 'PID_E', creditor: 'PID_A', equivalent: 'EUR', amount: '2.00' },
    ])

    const participantByPid = computed(() => {
      const m = new Map<string, Participant>()
      for (const p of participants.value) m.set(p.pid, p)
      return m
    })

    const g = useGraphAnalytics({
      isRealMode: computed(() => false),
      threshold: ref('0.10'),
      analyticsEq: computed(() => 'EUR'),

      precisionByEq: computed(() => new Map([['EUR', 2]])),
      availableEquivalents: computed(() => ['ALL', 'EUR']),
      participantByPid,

      participants,
      trustlines: ref<Trustline[]>([]),
      debts,
      incidents: ref<Incident[]>([]),
      auditLog: ref<AuditLogEntry[]>([]),
      transactions: ref<Transaction[]>([]),
      clearingCycles: ref<ClearingCycles | null>(null),

      selected,
    })

    const c = g.selectedConcentration.value
    expect(c.eq).toBe('EUR')

    expect(c.outgoing.top1).toBeCloseTo(0.75)
    expect(c.outgoing.top5).toBeCloseTo(1)
    expect(c.outgoing.hhi).toBeCloseTo(0.75 ** 2 + 0.25 ** 2)
    expect(c.outgoing.level.label).toBe('high')

    expect(c.incoming.top1).toBeCloseTo(0.5)
    expect(c.incoming.top5).toBeCloseTo(1)
    expect(c.incoming.hhi).toBeCloseTo(0.5)
    expect(c.incoming.level.label).toBe('high')
  })

  it('flags bottlenecks in selectedCapacity using threshold', () => {
    const selected = ref<SelectedInfo | null>({ kind: 'node', pid: 'PID_A', degree: 0, inDegree: 0, outDegree: 0 })
    const threshold = ref('0.20')

    const participants = ref<Participant[]>([
      { pid: 'PID_A', display_name: 'Alice' },
      { pid: 'PID_B', display_name: 'Bob' },
      { pid: 'PID_C', display_name: 'Carol' },
    ])

    const trustlines = ref<Trustline[]>([
      // 1/10 = 0.1 < 0.2 => bottleneck
      { from: 'PID_A', to: 'PID_B', equivalent: 'EUR', limit: '10.00', used: '9.00', available: '1.00', status: 'active', created_at: 't' },
      // 4/10 = 0.4 >= 0.2 => not bottleneck
      { from: 'PID_C', to: 'PID_A', equivalent: 'EUR', limit: '10.00', used: '6.00', available: '4.00', status: 'active', created_at: 't' },
    ])

    const participantByPid = computed(() => {
      const m = new Map<string, Participant>()
      for (const p of participants.value) m.set(p.pid, p)
      return m
    })

    const g = useGraphAnalytics({
      isRealMode: computed(() => false),
      threshold,
      analyticsEq: computed(() => 'EUR'),

      precisionByEq: computed(() => new Map([['EUR', 2]])),
      availableEquivalents: computed(() => ['ALL', 'EUR']),
      participantByPid,

      participants,
      trustlines,
      debts: ref<Debt[]>([]),
      incidents: ref<Incident[]>([]),
      auditLog: ref<AuditLogEntry[]>([]),
      transactions: ref<Transaction[]>([]),
      clearingCycles: ref<ClearingCycles | null>(null),

      selected,
    })

    const cap = g.selectedCapacity.value
    expect(cap).not.toBeNull()
    expect(cap!.bottlenecks.length).toBe(1)

    const first = cap!.bottlenecks[0]
    if (!first) throw new Error('Expected bottlenecks[0] to be present')
    expect(first.dir).toBe('out')
    expect(first.other).toBe('PID_B')
  })

  it('counts canonical participant audit actions in fixture-mode activity', () => {
    const selected = ref<SelectedInfo | null>({ kind: 'node', pid: 'PID_A', degree: 0, inDegree: 0, outDegree: 0 })
    const participants = ref<Participant[]>([{ pid: 'PID_A', display_name: 'Alice' }])
    const graph = useGraphAnalytics({
      isRealMode: computed(() => false),
      threshold: ref('0.10'),
      analyticsEq: computed(() => 'EUR'),
      precisionByEq: computed(() => new Map([['EUR', 2]])),
      availableEquivalents: computed(() => ['EUR']),
      participantByPid: computed(() => new Map(participants.value.map((participant) => [participant.pid, participant]))),
      participants,
      trustlines: ref<Trustline[]>([]),
      debts: ref<Debt[]>([]),
      incidents: ref<Incident[]>([]),
      auditLog: ref<AuditLogEntry[]>([{
        id: '00000000-0000-4000-8000-000000000001',
        timestamp: new Date().toISOString(),
        action: 'admin.participants.freeze',
        object_type: 'participant',
        object_id: 'PID_A',
      }]),
      transactions: ref<Transaction[]>([]),
      clearingCycles: ref<ClearingCycles | null>(null),
      selected,
    })

    expect(graph.selectedActivity.value?.participantOps[7]).toBe(1)
  })

  it('does not let an older metrics rejection replace the latest metrics state', async () => {
    const older = deferred<ReturnType<typeof metricsEnvelope>>()
    const latest = deferred<ReturnType<typeof metricsEnvelope>>()
    apiMock.participantMetrics.mockReturnValueOnce(older.promise).mockReturnValueOnce(latest.promise)

    const selected = ref<SelectedInfo | null>({ kind: 'node', pid: 'PID_A', degree: 0, inDegree: 0, outDegree: 0 })
    const participants = ref<Participant[]>([{ pid: 'PID_A', display_name: 'Alice' }])
    const graph = useGraphAnalytics({
      isRealMode: computed(() => true),
      threshold: ref('0.10'),
      analyticsEq: computed(() => 'EUR'),
      precisionByEq: computed(() => new Map([['EUR', 2]])),
      availableEquivalents: computed(() => ['EUR']),
      participantByPid: computed(() => new Map(participants.value.map((participant) => [participant.pid, participant]))),
      participants,
      trustlines: ref<Trustline[]>([]),
      debts: ref<Debt[]>([]),
      incidents: ref<Incident[]>([]),
      auditLog: ref<AuditLogEntry[]>([]),
      transactions: ref<Transaction[]>([]),
      clearingCycles: ref<ClearingCycles | null>(null),
      selected,
    })

    const olderLoad = graph.loadSelectedMetrics()
    const latestLoad = graph.loadSelectedMetrics()
    latest.resolve(metricsEnvelope('2.00'))
    await latestLoad
    expect(graph.selectedBalanceRows.value[0]?.net).toBe('2.00')
    expect(graph.metricsError.value).toBeNull()
    expect(graph.metricsLoading.value).toBe(false)

    older.reject(new Error('stale metrics failure'))
    await olderLoad
    expect(graph.selectedBalanceRows.value[0]?.net).toBe('2.00')
    expect(graph.metricsError.value).toBeNull()
    expect(graph.metricsLoading.value).toBe(false)
  })

  it('blocks invalid thresholds and preserves a valid high-precision threshold', async () => {
    const threshold = ref('1.00000000000000001')
    const participants = ref<Participant[]>([{ pid: 'PID_A', display_name: 'Alice' }])
    const graph = useGraphAnalytics({
      isRealMode: computed(() => true),
      threshold,
      analyticsEq: computed(() => 'EUR'),
      precisionByEq: computed(() => new Map([['EUR', 2]])),
      availableEquivalents: computed(() => ['EUR']),
      participantByPid: computed(() => new Map(participants.value.map((participant) => [participant.pid, participant]))),
      participants,
      trustlines: ref<Trustline[]>([]),
      debts: ref<Debt[]>([]),
      incidents: ref<Incident[]>([]),
      auditLog: ref<AuditLogEntry[]>([]),
      transactions: ref<Transaction[]>([]),
      clearingCycles: ref<ClearingCycles | null>(null),
      selected: ref<SelectedInfo | null>({ kind: 'node', pid: 'PID_A', degree: 0, inDegree: 0, outDegree: 0 }),
    })

    await graph.loadSelectedMetrics()
    expect(apiMock.participantMetrics).not.toHaveBeenCalled()
    expect(graph.metricsError.value).toBeTruthy()

    apiMock.participantMetrics.mockResolvedValueOnce(metricsEnvelope('1.00'))
    threshold.value = '0.10000000000000001'
    await nextTick()
    await Promise.resolve()
    expect(apiMock.participantMetrics).toHaveBeenLastCalledWith('PID_A', {
      equivalent: 'EUR',
      threshold: '0.10000000000000001',
    })
    expect(graph.metricsError.value).toBeNull()
  })

  it('does not publish pending metrics after scope disposal', async () => {
    const pendingMetrics = deferred<ReturnType<typeof metricsEnvelope>>()
    apiMock.participantMetrics.mockReturnValueOnce(pendingMetrics.promise)
    const participants = ref<Participant[]>([{ pid: 'PID_A', display_name: 'Alice' }])
    const scope = effectScope()
    const graph = scope.run(() => useGraphAnalytics({
      isRealMode: computed(() => true),
      threshold: ref('0.10'),
      analyticsEq: computed(() => 'EUR'),
      precisionByEq: computed(() => new Map([['EUR', 2]])),
      availableEquivalents: computed(() => ['EUR']),
      participantByPid: computed(() => new Map(participants.value.map((participant) => [participant.pid, participant]))),
      participants,
      trustlines: ref<Trustline[]>([]),
      debts: ref<Debt[]>([]),
      incidents: ref<Incident[]>([]),
      auditLog: ref<AuditLogEntry[]>([]),
      transactions: ref<Transaction[]>([]),
      clearingCycles: ref<ClearingCycles | null>(null),
      selected: ref<SelectedInfo | null>({ kind: 'node', pid: 'PID_A', degree: 0, inDegree: 0, outDegree: 0 }),
    }))
    if (!graph) throw new Error('Expected graph analytics owner')

    const pending = graph.loadSelectedMetrics()
    scope.stop()
    pendingMetrics.resolve(metricsEnvelope('9.00'))
    await pending

    expect(graph.selectedBalanceRows.value[0]?.net).not.toBe('9.00')
    expect(graph.metricsError.value).toBeNull()
  })

  it('normalizes equivalent keys and preserves non-default precision in fixture analytics', () => {
    const participants = ref<Participant[]>([
      { pid: 'PID_A', display_name: 'Alice' },
      { pid: 'PID_B', display_name: 'Bob' },
    ])
    const graph = useGraphAnalytics({
      isRealMode: computed(() => false),
      threshold: ref('0.10'),
      analyticsEq: computed(() => ' eur '),
      precisionByEq: computed(() => new Map([['EUR', 4]])),
      availableEquivalents: computed(() => ['eur']),
      participantByPid: computed(() => new Map(participants.value.map((participant) => [participant.pid, participant]))),
      participants,
      trustlines: ref<Trustline[]>([]),
      debts: ref<Debt[]>([{ debtor: 'PID_A', creditor: 'PID_B', equivalent: 'eur', amount: '0.0001' }]),
      incidents: ref<Incident[]>([]),
      auditLog: ref<AuditLogEntry[]>([]),
      transactions: ref<Transaction[]>([]),
      clearingCycles: ref<ClearingCycles | null>(null),
      selected: ref<SelectedInfo | null>({ kind: 'node', pid: 'PID_A', degree: 0, inDegree: 0, outDegree: 0 }),
    })

    expect(graph.selectedBalanceRows.value).toEqual([
      expect.objectContaining({ equivalent: 'EUR', total_debt: '0.0001', net: '-0.0001' }),
    ])
    expect(graph.netDistribution.value?.min).toBe(-1n)
  })

  it('fails closed when equivalent precision is unavailable', () => {
    const participants = ref<Participant[]>([
      { pid: 'PID_A', display_name: 'Alice' },
      { pid: 'PID_B', display_name: 'Bob' },
    ])
    const graph = useGraphAnalytics({
      isRealMode: computed(() => false),
      threshold: ref('0.10'),
      analyticsEq: computed(() => 'EUR'),
      precisionByEq: computed(() => new Map()),
      availableEquivalents: computed(() => ['EUR']),
      participantByPid: computed(() => new Map(participants.value.map((participant) => [participant.pid, participant]))),
      participants,
      trustlines: ref<Trustline[]>([
        { from: 'PID_A', to: 'PID_B', equivalent: 'EUR', limit: '1.0000', used: '0.0001', available: '0.9999', status: 'active', created_at: 't' },
      ]),
      debts: ref<Debt[]>([{ debtor: 'PID_A', creditor: 'PID_B', equivalent: 'EUR', amount: '0.0001' }]),
      incidents: ref<Incident[]>([]),
      auditLog: ref<AuditLogEntry[]>([]),
      transactions: ref<Transaction[]>([]),
      clearingCycles: ref<ClearingCycles | null>(null),
      selected: ref<SelectedInfo | null>({ kind: 'node', pid: 'PID_A', degree: 0, inDegree: 0, outDegree: 0 }),
    })

    expect(graph.selectedBalanceRows.value).toEqual([])
    expect(graph.selectedCounterpartySplit.value.eq).toBeNull()
    expect(graph.netDistribution.value).toBeNull()
    expect(graph.selectedCapacity.value).toBeNull()
  })
})
