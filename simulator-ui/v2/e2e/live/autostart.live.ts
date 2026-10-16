/**
 * 043 S2, reproducer (b) on a REAL seeded backend (config `e2e/playwright.live.config.ts`; CI: job `ui-smoke`, the
 * backend of the Admin smoke). A first visit - nothing in storage, the address `/` without `?mode` and `?ui` - must
 * show the network living: within 15 s of opening, a `tx.updated` of a run has arrived and its beam is on the canvas,
 * with no click on Start.
 *
 * Read through the real mode's diagnostic counters (`__geo_real_mode_diag`, installed on a localhost page, production
 * build too) and the pixels of the FX canvas (cleared to transparent every frame: a painted pixel is an effect of an
 * event - a beam, a pulse, a burst - never the graph, which lives on the base canvas). A production build has no dev
 * hook, so "the beam" is read as "the FX canvas is painted after a `tx.updated` arrived", not as `fxState.sparks`.
 * WHAT IT DOES NOT SEE: which effect was painted (a clearing's would count too), another scenario than the one the
 * product picks by default, a backend under load. The run it starts is stopped at the end.
 */
import { expect, test, type Page } from '@playwright/test'

type Diag = { events_by_type?: Record<string, number> }

const txUpdatedSeen = (page: Page) =>
  page.evaluate(() => Number(((globalThis as unknown as { __geo_real_mode_diag?: Diag }).__geo_real_mode_diag?.events_by_type ?? {})['tx.updated'] ?? 0))
/** Pixels of the FX canvas with alpha >= 24 (the stand's threshold, `sampleCanvas`). */
const fxPainted = (page: Page) =>
  page.evaluate(() => {
    const c = document.querySelector<HTMLCanvasElement>('canvas.canvas-fx')
    const ctx = c?.getContext('2d')
    if (!c || !ctx || c.width === 0 || c.height === 0) return 0
    const d = ctx.getImageData(0, 0, c.width, c.height).data
    let n = 0
    for (let i = 3; i < d.length; i += 4) if (d[i]! >= 24) n += 1
    return n
  })

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
  await expect.poll(() => fxPainted(page), { timeout: Math.max(1, 15_000 - (Date.now() - opened)), intervals: [50], message: 'a beam is on the canvas within 15 s of opening' })
    .toBeGreaterThan(0)
  const tookMs = Date.now() - opened
  console.log(`043 S2 LIVE ${JSON.stringify({ tookMs, creates, pauses: pauses.length })}`)

  expect(creates, 'exactly one run was created, by the product itself').toHaveLength(1)
  expect(creates[0], 'at the default intensity').toMatchObject({ intensity_percent: 30 })
  expect(pauses, 'nothing paused it').toEqual([])
  await expect(page.locator('[data-testid="network-state"]')).toContainText('живёт')

  // Leave the backend as found: stop the run this test started (Details -> Stop).
  await page.locator('[data-testid="details-toggle"]').click()
  await page.getByRole('button', { name: 'Stop', exact: true }).click()
  await expect(page.locator('[data-testid="network-state"]')).toContainText(/остановлен|останавливается/)
})
