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
 * - `unavailable` — HTTP 503 carrying one of `UNAVAILABLE_REASONS` in `error.details.reason`: real
 *   mode refused to substitute synthetic data for measurements it does not have
 *   (`metrics_bottlenecks.py` `_real_mode_unavailable`: storage disabled or DB read failed). This
 *   is the backend being honest, not the application breaking, and the UI must say "no data", not
 *   "something went wrong". The reason token is what makes it that statement; a bare 503 is not.
 * - `error` — everything else, INCLUDING a 503 without a recognised reason: a real failure the
 *   user should see as a failure. A 503 from a proxy, an ingress or a balancer in front of a
 *   restarting application never reached this application at all, so it is not evidence about
 *   whether the run has measurements.
 * - `loading` — a request is in flight and nothing has been answered yet.
 * - `idle` — the gate is closed (no run, or the run is not `running`) and nothing was fetched.
 *
 * A phase describes ONE stream, and there is deliberately no aggregate of the two.
 * `GET /metrics` and `GET /bottlenecks` are answered from two different database sessions
 * (`app/core/simulator/metrics_bottlenecks.py:250`, `:422`), each of which raises `db_read_failed`
 * on its own, so the pair `(ready, unavailable)` is an ordinary outcome — and any single word
 * covering it would have to state the absence of measurements this composable is holding.
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
 * The complete set of reasons this application answers 503 with, mirrored from the backend:
 * `REASON_MESSAGES` in `app/core/simulator/metrics_bottlenecks.py:35-45`, raised by
 * `_real_mode_unavailable` on all four of its call sites (`:188`, `:250`, `:349`, `:422`).
 *
 * It is a closed set on purpose. A 503 is only "the backend has no measurements" when the backend
 * itself said so; every other 503 on the wire — a proxy, a load balancer, an ingress, a restart
 * window — is a request that never reached this application, and it says nothing whatsoever about
 * whether the run has measurements.
 */
export const UNAVAILABLE_REASONS = ['storage_disabled', 'db_read_failed'] as const

export type UnavailableReason = (typeof UNAVAILABLE_REASONS)[number]

function isUnavailableReason(value: unknown): value is UnavailableReason {
  return typeof value === 'string' && (UNAVAILABLE_REASONS as readonly string[]).includes(value)
}

/**
 * The structural reason a 503 carries, or `null` when it carries none this application recognises.
 *
 * The body of an application 503 is
 * `{"error": {"code", "message", "details": {"run_id", "equivalent", "reason"}}}`
 * (`app/utils/exceptions.py:43-44`), and `reason` is the same token the backend logged, so a
 * message on screen can be found in the log.
 *
 * `null` for a missing body, an unparseable body, a body without the token, and — deliberately —
 * a token this build does not know. An unknown token means the two sides disagree about what the
 * backend can say; guessing "no measurements" from a word we cannot read would be the same claim
 * about the user's data made on even weaker evidence. Each stage is correct on its own and does
 * not lean on a later one to correct it (§9).
 */
function unavailableReasonOf(e: ApiError): UnavailableReason | null {
  const raw = e.bodyText
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as { error?: { details?: { reason?: unknown } } } | null
    const reason = parsed?.error?.details?.reason
    return isUnavailableReason(reason) ? reason : null
  } catch {
    return null
  }
}

/**
 * Everything one endpoint says about itself: what it answered, which state it is in, and — when it
 * failed — why. Held together so the four can never drift apart, and so one stream's reason can
 * never be shown under the other stream's phase.
 */
type StreamState<T> = {
  data: ShallowRef<T | null>
  phase: ShallowRef<MetricsStreamPhase>
  /** Non-empty only while `phase === 'error'`. A 503 never lands here. */
  error: ShallowRef<string>
  /**
   * Non-empty exactly while `phase === 'unavailable'`, and always one of `UNAVAILABLE_REASONS`:
   * that phase is not reachable without a recognised token.
   */
  unavailableReason: ShallowRef<string>
}

