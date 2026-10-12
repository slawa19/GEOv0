/**
 * 039 T3901 - R14, the GEOMETRY third (runner behaviour and project membership: `r14-membership.p039-repro.mjs`).
 *
 * The spec asked the oracle to refuse a route of two or more edges whose chord sender -> receiver lies within
 * `2 * CORRIDOR_PX` of a route edge (a straightened beam could then not be told from the route). Corrected form (P-2,
 * arbiter F9): the stand's six-leg case is the whole ring of six - sender and receiver are one node, the chord has no
 * length, its clearance is 0 - so a refusal for every route would reject that fixture on a correct product. The case
 * for the refusal is an OPEN route of six legs over seven different nodes.
 *
 * MEASUREMENT: the clearance of the chord for the stand's own routes and for the open route. One fact is asserted as
 * the state of today: `pickRingRoute` hands back the closed six-leg route although its chord clearance is 0 - there is
 * no refusal (in `oracle.ts` the clearance only enters the score of a candidate).
 * NOT a reproducer: whether an open route with a chord under the threshold exists depends on the force layout of the
 * day (the candidates are listed with their clearance); a red test built on that would be red by luck of the layout.
 *
 * No slice flips anything here: measurements, green today. They serve SLICE A's stand work (the chord refusal).
 */
import { expect, test } from '@playwright/test'

import { CORRIDOR_PX, pickRingRoute } from '../oracle.js'
import { boot, ringSnapshot } from '../stand.js'
import { ROUTE7, route7Snapshot, routeGeom } from './route7.js'

const RING = ['a', 'b', 'c', 'd', 'e', 'f']

test('039 R14 measurement - clearance of the chord: the routes of the ring of six, closed six-leg route included', async ({ page }) => {
  await boot(page, { snapshot: ringSnapshot(), clock: true })
  const rows: Array<{ hops: number; ids: string; closed: boolean; chordClearPx: number }> = []
  for (const hops of [2, 3, 6]) {
    const picked = await pickRingRoute(page, RING, hops) // does not throw today, whatever the clearance
    const g = await routeGeom(page, picked.ids)
    rows.push({ hops, ids: picked.ids.join('>'), closed: picked.ids[0] === picked.ids[picked.ids.length - 1], chordClearPx: Number(g.chordClearPx.toFixed(1)) })
  }
  console.log('[039 R14 chord, ring of six]', JSON.stringify({ refusalBelowPx: 2 * CORRIDOR_PX, rows }))
  test.info().annotations.push({ type: '039-R14-chord-ring', description: JSON.stringify(rows) })
  const six = rows.find((r) => r.hops === 6)!
  expect(six.closed, 'the six-leg route of the ring of six is closed: sender and receiver are one node').toBe(true)
  expect(six.chordClearPx, 'so its chord has no length and no clearance - and the oracle hands the route back all the same').toBe(0)
  for (const r of rows.filter((x) => x.hops < 6)) expect(r.chordClearPx, `the ${r.hops}-leg route is told from its chord`).toBeGreaterThan(2 * CORRIDOR_PX)
})

test('039 R14 measurement - clearance of the chord on OPEN six-leg routes over seven nodes (the case a refusal can be judged on)', async ({ page }) => {
  await boot(page, { snapshot: route7Snapshot(), clock: true })
  // Every open six-leg route this order of seven nodes gives: seven starts, two directions.
  const candidates: Array<{ route: string; legsPx: number[]; chordClearPx: number }> = []
  for (const dir of [1, -1]) {
    for (let i = 0; i < ROUTE7.length; i += 1) {
      const ids = Array.from({ length: 7 }, (_, k) => ROUTE7[(((i + dir * k) % 7) + 7) % 7]!)
      const g = await routeGeom(page, ids)
      candidates.push({ route: ids.join('>'), legsPx: g.lengths.map((l) => Math.round(l)), chordClearPx: Number(g.chordClearPx.toFixed(1)) })
    }
  }
  const below = candidates.filter((c) => c.chordClearPx <= 2 * CORRIDOR_PX)
  let picked: string
  try {
    picked = (await pickRingRoute(page, [...ROUTE7], 6)).ids.join('>')
  } catch (e) {
    picked = `refused or failed a precondition: ${String((e as Error).message).split(/\r?\n/)[0]}`
  }
  const row = { refusalBelowPx: 2 * CORRIDOR_PX, candidates, belowTheThreshold: below.map((c) => c.route), pickedByTheOracle: picked }
  console.log('[039 R14 chord, open routes]', JSON.stringify(row))
  test.info().annotations.push({ type: '039-R14-chord-open', description: JSON.stringify(row) })
  expect(candidates.every((c) => new Set(c.route.split('>')).size === 7), 'six legs over seven different nodes, every candidate').toBe(true)
  expect(candidates.some((c) => c.chordClearPx > 2 * CORRIDOR_PX), 'the scene offers an open route whose chord runs clear of its edges: the refusal can be judged on it without rejecting everything').toBe(true)
})
