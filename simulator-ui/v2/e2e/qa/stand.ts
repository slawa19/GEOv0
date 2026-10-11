/**
 * 038 QA stand helpers: mocked backend with a controllable SSE stream, clock, rAF counter, geometry, pixel sampler.
 * Not a test file: the stand collects `*.qa.ts` / `*.qaperf.ts` only.
 *
 * What these helpers do NOT see is listed next to each of them; their silence is not evidence.
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { expect, type Page, type Route } from '@playwright/test'

export const RUN_ID = 'run-p038'
export const SCENARIO_ID = 'p038-qa-scenario'
export const EQ = 'UAH'

export type QaSnapshot = {
  equivalent: string
  generated_at: string
  nodes: Array<Record<string, unknown> & { id: string }>
  links: Array<Record<string, unknown> & { source: string; target: string }>
  [k: string]: unknown
}

function node(id: string, name: string) {
  return {
    id, name, type: 'person', status: 'active', links_count: 2, net_balance_atoms: '0', net_sign: 0, net_balance: '0',
    viz_color_key: 'default', viz_shape_key: 'circle', viz_size: { w: 24, h: 24 }, viz_badge_key: '',
  }
}

function link(source: string, target: string) {
  return {
    source, target, trust_limit: '100', used: '0', available: '100', status: 'active',
    // Keys the backend really emits (`vizMapping.ts`: width thin|mid|thick, alpha bg|muted|active|hi). With unknown
    // keys the trust lines were drawn too faint to see, and a beam in a video flew along nothing.
    viz_color_key: 'default', viz_width_key: 'mid', viz_alpha_key: 'hi',
  }
}

/**
 * A ring of six: a route a -> b -> c -> d runs round three sides, and the chord a -> d crosses the inside of the
 * ring, away from every route edge. A chain would lay out straight and hide the straightening of the beam.
 */
export function ringSnapshot(): QaSnapshot {
  const ids = ['a', 'b', 'c', 'd', 'e', 'f']
  return {
    equivalent: EQ,
    generated_at: '2026-02-01T00:00:00.000Z',
    palette: { default: { color: '#64748b', label: 'Default' } },
    limits: { max_particles: 120 },
    nodes: ids.map((id) => node(id, id.toUpperCase())),
    links: ids.map((id, i) => link(id, ids[(i + 1) % ids.length]!)),
  }
}

/** The versioned 100-node demo snapshot (029 S4): the size the no-GPU policy speaks about. */
export function fixtureSnapshot100(): QaSnapshot {
  const path = fileURLToPath(new URL('../../public/simulator-fixtures/v1/UAH/snapshot.json', import.meta.url))
  return JSON.parse(readFileSync(path, 'utf8')) as QaSnapshot
}

/**
 * The whole backend on `page.route`. Anything not listed answers 501 and is recorded in `unmocked`: without the
 * catch-all an unknown call would go through the Vite proxy to whatever listens on the developer's backend port.
 */
