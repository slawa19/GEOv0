/**
 * 039 slice C - the motion contract of the payment beam and the clearing (spec 039: criterion 4, P-3, decision
 * "glow only on active nodes"), judged on the stand's ring and its 100-node scene. Events enter through the product's
 * own SSE reader, normalizer and pipeline; FX state is only read.
 *
 * Four questions, one test each (each red on the product before slice C - the red output is in the Changelog of 039):
 *  1. PACE (a 3-edge payment on the ring and a 6-edge payment on the open route, whose legs differ in length): every leg moves for at least 450 ms (criterion 4), at ONE speed along the
 *     route (visible length over time equal on every leg; the last one 1.5 x longer, eased into the receiver), the legs
 *     follow each other with no gap and no overlap, and the receiver's label comes when the beam arrives.
 *  2. HANDOFF (the same flights, pixels): the route is never dark between two legs for longer than the head needs to
 *     leave the node it passes - a bound DERIVED from geometry the stand fixes independently of what is painted
 *     (`NODE_CLEAR_PX`, the zone around a route node the oracle does not judge) and from the schedule of the leg in FX
 *     state, never from the frames in which the beam was seen (BACKLOG 039-3 (v), 039-4 (k)).
 *  3. READABLE WITHOUT A GPU (the same flights; the stand is the no-GPU profile, quality low): on every leg long enough
 *     to have a judged part, the beam is painted in most frames of its leg and with a body, not as a dot.
 *  4. CLEARING: one spark goes round the cycle in 1.2 s - SEEN on the FX canvas line after line, forward (Codex §15,
 *     2026-10-18: until then only its schedule was read) - then the lines pulse and the amount comes; nothing is painted
 *     away from the cycle (the full-screen flash is gone). A clearing of two cycles: a spark round each, seen likewise.
 *  5. GLOW ONLY ON ACTIVE NODES: a static frame of the 100-node scene draws no glow sprite on the base canvas, and the
 *     base canvas shows no halo around isolated idle nodes nor beside idle lines (median luminance over the background;
 *     Codex §15: a glow drawn without sprites); a selected node gets its glow and its halo is seen; without a GPU no
 *     glow is composited with `screen`, with a GPU believed present it is. LIMIT of the halo measure: it does not see a
 *     glow as faint as the old bloom of an idle node at quality low (median 0 on the base too) - the sprite count holds
 *     that; it sees a blurred stroke (median 5.7) and a glowing line (61). Further limits (fix-delta review 2026-10-18):
 *     dense and non-circle nodes are excluded from the sample, and it is a median over the sampled nodes (and two
 *     lines), so a glow on a minority of nodes, or on excluded nodes and lines, can pass; the threshold is a one-sided
 *     empirical +3 over the background, not a measured cross-platform bound. Run in `saas` and `hud`.
 *
 * What this does NOT see: colour and shape of the beam; the composed page (the oracle's property 5, `motion.qa.ts`);
 * what happens inside `NODE_CLEAR_PX` of a node (the head passes through it unjudged - that is the bound of 2);
 * quality med/high (`motion.qa.ts` runs the oracle there); the label's own fade (`label-fade.qa.ts`).
 */
import { expect, test, type Page } from '@playwright/test'

import { CORRIDOR_PX, NODE_CLEAR_PX, pickRingRoute, probeFrame, rememberBaseline, routeGeomOf, summarize, type FrameProbe, type RouteGeom } from './oracle.js'
import { OPEN_ROUTE, RING, boot, distToSegment, fixtureSnapshot100, frames, loopCounters, openRouteSnapshot, pretendGpu, pushEvent, ringSnapshot, sampleCanvas, screenNodes, txUpdated, type Pt } from './stand.js'

/** The floor of criterion 4 (spec 039): a leg moves for no less than this. The spec's number, not the product's constant. */
const LEG_FLOOR_MS = 450
/** The last leg slows into the receiver over this many times its time at the route's speed (P-3: "easing only on the last leg"). */
const ARRIVE_STRETCH = 1.5
/** Radius of a node of the ring on screen: `viz_size` 24 x 24 (`stand.ts`), drawn at a constant screen size. */
const RING_NODE_R = 12
/**
 * READABLE: a painted frame of a judged leg carries at least this many lit pixels (median over the leg). Twice the most
 * the head dot alone ever painted in the corridor without a GPU before slice C (26 px, measured 2026-10-18 on the
 * workbench, `p039-repro/c-motion.p039-repro.ts`): a dot cannot pass, a beam with a body does.
 */
const LOW_MIN_MEDIAN_PX = 52
/** ... and is painted in at least this share of the frames of its leg (before slice C: 0.40 median, a beam that faded inside its leg). */
const LOW_MIN_PAINTED_SHARE = 0.7

type Leg = { edge: string; startedAtMs: number; moveMs: number; ttlMs: number; motion: string | null }

