import { ref } from 'vue'
import { describe, expect, it } from 'vitest'
import { useSnapshotIndex } from './useSnapshotIndex'

describe('useSnapshotIndex', () => {
  it('returns null when no snapshot', () => {
    const snapshotRef = ref<any>(null)
    const { getNodeById } = useSnapshotIndex({
      getSnapshot: () => snapshotRef.value,
    })

    expect(getNodeById('A')).toBe(null)
  })

  it('indexes nodes by id and updates when snapshot changes', () => {
    const snapshotRef = ref<any>({
      equivalent: 'UAH',
      nodes: [
        { id: 'A', name: 'Alice' },
        { id: 'B', name: 'Bob' },
      ],
      links: [],
    })

    const { getNodeById, nodeById } = useSnapshotIndex({
      getSnapshot: () => snapshotRef.value,
    })

    expect(nodeById.value.size).toBe(2)
    expect(getNodeById('A')?.name).toBe('Alice')

    snapshotRef.value = {
      equivalent: 'UAH',
      nodes: [{ id: 'C', name: 'Carol' }],
      links: [],
    }

    expect(nodeById.value.size).toBe(1)
    expect(getNodeById('A')).toBe(null)
    expect(getNodeById('C')?.name).toBe('Carol')
  })
})