export async function mockBackend(page: Page, snapshot: QaSnapshot): Promise<{ unmocked: string[] }> {
  const unmocked: string[] = []
  const json = (route: Route, body: unknown, status = 200) =>
    route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
  const runBody = {
    api_version: 'simulator-api/1', run_id: RUN_ID, scenario_id: SCENARIO_ID, mode: 'real', state: 'paused',
    sim_time_ms: 0, intensity_percent: 0, ops_sec: 0, queue_depth: 0,
  }
  const participants = snapshot.nodes.map((n) => ({ pid: n.id, name: String(n.name ?? n.id) }))

  await page.route('**/api/v1/**', (r) => {
    unmocked.push(`${r.request().method()} ${new URL(r.request().url()).pathname}`)
    return json(r, { code: 'QA_STAND_UNMOCKED', message: 'not mocked by the 038 stand' }, 501)
  })
  await page.route('**/simulator/session/ensure', (r) => json(r, { actor_kind: 'anon', owner_id: 'owner-p038' }))
  await page.route('**/simulator/runs/active', (r) => json(r, { run_id: null }))
  await page.route(/\/simulator\/scenarios$/i, (r) =>
    json(r, {
      api_version: 'simulator-api/1',
      items: [{ api_version: 'simulator-api/1', scenario_id: SCENARIO_ID, name: 'QA stand',
        participants_count: snapshot.nodes.length, trustlines_count: snapshot.links.length, equivalents: [EQ] }],
    }))
  await page.route(/\/simulator\/scenarios\/[^/]+\/graph\/preview/i, (r) => json(r, snapshot))
  await page.route(/\/simulator\/runs$/i, (r) => json(r, { run_id: RUN_ID }))
  await page.route(`**/simulator/runs/${RUN_ID}/pause`, (r) => json(r, runBody))
  await page.route(`**/simulator/runs/${RUN_ID}/intensity`, (r) => json(r, runBody))
  await page.route(`**/simulator/runs/${RUN_ID}`, (r) => json(r, runBody))
  await page.route(new RegExp(`/simulator/runs/${RUN_ID}/graph/snapshot`, 'i'), (r) => json(r, snapshot))
  await page.route(`**/simulator/runs/${RUN_ID}/actions/participants-list`, (r) => json(r, { items: participants }))
  await page.route(new RegExp(`/simulator/runs/${RUN_ID}/actions/trustlines-list`, 'i'), (r) =>
    json(r, {
      items: snapshot.links.map((l) => ({
        from_pid: l.source, from_name: l.source, to_pid: l.target, to_name: l.target, equivalent: EQ,
        limit: '100.00', used: '0.00', reverse_used: '0.00', available: '100.00', status: 'active',
      })),
    }))
  await page.route(new RegExp(`/simulator/runs/${RUN_ID}/payment-targets`, 'i'), (r) => json(r, { items: [] }))
  return { unmocked }
}

/**
 * The SSE endpoint as a stream the test writes to. The product reads SSE with `fetch` (`api/sse.ts`), and a
 * `route.fulfill` body ends at once, which the product answers with a reconnect: an event would be delivered on
 * every reconnect. The shim keeps one open stream per connection; every byte still goes through the product's own
 * parser, normalizer and event pipeline.
 *
 * `push` answers WHEN THE PRODUCT HAS TAKEN THE FRAME (038 S2). The stream is read in real time and the tests step a
 * fake clock: a test that stepped right after writing could step before the product had read the frame (shown by
 * `delivery.qa.ts` with a frame held in transport; it has not been seen to happen by itself). The signal is the product's own read
 * loop: it handles every message of a chunk synchronously and only then asks the stream for the next chunk, so the
 * `read()` call that FOLLOWS the one that returned our chunk means the handler has run - for a duplicate and for a
 * rejected event as well (the frame reached the handler; whether it animated is the test's question). No timer is
 * involved. A connection that is dropped answers for its frames too: they can no longer be taken.
 * `hold()` / `release()` keep written frames out of the stream: a delay in transport, for the probe of the above.
 */