async function liveSparks(page: Page, t0: number): Promise<Array<Leg & { alive: boolean }>> {
  return page.evaluate((t0) => {
    const now = performance.now()
    return (window as unknown as { __geoSim: { fxState: { sparks: Array<{ source: string; target: string; startedAtMs: number; ttlMs: number; moveMs?: number; motion?: string }> } } })
      .__geoSim.fxState.sparks.map((s) => ({
        edge: `${s.source}>${s.target}`, startedAtMs: s.startedAtMs - t0, moveMs: s.moveMs ?? s.ttlMs, ttlMs: s.ttlMs, motion: s.motion ?? null,
        alive: now - s.startedAtMs < s.ttlMs,
      }))
  }, t0)
}

const receiverLabel = (page: Page) => page.evaluate(() => [...document.querySelectorAll('.floating-label-inner')].some((el) => (el.textContent ?? '').trim().startsWith('+')))

/** One payment over `hops` edges of the ring, probed every frame (FX buffer only) until the beam is gone. */
async function fly(page: Page, route: number | readonly string[]) {
  const g = typeof route === 'number' ? await pickRingRoute(page, RING, route) : await routeGeomOf(page, route)
  await rememberBaseline(page, g)
  const t0 = await page.evaluate(() => performance.now())
  await pushEvent(page, txUpdated(g.ids, '10.00'))
  const legs = new Map<string, Leg>()
  const probes: FrameProbe[] = []
  let labelAtFrame: number | null = null
  let quiet = 0
  for (let f = 1; f <= 400 && quiet < 3; f += 1) {
    await frames(page, 1)
    probes.push(await probeFrame(page, g, f, false))
    const live = await liveSparks(page, t0)
    for (const s of live) if (!legs.has(`${s.edge}@${s.startedAtMs}`)) legs.set(`${s.edge}@${s.startedAtMs}`, s)
    if (labelAtFrame === null && (await receiverLabel(page))) labelAtFrame = f
    quiet = legs.size > 0 && !live.some((s) => s.alive) ? quiet + 1 : 0
  }
  return { g, legs: [...legs.values()].sort((a, b) => a.startedAtMs - b.startedAtMs), probes, labelAtFrame }
}

/** Visible length of a route edge on screen: centre to centre less the two node radii (the link terminations). */
const visibleLen = (g: RouteGeom, i: number) => Math.hypot(g.edges[i]!.b.x - g.edges[i]!.a.x, g.edges[i]!.b.y - g.edges[i]!.a.y) - 2 * RING_NODE_R

