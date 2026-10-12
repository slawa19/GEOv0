/**
 * 039 T3901 - an OPEN route of six legs over seven different nodes, with legs of unequal length. Not a test file.
 *
 * The stand's own six-leg case is the whole ring of six (`motion.qa.ts`): sender and receiver are one node, the chord
 * has no length, and every leg is a side of the same hexagon. That fixture can show neither "constant speed on legs of
 * unequal length" (P-3) nor a chord that is told from the route (P-2, R14). This one can: seven nodes on a ring, and a
 * route that mixes sides of the ring with chords across it - 0 > 1 > 3 > 6 > 5 > 2 > 4.
 *
 * Since 2026-10-12 (039 slice A) the scene and the route are the stand's own (`OPEN_ROUTE`, `openRouteSnapshot` in
 * `stand.ts`; `routeGeomOf` in `oracle.ts`): the gate judges the chord on them (`chord.qa.ts`). The names below stay
 * for the reproducers of T3901 and give the same values.
 *
 * The lengths are whatever the force layout gives; callers assert that they ARE unequal before judging anything.
 */
import type { Page } from '@playwright/test'

import { NODE_CLEAR_PX, routeGeomOf, type RouteGeom } from '../oracle.js'
import { OPEN_ROUTE, distToSegment, openRouteSnapshot, type QaSnapshot } from '../stand.js'

export const ROUTE7 = OPEN_ROUTE

export const route7Snapshot = (): QaSnapshot => openRouteSnapshot()

export async function routeGeom(page: Page, ids: readonly string[]): Promise<RouteGeom & { lengths: number[]; chordClearPx: number }> {
  const g = await routeGeomOf(page, ids)
  const first = g.nodes[0]!
  const last = g.nodes[g.nodes.length - 1]!
  const mid = { x: (first.x + last.x) / 2, y: (first.y + last.y) / 2 }
  return {
    ...g,
    lengths: g.edges.map((e) => Math.hypot(e.b.x - e.a.x, e.b.y - e.a.y)),
    // The measure of T3901 (the score of `pickRingRoute`): how far the MIDDLE of the chord sender -> receiver is from every
    // route edge. The refusal the oracle got in slice A is not this number - see `chordSeen` in `oracle.ts`.
    chordClearPx: Math.min(...g.edges.map((e) => distToSegment(mid, e.a, e.b))),
  }
}

/** The part of a leg the oracle judges: what is left of it outside `NODE_CLEAR_PX` of its two nodes. */
export const judgedLength = (len: number) => Math.max(0, len - 2 * NODE_CLEAR_PX)