export async function installSseShim(page: Page): Promise<void> {
  await page.addInitScript(() => {
    type Ctl = ReadableStreamDefaultController<Uint8Array>
    type Done = (() => void) | null
    /** `queued`: chunks in the stream, not yet read. `handed`: chunks the last read returned, not yet followed by a read. */
    type Conn = { queued: Done[]; handed: Done[] }
    const realFetch = window.fetch.bind(window)
    const enc = new TextEncoder()
    const state = { controllers: [] as Ctl[], opened: 0, pushed: 0, held: null as Array<() => void> | null }
    const conns = new Map<Ctl, Conn>()
    const drop = (c: Ctl) => {
      const i = state.controllers.indexOf(c)
      if (i >= 0) state.controllers.splice(i, 1)
      const conn = conns.get(c)
      conns.delete(c)
      for (const done of [...(conn?.queued ?? []), ...(conn?.handed ?? [])]) done?.()
    }
    const send = (text: string): Promise<number> =>
      Promise.all(state.controllers.map((c) => new Promise<void>((done) => {
        conns.get(c)!.queued.push(done)
        c.enqueue(enc.encode(text))
      }))).then((all) => all.length)
    ;(window as unknown as { __qaSse: unknown }).__qaSse = {
      state,
      /** Resolves with the number of connections that took the frame (0: nobody was listening, the frame is lost). */
      push(text: string): Promise<number> {
        state.pushed += 1
        const held = state.held
        if (held) return new Promise((taken) => { held.push(() => taken(send(text))) })
        return send(text)
      },
      hold() { state.held = state.held ?? [] },
      release() {
        const held = state.held ?? []
        state.held = null
        for (const go of held) go()
      },
    }
    window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof Request ? input.url : String(input)
      if (!/\/simulator\/runs\/[^/]+\/events(\?|$)/.test(url)) return realFetch(input, init)
      state.opened += 1
      let ctl: Ctl | null = null
      const conn: Conn = { queued: [null], handed: [] } // the first chunk is the `:ok` comment below
      const body = new ReadableStream<Uint8Array>({
        start(c) {
          ctl = c
          state.controllers.push(c)
          conns.set(c, conn)
          c.enqueue(enc.encode(':ok\n\n'))
        },
        cancel() {
          if (ctl) drop(ctl)
        },
      })
      init?.signal?.addEventListener('abort', () => {
        if (!ctl) return
        drop(ctl)
        try {
          ctl.error(new DOMException('Aborted', 'AbortError'))
        } catch { /* already closed */ }
      })
      const res = new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } })
      const reader = body.getReader()
      const counting = {
        read() {
          for (const done of conn.handed.splice(0)) done?.()
          return reader.read().then((r) => {
            if (!r.done) conn.handed.push(conn.queued.shift() ?? null)
            return r
          })
        },
        cancel: (reason?: unknown) => reader.cancel(reason),
        releaseLock: () => reader.releaseLock(),
        get closed() { return reader.closed },
      }
      Object.defineProperty(res, 'body', { value: { getReader: () => counting } })
      return Promise.resolve(res)
    }
  })
}

/**
 * Counts every `requestAnimationFrame` call, the time each callback takes, and the draws of the render loop. Register it AFTER `page.clock.install()`:
 * the clock replaces `window.requestAnimationFrame`, and a wrapper installed before it would be thrown away.
 * It does not see a caller that captured the function before this script ran; the counter-check
 * ("> 0 while a spark is flying") is what shows the render loop is seen.
 */
export async function installRafCounter(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const w = window as unknown as {
      __qaRaf: { calls: number; frames: number; draws: number; drawAt: number[]; callbackMs: number[]; frameT: number[]; record: boolean }
    }
    w.__qaRaf = { calls: 0, frames: 0, draws: 0, drawAt: [], callbackMs: [], frameT: [], record: false }
    // A completed pass of the render loop owner, not a registered callback: `useRenderLoop` clears the base canvas
    // exactly once per `renderFrame`, whether the frame came from rAF or `renderOnce`. One more clear is counted
    // here and is not a frame: the one-off blanking when the scene model becomes empty (038 S1, `blankCanvases`).
    const clearRect = CanvasRenderingContext2D.prototype.clearRect
    CanvasRenderingContext2D.prototype.clearRect = function (this: CanvasRenderingContext2D, ...a: [number, number, number, number]) {
      const c = this.canvas as HTMLCanvasElement
      if (c.classList?.contains('canvas') && !c.classList.contains('canvas-fx')) {
        w.__qaRaf.draws += 1
        if (w.__qaRaf.record) w.__qaRaf.drawAt.push(performance.now())
      }
      return clearRect.apply(this, a)
    }
    const inner = window.requestAnimationFrame.bind(window)
    window.requestAnimationFrame = (cb: FrameRequestCallback) => {
      w.__qaRaf.calls += 1
      return inner((t) => {
        const q = w.__qaRaf
        q.frames += 1
        if (!q.record) return cb(t)
        const t0 = performance.now()
        cb(t)
        const t1 = performance.now()
        q.callbackMs.push(t1 - t0)
        q.frameT.push(t)
      })
    }
  })
}

/** `calls`: registrations of a callback (weak: any caller). `draws`: completed passes of the render loop (see above). */
export async function loopCounters(page: Page): Promise<{ calls: number; frames: number; draws: number }> {
  return page.evaluate(() => {
    const q = (window as unknown as { __qaRaf: { calls: number; frames: number; draws: number } }).__qaRaf
    return { calls: q.calls, frames: q.frames, draws: q.draws }
  })
}

