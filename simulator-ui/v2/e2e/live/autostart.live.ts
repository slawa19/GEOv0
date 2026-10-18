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

type Diag = { events_by_type?: Record<string, number> }

const txUpdatedSeen = (page: Page) =>
  page.evaluate(() => Number(((globalThis as unknown as { __geo_real_mode_diag?: Diag }).__geo_real_mode_diag?.events_by_type ?? {})['tx.updated'] ?? 0))
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
  page.on('request', (r) => {
    const path = new URL(r.url()).pathname
    if (r.method() === 'POST' && /\/simulator\/runs$/.test(path)) creates.push(JSON.parse(r.postData() ?? '{}') as Record<string, unknown>)
    if (r.method() === 'POST' && /\/simulator\/runs\/[^/]+\/pause$/.test(path)) pauses.push(path)
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
  // 044 stage 2: the live config empties the dev token, so this is a visitor - the visitor tempo 15.
  expect(creates[0], 'at the visitor default intensity').toMatchObject({ intensity_percent: 15 })
  expect(pauses, 'nothing paused it').toEqual([])
  await expect(page.locator('[data-testid="network-state"]')).toContainText('живёт')

  // Leave the backend as found: stop the run this test started (Details -> Stop).
  await page.locator('[data-testid="details-toggle"]').click()
  await page.getByRole('button', { name: 'Stop', exact: true }).click()
  await expect(page.locator('[data-testid="network-state"]')).toContainText(/остановлен|останавливается/)
})
