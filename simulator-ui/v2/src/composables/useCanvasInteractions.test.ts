import { describe, expect, it, vi } from 'vitest'
import { useCanvasInteractions } from './useCanvasInteractions'

type MouseEventLike = Pick<MouseEvent, 'clientX' | 'clientY'>
type PointerEventLike = Partial<Pick<PointerEvent, 'pointerId' | 'clientX' | 'clientY'>>
type WheelEventLike = Partial<Pick<WheelEvent, 'deltaY' | 'clientX' | 'clientY'>>

function mouseEvent(init: MouseEventLike): MouseEvent {
  return init as unknown as MouseEvent
}

function pointerEvent(init: PointerEventLike = {}): PointerEvent {
  return ({ pointerId: 1, clientX: 0, clientY: 0, ...init }) as unknown as PointerEvent
}

function wheelEvent(init: WheelEventLike = {}): WheelEvent {
  return ({ deltaY: 0, clientX: 0, clientY: 0, ...init }) as unknown as WheelEvent
}

describe('useCanvasInteractions', () => {
  it('selects node on click, clears selection on empty hit', () => {
    const setSelectedNodeId = vi.fn()

    const h = useCanvasInteractions({
      isTestMode: () => false,
      pickNodeAt: (x, y) => (x === 1 && y === 2 ? { id: 'A' } : null),
      setSelectedNodeId,
      clearHoveredEdge: vi.fn(),
      dragToPin: {
        dragState: { active: false },
        onPointerDown: () => false,
        onPointerMove: () => false,
        onPointerUp: () => false,
      },
      cameraSystem: {
        onPointerDown: vi.fn(),
        onPointerMove: vi.fn(),
        onPointerUp: vi.fn(() => false),
        onWheel: vi.fn(),
      },
      edgeHover: { onPointerMove: vi.fn() },
      getPanActive: () => false,
    })

    h.onCanvasClick(mouseEvent({ clientX: 1, clientY: 2 }))
    expect(setSelectedNodeId).toHaveBeenLastCalledWith('A')

    h.onCanvasClick(mouseEvent({ clientX: 9, clientY: 9 }))
    expect(setSelectedNodeId).toHaveBeenLastCalledWith(null)
  })

  it('double click selects node (single click is selection-only too)', () => {
    vi.useFakeTimers()
    const setSelectedNodeId = vi.fn()

    const h = useCanvasInteractions({
      isTestMode: () => false,
      pickNodeAt: (x, y) => (x === 3 && y === 4 ? { id: 'B' } : null),
      setSelectedNodeId,
      clearHoveredEdge: vi.fn(),
      dragToPin: {
        dragState: { active: false },
        onPointerDown: () => false,
        onPointerMove: () => false,
        onPointerUp: () => false,
      },
      cameraSystem: {
        onPointerDown: vi.fn(),
        onPointerMove: vi.fn(),
        onPointerUp: vi.fn(() => false),
        onWheel: vi.fn(),
      },
      edgeHover: { onPointerMove: vi.fn() },
      getPanActive: () => false,
    })

    try {
      h.onCanvasClick(mouseEvent({ clientX: 3, clientY: 4 }))
      expect(setSelectedNodeId).toHaveBeenLastCalledWith('B')

      // RACE-1: dblclick is debounced (150ms). No immediate selection side-effect.
      h.onCanvasDblClick(mouseEvent({ clientX: 3, clientY: 4 }))
      expect(setSelectedNodeId).toHaveBeenCalledTimes(1)

      vi.advanceTimersByTime(150)
      expect(setSelectedNodeId).toHaveBeenCalledTimes(2)
      expect(setSelectedNodeId).toHaveBeenLastCalledWith('B')
    } finally {
      vi.clearAllTimers()
      vi.useRealTimers()
    }
  })

  it('dblclick can be intercepted via onNodeDblClick hook', () => {
    vi.useFakeTimers()
    const setSelectedNodeId = vi.fn()
    const onNodeDblClick = vi.fn<
      (node: { id: string }, ptr: { clientX: number; clientY: number }) => boolean
    >(() => true)

    const h = useCanvasInteractions({
      isTestMode: () => false,
      pickNodeAt: (x, y) => (x === 7 && y === 8 ? { id: 'C' } : null),
      setSelectedNodeId,
      clearHoveredEdge: vi.fn(),
      onNodeDblClick,
      dragToPin: {
        dragState: { active: false },
        onPointerDown: () => false,
        onPointerMove: () => false,
        onPointerUp: () => false,
      },
      cameraSystem: {
        onPointerDown: vi.fn(),
        onPointerMove: vi.fn(),
        onPointerUp: vi.fn(() => false),
        onWheel: vi.fn(),
      },
      edgeHover: { onPointerMove: vi.fn() },
      getPanActive: () => false,
    })

    try {
      h.onCanvasDblClick(mouseEvent({ clientX: 7, clientY: 8 }))
      // Debounced: not called immediately.
      expect(onNodeDblClick).toHaveBeenCalledTimes(0)

      vi.advanceTimersByTime(150)
      expect(onNodeDblClick).toHaveBeenCalledTimes(1)
      expect(setSelectedNodeId).toHaveBeenCalledTimes(0)
    } finally {
      vi.clearAllTimers()
      vi.useRealTimers()
    }
  })

  it('debounces rapid dblclicks and applies only the last node (RACE-1)', () => {
    vi.useFakeTimers()
    const setSelectedNodeId = vi.fn()

    // In the real app this hook opens the node card window (wm.open({ reuse })).
    // This test asserts we only execute that action once, and only for the last node.
    const onNodeDblClick = vi.fn<
      (node: { id: string }, ptr: { clientX: number; clientY: number }) => boolean
    >(() => true)

    const h = useCanvasInteractions({
      isTestMode: () => false,
      pickNodeAt: (x) => {
        const ids = ['A', 'B', 'C', 'D', 'E']
        const idx = Number(x) - 1
        return idx >= 0 && idx < ids.length ? { id: ids[idx] } : null
      },
      setSelectedNodeId,
      clearHoveredEdge: vi.fn(),
      onNodeDblClick,
      dragToPin: {
        dragState: { active: false },
        onPointerDown: () => false,
        onPointerMove: () => false,
        onPointerUp: () => false,
      },
      cameraSystem: {
        onPointerDown: vi.fn(),
        onPointerMove: vi.fn(),
        onPointerUp: vi.fn(() => false),
        onWheel: vi.fn(),
      },
      edgeHover: { onPointerMove: vi.fn() },
      getPanActive: () => false,
    })

    try {
      // 5 rapid dblclicks: A → B → C → D → E within <150ms between events.
      const coords = [1, 2, 3, 4, 5]
      for (let i = 0; i < coords.length; i++) {
        h.onCanvasDblClick(mouseEvent({ clientX: coords[i], clientY: 1 }))
        if (i < coords.length - 1) vi.advanceTimersByTime(20)
      }

      // Nothing should have executed yet.
      expect(onNodeDblClick).toHaveBeenCalledTimes(0)
      expect(setSelectedNodeId).toHaveBeenCalledTimes(0)

      // Still pending before 150ms elapsed since the last dblclick.
      vi.advanceTimersByTime(149)
      expect(onNodeDblClick).toHaveBeenCalledTimes(0)

      // Flush the debounce window: exactly one action, for the last nodeId (E).
      vi.advanceTimersByTime(1)
      expect(onNodeDblClick).toHaveBeenCalledTimes(1)
      const firstArg = onNodeDblClick.mock.calls[0]?.[0]
      expect(firstArg?.id).toBe('E')
      expect(setSelectedNodeId).toHaveBeenCalledTimes(0)
    } finally {
      vi.clearAllTimers()
      vi.useRealTimers()
    }
  })

  it('ignores pointerdown in test mode', () => {
    const cameraDown = vi.fn()

    const h = useCanvasInteractions({
      isTestMode: () => true,
      pickNodeAt: () => null,
      setSelectedNodeId: vi.fn(),
      clearHoveredEdge: vi.fn(),
      dragToPin: {
        dragState: { active: false },
        onPointerDown: vi.fn(() => false),
        onPointerMove: vi.fn(() => false),
        onPointerUp: vi.fn(() => false),
      },
      cameraSystem: {
        onPointerDown: cameraDown,
        onPointerMove: vi.fn(),
        onPointerUp: vi.fn(() => false),
        onWheel: vi.fn(),
      },
      edgeHover: { onPointerMove: vi.fn() },
      getPanActive: () => false,
    })

    h.onCanvasPointerDown(pointerEvent())
    expect(cameraDown).not.toHaveBeenCalled()
  })

  it('pointermove delegates to edgeHover and only pans when active', () => {
    const cameraMove = vi.fn()
    const hoverMove = vi.fn()

    let pan = false

    const h = useCanvasInteractions({
      isTestMode: () => false,
      pickNodeAt: () => null,
      setSelectedNodeId: vi.fn(),
      clearHoveredEdge: vi.fn(),
      dragToPin: {
        dragState: { active: false },
        onPointerDown: () => false,
        onPointerMove: () => false,
        onPointerUp: () => false,
      },
      cameraSystem: {
        onPointerDown: vi.fn(),
        onPointerMove: cameraMove,
        onPointerUp: vi.fn(() => false),
        onWheel: vi.fn(),
      },
      edgeHover: { onPointerMove: hoverMove },
      getPanActive: () => pan,
    })

    h.onCanvasPointerMove(pointerEvent())
    expect(hoverMove).toHaveBeenCalledTimes(1)
    expect(cameraMove).not.toHaveBeenCalled()

    pan = true
    h.onCanvasPointerMove(pointerEvent())
    expect(hoverMove).toHaveBeenCalledTimes(2)
    expect(cameraMove).toHaveBeenCalledTimes(1)
  })

  it('pointerup never directly clears selection — delegates entirely to onCanvasClick', () => {
    const setSelectedNodeId = vi.fn()
    const clearHoveredEdge = vi.fn()

    const cameraUp = vi.fn(() => false)

    const h = useCanvasInteractions({
      isTestMode: () => false,
      pickNodeAt: () => null,
      setSelectedNodeId,
      clearHoveredEdge,
      dragToPin: {
        dragState: { active: false },
        onPointerDown: () => false,
        onPointerMove: () => false,
        onPointerUp: () => false,
      },
      cameraSystem: {
        onPointerDown: vi.fn(),
        onPointerMove: vi.fn(),
        onPointerUp: cameraUp,
        onWheel: vi.fn(),
      },
      edgeHover: { onPointerMove: vi.fn() },
      getPanActive: () => false,
    })

    // wasClick=false: nothing should happen
    h.onCanvasPointerUp(pointerEvent())
    expect(setSelectedNodeId).not.toHaveBeenCalled()

    // wasClick=true: selection logic is delegated to onCanvasClick; pointerup must NOT clear it
    cameraUp.mockReturnValueOnce(true)
    h.onCanvasPointerUp(pointerEvent())
    expect(setSelectedNodeId).not.toHaveBeenCalled()
    expect(clearHoveredEdge).not.toHaveBeenCalled()
  })

  it('does NOT suppress click when dragToPin consumes pointerup but no drag occurred', () => {
    const setSelectedNodeId = vi.fn()

    const dragState = { active: true, dragging: false }

    const h = useCanvasInteractions({
      isTestMode: () => false,
      pickNodeAt: (x, y) => (x === 1 && y === 2 ? { id: 'A' } : null),
      setSelectedNodeId,
      clearHoveredEdge: vi.fn(),
      dragToPin: {
        dragState,
        onPointerDown: () => true,
        onPointerMove: () => true,
        // IMPORTANT: this simulates current dragToPin contract: pointerup is consumed
        // even for a simple click (no actual drag).
        onPointerUp: () => {
          dragState.active = false
          dragState.dragging = false
          return true
        },
      },
      cameraSystem: {
        onPointerDown: vi.fn(),
        onPointerMove: vi.fn(),
        onPointerUp: vi.fn(() => false),
        onWheel: vi.fn(),
      },
      edgeHover: { onPointerMove: vi.fn() },
      getPanActive: () => false,
    })

    // Complete the pointer gesture (consumed by dragToPin).
    h.onCanvasPointerUp(pointerEvent({ pointerId: 1 }))

    // Browser still fires `click` after pointerup; it must NOT be suppressed.
    h.onCanvasClick(mouseEvent({ clientX: 1, clientY: 2 }))
    expect(setSelectedNodeId).toHaveBeenCalledTimes(1)
    expect(setSelectedNodeId).toHaveBeenLastCalledWith('A')
  })

  it('suppresses click after a real drag-to-pin gesture', () => {
    const setSelectedNodeId = vi.fn()

    const dragState = { active: true, dragging: true }

    const h = useCanvasInteractions({
      isTestMode: () => false,
      pickNodeAt: () => ({ id: 'A' }),
      setSelectedNodeId,
      clearHoveredEdge: vi.fn(),
      dragToPin: {
        dragState,
        onPointerDown: () => true,
        onPointerMove: () => true,
        onPointerUp: () => {
          // Typical drag end: state resets.
          dragState.active = false
          dragState.dragging = false
          return true
        },
      },
      cameraSystem: {
        onPointerDown: vi.fn(),
        onPointerMove: vi.fn(),
        onPointerUp: vi.fn(() => false),
        onWheel: vi.fn(),
      },
      edgeHover: { onPointerMove: vi.fn() },
      getPanActive: () => false,
    })

    // Pointerup after real drag should mark suppressNextClick.
    h.onCanvasPointerUp(pointerEvent({ pointerId: 1 }))

    // The subsequent click must be ignored (otherwise we'd accidentally change selection).
    h.onCanvasClick(mouseEvent({ clientX: 1, clientY: 2 }))
    expect(setSelectedNodeId).not.toHaveBeenCalled()
  })

  it('wheel is ignored during drag-to-pin active', () => {
    const wheel = vi.fn()

    const dragState = { active: true }

    const h = useCanvasInteractions({
      isTestMode: () => false,
      pickNodeAt: () => null,
      setSelectedNodeId: vi.fn(),
      clearHoveredEdge: vi.fn(),
      dragToPin: {
        dragState,
        onPointerDown: () => false,
        onPointerMove: () => false,
        onPointerUp: () => false,
      },
      cameraSystem: {
        onPointerDown: vi.fn(),
        onPointerMove: vi.fn(),
        onPointerUp: vi.fn(() => false),
        onWheel: wheel,
      },
      edgeHover: { onPointerMove: vi.fn() },
      getPanActive: () => false,
    })

    h.onCanvasWheel(wheelEvent())
    expect(wheel).not.toHaveBeenCalled()

    dragState.active = false
    h.onCanvasWheel(wheelEvent())
    expect(wheel).toHaveBeenCalledTimes(1)
  })
})