export type BootOptions = {
  snapshot: QaSnapshot
  /** `true`: fake clock installed before load and paused after the layout settled. */
  clock: boolean
  query?: string
  /** The segment of the top bar the app opens in. `auto` (Auto-Run) shows the scenario selector; default `interact`. */
  ui?: 'interact' | 'auto'
  /**
   * `false`: the CSS animations of labels are left to run by themselves. Only for the wall-clock videos for a human
   * (`*.qavideo.ts`); every deterministic check needs them on the JS clock. (The perf project still boots with the
   * pause - BACKLOG 034-6 (k), slice S3.)
   */
  cssClock?: false
  /** `false` on a production build: there is no `window.__geoSim`, so layout and FX state cannot be read. */
  devHook?: boolean
  /** `false`: the caller routes the SSE endpoint itself (`page.route`), the in-page stream is not installed. */
  sseShim?: boolean
  /** Runs after the backend mock is routed and before the app loads (a later `page.route` wins over an earlier one). */
  beforeLoad?: (page: Page) => Promise<void>
  /** Only for the probe that deliberately opens a WebDriver browser to show what it hides. Everything else must run as a user. */
  allowWebdriver?: true
  /**
   * `false` (038 S2): return as soon as the page runs in the user's mode, without waiting for a scene. For the two
   * surfaces that exist only while there is no scene (the loading and the error message). The clock is not paused.
   */
  awaitScene?: false
  /**
   * `false` (038 S2): do not wait for physics to stop moving nodes. Only for checks of DOM surfaces that do not read
   * positions at load; whatever then clicks the canvas waits for a still layout itself. Not with `clock: true`.
   */
  awaitLayout?: false
}

/**
 * The stand refuses to run on anything but the user's branches of the product: test mode off, navigator.webdriver
 * false. Checked in the browser, because a line in a config does not see a developer's `.env.local` or a reused dev
 * server. On a production build there is no dev hook, so test mode cannot be read there; the stand's own server is
 * started with VITE_TEST_MODE=0 (`playwright.qa.config.ts`), which is the only guard in that case.
 */
async function assertUserMode(page: Page, o: BootOptions): Promise<void> {
  const hook = o.devHook !== false
  if (hook) await page.waitForFunction(() => '__geoSim' in window, undefined, { timeout: 30_000 })
  const seen = await page.evaluate(() => {
    const g = (window as unknown as { __geoSim?: { isTestMode?: boolean } }).__geoSim
    return { isTestMode: g ? g.isTestMode : null, webdriver: navigator.webdriver }
  })
  if (hook && seen.isTestMode !== false) {
    throw new Error(
      `038 QA stand refuses to run: the product is in test mode (__geoSim.isTestMode = ${String(seen.isTestMode)}, expected false). `
      + 'In test mode sparks are not spawned, the render loop never sleeps and dragging is off, so every result here would be about another product. '
      + 'Look for VITE_TEST_MODE=1 in simulator-ui/v2/.env.local or in the environment, or for a dev server of the snapshot stand reused on this port.',
    )
  }
  if (!o.allowWebdriver && seen.webdriver !== false) {
    throw new Error(
      `038 QA stand refuses to run: navigator.webdriver = ${String(seen.webdriver)}, expected false. `
      + 'Under WebDriver the product puts the FX canvas under the base canvas (App.css:49-51) and turns off label glow, edge hover and the quality autoguard. '
      + 'Run through e2e/playwright.qa.config.ts, which starts Chromium with --disable-blink-features=AutomationControlled.',
    )
  }
}

