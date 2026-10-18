/**
 * 039 slice C - the motion contract of the payment beam and the clearing (spec 039: criterion 4, P-3, decision
 * "glow only on active nodes"), judged on the stand's ring and its 100-node scene. Events enter through the product's
 * own SSE reader, normalizer and pipeline; FX state is only read.
 *
 * Four questions, one test each (each red on the product before slice C - the red output is in the Changelog of 039):
 *  1. PACE (a 3-edge and a 6-edge payment): every leg moves for at least 450 ms (criterion 4), at ONE speed along the
 *     route (visible length over time equal on every leg; the last one 1.5 x longer, eased into the receiver), the legs
 *     follow each other with no gap and no overlap, and the receiver's label comes when the beam arrives.
 *  2. HANDOFF (the same flights, pixels): the route is never dark between two legs for longer than the head needs to
 *     leave the node it passes - a bound DERIVED from geometry the stand fixes independently of what is painted
 *     (`NODE_CLEAR_PX`, the zone around a route node the oracle does not judge) and from the schedule of the leg in FX
 *     state, never from the frames in which the beam was seen (BACKLOG 039-3 (v), 039-4 (k)).
 *  3. READABLE WITHOUT A GPU (the same flights; the stand is the no-GPU profile, quality low): on every leg long enough
 *     to have a judged part, the beam is painted in most frames of its leg and with a body, not as a dot.
 *  4. CLEARING: one spark goes round the cycle in 1.2 s, then the lines pulse and the amount comes; nothing is painted
 *     away from the cycle (the full-screen flash is gone).
 *  5. GLOW ONLY ON ACTIVE NODES: a static frame of the 100-node scene draws no glow sprite on the base canvas; a selected
 *     node gets its glow; without a GPU no glow is composited with `screen`, with a GPU believed present it is.
 *
 * What this does NOT see: colour and shape of the beam; the composed page (the oracle's property 5, `motion.qa.ts`);
 * what happens inside `NODE_CLEAR_PX` of a node (the head passes through it unjudged - that is the bound of 2);
 * quality med/high (`motion.qa.ts` runs the oracle there); the label's own fade (`label-fade.qa.ts`).
 */
import { expect, test, type Page } from '@playwright/test'

