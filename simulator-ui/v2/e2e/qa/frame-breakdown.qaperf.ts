/**
 * 038 S3 - where a frame goes on software-only Chromium. DIAGNOSTICS, wall clock, project `qa-perf`.
 *
 * Two instruments, both on the named load `S3-MIXED-V1`:
 *   1. a Chromium trace of one window, summed as SELF time per event name and per thread, divided by the number of
 *      product frames in the window - the stages of a frame (script, style, paint, canvas raster, compositing, ...);
 *   2. the same window with one thing taken away by the STAND (a style injected by the test, a field of the events, the
 *      viewport) - what each thing costs in frames per second. The product is not changed and none of these variants
 *      is a proposal by itself: they only say where the time is.
 *
 * Nothing here is a threshold. The tests fail only when a measurement is void (no frames, load not delivered, the
 * trace has no renderer main thread). Output: `qa-perf-frame-breakdown-<dev|preview>.json` in the output folder.
 *
 * Limits: CDP CPU throttling slows the renderer's MAIN thread only - compositor, raster workers and the display
 * compositor (viz) run at full speed, so "x4" is not a 4x slower machine; a trace costs time itself (the traced window
 * is slower than an untraced one, shares are what to read, not absolutes); one machine.
 */
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import { expect, test, type Browser, type Page } from '@playwright/test'

import { buildPerfLoad } from './perf-load.js'
import { measureWindow, r1, summarizeWindow, type WindowSummary } from './perf-measure.js'
import { boot, fixtureSnapshot100, waitLoopAsleep } from './stand.js'

const WINDOW_MS = 6_000
const devHook = process.env.QA_STAND_SERVER !== 'preview'
const build = devHook ? 'dev' : 'preview'
const snapshot = fixtureSnapshot100()
const load = buildPerfLoad(snapshot)
const slots = Math.floor((WINDOW_MS - 1) / load.everyMs) + 1

const out: Record<string, unknown> = {}

let tagSeq = 0
async function windowAt(page: Page, rate: number, name: string, mapFrames: (f: string[]) => string[] = (f) => f): Promise<WindowSummary> {
  await waitLoopAsleep(page)
  const cdp = await page.context().newCDPSession(page)
  await cdp.send('Emulation.setCPUThrottlingRate', { rate })
  const frames = mapFrames(load.frames(0, slots, `v${(tagSeq += 1)}`))
  const s = summarizeWindow(await measureWindow(page, frames, WINDOW_MS, load.everyMs), name, frames.length)
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: 1 })
  await cdp.detach()
  return s
}

// ---------------------------------------------------------------------------------------------------------------
// 1. Trace
// ---------------------------------------------------------------------------------------------------------------

type TraceEvent = { ph: string; name: string; cat?: string; pid: number; tid: number; ts: number; dur?: number; args?: { name?: string } }

