import { describe, expect, it, vi } from 'vitest'
import { useCanvasInteractions } from './useCanvasInteractions'

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

    h.onCanvasClick({ clientX: 1, clientY: 2 } as any)
    expect(setSelectedNodeId).toHaveBeenLastCalledWith('A')

    h.onCanvasClick({ clientX: 9, clientY: 9 } as any)
    expect(setSelectedNodeId).toHaveBeenLastCalledWith(null)
  })

  it('double click selects node (single click is selection-only too)', () => {
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

    h.onCanvasClick({ clientX: 3, clientY: 4 } as any)
    expect(setSelectedNodeId).toHaveBeenLastCalledWith('B')

    h.onCanvasDblClick({ clientX: 3, clientY: 4 } as any)
    expect(setSelectedNodeId).toHaveBeenLastCalledWith('B')
  })

  it('dblclick can be intercepted via onNodeDblClick hook', () => {
    const setSelectedNodeId = vi.fn()
    const onNodeDblClick = vi.fn(() => true)

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

    h.onCanvasDblClick({ clientX: 7, clientY: 8 } as any)
    expect(onNodeDblClick).toHaveBeenCalledTimes(1)
    expect(setSelectedNodeId).toHaveBeenCalledTimes(0)
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

    h.onCanvasPointerDown({} as any)
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

    h.onCanvasPointerMove({} as any)
    expect(hoverMove).toHaveBeenCalledTimes(1)
    expect(cameraMove).not.toHaveBeenCalled()

    pan = true
    h.onCanvasPointerMove({} as any)
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
    h.onCanvasPointerUp({} as any)
    expect(setSelectedNodeId).not.toHaveBeenCalled()

    // wasClick=true: selection logic is delegated to onCanvasClick; pointerup must NOT clear it
    cameraUp.mockReturnValueOnce(true)
    h.onCanvasPointerUp({} as any)
    expect(setSelectedNodeId).not.toHaveBeenCalled()
    expect(clearHoveredEdge).not.toHaveBeenCalled()
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

    h.onCanvasWheel({} as any)
    expect(wheel).not.toHaveBeenCalled()

    dragState.active = false
    h.onCanvasWheel({} as any)
    expect(wheel).toHaveBeenCalledTimes(1)
  })
})
