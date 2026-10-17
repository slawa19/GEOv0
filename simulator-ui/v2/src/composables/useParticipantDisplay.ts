import { inject, provide, type ComputedRef, type InjectionKey, type Ref } from 'vue'

import { displayParticipantId } from '../utils/participantDisplayId'

type ScenarioIdSource = Ref<string | null | undefined> | ComputedRef<string | null | undefined>

/**
 * The scenario of the scene on screen, for ONE purpose: removing its namespace from participant ids that are
 * printed to a person (`displayParticipantId`). Provided once by `SimulatorAppRoot`; a component mounted without
 * a provider (a unit test, a fixture-only page) simply shows ids as they are.
 */
const SHOWN_SCENARIO_ID: InjectionKey<ScenarioIdSource> = Symbol('simulator.shownScenarioId')

/**
 * 044 `T4405`: a run's participants are fresh rows under identities of that run (`<run id>:p<n>`), which mean nothing
 * to a person. So a participant id that is PRINTED is shown as the participant's NAME when the scene knows it (the
 * backend gives a participant without a name its scenario's source pid as its name, so a name is always there for a
 * run's participant). Provided once by `SimulatorAppRoot` from the snapshot's nodes.
 */
type ParticipantNameOf = (pid: string) => string | null | undefined
const PARTICIPANT_NAME_OF: InjectionKey<ParticipantNameOf> = Symbol('simulator.participantNameOf')

export function provideShownScenarioId(source: ScenarioIdSource): void {
  provide(SHOWN_SCENARIO_ID, source)
}

export function provideParticipantNameOf(nameOf: ParticipantNameOf): void {
  provide(PARTICIPANT_NAME_OF, nameOf)
}

/** The text a person reads for a participant id: its name when known, else the id without the scene's namespace. */
export function shownParticipant(
  pid: string | null | undefined,
  scenarioId: string | null | undefined,
  nameOf?: ParticipantNameOf | null,
): string {
  const id = String(pid ?? '')
  const name = id && nameOf ? String(nameOf(id) ?? '').trim() : ''
  return name || displayParticipantId(id, scenarioId)
}

/** `(pid) => text for a person`. Reactive: reads the scenario id and the names at call time. */
export function useParticipantDisplayId(): (pid: string | null | undefined) => string {
  const source = inject(SHOWN_SCENARIO_ID, null)
  const nameOf = inject(PARTICIPANT_NAME_OF, null)
  return (pid) => shownParticipant(pid, source?.value, nameOf)
}

/** For composables that are not components and so cannot inject: the same rule on an explicit scenario id. */
export function participantDisplayIdFor(
  scenarioId: () => string | null | undefined,
  nameOf?: ParticipantNameOf,
): (pid: string | null | undefined) => string {
  return (pid) => shownParticipant(pid, scenarioId(), nameOf)
}

/** The scene's scenario: the shown run's, else the selected one (the scene before a run is that scenario's preview). */
export function shownScenarioIdOf(
  runStatus: { scenario_id?: string | null } | null | undefined,
  selectedScenarioId: string | null | undefined,
): string {
  return String(runStatus?.scenario_id ?? '').trim() || String(selectedScenarioId ?? '').trim()
}
