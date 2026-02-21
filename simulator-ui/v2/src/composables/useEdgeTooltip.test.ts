import { ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'
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

  describe('edgeTooltipStyle — overlay size caching (§7.1 regression)', () => {
    it('getBoundingClientRect on tooltip element is called only once per null→key show transition, not on every pointermove', () => {
      // Build a minimal host DOM tree: host > tooltipEl[aria-label="Edge tooltip"]
      const host = document.createElement('div')
      const tooltipEl = document.createElement('div')
      tooltipEl.setAttribute('aria-label', 'Edge tooltip')
      host.appendChild(tooltipEl)

      // Spy on the tooltip element's getBoundingClientRect (the DOM measurement we cache).
      const tooltipRectSpy = vi
        .spyOn(tooltipEl, 'getBoundingClientRect')
        .mockReturnValue({ width: 220, height: 80, top: 0, left: 0, right: 220, bottom: 80, x: 0, y: 0, toJSON: () => ({}) } as DOMRect)

      // Also stub the host rect so placeOverlayNearAnchor gets a real viewport size.
      vi.spyOn(host, 'getBoundingClientRect').mockReturnValue({
        width: 800, height: 600, top: 0, left: 0, right: 800, bottom: 600, x: 0, y: 0, toJSON: () => ({}),
      } as DOMRect)

      const hoveredEdge = { key: null as string | null, screenX: 100, screenY: 100 }

      const { edgeTooltipStyle } = useEdgeTooltip({
        hostEl: ref(host),
        hoveredEdge,
        clamp: (v) => v,
        getUnit: () => 'UAH',
      })

      // ── First show: null → key triggers the DOM measurement ──
      hoveredEdge.key = 'node-A→node-B'
      edgeTooltipStyle()

      expect(tooltipRectSpy).toHaveBeenCalledTimes(1)

      // ── Second call: same key, cursor moved (simulates pointermove) ──
      // Must NOT re-measure; cached size is reused.
      hoveredEdge.screenX = 120
      edgeTooltipStyle()

      expect(tooltipRectSpy).toHaveBeenCalledTimes(1)
    })

    it('edgeTooltipStyle returns identical style on repeated calls for the same key (smoke)', () => {
      const host = document.createElement('div')
      vi.spyOn(host, 'getBoundingClientRect').mockReturnValue({
        width: 800, height: 600, top: 0, left: 0, right: 800, bottom: 600, x: 0, y: 0, toJSON: () => ({}),
      } as DOMRect)

      const hoveredEdge = { key: 'node-A→node-B' as string | null, screenX: 100, screenY: 100 }

      const { edgeTooltipStyle } = useEdgeTooltip({
        hostEl: ref(host),
        hoveredEdge,
        clamp: (v) => v,
        getUnit: () => 'UAH',
      })

      const style1 = edgeTooltipStyle()
      const style2 = edgeTooltipStyle()

      expect(style1).toEqual(style2)
    })
  })
})
