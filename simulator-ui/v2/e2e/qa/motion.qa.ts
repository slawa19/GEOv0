/**
 * 038 - R1 (the stand sees a spark), the motion oracle (S1: five properties of a beam, `oracle.ts`), R3 (the beam of a
 * route, F-038-3), R7 (a repeated event). Events enter through the product's own SSE reader, normalizer and pipeline;
 * FX state is only read.
 */
import { expect, test, type Page } from '@playwright/test'

import { expectBeamOnRoute, pickRingRoute, probeFrame, rememberBaseline, watchFlight } from './oracle.js'
import { RING, boot, frames, fxCounts, loopCounters, pushEvent, ringSnapshot, sampleCanvas, txUpdated } from './stand.js'

async function sparks(page: Page) {
  return page.evaluate(() =>
    (window as unknown as { __geoSim: { fxState: { sparks: Array<{ source: string; target: string; startedAtMs: number; ttlMs: number }> } } })
      .__geoSim.fxState.sparks.map((s) => ({ source: s.source, target: s.target, startedAtMs: s.startedAtMs, ttlMs: s.ttlMs })))
}

/**
 * One payment over `hops` consecutive edges of the ring, watched frame by frame for its whole 1200 ms and judged by the
 * five properties. The amount labels are switched off in the event (`amount_flyout: false`): a label is DOM over the
 * scene, it would change the screenshot by itself and make "the page changed here" true without a beam.
 */
async function flyAndJudge(page: Page, hops: number, o: { compositionMinimums?: boolean } = {}) {
  const g = await pickRingRoute(page, RING, hops)
  await rememberBaseline(page, g)
  const idle = await probeFrame(page, g, 0, true)
  expect(
    { offRoute: idle.offRoute, nearNode: idle.nearNode, route: Object.values(idle.edges).reduce((s, e) => s + e.px, 0) },
    'counter-check: nothing is painted on the FX canvas before the event',
  ).toEqual({ offRoute: 0, nearNode: 0, route: 0 })

  await pushEvent(page, txUpdated(g.ids, '10.00', { amount_flyout: false }))
  const probes = await watchFlight(page, g, 76, 1) // 1216 ms: the whole beam and one frame more
  const sum = expectBeamOnRoute(g, probes, { order: hops > 1, compositionMinimums: o.compositionMinimums })
  const row = sum.map((e) => ({
    edge: e.name, lit: [e.firstLit, e.lastLit], paintedFrames: e.litFrames.length, head: [e.headFirst?.toFixed(3), e.headLast?.toFixed(3)],
    places: e.distinctHeads, maxStep: e.maxStepPerFrame.toFixed(3), covered: e.headCovered.toFixed(3),
    buffer: [e.framesStrong, e.strongPx], page: [e.framesShown, e.shownPx], seenSpan: e.seenSpan?.toFixed(3),
  }))
  console.log(`[038 oracle] ${hops} edge(s) ${g.ids.join('>')}`, JSON.stringify(row), 'offRoute max', Math.max(...probes.map((p) => p.offRoute)), 'halfWidthPx', Math.max(...probes.map((p) => p.maxOffAxis)).toFixed(1))
  test.info().annotations.push({ type: `038-oracle-${hops}`, description: JSON.stringify(row) })

  const last = probes[probes.length - 1]!
  expect.soft(Object.values(last.edges).reduce((s, e) => s + e.px, 0), 'the beam is gone from the route by the end of its life (1200 ms)').toBe(0)
  await frames(page, 30) // the receiver's burst (420 ms) fades too
  expect.soft((await fxCounts(page)).sparks, 'and no spark is left in the FX state').toBe(0)
}

test.beforeEach(async ({ page }) => {
  await boot(page, { snapshot: ringSnapshot(), clock: true })
})