function judge(hops: number, r: Awaited<ReturnType<typeof fly>>, o: { unequal?: boolean; readableLegs: number }) {
  const { g, legs, probes, labelAtFrame } = r
  // 1. PACE.
  expect(legs.map((l) => l.edge), `PACE: one leg per edge, in route order (${g.ids.join(' > ')})`).toEqual(g.edges.map((e) => e.name))
  for (const [i, l] of legs.entries()) {
    expect.soft(l.moveMs, `PACE: leg ${l.edge} moves for at least ${LEG_FLOOR_MS} ms (criterion 4 of spec 039)`).toBeGreaterThanOrEqual(LEG_FLOOR_MS)
    if (i > 0) expect.soft(l.startedAtMs, `PACE: leg ${l.edge} starts when leg ${legs[i - 1]!.edge} has arrived - no pause, no overlap`).toBeCloseTo(legs[i - 1]!.startedAtMs + legs[i - 1]!.moveMs, 0)
    expect.soft(l.motion, `PACE: easing only on the last leg (${l.edge})`).toBe(i === legs.length - 1 ? 'arrive' : 'linear')
  }
  const lens = legs.map((_, i) => visibleLen(g, i))
  const speeds = legs.map((l, i) => (lens[i]! * (i === legs.length - 1 ? ARRIVE_STRETCH : 1)) / l.moveMs)
  if (o.unequal) {
    // Anti-vacuum (adversarial pass 2026-10-18): on legs of equal length "equal time a leg" passes as "one speed".
    expect(Math.max(...lens) / Math.min(...lens), `counter-check: the legs of this route differ in visible length (${lens.map(Math.round).join(', ')} px)`).toBeGreaterThan(1.15)
  }
  for (const [i, v] of speeds.entries()) {
    expect.soft(Math.abs(v / speeds[0]! - 1), `PACE: one speed along the route - leg ${legs[i]!.edge} at ${v.toFixed(4)} px/ms, the first at ${speeds[0]!.toFixed(4)}`).toBeLessThan(0.02)
  }
  const last = legs[legs.length - 1]!
  const arrivalFrame = Math.ceil((last.startedAtMs + last.moveMs) / 16)
  // A closed route (the whole ring: sender = receiver) has no receiver's "+" label - the product shows none (`realEventPipeline.ts`).
  if (g.ids[0] !== g.ids[g.ids.length - 1]) {
    expect.soft(labelAtFrame, `PACE: the receiver's label comes when the beam arrives (frame ${arrivalFrame})`).toBeGreaterThanOrEqual(arrivalFrame)
    expect.soft(labelAtFrame ?? Infinity).toBeLessThanOrEqual(arrivalFrame + 2)
  }

  // 2. HANDOFF: dark frames between two legs, bounded by the time the head takes to cross the unjudged zone.
  const sum = summarize(g, probes)
  const handoffs = sum.slice(1).map((cur, i) => {
    const prev = sum[i]!
    const leg = legs[i + 1]!
    // Screen px per frame of the head when the leg begins: the route's speed (the eased last leg starts at it too).
    const pxPerFrame = speeds[i + 1]! * 16
    const allowed = Math.ceil(NODE_CLEAR_PX / pxPerFrame) + 1
    const dark = prev.lastLit === null || cur.firstLit === null ? null : cur.firstLit - prev.lastLit - 1
    return { from: prev.name, to: cur.name, dark, allowed, pxPerFrame: Number(pxPerFrame.toFixed(2)), legMs: Math.round(leg.moveMs) }
  })
  for (const h of handoffs) {
    const what = `HANDOFF ${h.from} -> ${h.to}: the route is dark for ${String(h.dark)} frames between the two legs; allowed ${h.allowed} = ceil(NODE_CLEAR_PX ${NODE_CLEAR_PX} px / head speed ${h.pxPerFrame} px a frame) + 1 frame of grain`
    expect.soft(h.dark, `${what} (counter-check: both legs were painted)`).not.toBeNull()
    expect.soft(h.dark ?? Infinity, what).toBeLessThanOrEqual(h.allowed)
  }

  // 3. READABLE WITHOUT A GPU, on the legs that have a judged part (the oracle's own precondition, `pickRingRoute`).
  // Only the legs with a judged part (the oracle's own precondition in `pickRingRoute`: > 2 x NODE_CLEAR_PX + 60).
  const readable = sum.flatMap((e, i) => (lens[i]! + 2 * RING_NODE_R > 2 * NODE_CLEAR_PX + 60 ? [i] : [])).map((i) => {
    const e = sum[i]!
    const leg = legs[i]!
    const first = Math.floor(leg.startedAtMs / 16) + 1
    const lastF = Math.ceil((leg.startedAtMs + leg.moveMs) / 16)
    const inLeg = probes.filter((p) => p.frame >= first && p.frame <= lastF)
    const px = inLeg.map((p) => p.edges[e.name]!.px).sort((a, b) => a - b)
    return { edge: e.name, frames: inLeg.length, painted: inLeg.filter((p) => p.edges[e.name]!.px > 0).length, medianPx: px[Math.floor(px.length / 2)] ?? 0 }
  })
  // Anti-vacuum (Codex §15, 2026-10-18): the legs READABLE judges are counted - a layout of short legs would judge none.
  expect(readable.length, `READABLE: legs with a judged part (> ${2 * NODE_CLEAR_PX + 60} px between centres) on this route`).toBe(o.readableLegs)
  for (const r of readable) {
    expect.soft(r.painted / Math.max(1, r.frames), `READABLE: the beam on ${r.edge} is painted in ${r.painted} of the ${r.frames} frames of its leg`).toBeGreaterThanOrEqual(LOW_MIN_PAINTED_SHARE)
    expect.soft(r.medianPx, `READABLE: the beam on ${r.edge} paints a median of ${r.medianPx} px a frame in its corridor (${CORRIDOR_PX} px of the axis, away from the nodes); a head dot alone paints up to 26`).toBeGreaterThanOrEqual(LOW_MIN_MEDIAN_PX)
  }
  const row = { hops, legs: legs.map((l) => [l.edge, Math.round(l.moveMs), l.motion]), speeds: speeds.map((v) => v.toFixed(4)), handoffs, readable, labelAtFrame, arrivalFrame }
  console.log(`[039 C] ${hops} edge(s)`, JSON.stringify(row))
  test.info().annotations.push({ type: `039-c-${hops}`, description: JSON.stringify(row) })
}

