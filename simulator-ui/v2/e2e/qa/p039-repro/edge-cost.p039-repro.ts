/**
 * 039, before slice A closes - what the EDGE PASS of the base canvas costs in a frame at CPU x4. A MEASUREMENT by
 * subtraction, debug-only config (`playwright.p039-repro.config.ts`), wall clock, no threshold. Dated 2026-10-12, base
 * c55933fd. The product is not changed: everything is done from the page by wrapping methods of
 * `CanvasRenderingContext2D.prototype` ON TOP of the stand's own wrappers (`installRafCounter`).
 *
 * Conditions (switched at run time on ONE page, so they are interleaved and share the page's drift):
 *   A    `off`        - my wrappers removed: the stand's baseline, the product as is;
 *   A'   `pass`       - my wrappers installed, everything passes through (what the wrappers themselves cost);
 *   B    `drop`       - the `stroke()` of the base pass of trust lines is not issued (348 per frame);
 *   B2   `drop-path`  - B, and the `beginPath / moveTo / lineTo` of those lines are not issued either;
 *   C    `drop-base`  - nothing is drawn on the base canvas after its background: no stroke, fill, drawImage. This is
 *                       NOT the edge pass - it is the ceiling of what a cached static layer could give back.
 * B, B2 and C are compared with A', never with A.
 *
 * Which strokes are "the edge pass": on the base canvas (`canvas.canvas`, not `.canvas-fx`), a path of exactly one
 * `moveTo` and one `lineTo`, and among the first `links.length` such strokes after the frame's `clearRect`
 * (`baseGraph.ts`: the base pass of links comes first, the overlay of active/selected lines after it, nodes last).
 * Overlay strokes of active lines are NOT dropped in B/B2: they change every frame and no static layer holds them.
 *
 * What B does not remove: the JavaScript of the loop over the links (`getLinkTermination`, `withAlpha`, the style
 * setters) - it stays in every condition. So B is "recording + rasterising 348 strokes", not "the loop is gone".
 *
 * Load states: `mixed` = the stand's S3-MIXED-V1; `static` = no events at all, the loop kept at full rate by a
 * synthetic `pointermove` on the canvas every 60 ms (the product's own wake-up path, `useAppCanvasInteractionsWiring`).
 * Without that the product's loop does not run: it idles at 4 fps and stops after 3 s (`useRenderLoop.ts`).
 *
 * Run only this file: `npx playwright test --config=e2e/qa/p039-repro/playwright.p039-repro.config.ts "edge-cost\.p039"`
 * (a bare `edge-cost` also matches a checkout path that contains it, and then every reproducer of the folder runs).
 *
 * Limits: one machine; software-only Chromium (no GPU raster, canvas 2D rastered on the main thread); CDP throttling
 * slows the renderer's main thread only; a window is a fixed TIME (6 s, as the stand), not a fixed number of frames;
 * a trace costs time itself; the product's FX budget adapts to fps, so a faster condition may draw more FX.
 */
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import { expect, test, type Browser, type Page } from '@playwright/test'

import { buildPerfLoad } from '../perf-load.js'
import { measureWindow, med, r1, summarizeWindow } from '../perf-measure.js'
import { boot, fixtureSnapshot100, loopCounters, pushEvent, screenNodes, txUpdated, waitLoopAsleep } from '../stand.js'

type Mode = 'off' | 'pass' | 'drop' | 'drop-path' | 'drop-base'
type LoadKind = 'mixed' | 'static'
const CONDS: Array<{ id: string; mode: Mode }> = [
  { id: 'A', mode: 'off' }, { id: "A'", mode: 'pass' }, { id: 'B', mode: 'drop' }, { id: 'B2', mode: 'drop-path' }, { id: 'C', mode: 'drop-base' },
]
const WINDOW_MS = 6_000
const RATE = 4
const ROUNDS = Number.parseInt(process.env.EDGE_COST_ROUNDS ?? '', 10) || 4
const TRACE_ROUNDS = Number.parseInt(process.env.EDGE_COST_TRACE_ROUNDS ?? '', 10) || 3
const snapshot = fixtureSnapshot100()
const load = buildPerfLoad(snapshot)
const slots = Math.floor((WINDOW_MS - 1) / load.everyMs) + 1
const LINKS = snapshot.links.length
const NODES = snapshot.nodes.length

type Counts = {
  frames: number; edgeDropped: number; edgePassed: number; overlayLines: number; shapeStrokes: number; shapeDropped: number
  fills: number; drawImages: number; baseDropped: number; pathCallsDropped: number; anomalies: number; fxDraws: number
  perFrameEdge: Record<string, number>; perFrameShape: Record<string, number>
  /** Frames by the number of overlay lines (active or selected trust lines) and of sprites: a frame with more than the static 0 and 2 x nodes CHANGED the base canvas. */
  perFrameOverlay: Record<string, number>; perFrameSprites: Record<string, number>
}

