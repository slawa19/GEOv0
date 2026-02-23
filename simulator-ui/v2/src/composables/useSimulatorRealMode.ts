import { watch, type ComputedRef } from 'vue'

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

import type { ClearingDoneEvent, EdgePatch, NodePatch, TxUpdatedEvent } from '../types'
import type { SimulatorAppState } from '../types/simulatorApp'
import { resolveTxDirection } from '../utils/txDirection'
import { incCounter } from '../utils/counters'
import { toLower } from '../utils/stringHelpers'

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
  runRealClearingDoneFx: (done: ClearingDoneEvent) => void

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
    tx_receiver_scheduled: 0,
    tx_receiver_guard_dropped: 0,
    amount_flyout_suppressed: 0,
    burst_throttle_enabled_events: 0,
    burst_throttle_ms_last: 0,
  }

  if (isLocalhost) {
    ;(globalThis as any).__geo_real_mode_diag = diag
  }

  const incDiag = incCounter

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

  function resetStaleRun(resetOpts?: { clearError?: boolean }) {
    teardownRefreshSnapshot()
    real.runId = null
    real.runStatus = null
    real.lastEventId = null
    real.artifacts = []
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
    // No accessToken guard: anonymous visitors use cookie-auth (geo_sim_sid).
    if (!real.runId) return
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
    // No accessToken guard: anonymous visitors use cookie-auth (geo_sim_sid).
    if (!isRealMode.value) return
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
      // No accessToken guard: anonymous visitors use cookie-auth (geo_sim_sid).

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
    // Note: accessToken is not required here — anonymous visitors use cookie-auth (geo_sim_sid).
    // credentials: 'include' is set at the SSE/fetch layer; see api/sse.ts connectSse.
    if (!real.runId) return

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
              const resolved = resolveTxDirection({ from: tx.from, to: tx.to, edges })
              const senderId = resolved.from
              const receiverId = resolved.to

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

                diag.tx_receiver_scheduled += 1
                scheduleTimeout(
                  () => {
                    // IMPORTANT: do NOT guard on ctrl.signal.aborted.
                    // AbortSignal is tied to the fetch/SSE-loop lifecycle; on reconnect we abort
                    // the old connection, but UI timers for still-valid events should still fire.
                    if (real.runId !== runIdAtEvent) {
                      diag.tx_receiver_guard_dropped += 1
                      return
                    }
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


            if (evt.type === 'clearing.done') {
              const done = evt as ClearingDoneEvent

              if (done.node_patch) realPatchApplier.applyNodePatches(done.node_patch)
              if (done.edge_patch) realPatchApplier.applyEdgePatches(done.edge_patch)

              runRealClearingDoneFx(done)
              wakeUp?.()
              return
            }

            if (evt.type === 'topology.changed') {
              const t = evt as any
              const payload = t?.payload as any
              const hasPayload = !!payload && typeof payload === 'object'

              const hasPatches =
                hasPayload &&
                (((payload.node_patch?.length ?? 0) > 0) || ((payload.edge_patch?.length ?? 0) > 0))

              const isEmptyPayload =
                !hasPayload ||
                ((payload.added_nodes?.length ?? 0) === 0 &&
                  (payload.removed_nodes?.length ?? 0) === 0 &&
                  (payload.frozen_nodes?.length ?? 0) === 0 &&
                  (payload.added_edges?.length ?? 0) === 0 &&
                  (payload.removed_edges?.length ?? 0) === 0 &&
                  (payload.frozen_edges?.length ?? 0) === 0 &&
                  !hasPatches)

              if ((isEmptyPayload && !hasPatches) || !state.snapshot) {
                void refreshSnapshot()
                return
              }

              // Incremental topology patch: update snapshot in-place.
              const snap = state.snapshot
              if (String(snap.equivalent ?? '').toUpperCase() !== String(t.equivalent ?? '').toUpperCase()) {
                // If the event is for a different equivalent than the current scene,
                // keep behavior safe and let the next refresh/load handle it.
                return
              }

              // Patch-only event (e.g. trust drift / inject_debt): apply patches and return.
              if (
                hasPatches &&
                (payload.added_nodes?.length ?? 0) === 0 &&
                (payload.removed_nodes?.length ?? 0) === 0 &&
                (payload.frozen_nodes?.length ?? 0) === 0 &&
                (payload.added_edges?.length ?? 0) === 0 &&
                (payload.removed_edges?.length ?? 0) === 0 &&
                (payload.frozen_edges?.length ?? 0) === 0
              ) {
                if (payload.node_patch) realPatchApplier.applyNodePatches(payload.node_patch)
                if (payload.edge_patch) realPatchApplier.applyEdgePatches(payload.edge_patch)
                snap.generated_at = String(t.ts ?? new Date().toISOString())
                wakeUp?.()
                return
              }

              const nodeColorKey = (type?: string | null, status?: string | null): string => {
                const st = String(status ?? '').trim().toLowerCase()
                if (st === 'suspended' || st === 'left' || st === 'deleted') return st
                const tp = String(type ?? '').trim().toLowerCase()
                if (tp === 'business' || tp === 'person') return tp
                return 'unknown'
              }

              const ensureNodeDefaults = (n: any) => {
                if (!n) return
                const tp = String(n.type ?? '').trim().toLowerCase()
                const isBiz = tp === 'business'
                if (!n.viz_shape_key) n.viz_shape_key = isBiz ? 'rounded-rect' : 'circle'
                if (!n.viz_size) n.viz_size = isBiz ? { w: 26, h: 22 } : { w: 16, h: 16 }
                if (!n.viz_color_key) n.viz_color_key = nodeColorKey(n.type, n.status)
              }

              const nodesById = new Map(snap.nodes.map((n) => [n.id, n] as const))

              const removedNodes = new Set<string>(payload.removed_nodes ?? [])
              const frozenNodes = new Set<string>(payload.frozen_nodes ?? [])

              // Apply node additions.
              for (const r of payload.added_nodes ?? []) {
                const id = String(r?.pid ?? '').trim()
                if (!id || nodesById.has(id)) continue
                const node: any = {
                  id,
                  name: r?.name ?? undefined,
                  type: r?.type ?? undefined,
                  status: 'active',
                }
                ensureNodeDefaults(node)
                snap.nodes.push(node)
                nodesById.set(id, node)
              }

              // Apply node freeze (status change).
              for (const id of frozenNodes) {
                const node = nodesById.get(id)
                if (!node) continue
                node.status = 'suspended'
                node.viz_color_key = nodeColorKey(node.type, node.status)
              }

              // Apply node removals.
              if (removedNodes.size) {
                snap.nodes = snap.nodes.filter((n) => !removedNodes.has(n.id))
                // Remove incident links too.
                snap.links = snap.links.filter((l) => !removedNodes.has(String(l.source)) && !removedNodes.has(String(l.target)))
              }

              // Links: use (source,target) as identity.
              const keyEdge = (s: string, d: string) => `${s}→${d}`
              const linksByKey = new Map(snap.links.map((l) => [keyEdge(String(l.source), String(l.target)), l] as const))

              // Apply edge additions.
              for (const r of payload.added_edges ?? []) {
                const s = String(r?.from_pid ?? '').trim()
                const d = String(r?.to_pid ?? '').trim()
                if (!s || !d) continue
                const k = keyEdge(s, d)
                if (linksByKey.has(k)) continue
                const limit = r?.limit ?? undefined
                const link: any = {
                  source: s,
                  target: d,
                  trust_limit: limit,
                  used: 0,
                  available: limit,
                  status: 'active',
                  viz_width_key: 'thin',
                  viz_alpha_key: 'active',
                }
                snap.links.push(link)
                linksByKey.set(k, link)
              }

              // Apply frozen edges.
              for (const r of payload.frozen_edges ?? []) {
                const s = String(r?.from_pid ?? '').trim()
                const d = String(r?.to_pid ?? '').trim()
                if (!s || !d) continue
                const link = linksByKey.get(keyEdge(s, d))
                if (!link) continue
                link.status = 'frozen'
                link.viz_alpha_key = 'muted'
              }

              // Apply removed edges.
              const removedEdgeKeys = new Set<string>()
              for (const r of payload.removed_edges ?? []) {
                const s = String(r?.from_pid ?? '').trim()
                const d = String(r?.to_pid ?? '').trim()
                if (!s || !d) continue
                removedEdgeKeys.add(keyEdge(s, d))
              }
              if (removedEdgeKeys.size) {
                snap.links = snap.links.filter((l) => !removedEdgeKeys.has(keyEdge(String(l.source), String(l.target))))
              }

              // Recompute links_count.
              const counts: Record<string, number> = {}
              for (const l of snap.links) {
                const s = String((l as any).source)
                const d = String((l as any).target)
                counts[s] = (counts[s] ?? 0) + 1
                counts[d] = (counts[d] ?? 0) + 1
              }
              for (const n of snap.nodes) {
                ;(n as any).links_count = counts[String((n as any).id)] ?? 0
              }

              // Bump generated_at so layout coordinator can detect updates.
              snap.generated_at = String(t.ts ?? new Date().toISOString())

              // Apply backend-authoritative patches (limits/used/available/viz keys) if provided.
              if (payload.node_patch) realPatchApplier.applyNodePatches(payload.node_patch)
              if (payload.edge_patch) realPatchApplier.applyEdgePatches(payload.edge_patch)

              wakeUp?.()
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
    // No accessToken guard: anonymous visitors use cookie-auth (geo_sim_sid).
    await ensureAnonSessionOnce()
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
          const activeRunId = String((active as any)?.run_id ?? '').trim()
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
    // No accessToken guard: anonymous visitors use cookie-auth (geo_sim_sid).
    if (!real.runId) return
    try {
      real.runStatus = await pauseRun({ apiBase: real.apiBase, accessToken: real.accessToken }, real.runId)
    } catch (e: unknown) {
      real.lastError = String((e as any)?.message ?? e)
    }
  }

  async function resume() {
    // No accessToken guard: anonymous visitors use cookie-auth (geo_sim_sid).
    if (!real.runId) return
    try {
      real.runStatus = await resumeRun({ apiBase: real.apiBase, accessToken: real.accessToken }, real.runId)
    } catch (e: unknown) {
      real.lastError = String((e as any)?.message ?? e)
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

  /** Admin helper: attach UI to an arbitrary run_id (observer/control path). */
  async function attachToRun(runId: string) {
    const rid = String(runId ?? '').trim()
    if (!rid) return

    try {
      teardownRefreshSnapshot()
      stopSse()
      cleanupRealRunFxAndTimers()

      real.lastError = ''
      real.runId = rid
      real.runStatus = null
      real.lastEventId = null
      real.artifacts = []

      await refreshRunStatus()
      await refreshSnapshot()
      void runSseLoop()
    } catch (e: unknown) {
      real.lastError = String((e as any)?.message ?? e)
    }
  }

  /** Admin helper: stop a specific run_id (may be different from current run). */
  async function stopRunById(runId: string, opts?: { source?: string; reason?: string }) {
    const rid = String(runId ?? '').trim()
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
      real.lastError = String((e as any)?.message ?? e)
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
      real.lastError = String((e as any)?.message ?? e)
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

          // No accessToken guard: anonymous visitors use cookie-auth (geo_sim_sid).
          if (real.runId) {
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

    // Admin helpers
    attachToRun,
    stopRunById,
  }
}
