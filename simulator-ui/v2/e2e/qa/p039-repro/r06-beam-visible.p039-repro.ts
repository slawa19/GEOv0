/**
 * 039 T3901 - R6 (pace of the beam), what is SEEN: one leg, and six legs. Schedule tier:
 * `src/p039-repro/r06-beam-schedule.p039-repro.test.ts`.
 *
 * Contract (P-3): a leg has a lower bound; the handoff between legs must be visible - today alpha decays inside every
 * leg (`renderFrame.ts`), so a slower leg alone would not give it. Corrected form (P-2) and the review of 2026-10-12:
 * coverage is required on EVERY leg and must be attributable to that leg; the dark gap between legs is bounded; the
 * flight is watched until the product has finished it, however long the route now takes.
 *
 * SLICE C must turn the six-leg test green; plain failing test, no `test.fail`. The one-leg test is a measurement.
 *
 * HOW A LEG IS JUDGED. The oracle of the stand (`../oracle.ts`) attributes a painted pixel of the FX canvas to a leg
 * only inside that leg's corridor (5 px from its axis) and away from the route's nodes (46 px): a node burst, a
 * highlighted intermediary, or paint anywhere else on the canvas is NOT a beam on a leg. Per leg it gives the frames
 * in which the leg was painted and where the head was. Two reproducers, both red on the base:
 *  (a) every leg of the six shows the beam MOVING, by the oracle's own unrelaxed limits (`MOTION_LIMITS`: at least 6
 *      different head positions, at most 0.15 of the edge per frame, at least 0.6 of the edge covered). The stand
 *      itself needs relaxed limits at 200 ms a leg (`MOTION_LIMITS_AT_CAP`); these are the ordinary ones.
 *  (b) handoff: leg k is first painted no later than the beam's own motion says. From the frames a leg IS painted in,
 *      its pace is known (frames per unit of the edge); the stretch nobody judges - the end of leg k-1 beyond its
 *      last painted position, and the start of leg k before its first - takes, at that pace, a known number of
 *      frames. The dark gap may exceed that by `GAP_SLACK_FRAMES` at most. A beam that fades out before the end of a
 *      leg, or a pause between legs, is a longer gap; a beam that simply passes a node is not.
 * Measurement only: speed (`length / life` per leg). Under P-3 a leg at the lower bound is slower than the route by
 * design, so "one speed for all legs" is not a property of a correct product; it is logged.
 *
 * THE WINDOW. The flight is stepped until the product holds no spark for `QUIET_FRAMES` frames in a row after having
 * held one - bounded by `MAX_FRAMES`; a flight that has not ended by then is an error of the run, not a pass.
 *
 * The six-leg route is found in the 100-node scene (trust lines of the stand's small scenes are all about one length):
 * an open path of six trust lines over seven nodes, every leg long enough for the oracle to judge (its own
 * precondition: 2 x 46 + 60 px).
 *
 * LIMITS. Quality `low` (a beam is its head dot). `ALPHA_MIN` is the oracle's threshold of "painted", not of "a human
 * sees it". One layout of one scene. The route is a path over trust lines found by geometry, not a route the backend
 * computed. Reduced motion and the clearing spark (P-3) are not walked here.
 */
import { expect, test, type Page } from '@playwright/test'

import { ALPHA_MIN, MOTION_LIMITS, NODE_CLEAR_PX, pickRingRoute, probeFrame, summarize, type EdgeSummary, type FrameProbe, type RouteGeom } from '../oracle.js'
import { boot, fixtureSnapshot100, frames, pushEvent, ringSnapshot, screenNodes, txUpdated, type Pt } from '../stand.js'
import { routeGeom } from './route7.js'

const RING = ['a', 'b', 'c', 'd', 'e', 'f']
/** The product holds no spark for this long after a flight: it is over (legs follow each other at once; 30 frames = 480 ms). */
const QUIET_FRAMES = 30
/** The bound of the wait: 8 s. Six legs of a whole second each fit; a flight still running then is an error. */
const MAX_FRAMES = 500
/** Frames the dark gap between two legs may exceed what the beam's own pace predicts (sampling, one frame at each end). */
const GAP_SLACK_FRAMES = 2
/** The oracle's own precondition for an edge it can watch a beam on (`pickRingRoute`). */
const MIN_LEG_PX = 2 * NODE_CLEAR_PX + 60

