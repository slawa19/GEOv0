import { computed, unref, type ComputedRef, type Ref } from 'vue'

import { participantLabel } from '../utils/participants'

type MaybeRef<T> = T | Ref<T> | ComputedRef<T>
type MaybeRefOrGetter<T> = MaybeRef<T> | (() => T)

function toValue<T>(src: MaybeRefOrGetter<T>): T {
  return typeof src === 'function' ? (src as () => T)() : unref(src)
}

export type UseParticipantsListInput<P extends { pid?: string | null; name?: string | null }> = {
  participants: MaybeRefOrGetter<readonly P[] | null | undefined>
  /** Used for building `toParticipants`: excludes currently selected "from" pid. */
  fromParticipantId?: MaybeRefOrGetter<string | null | undefined>
  /**
   * Tri-state filtering for `toParticipants`.
   *  - `undefined` => unknown (fallback allowed)
   *  - `Set` (incl. empty) => known (no fallback)
   */
  availableTargetIds: MaybeRefOrGetter<Set<string> | undefined>
}

export function useParticipantsList<P extends { pid?: string | null; name?: string | null }>(
  input: UseParticipantsListInput<P>,
) {
  const participantsSorted = computed<P[]>(() => {
    const items = toValue(input.participants)
    const arr = Array.isArray(items) ? items : []

    return [...arr]
      .filter((p) => (p?.pid ?? '').trim())
      .sort((a, b) => participantLabel(a).localeCompare(participantLabel(b)))
  })

  const toParticipants = computed<P[]>(() => {
    const from = ((input.fromParticipantId ? toValue(input.fromParticipantId) : '') ?? '').trim()
    const targets = toValue(input.availableTargetIds)

    // Known-empty: do NOT fallback.
    if (targets !== undefined && targets.size === 0) return []

    return participantsSorted.value.filter((p) => {
      const pid = (p?.pid ?? '').trim()
      if (from && pid === from) return false
      if (targets === undefined) return true
      return targets.has(pid)
    })
  })

  return { participantsSorted, toParticipants }
}