/** The switch. Registered through `beforeLoad`, i.e. AFTER the stand's `installRafCounter`: `off` restores the stand's wrappers. */
async function installSwitch(page: Page): Promise<void> {
  await page.addInitScript((links: number) => {
    type Ctx = CanvasRenderingContext2D
    const fresh = () => ({
      frames: 0, edgeDropped: 0, edgePassed: 0, overlayLines: 0, shapeStrokes: 0, shapeDropped: 0, fills: 0, drawImages: 0, baseDropped: 0,
      pathCallsDropped: 0, anomalies: 0, fxDraws: 0, perFrameEdge: {} as Record<string, number>, perFrameShape: {} as Record<string, number>,
      perFrameOverlay: {} as Record<string, number>, perFrameSprites: {} as Record<string, number>,
    })
    const S = { mode: 'off' as string, c: fresh(), i: 0, mv: 0, ln: 0, ot: 0, fEdge: 0, fShape: 0, fOv: 0, fImg: 0, open: false }
    const kinds = new WeakMap<object, number>() // 1 base, 2 fx, 0 anything else (sprite baking, the stand's own canvases)
    const kind = (ctx: Ctx): number => {
      const k = kinds.get(ctx)
      if (k !== undefined) return k
      const el = ctx.canvas as HTMLCanvasElement
      const cl = el.classList
      const v = !cl?.contains('canvas') ? 0 : cl.contains('canvas-fx') ? 2 : 1
      if (v !== 0 || el.isConnected === false) kinds.set(ctx, v)
      return v
    }
    const closeFrame = () => {
      if (!S.open) return
      S.c.frames += 1
      S.c.perFrameEdge[S.fEdge] = (S.c.perFrameEdge[S.fEdge] ?? 0) + 1
      S.c.perFrameShape[S.fShape] = (S.c.perFrameShape[S.fShape] ?? 0) + 1
      S.c.perFrameOverlay[S.fOv] = (S.c.perFrameOverlay[S.fOv] ?? 0) + 1
      S.c.perFrameSprites[S.fImg] = (S.c.perFrameSprites[S.fImg] ?? 0) + 1
      S.open = false
    }
    const proto = CanvasRenderingContext2D.prototype as unknown as Record<string, (...a: unknown[]) => unknown>
    const orig: Record<string, (...a: unknown[]) => unknown> = {}
    const mine: Record<string, (...a: unknown[]) => unknown> = {}
    const def = (name: string, make: (real: (...a: unknown[]) => unknown) => (this: Ctx, ...a: unknown[]) => unknown) => {
      orig[name] = proto[name]!
      mine[name] = make(orig[name]!) as (...a: unknown[]) => unknown
    }
    def('clearRect', (real) => function (...a) {
      if (kind(this) === 1) { closeFrame(); S.open = true; S.i = 0; S.fEdge = 0; S.fShape = 0; S.fOv = 0; S.fImg = 0; S.mv = 0; S.ln = 0; S.ot = 0 }
      return real.apply(this, a)
    })
    const inEdgePass = () => S.mode === 'drop-path' && S.open && S.i < links
    def('beginPath', (real) => function (...a) {
      if (kind(this) === 1) { S.mv = 0; S.ln = 0; S.ot = 0; if (inEdgePass()) { S.c.pathCallsDropped += 1; return undefined } }
      return real.apply(this, a)
    })
    def('moveTo', (real) => function (...a) {
      if (kind(this) === 1) { S.mv += 1; if (inEdgePass()) { S.c.pathCallsDropped += 1; return undefined } }
      return real.apply(this, a)
    })
    def('lineTo', (real) => function (...a) {
      if (kind(this) === 1) { S.ln += 1; if (inEdgePass()) { S.c.pathCallsDropped += 1; return undefined } }
      return real.apply(this, a)
    })
    for (const m of ['arc', 'arcTo', 'rect', 'roundRect', 'ellipse', 'quadraticCurveTo', 'bezierCurveTo', 'closePath']) {
      def(m, (real) => function (...a) {
        // A shape call while the edge pass of `drop-path` is still open means the pass was not what this file assumes.
        if (kind(this) === 1) { S.ot += 1; if (inEdgePass()) S.c.anomalies += 1 }
        return real.apply(this, a)
      })
    }
    def('stroke', (real) => function (...a) {
      const k = kind(this)
      if (k === 2) S.c.fxDraws += 1
      else if (k === 1) {
        const line = S.ot === 0 && S.mv === 1 && S.ln === 1 && a.length === 0
        if (line && S.open && S.i < links) {
          S.i += 1
          S.fEdge += 1
          if (S.mode === 'drop' || S.mode === 'drop-path' || S.mode === 'drop-base') { S.c.edgeDropped += 1; return undefined }
          S.c.edgePassed += 1
        } else if (line) { S.c.overlayLines += 1; S.fOv += 1 }
        else {
          if (inEdgePass()) S.c.anomalies += 1
          S.fShape += 1
          if (S.mode === 'drop-base') { S.c.shapeDropped += 1; return undefined }
          S.c.shapeStrokes += 1
        }
        if (line && S.mode === 'drop-base') { S.c.baseDropped += 1; return undefined }
      }
      return real.apply(this, a)
    })
    for (const m of ['fill', 'drawImage']) {
      def(m, (real) => function (...a) {
        const k = kind(this)
        if (k === 2) S.c.fxDraws += 1
        else if (k === 1) {
          if (m === 'drawImage') S.fImg += 1
          if (S.mode === 'drop-base') { S.c.baseDropped += 1; return undefined }
          if (m === 'fill') S.c.fills += 1
          else S.c.drawImages += 1
        }
        return real.apply(this, a)
      })
    }
    ;(window as unknown as { __p039Edge: unknown }).__p039Edge = {
      setMode: (m: string) => { S.mode = m; for (const n of Object.keys(orig)) proto[n] = m === 'off' ? orig[n]! : mine[n]! },
      reset: () => { S.c = fresh(); S.open = false },
      read: () => { closeFrame(); return S.c },
    }
  }, LINKS)
}

