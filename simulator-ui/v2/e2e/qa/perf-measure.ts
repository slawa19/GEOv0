/**
 * 038 S3 - one wall-clock window of a load, measured by the page itself. Not a test file; never used with `page.clock`.
 *
 * A frame is a completed draw of the PRODUCT's render loop (one clear of the base canvas per `renderFrame`, counted
 * by `installRafCounter`), not a tick of a rAF chain of the stand's own: if the product's loop sleeps under load, that
 * is what the numbers must show. JS work of a frame: the callbacks of one frame share the rAF timestamp, so their
 * durations are summed by it.
 *
 * `pushed` counts what the stand wrote into the SSE stream; `taken` counts what the PRODUCT read from it and handled -
 * the counting reader of the SSE shim (`stand.ts`, `installSseShim`: the product's read loop asked for the next chunk
 * after ours). A stream that stops being read part of the way leaves `taken` short of `pushed`, and the window is
 * refused: a load that did not arrive makes any frame time look good (external review of S3, P2; 038 S2, I2).
 *
 * It does not read pixels, and it does not see: time spent outside `requestAnimationFrame` callbacks (style, paint,
 * raster, compositing - `frame-breakdown.qaperf.ts` traces those), input latency, the first load of the page.
 */
import { expect, type Page } from '@playwright/test'

export type Live = { sparks: number | null; edgePulses: number | null; nodeBursts: number | null; labels: number }
export type RawWindow = {
  drawAt: number[]; frameT: number[]; callbackMs: number[]; draws: number; heapUsed: number | null
  pushed: number; taken: number; maxLive: Live; labelAnims: { running: number; paused: number }
  telemetry: { lastFps?: number; fxBudgetScale?: number; maxParticles?: number; renderQuality?: string } | null
  minBudgetScale: number | null
}

/**
 * Deliver the frames on their schedule (frame k at k * everyMs of wall time; a late timer delivers what is due, so a
 * throttled page gets the same events) for `windowMs`, and record what the page can measure about itself.
 */
