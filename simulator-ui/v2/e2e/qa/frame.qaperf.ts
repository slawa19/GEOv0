/**
 * 038 S0 - wall-clock measurements on software-only Chromium (F-038-4). Project `qa-perf`, never `page.clock`.
 * Three runs; the summary is the median with the spread. NOTHING here is a threshold: the numbers go to the spec and
 * become ratchet constants in S3. A dev server (unminified modules, Vue dev build) unless QA_STAND_SERVER=preview.
 *
 * Load is the mocked backend's SSE stream only. The product's own limiter decides how many sparks live at once
 * (token bucket 3/s, burst 2, 120 ms gap, at most 2 edges per event: `useRealTxFx.ts:19-33`); the count actually
 * reached is recorded, "20 sparks at once" is not reachable by events and is not faked by writing into FX state.
 */
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import { expect, test, type CDPSession, type Page } from '@playwright/test'

import { EQ, RUN_ID, boot, fixtureSnapshot100 } from './stand.js'

const RUNS = 3
const WINDOW_MS = 8_000
const HEAP_WINDOW_MS = 60_000
const EVERY_MS = 150
const devHook = process.env.QA_STAND_SERVER !== 'preview'
const snapshot = fixtureSnapshot100()

let seq = 0
/** SSE frames: a 2-edge payment every 150 ms and a 20-edge clearing every 3.6 s, over real trust lines of the fixture. */
function load(durationMs: number): string[] {
  const frames: string[] = []
  const links = snapshot.links
  const n = Math.ceil(durationMs / EVERY_MS)
  for (let i = 0; i < n; i += 1) {
    seq += 1
    const id = `evt_${RUN_ID}_${String(seq).padStart(7, '0')}`
    const pick = (k: number) => links[(seq * 7 + k * 13) % links.length]!
    const base = { event_id: id, ts: '2026-02-01T00:00:01.000Z', equivalent: EQ }
    const evt = i % 24 === 12
      ? { ...base, type: 'clearing.done', plan_id: `plan-${seq}`, cleared_cycles: 1, cleared_amount: '5.00',
          cycle_edges: Array.from({ length: 20 }, (_, k) => ({ from: pick(k).source, to: pick(k).target })) }
      : { ...base, type: 'tx.updated', from: pick(0).source, to: pick(1).target, amount: '10.00', amount_flyout: true, ttl_ms: 1200,
          edges: [{ from: pick(0).source, to: pick(0).target }, { from: pick(1).source, to: pick(1).target }] }
    frames.push(`id: ${id}\nevent: ${evt.type}\ndata: ${JSON.stringify(evt)}\n\n`)
  }
  return frames
}

type Live = { sparks: number | null; edgePulses: number | null; nodeBursts: number | null; labels: number }
type Window = {
  drawAt: number[]; frameT: number[]; callbackMs: number[]; draws: number; heapUsed: number | null
  maxLive: Live
  telemetry: { lastFps?: number; fxBudgetScale?: number; maxParticles?: number; renderQuality?: string } | null
  minBudgetScale: number | null
}

