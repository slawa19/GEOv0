import type { Ref } from 'vue'
import { onUnmounted, ref, watch } from 'vue'

import { actionClearingOnce, actionTxOnce, getActiveRun } from '../api/simulatorApi'
import { toLower } from '../utils/stringHelpers'

export function useFxDebugControls(deps: {
  isFxDebugEnabled: Readonly<Ref<boolean>>
  real: {
    apiBase: string
    accessToken: string | null
    selectedScenarioId: string | null
    runId: string | null
    runStatus?: { state?: string | null } | null
    lastError?: string | null
    sseState?: string | null
    loadingScenarios?: boolean
    scenarios?: unknown[] | null
  }
  effectiveEq: Readonly<Ref<string>>
  markDemoActivity: () => void
  realMode: {
    refreshRunStatus: () => Promise<void>
    resetStaleRun: (opts?: { clearError?: boolean }) => void
    refreshScenarios: () => Promise<void>
    startRun: (opts: { mode: 'real'; pauseImmediately: true }) => Promise<void>
    pause: () => Promise<void>
    refreshSnapshot: () => Promise<void>
  }
}): {
  busy: Ref<boolean>
  ensureRunSerialized: () => Promise<string>
  runTxOnce: () => Promise<void>
  runClearingOnce: () => Promise<void>
  waitForRealSseOpen: (opts?: { timeoutMs?: number }) => Promise<boolean>
  dispose: () => void
} {
  const busy = ref(false)

  let disposed = false
  const pendingWaitResolves = new Set<(ok: boolean) => void>()

  let _ensureRunPromise: Promise<string> | null = null

  function dispose(): void {
    disposed = true
    for (const r of pendingWaitResolves) {
      try {
        r(false)
      } catch {
        // ignore
      }
    }
    pendingWaitResolves.clear()
  }

  onUnmounted(() => {
    dispose()
  })

  async function ensureRunForFxDebug(): Promise<string> {
    if (deps.real.runId) {
      try {
        await deps.realMode.refreshRunStatus()
      } catch {
        // best-effort; refreshRunStatus already handles 404 by resetting stale run
      }

      const st = toLower(deps.real.runStatus?.state)
      const isTerminal = st === 'stopped' || st === 'error'

      if (isTerminal) {
        deps.realMode.resetStaleRun({ clearError: true })
      } else {
        try {
          const isFxDebugRun = localStorage.getItem('geo.sim.v2.fxDebugRun') === '1'
          if (isFxDebugRun) {
            const shouldPause = !!st && st !== 'paused' && st !== 'stopped' && st !== 'error'
            if (shouldPause) {
              await deps.realMode.pause()
            }
          }
        } catch {
          // ignore
        }
        return deps.real.runId
      }
    }

    if (!String(deps.real.accessToken ?? '').trim()) {
      throw new Error('Missing access token')
    }

    if (!String(deps.real.selectedScenarioId ?? '').trim()) {
      await deps.realMode.refreshScenarios()
    }

    if (!String(deps.real.selectedScenarioId ?? '').trim()) {
      const detail = String(deps.real.lastError ?? '').trim()
      throw new Error(detail ? `Failed to select scenario for FX debug: ${detail}` : 'No scenario selected for FX debug')
    }

    try {
      localStorage.setItem('geo.sim.v2.fxDebugRun', '1')
    } catch {
      // ignore
    }

    await deps.realMode.startRun({ mode: 'real', pauseImmediately: true })

    if (!deps.real.runId) {
      const msg = String(deps.real.lastError ?? '')
      const looksLikeConflict = msg.includes('HTTP 409') || msg.includes(' 409 ') || msg.toLowerCase().includes('conflict')
      if (looksLikeConflict) {
        try {
          const active = await getActiveRun({ apiBase: deps.real.apiBase, accessToken: deps.real.accessToken })
          const activeRunId = String(active.run_id ?? '').trim()
          if (activeRunId) {
            deps.real.runId = activeRunId
            await deps.realMode.refreshRunStatus()
            try {
              const st = toLower(deps.real.runStatus?.state)
              const shouldPause = !!st && st !== 'paused' && st !== 'stopped' && st !== 'error'
              if (shouldPause) {
                await deps.realMode.pause()
              }
            } catch {
              // ignore
            }
            await deps.realMode.refreshSnapshot()
            return deps.real.runId
          }
        } catch {
          // fall through to error
        }
      }
    }

    if (!deps.real.runId) {
      const detail = String(deps.real.lastError ?? '').trim()
      throw new Error(detail ? `Failed to start run for FX debug: ${detail}` : 'Failed to start run for FX debug')
    }

    return deps.real.runId
  }

  function ensureRunSerialized(): Promise<string> {
    if (!_ensureRunPromise) {
      _ensureRunPromise = ensureRunForFxDebug().finally(() => {
        _ensureRunPromise = null
      })
    }
    return _ensureRunPromise
  }

  async function waitForRealSseOpen(opts?: { timeoutMs?: number }): Promise<boolean> {
    const timeoutMs = Math.max(0, Number(opts?.timeoutMs ?? 0))
    if (timeoutMs <= 0) return deps.real.sseState === 'open'
    if (deps.real.sseState === 'open') return true

    return await new Promise<boolean>((resolve) => {
      if (disposed) return resolve(false)
      pendingWaitResolves.add(resolve)

      let done = false
      const cleanup = () => {
        if (done) return
        done = true
        pendingWaitResolves.delete(resolve)
        stopWatch()
        window.clearTimeout(timer)
      }

      const stopWatch = watch(
        () => deps.real.sseState,
        (st) => {
          if (st === 'open') {
            cleanup()
            resolve(true)
          }
        },
        { flush: 'post' },
      )

      const timer = window.setTimeout(() => {
        cleanup()
        resolve(deps.real.sseState === 'open')
      }, timeoutMs)
    })
  }

  async function runTxOnce(): Promise<void> {
    if (!deps.isFxDebugEnabled.value) return
    if (!deps.real.accessToken) throw new Error('Missing access token')

    deps.markDemoActivity()
    busy.value = true
    try {
      const runId = await ensureRunSerialized()
      await waitForRealSseOpen({ timeoutMs: 1200 })
      await actionTxOnce({ apiBase: deps.real.apiBase, accessToken: deps.real.accessToken }, runId, {
        equivalent: deps.effectiveEq.value,
        seed: `ui-fx-debug-tx:${Date.now()}`,
        client_action_id: `ui-fx-debug-tx:${Date.now()}`,
      })
    } finally {
      busy.value = false
    }
  }

  async function runClearingOnce(): Promise<void> {
    if (!deps.isFxDebugEnabled.value) return
    if (!deps.real.accessToken) throw new Error('Missing access token')

    deps.markDemoActivity()
    busy.value = true
    try {
      const runId = await ensureRunSerialized()
      await waitForRealSseOpen({ timeoutMs: 1200 })
      await actionClearingOnce({ apiBase: deps.real.apiBase, accessToken: deps.real.accessToken }, runId, {
        equivalent: deps.effectiveEq.value,
        seed: `ui-fx-debug-clearing:${Date.now()}`,
        client_action_id: `ui-fx-debug-clearing:${Date.now()}`,
      })
    } finally {
      busy.value = false
    }
  }

  return { busy, ensureRunSerialized, runTxOnce, runClearingOnce, waitForRealSseOpen, dispose }
}
