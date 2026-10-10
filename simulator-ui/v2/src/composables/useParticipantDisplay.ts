import { inject, provide, type ComputedRef, type InjectionKey, type Ref } from 'vue'

import { displayParticipantId } from '../utils/participantDisplayId'

type ScenarioIdSource = Ref<string | null | undefined> | ComputedRef<string | null | undefined>

/**
 * The scenario of the scene on screen, for ONE purpose: removing its namespace from participant ids that are
 * printed to a person (`displayParticipantId`). Provided once by `SimulatorAppRoot`; a component mounted without
 * a provider (a unit test, a fixture-only page) simply shows ids as they are.
 */
const SHOWN_SCENARIO_ID: InjectionKey<ScenarioIdSource> = Symbol('simulator.shownScenarioId')

export function provideShownScenarioId(source: ScenarioIdSource): void {
  provide(SHOWN_SCENARIO_ID, source)
}

/** `(pid) => text for a person`. Reactive: reads the scenario id at call time. */
export function useParticipantDisplayId(): (pid: string | null | undefined) => string {
  const source = inject(SHOWN_SCENARIO_ID, null)
  return (pid) => displayParticipantId(pid, source?.value)
}

/** For composables that are not components and so cannot inject: the same rule on an explicit scenario id. */
export function participantDisplayIdFor(scenarioId: () => string | null | undefined): (pid: string | null | undefined) => string {
  return (pid) => displayParticipantId(pid, scenarioId())
}

/** The scene's scenario: the shown run's, else the selected one (the scene before a run is that scenario's preview). */
export function shownScenarioIdOf(
  runStatus: { scenario_id?: string | null } | null | undefined,
  selectedScenarioId: string | null | undefined,
): string {
  return String(runStatus?.scenario_id ?? '').trim() || String(selectedScenarioId ?? '').trim()
}
