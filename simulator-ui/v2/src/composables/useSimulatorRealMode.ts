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
} from '../api/simulatorTypes'
import { connectSse, type SseParsedMessage } from '../api/sse'
import { normalizeSimulatorEvent } from '../api/normalizeSimulatorEvent'
import { ApiError, authHeaders } from '../api/http'
import { fetchEquivalentPrecisions } from '../api/equivalentsApi'
import { resetEquivalentPrecisions, setEquivalentPrecisions } from '../config/equivalentPrecision'
import { canonicalEquivalent, sameEquivalent } from '../config/equivalents'

import type { ClearingDoneEvent, EdgePatch, NodePatch, TxUpdatedEvent } from '../types'
import type { SimulatorAppState } from '../types/simulatorApp'
import { incCounter } from '../utils/counters'
import { isLiveRunState } from '../utils/status'
import { toLower } from '../utils/stringHelpers'
import {
  failureRunErrorLine,
  isGlobalLimitRefusal,
  NO_SCENARIO_LINE,
  runStatusErrorLine,
  startRunErrorLine,
  writeRunErrorLine,
  ZERO_INTENSITY_LINE,
  type RunErrorLine,
} from '../utils/runErrorClassification'
import { uiLocaleRef } from '../i18n/locale'
import {
  applyAcceptedRealEvent,
  createRealEventReplayOwner,
  executeRealEventIntents,
  type RealEventFeedItem,
  type RealEventIntent,
} from './realEventPipeline'

export type RealModeState = {
  apiBase: string
  accessToken: string
  loadingScenarios: boolean
  scenarios: ScenarioSummary[]
  selectedScenarioId: string
  intensityPercent: number
  runId: string | null
  runStatus: RunStatus | null
  sseState: string
  lastEventId: string | null
  lastError: string
  /**
   * 046 wave 1 (§15 Codex fix-delta): the VALUE of the error line - its cause (`RunErrorLine`: a dictionary key and its
   * data, or a text kept as it came) - whose text in the current language is `lastError`. Written with `lastError` by
   * `writeRunErrorLine`; a text written into `lastError` without it makes it null (the watcher below), so it never
   * describes another text. Optional: a state built without it has text-only lines.
   */
  lastErrorLine?: RunErrorLine | null
  /**
   * 046 wave 1: the line of the last start the global limit of runs refused (`isGlobalLimitRefusal`), null otherwise.
   * The top bar offers "Try again" while the line on screen is this same value (`startRetryOffered`, by identity).
   */
  retryableStartLine?: RunErrorLine | null
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
  applyNodeStatus: (id: string, status: string, vizColorKey: string) => void
  applyEdgeStatus: (source: string, target: string, status: string, vizAlphaKey: string) => void
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
])

const REPLAY_GAP_BUFFER_MAX_MESSAGES = 512

/** The reconnect ladder. Its top step is also how long a connection must have worked to reset the ladder. */
const SSE_BACKOFF_STEPS_MS = [1000, 2000, 5000, 10000, 20000] as const
const SSE_PROVEN_CONNECTION_MS = SSE_BACKOFF_STEPS_MS[SSE_BACKOFF_STEPS_MS.length - 1]!

function diagnosticEventTypeBucket(eventType: string | undefined): string {
  if (!eventType) return 'untyped'
  return DIAGNOSTIC_EVENT_TYPE_BUCKETS.has(eventType) ? eventType : 'other'
}

/**
 * The HTTP status of a failed request, or null when the failure carries none (network error, abort, timeout -
 * whose status is 0). Failures are classified by this, never by words in the message: the message carries the
 * answer's body (034 S8, F-034-D14).
 */
