/**
 * 038 S3 - the DISCRETE performance budgets (`budgets.ts`), a two-sided ratchet (AGENTS.md, section 6).
 * Every test here runs on `page.clock`: the numbers are counts on a fake clock and do not depend on the speed of the
 * machine. No wall-clock measurement is taken in this file (those are `frame.qaperf.ts` and are diagnostics).
 *
 * "Low FPS" is forced with `page.clock.fastForward(100)`: one animation frame per 100 ms of the product's clock, which
 * is what the product's own meters see on a slow machine (`updateFxBudgetScale` reads the rAF timestamps).
 *
 * MEMBERSHIP (2026-10-12, 039 slice A, R14): this file is `*.qa.ts`, a member of the counted project `qa-stand`, and
 * carries no `@extended` tag - the required gate runs all six tests (`run-stand.mjs`, `EXPECTED`). It used to be
 * `budgets.qaperf.ts`, collected only by `qa-perf`, which runs on a schedule. Nothing here may read a wall clock.
 *
 * On a production build there is no `window.__geoSim`: the budgets read from FX state are SKIPPED there with the
 * reason printed - not measured, which is not the same as held. Draw counts and label counts are measured on both.
 * A production build is run by `qa-perf` only (`playwright.qa.config.ts`); in the gate, on the dev server, nothing
 * skips - and a skip there is a refusal (`run-verdict.mjs`).
 *
 * What these budgets do not see: how long a frame takes; what is painted (the motion oracle of `motion.qa.ts` does);
 * a second render loop that draws on another canvas; work done outside `requestAnimationFrame`.
 */
import { expect, test, type Page } from '@playwright/test'

import { BUDGETS, type Budget } from './budgets.js'
import { buildPerfLoad } from './perf-load.js'
import { EQ, RUN_ID, boot, fixtureSnapshot100, loopCounters, pausedClockTime, pretendGpu, pushFrames, stepToFrameBoundary, type QaSnapshot } from './stand.js'

const devHook = process.env.QA_STAND_SERVER !== 'preview'
const NEEDS_HOOK = 'reads FX state through window.__geoSim, which exists on the dev server only: on a production build this budget is NOT measured'
const snapshot = fixtureSnapshot100()
const load = buildPerfLoad(snapshot)

/** Exact equality in both directions: worse is a regression, better must be recorded - or the budget drifts above reality. */
function hold(actual: number, b: Budget) {
  expect(actual, `budget ${b.name} (${b.what}) is ${b.value}, recorded ${b.since}. `
    + (b.worseIs === 'higher' ? 'Higher is a regression' : b.worseIs === 'lower' ? 'Lower is a regression' : 'Any other value is a change of behaviour')
    + ' - find it, do not move the number. If the product got better on purpose, record it: move the constant in e2e/qa/budgets.ts with a dated line and the reason.',
  ).toBe(b.value)
}

/** Boot on the fake clock, paused, with the next animation frame a whole 16 ms away: draw counts do not depend on when the boot ended. */
async function bootClocked(page: Page, snap: QaSnapshot, beforeLoad?: (page: Page) => Promise<void>) {
  await boot(page, { snapshot: snap, clock: true, devHook, beforeLoad })
  if (!devHook) await page.clock.pauseAt(pausedClockTime((await page.evaluate(() => Date.now())) + 1000))
  await page.clock.runFor(3600) // IDLE_DELAY_MS 3000 + hold + one idle period: the loop is in deep sleep
  await stepToFrameBoundary(page) // the one phase mechanism of the stand (`stand.ts`); these counts hold at any `CLOCK_PHASE_MS`
}

// Awaited (2026-10-12, 039 slice A): a count taken after frames that the product has not read yet is a count of
// something else. `pushFrames` returns when the product took every frame, and throws when `taken !== pushed`.
const push = pushFrames

let seq = 0
function sse(evt: Record<string, unknown> & { type: string }): string {
  const id = `evt_${RUN_ID}_b_${String((seq += 1)).padStart(6, '0')}`
  return `id: ${id}\nevent: ${evt.type}\ndata: ${JSON.stringify({ event_id: id, ts: '2026-02-01T00:00:01.000Z', equivalent: EQ, ...evt })}\n\n`
}
const payment = (from: string, to: string, extra: Record<string, unknown> = {}) =>
  sse({ type: 'tx.updated', from, to, amount: '10.00', amount_flyout: false, ttl_ms: 1200, edges: [{ from, to }], ...extra })
