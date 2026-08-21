import { effectScope, nextTick, ref, type Ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/simulatorApi', () => ({
  getMetrics: vi.fn(),
  getBottlenecks: vi.fn(),
}))

import { ApiError } from '../api/http'
import { getBottlenecks, getMetrics } from '../api/simulatorApi'
import type { BottlenecksResponse, MetricsResponse, RunStatus } from '../api/simulatorTypes'
import {
  computeMetricsWindow,
  METRICS_POLL_INTERVAL_MS,
  METRICS_STEP_MS,
  METRICS_WINDOW_MS,
  useMetricsPolling,
} from './useMetricsPolling'

const getMetricsMock = vi.mocked(getMetrics)
const getBottlenecksMock = vi.mocked(getBottlenecks)

// ----------------------------------------------------------------
// Fixtures
// ----------------------------------------------------------------

function makeRunStatus(over: Partial<RunStatus> = {}): RunStatus {
  return {
    api_version: 'v1',
    run_id: 'run-1',
    scenario_id: 'sc-1',
    mode: 'real',
    state: 'running',
    sim_time_ms: 600_000,
    ...over,
  }
}

/**
 * A response with a real gap: the second point has no measurement, the third has a measured zero.
 * The two must never become the same thing.
 */
function makeMetrics(over: Partial<MetricsResponse> = {}): MetricsResponse {
  return {
    api_version: 'v1',
    run_id: 'run-1',
    equivalent: 'UAH',
    from_ms: 300_000,
    to_ms: 600_000,
    step_ms: 5_000,
    series: [
      {
        key: 'total_debt',
        unit: 'amount',
        points: [
          { t_ms: 300_000, v: '12500.00000000' },
          { t_ms: 305_000, v: null },
          { t_ms: 310_000, v: '0.00000000' },
        ],
      },
    ],
    ...over,
  }
}

function makeBottlenecks(over: Partial<BottlenecksResponse> = {}): BottlenecksResponse {
  return {
    api_version: 'v1',
    run_id: 'run-1',
    equivalent: 'UAH',
    items: [
      {
        target: { kind: 'edge', from: 'alice', to: 'bob' },
        score: 0.85,
        reason_code: 'FREQUENT_ABORTS',
        label: 'Alice -> Bob',
        suggested_action: 'Raise trust limit',
      },
    ],
    ...over,
  }
}

function apiError(status: number, bodyText?: string): ApiError {
  return new ApiError(`HTTP ${status} for /simulator/runs/run-1/metrics`, { status, bodyText })
}

const UNAVAILABLE_BODY = JSON.stringify({
  error: {
    code: 'E010',
    message: 'Analytics storage is unavailable',
    details: { run_id: 'run-1', equivalent: 'UAH', reason: 'storage_disabled' },
  },
})

// ----------------------------------------------------------------
// Harness
// ----------------------------------------------------------------

type Harness = {
  scope: ReturnType<typeof effectScope>
  api: ReturnType<typeof useMetricsPolling>
  runId: Ref<string | null>
  equivalent: Ref<string>
  runStatus: Ref<RunStatus | null>
}

function setup(over: { runId?: string | null; runStatus?: RunStatus | null } = {}): Harness {
  const apiBase = ref('/api/v1')
  const accessToken = ref<string | null>(null)
  const runId = ref<string | null>(over.runId === undefined ? 'run-1' : over.runId)
  const equivalent = ref('UAH')
  const runStatus = ref<RunStatus | null>(
    over.runStatus === undefined ? makeRunStatus() : over.runStatus,
  )

  const scope = effectScope()
  let api: ReturnType<typeof useMetricsPolling> | null = null
  scope.run(() => {
    api = useMetricsPolling({ apiBase, accessToken, runId, equivalent, runStatus })
  })
  if (api === null) throw new Error('composable did not initialise')

  return { scope, api, runId, equivalent, runStatus }
}

/** A promise whose settlement the test controls, so "still in flight" is an observable state. */
function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((r) => {
    resolve = r
  })
  return { promise, resolve }
}

/** Drains microtasks (and Vue's scheduler) without advancing the fake clock. */
async function flush(): Promise<void> {
  await vi.advanceTimersByTimeAsync(0)
  await nextTick()
  await vi.advanceTimersByTimeAsync(0)
}

// ----------------------------------------------------------------
// Tests
// ----------------------------------------------------------------

