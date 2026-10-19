/**
 * Programme 048, slice S2 (T4820) - "крайние случаи вон" (amendments 8-12; the owner's rule of 2026-10-19: edge cases
 * are not handled in the interface), in the browser at 1366x768 on the mocked backend of the 037 specs
 * (`helpers/p037Mock.ts`), a living run whose event stream stays open as the server's does:
 *
 *  - amendment 12: the whole of "Подробнее" for a visitor (a participant JWT) - top: the theme and the language; the
 *    network figures; bottom: the equivalent, the layout and "Inspect graph"; no run row, no payments badge, no
 *    "Admin ▾", no Labels, no analytics toggle. The administrator (the dev build's admin token): the same plus the row
 *    of the own run and the payments badge. Read as lists of what each part holds, by test id / aria-label, both
 *    addressees by the same function on the same page (anti-vacuum);
 *  - amendment 10: with `?debug=1` the analytics toggle is in the service row and opens the dock - for a visitor, with no
 *    token; without `?debug=1` the administrator has no toggle and no dock;
 *  - amendment 8, the path the spec names (`ActionBar.vue`: Start is hidden after a stop): a visitor whose run is stopped
 *    while the tab is open (here: by the operator's stop-all) has no Start anywhere - by the rule, no new control - and a
 *    reload is the way back: the opening's autostart creates a run.
 *
 * An absence is "not in the DOM" (`toHaveCount(0)`), never a style (048, Verification plan 4).
 * What this does NOT see: the real server, the words (046), the stand's geometry and axe (`e2e/qa/`), a production build.
 */
import { expect, test, type Page, type Route } from '@playwright/test'

import { mockApp, ready, RUN_ID, SCENARIO_ID } from './helpers/p037Mock.js'

const DETAILS = '[data-testid="details-toggle"]'
const JWT = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJwIn0.c2lnbmF0dXJl'
const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
const runBody = (extra: Record<string, unknown> = {}) => ({
  api_version: 'simulator-api/1', run_id: RUN_ID, scenario_id: SCENARIO_ID, mode: 'real', state: 'running',
  sim_time_ms: 0, intensity_percent: 30, ops_sec: 0, queue_depth: 0, ...extra,
})

type Opened = { creates: () => number; endStreams: () => Promise<void>; setRun: (extra: Record<string, unknown>) => void }

/**
 * The mocked app with a running run. `visitor`: the stored token is a participant JWT (an EMPTY token is the dev build's
 * admin token on localhost, `useSimulatorApp.ts`). The event stream is held open until `endStreams()`.
 */
async function open(page: Page, o: { visitor: boolean; query?: string }): Promise<Opened> {
  await mockApp(page, { paymentRealBodies: [] })
  if (o.visitor) await page.addInitScript((t) => { try { localStorage.setItem('geo.sim.v2.accessToken', t) } catch { /* ignore */ } }, JWT)
  let run = runBody()
  let creates = 0
  const held: Route[] = []
  await page.route(`**/simulator/runs/${RUN_ID}`, (r) => json(r, run))
  await page.route(/\/simulator\/runs$/i, (r) => {
    if (r.request().method() === 'POST') creates += 1
    return json(r, { run_id: RUN_ID })
  })
  await page.route(/\/simulator\/runs\/[^/]+\/events/i, (r) => { held.push(r) })
  await page.route(new RegExp(`/simulator/runs/${RUN_ID}/(metrics|bottlenecks)`, 'i'), (r) =>
    json(r, r.request().url().includes('/metrics')
      ? { api_version: 'simulator-api/1', run_id: RUN_ID, equivalent: 'UAH', from_ms: 0, to_ms: 0, step_ms: 1000, series: [] }
      : { api_version: 'simulator-api/1', run_id: RUN_ID, equivalent: 'UAH', items: [] }))
  await page.setViewportSize({ width: 1366, height: 768 })
  await page.goto(`/?mode=real&e2eReal=1${o.query ?? ''}`)
  await ready(page, true)
  return {
    creates: () => creates,
    setRun: (extra) => { run = runBody(extra) },
    endStreams: async () => {
      for (const r of held.splice(0)) {
        await r.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream; charset=utf-8', 'cache-control': 'no-cache' }, body: ':bye\n\n' }).catch(() => undefined)
      }
    },
  }
}

/** What "Подробнее" holds: each part as a list (a control by test id, aria-label or text; a passive item by test id). */
function contents(page: Page) {
  return page.evaluate(() => {
    const sel = 'select, button, input, summary, a[href], [role="button"], [tabindex]:not([tabindex="-1"]), [data-testid="stop-reason"], [data-testid="run-payments"], [data-testid="sse-badge"], [data-testid="equivalent-text"]'
    const list = (root: Element | null) => (root ? [...root.querySelectorAll<HTMLElement>(sel)] : [])
      .filter((el) => {
        const closed = el.closest('details:not([open])')
        return !closed || (el.tagName === 'SUMMARY' && el.parentElement === closed)
      })
      .map((el) => el.dataset.testid ?? el.getAttribute('aria-label') ?? (el.textContent ?? '').replace(/\s+/g, ' ').trim())
    return {
      top: list(document.querySelector('[data-testid="top-details-row"]')),
      figures: [...document.querySelectorAll<HTMLElement>('[data-testid="system-metrics"] [data-testid^="sbb-"]')].map((el) => el.dataset.testid ?? ''),
      bottom: list(document.querySelector('[data-testid="bottom-details-row"]')),
    }
  })
}