type Sw = { __p039Edge: { setMode: (m: string) => void; reset: () => void; read: () => Counts }; __p039Pump?: ReturnType<typeof setInterval> }
const setMode = (page: Page, m: Mode) => page.evaluate((m) => (window as unknown as Sw).__p039Edge.setMode(m), m)
const resetCounts = (page: Page) => page.evaluate(() => (window as unknown as Sw).__p039Edge.reset())
const readCounts = (page: Page) => page.evaluate(() => (window as unknown as Sw).__p039Edge.read())
const startPump = (page: Page) => page.evaluate(() => {
  const c = document.querySelector<HTMLCanvasElement>('canvas.canvas:not(.canvas-fx)')!
  ;(window as unknown as Sw).__p039Pump = setInterval(() => c.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, clientX: 4, clientY: 716, pointerType: 'mouse' })), 60)
})
const stopPump = (page: Page) => page.evaluate(() => { const w = window as unknown as Sw; if (w.__p039Pump) clearInterval(w.__p039Pump); w.__p039Pump = undefined })

const outDir = () => { const d = test.info().project.outputDir; mkdirSync(d, { recursive: true }); return d }
const save = (name: string, data: unknown) => writeFileSync(join(outDir(), name), JSON.stringify(data, null, 2))

// ---------------------------------------------------------------------------------------------------------------
// One window
// ---------------------------------------------------------------------------------------------------------------

type TraceEvent = { ph: string; name: string; pid: number; tid: number; ts: number; dur?: number; args?: { name?: string } }
// Stage of a frame by trace event name: the table of `frame-breakdown.qaperf.ts` (not exported there), copied.
const STAGES: Array<[string, RegExp]> = [
  ['script', /^(FunctionCall|EvaluateScript|v8\.|V8\.(?!GC)|FireAnimationFrame|TimerFire|RunMicrotasks|EventDispatch|XHR|ParseHTML|v8::|PageAnimator::serviceScriptedAnimations|ScriptedAnimationController|WebSocket|ResourceReceive|Fetch)/],
  ['GC', /(GC|Scavenge|MarkCompact|Sweep|Heap::)/],
  ['style+layout', /^(UpdateLayoutTree|RecalculateStyles|Layout|LocalFrameView::Run(Style|Post)|Document::(updateStyle|UpdateStyle)|ScheduleStyleRecalculation|InvalidateLayout|IntersectionObserver|HitTest|UpdateLayerTree|LocalFrameView::UpdateStyleAndLayout|ParseAuthorStyleSheet|LocalFrameView::layout)/],
  ['prepaint+paint+layerize', /^(PrePaint|Paint|PaintImage|Layerize|UpdateLayer|LocalFrameView::RunPaint|LocalFrameView::RunPrePaint|PaintArtifactCompositor|SetLayerTreeId|Animation|CompositeLayers|LocalFrameView::UpdateAllLifecyclePhases|Animate|BeginMainThreadFrame|WidgetBase::BeginMainFrame|LayerTreeHost|PaintController|UpdateAnimation|CullRect)/],
  ['canvas2D raster (main thread)', /(Canvas|canvas|CanvasResourceProvider|RasterRecord|PaintOpBuffer|SkCanvas|Flush|ImageBitmap|StaticBitmapImage|UnacceleratedStaticBitmapImage|MemoryManagedPaintRecorder)/],
  ['commit', /^(Commit|ProxyMain::BeginMainFrame::commit|ProxyImpl::NotifyReadyToCommit|LayerTreeHostImpl::(BeginCommit|CommitComplete|FinishCommit)|ProxyMain)/],
  ['tile raster', /(RasterTask|RasterizerTask|ImageDecodeTask|TileManager|Raster|PlaybackToMemory|DisplayItemList|TaskGraphRunner|GpuRasterization|SoftwareImageDecode|TileTaskManager|ScheduledTasks)/],
  ['compositor thread', /^(DrawFrame|LayerTreeHostImpl|Scheduler|ProxyImpl|ActivateLayerTree|LayerTreeImpl|DrawLayers|AnimationHost|BeginFrame|ThreadProxy|DelayBasedTimeSource|CompositorFrameSink|SubmitCompositorFrame|GenerateCompositorFrame|PrepareToDraw|SingleThreadProxy|TreeSynchronizer|PictureLayerImpl|RenderSurface|CalculateRenderPasses|draw_property|cc::|viz::mojom::CompositorFrameSink)/],
  ['display compositor (viz)', /(viz|Viz|Display::|DirectRenderer|SoftwareRenderer|SurfaceAggregator|Swap|OutputSurface|SoftwareOutput|DrawAndSwap|Graphics\.Pipeline|GLRenderer|SkiaRenderer|FrameSinkManager|Present|DoDrawQuad|RenderPass|SkiaOutput|Presentation|RootCompositorFrameSink|ExternalBeginFrame|HostFrameSink|CompositorFrameSinkSupport|Surface::|blit|Blit)/],
]
const stageOf = (name: string) => STAGES.find(([, re]) => re.test(name))?.[0] ?? 'other'
const TOP_LEVEL = /^(RunTask|ThreadControllerImpl::RunTask|ThreadPool_RunTask|SequenceManager::DoIdleWork|TaskQueueManager::ProcessTaskFromWorkQueue|SimpleWatcher::OnHandleReady|Receive mojo message|MessagePumpForUI|ThreadController::Task|Thread::Run)/

