/**
 * 054-B-9: the tooltip's size was measured only on the null->key call of `edgeTooltipStyle`. On the real path that call
 * comes BEFORE the tooltip exists: `EdgeTooltip.vue` renders under `v-if="edge.key"`, and its `:style` is the computed of
 * `SimulatorAppRoot.vue` that calls `edgeTooltipStyle` with the key already set. So the measurement never found the
 * element and the edge clamp always used the fallback 260x120. The stand renders in that order: first call with the key
 * set and no element, then the element appears, then the pointer moves.
 */
import { ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useEdgeTooltip } from './useEdgeTooltip'

const rect = (width: number, height: number) =>
  ({ width, height, top: 0, left: 0, right: width, bottom: height, x: 0, y: 0, toJSON: () => ({}) }) as DOMRect

function stand(size: { w: number; h: number }) {
  const host = document.createElement('div')
  vi.spyOn(host, 'getBoundingClientRect').mockReturnValue(rect(800, 600))
  const hoveredEdge = { key: null as string | null, screenX: 500, screenY: 100 }
  const api = useEdgeTooltip({ hostEl: ref(host), hoveredEdge, clamp: (v) => v, getUnit: () => 'UAH' })
  const tooltipEl = document.createElement('div')
  tooltipEl.setAttribute('data-testid', 'edge-tooltip')
  const measure = vi.spyOn(tooltipEl, 'getBoundingClientRect').mockReturnValue(rect(size.w, size.h))
  return {
    ...api, hoveredEdge, measure,
    render: () => host.appendChild(tooltipEl),
    unrender: () => tooltipEl.remove(),
  }
}

afterEach(() => { vi.restoreAllMocks() })

describe('054-B-9: the edge tooltip is clamped by its measured size', () => {
  it('measures the rendered tooltip once it exists: a 400-wide tooltip at x=500 is clamped to 800-400-10', () => {
    const s = stand({ w: 400, h: 200 })
    s.hoveredEdge.key = 'A→B'
    expect(s.edgeTooltipStyle(), 'before the element exists: the fallback size (500+12 fits 260)').toMatchObject({ left: '512px' })
    s.render()
    s.hoveredEdge.screenX = 501
    expect(s.edgeTooltipStyle()).toMatchObject({ left: '390px' })
    expect(s.measure, 'measured once for this show').toHaveBeenCalledTimes(1)
    s.hoveredEdge.screenX = 502
    s.edgeTooltipStyle()
    expect(s.measure, 'not on every pointermove (§7.1)').toHaveBeenCalledTimes(1)
  })

  it('measures again on the next show (another content), once', () => {
    const s = stand({ w: 400, h: 200 })
    s.hoveredEdge.key = 'A→B'
    s.edgeTooltipStyle(); s.render(); s.edgeTooltipStyle()
    s.hoveredEdge.key = null
    s.edgeTooltipStyle(); s.unrender()
    s.measure.mockReturnValue(rect(300, 100))
    s.hoveredEdge.key = 'C→D'
    expect(s.edgeTooltipStyle(), 'the last measured size until the new one is measured').toMatchObject({ left: '390px' })
    s.render()
    expect(s.edgeTooltipStyle()).toMatchObject({ left: '490px' })
    expect(s.measure).toHaveBeenCalledTimes(2)
  })
})
