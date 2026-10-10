/**
 * How a participant id is SHOWN to a person.
 *
 * Seed scenarios namespace their participant ids as `<scenario id>:<community pid>` (the generator,
 * `scripts/generate_simulator_seed_scenarios.py::scenario_participant_id`). The namespace makes the id unique across
 * scenarios on the wire; it carries nothing a person reading a label needs, and it makes labels three lines long.
 *
 * The rule is exact, not a guess from the form of the id: the prefix is removed only when the id begins with
 * EXACTLY `<scenario id of the scene on screen>:` and something follows it. Every other id (another scenario's prefix,
 * no prefix, an unknown scene) is returned unchanged. An id used as a value, key or API argument is never passed
 * through this function: it is for display text only.
 */
export function displayParticipantId(pid: string | null | undefined, scenarioId: string | null | undefined): string {
  const id = String(pid ?? '')
  const scenario = String(scenarioId ?? '').trim()
  if (!scenario) return id
  const prefix = `${scenario}:`
  if (id.length > prefix.length && id.startsWith(prefix)) return id.slice(prefix.length)
  return id
}
