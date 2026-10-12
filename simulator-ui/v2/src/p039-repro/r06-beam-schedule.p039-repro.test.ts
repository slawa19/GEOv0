/**
 * 039 T3901 - R6 (pace of the beam), SCHEDULE tier. Visible movement, handoff between legs and speed on legs of
 * unequal length: `e2e/qa/p039-repro/r06-beam-visible.p039-repro.ts`.
 *
 * Contract (P-3): the duration of a route is computed once; a leg has a lower bound; the bound of the spec (450 ms) is
 * a DESIGN BUDGET, not a derived number. So the assertion here does not use 450. It uses the product's own declared
 * floor: `ttlMinMs` (340 ms), the shortest life the product allows a spark to have ("clamp TTL", `useRealTxFx.ts`).
 * Today the clamp is applied to the whole route and then divided by the number of legs, so a leg of a 6-edge route
 * lives 200 ms - below the floor the same file declares. 450 is reported next to it as a measurement.
 *
 * LIMITS. A schedule is not what is seen: alpha decays inside every leg (`renderFrame.ts`), so a longer leg alone does
 * not make the handoff visible. Nothing here knows the length of an edge: "constant speed" cannot be judged at this tier.
 */
import { ref } from 'vue'
import { describe, expect, it } from 'vitest'

import { REAL_TX_FX_DEFAULT, useRealTxFx } from '../composables/realFx/useRealTxFx'
import { createFxState } from '../render/fxRenderer'
import type { TxUpdatedEvent } from '../types'
import { measured, red } from './p039'

const NODES = ['A', 'B', 'C', 'D', 'E', 'F', 'G']
const route = (k: number) => Array.from({ length: k }, (_, i) => ({ from: NODES[i]!, to: NODES[i + 1]! }))

/** Every spark the product spawns for one payment over `k` edges, and when the receiver's burst comes. */
function schedule(k: number, ttl_ms = 1200) {
  const fxState = createFxState()
  let now = 1000
  let timers: Array<{ at: number; fn: () => void }> = []
  const fx = useRealTxFx({
    fxState, isTestMode: ref(false), isWebDriver: false, intensityScale: () => 1,
    resolveTxDirection: (i) => ({ from: i.from ?? '', to: i.to ?? '', edges: i.edges }),
    keyEdge: (a, b) => `${a}>${b}`, seedFn: (s) => s.length, txSparkColorCore: '#fff', txSparkColorTrail: '#0ff', clearingDebtColor: '#f00',
    fxColorForNode: (_id, fallback) => fallback,
    scheduleTimeout: (fn, delayMs) => { timers.push({ at: now + delayMs, fn }) },
    pushFloatingLabelWhenReady: () => undefined, nowMs: () => now,
  })
  const edges = route(k)
  fx.runRealTxFx({ event_id: `evt-${k}`, ts: '2026-02-01T00:00:00Z', type: 'tx.updated', equivalent: 'UAH', from: 'A', to: NODES[k]!, ttl_ms, edges } as TxUpdatedEvent)
  for (const t of timers.sort((a, b) => a.at - b.at)) { now = t.at; t.fn() }
  timers = []
  const legs = fxState.sparks.map((s) => ({ edge: `${s.source}>${s.target}`, startMs: s.startedAtMs - 1000, lifeMs: s.ttlMs }))
  const receiverBurstAtMs = Math.max(...fxState.nodeBursts.map((b) => b.startedAtMs - 1000))
  return { legs, receiverBurstAtMs, totalMs: Math.max(...legs.map((l) => l.startMs + l.lifeMs)) }
}

describe('039 R6 - the schedule of a beam over a route', () => {
  it('measurement: life of a leg and of the whole route for 1..6 edges (ttl_ms 1200, the value the backend sends)', () => {
    const rows = [1, 2, 3, 4, 5, 6].map((k) => {
      const s = schedule(k)
      return { edges: k, legMs: s.legs.map((l) => l.lifeMs), totalMs: s.totalMs, receiverBurstAtMs: s.receiverBurstAtMs }
    })
    measured('R6 schedule', { ttlMinMs: REAL_TX_FX_DEFAULT.ttlMinMs, ttlMaxMs: REAL_TX_FX_DEFAULT.ttlMaxMs, specBudgetPerLegMs: 450, rows })
    for (const r of rows) expect(r.legMs, `counter-check: a ${r.edges}-edge route spawns one spark per edge`).toHaveLength(r.edges)
    expect(rows[0]!.legMs).toEqual([1200])
  })

  red('no leg of a 6-edge route lives less than the product\'s own floor for a spark, ttlMinMs = 340 ms (today: 200 ms)', () => {
    const s = schedule(6)
    expect(s.legs, 'coverage: all six legs are scheduled').toHaveLength(6)
    expect(Math.min(...s.legs.map((l) => l.lifeMs)), 'shortest leg of a 6-edge route, ms').toBeGreaterThanOrEqual(REAL_TX_FX_DEFAULT.ttlMinMs)
  })

  red('a route of more edges takes longer, it is not squeezed into the time of one edge (today: 1200 ms whatever the length)', () => {
    const one = schedule(1).totalMs
    const six = schedule(6).totalMs
    expect(six, `whole 6-edge route, ms (1-edge route: ${one})`).toBeGreaterThan(one)
  })

  it('invariant that must SURVIVE the fix: legs follow each other without overlap and without a gap, and the receiver\'s burst comes when the last leg ends', () => {
    for (const k of [2, 3, 6]) {
      const s = schedule(k)
      for (let i = 1; i < s.legs.length; i += 1) expect(s.legs[i]!.startMs, `leg ${i} of ${k} starts when leg ${i - 1} ends`).toBeCloseTo(s.legs[i - 1]!.startMs + s.legs[i - 1]!.lifeMs, 6)
      expect(s.receiverBurstAtMs, `receiver burst of a ${k}-edge route`).toBeCloseTo(s.totalMs, 6)
    }
  })
})