/** Stage of a frame by the name of the trace event that owns the self time. Unlisted names go to `other` and are printed. */
const STAGES: Array<[string, RegExp]> = [
  ['script (rAF callbacks, timers, events, microtasks)', /^(FunctionCall|EvaluateScript|v8\.|V8\.(?!GC)|FireAnimationFrame|TimerFire|RunMicrotasks|EventDispatch|XHR|ParseHTML|v8::|PageAnimator::serviceScriptedAnimations|ScriptedAnimationController|WebSocket|ResourceReceive|Fetch)/],
  ['GC', /(GC|Scavenge|MarkCompact|Sweep|Heap::)/],
  ['style and layout', /^(UpdateLayoutTree|RecalculateStyles|Layout|LocalFrameView::Run(Style|Post)|Document::(updateStyle|UpdateStyle)|ScheduleStyleRecalculation|InvalidateLayout|IntersectionObserver|HitTest|UpdateLayerTree|LocalFrameView::UpdateStyleAndLayout|ParseAuthorStyleSheet|LocalFrameView::layout)/],
  ['pre-paint, paint, layerize (recording display lists)', /^(PrePaint|Paint|PaintImage|Layerize|UpdateLayer|LocalFrameView::RunPaint|LocalFrameView::RunPrePaint|PaintArtifactCompositor|SetLayerTreeId|Animation|CompositeLayers|LocalFrameView::UpdateAllLifecyclePhases|Animate|BeginMainThreadFrame|WidgetBase::BeginMainFrame|LayerTreeHost|PaintController|UpdateAnimation|CullRect)/],
  ['canvas 2D raster on the main thread (software canvas)', /(Canvas|canvas|CanvasResourceProvider|RasterRecord|PaintOpBuffer|SkCanvas|Flush|ImageBitmap|StaticBitmapImage|UnacceleratedStaticBitmapImage|MemoryManagedPaintRecorder)/],
  ['commit to the compositor', /^(Commit|ProxyMain::BeginMainFrame::commit|ProxyImpl::NotifyReadyToCommit|LayerTreeHostImpl::(BeginCommit|CommitComplete|FinishCommit)|ProxyMain)/],
  ['tile raster (raster workers)', /(RasterTask|RasterizerTask|ImageDecodeTask|TileManager|Raster|PlaybackToMemory|DisplayItemList|TaskGraphRunner|GpuRasterization|SoftwareImageDecode|TileTaskManager|ScheduledTasks)/],
  ['compositor thread (draw, animate, schedule)', /^(DrawFrame|LayerTreeHostImpl|Scheduler|ProxyImpl|ActivateLayerTree|LayerTreeImpl|DrawLayers|AnimationHost|BeginFrame|ThreadProxy|DelayBasedTimeSource|CompositorFrameSink|SubmitCompositorFrame|GenerateCompositorFrame|PrepareToDraw|SingleThreadProxy|TreeSynchronizer|PictureLayerImpl|RenderSurface|CalculateRenderPasses|draw_property|cc::|viz::mojom::CompositorFrameSink)/],
  ['display compositor (viz: aggregate, software render, filters, swap)', /(viz|Viz|Display::|DirectRenderer|SoftwareRenderer|SurfaceAggregator|Swap|OutputSurface|SoftwareOutput|DrawAndSwap|Graphics\.Pipeline|GLRenderer|SkiaRenderer|FrameSinkManager|Present|DoDrawQuad|RenderPass|SkiaOutput|Presentation|RootCompositorFrameSink|ExternalBeginFrame|HostFrameSink|CompositorFrameSinkSupport|Surface::|blit|Blit)/],
]
const stageOf = (name: string) => STAGES.find(([, re]) => re.test(name))?.[0] ?? 'other (named in topOther)'
const TOP_LEVEL = /^(RunTask|ThreadControllerImpl::RunTask|ThreadPool_RunTask|SequenceManager::DoIdleWork|TaskQueueManager::ProcessTaskFromWorkQueue|SimpleWatcher::OnHandleReady|Receive mojo message|MessagePumpForUI|ThreadController::Task|Thread::Run)/

/** Self time (ms) per event name for every thread: the duration of a complete event minus its nested complete events. */
function selfTimes(events: TraceEvent[]) {
  const threadName = new Map<string, string>()
  for (const e of events) if (e.ph === 'M' && e.name === 'thread_name' && e.args?.name) threadName.set(`${e.pid}:${e.tid}`, e.args.name)
  const byThread = new Map<string, TraceEvent[]>()
  for (const e of events) {
    if (e.ph !== 'X' || typeof e.dur !== 'number') continue
    const k = `${e.pid}:${e.tid}`
    const l = byThread.get(k) ?? []
    l.push(e)
    byThread.set(k, l)
  }
  const threads: Array<{ key: string; name: string; pid: number; self: Map<string, number>; busyMs: number }> = []
  for (const [key, list] of byThread) {
    list.sort((a, b) => a.ts - b.ts || b.dur! - a.dur!)
    const self = new Map<string, number>()
    const stack: Array<{ end: number; name: string; self: number }> = []
    let busy = 0
    const pop = () => {
      const top = stack.pop()!
      self.set(top.name, (self.get(top.name) ?? 0) + Math.max(0, top.self) / 1000)
    }
    for (const e of list) {
      while (stack.length > 0 && stack[stack.length - 1]!.end <= e.ts) pop()
      if (stack.length > 0) stack[stack.length - 1]!.self -= e.dur!
      else busy += e.dur! / 1000
      stack.push({ end: e.ts + e.dur!, name: e.name, self: e.dur! })
    }
    while (stack.length > 0) pop()
    threads.push({ key, name: threadName.get(key) ?? '?', pid: list[0]!.pid, self, busyMs: busy })
  }
  return threads
}

