/**
 * 048 `T4840` - the navigator's dead end (advisor's decision 2026-10-19): closing the line window by ANY route ends the
 * "trust line" action, so the action bar returns to idle.
 *
 * Found on a live seeded backend at `7864e090`: "Подробнее" -> "Найти на графе" -> open a line -> close the line window by
 * Escape or by its x. The action stayed open with no window of it: "Линия доверия" active, "Платёж" and "Клиринг" disabled
 * with "Сначала отмените текущее действие (Esc)"; Escape had no window left to close, and the navigator is the only
 * keyboard route into the graph (the canvas has no tabindex) - only a reload released it.
 *
 * "Idle" is read from the page as a person meets it: the three actions enabled, none marked active, no "cancel the current
 * action first" line under them and in no button's title. Run on the mocked backend of the 037 specs (`helpers/p037Mock.ts`:
 * Alice, Bob, Carol; the line Bob -> Alice; the run is `paused`, so the three actions are enabled when nothing is open -
 * asserted before every path, so an idle bar at the end is not a bar that was never locked).
 *
 * The sibling paths of the same window: a participant opened from the navigator and its card closed; a participant
 * opened while a line is open (the card replaces the line window - one inspector at a time); a line opened by a
 * click on the canvas and closed; a line opened from the navigator, "Pay" pressed inside it - the payment goes on when the
 * line window (kept as its context) is closed, and the bar is idle once the payment is cancelled.
 *
 * What this does NOT see: a live backend (the manual walk on a seeded database is in the T4840 record of spec 048), a
 * request in flight (046 W3's busy tests hold that Escape and an outside click are ignored then), other viewports or
 * themes.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'

import { mockApp, ready } from './helpers/p037Mock.js'
import { t } from '../src/i18n/t'

const LINE = '.ws-shell[data-win-type="edge-detail"]'
const CARD = '.ws-shell[data-win-type="node-card"]'
const ACTIONS = ['actionbar-payment', 'actionbar-trustline', 'actionbar-clearing'] as const
const LOCKED = [t('card.flowOpenReason', undefined, 'en'), t('card.flowOpenReason', undefined, 'ru')]

/** The x of the line window: its role and its name in either language of the interface (the page picks the language). */
const closeOf = (line: Locator) =>
  line.getByRole('button', { name: new RegExp(`^(${t('common.close', undefined, 'en')}|${t('common.close', undefined, 'ru')})$`) })

async function open(page: Page): Promise<void> {
  await mockApp(page, { paymentRealBodies: [] })
  await page.setViewportSize({ width: 1366, height: 768 })
  await page.goto('/?mode=real&ui=interact&e2eReal=1')
  await ready(page, true)
  await page.waitForFunction(() => Boolean((window as unknown as { __geoSim?: unknown }).__geoSim))
}

/** The action bar as a person meets it when nothing is open. */
async function expectIdle(page: Page, when: string): Promise<void> {
  const bar = page.getByTestId('action-bar')
  for (const id of ACTIONS) {
    const button = bar.getByTestId(id)
    await expect(button, `${when}: ${id} is enabled`).toBeEnabled()
    await expect(button, `${when}: ${id} is not the active action`).toHaveAttribute('data-active', '0')
    expect(LOCKED, `${when}: ${id} does not say "cancel the current action first"`).not.toContain(await button.getAttribute('title'))
  }
  // BACKLOG 049-10: the line is in the first row of the top bar, beside "Подробнее" - not inside the action bar.
  await expect(page.getByTestId('actionbar-locked-hint'), `${when}: no "cancel the current action first" line`).toHaveCount(0)
}

/** The action bar locked by the open "trust line" action (the counter-check that the path did lock it). */
async function expectLockedByLine(page: Page): Promise<void> {
  const bar = page.getByTestId('action-bar')
  await expect(bar.getByTestId('actionbar-trustline')).toHaveAttribute('data-active', '1')
  await expect(bar.getByTestId('actionbar-payment')).toBeDisabled()
  await expect(bar.getByTestId('actionbar-clearing')).toBeDisabled()
  await expect(page.getByTestId('actionbar-locked-hint')).toBeVisible()
}