function clearing(n: number): string {
  const edges = Array.from({ length: 30 }, (_, k) => {
    const l = snapshot.links[(n * 31 + k * 11) % snapshot.links.length]!
    return { from: l.source, to: l.target }
  })
  return sse({ type: 'clearing.done', plan_id: `plan-b-${n}-${seq}`, cleared_cycles: 1, cleared_amount: '5.00', cycle_edges: edges })
}

type FxRead = { sparks: number; edgePulses: number; nodeBursts: number; live: number; scale: number | null; maxParticles: number | null; labels: number }
const readFx = (page: Page): Promise<FxRead> =>
  page.evaluate(() => {
    const fx = (window as unknown as { __geoSim?: { fxState: { sparks: unknown[]; edgePulses: unknown[]; nodeBursts: unknown[]; __fxBudgetScale?: number; __maxParticles?: number } } }).__geoSim?.fxState
    const labels = document.querySelectorAll('.floating-label').length
    if (!fx) return { sparks: -1, edgePulses: -1, nodeBursts: -1, live: -1, scale: null, maxParticles: null, labels }
    return {
      sparks: fx.sparks.length, edgePulses: fx.edgePulses.length, nodeBursts: fx.nodeBursts.length,
      live: fx.sparks.length + fx.edgePulses.length + fx.nodeBursts.length,
      scale: fx.__fxBudgetScale ?? null, maxParticles: fx.__maxParticles ?? null, labels,
    }
  })

/** Mounted amount labels: how many, how many carry a computed CSS filter or text-shadow, how many are the clearing label. */
const labelFilters = (page: Page) => page.evaluate(() => {
  const all = [...document.querySelectorAll('.floating-label-inner')]
  // `filter` has a 120 ms CSS transition, which runs on real time whatever `page.clock` says: a read right after a
  // change of the root's attributes would see the value in flight. Transitions are finished; the keyframe animation stays.
  for (const el of all) for (const a of el.getAnimations()) if (a instanceof CSSTransition) a.finish()
  const has = (el: Element) => getComputedStyle(el).filter !== 'none'
  const clearing = all.filter((el) => el.classList.contains('clearing-premium'))
  return { labels: all.length, withFilter: all.filter(has).length, clearing: clearing.length, clearingWithFilter: clearing.filter(has).length,
    withTextShadow: all.filter((el) => getComputedStyle(el).textShadow !== 'none').length, glow: all.filter((el) => el.classList.contains('is-glow')).length }
})

test('budget: in deep sleep nothing is drawn and no frame is requested; an event is drawn within 48 ms; one payment costs a fixed number of draws', async ({ page }) => {
  await bootClocked(page, snapshot)
  const a = await loopCounters(page)
  await page.clock.runFor(2000)
  const b = await loopCounters(page)
  const l = snapshot.links[0]!
  await push(page, [payment(l.source, l.target)])
  await page.clock.runFor(48)
  const c = await loopCounters(page)
  // The whole life of the effect and the way back to sleep: the flight (039 slice C: one leg, at most a few hundred ms,
  // and a 360 ms fade - until then 1200), receiver burst 420, hold 250, idle 3000, slack.
  await page.clock.runFor(1200 + 420 + 250 + 500)
  // Idle cadence now (4 fps, nothing but the base graph on screen): the next 250 ms hold exactly one pass.
  const i0 = await loopCounters(page)
  await page.clock.runFor(250)
  const i1 = await loopCounters(page)
  await page.clock.runFor(2500 + 1000 - 250)
  const d = await loopCounters(page)
  await page.clock.runFor(2000)
  const e = await loopCounters(page)

  const row = {
    drawsAsleep: b.draws - a.draws, rafAsleep: b.calls - a.calls, drawsIn48msAfterEvent: c.draws - b.draws,
    drawsForOnePayment: d.draws - b.draws, drawsAsleepAgain: e.draws - d.draws, rafAsleepAgain: e.calls - d.calls,
    idlePasses: i1.draws - i0.draws, commandsInAStaticFrame: i1.cmds - i0.cmds, commandsForOnePayment: d.cmds - b.cmds, commandsAsleep: b.cmds - a.cmds,
  }
  console.log('[038 budget] sleep', JSON.stringify(row))
  hold(row.drawsAsleep, BUDGETS.DRAWS_IN_2S_DEEP_SLEEP)
  hold(row.rafAsleep, BUDGETS.RAF_CALLS_IN_2S_DEEP_SLEEP)
  hold(row.drawsIn48msAfterEvent, BUDGETS.DRAWS_IN_48MS_AFTER_EVENT)
  hold(row.drawsForOnePayment, BUDGETS.DRAWS_FOR_ONE_PAYMENT_FROM_SLEEP)
  hold(row.drawsAsleepAgain, BUDGETS.DRAWS_IN_2S_DEEP_SLEEP)
  hold(row.rafAsleepAgain, BUDGETS.RAF_CALLS_IN_2S_DEEP_SLEEP)
  // The volume of drawing: a count of passes does not see a pass that draws twice as much.
  expect(row.commandsAsleep, 'nothing is drawn on the canvases in deep sleep').toBe(0)
  expect(row.idlePasses, 'precondition: exactly one pass of the loop in the measured idle period').toBe(1)
  hold(row.commandsInAStaticFrame, BUDGETS.DRAW_COMMANDS_IN_A_STATIC_FRAME)
  hold(row.commandsForOnePayment, BUDGETS.DRAW_COMMANDS_FOR_ONE_PAYMENT_FROM_SLEEP)
})