function httpStatusOf(e: unknown): number | null {
  return e instanceof ApiError && e.status > 0 ? e.status : null
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
  loadRecoveryScene?: (context: {
    runId: string
    equivalent: string
    isCurrent: () => boolean
  }) => Promise<boolean>

  // Live patch application + FX hooks
  realPatchApplier: PatchApplier
  // Backend-first tx labels (avoid frontend-derived balance deltas)
  pushTxAmountLabel: (nodeId: string, signedAmount: string, unit: string, opts?: { throttleMs?: number }) => void

  // When a payment's beam reaches the receiver: the one route time, shared with runRealTxFx (039 slice C, P-3)
  routeArrivalMs: (tx: TxUpdatedEvent) => number

  // Managed timers (must be cleaned up via cleanupRealRunFxAndTimers -> clearScheduledTimeouts)
  scheduleTimeout: (fn: () => void, delayMs: number, opts?: { critical?: boolean }) => void
  runRealTxFx: (tx: TxUpdatedEvent) => void
  runRealClearingDoneFx: (done: ClearingDoneEvent) => void

  // Optional: wake up render loop after snapshot/patch/FX updates.
  wakeUp?: () => void

  // Optional: notify on any accepted SSE event. No production caller since 043 S3 (its one caller kept the Demo UI
  // render loop awake); the tests of this module observe accepted events through it.
  onAnySseEvent?: () => void
  /** 049 S2 (decision 5): the line of the network's events of each accepted event (the history decides whether to keep it). */
  onFeedItem?: (item: RealEventFeedItem) => void
  // Optional visual-effects policy seam (e.g. reduced-motion integration).
  optionalFxEnabled?: () => boolean
}): {
  stopSse: () => void
  handleSceneContextChange: () => Promise<void>
  resetStaleRun: (opts?: { clearError?: boolean }) => void
  refreshRunStatus: () => Promise<void>
  refreshSnapshot: () => Promise<void>
  refreshScenarios: () => Promise<void>
  startRun: (opts?: { intensityPercent?: number; pauseImmediately?: boolean }) => Promise<void>
  pause: () => Promise<void>
  /** 043 S2 (decision 3): the pause a manual action asks for - see the function. */
  pauseForManualAction: () => Promise<void>
  resume: () => Promise<void>
  stop: () => Promise<void>
  applyIntensity: () => Promise<void>
  /**
   * Reads the run's file index. Resolves with the index array it put into `real.artifacts` when the read succeeded AND
   * still belongs to the attachment it was started for; `null` otherwise (no run, a failed read - its error is the
   * error line -, or a start/attach/reset of the index while it was in flight, whose answer is then dropped).
   */
  refreshArtifacts: () => Promise<ArtifactIndexItem[] | null>
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
    loadRecoveryScene,
    realPatchApplier,
    pushTxAmountLabel,
    routeArrivalMs,
    scheduleTimeout,
    runRealTxFx,
    runRealClearingDoneFx,
    wakeUp,
    onAnySseEvent,
    onFeedItem,
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

  // 048 S1 (§15 Codex fix-delta P2, 2026-10-18): the generation of the run's file index. Every reset (a start, an attach
  // - also of the same run again -, a stale run) begins a new one; a read started in an older one is not this
  // attachment's index: its answer is dropped, and it reports no success (the panel's "read" marker, `TopBar.vue`).
  let artifactIndexGeneration = 0
  function resetArtifactIndex(): void {
    artifactIndexGeneration += 1
    real.artifacts = []
  }
  let sceneContextRefreshSeq = 0
  let sceneContextRefresh: {
    seq: number
    runId: string | null
    accessToken: string
    equivalent: string
    apiBase: string
  } | null = null

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
  // 054-B-2: the attach by id owns its change of run (snapshot, then stream) like a start does.
  let attachRunInProgress = false

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

  function stopSse(stopOpts?: { preserveSceneRefresh?: boolean }) {
    if (!stopOpts?.preserveSceneRefresh) {
      sceneContextRefreshSeq += 1
      sceneContextRefresh = null
    }
    // Ensure no delayed refreshSnapshot() can fire after SSE stop/teardown.
    cancelPendingRefreshSnapshotDebounce()
    sseAbort?.abort()
    sseAbort = null
    real.sseState = 'idle'
  }

  function currentSceneContext() {
    return {
      runId: real.runId,
      accessToken: real.accessToken,
      equivalent: canonicalEquivalent(effectiveEq.value),
      apiBase: real.apiBase,
    }
  }

  function isSceneContextCurrent(context: ReturnType<typeof currentSceneContext>): boolean {
    const current = currentSceneContext()
    return current.runId === context.runId &&
      current.accessToken === context.accessToken &&
      sameEquivalent(current.equivalent, context.equivalent) &&
      current.apiBase === context.apiBase
  }

  async function replaceSceneSnapshotBeforeSse(): Promise<void> {
    const seq = ++sceneContextRefreshSeq
    const context = currentSceneContext()
    sceneContextRefresh = { seq, ...context }
    replayGapRecoveryRunId = null
    teardownRefreshSnapshot()
    stopSse({ preserveSceneRefresh: true })
    try {
      await loadScene()
    } catch (error: unknown) {
      if (sceneContextRefreshSeq === seq && isSceneContextCurrent(context)) {
        writeRunErrorLine(real, failureRunErrorLine(error))
      }
    }

    if (sceneContextRefreshSeq !== seq || !isSceneContextCurrent(context)) return
    sceneContextRefresh = null
    if (isRealMode.value && real.runId) void runSseLoop()
  }

  async function handleSceneContextChange(): Promise<void> {
    if (!isRealMode.value) {
      await loadScene()
      return
    }
    await replaceSceneSnapshotBeforeSse()
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
    resetArtifactIndex()
    cleanupRealRunFxAndTimers()
    resetRunStats()
    stopSse()

    replayOwner.reset()
    replayGapRecoveryRunId = null

    if (resetOpts?.clearError ?? true) {
      real.lastError = ''
      state.error = ''
      state.errorLine = null
    }

    // Keep selection usable after restart (do not auto-start).
    ensureScenarioSelectionValid()
  }

  /**
   * 054-B-4: the run's control commands (pause, resume, tempo, stop, the start's own pause) are answered in the order
   * they were asked. An answer to a command older than the latest one - or to a command for another run - is not
   * applied, neither its status nor its failure: a late Resume answer would otherwise turn a pause confirmed since
   * then back into `running`, and the server publishes no status of a paused run that would correct it. The latest
   * command is set when a command is SENT, so a later command that fails does not let an older answer through.
   */
  let runControlSeq = 0
  function issueRunControl(): () => boolean {
    const seq = ++runControlSeq
    const runId = real.runId
    return () => runControlSeq === seq && real.runId === runId
  }

  type RunStatusRefreshOutcome = 'current' | 'stale' | 'transient' | 'superseded'

  async function refreshRunStatusForCurrentContext(o: { opening?: boolean } = {}): Promise<RunStatusRefreshOutcome> {
    // No accessToken guard: anonymous visitors use cookie-auth (geo_sim_sid).
    const runId = real.runId
    if (!runId) return 'superseded'
    const mySeq = ++refreshRunStatusSeq
    const isCurrent = () => refreshRunStatusSeq === mySeq && real.runId === runId
    try {
      const st = await getRun({ apiBase: real.apiBase, accessToken: real.accessToken }, runId)
      if (!isCurrent()) return 'superseded'
      real.runStatus = st
      writeRunErrorLine(real, runStatusErrorLine(st, isUserFacingRunError))
      return 'current'
    } catch (e: unknown) {
      if (!isCurrent()) return 'superseded'
      // 048 S3 (044 closing review, advisor 2026-10-19; §15 Codex P2, orchestrator 2026-10-19): during the OPENING, a 403 on
      // the restored `geo.sim.v2.runId` is cleared like a 404 - the anonymous cookie was lost or expired, the saved run is
      // another session's - so the opening goes on as without one (the preview, then the autostart starts a run); a kept
      // id with no status answered `unknown` and the visitor was stuck until the storage was cleared. ONLY then: mid-session
      // a 403 is not proof that the run is gone - the backend answers every 403 with one code (E006, no details: not the
      // run's owner, an inactive participant, a wrong admin token), and matching its English message is not a rule (§9) -
      // so it stays a failure on the run's line and the run is kept. A 404 is cleared everywhere, as before.
      if (e instanceof ApiError && (e.status === 404 || (e.status === 403 && o.opening === true))) {
        resetStaleRun({ clearError: true })
        return 'stale'
      }
      writeRunErrorLine(real, failureRunErrorLine(e))
      return 'transient'
    }
  }

  async function refreshRunStatus(o: { opening?: boolean } = {}): Promise<void> {
    await refreshRunStatusForCurrentContext(o)
  }

  function startSnapshotRefresh(opts?: {
    debounce?: boolean
    load?: () => Promise<boolean | void>
    isContextCurrent?: () => boolean
  }): Promise<SnapshotRefreshOutcome> {
    const mySeq = ++refreshSnapshotSeq
    const runIdAtStart = real.runId

    const isContextStillValid = () => {
      if (!isRealMode.value) return false
      // No accessToken guard: anonymous visitors use cookie-auth (geo_sim_sid).

      // Abort if a teardown/stop happened or a newer refresh started.
      if (refreshSnapshotSeq !== mySeq) return false

      // Prevent a delayed refresh from applying to a different run context.
      if (real.runId !== runIdAtStart) return false

      if (opts?.isContextCurrent && !opts.isContextCurrent()) return false

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

    const promise: Promise<SnapshotRefreshOutcome> = Promise.resolve().then(async (): Promise<SnapshotRefreshOutcome> => {
      try {
        if (!isContextStillValid()) return 'superseded'

        try {
          const applied = await (opts?.load ? opts.load() : loadScene())
          if (applied === false) return 'superseded'
        } catch (e: unknown) {
          if (!isContextStillValid()) return 'superseded'
          if (httpStatusOf(e) === 404) {
            resetStaleRun({ clearError: true })
            return 'stale'
          }
          // 048 S3 (decision 3): a failure the scene loader already shows (`state.error`, the overlay over the canvas - a
          // recovery load rethrows it) is not written on the run's error line too: one failure, one place.
          if (!state.error) writeRunErrorLine(real, failureRunErrorLine(e))
          return 'transient'
        }

        // Context might have changed while loadScene() was in-flight.
        if (!isContextStillValid()) return 'superseded'

        // Snapshot change should be visible immediately even if deep idle stopped scheduling.
        wakeUp?.()

        // The scene loader stores its failure in `state.error`; the overlay over the canvas shows it (048 S3: its ONE place -
        // until S3 it was copied to the run's error line as well, the same failure twice).
        // A text is never read as "the run is gone" (034 S8, F-034-D14): the run's own 404 is handled by its status
        // where the error object still exists - in the scene's run-snapshot read and in the catch above.
        if (isRealMode.value && state.error) return 'transient'
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

  async function refreshSnapshotForReplayRecovery(input: {
    runId: string
    equivalent: string
    isCurrent: () => boolean
  }): Promise<SnapshotRefreshOutcome> {
    // Start a new authoritative load immediately. SceneState owns latest-request
    // application, so this invalidates both tracked and direct watcher loads.
    cancelPendingRefreshSnapshotDebounce()
    if (!isRealMode.value || real.runId !== input.runId || !input.isCurrent()) return 'superseded'
    if (!loadRecoveryScene) {
      writeRunErrorLine(real, failureRunErrorLine(new Error('Strict replay recovery snapshot loader is not configured'), { client: 'stream' }))
      return 'transient'
    }
    return await startSnapshotRefresh({
      debounce: false,
      isContextCurrent: input.isCurrent,
      load: () => loadRecoveryScene({
        runId: input.runId,
        equivalent: input.equivalent,
        isCurrent: input.isCurrent,
      }),
    })
  }

  /**
   * 046 wave 1 (§15 Codex P2 and its fix-delta): the error line in the language of the interface. The line is a value
   * (`lastErrorLine`); its text (`lastError`) is rendered again on a switch. A text written into `lastError` alone - by
   * any code that has only a text - is a line of its own: its value is forgotten at once (sync), so a stale value can
   * never re-render over it. `writeRunErrorLine` writes the text first and the value after, so its own write survives.
   */
  watch(
    () => real.lastError,
    () => {
      real.lastErrorLine = null
    },
    { flush: 'sync' },
  )
  watch(uiLocaleRef, (locale) => {
    const line = real.lastErrorLine
    if (line) writeRunErrorLine(real, line, locale)
  })

  // 048 S3 (decision 3, «одна форма ошибки»): a scene-load failure has ONE place - the overlay over the canvas, in the
  // place of the picture that did not come (`SimulatorAppRoot.vue`). Until S3 it was copied to this error line as well
  // (the "HUD copy", 034 S8 review C2, and its put-aside-and-bring-back of the line it displaced, 046 wave 1): the same
  // failure twice. The copy and its bookkeeping are gone; the line keeps the run's own failures.

  function nextBackoff(attempt: number): number {
    const steps = SSE_BACKOFF_STEPS_MS
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
    if (!sameEquivalent(effectiveEq.value, equivalent)) return
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
      getSceneEquivalent: () => state.snapshot?.equivalent ?? null,
      optionalFxEnabled,
      onAnySseEvent,
      onFeedItem,
      refreshSnapshot: () => void refreshSnapshot(),
      wakeUp,
      runRealTxFx,
      runRealClearingDoneFx,
      pushTxAmountLabel,
      routeArrivalMs,
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

  function replayBootstrapFrame(
    msg: SseParsedMessage,
    runId: string,
    equivalent: string,
  ): { terminal: boolean } | null {
    if (!msg.data) return null
    try {
      const payload: unknown = JSON.parse(msg.data)
      const normalized = normalizeSimulatorEvent(payload)
      if (normalized.status !== 'accepted' || normalized.event.type !== 'run_status') return null
      const admission = replayOwner.admit({
        event: normalized.event,
        frameId: msg.id,
        connectionRunId: runId,
        activeRunId: real.runId,
        connectionEquivalent: equivalent,
        currentCursor: real.lastEventId,
      })
      if (admission.status !== 'accepted') return null
      const state = String(normalized.event.state || '').trim().toLowerCase()
      return { terminal: state === 'stopped' || state === 'error' }
    } catch {
      return null
    }
  }

  type SseConnectionOutcome = { status: 'ended' } | { status: 'error'; error: unknown }
  type ReplayRecoveryConnectionOutcome = 'ended' | 'terminal' | 'retry' | 'stop'

  async function runReplayRecoveryConnection(input: {
    runId: string
    ctrl: AbortController
    sseLoopSeq: number
    equivalent: string
    url: string
  }): Promise<ReplayRecoveryConnectionOutcome> {
    const { runId, ctrl, sseLoopSeq, equivalent, url } = input
    const connectionCtrl = new AbortController()
    const isCurrentContext = () =>
      !ctrl.signal.aborted &&
      !connectionCtrl.signal.aborted &&
      sseLoopSeq === sseSeq &&
      isRealMode.value &&
      real.runId === runId &&
      sameEquivalent(effectiveEq.value, equivalent)

    if (!isCurrentContext()) return 'stop'

    const abortConnection = () => connectionCtrl.abort()
    ctrl.signal.addEventListener('abort', abortConnection, { once: true })

    const bufferedMessages: SseParsedMessage[] = []
    let buffering = true
    let bufferOverflowed = false
    let terminalBootstrap = false
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
            writeRunErrorLine(real, failureRunErrorLine(new Error(`SSE replay recovery buffer exceeded ${REPLAY_GAP_BUFFER_MAX_MESSAGES} messages`), { client: 'stream' }))
            resolveBufferOverflow()
            connectionCtrl.abort()
          }
          return
        }

        bufferedMessages.push(msg)
        const bootstrap = replayBootstrapFrame(msg, runId, equivalent)
        if (!firstBootstrapResolved && bootstrap) {
          firstBootstrapResolved = true
          terminalBootstrap = bootstrap.terminal
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
        writeRunErrorLine(real, failureRunErrorLine(new Error('SSE replay recovery connection ended before its bootstrap frame'), { client: 'stream' }))
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

      const snapshotPromise = refreshSnapshotForReplayRecovery({
        runId,
        equivalent,
        isCurrent: isCurrentContext,
      })
      const snapshotOrConnection = await Promise.race([
        snapshotPromise.then((outcome) => ({ kind: 'snapshot' as const, outcome })),
        bufferOverflow.then(() => ({ kind: 'overflow' as const })),
        connectionOutcome.then((outcome) => ({ kind: 'connection' as const, outcome })),
      ])

      if (snapshotOrConnection.kind === 'overflow') {
        await settleAfterAbort()
        return 'retry'
      }
      let snapshotOutcome: SnapshotRefreshOutcome
      if (snapshotOrConnection.kind === 'connection') {
        if (!isCurrentContext()) return 'stop'
        if (snapshotOrConnection.outcome.status === 'error') throw snapshotOrConnection.outcome.error
        if (!terminalBootstrap) {
          writeRunErrorLine(real, failureRunErrorLine(new Error('SSE replay recovery connection ended before the authoritative snapshot completed'), { client: 'stream' }))
          await settleAfterAbort()
          return 'retry'
        }
        snapshotOutcome = await snapshotPromise
      } else {
        snapshotOutcome = snapshotOrConnection.outcome
      }
      if (snapshotOutcome === 'transient') {
        await settleAfterAbort()
        return 'retry'
      }
      if (snapshotOutcome !== 'current' || !isCurrentContext()) {
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
      real.sseState = terminalBootstrap ? 'closed' : 'open'

      if (terminalBootstrap) {
        await settleAfterAbort()
        return 'terminal'
      }

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
    const state = String(real.runStatus?.state || '').trim().toLowerCase()
    if (state === 'stopped' || state === 'error') {
      replayGapRecoveryRunId = null
      real.sseState = 'closed'
      return 'stop'
    }
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
          if (recoveryConnectionOutcome === 'terminal') return
          if (recoveryConnectionOutcome === 'retry') {
            const delay = nextBackoff(attempt++)
            await new Promise((r) => setTimeout(r, delay))
            continue
          }
        } else {
          // 048 S1 (§15 Codex fix-delta P2, 2026-10-18): the stream is `open` only on EVIDENCE of a connection - the
          // first SSE message it delivers (every answered connection delivers one at once: the bootstrap `run_status`, or
          // replayed data events before it; see below). Until then the state stays what the loop set: `connecting` on the
          // first attempt, `reconnecting` on a later one. It was set `open` before the request, so a reconnect stalled in
          // `fetch()` or in `reader.read()` (`api/sse.ts`) read as a healthy stream, and failing attempts flickered
          // green between two "no connection" states of the network (`TopBar.vue` `streamLost`). The first message, not
          // the response headers: it covers a stall after the headers too. Keep-alive comments are not messages; a run's
          // stream always starts with its bootstrap, so they are not needed as evidence.
          // The back-off counts attempts that failed in a row, and only a connection that PROVED itself resets it
          // (034 S8): it delivered a frame and stayed open for the top step of the ladder, however it then ended.
          // A frame alone proves nothing: every answered connection delivers one at once (the bootstrap `run_status`,
          // or replayed data events before it), also a connection that is then closed straight away; resetting on
          // it reconnected once a second under overload.
          const openedAtMs = Date.now()
          let deliveredFrame = false
          try {
            await connectSse({
              url,
              headers: authHeaders(real.accessToken),
              lastEventId: real.lastEventId,
              signal: ctrl.signal,
              onMessage: (msg) => {
                if (!deliveredFrame && !ctrl.signal.aborted && mySeq === sseSeq && real.runId === runId) real.sseState = 'open'
                deliveredFrame = true
                handleSseMessage({ msg, signal: ctrl.signal, runId, equivalent: eqNow })
              },
            })
          } finally {
            if (deliveredFrame && Date.now() - openedAtMs >= SSE_PROVEN_CONNECTION_MS) attempt = 0
          }
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

        const status = httpStatusOf(e)
        if (status === 404) {
          resetStaleRun({ clearError: true })
          return
        }

        writeRunErrorLine(real, failureRunErrorLine(e, { client: 'stream' }))

        // Unrecoverable replay cursor: clear it and rebuild authoritative state.
        if (status === 410) {
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
    ([runId], previous) => {
      if (sceneContextRefresh) {
        if (!isSceneContextCurrent(sceneContextRefresh)) void replaceSceneSnapshotBeforeSse()
        return
      }
      if (replayGapRecoveryRunId !== null) {
        void replaceSceneSnapshotBeforeSse()
        return
      }
      if (!isRealMode.value) {
        stopSse()
        return
      }
      // 054-B-2 (§8, ws-protocol §2.1): a NEW run gets its snapshot before its stream. Opening the stream here, the
      // moment the id changed, applied its events to the old scene and moved the cursor past them before the snapshot
      // replaced that scene - the reconnect did not replay them. The owners of a change of run - a start (incl. its
      // attach on 409), the attach by id, the opening's discovery - read the status and the snapshot and open the
      // stream themselves, after the snapshot; they are the only code that sets a run id (a reset sets none).
      if (runId && runId !== previous?.[0] && (startRunInProgress || attachRunInProgress || initialBootInProgress)) return
      runSseLoop()
    },
    { flush: 'post' },
  )

  /**
   * Generation of the outstanding equivalents-catalogue load (`T1211`). The precision
   * registry is global and shared with demo mode, so a response may only be installed
   * while it is still the newest load anyone asked for.
   *
   * A generation stands in for the whole connection context because every change to that
   * context bumps it: leaving real mode invalidates the outstanding load, and a change of
   * `apiBase` or `accessToken` while real mode stays on starts a fresh one (see the
   * connection-context watcher). An older generation is therefore, by construction, an
   * answer to a question nobody is asking any more.
   */
  let equivalentPrecisionsSeq = 0

  function invalidateEquivalentPrecisionsLoad() {
    equivalentPrecisionsSeq += 1
  }

  /**
   * Loads `Equivalent.precision` for every active equivalent (012 / `F-012-4`).
   *
   * Best-effort by design: an anonymous cookie-auth visitor can read neither
   * `GET /equivalents` (participant JWT) nor `GET /admin/equivalents` (admin token), and
   * that is not an error worth putting in front of the operator — the shipped fixture
   * precisions stay in force. A failure here must never keep real mode from booting.
   *
   * A failure DROPS the catalogue rather than keeping it. This loader runs in exactly two
   * places — real-mode boot (:1318) and the connection-context watcher (:1397) — so on a
   * failure the registry never holds a still-valid catalogue for the backend now being
   * addressed: it is either empty (boot) or the PREVIOUS backend's (context changed).
   * Keeping the latter is not a stale-but-plausible value, it is a wrong scale:
   * `NodeCardOverlay.vue:108` turns `net_balance_atoms` into major units through it, so
   * carrying backend A's precision into backend B's data misplaces the decimal point by
   * `10^(A-B)`. Dropping it lands in the state an anonymous visitor is already in and that
   * `equivalentPrecision` documents as normal — layer 2, the shipped fixture value.
   *
   * The cost is narrow and accepted: when only the credential changed and the new read
   * fails, a catalogue that did belong to this same backend is dropped too. That degrades
   * to a documented fallback, whereas the alternative invents knowledge about a backend
   * whose catalogue was never read.
   */
  async function refreshEquivalentPrecisions() {
    const seq = ++equivalentPrecisionsSeq
    // Pinned at issue time: `real.*` can already describe a different backend by the time
    // the response lands, and the request must not be a mix of the two.
    const request = { apiBase: real.apiBase, accessToken: real.accessToken }
    try {
      const rows = await fetchEquivalentPrecisions(request)
      if (seq !== equivalentPrecisionsSeq) return
      setEquivalentPrecisions(rows)
    } catch {
      // Same generation check as the success path: a rejection from an abandoned context
      // must not destroy a catalogue a newer load already installed.
      if (seq !== equivalentPrecisionsSeq) return
      resetEquivalentPrecisions()
    }
  }

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
      writeRunErrorLine(real, failureRunErrorLine(e))
    } finally {
      real.loadingScenarios = false
    }
  }

  /**
   * 043 S2 (external review 2026-10-17): admission is serialised. A start asked for while another is in flight JOINS it
   * (returns the same promise) and never issues a second create: the callers - the opening's autostart, "Try again" of
   * the error line and Start under "Подробнее" (the administrator's since 048 S2; the Demo UI's paused bootstrap went
   * with Demo UI, 043 S3) - could otherwise put two runs side by side
   * (with a global limit >= 2) or a redundant create and a 409 recovery (with the default 1). The joining call's own
   * options are not applied: the run in flight is the one being started.
   */
  let startRunInFlight: Promise<void> | null = null
  function startRun(startOpts?: { intensityPercent?: number; pauseImmediately?: boolean }): Promise<void> {
    if (startRunInFlight) return startRunInFlight
    const p = startRunOnce(startOpts).finally(() => {
      if (startRunInFlight === p) startRunInFlight = null
    })
    startRunInFlight = p
    return p
  }

  async function startRunOnce(startOpts?: { intensityPercent?: number; pauseImmediately?: boolean }) {
    if (!real.selectedScenarioId) return
    const intensityPercent = startOpts?.intensityPercent ?? real.intensityPercent
    // 044 stage 2 (BACKLOG 043-19): a tempo below 1 starts no silent run; the hint says why.
    if (!(intensityPercent >= 1)) {
      writeRunErrorLine(real, ZERO_INTENSITY_LINE)
      return
    }
    // No accessToken guard: anonymous visitors use cookie-auth (geo_sim_sid).

    await ensureAnonSessionOnce()

    if (real.runId && !isLiveRunState(real.runStatus?.state)) {
      resetStaleRun({ clearError: true })
    }

    real.lastError = ''
    real.retryableStartLine = null
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
            // The only run mode there is (042 S2): no stored or UI-chosen mode feeds this request.
            mode: 'real',
            intensity_percent: intensityPercent,
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
      resetArtifactIndex()

      if (startOpts?.pauseImmediately) {
        const delaysMs = [80, 160]
        let paused = false
        for (let i = 0; i < delaysMs.length + 1; i++) {
          try {
            const isLatestControl = issueRunControl()
            const st = await pauseRun({ apiBase: real.apiBase, accessToken: real.accessToken }, real.runId)
            if (isLatestControl()) real.runStatus = st
            paused = true
            break
          } catch (e: unknown) {
            if (i === delaysMs.length) {
              // Best-effort: still proceed so UI can show status/errors.
              writeRunErrorLine(real, failureRunErrorLine(e))
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
      // SSE loop is long-lived; do not await it. `lastError` was reset at the top of this start.
      void runSseLoop({ preserveLastError: true })
    } catch (e: unknown) {
      // The one place a failed start is written to the error line (every start goes through here).
      const line = startRunErrorLine(e) ?? failureRunErrorLine(e)
      writeRunErrorLine(real, line)
      real.retryableStartLine = isGlobalLimitRefusal(e) ? line : null
    } finally {
      startRunInProgress = false
      // A start that ended without a run, or with a run that has ended, leaves no pause to wait for (043 S2). A run
      // whose status is not known yet (a transient failure of the first read; review C5) keeps the pending pause: it
      // is applied the moment a later read or the stream says `running`.
      const st = toLower(real.runStatus?.state)
      if (!real.runId || st === 'stopped' || st === 'error' || st === 'paused') manualPausePending = false
    }
  }

  async function pause() {
    // No accessToken guard: anonymous visitors use cookie-auth (geo_sim_sid).
    if (!real.runId) return
    const isLatestControl = issueRunControl()
    try {
      const st = await pauseRun({ apiBase: real.apiBase, accessToken: real.accessToken }, real.runId)
      if (isLatestControl()) real.runStatus = st
    } catch (e: unknown) {
      if (isLatestControl()) writeRunErrorLine(real, failureRunErrorLine(e))
    }
  }

  /**
   * 043 S2 (decision 3): a manual action pauses the automatic payments. A `running` run is paused now; a run that is
   * still being started (`startRun` in flight, or `created`) is paused the moment it reads `running`, so an action taken
   * during the opening is not run over by the run that opening starts. Any other state is left alone: a paused run is
   * already quiet, an ended one has nothing to pause. Only `resume` (the "Поток" button) cancels a pending pause.
   */
  let manualPausePending = false
  /** One pause request at a time: two acts in one tick (a flow started with both ends chosen) ask once. */
  let manualPauseInFlight: Promise<void> | null = null
  /** The one way a manual action's pause is asked - now or deferred (044 stage 2, Codex §15 P3: the deferred one too). */
  function pauseOnce(): Promise<void> {
    if (manualPauseInFlight) return manualPauseInFlight
    const p = pause().finally(() => {
      if (manualPauseInFlight === p) manualPauseInFlight = null
    })
    manualPauseInFlight = p
    return p
  }
  async function pauseForManualAction() {
    const st = toLower(real.runStatus?.state)
    if (st === 'running') {
      manualPausePending = false
      return pauseOnce()
    }
    // 044 stage 2 (BACKLOG 043-24): a run is held but its state is not known yet (a failed or pending status read of a
    // found run) - the pause waits for that state too, instead of being dropped.
    if (startRunInProgress || st === 'created' || (!st && !!real.runId)) manualPausePending = true
  }
  watch(
    () => toLower(real.runStatus?.state),
    (st) => {
      if (!manualPausePending) return
      if (st === 'running') {
        manualPausePending = false
        void pauseOnce()
      } else if (st && st !== 'created') {
        manualPausePending = false
      }
    },
  )

  async function resume() {
    manualPausePending = false
    // No accessToken guard: anonymous visitors use cookie-auth (geo_sim_sid).
    if (!real.runId) return
    const isLatestControl = issueRunControl()
    try {
      const st = await resumeRun({ apiBase: real.apiBase, accessToken: real.accessToken }, real.runId)
      if (isLatestControl()) real.runStatus = st
    } catch (e: unknown) {
      if (isLatestControl()) writeRunErrorLine(real, failureRunErrorLine(e))
    }
  }

  async function stop() {
    // No accessToken guard: anonymous visitors use cookie-auth (geo_sim_sid).
    if (!real.runId) return
    try {
      teardownRefreshSnapshot()
      stopSse()
      cleanupRealRunFxAndTimers()
      const isLatestControl = issueRunControl()
      const st = await stopRun({ apiBase: real.apiBase, accessToken: real.accessToken }, real.runId, {
        source: 'ui',
        reason: 'user_stop',
      })
      if (isLatestControl()) real.runStatus = st
      await refreshRunStatus()
    } catch (e: unknown) {
      // If the run is already gone server-side (e.g. TTL cleanup, double-stop, or restart),
      // treat stop as idempotent and reset local state instead of surfacing a hard error.
      if (httpStatusOf(e) === 404) {
        resetStaleRun({ clearError: true })
        return
      }

      writeRunErrorLine(real, failureRunErrorLine(e))
      // The stop request was not confirmed. The backend run may still be live,
      // so restore observation instead of leaving the UI permanently detached.
      if (isRealMode.value && real.runId) void runSseLoop({ preserveLastError: true })
    }
  }

  /** Admin helper: attach UI to an arbitrary run_id (observer/control path). */
  async function attachToRun(runId: string) {
    const rid = runId.trim()
    if (!rid) return

    attachRunInProgress = true
    try {
      teardownRefreshSnapshot()
      stopSse()
      cleanupRealRunFxAndTimers()

      real.lastError = ''
      real.runId = rid
      real.runStatus = null
      real.lastEventId = null
      replayGapRecoveryRunId = null
      resetArtifactIndex()

      await refreshRunStatus()
      await refreshSnapshot()
      // `lastError` was reset at the top of this attach, so what it holds now came from the reads above.
      void runSseLoop({ preserveLastError: true })
    } catch (e: unknown) {
      writeRunErrorLine(real, failureRunErrorLine(e))
    } finally {
      attachRunInProgress = false
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
    // 044 stage 2 (BACKLOG 043-19): 0 is not applied - the run would go on making no payment; the pause does that.
    if (!(real.intensityPercent >= 1)) {
      writeRunErrorLine(real, ZERO_INTENSITY_LINE)
      return
    }
    const isLatestControl = issueRunControl()
    try {
      const st = await setIntensity(
        { apiBase: real.apiBase, accessToken: real.accessToken },
        real.runId,
        real.intensityPercent,
      )
      if (isLatestControl()) real.runStatus = st
    } catch (e: unknown) {
      if (e instanceof ApiError && e.status === 404) {
        resetStaleRun({ clearError: true })
        return
      }
      if (isLatestControl()) writeRunErrorLine(real, failureRunErrorLine(e))
    }
  }

  async function refreshArtifacts(): Promise<ArtifactIndexItem[] | null> {
    // No accessToken guard: anonymous visitors use cookie-auth (geo_sim_sid).
    if (!real.runId) return null
    const runId = real.runId
    const generation = artifactIndexGeneration
    real.artifactsLoading = true
    try {
      const idx = await listArtifacts({ apiBase: real.apiBase, accessToken: real.accessToken }, runId)
      // 048 S1: an answer for an index that was reset (or another run attached) while it was in flight is not shown.
      if (generation !== artifactIndexGeneration || real.runId !== runId) return null
      const items = idx.items ?? []
      real.artifacts = items
      return items
    } catch (e: unknown) {
      writeRunErrorLine(real, failureRunErrorLine(e))
      return null
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
      writeRunErrorLine(real, failureRunErrorLine(e))
    }
  }

  // Preload scenarios on first real-mode mount.
  watch(
    () => isRealMode.value,
    (v) => {
      if (!v) {
        teardownRefreshSnapshot()
        stopSse()
        // Demo mode must not keep formatting money at a precision only the backend knew.
        invalidateEquivalentPrecisionsLoad()
        resetEquivalentPrecisions()
        return
      }
      initialBootInProgress = true
      void (async () => {
        try {
          await ensureAnonSessionOnce()
          // Deliberately not awaited: the catalogue is not needed to boot, and the boot
          // sequence's ordering is characterized by tests. The precision registry is
          // reactive, so a card already on screen re-renders when the answer arrives.
          void refreshEquivalentPrecisions()
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
            // 048 S3: the opening's read of the restored run - the one read where a 403 clears the saved id.
            await refreshRunStatus({ opening: true })
            if (real.runId) {
              await refreshSnapshot()
              // 043 S2 (review C6): an ENDED run (`stopped`, `error`) gets no event stream - there is nothing live to
              // follow, and on the one screen the opening replaces it (`useInteractAutoBootstrapRun.ts`). A live run, and
              // a run whose status could not be read (the stream is how it shows up again), still get it.
              const ended = !!real.runStatus && !isLiveRunState(real.runStatus.state)
              if (real.runId && !ended) {
                // SSE loop is long-lived; do not await it during boot.
                // The error of the status/snapshot reads above is not stale: keep it on screen.
                void runSseLoop({ preserveLastError: true })
              }
              return
            }
          }

          // No active run on mount: show scenario preview immediately.
          // No accessToken guard: anonymous visitors use cookie-auth (geo_sim_sid).
          if (real.selectedScenarioId && !real.runId) {
            await refreshSnapshot()
          }
          // 043 S2 (review B5/C4): no run and no scenario to show (the catalogue is empty, or its read failed) - nothing
          // will load a scene, so the opening ends here: the loading is over and the error line says why (a failed read
          // already holds its error).
          if (!real.runId && !real.selectedScenarioId) {
            state.loading = false
            if (!real.lastError) writeRunErrorLine(real, NO_SCENARIO_LINE)
          }
          // 043 S2 (external review 2026-10-17): the dev-only `?autostart=1` start that stood here is gone - the opening
          // of the one screen (`useInteractAutoBootstrapRun.ts`) is the single owner of a start nobody pressed.
        } finally {
          initialBootInProgress = false
        }
      })()
    },
    { immediate: true },
  )

  /**
   * The connection context is editable *while real mode stays on* (`T1211`): the operator
   * pastes an admin token after an anonymous start, or repoints `apiBase` at another
   * backend. The boot watcher above never fires again in either case, so without this the
   * catalogue read is a one-shot — a token that arrives late is never retried, and a
   * catalogue read from the previous backend keeps formatting amounts fetched from the new
   * one.
   *
   * Only the catalogue is re-read here; the rest of the boot sequence is deliberately not
   * repeated on a keystroke. The stale-response guard inside `refreshEquivalentPrecisions`
   * is what makes this safe — the re-read routinely overlaps a load that is still open.
   */
  watch(
    () => [isRealMode.value, real.apiBase, real.accessToken] as const,
    ([realMode, apiBase, accessToken], previous) => {
      if (!realMode) return
      // Entering real mode is the boot watcher's job; re-reading here too would double the
      // request on every mount.
      const [prevRealMode, prevApiBase, prevAccessToken] = previous ?? [realMode, apiBase, accessToken]
      if (realMode !== prevRealMode) return
      if (apiBase === prevApiBase && accessToken === prevAccessToken) return
      void refreshEquivalentPrecisions()
    },
  )

  // When user selects a different scenario (and no run is active), reload scene so preview graph appears.
  watch(
    () => [real.selectedScenarioId, effectiveEq.value, real.accessToken, real.runId] as const,
    async ([scenarioId]) => {
      if (!isRealMode.value) return
      if (!scenarioId) return
      // No accessToken guard: anonymous visitors use cookie-auth (geo_sim_sid).

      // Block watcher from calling refreshSnapshot during startRun (it handles its own refresh).
      if (startRunInProgress) return

      // Block watcher during initial boot: the immediate watcher already calls
      // refreshSnapshot() after refreshScenarios() — this watcher would duplicate it.
      if (initialBootInProgress) return

      const isActive =
        !!real.runId &&
        // Consider a run active only when we have a known status.
        // This allows scenario preview switching even when a stale runId exists but status cannot be fetched.
        !!real.runStatus &&
        isLiveRunState(real.runStatus.state)
      if (isActive) return

      await refreshSnapshot()
    },
    { flush: 'post' },
  )

  return {
    stopSse,
    handleSceneContextChange,
    resetStaleRun,
    refreshRunStatus,
    refreshSnapshot,
    refreshScenarios,
    startRun,
    pause,
    pauseForManualAction,
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
