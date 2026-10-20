/**
 * 048 `T4840` - the navigator's dead end (advisor's decision 2026-10-19): closing the line window by ANY route ends the
 * "trust line" action, so the action bar returns to idle.
 *
 * Found on a live seeded backend at `7864e090`: "Подробнее" -> "Найти на графе" -> open a line -> close the line window
 * (Escape or x). The phase stayed `editing-trustline` with no window of it on screen: "Линия доверия" active, "Платёж" and
 * "Клиринг" disabled with "Сначала отмените текущее действие (Esc)", and no route back (Escape had no window to close; the
 * keyboard has no canvas to click). The canvas click on an edge enters the same phase - an empty canvas click was its
 * only way out.
 *
 * Measured at the composable level: the real window controller, the real window manager and the real line-window state
 * machine; the interact mode is a stand-in whose `cancel` returns the phase to idle (as `useInteractMode.cancel` does via
 * `fsm.resetToIdle`). What this does NOT see: the DOM of the action bar (the e2e `p048-t4840-navigator-dead-end` walks the
 * real page), the step-0 outside click of the root (it cancels before the window closes - `useSimulatorApp.ts`).
 */
import { describe, expect, it, vi } from 'vitest'
import { computed, createApp, nextTick, reactive, ref, type Ref } from 'vue'

import type { InteractPhase, InteractState } from './useInteractMode'
import { useWindowController } from './useWindowController'
import { useWmEdgeDetail } from './useWmEdgeDetail'
import { useWindowManager } from './windowManager/useWindowManager'

type Harness = {
  ctl: ReturnType<typeof useWindowController>
  wm: ReturnType<typeof useWindowManager>
  edge: ReturnType<typeof useWmEdgeDetail>
  phase: Ref<InteractPhase>
  busy: Ref<boolean>
  fullEditor: Ref<boolean>
  state: InteractState
  cancel: ReturnType<typeof vi.fn>
  /** Where every node is on screen (a live graph moves it; the card follows it - UX-9). */
  center: Ref<{ x: number; y: number }>
  unmount: () => void
}

function mountHarness(): Harness {
  const phase = ref<InteractPhase>('idle')
  const busy = ref(false)
  const fullEditor = ref(false)
  const center = ref({ x: 100, y: 100 })
  const state = reactive({
    phase: 'idle',
    fromPid: null,
    toPid: null,
    selectedEdgeKey: null,
    edgeAnchor: null,
    initiatedWithPrefilledFrom: false,
  }) as unknown as InteractState
  const cancel = vi.fn(() => {
    // `useInteractMode.cancel` -> `fsm.resetToIdle()`: the phase and the selection go back to idle.
    phase.value = 'idle'
    state.fromPid = null
    state.toPid = null
    state.selectedEdgeKey = null
    state.edgeAnchor = null
  })
  const wm = useWindowManager()
  const edge = useWmEdgeDetail()
  let ctl!: ReturnType<typeof useWindowController>
  const app = createApp({
    setup() {
      ctl = useWindowController({
        apiMode: ref('real'),
        isLiveUi: ref(true),
        interactPhase: computed(() => phase.value),
        isFullEditor: fullEditor,
        interactState: state,
        interactMode: {
          state,
          busy,
          cancel,
          setPaymentToPid: () => {},
          setPaymentFromPid: () => {},
          setTrustlineToPid: () => {},
          setTrustlineFromPid: () => {},
        },
        wm,
        wmEdgeDetail: edge,
        getNodeScreenCenter: () => center.value,
      })
      return () => null
    },
  })
  const host = document.createElement('div')
  app.mount(host)
  return { ctl, wm, edge, phase, busy, fullEditor, state, cancel, center, unmount: () => app.unmount() }
}

const ANCHOR = { x: 300, y: 200 }

/** What `inspectEdgeFromNavigator` does (`SimulatorAppRoot.vue`): `interact.mode.selectEdge`, then the line window. */
async function openLineAsTheNavigatorDoes(h: Harness): Promise<void> {
  // `fsm.selectEdge` (useInteractFSM.ts): the pair, the anchor, the phase.
  h.state.fromPid = 'alice'
  h.state.toPid = 'bob'
  h.state.selectedEdgeKey = 'alice→bob'
  h.state.edgeAnchor = { ...ANCHOR }
  h.phase.value = 'editing-trustline'
  h.ctl.uiOpenOrUpdateEdgeDetail({ fromPid: 'alice', toPid: 'bob', anchor: { ...ANCHOR } })
  await nextTick()
  await nextTick()
}

