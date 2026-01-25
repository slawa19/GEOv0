import { ref } from 'vue'
import { describe, expect, it } from 'vitest'
import { useNodeCard } from './useNodeCard'

describe('useNodeCard', () => {
  it('returns display:none when no host or no selection', () => {
    const selectedNodeId = ref<string | null>(null)

    const api = useNodeCard({
      hostEl: ref(null),
      selectedNodeId,
      getNodeById: () => null,
      getLayoutNodeById: () => null,
      worldToScreen: (x, y) => ({ x, y }),
    })

    expect(api.nodeCardStyle()).toEqual({ display: 'none' })
  })

  it('computes clamped style from worldToScreen', () => {
    const selectedNodeId = ref<string | null>('A')

    const hostEl = ref<any>({
      getBoundingClientRect: () => ({ width: 300, height: 200 }),
    })

    const api = useNodeCard({
      hostEl,
      selectedNodeId,
      getNodeById: (id) => (id ? ({ id } as any) : null),
      getLayoutNodeById: (id) => ({ id, __x: 10, __y: 20 }),
      worldToScreen: () => ({ x: 1000, y: 1000 }),
    })

    const s = api.nodeCardStyle()
    expect(typeof s.left).toBe('string')
    expect(typeof s.top).toBe('string')
  })
})
