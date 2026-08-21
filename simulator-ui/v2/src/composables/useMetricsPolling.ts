import {
  computed,
  getCurrentScope,
  onScopeDispose,
  shallowRef,
  watch,
  type ComputedRef,
  type Ref,
  type ShallowRef,
} from 'vue'

import { ApiError, type HttpConfig } from '../api/http'
import { getBottlenecks, getMetrics } from '../api/simulatorApi'
import type { BottlenecksResponse, MetricsResponse, RunStatus } from '../api/simulatorTypes'
import { extractErrorMessage } from '../utils/errorMessage'
import { toLower } from '../utils/stringHelpers'

/** Poll cadence required by the product spec (acceptance item 5): every 5 seconds. */
export const METRICS_POLL_INTERVAL_MS = 5_000

/** Width of the requested window, in run-clock milliseconds. */
export const METRICS_WINDOW_MS = 300_000

/**
 * Resample step. `METRICS_WINDOW_MS / METRICS_STEP_MS + 1 = 61` points per series, far below the
 * server-side ceiling of 2000 (`app/core/simulator/metrics_bottlenecks.py:165-167`), so a window
 * built here can never be rejected as "Too many points".
 */
export const METRICS_STEP_MS = 5_000

/** Kept small on purpose: the panel shows a short list, not the full backlog. */
export const BOTTLENECKS_LIMIT = 10

/**
 * `from_ms`/`to_ms`/`step_ms` are all required by the server (`app/api/v1/simulator.py:2589-2591`),
 * which is the first of the two corrections the program spec makes to the product spec.
 */
export type MetricsWindow = { from_ms: number; to_ms: number; step_ms: number }

/**
 * The three outcomes this composable is required to keep apart, plus the two states that precede
 * the first answer.
 *
 * - `ready` — the backend answered with measurements.
 * - `unavailable` — HTTP 503: real mode refused to substitute synthetic data for measurements it
 *   does not have (`metrics_bottlenecks.py` `_real_mode_unavailable`: storage disabled or DB read
 *   failed). This is the backend being honest, not the application breaking, and the UI must say
 *   "no data", not "something went wrong".
 * - `error` — everything else: a real failure the user should see as a failure.
 * - `loading` — a request is in flight and nothing has been answered yet.
 * - `idle` — the gate is closed (no run, or the run is not `running`) and nothing was fetched.
 */
export type MetricsStreamPhase = 'idle' | 'loading' | 'ready' | 'unavailable' | 'error'

/**
 * The window is expressed in the run's own clock, not wall clock: persisted points are keyed by
 * `t_ms = run.sim_time_ms` (`app/core/simulator/real_tick_persistence.py:113-116`), which starts at
 * 0 for every run. Asking with `Date.now()` would query a range no persisted point can fall into.
 *
 * `sim_time_ms` is typed optional because the pydantic field is `Optional[int]`, but the runtime
 * record initialises it to `0` (`app/core/simulator/models.py:141`), so the null branch is a
 * type-level concern rather than an observed state; treating it as 0 keeps the window inside the
 * range the run has actually reached instead of inventing one.
 */
export function computeMetricsWindow(simTimeMs: number | null | undefined): MetricsWindow {
  const raw = Number(simTimeMs ?? 0)
  const toMs = Number.isFinite(raw) ? Math.max(0, Math.floor(raw)) : 0
  return {
    from_ms: Math.max(0, toMs - METRICS_WINDOW_MS),
    to_ms: toMs,
    step_ms: METRICS_STEP_MS,
  }
}

/**
 * Best-effort correlation handle for a 503: the body carries
 * `{"error": {"code", "message", "details": {"run_id", "equivalent", "reason"}}}`
 * (`app/utils/exceptions.py:43-44`), and `reason` is the same token the backend logged
 * (`storage_disabled` / `db_read_failed`), so a message on screen can be found in the log.
 */
function unavailableReasonOf(e: ApiError): string {
  const raw = e.bodyText
  if (!raw) return ''
  try {
    const parsed = JSON.parse(raw) as { error?: { details?: { reason?: unknown } } } | null
    const reason = parsed?.error?.details?.reason
    return typeof reason === 'string' ? reason : ''
  } catch {
    return ''
  }
}

