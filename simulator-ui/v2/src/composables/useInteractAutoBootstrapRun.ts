export const INTERACT_AUTOSTART_SCENARIO_ID = 'clearing-demo-10'

export function useInteractAutoBootstrapRun(deps: {
  isInteractUi: () => boolean
  isRealMode: () => boolean
  real: {
    runId: string | null
    selectedScenarioId: string | null
    loadingScenarios?: boolean
    scenarios?: unknown[] | null
  }
  realMode: {
    refreshScenarios: () => Promise<void>
    startRun: (opts: { mode: 'real'; intensityPercent: number; pauseImmediately: true }) => Promise<void>
  }
}): {
  ensureSerialized: () => Promise<void>
} {
  let _ensureInteractRunPromise: Promise<void> | null = null

  async function ensureRunForInteract(): Promise<void> {
    if (!deps.isInteractUi() || !deps.isRealMode()) return
    if (deps.real.runId) return

    if (!String(deps.real.selectedScenarioId ?? '').trim()) {
      if (!deps.real.loadingScenarios && (deps.real.scenarios?.length ?? 0) === 0) {
        await deps.realMode.refreshScenarios()
      }

      const scenarios = deps.real.scenarios ?? []

      const preferred = scenarios.find(
        (s) => String((s as any)?.scenario_id ?? '') === INTERACT_AUTOSTART_SCENARIO_ID,
      )
      if (preferred) {
        deps.real.selectedScenarioId = INTERACT_AUTOSTART_SCENARIO_ID
      } else if (scenarios.length > 0) {
        deps.real.selectedScenarioId = String((scenarios[0] as any)?.scenario_id ?? '').trim()
      }
    }

    if (!String(deps.real.selectedScenarioId ?? '').trim()) return

    await deps.realMode.startRun({ mode: 'real', intensityPercent: 0, pauseImmediately: true })
  }

  function ensureSerialized(): Promise<void> {
    if (!_ensureInteractRunPromise) {
      _ensureInteractRunPromise = ensureRunForInteract().finally(() => {
        _ensureInteractRunPromise = null
      })
    }
    return _ensureInteractRunPromise
  }

  return { ensureSerialized }
}
