import { computed, nextTick, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import { __closeTopmostOverlayOnOutsideClickPolicy, __selectNodeFromCanvasStep0 } from './useSimulatorApp'
import { useInteractMode } from './useInteractMode'
import type { GraphSnapshot } from '../types'

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
      isInteractCanvasNodePickPhase: false,
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
      isInteractCanvasNodePickPhase: true,
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
      isInteractCanvasNodePickPhase: true,
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

  // 046 wave 3 (advisor's decision 2026-10-19): the busy gate asked a native `confirm()` and cancelled on "yes"; spec 046
  // forbids the native confirm, and the owner's rule (no edge cases in the UI) means no dialog at all. While an operation is
  // in flight an outside click is IGNORED: no question, no cancel, no close - the operation finishes on its own. The two
  // tests of the confirm (true / false) are replaced by these.
  it('P0-2: busy + outside-click → ignored: no confirm, no cancel, no close, no selection', () => {
    const confirmSpy = vi.fn(() => true)
    vi.stubGlobal('confirm', confirmSpy)
    try {
      const cancelInteract = vi.fn()
      const selectNode = vi.fn()
      const closeTopmostOverlayOnOutsideClick = vi.fn()

      __selectNodeFromCanvasStep0({
        id: null,
        isInteractCanvasNodePickPhase: false,
        interactSelectNode: vi.fn(),
        isInteractBusy: true,
        closeTopmostOverlayOnOutsideClick,
        cancelInteract,
        selectNode,
      })

      expect(confirmSpy, 'no native confirm').not.toHaveBeenCalled()
      expect(cancelInteract).not.toHaveBeenCalled()
      expect(closeTopmostOverlayOnOutsideClick).not.toHaveBeenCalled()
      expect(selectNode).not.toHaveBeenCalled()
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('P0-2: busy + outside-click on the real interact mode → the payment in flight completes (it is not cancelled)', async () => {
    const confirmSpy = vi.fn(() => true)
    vi.stubGlobal('confirm', confirmSpy)
    try {
      let answer: (v: unknown) => void = () => undefined
      const sendPayment = vi.fn(() => new Promise((resolve) => { answer = resolve }))
      const snapshot = ref<GraphSnapshot | null>({
        equivalent: 'UAH', generated_at: '2026-01-01T00:00:00Z',
        nodes: [{ id: 'alice', name: 'Alice', type: 'person', status: 'active' }, { id: 'bob', name: 'Bob', type: 'person', status: 'active' }],
        links: [{ source: 'bob', target: 'alice', used: '0.00', available: '10.00', trust_limit: '10.00', status: 'active' }],
      })
      const actions = {
        actionsDisabled: ref(false), sendPayment, fetchParticipants: vi.fn(async () => []),
        fetchTrustlines: vi.fn(async () => []), fetchPaymentTargets: vi.fn(async () => []),
      } as unknown as Parameters<typeof useInteractMode>[0]['actions']
      const im = useInteractMode({ actions, runId: computed(() => 'run-1'), equivalent: computed(() => 'UAH'), snapshot })
      im.startPaymentFlow()
      im.selectNode('alice')
      im.selectNode('bob')
      const done = im.confirmPayment('1.00')
      await nextTick()
      expect(im.busy.value, 'counter-check: the payment is in flight').toBe(true)

      // The click outside, wired as the app wires it (`useSimulatorApp.ts` `selectNodeFromCanvas`).
      __selectNodeFromCanvasStep0({
        id: null,
        isInteractCanvasNodePickPhase: false,
        interactSelectNode: (id) => im.selectNode(id),
        isInteractBusy: !!im.busy.value,
        closeTopmostOverlayOnOutsideClick: vi.fn(),
        cancelInteract: () => im.cancelFromCanvas(),
        selectNode: vi.fn(),
      })
      expect(im.cancelling.value, 'not cancelled').toBe(false)

      answer({ ok: true, payment_id: 'p1', from_pid: 'alice', to_pid: 'bob', equivalent: 'UAH', amount: '1.00', status: 'COMMITTED', routes: [] })
      await done
      expect(confirmSpy, 'no native confirm').not.toHaveBeenCalled()
      expect(im.paymentOutcome.value?.kind, 'the payment completed and its result is shown').toBe('success')
      expect(im.history.length, 'and it is in the history').toBe(1)
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('P0-2: not busy + outside-click → cancelInteract called (and no confirm)', () => {
    const confirmSpy = vi.fn(() => false)
    vi.stubGlobal('confirm', confirmSpy)
    try {
      const cancelInteract = vi.fn()
      const selectNode = vi.fn()
      const closeTopmostOverlayOnOutsideClick = vi.fn()

      __selectNodeFromCanvasStep0({
        id: null,
        isInteractCanvasNodePickPhase: false,
        interactSelectNode: vi.fn(),
        isInteractBusy: false,
        closeTopmostOverlayOnOutsideClick,
        cancelInteract,
        selectNode,
      })

      expect(confirmSpy).not.toHaveBeenCalled()
      expect(cancelInteract).toHaveBeenCalledTimes(1)
    } finally {
      vi.unstubAllGlobals()
    }
  })
})