const note = (id: string, row: unknown) => {
  console.log(`[039 ${id}]`, JSON.stringify(row))
  test.info().annotations.push({ type: `039-${id}`, description: JSON.stringify(row) })
}

type Spark = { edge: string; startedAtMs: number; ttlMs: number }

async function sparks(page: Page): Promise<Spark[]> {
  return page.evaluate(() =>
    (window as unknown as { __geoSim: { fxState: { sparks: Array<{ source: string; target: string; startedAtMs: number; ttlMs: number }> } } })
      .__geoSim.fxState.sparks.map((s) => ({ edge: `${s.source}>${s.target}`, startedAtMs: s.startedAtMs, ttlMs: s.ttlMs })))
}

/**
 * Step the flight frame by frame until the product has finished it: the oracle's probe of every frame, and every
 * spark the product ever held. Throws when no spark ever appears or when the flight outlasts `MAX_FRAMES`.
 */
async function watchToTheEnd(page: Page, g: RouteGeom) {
  const probes: FrameProbe[] = []
  const lives = new Map<string, number>()
  let quiet = 0
  let lastWithSpark = 0
  for (let f = 1; f <= MAX_FRAMES; f += 1) {
    await frames(page, 1)
    probes.push(await probeFrame(page, g, f, false))
    const alive = await sparks(page)
    for (const s of alive) if (!lives.has(s.edge)) lives.set(s.edge, s.ttlMs)
    if (alive.length > 0) { quiet = 0; lastWithSpark = f } else if (lives.size > 0) quiet += 1
    if (lives.size > 0 && quiet >= QUIET_FRAMES) return { probes, lives, flightFrames: lastWithSpark }
  }
  throw new Error(lives.size === 0
    ? `039 R6: the product spawned no spark within ${MAX_FRAMES} frames of the event - nothing to judge`
    : `039 R6: the flight did not end within ${MAX_FRAMES} frames (${MAX_FRAMES * 16} ms) - the wait is bounded on purpose`)
}

const painted = (p: FrameProbe) => Object.values(p.edges).reduce((s, e) => s + e.px, 0) + p.offRoute + p.nearNode + p.crossing

/** Frames per unit of the edge, from the frames the leg is painted in; null when the leg was not seen moving. */
function pace(e: EdgeSummary): number | null {
  if (e.firstLit === null || e.lastLit === null || e.headFirst === null || e.headLast === null) return null
  const travelled = e.headLast - e.headFirst
  return travelled > 0.05 && e.lastLit > e.firstLit ? (e.lastLit - e.firstLit) / travelled : null
}

test('039 R6 measurement - one leg: for how long and how far the beam is painted, watched to the end of the flight', async ({ page }) => {
  await boot(page, { snapshot: ringSnapshot(), clock: true })
  const g = await pickRingRoute(page, RING, 1)
  const idle = await probeFrame(page, g, 0, false)
  expect(painted(idle), 'counter-check: nothing is painted on the FX canvas before the event').toBe(0)
  await pushEvent(page, txUpdated(g.ids, '10.00', { amount_flyout: false }))
  const { probes, lives, flightFrames } = await watchToTheEnd(page, g)
  const e = summarize(g, probes)[0]!
  note('R6 one leg', {
    edge: e.name, lifeMs: [...lives.values()], flightFrames, lengthPx: Math.round(Math.hypot(g.edges[0]!.b.x - g.edges[0]!.a.x, g.edges[0]!.b.y - g.edges[0]!.a.y)),
    paintedFrames: e.litFrames.length, firstLit: e.firstLit, lastLit: e.lastLit, paintedMs: e.litFrames.length * 16,
    head: [e.headFirst, e.headLast], distinctHeads: e.distinctHeads, maxStepPerFrame: e.maxStepPerFrame, headCovered: e.headCovered,
    framesOfTheFlightAfterTheLastPaintedOne: e.lastLit === null ? null : flightFrames - e.lastLit, alphaMin: ALPHA_MIN, specBudgetPerLegMs: 450,
  })
  expect(e.litFrames.length, 'counter-check: the beam was painted on its edge at all').toBeGreaterThan(0)
})

