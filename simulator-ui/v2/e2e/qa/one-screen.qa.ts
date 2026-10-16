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
 * (`e2e/live-autostart.live.ts`); the payment here is a frame the test writes into the product's own SSE pipeline.
 */
import { expect, test, type Page } from '@playwright/test'

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
  await expect(page.locator(STATE), 'the state of the network reads as living').toContainText('живёт', { timeout: 15_000 })
  await expect
    .poll(() => page.evaluate(() => (window as unknown as { __qaSse: { state: { controllers: unknown[] } } }).__qaSse.state.controllers.length), { timeout: 15_000 })
    .toBeGreaterThan(0)
  expect(log.calls, 'nothing paused the run at opening').not.toContain('POST /pause')

  await pushEvent(page, txUpdated([RING[0]!, RING[1]!, RING[2]!]))
  await expect.poll(() => fxCounts(page).then((c) => c.sparks), { timeout: 5_000, message: 'the beam of the payment is on the canvas' }).toBeGreaterThan(0)
})

test('(c) "Send Payment" pauses the run until "Поток": cancel does not resume it, the button does', async ({ page }) => {
  const log = await firstVisit(page)
  await expect(page.locator(STATE)).toContainText('живёт', { timeout: 15_000 })
  await expect(page.locator(FLOW), 'the flow button offers a pause while the run lives').toHaveAttribute('aria-pressed', 'true')

  await page.locator(SEND).click()
  await expect(page.locator('[data-testid="manual-payment-panel"]')).toBeVisible()
  await expect.poll(() => log.calls, { timeout: 5_000 }).toContain('POST /pause')
  await expect(page.locator(STATE)).toContainText('на паузе')

  // Cancel: the panel goes, the pause stays (decision 3 names only the button as the way back).
  await page.keyboard.press('Escape')
  await expect(page.locator('[data-testid="manual-payment-panel"]')).toBeHidden()
  await expect(page.locator(STATE)).toContainText('на паузе')
  expect(log.calls, 'nothing resumed the run on cancel').not.toContain('POST /resume')

  await expect(page.locator(FLOW)).toHaveAttribute('aria-pressed', 'false')
  await page.locator(FLOW).click()
  await expect.poll(() => log.calls, { timeout: 5_000 }).toContain('POST /resume')
  await expect(page.locator(STATE)).toContainText('живёт')
})

test('(c) choosing a participant in the living flow pauses it', async ({ page }) => {
  const log = await firstVisit(page)
  await expect(page.locator(STATE)).toContainText('живёт', { timeout: 15_000 })
  const nodes = await screenNodes(page)
  const at = nodes.get(RING[0]!)!
  await page.mouse.click(at.x, at.y)
  await expect.poll(() => log.calls, { timeout: 5_000, message: 'a click on a participant asks for a pause' }).toContain('POST /pause')
  await expect(page.locator(STATE)).toContainText('на паузе')
})

test('(c) a run somebody paused is attached and left paused: no create, no resume', async ({ page }) => {
  let log: LiveRunLog | null = null
  // A returning visitor: the stand seeds the stored run, which the backend says is paused.
  await boot(page, { snapshot: ringSnapshot(), clock: false, beforeLoad: async (p) => { log = await mockLiveRun(p, { initial: 'paused' }) } })
  await expect(page.locator(STATE)).toContainText('на паузе')
  await page.waitForTimeout(1_000) // the opening is over (the scene and the stream are up); a late start or resume would be in the log by now
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
