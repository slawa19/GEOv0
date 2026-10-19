/**
 * 043 S2 - one screen instead of three modes (spec 043, decisions 2 and 3; Verification plan, reproducers (b), (c), (d)).
 *
 *   (b) the first visit - clean storage, no `?mode` and no `?ui` - starts a live run by itself (the existing `startRun`,
 *       intensity 30, not paused) and a payment of that run is drawn, without a click on Start;
 *   (c) a manual action pauses the run (`/pause`), the state reads "на паузе", and only "Поток" brings it back
 *       (`/resume`); choosing a participant pauses it as well; a run somebody else paused is not resumed at opening;
 *   (d) entering the manual flow is zero navigations of the document.
 *
 * The backend is the stand's (`mockLiveRun`: a run whose state follows the requests). WHAT THIS DOES NOT SEE: that a
 * real backend emits payments after the create - the required `ui-smoke` job proves that on a seeded server
 * (`e2e/live/autostart.live.ts`, config `e2e/playwright.live.config.ts`); the payment here is a frame the test writes into the product's own SSE pipeline.
 */
import { expect, test, type Page } from '@playwright/test'

import { t } from '../../src/i18n/t'
import { expectNetworkState } from '../helpers/networkState.js'
import { RING, boot, fxCounts, mockLiveRun, pushEvent, ringSnapshot, screenNodes, txUpdated, type LiveRunLog } from './stand.js'

const STATE = '[data-testid="network-state"]'
const FLOW = '[data-testid="flow-toggle"]'
const SEND = '[data-testid="actionbar-payment"]'

/** A first visit with a live run behind the stand; returns the log of what the client asked the run. */
async function firstVisit(page: Page, initial?: 'paused'): Promise<LiveRunLog> {
  let log: LiveRunLog | null = null
  await boot(page, {
    snapshot: ringSnapshot(), clock: false, firstVisit: true, awaitScene: false,
    beforeLoad: async (p) => { log = await mockLiveRun(p, initial ? { initial } : {}) },
  })
  await expect(page.locator('[data-ready="1"]')).toBeVisible({ timeout: 30_000 })
  return log!
}

test('(b) the first visit starts a live run by itself: intensity 30, not paused, and its payment is drawn', async ({ page }) => {
  const log = await firstVisit(page)
  await expect.poll(() => log.calls, { timeout: 15_000, message: 'a run is created without a click on Start' }).toContain('POST /runs')
  expect(log.createBodies[0], 'the create asks for the default intensity').toMatchObject({ intensity_percent: 30 })
  // 046 wave 1, full (2026-10-19): the state by its attribute AND its word in the page's language (`expectNetworkState`,
  // §15 Codex round: the attribute alone let "paused" pass under the running word).
  await expectNetworkState(page, 'running', { timeout: 15_000, message: 'the state of the network reads as living' })
  await expect
    .poll(() => page.evaluate(() => (window as unknown as { __qaSse: { state: { controllers: unknown[] } } }).__qaSse.state.controllers.length), { timeout: 15_000 })
    .toBeGreaterThan(0)
  expect(log.calls, 'nothing paused the run at opening').not.toContain('POST /pause')

  await pushEvent(page, txUpdated([RING[0]!, RING[1]!, RING[2]!]))
  await expect.poll(() => fxCounts(page).then((c) => c.sparks), { timeout: 5_000, message: 'the beam of the payment is on the canvas' }).toBeGreaterThan(0)
})