/** Pump the frames at a fixed cadence for `windowMs` of wall time and record what the page can measure about itself. */
async function measure(page: Page, frames: string[], windowMs: number): Promise<Window> {
  return page.evaluate(async ({ frames, windowMs, everyMs }) => {
    type Fx = { sparks: unknown[]; edgePulses: unknown[]; nodeBursts: unknown[]; __lastFps?: number; __fxBudgetScale?: number; __maxParticles?: number; __renderQuality?: string }
    const w = window as unknown as {
      __qaRaf: { draws: number; drawAt: number[]; callbackMs: number[]; frameT: number[]; record: boolean }
      __qaSse: { push: (t: string) => void }
      __geoSim?: { fxState: Fx }
      gc?: () => void
    }
    const q = w.__qaRaf
    q.callbackMs = []
    q.frameT = []
    q.drawAt = []
    q.record = true
    const draws0 = q.draws
    const fx = w.__geoSim?.fxState
    const maxLive = { sparks: 0, edgePulses: 0, nodeBursts: 0, labels: 0 }
    let minBudgetScale = 1
    let i = 0
    const pump = setInterval(() => {
      if (i < frames.length) w.__qaSse.push(frames[i++]!)
      maxLive.labels = Math.max(maxLive.labels, document.querySelectorAll('.floating-label').length)
      if (!fx) return
      maxLive.sparks = Math.max(maxLive.sparks, fx.sparks.length)
      maxLive.edgePulses = Math.max(maxLive.edgePulses, fx.edgePulses.length)
      maxLive.nodeBursts = Math.max(maxLive.nodeBursts, fx.nodeBursts.length)
      if (typeof fx.__fxBudgetScale === 'number') minBudgetScale = Math.min(minBudgetScale, fx.__fxBudgetScale)
    }, everyMs)
    await new Promise((r) => setTimeout(r, windowMs))
    clearInterval(pump)
    q.record = false
    w.gc?.()
    const mem = (performance as unknown as { memory?: { usedJSHeapSize: number } }).memory
    return {
      drawAt: q.drawAt, frameT: q.frameT, callbackMs: q.callbackMs, draws: q.draws - draws0,
      heapUsed: mem && w.gc ? mem.usedJSHeapSize : null,
      maxLive: fx ? maxLive : { labels: maxLive.labels, sparks: null, edgePulses: null, nodeBursts: null },
      telemetry: fx ? { lastFps: fx.__lastFps, fxBudgetScale: fx.__fxBudgetScale, maxParticles: fx.__maxParticles, renderQuality: fx.__renderQuality } : null,
      minBudgetScale: fx ? minBudgetScale : null,
    }
  }, { frames, windowMs, everyMs: EVERY_MS })
}

const pct = (xs: number[], p: number) => {
  if (xs.length === 0) return NaN
  const s = [...xs].sort((a, b) => a - b)
  return s[Math.min(s.length - 1, Math.floor((p / 100) * s.length))]!
}
const r1 = (x: number) => Math.round(x * 10) / 10

/**
 * A frame is a completed draw of the PRODUCT's render loop (one clear of the base canvas per `renderFrame`), not a
 * tick of a rAF chain of the stand's own: if the product's loop sleeps under load, that is what the numbers must show.
 * A window with no product draws, or with too few to take a percentile of, is refused instead of summarized.
 * Work per frame: the callbacks of one frame share the rAF timestamp, so their durations are summed by it.
 */
function summarize(w: Window, name: string) {
  expect(w.draws, `${name}: the product's render loop drew during the window (0 = it slept under load, or the draw counter is blind)`).toBeGreaterThan(0)
  expect(w.drawAt.length, `${name}: at least 20 product frames to take a percentile of`).toBeGreaterThanOrEqual(20)
  expect(w.callbackMs.length, `${name}: rAF callbacks were timed`).toBeGreaterThan(0)
  const frameDeltaMs = w.drawAt.slice(1).map((t, i) => t - w.drawAt[i]!)
  const work = new Map<number, number>()
  w.frameT.forEach((t, i) => work.set(t, (work.get(t) ?? 0) + w.callbackMs[i]!))
  const perFrame = [...work.values()]
  const mean = frameDeltaMs.reduce((a, b) => a + b, 0) / frameDeltaMs.length
  return {
    frames: frameDeltaMs.length, draws: w.draws, fps: r1(1000 / mean),
    frameMs: { p50: r1(pct(frameDeltaMs, 50)), p95: r1(pct(frameDeltaMs, 95)), max: r1(Math.max(...frameDeltaMs)) },
    workMs: { p50: r1(pct(perFrame, 50)), p95: r1(pct(perFrame, 95)), max: r1(Math.max(...perFrame)) },
    maxLive: w.maxLive, telemetry: w.telemetry, minBudgetScale: w.minBudgetScale,
  }
}

