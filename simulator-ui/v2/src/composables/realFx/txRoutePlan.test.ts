import { describe, expect, it } from 'vitest'

import { ARRIVE_STRETCH, easeArrive } from '../../render/fxRenderer/easing'
import { BEAM_FADE_MS, BEAM_LEG_MIN_MS, BEAM_ROUTE_MAX_MS, BEAM_SPEED_MAX_PX_PER_MS, planTxRoute } from './txRoutePlan'

const NODES = ['A', 'B', 'C', 'D', 'E', 'F', 'G']
const route = (k: number) => Array.from({ length: k }, (_, i) => ({ from: NODES[i]!, to: NODES[i + 1]! }))
/** A route whose legs have these screen lengths. */
function plan(lengths: Array<number | null>) {
  const edges = route(lengths.length)
  return planTxRoute(edges, (e) => lengths[edges.findIndex((x) => x.from === e.from)] ?? null)
}
const ms = (p: ReturnType<typeof plan>) => p.legs.map((l) => Math.round(l.moveMs))

describe('039 slice C - the schedule of a payment beam (motion contract P-3)', () => {
  it('the numbers of the contract: floor 450 ms a leg (criterion 4), arrive stretch 1.5, fade 360 ms, ceiling 4000 ms over six floors and six ordinary legs', () => {
    expect([BEAM_LEG_MIN_MS, ARRIVE_STRETCH, BEAM_FADE_MS, BEAM_ROUTE_MAX_MS, BEAM_SPEED_MAX_PX_PER_MS]).toEqual([450, 1.5, 360, 4000, 0.4])
    // Six legs at the floor fit under the ceiling: rule 3 can always keep the floor.
    expect(5 * BEAM_LEG_MIN_MS + ARRIVE_STRETCH * BEAM_LEG_MIN_MS).toBeLessThanOrEqual(BEAM_ROUTE_MAX_MS)
    // Six ordinary legs (150 px: at the floor) do not meet the ceiling.
    expect(plan([150, 150, 150, 150, 150, 150]).heldAtFloor).toBe(0)
    expect(plan([150, 150, 150, 150, 150, 150]).arrivalMs).toBeCloseTo(5 * 450 + 675, 9)
  })

  it('equal legs of 300 px: one speed - the limit 0.4 px/ms - 750 ms a leg, the last stretched to 1125 ms, then the fade', () => {
    const p = plan([300, 300, 300])
    expect(ms(p)).toEqual([750, 750, 1125])
    expect(p.legs.map((l) => Math.round(l.startMs))).toEqual([0, 750, 1500])
    expect(p.legs.map((l) => l.arrive)).toEqual([false, false, true])
    expect(p.arrivalMs).toBeCloseTo(2625, 9)
    expect(p.endMs).toBeCloseTo(2625 + BEAM_FADE_MS, 9)
    expect(p.speedPxPerMs).toBeCloseTo(0.4, 9)
    expect(p.heldAtFloor).toBe(0)
  })

  it('equal legs of 150 px (the typical leg of the demo scene): the floor binds - 450 ms a leg, the last 675', () => {
    expect(ms(plan([150, 150, 150]))).toEqual([450, 450, 675])
  })

  it('ONE SPEED along a route of unequal legs: a leg lasts in proportion to its length (the last x 1.5)', () => {
    const lengths = [200, 250, 300, 210]
    const p = plan(lengths)
    expect(p.heldAtFloor, 'precondition: the ceiling does not bind').toBe(0)
    const speeds = p.legs.map((l, i) => (lengths[i]! * (l.arrive ? ARRIVE_STRETCH : 1)) / l.moveMs)
    for (const v of speeds) expect(v).toBeCloseTo(speeds[0]!, 9)
    expect(Math.min(...p.legs.map((l) => l.moveMs))).toBeGreaterThanOrEqual(BEAM_LEG_MIN_MS)
  })

  it('THE FLOOR: the shortest leg is exactly at 450 ms when the speed limit does not bind; none is shorter', () => {
    const p = plan([100, 150]) // 100 / 450 = 0.222 px/ms < 0.3
    expect(p.legs[0]!.moveMs).toBeCloseTo(450, 9)
    expect(p.legs[1]!.moveMs).toBeCloseTo((150 * ARRIVE_STRETCH * 450) / 100, 9)
    expect(p.heldAtFloor).toBe(0)
  })

  it('the one-leg route: eased into the receiver, 1.5 x its time at the speed limit, never under the floor', () => {
    expect(ms(plan([300]))).toEqual([1125]) // 1.5 x 300 / 0.4
    expect(ms(plan([108]))).toEqual([450]) // 1.5 x 108 / 0.4 = 405 < 450: the floor
    expect(plan([300]).legs[0]!.arrive).toBe(true)
  })

  it('THE CEILING: a very short leg next to long ones - the route fits 4000 ms, the short leg is held AT the floor, the rest keep one speed', () => {
    const p = plan([30, 400, 400])
    expect(p.endMs - BEAM_FADE_MS).toBeCloseTo(BEAM_ROUTE_MAX_MS, 3)
    expect(Math.round(p.legs[0]!.moveMs)).toBe(BEAM_LEG_MIN_MS)
    expect(p.heldAtFloor).toBe(1)
    // The two long legs share one speed.
    expect(400 / p.legs[1]!.moveMs).toBeCloseTo((400 * ARRIVE_STRETCH) / p.legs[2]!.moveMs, 6)
  })

  it('unknown geometry (a node not laid out): every leg counts as equal and sits at the floor', () => {
    const p = plan([150, null, 150])
    expect(ms(p)).toEqual([450, 450, 675])
    expect(p.speedPxPerMs).toBeNull()
  })

  it('six legs, any lengths: legs follow each other with no gap and no overlap, none under 450 ms, the route within the ceiling', () => {
    let seed = 7
    const rnd = () => { seed = (seed * 48271) % 2147483647; return seed / 2147483647 }
    for (let n = 0; n < 400; n += 1) {
      const k = 1 + Math.floor(rnd() * 6)
      const lengths = Array.from({ length: k }, () => 20 + rnd() * 380)
      const p = plan(lengths)
      for (let i = 0; i < k; i += 1) {
        expect(p.legs[i]!.moveMs, `leg ${i} of ${lengths.map(Math.round).join('/')}`).toBeGreaterThanOrEqual(BEAM_LEG_MIN_MS - 1e-9)
        if (i > 0) expect(p.legs[i]!.startMs).toBeCloseTo(p.legs[i - 1]!.startMs + p.legs[i - 1]!.moveMs, 9)
      }
      expect(p.arrivalMs).toBeLessThanOrEqual(BEAM_ROUTE_MAX_MS + 1e-6)
      expect(p.legs[0]!.startMs).toBe(0)
    }
  })

  it('an empty route plans nothing', () => {
    expect(planTxRoute([], () => 100)).toEqual({ legs: [], arrivalMs: 0, endMs: 0, speedPxPerMs: null, heldAtFloor: 0 })
  })
})

describe('easeArrive - the curve of the last leg', () => {
  it('starts at the route speed (slope = the stretch), ends at rest, never steps back', () => {
    const d = 1e-6
    expect((easeArrive(d) - easeArrive(0)) / d).toBeCloseTo(ARRIVE_STRETCH, 4)
    expect((easeArrive(1) - easeArrive(1 - d)) / d).toBeCloseTo(0, 4)
    expect([easeArrive(0), easeArrive(1)]).toEqual([0, 1])
    let prev = 0
    for (let u = 0; u <= 1; u += 0.01) {
      const v = easeArrive(u)
      expect(v).toBeGreaterThanOrEqual(prev - 1e-12)
      prev = v
    }
  })
})