/** ms per product frame: busy time and self time by stage and by event name, per thread group. */
function analyseTrace(events: TraceEvent[], frames: number) {
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
  const threads: Array<{ name: string; pid: number; self: Map<string, number>; busyMs: number }> = []
  for (const [key, list] of byThread) {
    list.sort((a, b) => a.ts - b.ts || b.dur! - a.dur!)
    const self = new Map<string, number>()
    const stack: Array<{ end: number; name: string; self: number }> = []
    let busy = 0
    const pop = () => { const top = stack.pop()!; self.set(top.name, (self.get(top.name) ?? 0) + Math.max(0, top.self) / 1000) }
    for (const e of list) {
      while (stack.length > 0 && stack[stack.length - 1]!.end <= e.ts) pop()
      if (stack.length > 0) stack[stack.length - 1]!.self -= e.dur!
      else busy += e.dur! / 1000
      stack.push({ end: e.ts + e.dur!, name: e.name, self: e.dur! })
    }
    while (stack.length > 0) pop()
    threads.push({ name: threadName.get(key) ?? '?', pid: list[0]!.pid, self, busyMs: busy })
  }
  const main = threads.filter((t) => t.name === 'CrRendererMain').sort((a, b) => b.busyMs - a.busyMs)[0]
  if (!main) return null
  const groupOf = (t: (typeof threads)[number]) => {
    if (t.pid === main.pid) return t.name === 'CrRendererMain' ? 'main' : t.name === 'Compositor' ? 'compositor' : /CompositorTileWorker|ThreadPool/.test(t.name) ? 'raster/pool workers' : null
    return /VizCompositor/.test(t.name) ? 'viz' : null
  }
  const out: Record<string, { busy: number; stages: Record<string, number>; names: Record<string, number> }> = {}
  for (const t of threads) {
    const g = groupOf(t)
    if (!g) continue
    const acc = (out[g] ??= { busy: 0, stages: {}, names: {} })
    acc.busy += t.busyMs / frames
    for (const [n, ms] of t.self) {
      const stage = TOP_LEVEL.test(n) ? 'untraced inside tasks' : stageOf(n)
      acc.stages[stage] = (acc.stages[stage] ?? 0) + ms / frames
      acc.names[n] = (acc.names[n] ?? 0) + ms / frames
    }
  }
  for (const g of Object.values(out)) {
    g.busy = Math.round(g.busy * 100) / 100
    for (const k of Object.keys(g.stages)) g.stages[k] = Math.round(g.stages[k]! * 100) / 100
    g.names = Object.fromEntries(Object.entries(g.names).sort((a, b) => b[1] - a[1]).slice(0, 30).map(([n, ms]) => [n, Math.round(ms * 100) / 100]))
  }
  return out
}

let tagSeq = 0
type Row = NonNullable<Awaited<ReturnType<typeof runWindowOnce>>>
type WindowOpts = { cond: string; mode: Mode | null; kind: LoadKind; rate: number; round: number; trace?: Browser }
/** Windows the stand refused (fewer than 20 product frames in 6 s: the page stalled). Kept and reported, never summarized. */
const stalled: Array<{ name: string; draws: number }> = []
/**
 * A stalled window is measured again, at most twice, and every stall is recorded: this machine is a desktop shared
 * with other work, and twice in the first runs one window of a page ran at 2-6 fps with its JavaScript six times
 * slower (in different conditions, A' once and C once). Dropping it is a choice of this file and is said in the output.
 */
