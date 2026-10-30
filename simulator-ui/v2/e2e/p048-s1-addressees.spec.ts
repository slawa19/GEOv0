/**
 * Programme 048, slice S1 (T4810) - the three addressees of the demo screen, in the browser, at 1366x768, on the mocked
 * backend of the 037 specs (`helpers/p037Mock.ts`). Spec 048, Verification plan, reproducers (в), (г) and (д):
 *
 *  (в) a visitor (no admin token) with "Подробнее" on has no Analytics toggle and no run files ("Artifacts"). 048 S2
 *      removed "Admin ▾" altogether: the administrator's half of (в) - "Admin ▾ holds the Analytics toggle and the run
 *      files" - is gone with it (a removed contract); what each addressee has now is `p048-s2-edge-cases-out.spec.ts`;
 *  (г) turned over by 054-B-7a (the owner's decision of 2026-10-30: the system tries the clearing itself): there is no
 *      capacity-stall alert at any `consec_all_rejected_ticks` - the anti-vacuum: the first view is on screen;
 *  (д) the source switch carries `?debug=1` into the new address, and the offline source with it shows the playlist
 *      workbench (its absence without `debug`: `p042-t4205-playlist-workbench.spec.ts`, last test).
 *
 * Each was red before S1 (the record of the red runs: spec 048, Changelog). Elements are found by test id, not by their
 * words (the words are 046's).
 *
 * What this does NOT see: the real server (the mock answers as `run_lifecycle.py` does), the dock's polling (the unit
 * tier: `SimulatorAppRoot.interact.test.ts`), the stand's geometry and axe (`e2e/qa/`), a production build.
 */
import { expect, test, type Page, type Route } from '@playwright/test'

import { mockApp, ready, RUN_ID, SCENARIO_ID } from './helpers/p037Mock.js'

const DETAILS = '[data-testid="details-toggle"]'
const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
const runBody = (extra: Record<string, unknown> = {}) => ({
  api_version: 'simulator-api/1', run_id: RUN_ID, scenario_id: SCENARIO_ID, mode: 'real', state: 'running',
  sim_time_ms: 0, intensity_percent: 30, ops_sec: 0, queue_depth: 0, ...extra,
})

/**
 * The mocked app. `visitor`: the stored access token is a participant's JWT - not an admin token. (An EMPTY token is
 * not a visitor on this dev server: the dev build fills an empty token with its admin token on localhost,
 * `useSimulatorApp.ts`; the anonymous visitor without any token is the unit tier's - `TopBar.admin.p048.test.ts`.)
 */
async function open(page: Page, o: { visitor?: boolean; run?: Record<string, unknown>; query?: string } = {}): Promise<void> {
  await mockApp(page, { paymentRealBodies: [] })
  // Registered after `mockApp`: its init script clears the storage first, then this one writes the token.
  if (o.visitor) await page.addInitScript(() => { try { localStorage.setItem('geo.sim.v2.accessToken', 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJwIn0.c2lnbmF0dXJl') } catch { /* ignore */ } })
  if (o.run) await page.route(`**/simulator/runs/${RUN_ID}`, (r) => json(r, runBody(o.run)))
  await page.setViewportSize({ width: 1366, height: 768 })
  await page.goto(`/?mode=real&e2eReal=1${o.query ?? ''}`)
  await ready(page, true)
}

test.describe('048 S1 (в): a visitor has none of the administrator\'s tools', () => {
  test('a visitor with "Подробнее" on: no Admin, no Analytics toggle, no run files', async ({ page }) => {
    await open(page, { visitor: true })
    await page.locator(DETAILS).click()
    // Counter-check: the service rows are open and their controls are found by the same kind of query.
    await expect(page.locator('[data-testid="top-details-row"]')).toBeVisible()
    await expect(page.locator('[data-testid="layout-select"]')).toBeVisible()
    for (const id of ['admin-summary', 'analytics-toggle', 'admin-run-files']) {
      await expect(page.getByTestId(id), `${id}: not rendered for a visitor`).toHaveCount(0)
    }
    await expect(page.locator('[data-surface="real-metrics-panel"]'), 'no analytics dock').toHaveCount(0)
  })
})

test.describe('048 S1 (г), turned over by 054-B-7a: no capacity-stall alert', () => {
  test('no alert without a stall, and none after a stall of 3 ticks, with "Подробнее" off', async ({ page }) => {
    for (const ticks of [0, 3]) {
      await page.unrouteAll({ behavior: 'ignoreErrors' })
      await open(page, { run: { consec_all_rejected_ticks: ticks } })
      await expect(page.locator(DETAILS), `${ticks} ticks: the first view`).toHaveAttribute('aria-expanded', 'false')
      await expect(page.getByTestId('network-state'), `${ticks} ticks: the bar is on screen`).toBeVisible()
      await expect(page.getByTestId('capacity-stall'), `${ticks} ticks: no alert`).toHaveCount(0)
    }
  })
})

test.describe('048 S1 (д): ?debug=1 is the developer\'s entry and survives the source switch', () => {
  test('switching the source under ?debug=1 keeps debug=1; the offline page then has the playlist workbench', async ({ page }) => {
    await open(page, { query: '&debug=1' })
    await page.locator(DETAILS).click()
    const source = page.getByTestId('source-select')
    await expect(source, 'the developer\'s source switch').toBeVisible()
    await expect(page.getByTestId('sse-badge'), 'and the SSE badge').toBeVisible()
    await Promise.all([page.waitForURL(/mode=fixtures/), source.selectOption('fixtures')])
    const url = new URL(page.url())
    expect([url.searchParams.get('mode'), url.searchParams.get('debug')], 'the new address').toEqual(['fixtures', '1'])
    await expect(page.locator('[data-ready="1"]')).toBeVisible({ timeout: 20_000 })
    // "Подробнее" is remembered in this browser; open it if the reload did not keep it.
    if ((await page.locator(DETAILS).getAttribute('aria-expanded')) !== 'true') await page.locator(DETAILS).click()
    await expect(page.getByTestId('playlist-workbench'), 'the offline source with ?debug=1: the workbench').toBeVisible()
    await expect(page.getByTestId('source-select'), 'and the switch back').toBeVisible()
  })
})
