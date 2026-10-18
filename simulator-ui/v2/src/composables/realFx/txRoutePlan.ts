import { ARRIVE_STRETCH } from '../../render/fxRenderer/easing'

/**
 * 039 slice C - THE schedule of a payment beam (motion contract P-3 of spec 039). Computed ONCE per payment
 * (`useRealTxFx.ts`, `planOf`) and shared by everything that is timed on it: the legs of the beam, the light of the
 * nodes the beam passes, the receiver's burst, the receiver's amount label (`realEventPipeline.ts`,
 * `tx-label-delayed`) and the pause of the offline workbench before its next event (`usePlaylistWorkbench.ts`).
 * There is no second duration: the wire's `ttl_ms` no longer times the beam (it used to be divided by the number of
 * legs - 200 ms a leg at six legs, F-039-4).
 *
 * The rules, in this order:
 *  1. ONE SPEED along the route: a leg lasts its length (screen px between the link terminations) over the speed.
 *     The last leg lasts `ARRIVE_STRETCH` times that - it slows into the receiver (`easeArrive`), the others are linear.
 *  2. A FLOOR per leg: no leg is shorter than `BEAM_LEG_MIN_MS`. The speed is chosen so that the shortest leg is
 *     exactly at the floor, and not faster than `BEAM_SPEED_MAX_PX_PER_MS` - so a route of long legs is not hurried.
 *  3. A CEILING for the route: when 1 and 2 make the route longer than `BEAM_ROUTE_MAX_MS` (a very short leg next to
 *     long ones), the speed is raised until the route fits, and the legs that would then be shorter than the floor
 *     are held AT the floor. Only then is the speed not one along the whole route - a recorded degradation, the floor
 *     wins over the constant speed (the floor is the criterion of the spec, criterion 4).
 *  4. After the beam arrives it fades out at the receiver for `BEAM_FADE_MS`: the beam is never cut off.
 * A leg whose length is unknown (a node not laid out yet) makes every leg count as equal: then each leg is at the floor.
 *
 * Why these numbers. 450 ms is the design budget of the spec (criterion 4); 400 ms was the sampled duration at which
 * the motion oracle's limits held (`e2e/qa/oracle.ts`), 450 is above it. The speed limit 0.4 px/ms (6.4 px a frame)
 * keeps a route of long legs from being hurried - without it the floor alone would fly a 360 px leg of the stand's ring
 * in 450 ms, 12.8 px a frame; with it the leg takes 900 ms - and leaves the typical leg of the 100-node
 * demo scene at 1366x768 (86-208 px between centres, measured 2026-10-18) to the floor: a 150 px leg would take 375 ms
 * at the limit, so it takes 450. The ceiling 4000 ms is above six legs at the floor (5 x 450 + 1.5 x 450 = 2925 ms;
 * six legs is the cap of a route, `REAL_TX_MAX_ROUTE_EDGES`), so the floor always fits; it binds only on a long route
 * of long legs (six legs of 300 px: 4875 ms at the limit) - then the speed rises, still one along the route.
 */
export const BEAM_LEG_MIN_MS = 450
export const BEAM_SPEED_MAX_PX_PER_MS = 0.4
export const BEAM_ROUTE_MAX_MS = 4000
export const BEAM_FADE_MS = 360

export type TxRouteLeg = {
  from: string
  to: string
  /** From the start of the route. */
  startMs: number
  /** How long the head takes over this leg. */
  moveMs: number
  /** The last leg: eased into the receiver, then the fade. */
  arrive: boolean
}

export type TxRoutePlan = {
  legs: TxRouteLeg[]
  /** The head reaches the receiver: the receiver's burst and amount label. */
  arrivalMs: number
  /** The beam is gone (arrival + fade). */
  endMs: number
  /** Screen px per ms of the legs that are not held at the floor; null when the lengths are unknown. */
  speedPxPerMs: number | null
  /** Legs held at the floor by the ceiling (rule 3): the speed is not one along the route. */
  heldAtFloor: number
}

export function planTxRoute(
  edges: ReadonlyArray<{ from: string; to: string }>,
  lengthPx: (edge: { from: string; to: string }) => number | null,
): TxRoutePlan {
  const n = edges.length
  if (n === 0) return { legs: [], arrivalMs: 0, endMs: 0, speedPxPerMs: null, heldAtFloor: 0 }
  const weight = (i: number) => (i === n - 1 ? ARRIVE_STRETCH : 1)
  const raw = edges.map((e) => lengthPx(e))
  const known = raw.every((l) => typeof l === 'number' && Number.isFinite(l) && l > 0)
  // Unknown geometry: equal legs, each at the floor.
  const len = known ? (raw as number[]) : edges.map(() => 1)
  const work = len.map((l, i) => weight(i) * l) // px of "time at speed 1"

  const durations = (v: number) => work.map((w) => Math.max(BEAM_LEG_MIN_MS, w / v))
  const total = (v: number) => durations(v).reduce((s, d) => s + d, 0)

  // Rule 2: the shortest weighted leg exactly at the floor, and no faster than the limit.
  let v = Math.min(...work) / BEAM_LEG_MIN_MS
  if (known) v = Math.min(v, BEAM_SPEED_MAX_PX_PER_MS)
  // Rule 3: raise the speed until the route fits the ceiling (`total` falls as v grows, down to n floors).
  if (total(v) > BEAM_ROUTE_MAX_MS) {
    let lo = v
    let hi = Math.max(...work) / BEAM_LEG_MIN_MS // every leg at the floor
    for (let i = 0; i < 50; i += 1) {
      const mid = (lo + hi) / 2
      if (total(mid) > BEAM_ROUTE_MAX_MS) lo = mid
      else hi = mid
    }
    v = hi
  }
  const d = durations(v)
  const legs: TxRouteLeg[] = []
  let at = 0
  let held = 0
  edges.forEach((e, i) => {
    if (d[i]! > work[i]! / v + 1e-9) held += 1
    legs.push({ from: e.from, to: e.to, startMs: at, moveMs: d[i]!, arrive: i === n - 1 })
    at += d[i]!
  })
  return { legs, arrivalMs: at, endMs: at + BEAM_FADE_MS, speedPxPerMs: known ? v : null, heldAtFloor: known ? held : 0 }
}
