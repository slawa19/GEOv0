import type { RunStatus, ScenarioSummary } from '../api/simulatorTypes'
import { isJwtLike } from '../utils/isJwtLike'
import { isLiveRunState } from '../utils/status'

/**
 * 034 S9-A (the owner's answer П-S9-1 (а), 2026-10-21): the network a person opens when no choice is stored - the
 * 10-participant «Демо клиринга», on which a clearing is seen in the first minute (`s9-plan.md` §1.2). ONE rule for the
 * catalogue load (`useSimulatorApp.ts`, `ensureScenarioSelectionValid`) and the opening's autostart below; until S9-A
 * the catalogue load preferred `greenfield-village-100-realistic-v2` and the autostart's preference for this one was
 * never reached.
 */
export const DEFAULT_SCENARIO_ID = 'clearing-demo-10'

/** 043 S2 (decision 2): the operator's default intensity (an admin token is held) - Start and the opening's run. */
export const AUTOSTART_DEFAULT_INTENSITY_PERCENT = 30

/**
 * 044 stage 2 (spec amendments (vii), (viii)): the default intensity of a VISITOR's run - a screen without an admin
 * token, the public demo. 10 concurrent visitor runs were measured on one process at 15 (64 % of a core, tick stretch
 * 1.24); at the operator's 30 the same 10 saturate the core. A stored preference still wins.
 *
 * 049 S2 (decision 10, the owner's word of 2026-10-20): 15 -> 5, the step «обычно» of the visitor's three (2 / 5 / 15 % -
 * «медленно / обычно / быстро», `TopBar.vue`): about one payment ATTEMPT a second (049-11: part is refused for lack of a route), slow enough to read the network's events.
 */
export const VISITOR_DEFAULT_INTENSITY_PERCENT = 5

/** The default intensity of this screen: the operator's when it holds an admin token (not a participant JWT), else the visitor's. */
export function defaultIntensityPercentFor(accessToken: string | null | undefined): number {
  const t = String(accessToken ?? '').trim()
  return t && !isJwtLike(t) ? AUTOSTART_DEFAULT_INTENSITY_PERCENT : VISITOR_DEFAULT_INTENSITY_PERCENT
}

function readScenarioId(scenario: ScenarioSummary | null | undefined): string {
  return String(scenario?.scenario_id ?? '').trim()
}

/**
 * The scenario picked when no choice is stored, or the stored one is not in the catalogue: `DEFAULT_SCENARIO_ID` if the
 * catalogue offers it, else the catalogue's first scenario ('' for an empty catalogue). A stored choice the catalogue
 * offers (`geo.sim.v2.selectedScenarioId` - the administrator's under «Подробнее», or an earlier pick) is not replaced:
 * the callers ask only when there is none.
 */
export function pickDefaultScenarioId(scenarios: readonly ScenarioSummary[] | null | undefined): string {
  const list = scenarios ?? []
  return list.find((s) => readScenarioId(s) === DEFAULT_SCENARIO_ID) ? DEFAULT_SCENARIO_ID : readScenarioId(list[0])
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
 * error on screen (`startRun` records it); a refusal by the global limit of runs offers "Try again" in that line (046
 * wave 1); after any other failure the way back is Start under "Подробнее" for the administrator (048 S2: the run row
 * is behind the admin token) and a reload of the page for a visitor (a reload makes this one attempt again).
 *
 * The run is started with the existing `startRun` - not paused, at the person's stored intensity (when none is stored,
 * the screen's default: 5 for a visitor (049 S2; was 15), 30 for the operator - `defaultIntensityPercentFor`, 044 stage 2; a stored 0 is
 * dropped when it is read, `useSimulatorApp.ts`, 043 S3). When the person is already in a manual action at that moment (a panel
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
    accessToken?: string | null
    loadingScenarios?: boolean
    scenarios?: ScenarioSummary[] | null
  }
  realMode: {
    startRun: (opts: { intensityPercent: number; pauseImmediately: boolean }) => Promise<void>
  }
}): {
  onOpened: () => Promise<AutostartDecision | 'no-scenario' | 'off'>
  /** 044 stage 2 (BACKLOG 043-23): an explicit Stop spends the attempt not made yet - the opening starts nothing after it. */
  cancel: () => void
} {
  let attempted = false

  async function onOpened(): Promise<AutostartDecision | 'no-scenario' | 'off'> {
    if (attempted) return 'off'
    attempted = true
    if (!deps.isLiveUi()) return 'off'

    const decision = autostartDecision(deps.real.runId, deps.real.runStatus)
    if (decision !== 'start') return decision

    // The catalogue was read by the opening itself (043 S2, review B5: no second read here - an empty or failed
    // catalogue has already ended the opening and put its reason on the error line).
    if (!String(deps.real.selectedScenarioId ?? '').trim()) deps.real.selectedScenarioId = pickDefaultScenarioId(deps.real.scenarios)
    if (!String(deps.real.selectedScenarioId ?? '').trim()) return 'no-scenario'

    await deps.realMode.startRun({
      intensityPercent: deps.real.intensityPercent > 0 ? deps.real.intensityPercent : defaultIntensityPercentFor(deps.real.accessToken),
      pauseImmediately: deps.isManualActionActive(),
    })
    return 'start'
  }

  return {
    onOpened,
    cancel: () => {
      attempted = true
    },
  }
}
