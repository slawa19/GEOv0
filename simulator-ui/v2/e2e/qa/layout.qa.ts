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
import { THEMES, VIEWPORT_MAIN, VIEWPORT_RESIZED } from './surfaceMatrix.js'

// 048 S1 (decisions 1, 4 and 5; Verification plan, forbidden item 4 - by test id, not by the words 046 translates):
// what "Подробнее" shows the stand's viewer (an admin token is set). Gone from this list: the "Run <state>" badge
// (removed), the source select and the quality (`?debug=1` only - DEVELOPER below), the analytics toggle (inside the
// "Admin ▾" menu: in the DOM, not on screen until the menu is opened - `surfaceMatrix.ts`, entry `analytics-panel`).
const SERVICE: Array<[string, string]> = [
  ['theme switcher', '[data-testid="theme-select"]'],
  ['Admin menu', '[data-testid="admin-summary"]'],
  ['equivalent', '[data-testid="equivalent-select"], [data-testid="equivalent-text"]'], // a select, or the code as text
  ['layout', '[data-testid="layout-select"]'],
  ['labels', '[data-testid="labels-select"]'],
  ['Inspect graph', '.graph-navigator'],
  ['full metrics', '[data-testid="system-metrics"]'],
]
/** 048 S1 (decision 5): the developer's controls - never on screen without `?debug=1`, not even with "Подробнее". */
const DEVELOPER: Array<[string, string]> = [
  ['source select', '[data-testid="source-select"]'],
  ['quality', '[data-testid="quality-select"]'],
  ['SSE badge', '[data-testid="sse-badge"]'],
  ['playlist workbench', '[data-testid="playlist-workbench"]'],
]
const ACTIONS = ['actionbar-payment', 'actionbar-trustline', 'actionbar-clearing']

const counts = (page: Page, list = SERVICE) => page.evaluate((service) => service.map(([name, sel]) => [name, document.querySelectorAll(sel).length] as const), list)

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
  // 048 S1: and the developer's controls are not rendered for this viewer at all (no `?debug=1`).
  expect(Object.fromEntries(await counts(page, DEVELOPER)), 'not rendered without ?debug=1').toEqual(Object.fromEntries(DEVELOPER.map(([n]) => [n, 0])))

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
  // 043 S2: "Поток" is a control of the first view (the stand's run is paused, so it reads ▶).
  expect(distinct, 'the flow button is reachable by Tab').toContain('flow-toggle')
  const allowed = new Set([...ACTIONS, 'flow-toggle', 'details-toggle', 'Close hint', 'Reset view', 'body'])
  expect(distinct.filter((d) => d !== 'body').length, 'at most seven controls on the first view (decision 1)').toBeLessThanOrEqual(7)
  expect(distinct.filter((d) => !allowed.has(d)), 'nothing else is in the tab order of the first view').toEqual([])
})

/**
 * 048 S1 (the owner's frame, 2026-10-19): "Inspect graph" in the bottom service row must not lie over another control and
 * must stay at the right edge of that row, also when the row wraps - with `?debug=1` (more controls; on the offline
 * source the playlist workbench) it did: the button, anchored absolutely, overlapped the playlist ("Шаг 0/20"). Read
 * from the DOM: the rectangle of its summary against every other interactive element of the row, and its right edge
 * against the row's.
 */