const openLineWindows = (h: Harness) => h.wm.windows.value.filter((w) => w.type === 'edge-detail')

function escape(h: Harness): void {
  h.ctl.onGlobalKeydown(new KeyboardEvent('keydown', { key: 'Escape', cancelable: true }))
}

describe('048 T4840: closing the line window ends the "trust line" action', () => {
  it('Escape on the line window opened from the navigator: the window closes AND the phase returns to idle', async () => {
    const h = mountHarness()
    try {
      await openLineAsTheNavigatorDoes(h)
      expect(openLineWindows(h), 'counter-check: the line window is open').toHaveLength(1)
      expect(h.phase.value).toBe('editing-trustline')

      escape(h)
      await nextTick()
      await nextTick()

      expect(openLineWindows(h)).toHaveLength(0)
      expect(h.cancel).toHaveBeenCalledTimes(1)
      expect(h.phase.value).toBe('idle')
    } finally {
      h.unmount()
    }
  })

  it('the x of the line window (`uiCloseEdgeDetailWindow("action")`): the window closes AND the phase returns to idle', async () => {
    const h = mountHarness()
    try {
      await openLineAsTheNavigatorDoes(h)
      expect(openLineWindows(h), 'counter-check: the line window is open').toHaveLength(1)

      h.ctl.uiCloseEdgeDetailWindow('action')
      await nextTick()
      await nextTick()

      expect(openLineWindows(h)).toHaveLength(0)
      expect(h.cancel).toHaveBeenCalledTimes(1)
      expect(h.phase.value).toBe('idle')
    } finally {
      h.unmount()
    }
  })

  it('after Escape the same line opens again from the navigator (the state machine learnt the window is gone)', async () => {
    const h = mountHarness()
    try {
      await openLineAsTheNavigatorDoes(h)
      escape(h)
      await nextTick()
      await nextTick()
      expect(openLineWindows(h)).toHaveLength(0)

      // The same line again: until T4840 the state machine still held the closed window's id and the same "applied" key,
      // so `applyToWindowManager` returned early - the phase went to `editing-trustline` with no window at all.
      await openLineAsTheNavigatorDoes(h)
      expect(openLineWindows(h)).toHaveLength(1)
      expect(h.edge.state.value).toBe('live')
    } finally {
      h.unmount()
    }
  })

  it('a line window kept alive as the context of a payment: closing it does NOT end the payment', async () => {
    const h = mountHarness()
    try {
      await openLineAsTheNavigatorDoes(h)
      // `onEdgeDetailSendPayment`: the line window is frozen as context, the payment flow takes the phase.
      h.edge.allowKeepAlive({ frozenLink: null })
      h.phase.value = 'confirm-payment'
      h.state.fromPid = 'bob'
      h.state.toPid = 'alice'
      await nextTick()
      await nextTick()
      expect(openLineWindows(h), 'counter-check: the line window is still open as context').toHaveLength(1)

      h.ctl.uiCloseEdgeDetailWindow('action')
      await nextTick()
      await nextTick()

      expect(openLineWindows(h)).toHaveLength(0)
      expect(h.cancel).not.toHaveBeenCalled()
      expect(h.phase.value).toBe('confirm-payment')
    } finally {
      h.unmount()
    }
  })

  it('"Change Limit" closes the line window programmatically into the full editor: the action goes on', async () => {
    const h = mountHarness()
    try {
      await openLineAsTheNavigatorDoes(h)
      // `onEdgeDetailChangeLimit` (SimulatorAppRoot.vue): close ONLY the line window, then the full editor.
      h.edge.close({ suppress: false })
      h.edge.applyToWindowManager(h.wm, { closeReason: 'programmatic' })
      h.fullEditor.value = true
      await nextTick()
      await nextTick()

      expect(openLineWindows(h)).toHaveLength(0)
      expect(h.cancel).not.toHaveBeenCalled()
      expect(h.phase.value).toBe('editing-trustline')
      expect(h.wm.windows.value.some((w) => w.type === 'interact-panel'), 'the full editor took over').toBe(true)
    } finally {
      h.unmount()
    }
  })

  it('046 W3 kept: while a request is in flight Escape and the x are ignored - the window stays and nothing is cancelled', async () => {
    const h = mountHarness()
    try {
      await openLineAsTheNavigatorDoes(h)
      h.busy.value = true

      escape(h)
      h.ctl.uiCloseEdgeDetailWindow('action')
      await nextTick()
      await nextTick()

      expect(openLineWindows(h)).toHaveLength(1)
      expect(h.cancel).not.toHaveBeenCalled()

      // The operation settles; the close works again.
      h.busy.value = false
      escape(h)
      await nextTick()
      await nextTick()
      expect(openLineWindows(h)).toHaveLength(0)
      expect(h.cancel).toHaveBeenCalledTimes(1)
    } finally {
      h.unmount()
    }
  })
})