/** One frame of the stand: `frames()` steps the paused clock by this much. */
export const FRAME_MS = 16
/**
 * The phase of the paused clock on the frame grid: `Date.now() % FRAME_MS` at the moment the clock is paused, and so
 * at every event a test writes afterwards. It used to be whatever the wall clock gave, and a test whose last frame
 * falls exactly on the end of a lifetime (75 frames x 16 ms = the 1200 ms of a route) saw that frame with the spark
 * at fifteen phases and without it at phase 0 - a required step red by lottery (038 S2, adversarial pass on a7f7e1a6;
 * CI run 38152711087). Fixed at 0: the phase at which a frame boundary and a lifetime boundary COINCIDE, the hard
 * case, so a test that leans on a lucky phase is red every time and not one time in sixteen.
 * `QA_STAND_CLOCK_PHASE_MS` overrides it (0..15) - a diagnostic, to run the stand at another phase.
 */
export const CLOCK_PHASE_MS = ((): number => {
  const raw = process.env.QA_STAND_CLOCK_PHASE_MS
  if (raw === undefined || raw === '') return 0
  const n = Number(raw)
  if (!Number.isInteger(n) || n < 0 || n >= FRAME_MS) throw new Error(`QA_STAND_CLOCK_PHASE_MS must be an integer 0..${FRAME_MS - 1}, got "${raw}"`)
  return n
})()

/** The first moment not earlier than `t` whose phase on the frame grid is `CLOCK_PHASE_MS`. */
export function pausedClockTime(t: number): number {
  const base = t - (t % FRAME_MS) + CLOCK_PHASE_MS
  return base >= t ? base : base + FRAME_MS
}

/** Seed the run the way a returning user has it, load the app in real mode, wait for the scene and the SSE stream. */
export async function boot(page: Page, o: BootOptions): Promise<{ unmocked: string[] }> {
  const mock = await mockBackend(page, o.snapshot)
  if (o.clock) await page.clock.install({ time: new Date('2026-02-01T00:00:00Z') })
  if (o.sseShim !== false) await installSseShim(page)
  await installRafCounter(page)
  if (o.cssClock !== false) await installCssClockSync(page)
  await page.addInitScript(({ scenarioId, runId }) => {
    try {
      localStorage.clear()
      localStorage.setItem('geo.sim.v2.apiBase', '/api/v1')
      localStorage.setItem('geo.sim.v2.selectedScenarioId', scenarioId)
      localStorage.setItem('geo.sim.v2.runId', runId)
    } catch { /* ignore */ }
  }, { scenarioId: SCENARIO_ID, runId: RUN_ID })

  if (o.beforeLoad) await o.beforeLoad(page)
  await page.goto(`/?mode=real${o.ui === 'auto' ? '' : '&ui=interact'}${o.query ?? ''}`)
  await assertUserMode(page, o)
  if (o.awaitScene === false) return mock
  await expect(page.locator('[data-ready="1"]')).toBeVisible({ timeout: 30_000 })
  if (o.sseShim !== false) {
    await expect
      .poll(() => page.evaluate(() => (window as unknown as { __qaSse: { state: { controllers: unknown[] } } }).__qaSse.state.controllers.length), { timeout: 20_000 })
      .toBeGreaterThan(0)
  }
  if (o.devHook === false) {
    await waitLoopAsleep(page)
    return mock
  }
  await expect.poll(() => layoutNodes(page).then((n) => n.length), { timeout: 20_000 }).toBe(o.snapshot.nodes.length)
  if (o.awaitLayout !== false || o.clock) await waitLayoutStill(page)
  if (o.clock) {
    const now = await page.evaluate(() => Date.now())
    await page.clock.pauseAt(pausedClockTime(now + 1000))
  }
  return mock
}

export type Pt = { x: number; y: number }

export async function layoutNodes(page: Page): Promise<Array<{ id: string; x: number; y: number }>> {
  return page.evaluate(() => {
    const h = (window as unknown as { __geoSim?: { layoutNodes?: Array<{ id: string; x: number; y: number }> } }).__geoSim
    return h?.layoutNodes ?? []
  })
}

export async function camera(page: Page): Promise<{ panX: number; panY: number; zoom: number }> {
  return page.evaluate(() => {
    const c = (window as unknown as { __geoSim: { camera: { panX: number; panY: number; zoom: number } } }).__geoSim.camera
    return { panX: c.panX, panY: c.panY, zoom: c.zoom }
  })
}