async function traced(browser: Browser, page: Page, rate: number, name: string, mapFrames?: (f: string[]) => string[]) {
  await waitLoopAsleep(page)
  await browser.startTracing(page, {
    categories: ['toplevel', 'devtools.timeline', 'disabled-by-default-devtools.timeline', 'blink', 'cc', 'viz', 'gpu', 'v8', 'benchmark', 'disabled-by-default-v8.gc'],
  })
  const window = await windowAt(page, rate, name, mapFrames)
  const events = (JSON.parse((await browser.stopTracing()).toString('utf8')) as { traceEvents: TraceEvent[] }).traceEvents
  const threads = selfTimes(events)
  const main = threads.filter((t) => t.name === 'CrRendererMain').sort((a, b) => b.busyMs - a.busyMs)[0]
  expect(main, `${name}: the trace contains a renderer main thread`).toBeTruthy()
  const frames = window.frames

  // Thread groups: the page's renderer by pid; everything else by thread name.
  const group = (t: (typeof threads)[number]) => {
    if (t.pid === main!.pid) {
      if (t.name === 'CrRendererMain') return 'renderer main thread (the one CPU throttling slows)'
      if (t.name === 'Compositor') return 'renderer compositor thread'
      if (/CompositorTileWorker|ThreadPool/.test(t.name)) return 'renderer raster / pool workers'
      return `renderer: ${t.name}`
    }
    if (/VizCompositor/.test(t.name)) return 'display compositor thread (viz)'
    if (/CrGpuMain|CrBrowserMain/.test(t.name)) return `${t.name} (other process)`
    return null
  }
  const perGroup = new Map<string, { busyMs: number; byStage: Map<string, number>; byName: Map<string, number> }>()
  for (const t of threads) {
    const g = group(t)
    if (!g) continue
    const acc = perGroup.get(g) ?? { busyMs: 0, byStage: new Map(), byName: new Map() }
    acc.busyMs += t.busyMs
    for (const [n, ms] of t.self) {
      // A top-level task wrapper's own time is the scheduler's bookkeeping plus whatever the trace has no event for.
      const stage = TOP_LEVEL.test(n) ? 'untraced inside tasks (no trace event names it)' : stageOf(n)
      acc.byStage.set(stage, (acc.byStage.get(stage) ?? 0) + ms)
      acc.byName.set(n, (acc.byName.get(n) ?? 0) + ms)
    }
    perGroup.set(g, acc)
  }
  const perFrame = (ms: number) => r1(ms / frames)
  const table = [...perGroup].sort((a, b) => b[1].busyMs - a[1].busyMs).map(([g, acc]) => ({
    thread: g,
    busyMsPerFrame: perFrame(acc.busyMs),
    stagesMsPerFrame: Object.fromEntries([...acc.byStage].sort((a, b) => b[1] - a[1]).filter(([, ms]) => ms / frames >= 0.05).map(([s, ms]) => [s, perFrame(ms)])),
    topNamesMsPerFrame: Object.fromEntries([...acc.byName].sort((a, b) => b[1] - a[1]).slice(0, 14).map(([n, ms]) => [n, perFrame(ms)])),
    topOther: Object.fromEntries([...acc.byName].filter(([n]) => !TOP_LEVEL.test(n) && stageOf(n).startsWith('other')).sort((a, b) => b[1] - a[1]).slice(0, 8).map(([n, ms]) => [n, perFrame(ms)])),
  }))
  const row = {
    cpuThrottling: rate, fps: window.fps, frameMs: window.frameMs, jsWorkMsInRafCallbacks: window.workMs, frames,
    note: 'ms per product frame; self time of trace events. The traced window is slower than an untraced one.',
    threads: table,
  }
  console.log(`[038 breakdown] trace ${name}`, JSON.stringify({ fps: row.fps, frameMean: row.frameMs.mean, jsWorkMean: row.jsWorkMsInRafCallbacks.mean,
    threads: table.map((t) => ({ thread: t.thread, busy: t.busyMsPerFrame, stages: t.stagesMsPerFrame })) }))
  return row
}

const noLabels = (fs: string[]) => fs.map((f) => f.replace('"amount_flyout":true', '"amount_flyout":false'))
/** An SSE comment in place of a removed event: the slots of the others keep their time. */
const SKIP = ': skipped by the stand\n\n'
const isClearing = (f: string) => f.includes('event: clearing.done')

test(`frame breakdown by trace: the named load at CPU x1 and x4, with and without amount labels (${build})`, async ({ page, browser }) => {
  await boot(page, { snapshot, clock: false, devHook })
  out.trace = {
    'x1, the load as it is': await traced(browser, page, 1, 'x1'),
    'x4, the load as it is': await traced(browser, page, 4, 'x4'),
    'x4, payments without amount labels': await traced(browser, page, 4, 'x4 no labels', noLabels),
  }
})

// ---------------------------------------------------------------------------------------------------------------
// 2. Take one thing away
// ---------------------------------------------------------------------------------------------------------------

