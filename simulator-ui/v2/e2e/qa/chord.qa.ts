/**
 * 039 slice A, R14 - the straight-chord hazard of the motion oracle: a payment over a route drawn as ONE beam from
 * the sender straight to the receiver (the defect F-038-3 fixed in 038 S1, and still how the product draws a route
 * longer than `REAL_TX_MAX_ROUTE_EDGES`).
 *
 * Two questions, kept apart (spec 039, P-2):
 *  1. GEOMETRY - when can the oracle tell a chord from the route at all? `chordSeen` / `expectChordTold` (`oracle.ts`):
 *     the refusal of a layout on which it cannot. Judged here on made-up points, no browser.
 *  2. PIXELS - given a layout on which it can: does property 3 (`offRouteVerdict`, the very string `expectBeamOnRoute`
 *     asserts) accept the beam on its route and REFUSE the beam on the chord? Judged on an OPEN route of six legs over
 *     seven different nodes: the stand's own six-leg case is the whole ring of six, sender and receiver are one node
 *     there and the chord has no length, so nothing about a chord can be shown on it.
 *
 * The chord is drawn by the product itself, with no mutation of it: an event whose `edges` is the single pair
 * sender > receiver. The product spawns a beam between any two nodes it is given (`render/fxRenderer/spawn.ts`); that
 * the pair is no trust line of the scene is the point.
 *
 * What this does NOT see: properties 1, 2, 4 and 5 on the open route (its legs cross each other and are of unequal
 * length; the tempo of a leg is slice C's) - only "nothing off the route" and "every leg was painted" are asked of the
 * correct flight; a chord beam fainter than `ALPHA_MIN`; a chord beam whose head jumps more than
 * `MOTION_LIMITS.maxStepPerFrame` of the chord per frame (the stretch the refusal asks for is that long, no longer).
 */
import { expect, test } from '@playwright/test'

import { CHORD_CLEAR_PX, CORRIDOR_PX, MOTION_LIMITS, NODE_CLEAR_PX, chordSeen, expectChordTold, offRouteVerdict, probeFrame, routeGeomOf, watchUntilGone } from './oracle.js'
import { OPEN_ROUTE, boot, distToSegment, frames, fxCounts, openRouteSnapshot, pushEvent, txUpdated, type Pt } from './stand.js'

const NOTHING = 'nothing painted off the route'

test('chord, geometry: the oracle refuses a route whose chord cannot be told from it, and only such a route', () => {
  expect(CHORD_CLEAR_PX, 'the clearance is derived from the corridor of the oracle, not from a fixture').toBe(2 * CORRIDOR_PX)
  const ids = (n: number) => Array.from({ length: n }, (_, i) => `p${i}`)

  // Three sides of a regular hexagon of radius 200: the chord is a diameter, far from every side.
  const hex: Pt[] = [0, 1, 2, 3].map((k) => ({ x: 400 + 200 * Math.cos((k * Math.PI) / 3), y: 300 + 200 * Math.sin((k * Math.PI) / 3) }))
  const bent = expectChordTold(ids(4), hex)
  expect(bent.share, 'a bent route: most of its chord is off the route').toBeGreaterThan(0.5)

  // Two legs that are almost one straight line: the middle node is 6 px off the chord. A straightened beam would be
  // painted inside the corridor of the route from end to end - P1 to P4 would hold for it. This is the case to refuse.
  const straight: Pt[] = [{ x: 100, y: 300 }, { x: 300, y: 306 }, { x: 500, y: 300 }]
  expect(chordSeen(straight).seenPx, 'an almost straight route: no stretch of its chord is off the route').toBe(0)
  expect(() => expectChordTold(ids(3), straight), 'and the oracle refuses it').toThrow(/R3 refusal/)

  // A Z: the chord crosses the middle leg exactly at its own middle. The middle of the chord has NO clearance, and the
  // chord is told from the route all the same, on both sides of the crossing. (A rule on the middle point alone - the
  // first wording of R14 - would have refused this, and with it the open route of the stand on today's layout.)
  const z: Pt[] = [{ x: 100, y: 100 }, { x: 500, y: 100 }, { x: 100, y: 400 }, { x: 500, y: 400 }]
  const mid = { x: 300, y: 250 }
  expect(distToSegment(mid, z[1]!, z[2]!), 'the middle of the chord lies on the middle leg').toBeLessThan(1)
  expect(expectChordTold(ids(4), z).share, 'yet a stretch of the chord is off the route').toBeGreaterThanOrEqual(MOTION_LIMITS.maxStepPerFrame)

  // A closed route has no chord (sender and receiver are one node), a route of one edge IS its chord: neither is judged.
  const ring: Pt[] = [...hex, { x: 300, y: 127 }, hex[0]!]
  expect(() => expectChordTold(['p0', 'p1', 'p2', 'p3', 'p4', 'p0'], ring), 'a closed route is not refused').not.toThrow()
  expect(() => expectChordTold(ids(2), [straight[0]!, straight[2]!]), 'nor is a route of one edge').not.toThrow()
})

