import { describe, expect, it } from 'vitest'
import { useOverlayState } from './useOverlayState'

describe('useOverlayState', () => {
  it('pushFloatingLabel respects throttleKey/throttleMs', () => {
    let now = 0

    const overlay = useOverlayState({
      getLayoutNodeById: () => ({ __x: 0, __y: 0 }),
      sizeForNode: () => ({ w: 40, h: 40 }),
      getCameraZoom: () => 1,
      setFlash: () => undefined,
      resetFxState: () => undefined,
      nowMs: () => now,
    })

    overlay.pushFloatingLabel({ nodeId: 'A', text: 't', color: '#fff', throttleKey: 'k', throttleMs: 100, id: 1 })
    expect(overlay.floatingLabels.length).toBe(1)

    now = 50
    overlay.pushFloatingLabel({ nodeId: 'A', text: 't', color: '#fff', throttleKey: 'k', throttleMs: 100, id: 2 })
    expect(overlay.floatingLabels.length).toBe(1)

    now = 101
    overlay.pushFloatingLabel({ nodeId: 'A', text: 't', color: '#fff', throttleKey: 'k', throttleMs: 100, id: 3 })
    expect(overlay.floatingLabels.length).toBe(2)
  })

  it('pushFloatingLabel enforces maxFloatingLabels', () => {
    let now = 0

    const overlay = useOverlayState({
      getLayoutNodeById: () => ({ __x: 0, __y: 0 }),
      sizeForNode: () => ({ w: 40, h: 40 }),
      getCameraZoom: () => 1,
      setFlash: () => undefined,
      resetFxState: () => undefined,
      nowMs: () => now,
    })

    for (let i = 0; i < 61; i++) {
      now = i
      overlay.pushFloatingLabel({ nodeId: 'A', text: String(i), color: '#fff', id: i })
    }

    expect(overlay.floatingLabels.length).toBe(60)
    expect(overlay.floatingLabels[0]!.id).toBe(1)
    expect(overlay.floatingLabels[59]!.id).toBe(60)
  })

  it('pruneFloatingLabels drops expired entries', () => {
    let now = 0

    const overlay = useOverlayState({
      getLayoutNodeById: () => ({ __x: 0, __y: 0 }),
      sizeForNode: () => ({ w: 40, h: 40 }),
      getCameraZoom: () => 1,
      setFlash: () => undefined,
      resetFxState: () => undefined,
      nowMs: () => now,
    })

    overlay.pushFloatingLabel({ nodeId: 'A', text: 'x', color: '#fff', ttlMs: 10, id: 1 })
    expect(overlay.floatingLabels.length).toBe(1)

    now = 20
    overlay.pruneFloatingLabels(now)
    expect(overlay.floatingLabels.length).toBe(0)
  })

  it('floatingLabelsView anchors above node top edge', () => {
    let now = 0

    const overlay = useOverlayState({
      getLayoutNodeById: () => ({ __x: 100, __y: 200 }),
      sizeForNode: () => ({ w: 40, h: 20 }),
      getCameraZoom: () => 1,
      setFlash: () => undefined,
      resetFxState: () => undefined,
      nowMs: () => now,
    })

    overlay.pushFloatingLabel({ nodeId: 'A', text: 'x', color: '#fff', id: 1 })

    const view = overlay.floatingLabelsView.value
    expect(view).toHaveLength(1)
    expect(view[0]).toMatchObject({ id: 1, x: 100, y: 170 })
  })
})