describe('useMetricsPolling', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    getMetricsMock.mockReset()
    getBottlenecksMock.mockReset()
    getMetricsMock.mockResolvedValue(makeMetrics())
    getBottlenecksMock.mockResolvedValue(makeBottlenecks())
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  // --- The three states, one test each ---------------------------

  it('reports data as ready when the backend answers with measurements', async () => {
    const h = setup()
    await flush()

    expect(h.api.phase.value).toBe('ready')
    expect(h.api.metricsPhase.value).toBe('ready')
    expect(h.api.bottlenecksPhase.value).toBe('ready')
    expect(h.api.metrics.value?.series[0]?.key).toBe('total_debt')
    expect(h.api.bottlenecks.value?.items).toHaveLength(1)
    expect(h.api.lastError.value).toBe('')
    expect(h.api.unavailableReason.value).toBe('')

    h.scope.stop()
  })

  it('reports HTTP 503 as "no measurements", never as a failure', async () => {
    getMetricsMock.mockRejectedValue(apiError(503, UNAVAILABLE_BODY))
    getBottlenecksMock.mockRejectedValue(apiError(503, UNAVAILABLE_BODY))

    const h = setup()
    await flush()

    // The point of the whole backend honesty block: 503 means the backend refused to invent
    // measurements. If this collapsed into 'error', that refusal would read as a malfunction.
    expect(h.api.phase.value).toBe('unavailable')
    expect(h.api.phase.value).not.toBe('error')
    expect(h.api.metricsPhase.value).toBe('unavailable')
    expect(h.api.bottlenecksPhase.value).toBe('unavailable')
    expect(h.api.lastError.value).toBe('')
    expect(h.api.unavailableReason.value).toBe('storage_disabled')
    expect(h.api.metrics.value).toBeNull()

    h.scope.stop()
  })

  it('reports any other failure as an error, with a message', async () => {
    getMetricsMock.mockRejectedValue(apiError(500, '{"error":{"code":"E010"}}'))
    getBottlenecksMock.mockRejectedValue(apiError(500, '{"error":{"code":"E010"}}'))

    const h = setup()
    await flush()

    expect(h.api.phase.value).toBe('error')
    expect(h.api.metricsPhase.value).toBe('error')
    expect(h.api.lastError.value).toContain('HTTP 500')
    expect(h.api.unavailableReason.value).toBe('')

    h.scope.stop()
  })

  it('keeps a non-ApiError rejection (network / decoder) in the error state', async () => {
    getMetricsMock.mockRejectedValue(new Error('metrics: $.series[0].points[1].v: expected string'))

    const h = setup()
    await flush()

    expect(h.api.metricsPhase.value).toBe('error')
    expect(h.api.phase.value).toBe('error')
    expect(h.api.lastError.value).toContain('expected string')

    h.scope.stop()
  })

  it('does not let one stream unavailable turn the other stream into an error', async () => {
    getMetricsMock.mockRejectedValue(apiError(503, UNAVAILABLE_BODY))

    const h = setup()
    await flush()

    expect(h.api.metricsPhase.value).toBe('unavailable')
    expect(h.api.bottlenecksPhase.value).toBe('ready')
    expect(h.api.bottlenecks.value?.items).toHaveLength(1)
    expect(h.api.lastError.value).toBe('')

    h.scope.stop()
  })

  // --- null stays a gap ------------------------------------------

  it('passes a null measurement through as a gap: not zero, not dropped', async () => {
    const h = setup()
    await flush()

    const points = h.api.metrics.value?.series[0]?.points
    expect(points).toHaveLength(3)
    expect(points?.[1]?.v).toBeNull()
    // A measured zero is a different statement and must stay distinguishable from the gap.
    expect(points?.[2]?.v).toBe('0.00000000')
    expect(points?.[1]?.t_ms).toBe(305_000)
    // Money never becomes a JS number on the way through.
    expect(typeof points?.[0]?.v).toBe('string')

    h.scope.stop()
  })

  // --- window computation ----------------------------------------

  it('asks for a window on the run clock, ending at sim_time_ms', async () => {
    const h = setup()
    await flush()

    expect(getMetricsMock).toHaveBeenCalledWith(
      { apiBase: '/api/v1', accessToken: null },
      'run-1',
      'UAH',
      { from_ms: 300_000, to_ms: 600_000, step_ms: METRICS_STEP_MS },
    )
    expect(h.api.window.value).toEqual({
      from_ms: 300_000,
      to_ms: 600_000,
      step_ms: METRICS_STEP_MS,
    })

    h.scope.stop()
  })

  it('clamps from_ms at zero for a young run and never goes negative', () => {
    expect(computeMetricsWindow(1_000)).toEqual({
      from_ms: 0,
      to_ms: 1_000,
      step_ms: METRICS_STEP_MS,
    })
    expect(computeMetricsWindow(0)).toEqual({ from_ms: 0, to_ms: 0, step_ms: METRICS_STEP_MS })
    expect(computeMetricsWindow(null)).toEqual({ from_ms: 0, to_ms: 0, step_ms: METRICS_STEP_MS })
    expect(computeMetricsWindow(undefined)).toEqual({
      from_ms: 0,
      to_ms: 0,
      step_ms: METRICS_STEP_MS,
    })
  })

  it('stays under the server-side 2000-point ceiling', () => {
    expect(METRICS_WINDOW_MS / METRICS_STEP_MS + 1).toBeLessThanOrEqual(2000)
  })

  it('re-computes the window from the advancing run clock on every poll', async () => {
    const h = setup({ runStatus: makeRunStatus({ sim_time_ms: 400_000 }) })
    await flush()
    expect(getMetricsMock.mock.calls[0]?.[3]).toEqual({
      from_ms: 100_000,
      to_ms: 400_000,
      step_ms: METRICS_STEP_MS,
    })

    h.runStatus.value = makeRunStatus({ sim_time_ms: 405_000 })
    await vi.advanceTimersByTimeAsync(METRICS_POLL_INTERVAL_MS)
    await flush()

    expect(getMetricsMock.mock.calls[1]?.[3]).toEqual({
      from_ms: 105_000,
      to_ms: 405_000,
      step_ms: METRICS_STEP_MS,
    })

    h.scope.stop()
  })

  // --- cadence ---------------------------------------------------

  // Literal 5000, not the exported constant: the cadence is the contract the spec states
  // ("раз в 5 секунд"), so a test written against the constant would follow the constant wherever
  // it drifted and could never catch the drift.
  it('polls once immediately and then once per 5 seconds', async () => {
    const h = setup()
    await flush()
    expect(getMetricsMock).toHaveBeenCalledTimes(1)
    expect(getBottlenecksMock).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(4_999)
    await flush()
    expect(getMetricsMock).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(1)
    await flush()
    expect(getMetricsMock).toHaveBeenCalledTimes(2)

    await vi.advanceTimersByTimeAsync(15_000)
    await flush()
    expect(getMetricsMock).toHaveBeenCalledTimes(5)

    expect(METRICS_POLL_INTERVAL_MS).toBe(5_000)

    h.scope.stop()
  })

  it('does not queue polls on top of a request that has not answered yet', async () => {
    const first = deferred<MetricsResponse>()
    getMetricsMock.mockImplementationOnce(() => first.promise)

    const h = setup()
    await flush()
    expect(getMetricsMock).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(METRICS_POLL_INTERVAL_MS * 4)
    await flush()
    expect(getMetricsMock).toHaveBeenCalledTimes(1)

    first.resolve(makeMetrics())
    await flush()
    await vi.advanceTimersByTimeAsync(METRICS_POLL_INTERVAL_MS)
    await flush()
    expect(getMetricsMock).toHaveBeenCalledTimes(2)

    h.scope.stop()
  })

  // --- the gate: when to start and when to stop -------------------

  it('never polls while the gate is closed', async () => {
    const h = setup({ runStatus: makeRunStatus({ state: 'created' }) })
    await flush()
    await vi.advanceTimersByTimeAsync(METRICS_POLL_INTERVAL_MS * 5)
    await flush()

    expect(getMetricsMock).not.toHaveBeenCalled()
    expect(h.api.phase.value).toBe('idle')
    expect(h.api.isPolling.value).toBe(false)

    h.scope.stop()
  })

  it('never polls without a run id', async () => {
    const h = setup({ runId: null, runStatus: null })
    await flush()
    await vi.advanceTimersByTimeAsync(METRICS_POLL_INTERVAL_MS * 5)
    await flush()

    expect(getMetricsMock).not.toHaveBeenCalled()
    expect(h.api.window.value).toBeNull()

    h.scope.stop()
  })

  it.each(['paused', 'stopping', 'stopped', 'error'] as const)(
    'stops polling when the run leaves running for %s',
    async (state) => {
      const h = setup()
      await flush()
      expect(getMetricsMock).toHaveBeenCalledTimes(1)

      h.runStatus.value = makeRunStatus({ state })
      await flush()

      const callsAtGateClose = getMetricsMock.mock.calls.length
      await vi.advanceTimersByTimeAsync(METRICS_POLL_INTERVAL_MS * 10)
      await flush()

      expect(getMetricsMock).toHaveBeenCalledTimes(callsAtGateClose)
      expect(h.api.isPolling.value).toBe(false)
      // No orphan interval survives the gate closing.
      expect(vi.getTimerCount()).toBe(0)

      h.scope.stop()
    },
  )

  it('resumes polling when the run goes back to running', async () => {
    const h = setup()
    await flush()
    h.runStatus.value = makeRunStatus({ state: 'paused' })
    await flush()
    const paused = getMetricsMock.mock.calls.length

    h.runStatus.value = makeRunStatus({ state: 'running' })
    await flush()
    expect(getMetricsMock.mock.calls.length).toBe(paused + 1)

    await vi.advanceTimersByTimeAsync(METRICS_POLL_INTERVAL_MS)
    await flush()
    expect(getMetricsMock.mock.calls.length).toBe(paused + 2)

    h.scope.stop()
  })

  it('drops data of the previous run when the run id changes', async () => {
    const h = setup()
    await flush()
    expect(h.api.metrics.value).not.toBeNull()

    h.runId.value = 'run-2'
    await nextTick()
    // Before the new answer lands there is nothing to show, and nothing stale is shown.
    expect(h.api.metrics.value).toBeNull()
    expect(h.api.phase.value).not.toBe('ready')

    await flush()
    expect(getMetricsMock).toHaveBeenLastCalledWith(
      expect.anything(),
      'run-2',
      'UAH',
      expect.anything(),
    )

    h.scope.stop()
  })

  it('drops an in-flight answer that belongs to the previous run', async () => {
    const stale = deferred<MetricsResponse>()
    getMetricsMock.mockImplementationOnce(() => stale.promise)

    const h = setup()
    await flush()

    h.runId.value = 'run-2'
    await nextTick()
    stale.resolve(makeMetrics({ run_id: 'run-1' }))
    await flush()

    expect(h.api.metrics.value?.run_id).not.toBe('run-1')

    h.scope.stop()
  })

  it('refetches when the equivalent changes', async () => {
    const h = setup()
    await flush()

    h.equivalent.value = 'USD'
    await flush()

    expect(getMetricsMock).toHaveBeenLastCalledWith(
      expect.anything(),
      'run-1',
      'USD',
      expect.anything(),
    )

    h.scope.stop()
  })

  // --- teardown ---------------------------------------------------

  it('clears the interval when the owning scope is stopped', async () => {
    const h = setup()
    await flush()
    expect(getMetricsMock).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBeGreaterThan(0)

    h.scope.stop()

    // The timer itself is gone, not merely inert: a leaked interval would keep firing forever.
    expect(vi.getTimerCount()).toBe(0)

    await vi.advanceTimersByTimeAsync(METRICS_POLL_INTERVAL_MS * 20)
    await flush()
    expect(getMetricsMock).toHaveBeenCalledTimes(1)
  })

  it('clears the interval on an explicit dispose', async () => {
    const h = setup()
    await flush()
    // Read before tearing down: a non-reactive disposed flag would leave this computed cached
    // at `true` afterwards, and the assertion below would pass only by never having been read.
    expect(h.api.isPolling.value).toBe(true)

    h.api.dispose()
    expect(vi.getTimerCount()).toBe(0)

    await vi.advanceTimersByTimeAsync(METRICS_POLL_INTERVAL_MS * 20)
    await flush()
    expect(getMetricsMock).toHaveBeenCalledTimes(1)
    expect(h.api.isPolling.value).toBe(false)

    h.scope.stop()
  })

  it('does not write into a disposed surface when a late answer arrives', async () => {
    const late = deferred<MetricsResponse>()
    getMetricsMock.mockImplementationOnce(() => late.promise)

    const h = setup()
    await flush()
    h.scope.stop()

    late.resolve(makeMetrics())
    await flush()

    expect(h.api.metrics.value).toBeNull()
    expect(h.api.metricsPhase.value).not.toBe('ready')
  })
})
