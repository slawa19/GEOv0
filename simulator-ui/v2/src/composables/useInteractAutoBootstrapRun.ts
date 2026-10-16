import type { RunStatus, ScenarioSummary } from '../api/simulatorTypes'
import { isLiveRunState } from '../utils/status'

/** The scenario started when none is selected yet and the catalogue offers it (unchanged since the Interact bootstrap). */
export const INTERACT_AUTOSTART_SCENARIO_ID = 'clearing-demo-10'

/** 043 S2 (decision 2): the intensity of a run the screen starts by itself when the person has no stored preference. */
export const AUTOSTART_DEFAULT_INTENSITY_PERCENT = 30

function readScenarioId(scenario: ScenarioSummary | null | undefined): string {
  return String(scenario?.scenario_id ?? '').trim()
}

export type AutostartDecision = 'start' | 'attached' | 'unknown'

/**
 * 043 S2 (decision 2): what opening the demo does with the run it found, once the opening is over.
 *   `start`    - there is no run, or the run found has ended (`stopped`, `error`): a live run is started;
 *   `attached` - a live run is on screen (`running`, `paused`, `created`, `stopping`): it is left AS IT IS. A run
 *                somebody paused is not resumed - a pause is a deliberate act, and opening a page is not;
 *   `unknown`  - a run id is held but its status could not be read: nothing is started, so that one open page cannot
 *                put a second run next to a live one it failed to see.
 */
export function autostartDecision(runId: string | null | undefined, status: RunStatus | null | undefined): AutostartDecision {
  if (!String(runId ?? '').trim()) return 'start'
  if (!status) return 'unknown'
  return isLiveRunState(status.state) ? 'attached' : 'start'
}

/**
 * 043 S2: opening the demo is a live run. ONE attempt per page load, made when the opening is over (the caller calls
 * `onOpened` once): a run the person stops afterwards is not restarted behind their back, and a failed start leaves its
 * error on screen (`startRun` records it) with Start under "Подробнее" as the way to try again.
 *
 * The run is started with the existing `startRun` - not paused, at the person's stored intensity (the default 30 when
 * none is stored; the caller resets a stored 0). When the person is already in a manual action at that moment (a panel
 * is open, a participant chosen - decision 3), the run is started paused instead, so no automatic payment runs over it.
 *
 * Until S2 this file was the Interact bootstrap: `?ui=interact` only, `intensityPercent: 0, pauseImmediately: true`.
 */
export function useInteractAutoBootstrapRun(deps: {
  isLiveUi: () => boolean
  /** True while a manual action holds the screen (decision 3): the run is then started paused. */
  isManualActionActive: () => boolean
  real: {
    runId: string | null
    runStatus?: RunStatus | null
    selectedScenarioId: string | null
    intensityPercent: number
    loadingScenarios?: boolean
    scenarios?: ScenarioSummary[] | null
  }
  realMode: {
    refreshScenarios: () => Promise<void>
    startRun: (opts: { intensityPercent: number; pauseImmediately: boolean }) => Promise<void>
  }
}): {
  onOpened: () => Promise<AutostartDecision | 'no-scenario' | 'off'>
} {
  let attempted = false

  async function onOpened(): Promise<AutostartDecision | 'no-scenario' | 'off'> {
    if (attempted) return 'off'
    attempted = true
    if (!deps.isLiveUi()) return 'off'

    const decision = autostartDecision(deps.real.runId, deps.real.runStatus)
    if (decision !== 'start') return decision

    if (!String(deps.real.selectedScenarioId ?? '').trim()) {
      if (!deps.real.loadingScenarios && (deps.real.scenarios?.length ?? 0) === 0) {
        await deps.realMode.refreshScenarios()
      }
      const scenarios = deps.real.scenarios ?? []
      const preferred = scenarios.find((s) => readScenarioId(s) === INTERACT_AUTOSTART_SCENARIO_ID)
      if (preferred) deps.real.selectedScenarioId = INTERACT_AUTOSTART_SCENARIO_ID
      else if (scenarios.length > 0) deps.real.selectedScenarioId = readScenarioId(scenarios[0])
    }
    if (!String(deps.real.selectedScenarioId ?? '').trim()) return 'no-scenario'

    await deps.realMode.startRun({
      intensityPercent: deps.real.intensityPercent > 0 ? deps.real.intensityPercent : AUTOSTART_DEFAULT_INTENSITY_PERCENT,
      pauseImmediately: deps.isManualActionActive(),
    })
    return 'start'
  }

  return { onOpened }
}
