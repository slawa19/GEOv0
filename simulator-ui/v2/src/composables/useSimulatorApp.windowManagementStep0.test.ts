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
})

