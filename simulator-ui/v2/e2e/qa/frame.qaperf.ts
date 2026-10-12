/**
 * 038 S3 - wall-clock DIAGNOSTICS on software-only Chromium (F-038-4). Project `qa-perf`, never `page.clock`.
 * A dev server (unminified modules, Vue dev build) unless QA_STAND_SERVER=preview (production build).
 *
 * Nothing here is a threshold on time: a frame time is a property of the machine the run happened on. The tests fail
 * only when the measurement itself is void - no product frames, the named load not pushed in full, labels not
 * animating (BACKLOG 034-6 (k): until S3 the stand paused them and the numbers described a lighter page).
 * The discrete, machine-independent budgets are in `budgets.qa.ts` (project `qa-stand`).
 *
 * Load: `S3-MIXED-V1` (`perf-load.ts`). Every window starts from a sleeping render loop and from slot 0, so the
 * windows at x1, x4 and x8 carry the same events. Output: `qa-perf-summary-<dev|preview>.json` in the output folder -
 * median, min..max, number of runs, machine, OS, Chromium, build, CPU throttling, noise of ten x4 windows.
 *
 * QA_PERF_CSS_PAUSED=1 reproduces the state before S3 (label animations paused at mount) for the before/after table.
 */
import { mkdirSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import { join } from 'node:path'

import { expect, test, type CDPSession, type Page } from '@playwright/test'

import { LOAD_FINGERPRINT } from './budgets.js'
import { buildPerfLoad } from './perf-load.js'
import { measureWindow, med, summarizeWindow, type RawWindow, type WindowSummary } from './perf-measure.js'
import { boot, fixtureSnapshot100, installCssClockSync, waitLoopAsleep } from './stand.js'

const RUNS = 3
const NOISE_WINDOWS = 10
const WINDOW_MS = 8_000
const HEAP_WINDOW_MS = 60_000
const devHook = process.env.QA_STAND_SERVER !== 'preview'
const cssPaused = process.env.QA_PERF_CSS_PAUSED === '1'
const build = devHook ? 'dev' : 'preview'
const snapshot = fixtureSnapshot100()
const load = buildPerfLoad(snapshot)

const bootPerf = (page: Page) =>
  boot(page, { snapshot, clock: false, devHook, beforeLoad: cssPaused ? installCssClockSync : undefined })

const measure = (page: Page, frames: string[], windowMs: number) => measureWindow(page, frames, windowMs, load.everyMs)

const slotsIn = (windowMs: number) => Math.floor((windowMs - 1) / load.everyMs) + 1

/** The shared refusals of a void window (`summarizeWindow`), plus this file's own: labels are there and they animate. */
function summarize(w: RawWindow, name: string, expectedEvents: number) {
  const s = summarizeWindow(w, name, expectedEvents)
  expect(w.maxLive.labels, `${name}: the load put amount labels on the screen`).toBeGreaterThan(0)
  // The old pause left a label invisible (opacity 0 of the first keyframe) and was partial: an animation restarted
  // after the mount ran on. In the "before" mode the number still running is recorded, not required to be zero.
  if (cssPaused) expect(w.labelAnims.paused, `${name}: QA_PERF_CSS_PAUSED=1 - the "before S3" state, label animations are paused at mount`).toBeGreaterThan(0)
  else {
    expect(w.labelAnims.running, `${name}: label CSS animations run by themselves under the measurement (BACKLOG 034-6 (k))`).toBeGreaterThan(0)
    expect(w.labelAnims.paused, `${name}: and none of them is paused`).toBe(0)
  }
  return s
}
type Summary = WindowSummary

let tagSeq = 0
/** One window of the load at a CPU throttling rate, started from a sleeping loop and from slot 0. */
async function throttled(page: Page, cdp: CDPSession, rate: number): Promise<Summary> {
  await waitLoopAsleep(page)
  await cdp.send('Emulation.setCPUThrottlingRate', { rate })
  const n = slotsIn(WINDOW_MS)
  const s = summarize(await measure(page, load.frames(0, n, `w${(tagSeq += 1)}`), WINDOW_MS), `CPU x${rate}`, n)
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: 1 })
  return s
}