/** Node centres in CSS pixels of the canvas: world * zoom + pan (the transform `useRenderLoop` applies). */
export async function screenNodes(page: Page): Promise<Map<string, Pt>> {
  const [nodes, cam] = await Promise.all([layoutNodes(page), camera(page)])
  return new Map(nodes.map((n) => [n.id, { x: n.x * cam.zoom + cam.panX, y: n.y * cam.zoom + cam.panY }]))
}

/** Physics has stopped moving nodes: three equal reads in a row. A condition, not a sleep; it times out if it never settles. */
export async function waitLayoutStill(page: Page): Promise<void> {
  let prev = ''
  let same = 0
  await expect
    .poll(async () => {
      const cur = JSON.stringify((await layoutNodes(page)).map((n) => [n.id, Math.round(n.x * 10), Math.round(n.y * 10)]))
      same = cur === prev && cur !== '[]' ? same + 1 : 0
      prev = cur
      return same
    }, { timeout: 30_000, intervals: [150] })
    .toBeGreaterThanOrEqual(3)
}

let eventSeq = 0

/** One SSE frame in the wire shape of the backend: `id` equals `event_id`, ids are of the run and grow. */
export async function pushEvent(
  page: Page, evt: Record<string, unknown> & { type: string },
  opts?: { eventId?: string; /** `false`: return as soon as the frame is written - only for the probe that shows what that costs. */ awaitTaken?: false },
): Promise<string> {
  eventSeq += 1
  const eventId = opts?.eventId ?? `evt_${RUN_ID}_${String(eventSeq).padStart(6, '0')}`
  const body = { event_id: eventId, ts: '2026-02-01T00:00:01.000Z', equivalent: EQ, ...evt }
  const frame = `id: ${eventId}\nevent: ${evt.type}\ndata: ${JSON.stringify(body)}\n\n`
  type Sse = { __qaSse: { push: (t: string) => Promise<number> } }
  if (opts?.awaitTaken === false) {
    await page.evaluate((f) => { void (window as unknown as Sse).__qaSse.push(f) }, frame)
    return eventId
  }
  // The answer comes from the product's read loop (see `installSseShim`), in real time and not on the fake clock.
  // The limit is not a wait for anything: it only turns a product that never reads into an error instead of a hang.
  let limit: ReturnType<typeof setTimeout> | undefined
  const never = new Promise<never>((_, fail) => {
    limit = setTimeout(() => fail(new Error(`038 QA stand: the product did not take the SSE frame ${eventId} (${evt.type}) in 15 s`)), 15_000)
  })
  try {
    await Promise.race([page.evaluate((f) => (window as unknown as Sse).__qaSse.push(f), frame), never])
  } finally {
    clearTimeout(limit)
  }
  return eventId
}

/** Keep written SSE frames out of the stream until `releaseSse`: a delay in transport. */
export const holdSse = (page: Page) => page.evaluate(() => (window as unknown as { __qaSse: { hold: () => void } }).__qaSse.hold())
export const releaseSse = (page: Page) => page.evaluate(() => (window as unknown as { __qaSse: { release: () => void } }).__qaSse.release())

export function txUpdated(route: string[], amount = '10.00', extra: Record<string, unknown> = {}) {
  const edges = route.slice(0, -1).map((from, i) => ({ from, to: route[i + 1]! }))
  return { type: 'tx.updated', from: route[0], to: route[route.length - 1], amount, amount_flyout: true, ttl_ms: 1200, edges, ...extra }
}

export async function fxCounts(page: Page): Promise<{ sparks: number; edgePulses: number; nodeBursts: number; labels: number }> {
  return page.evaluate(() => {
    const fx = (window as unknown as { __geoSim: { fxState: { sparks: unknown[]; edgePulses: unknown[]; nodeBursts: unknown[] } } }).__geoSim.fxState
    return {
      sparks: fx.sparks.length, edgePulses: fx.edgePulses.length, nodeBursts: fx.nodeBursts.length,
      labels: document.querySelectorAll('.floating-label').length,
    }
  })
}

/** Advance the paused clock by whole 16 ms frames. Timers and `requestAnimationFrame` of the page fire in order. */
export async function frames(page: Page, n: number): Promise<void> {
  for (let i = 0; i < n; i += 1) await page.clock.runFor(16)
}

