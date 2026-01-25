import { ref } from 'vue'
import { describe, expect, it } from 'vitest'
import { useEdgeTooltip } from './useEdgeTooltip'

describe('useEdgeTooltip', () => {
  it('formatEdgeAmountText uses used+trust_limit when present', () => {
    const api = useEdgeTooltip({
      hostEl: ref(null),
      hoveredEdge: { key: null, screenX: 0, screenY: 0 },
      clamp: (v) => v,
      getUnit: () => 'UAH',
    })

    const s = api.formatEdgeAmountText({ source: 'A', target: 'B', used: '10', trust_limit: '30' })
    expect(s).toContain('from:')
    expect(s).toContain('to:')
    expect(s).toContain('UAH')
  })

  it('formatEdgeAmountText falls back to available', () => {
    const api = useEdgeTooltip({
      hostEl: ref(null),
      hoveredEdge: { key: null, screenX: 0, screenY: 0 },
      clamp: (v) => v,
      getUnit: () => 'UAH',
    })

    const s = api.formatEdgeAmountText({ source: 'A', target: 'B', available: 7 })
    expect(s).toContain('7')
  })
})
