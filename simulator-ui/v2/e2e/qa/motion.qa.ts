/**
 * 038 S0 - R1 (the stand sees a spark), R3 (the beam of a long route is straightened, F-038-3), R7 (a repeated
 * event). Events enter through the product's own SSE reader, normalizer and pipeline; FX state is only read.
 */
import { expect, test, type Page } from '@playwright/test'

import {
  boot, distToSegment, frames, fxCounts, loopCounters, pointsAlong, pushEvent, ringSnapshot, sampleCanvas, screenNodes,
  txUpdated, type Pt,
} from './stand.js'

const RING = ['a', 'b', 'c', 'd', 'e', 'f']

type Bands = { ab: Pt[]; bc: Pt[]; cd: Pt[]; chord: Pt[] }
type Route = { ids: [string, string, string, string]; bands: Bands }

/**
 * Four consecutive nodes of the ring (a real path over trust lines) with bands along each of its three edges and along
 * the chord first -> last. The force layout does not promise a convex ring, so the route is the one whose chord has
 * the most points clear of every route edge; the test refuses to run on a layout that leaves it nothing to look at.
 */
async function pickRoute(page: Page): Promise<Route> {
  const n = await screenNodes(page)
  let best: Route | null = null
  for (let i = 0; i < RING.length; i += 1) {
    const ids = [0, 1, 2, 3].map((k) => RING[(i + k) % RING.length]!) as Route['ids']
    const [a, b, c, d] = ids.map((id) => n.get(id)!) as [Pt, Pt, Pt, Pt]
    const clear = (p: Pt) => [[a, b], [b, c], [c, d]].every(([s, e]) => distToSegment(p, s!, e!) > 24)
    const bands = { ab: pointsAlong(a, b), bc: pointsAlong(b, c), cd: pointsAlong(c, d), chord: pointsAlong(a, d, { endMarginPx: 40 }).filter(clear) }
    // The middle edge must be clear of the chord too, or a straightened beam would light it by accident.
    bands.bc = bands.bc.filter((p) => distToSegment(p, a, d) > 24)
    if (!best || Math.min(bands.chord.length, bands.bc.length) > Math.min(best.bands.chord.length, best.bands.bc.length)) best = { ids, bands }
  }
  expect(Math.min(best!.bands.chord.length, best!.bands.bc.length), 'the layout leaves a chord and a middle edge clear of each other').toBeGreaterThan(10)
  return best!
}

async function sparks(page: Page) {
  return page.evaluate(() =>
    (window as unknown as { __geoSim: { fxState: { sparks: Array<{ source: string; target: string; startedAtMs: number; ttlMs: number; kind: string }> } } })
      .__geoSim.fxState.sparks.map((s) => ({ source: s.source, target: s.target, startedAtMs: s.startedAtMs, ttlMs: s.ttlMs, kind: s.kind })))
}

/** Step the clock frame by frame and collect, per band, the largest number of lit points and the first frame it lit. */
async function watch(page: Page, bands: Bands, nFrames: number) {
  const acc: Record<string, { points: number; maxLit: number; firstLitFrame: number | null }> = {}
  for (const [k, pts] of Object.entries(bands)) acc[k] = { points: pts.length, maxLit: 0, firstLitFrame: null }
  for (let f = 1; f <= nFrames; f += 1) {
    await frames(page, 1)
    const s = await sampleCanvas(page, bands)
    for (const k of Object.keys(bands)) {
      const r = s[k]!
      if (r.lit > acc[k]!.maxLit) acc[k]!.maxLit = r.lit
      if (r.lit > 0 && acc[k]!.firstLitFrame === null) acc[k]!.firstLitFrame = f
    }
  }
  return acc
}

test.beforeEach(async ({ page }) => {
  await boot(page, { snapshot: ringSnapshot(), clock: true })
})

// LIMITS OF R1 (adversarial pass 2026-10-11, AGENTS.md 11: a guard names what it does not check).
// R1 proves "a spark exists, something is painted, the loop draws". It does NOT measure movement: a spark frozen at
// phase 0.333 (render/fxRenderer/renderFrame.ts:116) passes this whole file. It does not look at the composed screen
// either: a DOM panel over the path hides the spark and R1 stays green. Movement, direction and visibility along the
// whole path are the oracle S1 has to build (spec 038, Verification plan); until then R1 is not evidence of motion.
test('R1 - the stand sees the spark of a payment: FX state and pixels of the FX canvas (F-038-1)', async ({ page }) => {
  const { ids, bands } = await pickRoute(page)

  // Counter-checks first: the sampler reads "nothing" before the event, and the loop draws nothing while asleep.
  const idle = await sampleCanvas(page, bands)
  expect(idle.__canvas.lit, 'no painted pixel on the FX canvas before the event').toBe(0)
  expect((await fxCounts(page)).sparks).toBe(0)
  const d0 = await loopCounters(page)

  await pushEvent(page, txUpdated(ids))
  await frames(page, 31) // 496 ms of the clock, 31 frames

  const d1 = await loopCounters(page)
  const fx = await fxCounts(page)
  const lit = await sampleCanvas(page, bands)
  console.log('[038 R1]', JSON.stringify({ fx, canvas: lit.__canvas, draws: d1.draws - d0.draws, rafCalls: d1.calls - d0.calls }))
  expect(fx.sparks, 'fxState.sparks.length > 0').toBeGreaterThan(0)
  expect(lit.__canvas.lit, 'painted pixels on the FX canvas').toBeGreaterThan(0)
  expect(d1.draws - d0.draws, 'counter-check: the loop drew every frame while the spark flew').toBe(31)
})

