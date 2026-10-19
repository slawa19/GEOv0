/**
 * Programme 048, slice S1 (T4810) - the three addressees of the demo screen, in the browser, at 1366x768, on the mocked
 * backend of the 037 specs (`helpers/p037Mock.ts`). Spec 048, Verification plan, reproducers (в), (г) and (д):
 *
 *  (в) a visitor (no admin token) with "Подробнее" on has no Analytics toggle and no run files ("Artifacts"); the
 *      administrator (the dev build's admin token) has both, inside "Admin ▾";
 *  (г) the capacity-stall alert is on the first view ("Подробнее" off) once `consec_all_rejected_ticks` reaches the
 *      threshold - and, first, the anti-vacuum: no alert without a stall;
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
  await page.route('**/simulator/admin/runs', (r) => json(r, { items: [] }))
  await page.route(`**/simulator/runs/${RUN_ID}/artifacts`, (r) => json(r, { items: [{ name: 'events.ndjson' }] }))
  await page.setViewportSize({ width: 1366, height: 768 })
  await page.goto(`/?mode=real&e2eReal=1${o.query ?? ''}`)
  await ready(page, true)
}

test.describe('048 S1 (в): the administrator\'s tools are in "Admin ▾", a visitor has none', () => {
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

  test('the administrator: "Admin ▾" holds the Analytics toggle (it opens the dock) and the run files (they load)', async ({ page }) => {
    await open(page)
    await page.locator(DETAILS).click()
    const menu = page.locator('[data-testid="admin-menu"] > .ds-ov-dropdown')
    await expect(menu, 'closed until asked').toBeHidden()
    await page.getByTestId('admin-summary').click()
    await expect(menu).toBeVisible()
    await expect(menu.getByTestId('analytics-toggle')).toBeVisible()
    // The owner's word (2026-10-19): no refresh of the scenario catalogue in the UI - the tools row is the toggle alone.
    await expect(menu.locator('[data-testid="admin-tools"] button')).toHaveCount(1)
    const files = menu.getByTestId('admin-run-files')
    await expect(files).toBeVisible()
    await expect(files.getByTestId('admin-run-files-count'), 'no number before the index is read').toHaveCount(0)
    await files.getByRole('button', { name: 'Refresh artifacts' }).click()
    await expect(files.getByTestId('admin-run-files-count')).toHaveText('1')
    await expect(files.locator('button', { hasText: 'events.ndjson' })).toBeVisible()

    await menu.getByTestId('analytics-toggle').click()
    await expect(page.locator('[data-surface="real-metrics-panel"]'), 'the toggle opens the dock').toBeVisible()
    await expect(menu.getByTestId('analytics-toggle')).toHaveAttribute('aria-pressed', 'true')
  })
})

test.describe('048 S1 (г): the capacity stall is on the first view', () => {
  test('no stall: no alert (anti-vacuum); a stall of 3 ticks: the alert, with "Подробнее" off', async ({ page }) => {
    await open(page, { run: { consec_all_rejected_ticks: 0 } })
    await expect(page.locator(DETAILS)).toHaveAttribute('aria-expanded', 'false')
    await expect(page.getByTestId('capacity-stall'), 'no stall, no alert').toHaveCount(0)

    await page.unrouteAll({ behavior: 'ignoreErrors' })
    await open(page, { run: { consec_all_rejected_ticks: 3 } })
    await expect(page.locator(DETAILS), 'the first view').toHaveAttribute('aria-expanded', 'false')
    await expect(page.getByTestId('capacity-stall')).toBeVisible()
    // It is in the top stack, above the canvas, and not a control: the first view keeps its stops (043: <= 7).
    await expect(page.locator('.ds-ov-top [data-testid="capacity-stall"]')).toHaveCount(1)
    await expect(page.getByTestId('capacity-stall').locator('button, a, input, select, [tabindex]')).toHaveCount(0)
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
