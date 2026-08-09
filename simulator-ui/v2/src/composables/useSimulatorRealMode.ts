import { getCurrentScope, onScopeDispose, watch, type ComputedRef } from 'vue'

import {
  artifactDownloadUrl,
  createRun,
  ensureSession,
  getActiveRun,
  getRun,
  listArtifacts,
  listScenarios,
  pauseRun,
  resumeRun,
  setIntensity,
  stopRun,
} from '../api/simulatorApi'
import type {
  ActiveRunResponse,
  ArtifactIndexItem,
  RunStatus,
  ScenarioSummary,
  SimulatorMode,
} from '../api/simulatorTypes'
import { connectSse, type SseParsedMessage } from '../api/sse'
import { normalizeSimulatorEvent } from '../api/normalizeSimulatorEvent'
import { ApiError, authHeaders } from '../api/http'

import type { ClearingDoneEvent, EdgePatch, NodePatch, TxUpdatedEvent } from '../types'
import type { SimulatorAppState } from '../types/simulatorApp'
import { incCounter } from '../utils/counters'
import { toLower } from '../utils/stringHelpers'
import {
  applyAcceptedRealEvent,
  createRealEventReplayOwner,
  executeRealEventIntents,
  type RealEventIntent,
} from './realEventPipeline'

export type RealModeState = {
  apiBase: string
  accessToken: string
  loadingScenarios: boolean
  scenarios: ScenarioSummary[]
  selectedScenarioId: string
  desiredMode: SimulatorMode
  intensityPercent: number
  runId: string | null
  runStatus: RunStatus | null
  sseState: string
  lastEventId: string | null
  lastError: string
  artifacts: ArtifactIndexItem[]
  artifactsLoading: boolean
  runStats: {
    startedAtMs: number
    attempts: number
    committed: number
    rejected: number
    errors: number
    timeouts: number
    rejectedByCode: Record<string, number>
    errorsByCode: Record<string, number>
  }
}

export type PatchApplier = {
  applyNodePatches: (patches: NodePatch[] | undefined) => void
  applyEdgePatches: (patches: EdgePatch[] | undefined) => void
}

type RealModeDiag = {
  rx_messages: number
  json_parse_errors: number
  normalize_dropped: number
  rejected_by_reason: Record<string, number>
  frame_id_mismatches: number
  events_by_type: Record<string, number>
  tx_sender_labels: number
  tx_receiver_labels: number
  tx_receiver_scheduled: number
  tx_receiver_guard_dropped: number
  amount_flyout_suppressed: number
  burst_throttle_enabled_events: number
  burst_throttle_ms_last: number
}

type GeoRealModeGlobal = typeof globalThis & { __geo_real_mode_diag?: RealModeDiag }

const DIAGNOSTIC_EVENT_TYPE_BUCKETS = new Set([
  'run_status',
  'tx.updated',
  'tx.failed',
  'clearing.done',
  'topology.changed',
  'audit.drift',
])

const REPLAY_GAP_BUFFER_MAX_MESSAGES = 512