test('(c) "Send Payment" pauses the run until "Поток": cancel does not resume it, the button does', async ({ page }) => {
  const log = await firstVisit(page)
  await expectNetworkState(page, 'running', { timeout: 15_000 })
  await expect(page.locator(FLOW), 'the flow button offers a pause while the run lives').toHaveAttribute('aria-pressed', 'true')

  await page.locator(SEND).click()
  await expect(page.locator('[data-testid="manual-payment-panel"]')).toBeVisible()
  await expect.poll(() => log.calls, { timeout: 5_000 }).toContain('POST /pause')
  await expectNetworkState(page, 'paused')

  // Cancel: the panel goes, the pause stays (decision 3 names only the button as the way back).
  await page.keyboard.press('Escape')
  await expect(page.locator('[data-testid="manual-payment-panel"]')).toBeHidden()
  await expectNetworkState(page, 'paused')
  expect(log.calls, 'nothing resumed the run on cancel').not.toContain('POST /resume')

  await expect(page.locator(FLOW)).toHaveAttribute('aria-pressed', 'false')
  await page.locator(FLOW).click()
  await expect.poll(() => log.calls, { timeout: 5_000 }).toContain('POST /resume')
  await expectNetworkState(page, 'running')
})

test('(c) choosing a participant in the living flow pauses it', async ({ page }) => {
  const log = await firstVisit(page)
  await expectNetworkState(page, 'running', { timeout: 15_000 })
  const nodes = await screenNodes(page)
  const at = nodes.get(RING[0]!)!
  await page.mouse.click(at.x, at.y)
  await expect.poll(() => log.calls, { timeout: 5_000, message: 'a click on a participant asks for a pause' }).toContain('POST /pause')
  await expectNetworkState(page, 'paused')
})

test('(c) a run somebody paused is attached and left paused: no create, no resume', async ({ page }) => {
  let log: LiveRunLog | null = null
  // A returning visitor: the stand seeds the stored run, which the backend says is paused.
  await boot(page, { snapshot: ringSnapshot(), clock: false, beforeLoad: async (p) => { log = await mockLiveRun(p, { initial: 'paused' }) } })
  await expectNetworkState(page, 'paused')
  // The opening is over: the stream is connected, and the product decides on a start BEFORE it connects (the decision
  // is taken when the scene is up; the stream follows the run's snapshot). A start or a resume would be in the log by now.
  await expect
    .poll(() => page.evaluate(() => (window as unknown as { __qaSse: { state: { controllers: unknown[] } } }).__qaSse.state.controllers.length), { timeout: 15_000 })
    .toBeGreaterThan(0)
  expect(log!.calls, 'opening neither created nor resumed a run').toEqual([])
})

test('(d) entering the manual flow is zero navigations of the document', async ({ page }) => {
  await firstVisit(page)
  await page.evaluate(() => { (window as unknown as { __s2Marker?: number }).__s2Marker = 1 })
  let navigations = 0
  page.on('framenavigated', (f) => { if (f === page.mainFrame()) navigations += 1 })

  await expect(page.locator(SEND), 'the protocol actions are on the screen a first visit opens').toBeVisible({ timeout: 15_000 })
  await page.locator(SEND).click()
  await expect(page.locator('[data-testid="manual-payment-panel"]')).toBeVisible()

  expect(navigations, 'no document navigation on the way into the flow').toBe(0)
  expect(await page.evaluate(() => (window as unknown as { __s2Marker?: number }).__s2Marker), 'the same document').toBe(1)
})

