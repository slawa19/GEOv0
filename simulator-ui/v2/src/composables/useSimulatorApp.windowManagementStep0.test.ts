import { describe, expect, it, vi } from 'vitest'

import { __closeTopmostOverlayOnOutsideClickPolicy, __selectNodeFromCanvasStep0 } from './useSimulatorApp'

describe('useSimulatorApp - window management Step 0 policy', () => {
  it('empty click cancels interact; closes edge-detail then node-card when both are open (then clears selection)', () => {
    const cancelInteract = vi.fn()
    const selectNode = vi.fn()

    const closeEdgeDetail = vi.fn()
    const closeNodeCard = vi.fn()

    const state = {
      edgeOpen: true,
      nodeOpen: true,
    }

    closeEdgeDetail.mockImplementation(() => {
      state.edgeOpen = false
    })

    closeNodeCard.mockImplementation(() => {
      state.nodeOpen = false
    })

    const closeTopmostOverlayOnOutsideClick = vi.fn(() => {
      __closeTopmostOverlayOnOutsideClickPolicy({
        edgeDetail: {
          open: state.edgeOpen,
          closeOnOutsideClick: true,
          close: closeEdgeDetail,
        },
        nodeCard: {
          open: state.nodeOpen,
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

    expect(cancelInteract).toHaveBeenCalledTimes(1)

    // Policy: close both inspector cards on empty click.
    expect(closeTopmostOverlayOnOutsideClick).toHaveBeenCalledTimes(2)
    expect(closeEdgeDetail).toHaveBeenCalledTimes(1)
    expect(closeNodeCard).toHaveBeenCalledTimes(1)

    expect(selectNode).toHaveBeenCalledWith(null)
  })

  it('dblclick on empty canvas cancels interact twice (dblclick → 2 clicks)', () => {
    const cancelInteract = vi.fn()
    const selectNode = vi.fn()

    const closeEdgeDetail = vi.fn()
    const closeNodeCard = vi.fn()

    const state = {
      edgeOpen: true,
      nodeOpen: true,
    }

    closeEdgeDetail.mockImplementation(() => {
      state.edgeOpen = false
    })

    closeNodeCard.mockImplementation(() => {
      state.nodeOpen = false
    })

    const closeTopmostOverlayOnOutsideClick = vi.fn(() => {
      __closeTopmostOverlayOnOutsideClickPolicy({
        edgeDetail: {
          open: state.edgeOpen,
          closeOnOutsideClick: true,
          close: closeEdgeDetail,
        },
        nodeCard: {
          open: state.nodeOpen,
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

    // Reset overlay state between the two clicks (in real app, windows are already closed).
    state.edgeOpen = true
    state.nodeOpen = true

    __selectNodeFromCanvasStep0({
      id: null,
      isInteractPickingPhase: true,
      interactSelectNode: vi.fn(),
      closeTopmostOverlayOnOutsideClick,
      cancelInteract,
      selectNode,
    })

    expect(cancelInteract).toHaveBeenCalledTimes(2)
    // Each empty click closes both cards.
    expect(closeTopmostOverlayOnOutsideClick).toHaveBeenCalledTimes(4)
    expect(closeEdgeDetail).toHaveBeenCalledTimes(2)
    expect(closeNodeCard).toHaveBeenCalledTimes(2)
    expect(selectNode).toHaveBeenCalledWith(null)
  })
})