function diagnosticEventTypeBucket(eventType: string | undefined): string {
  if (!eventType) return 'untyped'
  return DIAGNOSTIC_EVENT_TYPE_BUCKETS.has(eventType) ? eventType : 'other'
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function getErrorMessage(e: unknown): string {
  if (e instanceof Error) return e.message
  if (isRecord(e) && typeof e.message === 'string') return e.message
  return String(e)
}

function extractRunId(v: ActiveRunResponse | null | undefined): string {
  return String(v?.run_id ?? '').trim()
}

export function useSimulatorRealMode(opts: {
  isRealMode: ComputedRef<boolean>
  isLocalhost: boolean
  effectiveEq: ComputedRef<string>
  state: SimulatorAppState
  real: RealModeState

  ensureScenarioSelectionValid: () => void
  resetRunStats: () => void
  cleanupRealRunFxAndTimers: () => void

  isUserFacingRunError: (code: string) => boolean
  inc: (map: Record<string, number>, key: string) => void

  // Scene loader integration (real mode uses snapshots via SceneState)
  loadScene: () => Promise<void>

  // Live patch application + FX hooks
  realPatchApplier: PatchApplier
  // Backend-first tx labels (avoid frontend-derived balance deltas)
  pushTxAmountLabel: (nodeId: string, signedAmount: string, unit: string, opts?: { throttleMs?: number }) => void

  // Shared TTL clamp (single source of truth with runRealTxFx)
  clampRealTxTtlMs: (ttlRaw: unknown, fallbackMs?: number) => number

  // Managed timers (must be cleaned up via cleanupRealRunFxAndTimers -> clearScheduledTimeouts)
  scheduleTimeout: (fn: () => void, delayMs: number, opts?: { critical?: boolean }) => void
  runRealTxFx: (tx: TxUpdatedEvent) => void
  runRealClearingDoneFx: (done: ClearingDoneEvent) => void

  // Optional: wake up render loop after snapshot/patch/FX updates.
  wakeUp?: () => void

  // Optional: notify on any SSE event (used to keep Demo UI render loop awake).
  onAnySseEvent?: () => void
  // Optional visual-effects policy seam (e.g. reduced-motion integration).
  optionalFxEnabled?: () => boolean
}): {
  stopSse: () => void
  resetStaleRun: (opts?: { clearError?: boolean }) => void
  refreshRunStatus: () => Promise<void>
  refreshSnapshot: () => Promise<void>
  refreshScenarios: () => Promise<void>
  startRun: (opts?: { mode?: SimulatorMode; intensityPercent?: number; pauseImmediately?: boolean }) => Promise<void>
  pause: () => Promise<void>
  resume: () => Promise<void>
  stop: () => Promise<void>
  applyIntensity: () => Promise<void>
  refreshArtifacts: () => Promise<void>
  downloadArtifact: (name: string) => Promise<void>

  // Admin helpers
  attachToRun: (runId: string) => Promise<void>
  stopRunById: (runId: string, opts?: { source?: string; reason?: string }) => Promise<void>
} {
  const {
    isRealMode,
    isLocalhost,
    effectiveEq,
    state,
    real,
    ensureScenarioSelectionValid,
    resetRunStats,
    cleanupRealRunFxAndTimers,
    isUserFacingRunError,
    inc,
    loadScene,
    realPatchApplier,
    pushTxAmountLabel,
    clampRealTxTtlMs,
    scheduleTimeout,
    runRealTxFx,
    runRealClearingDoneFx,
    wakeUp,
    onAnySseEvent,
    optionalFxEnabled,
  } = opts

  // -----------------
  // Diagnostics (dev-only, per plan section 10)
  // -----------------

  const diag: RealModeDiag = {
    rx_messages: 0,
    json_parse_errors: 0,
    normalize_dropped: 0,
    rejected_by_reason: {},
    frame_id_mismatches: 0,
    events_by_type: {},
    tx_sender_labels: 0,
    tx_receiver_labels: 0,
    tx_receiver_scheduled: 0,
    tx_receiver_guard_dropped: 0,
    amount_flyout_suppressed: 0,
    burst_throttle_enabled_events: 0,
    burst_throttle_ms_last: 0,
  }

  if (isLocalhost) {
    ;(globalThis as GeoRealModeGlobal).__geo_real_mode_diag = diag
  }

  const incDiag = incCounter

  // -----------------
  // Real Mode: SSE loop
  // -----------------

  let sseAbort: AbortController | null = null
  let sseSeq = 0
  let refreshRunStatusSeq = 0
  let replayGapRecoveryRunId: string | null = null

  const replayOwner = createRealEventReplayOwner()

  // Debounce multiple rapid calls to refreshSnapshot (e.g., during startRun + watcher cascade).
  let refreshSnapshotDebounceTimer: ReturnType<typeof setTimeout> | null = null
  type SnapshotRefreshOutcome = 'current' | 'stale' | 'transient' | 'superseded'
  let refreshSnapshotActivePromise: Promise<SnapshotRefreshOutcome> | null = null
  let refreshSnapshotPending = false

  // Sequence id used to invalidate pending debounced refreshes and to guard against stale context.
  // - incremented on each *actual* refresh attempt
  // - incremented on teardown/stop paths (invalidates delayed refreshes + post-await steps)
  let refreshSnapshotSeq = 0
  
  // Block watchers from calling refreshSnapshot during startRun to prevent double load.
  let startRunInProgress = false

  // Block the scenario-change watcher from calling refreshSnapshot during the initial
  // boot sequence (immediate watcher). refreshScenarios() may update selectedScenarioId,
  // which triggers the scenario watcher — but the immediate watcher is about to call
  // refreshSnapshot() itself, so the scenario watcher's call is redundant and causes
  // a visible "Loading…" flash.
  let initialBootInProgress = false

  // Anonymous visitors rely on cookie-auth (geo_sim_sid). Ensure the cookie exists
  // before running real-mode boot calls like /scenarios or /runs/active.
  let anonSessionEnsured = false
  let anonSessionEnsuring: Promise<void> | null = null
  async function ensureAnonSessionOnce(): Promise<void> {
    const token = String(real.accessToken ?? '').trim()
    if (token) return
    if (anonSessionEnsured) return
    if (anonSessionEnsuring) return await anonSessionEnsuring

    anonSessionEnsuring = (async () => {
      try {
        await ensureSession({ apiBase: real.apiBase })
        anonSessionEnsured = true
      } catch {
        // Best-effort: if session bootstrap fails, subsequent API calls may 401.
        // Let existing per-call error handling surface the failure.
      } finally {
        anonSessionEnsuring = null
      }
    })()

    await anonSessionEnsuring
  }

  function cancelPendingRefreshSnapshotDebounce() {
    if (refreshSnapshotDebounceTimer !== null) {
      clearTimeout(refreshSnapshotDebounceTimer)
      refreshSnapshotDebounceTimer = null
    }
    refreshSnapshotPending = false
  }

  function teardownRefreshSnapshot() {
    refreshSnapshotSeq += 1
    cancelPendingRefreshSnapshotDebounce()
  }

  function stopSse() {
    // Ensure no delayed refreshSnapshot() can fire after SSE stop/teardown.
    cancelPendingRefreshSnapshotDebounce()
    sseAbort?.abort()
    sseAbort = null
    real.sseState = 'idle'
  }

  if (getCurrentScope()) {
    onScopeDispose(() => {
      teardownRefreshSnapshot()
      stopSse()
    })
  }

  function resetStaleRun(resetOpts?: { clearError?: boolean }) {
    refreshRunStatusSeq += 1
    teardownRefreshSnapshot()
    real.runId = null
    real.runStatus = null
    real.lastEventId = null
    real.artifacts = []
    cleanupRealRunFxAndTimers()
    resetRunStats()
    stopSse()

    replayOwner.reset()
    replayGapRecoveryRunId = null

    if (resetOpts?.clearError ?? true) {
      real.lastError = ''
      state.error = ''
    }

    // Keep selection usable after restart (do not auto-start).
    ensureScenarioSelectionValid()
  }

  type RunStatusRefreshOutcome = 'current' | 'stale' | 'transient' | 'superseded'

  async function refreshRunStatusForCurrentContext(): Promise<RunStatusRefreshOutcome> {
    // No accessToken guard: anonymous visitors use cookie-auth (geo_sim_sid).
    const runId = real.runId
    if (!runId) return 'superseded'
    const mySeq = ++refreshRunStatusSeq
    const isCurrent = () => refreshRunStatusSeq === mySeq && real.runId === runId
    try {
      const st = await getRun({ apiBase: real.apiBase, accessToken: real.accessToken }, runId)
      if (!isCurrent()) return 'superseded'
      real.runStatus = st
      const le = st.last_error
      real.lastError = le && isUserFacingRunError(le.code) ? `${le.code}: ${le.message}` : ''
      return 'current'
    } catch (e: unknown) {
      if (!isCurrent()) return 'superseded'
      if (e instanceof ApiError && e.status === 404) {
        resetStaleRun({ clearError: true })
        return 'stale'
      }
      real.lastError = getErrorMessage(e)
      return 'transient'
    }
  }

  async function refreshRunStatus(): Promise<void> {
    await refreshRunStatusForCurrentContext()
  }

  function startSnapshotRefresh(opts?: { debounce?: boolean }): Promise<SnapshotRefreshOutcome> {
    const mySeq = ++refreshSnapshotSeq
    const runIdAtStart = real.runId

    const isContextStillValid = () => {
      if (!isRealMode.value) return false
      // No accessToken guard: anonymous visitors use cookie-auth (geo_sim_sid).

      // Abort if a teardown/stop happened or a newer refresh started.
      if (refreshSnapshotSeq !== mySeq) return false

      // Prevent a delayed refresh from applying to a different run context.
      if (real.runId !== runIdAtStart) return false

      return true
    }

    if (opts?.debounce ?? true) {
      refreshSnapshotDebounceTimer = setTimeout(() => {
        refreshSnapshotDebounceTimer = null

        // If context changed while we were debouncing, drop pending refresh safely.
        if (!isContextStillValid()) {
          refreshSnapshotPending = false
          return
        }

        if (refreshSnapshotPending) {
          refreshSnapshotPending = false
          void refreshSnapshot()
        }
      }, 80)
    }

    let promise: Promise<SnapshotRefreshOutcome>
    promise = Promise.resolve().then(async (): Promise<SnapshotRefreshOutcome> => {
      try {
        if (!isContextStillValid()) return 'superseded'

        try {
          await loadScene()
        } catch (e: unknown) {
          if (!isContextStillValid()) return 'superseded'
          const message = getErrorMessage(e)
          if (message.includes('HTTP 404')) {
            resetStaleRun({ clearError: true })
            return 'stale'
          }
          real.lastError = message
          return 'transient'
        }

        // Context might have changed while loadScene() was in-flight.
        if (!isContextStillValid()) return 'superseded'

        // Snapshot change should be visible immediately even if deep idle stopped scheduling.
        wakeUp?.()

        // Scene loader stores errors in state.error; in real mode surface them in the HUD.
        if (isRealMode.value && state.error) {
          if (state.error.includes('HTTP 404')) {
            resetStaleRun({ clearError: true })
            return 'stale'
          }
          real.lastError = state.error
          return 'transient'
        }
        return 'current'
      } finally {
        if (refreshSnapshotActivePromise === promise) {
          refreshSnapshotActivePromise = null
        }

        // If another call came in while we were loading, re-invoke.
        if (refreshSnapshotPending) {
          refreshSnapshotPending = false
          // Replay recovery already owns an authoritative refresh. Requests
          // coalesced into it are redundant and must not start a competing
          // snapshot between buffered-frame drain and live admission.
          if (replayGapRecoveryRunId === null) void refreshSnapshot()
        }
      }
    })
    refreshSnapshotActivePromise = promise
    return promise
  }

  async function refreshSnapshot(): Promise<void> {
    // Guard: never refresh snapshots when real mode is not active or context is missing.
    // No accessToken guard: anonymous visitors use cookie-auth (geo_sim_sid).
    if (!isRealMode.value) return
    if (!real.runId && !real.selectedScenarioId) return

    // If a refresh is already in flight, mark as pending and return — the current call will re-invoke.
    if (refreshSnapshotActivePromise) {
      refreshSnapshotPending = true
      await refreshSnapshotActivePromise
      return
    }

    // Debounce: if called within a short window, delay to coalesce multiple triggers.
    if (refreshSnapshotDebounceTimer !== null) {
      refreshSnapshotPending = true
      return
    }
    await startSnapshotRefresh()
  }

  async function refreshSnapshotForReplayRecovery(runId: string): Promise<SnapshotRefreshOutcome> {
    // Start a new authoritative load immediately. SceneState owns latest-request
    // application, so this invalidates both tracked and direct watcher loads.
    cancelPendingRefreshSnapshotDebounce()
    if (!isRealMode.value || real.runId !== runId) return 'superseded'
    return await startSnapshotRefresh({ debounce: false })
  }

  // Also surface initial scene-load errors (e.g., "No run started") that happen on mount.
  watch(
    () => state.error,
    (e) => {
      if (!isRealMode.value) return
      if (!e) return

      if (e.includes('HTTP 404')) {
        resetStaleRun({ clearError: true })
        return
      }

      real.lastError = e
    },
    { flush: 'post' },
  )

  function nextBackoff(attempt: number): number {
    const steps = [1000, 2000, 5000, 10000, 20000]
    const base = steps[Math.min(steps.length - 1, attempt)]!
    const jitter = 0.2 * base * (Math.random() - 0.5)
    return Math.max(250, Math.round(base + jitter))
  }

  function handleSseMessage(input: {
    msg: SseParsedMessage
    signal: AbortSignal
    runId: string
    equivalent: string
  }): void {
    const { msg, signal, runId, equivalent } = input
    if (signal.aborted) return
    if (!msg.data) return

    let parsed: unknown
    try {
      parsed = JSON.parse(msg.data)
    } catch {
      diag.rx_messages += 1
      diag.json_parse_errors += 1
      return
    }

    diag.rx_messages += 1

    const normalized = normalizeSimulatorEvent(parsed)
    if (normalized.status === 'ignored') {
      diag.normalize_dropped += 1
      incDiag(
        diag.rejected_by_reason,
        `${normalized.kind}:${diagnosticEventTypeBucket(normalized.eventType)}:${normalized.diagnostic}`,
      )
      return
    }

    const evt = normalized.event
    const admission = replayOwner.admit({
      event: evt,
      frameId: msg.id,
      connectionRunId: runId,
      activeRunId: real.runId,
      connectionEquivalent: equivalent,
      currentCursor: real.lastEventId,
    })
    if (admission.status === 'rejected') {
      diag.normalize_dropped += 1
      if (admission.diagnostic === 'frame_id_mismatch') diag.frame_id_mismatches += 1
      incDiag(diag.rejected_by_reason, `${admission.kind}:${evt.type}:${admission.diagnostic}`)
      return
    }
    if (admission.status === 'duplicate') return

    const intents = applyAcceptedRealEvent(evt, runId, { real, state }, {
      patchApplier: realPatchApplier,
      isUserFacingRunError,
      inc,
    })
    replayOwner.commit(evt.event_id, runId)
    real.lastEventId = admission.cursor
    incDiag(diag.events_by_type, evt.type)
    executeRealEventIntents(intents, {
      getActiveRunId: () => real.runId,
      optionalFxEnabled,
      onAnySseEvent,
      refreshSnapshot: () => void refreshSnapshot(),
      wakeUp,
      runRealTxFx,
      runRealClearingDoneFx,
      pushTxAmountLabel,
      clampRealTxTtlMs,
      scheduleTimeout,
      onIntentExecuted: (intent: RealEventIntent) => {
        if (intent.type === 'amount-flyout-suppressed') diag.amount_flyout_suppressed += 1
        else if (intent.type === 'burst-throttle') {
          diag.burst_throttle_ms_last = intent.throttleMs
          if (intent.throttleMs > 0) diag.burst_throttle_enabled_events += 1
        } else if (intent.type === 'tx-label') diag.tx_sender_labels += 1
        else if (intent.type === 'tx-label-delayed') diag.tx_receiver_scheduled += 1
      },
      onReceiverLabelPushed: () => {
        diag.tx_receiver_labels += 1
      },
      onReceiverGuardDropped: () => {
        diag.tx_receiver_guard_dropped += 1
      },
    })
  }

  function isReplayBootstrapFrame(msg: SseParsedMessage, runId: string): boolean {
    if (!msg.data) return false
    try {
      const payload: unknown = JSON.parse(msg.data)
      return isRecord(payload) && payload.type === 'run_status' && payload.run_id === runId
    } catch {
      return false
    }
  }

  type SseConnectionOutcome = { status: 'ended' } | { status: 'error'; error: unknown }
  type ReplayRecoveryConnectionOutcome = 'ended' | 'retry' | 'stop'

  async function runReplayRecoveryConnection(input: {
    runId: string
    ctrl: AbortController
    sseLoopSeq: number
    equivalent: string
    url: string
  }): Promise<ReplayRecoveryConnectionOutcome> {
    const { runId, ctrl, sseLoopSeq, equivalent, url } = input
    const isCurrentContext = () =>
      !ctrl.signal.aborted && sseLoopSeq === sseSeq && isRealMode.value && real.runId === runId

    if (!isCurrentContext()) return 'stop'

    const connectionCtrl = new AbortController()
    const abortConnection = () => connectionCtrl.abort()
    ctrl.signal.addEventListener('abort', abortConnection, { once: true })

    const bufferedMessages: SseParsedMessage[] = []
    let buffering = true
    let bufferOverflowed = false
    let firstBootstrapResolved = false
    let resolveFirstBootstrap!: () => void
    let resolveBufferOverflow!: () => void
    const firstBootstrap = new Promise<void>((resolve) => {
      resolveFirstBootstrap = resolve
    })
    const bufferOverflow = new Promise<void>((resolve) => {
      resolveBufferOverflow = resolve
    })

    const connectionOutcome: Promise<SseConnectionOutcome> = connectSse({
      url,
      headers: authHeaders(real.accessToken),
      lastEventId: null,
      signal: connectionCtrl.signal,
      onMessage: (msg) => {
        if (connectionCtrl.signal.aborted || !isCurrentContext()) return
        if (!buffering) {
          handleSseMessage({ msg, signal: connectionCtrl.signal, runId, equivalent })
          return
        }

        if (bufferedMessages.length >= REPLAY_GAP_BUFFER_MAX_MESSAGES) {
          if (!bufferOverflowed) {
            bufferOverflowed = true
            real.lastError = `SSE replay recovery buffer exceeded ${REPLAY_GAP_BUFFER_MAX_MESSAGES} messages`
            resolveBufferOverflow()
            connectionCtrl.abort()
          }
          return
        }

        bufferedMessages.push(msg)
        if (!firstBootstrapResolved && isReplayBootstrapFrame(msg, runId)) {
          firstBootstrapResolved = true
          resolveFirstBootstrap()
        }
      },
    }).then(
      () => ({ status: 'ended' as const }),
      (error: unknown) => ({ status: 'error' as const, error }),
    )

    const settleAfterAbort = async () => {
      connectionCtrl.abort()
      await connectionOutcome
    }

    try {
      const bootstrapOutcome = await Promise.race([
        firstBootstrap.then(() => ({ kind: 'bootstrap' as const })),
        bufferOverflow.then(() => ({ kind: 'overflow' as const })),
        connectionOutcome.then((outcome) => ({ kind: 'connection' as const, outcome })),
      ])

      if (bootstrapOutcome.kind === 'overflow') {
        await settleAfterAbort()
        return 'retry'
      }
      if (bootstrapOutcome.kind === 'connection') {
        if (!isCurrentContext()) return 'stop'
        if (bootstrapOutcome.outcome.status === 'error') throw bootstrapOutcome.outcome.error
        real.lastError = 'SSE replay recovery connection ended before its bootstrap frame'
        return 'retry'
      }
      if (bufferOverflowed) {
        await settleAfterAbort()
        return 'retry'
      }
      if (!isCurrentContext()) {
        await settleAfterAbort()
        return 'stop'
      }

      const snapshotPromise = refreshSnapshotForReplayRecovery(runId)
      const snapshotOrConnection = await Promise.race([
        snapshotPromise.then((outcome) => ({ kind: 'snapshot' as const, outcome })),
        bufferOverflow.then(() => ({ kind: 'overflow' as const })),
        connectionOutcome.then((outcome) => ({ kind: 'connection' as const, outcome })),
      ])

      if (snapshotOrConnection.kind === 'overflow') {
        await settleAfterAbort()
        return 'retry'
      }
      if (snapshotOrConnection.kind === 'connection') {
        if (!isCurrentContext()) return 'stop'
        if (snapshotOrConnection.outcome.status === 'error') throw snapshotOrConnection.outcome.error
        real.lastError = 'SSE replay recovery connection ended before the authoritative snapshot completed'
        return 'retry'
      }
      if (snapshotOrConnection.outcome === 'transient') {
        await settleAfterAbort()
        return 'retry'
      }
      if (snapshotOrConnection.outcome !== 'current' || !isCurrentContext()) {
        await settleAfterAbort()
        return 'stop'
      }
      if (bufferOverflowed) {
        await settleAfterAbort()
        return 'retry'
      }

      for (const msg of bufferedMessages) {
        handleSseMessage({ msg, signal: connectionCtrl.signal, runId, equivalent })
      }
      bufferedMessages.length = 0
      buffering = false
      replayGapRecoveryRunId = null
      real.sseState = 'open'

      const finalOutcome = await connectionOutcome
      if (finalOutcome.status === 'error') throw finalOutcome.error
      return 'ended'
    } finally {
      ctrl.signal.removeEventListener('abort', abortConnection)
    }
  }

  async function refreshReplayGapStatusForCurrentContext(input: {
    runId: string
    ctrl: AbortController
    sseLoopSeq: number
  }): Promise<'current' | 'retry' | 'stop'> {
    const { runId, ctrl, sseLoopSeq } = input
    if (replayGapRecoveryRunId !== runId) return 'current'

    real.sseState = 'reconnecting'
    const statusOutcome = await refreshRunStatusForCurrentContext()
    if (
      statusOutcome === 'stale' ||
      statusOutcome === 'superseded' ||
      ctrl.signal.aborted ||
      sseLoopSeq !== sseSeq ||
      real.runId !== runId
    ) {
      return 'stop'
    }
    if (statusOutcome === 'transient') return 'retry'
    return 'current'
  }

  async function runSseLoop(loopOpts?: { preserveLastError?: boolean }) {
    const mySeq = ++sseSeq
    stopSse()

    if (!isRealMode.value) return
    // Note: accessToken is not required here — anonymous visitors use cookie-auth (geo_sim_sid).
    // credentials: 'include' is set at the SSE/fetch layer; see api/sse.ts connectSse.
    if (!real.runId) return

    const ctrl = new AbortController()
    sseAbort = ctrl

    let attempt = 0
    real.sseState = 'connecting'
    if (!loopOpts?.preserveLastError) real.lastError = ''

    while (!ctrl.signal.aborted && mySeq === sseSeq) {
      const activeRunId = real.runId
      if (!activeRunId) return
      const runId: string = activeRunId
      if (replayGapRecoveryRunId !== null && replayGapRecoveryRunId !== runId) {
        replayGapRecoveryRunId = null
      }

      const eqNow = effectiveEq.value
      const url = `${real.apiBase.replace(/\/+$/, '')}/simulator/runs/${encodeURIComponent(runId)}/events?equivalent=${encodeURIComponent(
        eqNow,
      )}`

      try {
        if (replayGapRecoveryRunId === runId) {
          const statusOutcome = await refreshReplayGapStatusForCurrentContext({ runId, ctrl, sseLoopSeq: mySeq })
          if (statusOutcome === 'stop') return
          if (statusOutcome === 'retry') {
            const delay = nextBackoff(attempt++)
            await new Promise((r) => setTimeout(r, delay))
            continue
          }

          const recoveryConnectionOutcome = await runReplayRecoveryConnection({
            runId,
            ctrl,
            sseLoopSeq: mySeq,
            equivalent: eqNow,
            url,
          })
          if (recoveryConnectionOutcome === 'stop') return
          if (recoveryConnectionOutcome === 'retry') {
            const delay = nextBackoff(attempt++)
            await new Promise((r) => setTimeout(r, delay))
            continue
          }
        } else {
          real.sseState = 'open'
          await connectSse({
            url,
            headers: authHeaders(real.accessToken),
            lastEventId: real.lastEventId,
            signal: ctrl.signal,
            onMessage: (msg) => handleSseMessage({ msg, signal: ctrl.signal, runId, equivalent: eqNow }),
          })
        }

        await refreshRunStatus()
        // refreshRunStatus() owns stale-404 reset and may abort this loop,
        // advance its sequence, and clear runId. Do not overwrite the resulting
        // idle state with a reconnecting state from the obsolete connection.
        if (ctrl.signal.aborted || mySeq !== sseSeq || real.runId !== runId) return

        const st = String(real.runStatus?.state ?? '')
        if (st === 'stopped' || st === 'error') {
          real.sseState = 'closed'
          return
        }

        real.sseState = 'reconnecting'
      } catch (e: unknown) {
        if (ctrl.signal.aborted) return
        if (mySeq !== sseSeq || real.runId !== runId) return

        const msg = getErrorMessage(e)
        if (msg.includes('SSE HTTP 404') || msg.includes('HTTP 404')) {
          resetStaleRun({ clearError: true })
          return
        }

        real.lastError = msg

        // Unrecoverable replay cursor: clear it and rebuild authoritative state.
        if (msg.includes(' 410 ') || msg.includes('HTTP 410') || msg.includes('status 410')) {
          real.lastEventId = null
          replayGapRecoveryRunId = runId
          real.sseState = 'reconnecting'
          continue
        }

        if (ctrl.signal.aborted || mySeq !== sseSeq || real.runId !== runId) return
        real.sseState = 'reconnecting'
      }

      const delay = nextBackoff(attempt++)
      await new Promise((r) => setTimeout(r, delay))
    }
  }

  watch(
    () => [real.runId, real.accessToken, effectiveEq.value, real.apiBase] as const,
    () => {
      if (!isRealMode.value) {
        stopSse()
        return
      }
      runSseLoop()
    },
    { flush: 'post' },
  )

  async function refreshScenarios() {
    // No accessToken guard: anonymous visitors use cookie-auth (geo_sim_sid).
    await ensureAnonSessionOnce()
    real.loadingScenarios = true
    real.lastError = ''
    try {
      const res = await listScenarios({ apiBase: real.apiBase, accessToken: real.accessToken })
      real.scenarios = res.items ?? []
      ensureScenarioSelectionValid()
    } catch (e: unknown) {
      real.lastError = getErrorMessage(e)
    } finally {
      real.loadingScenarios = false
    }
  }

  async function startRun(startOpts?: { mode?: SimulatorMode; intensityPercent?: number; pauseImmediately?: boolean }) {
    if (!real.selectedScenarioId) return
    // No accessToken guard: anonymous visitors use cookie-auth (geo_sim_sid).

    await ensureAnonSessionOnce()

    const st = toLower(real.runStatus?.state)
    const isActive = !!real.runId && (st === 'running' || st === 'paused' || st === 'created' || st === 'stopping')
    if (real.runId && !isActive) {
      resetStaleRun({ clearError: true })
    }

    real.lastError = ''
    startRunInProgress = true
    try {
      stopSse()
      cleanupRealRunFxAndTimers()
      resetRunStats()

      try {
        const res = await createRun(
          { apiBase: real.apiBase, accessToken: real.accessToken },
          {
            scenario_id: real.selectedScenarioId,
            mode: startOpts?.mode ?? real.desiredMode,
            intensity_percent: startOpts?.intensityPercent ?? real.intensityPercent,
          },
        )
        real.runId = res.run_id
      } catch (e: unknown) {
        // If backend rejects creating a new run due to capacity (SIMULATOR_MAX_ACTIVE_RUNS),
        // attach to the already-active run instead of failing silently.
        if (e instanceof ApiError && e.status === 409) {
          const active = await getActiveRun({ apiBase: real.apiBase, accessToken: real.accessToken })
          const activeRunId = extractRunId(active)
          if (activeRunId) {
            real.runId = activeRunId
          } else {
            throw e
          }
        } else {
          throw e
        }
      }

      real.runStatus = null
      real.lastEventId = null
      replayGapRecoveryRunId = null
      real.artifacts = []

      if (startOpts?.pauseImmediately) {
        const delaysMs = [80, 160]
        let paused = false
        for (let i = 0; i < delaysMs.length + 1; i++) {
          try {
            real.runStatus = await pauseRun({ apiBase: real.apiBase, accessToken: real.accessToken }, real.runId)
            paused = true
            break
          } catch (e: unknown) {
            if (i === delaysMs.length) {
              // Best-effort: still proceed so UI can show status/errors.
              real.lastError = getErrorMessage(e)
              break
            }
            await new Promise((r) => setTimeout(r, delaysMs[i]!))
          }
        }
        if (!paused) {
          // No-op: higher-level logic may pause later.
        }
      }

      await refreshRunStatus()
      await refreshSnapshot()
      // SSE loop is long-lived; do not await it.
      void runSseLoop()
    } catch (e: unknown) {
      real.lastError = getErrorMessage(e)
    } finally {
      startRunInProgress = false
    }
  }

  async function pause() {
    // No accessToken guard: anonymous visitors use cookie-auth (geo_sim_sid).
    if (!real.runId) return
    try {
      real.runStatus = await pauseRun({ apiBase: real.apiBase, accessToken: real.accessToken }, real.runId)
    } catch (e: unknown) {
      real.lastError = getErrorMessage(e)
    }
  }

  async function resume() {
    // No accessToken guard: anonymous visitors use cookie-auth (geo_sim_sid).
    if (!real.runId) return
    try {
      real.runStatus = await resumeRun({ apiBase: real.apiBase, accessToken: real.accessToken }, real.runId)
    } catch (e: unknown) {
      real.lastError = getErrorMessage(e)
    }
  }

  async function stop() {
    // No accessToken guard: anonymous visitors use cookie-auth (geo_sim_sid).
    if (!real.runId) return
    try {
      teardownRefreshSnapshot()
      stopSse()
      cleanupRealRunFxAndTimers()
      real.runStatus = await stopRun({ apiBase: real.apiBase, accessToken: real.accessToken }, real.runId, {
        source: 'ui',
        reason: 'user_stop',
      })
      await refreshRunStatus()
    } catch (e: unknown) {
      const msg = getErrorMessage(e)

      // If the run is already gone server-side (e.g. TTL cleanup, double-stop, or restart),
      // treat stop as idempotent and reset local state instead of surfacing a hard error.
      if (msg.includes('HTTP 404') || msg.includes(' 404 ') || msg.includes('Not Found')) {
        resetStaleRun({ clearError: true })
        return
      }

      real.lastError = msg
      // The stop request was not confirmed. The backend run may still be live,
      // so restore observation instead of leaving the UI permanently detached.
      if (isRealMode.value && real.runId) void runSseLoop({ preserveLastError: true })
    }
  }

  /** Admin helper: attach UI to an arbitrary run_id (observer/control path). */
  async function attachToRun(runId: string) {
    const rid = runId.trim()
    if (!rid) return

    try {
      teardownRefreshSnapshot()
      stopSse()
      cleanupRealRunFxAndTimers()

      real.lastError = ''
      real.runId = rid
      real.runStatus = null
      real.lastEventId = null
      replayGapRecoveryRunId = null
      real.artifacts = []

      await refreshRunStatus()
      await refreshSnapshot()
      void runSseLoop()
    } catch (e: unknown) {
      real.lastError = getErrorMessage(e)
    }
  }

  /** Admin helper: stop a specific run_id (may be different from current run). */
  async function stopRunById(runId: string, opts?: { source?: string; reason?: string }) {
    const rid = runId.trim()
    if (!rid) return
    await stopRun({ apiBase: real.apiBase, accessToken: real.accessToken }, rid, {
      source: opts?.source ?? 'ui',
      reason: opts?.reason ?? 'admin_stop',
    })
  }

  async function applyIntensity() {
    // No accessToken guard: anonymous visitors use cookie-auth (geo_sim_sid).
    if (!real.runId) return
    try {
      real.runStatus = await setIntensity(
        { apiBase: real.apiBase, accessToken: real.accessToken },
        real.runId,
        real.intensityPercent,
      )
    } catch (e: unknown) {
      if (e instanceof ApiError && e.status === 404) {
        resetStaleRun({ clearError: true })
        return
      }
      real.lastError = getErrorMessage(e)
    }
  }

  async function refreshArtifacts() {
    // No accessToken guard: anonymous visitors use cookie-auth (geo_sim_sid).
    if (!real.runId) return
    real.artifactsLoading = true
    try {
      const idx = await listArtifacts({ apiBase: real.apiBase, accessToken: real.accessToken }, real.runId)
      real.artifacts = idx.items ?? []
    } catch (e: unknown) {
      real.lastError = getErrorMessage(e)
    } finally {
      real.artifactsLoading = false
    }
  }

  async function downloadArtifact(name: string) {
    // No accessToken guard: anonymous visitors use cookie-auth (geo_sim_sid).
    if (!real.runId) return
    try {
      const url = artifactDownloadUrl({ apiBase: real.apiBase, accessToken: real.accessToken }, real.runId, name)
      // credentials: 'include' sends cookies (geo_sim_sid) for anonymous-visitor auth.
      const res = await fetch(url, { headers: authHeaders(real.accessToken), credentials: 'include' })
      if (!res.ok) throw new Error(`Download failed: ${res.status} ${res.statusText}`)
      const blob = await res.blob()
      const blobUrl = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = blobUrl
      a.download = name
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(blobUrl)
    } catch (e: unknown) {
      real.lastError = getErrorMessage(e)
    }
  }

  // Preload scenarios on first real-mode mount.
  watch(
    () => isRealMode.value,
    (v) => {
      if (!v) {
        teardownRefreshSnapshot()
        stopSse()
        return
      }
      initialBootInProgress = true
      void (async () => {
        try {
          await ensureAnonSessionOnce()
          await refreshScenarios()

          ensureScenarioSelectionValid()

          // If no runId is persisted locally, try to discover an already-active run.
          // This makes cross-tab / cross-browser runs visible immediately in the UI
          // and prevents the confusing "Start → HTTP 409" experience.
          // No accessToken guard: anonymous visitors use cookie-auth (geo_sim_sid).
          if (!real.runId) {
            try {
              const active = await getActiveRun({ apiBase: real.apiBase, accessToken: real.accessToken })
              const activeRunId = String(active?.run_id ?? '').trim()
              if (activeRunId) {
                real.runId = activeRunId
              }
            } catch {
              // Best-effort: if discovery fails, keep normal preview flow.
            }
          }

          // If a runId is present (persisted or discovered), try to attach to it.
          // IMPORTANT: refreshRunStatus() may clear a stale runId (HTTP 404). In that case
          // we must fall through to the scenario preview flow below.
          // No accessToken guard: anonymous visitors use cookie-auth (geo_sim_sid).
          if (real.runId) {
            await refreshRunStatus()
            if (real.runId) {
              await refreshSnapshot()
              if (real.runId) {
                // SSE loop is long-lived; do not await it during boot.
                void runSseLoop()
              }
              return
            }
          }

          // No active run on mount: show scenario preview immediately.
          // No accessToken guard: anonymous visitors use cookie-auth (geo_sim_sid).
          if (real.selectedScenarioId && !real.runId) {
            await refreshSnapshot()
          }

          const shouldAutoStart =
            import.meta.env.DEV &&
            isLocalhost &&
            real.selectedScenarioId &&
            !real.runId &&
            new URLSearchParams(window.location.search).get('autostart') === '1'
          if (shouldAutoStart) await startRun()
        } finally {
          initialBootInProgress = false
        }
      })()
    },
    { immediate: true },
  )

  // When user selects a different scenario (and no run is active), reload scene so preview graph appears.
  watch(
    () => [real.selectedScenarioId, real.desiredMode, effectiveEq.value, real.accessToken, real.runId] as const,
    async ([scenarioId]) => {
      if (!isRealMode.value) return
      if (!scenarioId) return
      // No accessToken guard: anonymous visitors use cookie-auth (geo_sim_sid).

      // Block watcher from calling refreshSnapshot during startRun (it handles its own refresh).
      if (startRunInProgress) return

      // Block watcher during initial boot: the immediate watcher already calls
      // refreshSnapshot() after refreshScenarios() — this watcher would duplicate it.
      if (initialBootInProgress) return

      const st = toLower(real.runStatus?.state)
      const isActive =
        !!real.runId &&
        // Consider a run active only when we have a known status.
        // This allows scenario preview switching even when a stale runId exists but status cannot be fetched.
        !!real.runStatus &&
        (st === 'running' || st === 'paused' || st === 'created' || st === 'stopping')
      if (isActive) return

      await refreshSnapshot()
    },
    { flush: 'post' },
  )

  return {
    stopSse,
    resetStaleRun,
    refreshRunStatus,
    refreshSnapshot,
    refreshScenarios,
    startRun,
    pause,
    resume,
    stop,
    applyIntensity,
    refreshArtifacts,
    downloadArtifact,

    // Admin helpers
    attachToRun,
    stopRunById,
  }
}