// 043 S2 review round (Gemini B3/B4, 2026-10-17): the page's own language and a start the server refuses.
test('(b) a start refused by the global limit of runs: a Russian line from the real page language, and no action without a run', async ({ page }) => {
  let creates = 0
  await boot(page, {
    // 046 wave 0: the language is chosen explicitly (`?lang=ru`), not left to the browser's.
    snapshot: ringSnapshot(), clock: false, firstVisit: true, awaitScene: false, query: 'lang=ru',
    beforeLoad: async (p) => {
      await p.route(/\/simulator\/runs$/i, (r) => {
        if (r.request().method() !== 'POST') return r.fallback()
        creates += 1
        return r.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({
          error: { code: 'E008', message: 'Global active runs limit reached', details: { conflict_kind: 'global_active_limit', max_active_runs: 1, active_runs: 1 } },
        }) })
      })
    },
  })
  await expect(page.locator('[data-ready="1"]')).toBeVisible({ timeout: 30_000 })
  expect(await page.evaluate(() => document.documentElement.lang), 'the language the page declares (?lang=ru, written by i18n/locale.ts)').toBe('ru')
  // 046 wave 1: the line's text and label come from the dictionary (the server's number of places: 1 here), and the
  // line offers "Повторить"; it is found by its test id (its accessible name is in the interface language).
  const line = page.locator('[role="alert"][data-testid="top-error"]')
  await expect(line, 'the refusal in the interface language').toContainText(t('runStart.limitOne', undefined, 'ru'), { timeout: 15_000 })
  await expect(line).toHaveAttribute('aria-label', t('topBar.errorAria', undefined, 'ru'))
  await expect(line.getByTestId('start-retry')).toHaveText(t('topBar.retry', undefined, 'ru'))
  expect(creates, 'one attempt').toBe(1)
  // No run: the three actions are there and disabled (B4), so no raw "No active run_id" can be reached.
  for (const id of ['actionbar-payment', 'actionbar-trustline', 'actionbar-clearing']) await expect(page.getByTestId(id), `${id} without a run`).toBeDisabled()
})

// 043 S2, the owner's decision on review item B2 (2026-10-17): the payment window opens in the right rail under the top
// bar (mockup-s3), not over the centre of the graph; the action row does not move when a flow locks it.
test('B2: Send Payment opens its window in the right rail under the top bar; the action row does not shift; focus goes into the window', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 })
  await firstVisit(page)
  await expectNetworkState(page, 'running', { timeout: 15_000 })
  const xs = () => page.evaluate(() => ['actionbar-payment', 'actionbar-trustline', 'actionbar-clearing']
    .map((id) => Math.round(document.querySelector(`[data-testid="${id}"]`)!.getBoundingClientRect().left)))
  const before = await xs()
  await page.locator(SEND).click()
  const panel = page.locator('[data-testid="manual-payment-panel"]')
  await expect(panel).toBeVisible()
  await expect.poll(() => panel.evaluate((el) => (el.closest('.ws-shell') ?? el).contains(document.activeElement)), { message: 'focus is inside the window' }).toBe(true)
  const geo = await panel.evaluate((el) => {
    const shell = (el.closest('.ws-shell') ?? el).getBoundingClientRect()
    const top = document.querySelector('.ds-ov-top .ds-ov-top-stack')!.getBoundingClientRect()
    return { left: shell.left, right: shell.right, top: shell.top, vw: window.innerWidth, stackBottom: top.bottom }
  })
  console.log('[043 S2 B2]', JSON.stringify({ before, geo }))
  expect(geo.right, 'docked to the right edge').toBeGreaterThan(geo.vw - 40)
  expect(geo.left, 'not over the centre of the graph').toBeGreaterThan(geo.vw / 2)
  expect(geo.top, 'under the top bar').toBeGreaterThanOrEqual(geo.stackBottom - 1)
  const after = await xs()
  console.log('[043 S2 B2 after]', JSON.stringify(after))
  expect(after, 'the three actions did not move').toEqual(before)
})

// --- 043 S2 fix-delta (Codex, 2026-10-17): the last three residuals of the pause and of the right rail. ---

test('fix-delta 1: the SAME participant clicked again after "Поток" pauses again', async ({ page }) => {
  const log = await firstVisit(page)
  await expectNetworkState(page, 'running', { timeout: 15_000 })
  const at = (await screenNodes(page)).get(RING[0]!)!
  await page.mouse.click(at.x, at.y)
  await expect.poll(() => log.calls.filter((c) => c === 'POST /pause').length, { timeout: 5_000 }).toBe(1)
  await page.locator(FLOW).click()
  await expectNetworkState(page, 'running')
  await page.mouse.click(at.x, at.y)
  await expect.poll(() => log.calls.filter((c) => c === 'POST /pause').length, { timeout: 5_000, message: 'the second click on the same participant paused' }).toBe(2)
})