/**
 * Found by the T4840 walk on a live seeded backend (sibling path "a participant from the navigator, close its card"): on a
 * graph that keeps moving the participant card did not close - Escape and its x closed it and it came straight back. The
 * card follows its node (UX-9) through a trailing throttle; a follow request still pending when the card closed re-opened
 * it 100 ms later (`nodeCardAnchorUpdater` -> `wm.open`), and on a live graph a request is pending almost always.
 */
describe('048 T4840 (sibling): a closed participant card stays closed while its node moves', () => {
  for (const route of ['Escape', 'its x (action)'] as const) {
    it(`closed by ${route} with a follow pending: not re-opened by the trailing follow`, async () => {
      vi.useFakeTimers()
      const h = mountHarness()
      try {
        h.ctl.uiOpenOrUpdateNodeCard({ nodeId: 'alice', anchor: { x: 100, y: 100 } })
        await nextTick()
        const cards = () => h.wm.windows.value.filter((w) => w.type === 'node-card')
        expect(cards(), 'counter-check: the card is open').toHaveLength(1)

        // The node moves (a live graph): the card asks to follow it - one trailing request pending.
        h.center.value = { x: 140, y: 120 }
        await nextTick()
        await nextTick()

        if (route === 'Escape') escape(h)
        else h.wm.close(cards()[0]!.id, 'action')
        await nextTick()
        expect(cards()).toHaveLength(0)

        vi.advanceTimersByTime(500)
        await nextTick()
        expect(cards(), 'the card stays closed').toHaveLength(0)

        // Counter-check (anti-vacuum): the follow still works for an open card - open again, move, the trailing request
        // moves the window to the node.
        h.ctl.uiOpenOrUpdateNodeCard({ nodeId: 'alice', anchor: { x: 140, y: 120 } })
        await nextTick()
        h.center.value = { x: 200, y: 160 }
        await nextTick()
        await nextTick()
        vi.advanceTimersByTime(150)
        await nextTick()
        expect(cards()).toHaveLength(1)
        expect(cards()[0]!.anchor).toMatchObject({ x: 200, y: 160 })
      } finally {
        h.unmount()
        vi.useRealTimers()
      }
    })
  }

  it('switched to another participant with a follow pending: the card stays on the new participant', async () => {
    // Internal adversarial pass on the fix-delta, 2026-10-19 (P3): the guard compares the participant, not "any card" - a
    // pending follow for the old participant reverted a switch to the new one (also before T4840).
    vi.useFakeTimers()
    const h = mountHarness()
    try {
      h.ctl.uiOpenOrUpdateNodeCard({ nodeId: 'alice', anchor: { x: 100, y: 100 } })
      await nextTick()
      h.center.value = { x: 140, y: 120 }
      await nextTick()
      await nextTick()

      h.ctl.uiOpenOrUpdateNodeCard({ nodeId: 'bob', anchor: { x: 300, y: 300 } })
      vi.advanceTimersByTime(500)
      await nextTick()
      await nextTick()

      const cards = h.wm.windows.value.filter((w) => w.type === 'node-card')
      expect(cards).toHaveLength(1)
      expect((cards[0]!.data as { nodeId: string }).nodeId).toBe('bob')
    } finally {
      h.unmount()
      vi.useRealTimers()
    }
  })

  it('replaced by a line window with a follow pending: the follow does not bring the card back over the line', async () => {
    // Internal adversarial pass, 2026-10-19 (finding 2): the pending follow re-opened the card after a line window had
    // replaced it, and the card in turn pushed the line window out - the T4840 dead end with no participant click at all.
    vi.useFakeTimers()
    const h = mountHarness()
    try {
      h.ctl.uiOpenOrUpdateNodeCard({ nodeId: 'alice', anchor: { x: 100, y: 100 } })
      await nextTick()
      h.center.value = { x: 140, y: 120 }
      await nextTick()
      await nextTick()

      await openLineAsTheNavigatorDoes(h)
      expect(h.wm.windows.value.map((w) => w.type), 'counter-check: the line window replaced the card').toEqual(['edge-detail'])

      vi.advanceTimersByTime(500)
      await nextTick()
      await nextTick()
      expect(h.wm.windows.value.map((w) => w.type)).toEqual(['edge-detail'])
      expect(h.phase.value).toBe('editing-trustline')
      expect(h.cancel).not.toHaveBeenCalled()
    } finally {
      h.unmount()
      vi.useRealTimers()
    }
  })
})