async function runWindow(page: Page, o: WindowOpts): Promise<Row> {
  for (let attempt = 0; ; attempt += 1) {
    const row = await runWindowOnce(page, o, attempt < 2)
    if (row) return row
  }
}
async function runWindowOnce(page: Page, o: WindowOpts, mayStall = false) {
  const name = `${o.cond} ${o.kind} x${o.rate} #${o.round}${o.trace ? ' traced' : ''}`
  if (o.mode) await setMode(page, o.mode)
  await waitLoopAsleep(page)
  if (o.mode) await resetCounts(page)
  if (o.trace) {
    await o.trace.startTracing(page, { categories: ['toplevel', 'devtools.timeline', 'disabled-by-default-devtools.timeline', 'blink', 'cc', 'viz', 'gpu', 'v8', 'benchmark', 'disabled-by-default-v8.gc'] })
  }
  const cdp = await page.context().newCDPSession(page)
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: o.rate })
  const c0 = await loopCounters(page)
  const frames = o.kind === 'mixed' ? load.frames(0, slots, `e${(tagSeq += 1)}`) : []
  if (o.kind === 'static') {
    await startPump(page)
    await page.waitForTimeout(400) // the loop is awake and at its rate before the window opens
  }
  const raw = await measureWindow(page, frames, WINDOW_MS, load.everyMs)
  if (o.kind === 'static') await stopPump(page)
  const c1 = await loopCounters(page)
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: 1 })
  await cdp.detach()
  let trace: ReturnType<typeof analyseTrace> = null
  if (mayStall && raw.drawAt.length < 20) {
    if (o.trace) await o.trace.stopTracing()
    stalled.push({ name, draws: raw.drawAt.length })
    console.log('[039 edge-cost] STALLED window, measured again', JSON.stringify({ name, productFrames: raw.drawAt.length }))
    return null
  }
  const s = summarizeWindow(raw, name, frames.length)
  if (o.trace) {
    const events = (JSON.parse((await o.trace.stopTracing()).toString('utf8')) as { traceEvents: TraceEvent[] }).traceEvents
    trace = analyseTrace(events, s.frames)
    expect(trace, `${name}: the trace contains a renderer main thread`).toBeTruthy()
  }
  const counts = o.mode && o.mode !== 'off' ? await readCounts(page) : null
  const row = {
    cond: o.cond, load: o.kind, rate: o.rate, round: o.round, traced: !!o.trace,
    fps: s.fps, frameMs: s.frameMs, rafWorkMs: s.workMs, frames: s.frames,
    // The stand's own counter of drawing calls on both canvases, alive in every condition (also in `off`).
    drawCallsPerFrame: r1((c1.cmds - c0.cmds) / Math.max(1, c1.draws - c0.draws)),
    maxLive: s.maxLive, minBudgetScale: s.minBudgetScale,
    counts: counts && { frames: counts.frames, perFrameEdge: counts.perFrameEdge, perFrameShape: counts.perFrameShape, perFrameOverlay: counts.perFrameOverlay, perFrameSprites: counts.perFrameSprites, edgeDropped: counts.edgeDropped, edgePassed: counts.edgePassed,
      overlayLines: counts.overlayLines, baseDropped: counts.baseDropped, pathCallsDropped: counts.pathCallsDropped, anomalies: counts.anomalies, fxDraws: counts.fxDraws },
    trace,
  }
  console.log('[039 edge-cost] window', JSON.stringify({ ...row, trace: trace ? { main: trace.main?.busy } : undefined }))
  // Anti-vacuum of every window that carries the switch: the edge pass was seen whole in every frame, and dropped or not as the condition says.
  if (counts) {
    expect(counts.anomalies, `${name}: no shape call inside what this file takes for the edge pass`).toBe(0)
    expect(Object.keys(counts.perFrameEdge), `${name}: every frame issued exactly ${LINKS} edge strokes (the base canvas is redrawn whole every frame)`).toEqual([String(LINKS)])
    if (o.mode === 'pass') expect(counts.edgeDropped, `${name}: pass-through drops nothing`).toBe(0)
    else expect(counts.edgePassed, `${name}: every edge stroke was dropped`).toBe(0)
  }
  return row
}

const rotate = <T,>(xs: T[], by: number) => xs.map((_, i) => xs[(i + by) % xs.length]!)

function summarize(rows: Row[], metric: (r: Row) => number | null, ref = "A'") {
  const out: Record<string, unknown> = {}
  for (const kind of ['mixed', 'static'] as const) {
    const of = (cond: string) => rows.filter((r) => r.load === kind && r.cond === cond).map(metric)
    const base = med(of(ref))
    if (!base) continue
    const per: Record<string, unknown> = {}
    for (const cond of [...new Set(rows.map((r) => r.cond))]) {
      const m = med(of(cond))
      if (!m) continue
      per[cond] = { median: r1(m.median), runs: of(cond).map((x) => (x === null ? null : r1(x))), spreadPct: m.spreadPct, savedVsRefPct: cond === ref ? 0 : r1(((base.median - m.median) / base.median) * 100) }
    }
    out[kind] = per
  }
  return out
}

// ---------------------------------------------------------------------------------------------------------------
// 0. Proof that the switch does what it says, and the environment
// ---------------------------------------------------------------------------------------------------------------