type Run = { x1: ReturnType<typeof summarize>; x4: ReturnType<typeof summarize>; x8: ReturnType<typeof summarize>; heapGrowthMb: number | null; heapStartMb: number | null; maxLabels: number }
const runs: Run[] = []

async function throttled(page: Page, cdp: CDPSession, rate: number) {
  await cdp.send('Emulation.setCPUThrottlingRate', { rate })
  const s = summarize(await measure(page, load(WINDOW_MS), WINDOW_MS), `CPU x${rate}`)
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: 1 })
  return s
}

for (let run = 1; run <= RUNS; run += 1) {
  test(`perf run ${run}/${RUNS}: frame time on 100 nodes at CPU x1, x4, x8 and heap growth over 60 s (${devHook ? 'dev server' : 'production build'})`, async ({ page }) => {
    await boot(page, { snapshot, clock: false, devHook })
    const cdp = await page.context().newCDPSession(page)
    const x1 = await throttled(page, cdp, 1)
    const x4 = await throttled(page, cdp, 4)
    const x8 = await throttled(page, cdp, 8)

    const heap0 = await page.evaluate(() => {
      const w = window as unknown as { gc?: () => void }
      w.gc?.()
      const mem = (performance as unknown as { memory?: { usedJSHeapSize: number } }).memory
      return mem && w.gc ? mem.usedJSHeapSize : null
    })
    const long = await measure(page, load(HEAP_WINDOW_MS), HEAP_WINDOW_MS)
    const mb = (b: number) => Math.round((b / 1048576) * 100) / 100
    expect(long.draws, 'heap window: the product drew while the heap was watched').toBeGreaterThan(0)
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

test.afterAll(async ({}, testInfo) => {
  if (runs.length === 0) return
  // `null` means "not measurable in this build" (no dev hook, no gc); a negative number is a measurement and stays.
  const med = (xs: Array<number | null>) => {
    const v = xs.filter((x): x is number => x !== null).sort((a, b) => a - b)
    if (v.length === 0) return null
    if (v.some((x) => !Number.isFinite(x))) throw new Error(`a non-finite measurement reached the summary: ${JSON.stringify(xs)}`)
    const mid = v.length >> 1
    return { median: v.length % 2 ? v[mid]! : (v[mid - 1]! + v[mid]!) / 2, min: v[0]!, max: v[v.length - 1]!, n: v.length }
  }
  const at = (k: 'x1' | 'x4' | 'x8') => ({
    fps: med(runs.map((r) => r[k].fps)), frameP95Ms: med(runs.map((r) => r[k].frameMs.p95)), frameP50Ms: med(runs.map((r) => r[k].frameMs.p50)),
    workP95Ms: med(runs.map((r) => r[k].workMs.p95)), workP50Ms: med(runs.map((r) => r[k].workMs.p50)),
    maxLiveSparks: med(runs.map((r) => r[k].maxLive.sparks)), maxLiveEdgePulses: med(runs.map((r) => r[k].maxLive.edgePulses)),
    minBudgetScale: med(runs.map((r) => r[k].minBudgetScale)),
  })
  const summary = {
    build: devHook ? 'dev server' : 'production build (vite build + vite preview)', runs: runs.length, nodes: snapshot.nodes.length, links: snapshot.links.length,
    x1: at('x1'), x4: at('x4'), x8: at('x8'),
    heapGrowthMbOver60s: med(runs.map((r) => r.heapGrowthMb)), heapStartMb: med(runs.map((r) => r.heapStartMb)), maxLabels: med(runs.map((r) => r.maxLabels)),
  }
  console.log('[038 perf] SUMMARY (median of runs, min..max)', JSON.stringify(summary))
  mkdirSync(testInfo.project.outputDir, { recursive: true })
  writeFileSync(join(testInfo.project.outputDir, `qa-perf-summary-${devHook ? 'dev' : 'preview'}.json`), JSON.stringify({ summary, runs }, null, 2))
})