function makeStreamState<T>(): StreamState<T> {
  return {
    data: shallowRef<T | null>(null),
    phase: shallowRef<MetricsStreamPhase>('idle'),
    error: shallowRef(''),
    unavailableReason: shallowRef(''),
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
  /**
   * Two phases, and no aggregate. See `MetricsStreamPhase`: `(ready, unavailable)` is reachable
   * on any poll, so a merged phase is a statement no consumer could make truthfully.
   */
  metricsPhase: Ref<MetricsStreamPhase>
  bottlenecksPhase: Ref<MetricsStreamPhase>
  metricsError: Ref<string>
  bottlenecksError: Ref<string>
  metricsUnavailableReason: Ref<string>
  bottlenecksUnavailableReason: Ref<string>
  window: ComputedRef<MetricsWindow | null>
  isPolling: ComputedRef<boolean>
  poll: () => Promise<void>
  dispose: () => void
} {
  const metricsStream = makeStreamState<MetricsResponse>()
  const bottlenecksStream = makeStreamState<BottlenecksResponse>()

  // A ref, not a plain flag: `isPolling` is a computed, and a non-reactive flag would leave it
  // reporting a torn-down poller as live until some unrelated dependency happened to change.
  const disposed = shallowRef(false)
  let timer: number | null = null
  let seq = 0
  /**
   * Which subject the in-flight request pair belongs to (`null` when nothing is in flight), and
   * the sequence number that owns the slot.
   */
  let inFlightIdentity: string | null = null
  let inFlightSeq = 0

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

  function resetStream(stream: StreamState<unknown>): void {
    stream.data.value = null
    stream.phase.value = 'idle'
    stream.error.value = ''
    stream.unavailableReason.value = ''
  }

  function reset(): void {
    // Bumping the sequence orphans any in-flight response so data from the previous run or
    // equivalent can never land on the new one.
    seq += 1
    resetStream(metricsStream)
    resetStream(bottlenecksStream)
  }

  function applyResult<T>(result: PromiseSettledResult<T>, stream: StreamState<T>): void {
    stream.error.value = ''
    stream.unavailableReason.value = ''

    if (result.status === 'fulfilled') {
      stream.data.value = result.value
      stream.phase.value = 'ready'
      return
    }

    const reason: unknown = result.reason
    // Stale numbers rendered next to a failure banner read as current numbers; drop them.
    stream.data.value = null

    // "No measurements" is a statement about the user's data, so only the application may make it:
    // the status code alone is not evidence. A 503 counts as `unavailable` exactly when it carries
    // a structural reason from `_real_mode_unavailable`; anything else that answers 503 — a proxy,
    // an ingress, a balancer in front of a restarting app — is a failure and is shown as one.
    if (reason instanceof ApiError && reason.status === 503) {
      const structuralReason = unavailableReasonOf(reason)
      if (structuralReason !== null) {
        stream.phase.value = 'unavailable'
        stream.unavailableReason.value = structuralReason
        return
      }
    }

    stream.phase.value = 'error'
    stream.error.value = extractErrorMessage(reason)
  }

  async function poll(): Promise<void> {
    if (disposed.value) return
    const runId = activeRunId.value
    const equivalent = equivalentKey.value
    if (runId === null || equivalent === '') return
    const identity = `${runId}|${equivalent}`

    // One request pair at a time PER SUBJECT: a slow backend must not queue up polls. The identity
    // is captured before this check on purpose — a poll caused by the run or the equivalent
    // changing is not a queued poll, and swallowing it leaves the panel on `idle` for up to a full
    // interval while the run is running, i.e. saying "analytics updates while a run is running"
    // during a running run.
    if (inFlightIdentity === identity) return

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

    if (metricsStream.phase.value === 'idle') metricsStream.phase.value = 'loading'
    if (bottlenecksStream.phase.value === 'idle') bottlenecksStream.phase.value = 'loading'

    inFlightIdentity = identity
    inFlightSeq = mySeq
    try {
      // `allSettled`, not `all`: the two endpoints fail independently, and one of them being
      // unavailable must not be reported as the other one failing.
      const [metricsResult, bottlenecksResult] = await Promise.allSettled([
        getMetrics(cfg, runId, equivalent, win),
        getBottlenecks(cfg, runId, equivalent, { limit: BOTTLENECKS_LIMIT }),
      ])
      if (!isCurrent()) return
      applyResult(metricsResult, metricsStream)
      applyResult(bottlenecksResult, bottlenecksStream)
    } finally {
      // Only the pair that still owns the slot releases it: a superseded pair settling later must
      // not re-open the gate for the pair that is still running.
      if (inFlightSeq === mySeq) inFlightIdentity = null
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
    metrics: metricsStream.data,
    bottlenecks: bottlenecksStream.data,
    metricsPhase: metricsStream.phase,
    bottlenecksPhase: bottlenecksStream.phase,
    metricsError: metricsStream.error,
    bottlenecksError: bottlenecksStream.error,
    metricsUnavailableReason: metricsStream.unavailableReason,
    bottlenecksUnavailableReason: bottlenecksStream.unavailableReason,
    window: pollWindow,
    isPolling,
    poll,
    dispose,
  }
}