export function pointsAlong(a: Pt, b: Pt, o: { stepPx?: number; endMarginPx?: number } = {}): Pt[] {
  const step = o.stepPx ?? 4
  const margin = o.endMarginPx ?? 28
  const len = Math.hypot(b.x - a.x, b.y - a.y)
  const out: Pt[] = []
  for (let d = margin; d <= len - margin; d += step) out.push({ x: a.x + ((b.x - a.x) * d) / len, y: a.y + ((b.y - a.y) * d) / len })
  return out
}

export function distToSegment(p: Pt, a: Pt, b: Pt): number {
  const dx = b.x - a.x
  const dy = b.y - a.y
  const l2 = dx * dx + dy * dy
  const t = l2 === 0 ? 0 : Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / l2))
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy))
}

export type BandResult = { points: number; lit: number; maxAlpha: number }

/**
 * Reads a canvas once and reports, per band, how many of its points have a painted pixel within `halfPx`.
 * The FX canvas is cleared to transparent every frame, so "painted" is alpha above `alphaMin`, not brightness:
 * the base graph lives on another canvas and cannot be mistaken for a spark.
 * It does not judge colour, shape or easing, only where something was painted.
 */
export async function sampleCanvas(
  page: Page, bands: Record<string, Pt[]>, o: { selector?: string; halfPx?: number; alphaMin?: number } = {},
): Promise<Record<string, BandResult> & { __canvas: BandResult }> {
  return page.evaluate(({ bands, selector, halfPx, alphaMin }) => {
    const c = document.querySelector<HTMLCanvasElement>(selector)
    if (!c) throw new Error(`no canvas ${selector}`)
    const ctx = c.getContext('2d')
    if (!ctx) throw new Error('the canvas has no 2d context')
    const k = c.width / Math.max(1, c.clientWidth)
    const img = ctx.getImageData(0, 0, c.width, c.height)
    const alpha = (x: number, y: number) => (x < 0 || y < 0 || x >= c.width || y >= c.height ? 0 : img.data[(y * c.width + x) * 4 + 3]!)
    const out: Record<string, { points: number; lit: number; maxAlpha: number }> = {}
    for (const [name, pts] of Object.entries(bands)) {
      let lit = 0
      let maxAlpha = 0
      for (const p of pts) {
        let best = 0
        const cx = Math.round(p.x * k)
        const cy = Math.round(p.y * k)
        const h = Math.round(halfPx * k)
        for (let dy = -h; dy <= h; dy += 1) for (let dx = -h; dx <= h; dx += 1) best = Math.max(best, alpha(cx + dx, cy + dy))
        if (best >= alphaMin) lit += 1
        maxAlpha = Math.max(maxAlpha, best)
      }
      out[name] = { points: pts.length, lit, maxAlpha }
    }
    let painted = 0
    let maxA = 0
    for (let i = 3; i < img.data.length; i += 4) {
      const a = img.data[i]!
      if (a >= alphaMin) painted += 1
      if (a > maxA) maxA = a
    }
    return { ...out, __canvas: { points: img.data.length / 4, lit: painted, maxAlpha: maxA } }
  }, { bands, selector: o.selector ?? 'canvas.canvas-fx', halfPx: o.halfPx ?? 4, alphaMin: o.alphaMin ?? 24 }) as Promise<Record<string, BandResult> & { __canvas: BandResult }>
}

/** The render loop has stopped drawing: three equal reads of the draw counter, 500 ms apart. Real clock only. */
export async function waitLoopAsleep(page: Page): Promise<void> {
  let prev = -1
  let same = 0
  await expect
    .poll(async () => {
      const cur = (await loopCounters(page)).draws
      same = cur === prev && cur > 0 ? same + 1 : 0
      prev = cur
      return same
    }, { timeout: 60_000, intervals: [500] })
    .toBeGreaterThanOrEqual(3)
}

