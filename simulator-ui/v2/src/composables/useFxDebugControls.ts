import type { Ref } from 'vue'
import { onUnmounted, watch } from 'vue'

import { toLower } from '../utils/stringHelpers'

/**
 * REMNANT of the Demo UI (`?ui=demo`): the paused-run bootstrap (`ensureRunSerialized`) and the SSE-open wait, nothing
 * else. The server debug actions `tx-once` / `clearing-once` and their buttons are gone (042 S1, 2026-10-16): an event
 * about a payment is born only in the core, never drawn on request. The module itself is removed in programme 043 S3,
 * after 042 `T4205` (the offline playlist workbench).
 */
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
    startRun: (opts: { pauseImmediately: true }) => Promise<void>
    pause: () => Promise<void>
    refreshSnapshot: () => Promise<void>
  }
}): {
  ensureRunSerialized: () => Promise<string>
  waitForRealSseOpen: (opts?: { timeoutMs?: number }) => Promise<boolean>
  dispose: () => void
} {
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

    // No access-token guard, here and in the two actions below (034 S8, F-034-D7): an anonymous visitor's session
    // is the `geo_sim_sid` cookie, which `startRun` ensures and the transport sends (`credentials: 'include'`).
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

    await deps.realMode.startRun({ pauseImmediately: true })

    // A 409 is `startRun`'s to handle: it reads the status of the error and attaches to the active run itself.
    // What is left here is only `lastError`, a text - never a reason to pick a run (034 S8, F-034-D14).
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

  return { ensureRunSerialized, waitForRealSseOpen, dispose }
}