test('039 edge cost - proof of the switch: 348 strokes dropped, 100 node outlines kept, FX untouched, pass-through equals the product; environment', async ({ page, browser }) => {
  await boot(page, { snapshot, clock: true, beforeLoad: installSwitch })
  const env = await (async () => {
    const b = await browser.newBrowserCDPSession()
    const info = await b.send('SystemInfo.getInfo') as unknown as { gpu: { devices: unknown[]; auxAttributes?: Record<string, unknown>; featureStatus?: Record<string, unknown> }; modelName: string; commandLine: string }
    const ver = await b.send('Browser.getVersion')
    await b.detach()
    return {
      browser: ver, gpuDevices: info.gpu.devices, featureStatus: info.gpu.featureStatus, commandLine: info.commandLine,
      aux: info.gpu.auxAttributes && Object.fromEntries(Object.entries(info.gpu.auxAttributes).filter(([k]) => /glRenderer|glVendor|softwareRendering|displayType|skiaBackend|canSupportThreadedTextureMailbox|inProcessGpu|passthrough|initializationTime/i.test(k))),
      page: await page.evaluate(() => ({ ua: navigator.userAgent, cores: navigator.hardwareConcurrency, dpr: devicePixelRatio, canvas: [...document.querySelectorAll('canvas')].map((c) => `${c.className} ${c.width}x${c.height}`),
        quality: document.querySelector<HTMLElement>('.root')?.dataset.quality, gpu: document.querySelector<HTMLElement>('.root')?.dataset.gpu, viteDev: !!(window as unknown as { __geoSim?: unknown }).__geoSim })),
    }
  })()
  console.log('[039 edge-cost] environment', JSON.stringify(env))

  // Midpoints of trust lines that lie clear of every node: where a line, and nothing else, is painted on the base canvas.
  const pos = await screenNodes(page)
  // (Not the midpoint itself: the scene is dense. The point of each line, of nine along it, that is farthest from any node.)
  const CLEAR_PX = 24
  const all = [...pos.values()]
  const mids = snapshot.links.map((l) => {
    const a = pos.get(l.source)!
    const b = pos.get(l.target)!
    let best = { x: 0, y: 0, clear: -1 }
    for (let k = 1; k <= 9; k += 1) {
      const x = Math.round(a.x + ((b.x - a.x) * k) / 10)
      const y = Math.round(a.y + ((b.y - a.y) * k) / 10)
      const clear = Math.min(...all.map((n) => Math.hypot(n.x - x, n.y - y)))
      if (clear > best.clear) best = { x, y, clear }
    }
    return best
  }).filter((m) => m.x > 3 && m.y > 3 && m.x < 1277 && m.y < 717 && m.clear > CLEAR_PX)
  expect(mids.length, `precondition: enough trust lines have a point more than ${CLEAR_PX} px clear of every node`).toBeGreaterThan(30)

  const pixels = (sel: string) => page.evaluate(({ sel, mids }) => {
    const c = document.querySelector<HTMLCanvasElement>(sel)!
    const d = c.getContext('2d')!.getImageData(0, 0, c.width, c.height).data
    let hash = 2166136261
    let notBackground = 0
    let lit = 0
    for (let i = 0; i < d.length; i += 4) {
      hash = Math.imul(hash ^ d[i]!, 16777619); hash = Math.imul(hash ^ d[i + 1]!, 16777619); hash = Math.imul(hash ^ d[i + 2]!, 16777619); hash = Math.imul(hash ^ d[i + 3]!, 16777619)
      if (d[i] !== 2 || d[i + 1] !== 6 || d[i + 2] !== 23) notBackground += 1
      if (d[i + 3]! > 0) lit += 1
    }
    // The 3x3 pixels around every sampled point of a line, as text: compared between conditions point by point.
    const at = mids.map((m) => {
      let s = ''
      for (let dy = -1; dy <= 1; dy += 1) for (let dx = -1; dx <= 1; dx += 1) { const i = ((m.y + dy) * c.width + m.x + dx) * 4; s += `${d[i]},${d[i + 1]},${d[i + 2]};` }
      return s
    })
    return { hash: hash >>> 0, notBackground, lit, at }
  }, { sel, mids })

  const l = snapshot.links[0]!
  const seen: Record<string, { counts: Counts | null; mid: { base: Awaited<ReturnType<typeof pixels>>; fxLit: number }; rest: Awaited<ReturnType<typeof pixels>>; standDraws: number; standCmds: number }> = {}
  await page.clock.runFor(4_000) // deep sleep: the first condition starts from the same state as the others
  for (const { id, mode } of CONDS) {
    await setMode(page, mode)
    await resetCounts(page)
    const c0 = await loopCounters(page)
    await pushEvent(page, txUpdated([l.source, l.target], '10.00', { amount_flyout: false }))
    await page.clock.runFor(608)
    const mid = { base: await pixels('canvas.canvas:not(.canvas-fx)'), fxLit: (await pixels('canvas.canvas-fx')).lit }
    await page.screenshot({ path: join(outDir(), `edge-cost-proof-${mode}-midflight.png`) })
    await page.clock.runFor(2_400)
    const rest = await pixels('canvas.canvas:not(.canvas-fx)')
    await page.screenshot({ path: join(outDir(), `edge-cost-proof-${mode}-rest.png`) })
    const c1 = await loopCounters(page)
    seen[id] = { counts: mode === 'off' ? null : await readCounts(page), mid, rest, standDraws: c1.draws - c0.draws, standCmds: c1.cmds - c0.cmds }
    await page.clock.runFor(4_000) // deep sleep before the next condition: every condition starts from the same state
  }
  await setMode(page, 'off')
  const brief = Object.fromEntries(Object.entries(seen).map(([k, v]) => [k, { ...v, counts: v.counts && { ...v.counts } }]))
  const differsAt = (x: { at: string[] }, y: { at: string[] }) => x.at.filter((s, i) => s !== y.at[i]).length
  const strip = (v: (typeof seen)[string]) => ({ ...v, mid: { ...v.mid, base: { ...v.mid.base, at: undefined } }, rest: { ...v.rest, at: undefined } })
  console.log('[039 edge-cost] proof', JSON.stringify({ linePointsSampled: mids.length, ...Object.fromEntries(Object.entries(brief).map(([k, v]) => [k, strip(v)])),
    linePointsDiffering: { "B vs A'": differsAt(seen.B!.rest, seen["A'"]!.rest), 'B2 vs B': differsAt(seen.B2!.rest, seen.B!.rest), "A vs A'": differsAt(seen.A!.rest, seen["A'"]!.rest) } }))
  save('edge-cost-proof.json', { env, linePointsSampled: mids.length, seen: Object.fromEntries(Object.entries(seen).map(([k, v]) => [k, strip(v)])) })

  const A = seen.A!, P = seen["A'"]!, B = seen.B!, B2 = seen.B2!, C = seen.C!
  // A' is the product: same passes, same drawing calls reaching the canvas, same pixels.
  expect([P.standDraws, P.standCmds], "A' issues the same passes and drawing calls as A (the stand's counter, below the switch)").toEqual([A.standDraws, A.standCmds])
  expect([P.rest.hash, P.mid.base.hash], "A' paints the base canvas exactly as A, at rest and mid-flight").toEqual([A.rest.hash, A.mid.base.hash])
  expect(P.counts!.edgeDropped + P.counts!.baseDropped + P.counts!.pathCallsDropped, "A' drops nothing").toBe(0)
  expect(Object.keys(P.counts!.perFrameEdge), `A': every pass issues ${LINKS} edge strokes`).toEqual([String(LINKS)])
  expect(Object.keys(P.counts!.perFrameShape), `A': every pass issues ${NODES} node outline strokes`).toEqual([String(NODES)])
  for (const [id, X] of [['B', B], ['B2', B2]] as const) {
    const c = X.counts!
    expect(c.frames, `${id}: the same number of passes as A'`).toBe(P.counts!.frames)
    expect(c.edgeDropped, `${id}: ${LINKS} edge strokes dropped in every pass`).toBe(LINKS * c.frames)
    expect([c.edgePassed, c.anomalies], `${id}: no edge stroke passed, nothing else was taken for one`).toEqual([0, 0])
    expect(Object.keys(c.perFrameShape), `${id}: the ${NODES} node outlines are still stroked in every pass`).toEqual([String(NODES)])
    expect([c.shapeStrokes, c.fills, c.drawImages, c.overlayLines], `${id}: node outlines, fills, sprites and overlay lines reach the canvas as in A'`).toEqual([P.counts!.shapeStrokes, P.counts!.fills, P.counts!.drawImages, P.counts!.overlayLines])
    expect(c.fxDraws, `${id}: the FX canvas got the same drawing calls as in A'`).toBe(P.counts!.fxDraws)
    expect(X.mid.fxLit, `${id}: the FX canvas carries the beam mid-flight`).toBeGreaterThan(0)
    expect(A.standCmds - X.standCmds, `${id}: exactly the edge strokes are missing below the switch`).toBe(LINKS * c.frames)
    // The scene is too dense for "background where the line was" (node glow reaches most of it), so the picture is
    // compared with A' point by point: the pixels on a line changed. That the lines are GONE and the nodes are whole
    // is what the screenshots `edge-cost-proof-*-rest.png` show to an eye; this file does not judge that.
    expect(differsAt(X.rest, P.rest), `${id}: the pixels at the sampled points of trust lines differ from A' (the lines are not painted as in A')`).toBeGreaterThanOrEqual(Math.floor(mids.length * 0.9))
    expect(X.rest.notBackground, `${id}: the nodes are still painted`).toBeGreaterThan(NODES * 50)
  }
  expect(B2.counts!.pathCallsDropped, 'B2: beginPath, moveTo and lineTo of every edge were not issued').toBe(3 * LINKS * B2.counts!.frames)
  expect(B2.rest.hash, 'B2 paints what B paints').toBe(B.rest.hash)
  // What is left in C is what `fillRect` draws inside node icons (not dropped: the background of the frame is a `fillRect` too).
  expect(C.rest.notBackground, 'C: the base canvas is its background and a residue of icon rectangles').toBeLessThan(A.rest.notBackground / 50)
  expect(C.counts!.fxDraws, 'C: the FX canvas got the same drawing calls').toBe(P.counts!.fxDraws)
})