async function navigatorPlace(page: Page) {
  return page.evaluate(() => {
    const row = document.querySelector('[data-testid="bottom-details-row"]')!
    // The visible button is the navigator's panel (its frame), the summary inside it is the hit target.
    const nav = row.querySelector('.graph-navigator')!.getBoundingClientRect()
    const rr = row.getBoundingClientRect()
    const name = (el: Element) => `${el.tagName.toLowerCase()} "${(el.getAttribute('aria-label') ?? el.textContent ?? '').trim().slice(0, 24)}"`
    const CLEAR = 4
    const overlaps: string[] = []
    let controls = 0
    // Controls, and the texts that read them (the playlist's position "0 / 20" is what the owner saw covered).
    for (const el of row.querySelectorAll('button, select, input, summary, a[href], .ds-value, .ds-label')) {
      if (el.closest('.graph-navigator')) continue
      const r = el.getBoundingClientRect()
      if (r.width === 0 || r.height === 0) continue
      controls += 1
      // A clearance of CLEAR px around the button counts: the row's own gap is 8 px, and a frame decoration (the `hud`
      // corners) touching the next item read as "on top of it" on the owner's frame although the boxes met at 1 px.
      const ix = Math.min(r.right, nav.right + CLEAR) - Math.max(r.left, nav.left - CLEAR)
      const iy = Math.min(r.bottom, nav.bottom) - Math.max(r.top, nav.top)
      if (ix > 0.5 && iy > 0.5) overlaps.push(`${name(el)} ${Math.round(ix - CLEAR)}x${Math.round(iy)}`)
    }
    const pad = parseFloat(getComputedStyle(row).paddingRight) || 0
    return { overlaps, controls, rightGap: Math.round(rr.right - pad - nav.right), nav: [Math.round(nav.left), Math.round(nav.top), Math.round(nav.width)] }
  })
}

async function expectNavigatorPlaced(page: Page, what: string) {
  for (const vp of [VIEWPORT_MAIN, VIEWPORT_RESIZED]) {
    await page.setViewportSize(vp)
    await page.waitForTimeout(50)
    const p = await navigatorPlace(page)
    console.log('[048 S1 navigator]', what, `${vp.width}x${vp.height}`, JSON.stringify(p))
    expect(p.controls, `${what} ${vp.width}x${vp.height}: anti-vacuum - the row has other controls to compare with`).toBeGreaterThanOrEqual(4)
    expect.soft(p.overlaps, `${what} ${vp.width}x${vp.height}: "Inspect graph" lies over no other control or text of the row, nor within 4 px of one`).toEqual([])
    expect.soft(Math.abs(p.rightGap), `${what} ${vp.width}x${vp.height}: "Inspect graph" stays at the right edge of its row (gap ${p.rightGap} px)`).toBeLessThanOrEqual(2)
  }
  await page.setViewportSize(VIEWPORT_MAIN)
}

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

    // 048 S1 (the owner's frame, 2026-10-19): "Inspect graph" keeps its place - the visitor's row, then the developer's
    // (`?debug=1`) on the offline source, where the playlist workbench makes the row wrap. 1366x768 and 1024x640.
    await expectNavigatorPlaced(page, `[${theme}] visitor`)
    await page.goto(`/?mode=fixtures&debug=1&theme=${theme}`)
    await expect(page.locator('[data-ready="1"]')).toBeVisible({ timeout: 20_000 })
    if ((await page.locator(DETAILS_TOGGLE).getAttribute('aria-expanded')) !== 'true') await page.locator(DETAILS_TOGGLE).click()
    await expect(page.locator('[data-testid="playlist-workbench"]'), 'anti-vacuum: the developer\'s row has the playlist').toBeVisible()
    await expectNavigatorPlaced(page, `[${theme}] ?debug=1 offline`)
    // Details on: MEASURED, NOT ASSERTED (2026-10-16). Spec 043 (Verification plan 2) asks for 85 % in both states;
    // the approved mock-up (`mockup-s2.png`: two rows and a metrics row on top, a row of settings and tools above the
    // legend) gives about 69 % here, and no arrangement of those rows reaches 85 % at 1366x768 (four rows of 38-42 px
    // and the insets already exceed the 115 px the threshold leaves). A spec conflict for the orchestrator, recorded
    // with the number; NOT a threshold invented here.
    info.annotations.push({ type: '043-S1-R9-details-on', description: `measured ${on.heightSharePct} % (height), ${on.hitTestSharePct} % (hit-test); not asserted - spec conflict` })
  })
}
