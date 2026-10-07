import { beforeEach, describe, expect, it, vi } from 'vitest'
import { computed, effectScope, nextTick, ref } from 'vue'

const apiMock = vi.hoisted(() => ({
  participantMetrics: vi.fn(),
}))

vi.mock('../api', () => ({ api: apiMock }))

import { useGraphAnalytics } from './useGraphAnalytics'
import type { SelectedInfo } from './useGraphVisualization'
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

function metricsEnvelope(net: string, equivalent = 'EUR') {
  const metrics: ParticipantMetrics = {
    pid: 'PID_A',
    equivalent,
    balance_rows: [
      {
        equivalent,
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
  return metrics
}

function node(pid: string): SelectedInfo {
  return { kind: 'node', pid, degree: 0, inDegree: 0, outDegree: 0 }
}

async function flush() {
  await nextTick()
  await Promise.resolve()
  await Promise.resolve()
}

describe('useGraphAnalytics (032 S5: balance rows from the server only)', () => {
  beforeEach(() => vi.resetAllMocks())

  it('asks /metrics by pid and equivalent only, and caches the answer under that pair', async () => {
    apiMock.participantMetrics
      .mockResolvedValueOnce(metricsEnvelope('1.00'))
      .mockResolvedValueOnce(metricsEnvelope('5.0', 'HOUR'))
    const eq = ref<string | null>('EUR')
    const selected = ref<SelectedInfo | null>(node('PID_A'))
    const graph = useGraphAnalytics({ analyticsEq: computed(() => eq.value), selected })

    await graph.loadSelectedMetrics()
    expect(apiMock.participantMetrics).toHaveBeenCalledTimes(1)
    expect(apiMock.participantMetrics).toHaveBeenLastCalledWith('PID_A', { equivalent: 'EUR' })
    expect(graph.selectedBalanceRows.value.map((row) => row.net)).toEqual(['1.00'])

    // Same pair again: served from the cache, no second request.
    await graph.loadSelectedMetrics()
    expect(apiMock.participantMetrics).toHaveBeenCalledTimes(1)

    // Another equivalent is another answer; the EUR rows are not shown under it.
    eq.value = 'hour'
    await flush()
    expect(apiMock.participantMetrics).toHaveBeenCalledTimes(2)
    expect(apiMock.participantMetrics).toHaveBeenLastCalledWith('PID_A', { equivalent: 'HOUR' })
    expect(graph.selectedBalanceRows.value.map((row) => [row.equivalent, row.net])).toEqual([['HOUR', '5.0']])
  })

  it('passes no equivalent when ALL is selected and keeps every equivalent row as its own line', async () => {
    const all: ParticipantMetrics = {
      pid: 'PID_A',
      equivalent: null,
      balance_rows: [metricsEnvelope('-1.50').balance_rows[0]!, metricsEnvelope('3.0', 'HOUR').balance_rows[0]!],
    }
    apiMock.participantMetrics.mockResolvedValueOnce(all)
    const graph = useGraphAnalytics({ analyticsEq: computed(() => null), selected: ref(node('PID_A')) })

    await graph.loadSelectedMetrics()
    expect(apiMock.participantMetrics).toHaveBeenCalledWith('PID_A', { equivalent: null })
    expect(graph.selectedBalanceRows.value.map((row) => [row.equivalent, row.net])).toEqual([
      ['EUR', '-1.50'],
      ['HOUR', '3.0'],
    ])
  })

  it('shows loading and then the error, never rows computed in their place', async () => {
    const pending = deferred<ParticipantMetrics>()
    apiMock.participantMetrics.mockReturnValueOnce(pending.promise)
    const graph = useGraphAnalytics({ analyticsEq: computed(() => 'EUR'), selected: ref(node('PID_A')) })

    const load = graph.loadSelectedMetrics()
    expect(graph.metricsLoading.value).toBe(true)
    expect(graph.selectedBalanceRows.value).toEqual([])

    pending.reject(new Error('metrics unavailable'))
    await load
    expect(graph.metricsLoading.value).toBe(false)
    expect(graph.metricsError.value).toBe('metrics unavailable')
    expect(graph.selectedBalanceRows.value).toEqual([])
  })

  it('does not request metrics without a selected node', async () => {
    const graph = useGraphAnalytics({ analyticsEq: computed(() => 'EUR'), selected: ref<SelectedInfo | null>(null) })

    await graph.loadSelectedMetrics()
    expect(apiMock.participantMetrics).not.toHaveBeenCalled()
    expect(graph.selectedBalanceRows.value).toEqual([])
    expect(graph.metricsLoading.value).toBe(false)
  })

  it('does not let an older metrics rejection replace the latest metrics state', async () => {
    const older = deferred<ReturnType<typeof metricsEnvelope>>()
    const latest = deferred<ReturnType<typeof metricsEnvelope>>()
    apiMock.participantMetrics.mockReturnValueOnce(older.promise).mockReturnValueOnce(latest.promise)

    const graph = useGraphAnalytics({ analyticsEq: computed(() => 'EUR'), selected: ref(node('PID_A')) })

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

  it('does not publish pending metrics after scope disposal', async () => {
    const pendingMetrics = deferred<ReturnType<typeof metricsEnvelope>>()
    apiMock.participantMetrics.mockReturnValueOnce(pendingMetrics.promise)
    const scope = effectScope()
    const graph = scope.run(() => useGraphAnalytics({ analyticsEq: computed(() => 'EUR'), selected: ref(node('PID_A')) }))
    if (!graph) throw new Error('Expected graph analytics owner')

    const pending = graph.loadSelectedMetrics()
    scope.stop()
    pendingMetrics.resolve(metricsEnvelope('9.00'))
    await pending

    expect(graph.selectedBalanceRows.value[0]?.net).not.toBe('9.00')
    expect(graph.metricsError.value).toBeNull()
  })
})