/** "Подробнее" -> "Найти на графе" -> open the line (Bob -> Alice), by keyboard as the only keyboard route does. */
async function lineFromNavigator(page: Page): Promise<Locator> {
  await page.getByTestId('details-toggle').click()
  const nav = page.getByTestId('graph-navigator')
  await nav.getByTestId('graph-navigator-summary').click()
  const openEdge = nav.getByTestId('graph-navigator-open-edge')
  await openEdge.focus()
  await openEdge.press('Enter')
  const line = page.locator(LINE)
  await expect(line.getByTestId('edge-detail-popup')).toBeVisible()
  return line
}

test.describe('048 T4840: closing the line window opened from "Найти на графе" leaves the action bar usable', () => {
  test('Escape closes the line window and the action bar is idle', async ({ page }) => {
    await open(page)
    await expectIdle(page, 'before')
    const line = await lineFromNavigator(page)
    await expectLockedByLine(page)

    await page.keyboard.press('Escape')
    await expect(line).toHaveCount(0)
    await expectIdle(page, 'after Escape')
  })

  test('the x closes the line window and the action bar is idle', async ({ page }) => {
    await open(page)
    await expectIdle(page, 'before')
    const line = await lineFromNavigator(page)
    await expectLockedByLine(page)

    await closeOf(line).click()
    await expect(line).toHaveCount(0)
    await expectIdle(page, 'after the x')
  })

  test('the same line opens again after Escape, and Escape closes it again', async ({ page }) => {
    await open(page)
    const line = await lineFromNavigator(page)
    await page.keyboard.press('Escape')
    await expect(line).toHaveCount(0)

    const openEdge = page.getByTestId('graph-navigator').getByTestId('graph-navigator-open-edge')
    await openEdge.focus()
    await openEdge.press('Enter')
    await expect(line.getByTestId('edge-detail-popup')).toBeVisible()
    await expectLockedByLine(page)
    await page.keyboard.press('Escape')
    await expect(line).toHaveCount(0)
    await expectIdle(page, 'after the second Escape')
  })
})