test.describe('039 slice C - motion on the ring (no GPU, quality low)', () => {
  test.beforeEach(async ({ page }) => {
    await boot(page, { snapshot: ringSnapshot(), clock: true })
    expect(await page.locator('.root').evaluate((el) => (el as HTMLElement).dataset.quality), 'precondition: the no-GPU profile, quality low').toBe('low')
  })

  test('a 3-edge payment: legs of at least 450 ms at one speed, a visible handoff, a beam readable without a GPU', async ({ page }) => {
    judge(3, await fly(page, 3), { readableLegs: 3 })
  })

  test('a clearing: one spark goes round the cycle in 1.2 s - seen on the FX canvas, edge after edge, forward - then the lines pulse and the amount comes; nothing is painted away from the cycle', async ({ page }) => {
    const cycle = RING.map((id, i) => ({ from: id, to: RING[(i + 1) % RING.length]! }))
    const nodes = await screenNodes(page)
    // Points well inside the ring, away from every line and node of it: where the old full-screen flash was painted.
    const c = [...nodes.values()].reduce((s, p) => ({ x: s.x + p.x / nodes.size, y: s.y + p.y / nodes.size }), { x: 0, y: 0 })
    const away = [c, { x: c.x - 25, y: c.y }, { x: c.x + 25, y: c.y }, { x: c.x, y: c.y - 25 }, { x: c.x, y: c.y + 25 }]
    const g = await routeGeomOf(page, [...RING, RING[0]!])
    const t0 = await page.evaluate(() => performance.now())
    await pushEvent(page, { type: 'clearing.done', plan_id: 'plan-p039c-1', cleared_cycles: 1, cleared_amount: '7.00', cycle_edges: cycle })
    const seen: string[] = []
    let maxAlive = 0
    let loopMs = 0
    let pulsesBefore = 0
    let labelBefore = false
    let pulsesAfter = 0
    let labelAfter = false
    let awayLit = 0
    const loopProbes: FrameProbe[] = []
    for (let f = 1; f <= 100; f += 1) {
      await frames(page, 1)
      const ms = f * 16
      // The FX canvas along the cycle while the spark goes round, before the pulses (which light whole lines) begin.
      if (ms < 1200) loopProbes.push(await probeFrame(page, g, f, false))
      const live = (await liveSparks(page, t0)).filter((s) => s.alive)
      maxAlive = Math.max(maxAlive, live.length)
      for (const s of live) if (seen[seen.length - 1] !== s.edge) { seen.push(s.edge); loopMs += s.moveMs }
      const st = await page.evaluate(() => ({
        pulses: (window as unknown as { __geoSim: { fxState: { edgePulses: unknown[] } } }).__geoSim.fxState.edgePulses.length,
        label: document.querySelectorAll('.floating-label-inner.clearing-premium').length > 0,
      }))
      if (ms < 1200 - 16) { pulsesBefore = Math.max(pulsesBefore, st.pulses); labelBefore ||= st.label }
      if (ms >= 1200 + 32) { pulsesAfter = Math.max(pulsesAfter, st.pulses); labelAfter ||= st.label }
      awayLit = Math.max(awayLit, (await sampleCanvas(page, { away }, { halfPx: 2, alphaMin: 12 })).away!.lit)
    }
    const traversal = judgeLoopPixels(g, loopProbes, 'the ring')
    // Anti-vacuum (fix-delta review 2026-10-18): every line of the ring is long enough to be judged.
    expect(traversal.judged, 'counter-check: lines of the ring judged by pixels').toBe(6)
    const row = { seen, maxAlive, loopMs: Math.round(loopMs), pulsesBefore, labelBefore, pulsesAfter, labelAfter, awayLit, traversal }
    console.log('[039 C] clearing', JSON.stringify(row))
    expect.soft(seen, 'one spark goes round the whole cycle, line after line').toEqual(cycle.map((e) => `${e.from}>${e.to}`))
    expect.soft(maxAlive, 'one spark at a time').toBe(1)
    expect.soft(Math.abs(loopMs - 1200), 'round the cycle in 1200 ms').toBeLessThan(2)
    expect.soft({ pulsesBefore, labelBefore }, 'while the spark goes round, no line pulses and no amount is shown yet').toEqual({ pulsesBefore: 0, labelBefore: false })
    expect.soft({ pulses: pulsesAfter, label: labelAfter }, 'then every line of the cycle pulses and the amount comes').toEqual({ pulses: cycle.length, label: true })
    expect.soft(awayLit, 'nothing is painted away from the cycle (until slice C a full-screen flash covered the canvas)').toBe(0)
  })
})

/**
 * Codex §15 (2026-10-18): the clearing must be SEEN going round, not only scheduled. For the frames of the loop (before
 * the pulses), the FX canvas along the cycle (the oracle's corridor, away from the nodes): every judged line is painted,
 * the lines light up in the cycle's order (first painted frame strictly later than the line before), and on each line
 * the brightest point moves forward from its source to its target and never back. Anti-vacuum: the number of judged
 * lines is asserted by the caller. What it does not see: the 46 px around a node; colour.
 */