const VISITOR = {
  top: ['theme-select', 'language-select'],
  figures: ['sbb-available', 'sbb-trustlines', 'sbb-participants', 'sbb-utilization'],
  bottom: ['equivalent-text', 'layout-select', 'Inspect graph'],
}

test.describe('048 S2 (amendment 12): what "Подробнее" holds, by addressee', () => {
  test('a visitor: theme, language, the network figures, equivalent, layout, "Inspect graph" - nothing of the run, no menu', async ({ page }) => {
    await open(page, { visitor: true })
    await page.locator(DETAILS).click()
    await expect(page.locator('[data-testid="top-details-row"]')).toBeVisible()
    await expect(page.locator('[data-testid="system-metrics"]')).toBeVisible()
    expect(await contents(page)).toEqual(VISITOR)
    for (const id of ['own-run-row', 'scenario-select', 'run-payments', 'admin-summary', 'admin-menu', 'labels-select', 'analytics-toggle']) {
      await expect(page.getByTestId(id), `${id}: not rendered for a visitor`).toHaveCount(0)
    }
    const menus = await page.evaluate(() => [...document.querySelectorAll('.ds-ov-top details, .ds-ov-bottom details')].filter((d) => !d.closest('.graph-navigator')).length)
    expect(menus, 'no menu in the bars (the navigator\'s own is "Inspect graph")').toBe(0)
  })

  test('the administrator: the same plus the row of the own run and the payments badge; no "Admin ▾", no Labels', async ({ page }) => {
    await open(page, { visitor: false })
    await page.locator(DETAILS).click()
    await expect(page.getByTestId('own-run-row')).toBeVisible()
    expect(await contents(page)).toEqual({ ...VISITOR, top: [...VISITOR.top, 'scenario-select', 'Stop', 'Intensity percent', 'Apply', 'run-payments'] })
    for (const id of ['admin-summary', 'admin-menu', 'labels-select', 'analytics-toggle']) {
      await expect(page.getByTestId(id), `${id}: not rendered`).toHaveCount(0)
    }
  })
})

test.describe('048 S2 (amendment 10): the analytics dock is the developer\'s (?debug=1)', () => {
  test('a visitor with ?debug=1: the toggle is in the service row and opens the dock (no token asked)', async ({ page }) => {
    await open(page, { visitor: true, query: '&debug=1' })
    await page.locator(DETAILS).click()
    const toggle = page.getByTestId('analytics-toggle')
    await expect(toggle).toBeVisible()
    await expect(toggle).toHaveAttribute('aria-pressed', 'false')
    await expect(page.locator('[data-surface="real-metrics-panel"]')).toHaveCount(0)
    await toggle.click()
    await expect(page.locator('[data-surface="real-metrics-panel"]'), 'the toggle opens the dock').toBeVisible()
    await expect(toggle).toHaveAttribute('aria-pressed', 'true')
  })

  test('counter-check: the administrator without ?debug=1 has no toggle and no dock', async ({ page }) => {
    await open(page, { visitor: false })
    await page.locator(DETAILS).click()
    await expect(page.getByTestId('own-run-row'), 'the administrator\'s row is there (the token is read)').toBeVisible()
    await expect(page.getByTestId('analytics-toggle')).toHaveCount(0)
    await expect(page.locator('[data-surface="real-metrics-panel"]')).toHaveCount(0)
  })
})

test.describe('048 S2 (amendment 8): a visitor whose run was stopped has no Start - a reload is the way back', () => {
  test('stopped by the operator while the tab is open: the actions are disabled, no Start or Stop anywhere, the reason is text; a reload starts a run', async ({ page }) => {
    const app = await open(page, { visitor: true })
    await expect(page.getByTestId('network-state')).toHaveAttribute('data-run-state', 'running')
    expect(app.creates(), 'the run was found running: nothing created').toBe(0)

    app.setRun({ state: 'stopped', stop_source: 'admin', stop_reason: 'admin_stop_all' })
    await app.endStreams()
    await expect(page.getByTestId('network-state')).toHaveAttribute('data-run-state', 'stopped')
    for (const id of ['actionbar-payment', 'actionbar-trustline', 'actionbar-clearing']) await expect(page.getByTestId(id), id).toBeDisabled()
    await page.locator(DETAILS).click()
    await expect(page.getByTestId('stop-reason'), 'why it stopped - passive text').toBeVisible()
    await expect(page.getByTestId('own-run-row'), 'no run row: by the rule, no new control for this path').toHaveCount(0)
    expect(await contents(page), 'the visitor\'s "Подробнее" with the stop reason').toEqual({ ...VISITOR, top: [...VISITOR.top, 'stop-reason'] })

    await page.reload()
    await expect(page.locator('[data-ready="1"]')).toBeVisible({ timeout: 20_000 })
    await expect.poll(app.creates, { message: 'the opening\'s autostart replaces the stopped run on a reload' }).toBe(1)
  })
})
