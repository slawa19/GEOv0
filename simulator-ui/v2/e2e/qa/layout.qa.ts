/**
 * 043 S1 - the first view of the demo and the one "Подробнее" toggle (spec 043, decisions 1 and 4; Verification plan,
 * reproducer (d) and the invariants of point 2). In the gate: what a person meets first is the product's promise.
 *
 *  (d)  a service surface is NOT on the first view (not rendered: not hidden by CSS, not in the tab order) and is on
 *       screen after a pointer click on "Подробнее"; the first view keeps the three protocol actions.
 *  keys Tab through the first view reaches only the controls of the first view, every protocol action among them.
 *  R9   the share of the 1366x768 viewport left to the canvas, from the DOM: the rectangles of the two bars against
 *       the viewport (and, beside it, the hit-tested share of 039 R9), with Details off and on, in both themes.
 *
 * LIMITS: the stand's ring of six and its backend mock (an admin token is set, so `Admin` exists); a reload is not
 * exercised here (`boot()` clears storage on every navigation; the remembered choice is the unit tier's,
 * `SimulatorAppRoot.interact.test.ts`); Chromium without a GPU; the onboarding hint is dismissed by "Подробнее" and is
 * measured, with the bars, only while Details is off.
 */
import { expect, test, type Page } from '@playwright/test'

import { boot, DETAILS_TOGGLE, ringSnapshot } from './stand.js'
import { THEMES, VIEWPORT_MAIN } from './surfaceMatrix.js'

const SERVICE: Array<[string, string]> = [
  ['mode segment', 'button.ds-segment'],
  ['theme switcher', 'select[aria-label="UI theme"]'],
  ['Admin menu', 'summary[aria-label="Admin"]'],
  ['run state badge', '[aria-label="Run state"]'],
  ['equivalent', '[aria-label="Equivalent"]'], // a select, or the code as text when the scene has one equivalent
  ['layout', 'select[aria-label="Layout"]'],
  ['quality', 'select[aria-label="Quality"]'],
  ['labels', 'select[aria-label="Labels"]'],
  ['analytics toggle', '[data-testid="bottombar-analytics-toggle"]'],
  ['Dev Tools menu', 'details[aria-label="Dev tools"] > summary'],
  ['Inspect graph', '.graph-navigator'],
  ['full metrics', '[aria-label="System metrics"]'],
]
const ACTIONS = ['actionbar-payment', 'actionbar-trustline', 'actionbar-clearing']

const counts = (page: Page) => page.evaluate((service) => service.map(([name, sel]) => [name, document.querySelectorAll(sel).length] as const), SERVICE)

test('043 S1 (d): every service surface is absent from the first view and on screen after a click on "Подробнее"', async ({ page }) => {
  await page.setViewportSize(VIEWPORT_MAIN)
  await boot(page, { snapshot: ringSnapshot(), clock: false, awaitLayout: false })
  // Anti-vacuum first: the counter reads the page - the three actions are found by the same kind of query.
  for (const id of ACTIONS) await expect(page.getByTestId(id), `${id} is on the first view`).toBeVisible()
  expect(Object.fromEntries(await counts(page)), 'not rendered on the first view').toEqual(Object.fromEntries(SERVICE.map(([n]) => [n, 0])))
  const toggle = page.locator(DETAILS_TOGGLE)
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')

  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
  for (const [name, sel] of SERVICE) await expect(page.locator(sel).first(), `${name} after "Подробнее"`).toBeVisible()

  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  expect(Object.fromEntries(await counts(page)), 'gone again when it is turned off').toEqual(Object.fromEntries(SERVICE.map(([n]) => [n, 0])))
})