function judgeLoopPixels(g: RouteGeom, probes: FrameProbe[], name: string, only?: readonly string[]) {
  // `only`: the lines of ONE cycle, in walking order, when `g` holds the lines of several (their pixels are sorted to
  // the nearest line of all of them, and where two lines meet the pixel goes to neither - `probeFrame`).
  const all = summarize(g, probes)
  const len = (n: string) => { const e = g.edges.find((x) => x.name === n)!; return Math.hypot(e.b.x - e.a.x, e.b.y - e.a.y) }
  const sum = only ? only.map((n) => all.find((e) => e.name === n)!) : all
  const judged = sum.filter((e) => len(e.name) > 2 * NODE_CLEAR_PX + 30)
  // The spark goes round in CLEARING_LOOP_MS at one speed over the VISIBLE lengths (centre to centre less the two
  // radii of the 24 px nodes of these scenes), so a line takes loop x its share, and the head advances 16 ms / that
  // time of the line per frame. MAX_STEP_K of that is allowed in one frame (the frame grid and the brightest-pixel
  // reading); a head that holds and then jumps (fix-delta review 2026-10-18: "teleporting") steps further.
  const vis = (n: string) => len(n) - 2 * RING_NODE_R
  const loopLen = sum.reduce((acc, e) => acc + vis(e.name), 0)
  for (const e of judged) {
    const lineMs = (CLEARING_LOOP_MS * vis(e.name)) / loopLen
    const maxStep = (MAX_STEP_K * 16) / lineMs
    expect.soft(e.litFrames.length, `CLEARING ${name}: the spark is painted on ${e.name} while it goes round (painted in ${e.litFrames.length} frames)`).toBeGreaterThanOrEqual(2)
    const moved = e.headFirst === null || e.headLast === null ? 0 : e.headLast - e.headFirst
    expect.soft(moved, `CLEARING ${name}: on ${e.name} the spark moves forward, source to target (head ${String(e.headFirst?.toFixed(3))} -> ${String(e.headLast?.toFixed(3))})`).toBeGreaterThanOrEqual(0.3)
    expect.soft(e.maxStepBack, `CLEARING ${name}: on ${e.name} the spark never steps back`).toBeLessThanOrEqual(0.02)
    expect.soft(e.distinctHeads, `CLEARING ${name}: on ${e.name} the spark is seen at several places, not two (${e.distinctHeads} places)`).toBeGreaterThanOrEqual(MIN_PLACES)
    expect.soft(e.maxStepPerFrame, `CLEARING ${name}: on ${e.name} the spark advances at most ${maxStep.toFixed(3)} of the line a frame (${MAX_STEP_K} x 16 ms / ${Math.round(lineMs)} ms of the line), largest step ${e.maxStepPerFrame.toFixed(3)}`).toBeLessThanOrEqual(maxStep)
  }
  // In the cycle's order, starting wherever the spark started (a cycle has no first line: the product starts at the line
  // the reconstruction met first).
  const first = judged.map((e) => e.firstLit ?? Infinity)
  const k0 = first.indexOf(Math.min(...first))
  const order = judged.map((_, i) => judged[(k0 + i) % judged.length]!)
  for (let i = 1; i < order.length; i += 1) {
    expect.soft(order[i]!.firstLit ?? -1, `CLEARING ${name}: ${order[i]!.name} lights up after ${order[i - 1]!.name} (first painted frames ${order.map((e) => `${e.name} ${String(e.firstLit)}`).join(', ')})`).toBeGreaterThan(order[i - 1]!.firstLit ?? Infinity)
  }
  return { judged: judged.length, of: sum.length, lines: judged.map((e) => ({ edge: e.name, lit: [e.firstLit, e.lastLit], head: [e.headFirst?.toFixed(2), e.headLast?.toFixed(2)], places: e.distinctHeads, maxStep: Number(e.maxStepPerFrame.toFixed(3)) })) }
}

/** The clearing loop of the product (`CLEARING_LOOP_MS`, `useRealClearingFx.ts`): the spec's 1.2 s, written here independently. */
const CLEARING_LOOP_MS = 1200
/**
 * A head step may be this many times the planned advance of one frame (see `judgeLoopPixels`). Measured 2026-10-18 on
 * the product: up to 3.3 times where a line passes near another line of the cycle (pixels there go to neither line, and
 * the brightest-pixel reading jumps across the gap): f > a of the ring 0.260 against 0.078, n2 > n4 0.154 against 0.040.
 * LIMIT: with 4 the bound on a ring line is 0.31 of it a frame - a single jump of 0.3 alone would pass it; what refuses
 * a head that holds and jumps is `MIN_PLACES` (two or three places, not a traversal).
 */
const MAX_STEP_K = 4
/** Distinct head places on a judged line: a traversal is seen moving, a jump between two places is not one. */
const MIN_PLACES = 4

