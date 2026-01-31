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
import type { ArtifactIndexItem, RunStatus, ScenarioSummary, SimulatorMode } from '../api/simulatorTypes'
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
  signedBalanceForNodeId: (id: string) => bigint
  pushBalanceDeltaLabels: (
    beforeById: Map<string, bigint>,
    nodeIds: string[],
    equivalent: string,
    opts?: { throttleMs?: number },
  ) => void
  runRealTxFx: (tx: TxUpdatedEvent) => void
  runRealClearingPlanFx: (plan: ClearingPlanEvent) => void
  runRealClearingDoneFx: (plan: ClearingPlanEvent | undefined, done: ClearingDoneEvent) => void

  clearingPlansById: Map<string, ClearingPlanEvent>
}): {
  stopSse: () => void
  resetStaleRun: (opts?: { clearError?: boolean }) => void
  refreshRunStatus: () => Promise<void>
  refreshSnapshot: () => Promise<void>
  refreshScenarios: () => Promise<void>
  startRun: () => Promise<void>
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
    signedBalanceForNodeId,
    pushBalanceDeltaLabels,
    runRealTxFx,
    runRealClearingPlanFx,
    runRealClearingDoneFx,
    clearingPlansById,
  } = opts

  // -----------------
  // Real Mode: SSE loop
  // -----------------

  let sseAbort: AbortController | null = null
  let sseSeq = 0

  function stopSse() {
    sseAbort?.abort()
    sseAbort = null
    real.sseState = 'idle'
  }

  function resetStaleRun(resetOpts?: { clearError?: boolean }) {
    real.runId = null
    real.runStatus = null
    real.lastEventId = null
    real.artifacts = []
    clearingPlansById.clear()
    cleanupRealRunFxAndTimers()
    resetRunStats()
    stopSse()

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
      real.intensityPercent = Math.round(Number(st.intensity_percent ?? real.intensityPercent))
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
    await loadScene()

    // Scene loader stores errors in state.error; in real mode surface them in the HUD.
    if (isRealMode.value && state.error) {
      if (state.error.includes('HTTP 404')) {
        resetStaleRun({ clearError: true })
        return
      }
      real.lastError = state.error
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
              return
            }

            const evt = normalizeSimulatorEvent(parsed)
            if (!evt) return

            // Prefer payload event_id when present.
            if ((evt as any).event_id) real.lastEventId = String((evt as any).event_id)

            if ((evt as any).type === 'run_status') {
              real.runStatus = evt as any
              real.intensityPercent = Math.round(Number((evt as any).intensity_percent ?? real.intensityPercent))
              const le = (evt as any).last_error
              real.lastError = le && isUserFacingRunError(le.code) ? `${le.code}: ${le.message}` : ''
              return
            }

            if ((evt as any).type === 'tx.updated') {
              real.runStats.attempts += 1
              real.runStats.committed += 1

              const tx = evt as TxUpdatedEvent
              const beforeById = new Map<string, bigint>()
              const nodeIds = (tx.node_patch ?? []).map((p) => p.id)
              for (const id of nodeIds) beforeById.set(id, signedBalanceForNodeId(id))

              if (tx.node_patch) realPatchApplier.applyNodePatches(tx.node_patch)
              if (tx.edge_patch) realPatchApplier.applyEdgePatches(tx.edge_patch)

              if (nodeIds.length > 0) pushBalanceDeltaLabels(beforeById, nodeIds, tx.equivalent, { throttleMs: 220 })
              runRealTxFx(tx)
              return
            }

            if ((evt as any).type === 'tx.failed') {
              const code = String((evt as any).error?.code ?? 'TX_FAILED')
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

            if ((evt as any).type === 'clearing.plan') {
              const plan = evt as ClearingPlanEvent
              const planId = String(plan.plan_id ?? '')
              if (planId) {
                clearingPlansById.set(planId, plan)
                runRealClearingPlanFx(plan)
              }
              return
            }

            if ((evt as any).type === 'clearing.done') {
              const done = evt as ClearingDoneEvent
              const planId = String(done.plan_id ?? '')
              const plan = planId ? clearingPlansById.get(planId) : undefined

              const beforeById = new Map<string, bigint>()
              const nodeIds = (done.node_patch ?? []).map((p) => p.id)
              for (const id of nodeIds) beforeById.set(id, signedBalanceForNodeId(id))

              if (done.node_patch) realPatchApplier.applyNodePatches(done.node_patch)
              if (done.edge_patch) realPatchApplier.applyEdgePatches(done.edge_patch)

              if (nodeIds.length > 0) pushBalanceDeltaLabels(beforeById, nodeIds, done.equivalent, { throttleMs: 180 })

              runRealClearingDoneFx(plan, done)
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

  async function startRun() {
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
    try {
      stopSse()
      clearingPlansById.clear()
      cleanupRealRunFxAndTimers()
      resetRunStats()

      const res = await createRun(
        { apiBase: real.apiBase, accessToken: real.accessToken },
        { scenario_id: real.selectedScenarioId, mode: real.desiredMode, intensity_percent: real.intensityPercent },
      )
      real.runId = res.run_id
      real.runStatus = null
      real.lastEventId = null
      real.artifacts = []

      await refreshRunStatus()
      await refreshSnapshot()
      await runSseLoop()
    } catch (e: unknown) {
      real.lastError = String((e as any)?.message ?? e)
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
      stopSse()
      clearingPlansById.clear()
      cleanupRealRunFxAndTimers()
      real.runStatus = await stopRun({ apiBase: real.apiBase, accessToken: real.accessToken }, real.runId)
      await refreshRunStatus()
    } catch (e: unknown) {
      real.lastError = String((e as any)?.message ?? e)
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
      if (!v) return
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