export function useMetricsPolling(deps: {
  apiBase: Readonly<Ref<string>>
  accessToken: Readonly<Ref<string | null | undefined>>
  runId: Readonly<Ref<string | null | undefined>>
  equivalent: Readonly<Ref<string>>
  runStatus: Readonly<Ref<RunStatus | null | undefined>>
  /**
   * Optional second gate: is anything actually showing this data?
   *
   * The run-status gate answers "are new points being produced"; this one answers "is anyone
   * looking". A closed analytics panel (spec 007, T705) makes both requests pure waste — and not
   * only on the client: `GET /metrics` and `GET /bottlenecks` read the run's metric tables on
   * every poll. Omitted means "always enabled", which keeps every existing caller unchanged.
   */
  enabled?: Readonly<Ref<boolean>>
}): {
  /**
   * Decoded responses, stored verbatim. Nothing here rewrites `MetricPoint.v`: a `null` value
   * means "no measurement at this timestamp" and stays `null` all the way to the renderer — it is
   * never filled with zero, interpolated, or dropped. The backend and the decoder already hold
   * that line; collapsing it here would undo both.
   *
   * `shallowRef`, not `ref`: seven series of ~61 points would otherwise be deep-proxied on every
   * poll for no gain — the value is always replaced wholesale, never mutated in place.
   */
  metrics: ShallowRef<MetricsResponse | null>
  bottlenecks: ShallowRef<BottlenecksResponse | null>
  metricsPhase: Ref<MetricsStreamPhase>
  bottlenecksPhase: Ref<MetricsStreamPhase>
  phase: ComputedRef<MetricsStreamPhase>
  /** Non-empty only when `phase === 'error'`. A 503 never lands here. */
  lastError: Ref<string>
  /** Non-empty only when a 503 carried a reason token. */
  unavailableReason: Ref<string>
  window: ComputedRef<MetricsWindow | null>
  isPolling: ComputedRef<boolean>
  poll: () => Promise<void>
  dispose: () => void
} {
  const metrics = shallowRef<MetricsResponse | null>(null)
  const bottlenecks = shallowRef<BottlenecksResponse | null>(null)
  const metricsPhase = shallowRef<MetricsStreamPhase>('idle')
  const bottlenecksPhase = shallowRef<MetricsStreamPhase>('idle')
  const lastError = shallowRef('')
  const unavailableReason = shallowRef('')

  // A ref, not a plain flag: `isPolling` is a computed, and a non-reactive flag would leave it
  // reporting a torn-down poller as live until some unrelated dependency happened to change.
  const disposed = shallowRef(false)
  let timer: number | null = null
  let inFlight = false
  let seq = 0

  const activeRunId = computed<string | null>(() => {
    const id = String(deps.runId.value ?? '').trim()
    return id || null
  })

  const equivalentKey = computed(() => String(deps.equivalent.value ?? '').trim())

  /**
   * The gate is both "when to start" and "when to stop": a stopped, failed, stopping or paused run
   * produces no new points, so polling it is pure noise. Only `running` is polled, which is what
   * the program spec's verification plan asks for.
   */
  const isRunning = computed(() => toLower(deps.runStatus.value?.state) === 'running')

  const isEnabled = computed(() => deps.enabled?.value ?? true)

  const shouldPoll = computed(
    () =>
      isEnabled.value &&
      activeRunId.value !== null &&
      equivalentKey.value !== '' &&
      isRunning.value,
  )

  const pollWindow = computed<MetricsWindow | null>(() =>
    activeRunId.value === null ? null : computeMetricsWindow(deps.runStatus.value?.sim_time_ms),
  )

  const isPolling = computed(() => shouldPoll.value && !disposed.value)

  const phase = computed<MetricsStreamPhase>(() => {
    const m = metricsPhase.value
    const b = bottlenecksPhase.value
    if (m === 'error' || b === 'error') return 'error'
    if (m === 'unavailable' || b === 'unavailable') return 'unavailable'
    if (m === 'ready' && b === 'ready') return 'ready'
    if (m === 'loading' || b === 'loading') return 'loading'
    return 'idle'
  })

  function reset(): void {
    // Bumping the sequence orphans any in-flight response so data from the previous run or
    // equivalent can never land on the new one.
    seq += 1
    metrics.value = null
    bottlenecks.value = null
    metricsPhase.value = 'idle'
    bottlenecksPhase.value = 'idle'
    lastError.value = ''
    unavailableReason.value = ''
  }

  function applyResult<T>(
    result: PromiseSettledResult<T>,
    data: ShallowRef<T | null>,
    streamPhase: Ref<MetricsStreamPhase>,
  ): void {
    if (result.status === 'fulfilled') {
      data.value = result.value
      streamPhase.value = 'ready'
      return
    }

    const reason: unknown = result.reason
    // Stale numbers rendered next to a failure banner read as current numbers; drop them.
    data.value = null

    if (reason instanceof ApiError && reason.status === 503) {
      streamPhase.value = 'unavailable'
      unavailableReason.value = unavailableReasonOf(reason)
      return
    }

    streamPhase.value = 'error'
    lastError.value = extractErrorMessage(reason)
  }

  async function poll(): Promise<void> {
    if (disposed.value) return
    const runId = activeRunId.value
    const equivalent = equivalentKey.value
    if (runId === null || equivalent === '') return
    // One request pair at a time: a slow backend must not queue up polls.
    if (inFlight) return

    const mySeq = ++seq
    const isCurrent = (): boolean =>
      !disposed.value &&
      seq === mySeq &&
      activeRunId.value === runId &&
      equivalentKey.value === equivalent

    const cfg: HttpConfig = {
      apiBase: deps.apiBase.value,
      accessToken: deps.accessToken.value ?? null,
    }
    const win = computeMetricsWindow(deps.runStatus.value?.sim_time_ms)

    if (metricsPhase.value === 'idle') metricsPhase.value = 'loading'
    if (bottlenecksPhase.value === 'idle') bottlenecksPhase.value = 'loading'

    inFlight = true
    try {
      // `allSettled`, not `all`: the two endpoints fail independently, and one of them being
      // unavailable must not be reported as the other one failing.
      const [metricsResult, bottlenecksResult] = await Promise.allSettled([
        getMetrics(cfg, runId, equivalent, win),
        getBottlenecks(cfg, runId, equivalent, { limit: BOTTLENECKS_LIMIT }),
      ])
      if (!isCurrent()) return
      lastError.value = ''
      unavailableReason.value = ''
      applyResult(metricsResult, metrics, metricsPhase)
      applyResult(bottlenecksResult, bottlenecks, bottlenecksPhase)
    } finally {
      inFlight = false
    }
  }

  function stop(): void {
    if (timer !== null) {
      window.clearInterval(timer)
      timer = null
    }
  }

  function start(): void {
    if (disposed.value || timer !== null) return
    timer = window.setInterval(() => {
      void poll()
    }, METRICS_POLL_INTERVAL_MS)
  }

  // Identity of the polled subject. `''` is not a reachable value (the string always contains the
  // separator), so the immediate run always counts as a change and resets from a known state.
  let lastIdentity = ''

  watch(
    [shouldPoll, activeRunId, equivalentKey],
    () => {
      const identity = `${activeRunId.value ?? ''}|${equivalentKey.value}`
      const identityChanged = identity !== lastIdentity
      lastIdentity = identity

      if (identityChanged) reset()

      if (!shouldPoll.value) {
        stop()
        return
      }
      if (identityChanged || timer === null) void poll()
      start()
    },
    { immediate: true },
  )

  function dispose(): void {
    disposed.value = true
    // Orphans any in-flight response so it cannot write into a torn-down surface.
    seq += 1
    stop()
  }

  // Covers component unmount: a component's setup runs inside an effect scope, and unmounting
  // stops it. `dispose` is returned as well for the callers that run outside a scope.
  if (getCurrentScope()) onScopeDispose(dispose)

  return {
    metrics,
    bottlenecks,
    metricsPhase,
    bottlenecksPhase,
    phase,
    lastError,
    unavailableReason,
    window: pollWindow,
    isPolling,
    poll,
    dispose,
  }
}
