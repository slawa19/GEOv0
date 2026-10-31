import { describe, expect, it } from 'vitest'
import { ref } from 'vue'

import { createFxState } from '../../render/fxRenderer'
import type { ClearingDoneEvent } from '../../types'
import { useRealClearingFx } from './useRealClearingFx'

/**
 * MM1-M16: one Interact clearing reaches the composable twice - as the HTTP result (`runClearingFx`, no plan id) and as
 * the `clearing.done` the backend publishes inside the same HTTP handler (`runRealClearingDoneFx`, with a plan id).
 * Either may come first. Drawn once in both orders; two real clearings (different plan ids) over the same lines stay two.
 * The real spawn functions on a hand-driven clock; counted: the pulses spawned and the amount labels pushed.
 */
function harness() {
  const fxState = createFxState()
  let now = 1000
  let seq = 0
  let timers: Array<{ at: number; seq: number; fn: () => void }> = []
  const labels: string[] = []
  const fx = useRealClearingFx({
    fxState,
    isTestMode: ref(false),
    isWebDriver: false,
    keyEdge: (a, b) => `${a}>${b}`,
    seedFn: () => 0,
    clearingColor: () => '#f97316',
    sparkCoreColor: () => '#fff',
    addActiveNode: () => undefined,
    addActiveEdge: () => undefined,
    pushClearingAmountOverlay: (overlay) => { labels.push(overlay.text) },
    clearingLabelLifeMs: () => 4200,
    scheduleTimeout: (fn, ms) => { timers.push({ at: now + ms, seq: seq += 1, fn }) },
    getLayoutNodeById: (id) => ({ A: { __x: 0, __y: 0 }, B: { __x: 10, __y: 0 }, C: { __x: 5, __y: 9 } })[id] ?? null,
    edgeLengthPx: () => 100,
    nowMs: () => now,
    nowEpochMs: () => now,
  })
  const advance = (ms: number) => {
    const until = now + ms
    for (;;) {
      const due = timers.filter((t) => t.at <= until).sort((a, b) => a.at - b.at || a.seq - b.seq)[0]
      if (!due) break
      timers = timers.filter((t) => t !== due)
      now = due.at
      due.fn()
    }
    now = until
  }
  const http = () => fx.runClearingFx({ edges: TRIANGLE, totalAmount: '5.00', equivalent: 'UAH' })
  const sse = (planId: string) => fx.runRealClearingDoneFx(done(planId))
  const counts = () => ({ edgePulses: fxState.edgePulses.length, amountLabels: labels.length })
  return { fx, advance, http, sse, counts }
}

const TRIANGLE = [{ from: 'A', to: 'B' }, { from: 'B', to: 'C' }, { from: 'C', to: 'A' }]
// The server sends the SET sorted (tick.py); the composable reduces both paths to the same union.
const done = (planId: string) => ({
  event_id: `e-${planId}`, ts: 't', type: 'clearing.done', equivalent: 'UAH', plan_id: planId, cleared_amount: '5.00',
  cycle_edges: [{ from: 'A', to: 'B' }, { from: 'B', to: 'C' }, { from: 'C', to: 'A' }],
}) as unknown as ClearingDoneEvent

describe('MM1-M16: one Interact clearing, HTTP result and clearing.done in either order, is drawn once', () => {
  it('(a) HTTP then SSE: one effect', () => {
    const h = harness()
    h.http()
    h.advance(100)
    h.sse('p1')
    h.advance(5000)
    expect(h.counts()).toEqual({ edgePulses: 3, amountLabels: 1 })
  })

  it('(b) SSE then HTTP (the backend publishes clearing.done inside the HTTP handler): one effect', () => {
    const h = harness()
    h.sse('p1')
    h.advance(100)
    h.http()
    h.advance(5000)
    expect(h.counts()).toEqual({ edgePulses: 3, amountLabels: 1 })
  })

  it('(b2) SSE then HTTP in the same tick: one effect', () => {
    const h = harness()
    h.sse('p1')
    h.http()
    h.advance(5000)
    expect(h.counts()).toEqual({ edgePulses: 3, amountLabels: 1 })
  })

  it('(c) counter-check: two clearing.done with different plan ids over the same lines are two clearings', () => {
    const h = harness()
    h.sse('p1')
    h.advance(100)
    h.sse('p2')
    h.advance(5000)
    expect(h.counts()).toEqual({ edgePulses: 6, amountLabels: 2 })
  })

  it('(c2) counter-check: SSE, the HTTP of it (suppressed), then a second plan id over the same lines still draws', () => {
    const h = harness()
    h.sse('p1')
    h.http()
    h.advance(100)
    h.sse('p2')
    h.advance(5000)
    expect(h.counts()).toEqual({ edgePulses: 6, amountLabels: 2 })
  })

  it('(d) SSE then HTTP outside the dedup window: two effects', () => {
    const h = harness()
    h.sse('p1')
    h.advance(3500)
    h.http()
    h.advance(5000)
    expect(h.counts()).toEqual({ edgePulses: 6, amountLabels: 2 })
  })

  it('(e) after resetDedup an HTTP following an SSE of the reset scene draws', () => {
    const h = harness()
    h.sse('p1')
    h.advance(5000)
    h.fx.resetDedup()
    h.http()
    h.advance(5000)
    expect(h.counts()).toEqual({ edgePulses: 6, amountLabels: 2 })
  })

  it('(f) the same lines in another equivalent are another clearing: an SSE in UAH does not suppress an HTTP in EUR', () => {
    const h = harness()
    h.sse('p1')
    h.fx.runClearingFx({ edges: TRIANGLE, totalAmount: '5.00', equivalent: 'EUR' })
    h.advance(5000)
    expect(h.counts()).toEqual({ edgePulses: 6, amountLabels: 2 })
  })
})