/** One period of S3-MIXED-V1 (24 slots, 3.6 s: 23 payments over 1, 3, 6 edges and one clearing), sampled after every slot. */
async function onePeriod(page: Page, tag: string) {
  const before = await loopCounters(page)
  const peak = { sparks: 0, edgePulses: 0, nodeBursts: 0, labels: 0, labelsWithFilter: 0, clearingLabels: 0, glowLabels: 0 }
  for (const f of load.frames(0, 24, tag)) {
    await push(page, [f])
    await page.clock.runFor(load.everyMs)
    const r = await readFx(page)
    for (const k of ['sparks', 'edgePulses', 'nodeBursts', 'labels'] as const) peak[k] = Math.max(peak[k], r[k])
    const css = await labelFilters(page)
    peak.labelsWithFilter = Math.max(peak.labelsWithFilter, css.withFilter)
    peak.clearingLabels = Math.max(peak.clearingLabels, css.clearing)
    peak.glowLabels = Math.max(peak.glowLabels, css.glow)
  }
  return { before, after: await loopCounters(page), peak }
}

// The budgets read from FX state are a test of their own, so that on a production build (no dev hook) they are
// reported as SKIPPED with the reason by the runner, not passed over inside a green test.
test('budget: one period of the named load reaches the recorded peaks of sparks, edge pulses and node bursts, and leaves no FX object alive', async ({ page }) => {
  test.skip(!devHook, NEEDS_HOOK)
  await bootClocked(page, snapshot)
  const { peak } = await onePeriod(page, 'budget-fx')
  await page.clock.runFor(12_000)
  const drained = await readFx(page)
  console.log('[038 budget] FX state over one period', JSON.stringify({ peak, drained }))
  hold(peak.sparks, BUDGETS.PEAK_SPARKS_IN_ONE_PERIOD)
  hold(peak.edgePulses, BUDGETS.PEAK_EDGE_PULSES_IN_ONE_PERIOD)
  hold(peak.nodeBursts, BUDGETS.PEAK_NODE_BURSTS_IN_ONE_PERIOD)
  hold(drained.live, BUDGETS.LIVE_FX_AFTER_THE_STREAM)
})