/**
 * Internal adversarial pass, 2026-10-19 (finding 1, P1): the window manager keeps ONE inspector, so a participant card
 * opened while a line is open (the navigator's "Open participant", a double click on the canvas) closes the line window
 * with the reason `programmatic`. The first T4840 fix told the controller only about closes by a person, so the action
 * stayed `editing-trustline` behind the card - and once the card was closed, with no window at all: the same dead end.
 */
describe('048 T4840: a participant card that replaces the line window ends the "trust line" action', () => {
  it('line open -> participant card: the line window goes, the action ends; Escape then closes the card', async () => {
    const h = mountHarness()
    try {
      await openLineAsTheNavigatorDoes(h)
      h.ctl.uiOpenOrUpdateNodeCard({ nodeId: 'carol', anchor: { x: 50, y: 50 } })
      await nextTick()
      await nextTick()

      expect(h.wm.windows.value.map((w) => w.type)).toEqual(['node-card'])
      expect(h.cancel).toHaveBeenCalledTimes(1)
      expect(h.phase.value).toBe('idle')

      escape(h)
      await nextTick()
      expect(h.wm.windows.value).toHaveLength(0)
      expect(h.cancel).toHaveBeenCalledTimes(1)
    } finally {
      h.unmount()
    }
  })

  // §15 Codex, 2026-10-20 (P2): "Close line" may be in flight up to its 120 s timeout - ample time to open a participant.
  // The card replaced the line window, the request then failed, and its failure stayed in the closed window with the
  // action open and no window: the T4840 dead end again. Opening a card is ignored while the line's request is in flight.
  it('while the line\'s request is in flight a participant card is IGNORED: the line window stays; after it settles the card opens', async () => {
    const h = mountHarness()
    try {
      await openLineAsTheNavigatorDoes(h)
      h.busy.value = true
      h.ctl.uiOpenOrUpdateNodeCard({ nodeId: 'carol', anchor: { x: 50, y: 50 } })
      await nextTick()
      await nextTick()

      expect(h.wm.windows.value.map((w) => w.type)).toEqual(['edge-detail'])
      expect(h.phase.value).toBe('editing-trustline')
      expect(h.cancel).not.toHaveBeenCalled()

      // The request settles (a failure keeps the phase and the error, shown in the line window): the card opens again.
      h.busy.value = false
      h.ctl.uiOpenOrUpdateNodeCard({ nodeId: 'carol', anchor: { x: 50, y: 50 } })
      await nextTick()
      await nextTick()
      expect(h.wm.windows.value.map((w) => w.type), 'counter-check: the guard holds only while busy').toEqual(['node-card'])
    } finally {
      h.unmount()
    }
  })

  it('while the line\'s request is in flight a close the window manager makes on its own cancels nothing (the listener\'s guard)', async () => {
    const h = mountHarness()
    try {
      await openLineAsTheNavigatorDoes(h)
      h.busy.value = true
      // The last line of defence: any future opener that skipped `uiOpenOrUpdateNodeCard` would close the inspector group so.
      h.wm.closeGroup('inspector', 'programmatic')
      await nextTick()
      await nextTick()

      expect(openLineWindows(h), 'counter-check: the line window was closed').toHaveLength(0)
      expect(h.cancel).not.toHaveBeenCalled()
    } finally {
      h.unmount()
    }
  })
})
