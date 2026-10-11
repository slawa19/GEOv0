import { ref } from 'vue'
import { describe, expect, it } from 'vitest'

import { createFxState, type FxState } from '../../render/fxRenderer'
import type { TxUpdatedEvent } from '../../types'
import { REAL_TX_FX_DEFAULT, REAL_TX_MAX_ROUTE_EDGES, __clampRealTxTtlMs, __pickSparkEdges, useRealTxFx } from './useRealTxFx'

type SparkEdge = { from: string; to: string }

const NODES = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
/** A route of `k` edges A -> B -> C ... */
const route = (k: number): SparkEdge[] => Array.from({ length: k }, (_, i) => ({ from: NODES[i]!, to: NODES[i + 1]! }))

function tx(edges: SparkEdge[], ttl_ms = 1200): TxUpdatedEvent {
  return {
    event_id: `evt-${edges.length}`, ts: '2026-02-01T00:00:00Z', type: 'tx.updated', equivalent: 'UAH',
    from: edges[0]!.from, to: edges[edges.length - 1]!.to, ttl_ms, edges,
  } as TxUpdatedEvent
}

/** The composable on a hand-driven clock: `advance` fires the managed timers that are due, in order of time. */
function harness(opts: { fxState?: FxState; isTestMode?: boolean; isWebDriver?: boolean } = {}) {
  const fxState = opts.fxState ?? createFxState()
  let now = 1000
  let seq = 0
  let timers: Array<{ at: number; seq: number; fn: () => void }> = []
  const fx = useRealTxFx({
    fxState,
    isTestMode: ref(opts.isTestMode ?? false),
    isWebDriver: opts.isWebDriver ?? false,
    intensityScale: () => 1,
    resolveTxDirection: (i) => ({ from: i.from ?? '', to: i.to ?? '', edges: i.edges }),
    keyEdge: (a, b) => `${a}>${b}`,
    seedFn: (s) => s.length,
    txSparkColorCore: '#fff',
    txSparkColorTrail: '#0ff',
    clearingDebtColor: '#f00',
    fxColorForNode: (_id, fallback) => fallback,
    scheduleTimeout: (fn, delayMs) => { timers.push({ at: now + delayMs, seq: seq += 1, fn }) },
    pushFloatingLabelWhenReady: () => undefined,
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
  const legs = () => fxState.sparks.map((s) => `${s.source}>${s.target}@${s.startedAtMs - 1000}+${s.ttlMs}`)
  return { fx, fxState, advance, expire, legs, clearTimers: () => { timers = [] }, pending: () => timers.length, now: () => now }
}

describe('useRealTxFx helpers', () => {
  it('__clampRealTxTtlMs: clamps to [min,max] and handles invalid inputs', () => {
    expect(__clampRealTxTtlMs(REAL_TX_FX_DEFAULT.ttlMinMs - 1)).toBe(REAL_TX_FX_DEFAULT.ttlMinMs)
    expect(__clampRealTxTtlMs(REAL_TX_FX_DEFAULT.ttlMaxMs + 1)).toBe(REAL_TX_FX_DEFAULT.ttlMaxMs)
    expect(__clampRealTxTtlMs('not-a-number')).toBe(REAL_TX_FX_DEFAULT.ttlMaxMs)
    expect(__clampRealTxTtlMs(null, REAL_TX_FX_DEFAULT, 777)).toBe(777)
  })

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

describe('runRealTxFx: the beam walks the route edge by edge (038 S1, F-038-3)', () => {
  for (const k of [1, 2, 3, 5, 6]) {
    it(`a route of ${k} edge(s): one spark at a time, in route order, each for 1200 / ${k} ms, the whole route in 1200 ms`, () => {
      const h = harness()
      h.fx.runRealTxFx(tx(route(k)))
      const leg = 1200 / k
      const seen: string[] = []
      for (let i = 0; i < k; i += 1) {
        h.expire()
        expect(h.fxState.sparks, `during leg ${i} exactly one spark is alive`).toHaveLength(1)
        seen.push(h.legs()[0]!)
        h.advance(leg)
      }
      expect(seen).toEqual(route(k).map((e, i) => `${e.from}>${e.to}@${i * leg}+${leg}`))
      h.expire()
      expect(h.fxState.sparks, 'the beam is gone when the whole route time is over').toHaveLength(0)
      expect(h.now()).toBe(1000 + 1200)
      // Sender's burst at once, receiver's burst when the beam arrives: at the total, whatever the length.
      expect(h.fxState.nodeBursts.map((b) => `${b.nodeId}@${b.startedAtMs - 1000}`)).toEqual(['A@0', `${NODES[k]}@1200`])
    })
  }

  it('a route longer than the cap (7 edges) is one straight spark sender -> receiver for the whole time: the recorded degradation', () => {
    const h = harness()
    h.fx.runRealTxFx(tx(route(7)))
    expect(h.legs()).toEqual(['A>H@0+1200'])
    h.advance(1199)
    expect(h.legs(), 'no other spark is ever added').toEqual(['A>H@0+1200'])
  })

  it('the TTL clamp applies to the whole route, not to an edge: 6 edges with ttl_ms 100 take 340 ms in total', () => {
    const h = harness()
    h.fx.runRealTxFx(tx(route(6), 100))
    const leg = REAL_TX_FX_DEFAULT.ttlMinMs / 6
    expect(h.fxState.sparks[0]!.ttlMs).toBeCloseTo(leg, 9)
    h.advance(REAL_TX_FX_DEFAULT.ttlMinMs)
    expect(h.fxState.sparks.map((s) => `${s.source}>${s.target}`)).toEqual(route(6).map((e) => `${e.from}>${e.to}`))
    expect(h.fxState.sparks[5]!.startedAtMs + h.fxState.sparks[5]!.ttlMs).toBeCloseTo(1000 + REAL_TX_FX_DEFAULT.ttlMinMs, 9)
  })

  it('a spent particle budget skips a leg and does not fail; a later leg is drawn when the budget is free again', () => {
    const fxState = createFxState()
    const h = harness({ fxState })
    fxState.__maxParticles = 0
    expect(() => h.fx.runRealTxFx(tx(route(6)))).not.toThrow()
    expect(fxState.sparks, 'no budget: the first leg is skipped').toHaveLength(0)
    h.advance(200)
    expect(fxState.sparks, 'still no budget: the second leg is skipped too').toHaveLength(0)
    fxState.__maxParticles = 40
    h.advance(200)
    expect(h.legs(), 'budget is back: the leg that is due now is drawn, on its planned time').toEqual(['C>D@400+200'])
    h.advance(600)
    expect(h.legs().slice(-1)).toEqual(['F>G@1000+200'])
  })

  it('two payments one after another over routes that share an edge: each walks its own route', () => {
    const h = harness()
    h.fx.runRealTxFx(tx(route(3)))
    h.advance(200) // past the 120 ms minimum gap, inside the first leg (400 ms) of the first payment
    h.fx.runRealTxFx(tx([{ from: 'B', to: 'C' }, { from: 'C', to: 'D' }, { from: 'D', to: 'E' }]))
    h.advance(1400)
    expect(h.legs()).toEqual(['A>B@0+400', 'B>C@200+400', 'B>C@400+400', 'C>D@600+400', 'C>D@800+400', 'D>E@1000+400'])
  })

  it('an event inside the minimum gap after another is not animated at all (no token, no timers)', () => {
    const h = harness()
    h.fx.runRealTxFx(tx(route(3)))
    const pending = h.pending()
    h.advance(100)
    h.fx.runRealTxFx(tx(route(3)))
    expect(h.pending()).toBe(pending)
    expect(h.legs()).toEqual(['A>B@0+400'])
  })

  it('cancelled timers (scene change, snapshot refresh) stop the beam after its current edge and add nothing later', () => {
    const h = harness()
    h.fx.runRealTxFx(tx(route(3)))
    h.advance(100)
    h.clearTimers()
    h.advance(2000)
    expect(h.legs()).toEqual(['A>B@0+400'])
  })

  it('snapshot stand (test mode under WebDriver): nothing is spawned and no timer is scheduled', () => {
    const h = harness({ isTestMode: true, isWebDriver: true })
    h.fx.runRealTxFx(tx(route(3)))
    expect(h.fxState.sparks).toHaveLength(0)
    expect(h.fxState.nodeBursts).toHaveLength(0)
    expect(h.pending()).toBe(0)
  })
})