// Counter-check with TWO cycles (Codex §15): each is traversed by its own spark at the same time. The open route's
// scene has two edge-disjoint cycles over its lines: n2 > n3 > n6 > n0 > n1 > n2 and n5 > n2 > n4 > n5.
test('039 slice C - a clearing of two cycles: a spark goes round EACH, seen on the FX canvas in order and forward', async ({ page }) => {
  await boot(page, { snapshot: openRouteSnapshot(), clock: true })
  const c1 = ['n2', 'n3', 'n6', 'n0', 'n1', 'n2']
  const c2 = ['n5', 'n2', 'n4', 'n5']
  const edges = [c1, c2].flatMap((c) => c.slice(0, -1).map((id, i) => ({ from: id, to: c[i + 1]! })))
  // As the backend sends it: the set of lines, sorted (`tick.py`, `_done_cycle_edges`) - not in walking order.
  const sorted = [...edges].sort((x, y) => `${x.from}>${x.to}`.localeCompare(`${y.from}>${y.to}`))
  // ONE geometry of all eight lines: a pixel goes to the nearest line of either cycle, and where lines meet to neither.
  const at = await screenNodes(page)
  const g: RouteGeom = {
    ids: [...new Set([...c1, ...c2])],
    edges: edges.map((e) => ({ name: `${e.from}>${e.to}`, from: e.from, to: e.to, a: at.get(e.from)!, b: at.get(e.to)! })),
    nodes: [...new Set([...c1, ...c2])].map((id) => at.get(id)!),
    occluders: [],
    clip: { x: 0, y: 0, width: 1280, height: 720 },
  }
  const names = (c: string[]) => c.slice(0, -1).map((id, i) => `${id}>${c[i + 1]!}`)
  await pushEvent(page, { type: 'clearing.done', plan_id: 'plan-p039c-2', cleared_cycles: 2, cleared_amount: '9.00', cycle_edges: sorted })
  const probes: FrameProbe[] = []
  let maxAlive = 0
  for (let f = 1; f * 16 < 1200; f += 1) {
    await frames(page, 1)
    probes.push(await probeFrame(page, g, f, false))
    maxAlive = Math.max(maxAlive, (await liveSparks(page, 0)).filter((s) => s.alive).length)
  }
  const t1 = judgeLoopPixels(g, probes, 'cycle 1', names(c1))
  const t2 = judgeLoopPixels(g, probes, 'cycle 2', names(c2))
  console.log('[039 C] clearing, two cycles', JSON.stringify({ maxAlive, t1, t2 }))
  expect(maxAlive, 'two sparks at once: one per cycle').toBe(2)
  // Anti-vacuum: the judged lines of each cycle (long enough to have a part outside the nodes' 46 px).
  // Exactly as recorded (fix-delta review 2026-10-18: "at least" was weaker than the record): all lines of both cycles.
  expect.soft(t1.judged, `counter-check: lines of cycle 1 judged (${t1.judged} of ${t1.of})`).toBe(5)
  expect.soft(t2.judged, `counter-check: lines of cycle 2 judged (${t2.judged} of ${t2.of})`).toBe(3)
})

// Six legs (the cap) on legs of UNEQUAL length: the open route of seven nodes (`OPEN_ROUTE`, 039 slice A). On the
// ring the legs are near equal, and "one speed" could not be told from "equal time a leg" (adversarial pass 2026-10-18).
test('039 slice C - a 6-edge payment on legs of unequal length: at least 450 ms a leg at ONE speed, a visible handoff, a beam readable without a GPU', async ({ page }) => {
  await boot(page, { snapshot: openRouteSnapshot(), clock: true })
  expect(await page.locator('.root').evaluate((el) => (el as HTMLElement).dataset.quality), 'precondition: the no-GPU profile, quality low').toBe('low')
  // All six legs of the open route are long enough (measured 2026-10-18); a layout that shortens one is a red here, not a silent pass.
  judge(6, await fly(page, OPEN_ROUTE), { unequal: true, readableLegs: 6 })
})

/** Base-canvas `drawImage` calls (the glow sprites) of the next pass and the composite each was drawn with. */
async function spritesOfOnePass(page: Page): Promise<{ sprites: number; composites: Record<string, number> }> {
  await page.evaluate(() => {
    const w = window as unknown as { __p039cSprites?: { on: boolean; n: number; comp: Record<string, number> } }
    if (!w.__p039cSprites) {
      const S = { on: false, n: 0, comp: {} as Record<string, number> }
      w.__p039cSprites = S
      const proto = CanvasRenderingContext2D.prototype as unknown as { drawImage: (...a: unknown[]) => unknown }
      const real = proto.drawImage
      proto.drawImage = function (this: CanvasRenderingContext2D, ...a: unknown[]) {
        const cl = (this.canvas as HTMLCanvasElement).classList
        if (S.on && cl?.contains('canvas') && !cl.contains('canvas-fx')) {
          S.n += 1
          S.comp[this.globalCompositeOperation] = (S.comp[this.globalCompositeOperation] ?? 0) + 1
        }
        return real.apply(this, a)
      }
    }
    const S = w.__p039cSprites
    S.n = 0
    S.comp = {}
    S.on = true
  })
  // Wake the loop with a pointer move on the base canvas (the product's own wake-up path) and let one pass run.
  const d0 = (await loopCounters(page)).draws
  await page.evaluate(() => {
    const c = document.querySelector<HTMLCanvasElement>('canvas.canvas:not(.canvas-fx)')!
    c.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, clientX: 4, clientY: 4, pointerType: 'mouse' }))
  })
  await frames(page, 1)
  const out = await page.evaluate(() => {
    const S = (window as unknown as { __p039cSprites: { on: boolean; n: number; comp: Record<string, number> } }).__p039cSprites
    S.on = false
    return { sprites: S.n, composites: { ...S.comp } }
  })
  const passes = (await loopCounters(page)).draws - d0
  expect(passes, 'counter-check: exactly one pass of the render loop was counted').toBe(1)
  return out
}

