/**
 * 048 S3 (T4830) - one form of the viewer's error (decision 3), in the browser, on the mocked app of 037:
 *
 *   1. a window's failure is the window's own block - one sentence, the request id a line of its own and
 *      `data-request-id` (AGENTS.md section 12), the identifiers (HTTP status, code) the title only; no error toast;
 *   2. the run's error line - the same form, no «Error» / «Ошибка» before the sentence, no «HTTP» drawn;
 *   3. a scene-load failure is shown in the overlay over the canvas, in the same form (that it is not ALSO copied to the run's
 *      line is held by the unit tests - see the bounded claim at the test);
 *   4. (044 closing review) a saved run whose status answers 403 - the anonymous cookie was lost - is cleared like a 404:
 *      a reload starts exactly one new run, and the network lives.
 *
 * Read structurally (test ids, attributes), in both languages where the words matter. What this does NOT see: the live
 * backend (`e2e/live/autostart.live.ts`), the look of the blocks (the owner's frames).
 */
import { expect, test, type Page, type Route } from '@playwright/test'

import { t } from '../src/i18n/t'
import { mockApp, ready, RUN_ID, SCENARIO_ID, snapshot } from './helpers/p037Mock.js'

const json = (route: Route, body: unknown, status = 200, headers: Record<string, string> = {}) =>
  route.fulfill({ status, contentType: 'application/json', headers, body: JSON.stringify(body) })
