import { describe, expect, it } from 'vitest'
import { useFloatingLabelsViewFx } from './useFloatingLabelsViewFx'

describe('useFloatingLabelsViewFx', () => {
  it('forces glow=0 when webdriver', () => {
    const { floatingLabelsViewFx } = useFloatingLabelsViewFx({
      getFloatingLabelsView: () => [{ id: 1, x: 10, y: 20, text: 'x', color: '#fff' }],
      isWebDriver: () => true,
      getLayoutNodes: () => [{ id: 'n', __x: 0, __y: 0 } as any],
      sizeForNode: () => ({ w: 20, h: 20 }),
      worldToScreen: (x, y) => ({ x, y }),
    })

    expect(floatingLabelsViewFx.value).toEqual([{ id: 1, x: 10, y: 20, text: 'x', color: '#fff', glow: 0 }])
  })

  it('computes glow > 0 when label overlaps a node boundary', () => {
    const { floatingLabelsViewFx } = useFloatingLabelsViewFx({
      getFloatingLabelsView: () => [{ id: 1, x: 0, y: 0, text: 'x', color: '#fff' }],
      isWebDriver: () => false,
      getLayoutNodes: () => [{ id: 'n', __x: 0, __y: 0 } as any],
      sizeForNode: () => ({ w: 20, h: 20 }),
      worldToScreen: (x, y) => ({ x, y }),
    })

    expect(floatingLabelsViewFx.value[0]!.glow).toBeGreaterThan(0)
  })
})
