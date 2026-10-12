/**
 * 039 T3901 - R6 (pace of the beam), what is SEEN: one leg, and six legs of unequal length. Schedule tier:
 * `src/p039-repro/r06-beam-schedule.p039-repro.test.ts`.
 *
 * Contract (P-3): "constant speed" is a constant distance per time over the whole route, with a lower bound per leg;
 * the handoff between legs must be visible - today alpha decays inside every leg (`renderFrame.ts`), so a slower leg
 * alone would not give it. Corrected form (P-2): non-zero coverage is mandatory, and the dark gap between legs is bounded.
 *
 * Reproducer (red on the base), with a derived bound:
 *  - handoff: between the end of the sender's burst and the end of the route the FX canvas is never dark for more
 *    than one frame. "Dark" = no pixel at or above the oracle's `ALPHA_MIN` anywhere on the FX canvas, so the zones
 *    round nodes that the oracle does not attribute to a leg ARE counted here - a beam passing a node is not "dark".
 * Measurement only, on purpose:
 *  - speed: `length / life` per leg (screen px from the layout, lives from the product's FX state). It is NOT asserted.
 *    Under P-3 a leg at the lower bound is slower than the route by design, so "all legs at one speed" is false for a
 *    correct product, and "legs above the bound share one speed" is true for today's product too (every leg has the
 *    same life, so every leg reads as "at the bound"). The property cannot be judged before the bound has a value.
 *  - how long a single leg is painted, and the spec's design budget of 450 ms next to it.
 *
 * The six-leg route is found in the 100-node scene: there trust lines really differ in length. (The stand's small
 * scenes do not serve: the force layout gives every line about the same length - a ring of seven with chords gave
 * 350..422 px, a ratio of 1.2.)
 *
 * LIMITS. Quality `low` (a beam is its head dot). `ALPHA_MIN` is the oracle's threshold of "painted", not of "a human
 * sees it". One layout of one scene; the speed bound is judged on whatever lengths the force layout gave (the test
 * refuses a layout whose legs are nearly equal). The route is a path over trust lines found by geometry, not a route
 * the backend computed. Reduced motion and the clearing spark (P-3) are not walked here.
 *
 * SLICE C must turn the handoff test green; plain failing test, no `test.fail`. The one-leg test is a measurement.
 */
import { expect, test, type Page } from '@playwright/test'

import { ALPHA_MIN, pickRingRoute, probeFrame, summarize, type FrameProbe, type RouteGeom } from '../oracle.js'
import { boot, fixtureSnapshot100, frames, pushEvent, ringSnapshot, screenNodes, txUpdated, type Pt } from '../stand.js'
import { judgedLength, routeGeom } from './route7.js'

const RING = ['a', 'b', 'c', 'd', 'e', 'f']
/** The sender's burst lives 280 ms (`useRealTxFx.ts`): 18 frames. Until then the canvas is lit by the burst, not by the beam. */
const SENDER_BURST_FRAMES = 18
const ROUTE_FRAMES = 75 // 1200 ms

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

/** Step the flight frame by frame: the oracle's probe of every frame, and every spark the product ever held. */
async function watch(page: Page, g: RouteGeom, n: number) {
  const probes: FrameProbe[] = []
  const lives = new Map<string, number>()
  for (let f = 1; f <= n; f += 1) {
    await frames(page, 1)
    probes.push(await probeFrame(page, g, f, false))
    for (const s of await sparks(page)) if (!lives.has(s.edge)) lives.set(s.edge, s.ttlMs)
  }
  return { probes, lives }
}

const painted = (p: FrameProbe) => Object.values(p.edges).reduce((s, e) => s + e.px, 0) + p.offRoute + p.nearNode + p.crossing

/** Runs of consecutive dark frames inside [from, to]. */
function darkRuns(probes: FrameProbe[], from: number, to: number): number[] {
  const runs: number[] = []
  let cur = 0
  for (const p of probes) {
    if (p.frame < from || p.frame > to) continue
    if (painted(p) === 0) cur += 1
    else { if (cur) runs.push(cur); cur = 0 }
  }
  if (cur) runs.push(cur)
  return runs
}

