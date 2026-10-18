import { ref } from 'vue'
import { describe, expect, it } from 'vitest'

import { createFxState, type FxState } from '../../render/fxRenderer'
import type { TxUpdatedEvent } from '../../types'
import { BEAM_FADE_MS, BEAM_LEG_MIN_MS } from './txRoutePlan'
import { BEAM_NODE_BURST_MS, REAL_TX_FX_DEFAULT, REAL_TX_MAX_ROUTE_EDGES, __pickSparkEdges, useRealTxFx } from './useRealTxFx'

type SparkEdge = { from: string; to: string }

const NODES = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
/** A route of `k` edges A -> B -> C ... */
const route = (k: number): SparkEdge[] => Array.from({ length: k }, (_, i) => ({ from: NODES[i]!, to: NODES[i + 1]! }))

let txSeq = 0
function tx(edges: SparkEdge[], ttl_ms = 1200): TxUpdatedEvent {
  txSeq += 1
  return {
    event_id: `evt-${edges.length}-${txSeq}`, ts: '2026-02-01T00:00:00Z', type: 'tx.updated', equivalent: 'UAH',
    from: edges[0]!.from, to: edges[edges.length - 1]!.to, ttl_ms, edges,
  } as TxUpdatedEvent
}

/**
 * The composable on a hand-driven clock: `advance` fires the managed timers that are due, in order of time. Every edge
 * is 150 screen px long unless `lengths` says otherwise: the floor binds (150 px at the speed limit 0.4 px/ms would be
 * 375 ms), so a linear leg is 450 ms and the last (eased) leg 675 ms (`txRoutePlan.ts`).
 */