test.describe('048 T4840: the sibling paths of the line window', () => {
  test('a participant opened from the navigator: its card closes by Escape and the action bar is idle', async ({ page }) => {
    await open(page)
    await expectIdle(page, 'before')
    await page.getByTestId('details-toggle').click()
    const nav = page.getByTestId('graph-navigator')
    await nav.getByTestId('graph-navigator-summary').click()
    const openNode = nav.getByTestId('graph-navigator-open-node')
    await openNode.focus()
    await openNode.press('Enter')
    const card = page.locator(CARD)
    await expect(card.locator('.ds-ov-node-card')).toBeVisible()
    await expectIdle(page, 'with the card open')

    await page.keyboard.press('Escape')
    await expect(card).toHaveCount(0)
    await expectIdle(page, 'after the card closed')
  })

  test('a line from the navigator, then a participant from it: the card replaces the line window, the action ends', async ({ page }) => {
    // Internal adversarial pass, 2026-10-19 (finding 1): one inspector at a time - the card closes the line window, and
    // until this the action stayed open behind the card and, once the card closed, with no window at all.
    await open(page)
    await lineFromNavigator(page)
    await expectLockedByLine(page)
    const openNode = page.getByTestId('graph-navigator').getByTestId('graph-navigator-open-node')
    await openNode.focus()
    await openNode.press('Enter')
    const card = page.locator(CARD)
    await expect(card.locator('.ds-ov-node-card')).toBeVisible()
    await expect(page.locator(LINE)).toHaveCount(0)
    await expectIdle(page, 'with the card in place of the line window')

    await page.keyboard.press('Escape')
    await expect(card).toHaveCount(0)
    await expectIdle(page, 'after the card closed')
  })

  test('"Close line" in flight: a participant from the navigator is ignored; the refusal shows in the line window; Escape then frees the bar', async ({ page }) => {
    // §15 Codex, 2026-10-20 (P2): the request may take up to its 120 s timeout. Until this, the card replaced the line window
    // and the refusal stayed in the closed window, the action open with no window - the T4840 dead end again.
    let release!: () => void
    const held = new Promise<void>((resolve) => { release = resolve })
    await open(page)
    await page.route('**/simulator/runs/*/actions/trustline-close', async (r) => {
      await held
      // 409, not 404: an action's 404 also drops the run context (`useInteractActions.ts`), which is not this path.
      await r.fulfill({ status: 409, contentType: 'application/json', headers: { 'X-Request-ID': 'req-t4840' },
        body: JSON.stringify({ code: 'TRUSTLINE_CLOSED', message: 'Trustline already closed' }) })
    })
    const line = await lineFromNavigator(page)
    const closeLine = line.getByTestId('edge-close-line-btn')
    await closeLine.click() // arm
    await closeLine.click() // confirm: the request is in flight
    await expect(page.getByTestId('actionbar-busy-hint'), 'counter-check: the request is in flight').toBeVisible()

    const openNode = page.getByTestId('graph-navigator').getByTestId('graph-navigator-open-node')
    await openNode.focus()
    await openNode.press('Enter')
    await expect(page.locator(CARD), 'the participant card is not opened while the line\'s request is in flight').toHaveCount(0)
    await expect(line.getByTestId('edge-detail-popup')).toBeVisible()

    release()
    await expect(line.getByTestId('edge-detail-error'), 'the refusal is in the line window').toBeVisible()
    await expect(line.getByTestId('edge-detail-error')).toHaveAttribute('data-request-id', 'req-t4840')
    await page.keyboard.press('Escape')
    await expect(line).toHaveCount(0)
    await expectIdle(page, 'after the refusal was read and the window closed')
  })

  test('a line opened by a click on the canvas: the x closes it and the action bar is idle', async ({ page }) => {
    await open(page)
    await expectIdle(page, 'before')
    // A point of the line Bob -> Alice on the canvas: screen = world * zoom + pan (`__geoSim.camera` / `layoutNodes`).
    const mid = await page.evaluate(() => {
      const sim = (window as unknown as { __geoSim: {
        camera: { panX: number; panY: number; zoom: number }
        layoutNodes: ReadonlyArray<{ id: string; x: number; y: number }>
      } }).__geoSim
      const cam = sim.camera
      const a = sim.layoutNodes.find((n) => n.id === 'bob')
      const b = sim.layoutNodes.find((n) => n.id === 'alice')
      if (!a || !b) return null
      // A quarter of the way from Bob, not the middle: with three participants Carol sits near the middle and a click there
      // picks her (a node is picked before a line).
      const k = 0.25
      return { x: (a.x + (b.x - a.x) * k) * cam.zoom + cam.panX, y: (a.y + (b.y - a.y) * k) * cam.zoom + cam.panY }
    })
    expect(mid, 'counter-check: the line is laid out').not.toBeNull()
    const box = (await page.locator('canvas.canvas').first().boundingBox())!
    await page.mouse.click(box.x + mid!.x, box.y + mid!.y)
    const line = page.locator(LINE)
    await expect(line.getByTestId('edge-detail-popup')).toBeVisible()
    await expectLockedByLine(page)

    await closeOf(line).click()
    await expect(line).toHaveCount(0)
    await expectIdle(page, 'after the x')
  })

  test('"Pay" inside a line opened from the navigator: closing the line window keeps the payment; cancelling it leaves the bar idle', async ({ page }) => {
    await open(page)
    const line = await lineFromNavigator(page)
    await line.getByTestId('edge-send-payment').click()
    const payment = page.getByTestId('manual-payment-panel')
    await expect(payment).toBeVisible()
    await expect(line, 'counter-check: the line window stays as the context of the payment').toHaveCount(1)
    await expect(page.getByTestId('actionbar-payment')).toHaveAttribute('data-active', '1')

    // The line window is not the payment's window: closing it must not end the payment the person did not close. The
    // payment window lies over the line window's x (a mouse cannot reach it here), so the x is pressed from the keyboard.
    await closeOf(line).focus()
    await page.keyboard.press('Enter')
    await expect(line).toHaveCount(0)
    await expect(payment).toBeVisible()
    await expect(page.getByTestId('actionbar-payment')).toHaveAttribute('data-active', '1')

    await page.getByTestId('manual-payment-cancel').click()
    await expect(payment).toHaveCount(0)
    await expectIdle(page, 'after the payment was cancelled')
  })
})