test('chord, pixels: on an open six-leg route the oracle accepts the beam on its legs and refuses the same payment drawn as one beam sender > receiver', async ({ page }) => {
  await boot(page, { snapshot: openRouteSnapshot(), clock: true })
  const g = await routeGeomOf(page, OPEN_ROUTE)
  expect(new Set(g.ids).size, 'six legs over seven different nodes: the route is open').toBe(7)
  // The refusal itself, as a precondition: on a layout that hides the chord this test says so instead of judging.
  const chord = expectChordTold(g.ids, g.nodes)
  const sender = g.nodes[0]!
  const receiver = g.nodes[g.nodes.length - 1]!

  const idle = await probeFrame(page, g, 0, false)
  expect({ offRoute: idle.offRoute, nearNode: idle.nearNode }, 'counter-check: nothing is painted on the FX canvas before the event').toEqual({ offRoute: 0, nearNode: 0 })

  // 1. The payment as the product draws it: leg after leg. Property 3 accepts it.
  await pushEvent(page, txUpdated(g.ids, '10.00', { amount_flyout: false }))
  // The whole beam (039 slice C: as long as its legs, not a fixed 1216 ms); no screenshots (P5 is not asked).
  const onRoute = await watchUntilGone(page, g, { shotEvery: 0 })
  const painted = g.edges.map((e) => onRoute.filter((p) => p.edges[e.name]!.px > 0).length)
  expect(offRouteVerdict(onRoute), `P3 accepts the beam on its route ${g.ids.join(' > ')}`).toBe(NOTHING)
  expect(painted.every((n) => n > 0), `counter-check: every leg of the route was painted in some frame (frames per leg: ${painted.join(', ')}) - "nothing off the route" is not "nothing at all"`).toBe(true)
  await frames(page, 30) // the receiver's burst fades
  expect((await fxCounts(page)).sparks, 'the beam is over').toBe(0)

  // 2. The same payment as one straight beam sender > receiver, judged against the same six-leg route. Property 3 refuses it.
  await pushEvent(page, txUpdated([g.ids[0]!, g.ids[g.ids.length - 1]!], '10.00', { amount_flyout: false }))
  const onChord = await watchUntilGone(page, g, { shotEvery: 0 })
  const off = onChord.filter((p) => p.offRoute > 0)
  const verdict = offRouteVerdict(onChord)
  const row = {
    route: g.ids.join('>'), chordPx: Math.round(chord.chordPx), seenPx: chord.seenPx, share: Number(chord.share.toFixed(3)), needShare: MOTION_LIMITS.maxStepPerFrame,
    clearPx: CHORD_CLEAR_PX, nodeClearPx: NODE_CLEAR_PX, framesPaintedPerLeg: painted, chordFramesOffRoute: off.length, maxOffRoutePx: Math.max(0, ...onChord.map((p) => p.offRoute)), verdict,
  }
  console.log('[039 chord]', JSON.stringify(row))
  test.info().annotations.push({ type: '039-chord', description: JSON.stringify(row) })
  expect(verdict, 'P3 refuses the beam on the chord: the oracle is red for a payment straightened into one beam').toMatch(/px painted off the route in frame \d+/)
  expect(verdict).not.toBe(NOTHING)
  // What was refused is the chord and nothing else: every reported pixel lies on the segment sender > receiver.
  const stray = off.filter((p) => distToSegment(p.offRouteAt!, sender, receiver) > CORRIDOR_PX)
  expect(stray.map((p) => p.frame), 'frames whose first off-route pixel is NOT on the chord').toEqual([])
  await frames(page, 30)
  expect((await fxCounts(page)).sparks, 'and the chord beam is over too').toBe(0)
})