test('budget: one period of the named load is one pass per frame of a recorded drawing volume, with the recorded peak of labels, no blur effects, and nothing left', async ({ page }) => {
  // On-screen `shadowBlur` and `backdrop-filter` are the two effects the no-GPU policy turns off; both are counted.
  await bootClocked(page, snapshot, (p) => p.addInitScript(() => {
    const w = window as unknown as { __qaShadow: { onScreen: number; offScreen: number } }
    w.__qaShadow = { onScreen: 0, offScreen: 0 }
    const d = Object.getOwnPropertyDescriptor(CanvasRenderingContext2D.prototype, 'shadowBlur')!
    Object.defineProperty(CanvasRenderingContext2D.prototype, 'shadowBlur', {
      configurable: true, enumerable: d.enumerable, get: d.get,
      set(this: CanvasRenderingContext2D, v: number) {
        if (v > 0) {
          if ((this.canvas as HTMLCanvasElement).classList?.contains('canvas')) w.__qaShadow.onScreen += 1
          else w.__qaShadow.offScreen += 1
        }
        d.set!.call(this, v)
      },
    })
  }))
  const quality = await page.evaluate(() => {
    const r = document.querySelector<HTMLElement>('.root')!
    return { quality: r.dataset.quality, gpu: r.dataset.gpu }
  })
  expect(quality, 'precondition: the no-GPU profile (the autoguard put software rendering on low)').toEqual({ quality: 'low', gpu: '0' })

  const { before, after, peak } = await onePeriod(page, 'budget')
  const backdrop = () => page.evaluate(() => [...document.querySelectorAll('*')].filter((el) => getComputedStyle(el).backdropFilter !== 'none').length)
  const backdropUnderLoad = await backdrop()
  const shadow = await page.evaluate(() => ({ ...(window as unknown as { __qaShadow: { onScreen: number; offScreen: number } }).__qaShadow }))

  await page.clock.runFor(12_000) // the longest effect is the clearing: pulses 4200 ms, active edges 5200 ms; then hold and idle
  const drained = await readFx(page)
  const d0 = await loopCounters(page)
  await page.clock.runFor(2000)
  const d1 = await loopCounters(page)

  // The same period again on a warm page: glow sprites are baked off screen with shadowBlur once and cached
  // (`render/glowSprites.ts`); a cache that misses bakes them again on every frame, which the first period cannot show.
  await page.evaluate(() => { (window as unknown as { __qaShadow: { offScreen: number } }).__qaShadow.offScreen = 0 })
  const warm = await onePeriod(page, 'budget-warm')
  const offScreenWarm = await page.evaluate(() => (window as unknown as { __qaShadow: { offScreen: number } }).__qaShadow.offScreen)

  const row = {
    drawsInOnePeriod: after.draws - before.draws, commandsInOnePeriod: after.cmds - before.cmds, peak, backdropUnderLoad, shadow, drained,
    drawsAsleepAfter: d1.draws - d0.draws, warm: { draws: warm.after.draws - warm.before.draws, commands: warm.after.cmds - warm.before.cmds, offScreenShadowBlurSets: offScreenWarm },
  }
  console.log('[038 budget] one period of the load', JSON.stringify(row))
  hold(row.commandsInOnePeriod, BUDGETS.DRAW_COMMANDS_IN_ONE_PERIOD_OF_LOAD)
  expect(row.warm.commands, 'the same period on a warm page draws the same volume').toBe(row.commandsInOnePeriod)
  expect(shadow.offScreen, 'counter-check: the cold period did bake sprites off screen - the counter sees them').toBeGreaterThan(0)
  hold(offScreenWarm, BUDGETS.OFFSCREEN_SHADOW_BLUR_SETS_IN_A_WARM_PERIOD)

  hold(row.drawsInOnePeriod, BUDGETS.DRAWS_IN_ONE_PERIOD_OF_LOAD)
  hold(peak.labels, BUDGETS.PEAK_LABELS_IN_ONE_PERIOD)
  hold(drained.labels, BUDGETS.LIVE_LABELS_AFTER_THE_STREAM)
  hold(row.drawsAsleepAfter, BUDGETS.DRAWS_IN_2S_DEEP_SLEEP)
  expect(peak.clearingLabels, 'the clearing amount label was among the labels counted').toBeGreaterThanOrEqual(1)
  hold(peak.labelsWithFilter, BUDGETS.PEAK_LABELS_WITH_CSS_FILTER_NO_GPU)
  hold(shadow.onScreen, BUDGETS.ONSCREEN_SHADOW_BLUR_SETS_NO_GPU)
  hold(backdropUnderLoad, BUDGETS.ELEMENTS_WITH_BACKDROP_FILTER_NO_GPU)

  // Counter-checks: both probes see what they count.
  const probes = await page.evaluate(() => {
    const ctx = document.querySelector<HTMLCanvasElement>('canvas.canvas')!.getContext('2d')!
    ctx.shadowBlur = 1
    ctx.shadowBlur = 0
    const el = document.createElement('div')
    el.style.backdropFilter = 'blur(2px)'
    document.body.appendChild(el)
    const n = [...document.querySelectorAll('*')].filter((e) => getComputedStyle(e).backdropFilter !== 'none').length
    el.remove()
    return { shadowOnScreen: (window as unknown as { __qaShadow: { onScreen: number } }).__qaShadow.onScreen, backdrop: n }
  })
  expect(probes, 'the shadowBlur counter and the backdrop-filter count each see one planted case').toEqual({ shadowOnScreen: shadow.onScreen + 1, backdrop: backdropUnderLoad + 1 })
})