/** Click the centre of the first node that the base canvas owns there; the selected node shows its name label. Returns its id. */
async function selectOneNode(page: Page): Promise<string> {
  const iso = new Set((await haloSamples(page)).nodes.map((n) => n.id))
  const all = [...(await screenNodes(page)).entries()]
  // An isolated circle first: its halo is then measured without a neighbour's.
  for (const [id, at] of [...all.filter(([id]) => iso.has(id)), ...all]) {
    const onCanvas = await page.evaluate(({ x, y }) => { const el = document.elementFromPoint(x, y); return el instanceof HTMLCanvasElement && !el.classList.contains('canvas-fx') }, at)
    if (!onCanvas) continue
    await page.mouse.click(at.x, at.y)
    await frames(page, 2)
    if ((await page.locator('.node-label-inner').count()) > 0) return id
  }
  throw new Error('no node could be selected by a click on its centre')
}

/**
 * Luminance (0-255) over the background a ring or a band may show and still count as "no glow": the anti-aliased edge
 * of a 2 px rim stroke and of a crossing line reach into it at single samples, the median does not move. Measured
 * 2026-10-18: idle after slice C and before it - see the Changelog; the active node's ring at least HALO_ACTIVE_MIN.
 */
const HALO_TOLERANCE = 3
const HALO_ACTIVE_MIN = 10

/**
 * The halo seen on the BASE canvas (Codex §15, 2026-10-18: counting sprites does not see a glow drawn another way - a
 * blurred stroke, a gradient, a fill). For circle nodes with no other node within 16 px of their outline: the median
 * luminance of the base canvas in a ring 5 to 10 px outside the outline, minus the canvas background (`#020617`);
 * and the same for bands 4 to 8 px beside the middle third of idle trust lines away from every node. A median, so a line
 * crossing the ring does not count as a halo. Returns the medians over the sampled nodes and lines.
 */
async function haloLuminance(page: Page, nodes: Array<{ x: number; y: number; r: number }>, lines: Array<{ a: Pt; b: Pt }>) {
  return page.evaluate(({ nodes, lines }) => {
    const c = document.querySelector<HTMLCanvasElement>('canvas.canvas:not(.canvas-fx)')!
    const ctx = c.getContext('2d')!
    const k = c.width / Math.max(1, c.clientWidth)
    const box = c.getBoundingClientRect()
    const img = ctx.getImageData(0, 0, c.width, c.height)
    const lum = (x: number, y: number) => {
      const px = Math.round((x - box.x) * k)
      const py = Math.round((y - box.y) * k)
      if (px < 0 || py < 0 || px >= c.width || py >= c.height) return null
      const i = (py * c.width + px) * 4
      return 0.2126 * img.data[i]! + 0.7152 * img.data[i + 1]! + 0.0722 * img.data[i + 2]!
    }
    const BG = 0.2126 * 2 + 0.7152 * 6 + 0.0722 * 23
    const med = (xs: number[]) => { const v = [...xs].sort((p, q) => p - q); return v.length ? v[Math.floor(v.length / 2)]! : NaN }
    const nodeMeds = nodes.map((n) => {
      const vals: number[] = []
      for (let d = 5; d <= 10; d += 1) for (let a = 0; a < 360; a += 6) {
        const v = lum(n.x + Math.cos((a * Math.PI) / 180) * (n.r + d), n.y + Math.sin((a * Math.PI) / 180) * (n.r + d))
        if (v !== null) vals.push(v - BG)
      }
      return med(vals)
    })
    const lineMeds = lines.map((l) => {
      const dx = l.b.x - l.a.x
      const dy = l.b.y - l.a.y
      const len = Math.hypot(dx, dy)
      const ux = dx / len
      const uy = dy / len
      const vals: number[] = []
      for (let t = 0.34; t <= 0.66; t += 0.02) for (const off of [4, 5, 6, 7, 8, -4, -5, -6, -7, -8]) {
        const v = lum(l.a.x + dx * t - uy * off, l.a.y + dy * t + ux * off)
        if (v !== null) vals.push(v - BG)
      }
      return med(vals)
    })
    return { nodes: med(nodeMeds), lines: med(lineMeds), perNode: nodeMeds.map((v) => Math.round(v * 10) / 10), sampled: { nodes: nodes.length, lines: lines.length } }
  }, { nodes, lines })
}

