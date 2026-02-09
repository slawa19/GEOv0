import { watch, type ComputedRef } from 'vue'

import {
  artifactDownloadUrl,
  createRun,
  getRun,
  listArtifacts,
  listScenarios,
  pauseRun,
  resumeRun,
  setIntensity,
  stopRun,
} from '../api/simulatorApi'
import type {
  ArtifactIndexItem,
  RunStatus,
  RunStatusEvent,
  ScenarioSummary,
  SimulatorMode,
  TxFailedEvent,
} from '../api/simulatorTypes'
import { connectSse } from '../api/sse'
import { normalizeSimulatorEvent } from '../api/normalizeSimulatorEvent'
import { ApiError, authHeaders } from '../api/http'

import type { ClearingDoneEvent, ClearingPlanEvent, EdgePatch, NodePatch, TxUpdatedEvent } from '../types'
import type { SimulatorAppState } from '../types/simulatorApp'

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
  runRealClearingPlanFx: (plan: ClearingPlanEvent) => void
  runRealClearingDoneFx: (plan: ClearingPlanEvent | undefined, done: ClearingDoneEvent) => void

  clearingPlansById: Map<string, ClearingPlanEvent>

  // Optional: wake up render loop after snapshot/patch/FX updates.
  wakeUp?: () => void

  // Optional: notify on any SSE event (used to keep Demo UI render loop awake).
  onAnySseEvent?: () => void
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
    runRealClearingPlanFx,
    runRealClearingDoneFx,
    clearingPlansById,
    wakeUp,
    onAnySseEvent,
  } = opts

  // -----------------
  // Diagnostics (dev-only, per plan section 10)
  // -----------------

  const diag = {
    rx_messages: 0,
    json_parse_errors: 0,
    normalize_dropped: 0,
    events_by_type: {} as Record<string, number>,
    tx_sender_labels: 0,
    tx_receiver_labels: 0,
    amount_flyout_suppressed: 0,
    burst_throttle_enabled_events: 0,
    burst_throttle_ms_last: 0,
  }

  if (isLocalhost) {
    ;(globalThis as any).__geo_real_mode_diag = diag
  }

  function incDiag(map: Record<string, number>, key: string) {
    map[key] = (map[key] ?? 0) + 1
  }

  function getBurstLabelThrottleMs(): number {
    const ops = Number(real.runStatus?.ops_sec ?? 0)
    const qd = Number(real.runStatus?.queue_depth ?? 0)

    // Degradation policy (P2.2): when backend indicates bursts/overload, enable
    // overlay throttling to reduce cap evictions and perceived dropouts.
    if (ops >= 40 || qd >= 200) return 240
    if (ops >= 20 || qd >= 100) return 120
    return 0
  }

  // -----------------
  // Real Mode: SSE loop
  // -----------------

  let sseAbort: AbortController | null = null
  let sseSeq = 0

  // Best-effort event de-duplication: prevents duplicate UI effects (labels/FX)
  // when backend replays events after SSE reconnect.
  const processedEventIds = new Map<string, true>()
  let processedEventIdsRunId: string | null = null

  // Bound memory while keeping enough history to dedup large SSE replays.
  // Too small -> duplicates slip through -> extra labels -> cap eviction -> perceived dropouts.
  const PROCESSED_EVENT_IDS_MAX = 5000
  const PROCESSED_EVENT_IDS_PRUNE_BATCH = 500

  function markEventProcessed(runId: string, eventId: string): boolean {
    const rid = String(runId ?? '')
    const eid = String(eventId ?? '')
    if (!rid || !eid) return true

    if (processedEventIdsRunId !== rid) {
      processedEventIdsRunId = rid
      processedEventIds.clear()
    }

    if (processedEventIds.has(eid)) return false
    processedEventIds.set(eid, true)

    // Bound memory: keep only the most recent event IDs (Map preserves insertion order).
    // Prune in batches to keep the loop cheap under bursts.
    if (processedEventIds.size > PROCESSED_EVENT_IDS_MAX) {
      const targetSize = Math.max(0, PROCESSED_EVENT_IDS_MAX - PROCESSED_EVENT_IDS_PRUNE_BATCH)
      while (processedEventIds.size > targetSize) {
        const firstKey = processedEventIds.keys().next().value as string | undefined
        if (!firstKey) break
        processedEventIds.delete(firstKey)
      }
    }
    return true
  }

  // Debounce multiple rapid calls to refreshSnapshot (e.g., during startRun + watcher cascade).
  let refreshSnapshotDebounceTimer: ReturnType<typeof setTimeout> | null = null
  let refreshSnapshotInFlight = false
  let refreshSnapshotPending = false

  // Sequence id used to invalidate pending debounced refreshes and to guard against stale context.
  // - incremented on each *actual* refresh attempt
  // - incremented on teardown/stop paths (invalidates delayed refreshes + post-await steps)
  let refreshSnapshotSeq = 0
  
  // Block watchers from calling refreshSnapshot during startRun to prevent double load.
  let startRunInProgress = false

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

  function resetStaleRun(resetOpts?: { clearError?: boolean }) {
    teardownRefreshSnapshot()
    real.runId = null
    real.runStatus = null
    real.lastEventId = null
    real.artifacts = []
    clearingPlansById.clear()
    cleanupRealRunFxAndTimers()
    resetRunStats()
    stopSse()

    processedEventIdsRunId = null
    processedEventIds.clear()

    if (resetOpts?.clearError ?? true) {
      real.lastError = ''
      state.error = ''
    }

    // Keep selection usable after restart (do not auto-start).
    ensureScenarioSelectionValid()
  }

  async function refreshRunStatus() {
    if (!real.runId || !real.accessToken) return
    try {
      const st = await getRun({ apiBase: real.apiBase, accessToken: real.accessToken }, real.runId)
      real.runStatus = st
      const le = st.last_error
      real.lastError = le && isUserFacingRunError(le.code) ? `${le.code}: ${le.message}` : ''
    } catch (e: unknown) {
      if (e instanceof ApiError && e.status === 404) {
        resetStaleRun({ clearError: true })
        return
      }
      real.lastError = String((e as any)?.message ?? e)
    }
  }

  async function refreshSnapshot() {
    // Guard: never refresh snapshots when real mode is not active or context is missing.
    if (!isRealMode.value) return
    if (!real.accessToken) return
    if (!real.runId && !real.selectedScenarioId) return

    // If a refresh is already in flight, mark as pending and return — the current call will re-invoke.
    if (refreshSnapshotInFlight) {
      refreshSnapshotPending = true
      return
    }

    // Debounce: if called within a short window, delay to coalesce multiple triggers.
    if (refreshSnapshotDebounceTimer !== null) {
      refreshSnapshotPending = true
      return
    }

    const mySeq = ++refreshSnapshotSeq
    const runIdAtStart = real.runId

    const isContextStillValid = () => {
      if (!isRealMode.value) return false
      if (!real.accessToken) return false

      // Abort if a teardown/stop happened or a newer refresh started.
      if (refreshSnapshotSeq !== mySeq) return false

      // Prevent a delayed refresh from applying to a different run context.
      if (real.runId !== runIdAtStart) return false

      return true
    }

    refreshSnapshotDebounceTimer = setTimeout(() => {
      refreshSnapshotDebounceTimer = null

      // If context changed while we were debouncing, drop pending refresh safely.
      if (!isContextStillValid()) {
        refreshSnapshotPending = false
        return
      }

      if (refreshSnapshotPending) {
        refreshSnapshotPending = false
        refreshSnapshot()
      }
    }, 80)

    refreshSnapshotInFlight = true
    try {
      // If we got here via a delayed debounce call, ensure we still target the same context.
      if (!isContextStillValid()) return

      await loadScene()

      // Context might have changed while loadScene() was in-flight.
      if (!isContextStillValid()) return

      // Snapshot change should be visible immediately even if deep idle stopped scheduling.
      wakeUp?.()

      // Scene loader stores errors in state.error; in real mode surface them in the HUD.
      if (isRealMode.value && state.error) {
        if (state.error.includes('HTTP 404')) {
          resetStaleRun({ clearError: true })
          return
        }
        real.lastError = state.error
      }
    } finally {
      refreshSnapshotInFlight = false

      // If another call came in while we were loading, re-invoke.
      if (refreshSnapshotPending) {
        refreshSnapshotPending = false
        refreshSnapshot()
      }
    }
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

  function isRunStatusEvent(evt: unknown): evt is RunStatusEvent {
    if (!evt || typeof evt !== 'object') return false
    const e = evt as any
    return (
      e.type === 'run_status' &&
      typeof e.run_id === 'string' &&
      typeof e.scenario_id === 'string' &&
      typeof e.sim_time_ms === 'number' &&
      typeof e.intensity_percent === 'number' &&
      typeof e.ops_sec === 'number' &&
      typeof e.queue_depth === 'number'
    )
  }

  function isTxUpdatedEvent(evt: unknown): evt is TxUpdatedEvent {
    if (!evt || typeof evt !== 'object') return false
    const e = evt as any
    return e.type === 'tx.updated' && typeof e.equivalent === 'string'
  }

  function isTxFailedEvent(evt: unknown): evt is TxFailedEvent {
    if (!evt || typeof evt !== 'object') return false
    const e = evt as any
    return e.type === 'tx.failed' && typeof e.equivalent === 'string'
  }

  async function runSseLoop() {
    const mySeq = ++sseSeq
    stopSse()

    if (!isRealMode.value) return
    if (!real.runId || !real.accessToken) return

    const ctrl = new AbortController()
    sseAbort = ctrl

    let attempt = 0
    real.sseState = 'connecting'
    real.lastError = ''

    while (!ctrl.signal.aborted && mySeq === sseSeq) {
      const runId = real.runId
      const eqNow = effectiveEq.value
      const url = `${real.apiBase.replace(/\/+$/, '')}/simulator/runs/${encodeURIComponent(runId)}/events?equivalent=${encodeURIComponent(
        eqNow,
      )}`

      try {
        real.sseState = 'open'
        await connectSse({
          url,
          headers: authHeaders(real.accessToken),
          lastEventId: real.lastEventId,
          signal: ctrl.signal,
          onMessage: (msg) => {
            if (ctrl.signal.aborted) return
            if (msg.id) real.lastEventId = msg.id
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

            const evt = normalizeSimulatorEvent(parsed)
            if (!evt) {
              diag.normalize_dropped += 1
              return
            }

            incDiag(diag.events_by_type, String((evt as any).type ?? 'unknown'))

            // Prefer payload event_id when present.
            real.lastEventId = evt.event_id

            // Drop duplicates (SSE replay after reconnect).
            if (!markEventProcessed(runId, evt.event_id)) return

            onAnySseEvent?.()

            if (isRunStatusEvent(evt)) {
              const st = evt
              real.runStatus = st
              const le = st.last_error
              real.lastError = le && isUserFacingRunError(le.code) ? `${le.code}: ${le.message}` : ''

              // Backend-first: sync cumulative stats from authoritative run_status.
              // This overwrites optimistic local increments, ensuring SSE reconnect
              // does not lose history and classification stays consistent.
              if (typeof st.attempts_total === 'number') real.runStats.attempts = st.attempts_total
              if (typeof st.committed_total === 'number') real.runStats.committed = st.committed_total
              if (typeof st.rejected_total === 'number') real.runStats.rejected = st.rejected_total
              if (typeof st.errors_total === 'number') real.runStats.errors = st.errors_total
              if (typeof st.timeouts_total === 'number') real.runStats.timeouts = st.timeouts_total
              return
            }

            if (isTxUpdatedEvent(evt)) {
              real.runStats.attempts += 1
              real.runStats.committed += 1

              const tx = evt
              // Backward compatible:
              // - amount_flyout === false -> do not emit amount labels (even if amount is present)
              // - amount_flyout === true/undefined -> best-effort emit based on amount+endpoints
              const allowAmountFlyout = tx.amount_flyout !== false
              const edges = Array.isArray(tx.edges) ? tx.edges : []
              const senderId = String(tx.from ?? (edges.length > 0 ? edges[0]!.from : '') ?? '').trim()
              const receiverId = String(tx.to ?? (edges.length > 0 ? edges[edges.length - 1]!.to : '') ?? '').trim()

              // Backend-first: labels require explicit amount.
              const amount = String(tx.amount ?? '').trim()

              // Contract diagnostic: amount_flyout=true should always include enough data
              // for both labels. This should never trigger in healthy backend runs.
              if (tx.amount_flyout === true && (!amount || !senderId || !receiverId)) {
                // eslint-disable-next-line no-console
                console.warn('tx.updated amount_flyout contract violated (missing amount/endpoints)', {
                  event_id: tx.event_id,
                  amount,
                  from: tx.from,
                  to: tx.to,
                  edges_len: edges.length,
                })
              }

              // Apply patches to keep snapshot authoritative.
              if (tx.node_patch) realPatchApplier.applyNodePatches(tx.node_patch)
              if (tx.edge_patch) realPatchApplier.applyEdgePatches(tx.edge_patch)

              // Run spark FX first so we can align timing with ttl_ms clamping logic.
              runRealTxFx(tx)

              // Ensure canvas updates even if render loop entered deep idle.
              wakeUp?.()

              const labelThrottleMs = getBurstLabelThrottleMs()
              diag.burst_throttle_ms_last = labelThrottleMs
              if (labelThrottleMs > 0) diag.burst_throttle_enabled_events += 1

              if (!allowAmountFlyout) {
                diag.amount_flyout_suppressed += 1
              }

              if (allowAmountFlyout && amount && senderId) {
                pushTxAmountLabel(senderId, `-${amount}`, tx.equivalent, { throttleMs: labelThrottleMs })
                diag.tx_sender_labels += 1
              }

              // Note: self-payment (senderId === receiverId) intentionally does not emit a receiver label.
              if (allowAmountFlyout && amount && receiverId && receiverId !== senderId) {
                const ttlMs = clampRealTxTtlMs(tx.ttl_ms)

                const runIdAtEvent = runId
                const sseSeqAtEvent = mySeq

                scheduleTimeout(
                  () => {
                    // IMPORTANT: do NOT guard on ctrl.signal.aborted.
                    // AbortSignal is tied to the fetch/SSE-loop lifecycle; on reconnect we abort
                    // the old connection, but UI timers for still-valid events should still fire.
                    if (sseSeq !== sseSeqAtEvent) return
                    if (real.runId !== runIdAtEvent) return
                    pushTxAmountLabel(receiverId, `+${amount}`, tx.equivalent, { throttleMs: labelThrottleMs })
                    diag.tx_receiver_labels += 1
                  },
                  ttlMs,
                  { critical: true },
                )
              }
              return
            }

            if (isTxFailedEvent(evt)) {
              const failed = evt
              const code = String(failed.error?.code ?? 'TX_FAILED')
              real.runStats.attempts += 1
              if (code.toUpperCase() === 'PAYMENT_TIMEOUT') {
                real.runStats.timeouts += 1
                real.runStats.errors += 1
                inc(real.runStats.errorsByCode, code)
              } else if (isUserFacingRunError(code)) {
                real.runStats.errors += 1
                inc(real.runStats.errorsByCode, code)
              } else {
                real.runStats.rejected += 1
                inc(real.runStats.rejectedByCode, code)
              }
              return
            }

            if (evt.type === 'clearing.plan') {
              const plan = evt as ClearingPlanEvent
              const planId = String(plan.plan_id ?? '')
              if (planId) {
                clearingPlansById.set(planId, plan)
                runRealClearingPlanFx(plan)
                wakeUp?.()
              }
              return
            }

            if (evt.type === 'clearing.done') {
              const done = evt as ClearingDoneEvent
              const planId = String(done.plan_id ?? '')
              const plan = planId ? clearingPlansById.get(planId) : undefined

              if (done.node_patch) realPatchApplier.applyNodePatches(done.node_patch)
              if (done.edge_patch) realPatchApplier.applyEdgePatches(done.edge_patch)

              runRealClearingDoneFx(plan, done)
              wakeUp?.()
              if (planId) clearingPlansById.delete(planId)
            }
          },
        })

        await refreshRunStatus()

        const st = String(real.runStatus?.state ?? '')
        if (st === 'stopped' || st === 'error') {
          real.sseState = 'closed'
          return
        }

        real.sseState = 'reconnecting'
      } catch (e: unknown) {
        if (ctrl.signal.aborted) return

        const msg = String((e as any)?.message ?? e)
        if (msg.includes('SSE HTTP 404') || msg.includes('HTTP 404')) {
          resetStaleRun({ clearError: true })
          return
        }

        real.lastError = msg

        // Strict replay mode: backend may return 410 → do a full refresh.
        if (msg.includes(' 410 ') || msg.includes('HTTP 410') || msg.includes('status 410')) {
          real.lastEventId = null
          await refreshRunStatus()
          await refreshSnapshot()
        }

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
    if (!real.accessToken) {
      real.lastError = 'Missing access token'
      return
    }

    real.loadingScenarios = true
    real.lastError = ''
    try {
      const res = await listScenarios({ apiBase: real.apiBase, accessToken: real.accessToken })
      real.scenarios = res.items ?? []
      ensureScenarioSelectionValid()
    } catch (e: unknown) {
      real.lastError = String((e as any)?.message ?? e)
    } finally {
      real.loadingScenarios = false
    }
  }

  async function startRun(startOpts?: { mode?: SimulatorMode; intensityPercent?: number; pauseImmediately?: boolean }) {
    if (!real.selectedScenarioId) return
    if (!real.accessToken) {
      real.lastError = 'Missing access token'
      return
    }

    const st = String(real.runStatus?.state ?? '').toLowerCase()
    const isActive = !!real.runId && (st === 'running' || st === 'paused' || st === 'created' || st === 'stopping')
    if (real.runId && !isActive) {
      resetStaleRun({ clearError: true })
    }

    real.lastError = ''
    startRunInProgress = true
    try {
      stopSse()
      clearingPlansById.clear()
      cleanupRealRunFxAndTimers()
      resetRunStats()

      const res = await createRun(
        { apiBase: real.apiBase, accessToken: real.accessToken },
        {
          scenario_id: real.selectedScenarioId,
          mode: startOpts?.mode ?? real.desiredMode,
          intensity_percent: startOpts?.intensityPercent ?? real.intensityPercent,
        },
      )
      real.runId = res.run_id
      real.runStatus = null
      real.lastEventId = null
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
              real.lastError = String((e as any)?.message ?? e)
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
      real.lastError = String((e as any)?.message ?? e)
    } finally {
      startRunInProgress = false
    }
  }

  async function pause() {
    if (!real.runId || !real.accessToken) return
    try {
      real.runStatus = await pauseRun({ apiBase: real.apiBase, accessToken: real.accessToken }, real.runId)
    } catch (e: unknown) {
      real.lastError = String((e as any)?.message ?? e)
    }
  }

  async function resume() {
    if (!real.runId || !real.accessToken) return
    try {
      real.runStatus = await resumeRun({ apiBase: real.apiBase, accessToken: real.accessToken }, real.runId)
    } catch (e: unknown) {
      real.lastError = String((e as any)?.message ?? e)
    }
  }

  async function stop() {
    if (!real.runId || !real.accessToken) return
    try {
      teardownRefreshSnapshot()
      stopSse()
      clearingPlansById.clear()
      cleanupRealRunFxAndTimers()
        real.runStatus = await stopRun({ apiBase: real.apiBase, accessToken: real.accessToken }, real.runId, {
          source: 'ui',
          reason: 'user_stop',
        })
      await refreshRunStatus()
    } catch (e: unknown) {
      const msg = String((e as any)?.message ?? e)

      // If the run is already gone server-side (e.g. TTL cleanup, double-stop, or restart),
      // treat stop as idempotent and reset local state instead of surfacing a hard error.
      if (msg.includes('HTTP 404') || msg.includes(' 404 ') || msg.includes('Not Found')) {
        resetStaleRun({ clearError: true })
        return
      }

      real.lastError = msg
    }
  }

  async function applyIntensity() {
    if (!real.runId || !real.accessToken) return
    try {
      real.runStatus = await setIntensity(
        { apiBase: real.apiBase, accessToken: real.accessToken },
        real.runId,
        real.intensityPercent,
      )
    } catch (e: unknown) {
      real.lastError = String((e as any)?.message ?? e)
    }
  }

  async function refreshArtifacts() {
    if (!real.runId || !real.accessToken) return
    real.artifactsLoading = true
    try {
      const idx = await listArtifacts({ apiBase: real.apiBase, accessToken: real.accessToken }, real.runId)
      real.artifacts = idx.items ?? []
    } catch (e: unknown) {
      real.lastError = String((e as any)?.message ?? e)
    } finally {
      real.artifactsLoading = false
    }
  }

  async function downloadArtifact(name: string) {
    if (!real.runId || !real.accessToken) return
    try {
      const url = artifactDownloadUrl({ apiBase: real.apiBase, accessToken: real.accessToken }, real.runId, name)
      const res = await fetch(url, { headers: authHeaders(real.accessToken) })
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
      real.lastError = String((e as any)?.message ?? e)
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
      void (async () => {
        await refreshScenarios()

        ensureScenarioSelectionValid()

        if (real.runId && real.accessToken) {
          await refreshRunStatus()
          if (real.runId) {
            await refreshSnapshot()
            if (real.runId) {
              await runSseLoop()
            }
          }
          return
        }

        // No active run on mount: show scenario preview immediately.
        if (real.accessToken && real.selectedScenarioId && !real.runId) {
          await refreshSnapshot()
        }

        const shouldAutoStart =
          import.meta.env.DEV &&
          isLocalhost &&
          real.accessToken &&
          real.selectedScenarioId &&
          !real.runId &&
          new URLSearchParams(window.location.search).get('autostart') === '1'
        if (shouldAutoStart) await startRun()
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
      if (!real.accessToken) return

      // Block watcher from calling refreshSnapshot during startRun (it handles its own refresh).
      if (startRunInProgress) return

      const st = String(real.runStatus?.state ?? '').toLowerCase()
      const isActive =
        !!real.runId &&
        // Optimistic: if we have runId but status not fetched yet, treat it as active.
        (!real.runStatus || st === 'running' || st === 'paused' || st === 'created' || st === 'stopping')
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
  }
}