// ---------------------------------------------------------------------------------------------------------------
// 1. Frame interval, untraced: A / A' / B / B2 / C interleaved, rotated every round
// ---------------------------------------------------------------------------------------------------------------

for (const kind of ['mixed', 'static'] as const) {
  test(`039 edge cost - frame interval at CPU x${RATE}, load ${kind}: ${ROUNDS} interleaved rounds of A, A', B, B2, C`, async ({ page }) => {
    test.setTimeout(1_800_000)
    await boot(page, { snapshot, clock: false, beforeLoad: installSwitch })
    const rows: Row[] = []
    for (let round = 0; round < ROUNDS; round += 1) {
      for (const c of rotate(CONDS, round)) rows.push(await runWindow(page, { cond: c.id, mode: c.mode, kind, rate: RATE, round }))
    }
    // Context only: one round without throttling.
    const x1: Row[] = []
    for (const c of CONDS) x1.push(await runWindow(page, { cond: c.id, mode: c.mode, kind, rate: 1, round: 0 }))
    await setMode(page, 'off')
    const summary = {
      frameMeanMs: summarize(rows, (r) => r.frameMs.mean), frameP50Ms: summarize(rows, (r) => r.frameMs.p50), frameP95Ms: summarize(rows, (r) => r.frameMs.p95),
      fps: summarize(rows, (r) => r.fps), rafWorkMeanMs: summarize(rows, (r) => r.rafWorkMs.mean),
      x1: { frameMeanMs: summarize(x1, (r) => r.frameMs.mean), fps: summarize(x1, (r) => r.fps), rafWorkMeanMs: summarize(x1, (r) => r.rafWorkMs.mean) },
    }
    console.log(`[039 edge-cost] interval ${kind}`, JSON.stringify(summary))
    save(`edge-cost-interval-${kind}.json`, { stalledWindowsMeasuredAgain: stalled, load: kind === 'mixed' ? load.name : 'static (pointermove every 60 ms)', fingerprint: load.fingerprint, rate: RATE, windowMs: WINDOW_MS, summary, rows, x1 })
    expect(rows.length, 'every window was measured').toBe(ROUNDS * CONDS.length)
  })
}

