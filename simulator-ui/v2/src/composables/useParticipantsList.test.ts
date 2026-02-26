import { describe, expect, it } from 'vitest'
import { ref } from 'vue'

import { useParticipantsList } from './useParticipantsList'

describe('useParticipantsList', () => {
  it('toParticipants: unknown targets (undefined) => all except from', () => {
    const participants = ref([
      { pid: 'a', name: 'Alice' },
      { pid: 'b', name: 'Bob' },
      { pid: 'x', name: 'Xavier' },
    ])
    const fromPid = ref<string | null>('a')
    const availableTargetIds = ref<Set<string> | undefined>(undefined)

    const { toParticipants } = useParticipantsList({
      participants,
      fromParticipantId: fromPid,
      availableTargetIds,
    })

    expect(toParticipants.value.map((p) => p.pid)).toEqual(['b', 'x'])
  })

  it('toParticipants: known-empty targets (Set size 0) => [] (no fallback)', () => {
    const participants = ref([
      { pid: 'a', name: 'Alice' },
      { pid: 'b', name: 'Bob' },
    ])
    const fromPid = ref<string | null>('a')
    const availableTargetIds = ref<Set<string> | undefined>(new Set())

    const { toParticipants } = useParticipantsList({
      participants,
      fromParticipantId: fromPid,
      availableTargetIds,
    })

    expect(toParticipants.value).toEqual([])
  })

  it('toParticipants: known non-empty targets => intersection, excluding from', () => {
    const participants = ref([
      { pid: 'a', name: 'Alice' },
      { pid: 'b', name: 'Bob' },
      { pid: 'x', name: 'Xavier' },
    ])
    const fromPid = ref<string | null>('a')
    const availableTargetIds = ref<Set<string> | undefined>(new Set(['x']))

    const { toParticipants } = useParticipantsList({
      participants,
      fromParticipantId: fromPid,
      availableTargetIds,
    })

    expect(toParticipants.value.map((p) => p.pid)).toEqual(['x'])
  })
})