// LIMITS OF R1 (adversarial pass 2026-10-11, AGENTS.md 11: a guard names what it does not check).
// R1 proves "a spark exists, something is painted, the loop draws". It does NOT measure movement: a spark frozen at
// phase 0.333 (render/fxRenderer/renderFrame.ts:116) passes R1. It does not look at the composed screen either: a DOM
// panel over the path hides the spark and R1 stays green. Movement, direction, route, order and visibility along the
// whole path are judged by the motion oracle below, not here.
test('R1 - the stand sees the spark of a payment: FX state and pixels of the FX canvas (F-038-1)', async ({ page }) => {
  // Counter-checks first: the sampler reads "nothing" before the event, and the loop draws nothing while asleep.
  const idle = await sampleCanvas(page, {})
  expect(idle.__canvas.lit, 'no painted pixel on the FX canvas before the event').toBe(0)
  expect((await fxCounts(page)).sparks).toBe(0)
  const d0 = await loopCounters(page)

  await pushEvent(page, txUpdated(['a', 'b']))
  await frames(page, 31) // 496 ms of the clock, 31 frames

  const d1 = await loopCounters(page)
  const fx = await fxCounts(page)
  const lit = await sampleCanvas(page, {})
  console.log('[038 R1]', JSON.stringify({ fx, canvas: lit.__canvas, draws: d1.draws - d0.draws, rafCalls: d1.calls - d0.calls }))
  expect(fx.sparks, 'fxState.sparks.length > 0').toBeGreaterThan(0)
  expect(lit.__canvas.lit, 'painted pixels on the FX canvas').toBeGreaterThan(0)
  expect(d1.draws - d0.draws, 'counter-check: the loop drew every frame while the spark flew').toBe(31)
})

// THE MOTION ORACLE ON ONE EDGE. Four of the five properties (`oracle.ts`): displacement, direction, route,
// composition. Order (property 4) needs two edges and is NOT judged here; it is judged by the R3 tests after this
// measurement. Each property has a mutation of the product that must turn it red; the mutations and their red output
// are recorded in the changelog of spec 038 (S1), they are not committed.
test('oracle - a payment over one edge: the beam moves, from sender to receiver, on its edge only, and is seen on the page', async ({ page }) => {
  await flyAndJudge(page, 1)
})

// R3 (F-038-3 = F-034-D4), FIXED IN S1. Until 2026-10-11 the product drew a route of more than two edges as ONE
// straight beam sender -> receiver (a chord that is no edge of the route) and started the edges of a two-edge route
// at the same instant; both were recorded here as the current behaviour, the second and third test below stood under
// `test.fail`. The annotation was taken off by the single condition of spec 038 (Verification plan), not because the
// tests went green: all five properties are green on the fixed product AND the mutation of each was run against the
// fixed product and is red - phase frozen (P1), edge reversed in the renderer (P2), sparks on every edge of the graph
// (P3), all edges of the route started at once (P4), and for P5 all three: FX canvas under the base canvas, FX canvas
// with opacity 0, a DOM panel over the path. The red output of each is in the changelog of spec 038.
// DO NOT put `test.fail` back to make a red run green: a red test here is the beam leaving its route.
test('R3 - a payment over two edges: the second edge lights up only after the first has gone dark (F-038-3, property 4)', async ({ page }) => {
  await flyAndJudge(page, 2)
})

test('R3 - the beam of a 3-edge payment follows the edges of its route, one after another (F-038-3 = F-034-D4)', async ({ page }) => {
  await flyAndJudge(page, 3)
})