import { CORRIDOR_PX, NODE_CLEAR_PX, pickRingRoute, probeFrame, rememberBaseline, summarize, type FrameProbe, type RouteGeom } from './oracle.js'
import { RING, boot, fixtureSnapshot100, frames, loopCounters, pretendGpu, pushEvent, ringSnapshot, sampleCanvas, screenNodes, txUpdated } from './stand.js'

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
async function fly(page: Page, hops: number) {
  const g = await pickRingRoute(page, RING, hops)
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

function judge(hops: number, r: Awaited<ReturnType<typeof fly>>) {
  const { g, legs, probes, labelAtFrame } = r
  // 1. PACE.
  expect(legs.map((l) => l.edge), `PACE: one leg per edge, in route order (${g.ids.join(' > ')})`).toEqual(g.edges.map((e) => e.name))
  for (const [i, l] of legs.entries()) {
    expect.soft(l.moveMs, `PACE: leg ${l.edge} moves for at least ${LEG_FLOOR_MS} ms (criterion 4 of spec 039)`).toBeGreaterThanOrEqual(LEG_FLOOR_MS)
    if (i > 0) expect.soft(l.startedAtMs, `PACE: leg ${l.edge} starts when leg ${legs[i - 1]!.edge} has arrived - no pause, no overlap`).toBeCloseTo(legs[i - 1]!.startedAtMs + legs[i - 1]!.moveMs, 0)
    expect.soft(l.motion, `PACE: easing only on the last leg (${l.edge})`).toBe(i === legs.length - 1 ? 'arrive' : 'linear')
  }
  const speeds = legs.map((l, i) => (visibleLen(g, i) * (i === legs.length - 1 ? ARRIVE_STRETCH : 1)) / l.moveMs)
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
  const readable = sum.map((e, i) => {
    const leg = legs[i]!
    const first = Math.floor(leg.startedAtMs / 16) + 1
    const lastF = Math.ceil((leg.startedAtMs + leg.moveMs) / 16)
    const inLeg = probes.filter((p) => p.frame >= first && p.frame <= lastF)
    const px = inLeg.map((p) => p.edges[e.name]!.px).sort((a, b) => a - b)
    return { edge: e.name, frames: inLeg.length, painted: inLeg.filter((p) => p.edges[e.name]!.px > 0).length, medianPx: px[Math.floor(px.length / 2)] ?? 0 }
  })
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

  for (const hops of [3, 6]) {
    test(`a ${hops}-edge payment: legs of at least 450 ms at one speed, a visible handoff, a beam readable without a GPU`, async ({ page }) => {
      judge(hops, await fly(page, hops))
    })
  }

  test('a clearing: one spark goes round the cycle in 1.2 s, then the lines pulse and the amount comes; nothing is painted away from the cycle', async ({ page }) => {
    const cycle = RING.map((id, i) => ({ from: id, to: RING[(i + 1) % RING.length]! }))
    const nodes = await screenNodes(page)
    // Points well inside the ring, away from every line and node of it: where the old full-screen flash was painted.
    const c = [...nodes.values()].reduce((s, p) => ({ x: s.x + p.x / nodes.size, y: s.y + p.y / nodes.size }), { x: 0, y: 0 })
    const away = [c, { x: c.x - 25, y: c.y }, { x: c.x + 25, y: c.y }, { x: c.x, y: c.y - 25 }, { x: c.x, y: c.y + 25 }]
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
    for (let f = 1; f <= 100; f += 1) {
      await frames(page, 1)
      const live = (await liveSparks(page, t0)).filter((s) => s.alive)
      maxAlive = Math.max(maxAlive, live.length)
      for (const s of live) if (seen[seen.length - 1] !== s.edge) { seen.push(s.edge); loopMs += s.moveMs }
      const st = await page.evaluate(() => ({
        pulses: (window as unknown as { __geoSim: { fxState: { edgePulses: unknown[] } } }).__geoSim.fxState.edgePulses.length,
        label: document.querySelectorAll('.floating-label-inner.clearing-premium').length > 0,
      }))
      const ms = f * 16
      if (ms < 1200 - 16) { pulsesBefore = Math.max(pulsesBefore, st.pulses); labelBefore ||= st.label }
      if (ms >= 1200 + 32) { pulsesAfter = Math.max(pulsesAfter, st.pulses); labelAfter ||= st.label }
      awayLit = Math.max(awayLit, (await sampleCanvas(page, { away }, { halfPx: 2, alphaMin: 12 })).away!.lit)
    }
    const row = { seen, maxAlive, loopMs: Math.round(loopMs), pulsesBefore, labelBefore, pulsesAfter, labelAfter, awayLit }
    console.log('[039 C] clearing', JSON.stringify(row))
    expect.soft(seen, 'one spark goes round the whole cycle, line after line').toEqual(cycle.map((e) => `${e.from}>${e.to}`))
    expect.soft(maxAlive, 'one spark at a time').toBe(1)
    expect.soft(Math.abs(loopMs - 1200), 'round the cycle in 1200 ms').toBeLessThan(2)
    expect.soft({ pulsesBefore, labelBefore }, 'while the spark goes round, no line pulses and no amount is shown yet').toEqual({ pulsesBefore: 0, labelBefore: false })
    expect.soft({ pulses: pulsesAfter, label: labelAfter }, 'then every line of the cycle pulses and the amount comes').toEqual({ pulses: cycle.length, label: true })
    expect.soft(awayLit, 'nothing is painted away from the cycle (until slice C a full-screen flash covered the canvas)').toBe(0)
  })
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

/** Click the centre of the first node that the base canvas owns there; the selected node shows its name label. */
async function selectOneNode(page: Page) {
  for (const [, at] of await screenNodes(page)) {
    const onCanvas = await page.evaluate(({ x, y }) => { const el = document.elementFromPoint(x, y); return el instanceof HTMLCanvasElement && !el.classList.contains('canvas-fx') }, at)
    if (!onCanvas) continue
    await page.mouse.click(at.x, at.y)
    await frames(page, 2)
    if ((await page.locator('.node-label-inner').count()) > 0) return
  }
  throw new Error('no node could be selected by a click on its centre')
}

test.describe('039 slice C - glow only on active nodes (the 100-node scene)', () => {
  test('a static frame draws no glow sprite on the base canvas; a selected node gets its glow, composited without `screen` when there is no GPU', async ({ page }) => {
    await boot(page, { snapshot: fixtureSnapshot100(), clock: true })
    expect(await page.locator('.root').getAttribute('data-gpu'), 'precondition: no GPU').toBe('0')
    const idle = await spritesOfOnePass(page)
    await page.clock.runFor(16)
    await selectOneNode(page)
    const selected = await spritesOfOnePass(page)
    console.log('[039 C] glow', JSON.stringify({ idle, selected }))
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