test('fix-delta 2a: a deep link `?focus=` on a running stored run selects the participant and pauses nothing', async ({ page }) => {
  let log: LiveRunLog | null = null
  await boot(page, { snapshot: ringSnapshot(), clock: false, query: `&focus=${RING[1]!}`, beforeLoad: async (p) => { log = await mockLiveRun(p, { initial: 'running' }) } })
  await expectNetworkState(page, 'running', { timeout: 15_000 })
  await expect.poll(() => page.evaluate(() => document.querySelectorAll('.node-label-inner').length), { message: 'precondition: the deep link selected a participant (its labels are shown)' }).toBeGreaterThan(0)
  await expect
    .poll(() => page.evaluate(() => (window as unknown as { __qaSse: { state: { controllers: unknown[] } } }).__qaSse.state.controllers.length), { timeout: 15_000 })
    .toBeGreaterThan(0)
  expect(log!.calls, 'no pause without an act of the person').not.toContain('POST /pause')
})

test('fix-delta 2b: a deep link `?focus=` with no run - the opening starts its run unpaused', async ({ page }) => {
  let log: LiveRunLog | null = null
  await boot(page, { snapshot: ringSnapshot(), clock: false, firstVisit: true, awaitScene: false, query: `&focus=${RING[1]!}`,
    beforeLoad: async (p) => { log = await mockLiveRun(p) } })
  await expect(page.locator('[data-ready="1"]')).toBeVisible({ timeout: 30_000 })
  await expect.poll(() => log!.calls, { timeout: 15_000 }).toContain('POST /runs')
  await expectNetworkState(page, 'running', { timeout: 15_000 })
  expect(log!.calls, 'not started paused').not.toContain('POST /pause')
})

test('fix-delta 3: a right-rail window stays under the top stack when "Подробнее" opens after it', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 })
  await firstVisit(page)
  await expectNetworkState(page, 'running', { timeout: 15_000 })
  await page.locator(SEND).click()
  const panel = page.locator('[data-testid="manual-payment-panel"]')
  await expect(panel).toBeVisible()
  await page.locator('[data-testid="details-toggle"]').click()
  await expect(page.locator('[data-testid="top-details-row"]')).toBeVisible()
  const geo = () => panel.evaluate((el) => {
    const shell = (el.closest('.ws-shell') ?? el).getBoundingClientRect()
    const stack = document.querySelector('.ds-ov-top .ds-ov-top-stack')!.getBoundingClientRect()
    return { top: Math.round(shell.top), bottom: Math.round(shell.bottom), stackBottom: Math.round(stack.bottom), vh: window.innerHeight }
  })
  await expect.poll(async () => { const g = await geo(); return g.top >= g.stackBottom && g.bottom <= g.vh }, { timeout: 5_000, message: 'the window is under the expanded stack and inside the viewport' }).toBe(true)
  console.log('[043 S2 fix-delta 3]', JSON.stringify(await geo()))
})

// 043 S2, adversarial pass 1-A (2026-10-17): a drag of a participant is an explicit act and pauses (the click the browser
// fires after a drag is suppressed, so the canvas click entry never runs for it).
test('adversarial 1-A: dragging a participant in the living flow pauses it', async ({ page }) => {
  const log = await firstVisit(page)
  await expectNetworkState(page, 'running', { timeout: 15_000 })
  const at = (await screenNodes(page)).get(RING[2]!)!
  await page.mouse.move(at.x, at.y)
  await page.mouse.down()
  for (let i = 1; i <= 8; i += 1) await page.mouse.move(at.x + i * 6, at.y + i * 4)
  await page.mouse.up()
  await expect.poll(() => log.calls, { timeout: 5_000, message: 'the drag asked for a pause' }).toContain('POST /pause')
  await expectNetworkState(page, 'paused')
})