/**
 * An open route of six trust lines over seven different nodes of the 100-node scene whose legs differ most in length:
 * a bounded depth-first search over the lines as the layout drew them. A leg shorter than `minLegPx` is not taken (a
 * beam on it could not be judged). Deterministic for a given layout.
 */
function unequalRoute(pos: Map<string, Pt>, links: Array<{ source: string; target: string }>, minLegPx: number): { ids: string[]; lengths: number[]; ratio: number } | null {
  const adj = new Map<string, Array<{ to: string; len: number }>>()
  for (const l of links) {
    const a = pos.get(l.source)
    const b = pos.get(l.target)
    if (!a || !b) continue
    const len = Math.hypot(b.x - a.x, b.y - a.y)
    if (len < minLegPx) continue
    adj.set(l.source, [...(adj.get(l.source) ?? []), { to: l.target, len }])
    adj.set(l.target, [...(adj.get(l.target) ?? []), { to: l.source, len }])
  }
  // The oracle gives a painted pixel to NO leg where two legs of the route run within its corridor of each other
  // ("crossing"). A route that doubles back on itself (measured 2026-10-12: two legs nearly on one line were painted
  // and counted for neither) or crosses itself is therefore not taken: the next leg must leave its node at 30 degrees
  // or more from the leg that arrived, and must not cross an earlier leg.
  const cross = (p: Pt, q: Pt, r: Pt, t: Pt) => {
    const side = (a: Pt, b: Pt, c: Pt) => Math.sign((b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x))
    return side(p, q, r) !== side(p, q, t) && side(r, t, p) !== side(r, t, q)
  }
  const tellable = (ids: string[], next: string): boolean => {
    const cur = pos.get(ids[ids.length - 1]!)!
    const nxt = pos.get(next)!
    if (ids.length >= 2) {
      const prev = pos.get(ids[ids.length - 2]!)!
      const [ax, ay, bx, by] = [prev.x - cur.x, prev.y - cur.y, nxt.x - cur.x, nxt.y - cur.y]
      const cos = (ax * bx + ay * by) / (Math.hypot(ax, ay) * Math.hypot(bx, by))
      if (cos > Math.cos(Math.PI / 6)) return false
    }
    for (let i = 0; i + 2 < ids.length; i += 1) if (cross(pos.get(ids[i]!)!, pos.get(ids[i + 1]!)!, cur, nxt)) return false
    return true
  }
  let best: { ids: string[]; lengths: number[]; ratio: number } | null = null
  let budget = 400_000
  const walk = (ids: string[], lengths: number[]) => {
    if ((budget -= 1) < 0) return
    if (lengths.length === 6) {
      const ratio = Math.max(...lengths) / Math.min(...lengths)
      if (!best || ratio > best.ratio) best = { ids: [...ids], lengths: [...lengths], ratio }
      return
    }
    for (const e of adj.get(ids[ids.length - 1]!) ?? []) {
      if (ids.includes(e.to)) continue
      if (!tellable(ids, e.to)) continue
      ids.push(e.to)
      lengths.push(e.len)
      walk(ids, lengths)
      ids.pop()
      lengths.pop()
    }
  }
  for (const start of [...adj.keys()].sort()) walk([start], [])
  return best
}