type Run = { x1: Summary; x4: Summary; x8: Summary; heapGrowthMb: number | null; heapStartMb: number | null; maxLabels: number }
const runs: Run[] = []
let noise: Summary[] = []
let chromium = ''

test('the load is the named one, and the perf boot leaves the CSS animation of a label running', async ({ page, browser }) => {
  chromium = browser.version()
  // The same events on any machine: a changed generator or a changed fixture makes old numbers incomparable.
  expect(load.fingerprint, `the load ${load.name} is the one the recorded numbers were taken with; if it was changed on purpose, `
    + 'move LOAD_FINGERPRINT in e2e/qa/budgets.ts with a dated line and re-measure').toBe(LOAD_FINGERPRINT)
  const c = load.composition(slotsIn(WINDOW_MS))
  console.log(`[038 perf] load ${load.name} ${load.fingerprint}`, JSON.stringify(c))
  expect(c.clearings, 'a window contains clearings').toBeGreaterThanOrEqual(2)
  for (const k of ['1', '3', '6']) expect(c.byRouteEdges[k] ?? 0, `a window contains payments over ${k} edge(s)`).toBeGreaterThanOrEqual(10)

  await bootPerf(page)
  await page.evaluate((f) => (window as unknown as { __qaSse: { push: (t: string) => void } }).__qaSse.push(f), load.frames(0, 1, 'css')[0]!)
  const inner = page.locator('.floating-label-inner').first()
  await expect(inner).toBeAttached()
  const state = await inner.evaluate((el) => el.getAnimations().map((a) => a.playState))
  const opacity = await inner.evaluate((el) => getComputedStyle(el).opacity)
  console.log('[038 perf] label animation under the perf boot', JSON.stringify({ state, opacity }))
  expect(state, 'the label has exactly one CSS animation and it runs by itself; "paused" is the deterministic stand\'s clock sync leaking into the measurement')
    .toEqual([cssPaused ? 'paused' : 'running'])
})

for (let run = 1; run <= RUNS; run += 1) {
  test(`perf run ${run}/${RUNS}: frame time on 100 nodes at CPU x1, x4, x8 and heap growth over 60 s (${build})`, async ({ page }) => {
    await bootPerf(page)
    const cdp = await page.context().newCDPSession(page)
    const x1 = await throttled(page, cdp, 1)
    const x4 = await throttled(page, cdp, 4)
    const x8 = await throttled(page, cdp, 8)

    await waitLoopAsleep(page)
    const heap0 = await page.evaluate(() => {
      const w = window as unknown as { gc?: () => void }
      w.gc?.()
      const mem = (performance as unknown as { memory?: { usedJSHeapSize: number } }).memory
      return mem && w.gc ? mem.usedJSHeapSize : null
    })
    const n = slotsIn(HEAP_WINDOW_MS)
    const long = await measure(page, load.frames(0, n, `h${run}`), HEAP_WINDOW_MS)
    const mb = (b: number) => Math.round((b / 1048576) * 100) / 100
    expect(long.draws, 'heap window: the product drew while the heap was watched').toBeGreaterThan(0)
    expect(long.pushed, 'heap window: the whole load was pushed into the stream').toBe(n)
    const row: Run = {
      x1, x4, x8,
      heapStartMb: heap0 === null ? null : mb(heap0),
      heapGrowthMb: heap0 === null || long.heapUsed === null ? null : mb(long.heapUsed - heap0),
      maxLabels: long.maxLive.labels,
    }
    runs.push(row)
    console.log(`[038 perf] run ${run}`, JSON.stringify(row))
    test.info().annotations.push({ type: '038-perf-run', description: JSON.stringify(row) })
  })
}