/** Isolated circle nodes of the 100-node scene and idle trust lines clear of every node, in page px. */
async function haloSamples(page: Page) {
  const snap = fixtureSnapshot100()
  const at = await screenNodes(page)
  const size = new Map(snap.nodes.map((n) => [n.id, n as { id: string; viz_size?: { w: number; h: number }; viz_shape_key?: string }]))
  const radius = (id: string) => { const n = size.get(id)!; return Math.max(n.viz_size?.w ?? 12, n.viz_size?.h ?? 12) / 2 }
  const circles = [...at.entries()].filter(([id]) => (size.get(id)?.viz_shape_key ?? 'circle') === 'circle')
  const nodes = circles
    .filter(([id, p]) => [...at.entries()].every(([o, q]) => o === id || Math.hypot(q.x - p.x, q.y - p.y) > radius(id) + radius(o) + 16))
    .filter(([, p]) => p.x > 60 && p.x < 1220 && p.y > 80 && p.y < 640)
    .map(([id, p]) => ({ id, x: p.x, y: p.y, r: radius(id) }))
  // Lines whose middle third, and the 8 px bands beside it, keep 6 px clear of every node's outline.
  const lines = snap.links
    .map((l) => ({ a: at.get(l.source)!, b: at.get(l.target)! }))
    .filter((l) => {
      const mid = { x: (l.a.x + l.b.x) / 2, y: (l.a.y + l.b.y) / 2 }
      const p0 = { x: l.a.x + (l.b.x - l.a.x) * 0.34, y: l.a.y + (l.b.y - l.a.y) * 0.34 }
      const p1 = { x: l.a.x + (l.b.x - l.a.x) * 0.66, y: l.a.y + (l.b.y - l.a.y) * 0.66 }
      return Math.hypot(l.b.x - l.a.x, l.b.y - l.a.y) > 90 && [...at.entries()].every(([id, q]) => distToSegment(q, p0, p1) > radius(id) + 14) && mid.y > 80 && mid.y < 640
    })
  return { nodes, lines }
}

test.describe('039 slice C - glow only on active nodes (the 100-node scene)', () => {
  // Both themes (fix-delta review 2026-10-18): the graph canvas is filled with `#020617` in either (`useRenderLoop.ts`),
  // so the background and the tolerance are the same.
  for (const theme of ['saas', 'hud'] as const) test(`[${theme}] a static frame draws no glow sprite on the base canvas; a selected node gets its glow, composited without \`screen\` when there is no GPU`, async ({ page }) => {
    await boot(page, { snapshot: fixtureSnapshot100(), clock: true, query: `&theme=${theme}` })
    expect(await page.locator('.root').getAttribute('data-theme'), 'precondition: the theme').toBe(theme)
    expect(await page.locator('.root').getAttribute('data-gpu'), 'precondition: no GPU').toBe('0')
    const idle = await spritesOfOnePass(page)
    const samples = await haloSamples(page)
    const idleHalo = await haloLuminance(page, samples.nodes, samples.lines)
    await page.clock.runFor(16)
    const picked = await selectOneNode(page)
    const selected = await spritesOfOnePass(page)
    const at = (await screenNodes(page)).get(picked)!
    const selectedHalo = await haloLuminance(page, [{ x: at.x, y: at.y, r: samples.nodes.find((n) => n.id === picked)?.r ?? 12 }], [])
    console.log('[039 C] glow', JSON.stringify({ idle, selected, idleHalo, selectedHalo: selectedHalo.nodes }))
    // The halo as SEEN (not only as counted): idle nodes and idle lines are at the background within HALO_TOLERANCE;
    // the counter-check - the selected node's ring is brighter by at least HALO_ACTIVE_MIN.
    expect(idleHalo.sampled.nodes, 'counter-check: isolated idle circle nodes were sampled').toBeGreaterThanOrEqual(5)
    expect(idleHalo.sampled.lines, 'counter-check: idle lines clear of the nodes were sampled (the scene is dense: two qualify)').toBeGreaterThanOrEqual(2)
    expect.soft(idleHalo.nodes, `no halo around an idle node: the median luminance 5-10 px outside its outline over the background (per node ${idleHalo.perNode.join(', ')})`).toBeLessThanOrEqual(HALO_TOLERANCE)
    expect.soft(idleHalo.lines, 'no glow beside an idle line: the median luminance 4-8 px off its axis over the background').toBeLessThanOrEqual(HALO_TOLERANCE)
    expect.soft(selectedHalo.nodes, 'counter-check: the selected node has a halo the measure sees').toBeGreaterThanOrEqual(HALO_ACTIVE_MIN)
    expect(idle.sprites, 'no glow sprite for 100 idle nodes in a pass (until slice C: two per node, about 195 a pass)').toBe(0)
    expect(selected.sprites, 'counter-check: the selected node glows - its selection glow, bloom and rim').toBeGreaterThanOrEqual(2)
    expect(selected.sprites, 'and only it').toBeLessThanOrEqual(6)
    expect(selected.composites.screen ?? 0, 'without a GPU no glow is composited with `screen`').toBe(0)
  })

  test('counter-check: with a GPU believed present the selected node glows with `screen`', async ({ page }) => {
    await boot(page, { snapshot: fixtureSnapshot100(), clock: true, beforeLoad: pretendGpu })
    expect(await page.locator('.root').getAttribute('data-gpu')).toBe('1')
    await selectOneNode(page)
    const selected = await spritesOfOnePass(page)
    expect(selected.composites.screen ?? 0, 'the glow sprites of the selected node are composited with `screen`').toBeGreaterThanOrEqual(2)
  })
})