test('043 S1: Tab through the first view reaches the first view\'s controls only - the three actions among them', async ({ page }) => {
  await page.setViewportSize(VIEWPORT_MAIN)
  await boot(page, { snapshot: ringSnapshot(), clock: false, awaitLayout: false })
  const seen: string[] = []
  for (let i = 0; i < 20; i += 1) {
    await page.keyboard.press('Tab')
    seen.push(await page.evaluate(() => {
      const a = document.activeElement as HTMLElement | null
      if (!a || a === document.body) return 'body'
      return a.dataset.testid ?? a.getAttribute('aria-label') ?? (a.textContent ?? '').replace(/\s+/g, ' ').trim()
    }))
  }
  const distinct = [...new Set(seen)]
  console.log('[043 S1 tab]', JSON.stringify(distinct))
  for (const id of ACTIONS) expect(distinct, `${id} is reachable by Tab`).toContain(id)
  expect(distinct, 'the toggle is reachable by Tab').toContain('details-toggle')
  const allowed = new Set([...ACTIONS, 'details-toggle', 'Close hint', 'Reset view', 'body'])
  expect(distinct.filter((d) => !allowed.has(d)), 'nothing else is in the tab order of the first view').toEqual([])
})

/** The bars' rectangles against the viewport, and the hit-tested share of 039 R9 (the element under a point is a canvas). */
async function canvasShare(page: Page) {
  return page.evaluate(() => {
    const vh = window.innerHeight
    const vw = window.innerWidth
    const top = document.querySelector('.ds-ov-top .ds-ov-top-stack')!.getBoundingClientRect()
    const bottom = [...document.querySelectorAll<HTMLElement>('.ds-ov-bottom')].find((el) => !el.classList.contains('sar-interact-history-overlay'))!.getBoundingClientRect()
    let total = 0
    let free = 0
    for (let y = 2; y < vh; y += 4) for (let x = 2; x < vw; x += 4) { total += 1; if (document.elementFromPoint(x, y) instanceof HTMLCanvasElement) free += 1 }
    const pct = (n: number) => Math.round(n * 1000) / 10
    return {
      topBottomPx: Math.round(top.bottom), bottomTopPx: Math.round(bottom.top), topH: Math.round(top.height), bottomH: Math.round(bottom.height),
      heightSharePct: pct((bottom.top - top.bottom) / vh),
      hitTestSharePct: pct(free / total),
    }
  })
}

for (const theme of THEMES) {
  test(`043 S1 R9 [${theme}]: the first view leaves the canvas at least 85 % of the height at 1366x768; with Details on it is measured`, async ({ page }, info) => {
    await page.setViewportSize(VIEWPORT_MAIN)
    await boot(page, { snapshot: ringSnapshot(), clock: false, awaitLayout: false, query: `&theme=${theme}` })
    await expect(page.locator('.root')).toHaveAttribute('data-theme', theme)
    const off = await canvasShare(page)
    await page.locator(DETAILS_TOGGLE).click()
    await expect(page.locator('[data-testid="top-details-row"]')).toBeVisible()
    await expect(page.locator('[data-testid="bottom-details-row"]')).toBeVisible()
    const on = await canvasShare(page)
    const row = { theme, viewport: '1366x768', off, on }
    console.log('[043 S1 R9]', JSON.stringify(row))
    info.annotations.push({ type: '043-S1-R9', description: JSON.stringify(row) })

    // Anti-vacuum: both bars were measured with their rows in them, and Details made them taller.
    expect(off.topH, 'the top bar has its first row').toBeGreaterThan(30)
    expect(off.bottomH, 'the bottom bar has its main row').toBeGreaterThan(20)
    expect(on.topH, 'Details adds to the top bar').toBeGreaterThan(off.topH)
    expect(on.bottomH, 'Details adds to the bottom bar').toBeGreaterThan(off.bottomH)

    expect(off.heightSharePct, 'Details off (the first view): the canvas share of the height').toBeGreaterThanOrEqual(85)
    // Details on: MEASURED, NOT ASSERTED (2026-10-16). Spec 043 (Verification plan 2) asks for 85 % in both states;
    // the approved mock-up (`mockup-s2.png`: two rows and a metrics row on top, a row of settings and tools above the
    // legend) gives about 69 % here, and no arrangement of those rows reaches 85 % at 1366x768 (four rows of 38-42 px
    // and the insets already exceed the 115 px the threshold leaves). A spec conflict for the orchestrator, recorded
    // with the number; NOT a threshold invented here.
    info.annotations.push({ type: '043-S1-R9-details-on', description: `measured ${on.heightSharePct} % (height), ${on.hitTestSharePct} % (hit-test); not asserted - spec conflict` })
  })
}
