/**
 * Programme 037, PR C - the payment panel opened FROM A LINE (edge popup -> "Pay") fits the screen on 390x844, 375x667, 360x640 and
 * 844x390: the panel opens straight on the confirm step (summary, captions, the sentence about the direction, the amount), the
 * tallest first view of the flow, and the one the container specs of B1/B2 did not cover (they reach the confirm step by choosing).
 *
 * Entered through the DOM graph navigator ("Inspect graph" -> "Inspect edge"), because a click on a line of a phone-sized canvas
 * lands on the action buttons. "Fits" is the B1 rule (`helpers/p037Fit.ts`): the shell wholly in the viewport; the amount and
 * Confirm/Cancel whole and uncovered; measured before the next click, after the window manager settled.
 *
 * The window shell was seen at a NEGATIVE y here, and not in every run (-111 on 375x667 once, -138 on 360x640 another time): so each
 * viewport is entered several times in one test, and every entry has to fit.
 */
import { expect, test, type Page } from '@playwright/test'

import { measure, problems } from './helpers/p037Fit.js'
import { mockApp, ready } from './helpers/p037Mock.js'

const VIEWPORTS = [{ w: 390, h: 844 }, { w: 375, h: 667 }, { w: 360, h: 640 }, { w: 844, h: 390 }]
const ENTRIES = 4

async function viaLine(page: Page) {
  await page.goto('/?mode=real&ui=interact&e2eReal=1')
  await ready(page, true)
  const nav = page.getByRole('region', { name: 'Graph navigator' })
  await nav.getByText('Inspect graph', { exact: true }).tap()
  await nav.getByRole('button', { name: 'Inspect edge' }).tap()
  await expect(page.locator('[data-testid="edge-send-payment"]')).toBeVisible()
}

for (const vp of VIEWPORTS) {
  test.describe(`037 C - panel opened from a line ${vp.w}x${vp.h}`, () => {
    test.use({ viewport: { width: vp.w, height: vp.h }, hasTouch: true, isMobile: false })

    test(`the line popup and the confirm step opened from it fit, ${ENTRIES} entries in a row; the amount is where the focus is`, async ({ page }, testInfo) => {
      await mockApp(page, { paymentRealBodies: [] })
      const entries: Array<{ popup: string[]; confirm: string[]; shell: unknown; popupShell: unknown; focus: string }> = []

      for (let i = 0; i < ENTRIES; i += 1) {
        await viaLine(page)
        const popup = await measure(page, 'edge-popup', ['[data-testid="edge-send-payment"]'], 'edge-detail-popup')
        await page.locator('[data-testid="edge-send-payment"]').tap()
        await expect(page.locator('#mp-amount')).toBeVisible()
        const confirm = await measure(page, 'edge-confirm', ['#mp-amount', '[data-testid="manual-payment-confirm"]', '[data-testid="manual-payment-cancel"]'])
        const focus = await page.evaluate(() => (document.activeElement as HTMLElement | null)?.id ?? '')
        entries.push({ popup: problems(popup), confirm: problems(confirm), shell: confirm.shell, popupShell: popup.shell, focus })
      }

      console.log(`P037 C EDGE-ENTRY ${JSON.stringify({ viewport: vp, entries })}`)
      testInfo.annotations.push({ type: 'P037-C-EDGE', description: JSON.stringify(entries.map((e) => ({ popup: e.popup, confirm: e.confirm, focus: e.focus }))) })

      entries.forEach((e, i) => {
        expect.soft(e.popup, `entry ${i + 1}: the line popup in ${vp.w}x${vp.h}`).toEqual([])
        expect.soft(e.confirm, `entry ${i + 1}: the confirm step opened from the line in ${vp.w}x${vp.h}`).toEqual([])
        expect.soft(e.focus, `entry ${i + 1}: the focus is on the (empty) amount, so it is in view`).toBe('mp-amount')
      })
    })
  })
}