/** What a screen must never show of a failure (decision 3). */
const FORBIDDEN = /\bError\b|Ошибка|HTTP|Reference|ApiError|TypeError|\(ref:|req-/
type Lang = 'en' | 'ru'

async function openWith(page: Page, lang: Lang, extra: (page: Page) => Promise<void> = async () => {}): Promise<void> {
  await mockApp(page, { paymentRealBodies: [] })
  await extra(page)
  await page.setViewportSize({ width: 1366, height: 768 })
  await page.goto(`/?mode=real&e2eReal=1&lang=${lang}`)
}

/**
 * "Never there" over a short watch, not `toHaveCount(0)`: that assertion RETRIES until it holds, so a toast that shows and
 * expires within the expect timeout (4-8 s) would pass it - vacuous (found by the mutation "put the toast back").
 */
async function neverShown(page: Page, testid: string, ms = 1500): Promise<void> {
  const until = Date.now() + ms
  while (Date.now() < until) {
    expect(await page.getByTestId(testid).count(), `${testid} is never on screen`).toBe(0)
    await page.waitForTimeout(100)
  }
}

/** The block of a failure: its sentence, its id line, its attributes - read in one place. */
async function expectShown(page: Page, testid: string, lang: Lang, id: string, title: RegExp): Promise<void> {
  const block = page.getByTestId(testid)
  await expect(block).toBeVisible()
  await expect(block).toHaveAttribute('data-request-id', id)
  await expect(block.getByTestId('error-request-id')).toHaveText(t('common.requestId', { id }, lang))
  const sentence = block.getByTestId('error-text')
  await expect(sentence).not.toHaveText('')
  expect((await sentence.textContent()) ?? '', 'the sentence holds no id, no HTTP, no «Error», no class name').not.toMatch(FORBIDDEN)
  const titled = (await block.getAttribute('title')) ?? (await sentence.getAttribute('title')) ?? ''
  expect(titled, 'the identifiers are the title - not drawn').toMatch(title)
}

for (const lang of ['en', 'ru'] as Lang[]) {
  test.describe(`048 S3 [${lang}]: a window's failure is its own block - the request id apart, no toast`, () => {
    test('a payment refused with X-Request-ID', async ({ page }) => {
      await openWith(page, lang, async (p) => {
        await p.route(`**/simulator/runs/${RUN_ID}/actions/payment-real`, (r) =>
          json(r, { code: 'NO_ROUTE', message: 'No route found', details: { reason: 'no_route', max_available: '0' } }, 409, { 'X-Request-ID': 'req-s3-pay' }))
      })
      await ready(page, true)
      await page.locator('[data-testid="actionbar-payment"]').click()
      await page.locator('#mp-from__trigger').click()
      await page.locator('#mp-from__surface [role="option"][data-option-value="alice"]').click()
      await page.locator('#mp-to__surface [role="option"][data-option-value="bob"]').click()
      await page.locator('#mp-amount').fill('1.00')
      await page.locator('[data-testid="manual-payment-confirm"]').click()
      await expectShown(page, 'manual-payment-error', lang, 'req-s3-pay', /^HTTP 409 NO_ROUTE$/)
      await expect(page.getByTestId('manual-payment-error').getByTestId('error-text')).toHaveText(t('refusal.noRouteMax', { max: '0.00', eq: 'UAH' }, lang))
      await neverShown(page, 'error-toast')
    })

    test('a clearing refused with X-Request-ID', async ({ page }) => {
      await openWith(page, lang, async (p) => {
        await p.route(`**/simulator/runs/${RUN_ID}/actions/clearing-real`, (r) =>
          json(r, { code: 'CLEARING_FAILED', message: 'Clearing failed' }, 409, { 'X-Request-ID': 'req-s3-clr' }))
      })
      await ready(page, true)
      await page.locator('[data-testid="actionbar-clearing"]').click()
      await page.locator('[data-testid="clearing-confirm"]').click()
      await expectShown(page, 'clearing-error', lang, 'req-s3-clr', /^HTTP 409 CLEARING_FAILED$/)
      await neverShown(page, 'error-toast')
    })
  })

  test.describe(`048 S3 [${lang}]: the run's error line and the scene's failure`, () => {
    test('a failed status read: one sentence, no «Error» before it, the id on its own line, the status in the title', async ({ page }) => {
      await openWith(page, lang, async (p) => {
        await p.route(`**/simulator/runs/${RUN_ID}`, (r) => json(r, { error: { code: 'E000', message: 'boom' } }, 500, { 'X-Request-ID': 'req-s3-run' }))
      })
      const line = page.getByTestId('top-error')
      await expect(line).toBeVisible({ timeout: 20_000 })
      await expectShown(page, 'top-error', lang, 'req-s3-run', /^HTTP 500$/)
      await expect(line.getByTestId('error-text')).toHaveText(t('failure.serverFailed', {}, lang))
      await expect(line.locator('.ds-label'), 'no «Error» label - not in the DOM').toHaveCount(0)
    })

    test('a scene that cannot be loaded: the overlay is its one place - the run line carries no copy', async ({ page }) => {
      await openWith(page, lang, async (p) => {
        // The run's snapshot AND the scenario preview it falls back to (034 S8 D2) both fail.
        await p.route(/\/graph\/(snapshot|preview)/i, (r) =>
          json(r, { error: { code: 'E000', message: 'no scene' } }, 503, { 'X-Request-ID': 'req-s3-scene' }))
      })
      const overlay = page.getByTestId('scene-error')
      await expect(overlay).toBeVisible({ timeout: 20_000 })
      await expect(overlay).toHaveAttribute('role', 'alert')
      await expect(overlay).toHaveAttribute('data-request-id', 'req-s3-scene')
      await expect(overlay.getByTestId('error-text')).toHaveText(t('failure.serverFailed', {}, lang))
      await expect(overlay.getByTestId('error-request-id')).toHaveText(t('common.requestId', { id: 'req-s3-scene' }, lang))
      expect((await overlay.textContent()) ?? '').not.toMatch(/\bError\b|Ошибка|HTTP/)
      // The run line does not repeat it. Bounded claim (mutation M6b, 2026-10-19): on this mock a status read rewrites the line
      // right after, so a copy written back would be overwritten here as well - the browser cannot tell; "no copy" is held
      // by `useSimulatorApp.sceneLoadFailure.p034.test.ts` (red under the copy). These two lines only guard the end state.
      await expect(page.locator('[data-testid="top-error"][data-request-id="req-s3-scene"]')).toHaveCount(0)
      await expect(page.getByTestId('top-error')).toHaveCount(0)
    })
  })
}

/**
 * 044 closing review (advisor 2026-10-19): the anonymous cookie is lost or expired, the browser still keeps
 * `geo.sim.v2.runId`, its status answers 403. Red before: only 404 cleared the saved id, the opening kept an id with no
 * status and the autostart answered `unknown` - nothing started; the visitor was stuck until the storage was cleared.
 */
test.describe('048 S3: a saved run of another session (403) - a reload starts exactly one new run', () => {
  test('the visitor (participant JWT) opens with a saved run that answers 403: one POST /runs, the network lives', async ({ page }) => {
    const NEW_RUN_ID = 'run-p048s3-new'
    const JWT = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJwIn0.c2lnbmF0dXJl'
    let creates = 0
    const held: Route[] = []
    await mockApp(page, { paymentRealBodies: [] })
    await page.addInitScript((tok) => { try { localStorage.setItem('geo.sim.v2.accessToken', tok) } catch { /* ignore */ } }, JWT)
    await page.route(`**/simulator/runs/${RUN_ID}`, (r) => json(r, { error: { code: 'E403', message: 'Forbidden' } }, 403))
    await page.route(/\/simulator\/runs$/i, (r) => {
      if (r.request().method() !== 'POST') return json(r, { run_id: null })
      creates += 1
      return json(r, { run_id: NEW_RUN_ID })
    })
    const live = {
      api_version: 'simulator-api/1', run_id: NEW_RUN_ID, scenario_id: SCENARIO_ID, mode: 'real', state: 'running',
      sim_time_ms: 0, intensity_percent: 15, ops_sec: 0, queue_depth: 0,
    }
    await page.route(`**/simulator/runs/${NEW_RUN_ID}`, (r) => json(r, live))
    await page.route(new RegExp(`/simulator/runs/${NEW_RUN_ID}/graph/snapshot`, 'i'), (r) => json(r, snapshot()))
    // The new run's stream is held open, as the server holds it (a stream that ends reads as "no connection").
    await page.route(new RegExp(`/simulator/runs/${NEW_RUN_ID}/events`, 'i'), (r) => { held.push(r) })
    await page.setViewportSize({ width: 1366, height: 768 })
    await page.goto('/?mode=real&e2eReal=1')
    try {
      await expect.poll(() => creates, { message: 'the opening\'s autostart starts a new run', timeout: 20_000 }).toBe(1)
      await expect(page.getByTestId('network-state')).toHaveAttribute('data-run-state', 'running')
      await expect(page.getByTestId('network-state')).toHaveAttribute('data-network-state', 'running')
      for (const id of ['actionbar-payment', 'actionbar-trustline', 'actionbar-clearing']) await expect(page.getByTestId(id), id).toBeEnabled()
      expect(await page.evaluate(() => localStorage.getItem('geo.sim.v2.runId')), 'the saved id is the new run').toBe(NEW_RUN_ID)
      await expect(page.getByTestId('top-error'), 'the 403 of the foreign run is not left on screen').toHaveCount(0)
      expect(creates, 'exactly one').toBe(1)
    } finally {
      for (const r of held.splice(0)) await r.abort().catch(() => undefined)
    }
  })
})