test('R3 - FX state of a 3-edge payment: one spark alive at a time, in route order, 400 ms each; the receiver label comes at 1200 ms', async ({ page }) => {
  const g = await pickRingRoute(page, RING, 3)
  await pushEvent(page, txUpdated(g.ids, '10.00'))
  const seen: string[] = []
  let maxAlive = 0
  let receiverLabelAtFrame: number | null = null
  for (let f = 1; f <= 80; f += 1) {
    await frames(page, 1)
    // Alive = not yet past its TTL. A spark whose time is over stays in the array until the next frame drops it
    // (`renderFrame.ts`), and the timer of the next edge may fire before that frame; it is never drawn again.
    const all = await sparks(page)
    const now = await page.evaluate(() => performance.now())
    const alive = all.filter((s) => now - s.startedAtMs < s.ttlMs)
    maxAlive = Math.max(maxAlive, alive.length)
    for (const s of alive) {
      const k = `${s.source}>${s.target}+${s.ttlMs}`
      if (seen[seen.length - 1] !== k) seen.push(k)
    }
    const plus = await page.evaluate(() => [...document.querySelectorAll('.floating-label-inner')].some((el) => (el.textContent ?? '').trim().startsWith('+')))
    if (plus && receiverLabelAtFrame === null) receiverLabelAtFrame = f
  }
  console.log('[038 R3] fx state', JSON.stringify({ seen, maxAlive, receiverLabelAtFrame }))
  expect(seen).toEqual(g.edges.map((e) => `${e.name}+400`))
  expect(maxAlive, 'never two sparks of one payment at once').toBe(1)
  // 1200 ms is frame 75; the label is pushed by a timer at 1200 ms and mounted by the frame after it.
  expect(receiverLabelAtFrame, 'the receiver label appears when the beam arrives: at the total route time, as before the fix').toBeGreaterThanOrEqual(75)
  expect(receiverLabelAtFrame).toBeLessThanOrEqual(77)
})

// MEASUREMENT, not a verdict (spec 038, S1: "if an edge at k = 6 passes too fast to be read, S1 returns the number").
// Six edges is the cap (`REAL_TX_MAX_ROUTE_EDGES`): the whole ring, 200 ms = 12.5 frames an edge. Displacement,
// direction, route and order are judged here as everywhere. Composition is judged without its minimums ("3 frames,
// 0.15 of the edge"): at 200 ms an edge is strongly painted in 2-4 frames only. Every strong buffer pixel must still
// be on the page. What goes to the spec is the printed row: frames painted per edge and how far the head is seen.
test('R3 - measurement at the cap: a 6-edge route, 200 ms an edge - frames painted per edge and head travel', async ({ page }) => {
  await flyAndJudge(page, 6, { compositionMinimums: false })
})

// The stand's browser has no GPU and the quality autoguard puts it on `low`, where a beam is its head dot only
// (`renderFrame.ts`). `med` and `high` draw a trail and a bright segment behind the head; the same five properties
// must hold there. The quality is chosen as a user chooses it, in the bottom bar.
for (const quality of ['med', 'high'] as const) {
  test(`R3 - the five properties hold at quality "${quality}" too (a 2-edge payment)`, async ({ page }) => {
    await page.getByRole('combobox', { name: 'Quality' }).selectOption(quality)
    await frames(page, 10)
    expect(await page.evaluate(() => document.querySelector<HTMLElement>('.root')?.dataset.quality)).toBe(quality)
    await flyAndJudge(page, 2)
  })
}

test('prefers-reduced-motion: a payment draws no beam and schedules none, and its amount labels still appear', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  // The product learns of the preference from a `change` event of the media query (`useReducedMotionPreference.ts`),
  // which the browser delivers on one of ITS frames, not on the JS clock this test steps. Wait for the product to
  // have taken it (`data-motion` on the root), not for frames: pushing the event earlier animated it 2 times in 100.
  await expect(page.locator('.root')).toHaveAttribute('data-motion', 'reduced')
  const g = await pickRingRoute(page, RING, 3)
  await pushEvent(page, txUpdated(g.ids, '10.00'))
  let maxSparks = 0
  let maxLabels = 0
  let painted = 0
  for (let f = 0; f < 80; f += 1) { // the whole route time: a leg scheduled on a timer would show up here
    await frames(page, 1)
    const c = await fxCounts(page)
    maxSparks = Math.max(maxSparks, c.sparks)
    maxLabels = Math.max(maxLabels, c.labels)
    painted = Math.max(painted, (await sampleCanvas(page, {})).__canvas.lit)
  }
  console.log('[038 reduced-motion]', JSON.stringify({ maxSparks, maxLabels, painted }))
  expect(maxSparks, 'no spark at any moment of the route time').toBe(0)
  expect(painted, 'nothing is painted on the FX canvas').toBe(0)
  expect(maxLabels, 'counter-check: the event did arrive - its amount labels are shown').toBeGreaterThan(0)
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
