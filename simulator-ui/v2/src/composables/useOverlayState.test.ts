import { afterEach, describe, expect, it, vi } from 'vitest'
import { useOverlayState } from './useOverlayState'

describe('useOverlayState', () => {
  it('activeEdges supports TTL pruning', () => {
    let now = 0

    const overlay = useOverlayState({
      getLayoutNodeById: () => ({ __x: 0, __y: 0 }),
      sizeForNode: () => ({ w: 40, h: 40 }),
      getCameraZoom: () => 1,
      setFlash: () => undefined,
      resetFxState: () => undefined,
      nowMs: () => now,
    })

    overlay.addActiveEdge('A->B', 10)
    overlay.addActiveEdge('B->C', 100)
    expect(overlay.activeEdges.has('A->B')).toBe(true)
    expect(overlay.activeEdges.has('B->C')).toBe(true)

    now = 20
    overlay.pruneActiveEdges(now)
    expect(overlay.activeEdges.has('A->B')).toBe(false)
    expect(overlay.activeEdges.has('B->C')).toBe(true)
  })

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

  it('pushFloatingLabel auto-generates unique ids even within the same nowMs tick', () => {
    let now = 123.4

    const overlay = useOverlayState({
      getLayoutNodeById: () => ({ __x: 0, __y: 0 }),
      sizeForNode: () => ({ w: 40, h: 40 }),
      getCameraZoom: () => 1,
      setFlash: () => undefined,
      resetFxState: () => undefined,
      nowMs: () => now,
    })

    // Same timestamp, no explicit id.
    overlay.pushFloatingLabel({ nodeId: 'A', text: '1', color: '#fff' })
    overlay.pushFloatingLabel({ nodeId: 'A', text: '2', color: '#fff' })

    expect(overlay.floatingLabels).toHaveLength(2)
    expect(overlay.floatingLabels[0]!.id).not.toBe(overlay.floatingLabels[1]!.id)
  })

  it('pushFloatingLabel does not drop throttle-window events when prior label is missing', () => {
    let now = 0

    const overlay = useOverlayState({
      getLayoutNodeById: () => ({ __x: 0, __y: 0 }),
      sizeForNode: () => ({ w: 40, h: 40 }),
      getCameraZoom: () => 1,
      setFlash: () => undefined,
      resetFxState: () => undefined,
      nowMs: () => now,
    })

    overlay.pushFloatingLabel({ nodeId: 'A', text: 't1', color: '#fff', throttleKey: 'k', throttleMs: 100 })
    expect(overlay.floatingLabels.length).toBe(1)

    // Simulate eviction/prune of the label, but keep throttle map state.
    overlay.floatingLabels.splice(0, overlay.floatingLabels.length)
    expect(overlay.floatingLabels.length).toBe(0)

    now = 50
    overlay.pushFloatingLabel({ nodeId: 'A', text: 't2', color: '#fff', throttleKey: 'k', throttleMs: 100 })

    // Event happens within throttle window; should still emit a new label if none exists.
    expect(overlay.floatingLabels.length).toBe(1)
    expect(overlay.floatingLabels[0]!.text).toBe('t2')
  })

  it("pushFloatingLabel restarts throttled 'amt:' labels (new id) to avoid mid-flight updates", () => {
    let now = 0

    const overlay = useOverlayState({
      getLayoutNodeById: () => ({ __x: 0, __y: 0 }),
      sizeForNode: () => ({ w: 40, h: 40 }),
      getCameraZoom: () => 1,
      setFlash: () => undefined,
      resetFxState: () => undefined,
      nowMs: () => now,
    })

    overlay.pushFloatingLabel({ nodeId: 'A', text: '+1 EQ', color: '#fff', throttleKey: 'amt:+:A', throttleMs: 100 })
    expect(overlay.floatingLabels).toHaveLength(1)
    const id1 = overlay.floatingLabels[0]!.id

    now = 50
    overlay.pushFloatingLabel({ nodeId: 'A', text: '+2 EQ', color: '#fff', throttleKey: 'amt:+:A', throttleMs: 100 })
    expect(overlay.floatingLabels).toHaveLength(1)
    const id2 = overlay.floatingLabels[0]!.id

    expect(id2).not.toBe(id1)
    expect(overlay.floatingLabels[0]!.text).toBe('+2 EQ')
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

    for (let i = 0; i < 121; i++) {
      now = i
      overlay.pushFloatingLabel({ nodeId: 'A', text: String(i), color: '#fff', id: i })
    }

    expect(overlay.floatingLabels.length).toBe(120)
    expect(overlay.floatingLabels[0]!.id).toBe(1)
    expect(overlay.floatingLabels[119]!.id).toBe(120)
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

  it('floatingLabelsView retriggers for pending labels when node appears in layout later', () => {
    vi.useFakeTimers()
    let now = 0

    // Start with node NOT in layout.
    let nodeAvailable = false
    const overlay = useOverlayState({
      getLayoutNodeById: (id) => (nodeAvailable ? { __x: 50, __y: 60 } : undefined),
      sizeForNode: () => ({ w: 20, h: 20 }),
      getCameraZoom: () => 1,
      setFlash: () => undefined,
      resetFxState: () => undefined,
      nowMs: () => now,
    })

    overlay.pushFloatingLabel({ nodeId: 'B', text: 'receiver', color: '#0f0', id: 10 })

    // Label is in array but view should be empty (node not in layout).
    expect(overlay.floatingLabels).toHaveLength(1)
    expect(overlay.floatingLabelsView.value).toHaveLength(0)

    // Simulate node becoming available in layout.
    nodeAvailable = true

    // Before retrigger timer fires — still cached empty result.
    expect(overlay.floatingLabelsView.value).toHaveLength(0)

    // Advance past the pending label retrigger delay (120ms).
    vi.advanceTimersByTime(150)

    // Now the computed should re-evaluate and find the node.
    const view = overlay.floatingLabelsView.value
    expect(view).toHaveLength(1)
    expect(view[0]).toMatchObject({ id: 10, text: 'receiver', color: '#0f0' })

    vi.useRealTimers()
  })

  it('resetOverlays cancels pending label retrigger timer', () => {
    vi.useFakeTimers()
    let now = 0

    const overlay = useOverlayState({
      getLayoutNodeById: () => undefined, // always missing
      sizeForNode: () => ({ w: 20, h: 20 }),
      getCameraZoom: () => 1,
      setFlash: () => undefined,
      resetFxState: () => undefined,
      nowMs: () => now,
    })

    overlay.pushFloatingLabel({ nodeId: 'C', text: 'x', color: '#fff', id: 20 })

    // Trigger computed to schedule retrigger.
    expect(overlay.floatingLabelsView.value).toHaveLength(0)

    // Reset clears labels and cancels timer.
    overlay.resetOverlays()
    expect(overlay.floatingLabels).toHaveLength(0)

    // Advance past retrigger — should not throw or cause issues.
    vi.advanceTimersByTime(200)

    expect(overlay.floatingLabelsView.value).toHaveLength(0)

    vi.useRealTimers()
  })
})