test('R3 - characterization: a 3-edge route is one beam on the chord sender -> receiver; a 2-edge route is two beams started together', async ({ page }) => {
  const { ids, bands } = await pickRoute(page)
  await pushEvent(page, txUpdated(ids))
  await frames(page, 1)
  const three = await sparks(page)
  const seen3 = await watch(page, bands, 74) // the whole 1200 ms beam
  console.log('[038 R3] 3 edges', JSON.stringify({ sparks: three, seen: seen3 }))
  expect(three.map((s) => `${s.source}>${s.target}`)).toEqual([`${ids[0]}>${ids[3]}`])
  expect(seen3.chord!.maxLit, 'the chord, which is no edge of the route, lights up').toBeGreaterThan(0)
  expect(seen3.bc!.maxLit, 'the middle edge of the route never lights up').toBe(0)

  await frames(page, 40) // past the minimum gap between two animated payments and the end of the first beam
  expect((await fxCounts(page)).sparks).toBe(0)
  await pushEvent(page, txUpdated(ids.slice(0, 3)))
  await frames(page, 1)
  const two = await sparks(page)
  const seen2 = await watch(page, bands, 20)
  console.log('[038 R3] 2 edges', JSON.stringify({ sparks: two, seen: seen2 }))
  expect(two.map((s) => `${s.source}>${s.target}`)).toEqual([`${ids[0]}>${ids[1]}`, `${ids[1]}>${ids[2]}`])
  expect(two[0]!.startedAtMs, 'both edges start at the same instant, not one after another').toBe(two[1]!.startedAtMs)
  expect(seen2.ab!.firstLitFrame, 'the first edge lights up').not.toBeNull()
  expect(seen2.bc!.firstLitFrame, 'the second edge lights up in the same frame as the first').toBe(seen2.ab!.firstLitFrame)
})

test('R3 - the beam of a 3-edge payment follows the edges of its route (F-038-3 = F-034-D4)', async ({ page }) => {
  // Red today because of the product: `__pickSparkEdges` (useRealTxFx.ts:46-61) replaces a route longer than
  // `maxEdgesPerEvent` (2) with the single pair sender -> receiver. Goes green when S1 fixes it; the annotation then
  // fails the run, which is the signal to drop it.
  test.fail(true, 'F-038-3: a route of more than 2 edges is drawn as one straight beam sender -> receiver')
  // DO NOT REMOVE `test.fail` ON THE STRENGTH OF THIS TEST GOING GREEN (adversarial pass 2026-10-11).
  // The two assertions below are satisfied by wrong fixes: sparks on every edge of the graph (edges outside the route
  // are not sampled), route edges each drawn backwards (the bands are symmetric along the edge), and all edges
  // started at once (order is not measured). The annotation comes off only after S1 has built the motion oracle and
  // its four mutations are red: phase frozen, direction reversed, sparks on all edges, simultaneous start.
  const { ids, bands } = await pickRoute(page)
  await pushEvent(page, txUpdated(ids))
  const seen = await watch(page, bands, 75)
  console.log('[038 R3] target', JSON.stringify(seen))
  expect
    .soft({ ab: seen.ab!.maxLit > 0, bc: seen.bc!.maxLit > 0, cd: seen.cd!.maxLit > 0 }, 'every edge of the route lights up')
    .toEqual({ ab: true, bc: true, cd: true })
  expect.soft(seen.chord!.maxLit, 'nothing is painted on the chord a -> d away from the route').toBe(0)
})

test('R7 - characterization: the same event_id delivered twice animates once; a new event_id animates again', async ({ page }) => {
  const id = await pushEvent(page, txUpdated(['a', 'b']))
  await frames(page, 20) // 320 ms: past the 120 ms minimum gap, so only the admission by event_id can drop a repeat
  const first = await fxCounts(page)
  await pushEvent(page, txUpdated(['a', 'b']), { eventId: id })
  await frames(page, 2)
  const repeated = await fxCounts(page)
  // Counter-check: the same payload under a new event_id IS animated again, so "one" above is the admission at work.
  await pushEvent(page, txUpdated(['a', 'b']))
  await frames(page, 2)
  const fresh = await fxCounts(page)
  console.log('[038 R7]', JSON.stringify({ first, repeated, fresh }))
  expect(first.sparks).toBe(1)
  expect(repeated.sparks, 'a repeat of the same event_id adds no spark').toBe(1)
  expect(repeated.labels, 'and no label').toBe(first.labels)
  expect(fresh.sparks, 'a new event_id of the same payment is a second animation').toBe(2)
})
