import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
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
  UNAVAILABLE_REASONS,
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
  /** The surface gate (spec 007, T705): is anything actually showing this data? */
  enabled: Ref<boolean>
}

function setup(
  over: { runId?: string | null; runStatus?: RunStatus | null; enabled?: boolean } = {},
): Harness {
  const apiBase = ref('/api/v1')
  const accessToken = ref<string | null>(null)
  const runId = ref<string | null>(over.runId === undefined ? 'run-1' : over.runId)
  const equivalent = ref('UAH')
  const runStatus = ref<RunStatus | null>(
    over.runStatus === undefined ? makeRunStatus() : over.runStatus,
  )
  const enabled = ref(over.enabled ?? true)

  const scope = effectScope()
  let api: ReturnType<typeof useMetricsPolling> | null = null
  scope.run(() => {
    api = useMetricsPolling({ apiBase, accessToken, runId, equivalent, runStatus, enabled })
  })
  if (api === null) throw new Error('composable did not initialise')

  return { scope, api, runId, equivalent, runStatus, enabled }
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

    expect(h.api.metricsPhase.value).toBe('ready')
    expect(h.api.bottlenecksPhase.value).toBe('ready')
    expect(h.api.metrics.value?.series[0]?.key).toBe('total_debt')
    expect(h.api.bottlenecks.value?.items).toHaveLength(1)
    expect(h.api.metricsError.value).toBe('')
    expect(h.api.bottlenecksError.value).toBe('')
    expect(h.api.metricsUnavailableReason.value).toBe('')
    expect(h.api.bottlenecksUnavailableReason.value).toBe('')

    h.scope.stop()
  })

  it('reports HTTP 503 as "no measurements", never as a failure', async () => {
    getMetricsMock.mockRejectedValue(apiError(503, UNAVAILABLE_BODY))
    getBottlenecksMock.mockRejectedValue(apiError(503, UNAVAILABLE_BODY))

    const h = setup()
    await flush()

    // The point of the whole backend honesty block: 503 means the backend refused to invent
    // measurements. If this collapsed into 'error', that refusal would read as a malfunction.
    expect(h.api.metricsPhase.value).toBe('unavailable')
    expect(h.api.bottlenecksPhase.value).toBe('unavailable')
    expect(h.api.metricsPhase.value).not.toBe('error')
    expect(h.api.bottlenecksPhase.value).not.toBe('error')
    expect(h.api.metricsError.value).toBe('')
    expect(h.api.bottlenecksError.value).toBe('')
    expect(h.api.metricsUnavailableReason.value).toBe('storage_disabled')
    expect(h.api.bottlenecksUnavailableReason.value).toBe('storage_disabled')
    expect(h.api.metrics.value).toBeNull()

    h.scope.stop()
  })

  /**
   * The third defect of the same family as the two already closed: the panel stating something
   * about the user's data that it has no evidence for.
   *
   * "No measurements recorded / Nothing is being substituted for them" is an assertion ABOUT THE
   * RUN, and only this application can make it — it is the one party that looked at the run's
   * tables. A 503 with no body is what a reverse proxy, an ingress or a load balancer answers when
   * the request never reached the application at all; the run may hold thousands of measurements
   * at that moment. Reading that as "there are none" is a confident falsehood, so it is a failure.
   */
  it('reports a 503 with no body as a failure, never as "no measurements"', async () => {
    getMetricsMock.mockRejectedValue(apiError(503))
    getBottlenecksMock.mockRejectedValue(apiError(503))

    const h = setup()
    await flush()

    expect(h.api.metricsPhase.value).toBe('error')
    expect(h.api.bottlenecksPhase.value).toBe('error')
    expect(h.api.metricsPhase.value).not.toBe('unavailable')
    expect(h.api.bottlenecksPhase.value).not.toBe('unavailable')
    expect(h.api.metricsError.value).toContain('HTTP 503')
    expect(h.api.bottlenecksError.value).toContain('HTTP 503')
    expect(h.api.metricsUnavailableReason.value).toBe('')
    expect(h.api.bottlenecksUnavailableReason.value).toBe('')

    h.scope.stop()
  })

  /**
   * Same rule, from the side where a body exists but says something this build cannot read: a
   * reason token from a newer backend, or a 503 body minted by something that is not this
   * application. An unknown word is not evidence either, and §9 forbids leaning on a later stage
   * to correct a claim made here — the phase itself has to be right.
   */
  it('reports a 503 whose reason is not one the backend can raise as a failure', async () => {
    getMetricsMock.mockRejectedValue(
      apiError(503, JSON.stringify({ error: { details: { reason: 'upstream_connect_error' } } })),
    )
    getBottlenecksMock.mockRejectedValue(
      apiError(503, JSON.stringify({ error: { details: { reason: '' } } })),
    )

    const h = setup()
    await flush()

    expect(h.api.metricsPhase.value).toBe('error')
    expect(h.api.bottlenecksPhase.value).toBe('error')
    expect(h.api.metricsUnavailableReason.value).toBe('')
    expect(h.api.bottlenecksUnavailableReason.value).toBe('')
    // The unreadable token is not shown as a reason for anything, under any phase.
    expect(h.api.metricsError.value).not.toContain('upstream_connect_error')

    h.scope.stop()
  })

  /**
   * The set is the backend's, not a list invented here: `REASON_MESSAGES` in
   * `app/core/simulator/metrics_bottlenecks.py:35-45` is exactly what `_real_mode_unavailable`
   * can carry, and each of its members has to reach `unavailable` on its own.
   */
  it.each([...UNAVAILABLE_REASONS])('accepts %s — the reasons the backend actually raises', async (token) => {
    getMetricsMock.mockRejectedValue(
      apiError(503, JSON.stringify({ error: { details: { reason: token } } })),
    )

    const h = setup()
    await flush()

    expect(h.api.metricsPhase.value).toBe('unavailable')
    expect(h.api.metricsUnavailableReason.value).toBe(token)
    expect(h.api.metricsError.value).toBe('')

    h.scope.stop()
  })

  it('reports any other failure as an error, with a message', async () => {
    getMetricsMock.mockRejectedValue(apiError(500, '{"error":{"code":"E010"}}'))
    getBottlenecksMock.mockRejectedValue(apiError(500, '{"error":{"code":"E010"}}'))

    const h = setup()
    await flush()

    expect(h.api.metricsPhase.value).toBe('error')
    expect(h.api.bottlenecksPhase.value).toBe('error')
    expect(h.api.metricsError.value).toContain('HTTP 500')
    expect(h.api.bottlenecksError.value).toContain('HTTP 500')
    expect(h.api.metricsUnavailableReason.value).toBe('')

    h.scope.stop()
  })

  it('keeps a non-ApiError rejection (network / decoder) in the error state', async () => {
    getMetricsMock.mockRejectedValue(new Error('metrics: $.series[0].points[1].v: expected string'))

    const h = setup()
    await flush()

    expect(h.api.metricsPhase.value).toBe('error')
    expect(h.api.metricsError.value).toContain('expected string')
    // The decoder blew up on `/metrics`; `/bottlenecks` answered and is untouched by it.
    expect(h.api.bottlenecksPhase.value).toBe('ready')
    expect(h.api.bottlenecksError.value).toBe('')

    h.scope.stop()
  })

  it('does not let one stream unavailable turn the other stream into an error', async () => {
    getMetricsMock.mockRejectedValue(apiError(503, UNAVAILABLE_BODY))

    const h = setup()
    await flush()

    expect(h.api.metricsPhase.value).toBe('unavailable')
    expect(h.api.bottlenecksPhase.value).toBe('ready')
    expect(h.api.bottlenecks.value?.items).toHaveLength(1)
    expect(h.api.metricsError.value).toBe('')
    expect(h.api.bottlenecksError.value).toBe('')

    h.scope.stop()
  })

  /**
   * The pair that used to be collapsed into one word, from the other side.
   *
   * `/metrics` and `/bottlenecks` read the run's tables in two separate database sessions
   * (`metrics_bottlenecks.py:250`, `:422`), so `db_read_failed` on one and a 500 on the other is a
   * single ordinary poll. Neither the phases nor the two detail strings may leak across: a reason
   * token shown under the other stream's failure is the same lie in smaller print.
   */
  it('keeps phase, error message and reason token separate per stream', async () => {
    getMetricsMock.mockRejectedValue(
      apiError(
        503,
        JSON.stringify({ error: { details: { reason: 'db_read_failed' } } }),
      ),
    )
    getBottlenecksMock.mockRejectedValue(apiError(500, '{"error":{"code":"E010"}}'))

    const h = setup()
    await flush()

    expect(h.api.metricsPhase.value).toBe('unavailable')
    expect(h.api.metricsUnavailableReason.value).toBe('db_read_failed')
    expect(h.api.metricsError.value).toBe('')

    expect(h.api.bottlenecksPhase.value).toBe('error')
    expect(h.api.bottlenecksError.value).toContain('HTTP 500')
    expect(h.api.bottlenecksUnavailableReason.value).toBe('')

    h.scope.stop()
  })

  it('keeps the ready stream ready while the other one is unavailable', async () => {
    getBottlenecksMock.mockRejectedValue(apiError(503, UNAVAILABLE_BODY))

    const h = setup()
    await flush()

    // The composable holds a decoded measurement AND an unavailable bottlenecks stream at once.
    // There is no single word for this state, which is why there is no single word.
    expect(h.api.metricsPhase.value).toBe('ready')
    expect(h.api.metrics.value?.series[0]?.points[0]?.v).toBe('12500.00000000')
    expect(h.api.bottlenecksPhase.value).toBe('unavailable')
    expect(h.api.bottlenecksUnavailableReason.value).toBe('storage_disabled')
    expect(h.api.metricsUnavailableReason.value).toBe('')

    h.scope.stop()
  })

  // --- loading is a state the caller can see -----------------------

  it('says loading from the moment the first request leaves until it is answered', async () => {
    const pendingMetrics = deferred<MetricsResponse>()
    const pendingBottlenecks = deferred<BottlenecksResponse>()
    getMetricsMock.mockImplementationOnce(() => pendingMetrics.promise)
    getBottlenecksMock.mockImplementationOnce(() => pendingBottlenecks.promise)

    const h = setup()
    await flush()

    // Not `idle`: `idle` means the gate is closed and nothing was asked, which would be a false
    // statement while two requests are on the wire.
    expect(getMetricsMock).toHaveBeenCalledTimes(1)
    expect(h.api.metricsPhase.value).toBe('loading')
    expect(h.api.bottlenecksPhase.value).toBe('loading')

    pendingMetrics.resolve(makeMetrics())
    pendingBottlenecks.resolve(makeBottlenecks())
    await flush()

    expect(h.api.metricsPhase.value).toBe('ready')
    expect(h.api.bottlenecksPhase.value).toBe('ready')

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

  /**
   * "One pair at a time" is per subject, not global.
   *
   * The in-flight guard used to sit before the run id and equivalent were read, so a run change
   * during a slow request was swallowed: the identity watcher had already reset both streams to
   * `idle`, the immediate refetch returned at the guard, and the panel sat on "Analytics updates
   * while a run is running" — during a running run — until the next tick of the 5s interval.
   */
  it.each([
    [
      'the run changes',
      (h: Harness): void => {
        h.runId.value = 'run-2'
      },
    ],
    [
      'the equivalent changes',
      (h: Harness): void => {
        h.equivalent.value = 'USD'
      },
    ],
  ] as Array<[string, (h: Harness) => void]>)(
    'refetches at once when %s mid-request, instead of waiting out the interval',
    async (_label, change) => {
      const slow = deferred<MetricsResponse>()
      getMetricsMock.mockImplementationOnce(() => slow.promise)

      const h = setup()
      await flush()
      expect(getMetricsMock).toHaveBeenCalledTimes(1)

      change(h)
      await flush()

      // No clock was advanced: this is the immediate refetch, not the next interval.
      expect(getMetricsMock).toHaveBeenCalledTimes(2)
      expect(h.api.metricsPhase.value).not.toBe('idle')

      // ...and the superseded pair still cannot write into the new subject.
      slow.resolve(makeMetrics({ run_id: 'run-1', equivalent: 'STALE' }))
      await flush()
      expect(h.api.metrics.value).not.toBeNull()
      expect(h.api.metrics.value?.equivalent).not.toBe('STALE')

      h.scope.stop()
    },
  )

  it('does not let a superseded pair release the slot the current pair is holding', async () => {
    const first = deferred<MetricsResponse>()
    const second = deferred<MetricsResponse>()
    getMetricsMock
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise)

    const h = setup()
    await flush()
    h.runId.value = 'run-2'
    await flush()
    expect(getMetricsMock).toHaveBeenCalledTimes(2)

    // The superseded run-1 pair settles while the run-2 pair is still on the wire.
    first.resolve(makeMetrics({ run_id: 'run-1' }))
    await flush()

    await h.api.poll()
    // Still one pair in flight for run-2, so no third request was allowed.
    expect(getMetricsMock).toHaveBeenCalledTimes(2)

    second.resolve(makeMetrics({ run_id: 'run-2' }))
    await flush()
    expect(h.api.metrics.value?.run_id).toBe('run-2')

    h.scope.stop()
  })

  // --- the gate: when to start and when to stop -------------------

  it('never polls while the gate is closed', async () => {
    // `idle`, not `created`. `RunState` still lists `created`, but the decoder's canonical set
    // (`simulatorContracts.ts` `RUN_STATES`) does not accept it, so no response can carry it: a
    // fixture built on it tests a state the gate will never actually be asked about. `idle` is
    // the one canonical non-running state the `it.each` below does not already cover.
    const h = setup({ runStatus: makeRunStatus({ state: 'idle' }) })
    await flush()
    await vi.advanceTimersByTimeAsync(METRICS_POLL_INTERVAL_MS * 5)
    await flush()

    expect(getMetricsMock).not.toHaveBeenCalled()
    expect(h.api.metricsPhase.value).toBe('idle')
    expect(h.api.bottlenecksPhase.value).toBe('idle')
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
    expect(h.api.metricsPhase.value).not.toBe('ready')

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
    // Marked, not merely "the previous run id": the fixture's `run_id` is the same string the
    // fresh answer carries, so an assertion on `run_id` alone cannot tell the two apart.
    stale.resolve(makeMetrics({ run_id: 'run-1', equivalent: 'STALE' }))
    await flush()

    expect(h.api.metrics.value?.equivalent).not.toBe('STALE')

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

  // --- The surface gate (spec 007, T705) -------------------------
  //
  // A run can be `running` and still have nobody looking at it. Every poll costs two requests, and
  // both of them make the backend read the run's metric tables, so an invisible panel that keeps
  // polling is spending someone else's database.

  it('does not poll at all while the surface is hidden, however healthy the run is', async () => {
    const h = setup({ enabled: false })
    await flush()

    expect(getMetricsMock).not.toHaveBeenCalled()
    expect(getBottlenecksMock).not.toHaveBeenCalled()
    expect(h.api.isPolling.value).toBe(false)
    expect(vi.getTimerCount()).toBe(0)

    // Not merely "late": twenty intervals of a running run produce nothing.
    await vi.advanceTimersByTimeAsync(METRICS_POLL_INTERVAL_MS * 20)
    await flush()
    expect(getMetricsMock).not.toHaveBeenCalled()
    expect(getBottlenecksMock).not.toHaveBeenCalled()
    expect(h.api.metricsPhase.value).toBe('idle')
    expect(h.api.bottlenecksPhase.value).toBe('idle')

    h.scope.stop()
  })

  it('stops polling when the surface is hidden and resumes when it comes back', async () => {
    const h = setup()
    await flush()
    expect(getMetricsMock).toHaveBeenCalledTimes(1)

    h.enabled.value = false
    await flush()
    expect(h.api.isPolling.value).toBe(false)
    expect(vi.getTimerCount()).toBe(0)

    await vi.advanceTimersByTimeAsync(METRICS_POLL_INTERVAL_MS * 5)
    await flush()
    // Not one request while hidden.
    expect(getMetricsMock).toHaveBeenCalledTimes(1)

    h.enabled.value = true
    await flush()
    // Reopening refreshes immediately rather than making the user wait out an interval.
    expect(getMetricsMock).toHaveBeenCalledTimes(2)
    expect(h.api.isPolling.value).toBe(true)

    await vi.advanceTimersByTimeAsync(METRICS_POLL_INTERVAL_MS)
    await flush()
    expect(getMetricsMock).toHaveBeenCalledTimes(3)

    h.scope.stop()
  })

  it('keeps the last answer while hidden instead of blanking the panel', async () => {
    const h = setup()
    await flush()
    expect(h.api.metricsPhase.value).toBe('ready')

    h.enabled.value = false
    await flush()

    // Hiding is not a run change: nothing was invalidated, so nothing is thrown away. Re-opening
    // shows the last measurements immediately, with a fresh poll already on its way.
    expect(h.api.metrics.value?.series[0]?.key).toBe('total_debt')
    expect(h.api.metricsPhase.value).toBe('ready')

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

// ---------------------------------------------------------------------------
// Wiring guard: does the app actually feed this composable?
// ---------------------------------------------------------------------------

/**
 * The second position of a blind spot found while wiring T705, worth naming precisely.
 *
 * `useSimulatorApp` is instantiated exactly once, inside `SimulatorAppRoot`, and the only test
 * that mounts that component mocks the composable away. Nothing in this suite ever runs its body.
 * Measured, not assumed: deleting `getLayoutLinks` from its `useAppViewWiring` call left all 844
 * tests green and `vue-tsc` silent.
 *
 * The dependencies below sit in the same position and fail the same way. Every one of them is
 * tolerant by design — a ref that may read empty, a getter that may be absent — so dropping one
 * does not fail to compile. It makes `shouldPoll` false forever: the panel sits on `idle`, no
 * request is ever made, nothing is logged, and every test above still passes. A user sees an
 * analytics panel that shows nothing and reports nothing wrong.
 *
 * This reads source text, which is the weakest kind of test, and that is deliberate. Until
 * something can instantiate the real composable — separate and much larger work — a source
 * assertion is the only thing standing between that regression and a green build.
 */
function useSimulatorAppSource(): string {
  // `new URL(rel, import.meta.url)` is unusable here: under happy-dom the global `URL` resolves
  // against the document origin and hands back an http: URL.
  const here = dirname(fileURLToPath(import.meta.url))
  return readFileSync(resolve(here, './useSimulatorApp.ts'), 'utf8')
}

/**
 * The options literal of `callee(` … `)`, with comments removed.
 *
 * Stripping comments is not tidiness. The first version of the companion guard passed on the very
 * deletion it existed to catch, because it matched the dependency's name inside a comment that
 * described it. Prose about a dependency is not the dependency.
 */
function optionsBlockOf(source: string, callee: string): string {
  const start = source.indexOf(`${callee}({`)
  expect(start).toBeGreaterThan(-1)

  const end = source.indexOf('\n  })', start)
  expect(end).toBeGreaterThan(start)

  return source
    .slice(start, end)
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .filter((line) => !line.trim().startsWith('//'))
    .join('\n')
}

describe('useMetricsPolling — the app really feeds it (spec 007, T705)', () => {
  it('passes every gate dependency, each from its live source', () => {
    const block = optionsBlockOf(useSimulatorAppSource(), 'useMetricsPolling')

    // Counter-probes: the slice is the right call, and it is not empty or over-long.
    expect(block.startsWith('useMetricsPolling({')).toBe(true)
    expect(block).not.toContain('useAppViewWiring')
    // And the comment stripper works, so no assertion below can be satisfied by prose.
    expect(block).not.toContain('GET /metrics')

    // Credentials and target. Read from `real` live: captured values would pin the stream to
    // whatever the app happened to hold at setup, across run restarts and token refreshes.
    expect(block).toMatch(/\bapiBase\s*:[^\n]*\breal\.apiBase\b/)
    expect(block).toMatch(/\baccessToken\s*:[^\n]*\breal\.accessToken\b/)
    expect(block).toMatch(/\brunId\s*:[^\n]*\breal\.runId\b/)

    // The two gates. `runStatus` decides whether new points are being produced; without it the
    // run never reads as `running` and nothing is ever fetched.
    expect(block).toMatch(/\brunStatus\s*:[^\n]*\breal\.runStatus\b/)

    // The equivalent must be the effective one, not the raw picker value: an empty string here
    // closes `shouldPoll` silently.
    expect(block).toMatch(/\bequivalent\s*:\s*effectiveEq\b/)

    // The surface gate. Dropping it does not make the panel poll more — `enabled` defaults to
    // "always" only when omitted at the seam, and the app's own default is "no surface, no poll".
    expect(block).toMatch(/\benabled\s*:\s*isAnalyticsPanelVisible\b/)
  })
})

/**
 * The reason set is the backend's property, not a list this module is free to hold an opinion
 * about: an application 503 means "no measurements" only because `_real_mode_unavailable` said so
 * in `error.details.reason`, and the panel's phrasing on screen is that sentence repeated.
 *
 * A token added on the backend and not here would be shown to the user as a malfunction; a token
 * removed there and left here would keep a word alive that nothing can send any more. Both are
 * silent, so the two lists are compared against the source rather than trusted to stay in step.
 */
describe('the unavailable reasons are the backend’s, not a local invention', () => {
  it('matches REASON_MESSAGES in app/core/simulator/metrics_bottlenecks.py exactly', () => {
    const here = dirname(fileURLToPath(import.meta.url))
    const source = readFileSync(
      resolve(here, '../../../../app/core/simulator/metrics_bottlenecks.py'),
      'utf8',
    )

    const start = source.indexOf('REASON_MESSAGES = {')
    expect(start).toBeGreaterThan(-1)
    const end = source.indexOf('\n}', start)
    expect(end).toBeGreaterThan(start)

    const backendReasons = [...source.slice(start, end).matchAll(/^\s{4}"([a-z_]+)":/gm)].map(
      (m) => m[1],
    )

    // Guards the parse itself: an expression that stopped matching would otherwise "prove"
    // agreement with an empty set.
    expect(backendReasons.length).toBeGreaterThan(1)

    expect([...UNAVAILABLE_REASONS].sort()).toEqual([...backendReasons].sort())
  })
})