// ---------------------------------------------------------------------------------------------------------------
// 2. Trace: where the main thread's time goes, A' / B / C interleaved
// ---------------------------------------------------------------------------------------------------------------

test(`039 edge cost - trace at CPU x${RATE}: main-thread time per frame by stage, ${TRACE_ROUNDS} interleaved rounds of A', B, C on both loads`, async ({ page, browser }) => {
  test.setTimeout(1_800_000)
  await boot(page, { snapshot, clock: false, beforeLoad: installSwitch })
  const conds = CONDS.filter((c) => ["A'", 'B', 'C'].includes(c.id))
  const rows: Row[] = []
  for (const kind of ['mixed', 'static'] as const) {
    for (let round = 0; round < TRACE_ROUNDS; round += 1) {
      for (const c of rotate(conds, round)) rows.push(await runWindow(page, { cond: c.id, mode: c.mode, kind, rate: RATE, round, trace: browser }))
    }
  }
  await setMode(page, 'off')
  const stageNames = [...new Set(rows.flatMap((r) => Object.keys(r.trace?.main?.stages ?? {})))]
  const eventNames = [...new Set(rows.flatMap((r) => Object.keys(r.trace?.main?.names ?? {})))]
  const summary = {
    note: 'ms per product frame, traced windows (slower than untraced ones)',
    frameMeanMs: summarize(rows, (r) => r.frameMs.mean),
    mainBusyMs: summarize(rows, (r) => r.trace?.main?.busy ?? null),
    compositorBusyMs: summarize(rows, (r) => r.trace?.compositor?.busy ?? null),
    workersBusyMs: summarize(rows, (r) => r.trace?.['raster/pool workers']?.busy ?? null),
    vizBusyMs: summarize(rows, (r) => r.trace?.viz?.busy ?? null),
    mainByStage: Object.fromEntries(stageNames.map((s) => [s, summarize(rows, (r) => r.trace?.main?.stages[s] ?? 0)])),
    mainByEvent: Object.fromEntries(eventNames.map((n) => [n, summarize(rows, (r) => r.trace?.main?.names[n] ?? 0)])),
  }
  console.log('[039 edge-cost] trace', JSON.stringify({ frameMeanMs: summary.frameMeanMs, mainBusyMs: summary.mainBusyMs, mainByStage: summary.mainByStage }))
  save('edge-cost-trace.json', { stalledWindowsMeasuredAgain: stalled, rate: RATE, windowMs: WINDOW_MS, summary, rows })
  expect(rows.length, 'every window was traced').toBe(2 * TRACE_ROUNDS * conds.length)
})

// ---------------------------------------------------------------------------------------------------------------
// 3. Control: a page that never had the switch (does A = `off` on a switched page equal a page without it?)
// ---------------------------------------------------------------------------------------------------------------

test(`039 edge cost - control: a page without the switch, ${ROUNDS} windows per load at CPU x${RATE}`, async ({ page }) => {
  test.setTimeout(900_000)
  await boot(page, { snapshot, clock: false })
  const rows: Row[] = []
  for (let round = 0; round < ROUNDS; round += 1) {
    for (const kind of ['mixed', 'static'] as const) rows.push(await runWindow(page, { cond: 'A-virgin', mode: null, kind, rate: RATE, round }))
  }
  const summary = { frameMeanMs: summarize(rows, (r) => r.frameMs.mean, 'A-virgin'), frameP50Ms: summarize(rows, (r) => r.frameMs.p50, 'A-virgin'), frameP95Ms: summarize(rows, (r) => r.frameMs.p95, 'A-virgin'), fps: summarize(rows, (r) => r.fps, 'A-virgin') }
  console.log('[039 edge-cost] control', JSON.stringify(summary))
  save('edge-cost-control.json', { stalledWindowsMeasuredAgain: stalled, rate: RATE, windowMs: WINDOW_MS, summary, rows })
  expect(rows.length).toBe(2 * ROUNDS)
})