test(`noise: ${NOISE_WINDOWS} windows at CPU x4 in one page (${build})`, async ({ page }) => {
  await bootPerf(page)
  const cdp = await page.context().newCDPSession(page)
  const got: Summary[] = []
  for (let i = 0; i < NOISE_WINDOWS; i += 1) got.push(await throttled(page, cdp, 4))
  noise = got
  console.log('[038 perf] noise x4', JSON.stringify(got.map((s) => ({ fps: s.fps, frameP95: s.frameMs.p95, workP95: s.workMs.p95 }))))
})

test.afterAll(async ({}, testInfo) => {
  if (runs.length === 0 && noise.length === 0) return
  const at = (k: 'x1' | 'x4' | 'x8') => ({
    fps: med(runs.map((r) => r[k].fps)), frameP95Ms: med(runs.map((r) => r[k].frameMs.p95)), frameP50Ms: med(runs.map((r) => r[k].frameMs.p50)),
    workP95Ms: med(runs.map((r) => r[k].workMs.p95)), workP50Ms: med(runs.map((r) => r[k].workMs.p50)),
    maxLiveSparks: med(runs.map((r) => r[k].maxLive.sparks)), maxLiveEdgePulses: med(runs.map((r) => r[k].maxLive.edgePulses)),
    maxLiveNodeBursts: med(runs.map((r) => r[k].maxLive.nodeBursts)), maxLiveLabels: med(runs.map((r) => r[k].maxLive.labels)),
    labelAnimsRunning: med(runs.map((r) => r[k].labelAnims.running)), minBudgetScale: med(runs.map((r) => r[k].minBudgetScale)),
  })
  const summary = {
    what: 'DIAGNOSTICS of one machine, not a norm: wall-clock numbers do not transfer to another machine or to a CI runner',
    build: devHook ? 'dev server' : 'production build (vite build + vite preview)',
    cssLabelAnimations: cssPaused ? 'PAUSED (QA_PERF_CSS_PAUSED=1, the state before S3)' : 'running',
    machine: { cpu: os.cpus()[0]?.model.trim(), logicalCores: os.cpus().length, memGb: Math.round(os.totalmem() / 2 ** 30), os: `${os.type()} ${os.release()} ${os.arch()}` },
    chromium, rendering: 'software only (--disable-gpu --disable-accelerated-2d-canvas --disable-gpu-compositing)',
    viewport: '1280x720, deviceScaleFactor 1', cpuThrottling: 'CDP Emulation.setCPUThrottlingRate 1, 4, 8 (renderer main thread only)',
    load: { name: load.name, fingerprint: load.fingerprint, everyMs: load.everyMs, windowMs: WINDOW_MS, perWindow: load.composition(slotsIn(WINDOW_MS)) },
    runs: runs.length, nodes: snapshot.nodes.length, links: snapshot.links.length,
    x1: at('x1'), x4: at('x4'), x8: at('x8'),
    heapGrowthMbOver60s: med(runs.map((r) => r.heapGrowthMb)), heapStartMb: med(runs.map((r) => r.heapStartMb)), maxLabelsOver60s: med(runs.map((r) => r.maxLabels)),
    noiseX4: noise.length === 0 ? null : {
      windows: noise.length, fps: med(noise.map((s) => s.fps)), frameP95Ms: med(noise.map((s) => s.frameMs.p95)), workP95Ms: med(noise.map((s) => s.workMs.p95)),
    },
  }
  console.log('[038 perf] SUMMARY (median of runs, min..max)', JSON.stringify(summary))
  mkdirSync(testInfo.project.outputDir, { recursive: true })
  writeFileSync(join(testInfo.project.outputDir, `qa-perf-summary-${build}${cssPaused ? '-css-paused' : ''}.json`), JSON.stringify({ summary, runs, noise }, null, 2))
})