test('budget: at a sustained 10 fps the FX budget falls, the particle cap follows and holds; at 60 fps both come back', async ({ page }) => {
  test.skip(!devHook, NEEDS_HOOK)
  await bootClocked(page, snapshot)

  // 60 fps, FX on screen: five clearings 300 ms apart put more live objects on the scene than the low-FPS cap allows.
  // 039 slice C (2026-10-18, P-4): it was three clearings 480 ms apart. A clearing's lines now pulse for 1.6 s (+30 %
  // afterglow) after its spark went round, not 4.2 s from the start, and its nodes no longer burst all at once - so the
  // same three left 90 objects alive, under the cap of 135, and the cap had nothing to cut. The load is made denser to
  // keep the DEMAND above the cap: what this test judges is the cap, not the size of one clearing's effect.
  let n = 0
  for (let i = 0; i < 5; i += 1) {
    await push(page, [clearing((n += 1))])
    await page.clock.runFor(300)
  }
  const fast = await readFx(page)

  // 10 fps for 8 s: one frame per 100 ms; a clearing every 400 ms keeps the demand above the cap (039 slice C: every
  // second until then - see the warm-up above for why).
  const slow: FxRead[] = []
  for (let f = 0; f < 80; f += 1) {
    if (f % 4 === 0) await push(page, [clearing((n += 1))])
    await page.clock.fastForward(100)
    slow.push(await readFx(page))
  }
  const last = slow[slow.length - 1]!
  const overCap = slow.filter((r) => r.live > r.maxParticles!).length
  const atCap = slow.filter((r) => r.live === r.maxParticles).length

  // Back at 60 fps for 4 s with FX still on screen.
  for (let i = 0; i < 4; i += 1) {
    await push(page, [clearing((n += 1))])
    await page.clock.runFor(1000)
  }
  const recovered = await readFx(page)

  const row = {
    fast: { scale: fast.scale, maxParticles: fast.maxParticles, live: fast.live },
    slow: { scale: last.scale, maxParticles: last.maxParticles, live: last.live, framesOverCap: overCap, framesAtCap: atCap, peakLive: Math.max(...slow.map((r) => r.live)) },
    recovered: { scale: recovered.scale, maxParticles: recovered.maxParticles },
  }
  console.log('[038 budget] forced low fps', JSON.stringify(row))

  hold(fast.maxParticles!, BUDGETS.MAX_PARTICLES_AT_60_FPS)
  expect(fast.scale, 'at 60 fps the FX budget is not scaled down').toBe(1)
  expect(fast.live, 'precondition: more live FX objects at 60 fps than the low-FPS cap allows - otherwise the cap has nothing to cut')
    .toBeGreaterThan(BUDGETS.MAX_PARTICLES_AT_SUSTAINED_10_FPS.value)
  expect(last.scale!, 'the FX budget went down under a low frame rate').toBeLessThan(1)
  hold(Math.round(last.scale! * 1000), BUDGETS.FX_BUDGET_SCALE_X1000_AT_SUSTAINED_10_FPS)
  hold(last.maxParticles!, BUDGETS.MAX_PARTICLES_AT_SUSTAINED_10_FPS)
  hold(overCap, BUDGETS.FRAMES_WITH_LIVE_FX_ABOVE_THE_CAP)
  expect(atCap, 'counter-check: the cap was binding - live FX objects sat exactly at it in some frame, the demand was really cut').toBeGreaterThan(0)
  // The scale approaches 1 from below and never reaches it exactly, so the cap comes back to 299 or 300: not a budget.
  expect(recovered.scale!, 'and the budget comes back when the frame rate does').toBeGreaterThan(0.99)
  expect(recovered.maxParticles!, 'with the particle cap').toBeGreaterThanOrEqual(BUDGETS.MAX_PARTICLES_AT_60_FPS.value - 3)
})