/**
 * CSS animations do not follow `page.clock` (measured in `probes.qa.ts`): they run on the document timeline. This
 * script pauses the animation of every floating label the moment it is mounted and stamps the JS clock on it;
 * `syncCssToClock` then sets `currentTime` to the JS time elapsed since the mount. JS TTL and CSS fade thereby run on
 * one clock, as they do for a user, and a read of computed opacity is repeatable.
 * It does not see transitions, and it assumes the animation starts at mount (no `animation-delay`).
 */
export async function installCssClockSync(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const stamp = (el: HTMLElement) => {
      el.dataset.qaT0 = String(performance.now())
      for (const a of el.getAnimations()) a.pause()
    }
    const scan = (root: ParentNode) => {
      if (root instanceof HTMLElement && root.matches('.floating-label-inner')) stamp(root)
      for (const el of root.querySelectorAll<HTMLElement>('.floating-label-inner')) if (!el.dataset.qaT0) stamp(el)
    }
    const start = () =>
      new MutationObserver((records) => {
        for (const r of records) for (const n of r.addedNodes) if (n instanceof HTMLElement) scan(n)
      }).observe(document.documentElement, { childList: true, subtree: true })
    if (document.documentElement) start()
    else document.addEventListener('DOMContentLoaded', start)
  })
}

export type LabelSample = { text: string; cls: string; ageMs: number; opacity: number; durationMs: number; animations: number }

/** Set every label's CSS animation to its JS age and read the computed opacity. */
export async function syncCssToClock(page: Page): Promise<LabelSample[]> {
  return page.evaluate(() =>
    [...document.querySelectorAll<HTMLElement>('.floating-label-inner')].map((el) => {
      const age = performance.now() - Number(el.dataset.qaT0)
      const anims = el.getAnimations()
      let durationMs = 0
      for (const a of anims) {
        a.pause()
        a.currentTime = age
        durationMs = Math.max(durationMs, Number(a.effect?.getComputedTiming().duration ?? 0))
      }
      return {
        text: (el.textContent ?? '').trim(), cls: el.className, ageMs: age, opacity: Number(getComputedStyle(el).opacity),
        durationMs, animations: anims.length,
      }
    }))
}

// --- A second scenario next to the stand's own: for a change of scene and for a scene that cannot be loaded. ---

export const OTHER_SCENARIO = 'p038-qa-unloadable'
/** `other`: the preview of the other scenario answers 500. `all`: every scene read answers 500. */
export type SceneFail = { other: boolean; all: boolean; /** Scene reads seen so far (snapshot and preview), counted by the mock. */ reads?: number }

/** The stand's backend with a second scenario; its scene (or every scene) cannot be loaded while `fail` says so. */
export async function bootTwoScenarios(page: Page, fail: SceneFail) {
  const item = (scenario_id: string, name: string) => ({ api_version: 'simulator-api/1', scenario_id, name, participants_count: 6, trustlines_count: 6, equivalents: [EQ] })
  await boot(page, {
    snapshot: ringSnapshot(),
    clock: true,
    ui: 'auto', // the Auto-Run segment: the scenario selector lives there
    beforeLoad: async (p) => {
      await p.route(/\/simulator\/scenarios$/i, (r) => r.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ api_version: 'simulator-api/1', items: [item(SCENARIO_ID, 'QA stand'), item(OTHER_SCENARIO, 'QA stand, unloadable')] }),
      }))
      await p.route(/\/graph\/(snapshot|preview)/i, (r) => {
        const other = new URL(r.request().url()).pathname.includes(`/scenarios/${OTHER_SCENARIO}/`)
        fail.reads = (fail.reads ?? 0) + 1
        if (fail.all || (other && fail.other)) {
          return r.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ code: 'QA_STAND_NO_SCENE', message: 'qa stand: this scene cannot be loaded' }) })
        }
        // Loadable: other participants, so that choosing it is a full reload of the scene and not an increment.
        if (other) return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(otherRing()) })
        return r.fallback()
      })
    },
  })
}

/** The ring with other participant ids. */
export function otherRing() {
  const s = ringSnapshot()
  const rename = (id: string) => `x${id}`
  return { ...s, nodes: s.nodes.map((n) => ({ ...n, id: rename(n.id), name: `X${String(n.name)}` })), links: s.links.map((l) => ({ ...l, source: rename(l.source), target: rename(l.target) })) }
}
