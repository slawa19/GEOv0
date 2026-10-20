/**
 * 043 S2, reproducer (b) on a REAL seeded backend (config `e2e/playwright.live.config.ts`; CI: job `ui-smoke`, the
 * backend of the Admin smoke). A first visit - nothing in storage, the address `/` without `?mode` and `?ui` - must
 * show the network living: within 15 s of opening, a `tx.updated` of a run has arrived and its beam is on the canvas,
 * with no click on Start.
 *
 * Read through the real mode's diagnostic counters (`__geo_real_mode_diag`) and the dev hook of the dev server
 * (`window.__geoSim`: `fxState`, `layoutNodes`, `camera` - existing, nothing added for this test).
 * THE BEAM, specifically (external review 2026-10-17: "any FX pixel" also passed on the node bursts the same payment
 * paints): for each live spark of the FX state, the segment between its two nodes on screen is sampled on the FX
 * canvas, leaving out a disc of `NODE_CLEAR_PX` around both ends and around every node that is bursting at that moment.
 * A painted point there is the beam's body or trail, not a burst. The FX canvas is cleared every frame, and the graph
 * lives on the base canvas, so nothing else paints there but an edge pulse of a clearing (counted separately, below).
 * WHAT IT DOES NOT SEE: a production build (no dev hook; the test runs the dev server as an anonymous visitor, see the
 * config), another scenario than the one the product picks by default, a backend under load. The run it starts is
 * stopped at the end.
 */
import { expect, test, type Page } from '@playwright/test'

import { expectNetworkState } from '../helpers/networkState.js'

type Diag = { events_by_type?: Record<string, number> }

const txUpdatedSeen = (page: Page) =>
  page.evaluate(() => Number(((globalThis as unknown as { __geo_real_mode_diag?: Diag }).__geo_real_mode_diag?.events_by_type ?? {})['tx.updated'] ?? 0))
/** 049 S2: the payments the run attempted, as the client accepted them - committed (`tx.updated`) and failed (`tx.failed`). */
const attemptsSeen = (page: Page) =>
  page.evaluate(() => {
    const by = (globalThis as unknown as { __geo_real_mode_diag?: Diag }).__geo_real_mode_diag?.events_by_type ?? {}
    return Number(by['tx.updated'] ?? 0) + Number(by['tx.failed'] ?? 0)
  })

/**
 * 049 S2 (decision 10): the rate of the run's payment attempts at a tempo step, measured by WAITING for events - the time
 * the next `count` attempts take to arrive (no sleep; the timeout bounds a run that stopped paying). Attempts per second.
 */
async function attemptRate(page: Page, count: number, timeoutMs: number): Promise<number> {
  const from = await attemptsSeen(page)
  const started = Date.now()
  await expect.poll(async () => (await attemptsSeen(page)) - from, { timeout: timeoutMs, intervals: [100], message: `${count} payment attempts arrive` })
    .toBeGreaterThanOrEqual(count)
  const got = (await attemptsSeen(page)) - from
  return got / ((Date.now() - started) / 1000)
}
/** Screen pixels around a node left out of the beam sample: the outer reach of a node burst's glow (`renderFrame.ts`). */
const NODE_CLEAR_PX = 45

/**
 * Painted points (alpha >= 24, the stand's threshold) on the segments of the live sparks, away from every node that
 * is an end of a spark or is bursting. 0 when there is no spark, no long enough segment or nothing painted on it.
 */
const beamPointsLit = (page: Page) =>
  page.evaluate((clear) => {
    type G = { fxState: { sparks: Array<{ source: string; target: string }>; nodeBursts: Array<{ nodeId: string }>; edgePulses: unknown[] };
      layoutNodes: Array<{ id: string; x: number; y: number }>; camera: { panX: number; panY: number; zoom: number } }
    const g = (window as unknown as { __geoSim?: G }).__geoSim
    const c = document.querySelector<HTMLCanvasElement>('canvas.canvas-fx')
    const ctx = c?.getContext('2d')
    if (!g || !c || !ctx || c.width === 0 || g.fxState.sparks.length === 0) return { lit: 0, sparks: g?.fxState.sparks.length ?? 0, sampled: 0, pulses: g?.fxState.edgePulses.length ?? 0 }
    const k = c.width / Math.max(1, c.clientWidth)
    const img = ctx.getImageData(0, 0, c.width, c.height)
    const alpha = (x: number, y: number) => {
      const px = Math.round(x * k)
      const py = Math.round(y * k)
      return px < 0 || py < 0 || px >= c.width || py >= c.height ? 0 : img.data[(py * c.width + px) * 4 + 3]!
    }
    const cam = g.camera
    const at = new Map(g.layoutNodes.map((n) => [n.id, { x: n.x * cam.zoom + cam.panX, y: n.y * cam.zoom + cam.panY }]))
    const bursting = g.fxState.nodeBursts.map((b) => at.get(b.nodeId)).filter((p): p is { x: number; y: number } => !!p)
    let lit = 0
    let sampled = 0
    for (const s of g.fxState.sparks) {
      const a = at.get(s.source)
      const b = at.get(s.target)
      if (!a || !b) continue
      const len = Math.hypot(b.x - a.x, b.y - a.y)
      for (let d = clear; d <= len - clear; d += 2) {
        const p = { x: a.x + ((b.x - a.x) * d) / len, y: a.y + ((b.y - a.y) * d) / len }
        if (bursting.some((q) => Math.hypot(q.x - p.x, q.y - p.y) < clear)) continue
        sampled += 1
        let best = 0
        for (let dy = -2; dy <= 2; dy += 1) for (let dx = -2; dx <= 2; dx += 1) best = Math.max(best, alpha(p.x + dx, p.y + dy))
        if (best >= 24) lit += 1
      }
    }
    return { lit, sparks: g.fxState.sparks.length, sampled, pulses: g.fxState.edgePulses.length }
  }, NODE_CLEAR_PX)