test('budget: a flood of amount labels stops at the cap', async ({ page }) => {
  await bootClocked(page, snapshot)
  // Two waves of payments from every participant to a neighbour, 600 ms apart: 200 sender labels and the receivers'
  // (340 ms later, the lower TTL clamp) inside the 1900 ms a label lives - well above the cap of 120.
  const adj = new Map<string, string>()
  for (const l of snapshot.links) {
    if (!adj.has(l.source)) adj.set(l.source, l.target)
    if (!adj.has(l.target)) adj.set(l.target, l.source)
  }
  const wave = () => [...adj].map(([from, to]) => payment(from, to, { amount_flyout: true, ttl_ms: 340 }))
  expect(adj.size, 'precondition: enough participants with a trust line to ask for more labels than the cap').toBeGreaterThan(BUDGETS.LIVE_LABELS_CAP.value / 2)

  let peak = 0
  const sample = async (ms: number) => {
    for (let t = 0; t < ms; t += 48) {
      await page.clock.runFor(48)
      peak = Math.max(peak, (await readFx(page)).labels)
    }
  }
  // Control: 40 payments ask for at most 80 labels - under the cap, and all of them are shown.
  await push(page, wave().slice(0, 40))
  await sample(600)
  const underCap = peak
  await page.clock.runFor(4000)
  peak = 0
  await push(page, wave())
  await sample(600)
  await push(page, wave())
  await sample(960)
  await page.clock.runFor(8000)
  const left = (await readFx(page)).labels
  console.log('[038 budget] labels', JSON.stringify({ participants: adj.size, underCap, peak, left }))
  expect(underCap, 'counter-check: a demand under the cap is shown in full - at least the 40 sender labels').toBeGreaterThanOrEqual(40)
  expect(underCap, 'and stays under the cap: the cap below is reached by demand, not by a constant').toBeLessThan(BUDGETS.LIVE_LABELS_CAP.value)
  hold(peak, BUDGETS.LIVE_LABELS_CAP)
  hold(left, BUDGETS.LIVE_LABELS_AFTER_THE_STREAM)
})

// The other side of PEAK_LABELS_WITH_CSS_FILTER_NO_GPU: the gate is "no GPU", not "always" and not "quality low".
// Without this test a rule that removed the filter everywhere would hold the budget at 0 just as well.
test('counter-check: with a GPU believed present every amount label keeps its drop-shadow filter, whatever the quality; without one only the filter goes', async ({ page }) => {
  await bootClocked(page, snapshot, pretendGpu)
  const root = () => page.evaluate(() => {
    const r = document.querySelector<HTMLElement>('.root')!
    return { gpu: r.dataset.gpu, quality: r.dataset.quality }
  })
  expect((await root()).gpu, 'precondition: the product believes a GPU is there').toBe('1')

  const seen = { labels: 0, withFilter: 0, clearing: 0, clearingWithFilter: 0, withTextShadow: 0, glow: 0 }
  const watch = async () => {
    for (const f of load.frames(8, 6, 'gpu')) { // slots 8..13: five payments and the clearing of slot 12
      await push(page, [f])
      await page.clock.runFor(load.everyMs)
      const c = await labelFilters(page)
      expect(c.withFilter, 'every mounted label carries the filter').toBe(c.labels)
      for (const k of Object.keys(seen) as Array<keyof typeof seen>) seen[k] = Math.max(seen[k], c[k])
    }
  }
  await watch()
  const first = await root()
  // The same at quality low: the gate must not be the quality (a machine with a GPU on low keeps the shadow).
  // On the stand the FPS guard may already have put the quality on low by itself; either way it is low from here on.
  await page.evaluate(() => document.querySelector<HTMLElement>('.root')!.setAttribute('data-quality', 'low'))
  const atLow = await labelFilters(page)
  console.log('[038 budget] labels with a GPU believed present', JSON.stringify({ first, seen, atLow }))
  expect(seen.labels, 'payment labels were mounted').toBeGreaterThanOrEqual(5)
  expect(seen.clearing, 'and the clearing label').toBeGreaterThanOrEqual(1)
  expect(seen.clearingWithFilter, 'the clearing label keeps its filter with a GPU').toBe(seen.clearing)
  expect(atLow.labels, 'labels are still mounted when the quality attribute is low').toBeGreaterThan(0)
  expect(atLow.withFilter, 'data-quality=low alone does not remove the filter').toBe(atLow.labels)

  // And the gate itself, on the same labels: only the attribute changes, and only the filter goes - the glow stays.
  await page.evaluate(() => document.querySelector<HTMLElement>('.root')!.setAttribute('data-gpu', '0'))
  const noGpu = await labelFilters(page)
  expect(noGpu.labels).toBe(atLow.labels)
  expect(noGpu.withFilter, 'data-gpu=0 removes the filter from every label').toBe(0)
  expect(noGpu.withTextShadow, 'and leaves the text-shadow glow on every label').toBe(noGpu.labels)
})
