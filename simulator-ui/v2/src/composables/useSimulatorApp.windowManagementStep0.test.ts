import { describe, expect, it, vi } from 'vitest'

import { __closeTopmostOverlayOnOutsideClickPolicy, __selectNodeFromCanvasStep0 } from './useSimulatorApp'

describe('useSimulatorApp - window management Step 0 policy', () => {
  it('empty click does NOT cancel interact; closes edge-detail first when open (then clears selection)', () => {
    const cancelInteract = vi.fn()
    const selectNode = vi.fn()

    const closeEdgeDetail = vi.fn()
    const closeNodeCard = vi.fn()

    const closeTopmostOverlayOnOutsideClick = vi.fn(() => {
      __closeTopmostOverlayOnOutsideClickPolicy({
        edgeDetail: {
          open: true,
          closeOnOutsideClick: true,
          close: closeEdgeDetail,
        },
        nodeCard: {
          open: true,
          closeOnOutsideClick: true,
          close: closeNodeCard,
        },
      })
    })

    __selectNodeFromCanvasStep0({
      id: null,
      isInteractPickingPhase: false,
      interactSelectNode: vi.fn(),
      closeTopmostOverlayOnOutsideClick,
      cancelInteract,
      selectNode,
    })

    expect(cancelInteract).toHaveBeenCalledTimes(0)

    expect(closeTopmostOverlayOnOutsideClick).toHaveBeenCalledTimes(1)
    expect(closeEdgeDetail).toHaveBeenCalledTimes(1)
    expect(closeNodeCard).toHaveBeenCalledTimes(0)

    expect(selectNode).toHaveBeenCalledWith(null)
  })

  it('wm=1: dblclick on empty canvas does NOT cancel interact flow (dblclick → 2 clicks)', () => {
    const cancelInteract = vi.fn()
    const selectNode = vi.fn()

    const closeEdgeDetail = vi.fn()
    const closeNodeCard = vi.fn()

    const closeTopmostOverlayOnOutsideClick = vi.fn(() => {
      __closeTopmostOverlayOnOutsideClickPolicy({
        edgeDetail: {
          open: true,
          closeOnOutsideClick: true,
          close: closeEdgeDetail,
        },
        nodeCard: {
          open: true,
          closeOnOutsideClick: true,
          close: closeNodeCard,
        },
      })
    })

    // A browser dblclick typically produces: click, click, dblclick.
    // The important regression guard: the click(s) on empty canvas must NOT cancel interact.
    __selectNodeFromCanvasStep0({
      id: null,
      isInteractPickingPhase: true,
      interactSelectNode: vi.fn(),
      closeTopmostOverlayOnOutsideClick,
      cancelInteract,
      selectNode,
    })

    __selectNodeFromCanvasStep0({
      id: null,
      isInteractPickingPhase: true,
      interactSelectNode: vi.fn(),
      closeTopmostOverlayOnOutsideClick,
      cancelInteract,
      selectNode,
    })

    expect(cancelInteract).toHaveBeenCalledTimes(0)
    expect(closeTopmostOverlayOnOutsideClick).toHaveBeenCalledTimes(2)
    expect(closeEdgeDetail).toHaveBeenCalledTimes(2)
    expect(closeNodeCard).toHaveBeenCalledTimes(0)
    expect(selectNode).toHaveBeenCalledWith(null)
  })
})