function harness(opts: { fxState?: FxState; isTestMode?: boolean; isWebDriver?: boolean; lengths?: Record<string, number | null> } = {}) {
  const fxState = opts.fxState ?? createFxState()
  let now = 1000
  let seq = 0
  let timers: Array<{ at: number; seq: number; fn: () => void; critical: boolean }> = []
  const colors = { txSparkCore: '#fff', txSparkTrail: '#0ff', clearingDebt: '#f00' }
  const labels: Array<{ nodeId: string; text: string; color: string; ttlMs: number }> = []
  const lengthAsks: string[] = []
  const fx = useRealTxFx({
    fxState,
    isTestMode: ref(opts.isTestMode ?? false),
    isWebDriver: opts.isWebDriver ?? false,
    intensityScale: () => 1,
    resolveTxDirection: (i) => ({ from: i.from ?? '', to: i.to ?? '', edges: i.edges }),
    keyEdge: (a, b) => `${a}>${b}`,
    seedFn: (s) => s.length,
    fxColors: () => ({ ...colors }), // a copy, as the mapping hands out: only a read AFTER a change sees it
    fxColorForNode: (_id, fallback) => fallback,
    edgeLengthPx: (a, b) => {
      lengthAsks.push(`${a}>${b}`)
      const v = opts.lengths?.[`${a}>${b}`]
      return v === undefined ? 150 : v
    },
    txLabelLifeMs: () => 2600,
    scheduleTimeout: (fn, delayMs, o) => { timers.push({ at: now + delayMs, seq: seq += 1, fn, critical: !!o?.critical }) },
    pushFloatingLabelWhenReady: (l) => { labels.push(l) },
    nowMs: () => now,
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
  /** What the renderer does every frame: drop the sparks whose life is over (`renderFrame.ts`, `age >= ttlMs`). */
  const expire = () => { fxState.sparks = fxState.sparks.filter((s) => now - s.startedAtMs < s.ttlMs) }
  const legs = () => fxState.sparks.map((s) => `${s.source}>${s.target}@${s.startedAtMs - 1000}+${s.moveMs}`)
  const bursts = () => fxState.nodeBursts.map((b) => `${b.nodeId}@${b.startedAtMs - 1000}`)
  /** What the timer registry does: a load that starts clears the non-critical timers, a change of scene clears all. */
  const clearTimers = (o?: { keepCritical?: boolean }) => { timers = o?.keepCritical ? timers.filter((t) => t.critical) : [] }
  return { fx, fxState, advance, expire, legs, bursts, clearTimers, colors, labels, lengthAsks, pending: () => timers.length, now: () => now }
}

describe('useRealTxFx helpers', () => {
  // RECORD OF A DELIBERATE CHANGE (038 S1, F-038-3 = F-034-D4, 2026-10-11). Until then this test pinned the opposite:
  // "keeps short routes, collapses long routes to endpoints", with a route of THREE edges collapsed to the single pair
  // sender -> receiver (`maxEdgesPerEvent` was 2). That pinned the defect: the beam of a payment through intermediaries
  // was drawn on a chord that is no edge of the route. The cap is now the cap of payment routing (6), a route within
  // it keeps all its edges, and only a longer list is straightened - as a recorded degradation.
  it('__pickSparkEdges: a route within the cap keeps every edge; only a longer one is straightened to its endpoints', () => {
    expect(REAL_TX_MAX_ROUTE_EDGES).toBe(6)
    expect(REAL_TX_FX_DEFAULT.maxEdgesPerEvent).toBe(REAL_TX_MAX_ROUTE_EDGES)
    for (const k of [1, 2, 3, 5, 6]) expect(__pickSparkEdges(route(k), { from: 'A', to: NODES[k] })).toEqual(route(k))

    const tooLong = route(7)
    expect(__pickSparkEdges(tooLong, { from: 'A', to: 'H' })).toEqual([{ from: 'A', to: 'H' }])
    expect(__pickSparkEdges(tooLong, { from: '', to: 'H' })).toEqual([])
    expect(__pickSparkEdges(tooLong, undefined)).toEqual([{ from: 'A', to: 'H' }])
    expect(__pickSparkEdges([], { from: 'A', to: 'B' })).toEqual([])
  })
})

// RECORD OF A DELIBERATE CHANGE (039 slice C, 2026-10-18). Until then the cases below pinned "each leg lasts
// 1200 / k ms, the whole route 1200 ms" - the wire's ttl_ms divided by the number of legs: 200 ms a leg at six legs,
// and every leg eased and faded inside itself (F-039-4, R6). The schedule is now `planTxRoute`: one speed, a floor of
// 450 ms a leg, the last leg eased into the receiver and then a fade; `ttl_ms` no longer times the beam.
describe('runRealTxFx: the beam walks the route on its plan (039 slice C, P-3)', () => {
  for (const k of [1, 2, 3, 5, 6]) {
    it(`a route of ${k} edge(s) of 150 px: one spark at a time, in route order, 450 ms a leg, the last eased (675 ms; 562.5 alone), then a ${BEAM_FADE_MS} ms fade`, () => {
      const h = harness()
      h.fx.runRealTxFx(tx(route(k)))
      // The last leg is 1.5 x its time at the route's speed. With two or more legs the 150 px linear legs hold the
      // speed at the floor (150 / 450 px/ms), so the last is 675 ms; a route of ONE leg has only its stretched leg, and
      // the speed limit (0.4 px/ms) sets it: 1.5 x 150 / 0.4 = 562.5 ms.
      const lastMs = k === 1 ? 562.5 : 675
      const seen: string[] = []
      let at = 0
      for (let i = 0; i < k; i += 1) {
        h.expire()
        expect(h.fxState.sparks, `during leg ${i} exactly one spark is alive`).toHaveLength(1)
        seen.push(h.legs()[0]!)
        const s = h.fxState.sparks[0]!
        const last = i === k - 1
        expect({ motion: s.motion, ttlMs: s.ttlMs }, `leg ${i}: linear, or eased into the receiver and then the fade`).toEqual(
          last ? { motion: 'arrive', ttlMs: lastMs + BEAM_FADE_MS } : { motion: 'linear', ttlMs: 450 },
        )
        const legMs = last ? lastMs : 450
        h.advance(legMs)
        at += legMs
      }
      expect(seen).toEqual(route(k).map((e, i) => `${e.from}>${e.to}@${i * 450}+${i === k - 1 ? lastMs : 450}`))
      h.expire()
      expect(h.fxState.sparks, 'the beam still fades out at the receiver after it arrived').toHaveLength(1)
      h.advance(BEAM_FADE_MS)
      h.expire()
      expect(h.fxState.sparks, 'and is gone when the fade is over').toHaveLength(0)
      // The sender lights up at once, every intermediary when the beam leaves it, the receiver when the beam arrives.
      const arrival = (k - 1) * 450 + lastMs
      expect(h.bursts()).toEqual(['A@0', ...route(k).slice(1).map((e, i) => `${e.from}@${(i + 1) * 450}`), `${NODES[k]}@${arrival}`])
      expect(h.fx.routeArrivalMs(tx(route(k)))).toBe(arrival)
      expect(at).toBe(arrival)
    })
  }

  it('no leg of a six-edge route is shorter than the floor of 450 ms, whatever the lengths (R6 of spec 039)', () => {
    const h = harness({ lengths: { 'A>B': 20, 'B>C': 400, 'C>D': 35, 'D>E': 300, 'E>F': 90, 'F>G': 260 } })
    h.fx.runRealTxFx(tx(route(6)))
    h.advance(10_000)
    const moves = h.fxState.sparks.map((s) => s.moveMs!)
    expect(moves).toHaveLength(6)
    expect(Math.min(...moves)).toBeGreaterThanOrEqual(BEAM_LEG_MIN_MS)
  })

  it('the wire ttl_ms does not time the beam any more: ttl_ms 100 and 5000 give the same route', () => {
    const a = harness()
    a.fx.runRealTxFx(tx(route(3), 100))
    const b = harness()
    b.fx.runRealTxFx(tx(route(3), 5000))
    expect(a.fx.routeArrivalMs(tx(route(3), 100))).toBe(b.fx.routeArrivalMs(tx(route(3), 5000)))
    a.advance(5000)
    b.advance(5000)
    expect(a.legs()).toEqual(b.legs())
  })

  it('ONE route time: the plan is made once per event and the receiver label asks the same one (no second geometry read)', () => {
    const h = harness()
    const e = tx(route(3))
    h.fx.runRealTxFx(e)
    const asked = h.lengthAsks.length
    expect(asked, 'the beam measured each edge once').toBe(3)
    expect(h.fx.routeArrivalMs(e)).toBe(1575)
    expect(h.fx.routeEffectMs(e)).toBe(Math.max(1575 + BEAM_FADE_MS, 1575 + BEAM_NODE_BURST_MS))
    expect(h.lengthAsks.length, 'asking the arrival again reads no geometry').toBe(asked)
    // A fresh generation (a new run or playback) plans the same event object anew.
    h.fx.resetRateLimiter()
    h.fx.routeArrivalMs(e)
    expect(h.lengthAsks.length).toBe(asked + 3)
  })

  it('a route longer than the cap (7 edges) is one straight spark sender -> receiver, eased, on the floor rules: the recorded degradation', () => {
    const h = harness()
    h.fx.runRealTxFx(tx(route(7)))
    expect(h.legs()).toEqual(['A>H@0+562.5'])
    h.advance(1000)
    expect(h.legs(), 'no other spark is ever added').toEqual(['A>H@0+562.5'])
  })

  it('a spent particle budget skips a leg and does not fail; a later leg is drawn when the budget is free again', () => {
    const fxState = createFxState()
    const h = harness({ fxState })
    fxState.__maxParticles = 0
    expect(() => h.fx.runRealTxFx(tx(route(6)))).not.toThrow()
    expect(fxState.sparks, 'no budget: the first leg is skipped').toHaveLength(0)
    h.advance(450)
    expect(fxState.sparks, 'still no budget: the second leg is skipped too').toHaveLength(0)
    fxState.__maxParticles = 40
    h.advance(450)
    expect(h.legs(), 'budget is back: the leg that is due now is drawn, on its planned time').toEqual(['C>D@900+450'])
    h.advance(1350)
    expect(h.legs().slice(-1)).toEqual(['F>G@2250+675'])
  })

  it('two payments one after another over routes that share an edge: each walks its own route', () => {
    const h = harness()
    h.fx.runRealTxFx(tx(route(3)))
    h.advance(200) // past the 120 ms minimum gap, inside the first leg (450 ms) of the first payment
    h.fx.runRealTxFx(tx([{ from: 'B', to: 'C' }, { from: 'C', to: 'D' }, { from: 'D', to: 'E' }]))
    h.advance(2000)
    expect(h.legs()).toEqual(['A>B@0+450', 'B>C@200+450', 'B>C@450+450', 'C>D@650+450', 'C>D@900+675', 'D>E@1100+675'])
  })

  it('an event inside the minimum gap after another is not animated at all (no token, no timers)', () => {
    const h = harness()
    h.fx.runRealTxFx(tx(route(3)))
    const pending = h.pending()
    h.advance(100)
    h.fx.runRealTxFx(tx(route(3)))
    expect(h.pending()).toBe(pending)
    expect(h.legs()).toEqual(['A>B@0+450'])
  })

  // A4 (adversarial pass on fdd890c5): a refresh of the scene on screen cut the beam after its current edge while the
  // receiver's amount label, a critical timer, still came. The legs and the receiver's burst are now of that class.
  it('a snapshot refresh of the same scene (non-critical timers cleared) does not cut the beam: all legs and the receiver burst come', () => {
    const h = harness()
    h.fx.runRealTxFx(tx(route(3)))
    h.advance(100)
    h.clearTimers({ keepCritical: true })
    h.advance(3000)
    expect(h.legs()).toEqual(['A>B@0+450', 'B>C@450+450', 'C>D@900+675'])
    expect(h.bursts()).toEqual(['A@0', 'B@450', 'C@900', 'D@1575'])
  })

  it('a change of scene (all timers cleared) stops the beam after its current edge and adds nothing later', () => {
    const h = harness()
    h.fx.runRealTxFx(tx(route(3)))
    h.advance(100)
    h.clearTimers()
    h.advance(3000)
    expect(h.legs()).toEqual(['A>B@0+450'])
    expect(h.fxState.nodeBursts.map((b) => b.nodeId)).toEqual(['A'])
  })

  it('a stopped or restarted run (resetRateLimiter; its cleanup keeps critical timers) stops the beam: pending legs and bursts do nothing', () => {
    const h = harness()
    h.fx.runRealTxFx(tx(route(3)))
    h.advance(100)
    h.clearTimers({ keepCritical: true }) // what `cleanupRealRunFxAndTimers` does to the timers ...
    h.fxState.sparks.length = 0 // ... and to the FX state (`resetOverlays`),
    h.fxState.nodeBursts.length = 0
    h.fx.resetRateLimiter() // ... and to this composable
    h.advance(3000)
    expect(h.legs(), 'no leg of the old run is drawn on the scene of the new one').toEqual([])
    expect(h.fxState.nodeBursts).toHaveLength(0)
    // The next payment is animated in full.
    h.fx.runRealTxFx(tx(route(2)))
    const t0 = h.now() - 1000
    h.advance(1150)
    expect(h.legs()).toEqual([`A>B@${t0}+450`, `B>C@${t0 + 450}+675`])
  })

  it('snapshot stand (test mode under WebDriver): nothing is spawned and no timer is scheduled', () => {
    const h = harness({ isTestMode: true, isWebDriver: true })
    h.fx.runRealTxFx(tx(route(3)))
    expect(h.fxState.sparks).toHaveLength(0)
    expect(h.fxState.nodeBursts).toHaveLength(0)
    expect(h.pending()).toBe(0)
  })

  it('the FX colours are read when each leg is spawned, not when the payment started: a theme change in the middle of a flight reaches the next leg (BACKLOG 039-5 (zh))', () => {
    const h = harness()
    h.fx.runRealTxFx(tx(route(2)))
    h.colors.txSparkTrail = '#123456'
    h.advance(450)
    expect(h.fxState.sparks.map((s) => s.colorTrail)).toEqual(['#0ff', '#123456'])
  })
})

describe('pushTxAmountLabel (039 slice C)', () => {
  it('lives as long as its CSS fade (Р2) and is coloured by what the number IS: arriving money in the beam colour, leaving money in the debit colour', () => {
    const h = harness()
    h.fx.pushTxAmountLabel('A', '-10.00', 'UAH')
    h.fx.pushTxAmountLabel('B', '+10.00', 'UAH')
    expect(h.labels.map((l) => ({ text: l.text, color: l.color, ttlMs: l.ttlMs }))).toEqual([
      { text: '-10.00 UAH', color: '#f00', ttlMs: 2600 },
      { text: '+10.00 UAH', color: '#0ff', ttlMs: 2600 },
    ])
  })
})