test('039 R6 measurement - one leg of 1200 ms: for how long and how far the beam is painted', async ({ page }) => {
  await boot(page, { snapshot: ringSnapshot(), clock: true })
  const g = await pickRingRoute(page, RING, 1)
  const idle = await probeFrame(page, g, 0, false)
  expect(painted(idle), 'counter-check: nothing is painted on the FX canvas before the event').toBe(0)
  await pushEvent(page, txUpdated(g.ids, '10.00', { amount_flyout: false }))
  const { probes, lives } = await watch(page, g, ROUTE_FRAMES + 3)
  const e = summarize(g, probes)[0]!
  const dark = darkRuns(probes, SENDER_BURST_FRAMES + 1, ROUTE_FRAMES)
  note('R6 one leg', {
    edge: e.name, lifeMs: [...lives.values()], lengthPx: Math.round(Math.hypot(g.edges[0]!.b.x - g.edges[0]!.a.x, g.edges[0]!.b.y - g.edges[0]!.a.y)),
    paintedFrames: e.litFrames.length, firstLit: e.firstLit, lastLit: e.lastLit, paintedMs: e.litFrames.length * 16, ofMs: 1200,
    head: [e.headFirst, e.headLast], headCovered: e.headCovered,
    darkFramesBeforeTheEnd: dark.reduce((s, r) => s + r, 0), longestDarkRunFrames: Math.max(0, ...dark), alphaMin: ALPHA_MIN, specBudgetPerLegMs: 450,
  })
  expect(e.litFrames.length, 'counter-check: the beam was painted on its edge at all').toBeGreaterThan(0)
})

/**
 * An open route of six trust lines over seven different nodes of the 100-node scene whose legs differ most in length:
 * a bounded depth-first search over the lines as the layout drew them. A leg shorter than `minLegPx` is not taken (a
 * beam on it could not be watched). Deterministic for a given layout.
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

test('039 R6 - six legs of unequal length: no dark gap between legs (today: the beam fades out inside each leg); speed per leg is measured', async ({ page }) => {
  const snapshot = fixtureSnapshot100()
  await boot(page, { snapshot, clock: true })
  const found = unequalRoute(await screenNodes(page), snapshot.links, 60)
  if (!found) throw new Error('039 R6: no open route of six trust lines over seven nodes with legs of 60 px or more in this layout')
  const g = await routeGeom(page, found.ids)
  const idle = await probeFrame(page, g, 0, false)
  expect(painted(idle), 'counter-check: nothing is painted on the FX canvas before the event').toBe(0)
  const ratioOfLengths = found.ratio
  expect(new Set(found.ids).size, 'the route is open: six legs over seven different nodes').toBe(7)
  expect(ratioOfLengths, `the layout gave legs of unequal length (${g.lengths.map((l) => Math.round(l)).join(', ')} px) - otherwise speed has nothing to show`).toBeGreaterThan(1.3)

  await pushEvent(page, txUpdated(found.ids, '10.00', { amount_flyout: false }))
  const { probes, lives } = await watch(page, g, ROUTE_FRAMES + 3)
  const sum = summarize(g, probes)
  const legs = g.edges.map((e, i) => {
    const life = lives.get(e.name) ?? null
    const s = sum[i]!
    return {
      edge: `leg ${i + 1}`, lengthPx: Math.round(g.lengths[i]!), judgedPx: Math.round(judgedLength(g.lengths[i]!)), lifeMs: life,
      speedPxPerMs: life ? Number((g.lengths[i]! / life).toFixed(3)) : null, paintedFramesInJudgedPart: s.litFrames.length, firstLit: s.firstLit, lastLit: s.lastLit,
    }
  })
  const speeds = legs.map((l) => l.speedPxPerMs).filter((v): v is number => v !== null)
  const speedRatio = Math.max(...speeds) / Math.min(...speeds)
  const dark = darkRuns(probes, SENDER_BURST_FRAMES + 1, ROUTE_FRAMES)
  const paintedFrames = probes.filter((p) => p.frame > SENDER_BURST_FRAMES && p.frame <= ROUTE_FRAMES && painted(p) > 0).length
  const row = {
    scene: '100 nodes, 348 lines', legs, ratioOfLengths: Number(ratioOfLengths.toFixed(3)), speedRatioFastestToSlowest: Number(speedRatio.toFixed(3)),
    judgedWindowFrames: [SENDER_BURST_FRAMES + 1, ROUTE_FRAMES], paintedFramesInWindow: paintedFrames, darkFramesInWindow: dark.reduce((s, r) => s + r, 0), darkRunsFrames: dark, longestDarkRunFrames: Math.max(0, ...dark),
    chordClearPx: Number(g.chordClearPx.toFixed(1)), alphaMin: ALPHA_MIN, specBudgetPerLegMs: 450,
  }
  note('R6 six unequal legs', row)

  expect(legs.every((l) => l.lifeMs !== null), 'coverage: the product spawned a spark for every one of the six legs').toBe(true)
  expect(paintedFrames, 'coverage: the FX canvas was painted during the flight, after the burst of the sender').toBeGreaterThan(0)
  expect.soft(Math.max(0, ...dark), `longest run of frames with NOTHING painted on the FX canvas between the sender's burst and the end of the route (runs: ${JSON.stringify(dark)})`).toBeLessThanOrEqual(1)
})