test('a first visit to a seeded backend: a live run starts by itself, a payment arrives and its beam is drawn within 15 s', async ({ page }) => {
  // Cleared before the first load only.
  await page.addInitScript(() => {
    try {
      if (sessionStorage.getItem('live.firstVisit') === '1') return
      sessionStorage.setItem('live.firstVisit', '1')
      localStorage.clear()
    } catch { /* ignore */ }
  })
  const creates: Array<Record<string, unknown>> = []
  const pauses: string[] = []
  /** The run the product created (URL of the create and the id it answered) - the cleanup stops exactly this one. */
  const created: Array<{ url: string; runId: string }> = []
  page.on('request', (r) => {
    const path = new URL(r.url()).pathname
    if (r.method() === 'POST' && /\/simulator\/runs$/.test(path)) creates.push(JSON.parse(r.postData() ?? '{}') as Record<string, unknown>)
    if (r.method() === 'POST' && /\/simulator\/runs\/[^/]+\/pause$/.test(path)) pauses.push(path)
  })
  page.on('response', async (r) => {
    if (r.request().method() !== 'POST' || !/\/simulator\/runs$/.test(new URL(r.url()).pathname) || !r.ok()) return
    const body = (await r.json().catch(() => ({}))) as { run_id?: unknown }
    if (typeof body.run_id === 'string' && body.run_id) created.push({ url: r.url(), runId: body.run_id })
  })

  await page.goto('/')
  const opened = Date.now()
  await expect(page.locator('[data-ready="1"]'), 'the scene of the backend is on screen').toBeVisible({ timeout: 15_000 })
  expect(await page.evaluate(() => navigator.webdriver), 'the product runs as a user\'s browser').toBe(false)

  await expect.poll(() => txUpdatedSeen(page), { timeout: Math.max(1, 15_000 - (Date.now() - opened)), intervals: [100], message: 'a tx.updated of the run arrived within 15 s of opening' })
    .toBeGreaterThan(0)
  let seen = { lit: 0, sparks: 0, sampled: 0, pulses: 0 }
  // A frame with an edge pulse of a clearing (which paints along edges too) is not counted.
  await expect.poll(async () => { seen = await beamPointsLit(page); return seen.pulses === 0 ? seen.lit : 0 }, { timeout: Math.max(1, 15_000 - (Date.now() - opened)), intervals: [50], message: 'a beam is drawn on the segment of its edge, away from the nodes and their bursts, within 15 s of opening' })
    .toBeGreaterThan(0)
  const tookMs = Date.now() - opened
  console.log(`043 S2 LIVE ${JSON.stringify({ tookMs, beam: seen, creates, pauses: pauses.length })}`)

  expect(creates, 'exactly one run was created, by the product itself').toHaveLength(1)
  // 044 stage 2: the live config empties the dev token, so this is a visitor - the visitor tempo (049 S2: 5, «обычно»).
  expect(creates[0], 'at the visitor default intensity').toMatchObject({ intensity_percent: 5 })
  expect(pauses, 'nothing paused it').toEqual([])
  // 046 wave 1, full (2026-10-19): by test id / data attribute - the words are the dictionary's (language of the page). The live config is English (Chromium en-US).
  await expectNetworkState(page, 'running') // the attribute and its word in the page's language (§15 Codex round)

  // 049 S2 (decision 5; spec item 3): «Показывать события» on a real run. Anti-vacuum first: payments arrived above with
  // the switch off (its default) and the history shows no line of the network.
  await expect(page.locator('.interact-history__item[data-source="network"]'), 'switch off: no network line').toHaveCount(0)
  await page.getByTestId('details-toggle').click()
  const feedBox = page.getByTestId('events-feed-toggle')
  await expect(feedBox, 'off by default').not.toBeChecked()
  await feedBox.check()
  const network = page.locator('.interact-history__item[data-source="network"]')
  await expect(page.getByTestId('recent-actions')).toHaveAttribute('data-feed', 'on')
  await expect.poll(() => network.count(), { timeout: 15_000, message: 'the automatic payments of the run appear as lines' }).toBeGreaterThan(0)

  // A manual payment (it pauses the run, 043 decision 3): its result is in the window; the history holds it ONCE - the
  // line of its `tx.updated` (decision 5: the own line of a payment is not repeated beside the network's one; no «you»).
  await page.getByTestId('actionbar-payment').click()
  await page.locator('#mp-from__trigger').click()
  await page.locator('#mp-from__surface [role="option"][data-option-value]:not([data-option-value=""])').first().click()
  const to = page.locator('#mp-to__surface [role="option"][data-option-value]:not([data-option-value=""])').first()
  await expect(to, 'a recipient the sender can reach').toBeVisible({ timeout: 15_000 })
  await to.click()
  await page.locator('#mp-amount').fill('1.00')
  await page.getByTestId('manual-payment-confirm').click()
  await expect(page.getByTestId('mp-result'), 'the result is in the window').toBeVisible({ timeout: 15_000 })
  expect(await page.getByTestId('success-toast').count(), 'no success toast').toBe(0)
  const parties = ((await page.getByTestId('mp-result-parties').textContent()) ?? '').trim()
  expect(parties, 'counter-check: the parties are named').toMatch(/ → /)
  const manualLine = page.locator('.interact-history__item', { hasText: `${parties}: 1.00` })
  await expect(manualLine, 'the manual payment is one line of the network').toHaveCount(1, { timeout: 15_000 })
  await expect(manualLine).toHaveAttribute('data-source', 'network')
  await expect(page.locator('.interact-history__item[data-source="own"]'), 'no own line beside it').toHaveCount(0)
  await page.getByTestId('mp-result-close').click()
  await expect(page.locator('[data-testid="network-state"]')).toHaveAttribute('data-run-state', 'paused')
  // «Поток» brings the automatic payments back: new lines appear.
  const before = await attemptsSeen(page)
  await page.getByTestId('flow-toggle').click()
  await expect(page.locator('[data-testid="network-state"]')).toHaveAttribute('data-run-state', 'running')
  await expect.poll(() => attemptsSeen(page), { timeout: 15_000, message: 'the resumed run pays again' }).toBeGreaterThan(before)
  await expect.poll(async () => (await page.locator('.interact-history__item').first().textContent()) ?? '', { timeout: 15_000, message: 'the latest line is a new one' })
    .not.toContain(`${parties}: 1.00`)

  // 049 S2 (decision 10): the tempo by words reaches the run. «быстро» (15 %) against «медленно» (2 %): the slow step pays
  // less often - a generous comparison of the two rates, not a promise of a rate (the planner's budget is the backend's,
  // 049 S2a: one attempt in about 2.5 s at 2 %, about three a second at 15 %).
  await page.getByTestId('tempo-step-fast').click()
  await expect(page.getByTestId('tempo-step-fast')).toHaveAttribute('aria-pressed', 'true')
  const fast = await attemptRate(page, 6, 30_000)
  await page.getByTestId('tempo-step-slow').click()
  await expect(page.getByTestId('tempo-step-slow')).toHaveAttribute('aria-pressed', 'true')
  const slow = await attemptRate(page, 3, 45_000)
  console.log(`049 S2 LIVE tempo ${JSON.stringify({ fastPerSec: Math.round(fast * 100) / 100, slowPerSec: Math.round(slow * 100) / 100 })}`)
  expect(slow, `«медленно» pays less often than «быстро» (${slow.toFixed(2)}/s against ${fast.toFixed(2)}/s)`).toBeLessThan(fast)

  // Leave the backend as found: stop the run this test started. 048 S2 (amendment 8): a visitor has no Stop in the UI
  // (the run row is the administrator's), so the stop goes where the product's own Stop goes - `POST
  // /simulator/runs/{run_id}/stop` (`app/api/v1/simulator.py`, `stop_run`), which the run's owner may call - from inside
  // the page: the visitor's session cookie and the page's Origin (the CSRF check of a cookie actor, `deps.py`
  // `_check_csrf_origin`) go with it as with any request of the product.
  expect(created, 'the create answered with the id of the run').toHaveLength(1)
  const stopUrl = `${created[0]!.url}/${encodeURIComponent(created[0]!.runId)}/stop?source=e2e&reason=live_test_cleanup`
  const stopped = await page.evaluate(async (url) => {
    const r = await fetch(url, { method: 'POST', credentials: 'include' })
    return { status: r.status, state: String(((await r.json().catch(() => ({}))) as { state?: unknown }).state ?? '') }
  }, stopUrl)
  expect(stopped.status, `the stop of the run was accepted (${JSON.stringify(stopped)})`).toBe(200)
  expect(stopped.state, 'the run is stopping or stopped').toMatch(/^(stopping|stopped)$/)
  await expectNetworkState(page, ['stopped', 'stopping'], { message: 'and the screen says so' })
})
