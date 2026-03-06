import type { ScenarioSummary } from '../api/simulatorTypes'

export const INTERACT_AUTOSTART_SCENARIO_ID = 'clearing-demo-10'

function readScenarioId(scenario: ScenarioSummary | null | undefined): string {
  return String(scenario?.scenario_id ?? '').trim()
}

export function useInteractAutoBootstrapRun(deps: {
  isInteractUi: () => boolean
  isRealMode: () => boolean
  real: {
    runId: string | null
    selectedScenarioId: string | null
    loadingScenarios?: boolean
    scenarios?: ScenarioSummary[] | null
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
        (s) => readScenarioId(s) === INTERACT_AUTOSTART_SCENARIO_ID,
      )
      if (preferred) {
        deps.real.selectedScenarioId = INTERACT_AUTOSTART_SCENARIO_ID
      } else if (scenarios.length > 0) {
        deps.real.selectedScenarioId = readScenarioId(scenarios[0])
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