test('039 R6 - six legs: the beam is seen moving on EVERY leg, and each leg takes over from the one before without a dark gap (today: 200 ms a leg, and the beam fades out inside each)', async ({ page }) => {
  const snapshot = fixtureSnapshot100()
  await boot(page, { snapshot, clock: true })
  const found = unequalRoute(await screenNodes(page), snapshot.links, MIN_LEG_PX)
  if (!found) throw new Error(`039 R6: no open route of six trust lines over seven nodes with legs of ${MIN_LEG_PX} px or more in this layout`)
  const g = await routeGeom(page, found.ids)
  const idle = await probeFrame(page, g, 0, false)
  expect(painted(idle), 'counter-check: nothing is painted on the FX canvas before the event').toBe(0)
  expect(new Set(found.ids).size, 'the route is open: six legs over seven different nodes').toBe(7)
  expect(Math.min(...g.lengths), 'every leg is long enough for the oracle to judge a beam on it').toBeGreaterThanOrEqual(MIN_LEG_PX)

  await pushEvent(page, txUpdated(found.ids, '10.00', { amount_flyout: false }))
  const { probes, lives, flightFrames } = await watchToTheEnd(page, g)
  const sum = summarize(g, probes)
  const legs = g.edges.map((e, i) => {
    const life = lives.get(e.name) ?? null
    const s = sum[i]!
    return {
      leg: i + 1, lengthPx: Math.round(g.lengths[i]!), lifeMs: life, speedPxPerMs: life ? Number((g.lengths[i]! / life).toFixed(3)) : null,
      paintedFrames: s.litFrames.length, firstLit: s.firstLit, lastLit: s.lastLit, head: [s.headFirst === null ? null : Number(s.headFirst.toFixed(3)), s.headLast === null ? null : Number(s.headLast.toFixed(3))],
      distinctHeads: s.distinctHeads, maxStepPerFrame: Number(s.maxStepPerFrame.toFixed(3)), headCovered: Number(s.headCovered.toFixed(3)),
    }
  })
  const junctions = sum.slice(1).map((cur, i) => {
    const prev = sum[i]!
    const [pp, pc] = [pace(prev), pace(cur)]
    const gap = prev.lastLit === null || cur.firstLit === null ? null : cur.firstLit - prev.lastLit - 1
    // The stretch nobody judges, at the pace each leg showed while it was painted.
    const predicted = pp === null || pc === null ? null : (1 - prev.headLast!) * pp + cur.headFirst! * pc
    return { into: i + 2, darkFrames: gap, predictedFrames: predicted === null ? null : Number(predicted.toFixed(1)), allowedFrames: predicted === null ? null : Math.ceil(predicted) + GAP_SLACK_FRAMES }
  })
  note('R6 six legs', {
    scene: '100 nodes, 348 lines', flightFrames, flightMs: flightFrames * 16, legs, junctions,
    ratioOfLengths: Number(found.ratio.toFixed(3)), chordClearPx: Number(g.chordClearPx.toFixed(1)), alphaMin: ALPHA_MIN, limits: MOTION_LIMITS, specBudgetPerLegMs: 450,
  })

  expect(legs.every((l) => l.lifeMs !== null), 'coverage: the product spawned a spark for every one of the six legs').toBe(true)
  expect(Math.max(...probes.map((p) => p.crossing)), 'precondition of the attribution: no painted pixel fell where two legs of the route cannot be told apart').toBe(0)
  // (a) Visible, attributable movement on every leg.
  for (const l of legs) {
    expect.soft(
      { painted: l.paintedFrames > 0, severalPlaces: l.distinctHeads >= MOTION_LIMITS.minDistinctHeads, boundedStep: l.maxStepPerFrame <= MOTION_LIMITS.maxStepPerFrame, mostOfTheEdge: l.headCovered >= MOTION_LIMITS.minCovered },
      `leg ${l.leg}: the beam is seen moving along it (painted in ${l.paintedFrames} frames; ${l.distinctHeads} head positions, need ${MOTION_LIMITS.minDistinctHeads}; largest step ${l.maxStepPerFrame}, allowed ${MOTION_LIMITS.maxStepPerFrame}; covers ${l.headCovered}, need ${MOTION_LIMITS.minCovered})`,
    ).toEqual({ painted: true, severalPlaces: true, boundedStep: true, mostOfTheEdge: true })
  }
  // (b) Handoff at every junction.
  for (const j of junctions) {
    expect.soft(
      j.darkFrames !== null && j.allowedFrames !== null && j.darkFrames <= j.allowedFrames,
      `handoff into leg ${j.into}: ${String(j.darkFrames)} dark frames between the last painted frame of the leg before and the first of this one; the beam's own pace predicts ${String(j.predictedFrames)}, allowed ${String(j.allowedFrames)}`,
    ).toBe(true)
  }
})