type Variant = { name: string; css?: string; mapFrames?: (f: string[]) => string[]; viewport?: { width: number; height: number } }
const LABEL = '.floating-label-inner'
const VARIANTS: Variant[] = [
  { name: 'baseline: the load as it is' },
  { name: 'no amount labels of payments (amount_flyout: false); the clearing label stays', mapFrames: noLabels },
  { name: 'no amount labels of payments, and the clearing label without filter', mapFrames: noLabels, css: `${LABEL}.clearing-premium { filter: none !important; }` },
  { name: 'labels without their CSS animation (static, full opacity)', css: `${LABEL} { animation: none !important; }` },
  { name: 'labels without filter: drop-shadow', css: `${LABEL} { filter: none !important; }` },
  { name: 'labels without text-shadow', css: `${LABEL} { text-shadow: none !important; }` },
  { name: 'labels without filter and without text-shadow', css: `${LABEL} { filter: none !important; text-shadow: none !important; }` },
  { name: 'labels without filter, text-shadow and will-change', css: `${LABEL} { filter: none !important; text-shadow: none !important; will-change: auto !important; }` },
  { name: 'FX canvas not displayed (still drawn into)', css: 'canvas.canvas-fx { display: none !important; }' },
  { name: 'HUD not displayed (everything but the canvases and the label layers)', css: '.root > :not(canvas):not(.floating-layer):not(.labels-layer) { display: none !important; }' },
  { name: 'payments only, no clearing', mapFrames: (fs) => fs.map((f) => (isClearing(f) ? SKIP : f)) },
  { name: 'one 1-edge beam per 900 ms, no labels, no clearing (close to the base graph alone)',
    mapFrames: (fs) => noLabels(fs).map((f, i) => (i % 6 === 0 && !isClearing(f) ? f : SKIP)) },
  { name: 'viewport 640x360 (a quarter of the pixels)', viewport: { width: 640, height: 360 } },
  { name: 'baseline again (drift of the machine during the test)' },
]

for (const rate of [1, 4]) {
  test(`frame breakdown by subtraction at CPU x${rate}: what each part of the frame costs (${build})`, async ({ page }) => {
    test.setTimeout(600_000)
    await boot(page, { snapshot, clock: false, devHook })
    const dom = await page.evaluate(() => ({
      canvases: [...document.querySelectorAll('canvas')].map((c) => `${c.className} ${c.width}x${c.height}`),
      nodeLabels: document.querySelectorAll('.node-label').length, elements: document.querySelectorAll('*').length,
      quality: document.querySelector<HTMLElement>('.root')!.dataset.quality, dpr: window.devicePixelRatio,
    }))
    const rows: Array<{ variant: string; fps: number; frameMeanMs: number; frameP95Ms: number; jsWorkMeanMs: number; maxLabels: number; fpsVsBaselinePct?: number }> = []
    for (const v of VARIANTS) {
      if (v.viewport) await page.setViewportSize(v.viewport)
      const style = v.css ? await page.addStyleTag({ content: v.css }) : null
      const s = await windowAt(page, rate, `x${rate} ${v.name}`, v.mapFrames)
      await style?.evaluate((el) => (el as Element).remove())
      if (v.viewport) await page.setViewportSize({ width: 1280, height: 720 })
      rows.push({ variant: v.name, fps: s.fps, frameMeanMs: s.frameMs.mean, frameP95Ms: s.frameMs.p95, jsWorkMeanMs: s.workMs.mean, maxLabels: s.maxLive.labels })
    }
    const base = rows[0]!.fps
    for (const r of rows) r.fpsVsBaselinePct = r1(((r.fps - base) / base) * 100)
    console.log(`[038 breakdown] subtraction x${rate}`, JSON.stringify({ dom, rows }))
    out[`subtraction x${rate}`] = { dom, rows }
    expect(rows.length, 'every variant was measured').toBe(VARIANTS.length)
  })
}

test(`frame breakdown: the same load on a deviceScaleFactor 2 screen (${build})`, async ({ browser, baseURL }) => {
  const ctx = await browser.newContext({ baseURL, viewport: { width: 1280, height: 720 }, deviceScaleFactor: 2 })
  try {
    const page = await ctx.newPage()
    await boot(page, { snapshot, clock: false, devHook })
    const canvas = await page.evaluate(() => ({ dpr: window.devicePixelRatio, canvas: `${document.querySelector('canvas')!.width}x${document.querySelector('canvas')!.height}` }))
    const x1 = await windowAt(page, 1, 'dpr2 x1')
    const x4 = await windowAt(page, 4, 'dpr2 x4')
    const row = { ...canvas, x1: { fps: x1.fps, frameMeanMs: x1.frameMs.mean, frameP95Ms: x1.frameMs.p95 }, x4: { fps: x4.fps, frameMeanMs: x4.frameMs.mean, frameP95Ms: x4.frameMs.p95 } }
    console.log('[038 breakdown] deviceScaleFactor 2', JSON.stringify(row))
    out['deviceScaleFactor 2'] = row
  } finally {
    await ctx.close()
  }
})

test.afterAll(async ({}, testInfo) => {
  if (Object.keys(out).length === 0) return
  mkdirSync(testInfo.project.outputDir, { recursive: true })
  writeFileSync(join(testInfo.project.outputDir, `qa-perf-frame-breakdown-${build}.json`), JSON.stringify({
    what: 'DIAGNOSTICS of one machine: where a frame goes on software-only Chromium under the load ' + load.name, build, load: load.fingerprint, windowMs: WINDOW_MS, ...out,
  }, null, 2))
})