export async function measureWindow(page: Page, frames: string[], windowMs: number, everyMs: number): Promise<RawWindow> {
  return page.evaluate(async ({ frames, windowMs, everyMs }) => {
    type Fx = { sparks: unknown[]; edgePulses: unknown[]; nodeBursts: unknown[]; __lastFps?: number; __fxBudgetScale?: number; __maxParticles?: number; __renderQuality?: string }
    const w = window as unknown as {
      __qaRaf: { draws: number; drawAt: number[]; callbackMs: number[]; frameT: number[]; record: boolean }
      __qaSse: { push: (t: string) => Promise<number> }
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
    const labelAnims = { running: 0, paused: 0 }
    let minBudgetScale = 1
    let pushed = 0
    let taken = 0
    const answers: Array<Promise<void>> = []
    let tick = 0
    const t0 = performance.now()
    const pump = setInterval(() => {
      const due = Math.min(frames.length, Math.floor((performance.now() - t0) / everyMs) + 1)
      while (pushed < due) answers.push(w.__qaSse.push(frames[pushed++]!).then((connections) => { if (connections > 0) taken += 1 }))
      tick += 1
      maxLive.labels = Math.max(maxLive.labels, document.querySelectorAll('.floating-label').length)
      if (tick % 5 === 0) {
        let running = 0
        let paused = 0
        for (const el of document.querySelectorAll<HTMLElement>('.floating-label-inner')) {
          for (const a of el.getAnimations()) {
            if (a.playState === 'running') running += 1
            else if (a.playState === 'paused') paused += 1
          }
        }
        labelAnims.running = Math.max(labelAnims.running, running)
        labelAnims.paused = Math.max(labelAnims.paused, paused)
      }
      if (!fx) return
      maxLive.sparks = Math.max(maxLive.sparks, fx.sparks.length)
      maxLive.edgePulses = Math.max(maxLive.edgePulses, fx.edgePulses.length)
      maxLive.nodeBursts = Math.max(maxLive.nodeBursts, fx.nodeBursts.length)
      if (typeof fx.__fxBudgetScale === 'number') minBudgetScale = Math.min(minBudgetScale, fx.__fxBudgetScale)
    }, 50)
    await new Promise((r) => setTimeout(r, windowMs))
    clearInterval(pump)
    // The last frames were written moments ago: give their answers one second of this (wall) clock, no more - a stream
    // that is not read never answers, and that is the case to report, not to wait out.
    await Promise.race([Promise.all(answers), new Promise((r) => setTimeout(r, 1000))])
    q.record = false
    w.gc?.()
    const mem = (performance as unknown as { memory?: { usedJSHeapSize: number } }).memory
    return {
      drawAt: q.drawAt, frameT: q.frameT, callbackMs: q.callbackMs, draws: q.draws - draws0,
      heapUsed: mem && w.gc ? mem.usedJSHeapSize : null, pushed, taken, labelAnims,
      maxLive: fx ? maxLive : { labels: maxLive.labels, sparks: null, edgePulses: null, nodeBursts: null },
      telemetry: fx ? { lastFps: fx.__lastFps, fxBudgetScale: fx.__fxBudgetScale, maxParticles: fx.__maxParticles, renderQuality: fx.__renderQuality } : null,
      minBudgetScale: fx ? minBudgetScale : null,
    }
  }, { frames, windowMs, everyMs })
}

export const pct = (xs: number[], p: number) => {
  if (xs.length === 0) return NaN
  const s = [...xs].sort((a, b) => a - b)
  return s[Math.min(s.length - 1, Math.floor((p / 100) * s.length))]!
}
export const r1 = (x: number) => Math.round(x * 10) / 10

/**
 * A window with no product draws, with too few to take a percentile of, or with part of the load not pushed into the stream is
 * REFUSED instead of summarized: a void measurement must not look like a good one.
 */
export function summarizeWindow(w: RawWindow, name: string, expectedEvents: number) {
  expect(w.draws, `${name}: the product's render loop drew during the window (0 = it slept under load, or the draw counter is blind)`).toBeGreaterThan(0)
  expect(w.drawAt.length, `${name}: at least 20 product frames to take a percentile of`).toBeGreaterThanOrEqual(20)
  expect(w.callbackMs.length, `${name}: rAF callbacks were timed`).toBeGreaterThan(0)
  expect(w.pushed, `${name}: every event of the load was pushed into the SSE stream on schedule (a void load makes any frame time look good)`).toBe(expectedEvents)
  expect(w.taken, `: the product read and handled every event that was pushed (${w.pushed}); fewer means the SSE stream stopped being read part of the way - the load did not arrive`).toBe(w.pushed)
  const frameDeltaMs = w.drawAt.slice(1).map((t, i) => t - w.drawAt[i]!)
  const work = new Map<number, number>()
  w.frameT.forEach((t, i) => work.set(t, (work.get(t) ?? 0) + w.callbackMs[i]!))
  const perFrame = [...work.values()]
  const mean = frameDeltaMs.reduce((a, b) => a + b, 0) / frameDeltaMs.length
  return {
    frames: frameDeltaMs.length, draws: w.draws, pushed: w.pushed, taken: w.taken, fps: r1(1000 / mean),
    frameMs: { mean: r1(mean), p50: r1(pct(frameDeltaMs, 50)), p95: r1(pct(frameDeltaMs, 95)), max: r1(Math.max(...frameDeltaMs)) },
    workMs: { mean: r1(perFrame.reduce((a, b) => a + b, 0) / perFrame.length), p50: r1(pct(perFrame, 50)), p95: r1(pct(perFrame, 95)), max: r1(Math.max(...perFrame)) },
    maxLive: w.maxLive, labelAnims: w.labelAnims, telemetry: w.telemetry, minBudgetScale: w.minBudgetScale,
  }
}
export type WindowSummary = ReturnType<typeof summarizeWindow>

/** `null` means "not measurable in this build" (no dev hook, no gc); a negative number is a measurement and stays. */
export function med(xs: Array<number | null>) {
  const v = xs.filter((x): x is number => x !== null).sort((a, b) => a - b)
  if (v.length === 0) return null
  if (v.some((x) => !Number.isFinite(x))) throw new Error(`a non-finite measurement reached the summary: ${JSON.stringify(xs)}`)
  const mid = v.length >> 1
  const median = v.length % 2 ? v[mid]! : (v[mid - 1]! + v[mid]!) / 2
  // Spread of the runs around their median, in percent: what a difference between two runs has to exceed to mean anything.
  return { median, min: v[0]!, max: v[v.length - 1]!, n: v.length, spreadPct: median === 0 ? null : r1(((v[v.length - 1]! - v[0]!) / Math.abs(median)) * 100) }
}
