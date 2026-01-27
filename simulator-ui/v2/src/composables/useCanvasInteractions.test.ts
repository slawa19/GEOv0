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

  it('pointerup clears selection only when camera reports click', () => {
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

    h.onCanvasPointerUp({} as any)
    expect(setSelectedNodeId).not.toHaveBeenCalled()

    cameraUp.mockReturnValueOnce(true)
    h.onCanvasPointerUp({} as any)
    expect(setSelectedNodeId).toHaveBeenLastCalledWith(null)
    expect(clearHoveredEdge).toHaveBeenCalledTimes(1)
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
